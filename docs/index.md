# TokenLedger

**Lightweight governance layer for LLM applications. Zero database required.**

TokenLedger is an open-source Python package that helps developers and organizations **monitor, control, and optimize** the cost of using Large Language Models (LLMs). It provides a unified, zero-config solution by automatically recording token usage, calculating request costs, enforcing budgets, and generating usage analytics.

## How It Works

TokenLedger works by **wrapping LLM API calls**. When a request is made, it identifies the provider and model, collects token usage from the API response (or estimates when necessary), and calculates the cost using a built-in pricing registry. All data is stored in **pure in-memory structures** with optional file persistence (JSONL or SQLite).

```
Your Code → Wrapped Client → LLM API → Response → TokenLedger records usage
                              ↑
                    Budget check, circuit breaker,
                    rate limiter, retry
```

## Quick Start

```python
from openai import OpenAI
from tokenledger import TokenLedger

client = OpenAI()
ledger = TokenLedger()

# Wrap once — tracking is automatic
wrapped = ledger.wrap_openai(client)

# Use as normal
response = wrapped.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello!"}],
    user_id="alice",
    project_id="my-app",
)

# View analytics
summary = ledger.get_summary()
print(f"Spent: ${summary['cost_usd']:.4f}")
```

## Features at a Glance

| Category | Features |
|---|---|
| **Core** | Record usage manually, wrap 16 providers, streaming, async, budget enforcement, circuit breaker, rate limiter, retry with backoff |
| **Storage** | In-memory (default), JSONL persist, SQLite, pluggable `StorageBackend` protocol, ring buffer, age-based retention, Fernet AES encryption-at-rest |
| **Analytics** | Summary (budgets, top models, anomalies), spending by provider/model/user/project/conversation/agent/tenant, trends (hour/day/week/month), latency stats (p50/p95/p99), efficiency, cost breakdown |
| **Verification** | 6 built-in rules (token arithmetic, negative token, cost recalculation, unknown model, negative latency, anomaly detection), pluggable `VerificationRule` ABC, SHA-256 checksums with tamper detection |
| **CLI** | `summary`, `export` (csv/json), `verify`, `compact`, `health`, `update-pricing`, `cost` (instant preview) |
| **Extras** | OpenTelemetry instrumentation, Webhook/Slack notifier, async store wrapper, `@ledger.track` decorator, per-provider config, external pricing via JSON, `get_health()` |
| **Live** | `ledger.serve()` HTTP server — `/stats` JSON + `/stream` Server-Sent Events with 15s heartbeat, CORS for dashboards, `on_record` hook chaining |
| **Spend Control** | Budget wallets (`create_wallet`, reserve-checking `debit()`, `refill()`, `balance()`, one-shot low-balance alarm), usage context managers (`with ledger.usage(...)` sync + async), logging adapter (`attach_log_handler`), cost preview (`cost_preview()`)
| **Privacy** | Prompt redaction (SHA-256 hash), Fernet AES encryption-at-rest (XOR+HMAC fallback), differential privacy (Laplace noise at export/query boundary) |
| **Differentiators** | Ghost mode (dry-run), what-if cost simulator, agent/conversation ROI, signed offline ledgers (HMAC), prompt cache with near-duplicate detection, self-improving estimator, smart model router, agentic cost contracts, prompt evolution tracker, local LLM real-cost modeling |

## Installation

```bash
pip install tokenledger-module        # core (zero hard dependencies)
pip install "tokenledger-module[all]" # + provider SDKs, CLI (rich), system monitoring
```

Zero external services required. All data lives in memory by default.
