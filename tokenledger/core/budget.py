"""
Budget enforcement system.
Pre-flight spending control with multiple scope levels.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .store import MemoryStore
from .pricing import PricingRegistry


class BudgetExceededError(Exception):
    """Raised when a budget limit is exceeded."""

    def __init__(
        self,
        message: str,
        scope: str = "",
        scope_id: str = "",
        current_spend: float = 0.0,
        limit: float = 0.0,
    ):
        super().__init__(message)
        self.scope = scope
        self.scope_id = scope_id
        self.current_spend = current_spend
        self.limit = limit


class BudgetEnforcer:
    """
    Enforces spending limits before API calls are made.
    Supports multiple budget scopes: global, project, user, user_project.
    """

    def __init__(self, store: MemoryStore, pricing: PricingRegistry):
        self.store = store
        self.pricing = pricing

    def check_budget(
        self,
        user_id: str,
        project_id: str,
        provider: str,
        model: str,
        messages: Optional[List[Dict[str, str]]] = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> bool:
        """Check if the request is within budget."""
        applicable_budgets = self._get_applicable_budgets(user_id, project_id)

        for budget_key, budget in applicable_budgets:
            current_spend = self._calculate_current_spend(budget)
            estimated_cost = self._estimate_request_cost(provider, model, messages, input_tokens, output_tokens)
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

    def _get_applicable_budgets(self, user_id: str, project_id: str) -> List[tuple]:
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

    def _calculate_current_spend(self, budget: Dict[str, Any]) -> float:
        """Calculate current spend using running totals (O(1))."""
        reset_cycle = budget.get("reset_cycle", "monthly")
        window_start = self._get_window_start(reset_cycle)

        scope = budget.get("scope", "global")
        scope_id = budget.get("scope_id", "")

        if scope == "global":
            key = "global:all"
        elif scope in ("project", "user"):
            key = f"{scope}:{scope_id}"
        elif scope == "user_project":
            parts = scope_id.split(":")
            if len(parts) == 2:
                key_user = f"user:{parts[0]}"
                key_project = f"project:{parts[1]}"
                user_totals = self.store.running_totals.get(key_user, {})
                project_totals = self.store.running_totals.get(key_project, {})
                return round(
                    max(user_totals.get("cost_usd", 0), project_totals.get("cost_usd", 0)),
                    10,
                )
            return 0.0
        else:
            return 0.0

        totals = self.store.running_totals.get(key, {})
        return round(totals.get("cost_usd", 0), 10)

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

    def _record_matches_budget(self, record: Dict[str, Any], budget: Dict[str, Any]) -> bool:
        """Check if a historical record falls under a budget's scope."""
        scope = budget.get("scope", "global")
        scope_id = budget.get("scope_id", "")

        if scope == "global":
            return True
        if scope == "project":
            return record.get("project_id", "default") == scope_id
        if scope == "user":
            return record.get("user_id", "anonymous") == scope_id
        if scope == "user_project":
            parts = scope_id.split(":")
            if len(parts) == 2:
                return record.get("user_id") == parts[0] and record.get("project_id") == parts[1]
        return False

    def _estimate_request_cost(
        self,
        provider: str,
        model: str,
        messages: Optional[List[Dict[str, str]]] = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> float:
        pricing = self.pricing.get_pricing(provider, model)

        if input_tokens or output_tokens:
            estimated_input = input_tokens
            estimated_output = output_tokens
        elif messages:
            text = " ".join([str(m.get("content", "")) for m in messages])
            estimated_input = max(1, len(text) // 4)
            estimated_output = max(1, estimated_input // 2)
        else:
            estimated_input = 100
            estimated_output = 50

        return estimated_input * pricing["input_per_token"] + estimated_output * pricing["output_per_token"]
