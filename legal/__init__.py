"""Legal and privacy: the public Privacy Policy / Terms / account-deletion
pages, the recording-consent notice, and deleting an account with all of
its data.

  GET  /privacy, /terms, /account/delete   public pages (also the URLs the
                                           app stores ask for)
  GET  /consents                           which notices this user accepted
  POST /consents {"kind"}                  accept the current version
  POST /account/delete                     {"password", "confirm": "DELETE"}
                                           (+ "username" when not signed in)

app.py registers this with one call and hands over what it owns (DB
access, auth helpers, Redis, file storage, the email sender), and calls
record_consent() once from /register when the sign-up box was ticked.

Bumping a *_VERSION below re-asks every user on their next visit."""
from __future__ import annotations

import os
import threading
import time

from flask import Blueprint, jsonify, render_template, request, session

TERMS_VERSION = 1  # Terms of Service + Privacy Policy, effective date below
RECORDING_NOTICE_VERSION = 1
EFFECTIVE_DATE = "5 November 2026"
CONSENT_VERSIONS = {"terms": TERMS_VERSION, "recording_notice": RECORDING_NOTICE_VERSION}

DELETE_ATTEMPTS = 5  # wrong passwords per account per window before /account/delete refuses
DELETE_WINDOW_SECONDS = 900

CONSENT_DDL = """
CREATE TABLE IF NOT EXISTS user_consents (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    version INTEGER NOT NULL,
    accepted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, kind)
)
"""

_consent_table_ready = False
_failed_deletes: dict = {}
_lock = threading.Lock()


def legal_info() -> dict:
    """Who runs the service -- from .env so it can be filled in without a
    code change. Anything not set yet renders as a visible "to be added"
    marker on the pages rather than an invented value."""
    return {
        "entity": os.getenv("LEGAL_ENTITY_NAME", "").strip(),
        "address": os.getenv("LEGAL_ADDRESS", "").strip(),
        "contact_email": os.getenv("LEGAL_CONTACT_EMAIL", "").strip(),
        "grievance_officer": os.getenv("LEGAL_GRIEVANCE_OFFICER", "").strip(),
        "jurisdiction": os.getenv("LEGAL_JURISDICTION", "").strip(),
        "effective_date": EFFECTIVE_DATE,
    }


def ensure_consent_table(cur, conn) -> bool:
    global _consent_table_ready
    if _consent_table_ready:
        return True
    try:
        cur.execute(CONSENT_DDL)
        conn.commit()
        _consent_table_ready = True
    except Exception as e:  # e.g. no CREATE right on prod -- the deploy creates it as postgres
        conn.rollback()
        print(f"[legal] user_consents not created here: {e!r}", flush=True)
        try:
            cur.execute("SELECT to_regclass('public.user_consents') IS NOT NULL AS ok")
            row = cur.fetchone()
            _consent_table_ready = bool(row["ok"] if isinstance(row, dict) else row[0])
        except Exception:
            conn.rollback()
    return _consent_table_ready


