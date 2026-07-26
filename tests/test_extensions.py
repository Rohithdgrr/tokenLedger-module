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
