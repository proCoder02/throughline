"""Ops module's own connection pool -- deliberately separate from app.py's
db_pool, same reasoning as nudges/nudge_engine.py's _get_pool(): this
package's dashboard requests and background checks run on their own
request/timer cycle, independent of the main app, and must never contend
with or depend on another module's pool sizing/lifecycle."""
from __future__ import annotations

import os
import threading

import psycopg2.extras
import psycopg2.pool

_pool = None
_pool_lock = threading.Lock()


def _get_pool():
    """Double-checked locking -- confirmed live (via local multi-threaded
    import) that the naive "if _pool is None: create it" version races:
    two threads can both see None, both construct their own
    ThreadedConnectionPool, and whichever assignment loses leaves a
    connection obtained from the first instance being returned to the
    second, which never tracked it as checked out ("trying to put
    unkeyed connection"). The lock only matters for that one-time
    construction; every later call just reads the already-set _pool."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = psycopg2.pool.ThreadedConnectionPool(
                    minconn=1, maxconn=5,
                    dsn=os.getenv("DATABASE_URL"),
                )
    return _pool


def get_conn():
    return _get_pool().getconn()


def release_conn(conn) -> None:
    _get_pool().putconn(conn)


def dict_cursor(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
