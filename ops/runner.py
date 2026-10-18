"""Subprocess launching -- shared by the custom-job scheduler and the
"start now"/"restart" API actions. A launched subprocess is tracked in
ops.running_jobs (PID) while alive, and ops.job_runs (via ops.tracking)
records its start/finish, same shape as run_daily_ei_pipeline.py's own
tracking. A dedicated thread calls process.wait() so the finish gets
recorded as soon as the process actually exits, without polling."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from datetime import datetime

from . import db as ops_db
from . import job_control
from .tracking import finish_job_run, start_job_run


def launch_subprocess_job(job_name: str, script_path: str, log_dir: str, extra_args: list | None = None) -> dict:
    """Starts script_path as its own process group (so stop() can kill the
    whole tree, not just this one PID) with stdout/stderr redirected into
    log_dir. Returns immediately; a background thread waits for exit and
    records the finish. Raises FileNotFoundError if script_path doesn't
    exist, rather than silently recording a doomed 'running' row."""
    if not os.path.isfile(script_path):
        raise FileNotFoundError(f"script not found: {script_path}")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"{job_name}_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.log")
    log_fh = open(log_path, "a", encoding="utf-8")

    popen_kwargs = {"stdout": log_fh, "stderr": subprocess.STDOUT, "cwd": os.path.dirname(script_path) or None}
    # New process group on POSIX so stop() can os.killpg() the whole tree
    # (this process plus anything it itself subprocess.Popen()s, same
    # reasoning run_daily_ei_pipeline.py's own stages need); Windows has no
    # process-group equivalent here, so local dev just gets a plain PID.
    if sys.platform != "win32":
        popen_kwargs["start_new_session"] = True

    process = subprocess.Popen([sys.executable, script_path, *(extra_args or [])], **popen_kwargs)

    # Each DB step below checks a connection out, uses it, and releases it
    # immediately -- never held open across the subprocess's own lifetime
    # (which could be long), or a few concurrently-running jobs would
    # exhaust ops/db.py's small pool for everything else (the dashboard,
    # the alert worker) for as long as they run.
    conn = ops_db.get_conn()
    try:
        cur = ops_db.dict_cursor(conn)
        job_control.mark_running(cur, conn, job_name, process.pid)
        run_id = start_job_run(conn, job_name)
    finally:
        ops_db.release_conn(conn)

    def _wait_and_finish():
        exit_code = process.wait()
        log_fh.close()
        status = "success" if exit_code == 0 else "error"
        conn2 = ops_db.get_conn()
        try:
            finish_job_run(conn2, run_id, status, {"exit_code": exit_code, "log_file": log_path})
            cur2 = ops_db.dict_cursor(conn2)
            job_control.clear_running(cur2, conn2, job_name)
        finally:
            ops_db.release_conn(conn2)

    threading.Thread(target=_wait_and_finish, daemon=True).start()
    return {"pid": process.pid, "log_file": log_path, "run_id": run_id}


def stop_subprocess_job(job_name: str) -> bool:
    """Returns True if a running process was found and signaled, False if
    nothing was running for this job. Kills the whole process group
    (negative PID) so a currently-executing child survives this job's own
    process dying first -- same reasoning launch_subprocess_job's
    start_new_session=True sets this up for."""
    import signal

    conn = ops_db.get_conn()
    try:
        cur = ops_db.dict_cursor(conn)
        pid = job_control.get_running_pid(cur, conn, job_name)
        if pid is None:
            return False
        try:
            if sys.platform != "win32":
                os.killpg(pid, signal.SIGTERM)
            else:
                os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass  # already gone -- fine, that's the goal either way
        job_control.clear_running(cur, conn, job_name)
        return True
    finally:
        ops_db.release_conn(conn)
