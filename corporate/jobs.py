"""Background work, every 15 minutes: overdue marking, extracting
commitments from chat, the evidence check, the risk radar, and the 9am
daily brief push. Each step is bounded so a big backlog can't run up LLM use."""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone

from . import ai, chat, insights, ledger

INTERVAL = 15 * 60
CHAT_MIN_NEW = 5            # new messages before a thread is read for commitments
EVIDENCE_BATCH = 20
RADAR_BATCH = 10
IST = timezone(timedelta(hours=5, minutes=30))
_STOP = set("the a an and or to of for in on at by with from this that will be is are was we our your their it its "
            "into about after before need needs needed".split())


def mark_overdue(cur) -> int:
    cur.execute("UPDATE corp.commitments SET status = 'overdue', updated_at = now() WHERE status = 'open' "
                "AND due_date < (now() AT TIME ZONE 'Asia/Kolkata')::date RETURNING id")
    ids = [r["id"] for r in cur.fetchall()]
    from . import deps
    for cid in ids:
        ledger.event(cur, cid, None, "overdue")
        deps.evaluate_parent_of(cur, cid)  # a late supporting item puts the promise it supports at risk
    return len(ids)


def extract_chats(cur, call_llm) -> int:
    cur.execute("SELECT t.id, t.org_id, t.unit_id, t.extracted_upto FROM corp.threads t WHERE "
                "(SELECT count(*) FROM corp.messages m WHERE m.thread_id = t.id AND m.id > t.extracted_upto AND m.kind = 'text') >= %s "
                "LIMIT 10", (CHAT_MIN_NEW,))
    n = 0
    for t in cur.fetchall():
        cur.execute("SELECT m.id, us.username, m.body FROM corp.messages m JOIN users us ON us.id = m.sender_id "
                    "WHERE m.thread_id = %s AND m.id > %s AND m.kind = 'text' ORDER BY m.id LIMIT 150", (t["id"], t["extracted_upto"]))
        rows = cur.fetchall()
        text = "\n".join(f"{r['username']}: {r['body']}" for r in rows)
        people = list(ledger.org_people(cur, t["org_id"]).keys())
        result = ai.extract(call_llm, text, people, ledger.current_decisions(cur, t["org_id"], t["unit_id"]), summary=False)
        ledger.ingest(cur, t["org_id"], t["unit_id"], "thread", t["id"], result)
        cur.execute("UPDATE corp.threads SET extracted_upto = %s WHERE id = %s", (rows[-1]["id"], t["id"]))
        n += 1
    return n


def _activity_since(cur, c, since) -> str:
    """New chat and meetings in the commitment's own scope since `since`."""
    parts = []
    if c["unit_id"]:
        cur.execute("SELECT us.username, m.body FROM corp.messages m JOIN corp.threads t ON t.id = m.thread_id "
                    "JOIN users us ON us.id = m.sender_id WHERE t.unit_id = %s AND m.created_at > %s ORDER BY m.id LIMIT 80",
                    (c["unit_id"], since))
        parts += [f"{r['username']}: {r['body']}" for r in cur.fetchall()]
        cur.execute("SELECT title, summary, transcript FROM corp.meetings WHERE unit_id = %s AND status = 'ready' "
                    "AND created_at > %s ORDER BY id LIMIT 3", (c["unit_id"], since))
        parts += [f"Meeting {r['title']}: {r['summary']}\n{ai.clip(r['transcript'], 1500)}" for r in cur.fetchall()]
    from . import mail
    parts += mail.recent_for(cur, [c["owner_id"], c["owed_to_id"]], since)  # opted-in work email
    if c["owner_id"] and c["owed_to_id"]:
        lo, hi = sorted((c["owner_id"], c["owed_to_id"]))
        cur.execute("SELECT us.username, m.body FROM corp.messages m JOIN corp.threads t ON t.id = m.thread_id "
                    "JOIN users us ON us.id = m.sender_id WHERE t.dm_key = %s AND m.created_at > %s ORDER BY m.id LIMIT 40",
                    (f"{c['org_id']}:{lo}:{hi}", since))
        parts += [f"{r['username']}: {r['body']}" for r in cur.fetchall()]
    return "\n".join(parts)


def check_evidence(cur, call_llm, push=None) -> int:
    cur.execute("SELECT c.*, COALESCE(o.username, c.owner_name) AS owner FROM corp.commitments c "
                "LEFT JOIN users o ON o.id = c.owner_id WHERE c.status IN ('open','overdue') "
                "ORDER BY c.checked_at NULLS FIRST LIMIT %s", (EVIDENCE_BATCH,))
    closed = 0
    for c in cur.fetchall():
        since = c["checked_at"] or c["created_at"]
        activity = _activity_since(cur, c, since)
        cur.execute("UPDATE corp.commitments SET checked_at = now() WHERE id = %s", (c["id"],))
        if not activity.strip():
            continue  # nothing new: no LLM call
        done, quote = ai.evidence(call_llm, c["text"], c["owner"], activity)
        if done:
            cur.execute("UPDATE corp.commitments SET status = 'done', evidence = %s, updated_at = now() WHERE id = %s",
                        (quote or "Evidence found in later activity", c["id"]))
            ledger.event(cur, c["id"], None, "done", evidence=quote, by="evidence check")
            closed += 1
            if push and c["owed_to_id"]:
                push(c["owed_to_id"], "Commitment done", f"{c['owner'] or 'Someone'}: {c['text'][:90]}", {"type": "corp_commitment", "org_id": c["org_id"], "commitment_id": c["id"]})
    return closed


