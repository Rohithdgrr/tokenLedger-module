"""
Analytics and aggregation engine.
Pure Python queries with O(1) lookups via running totals.
"""

from collections import Counter
from typing import Any, Optional

from .store import StorageBackend


class AnalyticsEngine:
    """
    Provides usage analytics and reporting.
    Uses pre-computed running totals for O(1) common queries.
    """

    def __init__(self, store: StorageBackend):
        self.store = store

    def get_summary(
        self,
        scope: str = "global",
        scope_id: str = "all",
        top_k: int = 5,
    ) -> dict[str, Any]:
        """Get summary statistics with budget utilization, top models, and anomalies.

        Top models/providers are read from the store's running totals (O(1),
        no record scan) so ghost/blocked records never leak into rankings.
        """
        base = self.store.get_running_totals(scope, scope_id)
        budgets = self.store.get_all_budgets()

        base["budget_count"] = len(budgets)

        budget_utilization = {}
        for bk, b in budgets.items():
            spend = self.store.get_running_totals(b.get('scope', 'global'), b.get('scope_id', 'all'))
            limit = b.get("limit_usd", 0)
            budget_utilization[bk] = {
                "spend": round(spend.get("cost_usd", 0), 6),
                "limit": limit,
                "utilization_pct": round(spend.get("cost_usd", 0) / limit * 100, 2) if limit else 0,
            }
        base["budget_utilization"] = budget_utilization

        running = self.store.running_totals
        model_aggs = [
            {"model": key[len("model:"):], **agg}
            for key, agg in running.items()
            if key.startswith("model:")
        ]
        provider_aggs = [
            {"provider": key[len("provider:"):], **agg}
            for key, agg in running.items()
            if key.startswith("provider:")
        ]
        base["top_models"] = [
            {"model": m["model"], "tokens": m["total_tokens"]}
            for m in sorted(model_aggs, key=lambda x: -x["total_tokens"])[:top_k]
        ]
        base["top_providers"] = [
            {"provider": p["provider"], "tokens": p["total_tokens"]}
            for p in sorted(provider_aggs, key=lambda x: -x["total_tokens"])[:top_k]
        ]

        status_counts: Counter = Counter()
        for r in self.store.get_records():
            status_counts[r.get("status", "success")] += 1
        base["status_breakdown"] = dict(status_counts)

        status_counts.pop("success", None)
        base["anomalies"] = {"non_success_count": sum(status_counts.values())}

        return base

    def get_spending_by_dimension(self, dimension: str) -> list[dict[str, Any]]:
        """Get spending breakdown by a dimension."""
        results = []
        prefix = f"{dimension}:"

        all_totals = self.store.running_totals
        for key, agg in all_totals.items():
            if key.startswith(prefix):
                results.append({"id": key[len(prefix):], **agg})

        return sorted(results, key=lambda x: x.get("cost_usd", 0), reverse=True)

    def get_trend(self, dimension: str, dimension_id: str, granularity: str = "day") -> list[dict[str, Any]]:
        """Get time-series trend data."""
        slice_lengths = {
            "hour": 13,
            "day": 10,
            "week": 10,
            "month": 7,
        }

        slice_len = slice_lengths.get(granularity, 10)
        buckets: dict[str, dict[str, Any]] = {}

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

    def get_latency_stats(self, scope: str = "global", scope_id: str = "all") -> dict[str, Any]:
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

    def get_budget_utilization(self, scope: str, scope_id: str) -> Optional[dict[str, Any]]:
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

    def get_efficiency_stats(self, scope: str = "global", scope_id: str = "all") -> dict[str, Any]:
        """Token efficiency metrics (output/input ratio, cache hit rate)."""
        records = [r for r in self.store.get_records() if self._matches_dimension(r, scope, scope_id)]
        if not records:
            return {"avg_efficiency": 0, "cache_hit_rate": 0, "total_reasoning_tokens": 0}

        ratios = []
        cache_hits = 0
        total_reasoning = 0
        for r in records:
            inp = r.get("input_tokens", 0) or 1
            ratios.append(r.get("output_tokens", 0) / inp)
            if r.get("cache_hit"):
                cache_hits += 1
            total_reasoning += r.get("reasoning_tokens", 0)

        ratios.sort()
        n = len(ratios)
        return {
            "avg_efficiency": round(sum(ratios) / n, 4),
            "p50_efficiency": round(ratios[n // 2], 4),
            "cache_hit_rate": round(cache_hits / n, 4),
            "total_reasoning_tokens": total_reasoning,
        }

    def get_cost_breakdown(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        """Breakdown of costs by category (completion, cache, embedding, etc.)."""
        total = {"completion": 0.0, "cached": 0.0, "embedding": 0.0, "tool_calls": 0.0, "media": 0.0}
        for r in records:
            cost = r.get("cost_usd", 0)
            if r.get("embedding"):
                total["embedding"] += cost
            elif r.get("cache_hit"):
                total["cached"] += cost * 0.5
            else:
                total["completion"] += cost
        return total

    def _matches_dimension(self, record: dict[str, Any], dimension: str, dimension_id: str) -> bool:
        """Check if a record matches a dimension filter."""
        if dimension == "global":
            return True
        if dimension == "provider":
            return bool(record.get("provider") == dimension_id)
        if dimension == "model":
            return bool(record.get("model") == dimension_id)
        if dimension == "user":
            return bool(record.get("user_id", "anonymous") == dimension_id)
        if dimension == "project":
            return bool(record.get("project_id", "default") == dimension_id)
        if dimension == "conversation":
            return bool(record.get("conversation_id") == dimension_id)
        if dimension == "agent":
            return bool(record.get("agent_id") == dimension_id)
        if dimension == "tenant":
            return bool(record.get("tenant_id") == dimension_id)
        return False

    def _matches_budget_scope(self, record: dict[str, Any], budget: dict[str, Any]) -> bool:
        """Check if a record matches a budget's scope."""
        scope = budget.get("scope", "global")
        scope_id = budget.get("scope_id", "")

        if scope == "global":
            return True
        if scope == "provider":
            return bool(record.get("provider") == scope_id)
        if scope == "model":
            return bool(record.get("model") == scope_id)
        if scope == "project":
            return bool(record.get("project_id", "default") == scope_id)
        if scope == "user":
            return bool(record.get("user_id", "anonymous") == scope_id)
        if scope == "tenant":
            return bool(record.get("tenant_id") == scope_id)
        if scope == "user_project":
            parts = scope_id.split(":")
            if len(parts) == 2:
                return bool(record.get("user_id") == parts[0] and record.get("project_id") == parts[1])
        return False
