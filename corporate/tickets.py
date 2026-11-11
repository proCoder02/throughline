"""Tickets: small issues inside a project (PAY-12), and the trace of everything
said about them -- work chat, email, calls and meetings, commitments.

Who sees a ticket: members of its project, organisation admins, and anyone
assigned to or watching it.

Tagging (ticket_links), three ways:
  key     a key like PAY-12 typed in a work message or an email links it
  manual  someone tags a message, email, call or commitment to a ticket
  ai      after a call or meeting, likely tickets are *suggested*; someone
          who was in it confirms or rejects

Privacy: tagging never widens who can read the content itself. Everyone on
the ticket sees that something was linked (a generic label, who tagged it,
when) and a one-sentence summary; the content is shown only to people who
could already see it -- unless the tagger chose to share it.

Built so an outside tracker can slot in later (phase 2, Jira): a ticket
records its source and external key, and links, the timeline, the brief and
Ask work the same whatever the source."""
from __future__ import annotations

import json
import re
from datetime import date

from . import access, ai
from .errors import CorpError

KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,9})-(\d{1,6})\b")
PROJECT_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")
STATUSES = ("todo", "in_progress", "in_review", "blocked", "done", "cancelled")
OPEN = ("todo", "in_progress", "in_review", "blocked")
PRIORITIES = ("low", "medium", "high", "urgent")
SOURCES = ("message", "email", "meeting", "commitment")
SUGGEST_BATCH = 10      # meetings checked per run
SUMMARY_BATCH = 20      # link summaries written per run
TITLE_MAX, TEXT_MAX = 200, 5000


def _date(v):
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        raise CorpError(400, "Dates look like 2026-11-30.")


def visible() -> str:
    """SQL condition over alias x (corp.tickets); params %(me)s and %(admin)s."""
    return ("(%(admin)s OR x.project_id IN (SELECT project_id FROM corp.project_members WHERE user_id = %(me)s) "
            "OR EXISTS (SELECT 1 FROM corp.ticket_people tp WHERE tp.ticket_id = x.id AND tp.user_id = %(me)s))")


def keys_in(text: str) -> list:
    """Ticket keys written in text, e.g. 'see PAY-12' -> ['PAY-12'] (upper case only, as typed)."""
    return list(dict.fromkeys(f"{a}-{int(b)}" for a, b in KEY_RE.findall(text or "")))


# ------------------------------------------------------------------ projects
def set_project_key(cur, project_id: int, key: str) -> str:
    key = (key or "").strip().upper()
    if not PROJECT_KEY_RE.match(key):
        raise CorpError(400, "A ticket key is 2-10 capital letters or digits, starting with a letter (e.g. PAY).")
    cur.execute("SELECT org_id, ticket_key FROM corp.projects WHERE id = %s", (project_id,))
    p = cur.fetchone()
    cur.execute("SELECT count(*) AS n FROM corp.tickets WHERE project_id = %s", (project_id,))
    if p["ticket_key"] and p["ticket_key"] != key and cur.fetchone()["n"]:
        raise CorpError(400, "This project already has tickets, so its key can't change.")
    cur.execute("SELECT 1 FROM corp.projects WHERE org_id = %s AND upper(ticket_key) = %s AND id <> %s",
                (p["org_id"], key, project_id))
    if cur.fetchone():
        raise CorpError(400, f"Another project already uses {key}.")
    cur.execute("UPDATE corp.projects SET ticket_key = %s WHERE id = %s", (key, project_id))
    return key


# ------------------------------------------------------------------- tickets
def event(cur, ticket_id: int, actor_id, kind: str, **detail) -> None:
    cur.execute("INSERT INTO corp.ticket_events (ticket_id, actor_id, kind, detail) VALUES (%s,%s,%s,%s)",
                (ticket_id, actor_id, kind, json.dumps(detail, default=str)))
    cur.execute("UPDATE corp.tickets SET updated_at = now() WHERE id = %s", (ticket_id,))


def _is_member(cur, org_id: int, user_id: int) -> bool:
    cur.execute("SELECT 1 FROM corp.members WHERE org_id = %s AND user_id = %s AND status = 'active'", (org_id, user_id))
    return cur.fetchone() is not None


