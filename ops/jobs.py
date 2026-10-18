"""What jobs exist, their schedule, and their current status -- pure DB
read logic, no Flask dependency, so dashboard.py (the API), alerts.py (the
stale-job checker), and scheduler.py can all import this without any of
them depending on each other."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import job_control
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

# job_name -> how this job can be controlled from the dashboard:
#   "subprocess" -- a real OS process (ei_pipeline via cron, any custom
#     job) -- on ice/off ice/start/stop/restart all apply.
#   "in_process" -- a thread inside this same app.py process -- there is
#     nothing a web request can kill without killing the whole app, so
#     only on ice/off ice (gates real work) and start (trigger one extra
#     cycle) apply; stop/restart are not offered.
#   "none" -- purely a diagnostic heartbeat (ops_alert_check itself) --
#     not a real job, no controls at all.
# Custom jobs are always "subprocess" (added dynamically in job_status()).
JOB_CONTROL_KIND = {
    "ei_pipeline": "subprocess",
    "nudge_cycle": "in_process",
    "email_reminder_worker": "in_process",
    "call_ring_timeout_worker": "in_process",
    "ops_alert_check": "none",
}

JOB_HISTORY_LIMIT = 5


def _compute_next_run(sched: dict, last_started_at, now):
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


def next_run_at(job_name: str, last_started_at, now):
    """cron-kind jobs have a next run independent of history (the next
    clock time the schedule fires); interval-kind jobs only have a next
    run relative to when they last actually started, so None without any
    history yet. Built-in jobs only -- see _custom_job_schedule for
    user-created ones."""
    sched = JOB_SCHEDULE.get(job_name)
    if not sched:
        return None
    return _compute_next_run(sched, last_started_at, now)


def _custom_job_schedule(custom: dict) -> dict:
    """Builds the same {kind, cadence_label, ...} shape JOB_SCHEDULE uses,
    from a custom_jobs row, plus a stale-hours threshold scaled to the
    job's own declared cadence (3x the interval/gap, same generosity
    nudge_cycle's hardcoded 3h get relative to its 1h cadence)."""
    if custom["schedule_kind"] == "cron":
        hours = sorted(custom["schedule_hours_utc"] or [])
        label = "Daily at " + ", ".join(f"{h:02d}:00" for h in hours) + " UTC"
        gaps = [((hours[i + 1] - hours[i]) % 24) for i in range(len(hours))] if len(hours) > 1 else [24]
        stale_hours = max(gaps) * 1.5 if gaps else 36
        return {"kind": "cron", "hours_utc": hours, "cadence_label": label, "stale_hours": stale_hours}
    minutes = custom["schedule_interval_minutes"] or 60
    label = f"Every {minutes} min" if minutes < 60 else f"Every {minutes // 60} hour(s)"
    return {
        "kind": "interval", "interval_seconds": minutes * 60,
        "cadence_label": label, "stale_hours": (minutes / 60) * 3,
    }


def job_status(cur, conn) -> list:
    """Last JOB_HISTORY_LIMIT runs per job, not just the latest -- a job
    that flips success/fail/success looks identical to a solidly healthy
    one if only its single latest row is shown; the history list makes a
    flaky job visible instead of just its current (possibly lucky) state.
    Merges built-in jobs (JOB_SCHEDULE) with custom jobs (ops.custom_jobs)
    into one list, plus each job's on-ice/currently-running state, so the
    dashboard renders both kinds identically. Caller must have already
    ensured the ops schema exists."""
    custom_jobs = {c["job_name"]: c for c in job_control.list_custom_jobs(cur, conn)}
    on_ice = job_control.all_on_ice(cur, conn)
    running = job_control.all_running(cur, conn)

    all_schedules = dict(JOB_SCHEDULE)
    all_stale_hours = dict(JOB_STALE_HOURS)
    control_kind = dict(JOB_CONTROL_KIND)
    for name, custom in custom_jobs.items():
        sched = _custom_job_schedule(custom)
        all_schedules[name] = sched
        all_stale_hours[name] = sched.pop("stale_hours")
        control_kind[name] = "subprocess"

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

    def _build(job_name, latest, history):
        reference = latest["finished_at"] or latest["started_at"] if latest else None
        age_hours = (now - reference).total_seconds() / 3600 if reference else None
        stale_after = all_stale_hours.get(job_name, 24)
        duration_seconds = None
        if latest and latest["started_at"] and latest["finished_at"]:
            duration_seconds = (latest["finished_at"] - latest["started_at"]).total_seconds()
        sched = all_schedules.get(job_name, {})
        is_iced = bool(on_ice.get(job_name))
        nxt = None if is_iced else _compute_next_run(sched, latest["started_at"] if latest else None, now) if sched else None
        return {
            "job_name": job_name,
            "started_at": latest["started_at"].isoformat() if latest and latest["started_at"] else None,
            "finished_at": latest["finished_at"].isoformat() if latest and latest["finished_at"] else None,
            "status": latest["status"] if latest else "never_run",
            "detail": latest["detail"] if latest else None,
            "age_hours": round(age_hours, 2) if age_hours is not None else None,
            "stale": (not is_iced) and (age_hours is None or age_hours > stale_after),
            "duration_seconds": round(duration_seconds, 1) if duration_seconds is not None else None,
            "cadence_label": sched.get("cadence_label", "—"),
            "next_run_at": nxt.isoformat() if nxt else None,
            "on_ice": is_iced,
            "running": job_name in running,
            "control_kind": control_kind.get(job_name, "subprocess" if job_name in custom_jobs else "none"),
            "is_custom": job_name in custom_jobs,
            "history": [{"status": r["status"], "started_at": r["started_at"].isoformat() if r["started_at"] else None} for r in history],
        }

    jobs = [_build(name, runs[0], runs) for name, runs in by_job.items()]
    all_known_names = set(all_stale_hours) | set(custom_jobs)
    for job_name in all_known_names:
        if job_name not in by_job:
            jobs.append(_build(job_name, None, []))
    return jobs
