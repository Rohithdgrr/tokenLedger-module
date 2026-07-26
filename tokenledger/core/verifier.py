"""Multi-layer verification engine with pluggable rules."""

import abc
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .pricing import PricingRegistry
from .store import StorageBackend


class VerificationRule(abc.ABC):
    """Base class for custom verification rules."""

    @abc.abstractmethod
    def check(self, record: Dict[str, Any], store: StorageBackend, pricing: PricingRegistry) -> Optional[str]:
        """Return a flag string if the record fails this rule, or None."""
        ...


class TokenArithmeticRule(VerificationRule):
    def check(self, record: Dict[str, Any], store: StorageBackend, pricing: PricingRegistry) -> Optional[str]:
        expected = record.get("input_tokens", 0) + record.get("output_tokens", 0)
        if record.get("total_tokens", 0) != expected:
            record["total_tokens"] = expected
            return "TOKEN_ARITHMETIC_MISMATCH"
        return None


class NegativeTokenRule(VerificationRule):
    def check(self, record: Dict[str, Any], store: StorageBackend, pricing: PricingRegistry) -> Optional[str]:
        if record.get("input_tokens", 0) < 0 or record.get("output_tokens", 0) < 0:
            raise ValueError(f"Impossible token count: input={record.get('input_tokens')}, output={record.get('output_tokens')}")
        return None


class CostRecalculationRule(VerificationRule):
    def check(self, record: Dict[str, Any], store: StorageBackend, pricing: PricingRegistry) -> Optional[str]:
        expected = pricing.calculate_cost(
            record.get("provider", "unknown"), record.get("model", "unknown"),
            record.get("input_tokens", 0), record.get("output_tokens", 0),
        )
        if abs(record.get("cost_usd", 0) - expected) > 0.0001:
            record["cost_usd"] = expected
            return "COST_CALCULATION_MISMATCH"
        return None


class UnknownModelRule(VerificationRule):
    def check(self, record: Dict[str, Any], store: StorageBackend, pricing: PricingRegistry) -> Optional[str]:
        if not pricing.has_model(record.get("provider", ""), record.get("model", "")):
            if record.get("provider") != "custom":
                return "UNKNOWN_MODEL"
        return None


class NegativeLatencyRule(VerificationRule):
    def check(self, record: Dict[str, Any], store: StorageBackend, pricing: PricingRegistry) -> Optional[str]:
        if record.get("latency_ms", 0) < 0:
            record["latency_ms"] = 0
            return "NEGATIVE_LATENCY"
        return None


class AnomalyDetectionRule(VerificationRule):
    def __init__(self, threshold: float = 3.0):
        self.threshold = threshold

    def check(self, record: Dict[str, Any], store: StorageBackend, pricing: PricingRegistry) -> Optional[str]:
        user_id = record.get("user_id", "anonymous")
        user_totals = store.get_running_totals("user", user_id)
        if user_totals.get("requests", 0) < 10:
            return None
        avg_cost = user_totals["cost_usd"] / user_totals["requests"]
        current_cost = record.get("cost_usd", 0)
        if avg_cost > 0 and current_cost > (avg_cost * self.threshold):
            return "ANOMALOUS_USAGE_PATTERN"
        return None


class VerificationEngine:
    """Pluggable verification pipeline with default rules."""

    def __init__(self, pricing_registry: PricingRegistry, store: StorageBackend,
                 custom_rules: Optional[List[VerificationRule]] = None):
        self.pricing = pricing_registry
        self.store = store
        self.rules: List[VerificationRule] = custom_rules or [
            TokenArithmeticRule(),
            NegativeTokenRule(),
            CostRecalculationRule(),
            UnknownModelRule(),
            NegativeLatencyRule(),
            AnomalyDetectionRule(),
        ]

    def add_rule(self, rule: VerificationRule) -> None:
        self.rules.append(rule)

    def verify(self, record: Dict[str, Any], raw_response: Any = None) -> Dict[str, Any]:
        flags = []
        for rule in self.rules:
            try:
                flag = rule.check(record, self.store, self.pricing)
                if flag:
                    flags.append(flag)
            except ValueError:
                raise
            except Exception as e:
                flags.append(f"RULE_ERROR:{e}")
        record["verification"] = {
            "tokens_verified": "TOKEN_ARITHMETIC_MISMATCH" not in flags,
            "cost_verified": "COST_CALCULATION_MISMATCH" not in flags,
            "estimation_used": record.get("source") == "estimated",
            "anomaly_flags": flags,
            "verification_timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return record
