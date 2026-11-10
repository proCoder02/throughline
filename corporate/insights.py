"""What reads the ledger: the daily brief, Ask, and common ground. All of it
behind access.py, so the AI only sees what the asker may see."""
from __future__ import annotations

import json
from datetime import date, timedelta

from . import access, ai, ledger


def brief(cur, org_id: int, me: int) -> dict:
    cur.execute("SELECT x.*, un.name AS unit_name FROM corp.commitments x LEFT JOIN corp.units un ON un.id = x.unit_id "
                "WHERE x.org_id = %s AND x.status IN ('detected','open','overdue') AND (x.owner_id = %s OR x.owed_to_id = %s) "
                "ORDER BY x.due_date NULLS LAST", (org_id, me, me))
    rows = [ledger.commitment_view(r) for r in cur.fetchall()]
    my_units = _my_units(cur, me)
    cur.execute("SELECT r.id, r.why, c.id AS commitment_id, c.text AS commitment, c.due_date, c.owner_id, "
                "cu.name AS commitment_unit, bu.name AS blocker_unit, b.text AS blocker, b.unit_id AS blocker_unit_id "
                "FROM corp.risks r JOIN corp.commitments c ON c.id = r.commitment_id JOIN corp.blockers b ON b.id = r.blocker_id "
                "LEFT JOIN corp.units cu ON cu.id = c.unit_id LEFT JOIN corp.units bu ON bu.id = b.unit_id "
                "WHERE r.org_id = %(org)s AND r.resolved_at IS NULL AND c.status IN ('open','overdue','detected') AND "
                f"(c.owner_id = %(me)s OR c.owed_to_id = %(me)s OR b.unit_id IN {access.MY_UNITS})",
                {"org": org_id, "me": me})
    risks = []
    for r in cur.fetchall():
        mine = r["owner_id"] == me
        # Each side sees its own item and only the other side's team, owner and date.
        risks.append({
            "id": r["id"], "commitment_id": r["commitment_id"], "due_date": r["due_date"].isoformat() if r["due_date"] else None,
            "commitment": r["commitment"] if mine or r["blocker_unit_id"] not in my_units else None,
            "commitment_unit": r["commitment_unit"], "blocker_unit": r["blocker_unit"],
            "blocker": r["blocker"] if r["blocker_unit_id"] in my_units else None,
        })
    cur.execute("SELECT x.id, x.title, x.version, x.updated_at, un.name AS unit_name FROM corp.decisions x "
                f"LEFT JOIN corp.units un ON un.id = x.unit_id WHERE x.org_id = %(org)s AND {access.decisions()} "
                "AND x.updated_at > now() - interval '7 days' ORDER BY x.updated_at DESC LIMIT 10", {"org": org_id, "me": me})
    decisions = [dict(r) | {"updated_at": r["updated_at"].isoformat()} for r in cur.fetchall()]
    return {
        "owe": [r for r in rows if r["owner_id"] == me],
        "owed": [r for r in rows if r["owed_to_id"] == me and r["owner_id"] != me],
        "to_confirm": [r for r in rows if r["status"] == "detected"],
        "risks": risks,
        "decisions": decisions,
    }


def _my_units(cur, me) -> set:
    cur.execute("SELECT unit_id FROM corp.unit_members WHERE user_id = %s", (me,))
    return {r["unit_id"] for r in cur.fetchall()}


