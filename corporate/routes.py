"""HTTP API for Corporate -- a Flask Blueprint built by
create_corporate_blueprint(), which takes the host app's helpers instead of
importing app.py. Every route needs a signed-in user; an organisation the
caller isn't in answers 404.

  GET    /corp/me                                    my organisations
  POST   /corp/orgs                                  {name, departments?: [..]} create one (I become owner)
  GET    /corp/orgs/<org>                            organisation, its units, what I may do
  PATCH  /corp/orgs/<org>                            {name}                      admin
  DELETE /corp/orgs/<org>                            {confirm_name}              owner
  POST   /corp/orgs/<org>/leave                      leave (not the owner)
  POST   /corp/orgs/<org>/units                      {name, kind, parent_id}
  PATCH  /corp/orgs/<org>/units/<unit>               {name}
  DELETE /corp/orgs/<org>/units/<unit>               the unit and everything under it
  GET    /corp/orgs/<org>/units/<unit>/members
  PUT    /corp/orgs/<org>/units/<unit>/members/<user> {role: manager|member}
  DELETE /corp/orgs/<org>/units/<unit>/members/<user>
  GET    /corp/orgs/<org>/members                    the directory
  PATCH  /corp/orgs/<org>/members/<user>             {role?, title?}
  DELETE /corp/orgs/<org>/members/<user>             remove from the organisation
  GET    /corp/orgs/<org>/invites                    admins and managers
  POST   /corp/orgs/<org>/invites                    {emails, role, unit_id?, unit_role?}
  DELETE /corp/orgs/<org>/invites/<invite>           revoke
  GET    /corp/invites/<token>                       preview an invitation
  POST   /corp/invites/<token>/accept                join"""
from __future__ import annotations

import threading

from flask import Blueprint, jsonify, request

from . import invites, orgs, units
from .config import MAX_INVITES_PER_REQUEST, NAME_MAX, TITLE_MAX
from .errors import CorpError
from .roles import (INVITE_ROLES, UNIT_KINDS, UNIT_ROLES, can_create_under, can_edit_unit, can_invite,
                    can_manage_people, can_remove_member, can_set_org_role, capabilities, valid_parent)


def _int(value, what="id"):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise CorpError(400, f"Invalid {what}.")


