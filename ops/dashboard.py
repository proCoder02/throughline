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

from . import actions as ops_actions
from . import db as ops_db
from . import job_control as ops_job_control
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
            "jobs": job_status(cur, conn),
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


# ----------------------------------------------------------------------------
# Job control -- on ice / off ice / start now / stop / restart. Dispatches
# through ops/actions.py, which knows which of these make sense for a
# given job (a subprocess like ei_pipeline or a custom job supports all
# five; an in-process thread like nudge_cycle only supports ice/unice and,
# for nudge_cycle specifically, start).
# ----------------------------------------------------------------------------

@admin_bp.route("/admin/api/jobs/<job_name>/ice", methods=["POST"])
@admin_required
def admin_job_ice(job_name):
    try:
        ops_actions.ice(job_name)
        return jsonify({"ok": True})
    except ops_actions.ActionNotSupported as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@admin_bp.route("/admin/api/jobs/<job_name>/unice", methods=["POST"])
@admin_required
def admin_job_unice(job_name):
    try:
        ops_actions.unice(job_name)
        return jsonify({"ok": True})
    except ops_actions.ActionNotSupported as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@admin_bp.route("/admin/api/jobs/<job_name>/start", methods=["POST"])
@admin_required
def admin_job_start(job_name):
    try:
        result = ops_actions.start_now(job_name)
        return jsonify({"ok": True, **result})
    except ops_actions.ActionNotSupported as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except FileNotFoundError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@admin_bp.route("/admin/api/jobs/<job_name>/stop", methods=["POST"])
@admin_required
def admin_job_stop(job_name):
    try:
        result = ops_actions.stop(job_name)
        return jsonify({"ok": True, **result})
    except ops_actions.ActionNotSupported as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@admin_bp.route("/admin/api/jobs/<job_name>/restart", methods=["POST"])
@admin_required
def admin_job_restart(job_name):
    try:
        result = ops_actions.restart(job_name)
        return jsonify({"ok": True, **result})
    except ops_actions.ActionNotSupported as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except FileNotFoundError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


# ----------------------------------------------------------------------------
# Custom jobs -- "pass a script path, give it a schedule" for anything the
# user wants to run going forward without touching app.py or a crontab.
# ----------------------------------------------------------------------------

@admin_bp.route("/admin/api/custom-jobs", methods=["GET"])
@admin_required
def admin_list_custom_jobs():
    conn = ops_db.get_conn()
    try:
        cur = ops_db.dict_cursor(conn)
        return jsonify({"jobs": ops_job_control.list_custom_jobs(cur, conn)})
    finally:
        ops_db.release_conn(conn)


@admin_bp.route("/admin/api/custom-jobs", methods=["POST"])
@admin_required
def admin_create_custom_job():
    data = request.get_json(silent=True) or {}
    job_name = (data.get("job_name") or "").strip()
    script_path = (data.get("script_path") or "").strip()
    schedule_kind = (data.get("schedule_kind") or "").strip()
    hours_utc = data.get("hours_utc") or None
    interval_minutes = data.get("interval_minutes") or None
    log_dir = (data.get("log_dir") or "").strip()

    if not script_path or not os.path.isfile(script_path):
        return jsonify({"ok": False, "error": f"script_path does not exist on the server: {script_path!r}"}), 400
    if not log_dir:
        log_dir = os.path.join(os.path.dirname(script_path), "logs")

    conn = ops_db.get_conn()
    try:
        cur = ops_db.dict_cursor(conn)
        if ops_job_control.get_custom_job(cur, conn, job_name):
            return jsonify({"ok": False, "error": f"a job named {job_name!r} already exists"}), 400
        ops_job_control.create_custom_job(cur, conn, job_name, script_path, schedule_kind,
                                           hours_utc, interval_minutes, log_dir)
        return jsonify({"ok": True, "job_name": job_name})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    finally:
        ops_db.release_conn(conn)


@admin_bp.route("/admin/api/custom-jobs/<job_name>", methods=["DELETE"])
@admin_required
def admin_delete_custom_job(job_name):
    conn = ops_db.get_conn()
    try:
        cur = ops_db.dict_cursor(conn)
        if not ops_job_control.get_custom_job(cur, conn, job_name):
            return jsonify({"ok": False, "error": f"no such custom job: {job_name}"}), 404
        ops_job_control.delete_custom_job(cur, conn, job_name)
        return jsonify({"ok": True})
    finally:
        ops_db.release_conn(conn)
