# Getting Started

## Installation

```bash
pip install tokenledger
```

## Basic Usage

```python
from tokenledger import TokenLedger

ledger = TokenLedger()
ledger.record_usage("openai", "gpt-4", input_tokens=100, output_tokens=50)
print(ledger.get_summary())
```

## Wrapping Providers

```python
import openai
client = openai.OpenAI()
ledger.wrap_openai(client)
```

## Budget Enforcement

```python
ledger.set_budget("user", "alice", limit_usd=10.0)
```
