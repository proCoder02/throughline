-- ============================================================================
-- nudges -- isolated Postgres schema for the proactive-nudge feature
-- (overdue tasks, mood-trend shifts, relationship touchpoints). Mirrors
-- emotional_intelligence's isolation pattern: this module owns everything
-- in this schema, only ever READS public tables it needs (tasks, mood_logs,
-- friendships, calls, call_participants, users), never writes them, and
-- every statement here is IF NOT EXISTS so re-running this file is always
-- safe.
--
-- Run with: python apply_nudges_schema.py
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS nudges;

-- ----------------------------------------------------------------------------
-- user_settings -- one row per user who has ever touched nudge/cognitive-
-- intelligence settings. No row yet means "use the defaults" (both on) --
-- see nudge_engine.get_user_settings, which returns the same defaults for
-- a missing row rather than requiring a row to exist for every user up
-- front (an opt-OUT model: nudges are on until a user turns them off).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nudges.user_settings (
    user_id INTEGER PRIMARY KEY,
    nudges_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    -- Reserved for future use -- a per-user opt-out for the emotional_intelligence
    -- pipeline (currently gated only by the global EMOTIONAL_INTELLIGENCE_ENABLED
    -- flag in ei_adapter.py). Not yet enforced anywhere; stored here now per
    -- request so both settings live in one place instead of two later.
    cognitive_intelligence_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    -- Enforced directly in app.py's email_reminder_worker (a JOIN + COALESCE
    -- against this table) -- when false, that worker skips sending the
    -- reminder email/push for this user's tasks entirely.
    task_reminder_notifications_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    -- Enforced directly in app.py's run_background_analysis -- when false,
    -- the topics/questions chips are blanked out of that call's
    -- 'background_update' push (the extraction LLM call itself still runs,
    -- since topics/questions come from the same shared call as
    -- tasks/speakers/mood -- this only suppresses what's shown, not the
    -- underlying extraction cost).
    tags_questions_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    -- The master switch's OWN state -- deliberately NOT derived from the
    -- four fields above (e.g. "true only if all four are true"). Turning
    -- this off cascades down and turns off all four; turning an individual
    -- feature off does NOT cascade up and flip this -- it stays showing
    -- whatever it was last explicitly set to. See app.py's
    -- update_nudge_settings for the cascade-down-only logic.
    smart_features_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- ADD COLUMN IF NOT EXISTS for the lines above -- the table already existed
-- in the real DB before this revision (same reasoning as
-- emotional_intelligence_schema.sql's search_tsv additions: CREATE TABLE
-- IF NOT EXISTS alone won't add a column to a table that already exists).
ALTER TABLE nudges.user_settings ADD COLUMN IF NOT EXISTS task_reminder_notifications_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE nudges.user_settings ADD COLUMN IF NOT EXISTS tags_questions_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE nudges.user_settings ADD COLUMN IF NOT EXISTS smart_features_enabled BOOLEAN NOT NULL DEFAULT TRUE;

-- ----------------------------------------------------------------------------
-- sent_log -- one row per nudge actually delivered, so each checker can ask
-- "have I already nudged this (user, type, reference) recently" before
-- sending again. reference_id is TEXT (not INTEGER) since it holds
-- different ID shapes across nudge_type -- a task id, a friend's user id,
-- or a synthetic "old_mood->new_mood" key for mood-trend nudges (which
-- aren't tied to one row).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nudges.sent_log (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    nudge_type TEXT NOT NULL,  -- 'overdue_task' | 'mood_trend' | 'relationship_touchpoint' -- app-validated
    reference_id TEXT NOT NULL,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_nudges_sent_log_lookup
    ON nudges.sent_log (user_id, nudge_type, reference_id, sent_at DESC);
