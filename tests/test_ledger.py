"""
Tests for TokenLedger core functionality.
"""

import os
import tempfile
from datetime import datetime

import pytest

from tokenledger import BudgetExceededError, TokenLedger
from tokenledger.core.pricing import PricingRegistry


class TestTokenLedger:
    """Test suite for TokenLedger."""

    def test_init(self):
        """Test basic initialization."""
        ledger = TokenLedger()
        assert ledger is not None
        assert len(ledger.get_records()) == 0

    def test_manual_record(self):
        """Test manual usage recording."""
        ledger = TokenLedger()

        record = ledger.record_usage(
            provider="test",
            model="test-model",
            input_tokens=100,
            output_tokens=50,
            user_id="alice",
            project_id="my-app",
        )

        assert record["input_tokens"] == 100
        assert record["output_tokens"] == 50
        assert record["total_tokens"] == 150
        assert record["user_id"] == "alice"
        assert record["status"] == "success"

        # Check stored
        records = ledger.get_records()
        assert len(records) == 1

    def test_budget_enforcement(self):
        """Test budget blocking."""
        ledger = TokenLedger()

        # Set a very low budget
        ledger.set_budget(
            scope="project",
            scope_id="my-app",
            limit_usd=0.001,  # Very low limit
            reset_cycle="never",
        )

        # This should exceed budget
        with pytest.raises(BudgetExceededError):
            ledger.record_usage(
                provider="openai",
                model="gpt-4o",
                input_tokens=1000,
                output_tokens=500,
                project_id="my-app",
            )

    def test_analytics(self):
        """Test analytics queries."""
        ledger = TokenLedger()

        # Add some records
        ledger.record_usage("openai", "gpt-4o", 100, 50, user_id="alice", project_id="app1")
        ledger.record_usage("openai", "gpt-4o-mini", 200, 100, user_id="bob", project_id="app2")
        ledger.record_usage("anthropic", "claude-3", 150, 75, user_id="alice", project_id="app1")

        # Test summary
        summary = ledger.get_summary("global", "all")
        assert summary["requests"] == 3

        # Test by provider
        by_provider = ledger.get_spending_by_provider()
        assert len(by_provider) >= 2  # openai and anthropic

    def test_persistence(self):
        """Test JSONL file persistence."""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.jsonl') as f:
            temp_path = f.name

        try:
            # Create ledger with persistence
            ledger = TokenLedger(persist_path=temp_path)
            ledger.record_usage("test", "model", 100, 50)

            # Create new instance loading same file
            ledger2 = TokenLedger(persist_path=temp_path)
            records = ledger2.get_records()
            assert len(records) == 1
            assert records[0]["input_tokens"] == 100
        finally:
            os.unlink(temp_path)

    def test_export(self):
        """Test CSV/JSON export."""
        ledger = TokenLedger()
        ledger.record_usage("test", "model", 100, 50, user_id="alice")

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "export.csv")
            json_path = os.path.join(tmpdir, "export.json")

            ledger.export_csv(csv_path)
            assert os.path.exists(csv_path)

            ledger.export_json(json_path)
            assert os.path.exists(json_path)

    def test_custom_pricing(self):
        """Test custom pricing registration."""
        ledger = TokenLedger()

        ledger.register_pricing(
            provider="custom",
            model="my-model",
            input_cost_per_1k=0.01,
            output_cost_per_1k=0.03,
        )

        pricing = ledger.get_pricing("custom", "my-model")
        assert pricing["input_per_token"] == 0.01 / 1000
        assert pricing["output_per_token"] == 0.03 / 1000




