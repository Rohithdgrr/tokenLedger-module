# Changelog

## v1.3.1 (2026-08-19)

- **Streaming usage now always recorded**: `StreamWrapper` is callback-based (`__iter__`/`__aiter__` can't be monkey-patched per-instance); fallback estimation measures output from streamed text instead of inventing tokens; async stream path covered end-to-end
- **Thread-safe rate limiting**: `TokenBucket` guarded by a lock with `async_consume()`; async variant of retry/rate-limit used on wrapped async methods
- **Retries only on transient errors**: `_is_transient` (connection/timeout/status 408, 409, 429, 500, 502-504) — permanent failures no longer retried, with exponential backoff and optional per-provider `request_timeout`
- **Unknown-model policy enforced everywhere**: `block` raises `UnknownModelError`, `allow` zeroes cost, on both the manual `record_usage()` and interceptor wrap paths
- **Ghost/blocked records excluded from spend**: `_is_billable` filter applied to running totals (memory + SQLite) and summary `top_models`/`top_providers`
- **Cohere v2, Gemini async, Ollama async wraps**: `wrap_cohere` targets `v2.chat` with `chat` fallback; `wrap_gemini`/`wrap_ollama` wrap async variants (`generate_content_async`, `achat`, `agenerate`)
- **Differential privacy via inverse-CDF Laplace** (cryptographically secure uniform from `secrets`); applied on both record paths
- **"Encryption" relabeled to obfuscation-at-rest**: XOR + HMAC tag with wrong-key detection; `encryption_key` accepts `str` or `bytes` (normalized); docs updated (README, docs/)
- **Retention fixes**: naive timestamps treated as UTC; `MemoryStore.compact(max_age_days=...)`; `apply_retention()` on the store ABC; SQLite WAL mode + thread-local connections + `close()` (Windows-compatible)
- **Estimator no longer invents outputs**: `output_tokens=0` without output text/`output_text`; `get_default_key()` prefers `_default:unknown`
- **CLI/system hardening**: subprocess calls use argv lists (no `shell=True`); exports return the audit dict; tenant dimension in budget scoping
- **Type/lint hygiene**: `StorageBackend`/`BudgetEnforcer`/analytics accept the backend protocol; mypy errors cut 56→12 (all pre-existing, in `ext/`); ruff clean; bandit back to baseline (13 findings, including DP-noise B311 eliminated via `secrets`)
- Test coverage raised to **87%** (gate: 80%) with ~25 regression tests for the fixes above

## v1.3.0 (2026-08-18)

- **Budget windows actually enforced**: `reset_cycle` (`daily`/`weekly`/`monthly`) now applied to spend calculation — spend is computed from records inside the current window; `never` keeps the O(1) running-totals path; `user_project` spend now uses the intersection of user + project instead of `max(user, project)`; blocked/error/ghost records excluded from windowed spend
- **Pricing corrected**: all bundled rates normalized to **USD per 1M tokens** (declared via `_meta.unit`), fixing ~1000× scale errors for Mistral/Cohere/NVIDIA/Kimi/GLM/MiniMax; legacy per-1k files still load; load-time validation warns on negative or suspiciously high rates
- **CLI no longer hard-depends on `rich`**: falls back to plain-text tables/progress/prompts when `rich` is missing; new `cli` extra; `rich` and `psutil` added to `all` extra
- **`SqliteStore.compact()` fixed**: previously deleted nearly everything (cutoff was "now"); now prunes only records older than `max_age_days` (default 90) and caps at `max_records`
- **Budgets persisted in SQLite**: `set_budget()` writes to a `budgets` table, restored on reopen
- **Packaging**: `pricing_data.json` now included as package data; install docs corrected (`pip install tokenledger-module`, import `tokenledger`)
- Test coverage added for budget windows, pricing golden values, pricing validation, and SQLite compact/budget persistence

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
