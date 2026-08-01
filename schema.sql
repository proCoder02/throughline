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

-- Tags each conversation with the personalization mode active when it was
-- started (personal/office/study), so the Chats list can be filtered down
-- to just one category instead of always showing every conversation mixed
-- together. Validated app-side, same reasoning as users.personalization.
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'personal';
CREATE INDEX IF NOT EXISTS idx_conversations_user_category ON conversations (user_id, category);

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

-- Case-insensitive on top of the exact-match UNIQUE above, so "Kunal" and
-- "kunal" resolve to one profile instead of forking into two -- app code
-- looks profiles up via lower(name) (get_or_create_profile_id) before ever
-- relying on this constraint, but the index makes that guarantee enforced
-- at the DB layer too, not just by convention in application code.
CREATE UNIQUE INDEX IF NOT EXISTS idx_speaker_profiles_user_name_ci
    ON speaker_profiles (user_id, lower(name));

-- Links each observation to the canonical profile it's about (resolved
-- case-insensitively), instead of only the free-text label captured at
-- analysis time -- lets one real person be tracked across every
-- conversation they appear in, however their name was capitalized that
-- session.
ALTER TABLE personality_notes ADD COLUMN IF NOT EXISTS profile_id INTEGER REFERENCES speaker_profiles (id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_notes_profile ON personality_notes (profile_id);

-- Deleting a profile (DELETE /profiles/<id>) should purge that person's
-- observations along with their identity -- not just unlink them, which
-- would leave the notes' free-text speaker_label to resurface the "deleted"
-- person right back in the list via the legacy fallback grouping in
-- list_profiles(). CASCADE (not the original SET NULL) makes delete mean
-- delete. Idempotent: drops+recreates only if the old SET NULL behavior is
-- still in place.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'personality_notes_profile_id_fkey' AND confdeltype != 'c'
    ) THEN
        ALTER TABLE personality_notes DROP CONSTRAINT personality_notes_profile_id_fkey;
        ALTER TABLE personality_notes
            ADD CONSTRAINT personality_notes_profile_id_fkey
            FOREIGN KEY (profile_id) REFERENCES speaker_profiles (id) ON DELETE CASCADE;
    END IF;
END $$;

-- The actual many-to-many mapping between people and categories, stored
-- explicitly rather than only derived by joining personality_notes ->
-- conversations.category: one profile can appear under several categories
-- (an office conversation one day, a personal one the next) and one
-- category holds many profiles. Storing it here means the association
-- survives even after the source conversation is deleted (conversation_id
-- on personality_notes/tasks/mood_logs is ON DELETE SET NULL, which would
-- otherwise erase the category link entirely).
CREATE TABLE IF NOT EXISTS profile_categories (
    id SERIAL PRIMARY KEY,
    profile_id INTEGER NOT NULL REFERENCES speaker_profiles (id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (profile_id, category)
);
CREATE INDEX IF NOT EXISTS idx_profile_categories_profile ON profile_categories (profile_id);

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

-- conversation_id NULL = a /chat/global message -- the cross-session
-- assistant thread isn't scoped to any one conversation, so it needs a
-- home in this same table that isn't tied to one. Safe/idempotent to
-- rerun: DROP NOT NULL on an already-nullable column is a no-op.
ALTER TABLE chat_messages ALTER COLUMN conversation_id DROP NOT NULL;
CREATE INDEX IF NOT EXISTS idx_chat_messages_global ON chat_messages (user_id, created_at)
    WHERE conversation_id IS NULL;

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
-- history built up over time. testing dev branch
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

-- ============================================================================
-- Persona onboarding -- captured once, right after registration, and fed
-- into the LLM system prompts (chat + analysis) so responses/extraction can
-- be tailored to the person instead of generic. One row per user; its mere
-- existence is also the "has this user completed onboarding" flag.
-- hobbies/interests are multi-select from a small fixed list (validated
-- app-side, same convention as personalization/category) so a Postgres
-- array is a natural fit -- no join table needed for a bounded pick-list.
-- ============================================================================
CREATE TABLE IF NOT EXISTS user_personas (
    user_id INTEGER PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
    age INTEGER,
    gender TEXT,
    language_preference TEXT,
    hobbies TEXT[] NOT NULL DEFAULT '{}',
    interests TEXT[] NOT NULL DEFAULT '{}',
    occupation TEXT,
    primary_goal TEXT,
    completed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- Custom categories -- personal/office/study are always available (built
-- into app code, not a DB row); this table holds each user's additional
-- ones. Referenced by name (not id) from conversations.category / tasks'
-- derived category / users.personalization, same as the 3 built-ins, so
-- nothing about the existing category plumbing needs to change -- this
-- just widens what app-side validation accepts for a given user.
-- ============================================================================
CREATE TABLE IF NOT EXISTS user_categories (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, name)
);
CREATE INDEX IF NOT EXISTS idx_user_categories_user ON user_categories (user_id);

-- Lets a task's description/due_date be corrected after extraction (the LLM
-- sometimes mishears/misparses) without deleting and recreating the task
-- (which would lose reminder_at/email_sent history).
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;