def record_consent(cur, conn, user_id: int, kind: str) -> None:
    """Best effort from /register: a failure here must never fail sign-up."""
    if kind not in CONSENT_VERSIONS:
        raise ValueError(kind)
    try:
        if not ensure_consent_table(cur, conn):
            return
        cur.execute(
            "INSERT INTO user_consents (user_id, kind, version) VALUES (%s, %s, %s) "
            "ON CONFLICT (user_id, kind) DO UPDATE SET version = EXCLUDED.version, accepted_at = now()",
            (user_id, kind, CONSENT_VERSIONS[kind]),
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[legal] record_consent failed ({kind}, user {user_id}): {e!r}", flush=True)


def consent_status(cur, conn, user_id: int) -> dict:
    out = {k: {"current_version": v, "accepted_version": None, "accepted_at": None, "needed": True}
           for k, v in CONSENT_VERSIONS.items()}
    if not ensure_consent_table(cur, conn):
        return out
    cur.execute("SELECT kind, version, accepted_at FROM user_consents WHERE user_id = %s", (user_id,))
    for r in cur.fetchall():
        if r["kind"] in out:
            out[r["kind"]].update(accepted_version=r["version"], accepted_at=r["accepted_at"].isoformat(),
                                  needed=r["version"] < CONSENT_VERSIONS[r["kind"]])
    return out


# --------------------------------------------------------------- deletion

def _run(cur, sql, args=()):
    """One statement inside a savepoint: a table that doesn't exist on this
    install (e.g. the EI or nudges schema never created) is skipped
    instead of aborting the whole deletion."""
    cur.execute("SAVEPOINT del_step")
    try:
        cur.execute(sql, args)
        cur.execute("RELEASE SAVEPOINT del_step")
        return cur.rowcount
    except Exception as e:
        cur.execute("ROLLBACK TO SAVEPOINT del_step")
        if getattr(e, "pgcode", None) in ("42P01", "3F000"):  # undefined table / schema
            return 0
        raise


def delete_account_data(cur, conn, user_id: int) -> dict:
    """Removes every database row belonging to the user, in one
    transaction. Almost everything hangs off users(id) ON DELETE CASCADE;
    the rest (EI subject data, nudges, push-send log) has no foreign key and
    is removed explicitly first. Files and Redis are handled by the caller
    after this commits."""
    counts = {}
    # EI "subjects" only this user is linked to (shared ones stay for the other user).
    cur.execute("SAVEPOINT ei")
    try:
        cur.execute(
            "SELECT subject_id FROM emotional_intelligence.app_user_subject_links WHERE subject_id IN "
            "(SELECT subject_id FROM emotional_intelligence.app_user_subject_links WHERE user_id = %s) "
            "GROUP BY subject_id HAVING count(DISTINCT user_id) = 1",
            (user_id,),
        )
        subject_ids = [r["subject_id"] for r in cur.fetchall()]
        cur.execute("RELEASE SAVEPOINT ei")
    except Exception as e:
        cur.execute("ROLLBACK TO SAVEPOINT ei")
        if getattr(e, "pgcode", None) not in ("42P01", "3F000"):
            raise
        subject_ids = []
    if subject_ids:
        # events/analysis_runs only SET NULL on subject delete -- remove them outright.
        counts["ei_events"] = _run(cur, "DELETE FROM emotional_intelligence.events WHERE subject_id = ANY(%s)", (subject_ids,))
        counts["ei_runs"] = _run(cur, "DELETE FROM emotional_intelligence.analysis_runs WHERE subject_id = ANY(%s)", (subject_ids,))
        counts["ei_subjects"] = _run(cur, "DELETE FROM emotional_intelligence.subjects WHERE id = ANY(%s)", (subject_ids,))
    counts["ei_links"] = _run(cur, "DELETE FROM emotional_intelligence.app_user_subject_links WHERE user_id = %s", (user_id,))
    counts["ei_digests"] = _run(cur, "DELETE FROM emotional_intelligence.weekly_digests WHERE user_id = %s", (user_id,))
    counts["nudges_sent"] = _run(cur, "DELETE FROM nudges.sent_log WHERE user_id = %s", (user_id,))
    counts["nudges_settings"] = _run(cur, "DELETE FROM nudges.user_settings WHERE user_id = %s", (user_id,))
    counts["push_log"] = _run(cur, "DELETE FROM ops.fcm_sends WHERE user_id = %s", (user_id,))
    cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
    counts["users"] = cur.rowcount
    conn.commit()
    return counts


def delete_redis_data(redis_client, user_id: int) -> int:
    if redis_client is None:
        return 0
    n = 0
    keys = [f"pending_events:{user_id}", f"ei_recent_statements:{user_id}"]
    keys += list(redis_client.scan_iter(match=f"user:{user_id}:*", count=500))
    for i in range(0, len(keys), 500):
        n += redis_client.delete(*keys[i:i + 500])
    return n


def _too_many_attempts(key) -> bool:
    now = time.monotonic()
    with _lock:
        recent = [t for t in _failed_deletes.get(key, []) if now - t < DELETE_WINDOW_SECONDS]
        _failed_deletes[key] = recent
        return len(recent) >= DELETE_ATTEMPTS


def _note_failure(key) -> None:
    with _lock:
        _failed_deletes.setdefault(key, []).append(time.monotonic())


# --------------------------------------------------------------- blueprint

def create_legal_blueprint(*, get_db, dict_cursor, login_required, current_user_id, check_password_hash,
                           redis_client=None, storage=None, send_plain_email=None, before_delete=None):
    """before_delete: callables (cur, user_id) run in the same transaction just
    before the account's rows are deleted -- e.g. Corporate handing over
    ownership of organisations. A list, so features can add to it later."""
    bp = Blueprint("legal", __name__, template_folder="templates")

    @bp.route("/privacy")
    def privacy_page():
        return render_template("legal/privacy.html", info=legal_info())

    @bp.route("/terms")
    def terms_page():
        return render_template("legal/terms.html", info=legal_info())

    @bp.route("/account/delete", methods=["GET"])
    def delete_account_page():
        return render_template("legal/delete_account.html", info=legal_info())

    @bp.route("/consents", methods=["GET"])
    @login_required
    def get_consents():
        db = get_db()
        return jsonify(consent_status(dict_cursor(db), db, current_user_id()))

    @bp.route("/consents", methods=["POST"])
    @login_required
    def accept_consent():
        kind = ((request.get_json(silent=True) or {}).get("kind") or "").strip()
        if kind not in CONSENT_VERSIONS:
            return jsonify({"error": f"kind must be one of {sorted(CONSENT_VERSIONS)}"}), 400
        db = get_db()
        cur = dict_cursor(db)
        if not ensure_consent_table(cur, db):
            return jsonify({"error": "Consent storage isn't available right now. Please try again."}), 503
        cur.execute(
            "INSERT INTO user_consents (user_id, kind, version) VALUES (%s, %s, %s) "
            "ON CONFLICT (user_id, kind) DO UPDATE SET version = EXCLUDED.version, accepted_at = now()",
            (current_user_id(), kind, CONSENT_VERSIONS[kind]),
        )
        db.commit()
        return jsonify({"ok": True, **consent_status(cur, db, current_user_id())})

    @bp.route("/account/delete", methods=["POST"])
    def delete_account():
        data = request.get_json(silent=True) or {}
        password = data.get("password") or ""
        if (data.get("confirm") or "").strip() != "DELETE":
            return jsonify({"error": 'Type DELETE to confirm.'}), 400
        db = get_db()
        cur = dict_cursor(db)
        # A typed username (the public /account/delete page) always decides
        # which account is meant -- never whoever is signed in on this browser.
        username = (data.get("username") or "").strip()
        uid = None if username else current_user_id()
        if uid:
            cur.execute("SELECT id, username, email, password_hash FROM users WHERE id = %s", (uid,))
        elif username:
            cur.execute("SELECT id, username, email, password_hash FROM users WHERE username = %s", (username,))
        else:
            return jsonify({"error": "Enter your username and password."}), 400
        row = cur.fetchone()
        throttle_key = row["id"] if row else ("name", (data.get("username") or "").strip().lower())
        if _too_many_attempts(throttle_key):
            return jsonify({"error": "Too many wrong passwords. Try again in 15 minutes."}), 429
        if not row or not check_password_hash(row["password_hash"], password):
            _note_failure(throttle_key)
            return jsonify({"error": "That username and password don't match."}), 403

        user_id, email, username = row["id"], row["email"], row["username"]
        try:
            for hook in (before_delete or []):
                hook(cur, user_id)
            counts = delete_account_data(cur, db, user_id)
        except Exception as e:
            db.rollback()
            print(f"[legal] account deletion failed for user {user_id}: {e!r}", flush=True)
            return jsonify({"error": "Something went wrong deleting your account. Nothing was deleted -- please try again."}), 500

        problems = []
        try:
            counts["files"] = storage.delete_user_files(user_id) if storage else 0
        except Exception as e:
            problems.append("files")
            print(f"[legal] file cleanup failed for deleted user {user_id}: {e!r}", flush=True)
        try:
            counts["cache_keys"] = delete_redis_data(redis_client, user_id)
        except Exception as e:
            problems.append("cache")
            print(f"[legal] cache cleanup failed for deleted user {user_id}: {e!r}", flush=True)
        print(f"[legal] deleted account {user_id}: {counts}" + (f" (cleanup issues: {problems})" if problems else ""), flush=True)

        session.clear()
        if email and send_plain_email:
            body = (f"Hi {username},\n\nYour Throughline account and the data stored with it have been deleted, "
                    "as you asked. If you didn't do this, reply to this email straight away.\n")

            def _send():
                try:
                    send_plain_email(email, "Your Throughline account has been deleted", body)
                except Exception as e:
                    print(f"[legal] deletion email failed: {e!r}", flush=True)
            threading.Thread(target=_send, daemon=True).start()
        return jsonify({"ok": True, "message": "Your account and its data have been deleted."})

    return bp
