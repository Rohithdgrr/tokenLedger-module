"""
Budget enforcement system.
Pre-flight spending control with multiple scope levels.
"""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .exceptions import BudgetExceededError
from .pricing import PricingRegistry
from .store import StorageBackend

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
    ) -> bool:
        """Check if the request is within budget."""
        if not self.store.get_all_budgets():
            return True
        applicable_budgets = self._get_applicable_budgets(user_id, project_id)

        for budget_key, budget in applicable_budgets:
            current_spend = self._calculate_current_spend(budget)
            estimated_cost = self._estimate_request_cost(provider, model, messages, input_tokens, output_tokens, max_tokens)
            projected_spend = current_spend + estimated_cost

            if projected_spend > budget.get("limit_usd", float("inf")):
                raise BudgetExceededError(
                    message=(
                        f"Budget exceeded for {budget_key}: "
                        f"${current_spend:.4f} + ${estimated_cost:.4f} > ${budget['limit_usd']:.4f}"
                    ),
                    scope=budget.get("scope", ""),
                    scope_id=budget.get("scope_id", ""),
                    current_spend=current_spend,
                    limit=budget.get("limit_usd", 0.0),
                )

        return True

    def _get_applicable_budgets(self, user_id: str, project_id: str) -> list[tuple]:
        """Find all budget rules that apply to this request."""
        budgets = []
        scope_keys = [
            ("global", "all"),
            ("project", project_id),
            ("user", user_id),
            ("user_project", f"{user_id}:{project_id}"),
        ]
        for scope, scope_id in scope_keys:
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
            key = "global:all" if scope == "global" else f"{scope}:{scope_id}"
            totals = self.store.running_totals.get(key, {})
            return round(float(totals.get("cost_usd", 0)), 10)

        window_start = self._get_window_start(reset_cycle)
        # Try store-optimized path first
        if hasattr(self.store, "get_windowed_spend"):
            try:
                optimized = self.store.get_windowed_spend(budget, window_start)  # type: ignore[operator]
                if optimized is not None:
                    return round(float(optimized), 10)
            except Exception:
                pass
        total = 0.0
        for record in self.store.get_records():
            ts = record.get("timestamp", "")
            if ts and ts < window_start:
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
        if reset_cycle == "daily":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif reset_cycle == "weekly":
            days_since_monday = now.weekday()
            start = (now - timedelta(days=days_since_monday)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        elif reset_cycle == "monthly":
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        elif reset_cycle == "never":
            start = datetime.min
        else:
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start.isoformat()

    def _record_matches_budget(self, record: dict[str, Any], budget: dict[str, Any]) -> bool:
        """Check if a historical record falls under a budget's scope."""
        scope = budget.get("scope", "global")
        scope_id = budget.get("scope_id", "")
        if scope == "global":
            return True
        if scope == "project":
            return bool(record.get("project_id", "default") == scope_id)
        if scope == "user":
            return bool(record.get("user_id", "anonymous") == scope_id)
        if scope == "user_project":
            parts = scope_id.split(":")
            if len(parts) == 2:
                return bool(record.get("user_id") == parts[0] and record.get("project_id") == parts[1])
        return False

    def _estimate_request_cost(
        self,
        provider: str,
        model: str,
        messages: Optional[list[dict[str, str]]] = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        max_tokens: Optional[int] = None,
    ) -> float:
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
