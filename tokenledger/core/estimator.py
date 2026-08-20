"""
Token estimation when APIs do not report usage data.
Uses tiktoken for OpenAI models, character heuristic for others.
"""

from typing import Any


class TokenEstimator:
    """Estimate token counts when provider APIs do not report them."""

    def __init__(self) -> None:
        self._tiktoken_available = self._check_tiktoken()
        self._encoders: dict[str, Any] = {}

    def _check_tiktoken(self) -> bool:
        """Check if tiktoken is available."""
        try:
            import tiktoken  # noqa: F401

            return True
        except ImportError:
            return False

    def estimate(
        self,
        messages: list[dict[str, str]],
        model: str,
        provider: str,
        output_text: str | None = None,
    ) -> dict[str, Any]:
        """Estimate tokens for a request.

        ``output_text`` is optional and used to measure (not invent) the
        output side. When absent (e.g. pre-call budget checks) ``output_tokens``
        is 0 rather than a fabricated 40% guess.
        """
        text = " ".join([str(m.get("content", "")) for m in messages])
        return self.estimate_from_text(text, provider, model, output_text)

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

    def estimate_from_text(
        self,
        text: str,
        provider: str = "generic",
        model: str = "unknown",
        output_text: str | None = None,
    ) -> dict[str, Any]:
        inp = self._estimate_with_tiktoken(text, model) if provider == "openai" and self._tiktoken_available else self._char_heuristic(text)
        if output_text:
            out = (
                self._estimate_with_tiktoken(output_text, model)
                if provider == "openai" and self._tiktoken_available
                else self._char_heuristic(output_text)
            )
        else:
            out = 0
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
        # CJK characters are roughly 1 token each; Latin is ~4 chars/token.
        cjk_count = sum(
            1 for ch in text if "\u4e00" <= ch <= "\u9fff" or "\u3000" <= ch <= "\u303f"
        )
        latin_count = len(text) - cjk_count
        return max(1, (latin_count // 4) + cjk_count)
