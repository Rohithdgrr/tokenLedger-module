"""Auto-detect LLM provider and model from messages or model name."""

from __future__ import annotations

from typing import Any

PROVIDER_MODEL_PREFIXES: dict[str, list[str]] = {
    "openai": ["gpt-", "o1-", "o3-", "dall-e", "text-embedding", "tts-", "whisper-"],
    "anthropic": ["claude"],
    "google": ["gemini"],
    "groq": ["llama-", "mixtral", "gemma", "whisper-large"],
    "deepseek": ["deepseek"],
    "mistral": ["mistral-", "open-mistral", "codestral", "pixtral"],
    "cohere": ["command", "embed-"],
    "perplexity": ["sonar", "pplx"],
    "together": ["together"],
    "openrouter": ["openai/", "anthropic/", "google/", "meta-", "mistralai/"],
    "ollama": ["llama3", "llama2", "codellama", "mistral"],
    "nvidia": ["nemotron", "llama-3.1-nemotron"],
    "kimi": ["moonshot"],
    "glm": ["glm-"],
    "minimax": ["minimax-abab", "abab"],
}

MESSAGE_SHAPE_HEURISTICS: list[tuple[str, Any]] = [
    ("anthropic", lambda m: isinstance(m, dict) and "messages" in m and isinstance(m.get("messages"), list)),
    ("google", lambda m: isinstance(m, dict) and "contents" in m and isinstance(m.get("contents"), list)),
    ("openai", lambda m: isinstance(m, (list, tuple)) and len(m) > 0 and isinstance(m[0], dict) and "role" in m[0] and "content" in m[0]),
    ("ollama", lambda m: isinstance(m, dict) and "prompt" in m),
]


def detect_provider_from_model(model_name: str) -> str | None:
    if not model_name:
        return None
    lower = model_name.lower()
    for provider, prefixes in PROVIDER_MODEL_PREFIXES.items():
        for prefix in prefixes:
            if lower.startswith(prefix.lower()):
                return provider
    return None


def detect_provider_from_messages(messages: Any) -> str | None:
    if messages is None:
        return None
    if not isinstance(messages, (list, tuple, dict)):
        return None
    for provider, check in MESSAGE_SHAPE_HEURISTICS:
        try:
            if check(messages):
                return provider
        except Exception:
            continue  # nosec B112: malformed messages shouldn't abort detection
    return None


def auto_detect(
    model_name: str | None = None,
    messages: Any = None,
    provider: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "provider": None,
        "model": model_name,
        "confidence": "low",
        "detection_method": None,
    }

    if model_name:
        p = detect_provider_from_model(model_name)
        if p:
            result.update(provider=p, confidence="high", detection_method="model_name")
            return result

    p = detect_provider_from_messages(messages)
    if p:
        result.update(provider=p, model=model_name or "unknown", confidence="medium", detection_method="message_shape")
        return result

    result.update(provider=detect_provider_from_model(model_name) or provider or "unknown",
                  confidence="none", detection_method="not_detected")
    return result
