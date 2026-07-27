"""Storage backends for TokenLedger with formal ABC/protocol."""

import abc
import asyncio
import hashlib
import json
import logging
import os
import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


class StorageBackend(abc.ABC):
    """Abstract storage backend that all stores must implement."""

    @abc.abstractmethod
    def insert_record(self, record: dict[str, Any]) -> None:
        ...

    @abc.abstractmethod
    def get_records(self) -> list[dict[str, Any]]:
        ...

    @abc.abstractmethod
    def get_running_totals(self, scope: str, scope_id: str) -> dict[str, Any]:
        ...

    @abc.abstractmethod
    def set_budget(self, scope: str, scope_id: str, budget_config: dict[str, Any]) -> None:
        ...

    @abc.abstractmethod
    def get_budget(self, scope: str, scope_id: str) -> Optional[dict[str, Any]]:
        ...

    @abc.abstractmethod
    def get_all_budgets(self) -> dict[str, dict[str, Any]]:
        ...

    @abc.abstractmethod
    def clear(self) -> None:
        ...

    @abc.abstractmethod
    def compact(self) -> dict[str, Any]:
        ...

    @abc.abstractmethod
    def get_record_count(self) -> int:
        ...

    @abc.abstractmethod
    def verify_immutability(self) -> list[str]:
        ...


class RetentionPolicy:
    def __init__(self, max_age_days: int = 90, max_records: int = 100_000, archive_on_trim: bool = True):
        self.max_age_days = max_age_days
        self.max_records = max_records
        self.archive_on_trim = archive_on_trim


