"""One-click fixes for the common problems, run from the dashboard.

Each fix has an id ("rq_requeue_failed:calls"), a label, a confirmation
question and a function returning a short result message. Every run --
success or failure -- is written to the event log with who clicked it.

What's registered depends on what app.py hands over via install():
  - rq_queues: requeue / clear failed jobs per queue
  - email: (send_email_record, get_conn, release_conn, dict_cursor) for
    retrying failed sends and un-sticking interrupted ones
  - units from OPS_RESTARTABLE_UNITS (comma-separated systemd units):
    restart via `sudo -n systemctl restart <unit>` -- needs a matching
    NOPASSWD sudoers rule for that exact command, nothing broader
Probe-backed jobs always get "check again".

Pushes can't be resent: ops.fcm_sends records the outcome of each push,
not its content, and most pushes (incoming call, missed call) are only
meaningful at the moment they were sent."""
from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime, timezone

from . import db as ops_db
from . import events
from . import probe
from .tracking import record_completed_run

EMAIL_RETRY_HOURS = 24
EMAIL_RETRY_LIMIT = 20
STUCK_SENDING_MINUTES = 10
# send_error text for an email interrupted mid-send: it may already have
# gone out, so "retry failed sends" deliberately skips these.
INTERRUPTED_PREFIX = "Interrupted while sending"

_fixes: dict = {}


class FixError(Exception):
    pass


def register(fix_id: str, label: str, fn, confirm: str, danger: bool = False) -> None:
    _fixes[fix_id] = {"label": label, "fn": fn, "confirm": confirm, "danger": danger}


def available() -> dict:
    """id -> {label, confirm, danger} for the dashboard. Probe "check
    again" fixes are listed per probe."""
    out = {k: {"label": v["label"], "confirm": v["confirm"], "danger": v["danger"]} for k, v in _fixes.items()}
    for name in probe.probe_jobs():
        out[f"probe_now:{name}"] = {"label": "Check again now", "confirm": "", "danger": False}
    return out


def run(fix_id: str, actor: str) -> str:
    if fix_id.startswith("probe_now:"):
        fn = lambda: _probe_now(fix_id.split(":", 1)[1])  # noqa: E731
        label = "Check again now"
    elif fix_id in _fixes:
        fn, label = _fixes[fix_id]["fn"], _fixes[fix_id]["label"]
    else:
        raise FixError(f"unknown fix: {fix_id}")
    try:
        message = fn()
    except FixError as e:
        events.record("action", fix_id, f"{label} -- failed: {e}", actor=actor, detail={"ok": False})
        raise
    except Exception as e:
        events.record("action", fix_id, f"{label} -- failed: {e!r}"[:500], actor=actor, detail={"ok": False})
        raise FixError(repr(e)[:300])
    events.record("action", fix_id, f"{label} -- {message}", actor=actor, detail={"ok": True})
    return message


# ---------------------------------------------------------------- probes

def _probe_now(name: str) -> str:
    if name not in probe.probe_jobs():
        raise FixError(f"no health check named {name}")
    started = datetime.now(timezone.utc)
    status, detail = probe.run_probe(name)
    conn = ops_db.get_conn()
    try:
        cur = ops_db.dict_cursor(conn)
        record_completed_run(cur, conn, name, started, status, detail)
    finally:
        ops_db.release_conn(conn)
    probe.note_written(name, status)
    return f"{status}" + (f": {detail.get('problem')}" if isinstance(detail, dict) and detail.get("problem") else "")


# ---------------------------------------------------------------- RQ

def _rq_requeue(queue):
    def fix():
        reg = queue.failed_job_registry
        ids = reg.get_job_ids()
        done = 0
        for job_id in ids:
            try:
                reg.requeue(job_id)
                done += 1
            except Exception as e:
                print(f"[ops.remediations] requeue {job_id} failed: {e!r}", flush=True)
        if not ids:
            return "no failed jobs to retry"
        return f"requeued {done} of {len(ids)} failed job(s)"
    return fix


def _rq_clear(queue):
    def fix():
        reg = queue.failed_job_registry
        ids = reg.get_job_ids()
        for job_id in ids:
            try:
                reg.remove(job_id, delete_job=True)
            except Exception as e:
                print(f"[ops.remediations] remove {job_id} failed: {e!r}", flush=True)
        return f"cleared {len(ids)} failed job(s)" if ids else "no failed jobs to clear"
    return fix


# ---------------------------------------------------------------- email

def _email_retry(send_email_record, get_conn, release_conn, dict_cursor):
    def fix():
        conn = get_conn()
        try:
            cur = dict_cursor(conn)
            cur.execute(
                "SELECT id, user_id FROM email_drafts WHERE status = 'pending' AND send_error IS NOT NULL "
                "AND send_error NOT LIKE %s AND created_at > now() - make_interval(hours => %s) "
                "ORDER BY id LIMIT %s",
                (INTERRUPTED_PREFIX + "%", EMAIL_RETRY_HOURS, EMAIL_RETRY_LIMIT),
            )
            rows = cur.fetchall()
            if not rows:
                return f"no failed sends from the last {EMAIL_RETRY_HOURS}h to retry"
            sent, errors = 0, []
            for r in rows:
                try:
                    code, payload = send_email_record(cur, conn, r["user_id"], r["id"])
                except Exception as e:
                    conn.rollback()
                    code, payload = 500, {"error": repr(e)}
                if code == 200 and payload.get("ok"):
                    sent += 1
                else:
                    errors.append(f"#{r['id']}: {(payload.get('error') or code)}"[:120])
            msg = f"sent {sent} of {len(rows)}"
            return msg + (f"; still failing: {'; '.join(errors[:3])}" if errors else "")
        finally:
            release_conn(conn)
    return fix


