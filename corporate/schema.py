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

-- "Active on the website" (pinged by open, visible tabs): phone pushes for work
-- messages are skipped while it's in the future -- the person already sees them.
CREATE TABLE IF NOT EXISTS corp.presence (
    user_id INTEGER PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
    active_until TIMESTAMPTZ NOT NULL
);

-- ---- Projects, external contacts, cross-team requests (iteration 3)

CREATE TABLE IF NOT EXISTS corp.projects (
    id SERIAL PRIMARY KEY,
    org_id INTEGER NOT NULL REFERENCES corp.orgs (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    client TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    start_date DATE,
    end_date DATE,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'on_hold', 'closed')),
    created_by INTEGER REFERENCES users (id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Who can see a project: its members only (lead = can edit it).
CREATE TABLE IF NOT EXISTS corp.project_members (
    project_id INTEGER NOT NULL REFERENCES corp.projects (id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('lead', 'member')),
    PRIMARY KEY (project_id, user_id)
);
-- People outside the company (client ministry officials, vendor contacts), with
-- their position and who they report to -- the stakeholder map.
CREATE TABLE IF NOT EXISTS corp.contacts (
    id SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES corp.projects (id) ON DELETE CASCADE,
    side TEXT NOT NULL DEFAULT 'client' CHECK (side IN ('client', 'vendor', 'partner')),
    organisation TEXT NOT NULL DEFAULT '',
    department TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    position TEXT NOT NULL DEFAULT '',
    reports_to INTEGER REFERENCES corp.contacts (id) ON DELETE SET NULL,
    email TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_corp_contacts_project ON corp.contacts (project_id);

ALTER TABLE corp.commitments ADD COLUMN IF NOT EXISTS project_id INTEGER REFERENCES corp.projects (id) ON DELETE SET NULL;
ALTER TABLE corp.commitments ADD COLUMN IF NOT EXISTS supports_id INTEGER REFERENCES corp.commitments (id) ON DELETE SET NULL;
ALTER TABLE corp.commitments ADD COLUMN IF NOT EXISTS contact_id INTEGER REFERENCES corp.contacts (id) ON DELETE SET NULL;
ALTER TABLE corp.commitments ADD COLUMN IF NOT EXISTS requested_by INTEGER REFERENCES users (id) ON DELETE SET NULL;
ALTER TABLE corp.commitments ADD COLUMN IF NOT EXISTS requested_due DATE;
ALTER TABLE corp.commitments ADD COLUMN IF NOT EXISTS risk_note TEXT;
ALTER TABLE corp.commitments DROP CONSTRAINT IF EXISTS commitments_status_check;
ALTER TABLE corp.commitments ADD CONSTRAINT commitments_status_check
    CHECK (status IN ('requested', 'detected', 'open', 'overdue', 'done', 'dropped', 'declined'));
ALTER TABLE corp.commitments DROP CONSTRAINT IF EXISTS commitments_source_type_check;
ALTER TABLE corp.commitments ADD CONSTRAINT commitments_source_type_check
    CHECK (source_type IN ('meeting', 'thread', 'manual', 'request'));
CREATE INDEX IF NOT EXISTS idx_corp_commitments_supports ON corp.commitments (supports_id);
CREATE INDEX IF NOT EXISTS idx_corp_commitments_project ON corp.commitments (project_id);

-- Progress notes and blockers on one commitment, visible to whoever can see it.
CREATE TABLE IF NOT EXISTS corp.commitment_updates (
    id SERIAL PRIMARY KEY,
    commitment_id INTEGER NOT NULL REFERENCES corp.commitments (id) ON DELETE CASCADE,
    author_id INTEGER REFERENCES users (id) ON DELETE SET NULL,
    kind TEXT NOT NULL DEFAULT 'update' CHECK (kind IN ('update', 'blocker', 'unblocked')),
    text TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Work calls (corporate/calls.py): one meeting per call, however many sides were recorded.
ALTER TABLE corp.meetings ADD COLUMN IF NOT EXISTS call_id INTEGER;
CREATE UNIQUE INDEX IF NOT EXISTS uq_corp_meetings_call ON corp.meetings (org_id, call_id) WHERE call_id IS NOT NULL;
ALTER TABLE corp.meetings DROP CONSTRAINT IF EXISTS meetings_source_check;
ALTER TABLE corp.meetings ADD CONSTRAINT meetings_source_check CHECK (source IN ('upload', 'record', 'text', 'import', 'call', 'email'));

-- Work email (corporate/mail.py): opt-in per person; what's been read, with a short
-- excerpt kept as evidence for commitments. Visible to that person only.
CREATE TABLE IF NOT EXISTS corp.mail_links (
    org_id INTEGER NOT NULL REFERENCES corp.orgs (id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    enabled BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, user_id)
);
CREATE TABLE IF NOT EXISTS corp.mail_items (
    id SERIAL PRIMARY KEY,
    org_id INTEGER NOT NULL REFERENCES corp.orgs (id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    message_key TEXT NOT NULL,
    folder TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    sender TEXT NOT NULL DEFAULT '',
    excerpt TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, user_id, message_key)
);
ALTER TABLE corp.commitments DROP CONSTRAINT IF EXISTS commitments_source_type_check;
ALTER TABLE corp.commitments ADD CONSTRAINT commitments_source_type_check
    CHECK (source_type IN ('meeting', 'thread', 'manual', 'request', 'email'));

-- Tickets (corporate/tickets.py): small issues inside a project, keyed like PAY-12,
-- and everything said about them in chat, email and calls (ticket_links).
ALTER TABLE corp.projects ADD COLUMN IF NOT EXISTS ticket_key TEXT;
ALTER TABLE corp.projects ADD COLUMN IF NOT EXISTS ticket_seq INTEGER NOT NULL DEFAULT 0;
CREATE UNIQUE INDEX IF NOT EXISTS uq_corp_projects_ticket_key ON corp.projects (org_id, upper(ticket_key))
    WHERE ticket_key IS NOT NULL;
CREATE TABLE IF NOT EXISTS corp.tickets (
    id SERIAL PRIMARY KEY,
    org_id INTEGER NOT NULL REFERENCES corp.orgs (id) ON DELETE CASCADE,
    project_id INTEGER NOT NULL REFERENCES corp.projects (id) ON DELETE CASCADE,
    number INTEGER NOT NULL,
    key TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'todo' CHECK (status IN ('todo', 'in_progress', 'in_review', 'done', 'cancelled')),
    priority TEXT NOT NULL DEFAULT 'medium' CHECK (priority IN ('low', 'medium', 'high', 'urgent')),
    due_date DATE,
    -- Where the ticket lives: here, or (phase 2) an outside tracker such as Jira.
    source TEXT NOT NULL DEFAULT 'throughline' CHECK (source IN ('throughline', 'jira')),
    external_key TEXT,
    external_url TEXT,
    summary TEXT,
    summary_at TIMESTAMPTZ,
    created_by INTEGER REFERENCES users (id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at TIMESTAMPTZ,
    UNIQUE (org_id, key)
);
CREATE INDEX IF NOT EXISTS idx_corp_tickets_project ON corp.tickets (project_id, status);
CREATE TABLE IF NOT EXISTS corp.ticket_people (
    ticket_id INTEGER NOT NULL REFERENCES corp.tickets (id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('assignee', 'watcher')),
    PRIMARY KEY (ticket_id, user_id, role)
);
CREATE INDEX IF NOT EXISTS idx_corp_ticket_people_user ON corp.ticket_people (user_id);
-- A conversation tagged to a ticket. Private content (a direct message, an email, a
-- private call) is shown to others only as its summary unless the tagger shared it.
CREATE TABLE IF NOT EXISTS corp.ticket_links (
    id SERIAL PRIMARY KEY,
    ticket_id INTEGER NOT NULL REFERENCES corp.tickets (id) ON DELETE CASCADE,
    source_type TEXT NOT NULL CHECK (source_type IN ('message', 'email', 'meeting', 'commitment')),
    source_id INTEGER NOT NULL,
    how TEXT NOT NULL CHECK (how IN ('key', 'manual', 'ai')),
    status TEXT NOT NULL DEFAULT 'linked' CHECK (status IN ('linked', 'suggested', 'rejected')),
    tagged_by INTEGER REFERENCES users (id) ON DELETE SET NULL,
    label TEXT NOT NULL DEFAULT '',
    summary TEXT,
    shared BOOLEAN NOT NULL DEFAULT false,
    shared_text TEXT,  -- the content, kept only when the tagger chose to share it
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ticket_id, source_type, source_id)
);
CREATE INDEX IF NOT EXISTS idx_corp_ticket_links_source ON corp.ticket_links (source_type, source_id);
CREATE TABLE IF NOT EXISTS corp.ticket_events (
    id SERIAL PRIMARY KEY,
    ticket_id INTEGER NOT NULL REFERENCES corp.tickets (id) ON DELETE CASCADE,
    actor_id INTEGER REFERENCES users (id) ON DELETE SET NULL,
    kind TEXT NOT NULL,
    detail JSONB NOT NULL DEFAULT '{}',
    at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_corp_ticket_events_ticket ON corp.ticket_events (ticket_id);
CREATE TABLE IF NOT EXISTS corp.ticket_comments (
    id SERIAL PRIMARY KEY,
    ticket_id INTEGER NOT NULL REFERENCES corp.tickets (id) ON DELETE CASCADE,
    author_id INTEGER REFERENCES users (id) ON DELETE SET NULL,
    body TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_corp_ticket_comments_ticket ON corp.ticket_comments (ticket_id);
-- Meetings and calls already checked for ticket suggestions.
ALTER TABLE corp.meetings ADD COLUMN IF NOT EXISTS tickets_scanned_at TIMESTAMPTZ;

-- Client view: tickets roll up into the client deliverable they support; a
-- 'blocked' status; read-only project stakeholders (Sales, BU heads); the
-- weekly client-projects digest is sent once per person per week.
ALTER TABLE corp.tickets ADD COLUMN IF NOT EXISTS supports_id INTEGER REFERENCES corp.commitments (id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_corp_tickets_supports ON corp.tickets (supports_id);
ALTER TABLE corp.tickets DROP CONSTRAINT IF EXISTS tickets_status_check;
ALTER TABLE corp.tickets ADD CONSTRAINT tickets_status_check
    CHECK (status IN ('todo', 'in_progress', 'in_review', 'blocked', 'done', 'cancelled'));
ALTER TABLE corp.project_members DROP CONSTRAINT IF EXISTS project_members_role_check;
ALTER TABLE corp.project_members ADD CONSTRAINT project_members_role_check CHECK (role IN ('lead', 'member', 'stakeholder'));
CREATE TABLE IF NOT EXISTS corp.digest_sent (
    org_id INTEGER NOT NULL REFERENCES corp.orgs (id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    week TEXT NOT NULL,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, user_id, week)
);

-- Ask conversations (one person's own, per organisation), kept so earlier chats
-- stay listed and follow-up questions know what came before.
CREATE TABLE IF NOT EXISTS corp.ask_threads (
    id SERIAL PRIMARY KEY,
    org_id INTEGER NOT NULL REFERENCES corp.orgs (id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_corp_ask_threads_user ON corp.ask_threads (org_id, user_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS corp.ask_messages (
    id SERIAL PRIMARY KEY,
    thread_id INTEGER NOT NULL REFERENCES corp.ask_threads (id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    sources JSONB NOT NULL DEFAULT '[]',
    unit_id INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_corp_ask_messages_thread ON corp.ask_messages (thread_id, id);

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
