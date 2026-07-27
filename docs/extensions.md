# Extensions

## CLI

```bash
tokenledger summary              # Aggregated usage
tokenledger summary --detail     # With per-provider breakdown
tokenledger export --format csv -o report.csv
tokenledger export --format json -o report.json
tokenledger verify               # Check checksum integrity
tokenledger compact              # Force retention pruning
tokenledger health               # Store health and status
tokenledger update-pricing path/to/pricing.json
```

## SQLite Store

```python
from tokenledger import TokenLedger
from tokenledger.ext.sqlite_store import SqliteStore

store = SqliteStore("usage.db")
ledger = TokenLedger(store=store)
```

## OpenTelemetry

```python
from tokenledger.ext.opentelemetry import instrument_tokenledger

instrument_tokenledger(ledger, tracer_name="my-app")
```

All record operations are traced with spans containing token and cost attributes.

## Webhook Notifier

```python
from tokenledger.ext.notifier import WebhookNotifier

notifier = WebhookNotifier(
    budget_exceeded_url="https://hooks.slack.com/...",
    record_url="https://api.example.com/usage",
)

# Wire up callbacks
ledger.interceptor.on_budget_exceeded = notifier.on_budget_exceeded
ledger.interceptor.on_record = notifier.on_record
```

## Async Store Wrapper

```python
from tokenledger.ext.async_store import ThreadedMemoryStore

async_store = ThreadedMemoryStore(base_store=ledger.store)
# All insert/query operations use a background thread
```

## Per-Provider Configuration

```python
ledger.interceptor.configure_provider("openai", max_retries=5, rate_limit_rps=50)
```

Override retry, timeout, circuit breaker, and rate limit per provider.

## External Pricing

```json
{
    "_meta": {"last_updated": "2026-07-27"},
    "my_provider": {
        "custom-model": {"input_per_1k": 0.01, "output_per_1k": 0.02}
    }
}
```

```python
# Auto-loaded from pricing_data.json in CWD or alongside module
ledger = TokenLedger()
ledger.update_pricing("pricing_data.json")
```