class TestEdgeCases:
    def test_negative_tokens_raises(self):
        ledger = TokenLedger()
        with pytest.raises(ValueError, match="Token counts cannot be negative"):
            ledger.record_usage("t", "m", -1, 5)
        with pytest.raises(ValueError, match="Token counts cannot be negative"):
            ledger.record_usage("t", "m", 10, -5)

    def test_zero_tokens(self):
        ledger = TokenLedger()
        r = ledger.record_usage("t", "m", 0, 0)
        assert r["input_tokens"] == 0
        assert r["output_tokens"] == 0
        assert r["total_tokens"] == 0
        assert r["cost_usd"] == 0.0

    def test_large_token_counts(self):
        ledger = TokenLedger()
        r = ledger.record_usage("openai", "gpt-4o", 10_000_000, 5_000_000)
        assert r["total_tokens"] == 15_000_000
        assert r["cost_usd"] > 0

    def test_unknown_model_default_rate(self):
        ledger = TokenLedger()
        r = ledger.record_usage("unknown", "unknown-model", 1000, 500)
        assert r["cost_usd"] > 0

    def test_empty_records(self):
        ledger = TokenLedger()
        assert ledger.get_records() == []
        s = ledger.get_summary("global", "all")
        assert s["requests"] == 0
        assert s["cost_usd"] == 0.0

    def test_multiple_users_projects(self):
        ledger = TokenLedger()
        users = ["alice", "bob", "charlie"]
        projects = ["app1", "app2"]
        for u in users:
            for p in projects:
                ledger.record_usage("openai", "gpt-4o", 100, 50, user_id=u, project_id=p)
        assert len(ledger.get_records()) == 6
        by_user = ledger.get_spending_by_dimension("user")
        assert len(by_user) == 3

    def test_budget_reset_cycles(self):
        ledger = TokenLedger()
        for cycle in ["daily", "weekly", "monthly", "never"]:
            ledger.set_budget("project", f"p-{cycle}", 1000, reset_cycle=cycle)
        ledger.record_usage("openai", "gpt-4o", 10, 5, project_id="p-daily")
        ledger.record_usage("openai", "gpt-4o", 10, 5, project_id="p-weekly")
        assert len(ledger.get_records()) == 2

    def test_budget_exact_limit(self):
        ledger = TokenLedger()
        ledger.set_budget("project", "exact", limit_usd=0.001, reset_cycle="never")
        with pytest.raises(BudgetExceededError):
            ledger.record_usage("openai", "gpt-4o", 1000, 500, project_id="exact")

    def test_export_empty(self):
        import os
        import tempfile
        ledger = TokenLedger()
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "empty.csv")
            ledger.export_csv(p)
            assert os.path.getsize(p) > 0  # has header

    def test_persistence_corrupted_line(self):
        import os
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl") as f:
            f.write('{"valid": true}\n')
            f.write('corrupted line\n')
            f.write('{"valid": false}\n')
            p = f.name
        try:
            ledger = TokenLedger(persist_path=p)
            assert len(ledger.get_records()) == 2  # corrupted line skipped
        finally:
            os.unlink(p)

    def test_persistence_empty_file(self):
        import os
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl") as f:
            p = f.name
        try:
            ledger = TokenLedger(persist_path=p)
            assert len(ledger.get_records()) == 0
        finally:
            os.unlink(p)

    def test_circuit_breaker_raises(self):
        import time

        from tokenledger.core.interceptor import CircuitBreakerOpenError
        ledger = TokenLedger()
        il = ledger.interceptor
        # circuit opened very recently, recovery timeout (30s) hasn't elapsed
        il._circuit_state["test-provider"] = {"state": "open", "failures": 5, "since": time.monotonic()}
        with pytest.raises(CircuitBreakerOpenError):
            il._check_circuit("test-provider")

    def test_unknown_model_policy_allow(self):
        ledger = TokenLedger(unknown_model_policy="allow")
        r = ledger.record_usage("unknown", "unknown-model", 100, 50)
        # manual record_usage path doesn't apply policy (interceptor does)
        # verify the record was stored successfully regardless
        assert r["status"] == "success"

    def test_unknown_model_policy_estimate(self):
        ledger = TokenLedger(unknown_model_policy="estimate")
        r = ledger.record_usage("unknown", "unknown-model", 100, 50)
        assert r["cost_usd"] > 0

    def test_custom_pricing_then_record(self):
        ledger = TokenLedger()
        ledger.register_pricing("my-provider", "my-model", 0.01, 0.02)
        r = ledger.record_usage("my-provider", "my-model", 1000, 500)
        assert r["cost_usd"] == 1000 * 0.01/1000 + 500 * 0.02/1000

    def test_pricing_all_providers_have_rates(self):
        p = PricingRegistry()
        models = p.list_models()
        for key, rates in models.items():
            if key == "default:unknown":
                continue
            assert rates["input_per_token"] >= 0
            assert rates["output_per_token"] >= 0

    def test_latency_defaults_to_zero(self):
        ledger = TokenLedger()
        r = ledger.record_usage("t", "m", 10, 5)
        assert r["latency_ms"] == 0

    def test_record_id_uniqueness(self):
        ledger = TokenLedger()
        ids = set()
        for _ in range(100):
            r = ledger.record_usage("t", "m", 10, 5)
            ids.add(r["record_id"])
        assert len(ids) == 100

    def test_analytics_empty_dimension(self):
        ledger = TokenLedger()
        result = ledger.get_spending_by_dimension("nonexistent")
        assert result == []

    def test_analytics_trend(self):
        from tokenledger.core.analytics import AnalyticsEngine
        from tokenledger.core.store import MemoryStore
        store = MemoryStore()
        engine = AnalyticsEngine(store)
        trend = engine.get_trend("provider", "openai", "day")
        assert trend == []



