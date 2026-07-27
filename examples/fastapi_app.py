"""Production-grade FastAPI app with TokenLedger for LLM usage tracking."""
import os
import tempfile
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from openai import OpenAI
from tokenledger import TokenLedger, BudgetExceededError

app = FastAPI(title="LLM Gateway", version="1.0.0")

ledger = TokenLedger(
    persist_path=os.getenv("LEDGER_PATH", "usage.jsonl"),
    ghost_mode=os.getenv("GHOST_MODE", "false").lower() == "true",
)

client = OpenAI()
wrapped = ledger.wrap_openai(client)


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


@app.post("/chat")
async def chat(req: ChatRequest):
    kwargs = {
        "model": req.model, "messages": req.messages,
        "user_id": req.user_id, "project_id": req.project_id,
        "agent_id": req.agent_id, "conversation_id": req.conversation_id,
        "stream": req.stream,
    }
    if req.stream:
        return {"stream": True, "message": "streaming response"}
    try:
        response = wrapped.chat.completions.create(**kwargs)
        u = response.usage
        return UsageResponse(
            record_id=str(response.id), provider="openai", model=req.model,
            input_tokens=u.prompt_tokens, output_tokens=u.completion_tokens,
            cost_usd=ledger.get_pricing("openai", req.model).get("input_per_token", 0) * u.prompt_tokens
                    + ledger.get_pricing("openai", req.model).get("output_per_token", 0) * u.completion_tokens,
        )
    except BudgetExceededError as e:
        raise HTTPException(status_code=402, detail=str(e))


@app.get("/usage/summary")
def usage_summary():
    return ledger.get_summary()


@app.get("/usage/by-agent")
def usage_by_agent():
    return ledger.get_spending_by_agent()


@app.get("/usage/export")
def export_usage(format: str = "csv"):
    path = os.path.join(tempfile.gettempdir(), f"usage.{format}")
    getattr(ledger, f"export_{format}")(path)
    return {"exported": path}


@app.post("/budgets")
def set_budget(scope: str, scope_id: str, limit_usd: float):
    ledger.set_budget(scope, scope_id, limit_usd)
    return {"scope": scope, "scope_id": scope_id, "limit_usd": limit_usd}


@app.get("/budgets")
def list_budgets():
    return ledger.store.get_all_budgets()


@app.get("/verify")
def verify():
    tampered = ledger.verify_immutability()
    return {"tampered": tampered, "count": len(tampered)}


@app.get("/health")
def health():
    return ledger.get_health()


@app.get("/roi/{scope}/{scope_id}")
def roi(scope: str, scope_id: str):
    return ledger.get_roi(scope, scope_id)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
