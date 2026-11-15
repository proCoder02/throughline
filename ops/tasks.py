"""Outcome counters for one-off background work (transcript analysis,
conversation titling, reminder extraction, the chat follow-up resolver,
thread summaries) -- things that start per request rather than on a
schedule, so a job card doesn't fit them; the dashboard shows success /
error counts and latency per task instead, the same way it shows FCM sends.

Recording never touches the database on the calling thread: run() only
appends to an in-memory buffer, and ops.probe's worker writes the buffer
out every PROBE_INTERVAL_SECONDS in one batch. A chat request timing the
resolver pays for a deque append, not a DB round trip, and a burst of
background tasks can't exhaust the ops connection pool. The cost is that a
restart loses at most one interval of counts -- acceptable for monitoring.

Inside a run(), the task itself can say how it went without raising:
  fail(reason)  -- it returned normally but didn't do its job
  ignore()      -- nothing to do (e.g. no "remind" in the message): not recorded
Otherwise a normal return is "success" and an exception is "error"."""
from __future__ import annotations

import collections
import functools
import threading
import time
from datetime import datetime, timezone

MAX_BUFFERED = 5000
RETENTION_DAYS = 14

_buffer: collections.deque = collections.deque()
_buffer_lock = threading.Lock()
_dropped = 0
_local = threading.local()

_schema_ensured = False
_schema_lock = threading.Lock()


def _set_outcome(outcome) -> None:
    if getattr(_local, "active", False):
        _local.outcome = outcome


def fail(reason) -> None:
    _set_outcome(("error", str(reason)[:300]))


def ignore() -> None:
    _set_outcome(("ignored", None))


def record(task: str, status: str, duration_ms: int | None, error: str | None = None) -> None:
    global _dropped
    with _buffer_lock:
        if len(_buffer) >= MAX_BUFFERED:
            _buffer.popleft()
            _dropped += 1
        _buffer.append((task, status, duration_ms, error, datetime.now(timezone.utc)))


def run(task: str, fn, *args, **kwargs):
    """Calls fn and records how it went. Nested runs each keep their own
    outcome (the previous one is restored on the way out)."""
    prev = (getattr(_local, "active", False), getattr(_local, "outcome", None))
    _local.active, _local.outcome = True, None
    started = time.monotonic()
    status, error = "success", None
    try:
        result = fn(*args, **kwargs)
        if _local.outcome:
            status, error = _local.outcome
        return result
    except Exception as e:
        status, error = "error", repr(e)[:300]
        raise
    finally:
        _local.active, _local.outcome = prev
        if status != "ignored":
            record(task, status, int((time.monotonic() - started) * 1000), error)


def wrap(task: str, fn):
    """fn, recorded under `task` every time it's called -- for handing to
    threading.Thread / _spawn_background in place of fn itself."""
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        return run(task, fn, *args, **kwargs)
    return wrapped


def tracked(task: str):
    """Decorator form of wrap(): every call of the function is recorded."""
    return lambda fn: wrap(task, fn)


def ensure_schema(cur, conn) -> None:
    global _schema_ensured
    if _schema_ensured:
        return
    with _schema_lock:
        if _schema_ensured:
            return
        cur.execute("CREATE SCHEMA IF NOT EXISTS ops")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ops.task_runs (
                id BIGSERIAL PRIMARY KEY,
                task TEXT NOT NULL,
                status TEXT NOT NULL,
                duration_ms INTEGER,
                error TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ops_task_runs_task_created ON ops.task_runs (task, created_at DESC)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ops_task_runs_created ON ops.task_runs (created_at)")
        conn.commit()
        _schema_ensured = True


def flush(cur, conn) -> int:
    """Writes everything buffered so far; puts it back on failure (still
    bounded by MAX_BUFFERED). Returns how many rows were written."""
    with _buffer_lock:
        rows = list(_buffer)
        _buffer.clear()
    if not rows:
        return 0
    try:
        ensure_schema(cur, conn)
        cur.executemany(
            "INSERT INTO ops.task_runs (task, status, duration_ms, error, created_at) VALUES (%s, %s, %s, %s, %s)",
            rows,
        )
        conn.commit()
        return len(rows)
    except Exception as e:
        print(f"[ops.tasks] flush of {len(rows)} rows failed: {e!r}")
        try:
            conn.rollback()
        except Exception:
            pass
        with _buffer_lock:
            for row in reversed(rows):
                if len(_buffer) >= MAX_BUFFERED:
                    break
                _buffer.appendleft(row)
        return 0


def prune(cur, conn) -> None:
    try:
        ensure_schema(cur, conn)
        cur.execute("DELETE FROM ops.task_runs WHERE created_at < now() - make_interval(days => %s)", (RETENTION_DAYS,))
        conn.commit()
    except Exception as e:
        print(f"[ops.tasks] prune failed: {e!r}")
        try:
            conn.rollback()
        except Exception:
            pass


def buffer_state() -> dict:
    with _buffer_lock:
        return {"buffered": len(_buffer), "dropped_total": _dropped}


def task_stats(cur, conn) -> dict:
    """Per task over the last 24h: counts by status, median/p95 duration,
    and the most recent error."""
    ensure_schema(cur, conn)
    cur.execute(
        """
        SELECT task,
               count(*) FILTER (WHERE status = 'success') AS success,
               count(*) FILTER (WHERE status = 'error') AS error,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms) AS p50_ms,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_ms,
               max(created_at) AS last_at
        FROM ops.task_runs
        WHERE created_at > now() - interval '24 hours'
        GROUP BY task
        """
    )
    out = {
        r["task"]: {
            "success": r["success"], "error": r["error"],
            "p50_ms": int(r["p50_ms"]) if r["p50_ms"] is not None else None,
            "p95_ms": int(r["p95_ms"]) if r["p95_ms"] is not None else None,
            "last_at": r["last_at"].isoformat() if r["last_at"] else None,
            "last_error": None, "last_error_at": None,
        }
        for r in cur.fetchall()
    }
    cur.execute(
        """
        SELECT DISTINCT ON (task) task, error, created_at
        FROM ops.task_runs
        WHERE status = 'error' AND created_at > now() - interval '24 hours'
        ORDER BY task, created_at DESC
        """
    )
    for r in cur.fetchall():
        if r["task"] in out:
            out[r["task"]]["last_error"] = r["error"]
            out[r["task"]]["last_error_at"] = r["created_at"].isoformat()
    return out
