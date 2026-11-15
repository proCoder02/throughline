"""The built-in probes and gauges (see ops.probe) -- every health check the
dashboard runs lives here, not in app.py. app.py only hands over the
objects it owns through install(); each check reads them and returns
(status, detail).

Nothing here needs app.py at import time, same isolation rule as the rest
of ops/."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from . import probe

STUCK_LISTENER_SECONDS = 120
COSTLENS_STALE_SECONDS = 300
PENDING_EVENTS_SCAN_LIMIT = 10000


class ListenerState:
    """Shared between a long-running loop (which updates it) and its probe
    (which reads it) -- e.g. app.py's Redis pub/sub notify listener, which
    blocks inside Redis and so can't write job rows itself."""

    def __init__(self):
        self._lock = threading.Lock()
        self.connected = False
        self.last_loop_at = None  # time.monotonic() of the loop's last pass
        self.delivered = 0
        self.reconnects = 0
        self.last_error = None
        self.last_error_at = None

    def tick(self, connected=True):
        with self._lock:
            self.connected = connected
            self.last_loop_at = time.monotonic()

    def delivered_one(self):
        with self._lock:
            self.delivered += 1

    def failed(self, error):
        with self._lock:
            self.connected = False
            self.reconnects += 1
            self.last_error = repr(error)[:300]
            self.last_error_at = datetime.now(timezone.utc).isoformat()

    def snapshot(self):
        with self._lock:
            return {
                "connected": self.connected,
                "seconds_since_last_loop": round(time.monotonic() - self.last_loop_at, 1) if self.last_loop_at else None,
                "delivered_since_start": self.delivered,
                "reconnects_since_start": self.reconnects,
                "last_error": self.last_error,
                "last_error_at": self.last_error_at,
            }


class LiveCounter:
    """How many of something are open right now (e.g. live transcription
    sessions) -- shown as a gauge, since a count has no run history."""

    def __init__(self):
        self._lock = threading.Lock()
        self.current = 0
        self.peak = 0
        self.total = 0

    def opened(self):
        with self._lock:
            self.current += 1
            self.total += 1
            self.peak = max(self.peak, self.current)

    def closed(self):
        with self._lock:
            self.current = max(0, self.current - 1)

    def snapshot(self):
        with self._lock:
            return {"open_now": self.current, "peak_since_start": self.peak, "total_since_start": self.total}


def listener_check(state: ListenerState):
    def check():
        snap = state.snapshot()
        if snap["seconds_since_last_loop"] is None:
            return "error", {**snap, "problem": "listener loop has not started"}
        if snap["seconds_since_last_loop"] > STUCK_LISTENER_SECONDS:
            return "error", {**snap, "problem": f"no loop pass for {snap['seconds_since_last_loop']:.0f}s -- stuck"}
        if not snap["connected"]:
            return "error", {**snap, "problem": "not subscribed to Redis (retrying)"}
        return "success", snap
    return check


def redis_check(client):
    def check():
        started = time.monotonic()
        client.ping()
        ping_ms = round((time.monotonic() - started) * 1000, 1)
        mem = client.info("memory")
        clients = client.info("clients")
        keys = []
        for key in client.scan_iter(match="pending_events:*", count=500):
            keys.append(key)
            if len(keys) >= PENDING_EVENTS_SCAN_LIMIT:
                break
        pipe = client.pipeline(transaction=False)
        for key in keys:
            pipe.llen(key)
        pending_users, pending_events = len(keys), sum(pipe.execute()) if keys else 0
        return "success", {
            "ping_ms": ping_ms,
            "used_memory": mem.get("used_memory_human"),
            "maxmemory": mem.get("maxmemory_human"),
            "connected_clients": clients.get("connected_clients"),
            "users_with_undelivered_events": pending_users,
            "undelivered_events": pending_events,
        }
    return check


def rq_check(queue):
    """An RQ queue is only useful if some worker process is listening on
    it -- work enqueued with no worker just waits forever."""
    def check():
        from rq import Worker
        workers = Worker.all(queue=queue)
        queued = queue.count
        failed = queue.failed_job_registry.count
        oldest_age = None
        if queued:
            try:
                job_ids = queue.get_job_ids(0, 0)
                job = queue.fetch_job(job_ids[0]) if job_ids else None
                if job and job.enqueued_at:
                    enqueued = job.enqueued_at if job.enqueued_at.tzinfo else job.enqueued_at.replace(tzinfo=timezone.utc)
                    oldest_age = round((datetime.now(timezone.utc) - enqueued).total_seconds() / 60, 1)
            except Exception:
                pass  # age is a nice-to-have; the counts above are what matter
        detail = {
            "queue": queue.name, "workers": len(workers), "queued": queued,
            "oldest_queued_minutes": oldest_age, "failed": failed,
            "started": queue.started_job_registry.count, "finished": queue.finished_job_registry.count,
        }
        if not workers:
            return "error", {**detail, "problem": f"no worker is listening on '{queue.name}' -- jobs wait forever (run worker.py)"}
        if failed:
            return "partial", {**detail, "problem": f"{failed} failed job(s) in the registry"}
        return "success", detail
    return check


def costlens_check(get_stats):
    """get_stats() -> the CostLens tracker's stats dict, or None when
    CostLens tracking isn't installed in this process."""
    last_dropped = {"n": None}

    def check():
        stats = get_stats()
        if stats is None:
            return "error", {"problem": "CostLens tracking is no longer installed"}
        dropped_now = stats["dropped_total"] - (last_dropped["n"] if last_dropped["n"] is not None else stats["dropped_total"])
        last_dropped["n"] = stats["dropped_total"]
        if not stats["flush_thread_alive"]:
            return "error", {**stats, "problem": "flush thread is not running -- usage is no longer sent"}
        # Never sent yet counts from startup, so a fresh restart isn't flagged
        # before the first flush has even had a chance to run.
        since_ok = stats["seconds_since_last_ok"]
        if since_ok is None:
            since_ok = stats["seconds_since_start"]
        if stats["buffered"] and since_ok > COSTLENS_STALE_SECONDS:
            return "error", {**stats, "problem": "no successful send to CostLens in 5+ min -- usage records are piling up (see last_error)"}
        if dropped_now > 0:
            return "partial", {**stats, "problem": f"{dropped_now} usage records dropped (buffer full)"}
        return "success", stats
    return check


def install(*, redis_client=None, notify_listener: ListenerState | None = None, rq_queues=(),
            costlens_stats=None, gauges=None) -> None:
    """The one call app.py makes. Anything left out simply isn't checked."""
    if redis_client is not None:
        probe.register_probe("redis", redis_check(redis_client))
    if notify_listener is not None:
        probe.register_probe("notify_pubsub_listener", listener_check(notify_listener))
    for queue in rq_queues:
        probe.register_probe(f"rq_worker_{queue.name}", rq_check(queue))
    if costlens_stats is not None and costlens_stats() is not None:  # only when tracking is installed
        probe.register_probe("costlens_flush", costlens_check(costlens_stats))
    for name, fn in (gauges or {}).items():
        probe.register_gauge(name, fn)
