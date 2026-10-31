"""HTTP API for Meet -- a Flask Blueprint built by create_meet_blueprint(),
which takes the host app's helpers instead of importing app.py.

  GET    /meet/card                      my card (a draft if never saved)
  PUT    /meet/card                      save / edit my card, switch sharing
  POST   /meet/card/suggest              draft traits + "how I communicate" (editor only; not saved)
  POST   /meet/session                   start broadcasting: code + tunables
  POST   /meet/encounter                 {code, seen_code, rssi} -> waiting | matched | too_far
  GET    /meet/encounter/<id>            status; the other card once both shared
  POST   /meet/encounter/<id>/confirm    {accept: true|false}
  POST   /meet/encounter/<id>/add-friend become friends after sharing
  DELETE /meet/encounter/<id>            stop sharing with this person
  GET    /meet/encounters                everyone I've met ("who has my card")
  POST   /meet/qr                        show-a-code: a short-lived QR payload
  GET    /meet/qr/<token>                for the phone showing it: scanned yet?
  POST   /meet/qr/scan                   {payload} -> matched (then the same confirm step)"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from . import cards, encounters
from .ephemeral import Ephemeral
from .schema import ensure_meet_schema


def create_meet_blueprint(*, get_db, dict_cursor, login_required, current_user_id, redis_client=None,
                          suggest_personality=None) -> Blueprint:
    """suggest_personality(user_id) -> {"traits": [...], "style": "..."} is
    optional: the host app's way of drafting the personality part of a card
    from what it knows. Without it, the suggest endpoint says it's
    unavailable and the rest of Meet is unaffected."""
    bp = Blueprint("meet", __name__)
    store = Ephemeral(redis_client)

    def db_cur():
        conn = get_db()
        cur = dict_cursor(conn)
        ensure_meet_schema(cur, conn)
        return conn, cur

    def encounter_view(cur, row, user_id) -> dict:
        status = encounters.status_of(row)
        other = encounters.other_side(row, user_id)
        view = {
            "id": row["id"],
            "status": status,
            "created_at": row["created_at"].isoformat(),
            "other": cards.identity(cur, other),
            "i_confirmed": encounters.i_confirmed(row, user_id),
            "they_confirmed": encounters.they_confirmed(row, user_id),
            "method": row.get("method") or "bluetooth",
        }
        if status == "pending":
            view["expires_in"] = encounters.seconds_left(row)
        if status == "shared":
            card = cards.public_card(cur, other)
            view["card"] = card
            view["card_hidden"] = card is None  # they switched sharing off afterwards
            view["in_common"] = cards.in_common(cards.get_saved(cur, user_id), card)
        return view

    @bp.route("/meet/card", methods=["GET"])
    @login_required
    def get_card():
        conn, cur = db_cur()
        user_id = current_user_id()
        saved = cards.get_saved(cur, user_id)
        return jsonify({
            "saved": bool(saved),
            "card": saved or cards.draft(cur, user_id),
            "me": cards.identity(cur, user_id),
            "trait_options": cards.TRAIT_OPTIONS,
            "colors": cards.COLORS,
            "can_suggest": suggest_personality is not None,
        })

    @bp.route("/meet/card", methods=["PUT"])
    @login_required
    def put_card():
        conn, cur = db_cur()
        card = cards.save(cur, current_user_id(), request.get_json(silent=True) or {})
        conn.commit()
        return jsonify({"saved": True, "card": card})

    @bp.route("/meet/card/suggest", methods=["POST"])
    @login_required
    def suggest_card():
        if suggest_personality is None:
            return jsonify({"error": "Suggestions aren't available right now."}), 503
        try:
            suggestion = cards.clean_suggestion(suggest_personality(current_user_id()))
        except Exception as e:
            print(f"[meet] suggest failed: {e!r}")
            return jsonify({"error": "Couldn't make a suggestion right now. Try again in a moment."}), 503
        if not suggestion["traits"] and not suggestion["style"]:
            return jsonify({"error": "Throughline doesn't know enough about you yet to suggest this. Add it yourself."}), 422
        return jsonify(suggestion)

    @bp.route("/meet/session", methods=["POST"])
    @login_required
    def start_session():
        conn, cur = db_cur()
        user_id = current_user_id()
        return card_required(cur, user_id) or jsonify(encounters.start_session(store, user_id))

    @bp.route("/meet/encounter", methods=["POST"])
    @login_required
    def report():
        conn, cur = db_cur()
        data = request.get_json(silent=True) or {}
        result = encounters.report_sighting(cur, conn, store, current_user_id(), data.get("code"), data.get("seen_code"),
                                            data.get("rssi"))
        if result["status"] == "matched":
            row = encounters.get_for(cur, result["encounter_id"], current_user_id())
            result["encounter"] = encounter_view(cur, row, current_user_id())
        code = {"invalid": 409, "limited": 429}.get(result["status"], 200)
        return jsonify(result), code

    def card_required(cur, user_id):
        saved = cards.get_saved(cur, user_id)
        if not saved or not saved["sharing"]:
            return jsonify({"error": "card_required", "message": "Set up your card before using Meet."}), 409
        return None

    @bp.route("/meet/qr", methods=["POST"])
    @login_required
    def start_qr():
        conn, cur = db_cur()
        user_id = current_user_id()
        return card_required(cur, user_id) or jsonify(encounters.start_qr(store, user_id))

    @bp.route("/meet/qr/<token>", methods=["GET"])
    @login_required
    def qr_status(token):
        conn, cur = db_cur()
        user_id = current_user_id()
        result = encounters.qr_status(store, user_id, token)
        if result["status"] == "scanned":
            row = encounters.get_for(cur, result["encounter_id"], user_id)
            if not row:  # someone else's code id -- never leak it
                return jsonify({"status": "expired"})
            result["encounter"] = encounter_view(cur, row, user_id)
        return jsonify(result)

    @bp.route("/meet/qr/scan", methods=["POST"])
    @login_required
    def scan_qr():
        conn, cur = db_cur()
        user_id = current_user_id()
        blocked = card_required(cur, user_id)
        if blocked:
            return blocked
        result = encounters.scan_qr(cur, conn, store, user_id, (request.get_json(silent=True) or {}).get("payload"))
        if result["status"] == "matched":
            row = encounters.get_for(cur, result["encounter_id"], user_id)
            result["encounter"] = encounter_view(cur, row, user_id)
        code = {"expired": 410, "unknown": 400, "limited": 429}.get(result["status"], 200)
        return jsonify(result), code

    def load(encounter_id):
        conn, cur = db_cur()
        row = encounters.get_for(cur, encounter_id, current_user_id())
        return conn, cur, row

    @bp.route("/meet/encounter/<int:encounter_id>", methods=["GET"])
    @login_required
    def get_encounter(encounter_id):
        conn, cur, row = load(encounter_id)
        if not row:
            return jsonify({"error": "Not found"}), 404
        return jsonify(encounter_view(cur, row, current_user_id()))

    @bp.route("/meet/encounter/<int:encounter_id>/confirm", methods=["POST"])
    @login_required
    def confirm(encounter_id):
        conn, cur, row = load(encounter_id)
        if not row:
            return jsonify({"error": "Not found"}), 404
        if encounters.status_of(row) != "pending":
            return jsonify(encounter_view(cur, row, current_user_id()))
        accept = bool((request.get_json(silent=True) or {}).get("accept", True))
        encounters.confirm(cur, row, current_user_id(), accept)
        conn.commit()
        return jsonify(encounter_view(cur, encounters.get_for(cur, encounter_id, current_user_id()), current_user_id()))

    @bp.route("/meet/encounter/<int:encounter_id>/add-friend", methods=["POST"])
    @login_required
    def add_friend(encounter_id):
        conn, cur, row = load(encounter_id)
        user_id = current_user_id()
        if not row or encounters.status_of(row) != "shared":
            return jsonify({"error": "You can add a friend after you've both shared cards."}), 409
        other = encounters.other_side(row, user_id)
        # Same two-row shape POST /friends/add writes; existing rows are left as they are.
        cur.execute(
            "INSERT INTO friendships (user_id, friend_id) VALUES (%s, %s), (%s, %s) ON CONFLICT (user_id, friend_id) DO NOTHING",
            (user_id, other, other, user_id),
        )
        conn.commit()
        return jsonify({"ok": True, "friend": cards.identity(cur, other)})

    @bp.route("/meet/encounter/<int:encounter_id>", methods=["DELETE"])
    @login_required
    def remove(encounter_id):
        conn, cur, row = load(encounter_id)
        if not row:
            return jsonify({"error": "Not found"}), 404
        encounters.remove(cur, row, current_user_id())
        conn.commit()
        return jsonify({"ok": True})

    @bp.route("/meet/encounters", methods=["GET"])
    @login_required
    def list_encounters():
        conn, cur = db_cur()
        user_id = current_user_id()
        return jsonify([encounter_view(cur, r, user_id) for r in encounters.list_for(cur, user_id)])

    return bp
