# BACKEND.md

> **Deep dive into TokenLedger's storage engine, pricing system, verification logic, system monitoring, and memory management — all without a database.**

This document details the internal backend systems that power TokenLedger. Every component is implemented in pure Python using in-memory data structures, with optional lightweight file persistence.

---

## Table of Contents

1. [Storage Engine](#1-storage-engine)
2. [Pricing Registry](#2-pricing-registry)
3. [Token Extraction & Estimation](#3-token-extraction--estimation)
4. [Verification Engine](#4-verification-engine)
5. [Budget Enforcement](#5-budget-enforcement)
6. [Analytics & Aggregation](#6-analytics--aggregation)
7. [Export System](#7-export-system)
8. [System Monitoring](#8-system-monitoring)
9. [Memory Management](#9-memory-management)
10. [Concurrency Model](#10-concurrency-model)

---

## 1. Storage Engine

TokenLedger's storage layer is designed around a **zero-database philosophy**. All runtime state is held in Python's native data structures, providing microsecond-level read/write latency.

### 1.1 Core Data Structures

```python
class MemoryStore:
    def __init__(self, persist_path: str = None, max_records: int = 100_000, retention_days: int = 90):
        self.records: deque[dict] = deque(maxlen=max_records)  # Ring buffer
        self.budgets: dict[str, dict] = {}                      # Active budget rules
        self.running_totals: dict[str, dict] = {}               # Pre-computed aggregates
        self.lock = threading.RLock()                            # Thread safety
        self.persist_path = persist_path                         # Optional JSONL file
        self.retention = RetentionPolicy(max_age_days=retention_days, max_records=max_records)
```

### 1.2 Record Schema

Every API call produces a single record:

```python
{
    "record_id": "550e8400-e29b-41d4-a716-446655440000",  # UUID4
    "timestamp": "2026-07-25T23:15:00.000000",              # ISO 8601 UTC
    "provider": "openai",                                    # Provider slug
    "model": "gpt-4o",                                       # Model identifier
    "input_tokens": 150,                                     # Integer
    "output_tokens": 75,                                     # Integer
    "total_tokens": 225,                                     # Integer (verified)
    "cost_usd": 0.00375,                                    # Float
    "latency_ms": 1240.5,                                   # Float
    "user_id": "alice",                                      # String
    "project_id": "my-app",                                  # String
    "status": "success",                                     # success | error | blocked
    "source": "api_reported",                                # api_reported | estimated | manual
    "conversation_id": "conv-123",                           # Optional (per-conversation tracking)
    "agent_id": "support-bot",                               # Optional (per-agent tracking)
    "prompt_hash": "sha256...",                              # Optional (prompt fingerprint)
    "reasoning_tokens": 30,                                  # Optional (o1 chain-of-thought)
    "cached_input_tokens": 80,                               # Optional (cache-hit input)
    "embedding_tokens": 100,                                 # Optional (embedding usage)
    "tool_call_count": 3,                                    # Optional (tool-call attribution)
    "tool_calls": [{"name": "get_weather", "tokens": 50}],  # Optional
    "media_type": "image",                                   # Optional (generation type)
    "cache_hit": True,                                       # Optional (cache flag)
    "system": { ... },                                       # Optional system snapshot
    "_checksum": "e1452b78defd...",                          # SHA-256 (internal, immutability)
    "verification": {
        "tokens_verified": True,
        "cost_verified": True,
        "estimation_used": False,
        "anomaly_flags": [],
        "verification_timestamp": "2026-07-25T23:15:00.001000"
    }
}
```

### 1.3 Insertion Flow

```python
def insert_record(self, record: dict):
    with self.lock:
        record["_checksum"] = self._checksum(record)  # SHA-256 for immutability
        self.records.append(record)                     # Ring buffer auto-evicts oldest
        self._update_running_totals(record)             # O(1) aggregates
        self._apply_retention()                         # Age-based pruning
        if self.persist_path:
            self._append_to_disk(record)
```

### 1.4 File Persistence (JSON Lines with Checksums)

TokenLedger uses an **append-only JSON Lines (JSONL)** format with embedded integrity checksums:

- **Checksum prefix**: Each line is written as `sha256_hex:json\n` for tamper detection.
- **Append-only writes**: O(1) time, no read-before-write, safe for concurrent access.
- **Line-oriented**: Corrupted lines do not break the entire file. Checksum validation skips tampered lines.
- **Human-readable**: Check `sha256_hex` with `sha256sum` tool.
- **Streaming load**: Startup reads line-by-line with validation.

**Write:**
```python
def _append_to_disk(self, record: dict):
    line = json.dumps(record, default=str)
    checksum = hashlib.sha256(line.encode()).hexdigest()
    with open(self.persist_path, "a", encoding="utf-8") as f:
        f.write(f"{checksum}:{line}\n")
        f.flush()
        os.fsync(f.fileno())
```

**Immutability Verification:**
```python
def verify_immutability(self) -> list[str]:
    tampered = []
    for r in self.get_records():
        expected = r.get("_checksum", "")
        actual = self._checksum({k: v for k, v in r.items() if k != "_checksum"})
        if expected and expected != actual:
            tampered.append(r.get("record_id", "unknown"))
    return tampered
```

### 1.5 Retention & Ring Buffer

```python
class RetentionPolicy:
    def __init__(self, max_age_days=90, max_records=100_000, archive_on_trim=True):
        self.max_age_days = max_age_days
        self.max_records = max_records
        self.archive_on_trim = archive_on_trim
```

- **Ring buffer**: `collections.deque(maxlen=max_records)` automatically discards the oldest records when the capacity is reached. No manual archiving needed.
- **Age-based retention**: `_apply_retention()` runs on every insert, pruning records older than `retention_days`. Running totals are rebuilt from the surviving records.
- **Manual trigger**: Call `ledger.apply_retention(max_age_days=N)` to override and purge.

---

## 2. Pricing Registry

The pricing registry is an in-memory dictionary that maps `provider:model` keys to per-token cost rates.

### 2.1 Registry Structure

```python
self._registry = {
    "openai:gpt-4o": {"input_per_token": 0.000005, "output_per_token": 0.000015, "currency": "USD"},
    "openai:gpt-4o-mini": {"input_per_token": 0.00000015, "output_per_token": 0.0000006},
    "anthropic:claude-3-5-sonnet-20241022": {"input_per_token": 0.000003, "output_per_token": 0.000015},
    "google:gemini-1.5-pro": {"input_per_token": 0.0000035, "output_per_token": 0.0000105},
    "groq:llama-3.1-70b": {"input_per_token": 0.00000059, "output_per_token": 0.00000079},
    "deepseek:deepseek-chat": {"input_per_token": 0.00000014, "output_per_token": 0.00000028},
    "mistral:mistral-large": {"input_per_token": 0.000002, "output_per_token": 0.000006},
    "cohere:command-r-plus": {"input_per_token": 0.000003, "output_per_token": 0.000015},
    "nvidia:llama-3.1-nemotron-70b": {"input_per_token": 0.000001, "output_per_token": 0.000002},
    "kimi:moonshot-v1-8k": {"input_per_token": 0.000003, "output_per_token": 0.000003},
    "glm:glm-4-plus": {"input_per_token": 0.000005, "output_per_token": 0.000005},
    "minimax:minimax-abab-6.5": {"input_per_token": 0.000001, "output_per_token": 0.000001},
    "together:llama-3.1-70b": {"input_per_token": 0.00000054, "output_per_token": 0.00000054},
    "perplexity:sonar-pro": {"input_per_token": 0.000003, "output_per_token": 0.000015},
    "default:unknown": {"input_per_token": 0.000002, "output_per_token": 0.000002},
}
```

### 2.2 Cost Calculation

```python
def calculate_cost(self, provider: str, model: str,
                   input_tokens: int, output_tokens: int) -> float:
    key = f"{provider}:{model}"
    pricing = self._registry.get(key, self._registry["default:unknown"])
    cost = (input_tokens * pricing["input_per_token"] +
            output_tokens * pricing["output_per_token"])
    return round(cost, 10)
```

### 2.3 Unknown Model Policies

| Policy | Behavior | Use Case |
|--------|----------|----------|
| `estimate` | Use default rate ($0.002/1K), flag as estimated | Development / experimentation |
| `block` | Raise `UnknownModelError` | Production strictness |
| `allow` | Store with zero cost, flag for review | Audit-first approach |

---

## 3. Token Extraction & Estimation

### 3.1 Provider-Specific Parsers

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
    }
```

OpenAI-compatible providers (deepseek, mistral, nvidia, kimi, glm, minimax, together, perplexity) route to `_parse_openai`.

**Cohere Parser:**
```python
def _parse_cohere(self, response) -> dict:
    meta = response.meta if hasattr(response, 'meta') else None
    if meta and hasattr(meta, 'billed_units'):
        return {
            "input_tokens": getattr(meta.billed_units, 'input_tokens', 0),
            "output_tokens": getattr(meta.billed_units, 'output_tokens', 0),
            "total_tokens": (getattr(meta.billed_units, 'input_tokens', 0) +
                            getattr(meta.billed_units, 'output_tokens', 0)),
            "source": "api_reported"
        }
    return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "source": "api_reported"}
```

### 3.2 Estimation Pipeline

When the API does not report usage:

```
Input: messages list + model + provider
          |
          v
   +-------------+
   | Concatenate |
   | all content |
   +------+------+
          |
          v
   +-------------+
   | Provider =  |
   |  "openai"?  |
   +------+------+
      YES |
          v
   +-------------+     +-------------+
   |  tiktoken   |---->|  model-     |
   |   import    |     |  specific   |
   |   attempt   |     |  encoder    |
   +-------------+     +------+------+
                               |
                               v
                         +-------------+
                         |  token      |
                         |  count      |
                         +-------------+
      NO  |
          v
   +-------------+
   | Character   |
   | Heuristic:  |
   | len(text)//4|
   +-------------+
          |
          v
   Return: {input_tokens, output_tokens, total_tokens,
            source: "estimated",
            estimation_method: "tiktoken" | "character_heuristic"}
```

**Accuracy:**
| Method | Accuracy | Dependencies |
|--------|----------|--------------|
| API-reported | 100% | None |
| tiktoken | >98% | `tiktoken` |
| Character heuristic | ~85% | None |

---

## 4. Verification Engine

### 4.1 Check Matrix

```python
class VerificationEngine:
    def verify(self, record: dict, raw_response=None) -> dict:
        flags = []

        # CHECK 1: Token Arithmetic
        expected_total = record["input_tokens"] + record["output_tokens"]
        if record["total_tokens"] != expected_total:
            flags.append("TOKEN_ARITHMETIC_MISMATCH")
            record["total_tokens"] = expected_total

        # CHECK 2: Sanity Bounds
        if record["input_tokens"] < 0 or record["output_tokens"] < 0:
            flags.append("NEGATIVE_TOKEN_COUNT")
            raise ValueError("Impossible token count detected")

        # CHECK 3: Cost Verification
        expected_cost = self._calculate_expected_cost(record)
        if abs(record["cost_usd"] - expected_cost) > 0.0001:
            flags.append("COST_CALCULATION_MISMATCH")
            record["cost_usd"] = expected_cost

        # CHECK 4: Model Registry
        model_key = f"{record['provider']}:{record['model']}"
        if model_key not in self.pricing and record.get("provider", "") != "custom":
            flags.append("UNKNOWN_MODEL")

        # CHECK 5: Latency Sanity
        if record["latency_ms"] < 0:
            flags.append("NEGATIVE_LATENCY")
            record["latency_ms"] = 0

        # CHECK 6: Anomaly Detection
        if self._is_anomalous(record):
            flags.append("ANOMALOUS_USAGE_PATTERN")

        record["verification"] = {
            "tokens_verified": "TOKEN_ARITHMETIC_MISMATCH" not in flags,
            "cost_verified": "COST_CALCULATION_MISMATCH" not in flags,
            "estimation_used": record.get("source") == "estimated",
            "anomaly_flags": flags,
            "verification_timestamp": datetime.now(timezone.utc).isoformat()
        }
        return record
```

### 4.2 Severity Levels

| Check | Severity | Auto-Correct | Blocks Storage |
|-------|----------|--------------|----------------|
| Token Arithmetic | Warning | Yes | No |
| Sanity Bounds | Critical | No | Yes |
| Cost Verification | Warning | Yes | No |
| Model Registry | Info | No | No |
| Latency Sanity | Warning | Yes | No |
| Anomaly Detection | Info | No | No |

---

## 5. Budget Enforcement

### 5.1 Budget Data Model

```python
{
    "scope": "user",           # global | user | project | user_project
    "scope_id": "alice",
    "limit_usd": 50.0,
    "reset_cycle": "monthly",  # daily | weekly | monthly | never
}
```

### 5.2 Scope Resolution

```
Request from user="alice", project="my-app"
         |
         +-- global budget (applies to everyone)
         +-- project:my-app budget
         +-- user:alice budget
         +-- user_project:alice:my-app budget
```

### 5.3 Spend Calculation

```python
def _calculate_current_spend(self, budget) -> float:
    now = datetime.now(timezone.utc)
    window_start = self._get_window_start(budget["reset_cycle"], now)

    total = 0.0
    for record in self.store.records:
        if record["timestamp"] >= window_start.isoformat():
            if self._record_matches_budget(record, budget):
                total += record["cost_usd"]
    return total
```

| Cycle | Window Start |
|-------|-------------|
| `daily` | 00:00 UTC today |
| `weekly` | 00:00 UTC Monday |
| `monthly` | 00:00 UTC 1st of month |
| `never` | All records |

---

## 6. Analytics & Aggregation

### 6.1 Running Totals (O(1))

```python
def _update_running_totals(self, record: dict):
    # Core dimensions always tracked
    dimensions = [
        ("global", "all"),
        ("provider", record["provider"]),
        ("model", record["model"]),
        ("user", record.get("user_id", "anonymous")),
        ("project", record.get("project_id", "default")),
        ("month", record["timestamp"][:7])
    ]
    # Conditional dimensions
    if record.get("conversation_id"):
        dimensions.append(("conversation", record["conversation_id"]))
    if record.get("agent_id"):
        dimensions.append(("agent", record["agent_id"]))

    for scope, scope_id in dimensions:
        key = f"{scope}:{scope_id}"
        agg = self.running_totals.setdefault(key, {"requests": 0, "input_tokens": 0,
                                                    "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0})
        agg["requests"] += 1
        agg["input_tokens"] += record["input_tokens"]
        agg["output_tokens"] += record["output_tokens"]
        agg["total_tokens"] += record["total_tokens"]
        agg["cost_usd"] += record["cost_usd"]
```

### 6.2 Efficiency & Cost Breakdown

```python
def get_efficiency_stats(self, scope, scope_id) -> dict:
    """Average output/input ratio, cache hit rate, total reasoning tokens."""
    records = [r for r in filtered_records if _matches_dimension(r, scope, scope_id)]
    return {
        "avg_efficiency": sum(output/input) / n,   # output tokens / input tokens
        "cache_hit_rate": cache_hits / n,
        "total_reasoning_tokens": sum(reasoning_tokens),
    }

def get_cost_breakdown(self, records) -> dict:
    """Split cost by category: completion, cached, embedding, tool_calls, media."""
```

### 6.3 Query Performance

| Query Type | Complexity | Source |
|------------|-----------|--------|
| Single scope summary | O(1) | Running totals |
| Dimension breakdown | O(k) | Running totals |
| Time-series trend | O(n) | Raw records |
| Efficiency stats | O(n) | Raw records |
| Cost breakdown | O(n) | Raw records |
| Filtered export | O(n) | Raw records |

---

## 7. Export System

### 7.1 CSV Export

```python
# Base fields always included
BASE_FIELDS = [
    "timestamp", "provider", "model", "user_id", "project_id",
    "input_tokens", "output_tokens", "total_tokens",
    "cost_usd", "latency_ms", "status", "source",
]
# AI-specific fields included when present
EXTRA_FIELDS = [
    "conversation_id", "agent_id", "prompt_hash",
    "reasoning_tokens", "cached_input_tokens", "embedding_tokens",
    "tool_call_count", "media_type", "cache_hit",
]

def export_csv(self, filepath: str, records: list[dict]):
    fieldnames = self.BASE_FIELDS + self.EXTRA_FIELDS
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow(record)
```

### 7.2 JSON Export

```python
def export_json(self, filepath: str, records: list[dict]):
    # Strip internal keys (starting with "_") before export
    cleaned = [{k: v for k, v in r.items() if not k.startswith("_")} for r in records]
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, indent=2, default=str)
```

---

## 8. System Monitoring

The `SystemMonitor` collects hardware and OS metrics at configurable intervals. It requires `psutil` (optional dependency — zeroed safe defaults without it).

### 8.1 Snapshot Schema

```python
{
    "timestamp": "2026-07-25T23:15:00.000000+00:00",
    "cpu": {
        "percent": 45.2,      # CPU utilization %
        "count": 16,          # Logical CPU count
        "freq": 2400.0        # Current frequency (MHz)
    },
    "ram": {
        "total": 34359738368,   # Bytes
        "available": 8589934592,
        "percent": 75.0,
        "used": 25769803776
    },
    "disk": {
        "C:\\": {"total": 500e9, "used": 200e9, "free": 300e9,
                 "percent": 40.0, "fstype": "NTFS"}
    },
    "network": {
        "bytes_sent": 123456789,
        "bytes_recv": 987654321,
        "packets_sent": 500000,
        "packets_recv": 800000
    },
    "internet": {
        "host": "8.8.8.8",
        "reachable": True,
        "latency_ms": 12.5
    },
    "gpu": {
        "NVIDIA GeForce RTX 3050": {
            "utilization_percent": 4.0,
            "memory_total_mb": 6144.0,
            "memory_used_mb": 724.0,
            "temperature_c": 47.0
        }
    },
    "temperature": {"cpu": 65.0},  # Celsius
    "power": {"power_draw_watts": 45.0},
    "processor": "Intel64 Family 6 Model ..."
}
```

### 8.2 Background Collection

```python
class SystemMonitor:
    def __init__(self, collection_interval=60.0):
        self.metrics = []
        self.interval = collection_interval
        self._stop = threading.Event()

    def start(self):
        self._thread = threading.Thread(target=self._collect_loop, daemon=True)
        self._thread.start()

    def _collect_loop(self):
        while not self._stop.wait(self.interval):
            self.metrics.append(self.snapshot())

    def stop(self):
        self._stop.set()
```

### 8.3 Metric Queries

```python
def get_metrics(self, start=None, end=None) -> list[dict]:
    if start is None and end is None:
        return list(self.metrics)
    return [m for m in self.metrics
            if (start is None or m["timestamp"] >= start.isoformat()) and
               (end is None or m["timestamp"] <= end.isoformat())]

def get_summary(self) -> dict:
    if not self.metrics:
        return {}
    latest = self.metrics[-1]
    return {
        "cpu_avg": sum(m["cpu"]["percent"] for m in self.metrics) / len(self.metrics),
        "ram_avg": sum(m["ram"]["percent"] for m in self.metrics) / len(self.metrics),
        "samples": len(self.metrics),
        "gpu": latest.get("gpu", {}) if "gpu" in latest else {},
    }
```

---

## 9. Memory Management

### 9.1 Growth Characteristics

| Records | Memory (approx) | Retention Behavior |
|---------|----------------|-------------------|
| 1,000 | ~2 MB | No eviction |
| 10,000 | ~20 MB | Ring buffer begins eviction at max_records |
| 100,000 | ~200 MB | Age-based retention trims old records |
| 1,000,000 | ~2 GB | Hard-limited by ring buffer maxlen |

### 9.2 Ring Buffer

TokenLedger uses `collections.deque(maxlen=...)` as the backing store:

```python
from collections import deque
self.records = deque(maxlen=max_records)
```

When `maxlen` is reached, the oldest record is automatically discarded on each new `append()`. This bounds memory to a predictable maximum regardless of request volume.

### 9.3 Retention

Age-based retention runs automatically on every insert. Records older than `retention_days` are pruned:

```python
def _apply_retention(self) -> None:
    if self.retention.max_age_days < 0:
        return
    cutoff = (datetime.now(timezone.utc) - timedelta(days=self.retention.max_age_days)).isoformat()
    pruned = [r for r in self.records if r.get("timestamp", "") > cutoff]
    if len(pruned) < len(self.records):
        self.records = deque(pruned, maxlen=self.retention.max_records)
        self.running_totals.clear()       # Rebuild from scratch
        for r in self.records:
            self._update_running_totals(r)
```

Running totals are rebuilt from the surviving records after any pruning to ensure O(1) query correctness.

---

## 10. Concurrency Model

### 10.1 Thread Safety

TokenLedger uses a **reentrant lock (RLock)** to protect shared state:

```python
class MemoryStore:
    def __init__(self):
        self.lock = threading.RLock()

    def insert_record(self, record: dict):
        with self.lock:
            self.records.append(record)
            self._update_running_totals(record)
```

**Lock Scope:**
- Record insertion
- Running totals update
- Budget spend calculation
- File persistence append

**No Lock Needed:**
- Read-only analytics queries
- Pricing registry lookups

### 10.2 Async Compatibility

```python
async def tracked_create(self, *args, **kwargs):
    # Budget check (synchronous, fast)
    self.ledger._check_budget(...)

    # API call (async)
    response = await original_create(*args, **kwargs)

    # Storage (synchronous, under lock, ~microseconds)
    self.ledger._store_record(record)

    return response
```

### 10.3 Multi-Process Considerations

TokenLedger is designed for **single-process** applications. For multi-process deployments:

- Each process maintains its own in-memory state.
- File persistence (`persist_path`) provides a shared append-only log.
- Budgets are **per-process** unless external coordination is added.
- For true multi-process budget enforcement, use a shared Redis/Memcached layer.

---

## Summary

| Component | Data Structure | Persistence | Query Complexity |
|-----------|---------------|-------------|------------------|
| Records | `deque[dict]` (ring buffer) | Optional checksummed JSONL | O(n) |
| Running Totals | `dict[str, dict]` | Rebuilt on load | O(1) |
| Budgets | `dict[str, dict]` | In-memory only | O(k) |
| Pricing | `dict[str, dict]` | Hardcoded + custom | O(1) |
| System Metrics | `list[dict]` | In-memory only | O(n) |
| Exports | File generation | CSV / JSON | O(n) |
| Retention Policy | `RetentionPolicy` | In-memory config | O(n) on insert |
| Checksums | SHA-256 per record | Embedded in JSONL | O(n) on verify |

---

## Next Steps

- **Workflow**: See `WORKFLOW.md` for the end-to-end call lifecycle.
- **Architecture**: See `ARCHITECTURE.md` for system design and component relationships.
