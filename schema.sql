-- ============================================================================
-- Postgres schema for the speech2text app.
-- Run with: psql -U your_user -d your_db -f schema.sql
-- (or via setup_postgres.py, which applies this same schema from Python)
--
-- Two real changes vs. the SQLite version, both improvements, not just
-- syntax translation:
--   1. Timestamps are TIMESTAMPTZ, not ISO-format TEXT. The app currently
--      compares reminder times as strings ("2026-07-24..." <= "2026-07-24...")
--      which happens to work because ISO 8601 sorts the same lexicographically
--      as chronologically -- but it's fragile. Native timestamp comparison in
--      Postgres is correct by construction, not by lucky string formatting.
--   2. reminder_sent / email_sent are BOOLEAN, not INTEGER 0/1.
-- ============================================================================

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    email TEXT,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS conversations (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    title TEXT,
    raw_transcript TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS tasks (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    conversation_id INTEGER REFERENCES conversations (id) ON DELETE SET NULL,
    description TEXT NOT NULL,
    owner TEXT,
    due_date TEXT,                     -- kept as free-text display phrase ("Friday", "tonight") -- this is intentional, not a translation gap
    reminder_at TIMESTAMPTZ,
    reminder_sent BOOLEAN NOT NULL DEFAULT FALSE,
    email_sent BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS personality_notes (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    conversation_id INTEGER REFERENCES conversations (id) ON DELETE SET NULL,
    speaker_label TEXT NOT NULL,
    observation TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Standard per-user lookups (every list_tasks / list_profiles / conversation
-- query filters by user_id first)
CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations (user_id);
CREATE INDEX IF NOT EXISTS idx_tasks_user ON tasks (user_id);
CREATE INDEX IF NOT EXISTS idx_notes_user ON personality_notes (user_id);

-- Matches the actual list_tasks query pattern (WHERE user_id = ? AND status = ?)
CREATE INDEX IF NOT EXISTS idx_tasks_user_status ON tasks (user_id, status);

-- Partial index for the email_reminder_worker's hot query -- it only ever
-- looks at rows where email_sent is still false, so indexing only those
-- rows keeps the index small and the scan fast even with a large task
-- history built up over time.
CREATE INDEX IF NOT EXISTS idx_tasks_pending_email
    ON tasks (reminder_at)
    WHERE email_sent = FALSE AND reminder_at IS NOT NULL;