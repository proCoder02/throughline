"""Which providers, in what order, with which keys and models."""
from __future__ import annotations

import os

# Default order; only providers with a key set are used. Override with
# LLM_PROVIDERS=openrouter,ollama (comma separated).
DEFAULT_ORDER = "ollama,openrouter,groq"

OLLAMA_URL = "https://ollama.com/api/chat"
OPENAI_COMPATIBLE = {
    # name: (endpoint, key env var, model env var)
    "openrouter": ("https://openrouter.ai/api/v1/chat/completions", "OPENROUTER_API_KEY", "OPENROUTER_MODEL"),
    "groq": ("https://api.groq.com/openai/v1/chat/completions", "GROQ_API_KEY", "GROQ_CHAT_MODEL"),
}
DEFAULT_MODEL = "openai/gpt-oss-120b"
DEFAULT_OLLAMA_MODEL = "gpt-oss:120b"
TIMEOUT = 90

# How long a failing provider is skipped (seconds), by kind of failure.
COOLDOWN = {
    "auth": 3600,   # out of credit / bad or deleted key (401, 402, 403)
    "rate": 120,    # rate limited (429)
    "down": 300,    # timeout, connection error, 5xx
}


def ollama_keys() -> list:
    return [k for k in (os.getenv("OLLAMA_API_KEY") or os.getenv("olama_api_key"),
                        os.getenv("OLLAMA_API_KEY_2") or os.getenv("olama_api_key_2")) if k]


def has_key(name: str) -> bool:
    if name == "ollama":
        return bool(ollama_keys())
    spec = OPENAI_COMPATIBLE.get(name)
    return bool(spec and os.getenv(spec[1], "").strip())


def providers() -> list:
    """Configured order, keeping only known providers that have a key."""
    from .backends import PROVIDERS
    raw = os.getenv("LLM_PROVIDERS", "").strip() or DEFAULT_ORDER
    return [p for p in (x.strip().lower() for x in raw.split(",")) if p in PROVIDERS and has_key(p)]
