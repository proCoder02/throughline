"""Work email -> commitments and evidence. Opt-in per person (corp.mail_links),
using the Gmail/Outlook mailbox they already connected in Personal. Every 15
minutes (job corp_mail) a few new sent and received emails are read:

  * promises in them ("I'll send the contract by Friday", "we'll deliver on the
    20th") become private commitments marked To confirm -- never added silently;
  * a short excerpt is kept so the evidence check can close commitments when a
    later email shows the work was done ("attached is the signed contract").

Email content is untrusted: it only ever becomes items the person confirms.
The host passes mail_handler(name) -> handler(cur, db, user_id, args) (the
chat assistant's existing mail tools), so this module imports no mail code."""
from __future__ import annotations

from . import ai, ledger

PROVIDERS = ("gmail", "outlook")
PER_PERSON_PER_RUN = 6     # bounded AI use per run
BODY_CAP = 3000


def scan(cur, conn, call_llm, mail_handler) -> int:
    if not mail_handler:
        return 0
    cur.execute("SELECT l.org_id, l.user_id FROM corp.mail_links l JOIN corp.members m ON m.org_id = l.org_id "
                "AND m.user_id = l.user_id AND m.status = 'active' WHERE l.enabled")
    found = 0
    for link in cur.fetchall():
        found += _scan_person(cur, conn, call_llm, mail_handler, link["org_id"], link["user_id"])
    return found


def _scan_person(cur, conn, call_llm, mail_handler, org_id, user_id) -> int:
    budget, found = PER_PERSON_PER_RUN, 0
    people = ledger.org_people(cur, org_id)
    for prov in PROVIDERS:
        lister, reader = mail_handler(f"{prov}_list_recent_emails"), mail_handler(f"{prov}_read_email")
        if not (lister and reader):
            continue
        for folder in ("sent", "inbox"):
            res = lister(cur, conn, user_id, {"folder": folder, "days": 2, "limit": 15})
            if not isinstance(res, dict) or res.get("error"):
                break  # not connected / needs reconnect -- try the other provider
            for e in res.get("emails") or []:
                if budget <= 0:
                    return found
                if e.get("promotional"):
                    continue
                key = f"{prov}:{e.get('id')}"
                cur.execute("SELECT 1 FROM corp.mail_items WHERE org_id = %s AND user_id = %s AND message_key = %s",
                            (org_id, user_id, key))
                if cur.fetchone():
                    continue
                full = reader(cur, conn, user_id, {"message_id": e.get("id")})
                if not isinstance(full, dict) or full.get("error"):
                    continue
                body = (full.get("body") or "")[:BODY_CAP]
                cur.execute("INSERT INTO corp.mail_items (org_id, user_id, message_key, folder, subject, sender, excerpt) "
                            "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id",
                            (org_id, user_id, key, folder, (full.get("subject") or "")[:300], (full.get("from") or "")[:200], body[:800]))
                row = cur.fetchone()
                if row:  # "PAY-12" in the subject or body tags the email to that ticket
                    from . import tickets
                    tickets.link_by_keys(cur, org_id, user_id, "email", row["id"], f"{full.get('subject') or ''}\n{body}")
                conn.commit()
                budget -= 1
                if not row:
                    continue
                text = (f"{'Email I sent' if folder == 'sent' else 'Email I received'}\nFrom: {full.get('from')}\n"
                        f"To: {', '.join(full.get('to') or [])}\nSubject: {full.get('subject')}\n\n{body}")
                result = ai.extract(call_llm, text, list(people.keys()), [], summary=False)
                for c in result.get("commitments", [])[:5]:
                    if not isinstance(c, dict) or not c.get("text"):
                        continue
                    owner = ledger.match_person(people, c.get("owner"))
                    owed = ledger.match_person(people, c.get("owed_to"))
                    if user_id not in (owner, owed) and folder == "inbox" and owner is None:
                        continue  # only promises involving this person
                    ledger.add_commitment(cur, org_id, None, str(c["text"]), owner_id=owner or (user_id if folder == "sent" else None),
                                          owner_name=c.get("owner"), owed_to_id=owed, owed_to_name=c.get("owed_to"),
                                          due=c.get("due"), source_type="email", source_id=row["id"], quote=c.get("quote"),
                                          actor_id=user_id)
                    found += 1
                conn.commit()
    return found


def recent_for(cur, user_ids, since, limit=10) -> list:
    """Email excerpts of these people since `since` -- evidence for the evidence check."""
    ids = [u for u in user_ids if u]
    if not ids:
        return []
    cur.execute("SELECT folder, subject, sender, excerpt FROM corp.mail_items WHERE user_id = ANY(%s) AND created_at > %s "
                "ORDER BY id DESC LIMIT %s", (ids, since, limit))
    return [f"Email ({r['folder']}) from {r['sender']}, “{r['subject']}”: {r['excerpt'][:500]}" for r in cur.fetchall()]
