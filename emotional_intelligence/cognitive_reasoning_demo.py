"""
Cognitive-memory reasoning demo -- the consumption side of the pipeline.
Assembles a subject's accumulated structured memory (facts, preferences,
beliefs, notable memories, personality, relationships -- whatever exists)
into a compact context block, then has the LLM reason grounded in that
structured state instead of raw transcript retrieval. This is the
"Cognitive RAG" pattern from the roadmap docs: the LLM synthesizes
structured knowledge rather than searching document text.

Usage:
    python cognitive_reasoning_demo.py --subject Ross --question "What does Ross care about, and how would he likely react to unexpected good news?"
"""
from __future__ import annotations

import argparse
import os

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

load_dotenv()

DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    raise SystemExit("DATABASE_URL is required")


def call_llm(messages: list[dict]) -> str:
    api_key = os.getenv("OLLAMA_API_KEY") or os.getenv("olama_api_key")
    if not api_key:
        raise RuntimeError("Missing Ollama API key")
    payload = {"model": os.getenv("OLLAMA_MODEL", "gpt-oss:120b"), "messages": messages, "stream": False}
    response = requests.post(
        "https://ollama.com/api/chat",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=180,
    )
    response.raise_for_status()
    return response.json().get("message", {}).get("content", "")


def fetch_subject_persona(cur, subject_name: str) -> dict:
    """Raw structured persona data for one subject -- the single source of
    truth both assemble_cognitive_memory() (text, for the LLM) and the
    /api/persona JSON endpoint (chat_server.py) build on, so the two never
    drift out of sync with separate queries."""
    cur.execute("SELECT id, canonical_name FROM emotional_intelligence.subjects WHERE lower(canonical_name) = %s",
                (subject_name.strip().lower(),))
    row = cur.fetchone()
    if not row:
        raise SystemExit(f"No subject found matching {subject_name!r}. Check emotional_intelligence.subjects.canonical_name.")
    subject_id, canonical_name = row["id"], row["canonical_name"]

    cur.execute(
        "SELECT predicate, object, confidence FROM emotional_intelligence.facts "
        "WHERE subject_id = %s ORDER BY created_at DESC LIMIT 20",
        (subject_id,),
    )
    facts = cur.fetchall()

    cur.execute(
        "SELECT category, item, weight FROM emotional_intelligence.preferences "
        "WHERE subject_id = %s ORDER BY updated_at DESC LIMIT 15",
        (subject_id,),
    )
    preferences = cur.fetchall()

    cur.execute(
        "SELECT topic, belief, confidence FROM emotional_intelligence.beliefs "
        "WHERE subject_id = %s ORDER BY created_at DESC LIMIT 15",
        (subject_id,),
    )
    beliefs = cur.fetchall()

    cur.execute(
        "SELECT summary, emotion, importance FROM emotional_intelligence.memories "
        "WHERE subject_id = %s AND archived = FALSE ORDER BY importance DESC NULLS LAST LIMIT 10",
        (subject_id,),
    )
    memories = cur.fetchall()

    cur.execute(
        "SELECT openness, conscientiousness, extraversion, agreeableness, neuroticism, confidence "
        "FROM emotional_intelligence.personality_snapshots WHERE subject_id = %s "
        "ORDER BY created_at DESC LIMIT 1",
        (subject_id,),
    )
    personality = cur.fetchone()

    cur.execute(
        """
        SELECT s2.canonical_name, r.trust_score, r.conflict_score, r.emotional_support, r.relationship_summary
        FROM emotional_intelligence.relationship_profiles r
        JOIN emotional_intelligence.subjects s2 ON s2.id = CASE WHEN r.subject_a = %s THEN r.subject_b ELSE r.subject_a END
        WHERE r.subject_a = %s OR r.subject_b = %s
        """,
        (subject_id, subject_id, subject_id),
    )
    relationships = cur.fetchall()

    return {
        "subject_id": subject_id,
        "canonical_name": canonical_name,
        "facts": [dict(r) for r in facts],
        "preferences": [dict(r) for r in preferences],
        "beliefs": [dict(r) for r in beliefs],
        "memories": [dict(r) for r in memories],
        "personality": dict(personality) if personality else None,
        "relationships": [dict(r) for r in relationships],
    }