def create_corporate_blueprint(*, get_db, dict_cursor, login_required, current_user_id,
                               send_plain_email=None, public_base_url=None, call_llm=None, transcribe=None,
                               push=None, spawn=None, get_raw_connection=None, release_raw_connection=None, live=None) -> Blueprint:
    """send_plain_email(to, subject, body) and public_base_url() are optional:
    without them invitations still work, the inviter just shares the link."""
    from .schema import ensure_corp_schema

    bp = Blueprint("corporate", __name__)

    def db_cur():
        conn = get_db()
        cur = dict_cursor(conn)
        ensure_corp_schema(cur, conn)
        return conn, cur

    def body() -> dict:
        return request.get_json(silent=True) or {}

    from .ai import AIUnavailable

    @bp.errorhandler(AIUnavailable)
    def _ai_down(e):
        try:
            get_db().rollback()
        except Exception:
            pass
        print(f"[corporate] AI unavailable: {e.cause}")
        return jsonify({"error": str(e)}), 503

    @bp.errorhandler(CorpError)
    def _corp_error(e: CorpError):
        try:
            get_db().rollback()
        except Exception:
            pass
        return jsonify({"error": e.message}), e.status

    def unit_in_org(ctx, unit_id):
        unit_id = _int(unit_id, "unit")
        if unit_id not in ctx.units:
            raise CorpError(404, "Department or team not found.")
        return unit_id

    def org_view(cur, ctx) -> dict:
        org = orgs.get_org(cur, ctx.org_id)
        org["created_at"] = org["created_at"].isoformat()
        caps = capabilities(ctx)
        unit_rows = units.list_units(cur, ctx.org_id)
        for u in unit_rows:
            u.update(caps["units"].get(u["id"], {}))
            u["my_role"] = ctx.my_units.get(u["id"])
        cur.execute("SELECT count(*) AS n FROM corp.members WHERE org_id = %s", (ctx.org_id,))
        org["member_count"] = cur.fetchone()["n"]
        return {
            "org": org,
            "me": {"user_id": ctx.user_id, "role": ctx.role,
                   "org_admin": caps["org_admin"], "org_owner": caps["org_owner"],
                   "can_invite": ctx.is_admin or any(r == "manager" for r in ctx.my_units.values())},
            "units": unit_rows,
        }

    # ---------------------------------------------------------------- orgs

    @bp.route("/corp/me", methods=["GET"])
    @login_required
    def me():
        _, cur = db_cur()
        return jsonify({"enabled": True, "orgs": orgs.my_orgs(cur, current_user_id())})

    @bp.route("/corp/orgs", methods=["POST"])
    @login_required
    def create_org():
        conn, cur = db_cur()
        data = body()
        name = orgs.clean_name(data.get("name"), NAME_MAX, "Organisation name")
        departments = data.get("departments") or []
        if not isinstance(departments, list) or len(departments) > 50:
            raise CorpError(400, "Up to 50 departments can be added at once.")
        user_id = current_user_id()
        org_id = orgs.create_org(cur, user_id, name)
        seen = set()
        for d in departments:
            dname = orgs.clean_name(d, NAME_MAX, "Department name")
            if dname.lower() not in seen:
                seen.add(dname.lower())
                units.create_unit(cur, org_id, None, "department", dname)
        conn.commit()
        return jsonify(org_view(cur, orgs.load_context(cur, org_id, user_id))), 201

    @bp.route("/corp/orgs/<int:org_id>", methods=["GET"])
    @login_required
    def get_org(org_id):
        _, cur = db_cur()
        return jsonify(org_view(cur, orgs.load_context(cur, org_id, current_user_id())))

    @bp.route("/corp/orgs/<int:org_id>", methods=["PATCH"])
    @login_required
    def rename_org(org_id):
        conn, cur = db_cur()
        ctx = orgs.load_context(cur, org_id, current_user_id())
        if not ctx.is_admin:
            raise CorpError(403, "Only an admin can rename the organisation.")
        orgs.rename_org(cur, org_id, orgs.clean_name(body().get("name"), NAME_MAX, "Organisation name"))
        conn.commit()
        return jsonify(org_view(cur, ctx))

    @bp.route("/corp/orgs/<int:org_id>", methods=["DELETE"])
    @login_required
    def delete_org(org_id):
        conn, cur = db_cur()
        ctx = orgs.load_context(cur, org_id, current_user_id())
        if not ctx.is_owner:
            raise CorpError(403, "Only the owner can delete the organisation.")
        org = orgs.get_org(cur, org_id)
        typed = " ".join(str(body().get("confirm_name") or "").split()).lower()
        if typed != org["name"].lower():
            raise CorpError(400, "Type the organisation's name to confirm.")
        orgs.delete_org(cur, org_id)
        conn.commit()
        return jsonify({"ok": True})

    @bp.route("/corp/orgs/<int:org_id>/leave", methods=["POST"])
    @login_required
    def leave_org(org_id):
        conn, cur = db_cur()
        ctx = orgs.load_context(cur, org_id, current_user_id())
        if ctx.is_owner:
            raise CorpError(400, "The owner can't leave. Delete the organisation instead.")
        orgs.remove_member(cur, org_id, ctx.user_id)
        conn.commit()
        return jsonify({"ok": True})

    # --------------------------------------------------------------- units

    @bp.route("/corp/orgs/<int:org_id>/units", methods=["POST"])
    @login_required
    def create_unit(org_id):
        conn, cur = db_cur()
        ctx = orgs.load_context(cur, org_id, current_user_id())
        data = body()
        kind = data.get("kind")
        if kind not in UNIT_KINDS:
            raise CorpError(400, "kind must be department or team.")
        parent_id = data.get("parent_id")
        parent = None
        if parent_id is not None:
            parent_id = unit_in_org(ctx, parent_id)
            parent = ctx.units[parent_id]
        if not can_create_under(ctx, parent_id):
            raise CorpError(403, "You can't add a unit here.")
        if not valid_parent(kind, parent):
            raise CorpError(400, "A department can't sit inside a team.")
        name = orgs.clean_name(data.get("name"), NAME_MAX, "Name")
        unit_id = units.create_unit(cur, org_id, parent_id, kind, name)
        conn.commit()
        return jsonify({"id": unit_id, **org_view(cur, orgs.load_context(cur, org_id, ctx.user_id))}), 201

    @bp.route("/corp/orgs/<int:org_id>/units/<int:unit_id>", methods=["PATCH"])
    @login_required
    def rename_unit(org_id, unit_id):
        conn, cur = db_cur()
        ctx = orgs.load_context(cur, org_id, current_user_id())
        unit_id = unit_in_org(ctx, unit_id)
        if not can_edit_unit(ctx, unit_id):
            raise CorpError(403, "You can't rename this unit.")
        units.rename_unit(cur, unit_id, orgs.clean_name(body().get("name"), NAME_MAX, "Name"))
        conn.commit()
        return jsonify(org_view(cur, orgs.load_context(cur, org_id, ctx.user_id)))

    @bp.route("/corp/orgs/<int:org_id>/units/<int:unit_id>", methods=["DELETE"])
    @login_required
    def delete_unit(org_id, unit_id):
        conn, cur = db_cur()
        ctx = orgs.load_context(cur, org_id, current_user_id())
        unit_id = unit_in_org(ctx, unit_id)
        if not can_edit_unit(ctx, unit_id):
            raise CorpError(403, "You can't delete this unit.")
        units.delete_unit(cur, unit_id)
        conn.commit()
        return jsonify(org_view(cur, orgs.load_context(cur, org_id, ctx.user_id)))

    @bp.route("/corp/orgs/<int:org_id>/units/<int:unit_id>/members", methods=["GET"])
    @login_required
    def list_unit_members(org_id, unit_id):
        _, cur = db_cur()
        ctx = orgs.load_context(cur, org_id, current_user_id())
        unit_id = unit_in_org(ctx, unit_id)
        return jsonify({"unit": ctx.units[unit_id], "members": units.unit_members(cur, unit_id),
                        "can_edit": can_edit_unit(ctx, unit_id),
                        "can_manage_people": can_manage_people(ctx, unit_id)})

    @bp.route("/corp/orgs/<int:org_id>/units/<int:unit_id>/members/<int:user_id>", methods=["PUT"])
    @login_required
    def put_unit_member(org_id, unit_id, user_id):
        conn, cur = db_cur()
        ctx = orgs.load_context(cur, org_id, current_user_id())
        unit_id = unit_in_org(ctx, unit_id)
        role = body().get("role", "member")
        if role not in UNIT_ROLES:
            raise CorpError(400, "role must be manager or member.")
        if not orgs.member_role(cur, org_id, user_id):
            raise CorpError(400, "Invite this person to the organisation first.")
        current = units.unit_role(cur, unit_id, user_id)
        touches_manager = role == "manager" or current == "manager"
        allowed = can_edit_unit(ctx, unit_id) if touches_manager else can_manage_people(ctx, unit_id)
        if not allowed:
            raise CorpError(403, "You can't change who is in this unit." if not touches_manager
                            else "Only someone above this unit can appoint or change its manager.")
        units.put_unit_member(cur, unit_id, user_id, role)
        conn.commit()
        return jsonify({"members": units.unit_members(cur, unit_id)})

    @bp.route("/corp/orgs/<int:org_id>/units/<int:unit_id>/members/<int:user_id>", methods=["DELETE"])
    @login_required
    def delete_unit_member(org_id, unit_id, user_id):
        conn, cur = db_cur()
        ctx = orgs.load_context(cur, org_id, current_user_id())
        unit_id = unit_in_org(ctx, unit_id)
        current = units.unit_role(cur, unit_id, user_id)
        if current is None:
            raise CorpError(404, "That person isn't in this unit.")
        allowed = can_edit_unit(ctx, unit_id) if current == "manager" else can_manage_people(ctx, unit_id)
        if not allowed:
            raise CorpError(403, "You can't remove this person from the unit.")
        units.remove_unit_member(cur, unit_id, user_id)
        conn.commit()
        return jsonify({"members": units.unit_members(cur, unit_id)})

    # ------------------------------------------------------------- members

    @bp.route("/corp/orgs/<int:org_id>/members", methods=["GET"])
    @login_required
    def list_members(org_id):
        _, cur = db_cur()
        ctx = orgs.load_context(cur, org_id, current_user_id())
        return jsonify({"members": orgs.list_members(cur, org_id, show_email=True), "me": {"role": ctx.role}})

    @bp.route("/corp/orgs/<int:org_id>/members/<int:user_id>", methods=["PATCH"])
    @login_required
    def update_member(org_id, user_id):
        conn, cur = db_cur()
        ctx = orgs.load_context(cur, org_id, current_user_id())
        target_role = orgs.member_role(cur, org_id, user_id)
        if not target_role:
            raise CorpError(404, "That person isn't in this organisation.")
        data = body()
        role = data.get("role")
        if role is not None and role != target_role and not can_set_org_role(ctx, target_role, role):
            raise CorpError(403, "Only the owner can change organisation roles.")
        title = None
        if "title" in data:
            if not (ctx.is_admin or user_id == ctx.user_id):
                raise CorpError(403, "You can only change your own job title.")
            title = " ".join(str(data.get("title") or "").split())[:TITLE_MAX]
        orgs.set_member(cur, org_id, user_id, role=role if role != target_role else None, title=title)
        conn.commit()
        return jsonify({"members": orgs.list_members(cur, org_id, show_email=True)})

    @bp.route("/corp/orgs/<int:org_id>/members/<int:user_id>", methods=["DELETE"])
    @login_required
    def remove_member(org_id, user_id):
        conn, cur = db_cur()
        ctx = orgs.load_context(cur, org_id, current_user_id())
        target_role = orgs.member_role(cur, org_id, user_id)
        if not target_role:
            raise CorpError(404, "That person isn't in this organisation.")
        if not can_remove_member(ctx, user_id, target_role):
            raise CorpError(403, "You can't remove this person.")
        orgs.remove_member(cur, org_id, user_id)
        conn.commit()
        return jsonify({"members": orgs.list_members(cur, org_id, show_email=True)})

    # ------------------------------------------------------------- invites

    def can_see_invites(ctx) -> bool:
        return ctx.is_admin or any(r == "manager" for r in ctx.my_units.values())

    @bp.route("/corp/orgs/<int:org_id>/invites", methods=["GET"])
    @login_required
    def list_invites(org_id):
        _, cur = db_cur()
        ctx = orgs.load_context(cur, org_id, current_user_id())
        if not can_see_invites(ctx):
            raise CorpError(403, "Only admins and managers can see invitations.")
        return jsonify({"invites": invites.list_for_org(cur, org_id)})

    @bp.route("/corp/orgs/<int:org_id>/invites", methods=["POST"])
    @login_required
    def create_invites(org_id):
        conn, cur = db_cur()
        ctx = orgs.load_context(cur, org_id, current_user_id())
        data = body()
        role = data.get("role", "member")
        if role not in INVITE_ROLES:
            raise CorpError(400, "role must be admin or member.")
        unit_id = data.get("unit_id")
        unit_role = data.get("unit_role") or "member"
        if unit_id is not None:
            unit_id = unit_in_org(ctx, unit_id)
            if unit_role not in UNIT_ROLES:
                raise CorpError(400, "unit_role must be manager or member.")
        if not can_invite(ctx, role, unit_id, unit_role):
            raise CorpError(403, "You can't send this invitation.")
        emails, invalid = invites.parse_emails(data.get("emails", data.get("email")))
        if not emails:
            raise CorpError(400, "Add at least one valid email address.")
        if len(emails) > MAX_INVITES_PER_REQUEST:
            raise CorpError(400, f"At most {MAX_INVITES_PER_REQUEST} invitations at once.")
        cur.execute(
            "SELECT lower(u.email) AS email FROM corp.members m JOIN users u ON u.id = m.user_id "
            "WHERE m.org_id = %s AND lower(u.email) = ANY(%s)",
            (org_id, emails),
        )
        already = {r["email"] for r in cur.fetchall()}
        base = (public_base_url() if public_base_url else request.host_url).rstrip("/")
        created = []
        for email in emails:
            if email in already:
                continue
            invite_id, token = invites.create(cur, org_id, email, role, unit_id, unit_role, ctx.user_id)
            created.append({"id": invite_id, "email": email, "link": f"{base}/app/corp/join/{token}"})
        conn.commit()

        emailed = bool(send_plain_email and created)
        if emailed:
            org_name = orgs.get_org(cur, org_id)["name"]
            cur.execute("SELECT username FROM users WHERE id = %s", (ctx.user_id,))
            inviter = cur.fetchone()["username"]
            where = f" ({ctx.units[unit_id]['name']})" if unit_id else ""
            jobs = [(c["email"], c["link"]) for c in created]

            def _send():
                for to, link in jobs:
                    try:
                        send_plain_email(
                            to, f"{inviter} invited you to {org_name} on Throughline",
                            f"{inviter} invited you to join {org_name}{where} on Throughline.\n\n"
                            f"Open this link to accept (sign in or create an account with this email address):\n"
                            f"{link}\n\nThe link works for 14 days. If you weren't expecting this, ignore this email.",
                        )
                    except Exception as e:  # one bad address must not stop the rest
                        print(f"[corporate] invite email to {to} failed: {e!r}")
            threading.Thread(target=_send, daemon=True).start()

        return jsonify({"created": created, "already_members": sorted(already), "invalid": invalid,
                        "emailed": emailed}), 201

    @bp.route("/corp/orgs/<int:org_id>/invites/<int:invite_id>", methods=["DELETE"])
    @login_required
    def revoke_invite(org_id, invite_id):
        conn, cur = db_cur()
        ctx = orgs.load_context(cur, org_id, current_user_id())
        row = invites.get_by_id(cur, org_id, invite_id)
        if not row:
            raise CorpError(404, "Invitation not found.")
        if not (ctx.is_admin or row["invited_by"] == ctx.user_id):
            raise CorpError(403, "Only an admin or the person who sent it can revoke this invitation.")
        invites.revoke(cur, invite_id)
        conn.commit()
        return jsonify({"invites": invites.list_for_org(cur, org_id)})

    def my_email(cur, user_id):
        cur.execute("SELECT email FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        return (row["email"] or "").strip().lower() if row else ""

    @bp.route("/corp/invites/<token>", methods=["GET"])
    @login_required
    def preview_invite(token):
        _, cur = db_cur()
        row = invites.get_by_token(cur, token)
        if not row:
            raise CorpError(404, "This invitation link isn't valid.")
        user_id = current_user_id()
        return jsonify({
            "org_id": row["org_id"], "org_name": row["org_name"], "role": row["role"],
            "unit_name": row["unit_name"], "unit_kind": row["unit_kind"], "unit_role": row["unit_role"],
            "status": invites.status_of(row), "email_masked": invites.mask_email(row["email"]),
            "email_matches": my_email(cur, user_id) == row["email"],
            "already_member": orgs.member_role(cur, row["org_id"], user_id) is not None,
        })

    @bp.route("/corp/invites/<token>/accept", methods=["POST"])
    @login_required
    def accept_invite(token):
        conn, cur = db_cur()
        row = invites.get_by_token(cur, token, for_update=True)
        if not row:
            raise CorpError(404, "This invitation link isn't valid.")
        status = invites.status_of(row)
        if status != "pending":
            raise CorpError(410, {"accepted": "This invitation has already been used.",
                                  "expired": "This invitation has expired. Ask for a new one.",
                                  "revoked": "This invitation was withdrawn."}[status])
        user_id = current_user_id()
        if my_email(cur, user_id) != row["email"]:
            raise CorpError(403, f"This invitation was sent to {invites.mask_email(row['email'])}. "
                                 "Sign in with the account that uses that email address.")
        orgs.add_member(cur, row["org_id"], user_id, row["role"])
        if row["unit_id"]:
            current = units.unit_role(cur, row["unit_id"], user_id)
            if current != "manager":  # never downgrade an existing manager
                units.put_unit_member(cur, row["unit_id"], user_id, row["unit_role"] or "member")
        invites.mark_accepted(cur, row["id"], user_id)
        conn.commit()
        return jsonify({"ok": True, "org_id": row["org_id"]})

    if call_llm:  # work memory: chat, meetings, ledger, decisions, ask, brief (routes_work.py)
        from .routes_work import register
        register(bp, db_cur=db_cur, login_required=login_required, current_user_id=current_user_id, call_llm=call_llm,
                 transcribe=transcribe, push=push, spawn=spawn, get_raw_connection=get_raw_connection,
                 release_raw_connection=release_raw_connection, dict_cursor=dict_cursor, live=live)
    return bp
