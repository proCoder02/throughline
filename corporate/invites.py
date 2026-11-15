"""Invitation links. A link carries a random token; only its hash is stored.
An invitation can only be accepted by the account whose email it was sent
to, so a forwarded link doesn't let someone else in."""
from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

from .config import INVITE_DAYS

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalize_email(value) -> str | None:
    email = str(value or "").strip().lower()
    return email if _EMAIL_RE.match(email) and len(email) <= 254 else None


def parse_emails(raw) -> tuple[list, list]:
    """A list, or one string with emails separated by commas, semicolons,
    spaces or new lines (a pasted column from a spreadsheet works).
    Returns (valid unique emails in order, rejected strings)."""
    items = raw if isinstance(raw, list) else re.split(r"[\s,;]+", str(raw or ""))
    good, bad, seen = [], [], set()
    for item in items:
        item = str(item).strip()
        if not item:
            continue
        email = normalize_email(item)
        if not email:
            bad.append(item)
        elif email not in seen:
            seen.add(email)
            good.append(email)
    return good, bad


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return (local[:1] + "***@" + domain) if domain else "***"


def status_of(row) -> str:
    if row["revoked_at"]:
        return "revoked"
    if row["accepted_at"]:
        return "accepted"
    if row["expires_at"] < datetime.now(timezone.utc):
        return "expired"
    return "pending"


def create(cur, org_id: int, email: str, role: str, unit_id, unit_role, invited_by: int) -> tuple[int, str]:
    """Replaces any still-pending invitation to the same address in this
    organisation, so the newest link is the only one that works."""
    cur.execute(
        "UPDATE corp.invites SET revoked_at = now() WHERE org_id = %s AND email = %s "
        "AND accepted_at IS NULL AND revoked_at IS NULL",
        (org_id, email),
    )
    token = secrets.token_urlsafe(24)
    cur.execute(
        "INSERT INTO corp.invites (org_id, email, role, unit_id, unit_role, token_hash, invited_by, expires_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (org_id, email, role, unit_id, unit_role if unit_id else None, hash_token(token), invited_by,
         datetime.now(timezone.utc) + timedelta(days=INVITE_DAYS)),
    )
    return cur.fetchone()["id"], token


def list_for_org(cur, org_id: int) -> list:
    cur.execute(
        "SELECT i.id, i.email, i.role, i.unit_id, un.name AS unit_name, i.unit_role, i.created_at, i.expires_at, "
        "i.accepted_at, i.revoked_at, inv.username AS invited_by "
        "FROM corp.invites i LEFT JOIN corp.units un ON un.id = i.unit_id "
        "LEFT JOIN users inv ON inv.id = i.invited_by "
        "WHERE i.org_id = %s AND i.revoked_at IS NULL ORDER BY i.created_at DESC LIMIT 500",
        (org_id,),
    )
    out = []
    for r in cur.fetchall():
        d = dict(r)
        d["status"] = status_of(r)
        for k in ("created_at", "expires_at", "accepted_at", "revoked_at"):
            d[k] = d[k].isoformat() if d[k] else None
        out.append(d)
    return out


def get_by_id(cur, org_id: int, invite_id: int):
    cur.execute("SELECT * FROM corp.invites WHERE id = %s AND org_id = %s", (invite_id, org_id))
    return cur.fetchone()


def get_by_token(cur, token: str, for_update: bool = False):
    cur.execute(
        "SELECT i.*, o.name AS org_name, un.name AS unit_name, un.kind AS unit_kind FROM corp.invites i "
        "JOIN corp.orgs o ON o.id = i.org_id LEFT JOIN corp.units un ON un.id = i.unit_id "
        "WHERE i.token_hash = %s" + (" FOR UPDATE OF i" if for_update else ""),
        (hash_token(token or ""),),
    )
    return cur.fetchone()


def revoke(cur, invite_id: int) -> None:
    cur.execute("UPDATE corp.invites SET revoked_at = now() WHERE id = %s AND accepted_at IS NULL", (invite_id,))


def mark_accepted(cur, invite_id: int, user_id: int) -> None:
    cur.execute("UPDATE corp.invites SET accepted_at = now(), accepted_by = %s WHERE id = %s", (user_id, invite_id))
