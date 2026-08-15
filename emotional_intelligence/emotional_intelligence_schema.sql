-- ============================================================================
-- emotional_intelligence -- isolated Postgres schema for cognitive-
-- extraction research/prototyping. Kept separate from `public` (the main
-- app's schema) until "near perfect," per standing instruction: no
-- production/backend/Flutter changes for this work.
--
-- REVISION 2 -- identity model corrected. Revision 1 pointed every table's
-- user_id/conversation_id at public.users/public.conversations, assuming
-- the research subject would be a real app user's solo transcript. The
-- actual research dataset (Friends episode transcripts) has neither: no
-- app-user row exists for a TV character, and unlike solo dictation, one
-- episode/scene has MANY speakers, not one owner. This revision:
--   - Introduces subjects (research-local character identity) and episodes
--     (research-local "conversation" unit) instead of referencing public.*.
--   - Introduces speaker_aliases as an explicit, inspectable normalization
--     table (raw speaker_name -> canonical subject), rather than folding
--     name-matching invisibly into extraction code.
--   - Splits tables by what they actually describe: conversation_summaries/
--     topics/entities/events are EPISODE-scoped (a scene's topic isn't
--     "owned" by one character); facts/preferences/beliefs/memories/
--     personality_snapshots are SUBJECT-scoped (these describe one person,
--     extracted per-speaker from a multi-party transcript, same pattern the
--     main app already uses for per-speaker observations in a call).
-- All tables were empty (0 rows) under revision 1.
--
-- SAFETY NOTE: this file previously opened with
-- `DROP SCHEMA IF EXISTS emotional_intelligence CASCADE`, on the assumption
-- that this schema held nothing but empty tables this file itself owned.
-- That assumption was wrong -- episode_speaker_transcript (and its upstream
-- source tables: transcript, transcript_scenes, transcript_speakers,
-- transcript_speaker_turns) also live in this schema and are NOT owned by
-- this file, and the CASCADE drop destroyed them along with everything
-- else. Every statement below is now IF NOT EXISTS and this file never
-- drops the schema itself -- re-running it is safe and will never touch
-- tables it doesn't define.
--
-- Run with: psql "%DATABASE_URL%" -f emotional_intelligence_schema.sql
-- (or via apply_emotional_intelligence_schema.py)
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS emotional_intelligence;

-- ----------------------------------------------------------------------------
-- subjects -- research-local character identity (the analog of public.users
-- for this dataset). One row per real distinct character, resolved from the
-- noisy raw speaker_name values via speaker_aliases below.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS emotional_intelligence.subjects (
    id SERIAL PRIMARY KEY,
    canonical_name TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL DEFAULT 'friends_transcript',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ----------------------------------------------------------------------------
-- speaker_aliases -- explicit, inspectable/correctable normalization map
-- from raw episode_speaker_transcript.speaker_name values to a canonical
-- subject. This IS the "Normalizer" layer, made a real table instead of
-- inline string-matching in a script: wrong mappings are a data fix
-- (UPDATE this table), not a code change.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS emotional_intelligence.speaker_aliases (
    id SERIAL PRIMARY KEY,
    raw_speaker_name TEXT NOT NULL UNIQUE,
    subject_id INTEGER REFERENCES emotional_intelligence.subjects (id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ei_speaker_aliases_subject ON emotional_intelligence.speaker_aliases (subject_id);

-- ----------------------------------------------------------------------------
-- episodes -- research-local "conversation" unit (the analog of
-- public.conversations for this dataset). One row per distinct episode_id
-- already present in episode_speaker_transcript.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS emotional_intelligence.episodes (
    id SERIAL PRIMARY KEY,
    season INTEGER NOT NULL,
    episode_number INTEGER NOT NULL,
    episode_id TEXT NOT NULL UNIQUE,
    episode_name TEXT,
    source_url TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ei_episodes_season ON emotional_intelligence.episodes (season, episode_number);

-- ----------------------------------------------------------------------------
-- analysis_runs -- one row per LLM extraction call, independent of what it
-- produced (see prior revision's reasoning: "why are there no facts for
-- episode X" needs to be answerable). episode_id is the common case (one
-- run per episode/scene pass); subject_id is set only for a
-- subject-specific batch run (e.g. a future personality_snapshots pass).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS emotional_intelligence.analysis_runs (
    id SERIAL PRIMARY KEY,
    episode_id INTEGER REFERENCES emotional_intelligence.episodes (id) ON DELETE SET NULL,
    subject_id INTEGER REFERENCES emotional_intelligence.subjects (id) ON DELETE SET NULL,
    run_type TEXT NOT NULL DEFAULT 'episode_analysis',  -- 'episode_analysis' | 'personality_batch' | 'relationship_batch' -- app-validated
    model TEXT,
    prompt_version INTEGER NOT NULL,
    latency_ms INTEGER,
    tokens INTEGER,
    cost REAL,
    status TEXT NOT NULL DEFAULT 'success',  -- 'success' | 'error' | 'timeout' -- app-validated
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ei_analysis_runs_episode ON emotional_intelligence.analysis_runs (episode_id);
CREATE INDEX IF NOT EXISTS idx_ei_analysis_runs_subject ON emotional_intelligence.analysis_runs (subject_id);
CREATE INDEX IF NOT EXISTS idx_ei_analysis_runs_status ON emotional_intelligence.analysis_runs (status) WHERE status != 'success';

-- ----------------------------------------------------------------------------
-- conversation_summaries -- EPISODE-scoped (a scene's summary isn't owned
-- by one character). One row per episode.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS emotional_intelligence.conversation_summaries (
    episode_id INTEGER PRIMARY KEY REFERENCES emotional_intelligence.episodes (id) ON DELETE CASCADE,
    analysis_run_id INTEGER REFERENCES emotional_intelligence.analysis_runs (id) ON DELETE SET NULL,
    summary TEXT NOT NULL,
    key_points TEXT[] NOT NULL DEFAULT '{}',
    action_items TEXT[] NOT NULL DEFAULT '{}',
    sentiment TEXT,
    -- embedding vector(1536) -- add once semantic summary search is implemented
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ei_conversation_summaries_run ON emotional_intelligence.conversation_summaries (analysis_run_id);

-- ----------------------------------------------------------------------------
-- topics / episode_topics -- EPISODE-scoped, globally deduped by name
-- (not per-subject -- "the trivia game" isn't owned by one character).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS emotional_intelligence.topics (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    analysis_run_id INTEGER REFERENCES emotional_intelligence.analysis_runs (id) ON DELETE SET NULL,
    -- embedding vector(1536) -- add once semantic topic clustering is implemented
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ei_topics_run ON emotional_intelligence.topics (analysis_run_id);

CREATE TABLE IF NOT EXISTS emotional_intelligence.episode_topics (
    id SERIAL PRIMARY KEY,
    episode_id INTEGER NOT NULL REFERENCES emotional_intelligence.episodes (id) ON DELETE CASCADE,
    topic_id INTEGER NOT NULL REFERENCES emotional_intelligence.topics (id) ON DELETE CASCADE,
    analysis_run_id INTEGER REFERENCES emotional_intelligence.analysis_runs (id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (episode_id, topic_id)
);
CREATE INDEX IF NOT EXISTS idx_ei_episode_topics_topic ON emotional_intelligence.episode_topics (topic_id);
CREATE INDEX IF NOT EXISTS idx_ei_episode_topics_run ON emotional_intelligence.episode_topics (analysis_run_id);

-- ----------------------------------------------------------------------------
-- entities / episode_entities -- EPISODE-scoped, same reasoning as topics.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS emotional_intelligence.entities (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    entity_type TEXT,
    analysis_run_id INTEGER REFERENCES emotional_intelligence.analysis_runs (id) ON DELETE SET NULL,
    -- embedding vector(1536) -- add once semantic entity search is implemented
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (name, entity_type)
);
CREATE INDEX IF NOT EXISTS idx_ei_entities_run ON emotional_intelligence.entities (analysis_run_id);

CREATE TABLE IF NOT EXISTS emotional_intelligence.episode_entities (
    id SERIAL PRIMARY KEY,
    episode_id INTEGER NOT NULL REFERENCES emotional_intelligence.episodes (id) ON DELETE CASCADE,
    entity_id INTEGER NOT NULL REFERENCES emotional_intelligence.entities (id) ON DELETE CASCADE,
    analysis_run_id INTEGER REFERENCES emotional_intelligence.analysis_runs (id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (episode_id, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_ei_episode_entities_entity ON emotional_intelligence.episode_entities (entity_id);
CREATE INDEX IF NOT EXISTS idx_ei_episode_entities_run ON emotional_intelligence.episode_entities (analysis_run_id);

-- ----------------------------------------------------------------------------
-- events -- primarily EPISODE-scoped (when/where it was mentioned);
-- subject_id nullable since an event can involve several people and
-- participants (free text) already captures "who", not one hard owner.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS emotional_intelligence.events (
    id SERIAL PRIMARY KEY,
    episode_id INTEGER REFERENCES emotional_intelligence.episodes (id) ON DELETE SET NULL,
    subject_id INTEGER REFERENCES emotional_intelligence.subjects (id) ON DELETE SET NULL,
    analysis_run_id INTEGER REFERENCES emotional_intelligence.analysis_runs (id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    description TEXT,
    participants TEXT[] NOT NULL DEFAULT '{}',
    event_date DATE,
    importance REAL,
    -- embedding vector(1536) -- add once semantic event search is implemented
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ei_events_episode ON emotional_intelligence.events (episode_id);
CREATE INDEX IF NOT EXISTS idx_ei_events_subject ON emotional_intelligence.events (subject_id);
CREATE INDEX IF NOT EXISTS idx_ei_events_run ON emotional_intelligence.events (analysis_run_id);

-- ----------------------------------------------------------------------------
-- beliefs -- SUBJECT-scoped (a belief belongs to whoever holds it).
-- episode_id is the source reference (where it was said), nullable since a
-- belief can in principle be reinforced without a fresh source.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS emotional_intelligence.beliefs (
    id SERIAL PRIMARY KEY,
    subject_id INTEGER NOT NULL REFERENCES emotional_intelligence.subjects (id) ON DELETE CASCADE,
    episode_id INTEGER REFERENCES emotional_intelligence.episodes (id) ON DELETE SET NULL,
    analysis_run_id INTEGER REFERENCES emotional_intelligence.analysis_runs (id) ON DELETE SET NULL,
    topic TEXT NOT NULL,
    belief TEXT NOT NULL,
    confidence REAL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_ei_beliefs_subject ON emotional_intelligence.beliefs (subject_id);
CREATE INDEX IF NOT EXISTS idx_ei_beliefs_subject_topic ON emotional_intelligence.beliefs (subject_id, topic);
CREATE INDEX IF NOT EXISTS idx_ei_beliefs_run ON emotional_intelligence.beliefs (analysis_run_id);
-- ei_adapter.py full-text-searches this table by question relevance (see
-- PRODUCTION_READINESS_ANALYSIS.md item 3) -- indexed rather than computing
-- to_tsvector(...) fresh per row on every call.
ALTER TABLE emotional_intelligence.beliefs ADD COLUMN IF NOT EXISTS search_tsv tsvector
    GENERATED ALWAYS AS (to_tsvector('english', coalesce(topic, '') || ' ' || coalesce(belief, ''))) STORED;
CREATE INDEX IF NOT EXISTS idx_ei_beliefs_search_tsv ON emotional_intelligence.beliefs USING GIN (search_tsv);

-- ----------------------------------------------------------------------------
-- facts -- SUBJECT-scoped.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS emotional_intelligence.facts (
    id SERIAL PRIMARY KEY,
    subject_id INTEGER NOT NULL REFERENCES emotional_intelligence.subjects (id) ON DELETE CASCADE,
    episode_id INTEGER REFERENCES emotional_intelligence.episodes (id) ON DELETE SET NULL,
    analysis_run_id INTEGER REFERENCES emotional_intelligence.analysis_runs (id) ON DELETE SET NULL,
    subject TEXT NOT NULL,       -- the fact triple's grammatical subject (usually == subjects.canonical_name, kept as text for the triple shape)
    predicate TEXT NOT NULL,
    object TEXT NOT NULL,
    confidence REAL,
    source TEXT,
    valid_from TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_until TIMESTAMPTZ,
    -- embedding vector(1536) -- add once semantic fact retrieval is implemented
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ei_facts_subject ON emotional_intelligence.facts (subject_id);
CREATE INDEX IF NOT EXISTS idx_ei_facts_subject_predicate ON emotional_intelligence.facts (subject_id, predicate);
CREATE INDEX IF NOT EXISTS idx_ei_facts_subject_valid ON emotional_intelligence.facts (subject_id, valid_until);
CREATE INDEX IF NOT EXISTS idx_ei_facts_run ON emotional_intelligence.facts (analysis_run_id);
-- ei_adapter.py full-text-searches this table by question relevance (see
-- PRODUCTION_READINESS_ANALYSIS.md item 3) -- indexed rather than computing
-- to_tsvector(...) fresh per row on every call.
ALTER TABLE emotional_intelligence.facts ADD COLUMN IF NOT EXISTS search_tsv tsvector
    GENERATED ALWAYS AS (to_tsvector('english', coalesce(predicate, '') || ' ' || coalesce(object, ''))) STORED;
CREATE INDEX IF NOT EXISTS idx_ei_facts_search_tsv ON emotional_intelligence.facts USING GIN (search_tsv);

CREATE TABLE IF NOT EXISTS emotional_intelligence.fact_versions (
    id SERIAL PRIMARY KEY,
    fact_id INTEGER NOT NULL REFERENCES emotional_intelligence.facts (id) ON DELETE CASCADE,
    analysis_run_id INTEGER REFERENCES emotional_intelligence.analysis_runs (id) ON DELETE SET NULL,
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object TEXT NOT NULL,
    confidence REAL,
    superseded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ei_fact_versions_fact ON emotional_intelligence.fact_versions (fact_id, superseded_at DESC);
CREATE INDEX IF NOT EXISTS idx_ei_fact_versions_run ON emotional_intelligence.fact_versions (analysis_run_id);

-- ----------------------------------------------------------------------------
-- preferences -- SUBJECT-scoped.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS emotional_intelligence.preferences (
    id SERIAL PRIMARY KEY,
    subject_id INTEGER NOT NULL REFERENCES emotional_intelligence.subjects (id) ON DELETE CASCADE,
    analysis_run_id INTEGER REFERENCES emotional_intelligence.analysis_runs (id) ON DELETE SET NULL,
    category TEXT NOT NULL,
    item TEXT NOT NULL,
    weight REAL,
    confidence REAL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (subject_id, category, item)
);
CREATE INDEX IF NOT EXISTS idx_ei_preferences_subject ON emotional_intelligence.preferences (subject_id, category);
CREATE INDEX IF NOT EXISTS idx_ei_preferences_run ON emotional_intelligence.preferences (analysis_run_id);
-- ei_adapter.py full-text-searches this table by question relevance (see
-- PRODUCTION_READINESS_ANALYSIS.md item 3) -- indexed rather than computing
-- to_tsvector(...) fresh per row on every call.
ALTER TABLE emotional_intelligence.preferences ADD COLUMN IF NOT EXISTS search_tsv tsvector
    GENERATED ALWAYS AS (to_tsvector('english', coalesce(category, '') || ' ' || coalesce(item, ''))) STORED;
CREATE INDEX IF NOT EXISTS idx_ei_preferences_search_tsv ON emotional_intelligence.preferences USING GIN (search_tsv);

-- ----------------------------------------------------------------------------
-- personality_snapshots -- SUBJECT-scoped, batch (Big Five over time).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS emotional_intelligence.personality_snapshots (
    id SERIAL PRIMARY KEY,
    subject_id INTEGER NOT NULL REFERENCES emotional_intelligence.subjects (id) ON DELETE CASCADE,
    analysis_run_id INTEGER REFERENCES emotional_intelligence.analysis_runs (id) ON DELETE SET NULL,
    openness REAL,
    conscientiousness REAL,
    extraversion REAL,
    agreeableness REAL,
    neuroticism REAL,
    confidence REAL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ei_personality_snapshots_subject ON emotional_intelligence.personality_snapshots (subject_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ei_personality_snapshots_run ON emotional_intelligence.personality_snapshots (analysis_run_id);

-- ----------------------------------------------------------------------------
-- relationship_profiles -- pair-of-subjects scoped.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS emotional_intelligence.relationship_profiles (
    id SERIAL PRIMARY KEY,
    subject_a INTEGER NOT NULL REFERENCES emotional_intelligence.subjects (id) ON DELETE CASCADE,
    subject_b INTEGER NOT NULL REFERENCES emotional_intelligence.subjects (id) ON DELETE CASCADE,
    analysis_run_id INTEGER REFERENCES emotional_intelligence.analysis_runs (id) ON DELETE SET NULL,
    trust_score REAL,
    communication_frequency REAL,
    conflict_score REAL,
    emotional_support REAL,
    humor_similarity REAL,
    shared_topics TEXT[] NOT NULL DEFAULT '{}',
    relationship_summary TEXT,
    -- embedding vector(1536) -- add once semantic relationship search is implemented
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (subject_a, subject_b),
    CHECK (subject_a < subject_b)
);
CREATE INDEX IF NOT EXISTS idx_ei_relationship_profiles_a ON emotional_intelligence.relationship_profiles (subject_a);
CREATE INDEX IF NOT EXISTS idx_ei_relationship_profiles_b ON emotional_intelligence.relationship_profiles (subject_b);
CREATE INDEX IF NOT EXISTS idx_ei_relationship_profiles_run ON emotional_intelligence.relationship_profiles (analysis_run_id);

-- ----------------------------------------------------------------------------
-- memories -- SUBJECT-scoped episodic memory.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS emotional_intelligence.memories (
    id SERIAL PRIMARY KEY,
    subject_id INTEGER NOT NULL REFERENCES emotional_intelligence.subjects (id) ON DELETE CASCADE,
    episode_id INTEGER REFERENCES emotional_intelligence.episodes (id) ON DELETE SET NULL,
    analysis_run_id INTEGER REFERENCES emotional_intelligence.analysis_runs (id) ON DELETE SET NULL,
    memory_type TEXT NOT NULL DEFAULT 'episodic',
    summary TEXT NOT NULL,
    importance REAL,
    emotion TEXT,
    -- embedding vector(1536) -- add once semantic memory retrieval is implemented
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_accessed TIMESTAMPTZ,
    recall_count INTEGER NOT NULL DEFAULT 0,
    decay_score REAL,
    archived BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_ei_memories_subject ON emotional_intelligence.memories (subject_id);
CREATE INDEX IF NOT EXISTS idx_ei_memories_subject_importance ON emotional_intelligence.memories (subject_id, importance DESC)
    WHERE archived = FALSE;
CREATE INDEX IF NOT EXISTS idx_ei_memories_run ON emotional_intelligence.memories (analysis_run_id);
-- ei_adapter.py full-text-searches this table by question relevance (see
-- PRODUCTION_READINESS_ANALYSIS.md item 3) -- indexed rather than computing
-- to_tsvector(...) fresh per row on every call.
ALTER TABLE emotional_intelligence.memories ADD COLUMN IF NOT EXISTS search_tsv tsvector
    GENERATED ALWAYS AS (to_tsvector('english', coalesce(summary, ''))) STORED;
CREATE INDEX IF NOT EXISTS idx_ei_memories_search_tsv ON emotional_intelligence.memories USING GIN (search_tsv);

-- ----------------------------------------------------------------------------
-- knowledge_cards -- global, subject-independent psychoeducation corpus
-- (distilled from psychology literature, not extracted from any transcript).
-- Unlike every table above, this isn't about a specific person -- it's
-- reusable guidance retrieved by mood/situation and injected into a reply's
-- context alongside whatever the subject-scoped tables know about the user.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS emotional_intelligence.knowledge_cards (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL UNIQUE,
    moods TEXT[] NOT NULL DEFAULT '{}',
    -- e.g. 'sadness' | 'anxiety' | 'happiness' | 'anger' | 'stress' | 'grief' |
    -- 'planning' | 'conflict' | 'loneliness' -- app-validated, deliberately
    -- open-ended (not a DB enum) so adding a new mood is a data change, not a
    -- migration. One card can carry several moods (e.g. a grounding technique
    -- applies to both anxiety and stress).
    situation TEXT,               -- when this actually applies, e.g. "before a decision with no clearly best option"
    framework_name TEXT,          -- short label, e.g. 'CBT: cognitive restructuring', 'GROW model' -- lets cards be grouped/cited without parsing `framework`
    framework TEXT NOT NULL,      -- the technique itself, in plain language: what to actually do
    example_prompt TEXT,          -- a sample phrasing back to the user, so the LLM has a concrete tone reference instead of improvising on a sensitive topic
    source_citation TEXT,         -- paper/book this was distilled from
    scope TEXT NOT NULL DEFAULT 'general',  -- 'general' | 'clinical_adjacent' -- app-validated; clinical_adjacent cards (drawn from clinical-disorder literature) get an extra "not a substitute for therapy" framing when injected
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    -- embedding vector(1536) -- add once semantic retrieval is implemented, same as facts/memories/relationship_profiles above
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ei_knowledge_cards_moods ON emotional_intelligence.knowledge_cards USING GIN (moods);
CREATE INDEX IF NOT EXISTS idx_ei_knowledge_cards_active ON emotional_intelligence.knowledge_cards (is_active) WHERE is_active = TRUE;
-- ei_adapter.py will full-text-search this table the same way it already
-- does facts/beliefs/preferences/memories (see PRODUCTION_READINESS_ANALYSIS.md
-- item 3) -- indexed rather than computing to_tsvector(...) fresh per row.
-- Weighted (A: title + moods, B: situation/framework_name, C: framework body)
-- rather than one flat tsvector -- moods is included as literal text so a
-- query using one of the app's own canonical mood words ("anxious") gets a
-- precise, high-weight hit even with no mood_logs signal at all, instead of
-- relying only on situation/framework prose (which uses "anxiety" instead of
-- "anxious" -- different stems in Postgres's English config, so they don't
-- match each other -- and shares generic words like "feel" across nearly
-- every card, which produced false-positive matches when tried unweighted).
-- array_to_string() is marked STABLE, not IMMUTABLE (Postgres's blanket
-- anyarray volatility, not anything actually non-deterministic about
-- joining a text[] of lowercase mood words) -- a GENERATED column requires
-- IMMUTABLE, so it's wrapped here rather than called directly below.
CREATE OR REPLACE FUNCTION emotional_intelligence.array_to_string_immutable(arr TEXT[], sep TEXT)
    RETURNS TEXT AS $$ SELECT array_to_string(arr, sep) $$ LANGUAGE sql IMMUTABLE PARALLEL SAFE;

ALTER TABLE emotional_intelligence.knowledge_cards DROP COLUMN IF EXISTS search_tsv;
ALTER TABLE emotional_intelligence.knowledge_cards ADD COLUMN search_tsv tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(title, '') || ' ' || emotional_intelligence.array_to_string_immutable(moods, ' ')), 'A') ||
        setweight(to_tsvector('english', coalesce(situation, '') || ' ' || coalesce(framework_name, '')), 'B') ||
        setweight(to_tsvector('english', coalesce(framework, '')), 'C')
    ) STORED;
CREATE INDEX IF NOT EXISTS idx_ei_knowledge_cards_search_tsv ON emotional_intelligence.knowledge_cards USING GIN (search_tsv);

-- ----------------------------------------------------------------------------
-- weekly_digests -- one row per generated "here's what I've noticed about
-- you" digest (nudges/nudge_engine.py's _check_weekly_digest writes these,
-- GET /insights/digest in app.py reads them). user_id points at
-- public.users directly (unlike the subject-scoped tables above) since this
-- is real-app-user-facing content, not research data -- no FK across
-- schemas by convention elsewhere in this file, so left unconstrained same
-- as other user_id columns here.
--
-- content is a JSON array of insight cards: [{"category", "label",
-- "headline", "body"}, ...] -- see weekly_digest.py. Each card MAY carry a
-- "world" key (a real-world suggestion matched to that insight) once that
-- feature ships; it's intentionally omitted for now rather than a null
-- placeholder, so adding it later is additive, not a migration.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS emotional_intelligence.weekly_digests (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    content JSONB NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    viewed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_ei_weekly_digests_user_generated ON emotional_intelligence.weekly_digests (user_id, generated_at DESC);

-- ----------------------------------------------------------------------------
-- transcript / transcript_scenes / transcript_speakers /
-- transcript_speaker_turns -- the source ingestion tables populated by
-- load_friends_transcripts.py, reconstructed here (that script's own
-- CREATE TABLE statements were never saved to a file) so this file is a
-- complete, safe definition of everything in this schema. All IF NOT
-- EXISTS, matching that script's ON CONFLICT upsert targets exactly.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS emotional_intelligence.transcript (
    id SERIAL PRIMARY KEY,
    episode_id TEXT NOT NULL UNIQUE,
    season INTEGER NOT NULL,
    episode_number INTEGER NOT NULL,
    title TEXT,
    scene_structure JSONB,
    source_url TEXT,
    transcript_text TEXT,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ei_transcript_season ON emotional_intelligence.transcript (season, episode_number);

CREATE TABLE IF NOT EXISTS emotional_intelligence.transcript_scenes (
    id SERIAL PRIMARY KEY,
    transcript_id INTEGER NOT NULL REFERENCES emotional_intelligence.transcript (id) ON DELETE CASCADE,
    scene_number INTEGER NOT NULL,
    scene_name TEXT,
    UNIQUE (transcript_id, scene_number)
);

CREATE TABLE IF NOT EXISTS emotional_intelligence.transcript_speakers (
    id SERIAL PRIMARY KEY,
    transcript_id INTEGER NOT NULL REFERENCES emotional_intelligence.transcript (id) ON DELETE CASCADE,
    speaker_name TEXT NOT NULL,
    UNIQUE (transcript_id, speaker_name)
);

CREATE TABLE IF NOT EXISTS emotional_intelligence.transcript_speaker_turns (
    id SERIAL PRIMARY KEY,
    transcript_id INTEGER NOT NULL REFERENCES emotional_intelligence.transcript (id) ON DELETE CASCADE,
    scene_id INTEGER REFERENCES emotional_intelligence.transcript_scenes (id) ON DELETE CASCADE,
    speaker_id INTEGER REFERENCES emotional_intelligence.transcript_speakers (id) ON DELETE CASCADE,
    turn_number INTEGER NOT NULL,
    dialogue_text TEXT,
    UNIQUE (transcript_id, scene_id, turn_number)
);
CREATE INDEX IF NOT EXISTS idx_ei_transcript_speaker_turns_transcript ON emotional_intelligence.transcript_speaker_turns (transcript_id);

-- ----------------------------------------------------------------------------
-- episode_speaker_transcript -- the derived, per-turn-flattened table
-- create_episode_speaker_transcript.py builds FROM transcript above.
-- Reconstructed here from that script's own CREATE_SQL (exact match) so
-- it's also covered if this file is applied before that script runs.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS emotional_intelligence.episode_speaker_transcript (
    id SERIAL PRIMARY KEY,
    season INTEGER NOT NULL,
    episode_id TEXT NOT NULL,
    episode_number INTEGER NOT NULL,
    episode_name TEXT,
    scene_number INTEGER,
    scene_name TEXT,
    speaker_name TEXT NOT NULL,
    turn_number INTEGER,
    raw_transcript TEXT NOT NULL,
    source_url TEXT,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (episode_id, scene_number, turn_number, speaker_name)
);
CREATE INDEX IF NOT EXISTS idx_ei_episode_speaker_transcript_season_episode
    ON emotional_intelligence.episode_speaker_transcript (season, episode_id);
CREATE INDEX IF NOT EXISTS idx_ei_episode_speaker_transcript_speaker
    ON emotional_intelligence.episode_speaker_transcript (speaker_name);
