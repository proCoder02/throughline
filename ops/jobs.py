"""What jobs exist, their schedule, and their current status -- pure DB
read logic, no Flask dependency, so both dashboard.py (the API) and
alerts.py (the stale-job checker) can import this without either
depending on the other."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .tracking import HEARTBEAT_SECONDS

# job_name -> expected max gap between runs before it's flagged stale, in
# hours. ei_pipeline runs twice daily (cron: 08:00/20:00 UTC -- see
# emotional_intelligence/run_daily_ei_pipeline.py), nudge_cycle hourly (see
# nudges/nudge_engine.py's start_nudge_worker); both get a generous buffer
# over their own schedule so a slightly late run doesn't flash red. The two
# heartbeat workers get a generous buffer over HEARTBEAT_SECONDS instead of
# their real tick rate, for the same reason.
JOB_STALE_HOURS = {
    "ei_pipeline": 15, "nudge_cycle": 3,
    "email_reminder_worker": 0.2, "call_ring_timeout_worker": 0.2,
    "ops_alert_check": 1,  # diagnostic heartbeat -- see alerts.py's record_completed_run call
}

# Schedule metadata for the dashboard's "Autosys-style" job cards -- this is
# documentation of the real schedule (root's crontab for ei_pipeline, see
# infra/BASTION_ACCESS.md-adjacent ops notes; nudge_engine.py's
# start_nudge_worker(interval_seconds=3600) for nudge_cycle, or app.py's two
# while-True/time.sleep loops for the heartbeat workers), not read from any
# of them live -- if a schedule ever changes, this dict has to be updated
# to match, same as JOB_STALE_HOURS above it already does.
JOB_SCHEDULE = {
    "ei_pipeline": {
        "kind": "cron", "hours_utc": [8, 20],
        "cadence_label": "Daily at 08:00 & 20:00 UTC",
    },
    "nudge_cycle": {
        "kind": "interval", "interval_seconds": 3600,
        "cadence_label": "Every 1 hour",
    },
    "email_reminder_worker": {
        "kind": "interval", "interval_seconds": HEARTBEAT_SECONDS,
        "cadence_label": "Continuous (60s loop; heartbeat every 5 min)",
    },
    "call_ring_timeout_worker": {
        "kind": "interval", "interval_seconds": HEARTBEAT_SECONDS,
        "cadence_label": "Continuous (10s loop; heartbeat every 5 min)",
    },
    "ops_alert_check": {
        "kind": "interval", "interval_seconds": 1800,
        "cadence_label": "Every 30 min (diagnostic -- see ops/alerts.py)",
    },
}

JOB_HISTORY_LIMIT = 5


def next_run_at(job_name: str, last_started_at, now):
    """cron-kind jobs have a next run independent of history (the next
    clock time the schedule fires); interval-kind jobs only have a next
    run relative to when they last actually started, so None without any
    history yet."""
    sched = JOB_SCHEDULE.get(job_name)
    if not sched:
        return None
    if sched["kind"] == "cron":
        candidates = []
        for hour in sched["hours_utc"]:
            candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if candidate <= now:
                candidate += timedelta(days=1)
            candidates.append(candidate)
        return min(candidates)
    if sched["kind"] == "interval":
        if last_started_at is None:
            return None
        return last_started_at + timedelta(seconds=sched["interval_seconds"])
    return None


def job_status(cur) -> list:
    """Last JOB_HISTORY_LIMIT runs per job, not just the latest -- a job
    that flips success/fail/success looks identical to a solidly healthy
    one if only its single latest row is shown; the history list makes a
    flaky job visible instead of just its current (possibly lucky) state.
    Caller must have already ensured the ops schema exists."""
    cur.execute(
        """
        SELECT job_name, started_at, finished_at, status, detail
        FROM (
            SELECT job_name, started_at, finished_at, status, detail,
                   ROW_NUMBER() OVER (PARTITION BY job_name ORDER BY started_at DESC) AS rn
            FROM ops.job_runs
        ) ranked
        WHERE rn <= %s
        ORDER BY job_name, started_at DESC
        """,
        (JOB_HISTORY_LIMIT,),
    )
    rows = cur.fetchall()
    now = datetime.now(timezone.utc)
    by_job: dict = {}
    for r in rows:
        by_job.setdefault(r["job_name"], []).append(r)

    jobs = []
    for job_name, runs in by_job.items():
        latest = runs[0]
        reference = latest["finished_at"] or latest["started_at"]
        age_hours = (now - reference).total_seconds() / 3600 if reference else None
        stale_after = JOB_STALE_HOURS.get(job_name, 24)
        duration_seconds = None
        if latest["started_at"] and latest["finished_at"]:
            duration_seconds = (latest["finished_at"] - latest["started_at"]).total_seconds()
        nxt = next_run_at(job_name, latest["started_at"], now)
        sched = JOB_SCHEDULE.get(job_name, {})
        jobs.append({
            "job_name": job_name,
            "started_at": latest["started_at"].isoformat() if latest["started_at"] else None,
            "finished_at": latest["finished_at"].isoformat() if latest["finished_at"] else None,
            "status": latest["status"],
            "detail": latest["detail"],
            "age_hours": round(age_hours, 2) if age_hours is not None else None,
            "stale": age_hours is not None and age_hours > stale_after,
            "duration_seconds": round(duration_seconds, 1) if duration_seconds is not None else None,
            "cadence_label": sched.get("cadence_label", "—"),
            "next_run_at": nxt.isoformat() if nxt else None,
            "history": [
                {
                    "status": r["status"],
                    "started_at": r["started_at"].isoformat() if r["started_at"] else None,
                }
                for r in runs
            ],
        })
    for job_name in JOB_STALE_HOURS:
        if job_name not in by_job:
            sched = JOB_SCHEDULE.get(job_name, {})
            nxt = next_run_at(job_name, None, now)
            jobs.append({
                "job_name": job_name, "started_at": None, "finished_at": None,
                "status": "never_run", "detail": None, "age_hours": None, "stale": True,
                "duration_seconds": None, "cadence_label": sched.get("cadence_label", "—"),
                "next_run_at": nxt.isoformat() if nxt else None,
                "history": [],
            })
    return jobs
