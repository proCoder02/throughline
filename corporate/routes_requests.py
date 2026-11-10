"""Cross-team requests, and updates/blockers on a commitment.

  POST /corp/orgs/<org>/requests                      {text, unit_id, due_date, supports_id?, project_id?, contact_id?}
  POST /corp/orgs/<org>/requests/<c>/accept           {owner_id?, due_date?}   (a manager of the receiving team)
  POST /corp/orgs/<org>/requests/<c>/decline          {reason}
  GET  /corp/orgs/<org>/requests/incoming             requests waiting for teams I manage
  GET  /corp/orgs/<org>/commitments/<c>/updates       updates + supporting work + what it supports
  POST /corp/orgs/<org>/commitments/<c>/updates       {kind: update|blocker|unblocked, text}

A request is a commitment in the receiving team, status 'requested', owed to
the requester -- so the existing visibility rules already let the requester
follow it, and Engineering's own chats and meetings stay private."""
from __future__ import annotations

from datetime import date

from flask import jsonify, request

from . import access, deps, ledger, orgs
from .errors import CorpError
from .roles import can_manage_people


def _date(v):
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        raise CorpError(400, "Dates look like 2026-11-30.")


def register(bp, *, db_cur, login_required, current_user_id, push=None, spawn=None):
    P = "/corp/orgs/<int:org_id>"

    def ctx_for(org_id):
        conn, cur = db_cur()
        return conn, cur, orgs.load_context(cur, org_id, current_user_id())

    def body():
        return request.get_json(silent=True) or {}

    def tell(uids, title, text):
        if push:
            for uid in set(uids) - {None}:
                (spawn or (lambda f: f()))(lambda uid=uid: push(uid, title, text, {"type": "corp_request"}))

    def visible(cur, ctx, org_id, cid):
        row = access.one(cur, "commitments", access.commitments(), cid, ctx.user_id, org_id)
        if not row:
            raise CorpError(404, "Commitment not found.")
        return row

    def managers(cur, unit_id):
        cur.execute("SELECT user_id FROM corp.unit_members WHERE unit_id = %s AND role = 'manager'", (unit_id,))
        return [r["user_id"] for r in cur.fetchall()]

    @bp.route(P + "/requests", methods=["POST"])
    @login_required
    def make_request(org_id):
        conn, cur, ctx = ctx_for(org_id)
        d = body()
        text = str(d.get("text") or "").strip()
        if not text:
            raise CorpError(400, "Say what you need.")
        try:
            unit_id = int(d.get("unit_id"))
        except (TypeError, ValueError):
            raise CorpError(400, "Pick the team you're asking.")
        if unit_id not in ctx.units:
            raise CorpError(404, "Team not found.")
        due = _date(d.get("due_date"))
        if not due:
            raise CorpError(400, "Say when you need it by.")
        supports = d.get("supports_id")
        if supports:
            parent = visible(cur, ctx, org_id, int(supports))
            if parent["status"] not in deps.LIVE:
                raise CorpError(400, "That commitment is already closed.")
        project_id = int(d["project_id"]) if d.get("project_id") else None
        contact_id = int(d["contact_id"]) if d.get("contact_id") else None
        cid = ledger.add_commitment(cur, org_id, unit_id, text, owed_to_id=ctx.user_id, due=due, status="requested",
                                    source_type="request", actor_id=ctx.user_id)
        cur.execute("UPDATE corp.commitments SET requested_by = %s, requested_due = %s, supports_id = %s, project_id = %s, "
                    "contact_id = %s WHERE id = %s", (ctx.user_id, due, int(supports) if supports else None, project_id,
                                                      contact_id, cid))
        conn.commit()
        cur.execute("SELECT username FROM users WHERE id = %s", (ctx.user_id,))
        who = cur.fetchone()["username"]
        tell(managers(cur, unit_id), f"Request for {ctx.units[unit_id]['name']}", f"{who}: {text[:90]} (by {due:%d %b})")
        return jsonify({"id": cid}), 201

    def receiving(cur, ctx, org_id, cid):
        row = visible(cur, ctx, org_id, cid)
        if row["status"] != "requested":
            raise CorpError(400, "This request has already been answered.")
        if not (ctx.is_admin or (row["unit_id"] and can_manage_people(ctx, row["unit_id"]))):
            raise CorpError(403, "Only a lead or head of the receiving team can answer this request.")
        return row

    @bp.route(P + "/requests/<int:cid>/accept", methods=["POST"])
    @login_required
    def accept(org_id, cid):
        conn, cur, ctx = ctx_for(org_id)
        row = receiving(cur, ctx, org_id, cid)
        d = body()
        owner = int(d.get("owner_id") or ctx.user_id)
        if not orgs.member_role(cur, org_id, owner):
            raise CorpError(400, "The owner must be in the organisation.")
        due = _date(d.get("due_date")) or row["requested_due"]
        cur.execute("UPDATE corp.commitments SET status = 'open', owner_id = %s, due_date = %s, updated_at = now() WHERE id = %s",
                    (owner, due, cid))
        later = due and row["requested_due"] and due > row["requested_due"]
        ledger.event(cur, cid, ctx.user_id, "accepted", owner_id=owner, due_date=due,
                     **({"asked_for": row["requested_due"]} if later else {}))
        deps.evaluate(cur, row["supports_id"], push, ctx.user_id)
        conn.commit()
        msg = f"Accepted, due {due:%d %b}" + (f" ({(due - row['requested_due']).days} days after you asked)" if later else "")
        tell([row["requested_by"]], f"{ctx.units[row['unit_id']]['name']} accepted your request", f"{row['text'][:80]}: {msg}")
        return jsonify({"ok": True, "later_than_asked": bool(later)})

    @bp.route(P + "/requests/<int:cid>/decline", methods=["POST"])
    @login_required
    def decline(org_id, cid):
        conn, cur, ctx = ctx_for(org_id)
        row = receiving(cur, ctx, org_id, cid)
        reason = str(body().get("reason") or "").strip()[:400]
        if not reason:
            raise CorpError(400, "Say why, so the requester can plan around it.")
        cur.execute("UPDATE corp.commitments SET status = 'declined', updated_at = now() WHERE id = %s", (cid,))
        ledger.event(cur, cid, ctx.user_id, "declined", reason=reason)
        deps.evaluate(cur, row["supports_id"], push, ctx.user_id)
        conn.commit()
        tell([row["requested_by"]], f"{ctx.units[row['unit_id']]['name']} declined your request", f"{row['text'][:80]}: {reason[:100]}")
        return jsonify({"ok": True})

    @bp.route(P + "/requests/incoming", methods=["GET"])
    @login_required
    def incoming(org_id):
        _, cur, ctx = ctx_for(org_id)
        mine = [u for u in ctx.units if ctx.is_admin or can_manage_people(ctx, u)]
        cur.execute("SELECT c.*, un.name AS unit_name, rq.username AS requested_by_name, p.name AS project_name, "
                    "par.text AS supports_text FROM corp.commitments c JOIN corp.units un ON un.id = c.unit_id "
                    "LEFT JOIN users rq ON rq.id = c.requested_by LEFT JOIN corp.projects p ON p.id = c.project_id "
                    "LEFT JOIN corp.commitments par ON par.id = c.supports_id "
                    "WHERE c.org_id = %s AND c.status = 'requested' AND c.unit_id = ANY(%s) ORDER BY c.requested_due",
                    (org_id, mine or [0]))
        return jsonify({"requests": [ledger.commitment_view(r) for r in cur.fetchall()]})

    @bp.route(P + "/commitments/<int:cid>/updates", methods=["GET"])
    @login_required
    def updates(org_id, cid):
        _, cur, ctx = ctx_for(org_id)
        row = visible(cur, ctx, org_id, cid)
        cur.execute("SELECT u.id, u.kind, u.text, u.created_at, us.username AS author FROM corp.commitment_updates u "
                    "LEFT JOIN users us ON us.id = u.author_id WHERE u.commitment_id = %s ORDER BY u.id DESC LIMIT 50", (cid,))
        ups = [dict(r) | {"created_at": r["created_at"].isoformat()} for r in cur.fetchall()]
        # Supporting work: shown as status, owner, date and its own updates -- the people involved see
        # it because each piece is owed to them; other teams' private chat and meetings stay private.
        cur.execute("SELECT c.id, c.text, c.status, c.due_date, c.requested_due, c.risk_note, un.name AS unit_name, "
                    "COALESCE(o.username, c.owner_name) AS owner FROM corp.commitments c LEFT JOIN corp.units un ON un.id = c.unit_id "
                    "LEFT JOIN users o ON o.id = c.owner_id WHERE c.supports_id = %s ORDER BY c.id", (cid,))
        kids = []
        for k in cur.fetchall():
            k = dict(k)
            k["blocker"] = deps._open_blocker(cur, k["id"])
            for f in ("due_date", "requested_due"):
                k[f] = k[f].isoformat() if k[f] else None
            kids.append(k)
        parent = None
        if row["supports_id"]:
            p = access.one(cur, "commitments", access.commitments(), row["supports_id"], ctx.user_id, org_id)
            parent = {"id": p["id"], "text": p["text"], "due_date": p["due_date"].isoformat() if p["due_date"] else None} if p else {"hidden": True}
        # One thread: what happened (events) and what people wrote (notes), oldest first.
        # Events that only mirror a note (update/blocker/unblocked) are left out -- the note is the record.
        cur.execute("SELECT e.kind, e.detail, e.at, us.username AS actor FROM corp.commitment_events e "
                    "LEFT JOIN users us ON us.id = e.actor_id WHERE e.commitment_id = %s "
                    "AND e.kind NOT IN ('update', 'blocker', 'unblocked') ORDER BY e.id", (cid,))
        thread = [{"type": "event", "kind": r["kind"], "detail": r["detail"], "actor": r["actor"], "at": r["at"].isoformat()}
                  for r in cur.fetchall()]
        thread += [{"type": "note", "kind": u["kind"], "text": u["text"], "actor": u["author"], "at": u["created_at"]} for u in ups]
        thread.sort(key=lambda x: x["at"])
        return jsonify({"updates": ups, "supporting": kids, "supports": parent, "thread": thread,
                        "closed": row["status"] in ("done", "dropped", "declined")})

    @bp.route(P + "/commitments/<int:cid>/updates", methods=["POST"])
    @login_required
    def post_update(org_id, cid):
        conn, cur, ctx = ctx_for(org_id)
        row = visible(cur, ctx, org_id, cid)
        d = body()
        kind = d.get("kind", "update")
        if kind not in ("update", "blocker", "unblocked"):
            raise CorpError(400, "kind must be update, blocker or unblocked.")
        text = str(d.get("text") or "").strip()[:1000]
        if not text:
            raise CorpError(400, "Write the update.")
        cur.execute("INSERT INTO corp.commitment_updates (commitment_id, author_id, kind, text) VALUES (%s,%s,%s,%s)",
                    (cid, ctx.user_id, kind, text))
        ledger.event(cur, cid, ctx.user_id, kind, text=text[:200])
        deps.evaluate(cur, row["supports_id"], push, ctx.user_id)
        conn.commit()
        if kind == "blocker":
            tell({row["owed_to_id"], row["requested_by"], row["owner_id"]} - {ctx.user_id}, "Blocked", f"{row['text'][:70]}: {text[:100]}")
        return jsonify(updates(org_id, cid).get_json()), 201
