"""Where ops alerts go: email (ADMIN_ALERT_EMAIL) and, when configured,
a push to the admin's phone through the app's own FCM sender.

app.py hands over its push function once (register_push); the user ids to
push to come from ADMIN_ALERT_USER_IDS (comma-separated Throughline user
ids). Either channel can be off; every send is best-effort."""
from __future__ import annotations

import os
import smtplib
from email.mime.text import MIMEText
from email.utils import formataddr

DASHBOARD_URL = os.getenv("OPS_DASHBOARD_URL", "https://129-213-21-239.sslip.io/admin")

_push_fn = None


def register_push(send_fcm_to_user) -> None:
    global _push_fn
    _push_fn = send_fcm_to_user


def alert_email() -> str:
    return os.getenv("ADMIN_ALERT_EMAIL", "").strip()


def push_user_ids() -> list:
    out = []
    for part in os.getenv("ADMIN_ALERT_USER_IDS", "").split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out


def channels() -> dict:
    return {"email": bool(alert_email()), "push": bool(_push_fn and push_user_ids())}


def send_email(to_email: str, subject: str, body: str) -> bool:
    """Minimal standalone SMTP sender, same Hostinger implicit-TLS
    convention as app.py's _smtp_send -- reimplemented here so this
    package stays independent of app.py."""
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "465"))
    username = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")
    if not (host and username and password):
        print("[ops.notify] SMTP not configured -- cannot send alert email")
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
        print(f"[ops.notify] send failed: {e!r}")
        return False


def push(title: str, body: str) -> int:
    """Returns how many admin users a push was handed to."""
    if not _push_fn:
        return 0
    n = 0
    for uid in push_user_ids():
        try:
            _push_fn(uid, title, body[:180], data={"type": "ops_alert", "url": DASHBOARD_URL})
            n += 1
        except Exception as e:
            print(f"[ops.notify] push to {uid} failed: {e!r}")
    return n
