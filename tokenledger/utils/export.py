"""
Export utilities for TokenLedger.
"""

import csv
import json
from typing import Dict, Any, List


class ExportEngine:
    """
    Handles data export to CSV and JSON formats.
    """


    def __init__(self, store: Any):
        self.store = store

    def export_csv(self, filepath: str, records: List[Dict[str, Any]]) -> None:
        fieldnames = [
            "timestamp", "provider", "model", "user_id", "project_id",
            "input_tokens", "output_tokens", "total_tokens",
            "cost_usd", "latency_ms", "status", "source",
        ]
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for record in records:
                writer.writerow({k: record.get(k, "") for k in fieldnames})

    def export_json(self, filepath: str, records: List[Dict[str, Any]]) -> None:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2, default=str)
