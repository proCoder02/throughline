"""Stable, thin tracking functions that other backend code (app.py,
run_daily_ei_pipeline.py, nudge_engine.py) calls into to report job
activity. The whole point of this file: those call sites never need to
change again when ops/ internals evolve -- only this file does."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .schema import ensure_ops_schema

# Continuous-loop workers (app.py's email_reminder_worker/
# call_ring_timeout_worker) tick every 60s/10s -- logging a row on every
# single tick would be 1440/day and 8640/day of near-identical rows for no
# real monitoring benefit. Each only logs a heartbeat row at most once per
# this interval; ops.jobs.JOB_SCHEDULE's "interval_seconds" for those two
# jobs matches this throttle, not their real tick rate, so the dashboard's
# next-run/stale math stays consistent with what's actually recorded.
HEARTBEAT_SECONDS = 300


def record_heartbeat(cur, conn, job_name: str, detail: dict | None = None) -> None:
    """At most one row per HEARTBEAT_SECONDS for a continuous-loop worker.
    Best-effort: a tracking failure must never break the worker loop
    calling this. Reuses the caller's own open connection/cursor rather
    than opening a second one."""
    try:
        ensure_ops_schema(cur, conn)
        cur.execute(
            "SELECT started_at FROM ops.job_runs WHERE job_name = %s ORDER BY started_at DESC LIMIT 1",
            (job_name,),
        )
        row = cur.fetchone()
        now = datetime.now(timezone.utc)
        if row and (now - row["started_at"]).total_seconds() < HEARTBEAT_SECONDS:
            return
        cur.execute(
            "INSERT INTO ops.job_runs (job_name, started_at, finished_at, status, detail) "
            "VALUES (%s, %s, %s, 'success', %s)",
            (job_name, now, now, json.dumps(detail or {})),
        )
        conn.commit()
    except Exception as e:
        print(f"[ops.job_runs] heartbeat failed for {job_name}: {e!r}")
        try:
            conn.rollback()
        except Exception:
            pass


def record_fcm_send(cur, conn, user_id, status: str, notification_type) -> None:
    """Called from app.py's send_fcm_to_user for every token-send attempt
    (sent/stale/error/no_token) -- the only write path for ops.fcm_sends.
    Best-effort, same reasoning as record_heartbeat."""
    try:
        ensure_ops_schema(cur, conn)
        cur.execute(
            "INSERT INTO ops.fcm_sends (user_id, status, notification_type) VALUES (%s, %s, %s)",
            (user_id, status, notification_type),
        )
        conn.commit()
    except Exception as e:
        print(f"[ops.fcm_sends] record failed: {e!r}")
        try:
            conn.rollback()
        except Exception:
            pass


def start_job_run(conn, job_name: str) -> int | None:
    """For a real subprocess/cron job (ei_pipeline) -- call at the very
    start of its own process, before doing any real work. Returns the new
    ops.job_runs row's id, or None on failure (tracking is best-effort,
    never blocks the actual job)."""
    try:
        cur = conn.cursor()
        ensure_ops_schema(cur, conn)
        cur.execute(
            "INSERT INTO ops.job_runs (job_name, started_at, status) VALUES (%s, now(), 'running') RETURNING id",
            (job_name,),
        )
        run_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        return run_id
    except Exception as e:
        print(f"[ops.job_runs] failed to start tracking row: {e!r}")
        return None


def finish_job_run(conn, run_id: int | None, status: str, detail: dict) -> None:
    if run_id is None:
        return
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE ops.job_runs SET finished_at = now(), status = %s, detail = %s WHERE id = %s",
            (status, json.dumps(detail), run_id),
        )
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"[ops.job_runs] failed to finish tracking row: {e!r}")


def record_completed_run(cur, conn, job_name: str, started_at, status: str, detail: dict) -> None:
    """For a job that's already finished by the time it reports in (the
    nudge worker's cycle, which runs start-to-finish inside one call) --
    inserts one row with both timestamps set, rather than the separate
    start_job_run/finish_job_run pair a longer-running subprocess needs."""
    try:
        ensure_ops_schema(cur, conn)
        cur.execute(
            "INSERT INTO ops.job_runs (job_name, started_at, finished_at, status, detail) "
            "VALUES (%s, %s, now(), %s, %s)",
            (job_name, started_at, status, json.dumps(detail)),
        )
        conn.commit()
    except Exception as e:
        print(f"[ops.job_runs] record_completed_run failed for {job_name}: {e!r}")
        try:
            conn.rollback()
        except Exception:
            pass