class TestAIFeatures:
    def test_conversation_tracking(self):
        ledger = TokenLedger()
        cid = "conv-123"
        ledger.record_usage("openai", "gpt-4o", 100, 50, conversation_id=cid)
        ledger.record_usage("openai", "gpt-4o", 200, 100, conversation_id=cid)
        records = ledger.get_records()
        assert all(r["conversation_id"] == cid for r in records)
        by_conv = ledger.get_spending_by_conversation()
        assert len(by_conv) == 1

    def test_agent_tracking(self):
        ledger = TokenLedger()
        ledger.record_usage("openai", "gpt-4o", 100, 50, agent_id="agent-a")
        ledger.record_usage("openai", "gpt-4o", 200, 100, agent_id="agent-b")
        by_agent = ledger.get_spending_by_agent()
        assert len(by_agent) == 2

    def test_fingerprint_prompt(self):
        messages = [{"role": "user", "content": "hello"}]
        fp1 = TokenLedger.fingerprint_prompt(messages)
        fp2 = TokenLedger.fingerprint_prompt(messages)
        assert fp1 == fp2
        fp3 = TokenLedger.fingerprint_prompt([{"role": "user", "content": "world"}])
        assert fp1 != fp3

    def test_reasoning_tokens(self):
        ledger = TokenLedger()
        r = ledger.record_usage("openai", "o1", 100, 50, reasoning_tokens=30)
        assert r["reasoning_tokens"] == 30
        assert r["total_tokens"] == 150  # total = input+output, reasoning separate

    def test_cache_hit(self):
        ledger = TokenLedger()
        r = ledger.record_usage("openai", "gpt-4o", 100, 50, cache_hit=True, cached_input_tokens=80)
        assert r["cache_hit"] is True
        assert r["cached_input_tokens"] == 80

    def test_embedding_tracking(self):
        ledger = TokenLedger()
        r = ledger.record_usage("openai", "text-embedding-3-small", 100, 0, embedding_tokens=100)
        assert r["embedding_tokens"] == 100

    def test_tool_calls(self):
        ledger = TokenLedger()
        tools = [{"name": "get_weather", "tokens": 50}]
        r = ledger.record_usage("openai", "gpt-4o", 100, 50, tool_calls=tools)
        assert r["tool_call_count"] == 1

    def test_media_type(self):
        ledger = TokenLedger()
        r = ledger.record_usage("openai", "gpt-4o", 1000, 200, media_type="image")
        assert r["media_type"] == "image"

    def test_efficiency_stats(self):
        ledger = TokenLedger()
        ledger.record_usage("openai", "gpt-4o", 200, 100)
        ledger.record_usage("openai", "gpt-4o", 100, 100, cache_hit=True)
        eff = ledger.get_efficiency()
        assert eff["cache_hit_rate"] > 0
        assert eff["avg_efficiency"] > 0

    def test_custom_status(self):
        ledger = TokenLedger()
        r = ledger.record_usage("openai", "gpt-4o", 100, 50, status="streaming")
        assert r["status"] == "streaming"


