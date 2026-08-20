"""SQLite storage backend for TokenLedger — persists records to a local DB file."""

import contextlib
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from tokenledger.core.store import (
    StorageBackend,
    _is_billable,
    checksum_matches,
    normalize_ts,
)


class SqliteStore(StorageBackend):
    """Thread-safe SQLite-backed store compatible with MemoryStore's record interface.

    Usage:
        store = SqliteStore("usage.db")
        ledger = TokenLedger()
        ledger.store = store  # swap in the SQLite backend
    """

    def __init__(self, db_path: str, max_records: int = 100_000):
        self.db_path = db_path
        self.max_records = max_records
        self.lock = threading.RLock()
        self.budgets: dict[str, dict[str, Any]] = {}
        self.running_totals: dict[str, dict[str, Any]] = {}
        self._local = threading.local()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        """Return this thread's persistent connection (WAL mode, no per-op connect)."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
        return conn

    def _init_db(self) -> None:
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=5000")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS records (
                        record_id TEXT PRIMARY KEY,
                        timestamp TEXT,
                        provider TEXT,
                        model TEXT,
                        input_tokens INTEGER DEFAULT 0,
                        output_tokens INTEGER DEFAULT 0,
                        total_tokens INTEGER DEFAULT 0,
                        cost_usd REAL DEFAULT 0,
                        latency_ms REAL DEFAULT 0,
                        user_id TEXT DEFAULT 'anonymous',
                        project_id TEXT DEFAULT 'default',
                        status TEXT DEFAULT 'success',
                        source TEXT DEFAULT 'manual',
                        conversation_id TEXT,
                        agent_id TEXT,
                        prompt_hash TEXT,
                        tenant_id TEXT,
                        _checksum TEXT,
                        extra TEXT
                    )
                """)
                # Migration for DBs created before _checksum column existed
                try:
                    cols = {row[1] for row in conn.execute("PRAGMA table_info(records)")}
                    if "_checksum" not in cols:
                        conn.execute("ALTER TABLE records ADD COLUMN _checksum TEXT")
                except Exception:
                    pass
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ts ON records(timestamp)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_provider ON records(provider)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_user ON records(user_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_tenant ON records(tenant_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_conversation ON records(conversation_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_agent ON records(agent_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_project ON records(project_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_model ON records(model)")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS budgets (
                        key TEXT PRIMARY KEY,
                        config TEXT
                    )
                """)
                conn.commit()
                for key, config in conn.execute("SELECT key, config FROM budgets"):
                    with contextlib.suppress(json.JSONDecodeError, TypeError):
                        self.budgets[key] = json.loads(config)
            finally:
                conn.close()
        self._rebuild_running_totals()

    def _rebuild_running_totals(self) -> None:
        self.running_totals.clear()
        for r in self.get_records():
            self._update_running_totals(r)

    def _update_running_totals(self, record: dict[str, Any]) -> None:
        if not _is_billable(record):
            return
        dimensions = [
            ("global", "all"),
            ("provider", record.get("provider", "unknown")),
            ("model", record.get("model", "unknown")),
            ("user", record.get("user_id", "anonymous")),
            ("project", record.get("project_id", "default")),
            ("month", record.get("timestamp", "")[:7]),
        ]
        if record.get("conversation_id") and str(record["conversation_id"]).strip():
            dimensions.append(("conversation", record["conversation_id"]))
        if record.get("agent_id") and str(record["agent_id"]).strip():
            dimensions.append(("agent", record["agent_id"]))
        if record.get("tenant_id") and str(record["tenant_id"]).strip():
            dimensions.append(("tenant", record["tenant_id"]))
        for scope, scope_id in dimensions:
            key = f"{scope}:{scope_id}"
            agg = self.running_totals.setdefault(
                key,
                {
                    "requests": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "cost_usd": 0.0,
                },
            )
            agg["requests"] += 1
            agg["input_tokens"] += record.get("input_tokens", 0)
            agg["output_tokens"] += record.get("output_tokens", 0)
            agg["total_tokens"] += record.get("total_tokens", 0)
            agg["cost_usd"] += record.get("cost_usd", 0.0)

    def insert_record(self, record: dict[str, Any]) -> None:
        with self.lock:
            extra = {k: v for k, v in record.items() if k not in self._COLUMNS}
            conn = self._conn()
            conn.execute(
                """
                    INSERT OR REPLACE INTO records
                    (record_id, timestamp, provider, model, input_tokens, output_tokens,
                     total_tokens, cost_usd, latency_ms, user_id, project_id, status, source,
                     conversation_id, agent_id, prompt_hash, tenant_id, _checksum, extra)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.get("record_id"),
                    normalize_ts(record.get("timestamp")),
                    record.get("provider"),
                    record.get("model"),
                    record.get("input_tokens", 0),
                    record.get("output_tokens", 0),
                    record.get("total_tokens", 0),
                    record.get("cost_usd", 0.0),
                    record.get("latency_ms", 0.0),
                    record.get("user_id", "anonymous"),
                    record.get("project_id", "default"),
                    record.get("status", "success"),
                    record.get("source", "manual"),
                    record.get("conversation_id"),
                    record.get("agent_id"),
                    record.get("prompt_hash"),
                    record.get("tenant_id"),
                    record.get("_checksum"),
                    json.dumps(extra, default=str) if extra else None,
                ),
            )
            conn.commit()
            self._update_running_totals(record)

    def insert_records_batch(self, records: list[dict[str, Any]]) -> None:
        """Efficient bulk insert — single transaction for many records."""
        if not records:
            return
        with self.lock:
            conn = self._conn()
            conn.execute("BEGIN")
            try:
                for record in records:
                    extra = {k: v for k, v in record.items() if k not in self._COLUMNS}
                    conn.execute(
                        """
                            INSERT OR REPLACE INTO records
                            (record_id, timestamp, provider, model, input_tokens, output_tokens,
                             total_tokens, cost_usd, latency_ms, user_id, project_id, status, source,
                             conversation_id, agent_id, prompt_hash, tenant_id, _checksum, extra)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            record.get("record_id"),
                            normalize_ts(record.get("timestamp")),
                            record.get("provider"),
                            record.get("model"),
                            record.get("input_tokens", 0),
                            record.get("output_tokens", 0),
                            record.get("total_tokens", 0),
                            record.get("cost_usd", 0.0),
                            record.get("latency_ms", 0.0),
                            record.get("user_id", "anonymous"),
                            record.get("project_id", "default"),
                            record.get("status", "success"),
                            record.get("source", "manual"),
                            record.get("conversation_id"),
                            record.get("agent_id"),
                            record.get("prompt_hash"),
                            record.get("tenant_id"),
                            record.get("_checksum"),
                            json.dumps(extra, default=str) if extra else None,
                        ),
                    )
                    self._update_running_totals(record)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    _COLUMNS = {
        "record_id",
        "timestamp",
        "provider",
        "model",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cost_usd",
        "latency_ms",
        "user_id",
        "project_id",
        "status",
        "source",
        "conversation_id",
        "agent_id",
        "prompt_hash",
        "tenant_id",
        "_checksum",
    }

    def get_records(self, limit: Optional[int] = None) -> list[dict[str, Any]]:
        with self.lock:
            conn = self._conn()
            cursor = conn.execute(
                "SELECT * FROM records ORDER BY timestamp" + (" LIMIT ?" if limit else ""),  # nosec B608: constant fragment, values parameterized
                (limit,) if limit else (),
            )
            rows = []
            for row in cursor.fetchall():
                r = dict(zip([d[0] for d in cursor.description], row))
                extra = r.pop("extra", None)
                if extra:
                    with contextlib.suppress(json.JSONDecodeError, TypeError):
                        r.update(json.loads(extra))
                rows.append(r)
            return rows

    def get_running_totals(self, scope: str, scope_id: str) -> dict[str, Any]:
        key = f"{scope}:{scope_id}"
        with self.lock:
            return dict(
                self.running_totals.get(
                    key,
                    {
                        "requests": 0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": 0,
                        "cost_usd": 0.0,
                    },
                )
            )

    def set_budget(self, scope: str, scope_id: str, budget_config: dict[str, Any]) -> None:
        key = f"{scope}:{scope_id}"
        with self.lock:
            self.budgets[key] = budget_config
            conn = self._conn()
            conn.execute(
                "INSERT OR REPLACE INTO budgets (key, config) VALUES (?, ?)",
                (key, json.dumps(budget_config, default=str)),
            )
            conn.commit()

    def get_budget(self, scope: str, scope_id: str) -> Optional[dict[str, Any]]:
        with self.lock:
            return self.budgets.get(f"{scope}:{scope_id}")

    def delete_budget(self, scope: str, scope_id: str) -> None:
        key = f"{scope}:{scope_id}"
        with self.lock:
            self.budgets.pop(key, None)
            conn = self._conn()
            conn.execute("DELETE FROM budgets WHERE key = ?", (key,))
            conn.commit()

    def get_all_budgets(self) -> dict[str, dict[str, Any]]:
        with self.lock:
            return dict(self.budgets)

    def verify_immutability(self) -> list[str]:
        tampered = []
        for r in self.get_records():
            if not r.get("_checksum"):
                continue
            if not checksum_matches(r):
                tampered.append(r.get("record_id", "unknown"))
        return tampered

    def clear(self) -> None:
        with self.lock:
            conn = self._conn()
            conn.execute("DELETE FROM records")
            conn.execute("DELETE FROM budgets")
            conn.commit()
            self.running_totals.clear()
            self.budgets.clear()

    def compact(self, max_age_days: Optional[int] = None) -> dict[str, Any]:
        """Remove records older than ``max_age_days`` (default 90) and cap
        the table at ``max_records`` newest rows."""
        days = max_age_days if max_age_days is not None else 90
        # All timestamps are stored as naive UTC, so the naive cutoff
        # compares correctly with plain string comparison.
        cutoff_dt = datetime.now(timezone.utc) - timedelta(days=days)
        cutoff = cutoff_dt.replace(tzinfo=None).isoformat()
        with self.lock:
            before = self.get_record_count()
            conn = self._conn()
            conn.execute("DELETE FROM records WHERE timestamp < ?", (cutoff,))
            conn.execute(
                """
                DELETE FROM records WHERE record_id IN (
                    SELECT record_id FROM records
                    ORDER BY timestamp DESC
                    LIMIT -1 OFFSET ?
                )
            """,
                (self.max_records,),
            )
            conn.commit()
            self._rebuild_running_totals()
            after = self.get_record_count()
        return {"removed": before - after, "remaining": after}

    def get_windowed_spend(self, budget: dict[str, Any], window_start: str) -> Optional[float]:
        """Indexed SQLite SUM query for windowed budgets."""
        scope = budget.get("scope", "global")
        scope_id = budget.get("scope_id", "")
        # Build WHERE clauses that can use indexes; _ghost/blocked filtered in Python
        # because _ghost lives in the extra JSON column.
        window_start = normalize_ts(window_start)
        conditions = ["timestamp >= ?", "status NOT IN ('blocked','error')"]
        params: list[Any] = [window_start]
        if scope == "project":
            conditions.append("project_id = ?")
            params.append(scope_id)
        elif scope == "user":
            conditions.append("user_id = ?")
            params.append(scope_id)
        elif scope == "user_project":
            parts = scope_id.split(":")
            if len(parts) == 2:
                conditions.append("user_id = ?")
                params.append(parts[0])
                conditions.append("project_id = ?")
                params.append(parts[1])
            else:
                return 0.0
        elif scope == "provider":
            conditions.append("provider = ?")
            params.append(scope_id)
        elif scope == "model":
            conditions.append("model = ?")
            params.append(scope_id)
        elif scope == "tenant":
            conditions.append("tenant_id = ?")
            params.append(scope_id)
        elif scope == "conversation":
            conditions.append("conversation_id = ?")
            params.append(scope_id)
        elif scope == "agent":
            conditions.append("agent_id = ?")
            params.append(scope_id)
        # global scope: no extra filter
        sql = f"SELECT cost_usd, extra FROM records WHERE {' AND '.join(conditions)}"
        with self.lock:
            try:
                conn = self._conn()
                total = 0.0
                for cost_usd, extra in conn.execute(sql, params):
                    # Exclude ghost records (stored in extra JSON)
                    if extra:
                        try:
                            if json.loads(extra).get("_ghost"):
                                continue
                        except (json.JSONDecodeError, TypeError):
                            pass
                    total += float(cost_usd or 0)
                return total
            except Exception:
                return None

    def apply_retention(self, max_age_days: Optional[int] = None) -> None:
        self.compact(max_age_days=max_age_days)

    def close(self) -> None:
        """Close this thread's cached connection to release the DB file."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def get_record_count(self) -> int:
        with self.lock:
            conn = self._conn()
            row = conn.execute("SELECT COUNT(*) FROM records").fetchone()
            return row[0] if row else 0
