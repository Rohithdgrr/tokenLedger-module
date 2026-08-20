# TokenLedger

> **Lightweight governance layer for LLM applications. Zero database required.**

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

TokenLedger is an open-source Python package that helps developers and organizations **monitor, control, and optimize** the cost of using Large Language Models (LLMs). As AI applications increasingly rely on multiple providers — OpenAI, Anthropic, Google Gemini, Groq, OpenRouter, Ollama, DeepSeek, Mistral, Cohere, NVIDIA, and more — tracking usage and managing expenses becomes difficult. TokenLedger provides a **unified, zero-config solution** by automatically recording token usage, calculating request costs, enforcing budgets, and generating usage analytics.

The package works by **wrapping LLM API calls**. When a request is made, TokenLedger identifies the provider and model, collects token usage from the API response (or estimates it when necessary), and calculates the cost using a built-in pricing registry. It then stores details such as provider, model, input tokens, output tokens, total tokens, request latency, user, project, timestamp, and total cost in **pure in-memory structures** with optional lightweight file persistence.

---

## Features

- **Universal Provider Support** — 16 providers: OpenAI, Anthropic, Gemini, Groq, OpenRouter, Ollama, DeepSeek, Mistral, Cohere, NVIDIA, Kimi, GLM, MiniMax, Together, Perplexity, and any custom API.
- **Automatic Cost Calculation** — Built-in pricing registry with per-model input/output token rates.
- **Budget Enforcement** — Define spending limits for users, projects, or applications. Block requests before they exceed the limit.
- **Real-Time Analytics** — Query total requests, token consumption, spending by provider/model, monthly trends, and user/project breakdowns — all in pure Python, zero SQL.
- **Verification Engine** — Multi-layer integrity checks: token arithmetic validation, cost verification, anomaly detection, and latency sanity checks.
- **System Monitoring** — Optional CPU, RAM, disk, GPU, network, temperature, and power metrics attached to usage records.
- **Smart Estimation** — When APIs don't report token counts, TokenLedger estimates using `tiktoken` or character heuristics.
- **Resilience** — Configurable retry with backoff, circuit breaker (per-provider), rate limiter (token bucket), and request timeout.
- **CLI** — `tokenledger summary|export|verify|compact|health|update-pricing|cost` from the terminal.
- **SQLite Storage** — Optional SQLite backend via `TokenLedger(store=SqliteStore("usage.db"))`.
- **Multi-Tenant** — `tenant_id` dimension for isolating usage across organizations or environments.
- **Encryption-at-Rest** — Persisted JSONL is encrypted with Fernet (AES-128-CBC + HMAC) via `encryption_key` when `cryptography` is installed; falls back to HMAC-tagged XOR obfuscation otherwise.
- **Prompt Redaction** — `redact_prompts=True` hashes prompt content before recording.
- **Differential Privacy** — Laplace noise via `differential_privacy_epsilon`, applied only at the export/query boundary so internal budgets and analytics stay exact.
- **Audit Export** — `export_audit_json()` wraps records in a signed envelope with checksum.
- **@ledger.track Decorator** — `@ledger.track(provider="openai", model="gpt-4")` records any function.
- **Zero Database** — Everything runs in-memory. Optional append-only JSONL file for cross-session durability.
- **Minimal Code Changes** — Drop-in wrappers. No refactoring required.
- **AI-Specific Tracking** — Track conversation_id, agent_id, reasoning tokens, cache hits, embedding tokens, tool calls, and media generation costs.
- **Prompt Fingerprinting** — Deterministic SHA-256 hashing for prompt deduplication and caching analytics.
- **Data Retention** — Configurable max_records ring buffer and age-based retention policies.
- **Immutable Event Logs** — SHA-256 checksums on all records with tamper detection and verification.
- **Export** — Generate CSV or JSON reports for auditing and analysis.
- **Live Spend Server** — `ledger.serve()` (or `LiveServer`) exposes `/stats` (JSON snapshot) and `/stream` (Server-Sent Events per usage) over stdlib HTTP for dashboards.
- **Budget Wallets** — Per-user prepaid allowances with reserve-checking `debit()`, `refill()`, live `balance()`, and a one-shot low-balance alarm.
- **Usage Blocks** — `with ledger.usage(...)` / `async with` measure block latency and auto-record (tokens estimated from messages, `status="error"` on exception).
- **Logging Adapter** — `attach_log_handler(ledger)` streams every usage record into `logging` as structured fields.
- **Cost Preview** — `ledger.cost_preview(...)` or CLI `tokenledger cost "prompt" --model gpt-4o` estimates tokens and cost without recording.

