"""Control-plane state for jobs -- on-ice flags, which subprocess (if any)
is currently running, and the custom-job definitions a user creates from
the dashboard. All plain DB-backed state, no Flask dependency, shared by
ops/actions.py (the API-facing dispatch), ops/runner.py (the scheduler),
and the built-in jobs' own on-ice checks (run_daily_ei_pipeline.py,
nudge_engine.py, app.py's two heartbeat loops)."""
from __future__ import annotations

import re
import threading
from datetime import datetime, timezone

from .schema import ensure_ops_schema

_control_schema_ensured = False
_control_schema_lock = threading.Lock()

_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")


def validate_job_name(name: str) -> str:
    """Job names end up in file paths (log dirs) and as dict/SQL keys --
    restricting to a safe charset avoids path traversal or injection via a
    crafted name, not just a cosmetic check."""
    name = (name or "").strip()
    if not _SAFE_NAME_RE.match(name):
        raise ValueError(
            "Job name must be 1-64 characters: letters, numbers, underscore, hyphen only."
        )
    return name


def _ensure_control_schema(cur, conn) -> None:
    """Locked for the same reason ops/schema.py's ensure_ops_schema is --
    confirmed live that concurrent first-callers race CREATE TABLE IF NOT
    EXISTS into a real UniqueViolation otherwise."""
    global _control_schema_ensured
    if _control_schema_ensured:
        return
    with _control_schema_lock:
        if _control_schema_ensured:
            return
        _ensure_control_schema_locked(cur, conn)
        _control_schema_ensured = True


