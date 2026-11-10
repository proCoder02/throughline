"""Which AI answers what. Every function takes and returns a plain
call_llm(messages) -> str, so the rest of Corporate never knows which
provider is behind it.

  with_groq_fallback(call_llm)  people-facing work: as is (failover is in the llm/ package)
  background(call_llm)          background jobs: the self-hosted model (LOCAL_LLM_URL)
                                first, then the people-facing chain

Env: GROQ_API_KEY, GROQ_CHAT_MODEL (default openai/gpt-oss-120b),
     LOCAL_LLM_URL (e.g. http://10.0.0.12:11434), LOCAL_LLM_MODEL (default throughline-3b),
     LOCAL_LLM_TIMEOUT (seconds, default 120), CORPORATE_GROQ_FALLBACK=false to switch off."""
from __future__ import annotations

import os

import requests


def groq_chat(messages, model=None, timeout=90) -> str:
    key = os.getenv("GROQ_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GROQ_API_KEY is not set")
    r = requests.post("https://api.groq.com/openai/v1/chat/completions", timeout=timeout,
                      headers={"Authorization": f"Bearer {key}"},
                      json={"model": model or os.getenv("GROQ_CHAT_MODEL", "openai/gpt-oss-120b"),
                            "messages": messages, "temperature": 0.2})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"] or ""


def local_chat(messages) -> str:
    base = os.getenv("LOCAL_LLM_URL", "").rstrip("/")
    r = requests.post(f"{base}/api/chat", timeout=float(os.getenv("LOCAL_LLM_TIMEOUT", "120")),
                      json={"model": os.getenv("LOCAL_LLM_MODEL", "throughline-3b"), "messages": messages,
                            "stream": False, "options": {"temperature": 0.1}})
    r.raise_for_status()
    return r.json().get("message", {}).get("content", "")


def _chain(*fns):
    def call(messages):
        last = None
        for fn in fns:
            try:
                out = fn(messages)
                if out and out.strip():
                    return out
            except Exception as e:  # noqa: BLE001 -- try the next provider
                last = e
        if last:
            raise last
        return ""
    return call


def with_groq_fallback(call_llm):
    """Kept for callers: provider failover (Ollama, OpenRouter, Groq) now lives in
    the llm/ package, which app.py's call_llm already uses -- no second chain here."""
    return call_llm


def background(call_llm):
    chain = with_groq_fallback(call_llm)
    return _chain(local_chat, chain) if os.getenv("LOCAL_LLM_URL") else chain
