"""
In-memory pricing registry for LLM providers.
Loads from external JSON file with fallback to builtin data.

All source rates are expressed in USD per 1,000,000 tokens and converted
to per-token values at load time. Legacy files using ``input_per_1k`` /
``output_per_1k`` keys (or a ``_meta.unit`` of ``usd_per_1k``) are still
accepted and converted from per-1k units.
"""

import json
import logging
import os
from typing import Any, Optional, cast

logger = logging.getLogger(__name__)

_BIG_PRICE_WARNING = 500.0  # USD per 1M tokens — anything above is almost certainly a unit error

_BUILTIN = {
    "openai": {
        "gpt-4o": {"input_per_1m": 5.0, "output_per_1m": 15.0},
        "gpt-4o-mini": {"input_per_1m": 0.15, "output_per_1m": 0.6},
        "gpt-4-turbo": {"input_per_1m": 10.0, "output_per_1m": 30.0},
        "gpt-3.5-turbo": {"input_per_1m": 0.5, "output_per_1m": 1.5},
    },
    "anthropic": {
        "claude-3-5-sonnet-20241022": {"input_per_1m": 3.0, "output_per_1m": 15.0},
        "claude-3-opus-20240229": {"input_per_1m": 15.0, "output_per_1m": 75.0},
        "claude-3-haiku-20240307": {"input_per_1m": 0.25, "output_per_1m": 1.25},
    },
    "google": {
        "gemini-1.5-pro": {"input_per_1m": 3.5, "output_per_1m": 10.5},
        "gemini-1.5-flash": {"input_per_1m": 0.35, "output_per_1m": 1.05},
    },
    "groq": {
        "llama-3.1-70b": {"input_per_1m": 0.59, "output_per_1m": 0.79},
        "llama-3.1-8b": {"input_per_1m": 0.05, "output_per_1m": 0.08},
        "mixtral-8x7b": {"input_per_1m": 0.24, "output_per_1m": 0.24},
    },
    "openrouter": {
        "openai/gpt-4o": {"input_per_1m": 5.0, "output_per_1m": 15.0},
    },
    "deepseek": {
        "deepseek-chat": {"input_per_1m": 0.14, "output_per_1m": 0.28},
        "deepseek-coder": {"input_per_1m": 0.14, "output_per_1m": 0.28},
    },
    "mistral": {
        "mistral-large": {"input_per_1m": 2.0, "output_per_1m": 6.0},
        "mistral-small": {"input_per_1m": 0.6, "output_per_1m": 1.8},
        "open-mistral-nemo": {"input_per_1m": 0.3, "output_per_1m": 0.3},
    },
    "cohere": {
        "command-r-plus": {"input_per_1m": 2.5, "output_per_1m": 10.0},
        "command-r": {"input_per_1m": 0.15, "output_per_1m": 0.6},
    },
    "nvidia": {
        "llama-3.1-nemotron": {"input_per_1m": 0.7, "output_per_1m": 0.7},
        "mixtral-8x22b": {"input_per_1m": 0.9, "output_per_1m": 0.9},
    },
    "kimi": {
        "moonshot-v1-8k": {"input_per_1m": 1.0, "output_per_1m": 2.0},
        "moonshot-v1-32k": {"input_per_1m": 2.0, "output_per_1m": 4.0},
    },
    "glm": {
        "glm-4": {"input_per_1m": 0.5, "output_per_1m": 0.5},
        "glm-4v": {"input_per_1m": 0.5, "output_per_1m": 0.5},
    },
    "minimax": {
        "minimax-abab6.5": {"input_per_1m": 0.5, "output_per_1m": 1.0},
    },
    "together": {
        "llama-3.1-70b": {"input_per_1m": 0.59, "output_per_1m": 0.79},
        "llama-3.1-8b": {"input_per_1m": 0.18, "output_per_1m": 0.18},
    },
    "perplexity": {
        "llama-3.1-sonar": {"input_per_1m": 1.0, "output_per_1m": 1.0},
    },
    "ollama": {
        "llama3.1": {"input_per_1m": 0.0, "output_per_1m": 0.0},
    },
    "_default": {
        "unknown": {"input_per_1m": 2.0, "output_per_1m": 2.0},
    },
}


