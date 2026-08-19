# API Reference

## TokenLedger

Main class for usage tracking, budget enforcement, and analytics.

### Constructor

```python
TokenLedger(
    persist_path: Optional[str] = None,
    unknown_model_policy: str = "estimate",
    system_monitor: Optional[SystemMonitor] = None,
    max_records: int = 100_000,
    retention_days: int = 90,
    store: Optional[StorageBackend] = None,
    encryption_key: Optional[str | bytes] = None,
    differential_privacy_epsilon: Optional[float] = None,
    redact_prompts: bool = False,
    ghost_mode: bool = False,
)
```

> Note: `encryption_key` enables Fernet (AES-128-CBC + HMAC) encryption of the
> persisted JSONL file when `cryptography` is installed (`[security]` extra);
> without it, an HMAC-tagged XOR fallback is used — casual privacy only.
> `differential_privacy_epsilon` noise applies at the export/query boundary
> (`get_records(apply_dp=True)`, `export_*`), never to stored records.

### Core Methods

#### `record_usage(provider, model, input_tokens, output_tokens, ...)`
Record a usage event.

#### `wrap_openai(client)` / `wrap_anthropic(client)` / `wrap_gemini(client)` / etc.
Wrap provider client for automatic tracking.

#### `set_budget(scope, scope_id, limit_usd, reset_cycle="monthly")`
Define spending limits.

#### `get_summary(scope="global", scope_id="all")`
Aggregated usage statistics with budget utilization, top models, anomalies.

### Analytics Methods

- `get_spending_by_provider()` — cost breakdown by provider
- `get_spending_by_dimension(dimension)` — generic dimension breakdown
- `get_spending_by_conversation()` — cost per conversation
- `get_spending_by_agent()` — cost per agent
- `get_spending_by_tenant()` — cost per tenant
- `get_efficiency(scope, scope_id)` — output/input ratio, cache hit rate
- `get_roi(scope, scope_id)` — return on investment metrics (cost per output token, etc.)

### Export Methods

- `export_csv(filepath)` — CSV export with headers
- `export_json(filepath)` — JSON export
- `export_audit_json(filepath)` — signed audit export with checksum
- `sign_ledger(key)` — HMAC-SHA256 signed bundle

### Verification

- `verify_immutability()` — detect tampered records
- `apply_retention(max_age_days)` — prune old records

### Differentiator Methods

- `simulate_cost(provider, model, input_tokens, output_tokens, messages)` — what-if cost prediction
- `add_route_option(provider, model, input_cost, output_cost, ...)` — register route
- `add_cost_contract(name, max_cost_usd, ...)` — agentic cost contract
- `track_prompt_version(name, content)` — prompt evolution tracking
- `register_local_model(name, watts, cost_per_kwh, ...)` — local LLM cost model
- `estimate_local_cost(name, input_tokens, output_tokens)` — local cost estimate

## Supporting Classes

### StorageBackend (ABC)
- `MemoryStore` — In-memory with JSONL persist, ring buffer, obfuscation-at-rest
- `SqliteStore(path)` — SQLite-backed storage

### VerificationEngine
- `add_rule(rule)` — add custom `VerificationRule`
- `verify(record)` — run all rules

### CostContractRegistry
- `add(contract)` — register a cost contract
- `check(name, cost_usd)` — check against contract limit
- `reset(name)` — reset contract spend

### ModelRouter
- `add_option(option)` — add a route option
- `route(input_tokens, output_tokens, max_cost, prefer_latency)` — pick best model

### PromptCache
- `put(key, content)` — cache a prompt
- `get(key)` — retrieve exact match
- `find_similar(content, threshold)` — near-duplicate detection

### EstimatorFeedback
- `report(model, provider, estimated, actual)` — report estimation accuracy
- `get_accuracy(model, provider)` — get accuracy stats
- `adjust(model, provider, estimated)` — apply correction factor

### PromptEvolutionTracker
- `track(name, content)` — record new version
- `get_history(name)` — all versions
- `get_latest(name)` — most recent content

### LocalModelRegistry
- `register(model)` — register local model
- `estimate_cost(name, input_tokens, output_tokens)` — electricity + hardware cost
