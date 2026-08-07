"""
relationship_profiles batch job -- Phase 10 of COGNITIVE_EXTRACTION_PIPELINE_PLAN.md,
adapted to subject pairs. Per that plan's own recommendation: compute
what's mechanically countable directly from data (communication_frequency,
shared_topics) rather than asking the LLM to guess a number SQL can already
produce exactly; reserve the LLM only for genuinely qualitative judgment
(trust_score, conflict_score, emotional_support, relationship_summary).

Scope: pairs of subjects (both meeting the personality_batch data-volume
threshold) who co-occur in >= MIN_SHARED_EPISODES episodes together
(calibrated against the actual corpus: 73 pairs at the default threshold of
10 -- narrows to the main cast plus genuinely substantial recurring
relationships, not one-scene overlaps).

Reuses assemble_multi_subject_memory() (built for the chat's cross-subject
questions) so the LLM's qualitative assessment is grounded in exactly the
same per-person data the chat already reasons from -- same anchoring rules
apply (REASONING_SYSTEM_PROMPT's caveats about not inventing shared history
don't apply here since this prompt is separate, but the same discipline of
"ground it in what's actually there" is carried over).

Re-assesses over time, not just once: a pair already in relationship_profiles
re-qualifies once >= MIN_NEW_DATA new facts/beliefs/memories have
accumulated for either subject since that pair's last assessment. Unlike
personality_snapshots, relationship_profiles has a UNIQUE(subject_a,
subject_b) constraint -- it holds one current-state row per pair, not a
timeline -- so a re-assessment UPDATEs that row (refreshing trust/conflict/
emotional_support/communication_frequency/shared_topics/updated_at) rather
than inserting a new one. A genuine per-pair history would need a schema
change (e.g. a relationship_profile_versions table, mirroring
fact_versions); out of scope here since other code (ei_adapter.py's
get_friend_relationship_insight) reads this table expecting exactly one
row per pair.

Usage:
    python relationship_batch.py
    python relationship_batch.py --min-shared-episodes 15   # narrower scope
    python relationship_batch.py --min-new-data 10          # require more new data before refreshing
"""
from __future__ import annotations

import argparse
import os

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from cognitive_reasoning_demo import assemble_multi_subject_memory
from extraction_pipeline import PROMPT_VERSION, call_llm, extract_json

load_dotenv()

DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    raise SystemExit("DATABASE_URL is required")

RELATIONSHIP_PROMPT = """You are assessing the relationship between two people, using ONLY the individual cognitive memory provided for each of them below (each person's own facts, preferences, beliefs, and notable memories) -- not any external knowledge, not assumptions.

Ground every score in what's actually present. If one or both people's memory says little about the other, reflect that with lower scores/confidence rather than inventing depth that isn't there.

Score 0.0-1.0 each:
- trust_score: how much these two people seem to trust/rely on each other, based on what's stated
- conflict_score: how much friction/disagreement appears between them
- emotional_support: how much they appear to emotionally support one another

Also write a 1-2 sentence "relationship_summary" grounded strictly in the provided memory -- if there's minimal direct evidence of their relationship specifically (as opposed to just both being in the same social circle), say so explicitly in the summary rather than inventing closeness.

Reply with ONLY this JSON, no preamble/fences:
{"trust_score": 0.5, "conflict_score": 0.5, "emotional_support": 0.5, "relationship_summary": ""}"""


def fetch_subject_episode_sets(cur) -> dict[int, set[str]]:
    """subject_id -> set of episode_id (text) they actually appear/speak in,
    via the speaker_aliases normalization -- used for co-occurrence, a
    mechanically countable fact, not an LLM guess."""
    cur.execute(
        """
        SELECT sa.subject_id, est.episode_id
        FROM emotional_intelligence.speaker_aliases sa
        JOIN emotional_intelligence.episode_speaker_transcript est ON est.speaker_name = sa.raw_speaker_name
        GROUP BY sa.subject_id, est.episode_id
        """
    )
    result: dict[int, set[str]] = {}
    for row in cur.fetchall():
        result.setdefault(row["subject_id"], set()).add(row["episode_id"])
    return result


