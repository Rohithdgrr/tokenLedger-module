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

#### `usage(provider, model, messages=None, output_text=None, **kwargs)`
Context manager (sync and async) that records one usage event on block exit:
latency measured from entry, tokens estimated from `messages`/`output_text`,
`source="usage_block"`, `status="error"` if the block raised. Extra kwargs
(`user_id`, `tenant_id`, `conversation_id`, ...) pass through to `record_usage`.

```python
with ledger.usage("openai", "gpt-4o", messages=[...], user_id="alice"):
    ...  # app code
async with ledger.usage("openai", "gpt-4o", messages=[...]):
    ...  # async app code
```

#### `cost_preview(messages, model, provider, output_text=None) -> dict`
Estimate tokens and cost without recording anything. Returns
`{input_tokens, output_tokens, total_tokens, cost_usd, source}`.

#### `create_wallet(user_id, limit_usd, reset_cycle="daily", low_balance_threshold=0.2, on_low_balance=None) -> Wallet`
Create a per-user prepaid allowance wallet (see `Wallet` below).

#### `serve(host="127.0.0.1", port=8765) -> LiveServer`
Build a live spend server bound to this ledger (see `LiveServer` below).

#### `get_summary(scope="global", scope_id="all")`
Aggregated usage statistics with budget utilization, top models, anomalies.

#### `health() -> dict`
Operational health snapshot for monitoring probes: store type/count,
per-budget spend and utilization (via `BudgetEnforcer.get_budget_status()`),
per-provider circuit breaker state, uptime in seconds, and a `warnings` list
flagging any budget over 80% utilization. O(1) except windowed budget spend.

#### `get_budget_status() -> list[dict]`
Per-rule budget report: `{scope, scope_id, limit_usd, spent_usd,
utilization_percent, reset_cycle}`. Read failures degrade to `spent_usd=-1`
without raising — safe to poll frequently.

#### `wrap_proxy(client, attr_path, provider)` / `interceptor.wrap_proxy(...)`
Non-mutating proxy wrapper. Returns a `TokenLedgerProxy` that intercepts
`client.chat.completions.create`-style paths (e.g. `"chat.completions.create"`)
without monkey-patching the original object — ideal for shared clients or
strictly-mocked SDKs. `unwrap()` is not needed for proxies (the client is
never modified).

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

### Wallet
Per-user prepaid allowance built on the budget engine.

- `debit(provider, model, messages=None, input_tokens=0, output_tokens=0, max_tokens=None) -> bool` — reserve estimated request cost; raises `WalletExhaustedError` (subclass of `BudgetExceededError`) if the allowance would be exceeded; fires the one-shot `on_low_balance` alarm when balance drops below `low_balance_threshold * limit`
- `balance() -> float` — remaining allowance in USD
- `spend() -> float` — spend inside the current reset window
- `refill(amount) -> float` — top up; returns the new limit; re-arms the alarm if above threshold
- `reset()` — reset the cycle and re-arm the alarm
- `limit` — current allowance
- `WalletExhaustedError(BudgetExceededError)` — attributes `scope`, `scope_id`, `current_spend`, `limit`

### LiveServer
Pure-stdlib daemon HTTP server for live spend visibility.

- `start() -> LiveServer` — bind and serve in a daemon thread; installs an `on_record` hook (chaining any previous one)
- `stop()` — shut down, remove the hook, restore the previous `on_record`
- `__enter__` / `__exit__` — context manager lifecycle
- `GET /stats` — JSON: `record_count`, `total_tokens`, `cost_usd`, `providers` (per-provider breakdown), `running_totals` (`global:all`), `generated_at`
- `GET /stream` — Server-Sent Events: `event: record` per usage record, `: ping` heartbeat every 15s; CORS enabled
- Create via `ledger.serve(host, port)` or `LiveServer(ledger, host, port)`; pass `port=0` for an ephemeral port (read `server.port` after `start()`)

### Logging Adapter

- `attach_log_handler(ledger, logger_name="tokenledger.spend", level=logging.INFO) -> hook` — install an `on_record` callback that logs every usage record with fields attached as `LogRecord` `extra` (`provider`, `model`, `input_tokens`, `output_tokens`, `total_tokens`, `cost_usd`, `latency_ms`, `status`, `user_id`, `project_id`, `tenant_id`, `conversation_id`, `agent_id`, `source`, `timestamp`); preserves and chains the previous callback
- `detach_log_handler(ledger, hook=None)` — restore the callback active before attachment

### Context Propagation (`ledger_context`)

```python
from tokenledger import ledger_context

token = ledger_context.set({"user_id": request.user.id, "tenant_id": "acme"})
try:
    wrapped.chat.completions.create(...)   # tagged with user/tenant automatically
finally:
    ledger_context.reset(token)
```

A `contextvars.ContextVar` read by the interceptor before every tracked call.
Explicit kwargs always win over context values. Lets middleware (FastAPI/Flask)
tag every call without threading `user_id=...` through signatures, and is
safe under concurrency (asyncio tasks and threads each get their own context).

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

### CostContract
Dataclass: `name`, `max_cost_usd`, `scope="global"`, `scope_id="all"`,
`current_spend=0.0`, `callback=None`. Registered in a `CostContractRegistry`.

### LocalModelCost
Dataclass modeling on-prem inference cost:
- `name`, `power_watts` (default 10), `cost_per_kwh` (default 0.12),
  `tokens_per_second` (default 30), `hardware_cost` (default 0)
- Legacy `watts_per_second` kwarg is accepted and maps to `power_watts`
- `cost_per_token() -> float` — electricity cost per token
- `cost_for_tokens(input_tokens, output_tokens) -> float` — cost of a request
- Register with `LocalModelRegistry.register(model)`; query with
  `estimate_cost(name, input_tokens, output_tokens)` / `list_models()`

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
