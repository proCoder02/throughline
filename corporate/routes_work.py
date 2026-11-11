"""HTTP API for work memory, added to the Corporate blueprint by register().
All under /corp/orgs/<org>/ and all behind access.py.

  threads:      GET threads · POST threads/direct {user_id} · GET threads/<t>/messages?after=
                POST threads/<t>/messages {body} · POST threads/<t>/read {upto} · POST threads/<t>/common-ground
  meetings:     GET meetings?unit_id= · POST meetings (file | {text} | {conversation_id}) · GET/DELETE meetings/<m>
                POST meetings/<m>/common-ground · GET importable-conversations
  commitments:  GET commitments?scope=mine|owed|team&unit_id= · POST commitments · GET commitments/<c>
                POST commitments/<c>/<confirm|done|drop|reopen|edit>
  decisions:    GET decisions?unit_id= · POST decisions · GET decisions/<d> · POST decisions/<d>/versions
                POST common-ground/<g>/decision {text}
  ask, brief:   POST ask {question, unit_id?} · GET brief
  work note:    GET/PUT work-note {note, sharing}"""
from __future__ import annotations

import time

from flask import jsonify, request

from . import access, chat, deps, insights, ledger, meetings, orgs, tickets
from .errors import CorpError
from .roles import can_manage_people

MAX_AUDIO = 60 * 1024 * 1024


