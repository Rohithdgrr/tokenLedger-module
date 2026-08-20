"""Method wrapping and monkey-patching for LLM clients."""

import asyncio
import contextvars
import inspect
import logging
import threading
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any, Callable, Optional

from .budget import BudgetEnforcer, BudgetExceededError
from .estimator import TokenEstimator
from .exceptions import CircuitBreakerOpenError, UnknownModelError
from .extractor import TokenExtractor
from .pricing import PricingRegistry
from .record import build_record
from .scopes import matches_scope
from .store import StorageBackend
from .verifier import VerificationEngine

logger = logging.getLogger(__name__)

TRACKING_KWARGS = {"user_id", "project_id", "conversation_id", "agent_id", "prompt_hash", "tenant_id"}

# Context-bound tracking attributes. Middleware (FastAPI/Flask) can tag every
# wrapped call without polluting function signatures:
#   token = ledger_context.set({"user_id": request.user.id, "tenant_id": ...})
#   try: ... finally: ledger_context.reset(token)
ledger_context: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("ledger_context", default=None)

TRANSIENT_EXCEPTIONS: tuple[type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
    OSError,
    asyncio.TimeoutError,
)
TRANSIENT_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}


def _is_transient(exc: BaseException) -> bool:
    """Provider SDKs wrap network errors in their own types; check status codes too."""
    if isinstance(exc, TRANSIENT_EXCEPTIONS):
        return True
    return getattr(exc, "status_code", None) in TRANSIENT_STATUS_CODES


class TokenBucket:
    """Thread-safe token bucket rate limiter."""

    __slots__ = ("rate", "tokens", "last", "lock", "_async_lock")

    def __init__(self, rate: float):
        self.rate = rate
        self.tokens = rate
        self.last = time.monotonic()
        self.lock = threading.RLock()
        self._async_lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last
        self.tokens = min(self.rate, self.tokens + elapsed * self.rate)
        self.last = now

    def consume(self) -> None:
        with self.lock:
            self._refill()
            if self.tokens < 1:
                sleep = (1 - self.tokens) / self.rate
                self.tokens = 0
                self.last = time.monotonic()
            else:
                sleep = 0.0
                self.tokens -= 1
        if sleep:
            time.sleep(sleep)

    async def async_consume(self) -> None:
        async with self._async_lock:
            with self.lock:
                self._refill()
                if self.tokens < 1:
                    sleep = (1 - self.tokens) / self.rate
                    self.tokens = 0
                    self.last = time.monotonic()
                else:
                    sleep = 0.0
                    self.tokens -= 1
        if sleep:
            await asyncio.sleep(sleep)

    @property
    def available(self) -> float:
        with self.lock:
            now = time.monotonic()
            elapsed = now - self.last
            return min(self.rate, self.tokens + elapsed * self.rate)


class StreamWrapper:
    """Wraps a streaming response to accumulate usage metadata and text deltas.

    ``__iter__``/``__aiter__`` capture each chunk and run ``on_finish`` once
    the stream is exhausted, so usage is recorded even when the provider
    never emitted a final ``usage`` chunk.

    If the underlying stream raises mid-iteration, ``on_finish`` still runs
    via ``finally`` and records partial usage (estimated from streamed text
    when no usage chunk arrived). This is intentional — partial cost is
    still billable.
    """

    __slots__ = ("_stream", "_provider", "_on_finish", "_chunks", "_text_parts", "_max_chunks")

    def __init__(self, stream: Any, provider: str, on_finish: Optional[Callable] = None, max_chunks: int = 2000):
        self._stream = stream
        self._provider = provider
        self._on_finish = on_finish
        self._chunks: list[Any] = []
        self._text_parts: list[str] = []
        self._max_chunks = max_chunks

    def _capture(self, chunk: Any) -> None:
        self._chunks.append(chunk)
        if len(self._chunks) > self._max_chunks:
            # Bounded history — drop the oldest chunks but keep the tail,
            # which is where provider usage metadata lives.
            del self._chunks[: len(self._chunks) - self._max_chunks]
        delta = getattr(chunk, "delta", None)
        if delta is None:
            delta = getattr(chunk, "text", None)
        if isinstance(delta, str):
            self._text_parts.append(delta)
        else:
            content = getattr(delta, "content", None)
            if isinstance(content, str):
                self._text_parts.append(content)

    def _finalize(self) -> None:
        cb, self._on_finish = self._on_finish, None
        if cb:
            cb()

    def __iter__(self) -> Iterator[Any]:
        exc: Optional[BaseException] = None
        try:
            for chunk in self._stream:
                self._capture(chunk)
                yield chunk
        except BaseException as e:
            exc = e
            raise
        finally:
            try:
                self._finalize()
            except Exception as cb_err:
                if exc is None:
                    raise cb_err
                # Preserve the stream's original exception; the finalizer
                # error is secondary diagnostics.
                logger.warning("Stream finalizer raised after stream error: %s", cb_err)

    async def __aiter__(self) -> AsyncIterator[Any]:
        exc: Optional[BaseException] = None
        try:
            async for chunk in self._stream:
                self._capture(chunk)
                yield chunk
        except BaseException as e:
            exc = e
            raise
        finally:
            try:
                self._finalize()
            except Exception as cb_err:
                if exc is None:
                    raise cb_err
                logger.warning("Stream finalizer raised after stream error: %s", cb_err)

    def get_stream_text(self) -> str:
        return "".join(self._text_parts)

    def get_accumulated_usage(self) -> Optional[dict[str, Any]]:
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


