"""Tests for all 10 differentiating features."""
import pytest

from tokenledger import BudgetExceededError, TokenLedger, compute_roi, sign_ledger, verify_signed_ledger
from tokenledger.ext.differentiators import (
    CostContract,
    CostContractRegistry,
    EstimatorFeedback,
    LocalModelCost,
    LocalModelRegistry,
    ModelRouter,
    PromptCache,
    PromptEvolutionTracker,
    RouteOption,
)


class TestGhostMode:
    def test_ghost_mode_does_not_raise(self):
        ledger = TokenLedger(ghost_mode=True)
        ledger.set_budget("user", "alice", limit_usd=0.0)
        rec = ledger.record_usage("openai", "gpt-4", 100, 50, user_id="alice")
        assert rec["_ghost"] is True

    def test_ghost_mode_marks_records(self):
        ledger = TokenLedger(ghost_mode=False)
        ledger.set_budget("user", "bob", limit_usd=0.0)
        with pytest.raises(BudgetExceededError):
            ledger.record_usage("openai", "gpt-4", 100, 50, user_id="bob")

    def test_ghost_does_not_affect_normal(self):
        ledger = TokenLedger(ghost_mode=True)
        rec = ledger.record_usage("openai", "gpt-4", 100, 50)
        assert "_ghost" not in rec or not rec["_ghost"]


class TestSimulateCost:
    def test_simulate_known_model(self):
        ledger = TokenLedger()
        result = ledger.simulate_cost("openai", "gpt-4o", input_tokens=1000, output_tokens=500)
        assert result["estimated_cost_usd"] == pytest.approx(0.005 * 1000/1000 + 0.015 * 500/1000)
        assert result["provider"] == "openai"
        assert result["model"] == "gpt-4o"

    def test_simulate_with_messages(self):
        ledger = TokenLedger()
        messages = [{"role": "user", "content": "Hello world, this is a test!"}]
        result = ledger.simulate_cost("openai", "gpt-4", messages=messages)
        assert result["estimated_cost_usd"] > 0
        assert result["input_tokens"] > 0


class TestROI:
    def test_roi_empty(self):
        ledger = TokenLedger()
        roi = ledger.get_roi()
        assert roi["total_requests"] == 0

    def test_roi_with_records(self):
        ledger = TokenLedger()
        ledger.record_usage("openai", "gpt-4", 100, 50, user_id="alice", project_id="app1")
        ledger.record_usage("openai", "gpt-4", 200, 100, user_id="alice", project_id="app1")
        roi = ledger.get_roi("user", "alice")
        assert roi["total_requests"] == 2
        assert roi["total_output_tokens"] == 150
        assert roi["cost_per_output_token"] > 0

    def test_roi_standalone(self):
        roi = compute_roi([{"cost_usd": 0.01, "input_tokens": 100, "output_tokens": 50}])
        assert roi["total_requests"] == 1
        assert roi["cost_per_request"] == 0.01


class TestSignedLedger:
    def test_sign_and_verify(self):
        ledger = TokenLedger()
        ledger.record_usage("openai", "gpt-4", 100, 50)
        bundle = ledger.sign_ledger("my-key")
        assert bundle["algorithm"] == "hmac-sha256"
        assert bundle["record_count"] == 1
        assert verify_signed_ledger(bundle, "my-key") is True

    def test_verify_fails_wrong_key(self):
        bundle = sign_ledger([{"test": "data"}], "key1")
        assert verify_signed_ledger(bundle, "key2") is False

    def test_verify_fails_tampered(self):
        bundle = sign_ledger([{"test": "data"}], "key")
        bundle["records"][0]["test"] = "tampered"
        assert verify_signed_ledger(bundle, "key") is False


class TestPromptCache:
    def test_exact_match(self):
        c = PromptCache()
        c.put("prompt1", "What is the weather?")
        assert c.get("prompt1") == "What is the weather?"

    def test_near_duplicate(self):
        c = PromptCache()
        c.put("p1", "What is the weather in Paris?")
        results = c.find_similar("What is the weather in London?", threshold=0.7)
        assert len(results) >= 1
        assert results[0][0] == "p1"

    def test_below_threshold(self):
        c = PromptCache(similarity_threshold=0.99)
        c.put("p1", "Hello world")
        results = c.find_similar("Completely different text here")
        assert len(results) == 0


class TestEstimatorFeedback:
    def test_report_and_adjust(self):
        fb = EstimatorFeedback()
        fb.report("gpt-4", "openai", estimated_tokens=100, actual_tokens=120)
        info = fb.get_accuracy("gpt-4", "openai")
        assert info["count"] == 1
        assert info["correction_factor"] == pytest.approx(1.2)

    def test_adjust_only_after_threshold(self):
        fb = EstimatorFeedback()
        for _i in range(6):
            fb.report("gpt-4", "openai", 100, 120)
        adjusted = fb.adjust("gpt-4", "openai", 100)
        assert adjusted == 120  # 100 * 1.2

    def test_adjust_before_threshold(self):
        fb = EstimatorFeedback()
        fb.report("gpt-4", "openai", 100, 120)
        adjusted = fb.adjust("gpt-4", "openai", 100)
        assert adjusted == 100  # unchanged, < 6 reports


