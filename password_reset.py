"""Forgot-password flow: email a one-hour, single-use reset link.

  POST /password/forgot {"identifier": username or email}
      Always answers the same "if that account exists, we sent a link"
      message, whether or not it matched -- so the form can't be used to
      find out which usernames/emails exist.
  POST /password/reset {"token", "password"}
      Sets the new password. The token is signed with the app secret and
      carries a fingerprint of the current password hash, so it stops
      working the moment the password changes (single use) and after
      RESET_MAX_AGE_SECONDS.

app.py registers this with one call and hands over what it owns (DB
access, the hashing function and the plain-email sender)."""
from __future__ import annotations

import hashlib
import threading
import time

from flask import Blueprint, jsonify, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

RESET_MAX_AGE_SECONDS = 3600
MIN_PASSWORD_LENGTH = 6  # same rule as /register
THROTTLE_SECONDS = 60  # at most one email per account per minute
GENERIC_REPLY = "If that account exists, we've emailed a reset link to its address. It works for 1 hour."

_last_sent: dict = {}
_lock = threading.Lock()


def _fingerprint(password_hash: str) -> str:
    return hashlib.sha256((password_hash or "").encode()).hexdigest()[:16]


def create_password_reset_blueprint(*, secret_key, get_db, dict_cursor, generate_password_hash,
                                    send_plain_email, public_base_url):
    bp = Blueprint("password_reset", __name__)
    signer = URLSafeTimedSerializer(secret_key, salt="throughline-password-reset")

    @bp.route("/password/forgot", methods=["POST"])
    def forgot_password():
        data = request.get_json(silent=True) or {}
        ident = (data.get("identifier") or "").strip()
        if not ident:
            return jsonify({"error": "Enter your username or email"}), 400
        cur = dict_cursor(get_db())
        cur.execute(
            "SELECT id, username, email, password_hash FROM users "
            "WHERE username = %s OR lower(email) = lower(%s) ORDER BY id LIMIT 1",
            (ident, ident),
        )
        row = cur.fetchone()
        if row and row["email"]:
            now = time.monotonic()
            with _lock:
                recent = now - _last_sent.get(row["id"], -THROTTLE_SECONDS) < THROTTLE_SECONDS
                if not recent:
                    _last_sent[row["id"]] = now
            if not recent:
                token = signer.dumps({"uid": row["id"], "fp": _fingerprint(row["password_hash"])})
                link = f"{public_base_url().rstrip('/')}/app?reset_token={token}"
                body = (
                    f"Hi {row['username']},\n\n"
                    "Someone asked to reset the password for your Throughline account. "
                    "To choose a new password, open this link (it works once, for 1 hour):\n\n"
                    f"{link}\n\n"
                    "If you didn't ask for this, ignore this email -- your password stays the same."
                )
                # In the background: a slow SMTP round trip only for real
                # accounts would otherwise reveal which ones exist.
                uid, to = row["id"], row["email"]

                def _send():
                    try:
                        send_plain_email(to, "Reset your Throughline password", body)
                    except Exception as e:
                        print(f"[password_reset] email failed for user {uid}: {e!r}", flush=True)
                threading.Thread(target=_send, daemon=True).start()
        return jsonify({"ok": True, "message": GENERIC_REPLY})

    @bp.route("/password/reset", methods=["POST"])
    def reset_password():
        data = request.get_json(silent=True) or {}
        token = data.get("token") or ""
        password = data.get("password") or ""
        if len(password) < MIN_PASSWORD_LENGTH:
            return jsonify({"error": f"Password must be at least {MIN_PASSWORD_LENGTH} characters"}), 400
        try:
            payload = signer.loads(token, max_age=RESET_MAX_AGE_SECONDS)
        except SignatureExpired:
            return jsonify({"error": "This reset link has expired. Ask for a new one."}), 400
        except BadSignature:
            return jsonify({"error": "This reset link isn't valid. Ask for a new one."}), 400
        db = get_db()
        cur = dict_cursor(db)
        cur.execute("SELECT id, password_hash FROM users WHERE id = %s", (payload.get("uid"),))
        row = cur.fetchone()
        if not row or _fingerprint(row["password_hash"]) != payload.get("fp"):
            return jsonify({"error": "This reset link has already been used. Ask for a new one."}), 400
        cur.execute("UPDATE users SET password_hash = %s WHERE id = %s", (generate_password_hash(password), row["id"]))
        db.commit()
        return jsonify({"ok": True, "message": "Password changed. You can log in now."})

    return bp