def assemble_cognitive_memory(cur, subject_name: str) -> tuple[str, dict]:
    persona = fetch_subject_persona(cur, subject_name)
    canonical_name = persona["canonical_name"]

    lines = [f"=== Cognitive memory: {canonical_name} ==="]
    lines.append(f"\nFacts ({len(persona['facts'])}):")
    for f in persona["facts"]:
        lines.append(f"- {canonical_name} {f['predicate']} {f['object']} (confidence={f['confidence']})")
    lines.append(f"\nRecurring preferences/tastes -- general likes, NOT tied to one moment ({len(persona['preferences'])}):")
    for p in persona["preferences"]:
        lines.append(f"- {p['category']}: {p['item']} (weight={p['weight']})")
    lines.append(f"\nBeliefs ({len(persona['beliefs'])}):")
    for b in persona["beliefs"]:
        lines.append(f"- On {b['topic']}: {b['belief']} (confidence={b['confidence']})")
    lines.append(
        f"\nNotable one-time memories -- specific past moments, each with its OWN emotion tag "
        f"(importance = how significant, independent of whether the emotion is positive) ({len(persona['memories'])}):"
    )
    for m in persona["memories"]:
        lines.append(f"- {m['summary']} (emotion={m['emotion']}, importance={m['importance']})")
    personality = persona["personality"]
    if personality:
        lines.append(
            f"\nPersonality (Big Five, confidence={personality['confidence']}): "
            f"openness={personality['openness']}, conscientiousness={personality['conscientiousness']}, "
            f"extraversion={personality['extraversion']}, agreeableness={personality['agreeableness']}, "
            f"neuroticism={personality['neuroticism']}"
        )
    else:
        lines.append("\nPersonality: no snapshot yet (batch job not run).")
    lines.append(f"\nRelationships ({len(persona['relationships'])}):")
    for r in persona["relationships"]:
        lines.append(
            f"- With {r['canonical_name']}: trust={r['trust_score']}, conflict={r['conflict_score']}, "
            f"emotional_support={r['emotional_support']}. {r['relationship_summary'] or ''}"
        )

    stats = {
        "facts": len(persona["facts"]), "preferences": len(persona["preferences"]),
        "beliefs": len(persona["beliefs"]), "memories": len(persona["memories"]),
        "has_personality": bool(personality), "relationships": len(persona["relationships"]),
    }
    return "\n".join(lines), stats


def assemble_multi_subject_memory(cur, subject_names: list[str]) -> tuple[str, dict]:
    """Multi-subject variant -- concatenates each subject's own memory block
    so a question spanning two people (e.g. comparing them, asking about
    their relationship) has both sides available. Each subject's memory
    stays clearly separated/labeled; the LLM is instructed (see
    REASONING_SYSTEM_PROMPT) not to invent shared history neither side's
    own memory actually mentions -- concatenating two people's individual
    memory is not the same as having a real relationship_profiles record of
    their shared history, which doesn't exist yet."""
    blocks = []
    combined_stats = {}
    for name in subject_names:
        block, stats = assemble_cognitive_memory(cur, name)
        blocks.append(block)
        combined_stats[name] = stats
    return "\n\n".join(blocks), combined_stats


REASONING_SYSTEM_PROMPT = """You are reasoning about a person using ONLY the structured cognitive memory provided below -- not any external knowledge, not assumptions, not raw transcripts. This memory was extracted from prior observations (facts, preferences, beliefs, notable memories, personality, relationships).

Ground every claim in the memory provided. If the memory doesn't cover something the question asks about, say so explicitly rather than inventing an answer. Be direct and concise.

IMPORTANT -- preferences and memories are different categories that everyday words like "like", "love", "enjoy", "favorite", or "happiest" can each map to, and a question won't always specify which one it means:
- "Preferences" = recurring general tastes (weight = how strongly/consistently held), not tied to one moment.
- "Memories" = specific one-time past events, each with its own emotion tag; "importance" means how significant/memorable, NOT how positive -- a highly important memory can be shocking, sad, or urgent, not joyful.
If a question could reasonably mean either (e.g. "what does X love most?" could be a preference OR a joyful memory), answer BOTH: state the strongest matching preference, then separately the most relevant memory (if one exists with a matching emotion), and label which is which. Never silently pick one interpretation and drop the other.

If memory for more than one person is provided below (separated by "=== Cognitive memory: NAME ===" headers), you may compare/contrast them or reason about their relationship -- but ONLY using what each person's own individual memory actually states. There is no separate shared "relationship history" record here (that would be a different, not-yet-built data source) -- do not invent a joint narrative, a specific event, or a sequence of who-did-what-first that isn't explicitly grounded in at least one of the two people's own listed facts/beliefs/memories. If neither person's memory covers what's being asked, say so plainly."""


def main() -> int:
    parser = argparse.ArgumentParser(description="Reason about one or more subjects using their accumulated cognitive memory.")
    parser.add_argument("--subject", action="append", required=True,
                         help="Canonical subject name, e.g. Ross. Pass twice for a cross-subject question, e.g. "
                              "--subject Janice --subject Chandler")
    parser.add_argument("--question", required=True, help="Question to answer grounded in the given subject(s)' memory")
    args = parser.parse_args()

    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        if len(args.subject) == 1:
            memory_block, stats = assemble_cognitive_memory(cur, args.subject[0])
        else:
            memory_block, stats = assemble_multi_subject_memory(cur, args.subject)
    finally:
        cur.close()
        conn.close()

    print("--- Assembled cognitive memory stats ---")
    print(stats)
    print("\n--- Memory block sent to LLM as context ---")
    print(memory_block)

    answer = call_llm([
        {"role": "system", "content": REASONING_SYSTEM_PROMPT},
        {"role": "user", "content": f"{memory_block}\n\nQuestion: {args.question}"},
    ])

    print("\n--- LLM answer (grounded in structured memory, not raw transcript retrieval) ---")
    print(answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
