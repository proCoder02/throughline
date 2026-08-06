"""
Extraction pipeline for REAL app users -- the counterpart to
extraction_pipeline.py, which only ever processes the Friends-transcript
research corpus and never touches real user data. This reads
public.conversations (populated by app.py's own /analyze endpoint) and
writes cognitive memory into emotional_intelligence.facts/preferences/
beliefs/memories, scoped to a subject named "app_user:<user_id>" -- the
exact lookup key ei_adapter.py already queries for. Before this has run
for a given user, ei_adapter.py's hooks correctly find nothing; after, the
real cognitive-memory enrichment in /analyze and /friends/<id>/mood
actually has data to work with.

Unlike extraction_pipeline.py, no speaker normalization step is needed: a
conversation already belongs to exactly one account (user_id), so the
subject is known up front rather than resolved from a multi-character
cast. The prompt asks specifically for facts/preferences/beliefs/memories
about that account owner, not about other people who might be quoted in
their own transcript -- mirroring the anchoring rule already enforced in
cognitive_reasoning_demo.py (a person's own cognitive memory must come
from their own data, never borrowed from someone else's).

Usage:
    python real_user_extraction.py                  # all users, unprocessed conversations
    python real_user_extraction.py --user-id 33      # one user only
    python real_user_extraction.py --limit 5
"""
from __future__ import annotations

import argparse
import os

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from extraction_pipeline import PROMPT_VERSION, call_llm, extract_json

load_dotenv()

DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    raise SystemExit("DATABASE_URL is required")

RUN_TYPE = "real_user_extraction"

EXTRACTION_PROMPT = """You are extracting structured cognitive memory about ONE specific person -- the owner of this conversation transcript -- from a snippet of their own recorded conversation. The transcript may include other people speaking too, but you must ONLY extract information about the transcript owner named below, never about the other speakers -- their own transcripts get processed separately for their own memory.

Transcript owner: {owner_label}

From what the owner says, does, or reveals about themselves, extract:
- facts: durable factual statements about the owner (predicate/object pairs) -- job, plans, relationships, possessions.
- preferences: recurring tastes/likes attributed to the owner (category/item), NOT one-off mentions.
- beliefs: opinions or beliefs the owner expresses (topic/belief).
- memories: notable one-time events involving the owner, each with an emotion and importance (0.0-1.0).

Only extract what's actually said or clearly implied -- do not invent. Return an empty list for any category with nothing that qualifies.

Transcript:
{transcript}

Reply with ONLY this JSON, no preamble/fences:
{{"facts": [{{"predicate": "", "object": "", "confidence": 0.8}}], "preferences": [{{"category": "", "item": "", "weight": 0.8}}], "beliefs": [{{"topic": "", "belief": "", "confidence": 0.8}}], "memories": [{{"summary": "", "emotion": "", "importance": 0.8}}]}}"""


def ensure_migration(cur) -> None:
    """One-time additive column so this pipeline can tell which
    conversations it has already processed -- extraction_pipeline.py gets
    the equivalent resumability for free from episode_id, but a real
    conversation has no such natural per-episode identity to key off."""
    cur.execute("ALTER TABLE emotional_intelligence.analysis_runs ADD COLUMN IF NOT EXISTS source_ref TEXT")


def ensure_user_subject(cur, user_id: int) -> int:
    """Resolves to an explicit app_user_subject_links row if one exists --
    an opt-in merge into a pre-existing subject (e.g. a test account
    deliberately linked to its namesake Friends-TV subject) -- otherwise
    falls back to the namespaced app_user:<id> subject, creating it if
    needed. See ei_adapter.py's docstring for why this is never automatic
    by name."""
    cur.execute(
        "CREATE TABLE IF NOT EXISTS emotional_intelligence.app_user_subject_links ("
        "user_id INTEGER PRIMARY KEY, "
        "subject_id INTEGER NOT NULL REFERENCES emotional_intelligence.subjects (id) ON DELETE CASCADE, "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT now())"
    )
    cur.execute(
        "SELECT subject_id FROM emotional_intelligence.app_user_subject_links WHERE user_id = %s",
        (user_id,),
    )
    row = cur.fetchone()
    if row:
        return row["subject_id"]

    key = f"app_user:{user_id}"
    cur.execute("SELECT id FROM emotional_intelligence.subjects WHERE canonical_name = %s", (key,))
    row = cur.fetchone()
    if row:
        return row["id"]
    cur.execute(
        "INSERT INTO emotional_intelligence.subjects (canonical_name, source) VALUES (%s, 'app_user') RETURNING id",
        (key,),
    )
    return cur.fetchone()["id"]


def fetch_unprocessed_conversations(cur, user_id: int | None, limit: int) -> list[dict]:
    cur.execute(
        """
        SELECT c.id, c.user_id, u.username, c.raw_transcript
        FROM conversations c
        JOIN users u ON u.id = c.user_id
        WHERE (%(user_id)s IS NULL OR c.user_id = %(user_id)s)
          AND c.raw_transcript IS NOT NULL AND length(trim(c.raw_transcript)) > 0
          AND NOT EXISTS (
              SELECT 1 FROM emotional_intelligence.analysis_runs r
              WHERE r.run_type = %(run_type)s AND r.status = 'success'
                AND r.source_ref = 'conversations:' || c.id
          )
        ORDER BY c.created_at
        LIMIT %(limit)s
        """,
        {"user_id": user_id, "run_type": RUN_TYPE, "limit": limit or None},
    )
    return cur.fetchall()


