"""Short-lived state (broadcast codes, one-sided sightings). Redis when it's
reachable; otherwise an in-process dict -- the app runs a single gunicorn
worker, so that fallback is correct, just not durable across a restart
(nobody needs a 10-second sighting to survive one). Same "Redis is never
load-bearing" rule as the rest of the app."""
from __future__ import annotations

import threading
import time


class Ephemeral:
    def __init__(self, redis_client=None):
        self._redis = redis_client
        self._local: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def set(self, key: str, value: str, ttl: int) -> None:
        if self._redis is not None:
            try:
                self._redis.set(key, value, ex=ttl)
                return
            except Exception as e:  # any redis error -> local fallback
                print(f"[meet] redis SET failed, using local store: {e!r}")
        with self._lock:
            self._local[key] = (value, time.monotonic() + ttl)
            if len(self._local) > 5000:
                self._sweep()

    def get(self, key: str) -> str | None:
        if self._redis is not None:
            try:
                value = self._redis.get(key)
                if value is not None:
                    return value
            except Exception as e:
                print(f"[meet] redis GET failed, using local store: {e!r}")
        with self._lock:
            hit = self._local.get(key)
            if not hit:
                return None
            if hit[1] < time.monotonic():
                self._local.pop(key, None)
                return None
            return hit[0]

    def _sweep(self) -> None:
        now = time.monotonic()
        for k in [k for k, (_, exp) in self._local.items() if exp < now]:
            self._local.pop(k, None)
