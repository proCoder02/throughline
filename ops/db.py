"""Ops module's own connection pool -- deliberately separate from app.py's
db_pool, same reasoning as nudges/nudge_engine.py's _get_pool(): this
package's dashboard requests and background checks run on their own
request/timer cycle, independent of the main app, and must never contend
with or depend on another module's pool sizing/lifecycle."""
from __future__ import annotations

import os

import psycopg2.extras
import psycopg2.pool

_pool = None


def _get_pool():
    global _pool
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
