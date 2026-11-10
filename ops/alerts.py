"""Alerting -- turns the admin dashboard from a pull view (only useful if
someone opens it) into something that catches a job going stale or a
health check failing, and says so when it's fixed.

Every ALERT_CHECK_INTERVAL_SECONDS it looks at every job:
  - failing (stale, a failed run, or a failing health check) on
    ALERT_CONFIRM_COUNT checks in a row -> opens an incident and sends ONE
    alert (email + push), with the runbook's cause and command in it;
  - still failing REMIND_HOURS later and not acknowledged/snoozed -> one
    reminder;
  - healthy again -> closes the incident and sends one "recovered"
    message (only if the alert itself went out), and clears its
    acknowledgement.
Status changes, alerts and recoveries all go to the event log.

Has its own SMTP sender (ops/notify.py), not app.py's, so this package
stays independent of the main app."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from . import acks, events, notify
from . import db as ops_db
from .jobs import job_status
from .probe import probe_jobs
from .runbooks import RUNBOOKS
from .schema import ensure_ops_schema
from .tracking import record_completed_run

ALERT_CHECK_INTERVAL_SECONDS = 300  # 5 min
ALERT_CONFIRM_COUNT = 2  # consecutive failing checks before alerting (~5-10 min)
REMIND_HOURS = 24  # one reminder a day while still failing and nobody acknowledged it

_send_alert_email = notify.send_email  # module-level so tests can swap it

_consecutive: dict = {}  # job_name -> consecutive failing checks, in-memory
_last_level: dict = {}  # job_name -> "ok"/"failing" seen last check, for status events
_healthy_since: dict = {}  # job_name -> when it last became healthy, in-memory
ACK_CLEAR_AFTER_HEALTHY_SECONDS = 3600  # an acknowledgement survives blips shorter than this


def _failing(job) -> bool:
    if job.get("on_ice") or job.get("disabled"):
        return False
    return bool(job["stale"]) or job["status"] in ("error", "failed")


def _problem(job) -> str:
    d = job.get("detail") or {}
    if isinstance(d, dict) and (d.get("problem") or d.get("error")):
        return str(d.get("problem") or d.get("error"))[:300]
    if job["status"] == "never_run":
        return "has never reported a run"
    if job["stale"]:
        age = f"{job['age_hours']:.1f}h ago" if job["age_hours"] is not None else "never"
        return f"no run reported since {age} (expected: {job.get('cadence_label')})"
    return f"last run reported '{job['status']}'"


def _send(subject: str, body: str) -> dict:
    sent = {"email": False, "push": 0}
    to = notify.alert_email()
    if to:
        sent["email"] = _send_alert_email(to, subject, body + f"\n\nDashboard: {notify.DASHBOARD_URL}")
    sent["push"] = notify.push(subject.replace("[Throughline] ", ""), body.split("\n", 1)[0])
    return sent


def _alert_body(job_name, problem, reminder=False):
    rb = RUNBOOKS.get(f"job:{job_name}") or {}
    lines = [f"{job_name}: {problem}" + (" (still failing)" if reminder else "")]
    if rb.get("cause"):
        lines += ["", "Likely cause: " + rb["cause"]]
    if rb.get("command"):
        lines += ["", "Run: " + rb["command"]]
    if rb.get("fixed_when"):
        lines += ["", "Fixed when: " + rb["fixed_when"]]
    lines += ["", "Acknowledge or snooze it on the dashboard to stop reminders."]
    return "\n".join(lines)


def check_once(cur, conn) -> dict:
    """One pass over every job. Returns what it found, for the
    ops_alert_check diagnostic row."""
    checked = {}
    probe_names = set(probe_jobs())
    jobs = job_status(cur, conn)
    cur.execute("SELECT job_name, opened_at, problem, last_notified_at FROM ops.incidents")
    incidents = {r["job_name"]: r for r in cur.fetchall()}
    now = datetime.now(timezone.utc)

    for job in jobs:
        name = job["job_name"]
        key = f"job:{name}"
        failing = _failing(job)
        level = "failing" if failing else "ok"
        checked[name] = {"failing": failing, "status": job["status"], "stale": job["stale"]}

        # Status changes for jobs that aren't probes (probes log their own,
        # the moment they happen).
        prev = _last_level.get(name)
        _last_level[name] = level
        if name not in probe_names and prev is not None and prev != level:
            events.record("status", name, ("healthy → failing: " + _problem(job)) if failing else "failing → healthy",
                          detail={"to": level}, cur=cur, conn=conn)

        inc = incidents.pop(name, None)
        if not failing:
            _consecutive[name] = 0
            _healthy_since.setdefault(name, now)
            if inc:
                cur.execute("DELETE FROM ops.incidents WHERE job_name = %s", (name,))
                conn.commit()
                mins = round((now - inc["opened_at"]).total_seconds() / 60)
                msg = f"recovered after {mins} min"
                # No "recovered" email while it's snoozed -- a flapping service (e.g. CostLens
                # behind a tunnel) would otherwise email on every blip.
                cur.execute("SELECT 1 FROM ops.acks WHERE item_key = %s AND kind = 'snooze' AND until_at > now()", (key,))
                snoozed = cur.fetchone() is not None
                if inc["last_notified_at"] and not snoozed:
                    checked[name]["recovery_sent"] = _send(f"[Throughline] {name} recovered", f"{name} is healthy again ({msg}).")
                events.record("recovered", name, msg, cur=cur, conn=conn)
            # An acknowledgement ends only once it has stayed healthy for a while, so a brief
            # recovery between failures doesn't re-arm alerts for the same flapping problem.
            if (now - _healthy_since[name]).total_seconds() >= ACK_CLEAR_AFTER_HEALTHY_SECONDS:
                acks.clear_on_recovery(cur, conn, key)
            continue

        _healthy_since.pop(name, None)

        _consecutive[name] = _consecutive.get(name, 0) + 1
        checked[name]["consecutive"] = _consecutive[name]
        if _consecutive[name] < ALERT_CONFIRM_COUNT and not inc:
            continue
        problem = _problem(job)
        silenced = acks.is_silenced(cur, key)
        checked[name]["silenced"] = silenced
        if not inc:
            cur.execute("INSERT INTO ops.incidents (job_name, problem) VALUES (%s, %s) ON CONFLICT (job_name) DO NOTHING",
                        (name, problem))
            conn.commit()
            if silenced:
                events.record("alert", name, f"failing (not notified -- acknowledged/snoozed): {problem}", cur=cur, conn=conn)
                continue
            sent = _send(f"[Throughline] {name} is failing", _alert_body(name, problem))
            cur.execute("UPDATE ops.incidents SET last_notified_at = now() WHERE job_name = %s", (name,))
            conn.commit()
            checked[name]["alert_sent"] = sent
            events.record("alert", name, problem, detail={"sent": sent}, cur=cur, conn=conn)
            print(f"[ops_alert_worker] alert for {name}: {problem}", flush=True)
        elif not silenced and (inc["last_notified_at"] is None
                               or (now - inc["last_notified_at"]).total_seconds() >= REMIND_HOURS * 3600):
            sent = _send(f"[Throughline] {name} is still failing", _alert_body(name, problem, reminder=True))
            cur.execute("UPDATE ops.incidents SET last_notified_at = now(), problem = %s WHERE job_name = %s", (problem, name))
            conn.commit()
            checked[name]["reminder_sent"] = sent
            events.record("alert", name, "still failing: " + problem, detail={"sent": sent, "reminder": True}, cur=cur, conn=conn)

    # Incidents for jobs that no longer exist (a deleted custom job).
    for gone in incidents:
        cur.execute("DELETE FROM ops.incidents WHERE job_name = %s", (gone,))
        conn.commit()
    return checked


def open_incidents(cur) -> dict:
    cur.execute("SELECT job_name, opened_at, problem, last_notified_at FROM ops.incidents")
    return {r["job_name"]: {"opened_at": r["opened_at"].isoformat(), "problem": r["problem"],
                            "last_notified_at": r["last_notified_at"].isoformat() if r["last_notified_at"] else None}
            for r in cur.fetchall()}


def ops_alert_worker():
    """Runs continuously in a background thread (app.py starts it). Sleeps
    one interval before its first check, so the moment right after a
    restart -- when every worker is starting at once -- is never judged;
    with ALERT_CONFIRM_COUNT on top, a one-check blip never alerts.
    Runs even with no alert channel configured: the event log, incidents
    and acknowledgement clean-up still depend on it."""
    time.sleep(ALERT_CHECK_INTERVAL_SECONDS)
    while True:
        conn = None
        cycle_started_at = datetime.now(timezone.utc)
        try:
            conn = ops_db.get_conn()
            cur = ops_db.dict_cursor(conn)
            ensure_ops_schema(cur, conn)
            checked = check_once(cur, conn)
            # Diagnostic heartbeat on every cycle -- positive proof of each
            # check's timing and findings.
            record_completed_run(cur, conn, "ops_alert_check", cycle_started_at, "success",
                                 {"checked": checked, "channels": notify.channels()})
            cur.close()
        except Exception as e:
            print(f"[ops_alert_worker] error: {e!r}", flush=True)
            if conn:
                conn.rollback()
        finally:
            if conn:
                ops_db.release_conn(conn)
        time.sleep(ALERT_CHECK_INTERVAL_SECONDS)