def fetch_qualifying_subjects(cur, min_data: int) -> list[dict]:
    cur.execute(
        """
        SELECT s.id, s.canonical_name,
            (SELECT count(*) FROM emotional_intelligence.facts f WHERE f.subject_id=s.id) +
            (SELECT count(*) FROM emotional_intelligence.beliefs b WHERE b.subject_id=s.id) +
            (SELECT count(*) FROM emotional_intelligence.memories m WHERE m.subject_id=s.id) AS total
        FROM emotional_intelligence.subjects s
        """
    )
    return [dict(row) for row in cur.fetchall() if row["total"] >= min_data]


def fetch_existing_pairs(cur) -> dict[tuple[int, int], object]:
    """(subject_a, subject_b) -> when that pair was last assessed (via the
    linked analysis_run's created_at), so main() can tell a genuinely new
    pair apart from one that's just due for a refresh."""
    cur.execute(
        """
        SELECT rp.subject_a, rp.subject_b, r.created_at
        FROM emotional_intelligence.relationship_profiles rp
        LEFT JOIN emotional_intelligence.analysis_runs r ON r.id = rp.analysis_run_id
        """
    )
    return {(row["subject_a"], row["subject_b"]): row["created_at"] for row in cur.fetchall()}


def count_new_data_since(cur, subject_id: int, since) -> int:
    if since is None:
        return 0
    cur.execute(
        """
        SELECT
            (SELECT count(*) FROM emotional_intelligence.facts f WHERE f.subject_id=%(id)s AND f.created_at > %(since)s) +
            (SELECT count(*) FROM emotional_intelligence.beliefs b WHERE b.subject_id=%(id)s AND b.created_at > %(since)s) +
            (SELECT count(*) FROM emotional_intelligence.memories m WHERE m.subject_id=%(id)s AND m.created_at > %(since)s) AS n
        """,
        {"id": subject_id, "since": since},
    )
    return cur.fetchone()["n"]


def compute_shared_topics(cur, episode_ids_text: set[str], limit: int = 5) -> list[str]:
    if not episode_ids_text:
        return []
    cur.execute(
        """
        SELECT t.name, count(*) AS n
        FROM emotional_intelligence.episode_topics et
        JOIN emotional_intelligence.topics t ON t.id = et.topic_id
        JOIN emotional_intelligence.episodes e ON e.id = et.episode_id
        WHERE e.episode_id = ANY(%s)
        GROUP BY t.name
        ORDER BY n DESC
        LIMIT %s
        """,
        (list(episode_ids_text), limit),
    )
    return [row["name"] for row in cur.fetchall()]


