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

from ei_adapter import NOT_EXPIRED, _count_table, _search_table

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


def fetch_subject_persona(cur, subject_name: str, question: str | None = None) -> dict:
    """Raw structured persona data for one subject -- the single source of
    truth both assemble_cognitive_memory() (text, for the LLM) and the
    /api/persona JSON endpoint (chat_server.py) build on, so the two never
    drift out of sync with separate queries.

    question is optional and defaults to None, which reproduces the
    original plain-recency behavior exactly -- the /api/persona JSON
    endpoint calls this with no question (it means "show me everything"),
    so that path is unaffected. When a real question is passed (from
    assemble_cognitive_memory, in turn from an actual chat message), each
    table is searched via ei_adapter.py's shared _search_table() instead,
    so relevant-but-not-recent rows (a preference weighted higher but
    entered earlier, say) aren't silently dropped by a fixed cutoff."""
    cur.execute("SELECT id, canonical_name FROM emotional_intelligence.subjects WHERE lower(canonical_name) = %s",
                (subject_name.strip().lower(),))
    row = cur.fetchone()
    if not row:
        raise SystemExit(f"No subject found matching {subject_name!r}. Check emotional_intelligence.subjects.canonical_name.")
    subject_id, canonical_name = row["id"], row["canonical_name"]

    facts = _search_table(cur, "facts", subject_id, question,
                           "predicate, object, confidence", "created_at DESC", 20, tiebreak_col="confidence",
                           extra_where=NOT_EXPIRED)
    preferences = _search_table(cur, "preferences", subject_id, question,
                                 "category, item, weight", "updated_at DESC", 15, tiebreak_col="weight")
    beliefs = _search_table(cur, "beliefs", subject_id, question,
                             "topic, belief, confidence", "created_at DESC", 15, tiebreak_col="confidence")
    memories = _search_table(cur, "memories", subject_id, question,
                              "summary, emotion, importance", "importance DESC NULLS LAST", 10,
                              extra_where="archived = FALSE", tiebreak_col="importance")

    total_facts = _count_table(cur, "facts", subject_id, extra_where=NOT_EXPIRED)
    total_preferences = _count_table(cur, "preferences", subject_id)
    total_beliefs = _count_table(cur, "beliefs", subject_id)
    total_memories = _count_table(cur, "memories", subject_id, extra_where="archived = FALSE")

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
        "total_facts": total_facts,
        "total_preferences": total_preferences,
        "total_beliefs": total_beliefs,
        "total_memories": total_memories,
        "personality": dict(personality) if personality else None,
        "relationships": [dict(r) for r in relationships],
    }


def assemble_cognitive_memory(cur, subject_name: str, question: str | None = None) -> tuple[str, dict]:
    persona = fetch_subject_persona(cur, subject_name, question)
    canonical_name = persona["canonical_name"]

    lines = [
        f"=== Cognitive memory: {canonical_name} ===",
        "(Each section below is labeled 'showing X of Y total' -- Y is the TRUE total count; X is "
        "just how many of the most relevant ones are listed. For any question about quantity or "
        "frequency, always use the TRUE TOTAL, never the number of items actually listed below, "
        "which is only a relevance-filtered sample.)",
    ]
    lines.append(f"\nFacts (showing {len(persona['facts'])} of {persona['total_facts']} total):")
    for f in persona["facts"]:
        lines.append(f"- {canonical_name} {f['predicate']} {f['object']} (confidence={f['confidence']})")
    lines.append(
        f"\nRecurring preferences/tastes -- general likes, NOT tied to one moment "
        f"(showing {len(persona['preferences'])} of {persona['total_preferences']} total):"
    )
    for p in persona["preferences"]:
        lines.append(f"- {p['category']}: {p['item']} (weight={p['weight']})")
    lines.append(f"\nBeliefs (showing {len(persona['beliefs'])} of {persona['total_beliefs']} total):")
    for b in persona["beliefs"]:
        lines.append(f"- On {b['topic']}: {b['belief']} (confidence={b['confidence']})")
    lines.append(
        f"\nNotable one-time memories -- specific past moments, each with its OWN emotion tag "
        f"(importance = how significant, independent of whether the emotion is positive) "
        f"(showing {len(persona['memories'])} of {persona['total_memories']} total):"
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
        "facts": len(persona["facts"]), "facts_total": persona["total_facts"],
        "preferences": len(persona["preferences"]), "preferences_total": persona["total_preferences"],
        "beliefs": len(persona["beliefs"]), "beliefs_total": persona["total_beliefs"],
        "memories": len(persona["memories"]), "memories_total": persona["total_memories"],
        "has_personality": bool(personality), "relationships": len(persona["relationships"]),
    }
    return "\n".join(lines), stats


