"""How each provider is called. Every function takes
(messages, tools, max_tokens, options, ollama_post=None) and returns an
Ollama-shaped assistant message. Adding a provider = one function + one entry
in PROVIDERS (+ its key in config.py)."""
from __future__ import annotations

import os

import requests

from . import config


def ollama(messages, tools, max_tokens, options, ollama_post=None):
    payload = {"model": os.getenv("OLLAMA_MODEL", config.DEFAULT_OLLAMA_MODEL), "messages": messages, "stream": False,
               "options": {**({"num_predict": max_tokens} if max_tokens else {}), **(options or {})}}
    if tools:
        payload["tools"] = tools
    if ollama_post:  # the host's own Ollama client (its key rotation, CostLens tracking)
        msg = ollama_post(payload).get("message", {}) or {}
    else:
        last = None
        for key in config.ollama_keys():
            try:
                r = requests.post(config.OLLAMA_URL, json=payload, timeout=config.TIMEOUT,
                                  headers={"Authorization": f"Bearer {key}"})
                r.raise_for_status()
                msg = r.json().get("message", {}) or {}
                break
            except requests.RequestException as e:
                last = e
        else:
            raise last or RuntimeError("OLLAMA_API_KEY is not set")
    out = {"role": "assistant", "content": msg.get("content") or ""}
    if msg.get("tool_calls"):
        out["tool_calls"] = msg["tool_calls"]
    return out


def openai_compatible(name, messages, tools, max_tokens, options=None, ollama_post=None):
    url, key_env, model_env = config.OPENAI_COMPATIBLE[name]
    body = {"model": os.getenv(model_env, config.DEFAULT_MODEL), "messages": messages, "max_tokens": max_tokens}
    if tools:
        body["tools"] = tools
    headers = {"Authorization": "Bearer " + os.getenv(key_env, "").strip()}
    if name == "openrouter":
        headers.update({"HTTP-Referer": os.getenv("PUBLIC_BASE_URL", "https://throughline.app"), "X-Title": "Throughline"})
    r = requests.post(url, headers=headers, json=body, timeout=config.TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):  # OpenRouter can answer 200 with an error body
        raise RuntimeError(str(data["error"])[:300])
    msg = (data.get("choices") or [{}])[0].get("message") or {}
    out = {"role": "assistant", "content": msg.get("content") or ""}
    if msg.get("tool_calls"):
        out["tool_calls"] = msg["tool_calls"]  # OpenAI shape: id + function.arguments as a JSON string
    return out


PROVIDERS = {
    "ollama": ollama,
    "openrouter": lambda *a, **k: openai_compatible("openrouter", *a, **k),
    "groq": lambda *a, **k: openai_compatible("groq", *a, **k),
}
