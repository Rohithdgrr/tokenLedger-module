"""SQLite storage backend for TokenLedger — persists records to a local DB file."""

import contextlib
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from tokenledger.core.store import StorageBackend, _is_billable


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
                        extra TEXT
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ts ON records(timestamp)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_provider ON records(provider)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_user ON records(user_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_tenant ON records(tenant_id)")
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
        if record.get("tenant_id"):
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
            extra = {k: v for k, v in record.items() if k not in self._COLUMNS and k != "_checksum"}
            conn = self._conn()
            conn.execute(
                """
                    INSERT OR REPLACE INTO records
                    (record_id, timestamp, provider, model, input_tokens, output_tokens,
                     total_tokens, cost_usd, latency_ms, user_id, project_id, status, source,
                     conversation_id, agent_id, prompt_hash, tenant_id, extra)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.get("record_id"),
                    record.get("timestamp"),
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
                    json.dumps(extra, default=str) if extra else None,
                ),
            )
            conn.commit()
            self._update_running_totals(record)

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

    def get_all_budgets(self) -> dict[str, dict[str, Any]]:
        with self.lock:
            return dict(self.budgets)

    def verify_immutability(self) -> list[str]:
        tampered = []
        for r in self.get_records():
            expected = r.get("_checksum", "")
            if not expected:
                continue
            if "_checksum" in r:
                raw = json.dumps({k: v for k, v in r.items() if k != "_checksum"}, sort_keys=True, default=str).encode()
                import hashlib

                actual = hashlib.sha256(raw).hexdigest()
                if expected != actual:
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
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
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
