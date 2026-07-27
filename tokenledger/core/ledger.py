"""Main TokenLedger implementation."""

from __future__ import annotations

import functools
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar

from ..ext.autodetect import auto_detect as _auto_detect
from ..ext.differentiators import (
    CostContract,
    CostContractRegistry,
    EstimatorFeedback,
    LocalModelCost,
    LocalModelRegistry,
    ModelRouter,
    PromptCache,
    PromptEvolutionTracker,
    RouteOption,
)
from ..ext.differentiators import (
    compute_roi as _compute_roi,
)
from ..ext.differentiators import (
    sign_ledger as _sign_ledger,
)
from ..ext.differentiators import (
    simulate_cost as _simulate_cost,
)
from ..ext.differentiators import (
    verify_signed_ledger as _verify_signed_ledger,
)
from ..utils.export import ExportEngine
from .analytics import AnalyticsEngine
from .budget import BudgetEnforcer, BudgetExceededError
from .estimator import TokenEstimator
from .extractor import TokenExtractor
from .interceptor import InterceptionLayer
from .pricing import PricingRegistry
from .record import build_record
from .store import MemoryStore, StorageBackend
from .system import SystemMonitor
from .verifier import VerificationEngine

F = TypeVar("F", bound=Callable)

class TokenLedger:
    """Lightweight governance layer for LLM usage tracking."""

    def __init__(
        self,
        persist_path: str | None = None,
        unknown_model_policy: str = "estimate",
        system_monitor: SystemMonitor | None = None,
        max_records: int = 100_000,
        retention_days: int = 90,
        store: StorageBackend | None = None,
        encryption_key: bytes | None = None,
        differential_privacy_epsilon: float | None = None,
        redact_prompts: bool = False,
        ghost_mode: bool = False,
    ):
        self.store = store or MemoryStore(
            persist_path=persist_path,
            max_records=max_records,
            retention_days=retention_days,
            encryption_key=encryption_key,
        )
        self.pricing = PricingRegistry()
        self.extractor = TokenExtractor()
        self.estimator = TokenEstimator()
        self.verifier = VerificationEngine(self.pricing, self.store)
        self.budget_enforcer = BudgetEnforcer(self.store, self.pricing)
        self.analytics = AnalyticsEngine(self.store)
        self.exporter = ExportEngine(self.store)
        self.system_monitor = system_monitor
        self.differential_privacy_epsilon = differential_privacy_epsilon
        self.redact_prompts = redact_prompts
        self.ghost_mode = ghost_mode
        self.interceptor = InterceptionLayer(
            ledger=self,
            store=self.store,
            pricing=self.pricing,
            enforcer=self.budget_enforcer,
            extractor=self.extractor,
            estimator=self.estimator,
            verifier=self.verifier,
            unknown_model_policy=unknown_model_policy,
            ghost_mode=getattr(self, 'ghost_mode', False),
        )
        self.prompt_cache = PromptCache()
        self.estimator_feedback = EstimatorFeedback()
        self.model_router = ModelRouter()
        self.cost_contracts = CostContractRegistry()
        self.prompt_evolution = PromptEvolutionTracker()
        self.local_models = LocalModelRegistry()

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

    def track(
        self,
        provider: str,
        model: str,
        user_id: str = "anonymous",
        project_id: str = "default",
        **tracking_kwargs: Any,
    ) -> Callable:
        def decorator(func: Callable) -> Callable:
            @functools.wraps(func)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                result = func(*args, **kwargs)
                inp = tracking_kwargs.pop("input_tokens", 0)
                out = tracking_kwargs.pop("output_tokens", 0)
                if not inp and not out:
                    td = self.extractor.extract(result, provider)
                    if td:
                        inp = td.get("input_tokens", 0)
                        out = td.get("output_tokens", 0)
                self.record_usage(provider=provider, model=model, user_id=user_id,
                                  project_id=project_id, input_tokens=inp or 0,
                                  output_tokens=out or 0, **tracking_kwargs)
                return result
            return wrapper
        return decorator

    def record_usage(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        user_id: str = "anonymous",
        project_id: str = "default",
        latency_ms: float | None = None,
        source: str = "manual",
        system_context: bool = False,
        tenant_id: str | None = None,
        conversation_id: str | None = None,
        agent_id: str | None = None,
        prompt_hash: str | None = None,
        reasoning_tokens: int = 0,
        cached_input_tokens: int = 0,
        embedding_tokens: int = 0,
        tool_calls: list[dict[str, Any]] | None = None,
        media_type: str | None = None,
        cache_hit: bool = False,
        status: str = "success",
    ) -> dict[str, Any]:
        if system_context and not self.system_monitor:
            raise ValueError("system_context=True requires a SystemMonitor instance")

        if self.redact_prompts and prompt_hash:
            prompt_hash = hashlib.sha256(prompt_hash.encode()).hexdigest()

        record = build_record(
            provider=provider, model=model,
            input_tokens=input_tokens, output_tokens=output_tokens,
            user_id=user_id, project_id=project_id,
            latency_ms=latency_ms, source=source, status=status,
            pricing=self.pricing,
            tenant_id=tenant_id,
            conversation_id=conversation_id, agent_id=agent_id,
            prompt_hash=prompt_hash,
            reasoning_tokens=reasoning_tokens,
            cached_input_tokens=cached_input_tokens,
            embedding_tokens=embedding_tokens,
            tool_calls=tool_calls, media_type=media_type, cache_hit=cache_hit,
        )

        try:
            self.budget_enforcer.check_budget(
                user_id=user_id, project_id=project_id,
                provider=provider, model=model,
                input_tokens=input_tokens, output_tokens=output_tokens,
            )
        except BudgetExceededError:
            if self.ghost_mode:
                record["_ghost"] = True
            else:
                raise

        if system_context:
            record["system"] = self.system_monitor.snapshot()

        verified = self.verifier.verify(record)
        if self.differential_privacy_epsilon:
            verified = self._add_noise(verified)

        self.store.insert_record(verified)
        if self.interceptor.on_record:
            self.interceptor.on_record(verified)
        return verified

    def _add_noise(self, record: dict[str, Any]) -> dict[str, Any]:
        import random
        eps = self.differential_privacy_epsilon or 1.0
        scale = 1.0 / eps
        record["input_tokens"] = max(0, int(record.get("input_tokens", 0) + random.gauss(0, scale)))
        record["output_tokens"] = max(0, int(record.get("output_tokens", 0) + random.gauss(0, scale)))
        record["total_tokens"] = record["input_tokens"] + record["output_tokens"]
        record["cost_usd"] = max(0.0, record.get("cost_usd", 0) + random.gauss(0, scale * 0.001))
        record["_dp_noise_applied"] = True
        return record

    def get_records(self) -> list[dict[str, Any]]:
        return self.store.get_records()

    def set_budget(self, scope: str, scope_id: str, limit_usd: float, reset_cycle: str = "monthly") -> None:
        self.store.set_budget(scope, scope_id, {"scope": scope, "scope_id": scope_id,
                                                  "limit_usd": limit_usd, "reset_cycle": reset_cycle})

    def get_summary(self, scope: str = "global", scope_id: str = "all") -> dict[str, Any]:
        return self.analytics.get_summary(scope, scope_id)

    def get_spending_by_provider(self) -> list[dict[str, Any]]:
        return self.analytics.get_spending_by_dimension("provider")

    def get_spending_by_dimension(self, dimension: str) -> list[dict[str, Any]]:
        return self.analytics.get_spending_by_dimension(dimension)

    def export_csv(self, filepath: str) -> None:
        self.exporter.export_csv(filepath, self.get_records())

    def export_json(self, filepath: str) -> None:
        self.exporter.export_json(filepath, self.get_records())

    def export_audit_json(self, filepath: str) -> None:
        """Export with verification status and checksums."""
        records = self.get_records()
        audit = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "record_count": len(records),
            "verified": self.verify_immutability(),
            "records": records,
        }
        audit["_checksum"] = hashlib.sha256(json.dumps(audit, sort_keys=True, default=str).encode()).hexdigest()
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(audit, f, indent=2, default=str)

    def register_pricing(self, provider: str, model: str, input_cost_per_1k: float, output_cost_per_1k: float) -> None:
        self.pricing.register_custom(provider, model, input_cost_per_1k, output_cost_per_1k)

    def get_pricing(self, provider: str, model: str) -> dict[str, Any]:
        return self.pricing.get_pricing(provider, model)

    def apply_retention(self, max_age_days: int | None = None) -> None:
        if max_age_days is not None:
            self.store.retention.max_age_days = max_age_days
        self.store._apply_retention()

    def verify_immutability(self) -> list[str]:
        return self.store.verify_immutability()

    def get_efficiency(self, scope: str = "global", scope_id: str = "all") -> dict[str, Any]:
        return self.analytics.get_efficiency_stats(scope, scope_id)

    @staticmethod
    def fingerprint_prompt(messages: list[dict[str, Any]]) -> str:
        raw = json.dumps(messages, sort_keys=True, default=str).strip()
        return hashlib.sha256(raw.encode()).hexdigest()

    def get_spending_by_conversation(self) -> list[dict[str, Any]]:
        return self.analytics.get_spending_by_dimension("conversation")

    def get_spending_by_agent(self) -> list[dict[str, Any]]:
        return self.analytics.get_spending_by_dimension("agent")

    def get_spending_by_tenant(self) -> list[dict[str, Any]]:
        return self.analytics.get_spending_by_dimension("tenant")

    # ── Differentiating Features ──────────────────────────────────────────

    @staticmethod
    def auto_detect(
        model_name: str | None = None,
        messages: list | dict | None = None,
        **kwargs: str,
    ) -> dict[str, str | None]:
        return _auto_detect(model_name, messages, **kwargs)

    def simulate_cost(
        self, provider: str, model: str, input_tokens: int = 0, output_tokens: int = 0,
        messages: list[dict] | None = None,
    ) -> dict[str, Any]:
        return _simulate_cost(self.pricing, provider, model, input_tokens, output_tokens, messages)

    def get_roi(self, scope: str = "global", scope_id: str = "all") -> dict[str, Any]:
        records = [r for r in self.get_records() if self._match_scope(r, scope, scope_id)]
        return _compute_roi(records)

    def sign_ledger(self, key: str) -> dict[str, Any]:
        return _sign_ledger(self.get_records(), key)

    @staticmethod
    def verify_signed_ledger(bundle: dict[str, Any], key: str) -> bool:
        return _verify_signed_ledger(bundle, key)

    def add_route_option(
        self, provider: str, model: str, input_cost_per_1k: float, output_cost_per_1k: float,
        max_tokens: int | None = None, latency_p95_ms: float | None = None,
    ) -> None:
        self.model_router.add_option(RouteOption(provider, model, input_cost_per_1k, output_cost_per_1k, max_tokens, latency_p95_ms))

    def add_cost_contract(
        self, name: str, max_cost_usd: float, scope: str = "global", scope_id: str = "all",
        callback: Callable | None = None,
    ) -> CostContract:
        c = CostContract(name, max_cost_usd, scope, scope_id, callback=callback)
        self.cost_contracts.add(c)
        return c

    def track_prompt_version(self, name: str, content: str, metadata: dict | None = None) -> dict[str, Any]:
        return self.prompt_evolution.track(name, content, metadata)

    def register_local_model(
        self, name: str, watts_per_second: float = 10.0, cost_per_kwh: float = 0.12,
        tokens_per_second: float = 30.0, hardware_cost: float = 0.0,
    ) -> None:
        self.local_models.register(LocalModelCost(name, watts_per_second, cost_per_kwh, tokens_per_second, hardware_cost))

    def estimate_local_cost(self, name: str, input_tokens: int, output_tokens: int) -> float | None:
        return self.local_models.estimate_cost(name, input_tokens, output_tokens)

    def _match_scope(self, record: dict[str, Any], scope: str, scope_id: str) -> bool:
        if scope == "global":
            return True
        if scope == "provider":
            return record.get("provider") == scope_id
        if scope == "model":
            return record.get("model") == scope_id
        if scope == "user":
            return record.get("user_id", "anonymous") == scope_id
        if scope == "project":
            return record.get("project_id", "default") == scope_id
        if scope == "conversation":
            return record.get("conversation_id") == scope_id
        if scope == "agent":
            return record.get("agent_id") == scope_id
        if scope == "tenant":
            return record.get("tenant_id") == scope_id
        return False
