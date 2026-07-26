"""
In-memory storage engine with optional JSONL file persistence.
No database required.
"""

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional


class MemoryStore:
    """
    Primary data container for TokenLedger.
    All state is held in Python native data structures.
    """

    def __init__(self, persist_path: Optional[str] = None):
        self.records: List[Dict[str, Any]] = []
        self.budgets: Dict[str, Dict[str, Any]] = {}
        self.running_totals: Dict[str, Dict[str, Any]] = {}
        self.lock = threading.RLock()
        self.persist_path = persist_path

        if persist_path and os.path.exists(persist_path):
            self._load_from_disk()

    def insert_record(self, record: Dict[str, Any]) -> None:
        """Thread-safe record insertion with aggregate updates."""
        with self.lock:
            self.records.append(record)
            self._update_running_totals(record)

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
        """Append-only write to JSONL file."""
        try:
            with open(self.persist_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
                f.flush()
                os.fsync(f.fileno())
        except (IOError, OSError) as e:
            print(f"Warning: Failed to persist record: {e}")

    def _load_from_disk(self) -> None:
        """Load records from JSONL on startup."""
        try:
            with open(self.persist_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            record = json.loads(line)
                            self.records.append(record)
                            self._update_running_totals(record)
                        except json.JSONDecodeError:
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
        """Store a budget rule."""
        with self.lock:
            self.budgets[f"{scope}:{scope_id}"] = budget_config

    def get_budget(self, scope: str, scope_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a budget rule."""
        with self.lock:
            return self.budgets.get(f"{scope}:{scope_id}")

    def get_all_budgets(self) -> Dict[str, Dict[str, Any]]:
        """Return all budget rules."""
        with self.lock:
            return dict(self.budgets)

    def clear(self) -> None:
        """Clear all in-memory data."""
        with self.lock:
            self.records.clear()
            self.running_totals.clear()
            self.budgets.clear()

    def rotate_if_needed(self, max_records: int = 100000) -> None:
        """Archive old records when memory limit is reached."""
        with self.lock:
            if len(self.records) > max_records:
                cutoff = len(self.records) - max_records
                archived = self.records[:cutoff]
                self.records = self.records[cutoff:]

                archive_path = (
                    self.persist_path.replace(".jsonl", "_archive.jsonl")
                    if self.persist_path
                    else "archive.jsonl"
                )
                try:
                    with open(archive_path, "a", encoding="utf-8") as f:
                        for record in archived:
                            f.write(json.dumps(record, default=str) + "\n")
                except (IOError, OSError):
                    pass

                self.running_totals.clear()
                for record in self.records:
                    self._update_running_totals(record)

    def apply_retention(self, max_age_days: int = 90) -> None:
        """Remove records older than max_age_days."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()

        with self.lock:
            self.records = [r for r in self.records if r.get("timestamp", "") >= cutoff]
            self.running_totals.clear()
            for record in self.records:
                self._update_running_totals(record)
