"""
Budget enforcement system.
Pre-flight spending control with multiple scope levels.
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .exceptions import BudgetExceededError
from .pricing import PricingRegistry
from .scopes import matches_scope
from .store import StorageBackend, normalize_ts

logger = logging.getLogger(__name__)

# Re-export for backwards compatibility
__all__ = ["BudgetExceededError"]


class BudgetEnforcer:
    """
    Enforces spending limits before API calls are made.
    Supports multiple budget scopes: global, project, user, user_project.
    Uses O(1) running totals for ``never`` budgets and windowed scans for
    daily/weekly/monthly reset cycles.
    """

    def __init__(self, store: StorageBackend, pricing: PricingRegistry):
        self.store = store
        self.pricing = pricing
        self._avg_output_per_model: dict[str, float] = defaultdict(lambda: 0.5)

    def __repr__(self) -> str:
        return f"<BudgetEnforcer budgets={len(self.store.get_all_budgets())}>"

    def check_budget(
        self,
        user_id: str,
        project_id: str,
        provider: str,
        model: str,
        messages: Optional[list[dict[str, str]]] = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        max_tokens: Optional[int] = None,
        tenant_id: str = "",
        conversation_id: str = "",
        agent_id: str = "",
    ) -> bool:
        """Check if the request is within budget."""
        if not self.store.get_all_budgets():
            return True
        applicable_budgets = self._get_applicable_budgets(user_id, project_id, provider, model, tenant_id, conversation_id, agent_id)

        for budget_key, budget in applicable_budgets:
            current_spend = self._calculate_current_spend(budget)
            estimated_cost = self._estimate_request_cost(provider, model, messages, input_tokens, output_tokens, max_tokens)
            projected_spend = current_spend + estimated_cost

            if projected_spend > budget.get("limit_usd", float("inf")):
                raise BudgetExceededError(
                    message=(
                        f"Budget exceeded for {budget_key}: ${current_spend:.4f} + ${estimated_cost:.4f} > ${budget['limit_usd']:.4f}"
                    ),
                    scope=budget.get("scope", ""),
                    scope_id=budget.get("scope_id", ""),
                    current_spend=current_spend,
                    limit=budget.get("limit_usd", 0.0),
                )

        return True

    def get_budget_status(self) -> list[dict[str, Any]]:
        """Return spend/utilization for every configured budget rule.

        Used by :meth:`TokenLedger.health` and monitoring dashboards.
        Never raises — a read failure on one rule yields ``-1`` spend so the
        rest of the report stays useful.
        """
        result = []
        for _key, budget in self.store.get_all_budgets().items():
            scope = budget.get("scope", "global")
            scope_id = budget.get("scope_id", "")
            limit = float(budget.get("limit_usd", 0.0))
            try:
                spent = float(self._calculate_current_spend(budget))
            except Exception:  # pragma: no cover — defensive
                spent = -1.0
            result.append(
                {
                    "scope": scope,
                    "scope_id": scope_id,
                    "limit_usd": limit,
                    "spent_usd": round(spent, 6),
                    "utilization_percent": round((spent / limit * 100) if limit else 0.0, 2),
                    "reset_cycle": budget.get("reset_cycle", "monthly"),
                }
            )
        return result

    def _get_applicable_budgets(
        self,
        user_id: str,
        project_id: str,
        provider: str = "",
        model: str = "",
        tenant_id: str = "",
        conversation_id: str = "",
        agent_id: str = "",
    ) -> list[tuple]:
        """Find all budget rules that apply to this request."""
        budgets = []
        scope_keys = [
            ("global", "all"),
            ("provider", provider),
            ("model", model),
            ("project", project_id),
            ("user", user_id),
            ("user_project", f"{user_id}:{project_id}"),
            ("tenant", tenant_id),
        ]
        if conversation_id:
            scope_keys.append(("conversation", conversation_id))
        if agent_id:
            scope_keys.append(("agent", agent_id))
        for scope, scope_id in scope_keys:
            if not scope_id:
                continue
            budget = self.store.get_budget(scope, scope_id)
            if budget:
                budgets.append((f"{scope}:{scope_id}", budget))
        return budgets

    def _calculate_current_spend(self, budget: dict[str, Any]) -> float:
        """Calculate current spend within the budget's reset window.

        Windowed budgets (daily/weekly/monthly) are computed by scanning
        records inside the current window; ``never`` budgets use running
        totals for O(1) lookups. ``user_project`` spends always use the
        intersection scan because no running-total dimension captures both
        dimensions at once.

        If the store implements :meth:`get_windowed_spend`, that indexed
        path is preferred (e.g. SQLite SUM query).
        """
        scope = budget.get("scope", "global")
        scope_id = budget.get("scope_id", "")
        reset_cycle = budget.get("reset_cycle", "monthly")

        if reset_cycle == "never" and scope != "user_project":
            totals = self.store.get_running_totals(scope, scope_id)
            return round(float(totals.get("cost_usd", 0)), 10)

        window_start = self._get_window_start(reset_cycle)
        # Normalize window_start to aware datetime for robust comparison
        try:
            ws_dt = datetime.fromisoformat(window_start.replace("Z", "+00:00"))
            if ws_dt.tzinfo is None:
                ws_dt = ws_dt.replace(tzinfo=timezone.utc)
        except Exception:
            ws_dt = None
        # Try store-optimized path first
        if hasattr(self.store, "get_windowed_spend"):
            try:
                optimized = self.store.get_windowed_spend(budget, window_start)  # type: ignore[operator]
                if optimized is not None:
                    return round(float(optimized), 10)
            except Exception as e:
                logger.debug("windowed spend query failed, falling back to scan: %s", e)
        total = 0.0
        ws_norm = normalize_ts(window_start)
        for record in self.store.get_records():
            ts = record.get("timestamp", "")
            if ts and ws_dt is not None:
                try:
                    ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if ts_dt.tzinfo is None:
                        ts_dt = ts_dt.replace(tzinfo=timezone.utc)
                    if ts_dt < ws_dt:
                        continue
                except Exception:
                    # Fallback to string compare if parsing fails
                    if ts and normalize_ts(ts) < ws_norm:
                        continue
            elif ts and normalize_ts(ts) < ws_norm:
                continue
            if record.get("status") in ("blocked", "error") or record.get("_ghost"):
                continue
            if not self._record_matches_budget(record, budget):
                continue
            total += float(record.get("cost_usd", 0) or 0)
        return round(total, 10)

    def _get_window_start(self, reset_cycle: str) -> str:
        """Get the start of the current budget window."""
        now = datetime.now(timezone.utc)
        if reset_cycle == "never":
            # Use a fixed epoch sentinel — naive UTC so it compares correctly
            # with normalize_ts() timestamps (which are also naive UTC).
            return "1970-01-01T00:00:00"
        if reset_cycle == "daily":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif reset_cycle == "weekly":
            days_since_monday = now.weekday()
            start = (now - timedelta(days=days_since_monday)).replace(hour=0, minute=0, second=0, microsecond=0)
        elif reset_cycle == "monthly":
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # Naive UTC: window starts are always computed in UTC, and strings
        # must compare correctly against normalized naively-stored timestamps.
        return start.astimezone(timezone.utc).replace(tzinfo=None).isoformat()

    def _record_matches_budget(self, record: dict[str, Any], budget: dict[str, Any]) -> bool:
        """Check if a historical record falls under a budget's scope."""
        return matches_scope(record, budget.get("scope", "global"), budget.get("scope_id", ""))

    def _estimate_request_cost(
        self,
        provider: str,
        model: str,
        messages: Optional[list[dict[str, str]]] = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        max_tokens: Optional[int] = None,
    ) -> float:
        """Estimate request cost for pre-flight checks.

        .. note::
            ``max_tokens`` is treated as an output-side cap; APIs that count
            ``max_tokens`` toward total context length (input + output) may
            be underestimated here. Estimates are only used for pre-flight
            gating — actual spend always comes from verified records.
        """
        pricing = self.pricing.get_pricing(provider, model)

        if input_tokens or output_tokens:
            estimated_input = input_tokens
            estimated_output = output_tokens
        elif messages:
            text = " ".join([str(m.get("content", "")) for m in messages])
            estimated_input = max(1, len(text) // 4)
            avg_ratio = self._avg_output_per_model.get(model, 0.5)
            estimated_output = max(1, int(estimated_input * avg_ratio))
            if max_tokens:
                estimated_output = min(max_tokens, estimated_output)
        else:
            estimated_input = 100
            estimated_output = max_tokens if max_tokens else 50

        return float(estimated_input) * float(pricing["input_per_token"]) + float(estimated_output) * float(pricing["output_per_token"])

    def update_model_stats(self, model: str, input_tokens: int, output_tokens: int) -> None:
        """Update cached average output ratio for a model."""
        if input_tokens > 0:
            ratio = output_tokens / input_tokens
            old = self._avg_output_per_model.get(model, 0.5)
            self._avg_output_per_model[model] = old * 0.9 + ratio * 0.1