def _words(text) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 3 and w not in _STOP}


def radar(cur, call_llm, push=None) -> int:
    cur.execute("SELECT b.*, un.name AS unit_name FROM corp.blockers b LEFT JOIN corp.units un ON un.id = b.unit_id "
                "WHERE b.status = 'open' AND (b.scanned_at IS NULL OR EXISTS (SELECT 1 FROM corp.commitments c "
                "WHERE c.org_id = b.org_id AND c.created_at > b.scanned_at)) LIMIT %s", (RADAR_BATCH,))
    found = 0
    for b in cur.fetchall():
        cur.execute("SELECT c.id, c.text, c.due_date, c.owner_id, c.owed_to_id FROM corp.commitments c WHERE c.org_id = %s "
                    "AND c.status IN ('open','overdue','detected') AND c.due_date IS NOT NULL "
                    "AND c.unit_id IS DISTINCT FROM %s AND (%s IS NULL OR c.created_at > %s)",
                    (b["org_id"], b["unit_id"], b["scanned_at"], b["scanned_at"]))
        bw = _words(b["text"])
        cands = [c for c in cur.fetchall() if bw & _words(c["text"])]  # keyword prefilter: no LLM call without overlap
        cur.execute("UPDATE corp.blockers SET scanned_at = now() WHERE id = %s", (b["id"],))
        if not cands:
            continue
        ids, why = ai.radar(call_llm, b["text"], cands)
        for c in [c for c in cands if c["id"] in ids]:
            cur.execute("INSERT INTO corp.risks (org_id, commitment_id, blocker_id, why) VALUES (%s,%s,%s,%s) "
                        "ON CONFLICT DO NOTHING RETURNING id", (b["org_id"], c["id"], b["id"], why))
            if not cur.fetchone():
                continue
            found += 1
            ledger.event(cur, c["id"], None, "at_risk", blocker_unit=b["unit_name"])
            if push:
                due = c["due_date"].strftime("%d %b")
                for uid in {c["owner_id"], c["owed_to_id"]} - {None}:
                    push(uid, "May slip", f"“{c['text'][:70]}”, due {due}: a blocker was logged in {b['unit_name']}.",
                         {"type": "corp_risk", "org_id": b["org_id"], "commitment_id": c["id"]})
                cur.execute("SELECT user_id FROM corp.unit_members WHERE unit_id = %s AND role = 'manager'", (b["unit_id"],))
                for r in cur.fetchall():
                    push(r["user_id"], "Your blocker affects another team",
                         f"“{b['text'][:60]}” may delay a commitment due {due}.", {"type": "corp_risk", "org_id": b["org_id"]})
    return found


