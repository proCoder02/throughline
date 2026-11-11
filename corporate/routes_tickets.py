"""Ticket routes (corporate/tickets.py has the rules).

  GET/POST  /corp/orgs/<org>/tickets                     ?project_id= &scope=all|mine|watching &status=open|all|<s> &q=
  GET/PATCH /corp/orgs/<org>/tickets/<t>                 PATCH {status, priority, due_date, title, description}
  PUT/DELETE /corp/orgs/<org>/tickets/<t>/people/<u>     {role: assignee|watcher}
  POST      /corp/orgs/<org>/tickets/<t>/comments        {body}
  POST      /corp/orgs/<org>/tickets/<t>/links           {source_type, source_id, shared}
  POST      /corp/orgs/<org>/tickets/<t>/links/<l>/confirm | /reject
  DELETE    /corp/orgs/<org>/tickets/<t>/links/<l>
  POST      /corp/orgs/<org>/tickets/<t>/brief           write "what we know so far" again
  GET       /corp/orgs/<org>/ticket-links                ?source_type=&source_id=   tickets on one item
  PUT       /corp/orgs/<org>/projects/<p>/ticket-key     {key}  (project lead or admin -- website)
  GET       /corp/orgs/<org>/mail-items                  my recent work emails, to tag (website)"""
from __future__ import annotations

from flask import jsonify, request

from . import orgs, tickets
from .errors import CorpError


