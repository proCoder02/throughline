"""Stale-job email alerting -- turns the admin dashboard from a pull view
(only useful if someone opens it) into something that actually catches a
job going silently stale overnight. Has its own minimal SMTP sender
(reimplemented, not imported from app.py's send_plain_email) so this
package has zero dependency on the main app -- same self-contained
convention every emotional_intelligence/ script already follows for its
own call_llm rather than importing app.py's."""
from __future__ import annotations

import os
import smtplib
import time
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.utils import formataddr

from . import db as ops_db
from .jobs import job_status
from .schema import ensure_ops_schema
from .tracking import record_completed_run

ALERT_CHECK_INTERVAL_SECONDS = 1800  # 30 min
ALERT_COOLDOWN_HOURS = 6  # re-alert while still stale, but not every check
ALERT_CONFIRM_COUNT = 2  # consecutive stale checks required before actually alerting

_consecutive_stale: dict = {}  # job_name -> count, in-memory, reset to 0 the moment a job looks healthy


def _ensure_alerts_table(cur, conn) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ops.alerts_sent (
            job_name TEXT PRIMARY KEY,
            last_alerted_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    conn.commit()


def _send_alert_email(to_email: str, subject: str, body: str) -> bool:
    """Minimal standalone SMTP sender, same Hostinger implicit-TLS
    convention as app.py's _smtp_send -- reimplemented here rather than
    imported, so this package stays fully independent of app.py."""
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "465"))
    username = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")
    if not (host and username and password):
        print("[ops.alerts] SMTP not configured -- cannot send alert email")
        return False
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = formataddr(("Throughline Ops", username))
    msg["To"] = to_email
    try:
        with smtplib.SMTP_SSL(host, port, timeout=20) as server:
            server.login(username, password)
            server.sendmail(username, [to_email], msg.as_string())
        return True
    except Exception as e:
        print(f"[ops.alerts] send failed: {e!r}")
        return False


def ops_alert_worker():
    """Runs continuously in a background thread (app.py starts it, same
    pattern as its own email_reminder_worker). Only fires when
    ADMIN_ALERT_EMAIL is configured; otherwise this loop is a cheap no-op,
    same convention as nudge_engine.is_enabled()'s early return.

    Two safeguards against a false alarm right after a deploy restart
    (confirmed live: the very first check immediately after a restart once
    reported ei_pipeline as never_run despite a real, hours-old successful
    row already in ops.job_runs -- some transient visibility/startup race,
    root cause not pinned down, but a one-off blip is exactly what these
    two guards are for regardless of its exact cause):
    1. Sleeps one full interval before its very first check, instead of
       checking immediately on thread start -- skips the riskiest window
       (the instant right after this and every other background worker/
       migration starts concurrently).
    2. Requires a job to look stale on ALERT_CONFIRM_COUNT consecutive
       checks (default 2, i.e. ~30-60 min of confirmed staleness) before
       sending anything -- a transient one-check blip no longer reaches
       _send_alert_email at all, not just a logging fix."""
    alert_email = os.getenv("ADMIN_ALERT_EMAIL", "").strip()
    time.sleep(ALERT_CHECK_INTERVAL_SECONDS)
    while True:
        if not alert_email:
            time.sleep(ALERT_CHECK_INTERVAL_SECONDS)
            continue
        conn = None
        cycle_started_at = datetime.now(timezone.utc)
        checked = {}
        try:
            conn = ops_db.get_conn()
            cur = ops_db.dict_cursor(conn)
            ensure_ops_schema(cur, conn)
            _ensure_alerts_table(cur, conn)
            for job in job_status(cur, conn):
                job_name = job["job_name"]
                checked[job_name] = {"stale": job["stale"], "age_hours": job["age_hours"], "status": job["status"]}
                if not job["stale"]:
                    _consecutive_stale[job_name] = 0
                    continue
                _consecutive_stale[job_name] = _consecutive_stale.get(job_name, 0) + 1
                checked[job_name]["consecutive"] = _consecutive_stale[job_name]
                if _consecutive_stale[job_name] < ALERT_CONFIRM_COUNT:
                    print(f"[ops_alert_worker] {job_name} looks stale "
                          f"({_consecutive_stale[job_name]}/{ALERT_CONFIRM_COUNT} checks) -- not yet confirmed", flush=True)
                    continue
                cur.execute("SELECT last_alerted_at FROM ops.alerts_sent WHERE job_name = %s", (job_name,))
                row = cur.fetchone()
                if row and (datetime.now(timezone.utc) - row["last_alerted_at"]).total_seconds() < ALERT_COOLDOWN_HOURS * 3600:
                    checked[job_name]["skipped_cooldown"] = True
                    continue
                age_desc = f"{job['age_hours']:.1f}h ago" if job["age_hours"] is not None else "never run"
                email_ok = _send_alert_email(
                    alert_email,
                    f"[Throughline] {job_name} is stale",
                    f"{job_name} last ran: {age_desc} (status: {job['status']}).\n\n"
                    f"Check https://129-213-21-239.sslip.io/admin for details.",
                )
                checked[job_name]["email_sent"] = email_ok
                cur.execute(
                    "INSERT INTO ops.alerts_sent (job_name, last_alerted_at) VALUES (%s, now()) "
                    "ON CONFLICT (job_name) DO UPDATE SET last_alerted_at = now()",
                    (job_name,),
                )
                conn.commit()
                print(f"[ops_alert_worker] sent stale-job alert for {job_name}", flush=True)
            # Diagnostic heartbeat on every cycle, not just when something's
            # stale -- confirmed live twice now that an alert email can
            # arrive with zero trace anywhere (ops.alerts_sent, journalctl)
            # of the check that supposedly sent it; this gives positive,
            # permanent proof of every single check's timing and findings,
            # so a third occurrence (if the problem is real and ongoing,
            # not stale mail delivery) is actually diagnosable.
            record_completed_run(cur, conn, "ops_alert_check", cycle_started_at, "success", {"checked": checked})
            cur.close()
        except Exception as e:
            print(f"[ops_alert_worker] error: {e!r}", flush=True)
            if conn:
                conn.rollback()
        finally:
            if conn:
                ops_db.release_conn(conn)
        time.sleep(ALERT_CHECK_INTERVAL_SECONDS)
