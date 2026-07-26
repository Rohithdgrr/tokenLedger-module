"""
Multi-layer verification engine for data integrity.
Validates token arithmetic, cost calculations, and anomaly detection.
"""

from datetime import datetime, timezone
from typing import Any, Dict

from .pricing import PricingRegistry
from .store import MemoryStore


class VerificationEngine:
    """Ensures data integrity before storage through independent checks."""

    def __init__(self, pricing_registry: PricingRegistry, store: MemoryStore):
        self.pricing = pricing_registry
        self.store = store
        self.anomaly_threshold = 3.0

    def verify(self, record: Dict[str, Any], raw_response: Any = None) -> Dict[str, Any]:
        """Run verification pipeline on a record."""
        flags = []

        expected_total = record.get("input_tokens", 0) + record.get("output_tokens", 0)
        if record.get("total_tokens", 0) != expected_total:
            flags.append("TOKEN_ARITHMETIC_MISMATCH")
            record["total_tokens"] = expected_total

        if record.get("input_tokens", 0) < 0 or record.get("output_tokens", 0) < 0:
            flags.append("NEGATIVE_TOKEN_COUNT")
            raise ValueError(
                f"Impossible token count: input={record.get('input_tokens')}, output={record.get('output_tokens')}"
            )

        expected_cost = self._calculate_expected_cost(record)
        if abs(record.get("cost_usd", 0) - expected_cost) > 0.0001:
            flags.append("COST_CALCULATION_MISMATCH")
            record["cost_usd"] = expected_cost

        if not self.pricing.has_model(record.get("provider", ""), record.get("model", "")):
            if record.get("provider") != "custom":
                flags.append("UNKNOWN_MODEL")

        if record.get("latency_ms", 0) < 0:
            flags.append("NEGATIVE_LATENCY")
            record["latency_ms"] = 0

        if self._is_anomalous(record):
            flags.append("ANOMALOUS_USAGE_PATTERN")

        record["verification"] = {
            "tokens_verified": "TOKEN_ARITHMETIC_MISMATCH" not in flags,
            "cost_verified": "COST_CALCULATION_MISMATCH" not in flags,
            "estimation_used": record.get("source") == "estimated",
            "anomaly_flags": flags,
            "verification_timestamp": datetime.now(timezone.utc).isoformat(),
        }

        return record

    def _calculate_expected_cost(self, record: Dict[str, Any]) -> float:
        """Recalculate expected cost from pricing registry."""
        return self.pricing.calculate_cost(
            record.get("provider", "unknown"),
            record.get("model", "unknown"),
            record.get("input_tokens", 0),
            record.get("output_tokens", 0),
        )

    def _is_anomalous(self, record: Dict[str, Any]) -> bool:
        """Detect anomalous usage patterns."""
        user_id = record.get("user_id", "anonymous")
        user_key = f"user:{user_id}"

        user_totals = self.store.running_totals.get(user_key)
        if not user_totals or user_totals.get("requests", 0) < 10:
            return False

        avg_cost = user_totals["cost_usd"] / user_totals["requests"]
        current_cost = record.get("cost_usd", 0)

        if avg_cost <= 0:
            return False

        return current_cost > (avg_cost * self.anomaly_threshold)
