"""
Token estimation when APIs do not report usage data.
Uses tiktoken for OpenAI models, character heuristic for others.
"""

from typing import Any


class TokenEstimator:
    """Estimate token counts when provider APIs do not report them."""

    def __init__(self):
        self._tiktoken_available = self._check_tiktoken()
        self._encoders: dict[str, Any] = {}

    def _check_tiktoken(self) -> bool:
        """Check if tiktoken is available."""
        try:
            import tiktoken  # noqa: F401

            return True
        except ImportError:
            return False

    def estimate(self, messages: list[dict[str, str]], model: str, provider: str) -> dict[str, Any]:
        text = " ".join([str(m.get("content", "")) for m in messages])
        return self.estimate_from_text(text, provider, model)

    def _estimate_with_tiktoken(self, text: str, model: str) -> int:
        import tiktoken
        try:
            if model not in self._encoders:
                try:
                    self._encoders[model] = tiktoken.encoding_for_model(model)
                except KeyError:
                    self._encoders[model] = tiktoken.get_encoding("cl100k_base")

            return len(self._encoders[model].encode(text))
        except Exception:
            return self._char_heuristic(text)

    def estimate_from_text(self, text: str, provider: str = "generic", model: str = "unknown") -> dict[str, Any]:
        inp = self._estimate_with_tiktoken(text, model) if provider == "openai" and self._tiktoken_available else self._char_heuristic(text)
        out = max(1, int(inp * 0.4))  # ponytail: 40% output ratio, tune per model if needed
        return {
            "input_tokens": inp,
            "output_tokens": out,
            "total_tokens": inp + out,
            "source": "estimated",
            "estimation_method": "tiktoken" if provider == "openai" and self._tiktoken_available else "character_heuristic",
        }

    def _char_heuristic(self, text: str) -> int:
        if not text:
            return 0
        return max(1, len(text) // 4)
