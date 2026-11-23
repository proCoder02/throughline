"""Payee memory: what Throughline knows about a UPI ID before you pay it --
how often you've paid, how much usually, when last, what for, your own name
for them, and whether anything looks off (a first-time payee, or a QR whose
name doesn't match the name you paid before)."""
from __future__ import annotations

from collections import Counter


def _norm(name):
    return " ".join((name or "").lower().split())


def memory(cur, user_id: int, vpa: str, shown_name: str | None = None) -> dict:
    cur.execute("SELECT label FROM pay.payees WHERE user_id = %s AND lower(vpa) = lower(%s)", (user_id, vpa))
    row = cur.fetchone()
    label = row["label"] if row else None
    cur.execute(
        "SELECT amount, note, payee_name, created_at FROM pay.payments "
        "WHERE user_id = %s AND lower(vpa) = lower(%s) ORDER BY created_at DESC LIMIT 100",
        (user_id, vpa),
    )
    rows = cur.fetchall()
    if not rows:
        return {"vpa": vpa, "label": label, "times_paid": 0, "new_payee": True, "warnings": [
            "First time paying this UPI ID. Check the name matches who you mean to pay."]}

    amounts = [float(r["amount"]) for r in rows]
    common, count = Counter(amounts).most_common(1)[0]
    last = rows[0]
    notes = []
    for r in rows:
        n = (r["note"] or "").strip()
        if n and n.lower() not in (x.lower() for x in notes):
            notes.append(n)
        if len(notes) == 3:
            break
    warnings = []
    before = last["payee_name"]
    if shown_name and before and _norm(shown_name) != _norm(before):
        warnings.append(f"This QR shows “{shown_name}”, but you paid this UPI ID as “{before}” before. Check before paying.")
    return {
        "vpa": vpa,
        "label": label,
        "times_paid": len(rows),
        "total_paid": round(sum(amounts), 2),
        "usual_amount": common if count >= 2 else None,
        "last": {"amount": float(last["amount"]), "at": last["created_at"].isoformat(), "note": last["note"]},
        "first_paid_at": rows[-1]["created_at"].isoformat(),
        "recent_notes": notes,
        "new_payee": False,
        "warnings": warnings,
    }


def set_label(cur, user_id: int, vpa: str, label: str | None) -> None:
    label = (label or "").strip()[:60] or None
    cur.execute(
        "INSERT INTO pay.payees (user_id, vpa, label) VALUES (%s, %s, %s) "
        "ON CONFLICT (user_id, vpa) DO UPDATE SET label = EXCLUDED.label, updated_at = now()",
        (user_id, vpa.strip().lower(), label),
    )


def recent(cur, user_id: int, limit: int = 8) -> list:
    """People and shops you pay, most recent first -- one row per UPI ID."""
    cur.execute(
        "SELECT DISTINCT ON (lower(p.vpa)) lower(p.vpa) AS vpa, COALESCE(l.label, p.payee_name, p.vpa) AS name, "
        "p.amount, p.note, p.created_at FROM pay.payments p "
        "LEFT JOIN pay.payees l ON l.user_id = p.user_id AND lower(l.vpa) = lower(p.vpa) "
        "WHERE p.user_id = %s ORDER BY lower(p.vpa), p.created_at DESC",
        (user_id,),
    )
    rows = sorted(cur.fetchall(), key=lambda r: r["created_at"], reverse=True)[:limit]
    return [{"vpa": r["vpa"], "name": r["name"], "last_amount": float(r["amount"]), "last_note": r["note"],
             "last_at": r["created_at"].isoformat()} for r in rows]


def find_by_person(cur, user_id: int, person: str | None):
    """A UPI ID you've paid before whose label or name matches a person
    named in a promise ("Ravi" -> ravi@okaxis), most recent first."""
    words = [w for w in (person or "").replace(".", " ").split() if len(w) >= 3]
    if not words:
        return None
    first = words[0]
    cur.execute(
        "SELECT p.vpa, COALESCE(l.label, p.payee_name) AS name FROM pay.payments p "
        "LEFT JOIN pay.payees l ON l.user_id = p.user_id AND lower(l.vpa) = lower(p.vpa) "
        "WHERE p.user_id = %s AND (l.label ILIKE %s OR p.payee_name ILIKE %s) "
        "ORDER BY p.created_at DESC LIMIT 1",
        (user_id, f"%{first}%", f"%{first}%"),
    )
    row = cur.fetchone()
    return {"vpa": row["vpa"], "name": row["name"]} if row else None
