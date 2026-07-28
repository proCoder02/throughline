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

-- Added for personalisation + friends features. ADD COLUMN IF NOT EXISTS
-- keeps this file safe to re-run against an already-provisioned database
-- (schema.sql doesn't create tables at runtime, so this is the migration
-- path for existing installs, applied via setup_postgres.py).
-- personalization is validated app-side (personal/office/study) rather
-- than with a CHECK constraint, since Postgres has no
-- "ADD CONSTRAINT IF NOT EXISTS" and this file needs to stay idempotent.
ALTER TABLE users ADD COLUMN IF NOT EXISTS personalization TEXT NOT NULL DEFAULT 'personal';
-- Short numeric code a friend enters to add this user -- UNIQUE alone is
-- fine pre-backfill since Postgres treats multiple NULLs as distinct;
-- the app lazily generates one on first use for pre-existing accounts.
ALTER TABLE users ADD COLUMN IF NOT EXISTS friend_code TEXT UNIQUE;

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

-- One row per background/manual analysis pass on the logged-in user's own
-- speech -- this is what lets a friend see mood *through the day*, not just
-- a single end-of-day snapshot.
CREATE TABLE IF NOT EXISTS mood_logs (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    conversation_id INTEGER REFERENCES conversations (id) ON DELETE SET NULL,
    mood_label TEXT NOT NULL,
    mood_score REAL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Mutual friendship: adding a friend by code inserts both directions, so a
-- friend list is always a plain "WHERE user_id = ?" with no OR/self-join.
CREATE TABLE IF NOT EXISTS friendships (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    friend_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, friend_id),
    CHECK (user_id <> friend_id)
);

-- One row per distinct named person a user has ever labeled a speaker as,
-- across all their conversations. This is what lets the "new speaker
-- detected" prompt offer a pick-list of people already known instead of
-- requiring the name to be retyped (and possibly mistyped/duplicated) every
-- session -- diarization speaker indices are session-local and don't carry
-- identity across sessions or even always within one.
CREATE TABLE IF NOT EXISTS speaker_profiles (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, name)
);

-- Full chat history, one row per message, permanent -- replaces the old
-- per-conversation JSON file on disk. Nothing ever deletes from this table;
-- /chat only ever bounds how many recent rows it re-sends to the LLM as
-- context (a cost control, not a retention policy), so every past chat
-- stays fully readable via GET /conversations/<id>/chat.
CREATE TABLE IF NOT EXISTS chat_messages (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    conversation_id INTEGER NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
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

-- Matches "today's mood for this friend" / "mood through the day" queries.
CREATE INDEX IF NOT EXISTS idx_mood_logs_user_time ON mood_logs (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_friendships_user ON friendships (user_id);

CREATE INDEX IF NOT EXISTS idx_speaker_profiles_user ON speaker_profiles (user_id);

-- Matches "all messages for this conversation, oldest first" (both the
-- LLM-context read in /chat and the full-history read in
-- GET /conversations/<id>/chat).
CREATE INDEX IF NOT EXISTS idx_chat_messages_conversation ON chat_messages (conversation_id, created_at);