def _checksum(record: dict[str, Any]) -> str:
    raw = json.dumps(record, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def _encrypt(data: bytes, key: bytes) -> bytes:
    """XOR-based encryption for lightweight at-rest protection."""
    return bytes(a ^ b for a, b in zip(data, key * (len(data) // len(key) + 1)))


class MemoryStore(StorageBackend):
    """Thread-safe in-memory store with ring buffer, retention, encryption, and JSONL persistence."""

    def __init__(self, persist_path: Optional[str] = None, max_records: int = 100_000,
                 retention_days: int = 90, encryption_key: Optional[bytes] = None):
        self.records: deque[dict[str, Any]] = deque(maxlen=max_records)
        self.budgets: dict[str, dict[str, Any]] = {}
        self.running_totals: dict[str, dict[str, Any]] = {}
        self.lock = threading.RLock()
        self.persist_path = persist_path
        self.encryption_key = encryption_key
        self.retention = RetentionPolicy(max_age_days=retention_days, max_records=max_records, archive_on_trim=True)
        if persist_path and os.path.exists(persist_path):
            self._load_from_disk()

    def insert_record(self, record: dict[str, Any]) -> None:
        with self.lock:
            record["_checksum"] = _checksum(record)
            self.records.append(record)
            self._update_running_totals(record)
            self._apply_retention()
            if self.persist_path:
                self._append_to_disk(record)

    def _update_running_totals(self, record: dict[str, Any]) -> None:
        dimensions = [
            ("global", "all"),
            ("provider", record.get("provider", "unknown")),
            ("model", record.get("model", "unknown")),
            ("user", record.get("user_id", "anonymous")),
            ("project", record.get("project_id", "default")),
            ("month", record.get("timestamp", "")[:7]),
        ]
        if record.get("conversation_id"):
            dimensions.append(("conversation", record["conversation_id"]))
        if record.get("agent_id"):
            dimensions.append(("agent", record["agent_id"]))
        if record.get("tenant_id"):
            dimensions.append(("tenant", record["tenant_id"]))
        for scope, scope_id in dimensions:
            key = f"{scope}:{scope_id}"
            agg = self.running_totals.setdefault(key, {"requests": 0, "input_tokens": 0, "output_tokens": 0,
                                                         "total_tokens": 0, "cost_usd": 0.0})
            agg["requests"] += 1
            agg["input_tokens"] += record.get("input_tokens", 0)
            agg["output_tokens"] += record.get("output_tokens", 0)
            agg["total_tokens"] += record.get("total_tokens", 0)
            agg["cost_usd"] += record.get("cost_usd", 0.0)

    def _append_to_disk(self, record: dict[str, Any]) -> None:
        try:
            line = json.dumps(record, default=str)
            checksum = hashlib.sha256(line.encode()).hexdigest()
            payload = f"{checksum}:{line}\n".encode()
            if self.encryption_key:
                payload = _encrypt(payload, self.encryption_key)
            with open(self.persist_path, "ab") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
        except OSError as e:
            logger.warning("Failed to persist record: %s", e)

    def _load_from_disk(self) -> None:
        try:
            with open(self.persist_path, "rb") as f:
                raw = f.read()
            if self.encryption_key:
                raw = _encrypt(raw, self.encryption_key)
            text = raw.decode("utf-8", errors="replace")
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    if ":" in line:
                        stored_checksum, rest = line.split(":", 1)
                        if len(stored_checksum) == 64:
                            actual = hashlib.sha256(rest.encode()).hexdigest()
                            if stored_checksum == actual:
                                record = json.loads(rest)
                                self.records.append(record)
                                self._update_running_totals(record)
                        else:
                            record = json.loads(line)
                            self.records.append(record)
                            self._update_running_totals(record)
                    else:
                        record = json.loads(line)
                        self.records.append(record)
                        self._update_running_totals(record)
                except (json.JSONDecodeError, ValueError):
                    continue
        except OSError:
            pass

    def get_records(self) -> list[dict[str, Any]]:
        with self.lock:
            return list(self.records)

    def get_running_totals(self, scope: str, scope_id: str) -> dict[str, Any]:
        key = f"{scope}:{scope_id}"
        with self.lock:
            return dict(self.running_totals.get(key, {"requests": 0, "input_tokens": 0, "output_tokens": 0,
                                                        "total_tokens": 0, "cost_usd": 0.0}))

    def set_budget(self, scope: str, scope_id: str, budget_config: dict[str, Any]) -> None:
        with self.lock:
            self.budgets[f"{scope}:{scope_id}"] = budget_config

    def get_budget(self, scope: str, scope_id: str) -> Optional[dict[str, Any]]:
        with self.lock:
            return self.budgets.get(f"{scope}:{scope_id}")

    def get_all_budgets(self) -> dict[str, dict[str, Any]]:
        with self.lock:
            return dict(self.budgets)

    def clear(self) -> None:
        with self.lock:
            self.records.clear()
            self.running_totals.clear()
            self.budgets.clear()

    def _apply_retention(self) -> None:
        if self.retention.max_age_days < 0:
            return
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.retention.max_age_days)).isoformat()
        pruned = [r for r in self.records if r.get("timestamp", "") > cutoff]
        if len(pruned) < len(self.records):
            len(self.records) - len(pruned)
            self.records = deque(pruned, maxlen=self.retention.max_records)
            self.running_totals.clear()
            for r in self.records:
                self._update_running_totals(r)
            if self.persist_path and self.retention.archive_on_trim:
                self._rewrite_disk()

    def _rewrite_disk(self) -> None:
        """Rewrite the on-disk JSONL to match current in-memory records (used after retention trim)."""
        try:
            if os.path.exists(self.persist_path):
                backup = self.persist_path + ".bak"
                os.replace(self.persist_path, backup)
            for r in self.records:
                self._append_to_disk(r)
        except OSError as e:
            logger.warning("Failed to rewrite disk after retention: %s", e)

    def compact(self) -> dict[str, Any]:
        with self.lock:
            before = len(self.records)
            self._apply_retention()
            after = len(self.records)
            return {"removed": before - after, "remaining": after}

    def get_record_count(self) -> int:
        with self.lock:
            return len(self.records)

    def verify_immutability(self) -> list[str]:
        tampered = []
        for r in self.get_records():
            expected = r.get("_checksum", "")
            if not expected:
                continue
            actual = _checksum({k: v for k, v in r.items() if k != "_checksum"})
            if expected != actual:
                tampered.append(r.get("record_id", "unknown"))
        return tampered

    async def async_insert_record(self, record: dict[str, Any]) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.insert_record, record)

    async def async_get_records(self) -> list[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.get_records)

    async def async_compact(self) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.compact)
