"""
fact_versions consolidation pass -- Phase 7 Step 2 of
COGNITIVE_EXTRACTION_PIPELINE_PLAN.md ("only after reviewing Step 1's real
output"). Step 1 (naive insert, no dedup) is done: 1,484 facts across 227
episodes. Inspecting that real output showed exact-match on
(subject_id, predicate) barely helps -- the LLM used wildly inconsistent
predicate vocabulary for the same underlying fact (Ross alone: works_at,
profession, occupation, works_as all describing the same kind of fact,
156 distinct predicates across 233 facts total). It also showed genuine
character development mixed in with the noise (Chandler's job changing
over the show's run) -- exact-match or plain similarity matching can't
tell those apart; that needs judgment, which is why this is an LLM
consolidation pass per subject, not embeddings or string matching.

For each qualifying subject, sends ALL their current facts (with real ids)
in one call and asks the LLM to cluster them: near-duplicate wordings of
the same current fact get merged into one canonical fact; facts
representing genuine change over time get the earlier one marked
superseded (moved to fact_versions) rather than merged away. Facts with no
overlap are left untouched.

This is a one-time/periodic maintenance pass over already-extracted data,
not a per-insert dedup check (predicate inconsistency means a single new
fact can't be reliably matched against history without seeing the whole
set at once).

Usage:
    python fact_dedup.py
    python fact_dedup.py --min-facts 10 --limit 2   # test on 2 subjects first
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

DEDUP_PROMPT = """You are consolidating a list of facts recorded about ONE person, gathered independently across many separate observations (different episodes). Some are duplicate or near-duplicate wordings of the same current fact (e.g. "works_at: office" and "occupation: works in an office" said at different times but describing the same ongoing reality). Others represent a GENUINE CHANGE over time (e.g. a job change, a breakup, a new address) -- those must NOT be merged together as if they were the same fact; keep the most current one as canonical and mark the earlier one as superseded.

Each fact below has a real id, predicate, and object. Group facts into clusters where each cluster is ONE underlying real-world fact (current or historical) about this person. Only include a cluster if it has 2+ fact ids -- facts with no overlap should not appear in your output at all.

