"""
In-memory pricing registry for LLM providers.
Loads from external JSON file with fallback to builtin data.
"""

import json
import os
from typing import Dict, Optional


_BUILTIN = {
    "openai": {
        "gpt-4o": {"input_per_1k": 0.005, "output_per_1k": 0.015},
        "gpt-4o-mini": {"input_per_1k": 0.00015, "output_per_1k": 0.0006},
        "gpt-4-turbo": {"input_per_1k": 0.01, "output_per_1k": 0.03},
        "gpt-3.5-turbo": {"input_per_1k": 0.0005, "output_per_1k": 0.0015},
    },
    "anthropic": {
        "claude-3-5-sonnet-20241022": {"input_per_1k": 0.003, "output_per_1k": 0.015},
        "claude-3-opus-20240229": {"input_per_1k": 0.015, "output_per_1k": 0.075},
        "claude-3-haiku-20240307": {"input_per_1k": 0.00025, "output_per_1k": 0.00125},
    },
    "google": {
        "gemini-1.5-pro": {"input_per_1k": 0.0035, "output_per_1k": 0.0105},
        "gemini-1.5-flash": {"input_per_1k": 0.00035, "output_per_1k": 0.00105},
    },
    "groq": {
        "llama-3.1-70b": {"input_per_1k": 0.00059, "output_per_1k": 0.00079},
        "llama-3.1-8b": {"input_per_1k": 0.00005, "output_per_1k": 0.00008},
        "mixtral-8x7b": {"input_per_1k": 0.00024, "output_per_1k": 0.00024},
    },
    "openrouter": {
        "openai/gpt-4o": {"input_per_1k": 0.005, "output_per_1k": 0.015},
    },
    "deepseek": {
        "deepseek-chat": {"input_per_1k": 0.00014, "output_per_1k": 0.00028},
        "deepseek-coder": {"input_per_1k": 0.00014, "output_per_1k": 0.00028},
    },
    "mistral": {
        "mistral-large": {"input_per_1k": 2.0, "output_per_1k": 6.0},
        "mistral-small": {"input_per_1k": 0.6, "output_per_1k": 1.8},
        "open-mistral-nemo": {"input_per_1k": 0.3, "output_per_1k": 0.3},
    },
    "cohere": {
        "command-r-plus": {"input_per_1k": 3.0, "output_per_1k": 15.0},
        "command-r": {"input_per_1k": 0.5, "output_per_1k": 1.0},
    },
    "nvidia": {
        "llama-3.1-nemotron": {"input_per_1k": 0.2, "output_per_1k": 0.2},
        "mixtral-8x22b": {"input_per_1k": 0.9, "output_per_1k": 0.9},
    },
    "kimi": {
        "moonshot-v1-8k": {"input_per_1k": 1.0, "output_per_1k": 2.0},
        "moonshot-v1-32k": {"input_per_1k": 2.0, "output_per_1k": 4.0},
    },
    "glm": {
        "glm-4": {"input_per_1k": 0.5, "output_per_1k": 0.5},
        "glm-4v": {"input_per_1k": 0.5, "output_per_1k": 0.5},
    },
    "minimax": {
        "minimax-abab6.5": {"input_per_1k": 0.5, "output_per_1k": 1.0},
    },
    "together": {
        "llama-3.1-70b": {"input_per_1k": 0.59, "output_per_1k": 0.79},
        "llama-3.1-8b": {"input_per_1k": 0.18, "output_per_1k": 0.18},
    },
    "perplexity": {
        "llama-3.1-sonar": {"input_per_1k": 1.0, "output_per_1k": 1.0},
    },
    "ollama": {
        "llama3.1": {"input_per_1k": 0.0, "output_per_1k": 0.0},
    },
    "_default": {
        "unknown": {"input_per_1k": 0.002, "output_per_1k": 0.002},
    },
}


def _load_pricing_file(path: str) -> dict:
    """Load pricing from external JSON file."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _find_pricing_file() -> Optional[str]:
    """Look for pricing_data.json alongside this module or in CWD."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "pricing_data.json"),
        os.path.join(os.getcwd(), "pricing_data.json"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


class PricingRegistry:
    def __init__(self, pricing_file: Optional[str] = None):
        self._registry: Dict[str, Dict[str, float]] = {}
        self._load_builtin_pricing()
        if pricing_file or (pricing_file is None and _find_pricing_file()):
            path = pricing_file or _find_pricing_file()
            if path:
                self._load_from_file(path)

    def _load_builtin_pricing(self) -> None:
        for provider, models in _BUILTIN.items():
            for model, rates in models.items():
                self._registry[f"{provider}:{model}"] = {
                    "input_per_token": rates["input_per_1k"] / 1000,
                    "output_per_token": rates["output_per_1k"] / 1000,
                    "currency": "USD",
                }

    def _load_from_file(self, path: str) -> None:
        try:
            data = _load_pricing_file(path)
            meta = data.pop("_meta", {})
            for provider, models in data.items():
                if provider.startswith("_"):
                    continue
                for model, rates in models.items():
                    self._registry[f"{provider}:{model}"] = {
                        "input_per_token": rates["input_per_1k"] / 1000,
                        "output_per_token": rates["output_per_1k"] / 1000,
                        "currency": "USD",
                    }
            if meta.get("last_updated"):
                self._registry["_meta:last_updated"] = meta["last_updated"]
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Failed to load pricing file %s: %s", path, e)

    def get_pricing(self, provider: str, model: str) -> Dict[str, float]:
        key = f"{provider}:{model}"
        entry = self._registry.get(key)
        if entry is not None:
            return entry
        for k, v in self._registry.items():
            if k.endswith(":unknown"):
                return v
        return {"input_per_token": 0.0, "output_per_token": 0.0, "currency": "USD"}

    def register_custom(
        self,
        provider: str,
        model: str,
        input_cost_per_1k: float,
        output_cost_per_1k: float,
        currency: str = "USD",
    ) -> None:
        self._registry[f"{provider}:{model}"] = {
            "input_per_token": input_cost_per_1k / 1000,
            "output_per_token": output_cost_per_1k / 1000,
            "currency": currency,
        }

    def has_model(self, provider: str, model: str) -> bool:
        return f"{provider}:{model}" in self._registry

    def list_models(self, provider: Optional[str] = None) -> Dict[str, Dict[str, float]]:
        if provider:
            return {k: v for k, v in self._registry.items() if k.startswith(f"{provider}:")}
        return dict(self._registry)

    def get_default_key(self) -> str:
        """Return the key used for unknown model fallback."""
        return "default:unknown"

    def calculate_cost(self, provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
        pricing = self.get_pricing(provider, model)
        cost = input_tokens * pricing.get("input_per_token", 0) + output_tokens * pricing.get("output_per_token", 0)
        return round(cost, 10)

    def get_last_updated(self) -> Optional[str]:
        return self._registry.get("_meta:last_updated")
