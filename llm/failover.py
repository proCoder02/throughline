"""Try providers in order; a provider that fails is skipped for its cooldown
(config.COOLDOWN), so a dead one doesn't add its delay to every request. If
every provider is cooling down, all are tried anyway rather than failing.
State is per process (web app, worker and EI cron each keep their own)."""
from __future__ import annotations

import threading
import time

from . import config
from . import backends as P

_lock = threading.Lock()
_down_until: dict = {}   # provider -> unix time it may be tried again
_last_error: dict = {}   # provider -> short reason
_local = threading.local()


def classify(e: Exception) -> str:
    code = getattr(getattr(e, "response", None), "status_code", None)
    text = str(e)
    if code in (401, 402, 403) or "402" in text or "Payment Required" in text or "User not found" in text:
        return "auth"
    if code == 429 or "429" in text:
        return "rate"
    return "down"


def _mark_down(name: str, e: Exception) -> None:
    kind = classify(e)
    with _lock:
        _down_until[name] = time.time() + config.COOLDOWN[kind]
        _last_error[name] = f"{kind}: {str(e)[:160]}"


def _available(order: list) -> list:
    now = time.time()
    return [p for p in order if _down_until.get(p, 0) <= now] or order


def chat(messages, *, tools=None, ollama_post=None, ollama_options=None, max_tokens=6000) -> dict:
    order = config.providers()
    if not order:
        raise RuntimeError("No AI provider configured: set OLLAMA_API_KEY, OPENROUTER_API_KEY or GROQ_API_KEY")
    last = None
    for name in _available(order):
        try:
            msg = P.PROVIDERS[name](messages, tools, max_tokens, ollama_options, ollama_post=ollama_post)
            with _lock:
                _down_until.pop(name, None)
            _local.provider = name
            return msg
        except Exception as e:  # noqa: BLE001 -- hop to the next provider
            _mark_down(name, e)
            print(f"[llm] {name} failed, trying the next provider: {str(e)[:160]}")
            last = e
    raise last


def last_provider() -> str | None:
    return getattr(_local, "provider", None)


def status() -> dict:
    now = time.time()
    return {p: {"configured": config.has_key(p), "cooling_down_for_s": max(0, int(_down_until.get(p, 0) - now)),
                "last_error": _last_error.get(p)} for p in P.PROVIDERS}