# email_drafts has no "sending since" column, and created_at is when the
# draft was written, not when sending began -- an old draft being sent
# right now must never look stuck. So the probe loop notes when each row
# was first seen in 'sending' (observe_sending), and only rows seen there
# for STUCK_SENDING_MINUTES count. In memory, so after a restart it takes
# that long again before anything can be released: slow, never wrong.
_sending_seen: dict = {}  # draft id -> time.monotonic() first seen in 'sending'
_email_deps: dict = {}


def observe_sending() -> None:
    if not _email_deps:
        return
    conn = _email_deps["get_conn"]()
    try:
        cur = _email_deps["dict_cursor"](conn)
        cur.execute("SELECT id FROM email_drafts WHERE status = 'sending'")
        ids = {r["id"] for r in cur.fetchall()}
        conn.commit()
    finally:
        _email_deps["release_conn"](conn)
    now = time.monotonic()
    for i in list(_sending_seen):
        if i not in ids:
            del _sending_seen[i]
    for i in ids:
        _sending_seen.setdefault(i, now)


def stuck_sending_ids() -> list:
    cutoff = time.monotonic() - STUCK_SENDING_MINUTES * 60
    return sorted(i for i, seen in _sending_seen.items() if seen <= cutoff)


def _email_unstick(get_conn, release_conn, dict_cursor):
    def fix():
        ids = stuck_sending_ids()
        if not ids:
            return f"nothing has been stuck in 'sending' for {STUCK_SENDING_MINUTES}+ min (watched since the last restart)"
        conn = get_conn()
        try:
            cur = dict_cursor(conn)
            cur.execute(
                "UPDATE email_drafts SET status = 'pending', send_error = %s "
                "WHERE status = 'sending' AND id = ANY(%s) RETURNING id",
                (f"{INTERRUPTED_PREFIX} -- it may already have gone out; check the Sent folder before sending again.",
                 ids),
            )
            moved = [r["id"] for r in cur.fetchall()]
            conn.commit()
        finally:
            release_conn(conn)
        for i in moved:
            _sending_seen.pop(i, None)
        return f"moved {len(moved)} email(s) back to the user's drafts" if moved else "nothing stuck in 'sending'"
    return fix


# ---------------------------------------------------------------- systemd

def _restart_unit(unit):
    def fix():
        try:
            res = subprocess.run(["sudo", "-n", "systemctl", "restart", unit], capture_output=True, text=True, timeout=60)
        except FileNotFoundError:
            raise FixError("sudo/systemctl not available on this host")
        if res.returncode != 0:
            raise FixError((res.stderr or res.stdout or f"exit {res.returncode}").strip()[:300])
        active = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=20).stdout.strip()
        if active != "active":
            raise FixError(f"restarted, but {unit} is now '{active}'")
        return f"{unit} restarted and active"
    return fix


def restartable_units() -> list:
    units = [u.strip() for u in os.getenv("OPS_RESTARTABLE_UNITS", "").split(",") if u.strip()]
    # Restarting the app itself from inside one of its own requests would
    # cut the request off mid-way -- that one stays a terminal job.
    return [u for u in units if u not in ("throughline", "throughline.service")]


def install(*, rq_queues=(), email=None) -> None:
    for q in rq_queues:
        register(f"rq_requeue_failed:{q.name}", f"Retry failed '{q.name}' jobs", _rq_requeue(q),
                 f"Put every failed job in the '{q.name}' queue back in line? They run again once a worker picks them up.")
        register(f"rq_clear_failed:{q.name}", f"Clear failed '{q.name}' jobs", _rq_clear(q),
                 f"Delete every failed job in the '{q.name}' queue? They can't be retried afterwards.", danger=True)
    if email is not None:
        send_email_record, get_conn, release_conn, dict_cursor = email
        _email_deps.update(get_conn=get_conn, release_conn=release_conn, dict_cursor=dict_cursor)
        register("email_retry_failed", "Retry failed email sends",
                 _email_retry(send_email_record, get_conn, release_conn, dict_cursor),
                 f"Send again every email from the last {EMAIL_RETRY_HOURS}h that failed to send (up to "
                 f"{EMAIL_RETRY_LIMIT})? Each one was already approved by its user. Emails interrupted mid-send are skipped.")
        register("email_unstick_sending", "Release emails stuck mid-send",
                 _email_unstick(get_conn, release_conn, dict_cursor),
                 f"Move emails that have sat in 'sending' for over {STUCK_SENDING_MINUTES} min back to the user's drafts, "
                 "marked 'may already have gone out'? Nothing is sent.")
    for unit in restartable_units():
        register(f"restart_unit:{unit}", f"Restart {unit}", _restart_unit(unit),
                 f"Restart the {unit} service on the server now?", danger=True)