def create(cur, ctx, org_id: int, project_id: int, title: str, *, description="", priority="medium", due=None,
           assignees=(), watchers=(), supports=None) -> int:
    cur.execute("SELECT p.id, p.ticket_key, pm.role FROM corp.projects p LEFT JOIN corp.project_members pm "
                "ON pm.project_id = p.id AND pm.user_id = %s WHERE p.id = %s AND p.org_id = %s",
                (ctx.user_id, project_id, org_id))
    p = cur.fetchone()
    if not p or not (p["role"] or ctx.is_admin):
        raise CorpError(404, "Project not found.")
    if p["role"] == "stakeholder" and not ctx.is_admin:
        raise CorpError(403, "Stakeholders follow a project; people on its team create tickets.")
    supports = check_supports(cur, org_id, project_id, supports)
    if not p["ticket_key"]:
        raise CorpError(400, "This project has no ticket key yet. A project lead or admin sets it on the website.")
    title = (title or "").strip()[:TITLE_MAX]
    if not title:
        raise CorpError(400, "Give the ticket a title.")
    if priority not in PRIORITIES:
        raise CorpError(400, "Priority is low, medium, high or urgent.")
    cur.execute("UPDATE corp.projects SET ticket_seq = ticket_seq + 1 WHERE id = %s RETURNING ticket_seq", (project_id,))
    number = cur.fetchone()["ticket_seq"]
    key = f"{p['ticket_key']}-{number}"
    cur.execute("INSERT INTO corp.tickets (org_id, project_id, number, key, title, description, priority, due_date, created_by, supports_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (org_id, project_id, number, key, title, (description or "").strip()[:TEXT_MAX], priority, _date(due), ctx.user_id,
                 supports))
    tid = cur.fetchone()["id"]
    event(cur, tid, ctx.user_id, "created")
    for uid in assignees:
        set_person(cur, org_id, tid, int(uid), "assignee", ctx.user_id)
    for uid in watchers:
        set_person(cur, org_id, tid, int(uid), "watcher", ctx.user_id)
    return tid


def get(cur, ctx, org_id: int, ticket_id: int):
    cur.execute(f"SELECT x.* FROM corp.tickets x WHERE x.id = %(id)s AND x.org_id = %(org)s AND {visible()}",
                {"id": ticket_id, "org": org_id, "me": ctx.user_id, "admin": ctx.is_admin})
    row = cur.fetchone()
    if not row:
        raise CorpError(404, "Ticket not found.")
    return row


def can_edit(cur, ctx, row) -> bool:
    """Project members, admins and assignees work on a ticket; watchers only follow it."""
    if ctx.is_admin:
        return True
    cur.execute("SELECT 1 FROM corp.project_members WHERE project_id = %s AND user_id = %s AND role <> 'stakeholder' "
                "UNION SELECT 1 FROM corp.ticket_people WHERE ticket_id = %s AND user_id = %s AND role = 'assignee'",
                (row["project_id"], ctx.user_id, row["id"], ctx.user_id))
    return cur.fetchone() is not None


def set_person(cur, org_id, ticket_id, user_id, role, actor_id) -> bool:
    if role not in ("assignee", "watcher"):
        raise CorpError(400, "Role is assignee or watcher.")
    if not _is_member(cur, org_id, user_id):
        raise CorpError(400, "That person isn't in this organisation.")
    cur.execute("INSERT INTO corp.ticket_people VALUES (%s,%s,%s) ON CONFLICT DO NOTHING RETURNING user_id",
                (ticket_id, user_id, role))
    added = cur.fetchone() is not None
    if added:
        event(cur, ticket_id, actor_id, "assigned" if role == "assignee" else "watching", user_id=user_id)
    return added


def remove_person(cur, ticket_id, user_id, role, actor_id) -> None:
    cur.execute("DELETE FROM corp.ticket_people WHERE ticket_id = %s AND user_id = %s AND role = %s RETURNING user_id",
                (ticket_id, user_id, role))
    if cur.fetchone():
        event(cur, ticket_id, actor_id, "unassigned" if role == "assignee" else "unwatched", user_id=user_id)


