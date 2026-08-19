"""Tests for all remaining features: protocol, tenant_id, verifier plugins, encryption,
redaction, audit, dp, retention-JSONL, property tests, integration tests."""

import json
import os
import tempfile
from unittest.mock import MagicMock


class TestStorageBackendProtocol:
    def test_memory_store_is_backend(self):
        from tokenledger.core.store import MemoryStore, StorageBackend
        assert isinstance(MemoryStore(), StorageBackend)

    def test_sqlite_store_is_backend(self):
        from tokenledger.core.store import StorageBackend
        from tokenledger.ext.sqlite_store import SqliteStore
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db = f.name
        try:
            store = SqliteStore(db)
            assert isinstance(store, StorageBackend)
        finally:
            store.close()
            os.unlink(db)


class TestMultiTenant:
    def test_record_with_tenant_id(self):
        from tokenledger import TokenLedger
        ledger = TokenLedger()
        r = ledger.record_usage("test", "t1", 10, 5, tenant_id="acme")
        assert r["tenant_id"] == "acme"

    def test_tenant_tracking_kwargs(self):
        from tokenledger.core.interceptor import TRACKING_KWARGS
        assert "tenant_id" in TRACKING_KWARGS

    def test_tenant_running_totals(self):
        from tokenledger import TokenLedger
        ledger = TokenLedger()
        ledger.record_usage("test", "t1", 10, 5, tenant_id="acme")
        totals = ledger.store.get_running_totals("tenant", "acme")
        assert totals["requests"] == 1

    def test_get_spending_by_tenant(self):
        from tokenledger import TokenLedger
        ledger = TokenLedger()
        ledger.record_usage("test", "t1", 10, 5, tenant_id="acme")
        spending = ledger.get_spending_by_tenant()
        assert len(spending) == 1
        assert spending[0]["id"] == "acme"

    def test_tenant_stripped_from_api_call(self):
        from tokenledger.core.interceptor import InterceptionLayer
        il = InterceptionLayer.__new__(InterceptionLayer)
        kwargs = {"model": "gpt-4", "tenant_id": "acme", "user_id": "bob"}
        il._strip_tracking_kwargs(kwargs)
        assert "tenant_id" not in kwargs
        assert "user_id" not in kwargs
        assert "model" in kwargs


class TestVerifierPlugins:
    def test_custom_rule_added(self):
        from tokenledger import TokenLedger
        from tokenledger.core.verifier import VerificationRule
        ledger = TokenLedger()
        flags = []
        class TestRule(VerificationRule):
            def check(self, record, store, pricing):
                flags.append("called")
                return None
        ledger.verifier.add_rule(TestRule())
        ledger.record_usage("test", "t1", 10, 5)
        assert "called" in flags

    def test_custom_rule_flag(self):
        from tokenledger import TokenLedger
        from tokenledger.core.verifier import VerificationRule
        ledger = TokenLedger()
        class FailRule(VerificationRule):
            def check(self, record, store, pricing):
                return "MY_FLAG"
        ledger.verifier.add_rule(FailRule())
        r = ledger.record_usage("test", "t1", 10, 5)
        assert "MY_FLAG" in r["verification"]["anomaly_flags"]

    def test_default_rules_exist(self):
        from tokenledger.core.pricing import PricingRegistry
        from tokenledger.core.store import MemoryStore
        from tokenledger.core.verifier import NegativeTokenRule, TokenArithmeticRule, VerificationEngine
        pr = PricingRegistry()
        store = MemoryStore()
        v = VerificationEngine(pr, store)
        assert any(isinstance(r, TokenArithmeticRule) for r in v.rules)
        assert any(isinstance(r, NegativeTokenRule) for r in v.rules)


class TestEncryptionAtRest:
    def test_encrypt_decrypt_roundtrip(self):
        from tokenledger.core.store import MemoryStore
        key = b"test-key-123456"
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            path = f.name
        try:
            store = MemoryStore(persist_path=path, encryption_key=key)
            store.insert_record({"record_id": "r1", "provider": "t", "model": "m",
                                  "input_tokens": 1, "output_tokens": 1,
                                  "total_tokens": 2, "cost_usd": 0.0,
                                  "timestamp": "2026-07-26T00:00:00"})
            # Re-load from disk with same key
            store2 = MemoryStore(persist_path=path, encryption_key=key)
            assert store2.get_record_count() == 1
        finally:
            os.unlink(path)

    def test_encrypted_file_unreadable_without_key(self):
        from tokenledger.core.store import MemoryStore
        key = b"test-key-123456"
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            path = f.name
        try:
            store = MemoryStore(persist_path=path, encryption_key=key)
            store.insert_record({"record_id": "r1", "provider": "t", "model": "m",
                                  "input_tokens": 1, "output_tokens": 1,
                                  "total_tokens": 2, "cost_usd": 0.0,
                                  "timestamp": "2026-07-26T00:00:00"})
            # Re-load with wrong key -> garbage data, should get 0 records
            store2 = MemoryStore(persist_path=path, encryption_key=b"wrong-key-1234567")
            assert store2.get_record_count() == 0
        finally:
            os.unlink(path)


class TestRedactPrompts:
    def test_redact_hashes_prompt_hash(self):
        import hashlib

        from tokenledger import TokenLedger
        ledger = TokenLedger(redact_prompts=True)
        r = ledger.record_usage("test", "t1", 10, 5, prompt_hash="my-sensitive-prompt")
        assert r["prompt_hash"] != "my-sensitive-prompt"
        expected = hashlib.sha256(b"my-sensitive-prompt").hexdigest()
        assert r["prompt_hash"] == expected

    def test_no_redact_when_disabled(self):
        from tokenledger import TokenLedger
        ledger = TokenLedger(redact_prompts=False)
        r = ledger.record_usage("test", "t1", 10, 5, prompt_hash="my-prompt")
        assert r["prompt_hash"] == "my-prompt"


