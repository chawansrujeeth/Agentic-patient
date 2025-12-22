from __future__ import annotations

import os
from functools import lru_cache

_PROVIDER_ALIASES = {
    "openai": "openai",
    "oai": "openai",
    "default": "openai",
    "gemini": "gemini",
    "google": "gemini",
    "googleai": "gemini",
}


@lru_cache(maxsize=1)
def get_ai_provider() -> str:
    """Return the configured provider (openai or gemini)."""

    explicit = os.getenv("LLM_PROVIDER") or os.getenv("AI_PROVIDER")
    if explicit:
        normalized = _PROVIDER_ALIASES.get(explicit.strip().lower())
        if not normalized:
            raise RuntimeError(f"Unknown AI provider specified: {explicit}")
        return normalized

    if os.getenv("GEMINI_API_KEY") and not os.getenv("OPENAI_API_KEY"):
        return "gemini"

    return "openai"


def is_gemini_provider() -> bool:
    return get_ai_provider() == "gemini"


def is_openai_provider() -> bool:
    return get_ai_provider() == "openai"
