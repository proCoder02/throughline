"""
Feedback-loop extraction: reads a real user's own CHAT messages (not their
dictated conversations -- that's real_user_extraction.py's job) looking
for new facts or corrections stated during actual back-and-forth chat.
Without this, a correction typed into chat ("actually Emma is my niece,
not my daughter") is never captured anywhere -- the pipeline only ever
learns from dictated monologues, never from the conversation it has with
the user itself.

Read-only against public.chat_messages; writes only into the
emotional_intelligence schema, via the exact same tables/shape
real_user_extraction.py already writes to (reuses its ensure_user_subject
so both scripts resolve the same subject for a given user_id). Deliberately
does NOT touch app.py or any public-schema table.

Most chat turns are questions or small talk, not new information -- the
prompt is written to return empty far more often than not.

Corrections are resolved HERE, directly, not deferred to fact_dedup.py.
That was the first design (extract blindly, let a later bulk dedup pass
notice the contradiction) -- tested against a real correction ("Emma is my
niece, not my daughter") and it failed twice, even after cleaning up the
extraction's predicate/object shape: fact_dedup.py clusters ~100 facts at
once per subject, and reliably missed that a new "relationship_to_emma:
niece" fact should replace the old "has_child: Emma" one, sitting among a
hundred unrelated others. So instead: before extracting, fetch this
subject's existing facts most relevant to the new messages (the same
full-text search ei_adapter.py's _search_table uses), hand the LLM those
specific candidates by id, and let it mark supersedes_fact_id directly --
a much smaller, targeted decision than bulk clustering, and one that
resolves the correction immediately instead of hoping a later pass finds
it. fact_dedup.py still runs in the daily pipeline for the OTHER thing it
does well (consolidating near-duplicate wordings across many observations,
proven on the Friends corpus) -- this doesn't replace that, it just stops
relying on it for corrections specifically, which it wasn't reliable at.

Only ever processes a user's OWN messages (role='user') -- the assistant's
own replies never contain facts about the user, and mixing them in would
risk the LLM extracting a "fact" from something the assistant said instead
of the user.

Usage:
    python chat_feedback_extraction.py                # all users, all new chat messages
    python chat_feedback_extraction.py --user-id 33    # one user only
"""
from __future__ import annotations

import argparse
import os

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from ei_adapter import NOT_EXPIRED, _search_table
from extraction_pipeline import PROMPT_VERSION, call_llm, extract_json
from real_user_extraction import ensure_user_subject

load_dotenv()

DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    raise SystemExit("DATABASE_URL is required")

RUN_TYPE = "chat_feedback_extraction"
MAX_MESSAGES_PER_BATCH = 40  # per user per run -- keeps one call's cost bounded

CHAT_FEEDBACK_PROMPT = """You are reviewing a batch of a user's own chat messages to their personal assistant, looking ONLY for new information or corrections about the user themselves -- not requests, questions, or small talk, which make up most chat messages and should produce nothing.

Extract an item ONLY if the user is genuinely stating something new or correcting something about themselves -- e.g. "actually Emma is my niece, not my daughter" (a correction), "I just started a new job at Google" (a new fact), "I've been getting really into rock climbing lately" (a new preference). Do NOT extract from ordinary questions ("what should I buy for Emma?"), requests ("suggest me some tv shows"), greetings, or anything that isn't a genuine statement about the user's own life.

When in doubt, extract NOTHING -- most messages contain nothing worth extracting, and it is much better to return empty than to invent a fact out of a question.

Some of this person's EXISTING facts are listed below, with their real ids. If a message corrects or contradicts one of these, set "supersedes_fact_id" to that exact id and write the corrected predicate/object as the replacement. If a fact is genuinely new and doesn't correct anything listed, leave "supersedes_fact_id" null. NEVER invent an id that isn't listed below.

Existing facts (id: predicate = object):
{existing_facts}

The user's own chat messages, oldest first:
{messages}

Extract:
- facts: durable factual statements or corrections about the user, each {{"predicate": "", "object": "", "confidence": 0.8, "supersedes_fact_id": null}}. The predicate must be a short attribute name ("relationship_to_emma", "has_niece", "works_at"), never a full sentence. NEVER use a bare relative time word ("today"/"tomorrow"/"tonight"/"yesterday") as the object -- a fact is read back as still true indefinitely.
- preferences: recurring tastes/likes the user states about themselves (category/item), NOT one-off mentions.
- beliefs: opinions or beliefs the user expresses (topic/belief).
- memories: notable one-time events the user mentions happening to them, each with an emotion and importance (0.0-1.0).

Reply with ONLY this JSON, no preamble/fences:
{{"facts": [{{"predicate": "", "object": "", "confidence": 0.8, "supersedes_fact_id": null}}], "preferences": [{{"category": "", "item": "", "weight": 0.8}}], "beliefs": [{{"topic": "", "belief": "", "confidence": 0.8}}], "memories": [{{"summary": "", "emotion": "", "importance": 0.8}}]}}
Empty lists if nothing genuinely qualifies -- this will be true for most batches."""


def fetch_users_with_new_messages(cur, user_id: int | None) -> list[dict]:
    """Real users (from public.users) with at least one chat message newer
    than their last successful chat_feedback_extraction run for that
    user's subject -- a timestamp watermark, same shape as personality_
    batch.py's re-snapshot check, rather than per-message tracking."""
    cur.execute(
        """
        SELECT DISTINCT cm.user_id
        FROM chat_messages cm
        WHERE cm.role = 'user' AND (%(user_id)s IS NULL OR cm.user_id = %(user_id)s)
        """,
        {"user_id": user_id},
    )
    return [row["user_id"] for row in cur.fetchall()]