def catch_up(cur, call_llm, org_id: int, me: int, days: int = 7, project_id=None, contact_id=None) -> dict:
    """A brief of what happened that the viewer may see: meetings, decisions,
    commitments (opened, closed, at risk, blocked) -- for the last N days, or
    scoped to a project / one client contact (meeting prep)."""
    p = {"org": org_id, "me": me, "days": days, "proj": project_id, "contact": contact_id}
    scope_c = (" AND x.project_id = %(proj)s" if project_id else "") + (" AND x.contact_id = %(contact)s" if contact_id else "")
    since = "now() - (%(days)s || ' days')::interval"
    srcs, parts = [], []
    if not project_id:
        cur.execute(f"SELECT x.id, x.title, x.summary, x.created_at FROM corp.meetings x WHERE x.org_id = %(org)s "
                    f"AND x.status = 'ready' AND x.created_at > {since} AND {access.meetings()} ORDER BY x.created_at DESC LIMIT 8", p)
        for r in cur.fetchall():
            srcs.append({"ref": f"M{r['id']}", "type": "meeting", "id": r["id"], "title": r["title"]})
            parts.append(f"[M{r['id']}] Meeting {r['created_at']:%d %b} \"{r['title']}\": {r['summary'][:500]}")
        cur.execute(f"SELECT x.id, x.title, x.version, v.text FROM corp.decisions x JOIN corp.decision_versions v ON v.decision_id = x.id "
                    f"AND v.version = x.version WHERE x.org_id = %(org)s AND x.updated_at > {since} AND {access.decisions()} "
                    "ORDER BY x.updated_at DESC LIMIT 10", p)
        for r in cur.fetchall():
            srcs.append({"ref": f"D{r['id']}", "type": "decision", "id": r["id"], "title": r["title"]})
            parts.append(f"[D{r['id']}] Decision{' (changed, v' + str(r['version']) + ')' if r['version'] > 1 else ''}: {r['title']}: {r['text'][:300]}")
    recent = "" if (project_id or contact_id) else f" AND (x.updated_at > {since} OR x.status IN ('open','overdue','requested'))"
    cur.execute("SELECT x.id, x.text, x.status, x.due_date, x.risk_note, x.owner_id, x.owed_to_id, COALESCE(o.username, x.owner_name) AS owner, "
                "COALESCE(w.username, x.owed_to_name, ct.name) AS owed_to, un.name AS unit_name, "
                "(SELECT text FROM corp.commitment_updates u WHERE u.commitment_id = x.id AND u.kind = 'blocker' "
                " AND NOT EXISTS (SELECT 1 FROM corp.commitment_updates z WHERE z.commitment_id = x.id AND z.kind = 'unblocked' AND z.id > u.id) "
                " ORDER BY u.id DESC LIMIT 1) AS blocker "
                "FROM corp.commitments x LEFT JOIN users o ON o.id = x.owner_id LEFT JOIN users w ON w.id = x.owed_to_id "
                "LEFT JOIN corp.contacts ct ON ct.id = x.contact_id LEFT JOIN corp.units un ON un.id = x.unit_id "
                f"WHERE x.org_id = %(org)s AND x.status NOT IN ('dropped') AND {access.commitments()}{scope_c}{recent} "
                "ORDER BY (x.status IN ('overdue')) DESC, (x.risk_note IS NOT NULL) DESC, x.due_date NULLS LAST LIMIT 40", p)
    for r in cur.fetchall():
        srcs.append({"ref": f"C{r['id']}", "type": "commitment", "id": r["id"], "title": r["text"][:80]})
        mine = " (you owe this)" if r["owner_id"] == me else " (owed to you)" if r["owed_to_id"] == me else ""
        parts.append(f"[C{r['id']}] {r['status']}{mine}: {r['text']} -- {r['owner'] or '?'} -> {r['owed_to'] or '-'}"
                     f"{', due ' + format(r['due_date'], '%d %b') if r['due_date'] else ''}{', ' + r['unit_name'] if r['unit_name'] else ''}"
                     f"{'; AT RISK: ' + r['risk_note'] if r['risk_note'] else ''}{'; BLOCKED: ' + r['blocker'] if r['blocker'] else ''}")
    if not parts:
        return {"brief": "Nothing new in what you can see for this period.", "sources": []}
    cur.execute("SELECT username FROM users WHERE id = %s", (me,))
    who = cur.fetchone()["username"]
    focus = "the last %d days" % days
    if project_id:
        cur.execute("SELECT name, client FROM corp.projects WHERE id = %s", (project_id,))
        pr = cur.fetchone()
        focus = f"project {pr['name']}" + (f" for {pr['client']}" if pr and pr["client"] else "")
    if contact_id:
        cur.execute("SELECT name, position FROM corp.contacts WHERE id = %s", (contact_id,))
        c = cur.fetchone()
        focus = f"preparing for a meeting with {c['name']}{', ' + c['position'] if c and c['position'] else ''}: what was promised to them and its status"
    text = ai.catch_up(call_llm, who, focus, "\n".join(parts))
    used = [s for s in srcs if f"[{s['ref']}]" in text]
    return {"brief": text or "Couldn't write the brief just now.", "sources": used}


