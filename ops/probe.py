"""Health probes for things that can't report on themselves through a job
row -- a loop blocked inside Redis pub/sub, the CostLens flush thread, the
RQ worker that lives in another process (or doesn't exist), Redis itself.

app.py registers a probe per thing: a function returning (status, detail),
status one of "success" / "partial" (working, but something needs a look)
/ "error". ops_probe_worker calls every probe each PROBE_INTERVAL_SECONDS
and writes an ops.job_runs row for it -- at most once per
HEARTBEAT_SECONDS while nothing changes, immediately when its status
changes -- so each probe shows up as an ordinary job card, goes stale if
this worker itself dies, and is covered by the existing stale-job alerts.

Gauges are live numbers with no history (e.g. open live-transcription
sessions): read straight from the registered function on each dashboard
refresh, which works because the dashboard runs in the same process.

The same loop also writes out ops.tasks' buffered task outcomes."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from . import db as ops_db
from . import tasks as ops_tasks
from .schema import ensure_ops_schema
from .tracking import HEARTBEAT_SECONDS, record_completed_run

PROBE_INTERVAL_SECONDS = 30
PRUNE_INTERVAL_SECONDS = 3600

_probes: dict = {}
_gauges: dict = {}


def register_probe(name: str, fn, cadence_label: str | None = None, stale_hours: float = 0.25) -> None:
    _probes[name] = {
        "fn": fn,
        "cadence_label": cadence_label or f"Checked every {PROBE_INTERVAL_SECONDS}s (heartbeat every 5 min)",
        "stale_hours": stale_hours,
    }


def register_gauge(name: str, fn) -> None:
    _gauges[name] = fn


def probe_jobs() -> dict:
    """name -> {cadence_label, stale_hours}, for ops.jobs.job_status."""
    return {n: {"cadence_label": p["cadence_label"], "stale_hours": p["stale_hours"]} for n, p in _probes.items()}


def gauges() -> dict:
    out = {}
    for name, fn in _gauges.items():
        try:
            out[name] = fn()
        except Exception as e:
            out[name] = {"error": repr(e)[:200]}
    return out


def run_probe(name: str):
    try:
        status, detail = _probes[name]["fn"]()
    except Exception as e:
        status, detail = "error", {"error": repr(e)[:300]}
    return status, detail


_last_beat: dict = {}  # name -> (monotonic time, status) of the last row written
_last_prune = {"at": 0.0}


def note_written(name: str, status: str) -> None:
    """A row for this probe was just written elsewhere (the dashboard's
    "check again") -- counts as its heartbeat."""
    prev = _last_beat.get(name)
    _last_beat[name] = (time.monotonic(), status)
    if prev and prev[1] != status:
        _status_event(name, prev[1], status, None)


_STATUS_WORD = {"success": "healthy", "partial": "warning", "error": "failing"}


def _status_event(name, old, new, detail, cur=None, conn=None):
    from . import events
    problem = (detail or {}).get("problem") if isinstance(detail, dict) else None
    events.record("status", name, f"{_STATUS_WORD.get(old, old)} → {_STATUS_WORD.get(new, new)}"
                  + (f": {problem}" if problem else ""), detail={"from": old, "to": new}, cur=cur, conn=conn)


def run_once() -> None:
    """One pass: write out buffered task counts, prune old ones hourly,
    run every probe and record the ones due a row."""
    conn = None
    try:
        conn = ops_db.get_conn()
        cur = ops_db.dict_cursor(conn)
        ensure_ops_schema(cur, conn)
        ops_tasks.flush(cur, conn)
        if time.monotonic() - _last_prune["at"] >= PRUNE_INTERVAL_SECONDS:
            from . import acks, events
            ops_tasks.prune(cur, conn)
            events.prune(cur, conn)
            acks.prune(cur, conn)
            _last_prune["at"] = time.monotonic()
        for name in list(_probes):
            started_at = datetime.now(timezone.utc)
            status, detail = run_probe(name)
            prev = _last_beat.get(name)
            if prev is None or prev[1] != status or time.monotonic() - prev[0] >= HEARTBEAT_SECONDS:
                record_completed_run(cur, conn, name, started_at, status, detail)
                _last_beat[name] = (time.monotonic(), status)
                if prev is not None and prev[1] != status:
                    _status_event(name, prev[1], status, detail, cur, conn)
                if status != "success":
                    print(f"[ops.probe] {name}: {status} {detail}", flush=True)
        try:
            from . import remediations
            remediations.observe_sending()
        except Exception as e:
            print(f"[ops.probe] sending-email watch failed: {e!r}", flush=True)
    except Exception as e:
        print(f"[ops.probe] error: {e!r}", flush=True)
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        if conn:
            ops_db.release_conn(conn)


def ops_probe_worker():
    from . import events
    events.record_startup()
    while True:
        time.sleep(PROBE_INTERVAL_SECONDS)
        run_once()