def fetch_new_messages(cur, user_id: int, subject_id: int) -> list[dict]:
    cur.execute(
        """
        SELECT MAX(created_at) AS last_at FROM emotional_intelligence.analysis_runs
        WHERE subject_id = %s AND run_type = %s AND status = 'success'
        """,
        (subject_id, RUN_TYPE),
    )
    watermark = cur.fetchone()["last_at"]

    cur.execute(
        """
        SELECT id, content, created_at FROM chat_messages
        WHERE user_id = %s AND role = 'user' AND (%s IS NULL OR created_at > %s)
        ORDER BY created_at
        LIMIT %s
        """,
        (user_id, watermark, watermark, MAX_MESSAGES_PER_BATCH),
    )
    return cur.fetchall()


def process_batch(cur, subject_id: int, messages: list[dict], run_type: str = RUN_TYPE) -> dict:
    """run_type is overridable so the real-time trigger (ei_adapter.py's
    trigger_chat_feedback_extraction, fired right after /chat responds) can
    tag its runs separately from the scheduled batch's own RUN_TYPE. They
    must stay separate: the batch script's watermark (fetch_new_messages)
    looks for the latest successful run of its OWN run_type -- if the
    real-time trigger shared that run_type and ever failed silently on one
    message, the batch's watermark would still advance past it on a LATER
    message's successful real-time run, and that failed message would be
    skipped forever. Keeping them separate means the batch always
    re-checks everything real-time already handled -- redundant (it'll
    just find nothing new the second time) but never silently incorrect."""
    joined = "\n".join(f"- {m['content']}" for m in messages if (m["content"] or "").strip())
    if not joined.strip():
        return {"status": "success", "facts": 0, "preferences": 0, "beliefs": 0, "memories": 0, "skipped": True}

    # Candidates the new messages might correct -- full-text-searched by the
    # messages themselves (reusing ei_adapter.py's shared search), the same
    # "search first, fall back to recency" shape used everywhere else in
    # this pipeline, just scoped to facts a correction could plausibly hit.
    candidates = _search_table(cur, "facts", subject_id, joined,
                                "id, predicate, object", "created_at DESC", 20,
                                tiebreak_col="confidence", extra_where=NOT_EXPIRED)
    valid_ids = {c["id"] for c in candidates}
    existing_facts_text = "\n".join(f"id={c['id']}: {c['predicate']} = {c['object']}" for c in candidates) or "(none)"

    prompt = CHAT_FEEDBACK_PROMPT.format(existing_facts=existing_facts_text, messages=joined)
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
            (subject_id, run_type, model, prompt_version, tokens, status)
        VALUES (%s, %s, %s, %s, %s, 'success')
        RETURNING id
        """,
        (subject_id, run_type, raw_response.get("model"), PROMPT_VERSION, tokens or None),
    )
    run_id = cur.fetchone()["id"]

    facts = [f for f in (parsed.get("facts") or []) if isinstance(f, dict) and (f.get("predicate") or "").strip()]
    superseded_count = 0
    for f in facts:
        cur.execute(
            """
            INSERT INTO emotional_intelligence.facts
                (subject_id, analysis_run_id, subject, predicate, object, confidence, source)
            VALUES (%s, %s, 'user', %s, %s, %s, 'chat_message')
            RETURNING id
            """,
            (subject_id, run_id, f["predicate"], f.get("object") or "", f.get("confidence")),
        )
        new_fact_id = cur.fetchone()["id"]

        # Never trust an LLM-echoed id blindly (same rule fact_dedup.py
        # follows) -- only supersede a candidate we ourselves offered it,
        # never an arbitrary id it happened to mention.
        supersedes_id = f.get("supersedes_fact_id")
        if supersedes_id is not None and supersedes_id in valid_ids:
            old = next(c for c in candidates if c["id"] == supersedes_id)
            cur.execute(
                """
                INSERT INTO emotional_intelligence.fact_versions (fact_id, analysis_run_id, subject, predicate, object)
                VALUES (%s, %s, 'user', %s, %s)
                """,
                (new_fact_id, run_id, old["predicate"], old["object"]),
            )
            cur.execute("DELETE FROM emotional_intelligence.facts WHERE id = %s", (supersedes_id,))
            superseded_count += 1

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
            "beliefs": len(beliefs), "memories": len(memories), "superseded": superseded_count}


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract facts/corrections from real users' own chat messages.")
    parser.add_argument("--user-id", type=int, default=None, help="Process only this user (default: all users with chat activity).")
    args = parser.parse_args()

    conn = psycopg2.connect(DB_URL)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        user_ids = fetch_users_with_new_messages(cur, args.user_id)
        print(f"users_to_check={len(user_ids)}")

        succeeded, failed, skipped = 0, 0, 0
        for user_id in user_ids:
            subject_id = ensure_user_subject(cur, user_id)
            conn.commit()

            messages = fetch_new_messages(cur, user_id, subject_id)
            if not messages:
                continue

            print(f"--- user {user_id} ({len(messages)} new chat messages) ---")
            try:
                result = process_batch(cur, subject_id, messages)
                if result["status"] != "success":
                    conn.rollback()
                    failed += 1
                    print(f"  FAILED: {result.get('error')}")
                    continue
                conn.commit()
                if result.get("skipped"):
                    skipped += 1
                    print("  skipped (no non-empty message content)")
                else:
                    succeeded += 1
                    print(f"  OK facts={result['facts']} (superseded={result['superseded']}) "
                          f"preferences={result['preferences']} beliefs={result['beliefs']} memories={result['memories']}")
            except Exception as e:
                conn.rollback()
                failed += 1
                print(f"  FAILED (exception): {e!r}")

        print(f"\nDone. succeeded={succeeded} failed={failed} skipped={skipped}")
    finally:
        cur.close()
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