def ask(cur, call_llm, org_id: int, me: int, question: str, unit_id=None) -> dict:
    p = {"org": org_id, "me": me, "q": question, "unit": unit_id}
    uf = " AND x.unit_id = %(unit)s" if unit_id else ""
    srcs, parts = [], []
    cur.execute("SELECT x.id, x.title, x.summary, x.transcript, ts_rank(to_tsvector('english', "
                "x.title || ' ' || x.summary || ' ' || x.transcript), plainto_tsquery('english', %(q)s)) AS r "
                f"FROM corp.meetings x WHERE x.org_id = %(org)s AND x.status = 'ready' AND {access.meetings()}{uf} "
                "ORDER BY r DESC, x.created_at DESC LIMIT 4", p)
    for r in cur.fetchall():
        srcs.append({"ref": f"M{r['id']}", "type": "meeting", "id": r["id"], "title": r["title"]})
        parts.append(f"[M{r['id']}] Meeting \"{r['title']}\": {r['summary']}\n{ai.clip(r['transcript'], 1800)}")
    cur.execute("SELECT x.id, x.title, v.text, v.version, x.updated_at FROM corp.decisions x JOIN corp.decision_versions v "
                f"ON v.decision_id = x.id AND v.version = x.version WHERE x.org_id = %(org)s AND {access.decisions()}{uf} "
                "ORDER BY x.updated_at DESC LIMIT 25", p)
    for r in cur.fetchall():
        srcs.append({"ref": f"D{r['id']}", "type": "decision", "id": r["id"], "title": r["title"]})
        parts.append(f"[D{r['id']}] Decision (v{r['version']}, {r['updated_at']:%d %b}): {r['title']}: {r['text']}")
    cur.execute("SELECT x.id, x.text, x.status, x.due_date, COALESCE(o.username, x.owner_name) AS owner FROM corp.commitments x "
                f"LEFT JOIN users o ON o.id = x.owner_id WHERE x.org_id = %(org)s AND {access.commitments()}{uf} "
                "ORDER BY x.updated_at DESC LIMIT 30", p)
    for r in cur.fetchall():
        srcs.append({"ref": f"C{r['id']}", "type": "commitment", "id": r["id"], "title": r["text"][:80]})
        parts.append(f"[C{r['id']}] Commitment ({r['status']}, owner {r['owner'] or '?'}, due {r['due_date'] or '-'}): {r['text']}")
    cur.execute("SELECT m.id, m.thread_id, us.username, m.body, m.created_at FROM corp.messages m JOIN corp.threads x ON x.id = m.thread_id "
                f"LEFT JOIN users us ON us.id = m.sender_id WHERE x.org_id = %(org)s AND {access.threads()}"
                + (" AND x.unit_id = %(unit)s" if unit_id else "") +
                " AND to_tsvector('english', m.body) @@ plainto_tsquery('english', %(q)s) ORDER BY m.id DESC LIMIT 15", p)
    for r in cur.fetchall():
        srcs.append({"ref": f"T{r['id']}", "type": "message", "id": r["thread_id"], "title": r["body"][:80]})
        parts.append(f"[T{r['id']}] {r['username']} ({r['created_at']:%d %b}): {r['body'][:300]}")
    if not parts:
        return {"answer": "I don't have anything about that yet in the teams you're in.", "sources": []}
    text = ai.answer(call_llm, question, "\n".join(parts))
    used = [s for s in srcs if f"[{s['ref']}]" in text]
    return {"answer": text or "I couldn't answer that just now. Please try again.", "sources": used}


def common_ground(cur, call_llm, org_id: int, me: int, *, thread=None, meeting=None) -> dict:
    """From a team/direct thread (last messages) or a meeting transcript.
    Work notes are used only when every participant has chosen to share."""
    if thread:
        cur.execute("SELECT m.sender_id, us.username, m.body FROM corp.messages m JOIN users us ON us.id = m.sender_id "
                    "WHERE m.thread_id = %s AND m.kind = 'text' ORDER BY m.id DESC LIMIT 60", (thread["id"],))
        rows = cur.fetchall()[::-1]
        discussion = "\n".join(f"{r['username']}: {r['body']}" for r in rows)
        participants = sorted({r["sender_id"] for r in rows})
    else:
        discussion = meeting["transcript"]
        cur.execute("SELECT user_id FROM corp.meeting_participants WHERE meeting_id = %s", (meeting["id"],))
        participants = [r["user_id"] for r in cur.fetchall()]
    if len(discussion.strip()) < 40:
        return {"error": "There isn't enough discussion yet to find common ground."}
    notes, used = [], False
    if participants:
        cur.execute("SELECT n.user_id, n.note, n.sharing, us.username FROM corp.work_notes n JOIN users us ON us.id = n.user_id "
                    "WHERE n.org_id = %s AND n.user_id = ANY(%s)", (org_id, participants))
        rows = {r["user_id"]: r for r in cur.fetchall()}
        if all(uid in rows and rows[uid]["sharing"] and rows[uid]["note"].strip() for uid in participants):
            notes = [f"{rows[u]['username']}: {rows[u]['note'][:300]}" for u in participants]
            used = True
    result = ai.common_ground(call_llm, discussion, notes)
    if not result:
        return {"error": "Couldn't work it out just now. Please try again."}
    cur.execute("INSERT INTO corp.common_ground (org_id, thread_id, meeting_id, result, used_notes, created_by) "
                "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                (org_id, thread["id"] if thread else None, meeting["id"] if meeting else None, json.dumps(result), used, me))
    return {"id": cur.fetchone()["id"], "result": result, "used_notes": used}
