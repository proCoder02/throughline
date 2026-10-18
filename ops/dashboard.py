"""Admin ops dashboard -- a Flask Blueprint, fully self-contained: its own
DB pool (ops/db.py), its own templates folder, its own auth (a fixed
credential, separate from the users table entirely -- there's no admin/
role concept anywhere else in this app and this is a single-operator ops
view, not a per-user feature). Session-scoped via its own "is_admin" flag,
independent of the main app's login "user_id" flag.

Registered into the main app with a single
`app.register_blueprint(admin_bp)` call -- every other change to ops
behavior happens inside this package, never in app.py."""
from __future__ import annotations

import hmac
import os
from datetime import datetime, timezone
from functools import wraps

from flask import Blueprint, jsonify, redirect, render_template, request, session

from . import db as ops_db
from .jobs import job_status
from .schema import ensure_ops_schema
from .stats import db_stats, ei_realtime_stats, fcm_stats, system_stats, weekly_digest_stats

admin_bp = Blueprint("admin", __name__, template_folder="templates")


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect("/admin/login")
        return view_func(*args, **kwargs)
    return wrapped


@admin_bp.route("/admin/login", methods=["GET"])
def admin_login_page():
    if session.get("is_admin"):
        return redirect("/admin")
    return render_template("admin_login.html", error=None)


@admin_bp.route("/admin/login", methods=["POST"])
def admin_login_submit():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    expected_user = os.getenv("ADMIN_USERNAME", "")
    expected_pass = os.getenv("ADMIN_PASSWORD", "")
    # hmac.compare_digest instead of == -- a naive comparison short-circuits
    # on the first mismatched character, leaking how many leading
    # characters matched via response timing.
    user_ok = bool(expected_user) and hmac.compare_digest(username, expected_user)
    pass_ok = bool(expected_pass) and hmac.compare_digest(password, expected_pass)
    if not (user_ok and pass_ok):
        return render_template("admin_login.html", error="Invalid username or password"), 401
    session["is_admin"] = True
    return redirect("/admin")


@admin_bp.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    return redirect("/admin/login")


@admin_bp.route("/admin")
@admin_required
def admin_dashboard_page():
    # Both optional -- an unset URL renders its nav item disabled instead
    # of a broken/empty link. Infra vs Finance stays two separate external
    # tools linked from one nav, not merged into this page, per standing
    # instruction (ops and finance stay apart for now).
    return render_template(
        "admin_dashboard.html",
        grafana_url=os.getenv("GRAFANA_DASHBOARD_URL", "").strip(),
        costlens_url=os.getenv("COSTLENS_DASHBOARD_URL", "").strip(),
    )


@admin_bp.route("/admin/api/status")
@admin_required
def admin_api_status():
    conn = ops_db.get_conn()
    try:
        cur = ops_db.dict_cursor(conn)
        ensure_ops_schema(cur, conn)
        return jsonify({
            "jobs": job_status(cur),
            "ei_realtime": ei_realtime_stats(cur),
            "weekly_digest": weekly_digest_stats(cur),
            "fcm": fcm_stats(cur),
            "db": db_stats(cur),
            "system": system_stats(),
            "flags": {
                "emotional_intelligence_enabled": os.getenv("EMOTIONAL_INTELLIGENCE_ENABLED", "false").strip().lower() == "true",
                "nudge_feature_enabled": os.getenv("NUDGE_FEATURE_ENABLED", "false").strip().lower() == "true",
            },
            "generated_at": datetime.now(timezone.utc).isoformat(),
        })
    finally:
        ops_db.release_conn(conn)