def process_pair(cur, subject_a: dict, subject_b: dict, shared_episode_ids: set[str], total_episodes: int) -> dict:
    memory_block, stats = assemble_multi_subject_memory(cur, [subject_a["canonical_name"], subject_b["canonical_name"]])
    messages = [
        {"role": "system", "content": RELATIONSHIP_PROMPT},
        {"role": "user", "content": memory_block},
    ]
    try:
        content, raw_response = call_llm(messages)
    except Exception as e:
        return {"status": "error", "error": f"LLM call failed: {e!r}"}

    parsed = extract_json(content)
    if not parsed:
        return {"status": "error", "error": "unparseable LLM response"}

    # Mechanically computed, not LLM-guessed.
    communication_frequency = len(shared_episode_ids) / total_episodes if total_episodes else None
    shared_topics = compute_shared_topics(cur, shared_episode_ids)

    tokens = (raw_response.get("prompt_eval_count") or 0) + (raw_response.get("eval_count") or 0)
    cur.execute(
        """
        INSERT INTO emotional_intelligence.analysis_runs (run_type, model, prompt_version, tokens, status)
        VALUES ('relationship_batch', %s, %s, %s, 'success')
        RETURNING id
        """,
        (raw_response.get("model"), PROMPT_VERSION, tokens or None),
    )
    run_id = cur.fetchone()["id"]

    a, b = sorted([subject_a["id"], subject_b["id"]])  # schema CHECK (subject_a < subject_b)
    cur.execute(
        """
        INSERT INTO emotional_intelligence.relationship_profiles
            (subject_a, subject_b, analysis_run_id, trust_score, communication_frequency,
             conflict_score, emotional_support, shared_topics, relationship_summary)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (subject_a, subject_b) DO UPDATE SET
            analysis_run_id = EXCLUDED.analysis_run_id,
            trust_score = EXCLUDED.trust_score,
            communication_frequency = EXCLUDED.communication_frequency,
            conflict_score = EXCLUDED.conflict_score,
            emotional_support = EXCLUDED.emotional_support,
            shared_topics = EXCLUDED.shared_topics,
            relationship_summary = EXCLUDED.relationship_summary,
            updated_at = now()
        """,
        (
            a, b, run_id,
            parsed.get("trust_score"), communication_frequency, parsed.get("conflict_score"),
            parsed.get("emotional_support"), shared_topics, parsed.get("relationship_summary"),
        ),
    )
    return {"status": "success", "run_id": run_id, "shared_episodes": len(shared_episode_ids)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate relationship_profiles for qualifying subject pairs.")
    parser.add_argument("--min-data", type=int, default=5, help="Minimum combined facts+beliefs+memories per subject to qualify.")
    parser.add_argument("--min-shared-episodes", type=int, default=10, help="Minimum co-occurring episodes for a pair to qualify.")
    parser.add_argument("--min-new-data", type=int, default=5, help="Minimum NEW facts+beliefs+memories (either subject) since last assessment to qualify a refresh of an existing pair.")
    parser.add_argument("--limit", type=int, default=0, help="Max pairs to process (0 = all qualifying).")
    args = parser.parse_args()

    conn = psycopg2.connect(DB_URL)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        cur.execute("SELECT count(*) AS n FROM emotional_intelligence.episodes")
        total_episodes = cur.fetchone()["n"]

        subjects = fetch_qualifying_subjects(cur, args.min_data)
        subj_episodes = fetch_subject_episode_sets(cur)
        existing = fetch_existing_pairs(cur)  # (a, b) -> last assessed at (or None)

        candidates = []
        for i in range(len(subjects)):
            for j in range(i + 1, len(subjects)):
                sa, sb = subjects[i], subjects[j]
                a, b = sorted([sa["id"], sb["id"]])
                shared = subj_episodes.get(sa["id"], set()) & subj_episodes.get(sb["id"], set())
                if len(shared) < args.min_shared_episodes:
                    continue
                last_at = existing.get((a, b), "not_existing")
                if last_at == "not_existing":
                    candidates.append((sa, sb, shared, "new"))
                    continue
                new_data = count_new_data_since(cur, a, last_at) + count_new_data_since(cur, b, last_at)
                if new_data >= args.min_new_data:
                    candidates.append((sa, sb, shared, f"refresh, {new_data} new since last"))
        candidates.sort(key=lambda c: len(c[2]), reverse=True)
        if args.limit:
            candidates = candidates[: args.limit]

        print(f"pairs_to_process={len(candidates)}")

        succeeded, failed = 0, 0
        for sa, sb, shared, kind in candidates:
            print(f"--- {sa['canonical_name']} <-> {sb['canonical_name']} (shared_episodes={len(shared)}, {kind}) ---")
            try:
                result = process_pair(cur, sa, sb, shared, total_episodes)
                if result["status"] == "success":
                    conn.commit()
                    succeeded += 1
                    print(f"  OK run_id={result['run_id']}")
                else:
                    conn.rollback()
                    failed += 1
                    cur.execute(
                        "INSERT INTO emotional_intelligence.analysis_runs (run_type, prompt_version, status, error) "
                        "VALUES ('relationship_batch', %s, 'error', %s)",
                        (PROMPT_VERSION, result.get("error")),
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
