"""Main TokenLedger implementation."""

from __future__ import annotations

import functools
import hashlib
import inspect
import json
import math
import secrets
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Literal, TypeVar

if TYPE_CHECKING:
    from ..ext.live_server import LiveServer
    from .wallet import Wallet

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
from .interceptor import InterceptionLayer, UnknownModelError
from .pricing import PricingRegistry
from .record import build_record
from .store import MemoryStore, StorageBackend
from .system import SystemMonitor
from .verifier import VerificationEngine

F = TypeVar("F", bound=Callable)


class _UsageContext:
    """Context manager returned by :meth:`TokenLedger.usage` (sync + async)."""

    def __init__(
        self,
        ledger: TokenLedger,
        provider: str,
        model: str,
        messages: list[dict[str, Any]] | None,
        output_text: str | None,
        **kwargs: Any,
    ):
        self._ledger = ledger
        self._provider = provider
        self._model = model
        self._messages = messages
        self._output_text = output_text
        self._kwargs = kwargs
        self._start = time.monotonic()

    def _record(self, status: str) -> None:
        latency = round((time.monotonic() - self._start) * 1000, 3)
        if self._messages:
            est = self._ledger.estimator.estimate(
                messages=self._messages,
                model=self._model,
                provider=self._provider,
                output_text=self._output_text,
            )
            inp, out = est["input_tokens"], est["output_tokens"]
        else:
            inp = out = 0
        self._ledger.record_usage(
            provider=self._provider,
            model=self._model,
            input_tokens=inp,
            output_tokens=out,
            latency_ms=latency,
            status=status,
            source="usage_block",
            **self._kwargs,
        )

    def __enter__(self) -> _UsageContext:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> Literal[False]:
        try:
            self._record("error" if exc_type else "success")
        except Exception as e:
            import logging

            logging.getLogger(__name__).warning("Failed to record usage block: %s", e)
        return False

    async def __aenter__(self) -> _UsageContext:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> Literal[False]:
        try:
            self._record("error" if exc_type else "success")
        except Exception as e:
            import logging

            logging.getLogger(__name__).warning("Failed to record usage block: %s", e)
        return False


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
        encryption_key: str | bytes | None = None,
        differential_privacy_epsilon: float | None = None,
        redact_prompts: bool = False,
        ghost_mode: bool = False,
        strict_budget: bool = False,
        ghost_persist: bool = True,
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
        self.unknown_model_policy = unknown_model_policy
        self.strict_budget = strict_budget
        self.ghost_persist = ghost_persist
        self.interceptor = InterceptionLayer(
            ledger=self,
            store=self.store,
            pricing=self.pricing,
            enforcer=self.budget_enforcer,
            extractor=self.extractor,
            estimator=self.estimator,
            verifier=self.verifier,
            unknown_model_policy=self.unknown_model_policy,
            ghost_mode=getattr(self, "ghost_mode", False),
        )
        self.prompt_cache = PromptCache()
        self.estimator_feedback = EstimatorFeedback()
        self.model_router = ModelRouter()
        self.cost_contracts = CostContractRegistry()
        self.prompt_evolution = PromptEvolutionTracker()
        self.local_models = LocalModelRegistry()

    def wrap_proxy(self, client: Any, attr_path: str, provider: str) -> Any:
        """Non-mutating proxy wrapper — does not monkey-patch *client*."""
        return self.interceptor.wrap_proxy(client, attr_path, provider)

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
        return self.interceptor.wrap_openai(client, provider="openrouter")

    def wrap_deepseek(self, client: Any) -> Any:
        return self.interceptor.wrap_openai(client, provider="deepseek")

    def wrap_mistral(self, client: Any) -> Any:
        return self.interceptor.wrap_openai(client, provider="mistral")

    def wrap_cohere(self, client: Any) -> Any:
        return self.interceptor.wrap_cohere(client)

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

    def register_parser(self, provider: str, parser: Callable[[Any], dict[str, Any] | None]) -> None:
        """Register a custom token-extraction parser for a provider."""
        self.extractor.register_parser(provider, parser)

    def register_pricing_provider(self, provider: str, model: str, input_cost_per_1k: float, output_cost_per_1k: float) -> None:
        """Alias for :meth:`register_pricing` to support plugin ergonomics."""
        self.register_pricing(provider, model, input_cost_per_1k, output_cost_per_1k)

    def track(
        self,
        provider: str,
        model: str,
        user_id: str = "anonymous",
        project_id: str = "default",
        **tracking_kwargs: Any,
    ) -> Callable:
        """Decorator that records usage for the wrapped function.

        Supports sync and async callables, uses extractor fallback when
        ``input_tokens``/``output_tokens`` are not provided, and never
        mutates the caller's ``tracking_kwargs``.
        """

        def decorator(func: Callable) -> Callable:
            if inspect.iscoroutinefunction(func):

                @functools.wraps(func)
                async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                    start = time.monotonic()
                    result = await func(*args, **kwargs)
                    self._record_decorated(provider, model, user_id, project_id, result, start, tracking_kwargs)
                    return result

                return async_wrapper

            @functools.wraps(func)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                start = time.monotonic()
                result = func(*args, **kwargs)
                self._record_decorated(provider, model, user_id, project_id, result, start, tracking_kwargs)
                return result

            return wrapper

        return decorator

    def _record_decorated(
        self,
        provider: str,
        model: str,
        user_id: str,
        project_id: str,
        result: Any,
        start: float,
        tracking_kwargs: dict[str, Any],
    ) -> None:
        inp = tracking_kwargs.get("input_tokens", 0) or 0
        out = tracking_kwargs.get("output_tokens", 0) or 0
        if not inp and not out:
            td = self.extractor.extract(result, provider)
            if td:
                inp = td.get("input_tokens", 0)
                out = td.get("output_tokens", 0)
        rest = {k: v for k, v in tracking_kwargs.items() if k not in ("input_tokens", "output_tokens")}
        latency_ms = round((time.monotonic() - start) * 1000, 3)
        self.record_usage(
            provider=provider,
            model=model,
            user_id=user_id,
            project_id=project_id,
            input_tokens=inp,
            output_tokens=out,
            latency_ms=latency_ms,
            **rest,
        )

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

        # Redact prompt_hash when requested, but avoid double-hashing an
        # already-hashed value (fingerprint is 64 hex chars).
        if self.redact_prompts and prompt_hash:
            is_hex_hash = len(prompt_hash) == 64 and all(c in "0123456789abcdefABCDEF" for c in prompt_hash)
            if not is_hex_hash:
                prompt_hash = hashlib.sha256(prompt_hash.encode()).hexdigest()

        record = build_record(
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            user_id=user_id,
            project_id=project_id,
            latency_ms=latency_ms,
            source=source,
            status=status,
            pricing=self.pricing,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            prompt_hash=prompt_hash,
            reasoning_tokens=reasoning_tokens,
            cached_input_tokens=cached_input_tokens,
            embedding_tokens=embedding_tokens,
            tool_calls=tool_calls,
            media_type=media_type,
            cache_hit=cache_hit,
        )

        if not self.pricing.has_model(provider, model):
            if self.unknown_model_policy == "block":
                raise UnknownModelError(f"Unknown model: {provider}:{model}")
            if self.unknown_model_policy == "allow":
                record["cost_usd"] = 0.0
                record["_allow_zero_cost"] = True

        try:
            self.budget_enforcer.check_budget(
                user_id=user_id,
                project_id=project_id,
                provider=provider,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        except BudgetExceededError:
            if self.ghost_mode:
                record["_ghost"] = True
            else:
                raise

        if system_context:
            record["system"] = self.system_monitor.snapshot()  # type: ignore[union-attr]

        verified = self.verifier.verify(record)

        # Ghost-mode storage optimization: optionally drop ghost records
        if verified.get("_ghost") and not getattr(self, "ghost_persist", True):
            if self.interceptor.on_record:
                self.interceptor.on_record(verified)
            return verified

        self.store.insert_record(verified)
        if self.interceptor.on_record:
            self.interceptor.on_record(verified)
        # Post-hoc strict budget enforcement: actual cost may exceed estimate
        if self.strict_budget and not verified.get("_ghost") and verified.get("status") not in ("blocked", "error"):
            try:
                # Re-check with actual tokens; budget_enforcer will raise if over
                # We use a small grace threshold (e.g., 5%) to avoid flapping
                for _key, b in self.store.get_all_budgets().items():
                    spend = self.budget_enforcer._calculate_current_spend(b)  # after insert, includes this record
                    limit = float(b.get("limit_usd", 0))
                    if limit and spend > limit * 1.05:
                        verified["budget_overrun"] = True
                        if self.interceptor.on_budget_exceeded:
                            # Synthesize an error for callback compatibility
                            from .exceptions import BudgetExceededError as _BEE

                            self.interceptor.on_budget_exceeded(
                                _BEE(f"Post-hoc budget overrun: ${spend:.4f} > ${limit:.4f}", b.get("scope",""), b.get("scope_id",""), spend, limit)
                            )
                        break
            except Exception:
                pass
        return verified

    def _add_noise(self, record: dict[str, Any]) -> dict[str, Any]:
        """Add Laplace noise (epsilon-DP) to token/cost fields in the record.

        Applied only at the export/query boundary (on record copies), never at
        insert time, so internal math (budgets, running totals, analytics)
        stays exact. ``total_tokens`` is recomputed after noise so the
        arithmetic invariant always holds.
        """
        eps = self.differential_privacy_epsilon or 1.0
        scale = 1.0 / eps
        record["input_tokens"] = max(0, int(record.get("input_tokens", 0) + self._laplace_sample(scale)))
        record["output_tokens"] = max(0, int(record.get("output_tokens", 0) + self._laplace_sample(scale)))
        record["total_tokens"] = record["input_tokens"] + record["output_tokens"]
        record["cost_usd"] = max(0.0, record.get("cost_usd", 0) + self._laplace_sample(scale * 0.001))
        record["_dp_noise_applied"] = True
        return record

    def _dp_records(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return noise-applied copies when differential privacy is enabled."""
        if not self.differential_privacy_epsilon:
            return records
        return [self._add_noise(dict(r)) for r in records]

    @staticmethod
    def _laplace_sample(scale: float) -> float:
        """Sample from Laplace(0, scale) using inverse CDF, pure stdlib.

        Uses ``secrets`` for a cryptographically secure uniform value —
        cheaper than a 64-bit Mersenne draw and safe for DP noise.
        """
        u = 1e-12 + secrets.randbelow(2**53) / 2**53 * (1.0 - 2e-12)
        if u < 0.5:
            return scale * math.log(2 * u)
        return -scale * math.log(2 * (1 - u))

    def get_records(self, apply_dp: bool = False) -> list[dict[str, Any]]:
        records = self.store.get_records()
        return self._dp_records(records) if apply_dp else records

    def usage(
        self,
        provider: str,
        model: str,
        messages: list[dict[str, Any]] | None = None,
        output_text: str | None = None,
        **kwargs: Any,
    ) -> _UsageContext:
        """Track an app code block as a usage record (``with`` or ``async with``).

        Records on exit with tokens estimated from ``messages`` (and
        ``output_text`` when given); marks ``status="error"`` if the block
        raised. Extra kwargs pass through to :meth:`record_usage`.
        """
        return _UsageContext(self, provider, model, messages, output_text, **kwargs)

    def cost_preview(
        self,
        messages: list[dict[str, Any]],
        model: str,
        provider: str,
        output_text: str | None = None,
    ) -> dict[str, Any]:
        """Estimate tokens and cost for a request without storing anything."""
        est = self.estimator.estimate(messages=messages, model=model, provider=provider, output_text=output_text)
        cost = self.pricing.calculate_cost(provider, model, est["input_tokens"], est["output_tokens"])
        return {**est, "cost_usd": round(cost, 10)}

    def create_wallet(
        self,
        user_id: str,
        limit_usd: float,
        reset_cycle: str = "daily",
        low_balance_threshold: float = 0.2,
        on_low_balance: Callable[[Wallet], None] | None = None,
    ) -> Wallet:
        """Create a per-user prepaid allowance wallet."""
        from .wallet import Wallet

        return Wallet(
            self,
            user_id=user_id,
            limit_usd=limit_usd,
            reset_cycle=reset_cycle,
            low_balance_threshold=low_balance_threshold,
            on_low_balance=on_low_balance,
        )

    def serve(self, host: str = "127.0.0.1", port: int = 8765, api_key: str | None = None) -> LiveServer:
        """Start a live spend server (``/stats`` JSON + ``/stream`` SSE)."""
        from ..ext.live_server import LiveServer

        return LiveServer(self, host=host, port=port, api_key=api_key)

    def set_budget(self, scope: str, scope_id: str, limit_usd: float, reset_cycle: str = "monthly") -> None:
        if limit_usd < 0:
            raise ValueError("limit_usd must be non-negative")
        self.store.set_budget(scope, scope_id, {"scope": scope, "scope_id": scope_id, "limit_usd": limit_usd, "reset_cycle": reset_cycle})

    def get_summary(self, scope: str = "global", scope_id: str = "all", apply_dp: bool = False) -> dict[str, Any]:
        eps = self.differential_privacy_epsilon if apply_dp else None
        return self.analytics.get_summary(scope, scope_id, apply_dp=apply_dp, epsilon=eps)

    def get_spending_by_provider(self) -> list[dict[str, Any]]:
        return self.analytics.get_spending_by_dimension("provider")

    def get_spending_by_dimension(self, dimension: str) -> list[dict[str, Any]]:
        return self.analytics.get_spending_by_dimension(dimension)

    def export_csv(self, filepath: str, apply_dp: bool = False) -> None:
        self.exporter.export_csv(filepath, self.get_records(apply_dp=apply_dp))

    def export_json(self, filepath: str, apply_dp: bool = False) -> None:
        self.exporter.export_json(filepath, self.get_records(apply_dp=apply_dp))

    def export_audit_json(self, filepath: str | None = None, apply_dp: bool = False) -> dict[str, Any]:
        """Export with verification status and checksums.

        Returns the audit bundle; writes it to ``filepath`` when provided.
        ``apply_dp`` applies differential privacy noise to exported copies
        when epsilon is configured.

        Delegates to :class:`ExportEngine` to avoid duplication.
        """
        records = self.get_records(apply_dp=apply_dp)
        return self.exporter.export_audit_json(filepath, records, verified=self.verify_immutability())

    def register_pricing(self, provider: str, model: str, input_cost_per_1k: float, output_cost_per_1k: float) -> None:
        self.pricing.register_custom(provider, model, input_cost_per_1k, output_cost_per_1k)

    def get_pricing(self, provider: str, model: str) -> dict[str, Any]:
        return self.pricing.get_pricing(provider, model)

    def apply_retention(self, max_age_days: int | None = None) -> None:
        self.store.apply_retention(max_age_days)

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

    def simulate_cost(
        self,
        provider: str,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
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
        self,
        provider: str,
        model: str,
        input_cost_per_1k: float,
        output_cost_per_1k: float,
        max_tokens: int | None = None,
        latency_p95_ms: float | None = None,
    ) -> None:
        self.model_router.add_option(RouteOption(provider, model, input_cost_per_1k, output_cost_per_1k, max_tokens, latency_p95_ms))

    def add_cost_contract(
        self,
        name: str,
        max_cost_usd: float,
        scope: str = "global",
        scope_id: str = "all",
        callback: Callable | None = None,
    ) -> CostContract:
        c = CostContract(name, max_cost_usd, scope, scope_id, callback=callback)
        self.cost_contracts.add(c)
        return c

    def track_prompt_version(self, name: str, content: str, metadata: dict | None = None) -> dict[str, Any]:
        return self.prompt_evolution.track(name, content, metadata)

    def register_local_model(
        self,
        name: str,
        watts_per_second: float = 10.0,
        cost_per_kwh: float = 0.12,
        tokens_per_second: float = 30.0,
        hardware_cost: float = 0.0,
    ) -> None:
        self.local_models.register(LocalModelCost(name, watts_per_second, cost_per_kwh, tokens_per_second, hardware_cost))

    def estimate_local_cost(self, name: str, input_tokens: int, output_tokens: int) -> float | None:
        return self.local_models.estimate_cost(name, input_tokens, output_tokens)

    def _match_scope(self, record: dict[str, Any], scope: str, scope_id: str) -> bool:
        if scope == "global":
            return True
        if scope == "provider":
            return bool(record.get("provider") == scope_id)
        if scope == "model":
            return bool(record.get("model") == scope_id)
        if scope == "user":
            return bool(record.get("user_id", "anonymous") == scope_id)
        if scope == "project":
            return bool(record.get("project_id", "default") == scope_id)
        if scope == "conversation":
            return bool(record.get("conversation_id") == scope_id)
        if scope == "agent":
            return bool(record.get("agent_id") == scope_id)
        if scope == "tenant":
            return bool(record.get("tenant_id") == scope_id)
        return False
