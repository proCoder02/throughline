"""Acknowledge / snooze a problem on the dashboard.

  - acknowledge: "I'm on it". Silences repeat alerts for that item and
    greys it out in the attention list. Clears itself when the item
    recovers (jobs), and lapses after ACK_HOURS at the latest, so a
    forgotten ack can never hide a problem for good.
  - snooze: silence for a set number of hours, then it comes back.

Items are keyed like the dashboard's attention list: "job:<job_name>",
"task:<task>", "email:failed", "compute:disk", ..."""
from __future__ import annotations

import re

from . import events

ACK_HOURS = 24
MAX_SNOOZE_HOURS = 72
KEY_RE = re.compile(r"^[a-z]+:[A-Za-z0-9_.\-]{1,80}$")


class BadKey(ValueError):
    pass


def _check(key: str) -> str:
    key = (key or "").strip()
    if not KEY_RE.match(key):
        raise BadKey(f"not a valid item key: {key!r}")
    return key


def _upsert(cur, conn, key, kind, hours, note, actor):
    cur.execute(
        "INSERT INTO ops.acks (item_key, kind, until_at, note, actor) "
        "VALUES (%s, %s, now() + %s * interval '1 hour', %s, %s) "
        "ON CONFLICT (item_key) DO UPDATE SET kind = EXCLUDED.kind, until_at = EXCLUDED.until_at, "
        "note = EXCLUDED.note, actor = EXCLUDED.actor, created_at = now()",
        (key, kind, hours, note, actor),
    )
    conn.commit()


def ack(cur, conn, key: str, actor: str, note: str | None = None) -> None:
    key = _check(key)
    note = (note or "").strip()[:300] or None
    _upsert(cur, conn, key, "ack", ACK_HOURS, note, actor)
    events.record("ack", key, f"Acknowledged{': ' + note if note else ''}", actor=actor, cur=cur, conn=conn)


def snooze(cur, conn, key: str, hours, actor: str) -> None:
    key = _check(key)
    try:
        hours = float(hours)
    except (TypeError, ValueError):
        raise BadKey("hours must be a number")
    if not 0 < hours <= MAX_SNOOZE_HOURS:
        raise BadKey(f"snooze between 0 and {MAX_SNOOZE_HOURS} hours")
    _upsert(cur, conn, key, "snooze", hours, None, actor)
    events.record("ack", key, f"Snoozed for {hours:g}h", actor=actor, cur=cur, conn=conn)


def remove(cur, conn, key: str, actor: str) -> bool:
    key = _check(key)
    cur.execute("DELETE FROM ops.acks WHERE item_key = %s", (key,))
    gone = cur.rowcount > 0
    conn.commit()
    if gone:
        events.record("ack", key, "Unmuted", actor=actor, cur=cur, conn=conn)
    return gone


def active(cur) -> dict:
    cur.execute("SELECT item_key, kind, until_at, note, actor, created_at FROM ops.acks WHERE until_at > now()")
    return {
        r["item_key"]: {"kind": r["kind"], "until": r["until_at"].isoformat(), "note": r["note"],
                        "actor": r["actor"], "at": r["created_at"].isoformat()}
        for r in cur.fetchall()
    }


def is_silenced(cur, key: str) -> bool:
    cur.execute("SELECT 1 FROM ops.acks WHERE item_key = %s AND until_at > now()", (key,))
    return cur.fetchone() is not None


def clear_on_recovery(cur, conn, key: str) -> None:
    """An acknowledgement is about one incident: once the item is healthy
    again it goes away, so the next failure alerts normally. Snoozes run
    their full time."""
    cur.execute("DELETE FROM ops.acks WHERE item_key = %s AND kind = 'ack'", (key,))
    if cur.rowcount:
        events.record("ack", key, "Acknowledgement cleared -- recovered", cur=cur, conn=conn)
    conn.commit()


def prune(cur, conn) -> None:
    cur.execute("DELETE FROM ops.acks WHERE until_at < now() - interval '1 day'")
    conn.commit()
