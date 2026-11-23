"""Throughline Pay memory -- the part of Pay only Throughline can do.

The UPI app still moves the money (the user approves with their PIN there);
Throughline never touches funds or bank details. This module remembers:

  GET  /pay/owed                     money you promised in conversations ("You owe")
  POST /pay/owed/<task_id>/dismiss   "not money I owe" -- hides it, task untouched
  GET  /pay/payee?vpa=&name=         what you know about a UPI ID before paying it
  PUT  /pay/payee                    your own name for a payee {vpa, label}
  GET  /pay/recent                   people and shops you pay, most recent first
  POST /pay/record                   a payment the user confirmed went through

/pay/record writes the same transactions row POST /transactions always did
(so spending views and Ask are unchanged), plus a pay.payments row keyed by
UPI ID, and closes the promise it paid (task -> done) when there is one.

Wiring (app.py): app.register_blueprint(create_pay_blueprint(...)); no other
app.py changes. Tables: pay/schema.py, created on first use."""
from __future__ import annotations

import re

from flask import Blueprint, jsonify, request

from . import owed, payees, schema

VPA = re.compile(r"^[A-Za-z0-9._-]{2,256}@[A-Za-z][A-Za-z0-9.-]{1,64}$")
MAX_AMOUNT = 1_000_000  # well above any UPI limit; only guards against typos


def create_pay_blueprint(*, get_db, dict_cursor, login_required, current_user_id, call_llm=None,
                         normalize_payment_method=None):
    bp = Blueprint("pay", __name__)

    def _cur():
        db = get_db()
        cur = dict_cursor(db)
        schema.ensure(cur, db)
        return db, cur

    @bp.route("/pay/owed", methods=["GET"])
    @login_required
    def get_owed():
        db, cur = _cur()
        return jsonify(owed.list_owed(cur, db, current_user_id(), call_llm))

    @bp.route("/pay/owed/<int:task_id>/dismiss", methods=["POST"])
    @login_required
    def dismiss_owed(task_id):
        db, cur = _cur()
        ok = owed.dismiss(cur, current_user_id(), task_id)
        db.commit()
        return (jsonify({"ok": True}), 200) if ok else (jsonify({"error": "Not found"}), 404)

    @bp.route("/pay/payee", methods=["GET"])
    @login_required
    def get_payee():
        vpa = (request.args.get("vpa") or "").strip()
        if not VPA.match(vpa):
            return jsonify({"error": "A valid UPI ID is required"}), 400
        _, cur = _cur()
        return jsonify(payees.memory(cur, current_user_id(), vpa, (request.args.get("name") or "").strip() or None))

    @bp.route("/pay/payee", methods=["PUT"])
    @login_required
    def put_payee():
        data = request.get_json(silent=True) or {}
        vpa = (data.get("vpa") or "").strip()
        if not VPA.match(vpa):
            return jsonify({"error": "A valid UPI ID is required"}), 400
        db, cur = _cur()
        payees.set_label(cur, current_user_id(), vpa, data.get("label"))
        db.commit()
        return jsonify({"ok": True})

    @bp.route("/pay/recent", methods=["GET"])
    @login_required
    def get_recent():
        _, cur = _cur()
        return jsonify({"payees": payees.recent(cur, current_user_id())})

    @bp.route("/pay/record", methods=["POST"])
    @login_required
    def record():
        data = request.get_json(silent=True) or {}
        vpa = (data.get("vpa") or "").strip()
        amount = data.get("amount")
        if not VPA.match(vpa):
            return jsonify({"error": "A valid UPI ID is required"}), 400
        if not isinstance(amount, (int, float)) or not 0 < amount <= MAX_AMOUNT:
            return jsonify({"error": "amount must be a positive number"}), 400
        name = (data.get("payee_name") or "").strip()[:120] or None
        note = (data.get("note") or "").strip()[:200] or None
        app_name = (data.get("app") or "").strip()[:40] or None
        method = normalize_payment_method(app_name) if (normalize_payment_method and app_name) else app_name
        task_id = data.get("task_id") if isinstance(data.get("task_id"), int) else None
        user_id = current_user_id()
        db, cur = _cur()

        # Same accidental-duplicate window as POST /transactions (a double tap,
        # or answering "Yes, paid" twice).
        cur.execute(
            "SELECT id, transaction_id FROM pay.payments WHERE user_id = %s AND lower(vpa) = lower(%s) AND amount = %s "
            "AND created_at >= now() - INTERVAL '10 minutes' ORDER BY created_at DESC LIMIT 1",
            (user_id, vpa, float(amount)),
        )
        dup = cur.fetchone()
        if dup:
            return jsonify({"id": dup["id"], "transaction_id": dup["transaction_id"], "duplicate": True, "closed_task": None})

        label = (data.get("label") or "").strip() or None
        if label:
            payees.set_label(cur, user_id, vpa, label)
        cur.execute("SELECT label FROM pay.payees WHERE user_id = %s AND lower(vpa) = lower(%s)", (user_id, vpa))
        lrow = cur.fetchone()
        shown = (lrow["label"] if lrow and lrow["label"] else None) or name or vpa

        cur.execute(
            "INSERT INTO transactions (user_id, amount, payee, payment_method, item) VALUES (%s,%s,%s,%s,%s) RETURNING id",
            (user_id, float(amount), shown, method, note),
        )
        txn_id = cur.fetchone()["id"]

        closed = None
        if task_id is not None:
            cur.execute("SELECT id, description, status FROM tasks WHERE id = %s AND user_id = %s", (task_id, user_id))
            t = cur.fetchone()
            if t:
                if t["status"] != "done":
                    cur.execute("UPDATE tasks SET status = 'done' WHERE id = %s", (task_id,))
                closed = {"task_id": t["id"], "text": t["description"]}
            else:
                task_id = None
        cur.execute(
            "INSERT INTO pay.payments (user_id, vpa, payee_name, amount, note, app, transaction_id, task_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (user_id, vpa.lower(), name, float(amount), note, method, txn_id, task_id),
        )
        pid = cur.fetchone()["id"]
        db.commit()
        return jsonify({"id": pid, "transaction_id": txn_id, "duplicate": False, "closed_task": closed})

    return bp
