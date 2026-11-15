"""The ops event log -- one timeline of what happened: health status
changes, alerts sent and recovered, every dashboard action (who clicked
what), acknowledgements, and deploys/restarts. Answers "what happened
overnight" in one look, and gives the trend charts their deploy markers.

Writing an event is best-effort everywhere: a failure here never breaks
the probe, alert or action that's reporting it."""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import db as ops_db
from .schema import ensure_ops_schema

KEEP_DAYS = 30
# Written by the deploy script next to app.py: commit hash + subject.
RELEASE_FILE = Path(__file__).resolve().parent.parent / ".release"


def record(kind: str, subject: str | None = None, message: str | None = None, actor: str | None = None,
           detail: dict | None = None, cur=None, conn=None) -> None:
    """kind: status / alert / recovered / action / ack / deploy / restart.
    Uses the caller's connection when given, otherwise borrows one."""
    own = conn is None
    try:
        if own:
            conn = ops_db.get_conn()
            cur = ops_db.dict_cursor(conn)
        ensure_ops_schema(cur, conn)
        cur.execute(
            "INSERT INTO ops.events (kind, subject, message, actor, detail) VALUES (%s, %s, %s, %s, %s)",
            (kind, subject, (message or "")[:1000] or None, actor, json.dumps(detail) if detail else None),
        )
        conn.commit()
    except Exception as e:
        print(f"[ops.events] record failed ({kind} {subject}): {e!r}", flush=True)
        try:
            conn and conn.rollback()
        except Exception:
            pass
    finally:
        if own and conn is not None:
            ops_db.release_conn(conn)


def recent(cur, limit: int = 100, hours: int = 48) -> list:
    cur.execute(
        "SELECT id, created_at, kind, subject, message, actor, detail FROM ops.events "
        "WHERE created_at > now() - make_interval(hours => %s) ORDER BY id DESC LIMIT %s",
        (int(hours), int(limit)),
    )
    return [{**r, "created_at": r["created_at"].isoformat()} for r in cur.fetchall()]


def prune(cur, conn) -> None:
    cur.execute("DELETE FROM ops.events WHERE created_at < now() - make_interval(days => %s)", (KEEP_DAYS,))
    conn.commit()


def read_release() -> str | None:
    try:
        text = RELEASE_FILE.read_text(encoding="utf-8").strip()
        return text[:300] or None
    except OSError:
        return None


def record_startup() -> None:
    """Called once when the app's ops worker starts: a "deploy" event when
    the release file changed since the last recorded deploy, otherwise a
    plain "restart"."""
    release = read_release()
    conn = None
    try:
        conn = ops_db.get_conn()
        cur = ops_db.dict_cursor(conn)
        ensure_ops_schema(cur, conn)
        cur.execute("SELECT detail FROM ops.events WHERE kind = 'deploy' ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        last = ((row or {}).get("detail") or {}).get("release")
        pid = os.getpid()
        if release and release != last:
            record("deploy", "App deployed", release, detail={"release": release, "pid": pid}, cur=cur, conn=conn)
        else:
            record("restart", "App restarted", release or "no release file", detail={"release": release, "pid": pid},
                   cur=cur, conn=conn)
    except Exception as e:
        print(f"[ops.events] startup record failed: {e!r}", flush=True)
    finally:
        if conn is not None:
            ops_db.release_conn(conn)
