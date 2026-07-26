# Changelog

## v1.2.0 (2026-07-26)

- **Streaming support**: `StreamWrapper` tracks accumulated usage from streamed responses; automatic detection via `stream=True` kwarg
- **Per-provider config**: `configure_provider(provider, max_retries=..., rate_limit_rps=..., request_timeout=...)` for per-provider overrides
- **Callbacks**: `on_budget_exceeded` and `on_record` hooks on `InterceptionLayer`
- **Health endpoint**: `interceptor.get_health()` returns circuit breaker + rate limiter status per provider
- **Smarter budget estimation**: `BudgetEnforcer.check_budget()` accepts `max_tokens`; per-model output ratio cache via `update_model_stats()`
- **Store `compact()`**: forces retention pruning + aggregate rebuild, returns removal stats
- **`get_record_count()`**: O(1) record count on `MemoryStore`
- **`prompt_hash` tracking**: added to `TRACKING_KWARGS` and auto-included in records

## v1.1.0 (2026-07-25)

- Data retention: ring buffer (`deque(maxlen=...)`), age-based pruning (`RetentionPolicy`), `compact()` for manual trim
- Immutable event logs: SHA-256 checksums per record, verified JSONL format with `verify_immutability()`
- AI-specific tracking: `conversation_id`, `agent_id`, `prompt_hash`, `reasoning_tokens`, `cached_input_tokens`, `embedding_tokens`, `tool_calls`, `media_type`, `cache_hit`
- Efficiency stats (`get_efficiency_stats`) and cost breakdown (`get_cost_breakdown`) in analytics
- Prompt fingerprinting: `ledger.fingerprint_prompt()` for cache-hit analysis
- Kwarg stripping: tracking metadata removed before original API call
- Token bucket rate limiter (per-provider, configurable RPS)
- O(1) budget spend via running totals (replaced O(n) scan)
- `print` statements replaced with `logging.warning`
- Concurrency tests, benchmark suite, kwarg stripping tests, rate limiter tests

## v1.0.0 (2026-07-24)

- Initial release: per-provider pricing, budget enforcement, in-memory store, analytics, method-level interception, circuit breaker, `record_usage()` API
