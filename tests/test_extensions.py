"""Tests for TokenLedger CLI, OpenTelemetry, and WebhookNotifier extensions."""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest


class TestCLI:
    def test_summary_empty(self):
        from tokenledger.__main__ import cmd_summary

        class Args:
            file = None
            detail = False

        cmd_summary(Args())

    def test_summary_with_records(self):
        from tokenledger import TokenLedger
        from tokenledger.__main__ import cmd_summary

        ledger = TokenLedger()
        ledger.record_usage(provider="openai", model="gpt-4", input_tokens=10, output_tokens=5)

        class Args:
            file = None
            detail = True

        with patch("tokenledger.__main__._build_ledger", return_value=ledger):
            cmd_summary(Args())

    def test_export_json(self):
        from tokenledger import TokenLedger
        from tokenledger.__main__ import cmd_export

        ledger = TokenLedger()
        ledger.record_usage(provider="test", model="t1", input_tokens=1, output_tokens=1)

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            out = f.name

        class Args:
            file = None
            format = "json"
            output = out

        try:
            with patch("tokenledger.__main__._build_ledger", return_value=ledger):
                cmd_export(Args())
            with open(out) as f:
                data = json.load(f)
            assert len(data) == 1
        finally:
            os.unlink(out)

    def test_verify_clean(self):
        from tokenledger import TokenLedger
        from tokenledger.__main__ import cmd_verify

        ledger = TokenLedger()
        ledger.record_usage(provider="test", model="t1", input_tokens=1, output_tokens=1)

        class Args:
            file = None

        with patch("tokenledger.__main__._build_ledger", return_value=ledger):
            cmd_verify(Args())

    def test_compact(self):
        from tokenledger import TokenLedger
        from tokenledger.__main__ import cmd_compact

        ledger = TokenLedger()
        ledger.record_usage(provider="test", model="t1", input_tokens=1, output_tokens=1)

        class Args:
            file = None

        with patch("tokenledger.__main__._build_ledger", return_value=ledger):
            cmd_compact(Args())

    def test_health(self):
        from tokenledger import TokenLedger
        from tokenledger.__main__ import cmd_health

        ledger = TokenLedger()
        ledger.record_usage(provider="test", model="t1", input_tokens=1, output_tokens=1)

        class Args:
            file = None

        with patch("tokenledger.__main__._build_ledger", return_value=ledger):
            cmd_health(Args())

    def test_main_parser_summary(self):
        from tokenledger.__main__ import main
        with patch("tokenledger.__main__.cmd_summary") as mock:
            with patch("sys.argv", ["tokenledger", "summary", "--detail"]):
                main()
        mock.assert_called_once()

    def test_main_parser_export(self):
        from tokenledger.__main__ import main
        with patch("tokenledger.__main__.cmd_export") as mock:
            with patch("sys.argv", ["tokenledger", "export", "--format", "csv", "-o", "out.csv"]):
                main()
        mock.assert_called_once()

    def test_main_parser_verify(self):
        from tokenledger.__main__ import main
        with patch("tokenledger.__main__.cmd_verify") as mock:
            with patch("sys.argv", ["tokenledger", "verify"]):
                main()
        mock.assert_called_once()

    def test_main_parser_compact(self):
        from tokenledger.__main__ import main
        with patch("tokenledger.__main__.cmd_compact") as mock:
            with patch("sys.argv", ["tokenledger", "compact"]):
                main()
        mock.assert_called_once()

    def test_main_parser_health(self):
        from tokenledger.__main__ import main
        with patch("tokenledger.__main__.cmd_health") as mock:
            with patch("sys.argv", ["tokenledger", "health"]):
                main()
        mock.assert_called_once()


class TestOpenTelemetry:
    def test_instrument_requires_otel(self):
        import tokenledger.ext.opentelemetry as otel_mod
        from tokenledger.ext.opentelemetry import _HAS_OTEL, instrument_ledger
        if not _HAS_OTEL:
            with patch.object(otel_mod, "_HAS_OTEL", False):
                with pytest.raises(ImportError):
                    instrument_ledger(None)

    def test_instrument_sets_callback(self):
        from tokenledger import TokenLedger
        from tokenledger.ext.opentelemetry import _HAS_OTEL, instrument_ledger

        if not _HAS_OTEL:
            pytest.skip("opentelemetry not installed")
            return

        ledger = TokenLedger()
        instrument_ledger(ledger)
        # callback should be set to our wrapper
        assert ledger.interceptor.on_record is not None

    def test_instrument_with_fake_tracer(self):
        from tokenledger import TokenLedger
        from tokenledger.ext.opentelemetry import _HAS_OTEL, instrument_ledger

        if not _HAS_OTEL:
            pytest.skip("opentelemetry not installed")
            return

        ledger = TokenLedger()
        fake_tracer = type("FakeTracer", (), {
            "start_as_current_span": lambda self, name, kind=None: _fake_span()
        })()
        instrument_ledger(ledger, tracer=fake_tracer)


def _fake_span():
    class FakeSpan:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass
        def set_attribute(self, k, v):
            pass
    return FakeSpan()


class TestWebhookNotifier:
    def test_on_budget_exceeded_logs(self, caplog):
        import logging

        from tokenledger.ext.notifier import WebhookNotifier
        caplog.set_level(logging.WARNING)
        n = WebhookNotifier()
        n.on_budget_exceeded(Exception("over limit"))
        assert "over limit" in caplog.text

    def test_on_budget_threshold_logs(self, caplog):
        import logging

        from tokenledger.ext.notifier import WebhookNotifier
        caplog.set_level(logging.WARNING)
        n = WebhookNotifier()
        n.on_budget_threshold("user", "alice", 8.0, 10.0)
        assert "80.0%" in caplog.text

    def test_on_record_no_cost(self):
        from tokenledger.ext.notifier import WebhookNotifier
        n = WebhookNotifier()
        n.on_record({"cost_usd": 0, "model": "t1", "total_tokens": 0})  # no-op

    def test_on_record_with_cost(self, caplog):
        import logging

        from tokenledger.ext.notifier import WebhookNotifier
        caplog.set_level(logging.WARNING)
        n = WebhookNotifier()
        n.on_record({"cost_usd": 0.5, "model": "gpt-4", "total_tokens": 100})

    def test_slack_webhook_error_logged(self, caplog):
        import logging

        from tokenledger.ext.notifier import WebhookNotifier
        caplog.set_level(logging.WARNING)
        n = WebhookNotifier(slack_url="http://localhost:1/nonexistent")
        n.on_budget_exceeded(Exception("test"))
        assert any("Slack webhook failed" in r.message for r in caplog.records)


