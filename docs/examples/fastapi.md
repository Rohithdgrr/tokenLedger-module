# FastAPI Integration Example

A production-grade FastAPI app using TokenLedger for LLM usage tracking, budget enforcement, and analytics.

## Complete Example

```python
"""FastAPI app with TokenLedger integration."""
import os
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from openai import OpenAI
from tokenledger import TokenLedger, BudgetExceededError

app = FastAPI(title="LLM Gateway")

# Initialize ledger with persistence
ledger = TokenLedger(
    persist_path="usage.jsonl",
    unknown_model_policy="estimate",
    ghost_mode=os.getenv("GHOST_MODE", "false").lower() == "true",
)

# Wrap OpenAI client
client = OpenAI()
wrapped = ledger.wrap_openai(client)


# -- Schemas --

class ChatRequest(BaseModel):
    messages: list
    model: str = "gpt-4o-mini"
    user_id: str = "anonymous"
    project_id: str = "default"
    agent_id: str | None = None
    conversation_id: str | None = None
    stream: bool = False


class UsageResponse(BaseModel):
    record_id: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


# -- Dependencies --

def get_ledger():
    return ledger


# -- Routes --

@app.post("/chat", response_model=UsageResponse | dict)
async def chat(req: ChatRequest, ledger: TokenLedger = Depends(get_ledger)):
    """Proxy chat completion with automatic tracking."""
    kwargs = {
        "model": req.model,
        "messages": req.messages,
        "user_id": req.user_id,
        "project_id": req.project_id,
        "agent_id": req.agent_id,
        "conversation_id": req.conversation_id,
        "stream": req.stream,
    }

    if req.stream:
        return {"message": "Streaming response", "stream": True}

    try:
        response = wrapped.chat.completions.create(**kwargs)
        usage = response.usage
        return UsageResponse(
            record_id=str(response.id),
            provider="openai",
            model=req.model,
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            cost_usd=ledger.get_pricing(req.model, req.model).get("cost_usd", 0),
        )
    except BudgetExceededError as e:
        raise HTTPException(status_code=402, detail=str(e))


@app.get("/usage/summary")
def usage_summary(ledger: TokenLedger = Depends(get_ledger)):
    """Get aggregated usage summary."""
    return ledger.get_summary()


@app.get("/usage/by-agent")
def usage_by_agent(ledger: TokenLedger = Depends(get_ledger)):
    """Usage breakdown by agent."""
    return ledger.get_spending_by_agent()


@app.get("/usage/export")
def export_usage(format: str = "csv", ledger: TokenLedger = Depends(get_ledger)):
    """Export usage data."""
    import tempfile, os
    path = os.path.join(tempfile.gettempdir(), f"usage.{format}")
    if format == "csv":
        ledger.export_csv(path)
    else:
        ledger.export_json(path)
    return {"exported": path}


@app.get("/budgets")
def list_budgets(ledger: TokenLedger = Depends(get_ledger)):
    """List all budgets."""
    return ledger.store.get_all_budgets()


@app.post("/budgets")
def set_budget(scope: str, scope_id: str, limit_usd: float,
               ledger: TokenLedger = Depends(get_ledger)):
    """Set a budget."""
    ledger.set_budget(scope, scope_id, limit_usd)
    return {"status": "ok", "scope": scope, "scope_id": scope_id, "limit_usd": limit_usd}


@app.get("/verify")
def verify_immutability(ledger: TokenLedger = Depends(get_ledger)):
    """Check record integrity."""
    tampered = ledger.verify_immutability()
    return {"tampered_count": len(tampered), "tampered_ids": tampered}


@app.get("/health")
def health(ledger: TokenLedger = Depends(get_ledger)):
    """Health check with store stats."""
    return ledger.get_health()


@app.get("/roi/{scope}/{scope_id}")
def roi(scope: str, scope_id: str, ledger: TokenLedger = Depends(get_ledger)):
    """Get ROI for a scope."""
    return ledger.get_roi(scope, scope_id)


# -- Run --
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

## Run

```bash
pip install fastapi uvicorn openai tokenledger
python app.py
# Visit http://localhost:8000/docs for Swagger UI
```
