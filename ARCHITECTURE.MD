# ARCHITECTURE.md

> **System design, component relationships, and extension patterns for TokenLedger.**

This document describes the high-level architecture of TokenLedger, how its components interact, and how developers can extend the system for custom providers, storage backends, and governance rules.

---

## Table of Contents

1. [Design Philosophy](#1-design-philosophy)
2. [System Overview](#2-system-overview)
3. [Component Breakdown](#3-component-breakdown)
4. [Data Flow](#4-data-flow)
5. [Resilience Layer](#5-resilience-layer)
6. [Extension Points](#6-extension-points)
7. [Deployment Patterns](#7-deployment-patterns)
8. [Security Model](#8-security-model)
9. [Future Roadmap](#9-future-roadmap)

---

## 1. Design Philosophy

TokenLedger is built on five core principles:

### 1.1 Zero External Dependencies

- No database server (SQLite, PostgreSQL, MongoDB)
- No message queue (Redis, RabbitMQ)
- No cloud service (AWS, GCP, Azure)
- Pure Python + standard library + optional `tiktoken` / `psutil`

### 1.2 Non-Invasive Integration

- One-line client wrapping
- No changes to existing API calls
- Metadata injected via kwargs (stripped before API call)
- Original method fully preserved and restorable

### 1.3 Fail-Safe by Default

- Budget exceeded? Block before charges occur.
- Unknown model? Estimate conservatively or block.
- Verification failed? Auto-correct minor issues, reject critical ones.
- API error? Pass through unchanged — TokenLedger never swallows exceptions.
- Circuit breaker opens after N consecutive failures; auto-recovers after timeout.

### 1.4 Transparency

- Every record stores verification metadata
- Estimations are explicitly flagged
- Cost calculations are reproducible
- All data stays on your infrastructure

### 1.5 Extensibility

- Custom pricing registration
- Pluggable storage backends (default: in-memory)
- System monitoring via optional `SystemMonitor`

---

## 2. System Overview

```
+------------------------------------------------------------------+
|                         APPLICATION LAYER                         |
|  Your code: wrapped_client.chat.completions.create(...)          |
+----------------------------+-------------------------------------+
                             |
+----------------------------v-------------------------------------+
|                     INTERCEPTION LAYER                            |
|  +------------------+  +------------------+  +------------------+ |
|  | Method Wrapper   |  | Decorator        |  | Manual Recorder  | |
|  | (Monkey Patch)   |  | (@track_usage)   |  | (record_usage)   | |
|  +--------+---------+  +--------+---------+  +--------+---------+ |
|           |                     |                     |            |
+-----------+---------------------+---------------------+------------+
                             |
+----------------------------v-------------------------------------+
|                       GOVERNANCE LAYER                            |
|  +------------------+  +------------------+  +------------------+ |
|  | Budget Enforcer  |  | Pricing Registry |  | Verification     | |
|  | (Pre-flight)     |  | (Cost lookup)    |  | Engine           | |
|  +--------+---------+  +--------+---------+  +--------+---------+ |
|           |                     |                     |            |
+-----------+---------------------+---------------------+------------+
                             |
+----------------------------v-------------------------------------+
|                       EXECUTION LAYER                              |
|  +------------------+  +------------------+  +------------------+ |
|  | API Call         |  | Token Extractor  |  | Token Estimator  | |
|  | (Original)       |  | (Provider Parse) |  | (Fallback)       | |
|  +--------+---------+  +--------+---------+  +--------+---------+ |
|           |                     |                     |            |
+-----------+---------------------+---------------------+------------+
                             |
+----------------------------v-------------------------------------+
|                      RESILIENCE LAYER                              |
|  +------------------+  +------------------+  +------------------+ |
|  | Retry w/ Backoff |  | Circuit Breaker  |  | Rate Limiter     | |
|  | (Exponential)    |  | (Per-provider)   |  | (Token Bucket)   | |
|  +------------------+  +------------------+  +------------------+ |
+------------------------------------------------------------------+
                             |
+----------------------------v-------------------------------------+
|                      STORAGE LAYER                                |
|  +------------------+  +------------------+  +------------------+ |
|  | Memory Store     |  | Running Totals   |  | File Persistence | |
|  | (Primary)        |  | (Aggregates)     |  | (JSONL)          | |
|  +--------+---------+  +--------+---------+  +--------+---------+ |
|           |                     |                     |            |
+-----------+---------------------+---------------------+------------+
                             |
+----------------------------v-------------------------------------+
|                    MONITORING LAYER                                |
|  +------------------+  +------------------+  +------------------+ |
|  | SystemMonitor    |  | Background       |  | Snapshot Query   | |
|  | (CPU/GPU/RAM/   |  | Collection       |  | & Time Ranges    | |
|  |  Disk/Network)   |  | Threading        |  |                  | |
|  +------------------+  +------------------+  +------------------+ |
+------------------------------------------------------------------+
                             |
+----------------------------v-------------------------------------+
|                     ANALYTICS LAYER                               |
|  +------------------+  +------------------+  +------------------+ |
|  | Summary Queries  |  | Trend Analysis   |  | Export Engine    | |
|  | (O(1))           |  | (Time-series)    |  | (CSV/JSON)       | |
|  +------------------+  +------------------+  +------------------+ |
+------------------------------------------------------------------+
```

---

## 3. Component Breakdown

### 3.1 TokenLedger (Facade)

The main entry point that coordinates all subsystems:

```python
class TokenLedger:
    def __init__(self, persist_path=None, unknown_model_policy="estimate",
                 system_monitor=None):
        self.store = MemoryStore(persist_path)
        self.pricing = PricingRegistry()
        self.enforcer = BudgetEnforcer(self.store)
        self.verifier = VerificationEngine(self.pricing)
        self.extractor = TokenExtractor()
        self.estimator = TokenEstimator()
        self.interceptor = InterceptionLayer(...)
        self.analytics = AnalyticsEngine(self.store)
        self.exporter = ExportEngine(self.store)
        self.system_monitor = system_monitor

    # Public API
    def wrap_openai(self, client): ...
    def wrap_anthropic(self, client): ...
    def wrap_deepseek(self, client): ...
    def set_budget(self, scope, scope_id, limit_usd, reset_cycle): ...
    def get_summary(self, scope, scope_id): ...
    def record_usage(self, provider, model, ...): ...
    def export_csv(self, filepath): ...
```

**Responsibilities:**
- Initialize and wire all subsystems
- Expose a clean public API
- Delegate operations to specialized components

### 3.2 InterceptionLayer

Handles the non-invasive wrapping of LLM client methods with resilience:

```python
class InterceptionLayer:
    def __init__(self, ledger, store, pricing, enforcer,
                 extractor, estimator, verifier,
                 unknown_model_policy="estimate",
                 request_timeout=120.0, max_retries=3,
                 retry_delay=1.0, circuit_breaker_threshold=5,
                 circuit_recovery_timeout=30.0, rate_limit_rps=100):
        # ...

    def wrap_openai(self, client, provider="openai"):
        # Uses generic _wrap_attr helper
        # Backs up original, replaces with tracked proxy

    def _make_tracked_fn(self, original, provider):
        # Pre-flight: extract metadata, check budget, rate limit, circuit breaker
        # Execution: call original (with retry & timeout)
        # Post-flight: extract tokens, calculate cost, verify, store
        # Handles both sync and async via asyncio.iscoroutinefunction

    def unwrap(self, client):
        # Restores all original methods
```

**Responsibilities:**
- Monkey-patch provider methods
- Preserve original behavior
- Inject pre/post-flight logic
- Retry with exponential backoff on transient failures
- Circuit breaker per provider (open → half-open → closed)
- Token bucket rate limiter per provider
- Configurable request timeout

### 3.3 MemoryStore

The primary data container:

```python
class MemoryStore:
    records: list[dict]          # All usage records
    budgets: dict[str, dict]     # Active budget rules
    running_totals: dict[str, dict]  # Pre-computed aggregates
    lock: threading.RLock        # Thread safety
```

### 3.4 PricingRegistry

In-memory pricing database supporting 16 providers:

```python
class PricingRegistry:
    _registry: dict[str, dict]   # "provider:model" -> pricing
    # Built-in: openai, anthropic, google, groq, openrouter, ollama,
    #           deepseek, mistral, cohere, nvidia, kimi, glm,
    #           minimax, together, perplexity
```

### 3.5 BudgetEnforcer

Pre-flight spending control:

```python
class BudgetEnforcer:
    def check_budget(self, user_id, project_id, provider, model,
                     input_tokens, output_tokens) -> bool:
        # 1. Find applicable budgets (global, user, project user_project)
        # 2. Calculate current spend (in-memory scan with time window)
        # 3. Compare against limits with actual token counts
        # 4. Raise BudgetExceededError or proceed
```

### 3.6 VerificationEngine

Post-flight data integrity:

```python
class VerificationEngine:
    def verify(self, record, raw_response=None) -> dict:
        # Run 6 independent checks
        # Auto-correct minor issues
        # Detect anomalies
        # Return verified record with metadata
```

### 3.7 TokenExtractor & TokenEstimator

Dual-path token acquisition:

```python
class TokenExtractor:
    PROVIDER_PARSERS = {
        "openai": "_parse_openai",
        "anthropic": "_parse_anthropic",
        "google": "_parse_gemini",
        "groq": "_parse_groq",
        "openrouter": "_parse_openrouter",
        "ollama": "_parse_ollama",
        "cohere": "_parse_cohere",
        # openai-compatible: deepseek, mistral, nvidia, kimi, glm,
        #                    minimax, together, perplexity
    }

class TokenEstimator:
    def estimate(self, text, model, provider) -> dict:
        # tiktoken for OpenAI models, character heuristic for others
```

### 3.8 SystemMonitor

Optional system metrics collector:

```python
class SystemMonitor:
    def __init__(self, collection_interval=60.0):
        self.metrics: list[dict] = []
        self._thread: threading.Thread = None

    def snapshot(self) -> dict:
        # Returns CPU, RAM, disk, network, internet, temperature,
        # GPU, power, processor info in a single dict

    def start(self): ...  # Background collection thread
    def stop(self): ...   # Stop collection thread
    def get_metrics(self, start=None, end=None) -> list[dict]: ...
    def get_summary(self) -> dict: ...
    def clear(self): ...

    # Without psutil: returns zeroed safe defaults for all metrics
```

### 3.9 AnalyticsEngine

Query and aggregation layer:

```python
class AnalyticsEngine:
    def get_summary(self, scope, scope_id) -> dict        # O(1)
    def get_spending_by_dimension(self, dimension) -> list  # O(k)
    def get_trend(self, dimension, id, granularity) -> list  # O(n)
```

### 3.10 ExportEngine

Data export functionality:

```python
class ExportEngine:
    def export_csv(self, filepath, records)
    def export_json(self, filepath, records)
```

---

## 4. Data Flow

### 4.1 Happy Path (Standard Request)

```
[Caller]
    |
    v
[InterceptionLayer.wrap_openai]
    |
    +---> [Rate Limiter] check token bucket (per-provider)
    +---> [Circuit Breaker] check provider state
    +---> [Metadata Extraction] user_id, project_id, model
    +---> [BudgetEnforcer.check_budget]
    |         Scan records -> Calculate spend
    |         Compare to limit -> PASS
    +---> [Latency Timer Start]
    |
    v
[Original API Method (with retry & timeout)]
    |
    v
[Provider API Response]
    |
    v
[Latency Timer Stop]
    |
    +---> [TokenExtractor.extract]
    |         Parse response.usage (or fallback to estimator)
    |
    +---> [PricingRegistry.get_pricing]
    |         Lookup model rates
    |
    +---> [Cost Calculation]
    |         input * rate + output * rate
    |
    +---> [VerificationEngine.verify]
    |         6-layer integrity checks
    |
    +---> [MemoryStore.insert_record]
    |         Append to records[]
    |         Update running_totals
    |         Append to JSONL (optional)
    |
    v
[Return Response to Caller]
```

### 4.2 Circuit Breaker Path

```
[Request to failing provider]
    |
    v
[Circuit Breaker Check]
    |
    +-- state = "closed" -> proceed normally
    +-- state = "open" -> raise CircuitBreakerOpenError
    |   (auto-transitions to half-open after recovery_timeout)
    +-- state = "half-open" -> allow one trial request
    |   success -> close circuit
    |   failure -> open circuit again
```

### 4.3 Retry Path

```
[API call fails with transient error]
    |
    v
[Retry loop]
    |
    +-- attempt 1: fail -> wait 1.0s (retry_delay)
    +-- attempt 2: fail -> wait 2.0s (exponential backoff)
    +-- attempt 3: fail -> wait 4.0s
    |   fail -> raise exception
    |   success at any point -> extract, store, return
```

---

## 5. Resilience Layer

### 5.1 Retry with Exponential Backoff

```python
def _call_with_retry(self, original, args, kwargs):
    last_exc = None
    for attempt in range(self.max_retries + 1):
        try:
            return original(*args, **kwargs)
        except Exception as e:
            last_exc = e
            if attempt < self.max_retries:
                time.sleep(self.retry_delay * (2 ** attempt))
    raise last_exc
```

### 5.2 Circuit Breaker (Per-Provider)

| State | Behavior | Transition |
|-------|----------|------------|
| `closed` | Requests pass normally | → `open` on threshold failures |
| `open` | Requests blocked immediately | → `half-open` after recovery timeout |
| `half-open` | One trial request allowed | → `closed` on success, → `open` on failure |

### 5.3 Rate Limiter (Token Bucket)

Each provider maintains its own token bucket. Tokens refill at `rate_limit_rps` per second. Requests are delayed if no tokens available.

```python
def _check_rate_limit(self, provider):
    now = time.monotonic()
    bucket = self._rate_buckets.get(provider, {"tokens": self.rate_limit_rps, "last": now})
    elapsed = now - bucket["last"]
    bucket["tokens"] = min(self.rate_limit_rps, bucket["tokens"] + elapsed * self.rate_limit_rps)
    if bucket["tokens"] < 1:
        sleep_time = (1 - bucket["tokens"]) / self.rate_limit_rps
        time.sleep(sleep_time)
        bucket["tokens"] = 0
    else:
        bucket["tokens"] -= 1
    bucket["last"] = now
    self._rate_buckets[provider] = bucket
```

---

## 6. Extension Points

### 6.1 Custom Storage Backend

While the default is in-memory, you can implement a custom storage backend:

```python
class RedisStorage:
    def __init__(self, redis_url):
        self.redis = redis.from_url(redis_url)

    def insert(self, record: dict):
        self.redis.lpush("tokenledger:records", json.dumps(record))

    def get_records(self) -> list[dict]:
        # Implement query logic
        pass
```

### 6.2 Custom Budget Rules

```python
class CustomBudgetRule:
    def evaluate(self, record: dict, current_spend: float) -> bool:
        tokens_this_hour = self.get_hourly_tokens(record["user_id"])
        return tokens_this_hour < self.max_tokens_per_hour

ledger.store.budgets["rule:alice"] = {
    "scope": "user", "scope_id": "alice",
    "evaluate": CustomBudgetRule(max_tokens_per_hour=100000).evaluate
}
```

### 6.3 Alert Hooks

```python
def send_slack_alert(budget, current_spend, projected_spend):
    requests.post(SLACK_WEBHOOK, json={
        "text": f"Budget {budget.scope_id} at 80%: ${current_spend}/${budget.limit_usd}"
    })

ledger.on_budget_threshold(threshold=0.8, callback=send_slack_alert)
```

### 6.4 Custom Verification Rules

```python
class CustomVerifier:
    def check(self, record: dict) -> list[str]:
        flags = []
        if record["latency_ms"] > 30000:
            flags.append("SLOW_REQUEST")
        return flags

ledger.verifier.add_rule(CustomVerifier())
```

---

## 7. Deployment Patterns

### 7.1 Single-Process Application

```python
# FastAPI / Flask / Django
ledger = TokenLedger(persist_path="usage.jsonl")

@app.post("/chat")
def chat(request: ChatRequest):
    response = wrapped_client.chat.completions.create(
        model="gpt-4o",
        messages=request.messages,
        user_id=request.user_id,
        project_id=request.project_id
    )
    return response
```

**Characteristics:**
- Simplest deployment
- All state in one process
- JSONL file for durability across restarts

### 7.2 Multi-Process (Gunicorn / Uvicorn Workers)

```python
# Each worker has its own TokenLedger instance
# Budgets are per-process (eventual consistency via JSONL)

# gunicorn -w 4 app:app
```

**Characteristics:**
- Each worker maintains independent state
- Budget enforcement is per-process (not global)
- JSONL provides a shared audit log
- Suitable when approximate budget control is acceptable

---

## 8. Security Model

### 8.1 Data Privacy

TokenLedger stores **only metadata**. It never stores:

- Prompt content / user queries
- API response content
- API keys or secrets
- PII (unless explicitly passed as user_id/project_id)

### 8.2 Access Control

TokenLedger does not implement authentication. It relies on the application layer to:

- Validate `user_id` and `project_id` before passing to TokenLedger
- Protect the JSONL persistence file (file permissions)
- Secure analytics endpoints

### 8.3 Data Integrity

- Records are immutable after storage
- Verification metadata prevents tampering
- JSONL append-only format prevents deletion attacks
- Running totals are recomputed from records on load

---

## 9. Future Roadmap

### 9.1 Planned Features

| Feature | Description | Priority |
|---------|-------------|----------|
| Redis Backend | Shared state for multi-process deployments | High |
| Web Dashboard | Real-time usage visualization | Medium |
| Alert Webhooks | Slack, PagerDuty, email notifications | Medium |
| Cost Forecasting | Predict spend based on usage trends | Low |
| Multi-Currency | Support EUR, GBP, JPY pricing | Low |
| OpenTelemetry | Export traces and metrics | Low |

---

## Component Relationship Diagram

```
                    +------------------+
                    |   Application    |
                    +--------+---------+
                             |
              +--------------+--------------+
              |              |              |
    +---------v------+ +-----v------+ +----v---------+
    | Method Wrapper | | Decorator  | | Manual API   |
    | (Monkey Patch) | |            | |              |
    +--------+-------+ +-----+------+ +----+---------+
             |               |              |
             +---------------+--------------+
                             |
                    +--------v---------+
                    |  TokenLedger     |
                    |  (Facade)        |
                    +--------+---------+
                             |
        +--------------------+--------------------+
        |                    |                    |
+-------v-------+   +--------v--------+   +------v--------+
| Interception  |   | BudgetEnforcer  |   | Analytics     |
| Layer         |   | (Pre-flight)    |   | Engine        |
| + Retry       |   +--------+--------+   +------+--------+
| + Circuit     |            |                    |
| + Rate Limit  |   +--------v--------+           |
+-------+-------+   | PricingRegistry |           |
        |           +--------+--------+           |
        |                    |                    |
        |           +--------v--------+           |
        |           | Verification    |           |
        |           | Engine          |           |
        |           +--------+--------+           |
        |                    |                    |
        |           +--------v--------+           |
        |           | TokenExtractor  |           |
        |           | TokenEstimator  |           |
        |           +--------+--------+           |
        |                    |                    |
        +--------------------+--------------------+
                             |
                    +--------v---------+
                    |   MemoryStore    |
                    |   (Primary)      |
                    +--------+---------+
                             |
              +--------------+--------------+
              |              |              |
    +---------v------+ +-----v------+ +----v---------+
    | records[]      | | running_   | | JSONL File   |
    |                | | totals{}   | | (Optional)   |
    +----------------+ +------------+ +--------------+
                             |
                    +--------v---------+
                    |  SystemMonitor   |
                    |  (Optional)      |
                    +------------------+
```

---

## Summary

TokenLedger's architecture is intentionally flat and modular:

- **10 core components** with clear, single responsibilities
- **Zero external dependencies** for the default configuration
- **In-memory storage** with O(1) analytics via running totals
- **Optional file persistence** for cross-session durability
- **Resilience layer** with retry, circuit breaker, and rate limiting
- **System monitoring** via optional `SystemMonitor` with psutil

The design prioritizes **simplicity, speed, and transparency** over enterprise features like distributed consensus or complex query languages. For most LLM applications — especially those running in a single process or with relaxed multi-process budget requirements — this architecture provides complete governance without operational overhead.

---

## Related Documentation

- **README.md**: Installation, quick start, and feature overview
- **WORKFLOW.md**: End-to-end call lifecycle and provider-specific flows
- **BACKEND.md**: Storage engine, pricing, verification, and memory management internals
