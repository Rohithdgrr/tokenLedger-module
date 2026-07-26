# WORKFLOW.md

> **Understanding how TokenLedger intercepts, processes, and governs every LLM API call.**

This document describes the complete lifecycle of an API call when TokenLedger is active — from the moment your application code invokes a method to the final storage of a verified usage record.

---

## Table of Contents

1. [The Interception Model](#1-the-interception-model)
2. [Pre-Flight Phase](#2-pre-flight-phase)
3. [Execution Phase](#3-execution-phase)
4. [Post-Flight Phase](#4-post-flight-phase)
5. [Failure Handling](#5-failure-handling)
6. [Workflow Diagrams](#6-workflow-diagrams)
7. [Provider-Specific Workflows](#7-provider-specific-workflows)
8. [Streaming & Async Workflows](#8-streaming--async-workflows)

---

## 1. The Interception Model

TokenLedger uses **method wrapping** (monkey-patching). Each provider wrapper uses a shared `_wrap_attr` helper that handles all providers generically.

### Wrapping vs. Manual

| Approach | When to Use | Code Change |
|----------|-------------|-------------|
| **Wrapper** | Existing codebases | Zero — one line to wrap client |
| **Manual** | Custom/non-standard APIs | Call `record_usage()` explicitly |

TokenLedger consolidates all wrapping through `_wrap_attr` which backs up, replaces, and restores via `_original_methods`. Both sync and async functions are detected and wrapped via `asyncio.iscoroutinefunction`.

---

## 2. Pre-Flight Phase

### Step 2.1: Rate Limiter Check

Before any network request, the token bucket is checked for the provider:

```python
tokens = bucket.get("tokens", rate_limit_rps)
if tokens < 1:
    sleep_time = (1 - tokens) / rate_limit_rps
    time.sleep(sleep_time)
```

### Step 2.2: Circuit Breaker Check

If the provider has had `circuit_breaker_threshold` consecutive failures, the circuit opens and `CircuitBreakerOpenError` is raised immediately. After `circuit_recovery_timeout` seconds, the circuit enters half-open state allowing one trial request.

### Step 2.3: Metadata Extraction

```python
metadata = {
    "user_id": kwargs.get("user_id", "anonymous"),
    "project_id": kwargs.get("project_id", "default"),
    "model": kwargs.get("model", "unknown"),
    "provider": provider,
    "messages": kwargs.get("messages", []),
    "conversation_id": kwargs.get("conversation_id"),
    "agent_id": kwargs.get("agent_id"),
}
```

### Step 2.4: Budget Enforcement

TokenLedger queries all applicable budgets:

1. **Determine Time Window** by `reset_cycle`
2. **Calculate Current Spend** from in-memory records
3. **Compare**: If `current_spend + estimated_cost > budget_limit`:
   - Raise `BudgetExceededError`
   - Do not execute the API call

### Step 2.5: Latency Timer Start

```python
start_time = time.monotonic()
```

---

## 3. Execution Phase

### Step 3.1: API Call with Retry

The original method is invoked with retry logic:

```python
for attempt in range(max_retries + 1):
    try:
        return await original(*args, **kwargs) if async_fn else original(*args, **kwargs)
    except Exception as e:
        last_exc = e
        if attempt < max_retries:
            time.sleep(retry_delay * (2 ** attempt))
raise last_exc
```

### Step 3.2: Latency Measurement

```python
latency_ms = (time.monotonic() - start_time) * 1000
```

---

## 4. Post-Flight Phase

### Step 4.1: Token Extraction

```
Response Object
      |
      v
TokenExtractor routes to provider-specific parser:
- openai, deepseek, mistral, nvidia, kimi, glm, minimax, together, perplexity
  -> _parse_openai (reads response.usage)
- anthropic -> _parse_anthropic (reads response.usage)
- gemini -> _parse_gemini (reads response.usage_metadata)
- groq -> _parse_groq (reads response.usage)
- openrouter -> _parse_openrouter (reads response.usage)
- cohere -> _parse_cohere (reads response.meta.billed_units)
- ollama -> _parse_ollama (often estimated)
```

### Step 4.2: Token Estimation (Fallback)

If the API response lacks usage data:

1. **OpenAI-compatible models**: Use `tiktoken` encoder >98% accuracy
2. **All others**: Character heuristic `len(text) // 4`
3. Record flagged: `"source": "estimated"`

### Step 4.3: Cost Calculation

```python
pricing = registry.get_pricing(provider, model)
cost = input_tokens * pricing["input_per_token"] + output_tokens * pricing["output_per_token"]
```

Unknown model policy:
- `"estimate"` → use default rate
- `"block"` → raise `UnknownModelError`
- `"allow"` → store with zero cost

### Step 4.4: System Context (Optional)

If a `SystemMonitor` is attached to the ledger, the interceptor automatically snapshots system metrics and attaches `record["system"] = snapshot()`.

### Step 4.5: Verification Pipeline

6 checks: Token Arithmetic, Sanity Bounds, Cost Verification, Model Registry, Latency Sanity, Anomaly Detection.

### Step 4.6: Record Construction

```python
record = {
    "record_id": str(uuid.uuid4()),
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "provider": provider,
    "model": model,
    "input_tokens": input_tokens,
    "output_tokens": output_tokens,
    "total_tokens": input_tokens + output_tokens,
    "cost_usd": calculated_cost,
    "latency_ms": latency_ms,
    "user_id": user_id,
    "project_id": project_id,
    "status": "success",
    "source": source,
    "system": { ... },              # Optional
    "conversation_id": "...",       # Optional (per-conversation tracking)
    "agent_id": "...",              # Optional (per-agent tracking)
    "prompt_hash": "sha256...",     # Optional (prompt fingerprint)
    "reasoning_tokens": 30,         # Optional (o1 chain-of-thought)
    "cached_input_tokens": 80,      # Optional (cache-hit input)
    "embedding_tokens": 100,        # Optional (embedding model usage)
    "tool_call_count": 3,           # Optional (tool-call attribution)
    "tool_calls": [...],            # Optional
    "media_type": "image",          # Optional (generation type)
    "cache_hit": True,              # Optional (cache flag)
    "_checksum": "sha256...",       # Immutability hash (auto, internal)
}
```

### Step 4.7: Storage

1. SHA-256 checksum computed and attached to record (`_checksum`)
2. In-memory append via ring buffer (`deque` with configurable `maxlen`)
3. Running totals update for O(1) analytics
4. Age-based retention check (`_apply_retention` prunes records older than `retention_days`)
5. Optional JSONL file append with `checksum:json\n` format for tamper-proof persistence

---

## 5. Failure Handling

### Budget Exceeded (Pre-Flight)

```
Application Code
      |
      v
   Wrapper
      |
      v
 Budget Check --NO--> BudgetExceededError
      |                 (no API call made)
     YES
      |
      v
   API Call
```

### Circuit Breaker Open

```
Application Code
      |
      v
 Circuit Breaker --OPEN--> CircuitBreakerOpenError
      |                       (no API call made)
    CLOSED
      |
      v
   API Call
```

### API Error (Execution)

If the provider API raises an exception:
1. Retry loop (up to `max_retries` times with exponential backoff)
2. If all retries exhausted: exception propagates to caller
3. TokenLedger increments circuit breaker failure counter
4. No usage record created

### Verification Failure (Post-Flight)

| Severity | Behavior |
|----------|----------|
| Critical (negative tokens) | Record rejected; exception raised |
| Warning (cost mismatch) | Record stored with auto-corrections |
| Info (anomaly detected) | Record stored normally; flag attached |

---

## 6. Workflow Diagrams

### Complete Synchronous Call

```
Caller
  |
  v
Wrapper (sync detection)
  |
  +-- Rate Limiter (token bucket per provider)
  +-- Circuit Breaker (per-provider state machine)
  +-- Metadata Extraction
  +-- Budget Check
  |     |
  |     +-- Exceeded? -> BudgetExceededError
  |     +-- OK -> continue
  |
  v
API Call with retry (original method)
  |
  +-- Success: continue
  +-- Failure: retry up to N times with exponential backoff
  |
  v
Latency Timer Stop
  |
  +-- Token Extraction (provider-specific parser)
  +-- Token Estimation (fallback if no usage data)
  +-- Cost Calculation (pricing registry)
  +-- System Snapshot (optional, auto-attached)
  +-- Verification (6 checks)
  +-- In-Memory Storage + JSONL append
  |
  v
Return Response to Caller
```

### Retry Flow

```
API Call fails (transient error)
  |
  v
Attempt 1 delay=1.0s --> fail
  |
  v
Attempt 2 delay=2.0s --> fail
  |
  v
Attempt 3 delay=4.0s --> success or propagate error
```

### Streaming Response

```
Caller requests stream=True
  |
  v
Wrapper intercepts
  |
  v
Pre-flight: rate limit, circuit, budget, metadata
  |
  v
API call with stream=True
  |
  v
Chunks flow to caller (unchanged, no buffering)
  |
  v
Stream completes
  |
  v
TokenLedger finalizes:
- Accumulates chunk metadata
- Calculates total usage & cost
- Attaches system context (if monitor present)
- Verifies & stores
```

---

## 7. Provider-Specific Workflows

### OpenAI & OpenAI-Compatible

```python
client = OpenAI(api_key="...")
wrapped = ledger.wrap_openai(client)
# Also: wrap_deepseek, wrap_mistral, wrap_nvidia, wrap_kimi,
#       wrap_glm, wrap_minimax, wrap_together, wrap_perplexity

# Internally: _wrap_attr with provider slug
# Replaces client.chat.completions.create
# Uses _parse_openai on response.usage
```

### Anthropic

```python
client = anthropic.Anthropic(api_key="...")
wrapped = ledger.wrap_anthropic(client)

# Replaces client.messages.create
# Reads response.usage.input_tokens, .output_tokens
# Character heuristic fallback
```

### Cohere

```python
import cohere
client = cohere.Client(api_key="...")
wrapped = ledger.wrap_cohere(client)

# Replaces client.chat
# Uses _parse_cohere: reads response.meta.billed_units
```

### Ollama (Local)

```python
client = ollama.Client(host="http://localhost:11434")
wrapped = ledger.wrap_ollama(client)

# Ollama often does NOT return token counts
# Always triggers TokenEstimator
# Flags all records as "estimated"
```

### Custom Provider (Manual)

```python
response = my_custom_api.chat(...)

ledger.record_usage(
    provider="custom-api",
    model="my-model",
    input_tokens=response.prompt_tokens,
    output_tokens=response.completion_tokens,
    user_id="alice",
    project_id="my-app",
    conversation_id="conv-789",            # optional
    agent_id="support-assistant",           # optional
    reasoning_tokens=30,                    # optional
    cached_input_tokens=80,                 # optional
    embedding_tokens=100,                   # optional
    tool_calls=[{"name": "get_weather"}],   # optional
    media_type="image",                     # optional
    cache_hit=True,                         # optional
)
```

---

## 8. Streaming & Async Workflows

### Async Wrapper

```python
async_client = openai.AsyncOpenAI(api_key="...")
wrapped_async = ledger.wrap_openai(async_client)

# Internally:
# _wrap_attr detects coroutine function via iscoroutinefunction
# Rewrites as async def wrapper
```

### Streaming with Budget Check

```python
stream = wrapped.chat.completions.create(
    model="gpt-4o", messages=[...], stream=True,
    user_id="alice", project_id="my-app"
)

# 1. Pre-flight checks (rate limit, circuit, budget)
# 2. If allowed: stream begins
# 3. Caller iterates chunks normally
# 4. On stream end: extract/estimate usage, attach system context, store
```

### Async Streaming

```python
async for chunk in await wrapped_async.chat.completions.create(
    model="gpt-4o", messages=[...], stream=True
):
    print(chunk.choices[0].delta.content)

# Same flow as sync streaming, but async-aware
```

---

## Summary Table: Workflow by Scenario

| Scenario | Pre-Flight | Token Source | System Context | Storage | Notes |
|----------|-----------|--------------|---------------|---------|-------|
| OpenAI sync | Rate limit + circuit + budget | API usage | Auto if monitor set | Ring buffer + checksum + retention | Standard path |
| OpenAI async | Same | API usage | Auto if monitor set | Same | Detected via iscoroutinefunction |
| OpenAI stream | Same | Post-stream | Auto if monitor set | After stream | No chunk buffering |
| Anthropic | Same | API usage | Auto if monitor set | Same | Claude-specific parser |
| Cohere | Same | API usage | Auto if monitor set | Same | billed_units parser |
| Ollama | Same | Estimated | Auto if monitor set | Same | Always estimated |
| Custom API | Manual call | Manual input | Manual via record_usage | Same | Developer provides counts |
| Budget exceeded | Blocks call | N/A | N/A | Blocked record only | No API charges |
| Circuit open | Blocks call | N/A | N/A | N/A | No API charges |
| API error | Passed through | N/A | N/A | Not stored | Exception propagates after retries |

### Immutability & Integrity

Every record incorporates a `_checksum` field (SHA-256 of the serialized record) at insert time. The JSONL persistence format embeds the checksum as a prefix per line: `checksum:json\n`, enabling tamper detection on reload. Call `verify_immutability()` to return a list of tampered `record_id`s.

---

## Next Steps

- **Architecture**: See `ARCHITECTURE.md` for system design and component relationships.
- **Backend**: See `BACKEND.md` for storage, pricing, verification, and system monitoring internals.
- **API Reference**: See docstrings in `tokenledger/core/` for method-level details.