def update(cur, row, data: dict, actor_id) -> list:
    """Changes title, description, status, priority or due date. Returns what changed."""
    changed = []
    if "status" in data:
        s = data["status"]
        if s not in STATUSES:
            raise CorpError(400, "Status is todo, in_progress, in_review, blocked, done or cancelled.")
        if s != row["status"]:
            closed = "now()" if s in ("done", "cancelled") else "NULL"
            cur.execute(f"UPDATE corp.tickets SET status = %s, closed_at = {closed} WHERE id = %s", (s, row["id"]))
            event(cur, row["id"], actor_id, "status", **{"from": row["status"], "to": s})
            changed.append("status")
    if "priority" in data and data["priority"] != row["priority"]:
        if data["priority"] not in PRIORITIES:
            raise CorpError(400, "Priority is low, medium, high or urgent.")
        cur.execute("UPDATE corp.tickets SET priority = %s WHERE id = %s", (data["priority"], row["id"]))
        event(cur, row["id"], actor_id, "priority", to=data["priority"])
        changed.append("priority")
    if "due_date" in data:
        due = _date(data["due_date"])
        if due != row["due_date"]:
            cur.execute("UPDATE corp.tickets SET due_date = %s WHERE id = %s", (due, row["id"]))
            event(cur, row["id"], actor_id, "due", to=due)
            changed.append("due_date")
    if "supports_id" in data:
        sup = check_supports(cur, row["org_id"], row["project_id"], data["supports_id"])
        if sup != row["supports_id"]:
            cur.execute("UPDATE corp.tickets SET supports_id = %s WHERE id = %s", (sup, row["id"]))
            event(cur, row["id"], actor_id, "supports", to=sup)
            changed.append("supports_id")
    if "title" in data:
        t = str(data["title"] or "").strip()[:TITLE_MAX]
        if not t:
            raise CorpError(400, "Give the ticket a title.")
        if t != row["title"]:
            cur.execute("UPDATE corp.tickets SET title = %s WHERE id = %s", (t, row["id"]))
            event(cur, row["id"], actor_id, "title")
            changed.append("title")
    if "description" in data:
        dsc = str(data["description"] or "").strip()[:TEXT_MAX]
        if dsc != row["description"]:
            cur.execute("UPDATE corp.tickets SET description = %s WHERE id = %s", (dsc, row["id"]))
            event(cur, row["id"], actor_id, "description")
            changed.append("description")
    return changed


def comment(cur, ticket_id, author_id, body: str) -> int:
    body = (body or "").strip()[:TEXT_MAX]
    if not body:
        raise CorpError(400, "Write something first.")
    cur.execute("INSERT INTO corp.ticket_comments (ticket_id, author_id, body) VALUES (%s,%s,%s) RETURNING id",
                (ticket_id, author_id, body))
    cid = cur.fetchone()["id"]
    cur.execute("UPDATE corp.tickets SET updated_at = now() WHERE id = %s", (ticket_id,))
    return cid


def people_of(cur, ticket_id) -> dict:
    cur.execute("SELECT tp.user_id, tp.role, u.username FROM corp.ticket_people tp JOIN users u ON u.id = tp.user_id "
                "WHERE tp.ticket_id = %s ORDER BY lower(u.username)", (ticket_id,))
    out = {"assignees": [], "watchers": []}
    for r in cur.fetchall():
        out["assignees" if r["role"] == "assignee" else "watchers"].append({"user_id": r["user_id"], "username": r["username"]})
    return out


def notify_ids(cur, ticket_id, exclude=None) -> set:
    cur.execute("SELECT DISTINCT user_id FROM corp.ticket_people WHERE ticket_id = %s", (ticket_id,))
    return {r["user_id"] for r in cur.fetchall()} - {exclude}


