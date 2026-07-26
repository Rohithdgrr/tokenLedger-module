"""Tests for TokenLedger CLI, OpenTelemetry, and WebhookNotifier extensions."""

import json
import os
import tempfile
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
        from tokenledger.ext.opentelemetry import instrument_ledger, _HAS_OTEL
        import tokenledger.ext.opentelemetry as otel_mod
        if not _HAS_OTEL:
            with patch.object(otel_mod, "_HAS_OTEL", False):
                with pytest.raises(ImportError):
                    instrument_ledger(None)

    def test_instrument_sets_callback(self):
        from tokenledger import TokenLedger
        from tokenledger.ext.opentelemetry import instrument_ledger, _HAS_OTEL, Tracer

        if not _HAS_OTEL:
            pytest.skip("opentelemetry not installed")
            return

        ledger = TokenLedger()
        instrument_ledger(ledger)
        # callback should be set to our wrapper
        assert ledger.interceptor.on_record is not None

    def test_instrument_with_fake_tracer(self):
        from tokenledger import TokenLedger
        from tokenledger.ext.opentelemetry import instrument_ledger, _HAS_OTEL

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
        from tokenledger.ext.notifier import WebhookNotifier
        import logging
        caplog.set_level(logging.WARNING)
        n = WebhookNotifier()
        n.on_budget_exceeded(Exception("over limit"))
        assert "over limit" in caplog.text

    def test_on_budget_threshold_logs(self, caplog):
        from tokenledger.ext.notifier import WebhookNotifier
        import logging
        caplog.set_level(logging.WARNING)
        n = WebhookNotifier()
        n.on_budget_threshold("user", "alice", 8.0, 10.0)
        assert "80.0%" in caplog.text

    def test_on_record_no_cost(self):
        from tokenledger.ext.notifier import WebhookNotifier
        n = WebhookNotifier()
        n.on_record({"cost_usd": 0, "model": "t1", "total_tokens": 0})  # no-op

    def test_on_record_with_cost(self, caplog):
        from tokenledger.ext.notifier import WebhookNotifier
        import logging
        caplog.set_level(logging.WARNING)
        n = WebhookNotifier()
        n.on_record({"cost_usd": 0.5, "model": "gpt-4", "total_tokens": 100})

    def test_slack_webhook_error_logged(self, caplog):
        from tokenledger.ext.notifier import WebhookNotifier
        import logging
        caplog.set_level(logging.WARNING)
        n = WebhookNotifier(slack_url="http://localhost:1/nonexistent")
        n.on_budget_exceeded(Exception("test"))
        assert any("Slack webhook failed" in r.message for r in caplog.records)


class TestAsyncStore:
    @pytest.mark.asyncio
    async def test_async_insert_and_count(self):
        from tokenledger import TokenLedger
        ledger = TokenLedger()
        record = ledger.record_usage(
            provider="test", model="t1", input_tokens=1, output_tokens=1,
        )
        assert ledger.store.get_record_count() == 1

    @pytest.mark.asyncio
    async def test_async_compact(self):
        from tokenledger import TokenLedger
        from datetime import datetime, timezone, timedelta
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
        ledger.interceptor.on_budget_threshold = lambda s, sid, c, l: calls.append((s, sid, c, l))
        assert ledger.interceptor.on_budget_threshold is not None


class TestPricingExternal:
    def test_pricing_loads_from_builtin(self):
        from tokenledger.core.pricing import PricingRegistry
        pr = PricingRegistry()
        assert pr.has_model("openai", "gpt-4o")
        assert not pr.has_model("openai", "nonexistent")

    def test_pricing_from_file(self):
        from tokenledger.core.pricing import PricingRegistry
        import json, tempfile
        data = {"_meta": {"version": 1, "last_updated": "2026-07-26"}, "testprov": {"testmodel": {"input_per_1k": 1.0, "output_per_1k": 2.0}}}
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
        from tokenledger.core.pricing import PricingRegistry
        import json, tempfile
        data = {"_meta": {"version": 1, "last_updated": "2026-07-26"}, "testprov": {"testmodel": {"input_per_1k": 0.5, "output_per_1k": 0.5}}}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(data, f)
            path = f.name
        try:
            pr = PricingRegistry(pricing_file=path)
            assert pr.get_last_updated() == "2026-07-26"
        finally:
            os.unlink(path)

    def test_update_pricing_cli(self):
        from tokenledger.__main__ import cmd_update_pricing
        import json, tempfile
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
        from tokenledger import TokenLedger
        from unittest.mock import MagicMock
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
        from tokenledger.ext.sqlite_store import SqliteStore
        import tempfile
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
            os.unlink(db)

    def test_running_totals(self):
        from tokenledger.ext.sqlite_store import SqliteStore
        import tempfile
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
            os.unlink(db)

    def test_get_record_count(self):
        from tokenledger.ext.sqlite_store import SqliteStore
        import tempfile
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
            os.unlink(db)
