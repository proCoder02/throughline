"""Last-24h trend data for the dashboard: one value per hour, oldest
first, so a one-off blip can be told apart from a slow decline -- plus
the hours that had a deploy or restart, drawn as markers on the charts."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

HOURS = 24
# Worst status seen in an hour: 0 healthy, 1 warning, 2 failing; None = no row.
_LEVEL = {"success": 0, "skipped": 0, "running": 0, "partial": 1, "error": 2, "failed": 2}


def _slot(ts, start) -> int | None:
    i = int((ts - start).total_seconds() // 3600)
    return i if 0 <= i < HOURS else None


def _start():
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return now - timedelta(hours=HOURS - 1)


def job_trends(cur) -> dict:
    start = _start()
    cur.execute(
        "SELECT job_name, date_trunc('hour', started_at) AS h, array_agg(DISTINCT status) AS statuses "
        "FROM ops.job_runs WHERE started_at >= %s GROUP BY 1, 2",
        (start,),
    )
    out: dict = {}
    for r in cur.fetchall():
        i = _slot(r["h"], start)
        if i is None:
            continue
        row = out.setdefault(r["job_name"], [None] * HOURS)
        row[i] = max(_LEVEL.get(s, 1) for s in r["statuses"])
    return out


def task_trends(cur) -> dict:
    start = _start()
    cur.execute(
        "SELECT task, date_trunc('hour', created_at) AS h, "
        "count(*) FILTER (WHERE status = 'success') AS ok, count(*) FILTER (WHERE status = 'error') AS err "
        "FROM ops.task_runs WHERE created_at >= %s GROUP BY 1, 2",
        (start,),
    )
    out: dict = {}
    for r in cur.fetchall():
        i = _slot(r["h"], start)
        if i is None:
            continue
        out.setdefault(r["task"], [[0, 0] for _ in range(HOURS)])[i] = [r["ok"], r["err"]]
    return out


def deploy_marks(cur) -> list:
    start = _start()
    cur.execute(
        "SELECT created_at, kind, message FROM ops.events WHERE kind IN ('deploy', 'restart') AND created_at >= %s "
        "ORDER BY created_at",
        (start,),
    )
    return [{"slot": _slot(r["created_at"], start), "kind": r["kind"], "at": r["created_at"].isoformat(),
             "message": r["message"]} for r in cur.fetchall()]


def all_trends(cur, conn) -> dict:
    try:
        return {"start": _start().isoformat(), "hours": HOURS, "jobs": job_trends(cur),
                "tasks": task_trends(cur), "deploys": deploy_marks(cur)}
    except Exception as e:
        conn.rollback()
        return {"error": repr(e)[:300]}
