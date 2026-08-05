"""
Speaker normalization pass -- resolves the noisy raw speaker_name values in
episode_speaker_transcript (838 distinct labels, mostly casing/parenthetical
variants of the same handful of real characters) into canonical `subjects`
rows, recording every raw->canonical mapping in `speaker_aliases` so it's
inspectable/correctable (UPDATE the table, not the code) rather than a
silent in-memory dict.

Deterministic, not LLM-based (per the "Normalizer" design decision from
COGNITIVE_EXTRACTION_PIPELINE_PLAN.md): strips parenthetical stage
directions (e.g. "ROSS (V.O.)" -> "ROSS"), then groups case/whitespace
variants together. The canonical name for each group is the most-frequently
-occurring exact original casing within that group (not a synthesized
.title()-cased string -- Python's str.title() mangles names with
apostrophes, e.g. "Ross's Mom" -> "Ross'S Mom").

Idempotent: re-running only adds subjects/aliases for speaker_name values
not already mapped.

Usage:
    python normalize_speakers.py
"""
from __future__ import annotations

import os
import re
from collections import Counter, defaultdict

import psycopg2
from dotenv import load_dotenv

load_dotenv()

DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    raise SystemExit("DATABASE_URL is required")

PARENTHETICAL_RE = re.compile(r"\s*[\(\[][^)\]]*[\)\]]\s*")
WHITESPACE_RE = re.compile(r"\s+")


def normalization_key(raw_name: str) -> str:
    """Grouping key: strip stage-direction parentheticals, collapse
    whitespace, lowercase. Two raw labels sharing this key are treated as
    the same character."""
    stripped = PARENTHETICAL_RE.sub(" ", raw_name)
    stripped = WHITESPACE_RE.sub(" ", stripped).strip()
    return stripped.lower()


def main() -> int:
    conn = psycopg2.connect(DB_URL)
    conn.autocommit = False
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT speaker_name, COUNT(*) FROM emotional_intelligence.episode_speaker_transcript "
            "GROUP BY speaker_name"
        )
        raw_counts = cur.fetchall()  # [(raw_speaker_name, turn_count), ...]

        groups: dict[str, Counter] = defaultdict(Counter)
        for raw_name, turn_count in raw_counts:
            key = normalization_key(raw_name)
            if not key:
                continue
            groups[key][raw_name] += turn_count

        cur.execute("SELECT raw_speaker_name FROM emotional_intelligence.speaker_aliases")
        already_aliased = {row[0] for row in cur.fetchall()}

        subjects_created = 0
        aliases_created = 0
        for key, variants in groups.items():
            canonical_name = variants.most_common(1)[0][0]

            cur.execute(
                "SELECT id FROM emotional_intelligence.subjects WHERE lower(canonical_name) = %s",
                (key,),
            )
            row = cur.fetchone()
            if row:
                subject_id = row[0]
            else:
                cur.execute(
                    "INSERT INTO emotional_intelligence.subjects (canonical_name, source) "
                    "VALUES (%s, 'friends_transcript') RETURNING id",
                    (canonical_name,),
                )
                subject_id = cur.fetchone()[0]
                subjects_created += 1

            for raw_name in variants:
                if raw_name in already_aliased:
                    continue
                cur.execute(
                    "INSERT INTO emotional_intelligence.speaker_aliases (raw_speaker_name, subject_id) "
                    "VALUES (%s, %s) ON CONFLICT (raw_speaker_name) DO NOTHING",
                    (raw_name, subject_id),
                )
                aliases_created += 1

        conn.commit()

        cur.execute("SELECT COUNT(*) FROM emotional_intelligence.subjects")
        total_subjects = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM emotional_intelligence.speaker_aliases")
        total_aliases = cur.fetchone()[0]

        print(f"raw_speaker_labels={len(raw_counts)}")
        print(f"subjects_created={subjects_created} (total_subjects={total_subjects})")
        print(f"aliases_created={aliases_created} (total_aliases={total_aliases})")

        # Top 10 subjects by total turn count -- a sanity check that the
        # main 6 characters collapsed correctly and dominate by volume,
        # names only, no dialogue content.
        cur.execute(
            """
            SELECT s.canonical_name, SUM(t.turn_count) AS total_turns
            FROM emotional_intelligence.subjects s
            JOIN emotional_intelligence.speaker_aliases a ON a.subject_id = s.id
            JOIN (
                SELECT speaker_name, COUNT(*) AS turn_count
                FROM emotional_intelligence.episode_speaker_transcript
                GROUP BY speaker_name
            ) t ON t.speaker_name = a.raw_speaker_name
            GROUP BY s.canonical_name
            ORDER BY total_turns DESC
            LIMIT 10
            """
        )
        print("\nTop 10 subjects by turn count:")
        for name, count in cur.fetchall():
            print(f"  {name}: {count}")
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
