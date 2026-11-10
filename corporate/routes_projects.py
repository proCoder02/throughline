"""Projects: a client engagement with its stakeholder map (client-side
officials by organisation, department and who reports to whom; your vendor
side), and its deliverables (commitments tagged with the project) with
dependencies and health. Visible to project members and organisation admins.

  GET/POST   /corp/orgs/<org>/projects
  GET        /corp/orgs/<org>/projects/<p>                 the dashboard
  PATCH/DELETE /corp/orgs/<org>/projects/<p>
  PUT/DELETE /corp/orgs/<org>/projects/<p>/members/<user>  {role: lead|member}
  POST       /corp/orgs/<org>/projects/<p>/contacts
  PATCH/DELETE /corp/orgs/<org>/projects/<p>/contacts/<c>"""
from __future__ import annotations

from datetime import date

from flask import jsonify, request

from . import access, deps, ledger, orgs
from .errors import CorpError

FIELDS = ("side", "organisation", "department", "name", "position", "reports_to", "email", "notes")


def _date(v):
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        raise CorpError(400, "Dates look like 2026-11-30.")


def register(bp, *, db_cur, login_required, current_user_id):
    P = "/corp/orgs/<int:org_id>/projects"

    def ctx_for(org_id):
        conn, cur = db_cur()
        return conn, cur, orgs.load_context(cur, org_id, current_user_id())

    def body():
        return request.get_json(silent=True) or {}

    def project(cur, ctx, org_id, pid, edit=False):
        cur.execute("SELECT p.*, pm.role AS my_role FROM corp.projects p LEFT JOIN corp.project_members pm "
                    "ON pm.project_id = p.id AND pm.user_id = %s WHERE p.id = %s AND p.org_id = %s", (ctx.user_id, pid, org_id))
        p = cur.fetchone()
        if not p or not (p["my_role"] or ctx.is_admin):
            raise CorpError(404, "Project not found.")
        if edit and not (p["my_role"] == "lead" or ctx.is_admin):
            raise CorpError(403, "Only a project lead or an admin can change this.")
        return p

    @bp.route(P, methods=["GET"])
    @login_required
    def list_projects(org_id):
        _, cur, ctx = ctx_for(org_id)
        cur.execute("SELECT p.id, p.name, p.client, p.status, p.end_date, pm.role AS my_role, "
                    "(SELECT count(*) FROM corp.project_members x WHERE x.project_id = p.id) AS members, "
                    "(SELECT count(*) FROM corp.commitments c WHERE c.project_id = p.id AND c.status IN ('open','overdue','requested','detected')) AS open_items, "
                    "(SELECT count(*) FROM corp.commitments c WHERE c.project_id = p.id AND (c.status = 'overdue' OR c.risk_note IS NOT NULL) "
                    "  AND c.status NOT IN ('done','dropped','declined')) AS at_risk "
                    "FROM corp.projects p LEFT JOIN corp.project_members pm ON pm.project_id = p.id AND pm.user_id = %(me)s "
                    "WHERE p.org_id = %(org)s AND (pm.user_id IS NOT NULL OR %(admin)s) ORDER BY p.status, p.end_date NULLS LAST",
                    {"me": ctx.user_id, "org": org_id, "admin": ctx.is_admin})
        return jsonify({"projects": [dict(r) | {"end_date": r["end_date"].isoformat() if r["end_date"] else None}
                                     for r in cur.fetchall()]})

    @bp.route(P, methods=["POST"])
    @login_required
    def create(org_id):
        conn, cur, ctx = ctx_for(org_id)
        d = body()
        name = orgs.clean_name(d.get("name"), 160, "Project name")
        cur.execute("INSERT INTO corp.projects (org_id, name, client, description, start_date, end_date, created_by) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (org_id, name, str(d.get("client") or "")[:160], str(d.get("description") or "")[:2000],
                     _date(d.get("start_date")), _date(d.get("end_date")), ctx.user_id))
        pid = cur.fetchone()["id"]
        cur.execute("INSERT INTO corp.project_members VALUES (%s, %s, 'lead')", (pid, ctx.user_id))
        conn.commit()
        return jsonify({"id": pid}), 201

    @bp.route(P + "/<int:pid>", methods=["GET"])
    @login_required
    def dashboard(org_id, pid):
        _, cur, ctx = ctx_for(org_id)
        p = project(cur, ctx, org_id, pid)
        cur.execute("SELECT pm.user_id, pm.role, u.username, m.title FROM corp.project_members pm JOIN users u ON u.id = pm.user_id "
                    "LEFT JOIN corp.members m ON m.org_id = %s AND m.user_id = pm.user_id WHERE pm.project_id = %s "
                    "ORDER BY (pm.role = 'lead') DESC, lower(u.username)", (org_id, pid))
        members = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT * FROM corp.contacts WHERE project_id = %s ORDER BY side, organisation, department, id", (pid,))
        contacts = [dict(r) | {"created_at": r["created_at"].isoformat()} for r in cur.fetchall()]
        cur.execute("SELECT x.*, un.name AS unit_name, COALESCE(o.username, x.owner_name) AS owner, "
                    "COALESCE(w.username, x.owed_to_name) AS owed_to, ct.name AS contact_name "
                    "FROM corp.commitments x LEFT JOIN corp.units un ON un.id = x.unit_id LEFT JOIN users o ON o.id = x.owner_id "
                    "LEFT JOIN users w ON w.id = x.owed_to_id LEFT JOIN corp.contacts ct ON ct.id = x.contact_id "
                    f"WHERE x.org_id = %(org)s AND x.project_id = %(p)s AND {access.commitments()} "
                    "ORDER BY x.supports_id NULLS FIRST, x.due_date NULLS LAST", {"org": org_id, "p": pid, "me": ctx.user_id})
        items = []
        for r in cur.fetchall():
            v = ledger.commitment_view(r)
            v["blocker"] = deps._open_blocker(cur, r["id"])
            v["can_change"] = ledger.can_change(ctx, r)
            items.append(v)
        live = [i for i in items if i["status"] not in ("done", "dropped", "declined")]
        late = [i for i in live if i["status"] == "overdue"]
        risky = [i for i in live if i.get("risk_note") or i.get("blocker")]
        health = "late" if late else "at_risk" if risky else "on_track"
        out = dict(p)
        for f in ("start_date", "end_date"):
            out[f] = out[f].isoformat() if out[f] else None
        out["created_at"] = out["created_at"].isoformat()
        out["can_edit"] = p["my_role"] == "lead" or ctx.is_admin
        return jsonify({"project": out, "members": members, "contacts": contacts, "deliverables": items, "health": health,
                        "counts": {"open": len(live), "late": len(late), "at_risk": len(risky),
                                   "done": sum(i["status"] == "done" for i in items)}})

    @bp.route(P + "/<int:pid>", methods=["PATCH"])
    @login_required
    def edit(org_id, pid):
        conn, cur, ctx = ctx_for(org_id)
        project(cur, ctx, org_id, pid, edit=True)
        d = body()
        sets, vals = [], []
        for f in ("name", "client", "description", "status"):
            if f in d:
                if f == "status" and d[f] not in ("active", "on_hold", "closed"):
                    raise CorpError(400, "status must be active, on_hold or closed.")
                sets.append(f"{f} = %s"); vals.append(str(d[f] or "")[:2000])
        for f in ("start_date", "end_date"):
            if f in d:
                sets.append(f"{f} = %s"); vals.append(_date(d[f]))
        if sets:
            cur.execute(f"UPDATE corp.projects SET {', '.join(sets)} WHERE id = %s", (*vals, pid))
            conn.commit()
        return dashboard(org_id, pid)

    @bp.route(P + "/<int:pid>", methods=["DELETE"])
    @login_required
    def delete(org_id, pid):
        conn, cur, ctx = ctx_for(org_id)
        project(cur, ctx, org_id, pid, edit=True)
        cur.execute("DELETE FROM corp.projects WHERE id = %s", (pid,))  # deliverables stay, untagged
        conn.commit()
        return jsonify({"ok": True})

    @bp.route(P + "/<int:pid>/members/<int:user_id>", methods=["PUT", "DELETE"])
    @login_required
    def member(org_id, pid, user_id):
        conn, cur, ctx = ctx_for(org_id)
        project(cur, ctx, org_id, pid, edit=True)
        if request.method == "DELETE":
            cur.execute("DELETE FROM corp.project_members WHERE project_id = %s AND user_id = %s", (pid, user_id))
        else:
            if not orgs.member_role(cur, org_id, user_id):
                raise CorpError(400, "Add people from your organisation.")
            role = body().get("role", "member")
            if role not in ("lead", "member"):
                raise CorpError(400, "role must be lead or member.")
            cur.execute("INSERT INTO corp.project_members VALUES (%s,%s,%s) ON CONFLICT (project_id, user_id) "
                        "DO UPDATE SET role = EXCLUDED.role", (pid, user_id, role))
        cur.execute("SELECT count(*) AS n FROM corp.project_members WHERE project_id = %s AND role = 'lead'", (pid,))
        if cur.fetchone()["n"] == 0:
            conn.rollback()
            raise CorpError(400, "A project needs at least one lead.")
        conn.commit()
        return dashboard(org_id, pid)

    def clean_contact(cur, pid, d, partial=False):
        out = {}
        for f in FIELDS:
            if f not in d:
                continue
            v = d[f]
            if f == "reports_to":
                v = int(v) if v else None
                if v:
                    cur.execute("SELECT 1 FROM corp.contacts WHERE id = %s AND project_id = %s", (v, pid))
                    if not cur.fetchone():
                        raise CorpError(400, "They must report to someone in this project.")
            elif f == "side":
                if v not in ("client", "vendor", "partner"):
                    raise CorpError(400, "side must be client, vendor or partner.")
            else:
                v = " ".join(str(v or "").split())[:300] if f != "notes" else str(v or "")[:1000]
            out[f] = v
        if not partial and not out.get("name"):
            raise CorpError(400, "Name is required.")
        return out

    def members_only(cur, ctx, org_id, pid):
        p = project(cur, ctx, org_id, pid)
        if not (p["my_role"] or ctx.is_admin):
            raise CorpError(403, "Only project members can edit contacts.")

    @bp.route(P + "/<int:pid>/contacts", methods=["POST"])
    @login_required
    def add_contact(org_id, pid):
        conn, cur, ctx = ctx_for(org_id)
        members_only(cur, ctx, org_id, pid)
        c = clean_contact(cur, pid, body())
        cols = list(c)
        cur.execute(f"INSERT INTO corp.contacts (project_id, {', '.join(cols)}) VALUES (%s, {', '.join(['%s'] * len(cols))}) RETURNING id",
                    (pid, *c.values()))
        conn.commit()
        return jsonify(dashboard(org_id, pid).get_json()), 201

    @bp.route(P + "/<int:pid>/contacts/<int:cid>", methods=["PATCH", "DELETE"])
    @login_required
    def edit_contact(org_id, pid, cid):
        conn, cur, ctx = ctx_for(org_id)
        members_only(cur, ctx, org_id, pid)
        if request.method == "DELETE":
            cur.execute("DELETE FROM corp.contacts WHERE id = %s AND project_id = %s", (cid, pid))
        else:
            c = clean_contact(cur, pid, body(), partial=True)
            if c.get("reports_to") == cid:
                raise CorpError(400, "Someone can't report to themselves.")
            if c:
                cur.execute(f"UPDATE corp.contacts SET {', '.join(f'{k} = %s' for k in c)} WHERE id = %s AND project_id = %s",
                            (*c.values(), cid, pid))
        conn.commit()
        return dashboard(org_id, pid)
