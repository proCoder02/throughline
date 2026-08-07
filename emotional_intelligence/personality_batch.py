"""
personality_snapshots batch job -- Phase 9 of COGNITIVE_EXTRACTION_PIPELINE_PLAN.md,
adapted to subjects. Does NOT fit the per-episode extraction schema (Big Five
traits aren't reliably inferable from one episode) -- this looks at each
qualifying subject's FULL accumulated memory (facts/preferences/beliefs/
memories across the whole corpus) and produces one snapshot per subject.

Scope: only subjects with >= MIN_DATA_POINTS combined facts+beliefs+memories
(calibrated against the actual corpus: 50 subjects qualify at the default
threshold of 5, dominated by the 6 main cast plus real recurring characters
-- thin-data subjects would just produce an unreliable/noisy estimate).

Re-snapshots over time, not just once: a subject with a prior successful
snapshot re-qualifies once >= MIN_NEW_DATA new facts/beliefs/memories have
accumulated since that snapshot's timestamp, producing a genuine timeline
(personality_snapshots has no unique constraint on subject_id and is
indexed (subject_id, created_at DESC) specifically for this -- the schema
already anticipated multiple snapshots per subject, this was the first
script to actually produce more than one). A subject who's never been
snapshotted still only needs MIN_DATA_POINTS total, same as before.

Usage:
    python personality_batch.py
    python personality_batch.py --min-data 10   # narrower scope
    python personality_batch.py --min-new-data 10   # require more new data before re-snapshotting
"""
from __future__ import annotations

import argparse
import os

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from cognitive_reasoning_demo import assemble_cognitive_memory
from extraction_pipeline import PROMPT_VERSION, call_llm, extract_json

load_dotenv()

DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    raise SystemExit("DATABASE_URL is required")

PERSONALITY_PROMPT = """You are a psychologist estimating Big Five (OCEAN) personality traits for a person, using ONLY the structured cognitive memory provided below (facts, preferences, beliefs, notable memories accumulated across many observations) -- not any external knowledge about who this person might be.

Score each trait 0.0-1.0 (0 = very low, 1 = very high), calibrated the way a trained psychologist would from behavioral evidence:
- openness: curiosity, creativity, openness to new experiences
- conscientiousness: organization, reliability, self-discipline
- extraversion: sociability, assertiveness, energy from social situations
- agreeableness: warmth, cooperation, empathy toward others
- neuroticism: emotional volatility, anxiety, stress reactivity

Also give an overall "confidence" (0.0-1.0) reflecting how much evidence the provided memory actually contains -- low confidence if the memory is thin or one-sided, high confidence only if there's substantial, varied evidence across many observations.

Reply with ONLY this JSON, no preamble/fences:
{"openness": 0.5, "conscientiousness": 0.5, "extraversion": 0.5, "agreeableness": 0.5, "neuroticism": 0.5, "confidence": 0.5}"""


def fetch_qualifying_subjects(cur, min_data: int, min_new_data: int, limit: int) -> list[dict]:
    cur.execute(
        """
        WITH last_snapshot AS (
            SELECT subject_id, MAX(created_at) AS last_at
            FROM emotional_intelligence.analysis_runs
            WHERE run_type = 'personality_batch' AND status = 'success'
            GROUP BY subject_id
        ),
        subject_totals AS (
            SELECT s.id, s.canonical_name, ls.last_at,
                (SELECT count(*) FROM emotional_intelligence.facts f WHERE f.subject_id=s.id) +
                (SELECT count(*) FROM emotional_intelligence.beliefs b WHERE b.subject_id=s.id) +
                (SELECT count(*) FROM emotional_intelligence.memories m WHERE m.subject_id=s.id) AS total,
                (SELECT count(*) FROM emotional_intelligence.facts f WHERE f.subject_id=s.id AND (ls.last_at IS NULL OR f.created_at > ls.last_at)) +
                (SELECT count(*) FROM emotional_intelligence.beliefs b WHERE b.subject_id=s.id AND (ls.last_at IS NULL OR b.created_at > ls.last_at)) +
                (SELECT count(*) FROM emotional_intelligence.memories m WHERE m.subject_id=s.id AND (ls.last_at IS NULL OR m.created_at > ls.last_at)) AS new_since_last
            FROM emotional_intelligence.subjects s
            LEFT JOIN last_snapshot ls ON ls.subject_id = s.id
        )
        SELECT id, canonical_name, total, last_at, new_since_last FROM subject_totals
        WHERE total >= %s AND (last_at IS NULL OR new_since_last >= %s)
        ORDER BY (last_at IS NULL) DESC, new_since_last DESC
        """ + (f" LIMIT {int(limit)}" if limit else ""),
        (min_data, min_new_data),
    )
    return [dict(row) for row in cur.fetchall()]