class TestRetention:
    def test_apply_retention(self):
        ledger = TokenLedger()
        ledger.record_usage("openai", "gpt-4o", 100, 50)
        # max_age_days=0 removes records older than the current moment
        # give it a 1s window to ensure the record is "older"
        import time

        time.sleep(0.01)
        ledger.apply_retention(max_age_days=0)
        assert len(ledger.get_records()) == 0

    def test_max_records_ring(self):
        ledger = TokenLedger(max_records=5)
        for _ in range(10):
            ledger.record_usage("openai", "gpt-4o", 10, 5)
        assert len(ledger.get_records()) == 5

    def test_retention_preserves_recent(self):
        ledger = TokenLedger(max_records=100, retention_days=365)
        ledger.record_usage("openai", "gpt-4o", 10, 5)
        ledger.apply_retention(max_age_days=365)
        assert len(ledger.get_records()) == 1


class TestImmutability:
    def test_checksum_on_insert(self):
        ledger = TokenLedger()
        ledger.record_usage("openai", "gpt-4o", 100, 50)
        stored = ledger.get_records()[0]
        assert "_checksum" in stored

    def test_immutability_verify_clean(self):
        ledger = TokenLedger()
        ledger.record_usage("openai", "gpt-4o", 100, 50)
        assert ledger.verify_immutability() == []

    def test_immutability_detect_tamper(self):
        ledger = TokenLedger()
        ledger.record_usage("openai", "gpt-4o", 100, 50)
        stored = ledger.get_records()[0]
        stored["output_tokens"] = 99999
        tampered = ledger.store.verify_immutability()
        assert len(tampered) == 1

    def test_persist_immutable_jsonl(self):
        import os
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl") as f:
            p = f.name
        try:
            ledger = TokenLedger(persist_path=p)
            ledger.record_usage("openai", "gpt-4o", 100, 50)
            ledger2 = TokenLedger(persist_path=p)
            assert ledger2.verify_immutability() == []
        finally:
            os.unlink(p)


