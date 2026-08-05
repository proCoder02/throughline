-- ============================================================================
-- Relationship Intelligence schema -- DDL only, nothing in app.py reads or
-- writes these tables yet. Additive to schema.sql (run after it); every
-- CREATE TABLE/INDEX below is IF NOT EXISTS, matching that file's convention
-- so this stays safe to re-run.
--
-- Deliberately NOT included: `embedding vector(...)` columns from the
-- original design doc. Nothing in this codebase uses pgvector today, and
-- per the "pick each feature independently, fix it, move on" plan, adding
-- that extension dependency to all 13 tables now would force a decision
-- (install pgvector? which dimension?) before any single feature actually
-- needs similarity search. Each table below has a comment marking where an
-- `embedding vector(N)` column would get ALTER TABLE'd in once that
-- specific feature is picked up and actually does retrieval by similarity.
--
-- Run with: psql -U your_user -d your_db -f schema_relationship_intelligence.sql
-- ============================================================================

-- ----------------------------------------------------------------------------
-- facts -- canonical subject/predicate/object knowledge extracted from a
-- conversation ("Amit -> works_at -> Morgan Stanley"). valid_from/valid_until
-- let a fact be superseded without deleting history (see fact_versions
-- below, which snapshots what a fact looked like before each supersession).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS facts (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    conversation_id INTEGER REFERENCES conversations (id) ON DELETE SET NULL,
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object TEXT NOT NULL,
    confidence REAL,
    source TEXT,                       -- e.g. 'call', 'chat', 'manual' -- app-validated, not a CHECK, same convention as conversations.category
    valid_from TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_until TIMESTAMPTZ,
    -- embedding vector(1536) -- add once semantic fact retrieval is implemented
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_facts_user ON facts (user_id);
CREATE INDEX IF NOT EXISTS idx_facts_user_subject ON facts (user_id, subject);
-- Matches "what's still true right now" queries (valid_until IS NULL or in the future).
CREATE INDEX IF NOT EXISTS idx_facts_user_valid ON facts (user_id, valid_until);

-- ----------------------------------------------------------------------------
-- fact_versions -- snapshot of a fact's prior state each time it's
-- superseded (e.g. favorite language Python -> Rust), so "what did this used
-- to be" stays queryable instead of being overwritten in place on facts.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fact_versions (
    id SERIAL PRIMARY KEY,
    fact_id INTEGER NOT NULL REFERENCES facts (id) ON DELETE CASCADE,
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object TEXT NOT NULL,
    confidence REAL,
    superseded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_fact_versions_fact ON fact_versions (fact_id, superseded_at DESC);

-- ----------------------------------------------------------------------------
-- memories -- episodic memory ("I bought my first motorcycle"), as opposed
-- to facts' structured triples. decay_score/archived (added beyond the
-- original doc's own "memory decay" section, which described these as
-- fields rather than a 13th table) are what let retrieval mimic human
-- memory -- rarely-recalled, old, low-importance memories rank lower or get
-- archived instead of every memory being weighted equally forever.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memories (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    conversation_id INTEGER REFERENCES conversations (id) ON DELETE SET NULL,
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
CREATE INDEX IF NOT EXISTS idx_memories_user ON memories (user_id);
CREATE INDEX IF NOT EXISTS idx_memories_user_importance ON memories (user_id, importance DESC)
    WHERE archived = FALSE;

-- ----------------------------------------------------------------------------
-- relationship_profiles -- one row per unordered pair (not two directional
-- rows like friendships), enforced via the user_a < user_b CHECK so a pair
-- can't accidentally get inserted both ways. Separate from friendships
-- (which stays the trust boundary for "can these two even see each other's
-- stuff") -- this is the evolving *content* of a relationship, not its
-- existence.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS relationship_profiles (
    id SERIAL PRIMARY KEY,
    user_a INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    user_b INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    trust_score REAL,
    communication_frequency REAL,
    conflict_score REAL,
    emotional_support REAL,
    humor_similarity REAL,
    shared_topics TEXT[] NOT NULL DEFAULT '{}',
    relationship_summary TEXT,
    -- embedding vector(1536) -- add once semantic relationship search is implemented
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_a, user_b),
    CHECK (user_a < user_b)
);
CREATE INDEX IF NOT EXISTS idx_relationship_profiles_a ON relationship_profiles (user_a);
CREATE INDEX IF NOT EXISTS idx_relationship_profiles_b ON relationship_profiles (user_b);

-- ----------------------------------------------------------------------------
-- events -- life timeline (promotion, marriage, vacation, exam). participants
-- is free-text names rather than a join table to speaker_profiles/users --
-- most participants of a life event (family, old friends) won't be app
-- users or even a tracked speaker_profile, so forcing a hard reference
-- would make this table unusable for the common case. Revisit as a proper
-- join table only if a feature actually needs to query "every event Rahul
-- was part of" structurally.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS events (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    conversation_id INTEGER REFERENCES conversations (id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    description TEXT,
    participants TEXT[] NOT NULL DEFAULT '{}',
    event_date DATE,
    importance REAL,
    -- embedding vector(1536) -- add once semantic event search is implemented
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_events_user ON events (user_id, event_date DESC);

-- ----------------------------------------------------------------------------
-- beliefs -- opinions/viewpoints, tracked separately from facts because they
-- carry confidence + explicit expiry rather than being treated as ground
-- truth ("thinks remote work is more productive" is a belief, not a fact).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS beliefs (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    conversation_id INTEGER REFERENCES conversations (id) ON DELETE SET NULL,
    topic TEXT NOT NULL,
    belief TEXT NOT NULL,
    confidence REAL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_beliefs_user ON beliefs (user_id);
CREATE INDEX IF NOT EXISTS idx_beliefs_user_topic ON beliefs (user_id, topic);

-- ----------------------------------------------------------------------------
-- preferences -- category/item/weight instead of one flattened column (e.g.
-- users.personalization), so a user can hold many preferences per category
-- (several favorite foods, several music genres) with independent
-- confidence. UNIQUE + the natural upsert key means re-extracting the same
-- preference just strengthens weight/confidence instead of duplicating rows.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS preferences (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    item TEXT NOT NULL,
    weight REAL,
    confidence REAL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, category, item)
);
CREATE INDEX IF NOT EXISTS idx_preferences_user ON preferences (user_id, category);

-- ----------------------------------------------------------------------------
-- personality_snapshots -- Big Five over time, one row per extraction pass
-- (not one row per user like user_personas) so drift is visible instead of
-- only ever showing the latest snapshot.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS personality_snapshots (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    openness REAL,
    conscientiousness REAL,
    extraversion REAL,
    agreeableness REAL,
    neuroticism REAL,
    confidence REAL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_personality_snapshots_user ON personality_snapshots (user_id, created_at DESC);

-- ----------------------------------------------------------------------------
-- emotion_timeline -- DECIDED AGAINST. This duplicated the existing
-- mood_logs table column-for-column (user_id, conversation_id, a label, a
-- score/intensity, created_at) -- mood_logs already powers the mood trend
-- calendar heatmap, friend_mood_update pushes, and /mood/history in
-- production, so a second parallel table would just be dead weight. The one
-- genuinely new field emotion_timeline would have added (`cause`) belongs on
-- mood_logs instead:
--
--     ALTER TABLE mood_logs ADD COLUMN IF NOT EXISTS cause TEXT;
--
-- Not run here -- add it whenever a feature actually populates it, per
-- COGNITIVE_EXTRACTION_PIPELINE_PLAN.md's Phase 0 decision 1.
--
-- If you already ran the earlier version of this file, an empty, unused
-- `emotion_timeline` table exists in your DB -- safe to drop:
--     DROP TABLE IF EXISTS emotion_timeline;
-- ----------------------------------------------------------------------------

-- ----------------------------------------------------------------------------
-- topics / conversation_topics -- discussion topics as first-class rows
-- instead of free text on each conversation, so "every conversation about
-- X" is a join instead of a text search.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS topics (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    -- embedding vector(1536) -- add once semantic topic clustering is implemented
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, name)
);
CREATE INDEX IF NOT EXISTS idx_topics_user ON topics (user_id);

