"""Export engine — CSV, JSON, and audit-ready exports."""

import csv
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from ..core.store import StorageBackend


class ExportEngine:
    """Handles export of records to various formats."""

    def __init__(self, store: StorageBackend):
        self.store = store

    def export_csv(self, filepath: str, records: list[dict[str, Any]]) -> None:
        keys = self._ordered_keys(records[0]) if records else ["record_id", "timestamp", "provider", "model",
                 "input_tokens", "output_tokens", "total_tokens", "cost_usd",
                 "latency_ms", "user_id", "project_id", "status", "source"]
        with open(filepath, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(records)

    def export_json(self, filepath: str, records: list[dict[str, Any]]) -> None:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2, default=str)

    def export_audit_json(self, filepath: str, records: list[dict[str, Any]]) -> None:
        """Export with verification checksums for audit trail."""
        audit = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "record_count": len(records),
            "records": records,
        }
        serialized = json.dumps(audit, sort_keys=True, default=str)
        audit["_checksum"] = hashlib.sha256(serialized.encode()).hexdigest()
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(audit, f, indent=2, default=str)

    def _ordered_keys(self, record: dict[str, Any]) -> list[str]:
        priority = ["record_id", "timestamp", "provider", "model", "input_tokens",
                     "output_tokens", "total_tokens", "cost_usd", "latency_ms",
                     "user_id", "project_id", "status", "source"]
        rest = sorted(k for k in record if k not in priority and not k.startswith("_"))
        return priority + rest