For each cluster:
- canonical_predicate/canonical_object: the clearest, most accurate current statement of this fact (write your own concise version if the originals are all vague/inconsistent; don't just copy one verbatim if you can do better).
- canonical_confidence: your confidence in this consolidated fact (0.0-1.0).
- keep_fact_id: which ORIGINAL fact id best represents the current/most-recent state (its row will be updated to the canonical wording).
- superseded_fact_ids: the OTHER fact ids in this cluster (their original wording gets preserved as history, not deleted).

Facts:
{facts_list}

Reply with ONLY this JSON, no preamble/fences:
{{"clusters": [{{"canonical_predicate": "", "canonical_object": "", "canonical_confidence": 0.8, "keep_fact_id": 0, "superseded_fact_ids": []}}]}}
Empty "clusters" list if nothing overlaps."""


def fetch_qualifying_subjects(cur, min_facts: int, subject_name: str | None = None) -> list[dict]:
    cur.execute(
        """
        SELECT s.id, s.canonical_name, count(f.id) AS fact_count
        FROM emotional_intelligence.subjects s
        JOIN emotional_intelligence.facts f ON f.subject_id = s.id
        WHERE (%s IS NULL OR lower(s.canonical_name) = lower(%s))
        GROUP BY s.id, s.canonical_name
        HAVING count(f.id) >= %s
        ORDER BY fact_count DESC
        """,
        (subject_name, subject_name, min_facts),
    )
    return [dict(row) for row in cur.fetchall()]


def process_subject(cur, subject: dict) -> dict:
    cur.execute(
        "SELECT id, predicate, object, confidence FROM emotional_intelligence.facts WHERE subject_id=%s ORDER BY id",
        (subject["id"],),
    )
    facts = cur.fetchall()
    valid_ids = {f["id"] for f in facts}
    facts_list = "\n".join(f"id={f['id']}: {f['predicate']} = {f['object']} (confidence={f['confidence']})" for f in facts)

    messages = [{"role": "user", "content": DEDUP_PROMPT.format(facts_list=facts_list)}]
    try:
        content, raw_response = call_llm(messages)
    except Exception as e:
        return {"status": "error", "error": f"LLM call failed: {e!r}"}

    parsed = extract_json(content)
    if not parsed:
        return {"status": "error", "error": "unparseable LLM response"}

    clusters = parsed.get("clusters") or []
    tokens = (raw_response.get("prompt_eval_count") or 0) + (raw_response.get("eval_count") or 0)
    cur.execute(
        """
        INSERT INTO emotional_intelligence.analysis_runs (subject_id, run_type, model, prompt_version, tokens, status)
        VALUES (%s, 'fact_dedup', %s, %s, %s, 'success')
        RETURNING id
        """,
        (subject["id"], raw_response.get("model"), PROMPT_VERSION, tokens or None),
    )
    run_id = cur.fetchone()["id"]

    merged_count, superseded_count, skipped_invalid = 0, 0, 0
    facts_by_id = {f["id"]: f for f in facts}
    for cluster in clusters:
        keep_id = cluster.get("keep_fact_id")
        superseded_ids = [i for i in (cluster.get("superseded_fact_ids") or []) if i != keep_id]
        # Safety check: only act on ids that actually exist for this subject -- never trust
        # LLM-echoed ids blindly (a hallucinated or cross-subject id here would corrupt data).
        if keep_id not in valid_ids or not all(i in valid_ids for i in superseded_ids) or not superseded_ids:
            skipped_invalid += 1
            continue

        for old_id in superseded_ids:
            old = facts_by_id[old_id]
            cur.execute(
                """
                INSERT INTO emotional_intelligence.fact_versions (fact_id, analysis_run_id, subject, predicate, object, confidence)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (keep_id, run_id, subject["canonical_name"], old["predicate"], old["object"], old["confidence"]),
            )
            cur.execute("DELETE FROM emotional_intelligence.facts WHERE id=%s", (old_id,))
            superseded_count += 1

        cur.execute(
            "UPDATE emotional_intelligence.facts SET predicate=%s, object=%s, confidence=%s, analysis_run_id=%s WHERE id=%s",
            (cluster.get("canonical_predicate"), cluster.get("canonical_object"), cluster.get("canonical_confidence"), run_id, keep_id),
        )
        merged_count += 1

    return {
        "status": "success", "run_id": run_id, "clusters_found": len(clusters),
        "merged": merged_count, "facts_superseded": superseded_count, "skipped_invalid": skipped_invalid,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Consolidate duplicate/superseded facts per subject.")
    parser.add_argument("--min-facts", type=int, default=5, help="Minimum fact count to qualify a subject.")
    parser.add_argument("--limit", type=int, default=0, help="Max subjects to process (0 = all qualifying).")
    parser.add_argument("--subject", default=None, help="Process only this canonical subject name (for targeted re-runs).")
    parser.add_argument("--max-passes", type=int, default=3,
                         help="Repeat clustering per subject until it finds 0 new clusters or this cap is hit -- "
                              "a single pass over a large/noisy fact set reliably under-merges (observed: Ross's "
                              "233 facts needed 2 passes to fully consolidate a single job-related cluster).")
    args = parser.parse_args()

    conn = psycopg2.connect(DB_URL)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        subjects = fetch_qualifying_subjects(cur, args.min_facts, args.subject)
        if args.limit:
            subjects = subjects[: args.limit]
        print(f"subjects_to_process={len(subjects)}")

        succeeded, failed = 0, 0
        total_before = sum(s["fact_count"] for s in subjects)
        total_superseded = 0
        for subj in subjects:
            subj_superseded = 0
            any_pass_failed = False
            for pass_num in range(1, args.max_passes + 1):
                print(f"--- {subj['canonical_name']} pass {pass_num} (facts={subj['fact_count']}) ---")
                try:
                    result = process_subject(cur, subj)
                    if result["status"] != "success":
                        conn.rollback()
                        any_pass_failed = True
                        print(f"  FAILED: {result.get('error')}")
                        break
                    conn.commit()
                    subj_superseded += result["facts_superseded"]
                    print(f"  OK clusters={result['clusters_found']} merged={result['merged']} "
                          f"facts_superseded={result['facts_superseded']} skipped_invalid={result['skipped_invalid']}")
                    if result["clusters_found"] == 0:
                        break  # converged -- nothing left to merge this round
                    # Refresh fact_count for the next pass's log line / qualification check.
                    cur.execute("SELECT count(*) AS n FROM emotional_intelligence.facts WHERE subject_id=%s", (subj["id"],))
                    subj["fact_count"] = cur.fetchone()["n"]
                except Exception as e:
                    conn.rollback()
                    any_pass_failed = True
                    print(f"  FAILED (exception): {e!r}")
                    break

            if any_pass_failed:
                failed += 1
            else:
                succeeded += 1
            total_superseded += subj_superseded

        print(f"\nDone. succeeded={succeeded} failed={failed} "
              f"total_facts_before={total_before} total_superseded_into_history={total_superseded}")
    finally:
        cur.close()
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
