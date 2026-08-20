"""
Tests for the v1.5.0 feature set: live spend server, wallets,
usage context managers, logging adapter, and cost preview.
"""

import asyncio
import json
import logging
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

from tokenledger import (
    BudgetExceededError,
    LiveServer,
    TokenLedger,
    WalletExhaustedError,
    attach_log_handler,
    detach_log_handler,
)

PROMPT = "Explain differentiable privacy in one paragraph."


# ---------------------------------------------------------------------------
# Cost preview
# ---------------------------------------------------------------------------


class TestCostPreview:
    def test_known_model_estimates_cost(self):
        ledger = TokenLedger()
        preview = ledger.cost_preview([{"role": "user", "content": PROMPT}], "gpt-4o", "openai")
        assert preview["input_tokens"] > 0
        assert preview["output_tokens"] == 0
        assert preview["total_tokens"] == preview["input_tokens"] + preview["output_tokens"]
        assert preview["cost_usd"] > 0
        assert preview["source"] == "estimated"

    def test_output_text_measures_output_side(self):
        ledger = TokenLedger()
        preview = ledger.cost_preview(
            [{"role": "user", "content": PROMPT}], "gpt-4o", "openai", output_text="Short reply."
        )
        assert preview["output_tokens"] > 0

    def test_unknown_model_returns_default_pricing_not_error(self):
        ledger = TokenLedger()
        preview = ledger.cost_preview([{"role": "user", "content": "hi"}], "no-such-model-xyz", "bogus")
        assert preview["input_tokens"] > 0
        assert 0.0 <= preview["cost_usd"] < 1.0

    def test_preview_does_not_store(self):
        ledger = TokenLedger()
        ledger.cost_preview([{"role": "user", "content": PROMPT}], "gpt-4o", "openai")
        assert ledger.get_records() == []

    def test_cli_cost_command(self):
        result = subprocess.run(
            [sys.executable, "-m", "tokenledger", "cost", "hello", "--model", "gpt-4o"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0
        assert "Cost Preview" in result.stdout
        assert "Estimated Cost" in result.stdout


# ---------------------------------------------------------------------------
# Usage context managers
# ---------------------------------------------------------------------------


class TestUsageContext:
    def test_sync_success_records(self):
        ledger = TokenLedger()
        with ledger.usage("openai", "gpt-4o", messages=[{"role": "user", "content": PROMPT}]):
            pass
        records = ledger.get_records()
        assert len(records) == 1
        rec = records[0]
        assert rec["model"] == "gpt-4o"
        assert rec["status"] == "success"
        assert rec["source"] == "usage_block"
        assert rec["input_tokens"] > 0
        assert rec["latency_ms"] >= 0

    def test_sync_error_records_failure(self):
        ledger = TokenLedger()
        with pytest.raises(RuntimeError):
            with ledger.usage("openai", "gpt-4o", messages=[{"role": "user", "content": "x"}]):
                raise RuntimeError("boom")
        rec = ledger.get_records()[0]
        assert rec["status"] == "error"

    def test_async_success_records(self):
        ledger = TokenLedger()

        async def run():
            async with ledger.usage("openai", "gpt-4o", messages=[{"role": "user", "content": PROMPT}]):
                await asyncio.sleep(0.001)

        asyncio.run(run())
        rec = ledger.get_records()[0]
        assert rec["status"] == "success"
        assert rec["source"] == "usage_block"

    def test_tracking_kwargs_propagate(self):
        ledger = TokenLedger()
        with ledger.usage(
            "openai", "gpt-4o",
            messages=[{"role": "user", "content": "hi"}],
            user_id="alice", tenant_id="acme", conversation_id="c1",
        ):
            pass
        rec = ledger.get_records()[0]
        assert rec["user_id"] == "alice"
        assert rec["tenant_id"] == "acme"
        assert rec["conversation_id"] == "c1"

    def test_without_messages_records_zero_tokens(self):
        ledger = TokenLedger()
        with ledger.usage("openai", "gpt-4o"):
            pass
        rec = ledger.get_records()[0]
        assert rec["input_tokens"] == 0
        assert rec["output_tokens"] == 0
        assert rec["status"] == "success"


# ---------------------------------------------------------------------------
# Wallets
# ---------------------------------------------------------------------------


class TestWallets:
    def test_debit_within_allowance(self):
        ledger = TokenLedger()
        wallet = ledger.create_wallet("carol", limit_usd=5.0)
        assert wallet.debit("openai", "gpt-4o", input_tokens=100, output_tokens=50) is True
        assert wallet.limit == 5.0
        assert wallet.balance() == 5.0  # reserves only; spend appears after recording

    def test_spend_and_balance_track_records(self):
        ledger = TokenLedger()
        wallet = ledger.create_wallet("carol", limit_usd=5.0)
        ledger.record_usage(
            provider="openai", model="gpt-4o", input_tokens=200000, output_tokens=0, user_id="carol"
        )
        assert wallet.spend() > 0
        assert wallet.balance() == pytest.approx(5.0 - wallet.spend(), abs=1e-6)

    def test_exhausted_raises_wallet_exhausted_error(self):
        ledger = TokenLedger()
        wallet = ledger.create_wallet("carol", limit_usd=0.05)
        with pytest.raises(WalletExhaustedError) as exc:
            wallet.debit("openai", "gpt-4o", input_tokens=30000, output_tokens=0)
        assert exc.value.scope == "user"
        assert exc.value.scope_id == "carol"

    def test_refill_restores_allowance(self):
        ledger = TokenLedger()
        wallet = ledger.create_wallet("carol", limit_usd=0.01)
        with pytest.raises(WalletExhaustedError):
            wallet.debit("openai", "gpt-4o", input_tokens=30000, output_tokens=0)
        new_limit = wallet.refill(5.0)
        assert new_limit == 5.01
        assert wallet.debit("openai", "gpt-4o", input_tokens=100, output_tokens=0) is True

    def test_low_balance_fires_once(self):
        ledger = TokenLedger()
        fired = []

        def alarm(w):
            fired.append(w)

        wallet = ledger.create_wallet("carol", limit_usd=2.0, low_balance_threshold=0.5, on_low_balance=alarm)
        wallet.debit("openai", "gpt-4o", input_tokens=240000, output_tokens=0)  # est $1.20 -> remaining $0.80 < $1.00
        assert len(fired) == 1
        wallet.debit("openai", "gpt-4o", input_tokens=120000, output_tokens=0)  # still low, no re-fire
        assert len(fired) == 1

    def test_refill_rearms_low_balance_alert(self):
        ledger = TokenLedger()
        fired = []

        def alarm(w):
            fired.append(w)

        wallet = ledger.create_wallet("carol", limit_usd=5.0, low_balance_threshold=0.2, on_low_balance=alarm)
        wallet.debit("openai", "gpt-4o", input_tokens=880000, output_tokens=0)  # est $4.40 -> remaining $0.60 < $1.00
        assert len(fired) == 1
        wallet.refill(5.0)
        fired.clear()
        wallet.debit("openai", "gpt-4o", input_tokens=100000, output_tokens=0)
        assert fired == []

    def test_invalid_constructor_args(self):
        ledger = TokenLedger()
        with pytest.raises(ValueError):
            ledger.create_wallet("carol", limit_usd=0)
        with pytest.raises(ValueError):
            ledger.create_wallet("carol", limit_usd=1.0, low_balance_threshold=0.0)

    def test_negative_refill_rejected(self):
        ledger = TokenLedger()
        wallet = ledger.create_wallet("carol", limit_usd=1.0)
        with pytest.raises(ValueError):
            wallet.refill(-1.0)

    def test_reset_rearms_low_balance_alert(self):
        ledger = TokenLedger()
        fired = []

        def alarm(w):
            fired.append(w)

        wallet = ledger.create_wallet("carol", limit_usd=2.0, low_balance_threshold=0.5, on_low_balance=alarm)
        wallet.debit("openai", "gpt-4o", input_tokens=240000, output_tokens=0)
        assert len(fired) == 1
        wallet.reset()
        fired.clear()
        wallet.debit("openai", "gpt-4o", input_tokens=240000, output_tokens=0)
        assert len(fired) == 1

    def test_wallet_exhausted_is_budget_exceeded(self):
        assert issubclass(WalletExhaustedError, BudgetExceededError)


# ---------------------------------------------------------------------------
# Live server
# ---------------------------------------------------------------------------


def _read_http(port: int, path: str, timeout: float = 5.0) -> bytes:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as resp:
        return resp.read()


@pytest.fixture
def live_server():
    ledger = TokenLedger()
    server = ledger.serve(port=0)
    server.start()
    yield ledger, server
    server.stop()


class TestLiveServer:
    def test_stats_endpoint(self, live_server):
        ledger, server = live_server
        ledger.record_usage(provider="openai", model="gpt-4o", input_tokens=100, output_tokens=50)
        body = json.loads(_read_http(server.port, "/stats"))
        assert body["record_count"] == 1
        assert body["total_tokens"] == 150
        assert body["cost_usd"] > 0
        assert any(p["id"] == "openai" for p in body["providers"])
        assert body["running_totals"]["global:all"]["requests"] == 1

    def test_stream_emits_record_events(self, live_server):
        ledger, server = live_server
        events = []

        def consume():
            sock = socket.create_connection(("127.0.0.1", server.port), timeout=5)
            sock.sendall(b"GET /stream HTTP/1.1\r\nHost: x\r\n\r\n")
            buf = b""
            while b"event: record" not in buf:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
            events.append(buf)
            sock.close()

        t = threading.Thread(target=consume)
        t.start()
        time.sleep(0.3)
        ledger.record_usage(provider="openai", model="gpt-4o", input_tokens=10, output_tokens=5)
        t.join(timeout=10)
        assert not t.is_alive()
        assert b"event: record" in events[0]
        assert b"gpt-4o" in events[0]

    def test_heartbeat_keeps_stream_alive(self, live_server, monkeypatch):
        monkeypatch.setattr("tokenledger.ext.live_server._HEARTBEAT_SECONDS", 0.2)
        _, server = live_server
        sock = socket.create_connection(("127.0.0.1", server.port), timeout=5)
        sock.sendall(b"GET /stream HTTP/1.1\r\nHost: x\r\n\r\n")
        buf = b""
        sock.settimeout(5)
        deadline = time.monotonic() + 5
        while b": ping" not in buf and time.monotonic() < deadline:
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
        sock.close()
        assert b": ping" in buf

    def test_unknown_path_returns_404(self, live_server):
        _, server = live_server
        with pytest.raises(urllib.error.HTTPError):
            _read_http(server.port, "/nope")

    def test_stop_restores_original_callback(self):
        ledger = TokenLedger()
        sentinel = lambda r: None  # noqa: E731
        ledger.interceptor.on_record = sentinel
        server = LiveServer(ledger, port=0)
        server.start()
        assert ledger.interceptor.on_record is server._on_record
        server.stop()
        assert ledger.interceptor.on_record is sentinel

    def test_context_manager(self):
        ledger = TokenLedger()
        with ledger.serve(port=0) as server:
            body = json.loads(_read_http(server.port, "/stats"))
            assert body["record_count"] == 0
        assert ledger.interceptor.on_record is None

    def test_start_failure_restores_hook(self, monkeypatch):
        ledger = TokenLedger()
        prev = lambda r: None  # noqa: E731
        ledger.interceptor.on_record = prev
        server = LiveServer(ledger, port=0)

        def boom(*args, **kwargs):
            raise OSError("bind failed")

        monkeypatch.setattr("tokenledger.ext.live_server._ThreadingHTTPServer", boom)
        with pytest.raises(OSError):
            server.start()
        assert ledger.interceptor.on_record is prev

    def test_chains_previous_callback(self):
        ledger = TokenLedger()
        seen = []
        prev = lambda r: seen.append(r)  # noqa: E731
        ledger.interceptor.on_record = prev
        with ledger.serve(port=0):
            ledger.record_usage(provider="openai", model="gpt-4o", input_tokens=1, output_tokens=0)
        assert len(seen) == 1
        assert seen[0]["model"] == "gpt-4o"


# ---------------------------------------------------------------------------
# Logging adapter
# ---------------------------------------------------------------------------


class TestLoggingAdapter:
    def test_emits_structured_log_record(self, caplog):
        ledger = TokenLedger()
        with caplog.at_level(logging.INFO, logger="tokenledger.spend"):
            attach_log_handler(ledger)
            ledger.record_usage(
                provider="openai", model="gpt-4o",
                input_tokens=10, output_tokens=5, user_id="alice", latency_ms=3.0,
            )
        records = [r for r in caplog.records if r.name == "tokenledger.spend"]
        assert len(records) == 1
        rec = records[0]
        assert rec.provider == "openai"
        assert rec.model == "gpt-4o"
        assert rec.input_tokens == 10
        assert rec.user_id == "alice"

    def test_chains_previous_callback(self, caplog):
        ledger = TokenLedger()
        seen = []
        prev = lambda r: seen.append(r)  # noqa: E731
        ledger.interceptor.on_record = prev
        with caplog.at_level(logging.INFO, logger="tokenledger.spend"):
            attach_log_handler(ledger)
            ledger.record_usage(provider="openai", model="gpt-4o", input_tokens=1, output_tokens=0)
        assert len(seen) == 1
        assert len([r for r in caplog.records if r.name == "tokenledger.spend"]) == 1

    def test_detach_restores_previous(self, caplog):
        ledger = TokenLedger()
        seen = []
        prev = lambda r: seen.append(r)  # noqa: E731
        ledger.interceptor.on_record = prev
        hook = attach_log_handler(ledger)
        with caplog.at_level(logging.INFO, logger="tokenledger.spend"):
            ledger.record_usage(provider="openai", model="gpt-4o", input_tokens=1, output_tokens=0)
        detach_log_handler(ledger, hook)
        with caplog.at_level(logging.INFO, logger="tokenledger.spend"):
            ledger.record_usage(provider="openai", model="gpt-4o", input_tokens=1, output_tokens=0)
        assert len(seen) == 2
        assert len([r for r in caplog.records if r.name == "tokenledger.spend"]) == 1
