"""Job control actions -- on ice / off ice / start now / stop / restart --
exposed to the Flask routes in dashboard.py. Dispatches on each job's
control_kind (see ops/jobs.py's JOB_CONTROL_KIND):

  - "subprocess" (ei_pipeline, any custom job): full support via
    ops/runner.py. ei_pipeline's own script path/log dir are hardcoded
    below (it has no ops.custom_jobs row -- it's built in, run by root's
    real crontab, not this module's scheduler).
  - "in_process" (nudge_cycle, email_reminder_worker,
    call_ring_timeout_worker): these are threads inside the main app.py
    process, not separate OS processes -- nothing a web request can kill
    without killing the whole app. Only ice/unice (gates real work) and
    start (nudge_cycle only, trigger one extra cycle now) are supported;
    stop/restart/start-for-the-other-two raise ActionNotSupported with a
    clear reason instead of silently doing nothing.
  - "none" (ops_alert_check, a diagnostic heartbeat, not a real job): no
    actions at all.

app.py injects the one thing this package can't construct itself (the
nudge cycle needs push_notification/send_fcm_to_user, which live in
app.py) via register_nudge_runner() at startup -- everything else here
has zero dependency on app.py, same as the rest of ops/."""
from __future__ import annotations

import os
import threading
from pathlib import Path

from . import db as ops_db
from . import job_control
from .jobs import JOB_CONTROL_KIND
from .runner import launch_subprocess_job, stop_subprocess_job

_REPO_ROOT = Path(__file__).resolve().parent.parent

BUILTIN_SUBPROCESS_INFO = {
    "ei_pipeline": {
        "script_path": str(_REPO_ROOT / "emotional_intelligence" / "run_daily_ei_pipeline.py"),
        "log_dir": str(_REPO_ROOT / "emotional_intelligence" / "logs"),
        # --force so "start now"/"restart" actually runs even if this job
        # is currently on ice -- a manual on-call trigger should be able
        # to override the hold, same as root's crontab firing on schedule
        # regardless (there's no crontab entry to disable, which is why
        # run_daily_ei_pipeline.py's own on-ice gate exists at all).
        "extra_args": ["--force"],
    },
}

_nudge_runner = None  # set via register_nudge_runner(); None until app.py calls it


def register_nudge_runner(run_nudge_cycle_fn) -> None:
    """app.py calls this once at startup with a zero-arg callable that
    triggers one nudge cycle immediately (see app.py's wiring) -- this is
    the one piece of app.py-specific state this package needs, injected
    rather than imported, same as nudge_engine.py's own
    start_nudge_worker(push_fn, fcm_fn) pattern."""
    global _nudge_runner
    _nudge_runner = run_nudge_cycle_fn


class ActionNotSupported(Exception):
    pass


def _control_kind(job_name: str) -> str:
    if job_name in JOB_CONTROL_KIND:
        return JOB_CONTROL_KIND[job_name]
    conn = ops_db.get_conn()
    try:
        cur = ops_db.dict_cursor(conn)
        if job_control.get_custom_job(cur, conn, job_name):
            return "subprocess"
    finally:
        ops_db.release_conn(conn)
    raise ActionNotSupported(f"unknown job: {job_name}")


def ice(job_name: str) -> None:
    _control_kind(job_name)  # raises ActionNotSupported if the job doesn't exist at all
    conn = ops_db.get_conn()
    try:
        cur = ops_db.dict_cursor(conn)
        job_control.set_on_ice(cur, conn, job_name, True)
    finally:
        ops_db.release_conn(conn)


def unice(job_name: str) -> None:
    _control_kind(job_name)
    conn = ops_db.get_conn()
    try:
        cur = ops_db.dict_cursor(conn)
        job_control.set_on_ice(cur, conn, job_name, False)
    finally:
        ops_db.release_conn(conn)


def start_now(job_name: str) -> dict:
    kind = _control_kind(job_name)
    if kind == "subprocess":
        if job_name in BUILTIN_SUBPROCESS_INFO:
            info = BUILTIN_SUBPROCESS_INFO[job_name]
            return launch_subprocess_job(job_name, info["script_path"], info["log_dir"], info.get("extra_args"))
        conn = ops_db.get_conn()
        try:
            cur = ops_db.dict_cursor(conn)
            custom = job_control.get_custom_job(cur, conn, job_name)
        finally:
            ops_db.release_conn(conn)
        if not custom:
            raise ActionNotSupported(f"unknown job: {job_name}")
        return launch_subprocess_job(job_name, custom["script_path"], custom["log_dir"])
    if kind == "in_process" and job_name == "nudge_cycle":
        if _nudge_runner is None:
            raise ActionNotSupported("nudge runner not wired up yet (app.py startup order issue)")
        threading.Thread(target=_nudge_runner, daemon=True).start()
        return {"started": True}
    if kind == "in_process":
        raise ActionNotSupported(
            f"{job_name} is a continuous background loop already running every few seconds -- "
            f"there's no separate 'start' for it, only ice/unice to pause/resume its real work."
        )
    raise ActionNotSupported(f"{job_name} has no start action")


def stop(job_name: str) -> dict:
    kind = _control_kind(job_name)
    if kind != "subprocess":
        raise ActionNotSupported(
            f"{job_name} runs as a thread inside the main app process, not a separate process -- "
            f"it can't be stopped without stopping the whole app. Use ice to pause its real work instead."
        )
    stopped = stop_subprocess_job(job_name)
    return {"stopped": stopped}


def restart(job_name: str) -> dict:
    kind = _control_kind(job_name)
    if kind != "subprocess":
        raise ActionNotSupported(
            f"{job_name} runs as a thread inside the main app process -- restart doesn't apply; "
            f"use ice then unice, or start for nudge_cycle, instead."
        )
    stop_subprocess_job(job_name)  # no-op if nothing was running
    return start_now(job_name)
