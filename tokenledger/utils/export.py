"""
Export utilities for TokenLedger.
"""

import csv
import json
from typing import Any, Dict, List


class ExportEngine:
    """Handles data export to CSV and JSON formats."""

    BASE_FIELDS = [
        "timestamp", "provider", "model", "user_id", "project_id",
        "input_tokens", "output_tokens", "total_tokens",
        "cost_usd", "latency_ms", "status", "source",
    ]
    EXTRA_FIELDS = [
        "conversation_id", "agent_id", "prompt_hash",
        "reasoning_tokens", "cached_input_tokens", "embedding_tokens",
        "tool_call_count", "media_type", "cache_hit",
    ]

    def __init__(self, store: Any):
        self.store = store

    def export_csv(self, filepath: str, records: List[Dict[str, Any]]) -> None:
        fieldnames = self.BASE_FIELDS + self.EXTRA_FIELDS
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for record in records:
                writer.writerow(record)

    def export_json(self, filepath: str, records: List[Dict[str, Any]]) -> None:
        cleaned = []
        for r in records:
            entry = {k: v for k, v in r.items() if not k.startswith("_")}
            cleaned.append(entry)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(cleaned, f, indent=2, default=str)