def _to_per_token(provider: str, model: str, rates: dict, unit: str = "usd_per_1k") -> dict:
    """Convert source rates to per-token USD, with unit sanity validation.

    Accepts ``input_per_1m``/``output_per_1m`` (modern), ``input``/``output``
    scaled by ``_meta.unit``, or legacy ``input_per_1k``/``output_per_1k``.
    """
    if "input_per_1m" in rates and "output_per_1m" in rates:
        per_1m_in = float(rates["input_per_1m"])
        per_1m_out = float(rates["output_per_1m"])
        per_token_in = per_1m_in / 1_000_000
        per_token_out = per_1m_out / 1_000_000
    elif "input_per_1k" in rates and "output_per_1k" in rates:
        per_token_in = float(rates["input_per_1k"]) / 1000
        per_token_out = float(rates["output_per_1k"]) / 1000
        per_1m_in = per_token_in * 1_000_000
        per_1m_out = per_token_out * 1_000_000
    elif "input" in rates and "output" in rates:
        factor = 1_000_000 if unit == "usd_per_1m" else 1000.0
        per_token_in = float(rates["input"]) / factor
        per_token_out = float(rates["output"]) / factor
        per_1m_in = per_token_in * 1_000_000
        per_1m_out = per_token_out * 1_000_000
    else:
        raise ValueError(
            f"Unrecognized pricing format for {provider}:{model}: expected "
            "input_per_1m/output_per_1m, input/output, or input_per_1k/output_per_1k"
        )

    if per_1m_in < 0 or per_1m_out < 0:
        logger.warning("Negative pricing rate for %s:%s (in=%s out=%s) — ignoring entry", provider, model, per_1m_in, per_1m_out)
        raise ValueError(f"Negative pricing rate for {provider}:{model}")
    if max(per_1m_in, per_1m_out) > _BIG_PRICE_WARNING:
        logger.warning(
            "Suspiciously high pricing rate for %s:%s (in=$%.2f/1M out=$%.2f/1M) — likely a unit/scale error; check the source data",
            provider,
            model,
            per_1m_in,
            per_1m_out,
        )

    return {
        "input_per_token": per_1m_in / 1_000_000,
        "output_per_token": per_1m_out / 1_000_000,
        "currency": rates.get("currency", "USD"),
    }


def _load_pricing_file(path: str) -> dict[str, Any]:
    """Load pricing from external JSON file."""
    with open(path, encoding="utf-8") as f:
        return cast(dict[str, Any], json.load(f))


def _find_pricing_file() -> Optional[str]:
    """Look for pricing_data.json alongside this module, in the package, or in CWD."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "pricing_data.json"),
        os.path.join(os.path.dirname(here), "pricing_data.json"),
        os.path.join(os.getcwd(), "pricing_data.json"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


class PricingRegistry:
    def __init__(self, pricing_file: Optional[str] = None):
        self._registry: dict[str, dict[str, Any]] = {}
        self._load_builtin_pricing()
        if pricing_file or (pricing_file is None and _find_pricing_file()):
            path = pricing_file or _find_pricing_file()
            if path:
                self._load_from_file(path)

    def _load_builtin_pricing(self) -> None:
        for provider, models in _BUILTIN.items():
            for model, rates in models.items():
                self._registry[f"{provider}:{model}"] = _to_per_token(provider, model, rates, unit="usd_per_1m")

    def _load_from_file(self, path: str) -> None:
        try:
            data = _load_pricing_file(path)
            meta = data.pop("_meta", {})
            unit = meta.get("unit", "usd_per_1k")
            for provider, models in data.items():
                if provider.startswith("_"):
                    continue
                for model, rates in models.items():
                    self._registry[f"{provider}:{model}"] = _to_per_token(provider, model, rates, unit=unit)
            if meta.get("last_updated"):
                self._registry["_meta:last_updated"] = meta["last_updated"]
        except Exception as e:
            logger.warning("Failed to load pricing file %s: %s", path, e)

    def get_pricing(self, provider: str, model: str) -> dict[str, Any]:
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
        self._registry[f"{provider}:{model}"] = _to_per_token(
            provider,
            model,
            {
                "input_per_1k": input_cost_per_1k,
                "output_per_1k": output_cost_per_1k,
                "currency": currency,
            },
        )

    def has_model(self, provider: str, model: str) -> bool:
        return f"{provider}:{model}" in self._registry

    def list_models(self, provider: Optional[str] = None) -> dict[str, dict[str, float]]:
        items = {k: v for k, v in self._registry.items() if not k.startswith("_meta")}
        if provider:
            return {k: v for k, v in items.items() if k.startswith(f"{provider}:")}
        return items

    def get_default_key(self) -> str:
        """Return the key used for unknown model fallback."""
        if "_default:unknown" in self._registry:
            return "_default:unknown"
        return "default:unknown"

    def calculate_cost(self, provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
        pricing = self.get_pricing(provider, model)
        cost = input_tokens * float(pricing.get("input_per_token", 0)) + output_tokens * float(pricing.get("output_per_token", 0))
        return round(cost, 10)

    def get_last_updated(self) -> Optional[str]:
        value = self._registry.get("_meta:last_updated")
        return value if isinstance(value, str) else None
