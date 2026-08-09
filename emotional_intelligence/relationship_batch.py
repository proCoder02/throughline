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

Also covers real app users, using shared calls (public.call_participants) as
the co-occurrence signal instead of shared Friends episodes -- the same
"mechanically countable co-occurrence, LLM only for qualitative judgment"
split, just a different source of co-occurrence. Two real users qualify as a
pair once they've both actually joined (call_participants.status='joined')
the same call together at least --min-shared-calls times. Reuses
fetch_existing_pairs/count_new_data_since as-is (both already operate purely
on subject_id, with no Friends-specific assumption) so the same "new vs due
for refresh" logic applies uniformly; process_real_user_pair mirrors
process_pair's LLM-call/insert-upsert logic but is a separate function
(rather than a shared one with more parameters) so this addition can't
regress the existing, already-verified Friends-pair path. shared_topics has
no real equivalent here yet (no per-call topic extraction exists) and is
left an empty list rather than inventing one out of scope.

Usage:
    python relationship_batch.py
    python relationship_batch.py --min-shared-episodes 15   # narrower scope (Friends pairs)
    python relationship_batch.py --min-shared-calls 2       # narrower scope (real-user pairs)
    python relationship_batch.py --min-new-data 10          # require more new data before refreshing
    python relationship_batch.py --skip-fictional           # real users only
    python relationship_batch.py --skip-real-users          # Friends pairs only (old behavior)
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


def fetch_real_user_subject_map(cur) -> dict[int, dict]:
    """user_id -> {"id": subject_id, "canonical_name": ...} for every real
    user who already has a resolvable EI subject -- either an explicit
    app_user_subject_links row (an opt-in merge into a pre-existing
    subject) or the namespaced "app_user:<id>" fallback that
    real_user_extraction.py's ensure_user_subject creates. Read-only lookup
    (mirrors ensure_user_subject's own resolution order but never creates
    anything): a batch job shouldn't silently create a subject for a user
    real_user_extraction.py has never actually processed."""
    cur.execute(
        """
        SELECT l.user_id AS user_id, s.id AS subject_id, s.canonical_name
        FROM emotional_intelligence.app_user_subject_links l
        JOIN emotional_intelligence.subjects s ON s.id = l.subject_id
        UNION
        SELECT (regexp_match(s.canonical_name, '^app_user:(\\d+)$'))[1]::int AS user_id,
               s.id AS subject_id, s.canonical_name
        FROM emotional_intelligence.subjects s
        WHERE s.canonical_name ~ '^app_user:\\d+$'
        """
    )
    return {row["user_id"]: {"id": row["subject_id"], "canonical_name": row["canonical_name"]} for row in cur.fetchall()}


def fetch_user_call_cooccurrence(cur) -> dict[tuple[int, int], int]:
    """(user_id_a, user_id_b) [a<b] -> count of distinct calls both users
    actually joined together -- the real-user equivalent of two Friends
    characters both speaking in the same episode. Works for both 1:1 and
    group calls: any two participants who both actually joined the same
    call_id co-occurred in that call.

    Uses joined_at IS NOT NULL, NOT status='joined' -- app.py's leave_call
    overwrites status to 'left' once someone exits (see app.py's
    UPDATE call_participants SET status = 'left' ... on leave), so by the
    time any call has actually ended, its real participants show status
    'left', not 'joined'. status='joined' only ever matches someone on a
    STILL-ACTIVE call, which would have silently made this whole feature
    find ~0 pairs against real (mostly-ended) call history -- confirmed by
    testing against live data before this fix. joined_at is set once (at
    the same moment status becomes 'joined') and never cleared afterward,
    so it reliably means "was actually connected to this call at some
    point," regardless of whether they've since left."""
    cur.execute(
        """
        SELECT cp1.user_id AS u1, cp2.user_id AS u2, count(DISTINCT cp1.call_id) AS n
        FROM call_participants cp1
        JOIN call_participants cp2
            ON cp2.call_id = cp1.call_id AND cp2.user_id > cp1.user_id
        WHERE cp1.joined_at IS NOT NULL AND cp2.joined_at IS NOT NULL
        GROUP BY cp1.user_id, cp2.user_id
        """
    )
    return {(row["u1"], row["u2"]): row["n"] for row in cur.fetchall()}


def fetch_total_calls(cur) -> int:
    """Denominator for communication_frequency, mirroring total_episodes --
    keeps the field's meaning consistent between fictional and real-user
    pairs (share of ALL co-occurrence opportunities in the system), rather
    than inventing a differently-scoped metric for real users."""
    cur.execute("SELECT count(*) AS n FROM calls")
    return cur.fetchone()["n"]


