# TokenLedger

Lightweight governance layer for LLM applications — zero database required.

Track, budget, and analyze LLM API usage with in-memory or SQLite storage.

## Quick Start

```python
from tokenledger import TokenLedger

ledger = TokenLedger()

# Manual recording
ledger.record_usage("openai", "gpt-4", input_tokens=100, output_tokens=50)

# Budget enforcement
ledger.set_budget("user", "alice", limit_usd=10.0)

# Wrap provider clients
client = ledger.wrap_openai(openai_client)

# Decorator
@ledger.track(provider="openai", model="gpt-4")
def call_llm(messages):
    return client.chat.completions.create(model="gpt-4", messages=messages)
```