def row_view(r) -> dict:
    d = dict(r)
    for k in ("due_date", "created_at", "updated_at", "closed_at", "summary_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


def list_for(cur, ctx, org_id, *, project_id=None, scope="all", status="open", q=None, limit=200) -> list:
    p = {"org": org_id, "me": ctx.user_id, "admin": ctx.is_admin, "proj": project_id, "q": f"%{(q or '').strip()}%",
         "key": (q or "").strip().upper(), "limit": limit}
    where = [f"x.org_id = %(org)s", visible()]
    if project_id:
        where.append("x.project_id = %(proj)s")
    if scope == "mine":
        where.append("EXISTS (SELECT 1 FROM corp.ticket_people t WHERE t.ticket_id = x.id AND t.user_id = %(me)s AND t.role = 'assignee')")
    elif scope == "watching":
        where.append("EXISTS (SELECT 1 FROM corp.ticket_people t WHERE t.ticket_id = x.id AND t.user_id = %(me)s)")
    if status == "open":
        where.append("x.status IN ('todo','in_progress','in_review','blocked')")
    elif status in STATUSES:
        where.append("x.status = %(st)s")
        p["st"] = status
    if q and q.strip():
        where.append("(x.key = %(key)s OR x.title ILIKE %(q)s OR x.description ILIKE %(q)s)")
    cur.execute(
        "SELECT x.id, x.key, x.title, x.status, x.priority, x.due_date, x.updated_at, x.project_id, x.source, x.external_url, "
        "pj.name AS project_name, "
        "(SELECT string_agg(u.username, ', ' ORDER BY u.username) FROM corp.ticket_people t JOIN users u ON u.id = t.user_id "
        "  WHERE t.ticket_id = x.id AND t.role = 'assignee') AS assignees, "
        "(SELECT count(*) FROM corp.ticket_links l WHERE l.ticket_id = x.id AND l.status = 'linked') AS links "
        f"FROM corp.tickets x JOIN corp.projects pj ON pj.id = x.project_id WHERE {' AND '.join(where)} "
        "ORDER BY (x.status IN ('done','cancelled')), "
        "array_position(ARRAY['urgent','high','medium','low'], x.priority), x.due_date NULLS LAST, x.id DESC LIMIT %(limit)s", p)
    return [row_view(r) for r in cur.fetchall()]


# --------------------------------------------------- client deliverables
def check_supports(cur, org_id, project_id, cid):
    """A ticket can support a deliverable (a commitment) in the same project."""
    if not cid:
        return None
    cur.execute("SELECT id FROM corp.commitments WHERE id = %s AND org_id = %s AND project_id = %s AND status <> 'dropped'",
                (int(cid), org_id, project_id))
    if not cur.fetchone():
        raise CorpError(400, "Pick a deliverable from this ticket's project.")
    return int(cid)


def rollup(cur, commitment_ids) -> dict:
    """Ticket progress per deliverable: {id: {total, done, blocked, late, open, tickets: [...]}} (cancelled not counted)."""
    ids = [i for i in commitment_ids if i]
    if not ids:
        return {}
    cur.execute("SELECT id, key, title, status, due_date, supports_id FROM corp.tickets WHERE supports_id = ANY(%s) "
                "AND status <> 'cancelled' ORDER BY number", (ids,))
    today = date.today()
    out = {}
    for t in cur.fetchall():
        r = out.setdefault(t["supports_id"], {"total": 0, "done": 0, "blocked": 0, "late": 0, "tickets": []})
        r["total"] += 1
        late = t["status"] != "done" and t["due_date"] is not None and t["due_date"] < today
        r["done"] += t["status"] == "done"
        r["blocked"] += t["status"] == "blocked"
        r["late"] += late
        r["tickets"].append({"id": t["id"], "key": t["key"], "title": t["title"], "status": t["status"],
                             "due_date": t["due_date"].isoformat() if t["due_date"] else None, "late": late})
    for r in out.values():
        r["open"] = r["total"] - r["done"]
    return out


def risk_reasons(cur, parent) -> list:
    """Why tickets put a deliverable at risk (used by deps.reasons_for)."""
    cur.execute("SELECT key, status, due_date FROM corp.tickets WHERE supports_id = %s AND status NOT IN ('done','cancelled') "
                "ORDER BY number", (parent["id"],))
    today, out = date.today(), []
    for t in cur.fetchall():
        if t["status"] == "blocked":
            out.append(f"{t['key']} is blocked")
        elif t["due_date"] and t["due_date"] < today:
            out.append(f"{t['key']} is overdue")
        if parent["due_date"] and t["due_date"] and t["due_date"] > parent["due_date"]:
            days = (t["due_date"] - parent["due_date"]).days
            out.append(f"{t['key']} expects {t['due_date']:%d %b}, {days} day{'s' if days != 1 else ''} after your date")
    return out


# --------------------------------------------------------------------- links
def source(cur, viewer, org_id: int, kind: str, sid: int):
    """What a tagged item is, if `viewer` may see it (viewer=None: the system).
    {label (generic, safe to show anyone), text (the content), summary (an
    existing shareable summary, if any), open (where to open it)} or None."""
    me = viewer.user_id if viewer else 0
    p = {"org": org_id, "me": me, "id": sid}
    if kind == "message":
        cond = access.threads() if viewer else "TRUE"
        cur.execute("SELECT m.id, m.body, m.thread_id, x.kind AS thread_kind, un.name AS unit_name, us.username AS sender "
                    "FROM corp.messages m JOIN corp.threads x ON x.id = m.thread_id LEFT JOIN corp.units un ON un.id = x.unit_id "
                    f"LEFT JOIN users us ON us.id = m.sender_id WHERE m.id = %(id)s AND x.org_id = %(org)s AND {cond}", p)
        r = cur.fetchone()
        if not r:
            return None
        team = r["thread_kind"] == "team"
        return {"label": f"Team chat · {r['unit_name']}" if team else "Direct message",
                "text": f"{r['sender'] or 'Someone'}: {r['body']}", "summary": None,
                "open": {"type": "thread", "id": r["thread_id"]}}
    if kind == "email":
        mine = " AND user_id = %(me)s" if viewer else ""
        cur.execute(f"SELECT id, folder, subject, sender, excerpt FROM corp.mail_items WHERE id = %(id)s AND org_id = %(org)s{mine}", p)
        r = cur.fetchone()
        if not r:
            return None
        return {"label": "Email sent" if r["folder"] == "sent" else "Email received",
                "text": f"Subject: {r['subject']}\nFrom: {r['sender']}\n\n{r['excerpt']}", "summary": None, "open": None}
    if kind == "meeting":
        r = access.one(cur, "meetings", access.meetings(), sid, me, org_id) if viewer else _row(cur, "meetings", sid, org_id)
        if not r:
            return None
        when = r["created_at"].strftime("%d %b")
        team = r["unit_id"] is not None
        label = (f"Meeting · {r['title']}" if team else ("Call" if r["source"] == "call" else "Private meeting")) + f" · {when}"
        return {"label": label, "text": f"{r['title']}\n{r['summary'] or ''}\n\n{ai.clip(r['transcript'] or '', 3000)}",
                "summary": (r["summary"] or "")[:600] or None, "open": {"type": "meeting", "id": sid}}
    if kind == "commitment":
        r = access.one(cur, "commitments", access.commitments(), sid, me, org_id) if viewer else _row(cur, "commitments", sid, org_id)
        if not r:
            return None
        return {"label": "Commitment", "text": r["text"], "summary": r["text"][:300], "open": {"type": "commitment", "id": sid}}
    raise CorpError(400, "Tag a message, email, meeting or commitment.")


def _row(cur, table, sid, org_id):
    cur.execute(f"SELECT * FROM corp.{table} WHERE id = %s AND org_id = %s", (sid, org_id))
    return cur.fetchone()


def link(cur, viewer, org_id, ticket_id, kind, sid, how, *, tagger_id=None, shared=False, status="linked") -> dict:
    """Tags an item to a ticket. viewer=None for the system (keys, AI). Re-tagging a
    suggested or rejected item by hand links it; the AI never re-suggests a rejected one."""
    if kind not in SOURCES:
        raise CorpError(400, "Tag a message, email, meeting or commitment.")
    src = source(cur, viewer, org_id, kind, sid)
    if not src:
        raise CorpError(404, "You can only tag something you can see.")
    cur.execute("SELECT * FROM corp.ticket_links WHERE ticket_id = %s AND source_type = %s AND source_id = %s",
                (ticket_id, kind, sid))
    old = cur.fetchone()
    shared_text = src["text"][:4000] if shared else None
    if old:
        if status == "suggested" or (old["status"] == "linked" and not shared):
            return {"id": old["id"], "created": False}
        cur.execute("UPDATE corp.ticket_links SET status = 'linked', how = %s, tagged_by = COALESCE(%s, tagged_by), "
                    "shared = shared OR %s, shared_text = COALESCE(%s, shared_text) WHERE id = %s",
                    (how, tagger_id, shared, shared_text, old["id"]))
        if old["status"] != "linked":
            event(cur, ticket_id, tagger_id, "linked", type=kind, label=old["label"], how=how)
        return {"id": old["id"], "created": False}
    cur.execute("INSERT INTO corp.ticket_links (ticket_id, source_type, source_id, how, status, tagged_by, label, summary, "
                "shared, shared_text) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (ticket_id, kind, sid, how, status, tagger_id, src["label"][:200], src["summary"], shared, shared_text))
    lid = cur.fetchone()["id"]
    if status == "linked":
        event(cur, ticket_id, tagger_id, "linked", type=kind, label=src["label"], how=how)
    return {"id": lid, "created": True}


def link_by_keys(cur, org_id, author_id, kind, sid, text) -> list:
    """A key typed in a message or email links it. Returns the tickets linked."""
    found = []
    for key in keys_in(text):
        cur.execute("SELECT id FROM corp.tickets WHERE org_id = %s AND key = %s", (org_id, key))
        t = cur.fetchone()
        if t:
            out = link(cur, None, org_id, t["id"], kind, sid, "key", tagger_id=author_id)
            if out["created"]:
                found.append(t["id"])
    return found


def links_for_source(cur, ctx, org_id, kind, sid) -> list:
    """Tickets linked to (or suggested for) one item, as its viewer may see them."""
    if not source(cur, ctx, org_id, kind, sid):
        raise CorpError(404, "Not found.")
    cur.execute("SELECT l.id AS link_id, l.status, l.how, x.id, x.key, x.title, x.status AS ticket_status "
                "FROM corp.ticket_links l JOIN corp.tickets x ON x.id = l.ticket_id "
                f"WHERE l.source_type = %(kind)s AND l.source_id = %(sid)s AND l.status <> 'rejected' AND x.org_id = %(org)s "
                f"AND {visible()} ORDER BY l.status, x.key",
                {"kind": kind, "sid": sid, "org": org_id, "me": ctx.user_id, "admin": ctx.is_admin})
    return [dict(r) for r in cur.fetchall()]


def detail(cur, ctx, org_id, row) -> dict:
    tid = row["id"]
    cur.execute("SELECT name, ticket_key FROM corp.projects WHERE id = %s", (row["project_id"],))
    proj = cur.fetchone()
    cur.execute("SELECT l.*, u.username AS tagged_by_name FROM corp.ticket_links l LEFT JOIN users u ON u.id = l.tagged_by "
                "WHERE l.ticket_id = %s AND l.status IN ('linked', 'suggested') ORDER BY l.created_at", (tid,))
    links, suggestions = [], []
    for l in cur.fetchall():
        src = source(cur, ctx, org_id, l["source_type"], l["source_id"])
        item = {"id": l["id"], "type": l["source_type"], "how": l["how"], "label": l["label"],
                "tagged_by": l["tagged_by_name"], "tagged_by_id": l["tagged_by"], "at": l["created_at"].isoformat(),
                "summary": l["summary"], "shared": l["shared"],
                # The content only for those who could already see it, or when the tagger shared it.
                "content": (src["text"] if src else l["shared_text"]) if (src or l["shared"]) else None,
                "open": src["open"] if src else None}
        if l["status"] == "linked":
            links.append(item)
        elif src:  # a suggestion is shown only to people who can check it
            suggestions.append(item)
    cur.execute("SELECT e.kind, e.detail, e.at, u.username AS actor, w.username AS person FROM corp.ticket_events e "
                "LEFT JOIN users u ON u.id = e.actor_id LEFT JOIN users w ON w.id = (e.detail->>'user_id')::int "
                "WHERE e.ticket_id = %s AND e.kind <> 'linked' ORDER BY e.id", (tid,))  # links show as their own entries
    timeline = [{"type": "event", "kind": r["kind"], "detail": r["detail"], "actor": r["actor"], "person": r["person"],
                 "at": r["at"].isoformat()} for r in cur.fetchall()]
    cur.execute("SELECT c.id, c.body, c.created_at, c.author_id, u.username AS author FROM corp.ticket_comments c "
                "LEFT JOIN users u ON u.id = c.author_id WHERE c.ticket_id = %s ORDER BY c.id", (tid,))
    timeline += [{"type": "comment", "id": r["id"], "body": r["body"], "actor": r["author"], "actor_id": r["author_id"],
                  "at": r["created_at"].isoformat()} for r in cur.fetchall()]
    timeline += [{**lk, "type": "link", "source_type": lk["type"]} for lk in links]
    timeline.sort(key=lambda x: x["at"])
    supports = None
    if row["supports_id"]:
        cur.execute("SELECT id, text, due_date, status FROM corp.commitments WHERE id = %s", (row["supports_id"],))
        s = cur.fetchone()
        if s:
            supports = {"id": s["id"], "text": s["text"], "status": s["status"],
                        "due_date": s["due_date"].isoformat() if s["due_date"] else None}
    cur.execute("SELECT x.id, x.text, x.due_date FROM corp.commitments x WHERE x.project_id = %s AND x.supports_id IS NULL "
                "AND x.status NOT IN ('dropped','declined','done') ORDER BY x.due_date NULLS LAST", (row["project_id"],))
    deliverables = [{"id": r["id"], "text": r["text"], "due_date": r["due_date"].isoformat() if r["due_date"] else None}
                    for r in cur.fetchall()]
    return {"ticket": row_view(row) | {"project_name": proj["name"], "project_key": proj["ticket_key"]},
            "supports": supports, "deliverables": deliverables,
            "people": people_of(cur, tid), "links": links, "suggestions": suggestions, "timeline": timeline,
            "can_edit": can_edit(cur, ctx, row)}


# ----------------------------------------------------- background (jobs.py)
def write_link_summaries(cur, call_llm) -> int:
    """One-sentence summaries for tagged messages and emails (meetings and
    commitments already have a shareable summary)."""
    cur.execute("SELECT l.id, l.source_type, l.source_id, x.org_id FROM corp.ticket_links l JOIN corp.tickets x ON x.id = l.ticket_id "
                "WHERE l.summary IS NULL AND l.status IN ('linked','suggested') ORDER BY l.id LIMIT %s", (SUMMARY_BATCH,))
    done = 0
    for l in cur.fetchall():
        src = source(cur, None, l["org_id"], l["source_type"], l["source_id"])
        text = ai.link_summary(call_llm, l["source_type"], src["text"]) if src else ""
        cur.execute("UPDATE corp.ticket_links SET summary = %s WHERE id = %s", (text or "(no longer available)", l["id"]))
        done += 1
    return done


def _words(text) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 3}


def suggest_for_meetings(cur, call_llm, push=None) -> int:
    """After a call or meeting: which open tickets was it about? Suggested to
    the people who were in it, to confirm."""
    cur.execute("SELECT * FROM corp.meetings WHERE status = 'ready' AND tickets_scanned_at IS NULL "
                "AND created_at > now() - interval '3 days' ORDER BY id LIMIT %s", (SUGGEST_BATCH,))
    suggested = 0
    for m in cur.fetchall():
        cur.execute("UPDATE corp.meetings SET tickets_scanned_at = now() WHERE id = %s", (m["id"],))
        cur.execute("SELECT user_id FROM corp.meeting_participants WHERE meeting_id = %s", (m["id"],))
        people = {r["user_id"] for r in cur.fetchall()} | {m["created_by"]} - {None}
        if m["unit_id"]:
            people |= set(access.unit_member_ids(cur, m["unit_id"]))
        if not people:
            continue
        cur.execute("SELECT DISTINCT x.id, x.key, x.title, x.description FROM corp.tickets x "
                    "JOIN corp.project_members pm ON pm.project_id = x.project_id "
                    "WHERE x.org_id = %s AND x.status IN ('todo','in_progress','in_review','blocked') AND pm.user_id = ANY(%s) "
                    "ORDER BY x.id DESC LIMIT 200", (m["org_id"], list(people)))
        tickets = cur.fetchall()
        if not tickets:
            continue
        text = f"{m['title']}\n{m['summary'] or ''}\n{m['transcript'] or ''}"
        literal = {k for k in keys_in(text.upper())}
        words = _words(text)
        cands = [t for t in tickets if t["key"] in literal or len(_words(f"{t['title']} {t['description']}") & words) >= 2]
        if not cands:
            continue
        ids = {t["id"] for t in cands if t["key"] in literal}
        rest = [t for t in cands if t["id"] not in ids]
        if rest:
            ids |= set(ai.ticket_match(call_llm, text, rest)) & {t["id"] for t in rest}
        for tid in ids:
            out = link(cur, None, m["org_id"], tid, "meeting", m["id"], "ai", status="suggested")
            if out["created"]:
                suggested += 1
                if push:
                    key = next(t["key"] for t in cands if t["id"] == tid)
                    cur.execute("SELECT user_id FROM corp.meeting_participants WHERE meeting_id = %s", (m["id"],))
                    for uid in ({r["user_id"] for r in cur.fetchall()} | {m["created_by"]}) - {None}:
                        push(uid, f"Was this about {key}?", f"“{m['title'][:60]}” sounds related. Tap to confirm or dismiss.",
                             {"type": "corp_ticket", "org_id": m["org_id"], "ticket_id": tid})
    return suggested


def run_job(cur, call_llm, push=None) -> int:
    return suggest_for_meetings(cur, call_llm, push) + write_link_summaries(cur, call_llm)


def refresh_brief(cur, call_llm, row) -> str:
    """The ticket's 'what we know so far', from what every viewer may see:
    description, comments, status history, link summaries and shared content."""
    tid = row["id"]
    parts = [f"Status: {row['status']}, priority {row['priority']}" + (f", due {row['due_date']}" if row["due_date"] else ""),
             f"Description: {row['description'] or '(none)'}"]
    people = people_of(cur, tid)
    if people["assignees"]:
        parts.append("Assigned to: " + ", ".join(p["username"] for p in people["assignees"]))
    cur.execute("SELECT c.body, c.created_at, u.username FROM corp.ticket_comments c LEFT JOIN users u ON u.id = c.author_id "
                "WHERE c.ticket_id = %s ORDER BY c.id DESC LIMIT 30", (tid,))
    parts += [f"Comment by {r['username']} ({r['created_at']:%d %b}): {r['body'][:500]}" for r in cur.fetchall()][::-1]
    cur.execute("SELECT kind, detail, at FROM corp.ticket_events WHERE ticket_id = %s AND kind = 'status' ORDER BY id", (tid,))
    parts += [f"Status changed {r['at']:%d %b}: {r['detail'].get('from')} -> {r['detail'].get('to')}" for r in cur.fetchall()]
    cur.execute("SELECT label, summary, shared, shared_text, created_at FROM corp.ticket_links WHERE ticket_id = %s "
                "AND status = 'linked' ORDER BY id DESC LIMIT 30", (tid,))
    for r in cur.fetchall()[::-1]:
        body = r["shared_text"][:800] if r["shared"] and r["shared_text"] else (r["summary"] or "")
        if body:
            parts.append(f"{r['label']} ({r['created_at']:%d %b}): {body}")
    text = ai.ticket_brief(call_llm, f"{row['key']} {row['title']}", "\n".join(parts))
    cur.execute("UPDATE corp.tickets SET summary = %s, summary_at = now() WHERE id = %s", (text, tid))
    return text
