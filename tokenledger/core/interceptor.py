"""Method wrapping and monkey-patching for LLM clients."""

import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from .budget import BudgetEnforcer, BudgetExceededError
from .estimator import TokenEstimator
from .extractor import TokenExtractor
from .pricing import PricingRegistry
from .store import MemoryStore
from .verifier import VerificationEngine


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open for a provider."""


class InterceptionLayer:
    """Wraps LLM client methods to inject tracking logic."""

    def __init__(
        self,
        ledger,
        store: MemoryStore,
        pricing: PricingRegistry,
        enforcer: BudgetEnforcer,
        extractor: TokenExtractor,
        estimator: TokenEstimator,
        verifier: VerificationEngine,
        unknown_model_policy: str = "estimate",
        request_timeout: Optional[float] = 120.0,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        circuit_breaker_threshold: int = 5,
        circuit_recovery_timeout: float = 30.0,
        rate_limit_rps: int = 100,
    ):
        self.ledger = ledger
        self.store = store
        self.pricing = pricing
        self.enforcer = enforcer
        self.extractor = extractor
        self.estimator = estimator
        self.verifier = verifier
        self.unknown_model_policy = unknown_model_policy
        self.request_timeout = request_timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.circuit_breaker_threshold = circuit_breaker_threshold
        self.circuit_recovery_timeout = circuit_recovery_timeout
        self.rate_limit_rps = rate_limit_rps
        self._original_methods: Dict[int, Dict[str, Callable]] = {}
        self._circuit_state: Dict[str, Dict] = {}
        self._rate_buckets: Dict[str, float] = {}

    def _make_tracked_fn(self, original: Callable, provider: str) -> Callable:
        if asyncio.iscoroutinefunction(original):
            async def tracked(*args, **kwargs):
                return await self._track_request_async(original, provider, args, kwargs)
            return tracked

        def tracked(*args, **kwargs):
            return self._track_request(original, provider, args, kwargs)
        return tracked

    def _wrap_attr(self, client: Any, attr_path: str, provider: str) -> Any:
        client_id = id(client)
        if client_id not in self._original_methods:
            self._original_methods[client_id] = {}
        parts = attr_path.split(".")
        parent = client
        for part in parts[:-1]:
            parent = getattr(parent, part)
        original = getattr(parent, parts[-1])
        self._original_methods[client_id][attr_path] = original
        setattr(parent, parts[-1], self._make_tracked_fn(original, provider))
        return client

    def wrap_openai(self, client: Any, provider: str = "openai") -> Any:
        return self._wrap_attr(client, "chat.completions.create", provider)

    def wrap_anthropic(self, client: Any) -> Any:
        return self._wrap_attr(client, "messages.create", "anthropic")

    def wrap_groq(self, client: Any) -> Any:
        return self._wrap_attr(client, "chat.completions.create", "groq")

    def wrap_gemini(self, client: Any) -> Any:
        return client

    def wrap_ollama(self, client: Any) -> Any:
        return client

    def unwrap(self, client: Any) -> Any:
        client_id = id(client)
        originals = self._original_methods.pop(client_id, {})
        for attr_path, original in originals.items():
            parts = attr_path.split(".")
            parent = client
            for part in parts[:-1]:
                parent = getattr(parent, part)
            setattr(parent, parts[-1], original)
        return client

    def _check_circuit(self, provider: str) -> None:
        state = self._circuit_state.get(provider)
        if state is None:
            return
        if state["state"] == "open":
            if time.monotonic() - state["since"] > self.circuit_recovery_timeout:
                state["state"] = "half-open"
                return
            raise CircuitBreakerOpenError(f"Circuit breaker open for {provider}")

    def _record_result(self, provider: str, success: bool) -> None:
        state = self._circuit_state.setdefault(provider, {"state": "closed", "failures": 0, "since": 0.0})
        if success:
            state["failures"] = 0
            state["state"] = "closed"
        else:
            state["failures"] += 1
            if state["failures"] >= self.circuit_breaker_threshold:
                state["state"] = "open"
                state["since"] = time.monotonic()

    def _check_rate_limit(self, provider: str) -> None:
        now = time.monotonic()
        last = self._rate_buckets.get(provider, 0.0)
        elapsed = now - last
        min_interval = 1.0 / self.rate_limit_rps
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        self._rate_buckets[provider] = time.monotonic()

    def _call_with_retry(self, original: Callable, args: tuple, kwargs: Dict[str, Any], provider: str, timeout: Optional[float] = None) -> Any:
        delay = self.retry_delay
        for attempt in range(self.max_retries + 1):
            try:
                if timeout and attempt == 0:
                    kwargs = {**kwargs, "timeout": timeout}
                return original(*args, **kwargs)
            except (ConnectionError, TimeoutError, CircuitBreakerOpenError):
                if attempt < self.max_retries:
                    time.sleep(delay)
                    delay *= 2
                else:
                    raise
            except Exception:
                raise

    async def _call_with_retry_async(self, original: Callable, args: tuple, kwargs: Dict[str, Any], provider: str, timeout: Optional[float] = None) -> Any:
        delay = self.retry_delay
        for attempt in range(self.max_retries + 1):
            try:
                if timeout and attempt == 0:
                    kwargs = {**kwargs, "timeout": timeout}
                if timeout:
                    return await asyncio.wait_for(original(*args, **kwargs), timeout=timeout)
                return await original(*args, **kwargs)
            except (ConnectionError, TimeoutError, asyncio.TimeoutError, CircuitBreakerOpenError):
                if attempt < self.max_retries:
                    await asyncio.sleep(delay)
                    delay *= 2
                else:
                    raise
            except Exception:
                raise

    def _extract_meta(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "user_id": kwargs.get("user_id", "anonymous"),
            "project_id": kwargs.get("project_id", "default"),
            "model": kwargs.get("model", "unknown"),
        }

    def _budget_check(self, metadata: Dict[str, Any], provider: str, messages: list) -> None:
        try:
            self.enforcer.check_budget(
                user_id=metadata["user_id"],
                project_id=metadata["project_id"],
                provider=provider,
                model=metadata["model"],
                messages=messages,
            )
        except BudgetExceededError:
            self._log_blocked_attempt(metadata, provider)
            raise

    def _record(self, provider: str, metadata: Dict[str, Any], messages: list, response: Any, latency_ms: float) -> None:
        token_data = self.extractor.extract(response, provider)
        if token_data is None:
            token_data = self.estimator.estimate(
                messages=messages, model=metadata["model"], provider=provider,
            )
        cost = self.pricing.calculate_cost(
            provider, metadata["model"], token_data["input_tokens"], token_data["output_tokens"],
        )
        record = {
            "record_id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "provider": provider,
            "model": metadata["model"],
            "input_tokens": token_data["input_tokens"],
            "output_tokens": token_data["output_tokens"],
            "total_tokens": token_data.get("total_tokens", token_data["input_tokens"] + token_data["output_tokens"]),
            "cost_usd": cost,
            "latency_ms": round(latency_ms, 3),
            "user_id": metadata["user_id"],
            "project_id": metadata["project_id"],
            "status": "success",
            "source": token_data.get("source", "api_reported"),
        }
        if not self.pricing.has_model(provider, metadata["model"]):
            if self.unknown_model_policy == "block":
                raise UnknownModelError(f"Unknown model: {provider}:{metadata['model']}")
            if self.unknown_model_policy == "allow":
                record["cost_usd"] = 0.0
        sysmon = getattr(self.ledger, "system_monitor", None)
        if sysmon:
            record["system"] = sysmon.snapshot()
        record = self.verifier.verify(record, response)
        self.store.insert_record(record)

    def _track_request(self, original: Callable, provider: str, args: tuple, kwargs: Dict[str, Any]) -> Any:
        metadata = self._extract_meta(kwargs)
        self._budget_check(metadata, provider, kwargs.get("messages", []))
        self._check_circuit(provider)
        self._check_rate_limit(provider)
        start_time = time.monotonic()
        try:
            response = self._call_with_retry(original, args, kwargs, provider, self.request_timeout)
        except Exception:
            self._record_result(provider, False)
            raise
        finally:
            latency_ms = (time.monotonic() - start_time) * 1000
        self._record_result(provider, True)
        self._record(provider, metadata, kwargs.get("messages", []), response, latency_ms)
        return response

    async def _track_request_async(self, original: Callable, provider: str, args: tuple, kwargs: Dict[str, Any]) -> Any:
        metadata = self._extract_meta(kwargs)
        self._budget_check(metadata, provider, kwargs.get("messages", []))
        self._check_circuit(provider)
        self._check_rate_limit(provider)
        start_time = time.monotonic()
        try:
            response = await self._call_with_retry_async(original, args, kwargs, provider, self.request_timeout)
        except Exception:
            self._record_result(provider, False)
            raise
        finally:
            latency_ms = (time.monotonic() - start_time) * 1000
        self._record_result(provider, True)
        self._record(provider, metadata, kwargs.get("messages", []), response, latency_ms)
        return response

    def _log_blocked_attempt(self, metadata: Dict[str, Any], provider: str) -> None:
        record = {
            "record_id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "provider": provider,
            "model": metadata.get("model", "unknown"),
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
            "latency_ms": 0.0,
            "user_id": metadata.get("user_id", "anonymous"),
            "project_id": metadata.get("project_id", "default"),
            "status": "blocked",
            "source": "budget_blocked",
        }
        self.store.insert_record(record)


class UnknownModelError(Exception):
    """Raised when an unknown model is encountered and policy is 'block'."""
