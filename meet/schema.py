"""Meet's own tables, created on first use -- same lazy, locked, idempotent
pattern as ops/schema.py, so turning the feature on needs no separate
migration step and turning it off leaves every existing table untouched."""
from __future__ import annotations

import threading

_ensured = False
_lock = threading.Lock()

DDL = """
CREATE TABLE IF NOT EXISTS meet_cards (
    user_id INTEGER PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
    headline TEXT NOT NULL DEFAULT '',
    about TEXT NOT NULL DEFAULT '',
    interests TEXT[] NOT NULL DEFAULT '{}',
    likes TEXT[] NOT NULL DEFAULT '{}',
    sharing BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per meeting. The pair is stored ordered (user_lo < user_hi) so
-- "the encounter between A and B" has exactly one spelling.
CREATE TABLE IF NOT EXISTS meet_encounters (
    id SERIAL PRIMARY KEY,
    user_lo INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    user_hi INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    method TEXT NOT NULL DEFAULT 'bluetooth',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    lo_confirmed_at TIMESTAMPTZ,
    hi_confirmed_at TIMESTAMPTZ,
    declined_by INTEGER,
    declined_at TIMESTAMPTZ,
    removed_by INTEGER,
    removed_at TIMESTAMPTZ,
    CHECK (user_lo < user_hi)
);
CREATE INDEX IF NOT EXISTS idx_meet_encounters_pair ON meet_encounters (user_lo, user_hi, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_meet_encounters_hi ON meet_encounters (user_hi, created_at DESC);

-- Richer card (who I am / personality / stay in touch). Added columns, so
-- cards saved before this keep working with empty values.
ALTER TABLE meet_cards ADD COLUMN IF NOT EXISTS organization TEXT NOT NULL DEFAULT '';
ALTER TABLE meet_cards ADD COLUMN IF NOT EXISTS city TEXT NOT NULL DEFAULT '';
ALTER TABLE meet_cards ADD COLUMN IF NOT EXISTS education TEXT NOT NULL DEFAULT '';
ALTER TABLE meet_cards ADD COLUMN IF NOT EXISTS traits TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE meet_cards ADD COLUMN IF NOT EXISTS style TEXT NOT NULL DEFAULT '';
ALTER TABLE meet_cards ADD COLUMN IF NOT EXISTS links TEXT[] NOT NULL DEFAULT '{}';

-- First-impression front of the card: a one-line hook, a conversation
-- starter, and the card's colour.
ALTER TABLE meet_cards ADD COLUMN IF NOT EXISTS hook TEXT NOT NULL DEFAULT '';
ALTER TABLE meet_cards ADD COLUMN IF NOT EXISTS ask_me TEXT NOT NULL DEFAULT '';
ALTER TABLE meet_cards ADD COLUMN IF NOT EXISTS color TEXT NOT NULL DEFAULT '';
"""


def ensure_meet_schema(cur, conn) -> None:
    global _ensured
    if _ensured:
        return
    with _lock:
        if _ensured:
            return
        cur.execute(DDL)
        conn.commit()
        _ensured = True