class TestModelRouter:
    def test_route_cheapest(self):
        r = ModelRouter([
            RouteOption("openai", "gpt-4", 0.03, 0.06),
            RouteOption("openai", "gpt-4o-mini", 0.00015, 0.0006),
        ])
        best = r.route(input_tokens=1000, output_tokens=500)
        assert best.model == "gpt-4o-mini"

    def test_route_with_cost_constraint(self):
        r = ModelRouter([
            RouteOption("openai", "gpt-4o", 0.005, 0.015),
            RouteOption("openai", "gpt-4o-mini", 0.00015, 0.0006),
        ])
        best = r.route(input_tokens=1000, output_tokens=1000, max_cost=0.001)
        assert best is not None
        assert best.model == "gpt-4o-mini"

    def test_route_no_candidates(self):
        r = ModelRouter([RouteOption("openai", "gpt-4o", 10.0, 30.0)])
        best = r.route(input_tokens=1000, output_tokens=1000, max_cost=0.001)
        assert best is None


class TestCostContract:
    def test_contract_honored(self):
        registry = CostContractRegistry()
        registry.add(CostContract("test-contract", max_cost_usd=10.0))
        assert registry.check("test-contract", 5.0) is True
        assert registry.check("test-contract", 4.0) is True

    def test_contract_breached(self):
        registry = CostContractRegistry()
        registry.add(CostContract("test-contract", max_cost_usd=10.0))
        registry.check("test-contract", 8.0)
        assert registry.check("test-contract", 3.0) is False

    def test_contract_unknown_name_allowed(self):
        registry = CostContractRegistry()
        assert registry.check("nonexistent", 999.0) is True

    def test_contract_callback(self):
        registry = CostContractRegistry()
        fired = []
        def cb(c):
            fired.append(c.name)
        registry.add(CostContract("cb-contract", max_cost_usd=5.0, callback=cb))
        registry.check("cb-contract", 6.0)
        assert fired == ["cb-contract"]


class TestPromptEvolution:
    def test_tracks_versions(self):
        t = PromptEvolutionTracker()
        v1 = t.track("greeting", "Hello")
        assert v1["version"] == 1
        assert "diff" not in v1

        v2 = t.track("greeting", "Hello world")
        assert v2["version"] == 2
        assert len(v2["diff"]) > 0

    def test_history(self):
        t = PromptEvolutionTracker()
        t.track("q", "Question 1")
        t.track("q", "Question 2")
        history = t.get_history("q")
        assert len(history) == 2

    def test_latest(self):
        t = PromptEvolutionTracker()
        t.track("x", "v1")
        t.track("x", "v2")
        assert t.get_latest("x") == "v2"

    def test_empty(self):
        t = PromptEvolutionTracker()
        assert t.get_history("none") == []
        assert t.get_latest("none") is None


class TestLocalModelCost:
    def test_cost_per_token(self):
        m = LocalModelCost("llama-local", watts_per_second=10, cost_per_kwh=0.12, tokens_per_second=30)
        cpt = m.cost_per_token()
        assert cpt > 0
        assert cpt < 0.001  # should be tiny

    def test_cost_for_tokens(self):
        m = LocalModelCost("llama-local", watts_per_second=10, cost_per_kwh=0.12, tokens_per_second=30)
        cost = m.cost_for_tokens(1000, 500)
        assert cost > 0

    def test_registry(self):
        reg = LocalModelRegistry()
        m = LocalModelCost("local-model", watts_per_second=15, cost_per_kwh=0.10, tokens_per_second=25)
        reg.register(m)
        assert reg.get("local-model") is m
        assert reg.estimate_cost("local-model", 1000, 500) is not None
        assert reg.estimate_cost("nonexistent", 1000, 500) is None

    def test_ledger_hooks(self):
        ledger = TokenLedger()
        ledger.register_local_model("my-local-model", watts_per_second=10, cost_per_kwh=0.12, tokens_per_second=30)
        cost = ledger.estimate_local_cost("my-local-model", 1000, 500)
        assert cost is not None
        assert cost > 0

class TestRouteOptionOnLedger:
    def test_add_route_and_route(self):
        ledger = TokenLedger()
        ledger.add_route_option("openai", "gpt-4o", 0.005, 0.015)
        ledger.add_route_option("openai", "gpt-4o-mini", 0.00015, 0.0006)
        best = ledger.model_router.route(input_tokens=1000, output_tokens=500)
        assert best is not None
        assert best.model == "gpt-4o-mini"

class TestCostContractOnLedger:
    def test_ledger_contract(self):
        ledger = TokenLedger()
        ledger.add_cost_contract("my-contract", max_cost_usd=5.0)
        assert ledger.cost_contracts.check("my-contract", 3.0) is True
        assert ledger.cost_contracts.check("my-contract", 3.0) is False

class TestPromptEvolutionOnLedger:
    def test_ledger_track_prompt(self):
        ledger = TokenLedger()
        v1 = ledger.track_prompt_version("prompt-a", "Hello")
        v2 = ledger.track_prompt_version("prompt-a", "Hello world")
        assert v1["version"] == 1
        assert v2["version"] == 2
        assert "diff" in v2
        hist = ledger.prompt_evolution.get_history("prompt-a")
        assert len(hist) == 2
