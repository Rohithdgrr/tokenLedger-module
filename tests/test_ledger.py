"""
Tests for TokenLedger core functionality.
"""

import pytest
import tempfile
import os
from datetime import datetime
from decimal import Decimal

from tokenledger import TokenLedger, BudgetExceededError
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
        l = TokenLedger()
        with pytest.raises(ValueError, match="Token counts cannot be negative"):
            l.record_usage("t", "m", -1, 5)
        with pytest.raises(ValueError, match="Token counts cannot be negative"):
            l.record_usage("t", "m", 10, -5)

    def test_zero_tokens(self):
        l = TokenLedger()
        r = l.record_usage("t", "m", 0, 0)
        assert r["input_tokens"] == 0
        assert r["output_tokens"] == 0
        assert r["total_tokens"] == 0
        assert r["cost_usd"] == 0.0

    def test_large_token_counts(self):
        l = TokenLedger()
        r = l.record_usage("openai", "gpt-4o", 10_000_000, 5_000_000)
        assert r["total_tokens"] == 15_000_000
        assert r["cost_usd"] > 0

    def test_unknown_model_default_rate(self):
        l = TokenLedger()
        r = l.record_usage("unknown", "unknown-model", 1000, 500)
        assert r["cost_usd"] > 0

    def test_empty_records(self):
        l = TokenLedger()
        assert l.get_records() == []
        s = l.get_summary("global", "all")
        assert s["requests"] == 0
        assert s["cost_usd"] == 0.0

    def test_multiple_users_projects(self):
        l = TokenLedger()
        users = ["alice", "bob", "charlie"]
        projects = ["app1", "app2"]
        for u in users:
            for p in projects:
                l.record_usage("openai", "gpt-4o", 100, 50, user_id=u, project_id=p)
        assert len(l.get_records()) == 6
        by_user = l.get_spending_by_dimension("user")
        assert len(by_user) == 3

    def test_budget_reset_cycles(self):
        l = TokenLedger()
        for cycle in ["daily", "weekly", "monthly", "never"]:
            l.set_budget("project", f"p-{cycle}", 1000, reset_cycle=cycle)
        l.record_usage("openai", "gpt-4o", 10, 5, project_id="p-daily")
        l.record_usage("openai", "gpt-4o", 10, 5, project_id="p-weekly")
        assert len(l.get_records()) == 2

    def test_budget_exact_limit(self):
        l = TokenLedger()
        l.set_budget("project", "exact", limit_usd=0.001, reset_cycle="never")
        with pytest.raises(BudgetExceededError):
            l.record_usage("openai", "gpt-4o", 1000, 500, project_id="exact")

    def test_export_empty(self):
        import tempfile, os
        l = TokenLedger()
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "empty.csv")
            l.export_csv(p)
            assert os.path.getsize(p) > 0  # has header

    def test_persistence_corrupted_line(self):
        import tempfile, os
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl") as f:
            f.write('{"valid": true}\n')
            f.write('corrupted line\n')
            f.write('{"valid": false}\n')
            p = f.name
        try:
            l = TokenLedger(persist_path=p)
            assert len(l.get_records()) == 2  # corrupted line skipped
        finally:
            os.unlink(p)

    def test_persistence_empty_file(self):
        import tempfile, os
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl") as f:
            p = f.name
        try:
            l = TokenLedger(persist_path=p)
            assert len(l.get_records()) == 0
        finally:
            os.unlink(p)

    def test_circuit_breaker_raises(self):
        from tokenledger.core.interceptor import CircuitBreakerOpenError
        import time
        l = TokenLedger()
        il = l.interceptor
        # circuit opened very recently, recovery timeout (30s) hasn't elapsed
        il._circuit_state["test-provider"] = {"state": "open", "failures": 5, "since": time.monotonic()}
        with pytest.raises(CircuitBreakerOpenError):
            il._check_circuit("test-provider")

    def test_unknown_model_policy_allow(self):
        l = TokenLedger(unknown_model_policy="allow")
        r = l.record_usage("unknown", "unknown-model", 100, 50)
        # manual record_usage path doesn't apply policy (interceptor does)
        # verify the record was stored successfully regardless
        assert r["status"] == "success"

    def test_unknown_model_policy_estimate(self):
        l = TokenLedger(unknown_model_policy="estimate")
        r = l.record_usage("unknown", "unknown-model", 100, 50)
        assert r["cost_usd"] > 0

    def test_custom_pricing_then_record(self):
        l = TokenLedger()
        l.register_pricing("my-provider", "my-model", 0.01, 0.02)
        r = l.record_usage("my-provider", "my-model", 1000, 500)
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
        l = TokenLedger()
        r = l.record_usage("t", "m", 10, 5)
        assert r["latency_ms"] == 0

    def test_record_id_uniqueness(self):
        l = TokenLedger()
        ids = set()
        for _ in range(100):
            r = l.record_usage("t", "m", 10, 5)
            ids.add(r["record_id"])
        assert len(ids) == 100

    def test_analytics_empty_dimension(self):
        l = TokenLedger()
        result = l.get_spending_by_dimension("nonexistent")
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
        l = TokenLedger()
        cid = "conv-123"
        l.record_usage("openai", "gpt-4o", 100, 50, conversation_id=cid)
        l.record_usage("openai", "gpt-4o", 200, 100, conversation_id=cid)
        records = l.get_records()
        assert all(r["conversation_id"] == cid for r in records)
        by_conv = l.get_spending_by_conversation()
        assert len(by_conv) == 1

    def test_agent_tracking(self):
        l = TokenLedger()
        l.record_usage("openai", "gpt-4o", 100, 50, agent_id="agent-a")
        l.record_usage("openai", "gpt-4o", 200, 100, agent_id="agent-b")
        by_agent = l.get_spending_by_agent()
        assert len(by_agent) == 2

    def test_fingerprint_prompt(self):
        messages = [{"role": "user", "content": "hello"}]
        fp1 = TokenLedger.fingerprint_prompt(messages)
        fp2 = TokenLedger.fingerprint_prompt(messages)
        assert fp1 == fp2
        fp3 = TokenLedger.fingerprint_prompt([{"role": "user", "content": "world"}])
        assert fp1 != fp3

    def test_reasoning_tokens(self):
        l = TokenLedger()
        r = l.record_usage("openai", "o1", 100, 50, reasoning_tokens=30)
        assert r["reasoning_tokens"] == 30
        assert r["total_tokens"] == 150  # total = input+output, reasoning separate

    def test_cache_hit(self):
        l = TokenLedger()
        r = l.record_usage("openai", "gpt-4o", 100, 50, cache_hit=True, cached_input_tokens=80)
        assert r["cache_hit"] is True
        assert r["cached_input_tokens"] == 80

    def test_embedding_tracking(self):
        l = TokenLedger()
        r = l.record_usage("openai", "text-embedding-3-small", 100, 0, embedding_tokens=100)
        assert r["embedding_tokens"] == 100

    def test_tool_calls(self):
        l = TokenLedger()
        tools = [{"name": "get_weather", "tokens": 50}]
        r = l.record_usage("openai", "gpt-4o", 100, 50, tool_calls=tools)
        assert r["tool_call_count"] == 1

    def test_media_type(self):
        l = TokenLedger()
        r = l.record_usage("openai", "gpt-4o", 1000, 200, media_type="image")
        assert r["media_type"] == "image"

    def test_efficiency_stats(self):
        l = TokenLedger()
        l.record_usage("openai", "gpt-4o", 200, 100)
        l.record_usage("openai", "gpt-4o", 100, 100, cache_hit=True)
        eff = l.get_efficiency()
        assert eff["cache_hit_rate"] > 0
        assert eff["avg_efficiency"] > 0

    def test_custom_status(self):
        l = TokenLedger()
        r = l.record_usage("openai", "gpt-4o", 100, 50, status="streaming")
        assert r["status"] == "streaming"


