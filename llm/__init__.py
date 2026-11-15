"""Throughline's AI providers -- self-contained, same convention as ops/:
nothing here imports app.py. Everything that talks to a hosted model
(app.py's call_llm / call_llm_with_tools for Personal and Corporate,
corporate/, the EI scripts) goes through chat() here, so if one provider
isn't working the next one answers, and changing providers is a .env change.

    import llm
    msg = llm.chat(messages, tools=None)   # -> {"role": "assistant", "content": ..., "tool_calls": [...]}
    llm.status()                           # each provider: configured? cooling down? last error
    llm.last_provider()                    # who answered this thread's last call

Modules:
  config.py     order (LLM_PROVIDERS), keys, models, cooldowns
  backends.py   how each provider is called (Ollama, OpenRouter, Groq)
  failover.py   try in order, skip providers that recently failed"""
from .config import providers
from .failover import chat, last_provider, status

__all__ = ["chat", "last_provider", "providers", "status"]
