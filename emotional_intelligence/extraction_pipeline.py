"""
Cognitive extraction pipeline -- the production-shaped batch job. Finds
episodes not yet analyzed, assembles each one's multi-speaker transcript,
runs one LLM extraction call per episode, and writes structured results
into the emotional_intelligence schema with full analysis_runs provenance.

Designed to run 2-3x/day against new source data in prod (per the original
ask): each run only processes episodes with no existing successful
analysis_runs row, so it's safe to re-invoke on a schedule (cron/
APScheduler) without reprocessing everything each time. A failed episode is
logged (status='error') and skipped, never crashes the batch -- matches
COGNITIVE_EXTRACTION_PIPELINE_PLAN.md's Phase 0 decision 4.

Self-contained: does not import app.py (per standing instruction -- no
backend changes for this work). Uses the same Ollama Cloud endpoint/env var
convention as app.py's call_llm(), independently.

Usage:
    python extraction_pipeline.py --limit 1                 # process 1 unprocessed episode (testing)
    python extraction_pipeline.py --episode-id s01e01        # process one specific episode
    python extraction_pipeline.py                            # process all unprocessed episodes (full batch)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

load_dotenv()

DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    raise SystemExit("DATABASE_URL is required")

# Bumped whenever the JSON schema below changes -- stored on every
# analysis_runs row so a later query can answer "which extracted rows came
# from a stale prompt" (see COGNITIVE_EXTRACTION_PIPELINE_PLAN.md, Phase 0
# decision 2 / the "remaining gap" prompt-versioning ask).
PROMPT_VERSION = 1

# Cap how many dialogue turns go into one extraction call -- a full episode
# can run long; this keeps a single call's cost/latency bounded. Matches
# the main app's BACKGROUND_ANALYSIS_BATCH_SIZE cost-knob pattern.
MAX_TURNS_PER_EPISODE = int(os.getenv("EI_MAX_TURNS_PER_EPISODE", "400"))


def call_llm(messages: list[dict]) -> tuple[str, dict]:
    """Same Ollama Cloud endpoint/env-var convention as app.py's call_llm --
    reimplemented standalone here (not imported) per the no-backend-changes
    instruction. Returns (content, raw_response) so the caller can pull
    timing/token metadata for analysis_runs provenance."""
    api_key = os.getenv("OLLAMA_API_KEY") or os.getenv("olama_api_key")
    if not api_key:
        raise RuntimeError("Missing Ollama API key (OLLAMA_API_KEY / olama_api_key)")

    payload = {
        "model": os.getenv("OLLAMA_MODEL", "gpt-oss:120b"),
        "messages": messages,
        "stream": False,
    }
    response = requests.post(
        "https://ollama.com/api/chat",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=180,
    )
    response.raise_for_status()
    result = response.json()
    return result.get("message", {}).get("content", ""), result


def extract_json(text: str) -> dict | None:
    """Best-effort JSON extraction, same forgiving pattern as app.py's
    extract_json (LLMs sometimes wrap JSON in ```json fences or add
    preamble)."""
    text = text.strip()
    text = re.sub(r"^```json\s*|^```\s*|```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except ValueError:
            return None
    return None


EXTRACTION_SCHEMA_PROMPT = """You are analyzing one episode transcript (a scripted, multi-character scene sequence, not a single person's speech). Extract structured information as JSON.

1. "summary": 2-3 sentence plain-language summary of the episode.
2. "key_points": up to 5 short bullet-style key points.
3. "action_items": up to 5 concrete things a character explicitly commits to doing (empty list if none).
4. "sentiment": one word describing the episode's overall emotional tone.
5. "topics": up to 8 short (1-4 word) topics actually discussed in this episode.
6. "entities": up to 10 named entities actually mentioned (real named people/places/organizations/things, not generic nouns), each {"name": "", "type": "person|organization|technology|place|other"}.
7. "events": up to 5 significant named-moment events in this episode (not routine small talk), each {"title": "", "description": "", "participants": ["character names"], "importance": 0.0-1.0}.
8. "subjects": for EACH main character who has a distinct personality moment in this episode, one object:
   {
     "name": "character name exactly as it appears in the transcript",
     "facts": [{"predicate": "", "object": "", "confidence": 0.0-1.0}]  // verifiable/stated life details, e.g. predicate="works_at", object="a museum" -- NEVER a bare relative time word ("today"/"tomorrow"/"tonight"/"yesterday") as the object; a fact is read back as still true indefinitely, so "birthday: today" said once becomes a false claim on every future day it's read. If the detail is genuinely time-bound like that, put it in "memories" instead (a memory is inherently a past moment, not an ongoing claim) or state the durable part only, without the relative-time word, and omit it entirely if there's no durable part.
     "preferences": [{"category": "", "item": "", "weight": 0.0-1.0}]  // stated/clearly implied likes/dislikes
     "beliefs": [{"topic": "", "belief": "", "confidence": 0.0-1.0}]  // stated opinions/stances, distinct from facts -- an opinion, not a verifiable detail
     "memories": [{"summary": "", "importance": 0.0-1.0, "emotion": ""}]  // distinct, notable episodic moments for this character, not a recap of the whole episode
   }
   Only include a subject if there's something genuine to report -- most episodes won't have all four categories filled for every character. Empty arrays are fine and expected.

Reply with ONLY this JSON, no preamble/fences:
{"summary": "", "key_points": [""], "action_items": [""], "sentiment": "", "topics": [""], "entities": [{"name": "", "type": ""}], "events": [{"title": "", "description": "", "participants": [""], "importance": 0.5}], "subjects": [{"name": "", "facts": [], "preferences": [], "beliefs": [], "memories": []}]}
Empty lists/arrays if nothing qualifies in a category -- do not invent content to fill them."""


def fetch_unprocessed_episodes(cur, limit: int, episode_id_filter: str | None) -> list[dict]:
    if episode_id_filter:
        cur.execute(
            "SELECT id, episode_id, season, episode_number, episode_name FROM emotional_intelligence.episodes "
            "WHERE episode_id = %s",
            (episode_id_filter,),
        )
    else:
        cur.execute(
            """
            SELECT e.id, e.episode_id, e.season, e.episode_number, e.episode_name
            FROM emotional_intelligence.episodes e
            WHERE NOT EXISTS (
                SELECT 1 FROM emotional_intelligence.analysis_runs r
                WHERE r.episode_id = e.id AND r.status = 'success' AND r.run_type = 'episode_analysis'
            )
            ORDER BY e.season, e.episode_number
            """ + (f" LIMIT {int(limit)}" if limit else "")
        )
    return [dict(row) for row in cur.fetchall()]


def sync_episodes_from_transcript(cur) -> int:
    """Idempotent upsert of emotional_intelligence.episodes from
    emotional_intelligence.transcript -- the research-local 'conversation'
    unit this pipeline processes."""
    cur.execute(
        """
        INSERT INTO emotional_intelligence.episodes (season, episode_number, episode_id, episode_name, source_url)
        SELECT season, episode_number, episode_id, title, source_url FROM emotional_intelligence.transcript
        ON CONFLICT (episode_id) DO UPDATE SET
            episode_name = EXCLUDED.episode_name,
            source_url = EXCLUDED.source_url
        """
    )
    return cur.rowcount


def build_episode_transcript(cur, episode_id_text: str) -> str:
    """Assembles this episode's dialogue turns (ordered by scene/turn) into
    'SPEAKER: line' text for the LLM -- the multi-speaker equivalent of the
    main app's raw_transcript, reconstructed from the per-turn table rather
    than stored as one blob."""
    cur.execute(
        """
        SELECT scene_number, scene_name, speaker_name, turn_number, raw_transcript
        FROM emotional_intelligence.episode_speaker_transcript
        WHERE episode_id = %s
        ORDER BY scene_number NULLS LAST, turn_number NULLS LAST
        LIMIT %s
        """,
        (episode_id_text, MAX_TURNS_PER_EPISODE),
    )
    lines = []
    current_scene = None
    for scene_number, scene_name, speaker_name, turn_number, raw_transcript in cur.fetchall():
        if scene_name != current_scene:
            lines.append(f"[Scene: {scene_name}]")
            current_scene = scene_name
        lines.append(f"{speaker_name}: {raw_transcript}")
    return "\n".join(lines)


def resolve_subject_id(cur, name: str, subject_cache: dict) -> int | None:
    """Resolves an LLM-mentioned character name to a subjects.id, via the
    speaker_aliases/subjects normalization already built -- does NOT create
    new subjects here (an LLM-mentioned name not seen in the source
    transcript is more likely a hallucination/paraphrase than a real new
    character); returns None (caller skips) rather than guessing."""
    key = name.strip().lower()
    if key in subject_cache:
        return subject_cache[key]
    cur.execute("SELECT id FROM emotional_intelligence.subjects WHERE lower(canonical_name) = %s", (key,))
    row = cur.fetchone()
    if not row:
        cur.execute(
            "SELECT subject_id FROM emotional_intelligence.speaker_aliases WHERE lower(raw_speaker_name) = %s",
            (key,),
        )
        row = cur.fetchone()
    subject_id = row[0] if row else None
    subject_cache[key] = subject_id
    return subject_id


def process_episode(cur, episode: dict, subject_cache: dict) -> dict:
    episode_id = episode["id"]
    episode_id_text = episode["episode_id"]

    transcript_text = build_episode_transcript(cur, episode_id_text)
    if not transcript_text.strip():
        return {"status": "error", "error": "empty transcript"}

    messages = [
        {"role": "system", "content": EXTRACTION_SCHEMA_PROMPT},
        {"role": "user", "content": transcript_text},
    ]

    start = time.monotonic()
    try:
        content, raw_response = call_llm(messages)
    except Exception as e:
        return {"status": "error", "error": f"LLM call failed: {e!r}"}
    latency_ms = int((time.monotonic() - start) * 1000)

    parsed = extract_json(content)
    if not parsed:
        return {"status": "error", "error": "unparseable LLM response", "latency_ms": latency_ms}

    tokens = (raw_response.get("prompt_eval_count") or 0) + (raw_response.get("eval_count") or 0)

    cur.execute(
        """
        INSERT INTO emotional_intelligence.analysis_runs
            (episode_id, run_type, model, prompt_version, latency_ms, tokens, status)
        VALUES (%s, 'episode_analysis', %s, %s, %s, %s, 'success')
        RETURNING id
        """,
        (episode_id, raw_response.get("model"), PROMPT_VERSION, latency_ms, tokens or None),
    )
    run_id = cur.fetchone()[0]

    # conversation_summaries (episode-scoped, 1:1)
    summary = (parsed.get("summary") or "").strip()
    if summary:
        cur.execute(
            """
            INSERT INTO emotional_intelligence.conversation_summaries
                (episode_id, analysis_run_id, summary, key_points, action_items, sentiment)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (episode_id) DO UPDATE SET
                analysis_run_id = EXCLUDED.analysis_run_id,
                summary = EXCLUDED.summary,
                key_points = EXCLUDED.key_points,
                action_items = EXCLUDED.action_items,
                sentiment = EXCLUDED.sentiment
            """,
            (
                episode_id, run_id, summary,
                [k for k in (parsed.get("key_points") or []) if isinstance(k, str)][:5],
                [a for a in (parsed.get("action_items") or []) if isinstance(a, str)][:5],
                (parsed.get("sentiment") or None),
            ),
        )

    # topics (episode-scoped, globally deduped)
    for topic_name in (parsed.get("topics") or [])[:8]:
        if not isinstance(topic_name, str) or not topic_name.strip():
            continue
        cur.execute(
            "INSERT INTO emotional_intelligence.topics (name, analysis_run_id) VALUES (%s, %s) "
            "ON CONFLICT (name) DO NOTHING RETURNING id",
            (topic_name.strip(), run_id),
        )
        row = cur.fetchone()
        if not row:
            cur.execute("SELECT id FROM emotional_intelligence.topics WHERE name = %s", (topic_name.strip(),))
            row = cur.fetchone()
        cur.execute(
            "INSERT INTO emotional_intelligence.episode_topics (episode_id, topic_id, analysis_run_id) "
            "VALUES (%s, %s, %s) ON CONFLICT (episode_id, topic_id) DO NOTHING",
            (episode_id, row[0], run_id),
        )

    # entities (episode-scoped, globally deduped by name+type)
    for ent in (parsed.get("entities") or [])[:10]:
        if not isinstance(ent, dict) or not (ent.get("name") or "").strip():
            continue
        name, etype = ent["name"].strip(), (ent.get("type") or None)
        cur.execute(
            "INSERT INTO emotional_intelligence.entities (name, entity_type, analysis_run_id) VALUES (%s, %s, %s) "
            "ON CONFLICT (name, entity_type) DO NOTHING RETURNING id",
            (name, etype, run_id),
        )
        row = cur.fetchone()
        if not row:
            cur.execute(
                "SELECT id FROM emotional_intelligence.entities WHERE name = %s AND entity_type IS NOT DISTINCT FROM %s",
                (name, etype),
            )
            row = cur.fetchone()
        cur.execute(
            "INSERT INTO emotional_intelligence.episode_entities (episode_id, entity_id, analysis_run_id) "
            "VALUES (%s, %s, %s) ON CONFLICT (episode_id, entity_id) DO NOTHING",
            (episode_id, row[0], run_id),
        )

    # events (episode-scoped, subject_id resolved only if exactly one participant named)
    for ev in (parsed.get("events") or [])[:5]:
        if not isinstance(ev, dict) or not (ev.get("title") or "").strip():
            continue
        participants = [p for p in (ev.get("participants") or []) if isinstance(p, str)]
        subject_id = None
        if len(participants) == 1:
            subject_id = resolve_subject_id(cur, participants[0], subject_cache)
        cur.execute(
            """
            INSERT INTO emotional_intelligence.events
                (episode_id, subject_id, analysis_run_id, title, description, participants, importance)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (episode_id, subject_id, run_id, ev["title"].strip(), ev.get("description"), participants, ev.get("importance")),
        )

    # subject-scoped: facts, preferences, beliefs, memories
    subjects_written = 0
    for subj in (parsed.get("subjects") or []):
        if not isinstance(subj, dict) or not (subj.get("name") or "").strip():
            continue
        subject_id = resolve_subject_id(cur, subj["name"], subject_cache)
        if subject_id is None:
            continue  # LLM named someone not in this episode's actual speaker list -- skip rather than guess
        subjects_written += 1

        for f in (subj.get("facts") or []):
            if not isinstance(f, dict) or not (f.get("predicate") or "").strip():
                continue
            cur.execute(
                """
                INSERT INTO emotional_intelligence.facts
                    (subject_id, episode_id, analysis_run_id, subject, predicate, object, confidence, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'episode_transcript')
                """,
                (subject_id, episode_id, run_id, subj["name"], f["predicate"], f.get("object") or "", f.get("confidence")),
            )

        for p in (subj.get("preferences") or []):
            if not isinstance(p, dict) or not (p.get("item") or "").strip():
                continue
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

        for b in (subj.get("beliefs") or []):
            if not isinstance(b, dict) or not (b.get("belief") or "").strip():
                continue
            cur.execute(
                """
                INSERT INTO emotional_intelligence.beliefs (subject_id, episode_id, analysis_run_id, topic, belief, confidence)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (subject_id, episode_id, run_id, b.get("topic") or "general", b["belief"], b.get("confidence")),
            )

        for m in (subj.get("memories") or []):
            if not isinstance(m, dict) or not (m.get("summary") or "").strip():
                continue
            cur.execute(
                """
                INSERT INTO emotional_intelligence.memories (subject_id, episode_id, analysis_run_id, summary, importance, emotion)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (subject_id, episode_id, run_id, m["summary"], m.get("importance"), m.get("emotion")),
            )

    return {
        "status": "success", "run_id": run_id, "latency_ms": latency_ms, "tokens": tokens,
        "topics": len(parsed.get("topics") or []), "entities": len(parsed.get("entities") or []),
        "events": len(parsed.get("events") or []), "subjects_written": subjects_written,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the cognitive extraction pipeline over Friends episodes.")
    parser.add_argument("--limit", type=int, default=0, help="Max episodes to process (0 = all unprocessed).")
    parser.add_argument("--episode-id", default=None, help="Process one specific episode_id (e.g. s01e01), ignoring the unprocessed filter.")
    args = parser.parse_args()

    conn = psycopg2.connect(DB_URL)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    plain_cur = conn.cursor()

    try:
        synced = sync_episodes_from_transcript(plain_cur)
        conn.commit()
        print(f"episodes_synced={synced}")

        episodes = fetch_unprocessed_episodes(cur, args.limit, args.episode_id)
        print(f"episodes_to_process={len(episodes)}")

        subject_cache: dict = {}
        succeeded, failed = 0, 0
        for ep in episodes:
            print(f"\n--- {ep['episode_id']} (S{ep['season']:02d}E{ep['episode_number']:02d}) ---")
            try:
                result = process_episode(plain_cur, ep, subject_cache)
                if result["status"] == "success":
                    conn.commit()
                    succeeded += 1
                    print(f"  OK run_id={result['run_id']} latency_ms={result['latency_ms']} tokens={result['tokens']} "
                          f"topics={result['topics']} entities={result['entities']} events={result['events']} "
                          f"subjects_written={result['subjects_written']}")
                else:
                    conn.rollback()
                    failed += 1
                    plain_cur.execute(
                        "INSERT INTO emotional_intelligence.analysis_runs "
                        "(episode_id, run_type, prompt_version, latency_ms, status, error) "
                        "VALUES (%s, 'episode_analysis', %s, %s, 'error', %s)",
                        (ep["id"], PROMPT_VERSION, result.get("latency_ms"), result.get("error")),
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
        plain_cur.close()
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
