"""Shared Postgres schema for the ops module -- the single source of truth
for ops.job_runs / ops.fcm_sends / ops.alerts_sent. Previously duplicated
three times (app.py, run_daily_ei_pipeline.py, nudge_engine.py each had
their own copy of this exact DDL); consolidated here so a future schema
change happens in one place instead of three."""
from __future__ import annotations

import threading

_ops_schema_ensured = False
_ops_schema_lock = threading.Lock()


def ensure_ops_schema(cur, conn) -> None:
    """Idempotent; only actually runs the DDL once per process (module-level
    flag). Safe across separate processes -- every statement is IF NOT
    EXISTS. Needs the lock for same-process concurrency though: confirmed
    live that several background threads hitting this for the first time
    at the same moment can each see the flag False and all issue the DDL
    concurrently -- CREATE TABLE IF NOT EXISTS is not atomic against that,
    and the loser gets a real UniqueViolation on the system catalog
    instead of silently no-op'ing."""
    global _ops_schema_ensured
    if _ops_schema_ensured:
        return
    with _ops_schema_lock:
        if _ops_schema_ensured:
            return
        _ensure_ops_schema_locked(cur, conn)
        _ops_schema_ensured = True


def _ensure_ops_schema_locked(cur, conn) -> None:
    cur.execute("CREATE SCHEMA IF NOT EXISTS ops")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ops.job_runs (
            id SERIAL PRIMARY KEY,
            job_name TEXT NOT NULL,
            started_at TIMESTAMPTZ NOT NULL,
            finished_at TIMESTAMPTZ,
            status TEXT NOT NULL DEFAULT 'running',
            detail JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ops_job_runs_job_name ON ops.job_runs (job_name, started_at DESC)")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ops.fcm_sends (
            id SERIAL PRIMARY KEY,
            user_id INTEGER,
            status TEXT NOT NULL,
            notification_type TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ops_fcm_sends_created ON ops.fcm_sends (created_at DESC)")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ops.alerts_sent (
            job_name TEXT PRIMARY KEY,
            last_alerted_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    conn.commit()