CREATE TABLE IF NOT EXISTS conversation_topics (
    id SERIAL PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    topic_id INTEGER NOT NULL REFERENCES topics (id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, topic_id)
);
CREATE INDEX IF NOT EXISTS idx_conversation_topics_topic ON conversation_topics (topic_id);

-- ----------------------------------------------------------------------------
-- entities / conversation_entities -- people/organizations/technologies/
-- places mentioned across conversations. entity_type is part of the
-- uniqueness so "Amazon" the company and a river/place of the same name
-- don't collide into one row.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS entities (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    entity_type TEXT,                  -- e.g. 'person', 'organization', 'technology', 'place' -- app-validated, not a CHECK
    -- embedding vector(1536) -- add once semantic entity search is implemented
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, name, entity_type)
);
CREATE INDEX IF NOT EXISTS idx_entities_user ON entities (user_id);

CREATE TABLE IF NOT EXISTS conversation_entities (
    id SERIAL PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    entity_id INTEGER NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_conversation_entities_entity ON conversation_entities (entity_id);

-- ----------------------------------------------------------------------------
-- conversation_summaries -- one row per conversation (conversation_id is the
-- PK itself, same 1:1 pattern as user_personas.user_id), so the LLM never
-- has to re-read raw_transcript/chat_messages in full to answer "what was
-- this conversation about."
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conversation_summaries (
    conversation_id INTEGER PRIMARY KEY REFERENCES conversations (id) ON DELETE CASCADE,
    summary TEXT NOT NULL,
    key_points TEXT[] NOT NULL DEFAULT '{}',
    action_items TEXT[] NOT NULL DEFAULT '{}',
    sentiment TEXT,
    -- embedding vector(1536) -- add once semantic summary search is implemented
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
