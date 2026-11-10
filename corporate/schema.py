"""Corporate's own tables, in their own `corp` schema, created on first use --
same lazy, locked, idempotent pattern as meet/schema.py. Turning the feature
off leaves every existing table untouched."""
from __future__ import annotations

import threading

_ensured = False
_lock = threading.Lock()

DDL = """
CREATE SCHEMA IF NOT EXISTS corp;

CREATE TABLE IF NOT EXISTS corp.orgs (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    created_by INTEGER REFERENCES users (id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Who belongs to an organisation, and their organisation-wide role.
-- owner: everything, incl. deleting the org; admin: everything else;
-- member: sees the structure and the directory.
CREATE TABLE IF NOT EXISTS corp.members (
    org_id INTEGER NOT NULL REFERENCES corp.orgs (id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner', 'admin', 'member')),
    title TEXT NOT NULL DEFAULT '',
    joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_corp_members_user ON corp.members (user_id);

-- Departments and teams as one tree (parent_id), so a company can have as
-- many levels as it needs: department > sub-department > team > sub-team.
CREATE TABLE IF NOT EXISTS corp.units (
    id SERIAL PRIMARY KEY,
    org_id INTEGER NOT NULL REFERENCES corp.orgs (id) ON DELETE CASCADE,
    parent_id INTEGER REFERENCES corp.units (id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('department', 'team')),
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_corp_units_org ON corp.units (org_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_corp_units_sibling_name
    ON corp.units (org_id, COALESCE(parent_id, 0), lower(name));

-- manager = department head / team lead (the label depends on the unit's kind).
CREATE TABLE IF NOT EXISTS corp.unit_members (
    unit_id INTEGER NOT NULL REFERENCES corp.units (id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('manager', 'member')),
    added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (unit_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_corp_unit_members_user ON corp.unit_members (user_id);

-- Invitation links. Only a hash of the token is stored.
CREATE TABLE IF NOT EXISTS corp.invites (
    id SERIAL PRIMARY KEY,
    org_id INTEGER NOT NULL REFERENCES corp.orgs (id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('admin', 'member')),
    unit_id INTEGER REFERENCES corp.units (id) ON DELETE SET NULL,
    unit_role TEXT CHECK (unit_role IN ('manager', 'member')),
    token_hash TEXT NOT NULL UNIQUE,
    invited_by INTEGER REFERENCES users (id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    accepted_at TIMESTAMPTZ,
    accepted_by INTEGER REFERENCES users (id) ON DELETE SET NULL,
    revoked_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_corp_invites_org ON corp.invites (org_id, created_at DESC);

-- ---- Work memory (iteration 2). Content with a unit_id is visible to that
-- unit's members only; content without one, to its participants only
-- (see access.py -- the permission filter).

-- Chat: one 'team' thread per unit, 'direct' threads between two people.
CREATE TABLE IF NOT EXISTS corp.threads (
    id SERIAL PRIMARY KEY,
    org_id INTEGER NOT NULL REFERENCES corp.orgs (id) ON DELETE CASCADE,
    unit_id INTEGER UNIQUE REFERENCES corp.units (id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('team', 'direct')),
    dm_key TEXT UNIQUE,
    extracted_upto INTEGER NOT NULL DEFAULT 0,
    last_message_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS corp.thread_members (
    thread_id INTEGER NOT NULL REFERENCES corp.threads (id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    last_read_id INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (thread_id, user_id)
);
CREATE TABLE IF NOT EXISTS corp.messages (
    id SERIAL PRIMARY KEY,
    thread_id INTEGER NOT NULL REFERENCES corp.threads (id) ON DELETE CASCADE,
    sender_id INTEGER REFERENCES users (id) ON DELETE SET NULL,
    kind TEXT NOT NULL DEFAULT 'text' CHECK (kind IN ('text', 'common_ground', 'system')),
    body TEXT NOT NULL,
    meta JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_corp_messages_thread ON corp.messages (thread_id, id);

CREATE TABLE IF NOT EXISTS corp.meetings (
    id SERIAL PRIMARY KEY,
    org_id INTEGER NOT NULL REFERENCES corp.orgs (id) ON DELETE CASCADE,
    unit_id INTEGER REFERENCES corp.units (id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('upload', 'record', 'text', 'import')),
    status TEXT NOT NULL DEFAULT 'processing' CHECK (status IN ('processing', 'ready', 'failed')),
    transcript TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    error TEXT,
    created_by INTEGER REFERENCES users (id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_corp_meetings_org ON corp.meetings (org_id, created_at DESC);
CREATE TABLE IF NOT EXISTS corp.meeting_participants (
    meeting_id INTEGER NOT NULL REFERENCES corp.meetings (id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    PRIMARY KEY (meeting_id, user_id)
);

-- The ledger. detected -> open -> done | overdue -> done | dropped.
CREATE TABLE IF NOT EXISTS corp.commitments (
    id SERIAL PRIMARY KEY,
    org_id INTEGER NOT NULL REFERENCES corp.orgs (id) ON DELETE CASCADE,
    unit_id INTEGER REFERENCES corp.units (id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    owner_id INTEGER REFERENCES users (id) ON DELETE SET NULL,
    owner_name TEXT,
    owed_to_id INTEGER REFERENCES users (id) ON DELETE SET NULL,
    owed_to_name TEXT,
    due_date DATE,
    status TEXT NOT NULL DEFAULT 'detected'
        CHECK (status IN ('detected', 'open', 'overdue', 'done', 'dropped')),
    source_type TEXT NOT NULL CHECK (source_type IN ('meeting', 'thread', 'manual')),
    source_id INTEGER,
    quote TEXT,
    evidence TEXT,
    created_by INTEGER REFERENCES users (id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    checked_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_corp_commitments_org ON corp.commitments (org_id, status);
CREATE TABLE IF NOT EXISTS corp.commitment_events (
    id SERIAL PRIMARY KEY,
    commitment_id INTEGER NOT NULL REFERENCES corp.commitments (id) ON DELETE CASCADE,
    actor_id INTEGER REFERENCES users (id) ON DELETE SET NULL,
    kind TEXT NOT NULL,
    detail JSONB NOT NULL DEFAULT '{}',
    at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS corp.decisions (
    id SERIAL PRIMARY KEY,
    org_id INTEGER NOT NULL REFERENCES corp.orgs (id) ON DELETE CASCADE,
    unit_id INTEGER REFERENCES corp.units (id) ON DELETE CASCADE,
    meeting_id INTEGER REFERENCES corp.meetings (id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS corp.decision_versions (
    decision_id INTEGER NOT NULL REFERENCES corp.decisions (id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    text TEXT NOT NULL,
    quote TEXT,
    made_by TEXT,
    source_type TEXT NOT NULL,
    source_id INTEGER,
    created_by INTEGER REFERENCES users (id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (decision_id, version)
);

CREATE TABLE IF NOT EXISTS corp.blockers (
    id SERIAL PRIMARY KEY,
    org_id INTEGER NOT NULL REFERENCES corp.orgs (id) ON DELETE CASCADE,
    unit_id INTEGER REFERENCES corp.units (id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id INTEGER,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
    scanned_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS corp.risks (
    id SERIAL PRIMARY KEY,
    org_id INTEGER NOT NULL REFERENCES corp.orgs (id) ON DELETE CASCADE,
    commitment_id INTEGER NOT NULL REFERENCES corp.commitments (id) ON DELETE CASCADE,
    blocker_id INTEGER NOT NULL REFERENCES corp.blockers (id) ON DELETE CASCADE,
    why TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ,
    UNIQUE (commitment_id, blocker_id)
);

-- Common ground results, and each person's opt-in work note for it.
CREATE TABLE IF NOT EXISTS corp.common_ground (
    id SERIAL PRIMARY KEY,
    org_id INTEGER NOT NULL REFERENCES corp.orgs (id) ON DELETE CASCADE,
    thread_id INTEGER REFERENCES corp.threads (id) ON DELETE CASCADE,
    meeting_id INTEGER REFERENCES corp.meetings (id) ON DELETE CASCADE,
    result JSONB NOT NULL,
    used_notes BOOLEAN NOT NULL DEFAULT false,
    created_by INTEGER REFERENCES users (id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS corp.work_notes (
    org_id INTEGER NOT NULL REFERENCES corp.orgs (id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    note TEXT NOT NULL DEFAULT '',
    sharing BOOLEAN NOT NULL DEFAULT false,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, user_id)
);
CREATE TABLE IF NOT EXISTS corp.job_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- Suspended members keep their place and history but can't open the organisation.
ALTER TABLE corp.members ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'suspended'));
"""


def ensure_corp_schema(cur, conn) -> None:
    global _ensured
    if _ensured:
        return
    with _lock:
        if _ensured:
            return
        cur.execute(DDL)
        conn.commit()
        _ensured = True
