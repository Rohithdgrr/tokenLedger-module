"""Tests for budget reset-window spend calculation and scope accounting."""

from datetime import datetime, timedelta, timezone

import pytest

from tokenledger import BudgetExceededError, TokenLedger


def _record(cost: float, timestamp: str, user_id: str = "anonymous", project_id: str = "default", status: str = "success") -> dict:
    return {
        "provider": "openai",
        "model": "gpt-4o",
        "input_tokens": 1000,
        "output_tokens": 500,
        "total_tokens": 1500,
        "cost_usd": cost,
        "timestamp": timestamp,
        "user_id": user_id,
        "project_id": project_id,
        "status": status,
    }


class TestBudgetResetWindows:
    def test_monthly_window_ignores_previous_month(self):
        ledger = TokenLedger()
        ledger.set_budget("project", "pm", limit_usd=0.01, reset_cycle="monthly")
        old = (datetime.now(timezone.utc) - timedelta(days=35)).isoformat()
        ledger.store.insert_record(_record(0.0125, old, project_id="pm"))

        spend = ledger.budget_enforcer._calculate_current_spend(ledger.store.get_budget("project", "pm"))
        assert spend == 0.0
        assert ledger.budget_enforcer.check_budget("anonymous", "pm", "openai", "gpt-4o", input_tokens=10, output_tokens=5)

    def test_daily_window_ignores_two_days_ago(self):
        ledger = TokenLedger()
        ledger.set_budget("project", "pd", limit_usd=0.01, reset_cycle="daily")
        old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        ledger.store.insert_record(_record(0.0125, old, project_id="pd"))

        spend = ledger.budget_enforcer._calculate_current_spend(ledger.store.get_budget("project", "pd"))
        assert spend == 0.0

    def test_weekly_window_ignores_last_week(self):
        ledger = TokenLedger()
        ledger.set_budget("project", "pw", limit_usd=0.01, reset_cycle="weekly")
        old = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()
        ledger.store.insert_record(_record(0.0125, old, project_id="pw"))

        spend = ledger.budget_enforcer._calculate_current_spend(ledger.store.get_budget("project", "pw"))
        assert spend == 0.0

    def test_window_counts_current_spend(self):
        ledger = TokenLedger()
        ledger.set_budget("project", "pc", limit_usd=0.001, reset_cycle="monthly")
        now = datetime.now(timezone.utc).isoformat()
        ledger.store.insert_record(_record(0.002, now, project_id="pc"))

        spend = ledger.budget_enforcer._calculate_current_spend(ledger.store.get_budget("project", "pc"))
        assert spend == pytest.approx(0.002, abs=1e-10)
        with pytest.raises(BudgetExceededError):
            ledger.budget_enforcer.check_budget("anonymous", "pc", "openai", "gpt-4o", input_tokens=10, output_tokens=5)

    def test_never_budget_counts_all_history(self):
        ledger = TokenLedger()
        ledger.set_budget("project", "pn", limit_usd=0.01, reset_cycle="never")
        old = (datetime.now(timezone.utc) - timedelta(days=35)).isoformat()
        ledger.store.insert_record(_record(0.0125, old, project_id="pn"))

        spend = ledger.budget_enforcer._calculate_current_spend(ledger.store.get_budget("project", "pn"))
        assert spend == pytest.approx(0.0125, abs=1e-10)
        with pytest.raises(BudgetExceededError):
            ledger.budget_enforcer.check_budget("anonymous", "pn", "openai", "gpt-4o", input_tokens=10, output_tokens=5)

    def test_default_reset_cycle_is_monthly(self):
        ledger = TokenLedger()
        ledger.set_budget("project", "pm", limit_usd=100.0)
        budget = ledger.store.get_budget("project", "pm")
        assert budget["reset_cycle"] == "monthly"


class TestBudgetUserProjectScope:
    def test_user_project_uses_intersection_not_max(self):
        ledger = TokenLedger()
        ledger.set_budget("user_project", "alice:app1", limit_usd=0.01, reset_cycle="never")
        now = datetime.now(timezone.utc).isoformat()
        ledger.store.insert_record(_record(0.009, now, user_id="alice", project_id="app1"))
        ledger.store.insert_record(_record(0.009, now, user_id="alice", project_id="app2"))
        ledger.store.insert_record(_record(0.009, now, user_id="bob", project_id="app1"))

        budget = ledger.store.get_budget("user_project", "alice:app1")
        spend = ledger.budget_enforcer._calculate_current_spend(budget)
        # Old behavior: max(user total, project total) = 0.018 — would block.
        assert spend == pytest.approx(0.009, abs=1e-10)
        assert ledger.budget_enforcer.check_budget("alice", "app1", "openai", "gpt-4o", input_tokens=10, output_tokens=5)


class TestBudgetExcludesNonBillable:
    def test_blocked_and_ghost_records_not_billed(self):
        ledger = TokenLedger()
        ledger.set_budget("project", "pb", limit_usd=0.01, reset_cycle="monthly")
        now = datetime.now(timezone.utc).isoformat()
        ledger.store.insert_record(_record(0.0125, now, project_id="pb", status="blocked"))
        blocked = ledger.budget_enforcer._calculate_current_spend(ledger.store.get_budget("project", "pb"))
        assert blocked == 0.0

        l2 = TokenLedger()
        l2.set_budget("project", "pg", limit_usd=0.01, reset_cycle="monthly")
        rec = _record(0.0125, now, project_id="pg")
        rec["_ghost"] = True
        l2.store.insert_record(rec)
        ghost = l2.budget_enforcer._calculate_current_spend(l2.store.get_budget("project", "pg"))
        assert ghost == 0.0