---

## Installation

```bash
pip install tokenledger-module        # core (zero hard dependencies)
pip install "tokenledger-module[all]" # + provider SDKs, CLI (rich), system monitoring
pip install "tokenledger-module[cli]" # CLI pretty-printing (rich)
pip install "tokenledger-module[security]" # Fernet AES-128 encryption-at-rest
```

The import and CLI names are `tokenledger`:

```python
from tokenledger import TokenLedger
```

TokenLedger has zero required external services. All data lives in memory. For optional file persistence, no extra dependencies are needed. The CLI works without `rich` too (falls back to plain text), but you get nicer output with `tokenledger-module[cli]`.

---

## Quick Start

### 1. Wrap Your Existing Client

```python
from openai import OpenAI
from tokenledger import TokenLedger

client = OpenAI(api_key="sk-...")
ledger = TokenLedger()

# Wrap once — tracking is now automatic
wrapped = ledger.wrap_openai(client)

response = wrapped.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello, world!"}],
    user_id="alice",
    project_id="my-app"
)
```

### 2. Set a Budget

```python
ledger.set_budget(
    scope="project",
    scope_id="my-app",
    limit_usd=50.00,
    reset_cycle="monthly"
)
```

If the project exceeds $50 this month, TokenLedger raises `BudgetExceededError` **before** the API call is made — preventing any charges.

### 3. View Analytics

```python
summary = ledger.get_summary(scope="project", scope_id="my-app")
print(f"Spent: ${summary['cost_usd']:.4f} | Tokens: {summary['total_tokens']}")

# Spending by model
for item in ledger.get_spending_by_dimension("model"):
    print(f"{item['id']}: ${item['cost_usd']:.4f}")
```

### 4. Export Usage

```python
ledger.export_csv("usage_report.csv")
ledger.export_json("usage_report.json")
```

---

## Supported Providers

| Provider | Wrapper | Token Source | Notes |
|----------|---------|--------------|-------|
| **OpenAI** | `wrap_openai()` | API-reported / tiktoken | Full support |
| **Anthropic** | `wrap_anthropic()` | API-reported | Claude 3/4 family |
| **Google Gemini** | `wrap_gemini()` | API-reported | Vertex AI compatible |
| **Groq** | `wrap_groq()` | API-reported | Ultra-fast inference |
| **OpenRouter** | `wrap_openrouter()` | API-reported | Unified API gateway |
| **Ollama** | `wrap_ollama()` | Estimated | Local models |
| **DeepSeek** | `wrap_deepseek()` | API-reported | OpenAI-compatible |
| **Mistral** | `wrap_mistral()` | API-reported | OpenAI-compatible |
| **Cohere** | `wrap_cohere()` | API-reported | Native parser |
| **NVIDIA** | `wrap_nvidia()` | API-reported | OpenAI-compatible |
| **Kimi** | `wrap_kimi()` | API-reported | OpenAI-compatible |
| **GLM** | `wrap_glm()` | API-reported | OpenAI-compatible |
| **MiniMax** | `wrap_minimax()` | API-reported | OpenAI-compatible |
| **Together** | `wrap_together()` | API-reported | OpenAI-compatible |
| **Perplexity** | `wrap_perplexity()` | API-reported | OpenAI-compatible |
| **Custom** | `record_usage()` | Manual / Custom parser | Bring your own client |

