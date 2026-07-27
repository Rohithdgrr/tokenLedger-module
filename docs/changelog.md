# Changelog

## v1.3.0 (2026-07-27)

### Features
- **Ghost Mode** — `ghost_mode=True` logs instead of blocking on budget exceeded
- **What-if Simulator** — `simulate_cost()` predicts cost before making calls
- **Agent/Conversation ROI** — `get_roi()` returns cost per output token, cost per request, output/input ratio
- **Signed Offline Ledgers** — HMAC-SHA256 signing and verification for offline audit bundles
- **Prompt Cache & Near-Duplicate Detection** — `PromptCache` with SequenceMatcher-based similarity search
- **Self-Improving Estimation** — `EstimatorFeedback` tracks accuracy, applies correction factor after 6+ reports
- **Smart Model Router** — `ModelRouter` picks cheapest model satisfying cost/latency constraints
- **Agentic Cost Contracts** — `CostContractRegistry` with named contracts and breach callbacks
- **Prompt Evolution Tracker** — versioned prompt history with unified diffs
- **Local LLM Real-Cost Modeling** — electricity + hardware cost per token for local models

### Tests
- 35 new tests covering all 10 features (174 total passing)

## v1.2.0 (2026-07-26)

### Features
- `StorageBackend` protocol with `MemoryStore` and `SqliteStore`
- Multi-tenant support (`tenant_id` dimension)
- Pluggable `VerificationEngine` with 6 built-in rules
- Encryption-at-rest for JSONL files
- Prompt redaction (SHA-256 hashing)
- Audit-ready export with checksum envelope
- Retention cleans on-disk JSONL
- Differential privacy with Laplace noise
- Property-based and mock integration tests
- Coverage target (80%) in pyproject.toml

## v1.1.0 (2026-07-25)

### Features
- CLI (`summary`, `export`, `verify`, `compact`, `health`, `update-pricing`)
- OpenTelemetry instrumentation
- Webhook/Slack notifier
- Async store wrapper
- `on_budget_threshold` callback
- Per-provider configuration
- External pricing via JSON file
- `@ledger.track` decorator
- Richer `get_summary()` with budget utilization, top models, anomalies

## v1.0.0 (2026-07-24)

### Features
- Streaming response support
- Per-provider callbacks (`on_budget_exceeded`, `on_record`)
- `configure_provider()` for per-provider overrides
- `get_health()` endpoint
- Smarter budget estimation with `max_tokens` and model stats cache
- `compact()` method
- CHANGELOG

## v0.x (2026-07-20 — 2026-07-23)

### Initial releases
- Core tracking with `record_usage()`
- Provider wrappers (OpenAI, Anthropic, Gemini, Groq, Ollama, etc.)
- Budget enforcement
- Analytics and summary
- CSV/JSON export
- AI-specific tracking (conversation, agent, reasoning, cache, embeddings, tools)
- Prompt fingerprinting
- Data retention and immutability
- Verification engine
- System monitoring
