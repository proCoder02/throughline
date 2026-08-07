"""
One-time correction pass: facts whose object contains a bare relative-time
word ("today", "tomorrow", "tonight", "yesterday") were extracted from one
line of dialogue/conversation and read back literally forever after --
confirmed real: Ross's account (merged with the TV subject) telling a real
user "it's your birthday today" months after that fact ("birthday: today")
was extracted from a Friends episode. ei_adapter.py / cognitive_reasoning_
demo.py now respect facts.valid_until (see NOT_EXPIRED in ei_adapter.py),
but that only helps if valid_until is actually set -- this script sets it,
for both existing corpus facts and any future ones this needs re-running
against (it's idempotent: only touches rows where valid_until IS NULL).

Rule: a relative-time fact is considered valid for 1 day after extraction,
then expires. Not precise (there's no way to know exactly what "tomorrow"
resolved to), but correct in spirit -- a fact like this should never be
read as still current after enough time has passed, whether it's a
Friends-corpus fact or a real user's own ("needs to pick up dinosaur bones
tomorrow" should also stop being presented as still-pending after a day).

Usage:
    python fix_relative_time_facts.py            # apply
    python fix_relative_time_facts.py --dry-run  # show what would change, no writes
"""
from __future__ import annotations

import argparse
import os

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    raise SystemExit("DATABASE_URL is required")

RELATIVE_TIME_WORDS = ["today", "tomorrow", "tonight", "yesterday"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Expire facts with bare relative-time objects, 1 day after extraction.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing.")
    args = parser.parse_args()

    conn = psycopg2.connect(DB_URL)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        # Case-SENSITIVE (LIKE, not ILIKE) is deliberate: every genuine
        # relative-time extraction observed uses the lowercase word ("today",
        # "tonight", "tomorrow at 3:30"), while a real false positive turned
        # up during testing -- "watches: Entertainment Tonight" -- where
        # "Tonight" is capitalized as part of a proper noun (a TV show
        # title), not a temporal reference. Case-sensitivity is what tells
        # them apart here, not a word-boundary check (both are whole words).
        where_clause = " OR ".join("object LIKE %s" for _ in RELATIVE_TIME_WORDS)
        params = [f"%{w}%" for w in RELATIVE_TIME_WORDS]

        cur.execute(
            f"""
            SELECT f.id, s.canonical_name, f.predicate, f.object, f.created_at
            FROM emotional_intelligence.facts f
            JOIN emotional_intelligence.subjects s ON s.id = f.subject_id
            WHERE ({where_clause}) AND f.valid_until IS NULL
            ORDER BY f.created_at
            """,
            params,
        )
        rows = cur.fetchall()
        print(f"facts_to_expire={len(rows)}")
        for r in rows:
            print(f"  {r['canonical_name']}: {r['predicate']} = {r['object']} (extracted {r['created_at']})")

        if not args.dry_run and rows:
            ids = [r["id"] for r in rows]
            cur.execute(
                "UPDATE emotional_intelligence.facts SET valid_until = created_at + interval '1 day' "
                "WHERE id = ANY(%s)",
                (ids,),
            )
            conn.commit()
            print(f"\nExpired {len(ids)} facts (valid_until = created_at + 1 day).")
        elif args.dry_run:
            print("\nDry run -- no changes made.")
    finally:
        cur.close()
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