---

## Advanced Usage

### AI-Specific Tracking

```python
ledger.record_usage(
    provider="openai",
    model="gpt-4o",
    input_tokens=150,
    output_tokens=75,
    user_id="alice",
    project_id="my-app",
    conversation_id="conv-123",       # cost per conversation
    agent_id="customer-support",      # cost per agent/assistant
    reasoning_tokens=30,              # chain-of-thought tokens
    cached_input_tokens=80,           # cache-hit input tokens
    embedding_tokens=100,             # embedding model tokens
    tool_calls=[{"name": "get_weather", "tokens": 50}],  # tool attribution
    media_type="image",               # generation type
    cache_hit=True,                   # cache-hit flag for cost analysis
    prompt_hash="abc123...",          # pre-computed prompt fingerprint
)
```

### Prompt Fingerprinting

```python
from tokenledger import TokenLedger

messages = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "What's the weather in Paris?"},
]
fp = TokenLedger.fingerprint_prompt(messages)

ledger.record_usage("openai", "gpt-4o", 100, 50, prompt_hash=fp)
```

### Per-Conversation Analytics

```python
# Ask the ledger to apply a conversation_id (interceptor path)
response = wrapped.chat.completions.create(
    model="gpt-4o",
    messages=[...],
    user_id="alice",
    conversation_id="conv-456",
)
# Query cost by conversation
for row in ledger.get_spending_by_conversation():
    print(f"{row['id']}: ${row['cost_usd']:.4f}")
```

### Per-Agent Analytics

```python
response = wrapped.chat.completions.create(
    model="gpt-4o",
    messages=[...],
    agent_id="billing-assistant",
)
for row in ledger.get_spending_by_agent():
    print(f"{row['id']}: ${row['cost_usd']:.4f}")
```

### Efficiency Metrics

```python
eff = ledger.get_efficiency()
print(f"Efficiency ratio: {eff['avg_efficiency']:.2f}")  # output/input
print(f"Cache hit rate: {eff['cache_hit_rate']:.1%}")
print(f"Reasoning tokens: {eff['total_reasoning_tokens']}")
```

### Data Retention

```python
# Ring buffer: keep at most 10k records
ledger = TokenLedger(max_records=10_000, retention_days=90)

# Manual age-based purge
ledger.apply_retention(max_age_days=30)
```

### Immutability Verification

```python
# Every record is SHA-256 checksummed at insert
tampered = ledger.verify_immutability()
if tampered:
    print(f"Tampered record IDs: {tampered}")
```

### Custom Pricing

```python
ledger.register_pricing(
    provider="my-api",
    model="enterprise-llm",
    input_cost_per_1k=0.01,
    output_cost_per_1k=0.03
)
```

### Manual Recording (Non-wrapped APIs)

```python
ledger.record_usage(
    provider="custom",
    model="enterprise-llm",
    input_tokens=150,
    output_tokens=75,
    user_id="alice",
    project_id="my-app"
)
```

### File Persistence

```python
# All records automatically append to usage.jsonl
ledger = TokenLedger(persist_path="usage.jsonl")

# On next startup, previous records are loaded back into memory
```

### Async & Streaming

```python
# Async clients are wrapped automatically
wrapped_async = ledger.wrap_openai(async_client)

# Streaming responses accumulate usage from chunks
stream = wrapped.chat.completions.create(
    model="gpt-4o",
    messages=[...],
    stream=True
)
for chunk in stream:
    print(chunk.choices[0].delta.content)
```

### CLI

```bash
tokenledger summary          # Aggregated usage
tokenledger export --format csv -o out.csv
tokenledger verify           # Checksum integrity
tokenledger compact          # Force retention prune
tokenledger health           # Store health & stats
tokenledger update-pricing path/to/pricing.json
```

### @ledger.track Decorator

