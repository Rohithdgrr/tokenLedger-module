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

KNOWN_MODELS: dict[str, str] = {
    "gpt-4o": "openai", "gpt-4o-mini": "openai", "gpt-4-turbo": "openai",
    "gpt-3.5-turbo": "openai", "o1-mini": "openai", "o1-preview": "openai",
    "claude-3-5-sonnet-20241022": "anthropic", "claude-3-opus-20240229": "anthropic",
    "claude-3-haiku-20240307": "anthropic", "claude-sonnet-4-20250514": "anthropic",
    "gemini-1.5-pro": "google", "gemini-1.5-flash": "google", "gemini-2.0-flash": "google",
    "llama-3.1-70b": "groq", "llama-3.1-8b": "groq", "mixtral-8x7b": "groq",
    "deepseek-chat": "deepseek", "deepseek-coder": "deepseek",
    "mistral-large": "mistral", "mistral-small": "mistral",
    "command-r-plus": "cohere", "command-r": "cohere",
    "llama3.1": "ollama", "llama3.2": "ollama",
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
    if model_name in KNOWN_MODELS:
        return KNOWN_MODELS[model_name]
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
            continue
    return None


def auto_detect(
    model_name: str | None = None,
    messages: Any = None,
    **kwargs: Any,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "provider": None,
        "model": model_name or kwargs.get("model"),
        "confidence": "low",
        "detection_method": None,
    }

    if model_name:
        provider = detect_provider_from_model(model_name)
        if provider:
            result["provider"] = provider
            result["confidence"] = "high"
            result["detection_method"] = "model_name"
            return result

    provider = detect_provider_from_messages(messages)
    if provider:
        result["provider"] = provider
        result["model"] = result["model"] or "unknown"
        result["confidence"] = "medium"
        result["detection_method"] = "message_shape"
        return result

    if model_name:
        provider = detect_provider_from_model(model_name)
        result["provider"] = provider or "unknown"
        result["confidence"] = "low" if provider else "none"
        result["detection_method"] = "model_name_fallback"
    else:
        result["provider"] = kwargs.get("provider", "unknown")
        result["confidence"] = "none"
        result["detection_method"] = "not_detected"

    return result
