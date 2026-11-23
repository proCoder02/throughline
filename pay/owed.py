"""You owe: open tasks that are money the user has promised to pay.

Tasks come from conversations, so their wording is loose ("send Ravi 500
for the tickets", "return forty rupees to puja") and the owner field is a
mix of names and speaker labels. A cheap word filter shortlists likely money
tasks; the AI then decides, once per task, whether it's a payment the user
must make, how much and to whom. That answer is kept (pay.task_money), so a
task is never sent to the AI twice."""
from __future__ import annotations

import json
import re

from . import payees

MONEY_WORDS = re.compile(
    r"₹|\brs\.?\s?\d|\binr\b|rupee|\bpay|\bpaid\b|\bsend\b.*\d|\btransfer|\bowe|\breturn\b.*(money|cash|\d|rupee)|"
    r"\breimburse|\bsettle|\bupi\b|\bgpay|\bphonepe|\bpaytm|\bfees?\b|\brent\b|\bbill\b|\bdues?\b|\bemi\b|"
    r"\b(lakh|thousand|hundred)\b|\bmoney\b|\bcash\b|"
    # Hinglish: bhejna/dena/lautana (send/give/return), paise/rupaye, udhaar (loan), chukana (settle)
    r"\bbhej|\bpaise?\b|\bpaisa\b|\brupaye|\budhaa?r|\bchuka|\blauta|\bwapas\b.*\d|\b\d+\s*(de|dena|dene|dedo)\b",
    re.I,
)
BATCH = 20


def _classify(call_llm, username: str, tasks: list) -> dict:
    """{task_id: {pay, amount, to, for}} for these tasks; {} if the AI fails."""
    items = [{"id": t["id"], "task": t["description"], "owner": t["owner"]} for t in tasks]
    prompt = (
        f"The user is called {username!r}. Each item is a to-do picked out of the user's own conversations; "
        "'owner' is who should do it (a name, a speaker label like 'Speaker 0', or null when unclear).\n"
        "For each item decide: is it MONEY THE USER HAS TO PAY someone (pay, send, transfer, return, settle, "
        "a bill or fee they must pay)? Not money someone owes the user, not a purchase idea, not someone "
        "else's payment. If the owner is clearly another person, it's false.\n"
        "Reply with JSON only: a list of {\"id\": <id>, \"pay\": true|false, \"amount\": <number in rupees or null>, "
        "\"to\": <who gets paid, short, or null>, \"for\": <what it's for, 2-5 words, or null>}. "
        "Turn written amounts into numbers ('forty rupees' -> 40, '2 lakh' -> 200000).\n\n"
        + json.dumps(items, ensure_ascii=False)
    )
    try:
        reply = call_llm([{"role": "user", "content": prompt}]) or ""
        m = re.search(r"\[.*\]", reply, re.S)
        rows = json.loads(m.group(0)) if m else []
    except Exception as e:  # noqa: BLE001 -- the list just shows what's already classified
        print(f"[pay] owed classification failed: {e!r}")
        return {}
    out = {}
    for r in rows:
        if not isinstance(r, dict) or not isinstance(r.get("id"), int):
            continue
        amount = r.get("amount")
        out[r["id"]] = {
            "pay": bool(r.get("pay")),
            "amount": float(amount) if isinstance(amount, (int, float)) and 0 < amount < 1e8 else None,
            "to": (str(r["to"]).strip()[:80] or None) if r.get("to") else None,
            "for": (str(r["for"]).strip()[:80] or None) if r.get("for") else None,
        }
    return out


def list_owed(cur, conn, user_id: int, call_llm) -> dict:
    cur.execute("SELECT username FROM users WHERE id = %s", (user_id,))
    row = cur.fetchone()
    username = row["username"] if row else "the user"
    cur.execute(
        "SELECT t.id, t.description, t.owner FROM tasks t "
        "LEFT JOIN pay.task_money m ON m.task_id = t.id "
        "WHERE t.user_id = %s AND t.status = 'open' AND m.task_id IS NULL "
        "ORDER BY t.created_at DESC LIMIT 200",
        (user_id,),
    )
    fresh = [t for t in cur.fetchall() if MONEY_WORDS.search(t["description"] or "")]
    pending = 0
    if fresh:
        batch, pending = fresh[:BATCH], max(0, len(fresh) - BATCH)
        decided = _classify(call_llm, username, batch) if call_llm else {}
        for t in batch:
            d = decided.get(t["id"])
            if d is None:
                pending += 1  # the AI didn't answer for it: try again next time
                continue
            cur.execute(
                "INSERT INTO pay.task_money (task_id, user_id, is_payment, amount, person, reason) "
                "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (task_id) DO NOTHING",
                (t["id"], user_id, d["pay"], d["amount"], d["to"], d["for"]),
            )
        conn.commit()

    cur.execute(
        "SELECT t.id, t.description, t.due_date, t.created_at, m.amount, m.person, m.reason, "
        "c.title AS source_title, c.created_at AS source_at "
        "FROM pay.task_money m JOIN tasks t ON t.id = m.task_id "
        "LEFT JOIN conversations c ON c.id = t.conversation_id "
        "WHERE m.user_id = %s AND m.is_payment AND NOT m.dismissed AND t.status = 'open' "
        "ORDER BY t.created_at DESC LIMIT 50",
        (user_id,),
    )
    items = []
    for r in cur.fetchall():
        items.append({
            "task_id": r["id"],
            "text": r["description"],
            "amount": float(r["amount"]) if r["amount"] is not None else None,
            "person": r["person"],
            "reason": r["reason"],
            "due": r["due_date"],
            "source_title": r["source_title"],
            "source_at": r["source_at"].isoformat() if r["source_at"] else None,
            "created_at": r["created_at"].isoformat(),
            "suggestion": payees.find_by_person(cur, user_id, r["person"]),
        })
    return {"items": items, "pending": pending}


def dismiss(cur, user_id: int, task_id: int) -> bool:
    """'Not money I owe': hide it from the list (the task itself is untouched)."""
    cur.execute("UPDATE pay.task_money SET dismissed = TRUE WHERE task_id = %s AND user_id = %s", (task_id, user_id))
    return cur.rowcount > 0