class _ProxyWrapper:
    """Non-mutating proxy that wraps a client without monkey-patching.

    Use :meth:`InterceptionLayer.wrap_proxy` for SDKs where mutating the
    original object is undesirable (e.g. shared clients or strict mocks).

    Supports dotted ``attr_path`` like ``"chat.completions.create"`` by
    recursively wrapping intermediate attributes.
    """

    __slots__ = ("_client", "_interceptor", "_provider", "_attr_path")

    def __init__(self, client: Any, interceptor: "InterceptionLayer", provider: str, attr_path: str):
        object.__setattr__(self, "_client", client)
        object.__setattr__(self, "_interceptor", interceptor)
        object.__setattr__(self, "_provider", provider)
        object.__setattr__(self, "_attr_path", attr_path)

    def __getattr__(self, name: str) -> Any:
        client = object.__getattribute__(self, "_client")
        try:
            attr = getattr(client, name)
        except AttributeError:
            raise
        attr_path = object.__getattribute__(self, "_attr_path")
        # Exact leaf match — including direct access to the leaf method
        # (e.g. proxy.create for attr_path="chat.completions.create") so the
        # untracked original never leaks through.
        if attr_path == name or attr_path.endswith("." + name):
            if callable(attr):
                return object.__getattribute__(self, "_interceptor")._make_tracked_fn(attr, object.__getattribute__(self, "_provider"))
            return attr
        # Intermediate segment: e.g. attr_path="chat.completions.create", name="chat"
        if attr_path.startswith(name + "."):
            remaining = attr_path[len(name) + 1 :]
            return _ProxyWrapper(attr, object.__getattribute__(self, "_interceptor"), object.__getattribute__(self, "_provider"), remaining)
        return attr

    def __setattr__(self, name: str, value: Any) -> None:
        # Allow setting proxy internals during __init__; otherwise delegate
        if name in {"_client", "_interceptor", "_provider", "_attr_path"}:
            object.__setattr__(self, name, value)
        else:
            setattr(object.__getattribute__(self, "_client"), name, value)

    def __repr__(self) -> str:
        client = object.__getattribute__(self, "_client")
        provider = object.__getattribute__(self, "_provider")
        attr = object.__getattribute__(self, "_attr_path")
        return f"<TokenLedgerProxy provider={provider} attr={attr} client={client!r}>"

    def __dir__(self) -> list[str]:
        return dir(object.__getattribute__(self, "_client"))