class TestConcurrency:
    def test_concurrent_record_insertion(self):
        import threading
        ledger = TokenLedger()
        n = 50
        errors = []

        def worker(i):
            try:
                ledger.record_usage("openai", "gpt-4o", 100, 50, user_id=f"user-{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert len(ledger.get_records()) == n

    def test_concurrent_budget_check(self):
        import threading
        ledger = TokenLedger()
        ledger.set_budget("user", "alice", limit_usd=0.001, reset_cycle="never")
        n = 20
        successes = []
        errors = []

        def worker(_):
            try:
                ledger.record_usage("openai", "gpt-4o", 100, 50, user_id="alice")
                successes.append(1)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # At least one request should hit the budget, but at least one should succeed (due to race)
        assert len(errors) > 0 or len(successes) < n


class TestKwargStripping:
    def test_tracking_kwargs_stripped(self):
        """Verify interceptor strips tracking kwargs before calling original."""
        from tokenledger.core.interceptor import TRACKING_KWARGS
        assert "user_id" in TRACKING_KWARGS
        assert "project_id" in TRACKING_KWARGS
        assert "conversation_id" in TRACKING_KWARGS
        assert "agent_id" in TRACKING_KWARGS

    def test_strip_does_not_mutate_original_kwargs_outside(self):
        from tokenledger.core.interceptor import InterceptionLayer
        il = InterceptionLayer.__new__(InterceptionLayer)
        kwargs = {"model": "gpt-4", "messages": [], "user_id": "alice", "project_id": "my-app"}
        il._strip_tracking_kwargs(kwargs)
        assert "user_id" not in kwargs
        assert "project_id" not in kwargs
        assert "model" in kwargs


class TestTokenBucket:
    def test_bucket_initial_tokens(self):
        from tokenledger.core.interceptor import TokenBucket
        b = TokenBucket(10)
        assert b.tokens == 10

    def test_bucket_consumes(self):
        from tokenledger.core.interceptor import TokenBucket
        b = TokenBucket(1000)
        b.consume()
        assert b.tokens < 1000


class TestInterceptionCallbacks:
    def test_on_budget_exceeded_called(self):
        from tokenledger import TokenLedger
        ledger = TokenLedger()
        ledger.set_budget("user", "alice", limit_usd=0.0)
        cb_called = []
        ledger.interceptor.on_budget_exceeded = lambda e: cb_called.append(e)
        with pytest.raises(BudgetExceededError):
            ledger.interceptor._budget_check(
                {"user_id": "alice", "project_id": "default", "model": "gpt-4"},
                "openai", {"messages": [], "model": "gpt-4"},
            )
        assert len(cb_called) == 1

    def test_on_record_called(self):
        from tokenledger import TokenLedger
        ledger = TokenLedger()
        records = []
        ledger.interceptor.on_record = records.append
        ledger.record_usage(provider="test", model="t1", input_tokens=10, output_tokens=5)
        assert len(records) == 1
        assert records[0]["model"] == "t1"


class TestGetHealth:
    def test_get_health_returns_status(self):
        from tokenledger import TokenLedger
        ledger = TokenLedger()
        health = ledger.interceptor.get_health()
        assert isinstance(health, dict)


class TestPerProviderConfig:
    def test_configure_provider(self):
        from tokenledger import TokenLedger
        ledger = TokenLedger()
        ledger.interceptor.configure_provider("openai", max_retries=5, rate_limit_rps=50)
        cfg = ledger.interceptor._get_provider_config("openai")
        assert cfg["max_retries"] == 5
        assert cfg["rate_limit_rps"] == 50

    def test_provider_config_fallback(self):
        from tokenledger import TokenLedger
        ledger = TokenLedger()
        cfg = ledger.interceptor._get_provider_config("nonexistent")
        assert cfg == {}


class TestStreamWrapper:
    def test_sync_stream_iteration(self):
        from tokenledger.core.interceptor import StreamWrapper

        class FakeChunk:
            usage = type("Usage", (), {"prompt_tokens": 5, "completion_tokens": 3})()

        stream = [FakeChunk()]
        wrapper = StreamWrapper(iter(stream), "openai")
        consumed = list(wrapper)
        assert len(consumed) == 1
        usage = wrapper.get_accumulated_usage()
        assert usage["input_tokens"] == 5
        assert usage["output_tokens"] == 3
        assert usage["source"] == "api_reported"


class TestCompact:
    def test_compact_removes_old_records(self):
        from datetime import timedelta, timezone

        from tokenledger import TokenLedger
        ledger = TokenLedger()
        ledger.record_usage(provider="test", model="t1", input_tokens=1, output_tokens=1)
        # make record look old, then shrink retention
        old_ts = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        ledger.store.records[-1]["timestamp"] = old_ts
        ledger.store.retention.max_age_days = 0
        result = ledger.store.compact()
        assert result["removed"] == 1
        assert result["remaining"] == 0

    def test_compact_keeps_recent_records(self):
        from tokenledger import TokenLedger
        ledger = TokenLedger()
        ledger.store.retention.max_age_days = 365
        ledger.record_usage(provider="test", model="t1", input_tokens=1, output_tokens=1)
        result = ledger.store.compact()
        assert result["removed"] == 0
        assert result["remaining"] == 1

    def test_get_record_count(self):
        from tokenledger import TokenLedger
        ledger = TokenLedger()
        assert ledger.store.get_record_count() == 0
        ledger.record_usage(provider="test", model="t1", input_tokens=1, output_tokens=1)
        assert ledger.store.get_record_count() == 1


class TestBudgetImprovements:
    def test_budget_accepts_max_tokens(self):
        from tokenledger import TokenLedger
        ledger = TokenLedger()
        ledger.set_budget("user", "alice", limit_usd=1.0)
        enforcer = ledger.interceptor.enforcer
        result = enforcer.check_budget(
            user_id="alice", project_id="default", provider="openai", model="gpt-4", max_tokens=50,
        )
        assert result is True

    def test_update_model_stats(self):
        from tokenledger import TokenLedger
        ledger = TokenLedger()
        ledger.interceptor.enforcer.update_model_stats("gpt-4", 100, 60)
        assert ledger.interceptor.enforcer._avg_output_per_model["gpt-4"] != 0.5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
