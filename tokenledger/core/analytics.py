"""
Analytics and aggregation engine.
Pure Python queries with O(1) lookups via running totals.
"""

import logging
from collections import Counter
from typing import Any, Optional

from .scopes import matches_scope
from .store import StorageBackend

logger = logging.getLogger(__name__)


class AnalyticsEngine:
    """
    Provides usage analytics and reporting.
    Uses pre-computed running totals for O(1) common queries.
    """

    def __init__(self, store: StorageBackend):
        self.store = store

    def __repr__(self) -> str:
        return f"<AnalyticsEngine records={self.store.get_record_count()}>"

    def get_summary(
        self,
        scope: str = "global",
        scope_id: str = "all",
        top_k: int = 5,
        apply_dp: bool = False,
        epsilon: float | None = None,
    ) -> dict[str, Any]:
        """Get summary statistics with budget utilization, top models, and anomalies.

        Top models/providers are read from the store's running totals (O(1),
        no record scan) so ghost/blocked records never leak into rankings.

        When ``apply_dp`` is True, Laplace noise is added to aggregate
        counts (scale=1/epsilon) rather than per-record, avoiding noise
        compounding.
        """
        import math
        import secrets

        def _laplace(scale: float) -> float:
            u = 1e-12 + secrets.randbelow(2**53) / 2**53 * (1.0 - 2e-12)
            return scale * math.log(2 * u) if u < 0.5 else -scale * math.log(2 * (1 - u))

        base = self.store.get_running_totals(scope, scope_id)
        if apply_dp:
            eps = epsilon or 1.0
            scale = 1.0 / eps
            # Add noise to aggregates (not per-record)
            for k in ("requests", "input_tokens", "output_tokens", "total_tokens"):
                base[k] = max(0, int(base.get(k, 0) + _laplace(scale)))
            base["cost_usd"] = max(0.0, float(base.get("cost_usd", 0) + _laplace(scale * 0.001)))
            base["_dp_noise_applied"] = True
        budgets = self.store.get_all_budgets()

        base["budget_count"] = len(budgets)

        budget_utilization = {}
        for bk, b in budgets.items():
            spend = self._budget_spend(b)
            limit = b.get("limit_usd", 0)
            budget_utilization[bk] = {
                "spend": round(spend, 6),
                "limit": limit,
                "utilization_pct": round(spend / limit * 100, 2) if limit else 0,
            }
        base["budget_utilization"] = budget_utilization

        model_aggs = [{"model": key[len("model:") :], **agg} for key, agg in self.store.list_running_totals("model:")]
        provider_aggs = [{"provider": key[len("provider:") :], **agg} for key, agg in self.store.list_running_totals("provider:")]
        base["top_models"] = [
            {"model": m["model"], "tokens": m["total_tokens"]} for m in sorted(model_aggs, key=lambda x: -x["total_tokens"])[:top_k]
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
        prefix = f"{dimension}:"
        all_totals = self.store.list_running_totals(prefix)
        results = [{"id": key[len(prefix) :], **agg} for key, agg in all_totals]
        return sorted(results, key=lambda x: x.get("cost_usd", 0), reverse=True)

    def get_trend(self, dimension: str, dimension_id: str, granularity: str = "day") -> list[dict[str, Any]]:
        """Get time-series trend data."""
        from datetime import datetime

        buckets: dict[str, dict[str, Any]] = {}

        for record in self.store.get_records():
            if not self._matches_dimension(record, dimension, dimension_id):
                continue

            timestamp = record.get("timestamp", "")
            try:
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue

            if granularity == "hour":
                bucket_key = dt.strftime("%Y-%m-%dT%H")
            elif granularity == "day":
                bucket_key = dt.strftime("%Y-%m-%d")
            elif granularity == "week":
                bucket_key = dt.strftime("%Y-W%W")
            elif granularity == "month":
                bucket_key = dt.strftime("%Y-%m")
            else:
                bucket_key = dt.strftime("%Y-%m-%d")

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

        def _percentile(sorted_vals: list[float], p: float) -> float:
            if not sorted_vals:
                return 0.0
            import math

            k = (len(sorted_vals) - 1) * p / 100
            f = math.floor(k)
            c = math.ceil(k)
            if f == c:
                return float(sorted_vals[int(k)])
            return float(sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f))

        return {
            "count": n,
            "min": round(latencies[0], 2),
            "max": round(latencies[-1], 2),
            "avg": round(sum(latencies) / n, 2),
            "p50": round(_percentile(latencies, 50), 2),
            "p95": round(_percentile(latencies, 95), 2),
            "p99": round(_percentile(latencies, 99), 2),
        }

    def _budget_spend(self, budget: dict[str, Any]) -> float:
        """Spend inside a budget's reset window (window-aware utilization).

        ``never`` budgets use running totals (O(1)); daily/weekly/monthly
        budgets use the store's indexed windowed spend when available.
        """
        from datetime import datetime, timedelta, timezone

        from .store import normalize_ts

        scope = budget.get("scope", "global")
        scope_id = budget.get("scope_id", "all")
        reset_cycle = budget.get("reset_cycle", "monthly")
        if reset_cycle == "never":
            totals = self.store.get_running_totals(scope, scope_id)
            return round(float(totals.get("cost_usd", 0)), 6)
        now = datetime.now(timezone.utc)
        if reset_cycle == "daily":
            window_start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif reset_cycle == "weekly":
            window_start_dt = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            window_start_dt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        window_start: str = window_start_dt.isoformat()
        if hasattr(self.store, "get_windowed_spend"):
            try:
                optimized = self.store.get_windowed_spend(budget, window_start)
                if optimized is not None:
                    return round(float(optimized), 6)
            except Exception as e:
                logger.debug("windowed spend query failed, falling back to scan: %s", e)
        ws_norm = normalize_ts(window_start)
        total = 0.0
        for record in self.store.get_records():
            ts = record.get("timestamp", "")
            if ts and normalize_ts(ts) < ws_norm:
                continue
            if record.get("status") in ("blocked", "error") or record.get("_ghost"):
                continue
            if matches_scope(record, scope, scope_id):
                total += float(record.get("cost_usd", 0) or 0)
        return round(total, 6)

    def get_budget_utilization(self, scope: str, scope_id: str) -> Optional[dict[str, Any]]:
        """Get current budget utilization percentage (window-aware)."""
        budget = self.store.get_budget(scope, scope_id)
        if not budget:
            return None

        total_spent = self._budget_spend(budget)
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
        return matches_scope(record, dimension, dimension_id)

    def _matches_budget_scope(self, record: dict[str, Any], budget: dict[str, Any]) -> bool:
        """Check if a record matches a budget's scope."""
        return matches_scope(record, budget.get("scope", "global"), budget.get("scope_id", ""))