```python
@ledger.track(provider="openai", model="gpt-4")
def ask_llm(messages):
    return client.chat.completions.create(model="gpt-4", messages=messages)
```

### SQLite Store

```python
from tokenledger.ext.sqlite_store import SqliteStore

ledger = TokenLedger(store=SqliteStore("usage.db"))
```

### Live Spend Server

```python
with ledger.serve(host="127.0.0.1", port=8765):   # context manager stops the server
    ...  # GET http://127.0.0.1:8765/stats   -> JSON snapshot
         # GET http://127.0.0.1:8765/stream  -> Server-Sent Events per usage record
```

Pure-stdlib daemon server (no dependencies). `/stream` emits `event: record` messages with a 15-second keepalive heartbeat; CORS is enabled so browser dashboards can consume both endpoints. Existing `on_record` hooks (notifiers, exporters) keep working — the server chains them and restores them on `stop()`.

### Budget Wallets

```python
wallet = ledger.create_wallet("alice", limit_usd=10.0, low_balance_threshold=0.2,
                              on_low_balance=lambda w: send_alert(w.user_id, w.balance()))
wallet.debit("openai", "gpt-4o", input_tokens=100, output_tokens=50)  # raises WalletExhaustedError if it would exceed
wallet.balance()    # remaining allowance in USD
wallet.refill(5.0)  # top up and return the new limit
wallet.reset()      # reset the cycle and re-arm the low-balance alarm
```

`WalletExhaustedError` subclasses `BudgetExceededError`, so existing handlers keep working. The low-balance callback fires at most once per cycle and re-arms on `refill()`/`reset()`.

### Usage Blocks

```python
with ledger.usage("openai", "gpt-4o", messages=[{"role": "user", "content": "hello"}],
                  user_id="alice", conversation_id="c-42"):
    reply = client.chat.completions.create(...)

# async:  async with ledger.usage("openai", "gpt-4o", messages=...): ...

# cost_preview(): estimate without recording anything
ledger.cost_preview([{"role": "user", "content": "hello"}], "gpt-4o", "openai")
```

Records on block exit with `source="usage_block"`, latency measured from entry, tokens estimated from `messages` (plus `output_text` when supplied), and `status="error"` if the block raised.

### Logging Adapter

```python
from tokenledger import attach_log_handler, detach_log_handler

hook = attach_log_handler(ledger)   # logs to "tokenledger.spend" at INFO
# structured fields land in LogRecord.extra: provider, model, input_tokens,
# output_tokens, total_tokens, cost_usd, latency_ms, status, user_id, ...
detach_log_handler(ledger, hook)    # restores the previous on_record sink
```

### CLI Cost Preview

```bash
tokenledger cost "Explain tokenization." --model gpt-4o --provider openai --output-text "A summary."
```

### Multi-Tenant

```python
ledger.record_usage("openai", "gpt-4", 100, 50, tenant_id="org-acme")
for row in ledger.get_spending_by_tenant():
    print(f"{row['id']}: ${row['cost_usd']:.4f}")
```

### Encryption-at-Rest

```python
ledger = TokenLedger(persist_path="usage.jsonl", encryption_key="my-secret-key")
```

### Prompt Redaction

```python
ledger = TokenLedger(redact_prompts=True)
# prompt_hash is SHA-256'd before storage; raw prompt never persisted
```

### Differential Privacy

```python
ledger = TokenLedger(differential_privacy_epsilon=1.0)
noisy = ledger.get_records(apply_dp=True)      # noise applied to copies
bundle = ledger.export_audit_json(apply_dp=True)  # or on any export
# stored records, budgets, and analytics stay exact
```

### Audit Export

```python
report = ledger.export_audit_json()
# {"exported_at": "...", "record_count": N, "_checksum": "sha256...", "records": [...]}
```

### System Monitoring

```python
from tokenledger import TokenLedger
from tokenledger.core.system import SystemMonitor

monitor = SystemMonitor(collection_interval=10.0)
monitor.start()

ledger = TokenLedger(system_monitor=monitor)
ledger.record_usage("openai", "gpt-4o", 150, 75, system_context=True)
```

