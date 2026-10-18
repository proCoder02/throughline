"""Background thread that fires custom jobs (user-created via the
dashboard) when their schedule is due. Built-in jobs (ei_pipeline via
root's real crontab, nudge_cycle/heartbeat workers via their own
in-process loops) are NOT driven by this -- only ops.custom_jobs entries,
since those have no real system cron entry of their own."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from . import db as ops_db
from . import job_control
from .jobs import _compute_next_run
from .runner import launch_subprocess_job

SCHEDULER_TICK_SECONDS = 30


def _is_due(cur, conn, job_name: str, hours_utc, interval_minutes) -> bool:
    cur.execute(
        "SELECT started_at FROM ops.job_runs WHERE job_name = %s ORDER BY started_at DESC LIMIT 1",
        (job_name,),
    )
    row = cur.fetchone()
    last_started_at = row["started_at"] if row else None
    sched = {"kind": "cron", "hours_utc": hours_utc} if hours_utc else \
            {"kind": "interval", "interval_seconds": (interval_minutes or 0) * 60}
    now = datetime.now(timezone.utc)
    nxt = _compute_next_run(sched, last_started_at, now)
    # No history yet (last_started_at is None) and interval-kind -> nxt is
    # None (see _compute_next_run) -- treat that as immediately due, same
    # as a cron job's first-ever fire, rather than never firing at all.
    if nxt is None:
        return last_started_at is None
    return nxt <= now


def custom_job_scheduler():
    """Runs continuously, same while-True/sleep pattern as every other
    background worker in this app. A failure checking or launching one
    job must never stop the loop from checking the rest."""
    while True:
        try:
            conn = ops_db.get_conn()
            try:
                cur = ops_db.dict_cursor(conn)
                jobs = job_control.list_custom_jobs(cur, conn)
                running = job_control.all_running(cur, conn)
                iced = job_control.all_on_ice(cur, conn)
            finally:
                ops_db.release_conn(conn)

            for job in jobs:
                job_name = job["job_name"]
                try:
                    if iced.get(job_name):
                        continue
                    if job_name in running:
                        continue  # already running -- don't double-fire
                    conn = ops_db.get_conn()
                    try:
                        cur = ops_db.dict_cursor(conn)
                        due = _is_due(cur, conn, job_name, job["schedule_hours_utc"], job["schedule_interval_minutes"])
                    finally:
                        ops_db.release_conn(conn)
                    if due:
                        print(f"[ops.scheduler] firing custom job {job_name}", flush=True)
                        launch_subprocess_job(job_name, job["script_path"], job["log_dir"])
                except Exception as e:
                    print(f"[ops.scheduler] error handling {job_name}: {e!r}", flush=True)
        except Exception as e:
            print(f"[ops.scheduler] error: {e!r}", flush=True)
        time.sleep(SCHEDULER_TICK_SECONDS)