def daily_brief(cur, push) -> int:
    now = datetime.now(IST)
    if now.hour < 9 or not push:
        return 0
    today = now.date().isoformat()
    cur.execute("SELECT value FROM corp.job_state WHERE key = 'brief_day'")
    row = cur.fetchone()
    if row and row["value"] == today:
        return 0
    cur.execute("INSERT INTO corp.job_state VALUES ('brief_day', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (today,))
    cur.execute("SELECT org_id, user_id FROM corp.members")
    sent = 0
    for m in cur.fetchall():
        b = insights.brief(cur, m["org_id"], m["user_id"])
        owe = len(b["owe"]); overdue = sum(1 for c in b["owe"] if c["status"] == "overdue")
        if not (owe or b["owed"] or b["risks"]):
            continue
        bits = [f"you owe {owe}" + (f" ({overdue} overdue)" if overdue else ""), f"owed to you {len(b['owed'])}"]
        if b["risks"]:
            bits.append(f"{len(b['risks'])} at risk")
        push(m["user_id"], "Your work brief", ", ".join(bits), {"type": "corp_brief", "org_id": str(m["org_id"])})
        sent += 1
    return sent


def weekly_digest(cur, push) -> int:
    """Mondays after 09:00 IST: each person on a client project (stakeholders too)
    gets one notification pointing to this week's client-projects digest."""
    now = datetime.now(IST)
    if now.weekday() != 0 or now.hour < 9 or not push:
        return 0
    week = now.strftime("%G-W%V")
    cur.execute("SELECT DISTINCT p.org_id, pm.user_id FROM corp.project_members pm JOIN corp.projects p ON p.id = pm.project_id "
                "WHERE p.status = 'active' UNION SELECT DISTINCT c.org_id, c.owner_id FROM corp.commitments c "
                "WHERE c.project_id IS NOT NULL AND c.supports_id IS NULL AND c.owner_id IS NOT NULL")
    sent = 0
    for m in cur.fetchall():
        cur.execute("SELECT 1 FROM corp.digest_sent WHERE org_id = %s AND user_id = %s AND week = %s", (m["org_id"], m["user_id"], week))
        if cur.fetchone():
            continue
        d = insights.client_digest(cur, m["org_id"], m["user_id"])
        cur.execute("INSERT INTO corp.digest_sent (org_id, user_id, week) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                    (m["org_id"], m["user_id"], week))
        if not d["projects"]:
            continue
        t = d["totals"]
        bits = [f"{len(d['projects'])} client project{'s' if len(d['projects']) != 1 else ''}"]
        if t["at_risk"]:
            bits.append(f"{t['at_risk']} at risk")
        if t["due_soon"]:
            bits.append(f"{t['due_soon']} due in 2 weeks")
        if t["tickets_done"]:
            bits.append(f"{t['tickets_done']} tickets done")
        push(m["user_id"], "Your client projects this week", " · ".join(bits), {"type": "corp_digest", "org_id": str(m["org_id"])})
        sent += 1
    return sent


# Each step is its own job on the ops dashboard (ops/jobs.py lists them under "Corporate").
STEPS = {
    "corp_overdue": lambda cur, llm, push: mark_overdue(cur),
    "corp_chat_extract": lambda cur, llm, push: extract_chats(cur, llm),
    "corp_evidence": lambda cur, llm, push: check_evidence(cur, llm, push),
    "corp_radar": lambda cur, llm, push: radar(cur, llm, push),
    "corp_daily_brief": lambda cur, llm, push: daily_brief(cur, push),
    "corp_mail": lambda cur, llm, push: _mail_scan(cur, llm),
    "corp_tickets": lambda cur, llm, push: _tickets(cur, llm, push),
    "corp_weekly_digest": lambda cur, llm, push: weekly_digest(cur, push),
}
_mail_handler = None  # set by start(): the host's mail tool lookup


def _tickets(cur, llm, push):
    from . import tickets
    return tickets.run_job(cur, llm, push)


def _mail_scan(cur, llm):
    from . import mail
    return mail.scan(cur, cur.connection, llm, _mail_handler)


def run_step(conn, cur, name, call_llm, push=None, report=None, paused=None, force=False):
    """One step, reported to ops as one job run. report(cur, conn, job_name,
    started_at, status, detail) and paused(job_name) are the host's ops hooks;
    without them the step just runs. force=True ignores Pause (dashboard "Run now")."""
    started = datetime.now(timezone.utc)
    if not force and paused and paused(name):
        if report:
            report(cur, conn, name, started, "skipped", {"paused": True})
        return "paused"
    try:
        n = STEPS[name](cur, call_llm, push)
        conn.commit()
        if report:
            report(cur, conn, name, started, "success", {"handled": n})
        return n
    except Exception as e:  # noqa: BLE001 -- one step failing must not stop the others
        conn.rollback()
        if report:
            report(cur, conn, name, started, "error", {"error": str(e)[:300]})
        return f"error: {e!r}"[:200]


def run_once(conn, cur, call_llm, push=None, report=None, paused=None) -> dict:
    return {name: run_step(conn, cur, name, call_llm, push, report, paused) for name in STEPS}


def start(get_raw_connection, release_raw_connection, dict_cursor, call_llm, push=None, spawn=None,
          report=None, paused=None, register_runner=None, mail_handler=None) -> None:
    global _mail_handler
    _mail_handler = mail_handler
    from .schema import ensure_corp_schema

    def with_conn(fn):
        conn = None
        try:
            conn = get_raw_connection()
            cur = dict_cursor(conn)
            ensure_corp_schema(cur, conn)
            return fn(conn, cur)
        finally:
            if conn is not None:
                release_raw_connection(conn)

    def loop():
        time.sleep(30)
        while True:
            try:
                result = with_conn(lambda conn, cur: run_once(conn, cur, call_llm, push, report, paused))
                if any(v for v in result.values()):
                    print(f"[corporate] jobs: {result}")
            except Exception as e:  # noqa: BLE001
                print(f"[corporate] jobs failed: {e!r}")
            time.sleep(INTERVAL)

    # Dashboard "Run now": one step, immediately, even if paused.
    if register_runner:
        for name in STEPS:
            register_runner(name, lambda name=name: with_conn(
                lambda conn, cur: run_step(conn, cur, name, call_llm, push, report, paused, force=True)))
    (spawn or (lambda f: __import__("threading").Thread(target=f, daemon=True).start()))(loop)
