"""
Weekly personal-insight digest generation -- the inverse of
chat_feedback_extraction.py: reads structured EI data instead of writing
it, and produces a JSON array of insight cards for a user's
emotional_intelligence.weekly_digests row.

Deliberately insight-only for now -- the "from the world" (real-world
news/trend suggestion matched to each insight) half designed in
WEEKLY_DIGEST_BRAINSTORM.md is out of scope for this build. Each card's
shape already tolerates an absent "world" key, so adding it later is
additive, not a migration.

Usage:
    python weekly_digest.py --user-id 3    # print the generated card array
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

# Every script in this folder bare-imports its siblings (e.g. "from
# ei_adapter import ..."), which only resolves when this folder itself is
# on sys.path -- true when this file is run/imported directly, but NOT
# when nudges/nudge_engine.py imports it as the package-qualified
# "emotional_intelligence.weekly_digest" (confirmed: raised
# ModuleNotFoundError from within nudge_engine.py before this fix). Same
# fix as ei_adapter.py's trigger_chat_feedback_extraction.
_ei_folder = str(Path(__file__).resolve().parent)
if _ei_folder not in sys.path:
    sys.path.insert(0, _ei_folder)

from ei_adapter import _resolve_subject_id
from extraction_pipeline import call_llm, extract_json

load_dotenv()

DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    raise SystemExit("DATABASE_URL is required")

LOOKBACK_DAYS = 7

# Matches the card categories settled on during mockup iteration
# (preference/mood/personality/relationship), with "reminder" replaced by
# "fact" -- the mockups' reminder-card example was illustrative, but this
# build's actual data sources are facts/beliefs/memories/preferences/
# personality_snapshots/relationship_profiles, not the tasks table, so
# "fact" (covering facts/beliefs/memories collectively) is what the
# vocabulary should actually name. Client-side badge color keeps the same
# slot ("fact" = amber, same as the mockups' "reminder").
CARD_CATEGORIES = ("preference", "fact", "mood", "personality", "relationship")

DIGEST_PROMPT = """Today is {today}. You're writing a short, warm "here's what I've noticed about you this week" digest for a user, based ONLY on the structured data below, gathered from their own conversations over the last {lookback_days} days.

Turn this into a JSON array of short insight cards. Each card:
- category: one of {categories}
- label: a short 1-2 word display label matching the category (e.g. "Preference", "Mood", "Personality", "Friendship", "Fact")
- headline: <8 words, specific and concrete, e.g. "Hiking keeps coming up"
- body: 1-2 sentences, warm and second-person ("you"), grounded ONLY in the data given -- never invent a detail that isn't there.

Skip any category with nothing genuinely noteworthy in the data below -- most weeks won't have something for every category, and it's much better to return fewer cards than to pad with something generic or invented. If NOTHING here is worth surfacing at all, return an empty array.

This week's data:
{data_summary}