class TestAsyncStore:
    @pytest.mark.asyncio
    async def test_async_insert_and_count(self):
        from tokenledger import TokenLedger
        ledger = TokenLedger()
        ledger.record_usage(
            provider="test", model="t1", input_tokens=1, output_tokens=1,
        )
        assert ledger.store.get_record_count() == 1

    @pytest.mark.asyncio
    async def test_async_compact(self):
        from datetime import datetime, timedelta, timezone

        from tokenledger import TokenLedger
        ledger = TokenLedger()
        ledger.record_usage(provider="test", model="t1", input_tokens=1, output_tokens=1)
        old_ts = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        ledger.store.records[-1]["timestamp"] = old_ts
        ledger.store.retention.max_age_days = 0
        result = await ledger.store.async_compact()
        assert result["removed"] == 1


class TestOnBudgetThreshold:
    def test_threshold_callback_settable(self):
        from tokenledger import TokenLedger
        ledger = TokenLedger()
        calls = []
        ledger.interceptor.on_budget_threshold = lambda s, sid, c, cost: calls.append((s, sid, c, cost))
        assert ledger.interceptor.on_budget_threshold is not None


class TestPricingExternal:
    def test_pricing_loads_from_builtin(self):
        from tokenledger.core.pricing import PricingRegistry
        pr = PricingRegistry()
        assert pr.has_model("openai", "gpt-4o")
        assert not pr.has_model("openai", "nonexistent")

    def test_pricing_from_file(self):
        import json
        import tempfile

        from tokenledger.core.pricing import PricingRegistry
        data = {
            "_meta": {"version": 1, "last_updated": "2026-07-26"},
            "testprov": {"testmodel": {"input_per_1k": 1.0, "output_per_1k": 2.0}},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(data, f)
            path = f.name
        try:
            pr = PricingRegistry(pricing_file=path)
            assert pr.has_model("testprov", "testmodel")
            p = pr.get_pricing("testprov", "testmodel")
            assert p["input_per_token"] == 0.001
            assert p["output_per_token"] == 0.002
        finally:
            os.unlink(path)

    def test_pricing_last_updated(self):
        import json
        import tempfile

        from tokenledger.core.pricing import PricingRegistry
        data = {
            "_meta": {"version": 1, "last_updated": "2026-07-26"},
            "testprov": {"testmodel": {"input_per_1k": 0.5, "output_per_1k": 0.5}},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(data, f)
            path = f.name
        try:
            pr = PricingRegistry(pricing_file=path)
            assert pr.get_last_updated() == "2026-07-26"
        finally:
            os.unlink(path)

    def test_update_pricing_cli(self):
        import json
        import tempfile

        from tokenledger.__main__ import cmd_update_pricing
        data = {"_meta": {"version": 1}, "clitest": {"m1": {"input_per_1k": 0.1, "output_per_1k": 0.2}}}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(data, f)
            path = f.name
        try:
            class Args:
                file = path
            cmd_update_pricing(Args())
        finally:
            os.unlink(path)

    def test_pricing_golden_values_per_1m(self):
        from tokenledger.core.pricing import PricingRegistry
        pr = PricingRegistry()
        assert pr.calculate_cost("openai", "gpt-4o", 1_000_000, 0) == pytest.approx(5.0)
        assert pr.calculate_cost("openai", "gpt-4o", 0, 1_000_000) == pytest.approx(15.0)
        assert pr.calculate_cost("openai", "gpt-4o-mini", 1_000_000, 0) == pytest.approx(0.15)
        assert pr.calculate_cost("mistral", "mistral-large", 1_000_000, 0) == pytest.approx(2.0)
        assert pr.calculate_cost("mistral", "mistral-large", 0, 1_000_000) == pytest.approx(6.0)
        assert pr.calculate_cost("cohere", "command-r-plus", 1_000_000, 0) == pytest.approx(2.5)
        assert pr.calculate_cost("cohere", "command-r-plus", 0, 1_000_000) == pytest.approx(10.0)

    def test_pricing_per_1m_file_format(self):
        import tempfile

        from tokenledger.core.pricing import PricingRegistry
        data = {
            "_meta": {"version": 2, "unit": "usd_per_1m"},
            "testprov": {"testmodel": {"input_per_1m": 2.0, "output_per_1m": 6.0}},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(data, f)
            path = f.name
        try:
            pr = PricingRegistry(pricing_file=path)
            assert pr.calculate_cost("testprov", "testmodel", 1_000_000, 0) == pytest.approx(2.0)
            assert pr.calculate_cost("testprov", "testmodel", 0, 1_000_000) == pytest.approx(6.0)
        finally:
            os.unlink(path)

    def test_pricing_validates_suspicious_rates(self, caplog):
        import logging
        import tempfile

        from tokenledger.core.pricing import PricingRegistry
        data = {"foo": {"bar": {"input_per_1m": 99999.0, "output_per_1m": 1.0}}}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(data, f)
            path = f.name
        try:
            with caplog.at_level(logging.WARNING, logger="tokenledger.core.pricing"):
                PricingRegistry(pricing_file=path)
            assert any("foo:bar" in r.message for r in caplog.records)
        finally:
            os.unlink(path)


class TestTrackDecorator:
    def test_track_decorator_records_usage(self):
        from tokenledger import TokenLedger
        ledger = TokenLedger()

        @ledger.track(provider="test", model="t1", user_id="u1", project_id="p1",
                       input_tokens=10, output_tokens=5)
        def my_func():
            return "ok"

        result = my_func()
        assert result == "ok"
        records = ledger.get_records()
        assert len(records) == 1
        assert records[0]["input_tokens"] == 10
        assert records[0]["output_tokens"] == 5
        assert records[0]["user_id"] == "u1"
        assert records[0]["project_id"] == "p1"

    def test_track_decorator_extracts_from_response(self):
        from unittest.mock import MagicMock

        from tokenledger import TokenLedger
        ledger = TokenLedger()

        class FakeResponse:
            usage = MagicMock(prompt_tokens=7, completion_tokens=3)

        @ledger.track(provider="openai", model="gpt-4")
        def call_api():
            return FakeResponse()

        call_api()
        records = ledger.get_records()
        assert len(records) == 1
        assert records[0]["input_tokens"] == 7
        assert records[0]["output_tokens"] == 3


class TestRicherSummary:
    def test_summary_includes_budgets(self):
        from tokenledger import TokenLedger
        ledger = TokenLedger()
        ledger.set_budget("user", "alice", limit_usd=10.0)
        ledger.record_usage(provider="test", model="t1", input_tokens=10, output_tokens=5,
                             user_id="alice")
        summary = ledger.get_summary()
        assert "budget_utilization" in summary
        assert "top_models" in summary
        assert "status_breakdown" in summary
        assert "anomalies" in summary

    def test_summary_top_models(self):
        from tokenledger import TokenLedger
        ledger = TokenLedger()
        for i in range(3):
            ledger.record_usage(provider="test", model=f"m{i}", input_tokens=10, output_tokens=5)
        summary = ledger.get_summary()
        assert len(summary["top_models"]) == 3


class TestSqliteStore:
    def test_insert_and_retrieve(self):
        import tempfile

        from tokenledger.ext.sqlite_store import SqliteStore
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db = f.name
        try:
            store = SqliteStore(db)
            store.insert_record({"record_id": "r1", "provider": "test", "model": "t1",
                                  "input_tokens": 10, "output_tokens": 5,
                                  "total_tokens": 15, "cost_usd": 0.001,
                                  "timestamp": "2026-07-26T00:00:00"})
            records = store.get_records()
            assert len(records) == 1
            assert records[0]["record_id"] == "r1"
        finally:
            store.close()
            os.unlink(db)

    def test_running_totals(self):
        import tempfile

        from tokenledger.ext.sqlite_store import SqliteStore
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db = f.name
        try:
            store = SqliteStore(db)
            store.insert_record({"record_id": "r1", "provider": "test", "model": "t1",
                                  "input_tokens": 10, "output_tokens": 5,
                                  "total_tokens": 15, "cost_usd": 0.001,
                                  "timestamp": "2026-07-26T00:00:00"})
            totals = store.get_running_totals("provider", "test")
            assert totals["requests"] == 1
            assert totals["total_tokens"] == 15
        finally:
            store.close()
            os.unlink(db)

    def test_get_record_count(self):
        import tempfile

        from tokenledger.ext.sqlite_store import SqliteStore
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db = f.name
        try:
            store = SqliteStore(db)
            assert store.get_record_count() == 0
            store.insert_record({"record_id": "r1", "provider": "test", "model": "t1",
                                  "input_tokens": 1, "output_tokens": 1,
                                  "total_tokens": 2, "cost_usd": 0.0,
                                  "timestamp": "2026-07-26T00:00:00"})
            assert store.get_record_count() == 1
        finally:
            store.close()
            os.unlink(db)

    def test_compact_removes_only_old(self):
        import tempfile

        from tokenledger.ext.sqlite_store import SqliteStore
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db = f.name
        try:
            store = SqliteStore(db)
            now = datetime.now(timezone.utc)
            store.insert_record({"record_id": "old", "provider": "test", "model": "t1",
                                  "input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
                                  "cost_usd": 0.0,
                                  "timestamp": (now - timedelta(days=200)).isoformat()})
            store.insert_record({"record_id": "new", "provider": "test", "model": "t1",
                                  "input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
                                  "cost_usd": 0.0,
                                  "timestamp": now.isoformat()})
            result = store.compact()
            assert result["removed"] == 1
            assert result["remaining"] == 1
            assert store.get_records()[0]["record_id"] == "new"
        finally:
            store.close()
            os.unlink(db)

    def test_compact_respects_max_age_days(self):
        import tempfile

        from tokenledger.ext.sqlite_store import SqliteStore
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db = f.name
        try:
            store = SqliteStore(db)
            now = datetime.now(timezone.utc)
            for i, days in enumerate([2, 30, 200]):
                store.insert_record({"record_id": f"r{i}", "provider": "test", "model": "t1",
                                      "input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
                                      "cost_usd": 0.0,
                                      "timestamp": (now - timedelta(days=days)).isoformat()})
            result = store.compact(max_age_days=7)
            assert result["removed"] == 2
            assert store.get_record_count() == 1
        finally:
            store.close()
            os.unlink(db)

    def test_compact_caps_max_records(self):
        import tempfile

        from tokenledger.ext.sqlite_store import SqliteStore
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db = f.name
        try:
            store = SqliteStore(db, max_records=5)
            now = datetime.now(timezone.utc)
            for i in range(10):
                store.insert_record({"record_id": f"r{i}", "provider": "test", "model": "t1",
                                      "input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
                                      "cost_usd": 0.0,
                                      "timestamp": (now - timedelta(days=i)).isoformat()})
            result = store.compact()
            assert result["removed"] == 5
            assert store.get_record_count() == 5
        finally:
            store.close()
            os.unlink(db)

    def test_budgets_persisted_across_reopen(self):
        import tempfile

        from tokenledger.ext.sqlite_store import SqliteStore
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db = f.name
        try:
            store1 = SqliteStore(db)
            store1.set_budget("user", "alice", {"scope": "user", "scope_id": "alice",
                                                 "limit_usd": 5.0, "reset_cycle": "monthly"})
            store2 = SqliteStore(db)
            budget = store2.get_budget("user", "alice")
            assert budget is not None
            assert budget["limit_usd"] == 5.0
            assert budget["reset_cycle"] == "monthly"
        finally:
            store1.close()
            store2.close()
            os.unlink(db)

    def test_clear_removes_budgets(self):
        import tempfile

        from tokenledger.ext.sqlite_store import SqliteStore
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db = f.name
        try:
            store = SqliteStore(db)
            store.set_budget("user", "alice", {"scope": "user", "scope_id": "alice",
                                                 "limit_usd": 5.0, "reset_cycle": "monthly"})
            store.clear()
            assert store.get_budget("user", "alice") is None
            store2 = SqliteStore(db)
            assert store2.get_all_budgets() == {}
        finally:
            store.close()
            store2.close()
            os.unlink(db)


class TestP2Fixes:
    """Regression tests for the remaining audit findings (H1/H2/H4/H5/H6, M5-M14)."""

    # ── H5: decorator ──────────────────────────────────────────────────

    def test_track_decorator_does_not_mutate_kwargs(self):
        from tokenledger import TokenLedger
        ledger = TokenLedger()

        @ledger.track(provider="test", model="t1", input_tokens=10, output_tokens=5)
        def my_func():
            return "ok"

        my_func()
        my_func()
        records = ledger.get_records()
        assert len(records) == 2
        assert records[0]["input_tokens"] == 10
        assert records[1]["input_tokens"] == 10

    def test_track_decorator_async(self):
        import asyncio

        from tokenledger import TokenLedger
        ledger = TokenLedger()

        @ledger.track(provider="test", model="t1", input_tokens=3, output_tokens=1)
        async def my_async_func():
            return "ok"

        asyncio.run(my_async_func())
        records = ledger.get_records()
        assert len(records) == 1
        assert records[0]["input_tokens"] == 3
        assert records[0]["latency_ms"] >= 0

    def test_track_decorator_records_latency(self):
        from tokenledger import TokenLedger
        ledger = TokenLedger()

        @ledger.track(provider="test", model="t1", input_tokens=3, output_tokens=1)
        def my_func():
            return "ok"

        my_func()
        assert ledger.get_records()[0]["latency_ms"] >= 0

    # ── H2: Laplace DP ────────────────────────────────────────────────

    def test_laplace_sample_centered_with_heavy_tails(self):
        from tokenledger.core.ledger import TokenLedger
        samples = [TokenLedger._laplace_sample(1.0) for _ in range(5000)]
        mean = sum(samples) / len(samples)
        heavy_tail = sum(1 for s in samples if abs(s) > 1.5) / len(samples)
        assert abs(mean) < 0.1
        assert 0.10 < heavy_tail < 0.35  # P(|X|>1.5) = exp(-1.5) ~= 0.22

    # ── M13: estimator output ─────────────────────────────────────────

    def test_estimator_no_invented_output(self):
        from tokenledger.core.estimator import TokenEstimator
        est = TokenEstimator()
        result = est.estimate(messages=[{"role": "user", "content": "hello world"}], model="m", provider="generic")
        assert result["output_tokens"] == 0
        assert result["source"] == "estimated"

    def test_estimator_measures_output_text(self):
        from tokenledger.core.estimator import TokenEstimator
        est = TokenEstimator()
        result = est.estimate(messages=[{"role": "user", "content": "hello"}], model="m", provider="generic",
                              output_text="a much longer streamed response text than the prompt")
        assert result["output_tokens"] > 0

    # ── M5: unknown_model_policy on manual path ───────────────────────

    def test_manual_policy_block_raises(self):
        import pytest

        from tokenledger import TokenLedger
        from tokenledger.core.interceptor import UnknownModelError
        ledger = TokenLedger(unknown_model_policy="block")
        with pytest.raises(UnknownModelError):
            ledger.record_usage("unknown", "unknown-model", 10, 5)

    def test_manual_policy_allow_zeroes_cost(self):
        from tokenledger import TokenLedger
        ledger = TokenLedger(unknown_model_policy="allow")
        r = ledger.record_usage("unknown", "unknown-model", 10, 5)
        assert r["cost_usd"] == 0.0

    # ── H4: stream fallback ───────────────────────────────────────────

    def test_stream_without_usage_still_records(self):
        import time

        from tokenledger import TokenLedger
        ledger = TokenLedger()

        class FakeChunk:
            def __init__(self, text):
                self.delta = text

        class FakeStream:
            def __iter__(self):
                yield FakeChunk("Hello ")
                yield FakeChunk("world")

        metadata = {
            "user_id": "u1", "project_id": "p1", "model": "gpt-4",
            "conversation_id": None, "agent_id": None, "prompt_hash": None, "tenant_id": None,
        }
        wrapped = ledger.interceptor._handle_stream(
            FakeStream(), "openai", metadata, [{"role": "user", "content": "hi"}], time.monotonic()
        )
        list(wrapped)
        records = ledger.get_records()
        assert len(records) == 1
        assert records[0]["source"] == "stream_fallback_estimated"
        assert records[0]["output_tokens"] > 0  # estimated from streamed delta text

    def test_stream_fallback_flagged_estimated(self):
        import time

        from tokenledger import TokenLedger
        ledger = TokenLedger()

        class FakeChunk:
            def __init__(self, text):
                self.delta = text

        class FakeStream:
            def __iter__(self):
                yield FakeChunk("Hello ")
                yield FakeChunk("world")

        metadata = {
            "user_id": "u1", "project_id": "p1", "model": "gpt-4o",
            "conversation_id": None, "agent_id": None, "prompt_hash": None, "tenant_id": None,
        }
        wrapped = ledger.interceptor._handle_stream(
            FakeStream(), "openai", metadata, [{"role": "user", "content": "hi"}], time.monotonic()
        )
        list(wrapped)
        records = ledger.get_records()
        assert len(records) == 1
        assert records[0]["source"] == "stream_fallback_estimated"

    # ── H6: retry / transient detection ───────────────────────────────

    def test_is_transient_detection(self):
        from tokenledger.core.interceptor import _is_transient
        assert _is_transient(ConnectionError("boom"))
        assert _is_transient(TimeoutError("boom"))

        class RateLimitError(Exception):
            status_code = 429

        class ForbiddenError(Exception):
            status_code = 403

        assert _is_transient(RateLimitError("too many"))
        assert not _is_transient(ForbiddenError("nope"))
        assert not _is_transient(ValueError("nope"))

    def test_retry_recovers_on_transient(self):
        from unittest.mock import MagicMock

        from tokenledger import TokenLedger
        ledger = TokenLedger()
        il = ledger.interceptor
        il.retry_delay = 0.0

        class RateLimitError(Exception):
            status_code = 429

        original = MagicMock(side_effect=[RateLimitError("slow down"), "ok"])
        result = il._call_with_retry(original, (), {}, "openai")
        assert result == "ok"
        assert original.call_count == 2

    # ── M3/M4: provider wraps ─────────────────────────────────────────

    def test_wrap_cohere_v2_chat(self):
        from unittest.mock import MagicMock

        from tokenledger import TokenLedger
        ledger = TokenLedger()

        class FakeCohere:
            def __init__(self):
                self.v2 = MagicMock()
                self.v2.chat = MagicMock(
                    return_value=MagicMock(
                        meta=MagicMock(tokens=MagicMock(input_tokens=4, output_tokens=2))
                    )
                )

        client = FakeCohere()
        ledger.interceptor.wrap_cohere(client)
        client.v2.chat(messages=[{"role": "user", "content": "hi"}], model="command-r")
        assert len(ledger.get_records()) == 1

    def test_wrap_gemini_wraps_all_methods(self):
        from unittest.mock import MagicMock

        from tokenledger import TokenLedger
        ledger = TokenLedger()

        class FakeGemini:
            def __init__(self):
                self.models = MagicMock()
                self.models.generate_content = MagicMock(return_value=MagicMock(usage_metadata=None))
                self.models.generate_content_async = MagicMock()

        client = FakeGemini()
        ledger.interceptor.wrap_gemini(client)
        client.models.generate_content("hello", model="gemini-1.5-flash")
        assert len(ledger.get_records()) == 1

    def test_wrap_ollama_wraps_chat(self):
        from unittest.mock import MagicMock

        from tokenledger import TokenLedger
        ledger = TokenLedger()

        class FakeOllama:
            def __init__(self):
                self.chat = MagicMock(return_value=MagicMock(usage=MagicMock(prompt_tokens=3, completion_tokens=1)))

        client = FakeOllama()
        ledger.interceptor.wrap_ollama(client)
        client.chat(model="llama3.1", messages=[{"role": "user", "content": "hi"}])
        assert len(ledger.get_records()) == 1

    # ── M7/M12: analytics ─────────────────────────────────────────────

    def test_summary_top_models_exclude_ghost(self):
        from tokenledger import TokenLedger
        ledger = TokenLedger()
        ledger.store.insert_record({
            "record_id": "g1", "provider": "test", "model": "ghost-model", "_ghost": True,
            "input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
            "cost_usd": 1.0, "timestamp": "2026-07-26T00:00:00", "status": "success",
        })
        ledger.record_usage(provider="test", model="real-model", input_tokens=10, output_tokens=5)
        summary = ledger.get_summary()
        models = [m["model"] for m in summary["top_models"]]
        assert "ghost-model" not in models
        assert "real-model" in models

    def test_tenant_dimension_filter(self):
        from tokenledger import TokenLedger
        ledger = TokenLedger()
        ledger.record_usage(provider="test", model="t1", input_tokens=10, output_tokens=5,
                            tenant_id="tenant-a", latency_ms=100)
        ledger.record_usage(provider="test", model="t1", input_tokens=10, output_tokens=5,
                            tenant_id="tenant-b", latency_ms=200)
        stats = ledger.get_efficiency("tenant", "tenant-a")
        assert stats["total_reasoning_tokens"] == 0
        latency = ledger.analytics.get_latency_stats("tenant", "tenant-a")
        assert latency["count"] == 1
        assert latency["p50"] == 100

    # ── M10: export_audit_json ────────────────────────────────────────

    def test_export_audit_json_returns_dict(self):
        import json
        import os
        import tempfile

        from tokenledger import TokenLedger
        ledger = TokenLedger()
        ledger.record_usage(provider="test", model="t1", input_tokens=10, output_tokens=5)
        audit = ledger.export_audit_json()
        assert audit["record_count"] == 1
        assert audit["verified"] == []
        assert "_checksum" in audit
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            ledger.export_audit_json(path)
            with open(path, encoding="utf-8") as f:
                written = json.load(f)
            assert written["record_count"] == 1
        finally:
            os.unlink(path)

    # ── H1: key normalization ─────────────────────────────────────────

    def test_str_and_bytes_keys_equivalent(self):
        import os
        import tempfile

        from tokenledger.core.store import MemoryStore, _normalize_key
        assert _normalize_key("test-key") == _normalize_key(b"test-key")
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            path = f.name
        try:
            store1 = MemoryStore(persist_path=path, encryption_key="test-key")
            store1.insert_record({"record_id": "r1", "provider": "t", "model": "m",
                                  "input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
                                  "cost_usd": 0.0, "timestamp": "2026-07-26T00:00:00"})
            store2 = MemoryStore(persist_path=path, encryption_key=b"test-key")
            assert len(store2.get_records()) == 1
        finally:
            os.unlink(path)

    def test_wrong_key_loads_no_records(self):
        import os
        import tempfile

        from tokenledger.core.store import MemoryStore
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            path = f.name
        try:
            store1 = MemoryStore(persist_path=path, encryption_key="right-key")
            store1.insert_record({"record_id": "r1", "provider": "t", "model": "m",
                                  "input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
                                  "cost_usd": 0.0, "timestamp": "2026-07-26T00:00:00"})
            store2 = MemoryStore(persist_path=path, encryption_key="wrong-key")
            assert store2.get_records() == []
        finally:
            os.unlink(path)

    # ── M6/M11: SQLite ────────────────────────────────────────────────

    def test_sqlite_wal_mode(self):
        import os
        import tempfile

        from tokenledger.ext.sqlite_store import SqliteStore
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db = f.name
        try:
            store = SqliteStore(db)
            conn = store._conn()
            assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        finally:
            store.close()
            os.unlink(db)

    def test_sqlite_ghost_not_in_totals(self):
        import os
        import tempfile

        from tokenledger.ext.sqlite_store import SqliteStore
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db = f.name
        try:
            store = SqliteStore(db)
            store.insert_record({"record_id": "g1", "provider": "t", "model": "m", "_ghost": True,
                                 "input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
                                 "cost_usd": 1.0, "timestamp": "2026-07-26T00:00:00"})
            totals = store.get_running_totals("global", "all")
            assert totals["requests"] == 0
            assert totals["cost_usd"] == 0.0
        finally:
            store.close()
            os.unlink(db)

    def test_sqlite_apply_retention_alias(self):
        import os
        import tempfile
        from datetime import datetime, timedelta, timezone

        from tokenledger.ext.sqlite_store import SqliteStore
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db = f.name
        try:
            store = SqliteStore(db)
            store.insert_record({"record_id": "old", "provider": "t", "model": "m",
                                 "input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
                                 "cost_usd": 0.0,
                                 "timestamp": (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()})
            store.apply_retention(90)
            assert store.get_record_count() == 0
        finally:
            store.close()
            os.unlink(db)

    # ── M8: retention / compact on MemoryStore ───────────────────────

    def test_memory_compact_honors_max_age_days(self):
        from datetime import datetime, timedelta, timezone

        from tokenledger.core.store import MemoryStore
        store = MemoryStore(retention_days=-1)
        store.insert_record({"record_id": "old", "provider": "t", "model": "m",
                             "input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
                             "cost_usd": 0.0,
                             "timestamp": (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()})
        store.insert_record({"record_id": "new", "provider": "t", "model": "m",
                             "input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
                             "cost_usd": 0.0,
                             "timestamp": datetime.now(timezone.utc).isoformat()})
        result = store.compact(max_age_days=90)
        assert result["removed"] == 1
        assert [r["record_id"] for r in store.get_records()] == ["new"]

    # ── pricing default key ───────────────────────────────────────────

    def test_pricing_default_key_matches_registry(self):
        from tokenledger.core.pricing import PricingRegistry
        reg = PricingRegistry()
        assert reg.get_default_key() in reg.list_models() or reg.get_default_key() == "_default:unknown"
        assert reg.get_default_key() == "_default:unknown"


class TestCoverageGaps:
    """Additional tests that keep the 80% coverage gate green.

    These exercise the wrap stubs, async/stream interceptor paths,
    analytics derived stats, and store/pricing edge branches that the
    audit-focused suites did not reach.
    """

    # ── ledger wrap stubs ─────────────────────────────────────────────

    def test_all_ledger_wrap_stubs(self):
        from unittest.mock import MagicMock

        from tokenledger import TokenLedger
        ledger = TokenLedger()
        client = MagicMock()
        client.chat.completions.create = MagicMock(
            return_value=MagicMock(usage=MagicMock(prompt_tokens=1, completion_tokens=1))
        )
        for name in ("wrap_openai", "wrap_openrouter", "wrap_deepseek", "wrap_mistral",
                     "wrap_nvidia", "wrap_kimi", "wrap_glm", "wrap_minimax",
                     "wrap_together", "wrap_perplexity", "wrap_groq"):
            getattr(ledger, name)(client)
        client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])
        assert len(ledger.get_records()) >= 11  # each wrap chains a tracked fn

        an = MagicMock()
        an.messages.create = MagicMock(
            return_value=MagicMock(usage=MagicMock(input_tokens=2, output_tokens=1))
        )
        ledger.wrap_anthropic(an)
        an.messages.create(model="claude-3-5-sonnet", max_tokens=10)
        assert ledger.get_records()[-1]["input_tokens"] == 2

        ge = MagicMock()
        ge.models.generate_content = MagicMock(return_value=MagicMock(usage_metadata=None))
        ledger.wrap_gemini(ge)
        ge.models.generate_content("hi", model="gemini-1.5-flash")

        ol = MagicMock()
        ol.chat = MagicMock(return_value=MagicMock(usage=MagicMock(prompt_tokens=3, completion_tokens=1)))
        ledger.wrap_ollama(ol)
        ol.chat(model="llama3.1", messages=[{"role": "user", "content": "hi"}])

        co = MagicMock()
        co.v2.chat = MagicMock(
            return_value=MagicMock(meta=MagicMock(tokens=MagicMock(input_tokens=1, output_tokens=1)))
        )
        ledger.wrap_cohere(co)
        co.v2.chat(model="command-r", messages=[{"role": "user", "content": "hi"}])
        assert len(ledger.get_records()) >= 14

    # ── interceptor: bucket / async / circuit / stream edges ───────────

    def test_token_bucket_async_consume_and_available(self):
        import asyncio
        import time

        from tokenledger.core.interceptor import TokenBucket
        bucket = TokenBucket(rate=10.0)
        bucket.tokens = 0.5
        t0 = time.monotonic()
        bucket.consume()
        assert time.monotonic() - t0 >= 0.03
        assert 0 <= bucket.available < 1.0

        bucket2 = TokenBucket(rate=10.0)
        bucket2.tokens = 0.5
        t0 = time.monotonic()
        asyncio.run(bucket2.async_consume())
        assert time.monotonic() - t0 >= 0.03

    def test_async_openai_wrap_retries_transient(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from tokenledger import TokenLedger
        ledger = TokenLedger()
        il = ledger.interceptor
        il.retry_delay = 0.0
        calls = {"n": 0}

        class RateLimitedError(Exception):
            status_code = 429

        class FakeClient:
            def __init__(self):
                self.chat = SimpleNamespace(completions=self)

            async def create(self, model="gpt-4o", messages=(), **kwargs):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RateLimitedError("slow down")
                return MagicMock(usage=MagicMock(prompt_tokens=3, completion_tokens=2))

        client = FakeClient()
        ledger.wrap_openai(client)
        asyncio.run(client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}],
                                                   user_id="u1"))
        assert calls["n"] == 2
        records = ledger.get_records()
        assert len(records) == 1
        assert records[0]["input_tokens"] == 3

    def test_async_track_failure_raises_immediately(self):
        import asyncio
        from types import SimpleNamespace

        import pytest

        from tokenledger import TokenLedger
        ledger = TokenLedger()
        il = ledger.interceptor
        il.max_retries = 2
        il.retry_delay = 0.0

        class FakeClient:
            def __init__(self):
                self.chat = SimpleNamespace(completions=self)

            async def create(self, model="gpt-4o", messages=(), **kwargs):
                raise ValueError("hard failure")

        client = FakeClient()
        ledger.wrap_openai(client)
        with pytest.raises(ValueError):
            asyncio.run(client.chat.completions.create(model="gpt-4o", messages=[]))
        assert il._circuit_state["openai"]["failures"] == 1

    def test_async_stream_track_records_on_exhaustion(self):
        import asyncio
        import time

        from tokenledger import TokenLedger
        ledger = TokenLedger()

        class FakeDelta:
            def __init__(self, text):
                self.content = text

        class FakeChunk:
            def __init__(self, text):
                self.delta = FakeDelta(text)

        class FakeStream:
            async def __aiter__(self):
                yield FakeChunk("Hello ")
                yield FakeChunk("async world")

        metadata = {
            "user_id": "u1", "project_id": "p1", "model": "gpt-4",
            "conversation_id": None, "agent_id": None, "prompt_hash": None, "tenant_id": None,
        }
        wrapped = ledger.interceptor._handle_stream(
            FakeStream(), "openai", metadata, [{"role": "user", "content": "hi"}], time.monotonic()
        )

        async def drain():
            async for _ in wrapped:
                pass

        asyncio.run(drain())
        records = ledger.get_records()
        assert len(records) == 1
        assert records[0]["source"] == "stream_fallback_estimated"
        assert records[0]["output_tokens"] > 0

    def test_circuit_breaker_opens_half_opens_and_health(self):
        import contextlib
        import time
        from types import SimpleNamespace

        from tokenledger import TokenLedger
        from tokenledger.core.interceptor import CircuitBreakerOpenError
        ledger = TokenLedger()
        il = ledger.interceptor
        il.circuit_breaker_threshold = 2
        il.circuit_recovery_timeout = 60.0

        class FakeClient:
            def __init__(self):
                self.chat = SimpleNamespace(completions=self)

            def create(self, model="gpt-4o", messages=(), **kwargs):
                raise ConnectionError("down")

        client = FakeClient()
        ledger.wrap_openai(client)
        with contextlib.suppress(ConnectionError):
            client.create(model="gpt-4o", messages=[])
        with contextlib.suppress(ConnectionError):
            client.create(model="gpt-4o", messages=[])
        health = il.get_health()
        assert health["openai"]["circuit_state"] == "open"
        assert health["openai"]["circuit_failures"] == 2
        assert health["openai"]["circuit_recovery_seconds"] > 0
        assert health["openai"]["rate_limit_available_tokens"] >= 0
        try:
            client.create(model="gpt-4o", messages=[])
            raise AssertionError("expected circuit breaker to reject the call")
        except CircuitBreakerOpenError:
            pass

        il._circuit_state["openai"] = {
            "state": "open", "failures": 1, "since": time.monotonic() - 120,
        }
        il._check_circuit("openai")
        assert il._circuit_state["openai"]["state"] == "half-open"

    def test_ghost_budget_and_blocked_record_callback(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        import pytest

        from tokenledger import TokenLedger
        from tokenledger.core.budget import BudgetExceededError
        callbacks: list = []
        ledger = TokenLedger(ghost_mode=True)
        ledger.interceptor.on_budget_exceeded = lambda e: callbacks.append(str(e))
        ledger.set_budget("global", "all", 0.0, reset_cycle="never")

        class FakeClient:
            def __init__(self):
                self.chat = SimpleNamespace(completions=self)

            def create(self, model="gpt-4o", messages=(), **kwargs):
                return MagicMock(usage=MagicMock(prompt_tokens=3, completion_tokens=2))

        client = FakeClient()
        ledger.wrap_openai(client)
        client.create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])
        assert ledger.get_records()[-1]["_ghost"] is True
        assert callbacks == []

        strict = TokenLedger(ghost_mode=False)
        strict.interceptor.retry_delay = 0.0
        strict.interceptor.on_budget_exceeded = lambda e: callbacks.append("budget-blocked")
        strict.set_budget("global", "all", 0.0, reset_cycle="never")
        strict.wrap_openai(FakeClient())
        with pytest.raises(BudgetExceededError):
            strict.interceptor._track_request(
                FakeClient().create, "openai", (), {"model": "gpt-4o", "messages": []}
            )
        blocked = strict.get_records()
        assert len(blocked) == 1
        assert blocked[0]["status"] == "blocked"
        assert callbacks[-1] == "budget-blocked"
        assert strict.store.get_running_totals("global", "all")["requests"] == 0

    def test_policy_allow_dp_and_redaction_on_wrap_path(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        import pytest

        from tokenledger import TokenLedger
        from tokenledger.core.interceptor import UnknownModelError

        class FakeClient:
            def __init__(self):
                self.chat = SimpleNamespace(completions=self)

            def create(self, model="nope-9000", messages=(), **kwargs):
                return MagicMock(usage=MagicMock(prompt_tokens=1, completion_tokens=1))

        blocking = TokenLedger(unknown_model_policy="block")
        blocking.wrap_openai(FakeClient())
        with pytest.raises(UnknownModelError):
            blocking.interceptor._track_request(
                FakeClient().create, "openai", (), {"model": "nope-9000", "messages": []}
            )

        allowing = TokenLedger(unknown_model_policy="allow")
        allowing.wrap_openai(FakeClient())
        record = allowing.interceptor._track_request(
            FakeClient().create, "openai", (), {"model": "nope-9000", "messages": []}
        )
        assert record is not None
        assert allowing.get_records()[-1]["cost_usd"] == 0.0

        dp = TokenLedger(differential_privacy_epsilon=1.0, redact_prompts=True)
        dp.wrap_openai(FakeClient())
        dp.interceptor._track_request(
            FakeClient().create, "openai", (),
            {"model": "gpt-4o", "messages": [{"role": "user", "content": "secret prompt"}], "user_id": "u1"},
        )
        stored = dp.get_records()[-1]
        assert stored.get("_dp_noise_applied") is None
        assert dp.get_records(apply_dp=True)[-1]["_dp_noise_applied"] is True
        assert stored["prompt_hash"]

    def test_on_record_callback_and_unwrap(self):
        from unittest.mock import MagicMock

        from tokenledger import TokenLedger
        seen: list = []
        ledger = TokenLedger()
        ledger.interceptor.on_record = lambda r: seen.append(r["record_id"])
        client = MagicMock()
        client.chat.completions.create = MagicMock(
            return_value=MagicMock(usage=MagicMock(prompt_tokens=1, completion_tokens=1))
        )
        ledger.wrap_openai(client)
        client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])
        assert len(seen) == 1

        class OldChat:
            @staticmethod
            def create():
                return "original"

        co = MagicMock()
        co.v2.chat = OldChat.create
        ledger.wrap_cohere(co)
        ledger.interceptor.unwrap(co)
        assert co.v2.chat() == "original"

    def test_wrap_warnings_on_missing_methods(self, caplog):
        import logging

        from tokenledger import TokenLedger
        ledger = TokenLedger()
        with caplog.at_level(logging.WARNING, logger="tokenledger.core.interceptor"):
            ledger.wrap_gemini(object())
            ledger.wrap_ollama(object())
            ledger.wrap_cohere(object())
        assert len(caplog.records) >= 3

    # ── analytics: derived stats ──────────────────────────────────────

    def test_analytics_trend_latency_breakdown_and_utilization(self):
        import pytest

        from tokenledger import TokenLedger
        ledger = TokenLedger()
        for i in range(3):
            ledger.record_usage(
                provider="test", model="t1", input_tokens=10 + i, output_tokens=5,
                user_id="u1", project_id="p1", latency_ms=100 + i * 10, cache_hit=(i == 0),
            )
        trend = ledger.analytics.get_trend("model", "t1", "day")
        assert len(trend) >= 1
        assert trend[0]["requests"] == 3

        latency = ledger.analytics.get_latency_stats("user", "u1")
        assert latency["count"] == 3
        assert latency["p50"] in (110, 120, 130)

        ledger.set_budget("user", "u1", 5.0, reset_cycle="monthly")
        util = ledger.analytics.get_budget_utilization("user", "u1")
        assert util is not None
        assert util["spent_usd"] > 0
        assert util["reset_cycle"] == "monthly"
        assert ledger.analytics.get_budget_utilization("user", "nobody") is None

        efficiency = ledger.analytics.get_efficiency_stats("model", "t1")
        assert efficiency["avg_efficiency"] > 0
        assert efficiency["cache_hit_rate"] == pytest.approx(1 / 3, abs=0.001)
        assert efficiency["total_reasoning_tokens"] == 0

        breakdown = ledger.analytics.get_cost_breakdown(ledger.get_records())
        assert breakdown["cached"] > 0
        assert breakdown["completion"] > 0

        ledger.set_budget("user_project", "u1:p1", 5.0)
        up = ledger.analytics.get_budget_utilization("user_project", "u1:p1")
        assert up is not None
        assert up["spent_usd"] > 0

    # ── ledger scope matching ─────────────────────────────────────────

    def test_scope_match_conversation_agent_tenant(self):
        from tokenledger import TokenLedger
        ledger = TokenLedger()
        ledger.record_usage(provider="p", model="m", input_tokens=1, output_tokens=1,
                            conversation_id="c1", agent_id="a1", tenant_id="t1", latency_ms=10)
        assert ledger.get_roi("conversation", "c1")["total_requests"] == 1
        assert ledger.get_roi("agent", "a1")["total_requests"] == 1
        assert ledger.get_roi("tenant", "t1")["total_requests"] == 1
        assert ledger.get_roi("conversation", "other")["total_requests"] == 0

    # ── extractor: usage variants ─────────────────────────────────────

    def test_extractor_provider_variants(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from tokenledger.core.extractor import TokenExtractor
        ex = TokenExtractor()
        assert ex.extract(MagicMock(usage=None), "openai") is None
        assert ex.extract(MagicMock(spec=[]), "openai") is None
        assert ex.extract(MagicMock(usage=MagicMock(input_tokens=3, output_tokens=2)), "anthropic")["input_tokens"] == 3
        assert ex.extract(MagicMock(usage=None), "anthropic") is None
        assert ex.extract(
            MagicMock(usage_metadata=MagicMock(prompt_token_count=4, candidates_token_count=1, total_token_count=5)),
            "google",
        )["output_tokens"] == 1
        assert ex.extract(MagicMock(usage_metadata=None), "google") is None
        assert ex.extract(
            MagicMock(usage=MagicMock(prompt_tokens=2, completion_tokens=2)), "groq"
        )["input_tokens"] == 2
        assert ex.extract(
            MagicMock(usage=MagicMock(prompt_tokens=2, completion_tokens=2, total_tokens=4)), "openrouter"
        )["total_tokens"] == 4
        assert ex.extract(MagicMock(usage=None, eval_count=7, prompt_eval_count=3), "ollama")["output_tokens"] == 7
        assert ex.extract(
            MagicMock(meta=MagicMock(tokens=MagicMock(input_tokens=2, output_tokens=1))), "cohere"
        )["total_tokens"] == 3
        assert ex.extract(MagicMock(meta=None), "cohere") is None
        assert ex.extract(
            SimpleNamespace(usage=SimpleNamespace(input_tokens=2, output_tokens=1)), "weird"
        )["input_tokens"] == 2
        assert ex.extract(SimpleNamespace(), "weird") is None
        assert ex.extract(MagicMock(spec=[]), "google") is None
        assert ex.extract(MagicMock(spec=[]), "cohere") is None

    # ── store: disk and edge branches ─────────────────────────────────

    def test_memory_store_disk_edges(self):
        import asyncio
        import hashlib
        import json
        import os
        import tempfile

        from tokenledger.core.store import MemoryStore

        async def async_roundtrip(store):
            await store.async_insert_record({"record_id": "ar", "provider": "t", "model": "m",
                                             "input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
                                             "cost_usd": 0.0, "timestamp": "2026-07-26T00:00:00"})
            return await store.async_get_records()

        store = MemoryStore()
        asyncio.run(async_roundtrip(store))
        assert len(store.get_records()) == 1

        with tempfile.TemporaryDirectory() as d:
            dir_store = MemoryStore(persist_path=d)  # load from a directory -> OSError swallowed
            dir_store.insert_record({"record_id": "r1", "provider": "t", "model": "m",
                                     "input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
                                     "cost_usd": 0.0, "timestamp": "2026-07-26T00:00:00"})
            assert len(dir_store.get_records()) == 1

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            path = f.name
        try:
            if os.path.lexists(path + ".bak"):
                os.remove(path + ".bak")
            os.mkdir(path + ".bak")  # rewrite's os.replace target is a directory -> OSError
            store = MemoryStore(persist_path=path, retention_days=0)
            store.insert_record({"record_id": "r1", "provider": "t", "model": "m",
                                 "input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
                                 "cost_usd": 0.0, "timestamp": "2026-07-26T00:00:00"})
            store.compact()
            assert store.get_record_count() == 0
        finally:
            if os.path.isdir(path + ".bak"):
                os.rmdir(path + ".bak")
            if os.path.exists(path):
                os.unlink(path)

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            path = f.name
        try:
            tampered = hashlib.sha256(b"tampered").hexdigest()
            valid = json.dumps({"record_id": "r2", "provider": "t", "model": "m",
                                "input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
                                "cost_usd": 0.0, "timestamp": "2026-07-26T00:00:00"})
            good = hashlib.sha256(valid.encode()).hexdigest()
            with open(path, "w", encoding="utf-8") as f:
                f.write("not-json-at-all\n")               # garbage -> skipped
                f.write(tampered + ":" + valid + "\n")     # checksum mismatch -> skipped
                f.write(good + ":" + valid + "\n")         # verified line -> ingested
                f.write(valid + "\n")                      # legacy plain line -> ingested
            store = MemoryStore(persist_path=path)
            ids = [r["record_id"] for r in store.get_records()]
            assert ids == ["r2", "r2"]

            good_checksum = hashlib.sha256(
                json.dumps(store.records[-1], sort_keys=True, default=str).encode()
            ).hexdigest()
            store.records[-1]["_checksum"] = good_checksum
            assert store.verify_immutability() == []      # record without checksum + valid one
            store.records[-1]["_checksum"] = "0" * 64
            assert store.verify_immutability() == ["r2"]
            store.clear()
            assert store.get_records() == []
            assert store.get_all_budgets() == {}
        finally:
            os.unlink(path)

    def test_storage_backend_default_apply_retention(self):
        from tokenledger.core.store import StorageBackend

        class Dummy(StorageBackend):
            def __init__(self):
                self.compacted = 0

            def insert_record(self, record):
                pass

            def get_records(self):
                return []

            def get_running_totals(self, scope, scope_id):
                return {}

            def set_budget(self, scope, scope_id, budget_config):
                pass

            def get_budget(self, scope, scope_id):
                return None

            def get_all_budgets(self):
                return {}

            def clear(self):
                pass

            def compact(self, max_age_days=None):
                self.compacted += 1
                return {}

            def get_record_count(self):
                return 0

            def verify_immutability(self):
                return []

            async def async_insert_record(self, record):
                pass

            async def async_get_records(self):
                return []

        dummy = Dummy()
        dummy.apply_retention(30)
        assert dummy.compacted == 1

    # ── pricing: file meta, errors, and fallbacks ─────────────────────

    def test_pricing_file_meta_errors_and_fallbacks(self):
        import json
        import os
        import tempfile

        import tokenledger.core.pricing as pr
        reg = pr.PricingRegistry()
        assert reg.list_models("openai")
        assert all(k.startswith("openai:") for k in reg.list_models("openai"))

        reg._registry.clear()
        assert reg.get_pricing("nope", "nope")["input_per_token"] == 0.0
        assert reg.get_default_key() == "default:unknown"

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "_meta": {"unit": "usd_per_1m", "last_updated": "2026-08-01"},
                    "acme": {"a1": {"input": 1.0, "output": 2.0}},
                }, f)
            reg2 = pr.PricingRegistry(pricing_file=path)
            assert reg2.get_last_updated() == "2026-08-01"
            assert reg2.get_pricing("acme", "a1")["input_per_token"] == 1e-6
        finally:
            os.unlink(path)

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            bad = f.name
        try:
            with open(bad, "w", encoding="utf-8") as f:
                f.write("{not json")
            pr.PricingRegistry(pricing_file=bad)  # warns, no crash
            with open(bad, "w", encoding="utf-8") as f:
                json.dump({"acme": {"a1": {"input_per_1k": -1, "output_per_1k": 1}}}, f)
            pr.PricingRegistry(pricing_file=bad)  # negative rate -> ValueError swallowed
            pr.PricingRegistry(pricing_file=bad + ".missing")  # missing file -> warn
        finally:
            os.unlink(bad)

    def test_pricing_empty_lookup_and_high_rate_warning(self, monkeypatch):
        import tokenledger.core.pricing as pr
        monkeypatch.setattr(pr, "_find_pricing_file", lambda: None)
        reg = pr.PricingRegistry()
        assert reg.get_default_key() == "_default:unknown"
        reg.register_custom("acme", "a2", 1000.0, 1000.0)  # $1/1K -> $1000/1M -> warning
        assert reg.get_pricing("acme", "a2")["input_per_token"] == 1.0
