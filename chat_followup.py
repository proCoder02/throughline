"""Follow-up carry-over for /chat/global.

chat_global decides what to fetch (a person's DMs/notes/conversations, this
month's spending) from the latest message alone, so a follow-up like "and
what did he say about the budget?" names nobody and fetches the wrong data
-- the earlier turns only reach the model after retrieval is already done.

This remembers, per user, who the thread is about and whether it was about
spending, and hands that back when the next message is a follow-up that
doesn't say so itself. No LLM call, no added latency: a Redis read before
retrieval and a write after the reply. Gated by CHAT_FOLLOWUP_CONTEXT_ENABLED
(off by default), read per request so it can be flipped without a restart.

Second layer, the resolver (CHAT_RESOLVER_ENABLED, off by default): for a
follow-up the rules above can't place ("what's the latest from him?" when
the name was only in the assistant's reply, "what did the second one say?"),
a small fast model on Groq rewrites the message into a standalone question
plus hints, used only to decide what to fetch. Any failure (timeout, bad
JSON, missing key) returns None and the request carries on exactly as
without it.

app.py owns the cache, the DB rows and the request; this module only shapes
the resolver call and checks its answer.
"""
import json
import os
import re
import time

import requests

from ops import tasks as ops_tasks  # reports why a resolver call failed (no-op outside ops_tasks.run)

STATE_TTL_SECONDS = 6 * 3600

# Gendered pronouns only: "they/them" refer to places and things ("are they
# open?") as often as to people, and carrying a person in on those would add
# "focus on <name>" to the prompt for a question that isn't about anyone.
PERSON_PRONOUN_RE = re.compile(r"\b(he|him|his|himself|she|her|hers|herself)\b", re.IGNORECASE)

# Openers and phrases that only make sense as a continuation of the thread.
CONTINUATION_RE = re.compile(
    r"^\s*(and|also|plus|then|so|what about|how about|same|ok(?:ay)?[,\s]+(?:and|what|how))\b"
    r"|\b(that one|this one|those|the same|instead|anything else|what else|more about|compared to)\b",
    re.IGNORECASE,
)

SHORT_MESSAGE_WORDS = 6

PROMPT_NOTE = (
    " Read the user's latest message as part of this chat, not on its own: resolve pronouns and "
    "references ('he', 'she', 'that one', 'the second one', 'same as before', 'and last week?') "
    "from the earlier messages here before answering, and answer the question they are actually "
    "continuing."
)


def enabled():
    return os.getenv("CHAT_FOLLOWUP_CONTEXT_ENABLED", "false").strip().lower() == "true"


def state_key(user_id):
    return f"user:{user_id}:global_chat_followup"


def _person(row):
    return {"id": row["id"], "name": row["name"]} if row else None


def resolve(prompt, state, *, named_person_found, fresh_topic_found):
    """What to carry into this turn from the previous one. fresh_topic_found
    means the message triggers a topic on its own (spending, nearby, mail).

    Returns a dict with any of: "speaker" / "friend" ({id, name} -- the
    caller re-checks them against the user's current rows), "finance" (True).
    Empty when there is nothing to carry or the message isn't a follow-up.
    """
    if not state or not prompt:
        return {}
    pronoun = bool(PERSON_PRONOUN_RE.search(prompt))
    continuation = bool(CONTINUATION_RE.search(prompt))
    short = len(prompt.split()) <= SHORT_MESSAGE_WORDS
    carried = {}

    has_person = state.get("speaker") or state.get("friend")
    # A pronoun points back at the last person; a bare continuation ("and the
    # villa name?") does too, unless the message brings its own topic ("and
    # how much did I spend?"), which is a switch, not a follow-up about them.
    if has_person and not named_person_found and (pronoun or (continuation and not fresh_topic_found)):
        if state.get("speaker"):
            carried["speaker"] = state["speaker"]
        if state.get("friend"):
            carried["friend"] = state["friend"]

    # "and on food?" / "what about last month?" after a spending answer.
    if (state.get("finance") and not fresh_topic_found and not named_person_found
            and (continuation or short)):
        carried["finance"] = True
    return carried


def next_state(previous, speaker_row, friend_row, finance_used):
    """State to store after a turn. The person sticks until a new one is
    named (so "how much did I spend?" in between doesn't forget them); the
    spending flag only describes the turn just answered."""
    previous = previous or {}
    if speaker_row or friend_row:
        speaker, friend = _person(speaker_row), _person(friend_row)
    else:
        speaker, friend = previous.get("speaker"), previous.get("friend")
    return {"speaker": speaker, "friend": friend, "finance": bool(finance_used)}


# --------------------------------------------------------------------------
# Resolver (small LLM)
# --------------------------------------------------------------------------

RESOLVER_TOPICS = ("spending", "messages", "conversations", "nearby", "mail", "other")

# Anything that leans on earlier turns: pronouns of every kind, ordinals,
# "same/again/else", plus the continuation openers above.
FOLLOWUP_HINT_RE = re.compile(
    r"\b(he|him|his|she|her|hers|they|them|their|it|its|that|this|those|these|there|one|ones|"
    r"first|second|third|fourth|last|latest|previous|earlier|same|again|else|more|instead|either|both)\b",
    re.IGNORECASE,
)

