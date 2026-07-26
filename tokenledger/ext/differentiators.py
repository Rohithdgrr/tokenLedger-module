"""Differentiating features: what-if, ROI, routing, contracts, evolution, local cost."""
from __future__ import annotations

import difflib
import hashlib
import hmac
import json
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


def simulate_cost(pricing_registry, provider: str, model: str, input_tokens: int = 0, output_tokens: int = 0, messages: Optional[List[Dict]] = None) -> Dict[str, Any]:
    if messages and not input_tokens:
        input_tokens = sum(len(m.get("content", "")) // 4 for m in messages)
    cost = pricing_registry.calculate_cost(provider, model, input_tokens, output_tokens)
    return {"provider": provider, "model": model, "input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": input_tokens + output_tokens, "estimated_cost_usd": round(cost, 6)}

def compute_roi(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    total_cost = sum(r.get("cost_usd", 0) for r in records)
    total_input = sum(r.get("input_tokens", 0) for r in records)
    total_output = sum(r.get("output_tokens", 0) for r in records)
    count = len(records)
    return {"total_requests": count, "total_cost_usd": round(total_cost, 6), "total_input_tokens": total_input, "total_output_tokens": total_output, "cost_per_output_token": round(total_cost / total_output, 10) if total_output else 0, "cost_per_request": round(total_cost / count, 6) if count else 0, "output_input_ratio": round(total_output / total_input, 4) if total_input else 0}

def sign_ledger(records: List[Dict], key: str) -> Dict[str, Any]:
    payload = json.dumps(records, sort_keys=True, default=str).encode()
    signature = hmac.new(key.encode(), payload, hashlib.sha256).hexdigest()
    return {"record_count": len(records), "records": records, "signature": signature, "algorithm": "hmac-sha256", "signed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

def verify_signed_ledger(bundle: Dict[str, Any], key: str) -> bool:
    payload = json.dumps(bundle["records"], sort_keys=True, default=str).encode()
    expected = hmac.new(key.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, bundle.get("signature", ""))

class PromptCache:
    def __init__(self, similarity_threshold: float = 0.95):
        self._cache: Dict[str, str] = {}
        self._threshold = similarity_threshold

    def put(self, key: str, content: str) -> None:
        self._cache[key] = content

    def get(self, key: str) -> Optional[str]:
        return self._cache.get(key)

    def find_similar(self, content: str, threshold: Optional[float] = None) -> List[tuple[str, float]]:
        t = threshold or self._threshold
        results = [(k, round(difflib.SequenceMatcher(None, content, v).ratio(), 4)) for k, v in self._cache.items() if difflib.SequenceMatcher(None, content, v).ratio() >= t]
        return sorted(results, key=lambda x: -x[1])

class EstimatorFeedback:
    def __init__(self):
        self._stats: Dict[str, Dict[str, float]] = {}

    def report(self, model: str, provider: str, estimated_tokens: int, actual_tokens: int) -> None:
        key = f"{provider}:{model}"
        s = self._stats.setdefault(key, {"count": 0, "total_error": 0.0, "total_estimated": 0, "total_actual": 0})
        s["count"] += 1
        s["total_error"] += abs(estimated_tokens - actual_tokens) / max(actual_tokens, 1)
        s["total_estimated"] += estimated_tokens
        s["total_actual"] += actual_tokens

    def get_accuracy(self, model: str, provider: str) -> Dict[str, Any]:
        s = self._stats.get(f"{provider}:{model}")
        if not s or s["count"] == 0:
            return {"count": 0, "avg_error_pct": 0, "correction_factor": 1.0}
        return {"count": s["count"], "avg_error_pct": round(s["total_error"] / s["count"] * 100, 2), "correction_factor": round(s["total_actual"] / s["total_estimated"], 4) if s["total_estimated"] else 1.0}

    def adjust(self, model: str, provider: str, estimated_tokens: int) -> int:
        info = self.get_accuracy(model, provider)
        return int(estimated_tokens * info["correction_factor"]) if info["count"] > 5 else estimated_tokens

@dataclass
class RouteOption:
    provider: str; model: str; input_cost_per_1k: float; output_cost_per_1k: float
    max_tokens: Optional[int] = None; latency_p95_ms: Optional[float] = None

class ModelRouter:
    def __init__(self, options: Optional[List[RouteOption]] = None):
        self.options: List[RouteOption] = list(options or [])

    def add_option(self, opt: RouteOption) -> None:
        self.options.append(opt)

    def route(self, input_tokens: int = 0, output_tokens: int = 0, max_cost: Optional[float] = None, prefer_latency: bool = False) -> Optional[RouteOption]:
        candidates = list(self.options)
        if max_cost is not None:
            candidates = [o for o in candidates if (o.input_cost_per_1k * input_tokens / 1000 + o.output_cost_per_1k * output_tokens / 1000) <= max_cost]
        if not candidates:
            return None
        key = lambda o: o.latency_p95_ms if (prefer_latency and o.latency_p95_ms is not None) else (o.input_cost_per_1k * input_tokens + o.output_cost_per_1k * output_tokens)
        return min(candidates, key=key)

@dataclass
class CostContract:
    name: str; max_cost_usd: float; scope: str = "global"; scope_id: str = "all"
    current_spend: float = 0.0; callback: Optional[Callable] = None

class CostContractRegistry:
    def __init__(self):
        self._contracts: Dict[str, CostContract] = {}

    def add(self, contract: CostContract) -> None:
        self._contracts[contract.name] = contract

    def get(self, name: str) -> Optional[CostContract]:
        return self._contracts.get(name)

    def check(self, name: str, cost_usd: float) -> bool:
        contract = self._contracts.get(name)
        if not contract:
            return True
        contract.current_spend += cost_usd
        if contract.current_spend > contract.max_cost_usd:
            if contract.callback:
                contract.callback(contract)
            return False
        return True

    def reset(self, name: str) -> None:
        c = self._contracts.get(name)
        if c:
            c.current_spend = 0.0

    def all(self) -> List[CostContract]:
        return list(self._contracts.values())

class PromptEvolutionTracker:
    def __init__(self):
        self._versions: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    def track(self, name: str, content: str, metadata: Optional[Dict] = None) -> Dict[str, Any]:
        history = self._versions[name]
        version_num = len(history) + 1
        entry = {"name": name, "version": version_num, "content": content, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "metadata": metadata or {}}
        if history:
            prev = history[-1]["content"]
            entry["diff"] = list(difflib.unified_diff(prev.splitlines(), content.splitlines(), lineterm=""))
        history.append(entry)
        return entry

    def get_history(self, name: str) -> List[Dict[str, Any]]:
        return list(self._versions.get(name, []))

    def get_latest(self, name: str) -> Optional[str]:
        h = self._versions.get(name)
        return h[-1]["content"] if h else None

@dataclass
class LocalModelCost:
    name: str; watts_per_second: float = 10.0; cost_per_kwh: float = 0.12
    tokens_per_second: float = 30.0; hardware_cost: float = 0.0

    def cost_per_token(self) -> float:
        energy_per_token = self.watts_per_second / self.tokens_per_second
        return (energy_per_token / 3_600_000) * self.cost_per_kwh

    def cost_for_tokens(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens + output_tokens) * self.cost_per_token()

class LocalModelRegistry:
    def __init__(self):
        self._models: Dict[str, LocalModelCost] = {}

    def register(self, model: LocalModelCost) -> None:
        self._models[model.name] = model

    def get(self, name: str) -> Optional[LocalModelCost]:
        return self._models.get(name)

    def estimate_cost(self, name: str, input_tokens: int, output_tokens: int) -> Optional[float]:
        m = self._models.get(name)
        return m.cost_for_tokens(input_tokens, output_tokens) if m else None

    def list_models(self) -> List[str]:
        return list(self._models.keys())
