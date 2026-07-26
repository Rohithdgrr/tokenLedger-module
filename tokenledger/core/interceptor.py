"""Method wrapping and monkey-patching for LLM clients."""

import asyncio
import logging
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

from .budget import BudgetEnforcer, BudgetExceededError
from .estimator import TokenEstimator
from .extractor import TokenExtractor
from .pricing import PricingRegistry
from .record import build_record
from .store import MemoryStore, StorageBackend
from .verifier import VerificationEngine

logger = logging.getLogger(__name__)

TRACKING_KWARGS = {"user_id", "project_id", "conversation_id", "agent_id", "prompt_hash", "tenant_id"}


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open for a provider."""


class TokenBucket:
    """Token bucket rate limiter."""

    def __init__(self, rate: float):
        self.rate = rate
        self.tokens = rate
        self.last = time.monotonic()

    def consume(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last
        self.tokens = min(self.rate, self.tokens + elapsed * self.rate)
        self.last = now
        if self.tokens < 1:
            sleep = (1 - self.tokens) / self.rate
            time.sleep(sleep)
            self.tokens = 0
            self.last = time.monotonic()
        else:
            self.tokens -= 1

    @property
    def available(self) -> float:
        now = time.monotonic()
        elapsed = now - self.last
        return min(self.rate, self.tokens + elapsed * self.rate)


class StreamWrapper:
    """Wraps a streaming response to accumulate usage metadata."""

    def __init__(self, stream, provider: str):
        self._stream = stream
        self._provider = provider
        self._chunks: List[Any] = []

    def __iter__(self):
        for chunk in self._stream:
            self._chunks.append(chunk)
            yield chunk

    def __aiter__(self):
        return self._aiter()

    async def _aiter(self):
        async for chunk in self._stream:
            self._chunks.append(chunk)
            yield chunk

    def get_accumulated_usage(self) -> Optional[Dict[str, Any]]:
        for chunk in reversed(self._chunks):
            usage = getattr(chunk, "usage", None)
            if usage:
                input_tokens = getattr(usage, "prompt_tokens", getattr(usage, "input_tokens", 0))
                output_tokens = getattr(usage, "completion_tokens", getattr(usage, "output_tokens", 0))
                return {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens,
                    "source": "api_reported",
                }
        return None


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
        on_budget_exceeded: Optional[Callable] = None,
        on_record: Optional[Callable] = None,
        ghost_mode: bool = False,
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
        self.on_budget_exceeded = on_budget_exceeded
        self.on_record = on_record
        self.on_budget_threshold: Optional[Callable] = None
        self.ghost_mode = ghost_mode
        self._original_methods: Dict[int, Dict[str, Callable]] = {}
        self._circuit_state: Dict[str, Dict] = {}
        self._rate_buckets: Dict[str, TokenBucket] = {}
        self._provider_config: Dict[str, Dict[str, Any]] = {}

    def configure_provider(self, provider: str, **kwargs) -> None:
        """Per-provider override for retries, timeout, rate limit, etc."""
        self._provider_config.setdefault(provider, {}).update(kwargs)

    def _get_provider_config(self, provider: str) -> Dict[str, Any]:
        return self._provider_config.get(provider, {})

    def _strip_tracking_kwargs(self, kwargs: Dict[str, Any]) -> None:
        for key in TRACKING_KWARGS:
            kwargs.pop(key, None)

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
        for attr in ("models.generate_content", "generate_content"):
            try:
                return self._wrap_attr(client, attr, "google")
            except AttributeError:
                continue
        logger.warning("wrap_gemini: client has no 'generate_content'; use record_usage() manually")
        return client

    def wrap_ollama(self, client: Any) -> Any:
        for attr in ("chat", "generate"):
            try:
                return self._wrap_attr(client, attr, "ollama")
            except AttributeError:
                continue
        logger.warning("wrap_ollama: client has no 'chat' or 'generate'; use record_usage() manually")
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

    def get_health(self) -> Dict[str, Any]:
        """Return circuit breaker and rate limiter status per provider."""
        now = time.monotonic()
        result = {}
        all_providers = set(self._circuit_state.keys()) | set(self._rate_buckets.keys())
        for provider in sorted(all_providers):
            cb = self._circuit_state.get(provider, {"state": "closed", "failures": 0, "since": 0.0})
            bucket = self._rate_buckets.get(provider)
            entry: Dict[str, Any] = {
                "circuit_state": cb["state"],
                "circuit_failures": cb["failures"],
            }
            if cb["state"] == "open":
                remaining = self.circuit_recovery_timeout - (now - cb["since"])
                entry["circuit_recovery_seconds"] = round(max(remaining, 0), 1)
            if bucket:
                entry["rate_limit_available_tokens"] = round(bucket.available, 1)
            result[provider] = entry
        return result

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
        bucket = self._rate_buckets.get(provider)
        if bucket is None:
            cfg = self._get_provider_config(provider)
            rps = cfg.get("rate_limit_rps", self.rate_limit_rps)
            bucket = TokenBucket(rps)
            self._rate_buckets[provider] = bucket
        bucket.consume()

    def _call_with_retry(self, original: Callable, args: tuple, kwargs: Dict[str, Any], provider: str, timeout: Optional[float] = None) -> Any:
        self._strip_tracking_kwargs(kwargs)
        cfg = self._get_provider_config(provider)
        max_retries = cfg.get("max_retries", self.max_retries)
        retry_delay = cfg.get("retry_delay", self.retry_delay)
        req_timeout = cfg.get("request_timeout", timeout)

        delay = retry_delay
        for attempt in range(max_retries + 1):
            try:
                if req_timeout and attempt == 0:
                    kwargs = {**kwargs, "timeout": req_timeout}
                return original(*args, **kwargs)
            except (ConnectionError, TimeoutError, CircuitBreakerOpenError):
                if attempt < max_retries:
                    logger.warning("Retry %d/%d for %s", attempt + 1, max_retries, provider)
                    time.sleep(delay)
                    delay *= 2
                else:
                    raise
            except Exception:
                raise

    async def _call_with_retry_async(self, original: Callable, args: tuple, kwargs: Dict[str, Any], provider: str, timeout: Optional[float] = None) -> Any:
        self._strip_tracking_kwargs(kwargs)
        cfg = self._get_provider_config(provider)
        max_retries = cfg.get("max_retries", self.max_retries)
        retry_delay = cfg.get("retry_delay", self.retry_delay)
        req_timeout = cfg.get("request_timeout", timeout)

        delay = retry_delay
        for attempt in range(max_retries + 1):
            try:
                if req_timeout and attempt == 0:
                    kwargs = {**kwargs, "timeout": req_timeout}
                if req_timeout:
                    return await asyncio.wait_for(original(*args, **kwargs), timeout=req_timeout)
                return await original(*args, **kwargs)
            except (ConnectionError, TimeoutError, asyncio.TimeoutError, CircuitBreakerOpenError):
                if attempt < max_retries:
                    logger.warning("Async retry %d/%d for %s", attempt + 1, max_retries, provider)
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
            "conversation_id": kwargs.get("conversation_id"),
            "agent_id": kwargs.get("agent_id"),
            "prompt_hash": kwargs.get("prompt_hash"),
            "tenant_id": kwargs.get("tenant_id"),
        }

    def _budget_check(self, metadata: Dict[str, Any], provider: str, kwargs: Dict[str, Any]) -> None:
        try:
            self.enforcer.check_budget(
                user_id=metadata["user_id"],
                project_id=metadata["project_id"],
                provider=provider,
                model=metadata["model"],
                messages=kwargs.get("messages", []),
                max_tokens=kwargs.get("max_tokens"),
            )
        except BudgetExceededError as e:
            if self.ghost_mode:
                return
            self._log_blocked_attempt(metadata, provider)
            if self.on_budget_exceeded:
                self.on_budget_exceeded(e)
            raise

    def _record(self, provider: str, metadata: Dict[str, Any], messages: list, response: Any, latency_ms: float) -> None:
        token_data = self.extractor.extract(response, provider)
        if token_data is None:
            token_data = self.estimator.estimate(
                messages=messages, model=metadata["model"], provider=provider,
            )
        record = build_record(
            provider=provider, model=metadata["model"],
            input_tokens=token_data["input_tokens"], output_tokens=token_data["output_tokens"],
            user_id=metadata["user_id"], project_id=metadata["project_id"],
            latency_ms=round(latency_ms, 3), source=token_data.get("source", "api_reported"),
            pricing=self.pricing, status="success",
            tenant_id=metadata.get("tenant_id"),
            conversation_id=metadata.get("conversation_id"),
            agent_id=metadata.get("agent_id"),
            prompt_hash=metadata.get("prompt_hash"),
        )
        if not self.pricing.has_model(provider, metadata["model"]):
            if self.unknown_model_policy == "block":
                raise UnknownModelError(f"Unknown model: {provider}:{metadata['model']}")
            if self.unknown_model_policy == "allow":
                record["cost_usd"] = 0.0
        sysmon = getattr(self.ledger, "system_monitor", None)
        if sysmon:
            record["system"] = sysmon.snapshot()
        if self.ghost_mode:
            record["_ghost"] = True
        record = self.verifier.verify(record, response)
        self.store.insert_record(record)
        self.enforcer.update_model_stats(metadata["model"], token_data["input_tokens"], token_data["output_tokens"])
        if self.on_record:
            self.on_record(record)

    def _handle_stream(self, response: Any, provider: str, metadata: Dict[str, Any]) -> Any:
        wrapped = StreamWrapper(response, provider)
        original_iter = wrapped.__iter__
        original_aiter = wrapped.__aiter__

        def tracked_iter():
            yield from original_iter()
            usage = wrapped.get_accumulated_usage()
            if usage:
                self._record_usage_from_stream(provider, metadata, usage)

        async def tracked_aiter():
            async for chunk in original_aiter():
                yield chunk
            usage = wrapped.get_accumulated_usage()
            if usage:
                self._record_usage_from_stream(provider, metadata, usage)

        wrapped.__iter__ = tracked_iter
        wrapped.__aiter__ = tracked_aiter
        return wrapped

    def _record_usage_from_stream(self, provider: str, metadata: Dict[str, Any], token_data: Dict[str, Any]) -> None:
        record = build_record(
            provider=provider, model=metadata["model"],
            input_tokens=token_data["input_tokens"], output_tokens=token_data["output_tokens"],
            user_id=metadata["user_id"], project_id=metadata["project_id"],
            source="stream", pricing=self.pricing, status="success",
            tenant_id=metadata.get("tenant_id"),
            conversation_id=metadata.get("conversation_id"),
            agent_id=metadata.get("agent_id"),
            prompt_hash=metadata.get("prompt_hash"),
        )
        if self.ghost_mode:
            record["_ghost"] = True
        self.store.insert_record(record)
        self.enforcer.update_model_stats(metadata["model"], token_data["input_tokens"], token_data["output_tokens"])

    def _track_request(self, original: Callable, provider: str, args: tuple, kwargs: Dict[str, Any]) -> Any:
        metadata = self._extract_meta(kwargs)
        self._budget_check(metadata, provider, kwargs)
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

        if kwargs.get("stream"):
            return self._handle_stream(response, provider, metadata)

        self._record(provider, metadata, kwargs.get("messages", []), response, latency_ms)
        return response

    async def _track_request_async(self, original: Callable, provider: str, args: tuple, kwargs: Dict[str, Any]) -> Any:
        metadata = self._extract_meta(kwargs)
        self._budget_check(metadata, provider, kwargs)
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

        if kwargs.get("stream"):
            return self._handle_stream(response, provider, metadata)

        self._record(provider, metadata, kwargs.get("messages", []), response, latency_ms)
        return response

    def _log_blocked_attempt(self, metadata: Dict[str, Any], provider: str) -> None:
        record = build_record(
            provider=provider, model=metadata.get("model", "unknown"),
            input_tokens=0, output_tokens=0,
            user_id=metadata.get("user_id", "anonymous"),
            project_id=metadata.get("project_id", "default"),
            source="budget_blocked", status="blocked",
            tenant_id=metadata.get("tenant_id"),
        )
        self.store.insert_record(record)


class UnknownModelError(Exception):
    """Raised when an unknown model is encountered and policy is 'block'."""
