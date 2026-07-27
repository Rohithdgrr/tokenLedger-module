"""
Provider-specific token extraction from API responses.
Falls back to estimation when usage data is unavailable.
"""

from typing import Any, Optional


class TokenExtractor:
    """Extracts token usage from provider API responses."""

    PROVIDER_PARSERS = {
        "openai": "_parse_openai",
        "anthropic": "_parse_anthropic",
        "google": "_parse_gemini",
        "groq": "_parse_groq",
        "openrouter": "_parse_openrouter",
        "ollama": "_parse_ollama",
        "cohere": "_parse_cohere",
        "deepseek": "_parse_openai",
        "mistral": "_parse_openai",
        "nvidia": "_parse_openai",
        "kimi": "_parse_openai",
        "glm": "_parse_openai",
        "minimax": "_parse_openai",
        "together": "_parse_openai",
        "perplexity": "_parse_openai",
    }

    def extract(self, response: Any, provider: str) -> Optional[dict[str, Any]]:
        """Extract token usage from a provider response."""
        parser_name = self.PROVIDER_PARSERS.get(provider, "_parse_generic")
        parser = getattr(self, parser_name, self._parse_generic)
        return parser(response)

    def _parse_openai(self, response: Any) -> Optional[dict[str, Any]]:
        """Parse OpenAI response usage."""
        try:
            usage = response.usage
            if usage is None:
                return None
            return {
                "input_tokens": getattr(usage, "prompt_tokens", 0),
                "output_tokens": getattr(usage, "completion_tokens", 0),
                "total_tokens": getattr(usage, "total_tokens", 0),
                "source": "api_reported",
            }
        except AttributeError:
            return None

    def _parse_anthropic(self, response: Any) -> Optional[dict[str, Any]]:
        """Parse Anthropic response usage."""
        try:
            usage = response.usage
            if usage is None:
                return None
            input_tokens = getattr(usage, "input_tokens", 0)
            output_tokens = getattr(usage, "output_tokens", 0)
            return {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "source": "api_reported",
            }
        except AttributeError:
            return None

    def _parse_gemini(self, response: Any) -> Optional[dict[str, Any]]:
        """Parse Google Gemini response usage."""
        try:
            metadata = response.usage_metadata
            if metadata is None:
                return None
            return {
                "input_tokens": getattr(metadata, "prompt_token_count", 0),
                "output_tokens": getattr(metadata, "candidates_token_count", 0),
                "total_tokens": getattr(metadata, "total_token_count", 0),
                "source": "api_reported",
            }
        except AttributeError:
            return None

    def _parse_groq(self, response: Any) -> Optional[dict[str, Any]]:
        """Parse Groq response usage."""
        try:
            usage = response.usage
            if usage is None:
                return None
            return {
                "input_tokens": getattr(usage, "prompt_tokens", 0),
                "output_tokens": getattr(usage, "completion_tokens", 0),
                "total_tokens": getattr(usage, "total_tokens", 0),
                "source": "api_reported",
            }
        except AttributeError:
            return None

    def _parse_openrouter(self, response: Any) -> Optional[dict[str, Any]]:
        """Parse OpenRouter response usage."""
        try:
            usage = response.usage
            if usage is None:
                return None
            return {
                "input_tokens": getattr(usage, "prompt_tokens", 0),
                "output_tokens": getattr(usage, "completion_tokens", 0),
                "total_tokens": getattr(usage, "total_tokens", 0),
                "source": "api_reported",
            }
        except AttributeError:
            return None

    def _parse_ollama(self, response: Any) -> Optional[dict[str, Any]]:
        """Parse Ollama response usage."""
        try:
            usage = getattr(response, "usage", None)
            if usage:
                return {
                    "input_tokens": getattr(usage, "prompt_tokens", 0),
                    "output_tokens": getattr(usage, "completion_tokens", 0),
                    "total_tokens": getattr(usage, "total_tokens", 0),
                    "source": "api_reported",
                }
            eval_count = getattr(response, "eval_count", 0)
            prompt_eval_count = getattr(response, "prompt_eval_count", 0)
            if eval_count or prompt_eval_count:
                return {
                    "input_tokens": prompt_eval_count,
                    "output_tokens": eval_count,
                    "total_tokens": prompt_eval_count + eval_count,
                    "source": "api_reported",
                }
            return None
        except AttributeError:
            return None

    def _parse_cohere(self, response: Any) -> Optional[dict[str, Any]]:
        try:
            meta = response.meta
            if meta is None:
                return None
            tokens = meta.tokens
            return {
                "input_tokens": getattr(tokens, "input_tokens", 0),
                "output_tokens": getattr(tokens, "output_tokens", 0),
                "total_tokens": getattr(tokens, "input_tokens", 0) + getattr(tokens, "output_tokens", 0),
                "source": "api_reported",
            }
        except AttributeError:
            return None

    def _parse_generic(self, response: Any) -> Optional[dict[str, Any]]:
        """Generic parser that tries common patterns."""
        try:
            usage = getattr(response, "usage", None)
            if usage:
                return {
                    "input_tokens": getattr(usage, "prompt_tokens", getattr(usage, "input_tokens", 0)),
                    "output_tokens": getattr(usage, "completion_tokens", getattr(usage, "output_tokens", 0)),
                    "total_tokens": getattr(usage, "total_tokens", 0),
                    "source": "api_reported",
                }
            return None
        except AttributeError:
            return None
