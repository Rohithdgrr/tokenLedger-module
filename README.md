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
- **Zero Database** — Everything runs in-memory. Optional append-only JSONL file for cross-session durability.
- **Minimal Code Changes** — Drop-in wrappers. No refactoring required.
- **Export** — Generate CSV or JSON reports for auditing and analysis.

---

## Installation

```bash
pip install tokenledger
```

TokenLedger has zero required external services. All data lives in memory. For optional file persistence, no extra dependencies are needed.

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

### System Monitoring

```python
from tokenledger import TokenLedger
from tokenledger.core.system import SystemMonitor

monitor = SystemMonitor(collection_interval=10.0)
monitor.start()  # begins background collection

ledger = TokenLedger(system_monitor=monitor)

# Attach system snapshot to a usage record
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
├── __init__.py              # Public API
├── core/
│   ├── ledger.py            # Main TokenLedger class
│   ├── store.py             # In-memory storage engine
│   ├── interceptor.py       # API wrapping, retry, circuit breaker, rate limiter
│   ├── budget.py            # Budget rules & enforcement
│   ├── pricing.py           # Pricing registry (16 providers)
│   ├── extractor.py         # Token extraction per provider
│   ├── estimator.py         # Token estimation fallback
│   ├── verifier.py          # Data integrity & verification
│   ├── analytics.py         # Aggregation & reporting
│   └── system.py            # System monitoring (CPU/GPU/RAM/disk/network)
└── utils/
    └── export.py            # CSV / JSON export
```

---

## Configuration

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `persist_path` | `str` | `None` | Path to append-only JSONL file |
| `unknown_model_policy` | `str` | `"estimate"` | `"estimate"`, `"block"`, or `"allow"` |
| `system_monitor` | `SystemMonitor` | `None` | Optional system metrics collector |
| `anonymize_ids` | `bool` | `False` | Hash user/project identifiers |
| `max_records_in_memory` | `int` | `100000` | Auto-archive older records |

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

- **Documentation**: See `WORKFLOW.md`, `BACKEND.md`, and `ARCHITECTURE.md` for deep dives.
- **Issues**: [GitHub Issues](https://github.com/your-org/tokenledger/issues)
- **Discussions**: [GitHub Discussions](https://github.com/your-org/tokenledger/discussions)

---

> *TokenLedger: See every token. Control every dollar.*
