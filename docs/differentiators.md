# Differentiating Features

## 1. Ghost Mode (Dry-Run)

Log instead of block on budget exceeded. Safe for testing.

```python
ledger = TokenLedger(ghost_mode=True)
ledger.set_budget("user", "alice", limit_usd=0.0)
rec = ledger.record_usage("openai", "gpt-4", 100, 50, user_id="alice")
assert rec["_ghost"] is True  # Recorded but not blocked
```

## 2. What-If Cost Simulator

Predict cost before making a call.

```python
result = ledger.simulate_cost("openai", "gpt-4", input_tokens=1000, output_tokens=500)
# {"estimated_cost_usd": 0.0125, "total_tokens": 1500, ...}

# With messages for auto-token estimation
result = ledger.simulate_cost("openai", "gpt-4", messages=[{"role": "user", "content": "Hello!"}])
```

## 3. Agent / Conversation ROI

Return-on-investment metrics for any scope.

```python
roi = ledger.get_roi("agent", "customer-support")
# {
#   "total_requests": 42,
#   "total_cost_usd": 0.15,
#   "cost_per_output_token": 2.5e-6,
#   "output_input_ratio": 0.45,
#   ...
# }
```

## 4. Signed Offline Ledgers

HMAC-SHA256 signature for offline verification.

```python
# Sign all records with a shared key
bundle = ledger.sign_ledger("shared-key")

# Bundle can be verified offline by another process
from tokenledger import verify_signed_ledger
assert verify_signed_ledger(bundle, "shared-key") is True

# Tampering is detected
bundle["records"][0]["cost_usd"] = 0.0
assert verify_signed_ledger(bundle, "shared-key") is False
```

## 5. Prompt Cache & Near-Duplicate Detection

```python
from tokenledger import PromptCache

cache = PromptCache(similarity_threshold=0.9)
cache.put("weather-q", "What is the weather in Paris?")

# Exact match
assert cache.get("weather-q") == "What is the weather in Paris?"

# Near-duplicate
results = cache.find_similar("What's the weather in London?")
# [("weather-q", 0.85)]
```

## 6. Self-Improving Estimation Engine

Learns from past estimation errors to improve future estimates.

```python
from tokenledger import EstimatorFeedback

fb = EstimatorFeedback()
for _ in range(6):
    fb.report("gpt-4", "openai", estimated_tokens=100, actual_tokens=120)

# After 6+ reports, adjust uses correction factor
adjusted = fb.adjust("gpt-4", "openai", estimated_tokens=100)
assert adjusted == 120  # 100 * 1.2 correction factor
```

## 7. Smart Cross-Provider Model Router

Pick the cheapest model satisfying constraints.

```python
from tokenledger import ModelRouter, RouteOption

router = ModelRouter()
router.add_option(RouteOption("openai", "gpt-4o", 0.005, 0.015))
router.add_option(RouteOption("openai", "gpt-4o-mini", 0.00015, 0.0006))

# Cheapest for 1000 input + 500 output tokens
best = router.route(input_tokens=1000, output_tokens=500)
assert best.model == "gpt-4o-mini"

# With cost constraint
best = router.route(input_tokens=1000, output_tokens=1000, max_cost=0.001)
```

## 8. Agentic Cost Contracts

Named contracts with callbacks on breach.

```python
from tokenledger import CostContract, CostContractRegistry

registry = CostContractRegistry()
registry.add(CostContract("agent-budget", max_cost_usd=10.0,
             callback=lambda c: print(f"Breached: {c.name}")))

assert registry.check("agent-budget", 8.0) is True
assert registry.check("agent-budget", 3.0) is False  # Breached!
```

## 9. Prompt Evolution Tracker

Track how prompts change over time with version diffs.

```python
from tokenledger import PromptEvolutionTracker

tracker = PromptEvolutionTracker()
v1 = tracker.track("greeting", "Hello, how can I help?")
v2 = tracker.track("greeting", "Hello! How can I help you today?")

# v2 includes unified_diff from v1
assert "diff" in v2
print("\n".join(v2["diff"]))

# View full history
history = tracker.get_history("greeting")
assert len(history) == 2

# Get latest version
latest = tracker.get_latest("greeting")
```

## 10. Local LLM Real-Cost Modeling

Model electricity + hardware cost for locally-run models.

```python
from tokenledger import LocalModelCost, LocalModelRegistry

registry = LocalModelRegistry()
registry.register(LocalModelCost(
    name="llama-3.1-8b-local",
    watts_per_second=10.0,    # Power draw
    cost_per_kwh=0.12,         # Electricity rate
    tokens_per_second=30.0,    # Throughput
))

cost = registry.estimate_cost("llama-3.1-8b-local", 1000, 500)
print(f"Estimated electricity cost: ${cost:.10f}")

# Via TokenLedger
ledger = TokenLedger()
ledger.register_local_model("my-model", watts_per_second=10, cost_per_kwh=0.12, tokens_per_second=30)
cost = ledger.estimate_local_cost("my-model", 1000, 500)
```
