"""Dependencies between commitments. A commitment (e.g. Sales' promise to a
client) can be supported by others (e.g. Engineering's work, requested from
that team). Whenever a supporting commitment changes, evaluate() recomputes the
promise's risk_note and tells its owner when it becomes at risk:

  * a supporting date falls after the promise's date  -> "expects 4 Dec, 4 days after your date"
  * supporting work is overdue, declined, or has an open blocker
"""
from __future__ import annotations

from . import ledger

LIVE = ("requested", "detected", "open", "overdue")


def _open_blocker(cur, commitment_id):
    cur.execute("SELECT kind, text FROM corp.commitment_updates WHERE commitment_id = %s AND kind IN ('blocker', 'unblocked') "
                "ORDER BY id DESC LIMIT 1", (commitment_id,))
    row = cur.fetchone()
    return row["text"] if row and row["kind"] == "blocker" else None


def reasons_for(cur, parent) -> list:
    cur.execute("SELECT c.*, un.name AS unit_name FROM corp.commitments c LEFT JOIN corp.units un ON un.id = c.unit_id "
                "WHERE c.supports_id = %s AND c.status <> 'dropped'", (parent["id"],))
    out = []
    for ch in cur.fetchall():
        who = ch["unit_name"] or "Supporting work"
        if ch["status"] == "declined":
            out.append(f"{who} declined the request")
            continue
        if ch["status"] == "done":
            continue
        if ch["status"] == "overdue":
            out.append(f"{who}'s work is overdue")
        if parent["due_date"] and ch["due_date"] and ch["due_date"] > parent["due_date"]:
            days = (ch["due_date"] - parent["due_date"]).days
            out.append(f"{who} expects {ch['due_date']:%d %b}, {days} day{'s' if days != 1 else ''} after your date")
        blocker = _open_blocker(cur, ch["id"])
        if blocker:
            out.append(f"{who} is blocked: {blocker[:140]}")
    return out


def evaluate(cur, parent_id, push=None, actor_id=None) -> None:
    if not parent_id:
        return
    cur.execute("SELECT * FROM corp.commitments WHERE id = %s", (parent_id,))
    parent = cur.fetchone()
    if not parent or parent["status"] not in LIVE:
        return
    note = "; ".join(reasons_for(cur, parent))[:500] or None
    if note == parent["risk_note"]:
        return
    cur.execute("UPDATE corp.commitments SET risk_note = %s, updated_at = now() WHERE id = %s", (note, parent_id))
    ledger.event(cur, parent_id, actor_id, "at_risk" if note else "risk_cleared", **({"why": note} if note else {}))
    if note and push:
        for uid in {parent["owner_id"], parent["owed_to_id"]} - {None, actor_id}:
            push(uid, "Your promise may slip", f"“{parent['text'][:60]}”: {note[:140]}", {"type": "corp_risk", "org_id": parent["org_id"], "commitment_id": parent["id"]})


def evaluate_parent_of(cur, child_id, push=None, actor_id=None) -> None:
    cur.execute("SELECT supports_id FROM corp.commitments WHERE id = %s", (child_id,))
    row = cur.fetchone()
    if row and row["supports_id"]:
        evaluate(cur, row["supports_id"], push, actor_id)