class InterceptionLayer:
    """Wraps LLM client methods to inject tracking logic."""

    def __init__(
        self,
        ledger: Any,
        store: StorageBackend,
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
        self._original_methods: dict[int, dict[str, tuple[Any, str, Callable]]] = {}
        self._circuit_state: dict[str, dict] = {}
        self._rate_buckets: dict[str, TokenBucket] = {}
        self._provider_config: dict[str, dict[str, Any]] = {}

    def __repr__(self) -> str:
        return f"<InterceptionLayer wrapped_clients={len(self._original_methods)} providers={len(self._provider_config)}>"

    def configure_provider(self, provider: str, **kwargs: Any) -> None:
        """Per-provider override for retries, timeout, rate limit, etc."""
        self._provider_config.setdefault(provider, {}).update(kwargs)

    def _get_provider_config(self, provider: str) -> dict[str, Any]:
        return self._provider_config.get(provider, {})

    def _strip_tracking_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Return a copy of *kwargs* with tracking fields removed.

        Never mutates the caller's dict — callers may reuse the same
        kwargs object for multiple API calls.
        """
        return {k: v for k, v in kwargs.items() if k not in TRACKING_KWARGS}

    def _make_tracked_fn(self, original: Callable, provider: str) -> Callable:
        if inspect.iscoroutinefunction(original):

            async def tracked_async(*args: Any, **kwargs: Any) -> Any:
                return await self._track_request_async(original, provider, args, kwargs)

            return tracked_async

        def tracked_sync(*args: Any, **kwargs: Any) -> Any:
            return self._track_request(original, provider, args, kwargs)

        return tracked_sync

    def _wrap_attr(self, client: Any, attr_path: str, provider: str) -> Any:
        """Monkey-patch *client* so calls at ``attr_path`` are tracked.

        Unwrap-safe: bound methods are stored as-is, but descriptors
        (``property``/``classmethod``/``staticmethod``) are stored at the
        class level so ``unwrap()`` restores the descriptor, not an
        evaluated value.
        """
        client_id = id(client)
        originals = self._original_methods.setdefault(client_id, {})
        if attr_path in originals:
            # Idempotent: re-wrapping would nest tracked fns (double
            # recording) and clobber the saved original.
            return client
        parts = attr_path.split(".")
        parent = client
        for part in parts[:-1]:
            parent = getattr(parent, part)
        attr_name = parts[-1]
        original = getattr(parent, attr_name)
        cls_attr = getattr(type(parent), attr_name, None)
        if isinstance(cls_attr, (property, classmethod, staticmethod)):
            original = cls_attr
        originals[attr_path] = (parent, attr_name, original)
        setattr(parent, attr_name, self._make_tracked_fn(getattr(parent, attr_name), provider))
        return client

    def wrap_proxy(self, client: Any, attr_path: str, provider: str) -> Any:
        """Return a proxy wrapping *client* without mutating the original.

        Example: ``proxy = interceptor.wrap_proxy(client, "chat.completions.create", "openai")``
        """
        return _ProxyWrapper(client, self, provider, attr_path)

    def wrap_openai(self, client: Any, provider: str = "openai") -> Any:
        return self._wrap_attr(client, "chat.completions.create", provider)

    def wrap_anthropic(self, client: Any) -> Any:
        return self._wrap_attr(client, "messages.create", "anthropic")

    def wrap_groq(self, client: Any) -> Any:
        return self._wrap_attr(client, "chat.completions.create", "groq")

    def wrap_gemini(self, client: Any) -> Any:
        wrapped = False
        for attr in ("models.generate_content", "generate_content", "models.generate_content_async"):
            try:
                self._wrap_attr(client, attr, "google")
                wrapped = True
            except AttributeError:
                continue
        if not wrapped:
            logger.warning("wrap_gemini: client has no 'generate_content'; use record_usage() manually")
        return client

    def wrap_ollama(self, client: Any) -> Any:
        wrapped = False
        for attr in ("chat", "generate", "achat", "agenerate"):
            try:
                self._wrap_attr(client, attr, "ollama")
                wrapped = True
            except AttributeError:
                continue
        if not wrapped:
            logger.warning("wrap_ollama: client has no 'chat' or 'generate'; use record_usage() manually")
        return client

    def wrap_cohere(self, client: Any) -> Any:
        """Wrap the Cohere v2 chat API (``client.v2.chat``) with a fallback to legacy ``client.chat``."""
        wrapped = False
        for attr in ("v2.chat", "chat", "v2.chat_stream", "chat_stream"):
            try:
                self._wrap_attr(client, attr, "cohere")
                wrapped = True
            except AttributeError:
                continue
        if not wrapped:
            logger.warning("wrap_cohere: client has no 'v2.chat' or 'chat'; use record_usage() manually")
        return client

    def unwrap(self, client: Any) -> Any:
        client_id = id(client)
        originals = self._original_methods.pop(client_id, {})
        for _attr_path, (parent, attr_name, original) in originals.items():
            setattr(parent, attr_name, original)
        return client

    def get_health(self) -> dict[str, Any]:
        """Return circuit breaker and rate limiter status per provider."""
        now = time.monotonic()
        result = {}
        all_providers = set(self._circuit_state.keys()) | set(self._rate_buckets.keys())
        for provider in sorted(all_providers):
            cb = self._circuit_state.get(provider, {"state": "closed", "failures": 0, "since": 0.0})
            bucket = self._rate_buckets.get(provider)
            entry: dict[str, Any] = {
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

    async def _check_rate_limit_async(self, provider: str) -> None:
        bucket = self._rate_buckets.get(provider)
        if bucket is None:
            cfg = self._get_provider_config(provider)
            rps = cfg.get("rate_limit_rps", self.rate_limit_rps)
            bucket = TokenBucket(rps)
            self._rate_buckets[provider] = bucket
        await bucket.async_consume()

    def _call_with_retry(
        self, original: Callable, args: tuple, kwargs: dict[str, Any], provider: str, timeout: Optional[float] = None
    ) -> Any:
        kwargs = self._strip_tracking_kwargs(kwargs)
        cfg = self._get_provider_config(provider)
        max_retries = cfg.get("max_retries", self.max_retries)
        retry_delay = cfg.get("retry_delay", self.retry_delay)
        req_timeout = cfg.get("request_timeout", timeout)

        delay = retry_delay
        for attempt in range(max_retries + 1):
            try:
                call_kwargs = {**kwargs, "timeout": req_timeout} if req_timeout else kwargs
                return original(*args, **call_kwargs)
            except Exception as e:
                if attempt < max_retries and _is_transient(e):
                    logger.warning("Retry %d/%d for %s: %s", attempt + 1, max_retries, provider, e)
                    time.sleep(delay)
                    delay *= 2
                else:
                    raise

    async def _call_with_retry_async(
        self, original: Callable, args: tuple, kwargs: dict[str, Any], provider: str, timeout: Optional[float] = None
    ) -> Any:
        # Rebinding locally keeps the caller's dict untouched.
        kwargs = self._strip_tracking_kwargs(kwargs)
        cfg = self._get_provider_config(provider)
        max_retries = cfg.get("max_retries", self.max_retries)
        retry_delay = cfg.get("retry_delay", self.retry_delay)
        req_timeout = cfg.get("request_timeout", timeout)

        delay = retry_delay
        for attempt in range(max_retries + 1):
            try:
                call_kwargs = {**kwargs, "timeout": req_timeout} if req_timeout else kwargs
                if req_timeout:
                    return await asyncio.wait_for(original(*args, **call_kwargs), timeout=req_timeout)
                return await original(*args, **call_kwargs)
            except Exception as e:
                if attempt < max_retries and _is_transient(e):
                    logger.warning("Async retry %d/%d for %s: %s", attempt + 1, max_retries, provider, e)
                    await asyncio.sleep(delay)
                    delay *= 2
                else:
                    raise

    def _extract_meta(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        ctx = ledger_context.get() or {}
        return {
            "user_id": kwargs.get("user_id", ctx.get("user_id", "anonymous")),
            "project_id": kwargs.get("project_id", ctx.get("project_id", "default")),
            "model": kwargs.get("model", "unknown"),
            "conversation_id": kwargs.get("conversation_id", ctx.get("conversation_id")),
            "agent_id": kwargs.get("agent_id", ctx.get("agent_id")),
            "prompt_hash": kwargs.get("prompt_hash", ctx.get("prompt_hash")),
            "tenant_id": kwargs.get("tenant_id", ctx.get("tenant_id")),
        }

    def _budget_check(self, metadata: dict[str, Any], provider: str, kwargs: dict[str, Any]) -> None:
        try:
            self.enforcer.check_budget(
                user_id=metadata["user_id"],
                project_id=metadata["project_id"],
                provider=provider,
                model=metadata["model"],
                messages=kwargs.get("messages", []),
                max_tokens=kwargs.get("max_tokens"),
                tenant_id=metadata.get("tenant_id") or "",
                conversation_id=metadata.get("conversation_id") or "",
                agent_id=metadata.get("agent_id") or "",
            )
        except BudgetExceededError as e:
            if self.ghost_mode:
                return
            self._log_blocked_attempt(metadata, provider)
            if self.on_budget_exceeded:
                self.on_budget_exceeded(e)
            raise

    def _apply_prompt_redaction(self, metadata: dict[str, Any], messages: list) -> None:
        redact = getattr(self.ledger, "redact_prompts", False)
        if redact and not metadata.get("prompt_hash") and messages:
            metadata["prompt_hash"] = self.ledger.fingerprint_prompt(messages)

    def _finalize_and_store(self, provider: str, metadata: dict[str, Any], record: dict[str, Any], raw_response: Any = None) -> None:
        """Shared pipeline: unknown-model policy, ghost, verify, DP, insert, callbacks."""
        if not self.pricing.has_model(provider, metadata["model"]):
            if self.unknown_model_policy == "block":
                raise UnknownModelError(f"Unknown model: {provider}:{metadata['model']}")
            if self.unknown_model_policy == "allow":
                record["cost_usd"] = 0.0
                record["_allow_zero_cost"] = True
        sysmon = getattr(self.ledger, "system_monitor", None)
        if sysmon:
            record["system"] = sysmon.snapshot()
        if self.ghost_mode:
            record["_ghost"] = True
        record = self.verifier.verify(record, raw_response)
        # Ghost persist optimization: allow dropping ghost records to save I/O
        if record.get("_ghost") and not getattr(self.ledger, "ghost_persist", True):
            if self.on_record:
                self.on_record(record)
            return
        try:
            self.store.insert_record(record)
        except Exception as e:
            # Best-effort persistence — a store failure (disk full, DB
            # locked) must never fail the user's API call.
            logger.error("Failed to persist usage record: %s", e)
        if self.on_record:
            self.on_record(record)
        # Post-hoc strict budget enforcement: actual cost may exceed the
        # pre-flight estimate. Matches budgets for this record's scope only.
        if getattr(self.ledger, "strict_budget", False) and record.get("status") not in ("blocked", "error"):
            self._enforce_post_hoc_budget(record)
        # Cost contracts — enforce after every stored record (scoped or global)
        try:
            ledger_contracts = getattr(self.ledger, "cost_contracts", None)
            if ledger_contracts:
                for contract in ledger_contracts.all():
                    if not ledger_contracts.check(contract.name, float(record.get("cost_usd", 0) or 0)):
                        record["contract_breached"] = contract.name
                        break
        except Exception as e:
            logger.debug("cost contract check failed: %s", e)

    def _enforce_post_hoc_budget(self, record: dict[str, Any]) -> None:
        """Raise after insertion when a post-hoc overrun exceeds the grace margin."""
        try:
            for _key, b in self.store.get_all_budgets().items():
                if not matches_scope(record, b.get("scope", "global"), b.get("scope_id", "all")):
                    continue
                spend = self.enforcer._calculate_current_spend(b)
                limit = float(b.get("limit_usd", 0))
                if limit and spend > limit * 1.05:
                    record["budget_overrun"] = True
                    err = BudgetExceededError(
                        f"Post-hoc budget overrun: ${spend:.4f} > ${limit:.4f}",
                        b.get("scope", ""),
                        b.get("scope_id", ""),
                        spend,
                        limit,
                    )
                    if self.on_budget_exceeded:
                        self.on_budget_exceeded(err)
                    # Ghost mode records the flag but never blocks the call.
                    if not record.get("_ghost"):
                        raise err
        except BudgetExceededError:
            raise
        except Exception as e:
            logger.debug("post-hoc budget check failed: %s", e)

    def _record(self, provider: str, metadata: dict[str, Any], messages: list, response: Any, latency_ms: float) -> None:
        self._apply_prompt_redaction(metadata, messages)
        token_data = self.extractor.extract(response, provider)
        if token_data is None:
            token_data = self.estimator.estimate(
                messages=messages,
                model=metadata["model"],
                provider=provider,
            )
        record = build_record(
            provider=provider,
            model=metadata["model"],
            input_tokens=token_data["input_tokens"],
            output_tokens=token_data["output_tokens"],
            user_id=metadata["user_id"],
            project_id=metadata["project_id"],
            latency_ms=round(latency_ms, 3),
            source=token_data.get("source", "api_reported"),
            pricing=self.pricing,
            status="success",
            tenant_id=metadata.get("tenant_id"),
            conversation_id=metadata.get("conversation_id"),
            agent_id=metadata.get("agent_id"),
            prompt_hash=metadata.get("prompt_hash"),
            cached_input_tokens=int(token_data.get("cached_input_tokens", 0) or 0),
            cache_hit=bool(token_data.get("cache_hit") or token_data.get("cached_input_tokens")),
            reasoning_tokens=int(token_data.get("reasoning_tokens", 0) or 0),
        )
        self._finalize_and_store(provider, metadata, record, response)
        self.enforcer.update_model_stats(metadata["model"], token_data["input_tokens"], token_data["output_tokens"])

    def _handle_stream(self, response: Any, provider: str, metadata: dict[str, Any], messages: list, start_time: float) -> Any:
        def on_finish() -> None:
            latency = (time.monotonic() - start_time) * 1000
            self._record_usage_from_stream(
                provider,
                metadata,
                messages,
                wrapped.get_accumulated_usage(),
                latency,
                wrapped.get_stream_text(),
            )

        wrapped = StreamWrapper(response, provider, on_finish=on_finish)
        return wrapped

    def _record_usage_from_stream(
        self,
        provider: str,
        metadata: dict[str, Any],
        messages: list,
        token_data: Optional[dict[str, Any]],
        latency_ms: float,
        stream_text: str = "",
    ) -> None:
        self._apply_prompt_redaction(metadata, messages)
        source = "stream"
        if not token_data:
            token_data = self._estimate_stream_usage(provider, metadata["model"], messages, stream_text)
            source = "stream_fallback_estimated"
        record = build_record(
            provider=provider,
            model=metadata["model"],
            input_tokens=token_data["input_tokens"],
            output_tokens=token_data["output_tokens"],
            user_id=metadata["user_id"],
            project_id=metadata["project_id"],
            latency_ms=round(latency_ms, 3),
            source=source,
            pricing=self.pricing,
            status="success",
            tenant_id=metadata.get("tenant_id"),
            conversation_id=metadata.get("conversation_id"),
            agent_id=metadata.get("agent_id"),
            prompt_hash=metadata.get("prompt_hash"),
        )
        self._finalize_and_store(provider, metadata, record)
        self.enforcer.update_model_stats(metadata["model"], token_data["input_tokens"], token_data["output_tokens"])

    def _estimate_stream_usage(self, provider: str, model: str, messages: list, stream_text: str = "") -> dict[str, Any]:
        """Estimate usage from request messages when the stream never reported usage.

        OpenAI requires ``stream_options={"include_usage": True}`` for usage in
        streams; other SDKs differ. We fall back to a request-side estimate and
        measure the output side from the streamed text when available.
        """
        return self.estimator.estimate(
            messages=messages,
            model=model,
            provider=provider,
            output_text=stream_text or None,
        )

    def _track_request(self, original: Callable, provider: str, args: tuple, kwargs: dict[str, Any]) -> Any:
        metadata = self._extract_meta(kwargs)
        self._budget_check(metadata, provider, kwargs)
        self._check_circuit(provider)
        self._check_rate_limit(provider)
        start_time = time.monotonic()
        try:
            response = self._call_with_retry(original, args, kwargs, provider, self.request_timeout)
        except Exception:
            try:
                self._record_result(provider, False)
            except Exception as cb_err:
                logger.warning("Circuit breaker record failed: %s", cb_err)
            raise
        latency_ms = (time.monotonic() - start_time) * 1000
        self._record_result(provider, True)

        if kwargs.get("stream"):
            return self._handle_stream(response, provider, metadata, kwargs.get("messages", []), start_time)

        self._record(provider, metadata, kwargs.get("messages", []), response, latency_ms)
        return response

    async def _track_request_async(self, original: Callable, provider: str, args: tuple, kwargs: dict[str, Any]) -> Any:
        metadata = self._extract_meta(kwargs)
        self._budget_check(metadata, provider, kwargs)
        self._check_circuit(provider)
        await self._check_rate_limit_async(provider)
        start_time = time.monotonic()
        try:
            response = await self._call_with_retry_async(original, args, kwargs, provider, self.request_timeout)
        except Exception:
            try:
                self._record_result(provider, False)
            except Exception as cb_err:
                logger.warning("Circuit breaker record failed: %s", cb_err)
            raise
        latency_ms = (time.monotonic() - start_time) * 1000
        self._record_result(provider, True)

        if kwargs.get("stream"):
            return self._handle_stream(response, provider, metadata, kwargs.get("messages", []), start_time)

        self._record(provider, metadata, kwargs.get("messages", []), response, latency_ms)
        return response

    def _log_blocked_attempt(self, metadata: dict[str, Any], provider: str) -> None:
        record = build_record(
            provider=provider,
            model=metadata.get("model", "unknown"),
            input_tokens=0,
            output_tokens=0,
            user_id=metadata.get("user_id", "anonymous"),
            project_id=metadata.get("project_id", "default"),
            source="budget_blocked",
            status="blocked",
            tenant_id=metadata.get("tenant_id"),
            conversation_id=metadata.get("conversation_id"),
            agent_id=metadata.get("agent_id"),
            prompt_hash=metadata.get("prompt_hash"),
        )
        try:
            self.store.insert_record(record)
        except Exception as e:
            logger.warning("Failed to persist blocked-attempt record: %s", e)