def register(bp, *, db_cur, login_required, current_user_id, call_llm, push=None, spawn=None,
             get_raw_connection=None, release_raw_connection=None, dict_cursor=None):
    P = "/corp/orgs/<int:org_id>"

    def ctx_for(org_id):
        conn, cur = db_cur()
        return conn, cur, orgs.load_context(cur, org_id, current_user_id())

    def body():
        return request.get_json(silent=True) or {}

    def run_later(fn):
        (spawn or (lambda f: f()))(fn)

    def tell(uids, title, text, org_id, ticket_id):
        if push:
            data = {"type": "corp_ticket", "org_id": org_id, "ticket_id": ticket_id}
            for uid in set(uids) - {None}:
                run_later(lambda uid=uid: push(uid, title, text, data))

    def summarise_soon():
        """Writes the one-line summaries of new links now, rather than at the next job run."""
        if not (get_raw_connection and dict_cursor):
            return

        def work():
            conn = get_raw_connection()
            try:
                cur = dict_cursor(conn)
                tickets.write_link_summaries(cur, call_llm)
                conn.commit()
            except Exception as e:  # noqa: BLE001 -- the job picks it up later
                conn.rollback()
                print(f"[corporate] link summaries: {e!r}")
            finally:
                release_raw_connection(conn)
        run_later(work)

    def ticket_for(cur, ctx, org_id, tid, edit=False):
        row = tickets.get(cur, ctx, org_id, tid)
        if edit and not tickets.can_edit(cur, ctx, row):
            raise CorpError(403, "Only people on the project, admins and assignees can change this ticket.")
        return row

    def detail(cur, ctx, org_id, tid):
        return tickets.detail(cur, ctx, org_id, tickets.get(cur, ctx, org_id, tid))

    @bp.route(P + "/tickets", methods=["GET"])
    @login_required
    def ticket_list(org_id):
        _, cur, ctx = ctx_for(org_id)
        a = request.args
        rows = tickets.list_for(cur, ctx, org_id, project_id=int(a["project_id"]) if a.get("project_id") else None,
                                scope=a.get("scope", "all"), status=a.get("status", "open"), q=a.get("q"))
        return jsonify({"tickets": rows})

    @bp.route(P + "/tickets", methods=["POST"])
    @login_required
    def ticket_create(org_id):
        conn, cur, ctx = ctx_for(org_id)
        d = body()
        tid = tickets.create(cur, ctx, org_id, int(d.get("project_id") or 0), d.get("title"),
                             description=d.get("description") or "", priority=d.get("priority") or "medium",
                             due=d.get("due_date"), assignees=d.get("assignees") or [], watchers=d.get("watchers") or [])
        conn.commit()
        row = tickets.get(cur, ctx, org_id, tid)
        tell(set(d.get("assignees") or []) - {ctx.user_id}, f"{row['key']} assigned to you", row["title"][:120], org_id, tid)
        return jsonify(detail(cur, ctx, org_id, tid)), 201

    @bp.route(P + "/tickets/<int:tid>", methods=["GET"])
    @login_required
    def ticket_get(org_id, tid):
        _, cur, ctx = ctx_for(org_id)
        return jsonify(detail(cur, ctx, org_id, tid))

    @bp.route(P + "/tickets/<int:tid>", methods=["PATCH"])
    @login_required
    def ticket_update(org_id, tid):
        conn, cur, ctx = ctx_for(org_id)
        row = ticket_for(cur, ctx, org_id, tid, edit=True)
        changed = tickets.update(cur, row, body(), ctx.user_id)
        conn.commit()
        if "status" in changed:
            new = tickets.get(cur, ctx, org_id, tid)
            tell(tickets.notify_ids(cur, tid, ctx.user_id), f"{row['key']}: {new['status'].replace('_', ' ')}",
                 row["title"][:120], org_id, tid)
        return jsonify(detail(cur, ctx, org_id, tid))

    @bp.route(P + "/tickets/<int:tid>/people/<int:user_id>", methods=["PUT", "DELETE"])
    @login_required
    def ticket_person(org_id, tid, user_id):
        conn, cur, ctx = ctx_for(org_id)
        role = (body().get("role") if request.method == "PUT" else request.args.get("role")) or "assignee"
        # Anyone who can see a ticket may watch or unwatch it themselves; assigning needs edit rights.
        row = ticket_for(cur, ctx, org_id, tid, edit=not (role == "watcher" and user_id == ctx.user_id))
        if request.method == "PUT":
            added = tickets.set_person(cur, org_id, tid, user_id, role, ctx.user_id)
            conn.commit()
            if added and role == "assignee" and user_id != ctx.user_id:
                tell([user_id], f"{row['key']} assigned to you", row["title"][:120], org_id, tid)
        else:
            tickets.remove_person(cur, tid, user_id, role, ctx.user_id)
            conn.commit()
        try:
            return jsonify(detail(cur, ctx, org_id, tid))
        except CorpError:  # unwatched a ticket they could only see as a watcher
            return jsonify({"removed": True, "visible": False})

    @bp.route(P + "/tickets/<int:tid>/comments", methods=["POST"])
    @login_required
    def ticket_comment(org_id, tid):
        conn, cur, ctx = ctx_for(org_id)
        row = ticket_for(cur, ctx, org_id, tid)
        text = str(body().get("body") or "")
        tickets.comment(cur, tid, ctx.user_id, text)
        # A key mentioned in a comment ("blocked by PAY-9") is just text; links are made from conversations.
        conn.commit()
        cur.execute("SELECT username FROM users WHERE id = %s", (ctx.user_id,))
        me = cur.fetchone()["username"]
        tell(tickets.notify_ids(cur, tid, ctx.user_id), f"{row['key']} · {me}", text.strip()[:140], org_id, tid)
        return jsonify(detail(cur, ctx, org_id, tid)), 201

    @bp.route(P + "/tickets/<int:tid>/links", methods=["POST"])
    @login_required
    def ticket_link(org_id, tid):
        conn, cur, ctx = ctx_for(org_id)
        ticket_for(cur, ctx, org_id, tid)  # anyone who can see the ticket may tag their own conversations to it
        d = body()
        kind = str(d.get("source_type") or "")
        tickets.link(cur, ctx, org_id, tid, kind, int(d.get("source_id") or 0), "manual",
                     tagger_id=ctx.user_id, shared=bool(d.get("shared")))
        conn.commit()
        summarise_soon()
        return jsonify(detail(cur, ctx, org_id, tid)), 201

    def own_link(cur, ctx, org_id, tid, lid):
        cur.execute("SELECT * FROM corp.ticket_links WHERE id = %s AND ticket_id = %s", (lid, tid))
        link = cur.fetchone()
        if not link:
            raise CorpError(404, "Link not found.")
        return link

    @bp.route(P + "/tickets/<int:tid>/links/<int:lid>/<action>", methods=["POST"])
    @login_required
    def ticket_suggestion(org_id, tid, lid, action):
        conn, cur, ctx = ctx_for(org_id)
        ticket_for(cur, ctx, org_id, tid)
        link = own_link(cur, ctx, org_id, tid, lid)
        if action not in ("confirm", "reject") or link["status"] != "suggested":
            raise CorpError(400, "Only a suggestion can be confirmed or dismissed.")
        # Only someone who was in the call or meeting can say what it was about.
        if not tickets.source(cur, ctx, org_id, link["source_type"], link["source_id"]):
            raise CorpError(403, "Only people who were in it can confirm this.")
        if action == "confirm":
            tickets.link(cur, ctx, org_id, tid, link["source_type"], link["source_id"], "ai", tagger_id=ctx.user_id)
        else:
            cur.execute("UPDATE corp.ticket_links SET status = 'rejected' WHERE id = %s", (lid,))
        conn.commit()
        return jsonify(detail(cur, ctx, org_id, tid))

    @bp.route(P + "/tickets/<int:tid>/links/<int:lid>", methods=["DELETE"])
    @login_required
    def ticket_unlink(org_id, tid, lid):
        conn, cur, ctx = ctx_for(org_id)
        row = ticket_for(cur, ctx, org_id, tid)
        link = own_link(cur, ctx, org_id, tid, lid)
        if link["tagged_by"] != ctx.user_id and not tickets.can_edit(cur, ctx, row):
            raise CorpError(403, "Only whoever tagged it, or people working on the ticket, can remove this.")
        cur.execute("DELETE FROM corp.ticket_links WHERE id = %s", (lid,))
        tickets.event(cur, tid, ctx.user_id, "unlinked", type=link["source_type"], label=link["label"])
        conn.commit()
        return jsonify(detail(cur, ctx, org_id, tid))

    @bp.route(P + "/tickets/<int:tid>/brief", methods=["POST"])
    @login_required
    def ticket_brief(org_id, tid):
        conn, cur, ctx = ctx_for(org_id)
        row = ticket_for(cur, ctx, org_id, tid)
        text = tickets.refresh_brief(cur, call_llm, row)
        conn.commit()
        return jsonify({"summary": text})

    @bp.route(P + "/ticket-links", methods=["GET"])
    @login_required
    def links_on_item(org_id):
        _, cur, ctx = ctx_for(org_id)
        return jsonify({"tickets": tickets.links_for_source(cur, ctx, org_id, request.args.get("source_type", ""),
                                                            int(request.args.get("source_id") or 0))})

    @bp.route(P + "/projects/<int:pid>/ticket-key", methods=["PUT"])
    @login_required
    def project_key(org_id, pid):
        conn, cur, ctx = ctx_for(org_id)
        cur.execute("SELECT p.id, pm.role FROM corp.projects p LEFT JOIN corp.project_members pm ON pm.project_id = p.id "
                    "AND pm.user_id = %s WHERE p.id = %s AND p.org_id = %s", (ctx.user_id, pid, org_id))
        p = cur.fetchone()
        if not p or not (p["role"] or ctx.is_admin):
            raise CorpError(404, "Project not found.")
        if not (p["role"] == "lead" or ctx.is_admin):
            raise CorpError(403, "Only a project lead or an admin can set the ticket key.")
        key = tickets.set_project_key(cur, pid, body().get("key"))
        conn.commit()
        return jsonify({"ticket_key": key})

    @bp.route(P + "/mail-items", methods=["GET"])
    @login_required
    def my_mail_items(org_id):
        _, cur, ctx = ctx_for(org_id)
        cur.execute("SELECT id, folder, subject, sender, left(excerpt, 200) AS excerpt, created_at FROM corp.mail_items "
                    "WHERE org_id = %s AND user_id = %s ORDER BY id DESC LIMIT 40", (org_id, ctx.user_id))
        return jsonify({"emails": [dict(r) | {"created_at": r["created_at"].isoformat()} for r in cur.fetchall()]})
