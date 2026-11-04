"""Running summary of long /chat/global threads.

Each turn only re-sends the last CHAT_HISTORY_MAX_TURNS exchanges to the
model, so anything said earlier in a long thread ("I'm getting Priya a
Kindle", "keep it under 2000") is simply gone by turn 8. This keeps a short
summary of everything that has scrolled out of that window: after a reply,
in the background, the messages that just fell out are folded into the
summary by a small fast model on Groq; on the next turn the summary rides
along in the system prompt (and to the follow-up resolver).

One row per user, created lazily (same pattern as meet/schema.py). Gated by
CHAT_THREAD_SUMMARY_ENABLED (off by default). Every failure leaves the old
summary in place -- the chat itself never waits on or breaks because of
this.
"""
import os
import threading
import time

import requests

from ops import tasks as ops_tasks  # reports outcomes when run via ops_tasks.wrap (no-op otherwise)

DDL = """
CREATE TABLE IF NOT EXISTS chat_thread_summaries (
    user_id INTEGER PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
    summary TEXT NOT NULL DEFAULT '',
    covered_until_id INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

# Fold messages in once at least this many have scrolled out of the window
# (two exchanges), so a long thread costs one small call every other turn.
MIN_NEW_MESSAGES = 4
MAX_MESSAGES_PER_CALL = 40
CHARS_PER_MESSAGE = 800
MAX_SUMMARY_CHARS = 1500

SUMMARY_SYSTEM = (
    "You maintain a short running summary of the EARLIER part of a chat between a user and their "
    "personal assistant. You get the current summary (may be empty) and some older messages that "
    "are about to scroll out of view. Return an updated summary the assistant can rely on later.\n"
    "Keep, as plain short bullet points (max ~150 words total):\n"
    "- who and what has been discussed (names, places, items, amounts, dates as stated);\n"
    "- anything the user said about themselves, their plans, preferences or decisions;\n"
    "- lists the assistant gave that the user may refer back to (keep their order);\n"
    "- open questions or requests not yet resolved.\n"
    "Drop small talk and anything superseded. Never invent details. Write facts, not commentary. "
    "Return only the bullet points."
)

_ensured = False
_lock = threading.Lock()


def enabled():
    return os.getenv("CHAT_THREAD_SUMMARY_ENABLED", "false").strip().lower() == "true"


def ensure_schema(cur, conn):
    global _ensured
    if _ensured:
        return
    with _lock:
        if _ensured:
            return
        cur.execute(DDL)
        conn.commit()
        _ensured = True


def load(cur, conn, user_id):
    """The current summary for this user's global chat, or ''."""
    try:
        ensure_schema(cur, conn)
        cur.execute("SELECT summary FROM chat_thread_summaries WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
    except Exception as e:
        print(f"[chat-summary] load failed: {e!r}")
        try:
            conn.rollback()
        except Exception:
            pass
        return ""
    if not row:
        return ""
    return (row["summary"] if isinstance(row, dict) else row[0]) or ""


def pending_messages(cur, user_id, recent_window, covered_until_id):
    """Messages newer than what the summary covers but older than the last
    recent_window messages (those are still sent verbatim every turn)."""
    cur.execute(
        "SELECT id, role, content FROM chat_messages "
        "WHERE user_id = %s AND conversation_id IS NULL AND id > %s AND id NOT IN ("
        "  SELECT id FROM chat_messages WHERE user_id = %s AND conversation_id IS NULL "
        "  ORDER BY id DESC LIMIT %s"
        ") ORDER BY id LIMIT %s",
        (user_id, covered_until_id, user_id, recent_window, MAX_MESSAGES_PER_CALL),
    )
    return cur.fetchall()


def build_messages(previous_summary, rows):
    lines = []
    for r in rows:
        role, content = (r["role"], r["content"]) if isinstance(r, dict) else (r[1], r[2])
        text = (content or "").strip().replace("\n", " ")
        if len(text) > CHARS_PER_MESSAGE:
            text = text[:CHARS_PER_MESSAGE] + "..."
        lines.append(f"{'user' if role == 'user' else 'assistant'}: {text}")
    user = (
        "Current summary:\n" + (previous_summary.strip() or "(none yet)")
        + "\n\nOlder messages to fold in:\n" + "\n".join(lines)
    )
    return [{"role": "system", "content": SUMMARY_SYSTEM}, {"role": "user", "content": user}]


def summarize(previous_summary, rows, post=requests.post):
    """New summary text, or None on any failure."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key or not rows:
        return None
    try:
        response = post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": os.getenv("CHAT_SUMMARY_MODEL", "openai/gpt-oss-20b"),
                "messages": build_messages(previous_summary, rows),
                "temperature": 0,
                "max_tokens": 700,
                "reasoning_effort": "low",
                "include_reasoning": False,
            },
            timeout=30,  # background: no user is waiting on this
        )
        response.raise_for_status()
        text = (response.json()["choices"][0]["message"].get("content") or "").strip()
    except Exception as e:
        print(f"[chat-summary] summarize failed: {e!r}")
        return None
    if not text:
        return None
    return text.replace("\x00", "")[:MAX_SUMMARY_CHARS]


def update(get_conn, release_conn, user_id, recent_window, post=requests.post):
    """Background entry point, after a global-chat reply. Never raises."""
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        ensure_schema(cur, conn)
        cur.execute(
            "INSERT INTO chat_thread_summaries (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
        cur.execute("SELECT summary, covered_until_id FROM chat_thread_summaries WHERE user_id = %s", (user_id,))
        summary, covered = cur.fetchone()
        conn.commit()
        rows = pending_messages(cur, user_id, recent_window, covered)
        conn.commit()
        if len(rows) < MIN_NEW_MESSAGES:
            ops_tasks.ignore()  # nothing has scrolled out yet: no model call
            return
        started = time.time()
        new_summary = summarize(summary, rows, post=post)
        if new_summary is None:
            ops_tasks.fail("summary model call failed or returned nothing")
            return
        last_id = rows[-1][0]
        # Only if nobody else moved it on meanwhile (two quick turns) --
        # otherwise the newer write wins and these messages get folded next time.
        cur.execute(
            "UPDATE chat_thread_summaries SET summary = %s, covered_until_id = %s, updated_at = now() "
            "WHERE user_id = %s AND covered_until_id = %s",
            (new_summary, last_id, user_id, covered),
        )
        conn.commit()
        print(f"[chat-summary] user {user_id}: folded {len(rows)} messages in {time.time() - started:.1f}s")
    except Exception as e:
        print(f"[chat-summary] update failed: {e!r}")
        ops_tasks.fail(repr(e))
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        if conn is not None:
            release_conn(conn)


def prompt_block(summary):
    """What goes into the system prompt -- '' when there's nothing yet."""
    if not summary or not summary.strip():
        return ""
    return (
        "\n\nEarlier in this chat (older messages no longer shown above, summarized -- treat these as things "
        "the user and you already said; resolve references to them):\n" + summary.strip()
    )
