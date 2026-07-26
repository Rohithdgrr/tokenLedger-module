"""
In-memory storage engine with ring buffer, retention policies,
immutable event logs, and optional JSONL file persistence.
"""

import asyncio
import hashlib
import json
import logging
import os
import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)


class RetentionPolicy:
    """Data retention configuration."""

    def __init__(
        self,
        max_age_days: int = 90,
        max_records: int = 100_000,
        archive_on_trim: bool = True,
    ):
        self.max_age_days = max_age_days
        self.max_records = max_records
        self.archive_on_trim = archive_on_trim


class MemoryStore:
    """Thread-safe in-memory store with ring buffer and retention."""

    def __init__(
        self,
        persist_path: Optional[str] = None,
        max_records: int = 100_000,
        retention_days: int = 90,
    ):
        self.records: Deque[Dict[str, Any]] = deque(maxlen=max_records)
        self.budgets: Dict[str, Dict[str, Any]] = {}
        self.running_totals: Dict[str, Dict[str, Any]] = {}
        self.lock = threading.RLock()
        self.persist_path = persist_path
        self.retention = RetentionPolicy(
            max_age_days=retention_days,
            max_records=max_records,
            archive_on_trim=True,
        )

        if persist_path and os.path.exists(persist_path):
            self._load_from_disk()

    def _checksum(self, record: Dict[str, Any]) -> str:
        """SHA-256 checksum of the record for immutability."""
        raw = json.dumps(record, sort_keys=True, default=str).encode()
        return hashlib.sha256(raw).hexdigest()

    def insert_record(self, record: Dict[str, Any]) -> None:
        """Thread-safe record insertion with aggregate updates."""
        with self.lock:
            record["_checksum"] = self._checksum(record)
            self.records.append(record)
            self._update_running_totals(record)
            self._apply_retention()

            if self.persist_path:
                self._append_to_disk(record)

    def _update_running_totals(self, record: Dict[str, Any]) -> None:
        """Maintain pre-computed aggregates for O(1) analytics."""
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

        for scope, scope_id in dimensions:
            key = f"{scope}:{scope_id}"
            if key not in self.running_totals:
                self.running_totals[key] = {
                    "requests": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "cost_usd": 0.0,
                }
            agg = self.running_totals[key]
            agg["requests"] += 1
            agg["input_tokens"] += record.get("input_tokens", 0)
            agg["output_tokens"] += record.get("output_tokens", 0)
            agg["total_tokens"] += record.get("total_tokens", 0)
            agg["cost_usd"] += record.get("cost_usd", 0.0)

    def _append_to_disk(self, record: Dict[str, Any]) -> None:
        """Append-only write with checksum for immutability."""
        try:
            line = json.dumps(record, default=str)
            checksum = hashlib.sha256(line.encode()).hexdigest()
            with open(self.persist_path, "a", encoding="utf-8") as f:
                f.write(f"{checksum}:{line}\n")
                f.flush()
                os.fsync(f.fileno())
        except (IOError, OSError) as e:
            logger.warning("Failed to persist record: %s", e)

    def _load_from_disk(self) -> None:
        """Load and verify records from disk."""
        try:
            with open(self.persist_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        if ":" in line:
                            stored_checksum, raw = line.split(":", 1)
                            # Backward compat: old JSONL without checksum
                            if len(stored_checksum) == 64:
                                actual = hashlib.sha256(raw.encode()).hexdigest()
                                if stored_checksum != actual:
                                    continue
                                record = json.loads(raw)
                            else:
                                record = json.loads(line)
                        else:
                            record = json.loads(line)
                        self.records.append(record)
                        self._update_running_totals(record)
                    except (json.JSONDecodeError, ValueError):
                        continue
        except (IOError, OSError):
            pass

    def get_records(self) -> List[Dict[str, Any]]:
        """Return all records (thread-safe copy)."""
        with self.lock:
            return list(self.records)

    def get_running_totals(self, scope: str, scope_id: str) -> Dict[str, Any]:
        """O(1) lookup of pre-computed aggregates."""
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

    def set_budget(self, scope: str, scope_id: str, budget_config: Dict[str, Any]) -> None:
        with self.lock:
            self.budgets[f"{scope}:{scope_id}"] = budget_config

    def get_budget(self, scope: str, scope_id: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            return self.budgets.get(f"{scope}:{scope_id}")

    def get_all_budgets(self) -> Dict[str, Dict[str, Any]]:
        with self.lock:
            return dict(self.budgets)

    def clear(self) -> None:
        with self.lock:
            self.records.clear()
            self.running_totals.clear()
            self.budgets.clear()

    def _apply_retention(self) -> None:
        """Apply age-based retention after each insert."""
        if self.retention.max_age_days < 0:
            return
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.retention.max_age_days)).isoformat()
        pruned = [r for r in self.records if r.get("timestamp", "") > cutoff]
        if len(pruned) < len(self.records):
            self.records = deque(pruned, maxlen=self.retention.max_records)
            self.running_totals.clear()
            for r in self.records:
                self._update_running_totals(r)

    def verify_immutability(self) -> List[str]:
        """Check all records for tampering. Returns list of tampered record_ids."""
        tampered = []
        for r in self.get_records():
            expected = r.get("_checksum", "")
            if not expected:
                continue
            actual = self._checksum({k: v for k, v in r.items() if k != "_checksum"})
            if expected != actual:
                tampered.append(r.get("record_id", "unknown"))
        return tampered

    def compact(self) -> Dict[str, Any]:
        """Force retention pruning + rebuild aggregates. Returns removal stats."""
        with self.lock:
            before = len(self.records)
            cutoff = (datetime.now(timezone.utc) - timedelta(days=self.retention.max_age_days)).isoformat()
            pruned = [r for r in self.records if r.get("timestamp", "") > cutoff]
            self.records = deque(pruned, maxlen=self.retention.max_records)
            self.running_totals.clear()
            for r in self.records:
                self._update_running_totals(r)
            return {"removed": before - len(self.records), "remaining": len(self.records)}

    def get_record_count(self) -> int:
        with self.lock:
            return len(self.records)

    async def async_insert_record(self, record: Dict[str, Any]) -> None:
        """Async wrapper for insert_record — runs in executor to avoid blocking."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.insert_record, record)

    async def async_get_records(self) -> List[Dict[str, Any]]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.get_records)

    async def async_compact(self) -> Dict[str, Any]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.compact)
