"""One place that decides which hosted model answers. call_llm and
call_llm_with_tools in app.py (Personal and Corporate) both go through
chat() here; switching provider or model is a .env change.

LLM_PROVIDERS  order to try, comma separated (default: openrouter,ollama when
               OPENROUTER_API_KEY is set, else ollama). Next one is tried when
               a provider errors, times out or is out of credit.
  openrouter   OPENROUTER_API_KEY, OPENROUTER_MODEL (default openai/gpt-oss-120b)
  groq         GROQ_API_KEY, GROQ_CHAT_MODEL (default openai/gpt-oss-120b)
  ollama       Ollama Cloud, through app.py's existing _post_ollama_chat

chat() always returns an Ollama-shaped assistant message
{"role": "assistant", "content": str, "tool_calls": [...]}, so callers don't
care which provider answered."""
from __future__ import annotations

import os

import requests

OPENAI_COMPATIBLE = {
    "openrouter": ("https://openrouter.ai/api/v1/chat/completions", "OPENROUTER_API_KEY", "OPENROUTER_MODEL"),
    "groq": ("https://api.groq.com/openai/v1/chat/completions", "GROQ_API_KEY", "GROQ_CHAT_MODEL"),
}
DEFAULT_MODEL = "openai/gpt-oss-120b"


def providers() -> list:
    raw = os.getenv("LLM_PROVIDERS", "").strip()
    if raw:
        return [p.strip().lower() for p in raw.split(",") if p.strip()]
    return ["openrouter", "ollama"] if os.getenv("OPENROUTER_API_KEY") else ["ollama"]


def _openai_compatible(name, messages, tools, max_tokens):
    url, key_env, model_env = OPENAI_COMPATIBLE[name]
    key = os.getenv(key_env, "").strip()
    if not key:
        raise RuntimeError(f"{key_env} is not set")
    body = {"model": os.getenv(model_env, DEFAULT_MODEL), "messages": messages, "max_tokens": max_tokens}
    if tools:
        body["tools"] = tools
    headers = {"Authorization": f"Bearer {key}"}
    if name == "openrouter":
        headers.update({"HTTP-Referer": os.getenv("PUBLIC_BASE_URL", "https://throughline.app"), "X-Title": "Throughline"})
    r = requests.post(url, headers=headers, json=body, timeout=90)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):  # OpenRouter can answer 200 with an error body
        raise RuntimeError(str(data["error"])[:300])
    msg = (data.get("choices") or [{}])[0].get("message") or {}
    out = {"role": "assistant", "content": msg.get("content") or ""}
    if msg.get("tool_calls"):
        out["tool_calls"] = msg["tool_calls"]  # OpenAI shape: id + function.arguments as a JSON string
    return out


def chat(messages, *, tools=None, ollama_post=None, ollama_options=None, max_tokens=6000) -> dict:
    last = None
    for name in providers():
        try:
            if name in OPENAI_COMPATIBLE:
                return _openai_compatible(name, messages, tools, max_tokens)
            if name == "ollama" and ollama_post:
                payload = {"model": os.getenv("OLLAMA_MODEL", "gpt-oss:120b"), "messages": messages, "stream": False}
                if tools:
                    payload["tools"] = tools
                if ollama_options:
                    payload["options"] = ollama_options
                msg = ollama_post(payload).get("message", {}) or {}
                return {"role": "assistant", "content": msg.get("content") or "", **({"tool_calls": msg["tool_calls"]} if msg.get("tool_calls") else {})}
        except Exception as e:  # noqa: BLE001 -- try the next provider
            print(f"[llm] {name} failed: {str(e)[:200]}")
            last = e
    if last:
        raise last
    raise RuntimeError("No LLM provider configured (LLM_PROVIDERS)")
