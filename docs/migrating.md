# Migrating Between Storage Backends

TokenLedger ships two `StorageBackend` implementations:

| | `MemoryStore` | `SqliteStore` |
|---|---|---|
| Location | `tokenledger.core.store.MemoryStore` | `tokenledger.ext.sqlite_store.SqliteStore` |
| Default | Yes (`TokenLedger()`) | No — opt in |
| Persistence | JSONL file (append + optional encryption) | Single SQLite database |
| Retention | Ring buffer + age-based compaction | SQL-deleted with pruning |
| Windowed spend | Full scan | Indexed `SUM` query |
| Checksums | `_checksum` per record + per-line HMAC | `_checksum` column per record |

## When to Use SQLite

Use `SqliteStore` when you need:

- durable, corruption-resistant storage for thousands of records,
- indexed budget-window queries (`get_windowed_spend`),
- concurrent writers from multiple processes (WAL mode),
- a single file you can back up and `VACUUM`.

Use `MemoryStore` when you need zero dependencies, ephemeral in-process
tracking, or encrypted-at-rest JSONL with human-readable files.

## Switching at Construction Time

```python
from tokenledger import TokenLedger
from tokenledger.ext.sqlite_store import SqliteStore

store = SqliteStore("usage.db")          # SQLite
ledger = TokenLedger(store=store)        # prefer this over ledger.store = store
```

> Swapping `ledger.store = store` after construction works but leaves the
> analytics/budget/verifier engines pointed at the old store. Construct the
> `TokenLedger` with `store=` instead.

## Migrating Existing Records

Both stores use the same record schema (plus the `_checksum` field). Two
options:

1. **Re-record**: replay `record_usage(...)` calls against the new ledger.
   Fast for small histories; recomputes checksums for free.

2. **Bulk import**: read old records and insert them into the new store.

```python
from tokenledger import TokenLedger
from tokenledger.ext.sqlite_store import SqliteStore

old = TokenLedger(persist_path="usage.jsonl")      # encrypted? pass encryption_key=
new_ledger = TokenLedger(store=SqliteStore("usage.db"))

for record in old.get_records():
    new_ledger.store.insert_record(dict(record))   # copy! insert mutates (_checksum)
```

### Caveats

- **Encryption**: `MemoryStore` encryption-at-rest is per-file; `SqliteStore`
  has no built-in encryption. If you need at-rest protection with SQLite,
  encrypt the database file itself (e.g. SQLCipher) or keep records redacted.
- **Checksums**: `insert_record` recomputes `_checksum` on insert. Records
  loaded from an old store keep their original checksums only if you trust
  them — recomputation is the safer default.
- **Timestamps**: timestamps are normalized to naive UTC. Records with
  timezone-aware or non-ISO timestamps are normalized on insert.
- **Budgets**: budgets live *in the store*, so `set_budget(...)` rules must
  be re-created on the new store:

```python
for key, budget in old.store.get_all_budgets().items():
    new_ledger.set_budget(budget["scope"], budget["scope_id"],
                          budget["limit_usd"], budget.get("reset_cycle", "monthly"))
```

- **`never` budgets**: spend is derived from `running_totals`, which are
  rebuilt on every insert. Never-budget totals survive retention pruning.

## Verifying the Migration

```python
assert new_ledger.store.verify_immutability() == []   # no tampered records
assert new_ledger.get_summary()["cost_usd"] == old.get_summary()["cost_usd"]
```

## Going Back (SQLite → MemoryStore)

Read from the SQLite ledger and insert into a fresh JSONL-backed one:

```python
from tokenledger import TokenLedger
from tokenledger.ext.sqlite_store import SqliteStore

old = TokenLedger(store=SqliteStore("usage.db"))
new = TokenLedger(persist_path="usage.jsonl")      # or encryption_key=...
for record in old.get_records():
    new.store.insert_record(dict(record))
```

> `insert_record` recomputes `_checksum` on insert; budgets must be
> re-created (see above).