def register(bp, *, db_cur, login_required, current_user_id, call_llm, transcribe=None, push=None, spawn=None,
             get_raw_connection=None, release_raw_connection=None, dict_cursor=None, live=None, mail_linked=None):
    """live(user_id, event): the host's real-time socket push (open browser tabs)."""
    P = "/corp/orgs/<int:org_id>"

    def ctx_for(org_id):
        conn, cur = db_cur()
        return conn, cur, orgs.load_context(cur, org_id, current_user_id())

    def body():
        return request.get_json(silent=True) or {}

    def in_unit(ctx, unit_id):
        """Writing team content needs membership of the team."""
        if unit_id is None:
            return None
        unit_id = int(unit_id)
        if ctx.my_units.get(unit_id) is None:
            raise CorpError(403, "Only members of that team can add to it.")
        return unit_id

    def background(fn, *args, **kw):
        def run():
            conn = get_raw_connection()
            try:
                fn(conn, dict_cursor(conn), *args, **kw)
            finally:
                release_raw_connection(conn)
        (spawn or (lambda f: __import__("threading").Thread(target=f, daemon=True).start()))(run)

    def notify(user_ids, title, text, data):
        if push:
            for uid in set(user_ids):
                (spawn or (lambda f: f()))(lambda uid=uid: push(uid, title, text, data))

    # ------------------------------------------------------------- chat
    @bp.route("/corp/presence", methods=["POST"])
    @login_required
    def presence():
        """An open, visible, recently used web tab says "I'm here" about once a
        minute; while that holds, phone pushes for work messages are skipped."""
        conn, cur = db_cur()
        cur.execute("INSERT INTO corp.presence (user_id, active_until) VALUES (%s, now() + interval '2 minutes') "
                    "ON CONFLICT (user_id) DO UPDATE SET active_until = EXCLUDED.active_until", (current_user_id(),))
        conn.commit()
        return jsonify({"ok": True})

    @bp.route("/corp/unread", methods=["GET"])
    @login_required
    def unread_total():
        """Unread work messages across all my organisations (for badges anywhere in the app)."""
        _, cur = db_cur()
        cur.execute("SELECT x.org_id, count(m.id) AS n FROM corp.threads x JOIN corp.messages m ON m.thread_id = x.id "
                    "LEFT JOIN corp.thread_members r ON r.thread_id = x.id AND r.user_id = %(me)s "
                    f"WHERE {access.threads()} AND m.sender_id IS DISTINCT FROM %(me)s AND m.id > COALESCE(r.last_read_id, 0) "
                    "GROUP BY x.org_id", {"me": current_user_id()})
        by_org = {r["org_id"]: r["n"] for r in cur.fetchall()}
        # The newest unread message: lets the browser alert even when its live socket is down.
        cur.execute("SELECT m.id AS message_id, x.id AS thread_id, x.org_id, x.kind = 'team' AS team, "
                    "COALESCE(un.name, us.username) AS title, us.username AS sender, left(m.body, 140) AS preview "
                    "FROM corp.threads x JOIN corp.messages m ON m.thread_id = x.id JOIN users us ON us.id = m.sender_id "
                    "LEFT JOIN corp.units un ON un.id = x.unit_id "
                    "LEFT JOIN corp.thread_members r ON r.thread_id = x.id AND r.user_id = %(me)s "
                    f"WHERE {access.threads()} AND m.sender_id <> %(me)s AND m.id > COALESCE(r.last_read_id, 0) "
                    "ORDER BY m.id DESC LIMIT 1", {"me": current_user_id()})
        latest = cur.fetchone()
        return jsonify({"total": sum(by_org.values()), "by_org": by_org, "latest": dict(latest) if latest else None})

    @bp.route(P + "/threads", methods=["GET"])
    @login_required
    def threads(org_id):
        conn, cur, ctx = ctx_for(org_id)
        out = chat.list_threads(cur, org_id, ctx.user_id)
        conn.commit()
        return jsonify({"threads": out})

    @bp.route(P + "/threads/direct", methods=["POST"])
    @login_required
    def direct(org_id):
        conn, cur, ctx = ctx_for(org_id)
        other = int(body().get("user_id") or 0)
        if not orgs.member_role(cur, org_id, other):
            raise CorpError(404, "That person isn't in this organisation.")
        tid = chat.direct_thread(cur, org_id, ctx.user_id, other)
        conn.commit()
        return jsonify({"thread_id": tid})

    @bp.route(P + "/threads/<int:tid>/messages", methods=["GET"])
    @login_required
    def thread_messages(org_id, tid):
        _, cur, ctx = ctx_for(org_id)
        t = chat.get_thread(cur, org_id, tid, ctx.user_id)
        return jsonify({"thread": {"id": t["id"], "kind": t["kind"], "unit_id": t["unit_id"]},
                        "messages": chat.messages(cur, tid, int(request.args.get("after") or 0))})

    @bp.route(P + "/threads/<int:tid>/messages", methods=["POST"])
    @login_required
    def send(org_id, tid):
        conn, cur, ctx = ctx_for(org_id)
        t = chat.get_thread(cur, org_id, tid, ctx.user_id)
        text = str(body().get("body") or "")
        mid = chat.post(cur, tid, ctx.user_id, text)
        tickets.link_by_keys(cur, org_id, ctx.user_id, "message", mid, text)  # "PAY-12" in a message tags it
        conn.commit()
        cur.execute("SELECT username FROM users WHERE id = %s", (ctx.user_id,))
        me = cur.fetchone()["username"]
        where = ctx.units[t["unit_id"]]["name"] if t["kind"] == "team" else me
        others = [u for u in chat.recipients(cur, t) if u != ctx.user_id]
        # Phone push only for people not active on the website right now (they get the live alert there).
        cur.execute("SELECT user_id FROM corp.presence WHERE user_id = ANY(%s) AND active_until > now()", (others or [0],))
        on_web = {r["user_id"] for r in cur.fetchall()}
        notify([u for u in others if u not in on_web], where, (f"{me}: " if t["kind"] == "team" else "") + text[:120],
               {"type": "corp_message", "org_id": str(org_id), "thread_id": str(tid), "title": where,
                "team": "1" if t["kind"] == "team" else "0"})
        if live:  # open tabs get it instantly: badge, toast, desktop notification
            event = {"type": "corp_message", "org_id": org_id, "thread_id": tid, "message_id": mid, "team": t["kind"] == "team",
                     "title": where, "sender": me, "preview": text[:140], "at": int(time.time())}
            for uid in others:
                (spawn or (lambda f: f()))(lambda uid=uid: live(uid, event))
        return jsonify({"messages": chat.messages(cur, tid, mid - 1)}), 201

    @bp.route(P + "/threads/<int:tid>/read", methods=["POST"])
    @login_required
    def read(org_id, tid):
        conn, cur, ctx = ctx_for(org_id)
        chat.get_thread(cur, org_id, tid, ctx.user_id)
        chat.mark_read(cur, tid, ctx.user_id, int(body().get("upto") or 0))
        conn.commit()
        return jsonify({"ok": True})

    @bp.route(P + "/threads/<int:tid>/common-ground", methods=["POST"])
    @login_required
    def thread_common_ground(org_id, tid):
        conn, cur, ctx = ctx_for(org_id)
        t = chat.get_thread(cur, org_id, tid, ctx.user_id)
        out = insights.common_ground(cur, call_llm, org_id, ctx.user_id, thread=t)
        if "error" in out:
            raise CorpError(400, out["error"])
        chat.post(cur, tid, ctx.user_id, "Common ground", kind="common_ground", meta=out)
        conn.commit()
        return jsonify(out)

    # ---------------------------------------------------------- meetings
    @bp.route(P + "/meetings", methods=["GET"])
    @login_required
    def meeting_list(org_id):
        _, cur, ctx = ctx_for(org_id)
        unit = request.args.get("unit_id")
        return jsonify({"meetings": meetings.list_visible(cur, org_id, ctx.user_id, int(unit) if unit else None)})

    @bp.route(P + "/importable-conversations", methods=["GET"])
    @login_required
    def importable(org_id):
        _, cur, ctx = ctx_for(org_id)
        cur.execute("SELECT id, title, category, created_at FROM conversations WHERE user_id = %s AND raw_transcript <> '' "
                    "ORDER BY (category = 'office') DESC, created_at DESC LIMIT 50", (ctx.user_id,))
        return jsonify({"conversations": [dict(r) | {"created_at": r["created_at"].isoformat()} for r in cur.fetchall()]})

    @bp.route(P + "/meetings", methods=["POST"])
    @login_required
    def meeting_create(org_id):
        conn, cur, ctx = ctx_for(org_id)
        form = request.form if request.files else body()
        unit_id = in_unit(ctx, form.get("unit_id") or None)
        title = str(form.get("title") or "").strip() or "Meeting"
        raw_parts = form.get("participants") or []
        parts = [int(p) for p in (raw_parts.split(",") if isinstance(raw_parts, str) else raw_parts) if str(p).strip()]
        for p in parts:
            if not orgs.member_role(cur, org_id, p):
                raise CorpError(400, "Participants must be in the organisation.")
        audio = None
        if "file" in request.files:
            f = request.files["file"]
            audio = f.read(MAX_AUDIO + 1)
            if len(audio) > MAX_AUDIO:
                raise CorpError(413, "Recordings up to 60 MB.")
            if not transcribe:
                raise CorpError(503, "Transcription isn't available on this server.")
            source, text, ctype = ("record" if form.get("recorded") else "upload"), "", f.mimetype or "audio/webm"
        elif form.get("conversation_id"):
            cur.execute("SELECT title, raw_transcript FROM conversations WHERE id = %s AND user_id = %s",
                        (int(form["conversation_id"]), ctx.user_id))
            conv = cur.fetchone()
            if not conv:
                raise CorpError(404, "Conversation not found.")
            source, text, ctype = "import", conv["raw_transcript"], None
            if title == "Meeting" and conv["title"]:
                title = conv["title"]
        else:
            source, text, ctype = "text", str(form.get("text") or "").strip(), None
            if len(text) < 20:
                raise CorpError(400, "Paste the meeting notes or transcript.")
        mid = meetings.create(cur, org_id, unit_id, title, source, ctx.user_id, parts, transcript=text)
        conn.commit()
        background(meetings.process, mid, call_llm, transcribe, audio, ctype)
        return jsonify({"id": mid}), 201

    @bp.route(P + "/meetings/<int:mid>", methods=["GET"])
    @login_required
    def meeting_get(org_id, mid):
        _, cur, ctx = ctx_for(org_id)
        d = meetings.detail(cur, org_id, mid, ctx.user_id)
        for c in d["commitments"]:
            c["can_change"] = ledger.can_change(ctx, c)
        return jsonify(d)

    @bp.route(P + "/meetings/<int:mid>", methods=["DELETE"])
    @login_required
    def meeting_delete(org_id, mid):
        conn, cur, ctx = ctx_for(org_id)
        m = access.one(cur, "meetings", access.meetings(), mid, ctx.user_id, org_id)
        if not m:
            raise CorpError(404, "Meeting not found.")
        if not (m["created_by"] == ctx.user_id or ctx.is_admin or (m["unit_id"] and can_manage_people(ctx, m["unit_id"]))):
            raise CorpError(403, "Only whoever added it, or a manager of the team, can delete it.")
        cur.execute("DELETE FROM corp.commitments WHERE source_type = 'meeting' AND source_id = %s", (mid,))
        cur.execute("DELETE FROM corp.blockers WHERE source_type = 'meeting' AND source_id = %s", (mid,))
        cur.execute("DELETE FROM corp.meetings WHERE id = %s", (mid,))
        conn.commit()
        return jsonify({"ok": True})

    @bp.route(P + "/meetings/<int:mid>/common-ground", methods=["POST"])
    @login_required
    def meeting_common_ground(org_id, mid):
        conn, cur, ctx = ctx_for(org_id)
        m = access.one(cur, "meetings", access.meetings(), mid, ctx.user_id, org_id)
        if not m:
            raise CorpError(404, "Meeting not found.")
        out = insights.common_ground(cur, call_llm, org_id, ctx.user_id, meeting=m)
        if "error" in out:
            raise CorpError(400, out["error"])
        conn.commit()
        return jsonify(out)

    # ------------------------------------------------------- commitments
    @bp.route(P + "/commitments", methods=["GET"])
    @login_required
    def commitment_list(org_id):
        _, cur, ctx = ctx_for(org_id)
        scope = request.args.get("scope", "mine")
        p = {"org": org_id, "me": ctx.user_id, "unit": request.args.get("unit_id")}
        extra = {"mine": " AND x.owner_id = %(me)s", "owed": " AND x.owed_to_id = %(me)s",
                 "team": " AND x.unit_id = %(unit)s" if p["unit"] else ""}.get(scope, "")
        cur.execute("SELECT x.*, COALESCE(o.username, x.owner_name) AS owner, COALESCE(w.username, x.owed_to_name) AS owed_to, "
                    "(SELECT name FROM corp.contacts ct WHERE ct.id = x.contact_id) AS contact_name, "
                    "(SELECT name FROM corp.projects pj WHERE pj.id = x.project_id) AS project_name, "
                    "(SELECT count(*) FROM corp.commitments k WHERE k.supports_id = x.id) AS supporting_count, "
                    "un.name AS unit_name, EXISTS (SELECT 1 FROM corp.risks r WHERE r.commitment_id = x.id AND r.resolved_at IS NULL) AS at_risk "
                    "FROM corp.commitments x LEFT JOIN users o ON o.id = x.owner_id LEFT JOIN users w ON w.id = x.owed_to_id "
                    f"LEFT JOIN corp.units un ON un.id = x.unit_id WHERE x.org_id = %(org)s AND {access.commitments()}{extra} "
                    "ORDER BY (x.status IN ('done','dropped')), x.due_date NULLS LAST, x.id DESC LIMIT 300", p)
        rows = [ledger.commitment_view(r) for r in cur.fetchall()]
        for r in rows:
            r["can_change"] = ledger.can_change(ctx, r)
        return jsonify({"commitments": rows})

    @bp.route(P + "/commitments", methods=["POST"])
    @login_required
    def commitment_create(org_id):
        conn, cur, ctx = ctx_for(org_id)
        d = body()
        text = str(d.get("text") or "").strip()
        if not text:
            raise CorpError(400, "Describe the commitment.")
        unit_id = in_unit(ctx, d.get("unit_id"))
        owner = int(d.get("owner_id") or ctx.user_id)
        owed = int(d["owed_to_id"]) if d.get("owed_to_id") else None
        for u in (owner, owed):
            if u and not orgs.member_role(cur, org_id, u):
                raise CorpError(400, "People must be in the organisation.")
        cid = ledger.add_commitment(cur, org_id, unit_id, text, owner_id=owner, owed_to_id=owed, due=d.get("due_date"),
                                    status="open", actor_id=ctx.user_id)
        # Optional: part of a project, owed to an outside contact (e.g. the client), or supporting another commitment.
        extra = {k: int(d[k]) for k in ("project_id", "contact_id", "supports_id") if d.get(k)}
        if extra.get("supports_id") and not access.one(cur, "commitments", access.commitments(), extra["supports_id"], ctx.user_id, org_id):
            raise CorpError(404, "Commitment not found.")
        if extra:
            cur.execute(f"UPDATE corp.commitments SET {', '.join(f'{k} = %s' for k in extra)} WHERE id = %s", (*extra.values(), cid))
            deps.evaluate(cur, extra.get("supports_id"), push, ctx.user_id)
        conn.commit()
        return jsonify({"id": cid}), 201

    def visible_commitment(cur, ctx, org_id, cid):
        row = access.one(cur, "commitments", access.commitments(), cid, ctx.user_id, org_id)
        if not row:
            raise CorpError(404, "Commitment not found.")
        return row

    @bp.route(P + "/commitments/<int:cid>", methods=["GET"])
    @login_required
    def commitment_get(org_id, cid):
        _, cur, ctx = ctx_for(org_id)
        row = visible_commitment(cur, ctx, org_id, cid)
        cur.execute("SELECT e.kind, e.detail, e.at, us.username AS actor FROM corp.commitment_events e "
                    "LEFT JOIN users us ON us.id = e.actor_id WHERE e.commitment_id = %s ORDER BY e.id", (cid,))
        events = [dict(r) | {"at": r["at"].isoformat()} for r in cur.fetchall()]
        return jsonify({"commitment": ledger.commitment_view(row) | {"can_change": ledger.can_change(ctx, row)}, "history": events})

    @bp.route(P + "/commitments/<int:cid>/<action>", methods=["POST"])
    @login_required
    def commitment_action(org_id, cid, action):
        conn, cur, ctx = ctx_for(org_id)
        row = visible_commitment(cur, ctx, org_id, cid)
        if not ledger.can_change(ctx, row):
            raise CorpError(403, "Only the people involved, or a manager of the team, can change this.")
        d = body()
        if action == "edit" and d.get("owner_id") and not orgs.member_role(cur, org_id, int(d["owner_id"])):
            raise CorpError(400, "The owner must be in the organisation.")
        ledger.apply_action(cur, ctx, row, action, d)
        # Dependencies: re-check what this supports, and (if its own date moved) what supports it.
        deps.evaluate(cur, row["supports_id"], push, ctx.user_id)
        deps.evaluate(cur, cid, push, ctx.user_id)
        if action == "done":
            cur.execute("UPDATE corp.risks SET resolved_at = now() WHERE commitment_id = %s AND resolved_at IS NULL", (cid,))
        conn.commit()
        return jsonify(commitment_get(org_id, cid).get_json())

    # --------------------------------------------------------- decisions
    @bp.route(P + "/decisions", methods=["GET"])
    @login_required
    def decision_list(org_id):
        _, cur, ctx = ctx_for(org_id)
        unit = request.args.get("unit_id")
        cur.execute("SELECT x.id, x.title, x.version, x.unit_id, un.name AS unit_name, x.updated_at, v.text, v.made_by "
                    "FROM corp.decisions x JOIN corp.decision_versions v ON v.decision_id = x.id AND v.version = x.version "
                    f"LEFT JOIN corp.units un ON un.id = x.unit_id WHERE x.org_id = %(org)s AND {access.decisions()}"
                    + (" AND x.unit_id = %(unit)s" if unit else "") + " ORDER BY x.updated_at DESC LIMIT 300",
                    {"org": org_id, "me": ctx.user_id, "unit": unit})
        return jsonify({"decisions": [dict(r) | {"updated_at": r["updated_at"].isoformat()} for r in cur.fetchall()]})

    @bp.route(P + "/decisions", methods=["POST"])
    @login_required
    def decision_create(org_id):
        conn, cur, ctx = ctx_for(org_id)
        d = body()
        unit_id = in_unit(ctx, d.get("unit_id"))
        if unit_id is None:
            raise CorpError(400, "Pick the team this decision belongs to.")
        text = str(d.get("text") or "").strip()
        if not text:
            raise CorpError(400, "Write the decision.")
        did = ledger.add_decision(cur, org_id, unit_id, None, str(d.get("title") or text)[:120], text, actor_id=ctx.user_id)
        conn.commit()
        return jsonify({"id": did}), 201

    def visible_decision(cur, ctx, org_id, did):
        row = access.one(cur, "decisions", access.decisions(), did, ctx.user_id, org_id)
        if not row:
            raise CorpError(404, "Decision not found.")
        return row

    @bp.route(P + "/decisions/<int:did>", methods=["GET"])
    @login_required
    def decision_get(org_id, did):
        _, cur, ctx = ctx_for(org_id)
        row = visible_decision(cur, ctx, org_id, did)
        cur.execute("SELECT v.version, v.text, v.quote, v.made_by, v.source_type, v.source_id, v.created_at, us.username AS by "
                    "FROM corp.decision_versions v LEFT JOIN users us ON us.id = v.created_by WHERE v.decision_id = %s "
                    "ORDER BY v.version DESC", (did,))
        versions = [dict(r) | {"created_at": r["created_at"].isoformat()} for r in cur.fetchall()]
        return jsonify({"decision": {"id": row["id"], "title": row["title"], "unit_id": row["unit_id"], "version": row["version"],
                                     "can_edit": row["unit_id"] in ctx.my_units}, "versions": versions})

    @bp.route(P + "/decisions/<int:did>/versions", methods=["POST"])
    @login_required
    def decision_version(org_id, did):
        conn, cur, ctx = ctx_for(org_id)
        row = visible_decision(cur, ctx, org_id, did)
        in_unit(ctx, row["unit_id"])
        text = str(body().get("text") or "").strip()
        if not text:
            raise CorpError(400, "Write the new decision.")
        ledger.add_decision(cur, org_id, row["unit_id"], None, row["title"], text, actor_id=ctx.user_id, updates_id=did)
        conn.commit()
        return jsonify(decision_get(org_id, did).get_json())

    @bp.route(P + "/common-ground/<int:gid>/decision", methods=["POST"])
    @login_required
    def decision_from_common_ground(org_id, gid):
        conn, cur, ctx = ctx_for(org_id)
        cur.execute("SELECT g.*, t.unit_id AS t_unit, m.unit_id AS m_unit FROM corp.common_ground g "
                    "LEFT JOIN corp.threads t ON t.id = g.thread_id LEFT JOIN corp.meetings m ON m.id = g.meeting_id "
                    "WHERE g.id = %s AND g.org_id = %s", (gid, org_id))
        g = cur.fetchone()
        if not g:
            raise CorpError(404, "Not found.")
        unit_id = g["t_unit"] or g["m_unit"]
        if unit_id is None or unit_id not in ctx.my_units:
            raise CorpError(400, "Decisions are logged in a team. Use a team conversation or a team meeting.")
        text = str(body().get("text") or "").strip()
        if not text:
            raise CorpError(400, "Pick what was agreed.")
        did = ledger.add_decision(cur, org_id, unit_id, g["meeting_id"], text[:120], text, source_type="common_ground",
                                  source_id=gid, actor_id=ctx.user_id)
        conn.commit()
        return jsonify({"id": did}), 201

    # ------------------------------------------------------ ask, brief, note
    @bp.route(P + "/ask", methods=["POST"])
    @login_required
    def ask(org_id):
        _, cur, ctx = ctx_for(org_id)
        d = body()
        q = str(d.get("question") or "").strip()[:500]
        if not q:
            raise CorpError(400, "Ask a question.")
        unit = d.get("unit_id")
        if unit is not None and int(unit) not in ctx.my_units:
            raise CorpError(403, "You can only ask about teams you're in.")
        return jsonify(insights.ask(cur, call_llm, org_id, ctx.user_id, q, int(unit) if unit is not None else None))

    @bp.route(P + "/catch-up", methods=["POST"])
    @login_required
    def catch_up(org_id):
        """{days} for "catch me up"; {project_id, contact_id} for meeting prep."""
        _, cur, ctx = ctx_for(org_id)
        d = body()
        days = min(max(int(d.get("days") or 7), 1), 60)
        project_id = int(d["project_id"]) if d.get("project_id") else None
        if project_id:
            cur.execute("SELECT 1 FROM corp.projects p LEFT JOIN corp.project_members pm ON pm.project_id = p.id AND pm.user_id = %s "
                        "WHERE p.id = %s AND p.org_id = %s AND (pm.user_id IS NOT NULL OR %s)", (ctx.user_id, project_id, org_id, ctx.is_admin))
            if not cur.fetchone():
                raise CorpError(404, "Project not found.")
        contact_id = int(d["contact_id"]) if d.get("contact_id") else None
        return jsonify(insights.catch_up(cur, call_llm, org_id, ctx.user_id, days, project_id, contact_id))

    @bp.route(P + "/brief", methods=["GET"])
    @login_required
    def brief(org_id):
        _, cur, ctx = ctx_for(org_id)
        return jsonify(insights.brief(cur, org_id, ctx.user_id))

    @bp.route(P + "/mail-link", methods=["GET", "PUT"])
    @login_required
    def mail_link(org_id):
        """Opt in to promises from my work email (the mailbox connected in Personal)."""
        conn, cur, ctx = ctx_for(org_id)
        linked = mail_linked(cur, ctx.user_id) if mail_linked else []
        if request.method == "PUT":
            on = bool(body().get("enabled"))
            if on and not linked:
                raise CorpError(400, "Connect Gmail or Outlook first: Personal, Settings, Email.")
            cur.execute("INSERT INTO corp.mail_links (org_id, user_id, enabled) VALUES (%s,%s,%s) "
                        "ON CONFLICT (org_id, user_id) DO UPDATE SET enabled = EXCLUDED.enabled", (org_id, ctx.user_id, on))
            conn.commit()
        cur.execute("SELECT enabled FROM corp.mail_links WHERE org_id = %s AND user_id = %s", (org_id, ctx.user_id))
        row = cur.fetchone()
        cur.execute("SELECT count(*) AS n FROM corp.mail_items WHERE org_id = %s AND user_id = %s", (org_id, ctx.user_id))
        return jsonify({"enabled": bool(row and row["enabled"]), "connected": linked, "read": cur.fetchone()["n"]})

    @bp.route(P + "/work-note", methods=["GET", "PUT"])
    @login_required
    def work_note(org_id):
        conn, cur, ctx = ctx_for(org_id)
        if request.method == "PUT":
            d = body()
            cur.execute("INSERT INTO corp.work_notes (org_id, user_id, note, sharing) VALUES (%s,%s,%s,%s) "
                        "ON CONFLICT (org_id, user_id) DO UPDATE SET note = EXCLUDED.note, sharing = EXCLUDED.sharing, updated_at = now()",
                        (org_id, ctx.user_id, str(d.get("note") or "")[:600], bool(d.get("sharing"))))
            conn.commit()
        cur.execute("SELECT note, sharing FROM corp.work_notes WHERE org_id = %s AND user_id = %s", (org_id, ctx.user_id))
        row = cur.fetchone()
        return jsonify(dict(row) if row else {"note": "", "sharing": False})
