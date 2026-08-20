# Security Policy

## Reporting a Vulnerability

Open an issue on the [GitHub repository](https://github.com/Rohithdgrr/tokenLedger-module/issues)
with the `security` label, or contact the maintainers directly. Do **not**
post exploit details in public issues.

We aim to acknowledge reports within 5 business days and ship a fix in a
patch release.

## Encryption: Know Your Fallback

TokenLedger persists JSONL with optional at-rest protection using
`encryption_key`:

- **Fernet (AES-128-CBC + HMAC-SHA256)** is used when the `cryptography`
  package is installed (`pip install tokenledger-module[security]`).
  This is real encryption and the only mode recommended for private data.
  The key is hashed with SHA-256 to a fixed 32-byte digest before use, so
  **any passphrase length is accepted — but treat it like a password**.
- **XOR + HMAC-SHA256 obfuscation** is the automatic fallback when
  `cryptography` is missing. It is **not** encryption: XOR is trivially
  reversible and provides casual privacy against casual reading only.
  Never store secrets, API keys, or PII in a ledger persisted without the
  `security` extra. A one-time `WARNING` is logged when the fallback engages.

If the persisted file is tampered with, the HMAC check fails on load and the
records are rejected — but this protects against *accidental corruption*,
not a determined adversary.

## LiveServer Exposure

The live server (`LiveServer`, port 8765 by default) has **no TLS** and only
optional bearer-token auth via `api_key`. It is designed for trusted local
diagnostics.

- Never expose it to the public internet without a reverse proxy (TLS +
  auth).
- When bound to `0.0.0.0`, protect it with `api_key` and a firewall.
- Prefer `127.0.0.1` binding for single-host setups.

## Prompt Hashing Is Not Authentication

`prompt_hash` (and `track_prompt_version`) use SHA-256 of the prompt text.
This is a deduplication/change-detection fingerprint, **not** a
cryptographic binding to ledger state:

- Two ledgers can hold identical hashes for different prompts (`_ghost`
  records, replayed writes).
- Do not use hashes as evidence of non-repudiation; use
  `sign_ledger`/`verify_signed_ledger` (HMAC-SHA256 over the full ledger) for
  that.

## Budgets and Circuit Breakers Are Rate Controls, Not Access Control

- `ghost_mode` records without enforcing/spending; `strict_budget` raises
  `BudgetExceededError` *after* the call returns. Neither is a security
  boundary.
- Budget enforcement runs client-side. A compromised process can bypass it.
- `differential_privacy_epsilon` adds Laplace noise at the query/export
  boundary — privacy protection, not confidentiality or encryption.

## Dependencies

- The core package has zero required dependencies.
- Optional extras (`openai`, `tiktoken`, `security`, `system`,
  `opentelemetry`, `cli`) are validated in CI via `pip-audit`.
- Pin exact versions in production deployments and subscribe to
  vulnerability notifications for the SDKs you use.