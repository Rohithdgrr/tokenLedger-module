"""Shared scope-matching logic — single source of truth for record filters."""

from typing import Any


def matches_scope(record: dict[str, Any], scope: str, scope_id: str) -> bool:
    """Return whether *record* falls under the given budget/analytics scope.

    Used by :class:`tokenledger.core.budget.BudgetEnforcer`,
    :class:`tokenledger.core.analytics.AnalyticsEngine` and
    :class:`tokenledger.core.ledger.TokenLedger` so all scope semantics
    stay consistent in one place.
    """
    if scope == "global":
        return True
    if scope == "provider":
        return bool(record.get("provider") == scope_id)
    if scope == "model":
        return bool(record.get("model") == scope_id)
    if scope == "user":
        return bool(record.get("user_id", "anonymous") == scope_id)
    if scope == "project":
        return bool(record.get("project_id", "default") == scope_id)
    if scope == "conversation":
        return bool(record.get("conversation_id") == scope_id)
    if scope == "agent":
        return bool(record.get("agent_id") == scope_id)
    if scope == "tenant":
        return bool(record.get("tenant_id") == scope_id)
    if scope == "user_project":
        parts = scope_id.split(":")
        if len(parts) == 2:
            return bool(record.get("user_id") == parts[0] and record.get("project_id") == parts[1])
    return False