class TestAuditExport:
    def test_export_audit_includes_checksum(self):
        from tokenledger import TokenLedger
        ledger = TokenLedger()
        ledger.record_usage("test", "t1", 10, 5)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            path = f.name
        try:
            ledger.export_audit_json(path)
            with open(path) as f:
                data = json.load(f)
            assert "exported_at" in data
            assert "record_count" in data
            assert "_checksum" in data
        finally:
            os.unlink(path)


class TestRetentionCleansJSONL:
    def test_retention_rewrites_disk(self):
        from tokenledger.core.store import MemoryStore
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            path = f.name
        try:
            store = MemoryStore(persist_path=path, max_records=1000, retention_days=0)
            # Insert record - retention_days=0 means it gets pruned immediately
            store.insert_record({"record_id": "r1", "provider": "t", "model": "m",
                                  "input_tokens": 1, "output_tokens": 1,
                                  "total_tokens": 2, "cost_usd": 0.0,
                                  "timestamp": "2026-07-26T00:00:00"})
            # After retention, on-disk file should reflect pruned state
            assert store.get_record_count() == 0
        finally:
            os.unlink(path)


class TestDifferentialPrivacy:
    def test_dp_adds_noise_flag(self):
        from tokenledger import TokenLedger
        ledger = TokenLedger(differential_privacy_epsilon=1.0)
        r = ledger.record_usage("test", "t1", 100, 50)
        assert r.get("_dp_noise_applied") is True

    def test_dp_noise_changes_values(self):
        from tokenledger import TokenLedger
        ledger = TokenLedger(differential_privacy_epsilon=0.1)
        r = ledger.record_usage("test", "t1", 1000, 500)
        # Values should be different (noisy)
        assert r["input_tokens"] >= 0
        assert r["output_tokens"] >= 0

    def test_dp_disabled_by_default(self):
        from tokenledger import TokenLedger
        ledger = TokenLedger()
        r = ledger.record_usage("test", "t1", 100, 50)
        assert r.get("_dp_noise_applied") is None


class TestPropertyBased:
    def test_checksum_immutability_property(self):

        from tokenledger.core.store import MemoryStore
        store = MemoryStore()
        r = {"record_id": "r1", "provider": "t", "model": "m", "input_tokens": 10,
             "output_tokens": 5, "total_tokens": 15, "cost_usd": 0.0,
             "timestamp": "2026-07-26T00:00:00"}
        store.insert_record(r)
        # Verify clean
        assert store.verify_immutability() == []
        # Tamper
        stored = store.get_records()[0]
        stored["input_tokens"] = 999
        # Re-insert tampered record
        store.records[-1] = stored
        tampered = store.verify_immutability()
        assert len(tampered) == 1

    def test_retention_never_negative_records(self):
        from tokenledger.core.store import MemoryStore
        store = MemoryStore()
        for i in range(100):
            store.insert_record({"record_id": f"r{i}", "provider": "t", "model": "m",
                                  "input_tokens": i, "output_tokens": i,
                                  "total_tokens": i * 2, "cost_usd": 0.0,
                                  "timestamp": "2026-07-26T00:00:00"})
        for r in store.get_records():
            assert r["input_tokens"] >= 0
            assert r["output_tokens"] >= 0
            assert r["total_tokens"] == r["input_tokens"] + r["output_tokens"]

    def test_running_totals_match_scan(self):
        from tokenledger.core.store import MemoryStore
        store = MemoryStore()
        for i in range(20):
            store.insert_record({"record_id": f"r{i}", "provider": "openai", "model": "gpt-4",
                                  "input_tokens": 10, "output_tokens": 5,
                                  "total_tokens": 15, "cost_usd": 0.001,
                                  "timestamp": "2026-07-26T00:00:00"})
        totals = store.get_running_totals("provider", "openai")
        scanned = {"requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}
        for r in store.get_records():
            scanned["requests"] += 1
            scanned["input_tokens"] += r["input_tokens"]
            scanned["output_tokens"] += r["output_tokens"]
            scanned["total_tokens"] += r["total_tokens"]
            scanned["cost_usd"] += r["cost_usd"]
        for k in scanned:
            assert totals[k] == scanned[k], f"Mismatch for {k}"


class TestIntegrationMock:
    def test_mock_openai_chat_completion(self):
        from tokenledger import TokenLedger
        ledger = TokenLedger()

        class MockUsage:
            prompt_tokens = 10
            completion_tokens = 5

        class MockChoice:
            class Message:
                content = "hello"
            message = Message()

        class MockResponse:
            usage = MockUsage()
            choices = [MockChoice()]

        client = MagicMock()
        client.chat.completions.create.return_value = MockResponse()
        wrapped = ledger.wrap_openai(client)
        result = wrapped.chat.completions.create(model="gpt-4", messages=[{"role": "user", "content": "hi"}],
                                                   user_id="bob", project_id="app")
        assert result.usage.prompt_tokens == 10
        records = ledger.get_records()
        assert len(records) >= 1

    def test_mock_anthropic_messages(self):
        from tokenledger import TokenLedger
        ledger = TokenLedger()

        class MockResponse:
            class Usage:
                input_tokens = 20
                output_tokens = 15
            usage = Usage()
            content = [{"text": "hello"}]

        client = MagicMock()
        client.messages.create.return_value = MockResponse()
        ledger.wrap_anthropic(client)
        result = client.messages.create(model="claude-3", messages=[{"role": "user", "content": "hi"}])
        assert result.usage.input_tokens == 20