RESOLVER_SYSTEM = (
    "You rewrite the user's LATEST message in a chat into a standalone question, so a search "
    "system that cannot see the chat knows what to look up. You do NOT answer it.\n"
    "Rules:\n"
    "- Replace pronouns and references (he, she, they, it, that one, the second one, same as "
    "before) with what they point to in the chat, using exact names and items from the chat.\n"
    "- Carry over the subject, person and time period the latest message is continuing "
    "(e.g. 'and on food?' after a spending question means spending on food in that same period).\n"
    "- Keep the person a referenced thing belongs to: in a chat about Kunal's trip, 'and the villa "
    "name?' becomes 'What is the name of the villa Kunal booked?'; 'is that before Kunal leaves?' "
    "after Rahul's launch date becomes 'Is Rahul's launch before or after Kunal leaves?'.\n"
    "- If the latest message already stands on its own, or is a new topic, return it unchanged.\n"
    "- Never add facts, answers or guesses that aren't in the chat.\n"
    "Return ONLY a JSON object with keys:\n"
    '  "standalone": the rewritten question (string),\n'
    '  "people": EVERY person the question involves, including whoever a referenced thing belongs '
    "to, as written in the chat (list, may be empty),\n"
    f'  "topics": any of {list(RESOLVER_TOPICS)} (list),\n'
    '  "time_range": the period it refers to in plain words, or "" (string).'
)

RESOLVER_HISTORY_TURNS = 6
RESOLVER_CHARS_PER_TURN = 500


def resolver_enabled():
    return os.getenv("CHAT_RESOLVER_ENABLED", "false").strip().lower() == "true"


def needs_resolution(prompt, history):
    """Only follow-ups pay for the call: there must be earlier chat, and the
    message must lean on it or be short enough that it probably does."""
    if not history or not prompt or not prompt.strip():
        return False
    return bool(
        PERSON_PRONOUN_RE.search(prompt) or CONTINUATION_RE.search(prompt)
        or FOLLOWUP_HINT_RE.search(prompt) or len(prompt.split()) <= 5
    )


def build_resolver_messages(history, prompt, known_people=(), thread_summary=""):
    lines = []
    for m in (history or [])[-RESOLVER_HISTORY_TURNS:]:
        text = (m.get("content") or "").strip().replace("\n", " ")
        if len(text) > RESOLVER_CHARS_PER_TURN:
            text = text[:RESOLVER_CHARS_PER_TURN] + "..."
        lines.append(f"{'user' if m.get('role') == 'user' else 'assistant'}: {text}")
    people = ", ".join(list(known_people)[:60])
    user = (
        ("People the user knows: " + people + "\n\n" if people else "")
        + ("Summary of earlier in this chat:\n" + thread_summary.strip() + "\n\n"
           if thread_summary and thread_summary.strip() else "")
        + "Chat so far:\n" + "\n".join(lines)
        + f"\n\nLATEST message to rewrite: {prompt.strip()}"
    )
    return [{"role": "system", "content": RESOLVER_SYSTEM}, {"role": "user", "content": user}]


def parse_resolution(text):
    """The model's JSON, checked and trimmed -- None if it's unusable."""
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    standalone = data.get("standalone")
    if not isinstance(standalone, str) or not standalone.strip():
        return None
    people = [p.strip() for p in (data.get("people") or []) if isinstance(p, str) and p.strip()][:5]
    topics = [t for t in (data.get("topics") or []) if t in RESOLVER_TOPICS]
    time_range = data.get("time_range") if isinstance(data.get("time_range"), str) else ""
    return {
        "standalone": standalone.strip()[:400],
        "people": people,
        "topics": topics,
        "time_range": time_range.strip()[:60],
    }


def resolve_with_llm(history, prompt, known_people=(), post=requests.post, thread_summary=""):
    """Ask the small model; None on any failure so the caller just carries on."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        ops_tasks.fail("GROQ_API_KEY not set")
        return None
    payload = {
        "model": os.getenv("CHAT_RESOLVER_MODEL", "openai/gpt-oss-20b"),
        "messages": build_resolver_messages(history, prompt, known_people, thread_summary),
        "temperature": 0,
        "max_tokens": 400,
        "reasoning_effort": "low",
        "include_reasoning": False,
        "response_format": {"type": "json_object"},
    }
    started = time.time()
    try:
        response = post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=float(os.getenv("CHAT_RESOLVER_TIMEOUT_SECONDS", "2.5")),
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"].get("content") or ""
    except Exception as e:
        print(f"[chat-resolver] failed after {time.time() - started:.2f}s: {e!r}")
        ops_tasks.fail("timeout" if isinstance(e, requests.Timeout) else repr(e))
        return None
    result = parse_resolution(content)
    if result is None:
        ops_tasks.fail("reply was not a usable resolution")
    print(f"[chat-resolver] {time.time() - started:.2f}s {prompt!r} -> {result!r}")
    return result
