"""Meetings: an uploaded or browser-recorded audio file, pasted notes, or an
existing recorded conversation (Listen / call) imported into a team.
Processing (transcribe -> extract -> ledger) runs in the background."""
from __future__ import annotations

from . import access, ai, ledger
from .errors import CorpError


def create(cur, org_id, unit_id, title, source, created_by, participants, transcript="") -> int:
    cur.execute("INSERT INTO corp.meetings (org_id, unit_id, title, source, transcript, created_by) "
                "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                (org_id, unit_id, (title or "Meeting")[:160], source, transcript, created_by))
    mid = cur.fetchone()["id"]
    for uid in set(participants) | {created_by}:
        cur.execute("INSERT INTO corp.meeting_participants VALUES (%s,%s) ON CONFLICT DO NOTHING", (mid, uid))
    return mid


def process(conn, cur, meeting_id: int, call_llm, transcribe=None, audio=None, content_type=None) -> None:
    """Background job. Never raises; a failure is recorded on the meeting."""
    try:
        cur.execute("SELECT * FROM corp.meetings WHERE id = %s", (meeting_id,))
        m = cur.fetchone()
        text = m["transcript"]
        if audio is not None:
            text, _segments = transcribe(audio, content_type)
            text = (text or "").strip()
            if not text:
                raise RuntimeError("No speech was found in the recording.")
            cur.execute("UPDATE corp.meetings SET transcript = %s WHERE id = %s", (text, meeting_id))
            conn.commit()
        people = list(ledger.org_people(cur, m["org_id"]).keys())
        result = ai.extract(call_llm, text, people, ledger.current_decisions(cur, m["org_id"], m["unit_id"]))
        ledger.ingest(cur, m["org_id"], m["unit_id"], "meeting", meeting_id, result, actor_id=m["created_by"],
                      meeting_id=meeting_id)
        cur.execute("UPDATE corp.meetings SET summary = %s, status = 'ready', error = NULL WHERE id = %s",
                    (result["summary"], meeting_id))
        conn.commit()
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        cur.execute("UPDATE corp.meetings SET status = 'failed', error = %s WHERE id = %s", (str(e)[:300], meeting_id))
        conn.commit()


def list_visible(cur, org_id, me, unit_id=None) -> list:
    extra = " AND x.unit_id = %(unit)s" if unit_id else ""
    cur.execute("SELECT x.id, x.title, x.unit_id, un.name AS unit_name, x.source, x.status, x.summary, x.created_at, "
                "us.username AS created_by_name FROM corp.meetings x LEFT JOIN corp.units un ON un.id = x.unit_id "
                f"LEFT JOIN users us ON us.id = x.created_by WHERE x.org_id = %(org)s AND {access.meetings()}{extra} "
                "ORDER BY x.created_at DESC LIMIT 200", {"org": org_id, "me": me, "unit": unit_id})
    return [dict(r) | {"created_at": r["created_at"].isoformat()} for r in cur.fetchall()]


def detail(cur, org_id, meeting_id, me) -> dict:
    m = access.one(cur, "meetings", access.meetings(), meeting_id, me, org_id)
    if not m:
        raise CorpError(404, "Meeting not found.")
    d = dict(m) | {"created_at": m["created_at"].isoformat()}
    cur.execute("SELECT u.id, u.username FROM corp.meeting_participants p JOIN users u ON u.id = p.user_id "
                "WHERE p.meeting_id = %s", (meeting_id,))
    d["participants"] = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT * FROM corp.commitments WHERE source_type = 'meeting' AND source_id = %s ORDER BY id", (meeting_id,))
    d["commitments"] = [ledger.commitment_view(r) for r in cur.fetchall()]
    cur.execute("SELECT d.id, d.title, v.text, v.quote, v.made_by, v.version FROM corp.decision_versions v "
                "JOIN corp.decisions d ON d.id = v.decision_id WHERE v.source_type = 'meeting' AND v.source_id = %s "
                "ORDER BY d.id", (meeting_id,))
    d["decisions"] = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT id, text, status FROM corp.blockers WHERE source_type = 'meeting' AND source_id = %s", (meeting_id,))
    d["blockers"] = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT un.name FROM corp.units un WHERE un.id = %s", (m["unit_id"],))
    row = cur.fetchone()
    d["unit_name"] = row["name"] if row else None
    return d
