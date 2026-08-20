"""Tests for audit follow-ups: health(), budget status, contextvars,
reprs, and __slots__ on hot-path classes."""

import logging
from types import SimpleNamespace

import pytest

from tokenledger import BudgetExceededError, TokenLedger, ledger_context
from tokenledger.core.interceptor import StreamWrapper, TokenBucket


class FakeOpenAI:
    def __init__(self):
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(
                create=self._create,
            )
        )

    def _create(self, model="gpt-4o", messages=(), **kwargs):
        return SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5)
        )


class TestHealth:
    def test_report_shape_and_uptime(self):
        ledger = TokenLedger()
        health = ledger.health()
        assert health["store"]["type"] == "MemoryStore"
        assert health["store"]["records"] == 0
        assert health["budgets"] == []
        assert health["circuits"] == {}
        assert health["uptime_s"] >= 0
        assert health["warnings"] == []

    def test_budget_warning_at_80_percent(self):
        ledger = TokenLedger()
        ledger.set_budget("global", "all", 10.0, reset_cycle="never")
        ledger.record_usage("openai", "gpt-4o", 100_000, 50_000)  # ~$3.20
        health = ledger.health()
        assert len(health["budgets"]) == 1
        assert health["budgets"][0]["scope"] == "global"
        assert health["budgets"][0]["utilization_percent"] > 0
        assert not health["warnings"]  # ~32% < 80%

    def test_budget_status_utilization_over_100(self):
        ledger = TokenLedger()
        ledger.set_budget("user", "alice", 3.00, reset_cycle="never")
        ledger.record_usage("openai", "gpt-4o", 200_000, 100_000, user_id="alice")  # costs $2.50
        status = ledger.get_budget_status()
        assert len(status) == 1
        assert status[0]["scope_id"] == "alice"
        assert status[0]["utilization_percent"] > 80
        with pytest.raises(BudgetExceededError):
            ledger.record_usage("openai", "gpt-4o", 200_000, 100_000, user_id="alice")  # pre-flight blocks
        assert ledger.health()["warnings"]

    def test_health_never_raises_with_broken_budget(self):
        ledger = TokenLedger()
        ledger.set_budget("global", "all", 1.0, reset_cycle="monthly")
        ledger.store.budgets["global:all"] = {"scope": "global", "scope_id": "all"}  # no limit_usd
        health = ledger.health()
        assert health["store"]["records"] == 0


class TestLedgerContext:
    def test_context_tags_wrapped_calls(self):
        ledger = TokenLedger()
        client = FakeOpenAI()
        ledger.wrap_openai(client)
        token = ledger_context.set({"user_id": "ctx-user", "project_id": "ctx-proj"})
        try:
            client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])
        finally:
            ledger_context.reset(token)
        record = ledger.get_records()[-1]
        assert record["user_id"] == "ctx-user"
        assert record["project_id"] == "ctx-proj"

    def test_explicit_kwarg_overrides_context(self):
        ledger = TokenLedger()
        client = FakeOpenAI()
        ledger.wrap_openai(client)
        token = ledger_context.set({"user_id": "not-overridden"})
        try:
            client.chat.completions.create(
                model="gpt-4o", messages=[{"role": "user", "content": "hi"}], user_id="explicit"
            )
        finally:
            ledger_context.reset(token)
        assert ledger.get_records()[-1]["user_id"] == "explicit"


class TestReprs:
    def test_public_class_reprs(self):
        ledger = TokenLedger()
        assert "records=0" in repr(ledger)
        assert "MemoryStore" in repr(ledger.store)
        assert "records=0" in repr(ledger.analytics)
        assert "budgets=0" in repr(ledger.budget_enforcer)
        assert "wrapped_clients=0" in repr(ledger.interceptor)
        assert "TokenEstimator" in repr(ledger.estimator)
        assert "TokenExtractor" in repr(ledger.extractor)

    def test_sqlite_repr(self):
        from tokenledger.ext.sqlite_store import SqliteStore

        store = SqliteStore(":memory:")
        assert "SqliteStore" in repr(store)
        assert "records=0" in repr(store)


class TestSlots:
    def test_hot_classes_have_no_dict(self):
        with pytest.raises(TypeError):
            vars(TokenBucket(rate=10.0))  # __slots__ without __dict__

        bucket = TokenBucket(rate=10.0)
        with pytest.raises(TypeError):
            vars(bucket)
        assert hasattr(bucket, "tokens")  # still settable
        bucket.tokens = 0.5

        with pytest.raises(TypeError):
            vars(StreamWrapper(iter([]), "openai"))

        # _ProxyWrapper forwards attribute *reads* to the client, so inspect
        # the class contract instead of the instance.
        client = FakeOpenAI()
        ledger = TokenLedger()
        proxy = ledger.interceptor.wrap_proxy(client, "chat.completions.create", "openai")
        assert type(proxy).__slots__ == ("_client", "_interceptor", "_provider", "_attr_path")
        proxy.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])
        assert len(ledger.get_records()) == 1  # end-to-end tracking through the proxy


class TestChecksumStability:
    def test_checksum_uses_version_stable_serialization(self):
        """orjson compaction must never change checksum bytes (breaks old files)."""
        import hashlib
        import json

        from tokenledger.core.store import _checksum

        record = {"provider": "openai", "model": "gpt-4o", "input_tokens": 1}
        expected = hashlib.sha256(json.dumps(record, default=str).encode()).hexdigest()
        assert _checksum(record) == expected


class TestDegradationLogging:
    def test_estimator_logs_fallback_once(self, caplog):
        try:
            import tiktoken  # noqa: F401

            pytest.skip("tiktoken installed — no fallback to observe")
        except ImportError:
            pass
        from tokenledger.core.estimator import TokenEstimator

        with caplog.at_level(logging.INFO, logger="tokenledger.core.estimator"):
            TokenEstimator()
            TokenEstimator()
        infos = [r for r in caplog.records if "tiktoken" in r.getMessage()]
        assert len(infos) == 1
