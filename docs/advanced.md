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

All persisted JSONL data is XOR-obfuscated and HMAC-tagged. Files written with
a key are unreadable without it (wrong or missing keys fail with a warning and
load zero records), but XOR is **not** strong encryption — it is casual
privacy only, not suitable for compliance-grade secrets.

## Prompt Redaction

```python
ledger = TokenLedger(redact_prompts=True)
# prompt_hash is SHA-256 hashed before storage
# Raw prompt content is never persisted
```

## Differential Privacy

```python
ledger = TokenLedger(differential_privacy_epsilon=1.0)
# Laplace noise added to input_tokens, output_tokens, cost_usd
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