def process_conversation(cur, convo: dict, subject_id: int) -> dict:
    prompt = EXTRACTION_PROMPT.format(owner_label=convo["username"], transcript=convo["raw_transcript"][:8000])
    try:
        content, raw_response = call_llm([{"role": "user", "content": prompt}])
    except Exception as e:
        return {"status": "error", "error": f"LLM call failed: {e!r}"}

    parsed = extract_json(content)
    if parsed is None:
        return {"status": "error", "error": "unparseable LLM response"}

    tokens = (raw_response.get("prompt_eval_count") or 0) + (raw_response.get("eval_count") or 0)
    cur.execute(
        """
        INSERT INTO emotional_intelligence.analysis_runs
            (subject_id, run_type, model, prompt_version, tokens, status, source_ref)
        VALUES (%s, %s, %s, %s, %s, 'success', %s)
        RETURNING id
        """,
        (subject_id, RUN_TYPE, raw_response.get("model"), PROMPT_VERSION, tokens or None,
         f"conversations:{convo['id']}"),
    )
    run_id = cur.fetchone()["id"]

    facts = [f for f in (parsed.get("facts") or []) if isinstance(f, dict) and (f.get("predicate") or "").strip()]
    for f in facts:
        cur.execute(
            """
            INSERT INTO emotional_intelligence.facts
                (subject_id, analysis_run_id, subject, predicate, object, confidence, source)
            VALUES (%s, %s, %s, %s, %s, %s, 'app_conversation')
            """,
            (subject_id, run_id, convo["username"], f["predicate"], f.get("object") or "", f.get("confidence")),
        )

    preferences = [p for p in (parsed.get("preferences") or []) if isinstance(p, dict) and (p.get("item") or "").strip()]
    for p in preferences:
        cur.execute(
            """
            INSERT INTO emotional_intelligence.preferences (subject_id, analysis_run_id, category, item, weight, confidence)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (subject_id, category, item) DO UPDATE SET
                weight = GREATEST(COALESCE(preferences.weight, 0), COALESCE(EXCLUDED.weight, 0)),
                confidence = GREATEST(COALESCE(preferences.confidence, 0), COALESCE(EXCLUDED.confidence, 0)),
                updated_at = now()
            """,
            (subject_id, run_id, p.get("category") or "general", p["item"], p.get("weight"), p.get("confidence")),
        )

    beliefs = [b for b in (parsed.get("beliefs") or []) if isinstance(b, dict) and (b.get("belief") or "").strip()]
    for b in beliefs:
        cur.execute(
            "INSERT INTO emotional_intelligence.beliefs (subject_id, analysis_run_id, topic, belief, confidence) "
            "VALUES (%s, %s, %s, %s, %s)",
            (subject_id, run_id, b.get("topic") or "general", b["belief"], b.get("confidence")),
        )

    memories = [m for m in (parsed.get("memories") or []) if isinstance(m, dict) and (m.get("summary") or "").strip()]
    for m in memories:
        cur.execute(
            "INSERT INTO emotional_intelligence.memories (subject_id, analysis_run_id, summary, importance, emotion) "
            "VALUES (%s, %s, %s, %s, %s)",
            (subject_id, run_id, m["summary"], m.get("importance"), m.get("emotion")),
        )

    return {"status": "success", "facts": len(facts), "preferences": len(preferences),
            "beliefs": len(beliefs), "memories": len(memories)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract cognitive memory for real app users from their own conversations.")
    parser.add_argument("--user-id", type=int, default=None, help="Process only this user (default: all users).")
    parser.add_argument("--limit", type=int, default=0, help="Max conversations to process (0 = all unprocessed).")
    args = parser.parse_args()

    conn = psycopg2.connect(DB_URL)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        ensure_migration(cur)
        conn.commit()

        conversations = fetch_unprocessed_conversations(cur, args.user_id, args.limit)
        print(f"conversations_to_process={len(conversations)}")

        succeeded, failed = 0, 0
        for convo in conversations:
            subject_id = ensure_user_subject(cur, convo["user_id"])
            conn.commit()  # subject creation stands even if extraction below fails
            print(f"--- conversation {convo['id']} ({convo['username']}) ---")
            try:
                result = process_conversation(cur, convo, subject_id)
                if result["status"] != "success":
                    conn.rollback()
                    failed += 1
                    print(f"  FAILED: {result.get('error')}")
                    continue
                conn.commit()
                succeeded += 1
                print(f"  OK facts={result['facts']} preferences={result['preferences']} "
                      f"beliefs={result['beliefs']} memories={result['memories']}")
            except Exception as e:
                conn.rollback()
                failed += 1
                print(f"  FAILED (exception): {e!r}")

        print(f"\nDone. succeeded={succeeded} failed={failed}")
    finally:
        cur.close()
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