def process_real_user_pair(cur, subject_a: dict, subject_b: dict, shared_calls: int, total_calls: int) -> dict:
    """Mirrors process_pair's LLM-call/insert-upsert logic exactly, but for
    a pair of real-user subjects with call-based co-occurrence instead of
    Friends-episode-based -- kept as its own function (not a shared one
    with more parameters) so this addition can't regress the existing,
    already-verified Friends-pair path above."""
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

    communication_frequency = shared_calls / total_calls if total_calls else None
    shared_topics: list[str] = []  # no per-call topic extraction exists yet

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
    return {"status": "success", "run_id": run_id, "shared_calls": shared_calls}


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
    parser.add_argument("--min-shared-episodes", type=int, default=10, help="Minimum co-occurring episodes for a Friends pair to qualify.")
    parser.add_argument("--min-shared-calls", type=int, default=1, help="Minimum shared joined calls for a real-user pair to qualify.")
    parser.add_argument("--min-new-data", type=int, default=5, help="Minimum NEW facts+beliefs+memories (either subject) since last assessment to qualify a refresh of an existing pair.")
    parser.add_argument("--limit", type=int, default=0, help="Max pairs to process per category (0 = all qualifying).")
    parser.add_argument("--skip-fictional", action="store_true", help="Skip the Friends-episode-based pairs entirely.")
    parser.add_argument("--skip-real-users", action="store_true", help="Skip the call-based real-user pairs entirely.")
    args = parser.parse_args()

    conn = psycopg2.connect(DB_URL)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        existing = fetch_existing_pairs(cur)  # (a, b) -> last assessed at (or None) -- shared by both categories below, since it's purely subject_id-keyed

        candidates = []  # (subject_a, subject_b, process_fn, process_arg, label, sort_key)

        if not args.skip_fictional:
            cur.execute("SELECT count(*) AS n FROM emotional_intelligence.episodes")
            total_episodes = cur.fetchone()["n"]

            subjects = fetch_qualifying_subjects(cur, args.min_data)
            subj_episodes = fetch_subject_episode_sets(cur)

            fictional_candidates = []
            for i in range(len(subjects)):
                for j in range(i + 1, len(subjects)):
                    sa, sb = subjects[i], subjects[j]
                    a, b = sorted([sa["id"], sb["id"]])
                    shared = subj_episodes.get(sa["id"], set()) & subj_episodes.get(sb["id"], set())
                    if len(shared) < args.min_shared_episodes:
                        continue
                    last_at = existing.get((a, b), "not_existing")
                    if last_at == "not_existing":
                        kind = "new"
                    else:
                        new_data = count_new_data_since(cur, a, last_at) + count_new_data_since(cur, b, last_at)
                        if new_data < args.min_new_data:
                            continue
                        kind = f"refresh, {new_data} new since last"
                    fictional_candidates.append({
                        "sa": sa, "sb": sb, "kind": kind, "sort": len(shared),
                        "label": f"{sa['canonical_name']} <-> {sb['canonical_name']} (shared_episodes={len(shared)}, {kind})",
                        "run": lambda cur, sa=sa, sb=sb, shared=shared: process_pair(cur, sa, sb, shared, total_episodes),
                    })
            fictional_candidates.sort(key=lambda c: c["sort"], reverse=True)
            if args.limit:
                fictional_candidates = fictional_candidates[: args.limit]
            candidates.extend(fictional_candidates)

        if not args.skip_real_users:
            real_subjects = fetch_real_user_subject_map(cur)  # user_id -> {id, canonical_name}
            cooccurrence = fetch_user_call_cooccurrence(cur)  # (user_id_a, user_id_b) -> shared call count
            total_calls = fetch_total_calls(cur)

            real_candidates = []
            for (uid_a, uid_b), shared_calls in cooccurrence.items():
                if shared_calls < args.min_shared_calls:
                    continue
                sa, sb = real_subjects.get(uid_a), real_subjects.get(uid_b)
                # Both users need a resolvable EI subject -- i.e.
                # real_user_extraction.py has actually run for them. A user
                # who's never had a conversation analyzed yet has nothing to
                # assess a relationship from.
                if sa is None or sb is None:
                    continue
                a, b = sorted([sa["id"], sb["id"]])
                last_at = existing.get((a, b), "not_existing")
                if last_at == "not_existing":
                    kind = "new"
                else:
                    new_data = count_new_data_since(cur, a, last_at) + count_new_data_since(cur, b, last_at)
                    if new_data < args.min_new_data:
                        continue
                    kind = f"refresh, {new_data} new since last"
                real_candidates.append({
                    "sa": sa, "sb": sb, "kind": kind, "sort": shared_calls,
                    "label": f"{sa['canonical_name']} <-> {sb['canonical_name']} (shared_calls={shared_calls}, {kind})",
                    "run": lambda cur, sa=sa, sb=sb, shared_calls=shared_calls: process_real_user_pair(cur, sa, sb, shared_calls, total_calls),
                })
            real_candidates.sort(key=lambda c: c["sort"], reverse=True)
            if args.limit:
                real_candidates = real_candidates[: args.limit]
            candidates.extend(real_candidates)

        print(f"pairs_to_process={len(candidates)}")

        succeeded, failed = 0, 0
        for c in candidates:
            print(f"--- {c['label']} ---")
            try:
                result = c["run"](cur)
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
