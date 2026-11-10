"""Commitments, decisions and blockers: storing what extraction found, and
every change to a commitment as history (commitment_events)."""
from __future__ import annotations

import json
from datetime import date

from .errors import CorpError

STATUSES = ("detected", "open", "overdue", "done", "dropped")


def org_people(cur, org_id: int) -> dict:
    """lower(username) -> user_id, for matching spoken names to members."""
    cur.execute("SELECT u.id, u.username FROM corp.members m JOIN users u ON u.id = m.user_id WHERE m.org_id = %s",
                (org_id,))
    return {r["username"].lower(): r["id"] for r in cur.fetchall()}


def match_person(people: dict, name):
    if not name:
        return None
    n = str(name).strip().lower()
    if n in people:
        return people[n]
    first = n.split()[0] if n.split() else ""
    hits = [uid for uname, uid in people.items() if first and (uname.split()[0] == first or uname.startswith(first))]
    return hits[0] if len(hits) == 1 else None


def _due(value):
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def event(cur, commitment_id: int, actor_id, kind: str, **detail) -> None:
    cur.execute("INSERT INTO corp.commitment_events (commitment_id, actor_id, kind, detail) VALUES (%s, %s, %s, %s)",
                (commitment_id, actor_id, kind, json.dumps(detail, default=str)))


def add_commitment(cur, org_id, unit_id, text, *, owner_id=None, owner_name=None, owed_to_id=None, owed_to_name=None,
                   due=None, status="detected", source_type="manual", source_id=None, quote=None, actor_id=None) -> int:
    cur.execute(
        "INSERT INTO corp.commitments (org_id, unit_id, text, owner_id, owner_name, owed_to_id, owed_to_name, due_date, "
        "status, source_type, source_id, quote, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (org_id, unit_id, text[:500], owner_id, owner_name, owed_to_id, owed_to_name, _due(due), status, source_type,
         source_id, (quote or "")[:400] or None, actor_id))
    cid = cur.fetchone()["id"]
    event(cur, cid, actor_id, "created", status=status, source=source_type)
    return cid