def assemble_multi_subject_memory(cur, subject_names: list[str], question: str | None = None) -> tuple[str, dict]:
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
        block, stats = assemble_cognitive_memory(cur, name, question)
        blocks.append(block)
        combined_stats[name] = stats
    return "\n\n".join(blocks), combined_stats


REASONING_SYSTEM_PROMPT = """You are reasoning about a person using ONLY the structured cognitive memory provided below -- not any external knowledge, not assumptions, not raw transcripts. This memory was extracted from prior observations (facts, preferences, beliefs, notable memories, personality, relationships).

HOW TO WRITE THE ANSWER -- this matters as much as getting the facts right. The memory block below is your private evidence, not a template for your reply. Never structure your answer as a labeled report that mirrors the internal categories (do not write headers like "Preference:", "Memory:", "Fact:", "Summary:", and do not present findings as a bulleted list of category -> evidence). Never let a raw number from the memory block appear in your answer -- no "(0.86)", no "confidence=0.95", no "trust score of 0.73", not even in passing or in parentheses. Those numbers exist for your own judgment while reading the evidence, not for quoting. If you need to convey how strong something is, say it in ordinary words instead ("clearly", "only in passing", "one of the more significant things that happened to her") -- if you catch yourself about to type a digit from the memory block, stop and rephrase in words. Write the way someone who actually knows this person would answer if asked casually -- lead with the real answer to the question in the first sentence, then fold in the specific supporting detail as part of the same thought, not as an itemized justification underneath. Two to four sentences is usually enough. It is fine, even good, to sound a little uncertain where the evidence is thin -- say so in plain words ("nothing on record says this outright, but...") rather than formalizing it into a labeled caveat section.

Ground every claim in the memory provided, and distinguish two different situations instead of treating them the same way:
1. No direct statement, but SOME relevant adjacent data exists (e.g. asked what one person likes about another, and there's no explicit statement of that, but the other person's own facts/preferences/personality ARE in the memory) -- in this case, DO offer a reasoned best-guess inference rather than refusing outright. Clearly flag it as an inference, not a stated fact -- e.g. "Nothing states this directly, but based on [specific detail actually in the memory], a plausible guess is ...". The inference must still be anchored to something specific actually present in the memory, not invented from nothing.
2. Truly nothing in the provided memory (direct or indirect) to reason from at all -- only in this case, say so explicitly rather than guessing blind.
Be direct and concise.

IMPORTANT -- preferences and memories are different categories that everyday words like "like", "love", "enjoy", "favorite", or "happiest" can each map to, and a question won't always specify which one it means:
- "Preferences" = recurring general tastes (weight = how strongly/consistently held), not tied to one moment.
- "Memories" = specific one-time past events, each with its own emotion tag; "importance" means how significant/memorable, NOT how positive -- a highly important memory can be shocking, sad, or urgent, not joyful.
If a question could reasonably mean either (e.g. "what does X love most?" could be a preference OR a joyful memory), answer BOTH: state the strongest matching preference, then separately the most relevant memory (if one exists with a matching emotion), and label which is which. Never silently pick one interpretation and drop the other.

If memory for more than one person is provided below (separated by "=== Cognitive memory: NAME ===" headers), you may compare/contrast them or reason about their relationship -- but ONLY using what each person's own individual memory actually states. There is no separate shared "relationship history" record here (that would be a different, not-yet-built data source) -- do not invent a joint narrative, a specific event, or a sequence of who-did-what-first that isn't explicitly grounded in at least one of the two people's own listed facts/beliefs/memories. If neither person's memory covers what's being asked, say so plainly.

CRITICAL when a question asks about person A's feelings/opinion toward person B (e.g. "what does A like about B"): any inference must be anchored in person A's OWN facts/preferences/beliefs/memories -- NOT person B's. Data showing that B enjoys A's company, or that B did something for A, is evidence of B's feelings, not A's -- a shared activity or one side's enthusiasm does NOT imply the other side felt the same way, and assuming so is an invalid leap (a real, observed failure mode: one person's own recorded fondness for another was previously mistaken for the other person's reciprocated feelings, producing a confidently wrong answer). If person A's own memory has nothing relevant to their feelings about B, say that plainly rather than substituting B's side as a stand-in."""


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
            memory_block, stats = assemble_cognitive_memory(cur, args.subject[0], args.question)
        else:
            memory_block, stats = assemble_multi_subject_memory(cur, args.subject, args.question)
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