### Resilience Configuration

```python
ledger = TokenLedger(
    unknown_model_policy="block",
)
ledger.interceptor.max_retries = 5
ledger.interceptor.retry_delay = 2.0
ledger.interceptor.circuit_breaker_threshold = 10
ledger.interceptor.circuit_recovery_timeout = 60.0
ledger.interceptor.rate_limit_rps = 50
ledger.interceptor.request_timeout = 30.0
```

---

## Verification & Data Integrity

Every record passes through a 6-layer verification pipeline:

1. **Token Arithmetic** — `input + output == total` (auto-corrects mismatches)
2. **Sanity Bounds** — Rejects negative token counts
3. **Cost Verification** — Recalculates cost from pricing registry; flags discrepancies
4. **Model Registry Check** — Warns if model is unknown
5. **Latency Sanity** — Ensures non-negative latency values
6. **Anomaly Detection** — Flags usage patterns deviating from historical averages

Records that fail critical checks are rejected. Minor mismatches are auto-corrected and flagged.

---

## Project Structure

```
tokenledger/
├── __init__.py              # Public API (TokenLedger, MemoryStore, errors)
├── __main__.py              # CLI entry point
├── core/
│   ├── ledger.py            # Main TokenLedger class
│   ├── store.py             # In-memory store with ring buffer, retention, checksums
│   ├── interceptor.py       # API wrapping, retry, circuit breaker, rate limiter
│   ├── budget.py            # Budget rules & enforcement
│   ├── pricing.py           # Pricing registry (16 providers)
│   ├── extractor.py         # Token extraction per provider
│   ├── estimator.py         # Token estimation fallback
│   ├── verifier.py          # VerificationEngine + VerificationRule ABC + 6 built-in rules
│   ├── analytics.py         # Aggregation, efficiency, cost breakdown
│   ├── record.py            # Shared build_record() factory
│   └── system.py            # System monitoring (CPU/GPU/RAM/disk/network)
├── ext/
│   ├── opentelemetry.py     # OpenTelemetry instrumentation
│   ├── notifier.py          # Webhook / Slack notifier
│   ├── async_store.py       # Threaded async store wrapper
│   └── sqlite_store.py      # StorageBackend implementation for SQLite
├── utils/
│   └── export.py            # CSV / JSON / audit export
├── pricing_data.json        # External pricing data (auto-loaded)
└── tests/
    ├── test_ledger.py       # Unit tests, edge cases, AI features, retention, immutability
    ├── test_system.py       # System monitor tests
    ├── test_extensions.py   # CLI, OTEL, Webhook, async, decorator, pricing, SQLite
    ├── test_comprehensive.py # StorageBackend, multi-tenant, verifier, encryption, DP, audit, property-based
    └── test_benchmark.py    # Performance benchmarks
```

---

## Configuration

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `persist_path` | `str` | `None` | Path to append-only JSONL file |
| `unknown_model_policy` | `str` | `"estimate"` | `"estimate"`, `"block"`, or `"allow"` |
| `system_monitor` | `SystemMonitor` | `None` | Optional system metrics collector |
| `max_records` | `int` | `100000` | Ring buffer capacity (oldest evicted when full) |
| `retention_days` | `int` | `90` | Max age in days before auto-purge |
| `store` | `StorageBackend` | `MemoryStore()` | Storage backend (SQLite via `SqliteStore`) |
| `tenant_id` | `str` | `None` | Isolate usage records by tenant |
| `encryption_key` | `str`/`bytes` | `None` | Fernet AES-128-CBC encryption for JSONL-at-rest (XOR+HMAC fallback without `cryptography`) |
| `redact_prompts` | `bool` | `False` | Hash prompt content before recording |
| `differential_privacy_epsilon` | `float` | `None` | Laplace noise scale applied at export/query (lower = more privacy) |
| `on_budget_exceeded` | `callable` | `None` | Callback fired when a budget is exceeded |
| `on_budget_threshold` | `callable` | `None` | Callback fired at configurable utilization threshold |