def add_decision(cur, org_id, unit_id, meeting_id, title, text, *, made_by=None, quote=None, source_type="manual",
                 source_id=None, actor_id=None, updates_id=None) -> int:
    """A new decision, or a new version of an existing one (history kept)."""
    if updates_id:
        cur.execute("SELECT id, version FROM corp.decisions WHERE id = %s AND org_id = %s AND unit_id IS NOT DISTINCT FROM %s",
                    (updates_id, org_id, unit_id))
        row = cur.fetchone()
        if row:
            v = row["version"] + 1
            cur.execute("UPDATE corp.decisions SET version = %s, updated_at = now() WHERE id = %s", (v, row["id"]))
            cur.execute("INSERT INTO corp.decision_versions (decision_id, version, text, quote, made_by, source_type, "
                        "source_id, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                        (row["id"], v, text[:1000], quote, made_by, source_type, source_id, actor_id))
            return row["id"]
    cur.execute("INSERT INTO corp.decisions (org_id, unit_id, meeting_id, title) VALUES (%s,%s,%s,%s) RETURNING id",
                (org_id, unit_id, meeting_id, (title or text)[:120]))
    did = cur.fetchone()["id"]
    cur.execute("INSERT INTO corp.decision_versions (decision_id, version, text, quote, made_by, source_type, source_id, "
                "created_by) VALUES (%s,1,%s,%s,%s,%s,%s,%s)", (did, text[:1000], quote, made_by, source_type, source_id, actor_id))
    return did


def current_decisions(cur, org_id, unit_id) -> list:
    cur.execute("SELECT d.id, d.title, v.text FROM corp.decisions d JOIN corp.decision_versions v "
                "ON v.decision_id = d.id AND v.version = d.version WHERE d.org_id = %s AND d.unit_id IS NOT DISTINCT FROM %s "
                "ORDER BY d.updated_at DESC LIMIT 30", (org_id, unit_id))
    return [dict(r) for r in cur.fetchall()]


def ingest(cur, org_id, unit_id, source_type, source_id, result, actor_id=None, meeting_id=None) -> dict:
    """Stores extraction output. Commitments start as 'detected' until the
    owner (or the person who recorded it) confirms them."""
    people = org_people(cur, org_id)
    valid_ids = {d["id"] for d in current_decisions(cur, org_id, unit_id)}
    counts = {"decisions": 0, "commitments": 0, "blockers": 0}
    for d in result.get("decisions", [])[:20]:
        if not isinstance(d, dict) or not (d.get("text") or d.get("title")):
            continue
        upd = d.get("updates_id") if d.get("updates_id") in valid_ids else None
        add_decision(cur, org_id, unit_id, meeting_id, str(d.get("title") or ""), str(d.get("text") or d.get("title")),
                     made_by=d.get("made_by"), quote=(d.get("quote") or "")[:400] or None, source_type=source_type,
                     source_id=source_id, actor_id=actor_id, updates_id=upd)
        counts["decisions"] += 1
    for c in result.get("commitments", [])[:30]:
        if not isinstance(c, dict) or not c.get("text"):
            continue
        add_commitment(cur, org_id, unit_id, str(c["text"]), owner_id=match_person(people, c.get("owner")),
                       owner_name=c.get("owner"), owed_to_id=match_person(people, c.get("owed_to")),
                       owed_to_name=c.get("owed_to"), due=c.get("due"), source_type=source_type, source_id=source_id,
                       quote=c.get("quote"), actor_id=actor_id)
        counts["commitments"] += 1
    for b in result.get("blockers", [])[:10]:
        if isinstance(b, dict) and b.get("text"):
            cur.execute("INSERT INTO corp.blockers (org_id, unit_id, text, source_type, source_id) VALUES (%s,%s,%s,%s,%s)",
                        (org_id, unit_id, str(b["text"])[:400], source_type, source_id))
            counts["blockers"] += 1
    return counts


def commitment_view(r) -> dict:
    d = dict(r)
    for k in ("due_date", "created_at", "updated_at", "checked_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


def can_change(ctx, row) -> bool:
    """Owner, the person owed, whoever recorded it, an org admin, or a
    manager of (or above) its unit."""
    from .roles import can_manage_people
    me = ctx.user_id
    return (me in (row["owner_id"], row["owed_to_id"], row["created_by"]) or ctx.is_admin
            or (row["unit_id"] is not None and can_manage_people(ctx, row["unit_id"])))


def apply_action(cur, ctx, row, action: str, data: dict) -> None:
    cid, me = row["id"], ctx.user_id
    if action == "confirm":
        if row["status"] != "detected":
            raise CorpError(400, "Only a detected commitment can be confirmed.")
        status = "overdue" if row["due_date"] and row["due_date"] < date.today() else "open"
        cur.execute("UPDATE corp.commitments SET status = %s, updated_at = now() WHERE id = %s", (status, cid))
    elif action == "done":
        note = str(data.get("note") or "").strip()[:400] or "Marked done"
        cur.execute("UPDATE corp.commitments SET status = 'done', evidence = %s, updated_at = now() WHERE id = %s", (note, cid))
    elif action == "drop":
        reason = str(data.get("reason") or "").strip()[:400]
        if not reason:
            raise CorpError(400, "Say why it's dropped.")
        cur.execute("UPDATE corp.commitments SET status = 'dropped', updated_at = now() WHERE id = %s", (cid,))
        data = {"reason": reason}
    elif action == "reopen":
        cur.execute("UPDATE corp.commitments SET status = 'open', evidence = NULL, updated_at = now() WHERE id = %s", (cid,))
    elif action == "edit":
        sets, vals = [], []
        if "due_date" in data:
            sets.append("due_date = %s"); vals.append(_due(data["due_date"]))
        if "owner_id" in data:
            sets.append("owner_id = %s"); vals.append(data["owner_id"])
        if "text" in data and str(data["text"]).strip():
            sets.append("text = %s"); vals.append(str(data["text"]).strip()[:500])
        if not sets:
            raise CorpError(400, "Nothing to change.")
        cur.execute(f"UPDATE corp.commitments SET {', '.join(sets)}, updated_at = now() WHERE id = %s", (*vals, cid))
        data = {k: data[k] for k in ("due_date", "owner_id", "text") if k in data}
    else:
        raise CorpError(400, "Unknown action.")
    event(cur, cid, me, action, **{k: v for k, v in data.items() if k in ("note", "reason", "due_date", "owner_id", "text")})
