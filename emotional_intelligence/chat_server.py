"""
Local chat server for the cognitive-memory reasoning demo. Serves chat.html
and a small JSON API backing it. Reuses assemble_cognitive_memory()/
call_llm() from cognitive_reasoning_demo.py directly (no duplication).

Entirely self-contained in this folder -- does not import or touch app.py
or anything in the Flutter app, per standing instruction. Local-only tool:
binds to 127.0.0.1, not meant to be exposed.

Usage:
    python chat_server.py
    (then open http://127.0.0.1:5050 in a browser)
"""
from __future__ import annotations

import os

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

from cognitive_reasoning_demo import (
    DB_URL,
    REASONING_SYSTEM_PROMPT,
    assemble_cognitive_memory,
    assemble_multi_subject_memory,
    call_llm,
    fetch_subject_persona,
)

load_dotenv()

app = Flask(__name__)


def get_conn():
    return psycopg2.connect(DB_URL)


@app.route("/")
def index():
    return send_from_directory(os.path.dirname(__file__), "chat.html")


@app.route("/api/subjects")
def api_subjects():
    """Subjects with at least some extracted data -- keeps the picker to
    characters actually worth chatting about instead of all 776 (mostly
    single-mention) normalized labels."""
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT s.id, s.canonical_name,
                   COUNT(DISTINCT f.id) AS fact_count,
                   COUNT(DISTINCT m.id) AS memory_count
            FROM emotional_intelligence.subjects s
            LEFT JOIN emotional_intelligence.facts f ON f.subject_id = s.id
            LEFT JOIN emotional_intelligence.memories m ON m.subject_id = s.id
            WHERE EXISTS (SELECT 1 FROM emotional_intelligence.facts x WHERE x.subject_id = s.id)
               OR EXISTS (SELECT 1 FROM emotional_intelligence.preferences p WHERE p.subject_id = s.id)
               OR EXISTS (SELECT 1 FROM emotional_intelligence.beliefs b WHERE b.subject_id = s.id)
               OR EXISTS (SELECT 1 FROM emotional_intelligence.memories y WHERE y.subject_id = s.id)
            GROUP BY s.id, s.canonical_name
            ORDER BY (COUNT(DISTINCT f.id) + COUNT(DISTINCT m.id)) DESC, s.canonical_name
            """
        )
        subjects = cur.fetchall()
        cur.close()
        return jsonify(subjects)
    finally:
        conn.close()


@app.route("/api/persona/<subject_name>")
def api_persona(subject_name):
    """Full structured persona JSON for one subject -- facts/preferences/
    beliefs/memories/personality/relationships, straight from the DB (not
    the LLM-formatted text block /api/chat uses internally)."""
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            persona = fetch_subject_persona(cur, subject_name)
        except SystemExit as e:
            return jsonify({"error": str(e)}), 404
        finally:
            cur.close()
    finally:
        conn.close()
    return jsonify(persona)


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(force=True) or {}
    raw_subject = data.get("subject")
    # Accepts either a single name (string) or a list of names -- a list
    # loads more than one person's memory into the same context, for
    # questions spanning two people (comparisons, "who did what first",
    # relationship questions -- see the standing caveat in
    # REASONING_SYSTEM_PROMPT about not inventing shared history neither
    # side's own memory actually states).
    if isinstance(raw_subject, list):
        subject_names = [s.strip() for s in raw_subject if isinstance(s, str) and s.strip()]
    else:
        subject_names = [raw_subject.strip()] if isinstance(raw_subject, str) and raw_subject.strip() else []
    message = (data.get("message") or "").strip()
    history = data.get("history") or []  # [{role: 'user'|'assistant', content: str}, ...]

    if not subject_names or not message:
        return jsonify({"error": "subject (or subjects) and message are required"}), 400

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            if len(subject_names) == 1:
                memory_block, stats = assemble_cognitive_memory(cur, subject_names[0], message)
            else:
                memory_block, stats = assemble_multi_subject_memory(cur, subject_names, message)
        except SystemExit as e:
            return jsonify({"error": str(e)}), 404
        finally:
            cur.close()
    finally:
        conn.close()

    messages = [{"role": "system", "content": f"{REASONING_SYSTEM_PROMPT}\n\n{memory_block}"}]
    for turn in history[-10:]:  # bounded, same cost-control reasoning as the main app's chat history cap
        role = turn.get("role")
        content = turn.get("content")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message})

    try:
        reply = call_llm(messages)
    except Exception as e:
        return jsonify({"error": f"LLM call failed: {e!r}"}), 502

    return jsonify({"reply": reply, "memory_stats": stats})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=True)
