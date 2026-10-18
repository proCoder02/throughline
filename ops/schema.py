"""Shared Postgres schema for the ops module -- the single source of truth
for ops.job_runs / ops.fcm_sends / ops.alerts_sent. Previously duplicated
three times (app.py, run_daily_ei_pipeline.py, nudge_engine.py each had
their own copy of this exact DDL); consolidated here so a future schema
change happens in one place instead of three."""
from __future__ import annotations

_ops_schema_ensured = False


def ensure_ops_schema(cur, conn) -> None:
    """Idempotent; only actually runs the DDL once per process (module-level
    flag), since callers may invoke this on every request/tick. Safe to call
    from multiple processes concurrently -- every statement is IF NOT EXISTS."""
    global _ops_schema_ensured
    if _ops_schema_ensured:
        return
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
    _ops_schema_ensured = True
