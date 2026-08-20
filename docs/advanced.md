# Advanced Usage

## Streaming

```python
stream = wrapped.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Stream this"}],
    stream=True,
)
for chunk in stream:
    print(chunk.choices[0].delta.content)
# Usage automatically accumulated and recorded after stream completes
```

## @track Decorator

```python
@ledger.track(provider="openai", model="gpt-4", user_id="alice")
def ask_llm(messages):
    return client.chat.completions.create(model="gpt-4", messages=messages)
```

Records usage automatically from the return value. Supports manual `input_tokens`/`output_tokens` override.

## System Monitoring

```python
from tokenledger.core.system import SystemMonitor

monitor = SystemMonitor(collection_interval=10.0)
monitor.start()

ledger = TokenLedger(system_monitor=monitor)
ledger.record_usage("openai", "gpt-4o", 100, 50, system_context=True)
# Record includes CPU, RAM, disk, GPU metrics at time of request
```

## Obfuscation-at-Rest

```python
ledger = TokenLedger(
    persist_path="usage.jsonl",
    encryption_key="my-secret-key",  # str or bytes; normalized internally
)
```

All persisted JSONL data is encrypted with Fernet (AES-128-CBC + HMAC) when the
`cryptography` package is installed (`pip install "tokenledger-module[security]"`).
Without it, files are XOR-obfuscated and HMAC-tagged — a casual-privacy fallback,
**not** encryption, and not suitable for compliance-grade secrets. Files written
with a key are unreadable without it (wrong or missing keys fail with a warning
and load zero records).

## Prompt Redaction

```python
ledger = TokenLedger(redact_prompts=True)
# prompt_hash is SHA-256 hashed before storage
# Raw prompt content is never persisted
```

## Differential Privacy

```python
ledger = TokenLedger(differential_privacy_epsilon=1.0)
noisy = ledger.get_records(apply_dp=True)          # Laplace noise on copies
bundle = ledger.export_audit_json(apply_dp=True)   # or on any export
# stored records, budgets, and analytics always use exact figures
# Lower epsilon = more privacy, less accuracy
```

## Audit Export

```python
audit = ledger.export_audit_json("audit.json")
# {
#   "exported_at": "2026-07-27T...",
#   "record_count": 100,
#   "verified": [],
#   "_checksum": "sha256...",
#   "records": [...]
# }
```

## Multi-Tenant

```python
ledger.record_usage("openai", "gpt-4", 100, 50, tenant_id="org-acme")
for row in ledger.get_spending_by_tenant():
    print(f"{row['id']}: ${row['cost_usd']:.4f}")
```

## Custom Pricing

```python
ledger.register_pricing("my-api", "my-model", input_cost_per_1k=0.01, output_cost_per_1k=0.03)
```

## Pluggable Verification Rules

```python
from tokenledger.core.verifier import VerificationRule

class MyRule(VerificationRule):
    def check(self, record, response=None):
        if record.get("total_tokens", 0) > 1_000_000:
            return {"rule": "my_rule", "status": "warn", "message": "High token count"}
        return {"rule": "my_rule", "status": "pass"}

ledger.verifier.add_rule(MyRule())
```

## Resilience Configuration

```python
ledger.interceptor.max_retries = 5
ledger.interceptor.retry_delay = 2.0
ledger.interceptor.circuit_breaker_threshold = 10
ledger.interceptor.circuit_recovery_timeout = 60.0
ledger.interceptor.rate_limit_rps = 50
ledger.interceptor.request_timeout = 30.0
```

## Usage Blocks

`ledger.usage()` wraps a block of application code and records exactly one
usage event on exit — latency measured from entry, tokens estimated from the
messages (plus `output_text` when supplied), and `status="error"` when the
block raises. It works for sync and async code.

```python
with ledger.usage("openai", "gpt-4o",
                  messages=[{"role": "user", "content": "Hello"}],
                  user_id="alice", conversation_id="c-42"):
    reply = client.chat.completions.create(...)

# async equivalent
async with ledger.usage("openai", "gpt-4o", messages=[...]):
    reply = await aclient.chat.completions.create(...)
```

Extra keyword arguments pass through to `record_usage()`, so all dimensions
(tenant, agent, prompt_hash, reasoning_tokens, ...) work the same way.
Records carry `source="usage_block"` and are excluded from nothing — they flow
through the normal policy, budget, ghost, and verification pipeline.

## Cost Preview

Estimate tokens and cost before a request is made. Nothing is stored.

```python
preview = ledger.cost_preview([{"role": "user", "content": "Explain circuits."}],
                              "gpt-4o", "openai", output_text="A short summary.")
# {'input_tokens': 7, 'output_tokens': 3, 'total_tokens': 10,
#  'cost_usd': 1.76e-05, 'source': 'estimated'}
```

The same preview is available from the CLI:

```bash
tokenledger cost "Explain circuits." --model gpt-4o --output-text "A summary."
```

## Budget Wallets

Wallets are per-user prepaid allowances layered over the budget engine. They
support reserve-checking debits, top-ups, a live balance, and a one-shot
low-balance alarm.

```python
wallet = ledger.create_wallet("alice", 10.0, low_balance_threshold=0.2,
                              on_low_balance=lambda w: alert(f"alice at ${w.balance():.2f}"))

wallet.debit("openai", "gpt-4o", input_tokens=100, output_tokens=50)   # ok
try:
    wallet.debit("openai", "gpt-4o", input_tokens=4_000_000)           # 5M? costs ~$20 > $10
except WalletExhaustedError as e:
    print(e.current_spend, e.limit)                                     # 0.0, 10.0
```

`debit()` estimates the request cost from the provider pricing registry and
raises `WalletExhaustedError` (a `BudgetExceededError`) when the request would
push spend past the limit. Spend is window-aware (`reset_cycle`) — balances
re-fill automatically when a daily/weekly/monthly window rolls over. The
low-balance alarm fires at most once per cycle and re-arms on `refill()` or
`reset()`.

## Live Spend Server

`ledger.serve()` starts a dependency-free daemon HTTP server (stdlib only) on
`127.0.0.1:8765` by default.

```python
with ledger.serve(port=8765):            # context manager; stop() on exit
    ...                                  # point a dashboard at localhost:8765
```

| Endpoint | Response |
|---|---|
| `GET /stats` | JSON snapshot: `record_count`, `total_tokens`, `cost_usd`, per-provider breakdown, `running_totals["global:all"]`, `generated_at` |
| `GET /stream` | Server-Sent Events — `event: record` per recorded usage, `: ping` heartbeat every 15 seconds |

CORS is enabled on both endpoints so browser dashboards can read them without
a proxy. The server hooks `interceptor.on_record` but chains the previous
callback, and restores it on `stop()`. Use `port=0` for an ephemeral port and
read `server.port` after `start()`.

## Logging Adapter

Stream usage into the standard `logging` module for exporters, SIEMs, or
JSON-structured telemetry.

```python
import logging
logging.basicConfig(level=logging.INFO)

from tokenledger import attach_log_handler, detach_log_handler

hook = attach_log_handler(ledger)               # logs "tokenledger.spend"
ledger.record_usage("openai", "gpt-4o", 10, 5)
# tokenledger.spend: tokenledger usage
#   extra['provider']='openai', extra['model']='gpt-4o', extra['input_tokens']=10, ...

detach_log_handler(ledger, hook)                # restore the previous sink
```

A formatter can reference the fields directly, e.g.
`"%(provider)s %(model)s $%(cost_usd).6f"`. Multiple `attach_log_handler()`
calls chain; `detach_log_handler(ledger, hook)` removes one and restores the
callback that was active before it was attached.
