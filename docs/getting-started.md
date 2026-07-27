# Getting Started

## Installation

```bash
pip install tokenledger
```

## Basic Usage

### 1. Record Usage Manually

```python
from tokenledger import TokenLedger

ledger = TokenLedger()
ledger.record_usage(
    provider="openai",
    model="gpt-4o",
    input_tokens=150,
    output_tokens=75,
    user_id="alice",
    project_id="my-app",
)
```

### 2. Set a Budget

```python
ledger.set_budget(
    scope="project",
    scope_id="my-app",
    limit_usd=50.00,
    reset_cycle="monthly",
)
```

If the project exceeds $50 this month, `BudgetExceededError` is raised **before** the API call.

### 3. Wrap Providers

```python
from openai import OpenAI

client = OpenAI()
wrapped = ledger.wrap_openai(client)

response = wrapped.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello!"}],
)
```

### 4. View Analytics

```python
summary = ledger.get_summary()
print(f"Requests: {summary['requests']}")
print(f"Total tokens: {summary['total_tokens']}")
print(f"Total cost: ${summary['cost_usd']:.4f}")

# By provider
for row in ledger.get_spending_by_provider():
    print(f"{row['id']}: ${row['cost_usd']:.4f}")

# Efficiency
eff = ledger.get_efficiency()
print(f"Output/input ratio: {eff['avg_efficiency']:.2f}")
```

### 5. Export

```python
ledger.export_csv("report.csv")
ledger.export_json("report.json")
```

## Supported Providers

| Provider | Wrapper Method | Token Source |
|---|---|---|
| OpenAI | `wrap_openai()` | API-reported / tiktoken |
| Anthropic | `wrap_anthropic()` | API-reported |
| Google Gemini | `wrap_gemini()` | API-reported |
| Groq | `wrap_groq()` | API-reported |
| OpenRouter | `wrap_openrouter()` | API-reported |
| Ollama | `wrap_ollama()` | Estimated |
| DeepSeek | `wrap_deepseek()` | API-reported |
| Mistral | `wrap_mistral()` | API-reported |
| Cohere | `wrap_cohere()` | API-reported |
| NVIDIA | `wrap_nvidia()` | API-reported |
| Kimi | `wrap_kimi()` | API-reported |
| GLM | `wrap_glm()` | API-reported |
| MiniMax | `wrap_minimax()` | API-reported |
| Together | `wrap_together()` | API-reported |
| Perplexity | `wrap_perplexity()` | API-reported |
| Custom | `record_usage()` | Manual |

## Retention & Persistence

```python
# File persistence with ring buffer and age-based retention
ledger = TokenLedger(
    persist_path="usage.jsonl",
    max_records=10_000,
    retention_days=90,
)

# Manual retention
ledger.apply_retention(max_age_days=30)
```