def process_subject(cur, subject: dict) -> dict:
    memory_block, stats = assemble_cognitive_memory(cur, subject["canonical_name"])
    messages = [
        {"role": "system", "content": PERSONALITY_PROMPT},
        {"role": "user", "content": memory_block},
    ]
    try:
        content, raw_response = call_llm(messages)
    except Exception as e:
        return {"status": "error", "error": f"LLM call failed: {e!r}"}

    parsed = extract_json(content)
    if not parsed:
        return {"status": "error", "error": "unparseable LLM response"}

    tokens = (raw_response.get("prompt_eval_count") or 0) + (raw_response.get("eval_count") or 0)
    cur.execute(
        """
        INSERT INTO emotional_intelligence.analysis_runs
            (subject_id, run_type, model, prompt_version, tokens, status)
        VALUES (%s, 'personality_batch', %s, %s, %s, 'success')
        RETURNING id
        """,
        (subject["id"], raw_response.get("model"), PROMPT_VERSION, tokens or None),
    )
    run_id = cur.fetchone()["id"]

    cur.execute(
        """
        INSERT INTO emotional_intelligence.personality_snapshots
            (subject_id, analysis_run_id, openness, conscientiousness, extraversion, agreeableness, neuroticism, confidence)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            subject["id"], run_id,
            parsed.get("openness"), parsed.get("conscientiousness"), parsed.get("extraversion"),
            parsed.get("agreeableness"), parsed.get("neuroticism"), parsed.get("confidence"),
        ),
    )
    return {"status": "success", "run_id": run_id, "data_points": subject["total"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate personality_snapshots for qualifying subjects.")
    parser.add_argument("--min-data", type=int, default=5, help="Minimum combined facts+beliefs+memories to qualify a first snapshot.")
    parser.add_argument("--min-new-data", type=int, default=5, help="Minimum NEW facts+beliefs+memories since the last snapshot to qualify a re-snapshot.")
    parser.add_argument("--limit", type=int, default=0, help="Max subjects to process (0 = all qualifying).")
    args = parser.parse_args()

    conn = psycopg2.connect(DB_URL)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        subjects = fetch_qualifying_subjects(cur, args.min_data, args.min_new_data, args.limit)
        print(f"subjects_to_process={len(subjects)}")

        succeeded, failed = 0, 0
        for subj in subjects:
            kind = "first snapshot" if subj["last_at"] is None else f"re-snapshot, {subj['new_since_last']} new since last"
            print(f"--- {subj['canonical_name']} (data_points={subj['total']}, {kind}) ---")
            try:
                result = process_subject(cur, subj)
                if result["status"] == "success":
                    conn.commit()
                    succeeded += 1
                    print(f"  OK run_id={result['run_id']}")
                else:
                    conn.rollback()
                    failed += 1
                    cur.execute(
                        "INSERT INTO emotional_intelligence.analysis_runs "
                        "(subject_id, run_type, prompt_version, status, error) "
                        "VALUES (%s, 'personality_batch', %s, 'error', %s)",
                        (subj["id"], PROMPT_VERSION, result.get("error")),
                    )
                    conn.commit()
                    print(f"  FAILED: {result.get('error')}")
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