class TestRetention:
    def test_apply_retention(self):
        l = TokenLedger()
        l.record_usage("openai", "gpt-4o", 100, 50)
        # max_age_days=0 removes records older than the current moment
        # give it a 1s window to ensure the record is "older"
        import time; time.sleep(0.01)
        l.apply_retention(max_age_days=0)
        assert len(l.get_records()) == 0

    def test_max_records_ring(self):
        l = TokenLedger(max_records=5)
        for _ in range(10):
            l.record_usage("openai", "gpt-4o", 10, 5)
        assert len(l.get_records()) == 5

    def test_retention_preserves_recent(self):
        l = TokenLedger(max_records=100, retention_days=365)
        l.record_usage("openai", "gpt-4o", 10, 5)
        l.apply_retention(max_age_days=365)
        assert len(l.get_records()) == 1


class TestImmutability:
    def test_checksum_on_insert(self):
        l = TokenLedger()
        r = l.record_usage("openai", "gpt-4o", 100, 50)
        stored = l.get_records()[0]
        assert "_checksum" in stored

    def test_immutability_verify_clean(self):
        l = TokenLedger()
        l.record_usage("openai", "gpt-4o", 100, 50)
        assert l.verify_immutability() == []

    def test_immutability_detect_tamper(self):
        l = TokenLedger()
        l.record_usage("openai", "gpt-4o", 100, 50)
        stored = l.get_records()[0]
        stored["output_tokens"] = 99999
        tampered = l.store.verify_immutability()
        assert len(tampered) == 1

    def test_persist_immutable_jsonl(self):
        import tempfile, os
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl") as f:
            p = f.name
        try:
            l = TokenLedger(persist_path=p)
            l.record_usage("openai", "gpt-4o", 100, 50)
            l2 = TokenLedger(persist_path=p)
            assert l2.verify_immutability() == []
        finally:
            os.unlink(p)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])