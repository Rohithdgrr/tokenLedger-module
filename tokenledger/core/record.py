"""Shared record-building logic — used by both record_usage and interceptor."""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .pricing import PricingRegistry


def build_record(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    user_id: str = "anonymous",
    project_id: str = "default",
    latency_ms: Optional[float] = None,
    source: str = "manual",
    status: str = "success",
    cost_usd: Optional[float] = None,
    pricing: Optional[PricingRegistry] = None,
    tenant_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    prompt_hash: Optional[str] = None,
    reasoning_tokens: int = 0,
    cached_input_tokens: int = 0,
    embedding_tokens: int = 0,
    tool_calls: Optional[List[Dict[str, Any]]] = None,
    media_type: Optional[str] = None,
    cache_hit: bool = False,
) -> Dict[str, Any]:
    """Shared record-building logic used by both record_usage and interceptor."""
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("Token counts cannot be negative")

    total_tokens = input_tokens + output_tokens
    if cost_usd is None and pricing:
        cost_usd = pricing.calculate_cost(provider, model, input_tokens, output_tokens)
    cost_usd = cost_usd or 0.0

    record: Dict[str, Any] = {
        "record_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "provider": provider,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cost_usd": round(cost_usd, 10),
        "latency_ms": latency_ms if latency_ms is not None else 0,
        "user_id": user_id,
        "project_id": project_id,
        "status": status,
        "source": source,
    }
    if tenant_id:
        record["tenant_id"] = tenant_id
    if conversation_id:
        record["conversation_id"] = conversation_id
    if agent_id:
        record["agent_id"] = agent_id
    if prompt_hash:
        record["prompt_hash"] = prompt_hash
    if reasoning_tokens:
        record["reasoning_tokens"] = reasoning_tokens
    if cached_input_tokens:
        record["cached_input_tokens"] = cached_input_tokens
    if embedding_tokens:
        record["embedding_tokens"] = embedding_tokens
        record["embedding"] = True
    if tool_calls:
        record["tool_calls"] = tool_calls
        record["tool_call_count"] = len(tool_calls)
    if media_type:
        record["media_type"] = media_type
    if cache_hit:
        record["cache_hit"] = True
    return record