Reply with ONLY this JSON, no preamble/fences:
{{"cards": [{{"category": "", "label": "", "headline": "", "body": ""}}]}}"""


def _fetch_recent_ei_data(cur, subject_id: int) -> dict:
    cur.execute(
        "SELECT predicate, object FROM emotional_intelligence.facts "
        "WHERE subject_id = %s AND created_at > now() - make_interval(days => %s) "
        "ORDER BY created_at DESC LIMIT 20",
        (subject_id, LOOKBACK_DAYS),
    )
    facts = cur.fetchall()

    cur.execute(
        "SELECT topic, belief FROM emotional_intelligence.beliefs "
        "WHERE subject_id = %s AND created_at > now() - make_interval(days => %s) "
        "ORDER BY created_at DESC LIMIT 20",
        (subject_id, LOOKBACK_DAYS),
    )
    beliefs = cur.fetchall()

    cur.execute(
        "SELECT summary, emotion, importance FROM emotional_intelligence.memories "
        "WHERE subject_id = %s AND created_at > now() - make_interval(days => %s) "
        "ORDER BY importance DESC NULLS LAST LIMIT 20",
        (subject_id, LOOKBACK_DAYS),
    )
    memories = cur.fetchall()

    cur.execute(
        "SELECT category, item FROM emotional_intelligence.preferences "
        "WHERE subject_id = %s AND updated_at > now() - make_interval(days => %s) "
        "ORDER BY updated_at DESC LIMIT 20",
        (subject_id, LOOKBACK_DAYS),
    )
    preferences = cur.fetchall()

    cur.execute(
        "SELECT openness, conscientiousness, extraversion, agreeableness, neuroticism, created_at "
        "FROM emotional_intelligence.personality_snapshots "
        "WHERE subject_id = %s ORDER BY created_at DESC LIMIT 2",
        (subject_id,),
    )
    personality_snapshots = cur.fetchall()

    return {
        "facts": facts,
        "beliefs": beliefs,
        "memories": memories,
        "preferences": preferences,
        "personality_snapshots": personality_snapshots,
    }


def _fetch_recent_relationship_insights(cur, user_id: int, subject_id: int) -> list[dict]:
    """Friends this user shared a call with in the last LOOKBACK_DAYS days,
    for whom a relationship_profiles row already exists -- describes
    current state only (no history table to diff against, see
    WEEKLY_DIGEST_BRAINSTORM.md/the approved plan)."""
    cur.execute(
        """
        SELECT DISTINCT cp2.user_id AS friend_id, u.username AS friend_name
        FROM call_participants cp1
        JOIN call_participants cp2 ON cp2.call_id = cp1.call_id AND cp2.user_id != cp1.user_id
        JOIN calls c ON c.id = cp1.call_id
        JOIN users u ON u.id = cp2.user_id
        WHERE cp1.user_id = %(user_id)s AND cp1.joined_at IS NOT NULL AND cp2.joined_at IS NOT NULL
          AND c.created_at > now() - make_interval(days => %(days)s)
        """,
        {"user_id": user_id, "days": LOOKBACK_DAYS},
    )
    friends_this_week = cur.fetchall()

    plain_cur = cur.connection.cursor()
    insights = []
    for row in friends_this_week:
        friend_subject_id = _resolve_subject_id(plain_cur, row["friend_id"])
        if friend_subject_id is None:
            continue
        sa, sb = sorted((subject_id, friend_subject_id))
        cur.execute(
            "SELECT trust_score, communication_frequency, conflict_score, emotional_support, relationship_summary "
            "FROM emotional_intelligence.relationship_profiles WHERE subject_a = %s AND subject_b = %s",
            (sa, sb),
        )
        profile = cur.fetchone()
        if profile:
            insights.append({"friend_name": row["friend_name"], **profile})
    return insights


def _summarize_for_prompt(data: dict, relationships: list[dict]) -> str:
    lines = []
    if data["facts"]:
        lines.append("Facts:\n" + "\n".join(f"- {r['predicate']}: {r['object']}" for r in data["facts"]))
    if data["beliefs"]:
        lines.append("Beliefs:\n" + "\n".join(f"- {r['topic']}: {r['belief']}" for r in data["beliefs"]))
    if data["memories"]:
        lines.append("Memories:\n" + "\n".join(f"- {r['summary']} (emotion: {r['emotion']})" for r in data["memories"]))
    if data["preferences"]:
        lines.append("Preferences:\n" + "\n".join(f"- {r['category']}: {r['item']}" for r in data["preferences"]))
    if data["personality_snapshots"]:
        latest = data["personality_snapshots"][0]
        prior = data["personality_snapshots"][1] if len(data["personality_snapshots"]) > 1 else None
        line = (f"Latest personality snapshot: openness={latest['openness']}, "
                f"conscientiousness={latest['conscientiousness']}, extraversion={latest['extraversion']}, "
                f"agreeableness={latest['agreeableness']}, neuroticism={latest['neuroticism']}")
        if prior:
            line += (f" (prior snapshot: openness={prior['openness']}, conscientiousness={prior['conscientiousness']}, "
                      f"extraversion={prior['extraversion']}, agreeableness={prior['agreeableness']}, neuroticism={prior['neuroticism']})")
        lines.append(line)
    if relationships:
        lines.append("Friendships active this week:\n" + "\n".join(
            f"- {r['friend_name']}: trust={r['trust_score']}, conflict={r['conflict_score']}, "
            f"support={r['emotional_support']}, summary={r['relationship_summary']}"
            for r in relationships
        ))
    return "\n\n".join(lines) if lines else "(nothing new this week)"


def generate_weekly_digest(user_id: int) -> list[dict] | None:
    """Returns a list of insight-card dicts, or None if the user has no EI
    subject yet, or nothing genuinely new was found (never an error in
    that case -- both are ordinary, common outcomes, not failures)."""
    conn = psycopg2.connect(DB_URL)
    try:
        plain_cur = conn.cursor()
        subject_id = _resolve_subject_id(plain_cur, user_id)
        if subject_id is None:
            return None

        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        data = _fetch_recent_ei_data(cur, subject_id)
        relationships = _fetch_recent_relationship_insights(cur, user_id, subject_id)

        if not any(data[k] for k in ("facts", "beliefs", "memories", "preferences", "personality_snapshots")) and not relationships:
            return None

        prompt = DIGEST_PROMPT.format(
            today=datetime.now().strftime("%A, %Y-%m-%d"),
            lookback_days=LOOKBACK_DAYS,
            categories=", ".join(CARD_CATEGORIES),
            data_summary=_summarize_for_prompt(data, relationships),
        )
        try:
            content, _raw = call_llm([{"role": "user", "content": prompt}])
        except Exception as e:
            print(f"[weekly_digest] LLM call failed for user {user_id}: {e!r}")
            return None

        parsed = extract_json(content)
        if not parsed:
            return None
        cards = parsed.get("cards") or []

        valid_cards = []
        for card in cards:
            category = (card.get("category") or "").strip()
            headline = (card.get("headline") or "").strip()
            body = (card.get("body") or "").strip()
            if category not in CARD_CATEGORIES or not headline or not body:
                continue
            valid_cards.append({
                "category": category,
                "label": (card.get("label") or category.capitalize()).strip(),
                "headline": headline,
                "body": body,
            })
        return valid_cards or None
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate (and print) a weekly digest for one user -- does not write to the DB.")
    parser.add_argument("--user-id", type=int, required=True)
    args = parser.parse_args()

    result = generate_weekly_digest(args.user_id)
    print(json.dumps(result, indent=2) if result else "None (no EI subject, or nothing new this week)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
