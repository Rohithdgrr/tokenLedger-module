"""Main TokenLedger implementation."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .analytics import AnalyticsEngine
from .budget import BudgetEnforcer
from .system import SystemMonitor
from .estimator import TokenEstimator
from .extractor import TokenExtractor
from .interceptor import InterceptionLayer
from .pricing import PricingRegistry
from .store import MemoryStore
from .verifier import VerificationEngine
from ..utils.export import ExportEngine


class TokenLedger:
    """Lightweight governance layer for LLM usage tracking."""

    def __init__(
        self,
        persist_path: Optional[str] = None,
        unknown_model_policy: str = "estimate",
        system_monitor: Optional[SystemMonitor] = None,
        max_records: int = 100_000,
        retention_days: int = 90,
    ):
        self.store = MemoryStore(
            persist_path=persist_path,
            max_records=max_records,
            retention_days=retention_days,
        )
        self.pricing = PricingRegistry()
        self.extractor = TokenExtractor()
        self.estimator = TokenEstimator()
        self.verifier = VerificationEngine(self.pricing, self.store)
        self.budget_enforcer = BudgetEnforcer(self.store, self.pricing)
        self.analytics = AnalyticsEngine(self.store)
        self.exporter = ExportEngine(self.store)
        self.system_monitor = system_monitor
        self.interceptor = InterceptionLayer(
            ledger=self,
            store=self.store,
            pricing=self.pricing,
            enforcer=self.budget_enforcer,
            extractor=self.extractor,
            estimator=self.estimator,
            verifier=self.verifier,
            unknown_model_policy=unknown_model_policy,
        )

    def wrap_openai(self, client: Any) -> Any:
        return self.interceptor.wrap_openai(client)

    def wrap_anthropic(self, client: Any) -> Any:
        return self.interceptor.wrap_anthropic(client)

    def wrap_groq(self, client: Any) -> Any:
        return self.interceptor.wrap_groq(client)

    def wrap_gemini(self, client: Any) -> Any:
        return self.interceptor.wrap_gemini(client)

    def wrap_ollama(self, client: Any) -> Any:
        return self.interceptor.wrap_ollama(client)

    def wrap_openrouter(self, client: Any) -> Any:
        return self.interceptor.wrap_openai(client)

    def wrap_deepseek(self, client: Any) -> Any:
        return self.interceptor.wrap_openai(client, provider="deepseek")

    def wrap_mistral(self, client: Any) -> Any:
        return self.interceptor.wrap_openai(client, provider="mistral")

    def wrap_cohere(self, client: Any) -> Any:
        return self.interceptor.wrap_openai(client, provider="cohere")

    def wrap_nvidia(self, client: Any) -> Any:
        return self.interceptor.wrap_openai(client, provider="nvidia")

    def wrap_kimi(self, client: Any) -> Any:
        return self.interceptor.wrap_openai(client, provider="kimi")

    def wrap_glm(self, client: Any) -> Any:
        return self.interceptor.wrap_openai(client, provider="glm")

    def wrap_minimax(self, client: Any) -> Any:
        return self.interceptor.wrap_openai(client, provider="minimax")

    def wrap_together(self, client: Any) -> Any:
        return self.interceptor.wrap_openai(client, provider="together")

    def wrap_perplexity(self, client: Any) -> Any:
        return self.interceptor.wrap_openai(client, provider="perplexity")

    def record_usage(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        user_id: str = "anonymous",
        project_id: str = "default",
        latency_ms: Optional[float] = None,
        source: str = "manual",
        system_context: bool = False,
        conversation_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        prompt_hash: Optional[str] = None,
        reasoning_tokens: int = 0,
        cached_input_tokens: int = 0,
        embedding_tokens: int = 0,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        media_type: Optional[str] = None,
        cache_hit: bool = False,
        status: str = "success",
    ) -> Dict[str, Any]:
        """Record a completed usage event."""
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("Token counts cannot be negative")
        if system_context and not self.system_monitor:
            raise ValueError("system_context=True requires a SystemMonitor instance")

        total_tokens = input_tokens + output_tokens
        cost_usd = self.pricing.calculate_cost(provider, model, input_tokens, output_tokens)

        record: Dict[str, Any] = {
            "record_id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "provider": provider,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "cost_usd": cost_usd,
            "latency_ms": latency_ms if latency_ms is not None else 0,
            "user_id": user_id,
            "project_id": project_id,
            "status": status,
            "source": source,
        }

        if conversation_id:
            record["conversation_id"] = conversation_id
        if agent_id:
            record["agent_id"] = agent_id
        if prompt_hash:
            record["prompt_hash"] = prompt_hash
        else:
            record["prompt_hash"] = ""
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

        self.budget_enforcer.check_budget(
            user_id=user_id,
            project_id=project_id,
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        if system_context:
            record["system"] = self.system_monitor.snapshot()

        verified = self.verifier.verify(record)
        self.store.insert_record(verified)
        if self.interceptor.on_record:
            self.interceptor.on_record(verified)
        return verified

    def get_records(self) -> List[Dict[str, Any]]:
        return self.store.get_records()

    def set_budget(
        self,
        scope: str,
        scope_id: str,
        limit_usd: float,
        reset_cycle: str = "monthly",
    ) -> None:
        self.store.set_budget(
            scope,
            scope_id,
            {
                "scope": scope,
                "scope_id": scope_id,
                "limit_usd": limit_usd,
                "reset_cycle": reset_cycle,
            },
        )

    def get_summary(self, scope: str = "global", scope_id: str = "all") -> Dict[str, Any]:
        return self.analytics.get_summary(scope, scope_id)

    def get_spending_by_provider(self) -> List[Dict[str, Any]]:
        return self.analytics.get_spending_by_dimension("provider")

    def get_spending_by_dimension(self, dimension: str) -> List[Dict[str, Any]]:
        return self.analytics.get_spending_by_dimension(dimension)

    def export_csv(self, filepath: str) -> None:
        self.exporter.export_csv(filepath, self.get_records())

    def export_json(self, filepath: str) -> None:
        self.exporter.export_json(filepath, self.get_records())

    def register_pricing(
        self,
        provider: str,
        model: str,
        input_cost_per_1k: float,
        output_cost_per_1k: float,
    ) -> None:
        self.pricing.register_custom(provider, model, input_cost_per_1k, output_cost_per_1k)

    def get_pricing(self, provider: str, model: str) -> Dict[str, Any]:
        return self.pricing.get_pricing(provider, model)

    def apply_retention(self, max_age_days: Optional[int] = None) -> None:
        """Manually trigger age-based retention."""
        if max_age_days is not None:
            self.store.retention.max_age_days = max_age_days
        self.store._apply_retention()

    def verify_immutability(self) -> List[str]:
        """Return list of tampered record_ids, empty if all clean."""
        return self.store.verify_immutability()

    def get_efficiency(self, scope: str = "global", scope_id: str = "all") -> Dict[str, Any]:
        return self.analytics.get_efficiency_stats(scope, scope_id)

    @staticmethod
    def fingerprint_prompt(messages: List[Dict[str, Any]]) -> str:
        """Deterministic prompt fingerprint ignoring minor whitespace."""
        raw = json.dumps(messages, sort_keys=True, default=str).strip()
        return hashlib.sha256(raw.encode()).hexdigest()

    def get_spending_by_conversation(self) -> List[Dict[str, Any]]:
        return self.analytics.get_spending_by_dimension("conversation")

    def get_spending_by_agent(self) -> List[Dict[str, Any]]:
        return self.analytics.get_spending_by_dimension("agent")