-- Per-relationship nickname -- each direction of a friendship is already
-- its own row (see friendships above), so "the name *I* call this friend"
-- naturally lives on *my* row without colliding with what they call me.
ALTER TABLE friendships ADD COLUMN IF NOT EXISTS nickname TEXT;

-- ============================================================================
-- Cross-session topic threading -- the reliable record of every
-- conversation a given profile (person) appeared in, recorded at
-- extraction time regardless of whether that pass happened to produce a
-- behavioral observation for them. personality_notes.conversation_id is
-- NOT a complete substitute for this: a speaker can appear in a session
-- with zero qualifying observations that batch, in which case no
-- personality_notes row (and thus no conversation link) would exist for
-- them at all. This table is the actual "which sessions was this person
-- in" index that /chat/global's cross-conversation retrieval depends on.
-- ============================================================================
CREATE TABLE IF NOT EXISTS profile_conversations (
    id SERIAL PRIMARY KEY,
    profile_id INTEGER NOT NULL REFERENCES speaker_profiles (id) ON DELETE CASCADE,
    conversation_id INTEGER NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (profile_id, conversation_id)
);
CREATE INDEX IF NOT EXISTS idx_profile_conversations_profile ON profile_conversations (profile_id, created_at DESC);

-- ============================================================================
-- Voice calling (1:1 + group), via LiveKit. One row shape covers both --
-- a 1:1 call is just a call with 2 call_participants rows, group is N --
-- no special-casing, matching how LiveKit itself treats rooms uniformly.
-- Calling is friends-only (app-side check against the friendships table,
-- same trust boundary mood-sharing already uses); recording/transcription
-- always runs on call end (LiveKit Egress -> Deepgram -> conversations),
-- same pipeline solo live-listening already uses.
-- ============================================================================
CREATE TABLE IF NOT EXISTS calls (
    id SERIAL PRIMARY KEY,
    initiator_user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    room_name TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL DEFAULT 'personal',
    status TEXT NOT NULL DEFAULT 'ringing',
    conversation_id INTEGER REFERENCES conversations (id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS call_participants (
    id SERIAL PRIMARY KEY,
    call_id INTEGER NOT NULL REFERENCES calls (id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'invited',
    joined_at TIMESTAMPTZ,
    left_at TIMESTAMPTZ,
    UNIQUE (call_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_call_participants_call ON call_participants (call_id);
CREATE INDEX IF NOT EXISTS idx_call_participants_user ON call_participants (user_id);
CREATE INDEX IF NOT EXISTS idx_calls_initiator ON calls (initiator_user_id);

-- Per-participant recording upload (mobile clients upload their own local
-- mic recording instead of the browser's mixed-stream capture -- see
-- upload_call_recording's `scope=own` branch). Separate from
-- calls.conversation_id, which tracks the legacy single mixed-upload flow.
ALTER TABLE call_participants ADD COLUMN IF NOT EXISTS recording_uploaded_at TIMESTAMPTZ;
ALTER TABLE call_participants ADD COLUMN IF NOT EXISTS conversation_id INTEGER REFERENCES conversations (id) ON DELETE SET NULL;