"""
In-memory pricing registry for LLM providers.
No external pricing API calls.
"""

from typing import Dict, Optional


class PricingRegistry:
    """
    Built-in pricing database mapping provider:model to per-token rates.
    All costs are stored and calculated in USD.
    """

    def __init__(self):
        self._registry: Dict[str, Dict[str, float]] = {}
        self._load_builtin_pricing()

    def _load_builtin_pricing(self) -> None:
        """Initialize with known provider pricing."""
        self._registry["openai:gpt-4o"] = {
            "input_per_token": 0.005 / 1000,
            "output_per_token": 0.015 / 1000,
            "currency": "USD",
        }
        self._registry["openai:gpt-4o-mini"] = {
            "input_per_token": 0.00015 / 1000,
            "output_per_token": 0.0006 / 1000,
            "currency": "USD",
        }
        self._registry["openai:gpt-4-turbo"] = {
            "input_per_token": 0.01 / 1000,
            "output_per_token": 0.03 / 1000,
            "currency": "USD",
        }
        self._registry["openai:gpt-3.5-turbo"] = {
            "input_per_token": 0.0005 / 1000,
            "output_per_token": 0.0015 / 1000,
            "currency": "USD",
        }

        self._registry["anthropic:claude-3-5-sonnet-20241022"] = {
            "input_per_token": 0.003 / 1000,
            "output_per_token": 0.015 / 1000,
            "currency": "USD",
        }
        self._registry["anthropic:claude-3-opus-20240229"] = {
            "input_per_token": 0.015 / 1000,
            "output_per_token": 0.075 / 1000,
            "currency": "USD",
        }
        self._registry["anthropic:claude-3-haiku-20240307"] = {
            "input_per_token": 0.00025 / 1000,
            "output_per_token": 0.00125 / 1000,
            "currency": "USD",
        }

        self._registry["google:gemini-1.5-pro"] = {
            "input_per_token": 0.0035 / 1000,
            "output_per_token": 0.0105 / 1000,
            "currency": "USD",
        }
        self._registry["google:gemini-1.5-flash"] = {
            "input_per_token": 0.00035 / 1000,
            "output_per_token": 0.00105 / 1000,
            "currency": "USD",
        }

        self._registry["groq:llama-3.1-70b"] = {
            "input_per_token": 0.00059 / 1000,
            "output_per_token": 0.00079 / 1000,
            "currency": "USD",
        }
        self._registry["groq:llama-3.1-8b"] = {
            "input_per_token": 0.00005 / 1000,
            "output_per_token": 0.00008 / 1000,
            "currency": "USD",
        }
        self._registry["groq:mixtral-8x7b"] = {
            "input_per_token": 0.00024 / 1000,
            "output_per_token": 0.00024 / 1000,
            "currency": "USD",
        }

        self._registry["openrouter:openai/gpt-4o"] = {
            "input_per_token": 0.005 / 1000,
            "output_per_token": 0.015 / 1000,
            "currency": "USD",
        }

        self._registry["deepseek:deepseek-chat"] = {
            "input_per_token": 0.00000014,
            "output_per_token": 0.00000028,
            "currency": "USD",
        }
        self._registry["deepseek:deepseek-coder"] = {
            "input_per_token": 0.00000014,
            "output_per_token": 0.00000028,
            "currency": "USD",
        }

        self._registry["mistral:mistral-large"] = {
            "input_per_token": 0.000002,
            "output_per_token": 0.000006,
            "currency": "USD",
        }
        self._registry["mistral:mistral-small"] = {
            "input_per_token": 0.0000006,
            "output_per_token": 0.0000018,
            "currency": "USD",
        }
        self._registry["mistral:open-mistral-nemo"] = {
            "input_per_token": 0.0000003,
            "output_per_token": 0.0000003,
            "currency": "USD",
        }

        self._registry["cohere:command-r-plus"] = {
            "input_per_token": 0.000003,
            "output_per_token": 0.000015,
            "currency": "USD",
        }
        self._registry["cohere:command-r"] = {
            "input_per_token": 0.0000005,
            "output_per_token": 0.000001,
            "currency": "USD",
        }

        self._registry["nvidia:llama-3.1-nemotron"] = {
            "input_per_token": 0.0000002,
            "output_per_token": 0.0000002,
            "currency": "USD",
        }
        self._registry["nvidia:mixtral-8x22b"] = {
            "input_per_token": 0.0000009,
            "output_per_token": 0.0000009,
            "currency": "USD",
        }

        self._registry["kimi:moonshot-v1-8k"] = {
            "input_per_token": 0.000001,
            "output_per_token": 0.000002,
            "currency": "USD",
        }
        self._registry["kimi:moonshot-v1-32k"] = {
            "input_per_token": 0.000002,
            "output_per_token": 0.000004,
            "currency": "USD",
        }

        self._registry["glm:glm-4"] = {
            "input_per_token": 0.0000005,
            "output_per_token": 0.0000005,
            "currency": "USD",
        }
        self._registry["glm:glm-4v"] = {
            "input_per_token": 0.0000005,
            "output_per_token": 0.0000005,
            "currency": "USD",
        }

        self._registry["minimax:minimax-abab6.5"] = {
            "input_per_token": 0.0000005,
            "output_per_token": 0.000001,
            "currency": "USD",
        }

        self._registry["together:llama-3.1-70b"] = {
            "input_per_token": 0.00000059,
            "output_per_token": 0.00000079,
            "currency": "USD",
        }
        self._registry["together:llama-3.1-8b"] = {
            "input_per_token": 0.00000018,
            "output_per_token": 0.00000018,
            "currency": "USD",
        }

        self._registry["perplexity:llama-3.1-sonar"] = {
            "input_per_token": 0.000001,
            "output_per_token": 0.000001,
            "currency": "USD",
        }

        self._registry["ollama:llama3.1"] = {
            "input_per_token": 0.0,
            "output_per_token": 0.0,
            "currency": "USD",
        }

        self._registry["default:unknown"] = {
            "input_per_token": 0.002 / 1000,
            "output_per_token": 0.002 / 1000,
            "currency": "USD",
        }

    def get_pricing(self, provider: str, model: str) -> Dict[str, float]:
        """Get pricing for a specific provider and model."""
        key = f"{provider}:{model}"
        return self._registry.get(key, self._registry["default:unknown"])

    def register_custom(
        self,
        provider: str,
        model: str,
        input_cost_per_1k: float,
        output_cost_per_1k: float,
        currency: str = "USD",
    ) -> None:
        """Register custom pricing for a provider/model."""
        self._registry[f"{provider}:{model}"] = {
            "input_per_token": input_cost_per_1k / 1000,
            "output_per_token": output_cost_per_1k / 1000,
            "currency": currency,
        }

    def has_model(self, provider: str, model: str) -> bool:
        """Check if a model exists in the registry."""
        return f"{provider}:{model}" in self._registry

    def list_models(self, provider: Optional[str] = None) -> Dict[str, Dict[str, float]]:
        """List all registered models, optionally filtered by provider."""
        if provider:
            return {k: v for k, v in self._registry.items() if k.startswith(f"{provider}:")}
        return dict(self._registry)

    def calculate_cost(self, provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
        """Calculate cost for a given token usage."""
        pricing = self.get_pricing(provider, model)
        cost = input_tokens * pricing["input_per_token"] + output_tokens * pricing["output_per_token"]
        return round(cost, 10)