Interceptor configuration (set after init):

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `max_retries` | `int` | `3` | Retry count on transient failures |
| `retry_delay` | `float` | `1.0` | Base delay between retries (exponential backoff) |
| `circuit_breaker_threshold` | `int` | `5` | Consecutive failures to open circuit |
| `circuit_recovery_timeout` | `float` | `30.0` | Seconds before half-open retry |
| `rate_limit_rps` | `int` | `100` | Max requests per second per provider |
| `request_timeout` | `float` | `120.0` | Maximum API call duration in seconds |

---

## Security Policy

- **Encryption-at-rest**: Persisted JSONL encrypted with `encryption_key` uses **Fernet (AES-128-CBC + HMAC)** when the `cryptography` package is installed (`pip install "tokenledger-module[security]"`). Without it, the fallback is HMAC-tagged XOR — obfuscation only, **not** encryption. For compliance-grade at-rest protection, install the `security` extra.
- **No secrets in logs**: API keys and prompts are never logged. Prompt content is SHA-256 hashed when `redact_prompts=True`.
- **Differential privacy**: Laplace noise (`differential_privacy_epsilon`) is applied **only at the export/query boundary** (`get_records(apply_dp=True)`, `export_*`, `export_audit_json`) — internal budgets, running totals, and analytics always use exact figures.
- **Unknown models**: Never priced at $0. Unknown models fall back to the bundled default rate and log a `WARNING`; `unknown_model_policy="block"` rejects them outright.
- **Reporting vulnerabilities**: Open an issue at [GitHub Issues](https://github.com/Rohithdgrr/tokenLedger-module/issues).

## Known Limitations

- **Pricing drift**: Built-in rates are a snapshot. Monitor provider pricing pages and use `register_pricing()` to update, or load from an external JSON file.
- **Estimation accuracy**: When APIs don't report token usage, `tiktoken` (>98%) or character heuristic (~85%) fallbacks are used. Records from fallbacks are flagged `source: "estimated"` (streams: `"stream_fallback_estimated"`).
- **Gemini & Ollama wrapping**: These providers lack a universal client interface for monkey-patching. `wrap_gemini` wraps `models.generate_content` if `google-genai` is installed; `wrap_ollama` wraps the `chat` method. For full control, use `record_usage()` manually.
- **Single-process**: TokenLedger is designed for single-process apps. Multi-process budget enforcement requires external coordination.
- **Rate limiter**: Simple token bucket suitable for single-process use. For distributed rate limiting, use an external proxy.
- **SQLite concurrent writes**: SqliteStore uses WAL mode with a 5s busy timeout — safe for single-process concurrent reads/writes; multi-process writes require an external connection pool.
- **Running totals growth**: `running_totals` aggregates per distinct dimension value (user, project, tenant...) for O(1) budget checks on `"never"` reset cycles — it grows with distinct IDs, not records. Budget windows (`daily`/`weekly`/`monthly`) derive spend from the bounded record buffer instead.
- **Mock-only integration tests**: Provider integration tests use mocked responses. Real API credentials are needed for end-to-end provider tests.

## Contributing

We welcome contributions! Areas of interest:

- New provider wrappers
- Additional pricing data
- Enhanced anomaly detection algorithms
- Memory optimization strategies
- Documentation improvements

Please open an issue or pull request on GitHub.

---

## License

TokenLedger is released under the **MIT License**.

---

## Support

- **Documentation**: See `docs/` for the full MkDocs site.
- **Issues**: [GitHub Issues](https://github.com/Rohithdgrr/tokenLedger-module/issues)
- **Discussions**: [GitHub Discussions](https://github.com/Rohithdgrr/tokenLedger-module/discussions)

---

> *TokenLedger: See every token. Control every dollar.*
