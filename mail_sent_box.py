"""Sent-folder record and verification for email the app sends.

Every email goes out over SMTP from the shared mailbox (see app.py's
_smtp_send). SMTP alone never files a copy anywhere -- confirmed live:
Hostinger's INBOX.Sent stayed at 0 messages after many real sends -- so
"was it sent?" had no answer except the app's own word for it. This module
puts each sent message into the mailbox's Sent folder over IMAP (unless the
provider already filed it) and then looks it up again by Message-ID, so a
"sent" status is backed by the mailbox itself.

Bounces come back to the same mailbox's INBOX (mailer-daemon reports quote
the original Message-ID), so find_bounce() lets a later status check notice
that a message the server accepted was not actually delivered.

Settings: IMAP_HOST (default: SMTP_HOST with "smtp." swapped for "imap."),
IMAP_PORT (993), IMAP_SENT_FOLDER (default: the folder flagged \\Sent),
login = SMTP_USERNAME / SMTP_PASSWORD. EMAIL_SENT_BOX_ENABLED=false turns
the whole thing off (sends still work, they just report unverified)."""

import imaplib
import os
import re
import time

_SENT_NAME_FALLBACKS = ("INBOX.Sent", "Sent", "Sent Items", "Sent Messages", "INBOX/Sent")
_LIST_RE = re.compile(r'\((?P<flags>[^)]*)\)\s+"(?P<sep>[^"]*)"\s+(?P<name>.+)$')


def settings():
    smtp_host = os.getenv("SMTP_HOST") or ""
    host = os.getenv("IMAP_HOST") or (smtp_host.replace("smtp.", "imap.", 1) if smtp_host.startswith("smtp.") else "")
    return {
        "host": host,
        "port": int(os.getenv("IMAP_PORT", "993")),
        "username": os.getenv("SMTP_USERNAME"),
        "password": os.getenv("SMTP_PASSWORD"),
        "sent_folder": os.getenv("IMAP_SENT_FOLDER") or None,
    }


def enabled():
    if os.getenv("EMAIL_SENT_BOX_ENABLED", "true").strip().lower() == "false":
        return False
    s = settings()
    return bool(s["host"] and s["username"] and s["password"])


def _connect(imap_factory):
    s = settings()
    conn = imap_factory(s["host"], s["port"], timeout=15)
    conn.login(s["username"], s["password"])
    return conn, s


def _quote(name):
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _find_sent_folder(conn, configured=None):
    if configured:
        return configured
    typ, rows = conn.list()
    names = []
    for raw in rows or []:
        line = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
        m = _LIST_RE.match(line.strip())
        if not m:
            continue
        name = m.group("name").strip().strip('"')
        if "\\sent" in m.group("flags").lower():
            return name
        names.append(name)
    for candidate in _SENT_NAME_FALLBACKS:
        if candidate in names:
            return candidate
    return None


def _search_message_id(conn, folder, message_id):
    typ, _ = conn.select(_quote(folder), readonly=True)
    if typ != "OK":
        return False
    typ, data = conn.search(None, "HEADER", "Message-ID", _quote(message_id))
    return typ == "OK" and bool(data and data[0].split())


def save_and_verify(raw_message, message_id, imap_factory=imaplib.IMAP4_SSL):
    """File raw_message (bytes) in the Sent folder unless it is already there,
    then confirm it by Message-ID. Never raises: returns
    {"verified", "folder", "saved_by", "error"}."""
    if not enabled():
        return {"verified": False, "folder": None, "saved_by": None, "error": "Sent-folder check is not configured"}
    conn = None
    try:
        conn, s = _connect(imap_factory)
        folder = _find_sent_folder(conn, s["sent_folder"])
        if not folder:
            return {"verified": False, "folder": None, "saved_by": None, "error": "No Sent folder found in the mailbox"}
        if _search_message_id(conn, folder, message_id):
            return {"verified": True, "folder": folder, "saved_by": "provider", "error": None}
        typ, _ = conn.append(_quote(folder), "(\\Seen)", imaplib.Time2Internaldate(time.time()), raw_message)
        if typ != "OK":
            return {"verified": False, "folder": folder, "saved_by": None, "error": "The Sent folder refused the copy"}
        found = _search_message_id(conn, folder, message_id)
        return {
            "verified": found, "folder": folder, "saved_by": "app" if found else None,
            "error": None if found else "Copy saved but not found again in the Sent folder",
        }
    except Exception as e:
        return {"verified": False, "folder": None, "saved_by": None, "error": f"Sent folder unreachable: {e!r}"[:300]}
    finally:
        if conn is not None:
            try:
                conn.logout()
            except Exception:
                pass


def verify(message_id, imap_factory=imaplib.IMAP4_SSL):
    """Live re-check that message_id is in the Sent folder. Never raises."""
    if not message_id:
        return {"verified": False, "folder": None, "error": "No Message-ID recorded for this email"}
    if not enabled():
        return {"verified": False, "folder": None, "error": "Sent-folder check is not configured"}
    conn = None
    try:
        conn, s = _connect(imap_factory)
        folder = _find_sent_folder(conn, s["sent_folder"])
        if not folder:
            return {"verified": False, "folder": None, "error": "No Sent folder found in the mailbox"}
        found = _search_message_id(conn, folder, message_id)
        return {"verified": found, "folder": folder, "error": None if found else "Not found in the Sent folder"}
    except Exception as e:
        return {"verified": False, "folder": None, "error": f"Sent folder unreachable: {e!r}"[:300]}
    finally:
        if conn is not None:
            try:
                conn.logout()
            except Exception:
                pass


def find_bounce(message_id, imap_factory=imaplib.IMAP4_SSL):
    """True if a delivery-failure report quoting message_id sits in INBOX,
    False if none, None if the inbox could not be checked."""
    if not message_id or not enabled():
        return None
    conn = None
    try:
        conn, _ = _connect(imap_factory)
        typ, _ = conn.select("INBOX", readonly=True)
        if typ != "OK":
            return None
        bare = message_id.strip("<>")
        # Only delivery reports count -- a copy of the message itself in
        # INBOX (an email sent to this same mailbox) also quotes its id.
        typ, data = conn.search(None, "TEXT", _quote(bare), "OR", "FROM", '"mailer-daemon"', "FROM", '"postmaster"')
        if typ != "OK":
            return None
        return bool(data and data[0].split())
    except Exception:
        return None
    finally:
        if conn is not None:
            try:
                conn.logout()
            except Exception:
                pass
