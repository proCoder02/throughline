"""Colleague chat: a team thread per unit and direct threads between two
members. Small on purpose -- it's a capture source for the ledger, not a
Slack replacement."""
from __future__ import annotations

from . import access
from .errors import CorpError


def team_thread(cur, org_id: int, unit_id: int) -> int:
    cur.execute("INSERT INTO corp.threads (org_id, unit_id, kind) VALUES (%s, %s, 'team') "
                "ON CONFLICT (unit_id) DO NOTHING", (org_id, unit_id))
    cur.execute("SELECT id FROM corp.threads WHERE unit_id = %s", (unit_id,))
    return cur.fetchone()["id"]


def direct_thread(cur, org_id: int, a: int, b: int) -> int:
    if a == b:
        raise CorpError(400, "Pick someone else to message.")
    lo, hi = sorted((a, b))
    key = f"{org_id}:{lo}:{hi}"
    cur.execute("INSERT INTO corp.threads (org_id, kind, dm_key) VALUES (%s, 'direct', %s) ON CONFLICT (dm_key) DO NOTHING",
                (org_id, key))
    cur.execute("SELECT id FROM corp.threads WHERE dm_key = %s", (key,))
    tid = cur.fetchone()["id"]
    for uid in (lo, hi):
        cur.execute("INSERT INTO corp.thread_members (thread_id, user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (tid, uid))
    return tid


def list_threads(cur, org_id: int, me: int) -> list:
    # Team threads exist for every unit I'm in, even before the first message.
    cur.execute("SELECT u.id FROM corp.unit_members m JOIN corp.units u ON u.id = m.unit_id "
                "WHERE m.user_id = %s AND u.org_id = %s", (me, org_id))
    for r in cur.fetchall():
        team_thread(cur, org_id, r["id"])
    cur.execute(
        "SELECT x.id, x.kind, x.unit_id, un.name AS unit_name, x.last_message_at, "
        "(SELECT us.username FROM corp.thread_members o JOIN users us ON us.id = o.user_id "
        "  WHERE o.thread_id = x.id AND o.user_id <> %(me)s LIMIT 1) AS other_name, "
        "(SELECT o.user_id FROM corp.thread_members o WHERE o.thread_id = x.id AND o.user_id <> %(me)s LIMIT 1) AS other_id, "
        "(SELECT body FROM corp.messages mm WHERE mm.thread_id = x.id ORDER BY mm.id DESC LIMIT 1) AS last_body, "
        "(SELECT count(*) FROM corp.messages mm WHERE mm.thread_id = x.id AND mm.sender_id IS DISTINCT FROM %(me)s "
        "  AND mm.id > COALESCE((SELECT last_read_id FROM corp.thread_members t WHERE t.thread_id = x.id AND t.user_id = %(me)s), 0)) AS unread "
        f"FROM corp.threads x LEFT JOIN corp.units un ON un.id = x.unit_id WHERE x.org_id = %(org)s AND {access.threads()} "
        "ORDER BY x.last_message_at DESC NULLS LAST, x.id",
        {"me": me, "org": org_id})
    out = []
    for r in cur.fetchall():
        d = dict(r)
        d["name"] = d["unit_name"] if d["kind"] == "team" else (d["other_name"] or "Direct message")
        d["last_message_at"] = d["last_message_at"].isoformat() if d["last_message_at"] else None
        out.append(d)
    return out


def get_thread(cur, org_id: int, thread_id: int, me: int):
    row = access.one(cur, "threads", access.threads(), thread_id, me, org_id)
    if not row:
        raise CorpError(404, "Conversation not found.")
    return row


def messages(cur, thread_id: int, after: int = 0, limit: int = 200) -> list:
    cur.execute("SELECT m.id, m.sender_id, COALESCE(us.username, CASE WHEN m.kind = 'text' THEN 'Former member' END) AS sender, "
                "m.kind, m.body, m.meta, m.created_at FROM corp.messages m "
                "LEFT JOIN users us ON us.id = m.sender_id WHERE m.thread_id = %s AND m.id > %s ORDER BY m.id DESC LIMIT %s",
                (thread_id, after, limit))
    rows = [dict(r) | {"created_at": r["created_at"].isoformat()} for r in cur.fetchall()]
    return rows[::-1]


def post(cur, thread_id: int, sender_id, body: str, kind: str = "text", meta=None) -> int:
    import json
    body = (body or "").strip()
    if not body:
        raise CorpError(400, "Write something first.")
    cur.execute("INSERT INTO corp.messages (thread_id, sender_id, kind, body, meta) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                (thread_id, sender_id, kind, body[:4000], json.dumps(meta or {})))
    mid = cur.fetchone()["id"]
    cur.execute("UPDATE corp.threads SET last_message_at = now() WHERE id = %s", (thread_id,))
    if sender_id:
        mark_read(cur, thread_id, sender_id, mid)
    return mid


def mark_read(cur, thread_id: int, user_id: int, upto: int) -> None:
    cur.execute("INSERT INTO corp.thread_members (thread_id, user_id, last_read_id) VALUES (%s,%s,%s) "
                "ON CONFLICT (thread_id, user_id) DO UPDATE SET last_read_id = GREATEST(corp.thread_members.last_read_id, EXCLUDED.last_read_id)",
                (thread_id, user_id, upto))


def recipients(cur, thread) -> list:
    if thread["kind"] == "team":
        return access.unit_member_ids(cur, thread["unit_id"])
    cur.execute("SELECT user_id FROM corp.thread_members WHERE thread_id = %s", (thread["id"],))
    return [r["user_id"] for r in cur.fetchall()]