def _ensure_control_schema_locked(cur, conn) -> None:
    ensure_ops_schema(cur, conn)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ops.job_control (
            job_name TEXT PRIMARY KEY,
            on_ice BOOLEAN NOT NULL DEFAULT FALSE,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ops.running_jobs (
            job_name TEXT PRIMARY KEY,
            pid INTEGER NOT NULL,
            started_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ops.custom_jobs (
            job_name TEXT PRIMARY KEY,
            script_path TEXT NOT NULL,
            schedule_kind TEXT NOT NULL,
            schedule_hours_utc INTEGER[],
            schedule_interval_minutes INTEGER,
            log_dir TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    conn.commit()


# ---------------------------------------------------------------------------
# On-ice flags -- default FALSE (opt-out, same convention as every other
# per-user/per-job toggle in this codebase) for any job never explicitly
# iced, built-in or custom.
# ---------------------------------------------------------------------------

def is_on_ice(cur, conn, job_name: str) -> bool:
    _ensure_control_schema(cur, conn)
    cur.execute("SELECT on_ice FROM ops.job_control WHERE job_name = %s", (job_name,))
    row = cur.fetchone()
    if row is None:
        return False
    return bool(row["on_ice"] if isinstance(row, dict) else row[0])


def set_on_ice(cur, conn, job_name: str, on_ice: bool) -> None:
    _ensure_control_schema(cur, conn)
    cur.execute(
        "INSERT INTO ops.job_control (job_name, on_ice, updated_at) VALUES (%s, %s, now()) "
        "ON CONFLICT (job_name) DO UPDATE SET on_ice = %s, updated_at = now()",
        (job_name, on_ice, on_ice),
    )
    conn.commit()


def all_on_ice(cur, conn) -> dict:
    """job_name -> on_ice, for every job that has ever been explicitly
    toggled (missing from this dict means "not iced", the default)."""
    _ensure_control_schema(cur, conn)
    cur.execute("SELECT job_name, on_ice FROM ops.job_control")
    rows = cur.fetchall()
    return {(r["job_name"] if isinstance(r, dict) else r[0]): (r["on_ice"] if isinstance(r, dict) else r[1]) for r in rows}


# ---------------------------------------------------------------------------
# Running-PID tracking -- only meaningful for subprocess-kind jobs
# (ei_pipeline, custom jobs). Lets "stop" find the right process, and lets
# the dashboard show "currently running" distinct from "last run was X ago".
# ---------------------------------------------------------------------------

def mark_running(cur, conn, job_name: str, pid: int) -> None:
    _ensure_control_schema(cur, conn)
    cur.execute(
        "INSERT INTO ops.running_jobs (job_name, pid, started_at) VALUES (%s, %s, now()) "
        "ON CONFLICT (job_name) DO UPDATE SET pid = %s, started_at = now()",
        (job_name, pid, pid),
    )
    conn.commit()


def clear_running(cur, conn, job_name: str) -> None:
    _ensure_control_schema(cur, conn)
    cur.execute("DELETE FROM ops.running_jobs WHERE job_name = %s", (job_name,))
    conn.commit()


def get_running_pid(cur, conn, job_name: str) -> int | None:
    _ensure_control_schema(cur, conn)
    cur.execute("SELECT pid FROM ops.running_jobs WHERE job_name = %s", (job_name,))
    row = cur.fetchone()
    if row is None:
        return None
    return row["pid"] if isinstance(row, dict) else row[0]


def all_running(cur, conn) -> dict:
    _ensure_control_schema(cur, conn)
    cur.execute("SELECT job_name, pid, started_at FROM ops.running_jobs")
    rows = cur.fetchall()
    out = {}
    for r in rows:
        name = r["job_name"] if isinstance(r, dict) else r[0]
        pid = r["pid"] if isinstance(r, dict) else r[1]
        started_at = r["started_at"] if isinstance(r, dict) else r[2]
        out[name] = {"pid": pid, "started_at": started_at}
    return out


# ---------------------------------------------------------------------------
# Custom job definitions -- user-created, script_path + a recurring
# schedule (same "cron" (daily, possibly multiple times/day) / "interval"
# (every N minutes) shape ops/jobs.py's built-in JOB_SCHEDULE already
# uses, so next-run/stale computation works identically for both).
# ---------------------------------------------------------------------------

def create_custom_job(cur, conn, job_name: str, script_path: str, schedule_kind: str,
                       hours_utc: list | None, interval_minutes: int | None, log_dir: str) -> None:
    job_name = validate_job_name(job_name)
    if schedule_kind not in ("cron", "interval"):
        raise ValueError("schedule_kind must be 'cron' or 'interval'")
    if schedule_kind == "cron" and not hours_utc:
        raise ValueError("cron schedule requires at least one hour (0-23 UTC)")
    if schedule_kind == "interval" and not interval_minutes:
        raise ValueError("interval schedule requires interval_minutes")
    _ensure_control_schema(cur, conn)
    cur.execute(
        "INSERT INTO ops.custom_jobs (job_name, script_path, schedule_kind, schedule_hours_utc, "
        "schedule_interval_minutes, log_dir) VALUES (%s, %s, %s, %s, %s, %s)",
        (job_name, script_path, schedule_kind, hours_utc, interval_minutes, log_dir),
    )
    conn.commit()


def list_custom_jobs(cur, conn) -> list:
    _ensure_control_schema(cur, conn)
    cur.execute(
        "SELECT job_name, script_path, schedule_kind, schedule_hours_utc, "
        "schedule_interval_minutes, log_dir, created_at FROM ops.custom_jobs ORDER BY job_name"
    )
    return [dict(r) for r in cur.fetchall()]


def get_custom_job(cur, conn, job_name: str) -> dict | None:
    _ensure_control_schema(cur, conn)
    cur.execute(
        "SELECT job_name, script_path, schedule_kind, schedule_hours_utc, "
        "schedule_interval_minutes, log_dir, created_at FROM ops.custom_jobs WHERE job_name = %s",
        (job_name,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def delete_custom_job(cur, conn, job_name: str) -> None:
    """Also clears this job's ops.job_runs history, not just its
    definition -- confirmed live that leaving old run rows behind makes
    job_status() (ops/jobs.py) keep rendering a zombie card for a job
    that no longer exists (it builds its job list from whatever job_names
    appear in ops.job_runs, deleted or not). Log files on disk are left
    alone -- only the dashboard's own tracking rows are removed."""
    _ensure_control_schema(cur, conn)
    cur.execute("DELETE FROM ops.custom_jobs WHERE job_name = %s", (job_name,))
    cur.execute("DELETE FROM ops.job_control WHERE job_name = %s", (job_name,))
    cur.execute("DELETE FROM ops.running_jobs WHERE job_name = %s", (job_name,))
    cur.execute("DELETE FROM ops.job_runs WHERE job_name = %s", (job_name,))
    conn.commit()
