"""
One-time (idempotent) migration: adds a generated, indexed search_tsv
column to each table ei_adapter.py full-text-searches (facts, preferences,
beliefs, memories), so _search_table()'s query is a GIN index lookup
instead of computing to_tsvector(...) fresh over every row on every call.
Safe to run repeatedly -- every statement is IF NOT EXISTS.

Usage:
    python ensure_search_indexes.py
"""
from __future__ import annotations

import os

import psycopg2
from dotenv import load_dotenv

load_dotenv()

DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    raise SystemExit("DATABASE_URL is required")

# table -> SQL expression (in terms of the table's own columns) to index.
TABLES = {
    "facts": "coalesce(predicate, '') || ' ' || coalesce(object, '')",
    "preferences": "coalesce(category, '') || ' ' || coalesce(item, '')",
    "beliefs": "coalesce(topic, '') || ' ' || coalesce(belief, '')",
    "memories": "coalesce(summary, '')",
}


def main() -> int:
    conn = psycopg2.connect(DB_URL)
    conn.autocommit = True
    cur = conn.cursor()
    try:
        for table, expr in TABLES.items():
            cur.execute(
                f"ALTER TABLE emotional_intelligence.{table} "
                f"ADD COLUMN IF NOT EXISTS search_tsv tsvector "
                f"GENERATED ALWAYS AS (to_tsvector('english', {expr})) STORED"
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_ei_{table}_search_tsv "
                f"ON emotional_intelligence.{table} USING GIN (search_tsv)"
            )
            print(f"ok: emotional_intelligence.{table}.search_tsv (+ GIN index)")
    finally:
        cur.close()
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
