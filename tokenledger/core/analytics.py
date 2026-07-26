"""
Analytics and aggregation engine.
Pure Python queries with O(1) lookups via running totals.
"""

from typing import Any, Dict, List, Optional

from .store import MemoryStore


class AnalyticsEngine:
    """
    Provides usage analytics and reporting.
    Uses pre-computed running totals for O(1) common queries.
    """

    def __init__(self, store: MemoryStore):
        self.store = store

    def get_summary(self, scope: str = "global", scope_id: str = "all") -> Dict[str, Any]:
        """Get summary statistics for a scope."""
        return self.store.get_running_totals(scope, scope_id)

    def get_spending_by_dimension(self, dimension: str) -> List[Dict[str, Any]]:
        """Get spending breakdown by a dimension."""
        results = []
        prefix = f"{dimension}:"

        all_totals = self.store.running_totals
        for key, agg in all_totals.items():
            if key.startswith(prefix):
                results.append({"id": key[len(prefix):], **agg})

        return sorted(results, key=lambda x: x.get("cost_usd", 0), reverse=True)

    def get_trend(self, dimension: str, dimension_id: str, granularity: str = "day") -> List[Dict[str, Any]]:
        """Get time-series trend data."""
        slice_lengths = {
            "hour": 13,
            "day": 10,
            "week": 10,
            "month": 7,
        }

        slice_len = slice_lengths.get(granularity, 10)
        buckets: Dict[str, Dict[str, Any]] = {}

        for record in self.store.get_records():
            if not self._matches_dimension(record, dimension, dimension_id):
                continue

            timestamp = record.get("timestamp", "")
            if len(timestamp) < slice_len:
                continue

            bucket_key = timestamp[:slice_len]

            if bucket_key not in buckets:
                buckets[bucket_key] = {
                    "period": bucket_key,
                    "requests": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "cost_usd": 0.0,
                }

            bucket = buckets[bucket_key]
            bucket["requests"] += 1
            bucket["input_tokens"] += record.get("input_tokens", 0)
            bucket["output_tokens"] += record.get("output_tokens", 0)
            bucket["total_tokens"] += record.get("total_tokens", 0)
            bucket["cost_usd"] += record.get("cost_usd", 0.0)

        return sorted(buckets.values(), key=lambda x: x["period"])

    def get_latency_stats(self, scope: str = "global", scope_id: str = "all") -> Dict[str, Any]:
        """Get latency statistics for a scope."""
        latencies = []

        for record in self.store.get_records():
            if self._matches_dimension(record, scope, scope_id):
                latency = record.get("latency_ms")
                if latency is not None and latency >= 0:
                    latencies.append(latency)

        if not latencies:
            return {"count": 0, "min": 0, "max": 0, "avg": 0, "p50": 0, "p95": 0, "p99": 0}

        latencies.sort()
        n = len(latencies)

        return {
            "count": n,
            "min": round(latencies[0], 2),
            "max": round(latencies[-1], 2),
            "avg": round(sum(latencies) / n, 2),
            "p50": round(latencies[int(n * 0.5)], 2),
            "p95": round(latencies[int(n * 0.95)] if n * 0.95 < n else latencies[-1], 2),
            "p99": round(latencies[int(n * 0.99)] if n * 0.99 < n else latencies[-1], 2),
        }

    def get_budget_utilization(self, scope: str, scope_id: str) -> Optional[Dict[str, Any]]:
        """Get current budget utilization percentage."""
        budget = self.store.get_budget(scope, scope_id)
        if not budget:
            return None

        total_spent = 0.0
        for record in self.store.get_records():
            if self._matches_budget_scope(record, budget):
                total_spent += record.get("cost_usd", 0)

        limit = budget.get("limit_usd", 0)
        utilization = (total_spent / limit * 100) if limit > 0 else 0

        return {
            "scope": scope,
            "scope_id": scope_id,
            "limit_usd": limit,
            "spent_usd": round(total_spent, 4),
            "remaining_usd": round(limit - total_spent, 4),
            "utilization_percent": round(utilization, 2),
            "reset_cycle": budget.get("reset_cycle", "monthly"),
        }

    def _matches_dimension(self, record: Dict[str, Any], dimension: str, dimension_id: str) -> bool:
        """Check if a record matches a dimension filter."""
        if dimension == "global":
            return True
        if dimension == "provider":
            return record.get("provider") == dimension_id
        if dimension == "model":
            return record.get("model") == dimension_id
        if dimension == "user":
            return record.get("user_id", "anonymous") == dimension_id
        if dimension == "project":
            return record.get("project_id", "default") == dimension_id
        return False

    def _matches_budget_scope(self, record: Dict[str, Any], budget: Dict[str, Any]) -> bool:
        """Check if a record matches a budget's scope."""
        scope = budget.get("scope", "global")
        scope_id = budget.get("scope_id", "")

        if scope == "global":
            return True
        if scope == "project":
            return record.get("project_id", "default") == scope_id
        if scope == "user":
            return record.get("user_id", "anonymous") == scope_id
        if scope == "user_project":
            parts = scope_id.split(":")
            if len(parts) == 2:
                return record.get("user_id") == parts[0] and record.get("project_id") == parts[1]
        return False
