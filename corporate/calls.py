"""Work calls become meetings. When a call recording has been turned into a
conversation (app.py's call job -> call_ready hooks), and the call was made
in Corporate mode (category 'office'), it becomes a private meeting in the
organisation the people on the call share -- visible to them only -- and its
decisions, commitments and blockers are extracted.

Each person may upload their own side of the call (the phone app records
each side separately), so all recordings of one call go into one meeting."""
from __future__ import annotations

from . import ai, ledger, meetings


def make_hook(get_raw_connection, release_raw_connection, dict_cursor, call_llm):
    def on_call_ready(call_id, user_id, conversation_id, transcript, category):
        if category != "office" or not (transcript or "").strip():
            return None
        conn = get_raw_connection()
        try:
            cur = dict_cursor(conn)
            cur.execute("SELECT to_regclass('corp.meetings') AS t")
            if cur.fetchone()["t"] is None:
                return None
            cur.execute("SELECT user_id FROM call_participants WHERE call_id = %s "
                        "UNION SELECT initiator_user_id FROM calls WHERE id = %s", (call_id, call_id))
            people = {r["user_id"] for r in cur.fetchall()} | {user_id}
            # The organisation this person shares with the most people on the call.
            cur.execute("SELECT m.org_id, array_agg(m.user_id) AS members FROM corp.members m "
                        "WHERE m.status = 'active' AND m.user_id = ANY(%s) AND m.org_id IN "
                        "(SELECT org_id FROM corp.members WHERE user_id = %s AND status = 'active') "
                        "GROUP BY m.org_id ORDER BY count(*) DESC, m.org_id LIMIT 1", (list(people), user_id))
            row = cur.fetchone()
            if not row:
                return None
            org_id, members = row["org_id"], set(row["members"])
            cur.execute("SELECT username FROM users WHERE id = %s", (user_id,))
            speaker = cur.fetchone()["username"]
            part = f"[{speaker}'s side]\n{transcript.strip()}"
            cur.execute("SELECT id, transcript FROM corp.meetings WHERE org_id = %s AND call_id = %s FOR UPDATE", (org_id, call_id))
            existing = cur.fetchone()
            if existing:
                mid = existing["id"]
                cur.execute("UPDATE corp.meetings SET transcript = transcript || %s, status = 'processing' WHERE id = %s",
                            ("\n\n" + part, mid))
                for uid in members:
                    cur.execute("INSERT INTO corp.meeting_participants VALUES (%s,%s) ON CONFLICT DO NOTHING", (mid, uid))
                conn.commit()
                # Only the new side is read, with the meeting's decisions so far for context.
                people_names = list(ledger.org_people(cur, org_id).keys())
                result = ai.extract(call_llm, part, people_names, ledger.current_decisions(cur, org_id, None), summary=False)
                ledger.ingest(cur, org_id, None, "meeting", mid, result, actor_id=user_id, meeting_id=mid)
                cur.execute("UPDATE corp.meetings SET status = 'ready' WHERE id = %s", (mid,))
                conn.commit()
                return mid
            cur.execute("SELECT string_agg(username, ', ' ORDER BY username) AS names FROM users WHERE id = ANY(%s)", (list(members),))
            title = f"Call: {cur.fetchone()['names']}"
            mid = meetings.create(cur, org_id, None, title, "call", user_id, members, transcript=part)
            cur.execute("UPDATE corp.meetings SET call_id = %s WHERE id = %s", (call_id, mid))
            conn.commit()
            meetings.process(conn, cur, mid, call_llm)
            return mid
        except Exception as e:  # noqa: BLE001 -- a Corporate failure must never break call processing
            conn.rollback()
            print(f"[corporate] call {call_id} -> meeting failed: {e!r}")
            return None
        finally:
            release_raw_connection(conn)
    return on_call_ready
