"""
Optional, additive proactive-nudge engine -- overdue tasks, mood-trend
shifts, and relationship touchpoints ("haven't talked to X in a while").
Mirrors emotional_intelligence/ei_adapter.py's isolation pattern: this is
the ONLY file app.py needs to import from, and nothing here is imported BY
any of app.py's existing request-handling code -- it only runs on its own
background timer, plus two small settings endpoints app.py delegates to.

Deliberately has NO import-time dependency on app.py (no `from app import
...`, anywhere) -- start_nudge_worker() takes push_notification and
send_fcm_to_user as plain callables instead of importing them, so this
module has zero coupling back to app.py. That's what makes it "plug and
play": this whole folder could be deleted, or its import wrapped in
try/except (which app.py does), without touching a single existing line
anywhere else.

Master switch: NUDGE_FEATURE_ENABLED in .env (defaults to "false", same
convention as EMOTIONAL_INTELLIGENCE_ENABLED in ei_adapter.py) -- when
false, the worker thread still runs on schedule but every cycle is a cheap
no-op. Per-user opt-out: nudges.user_settings.nudges_enabled (defaults to
enabled for a user who has never touched settings -- see
get_user_settings). Like EMOTIONAL_INTELLIGENCE_ENABLED, flipping the .env
value requires restarting the process to take effect (it's read into
os.environ once at startup, not live-reloaded).
"""
from __future__ import annotations

import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

_pool = None


def is_enabled() -> bool:
    return os.getenv("NUDGE_FEATURE_ENABLED", "false").strip().lower() == "true"


def _log(where: str, exc: Exception) -> None:
    print(f"[nudge_engine] {where} failed: {exc!r}", file=sys.stderr)


def _get_pool():
    """This module's own connection pool -- separate from app.py's db_pool
    and from emotional_intelligence/ei_adapter.py's pool, same reasoning as
    ei_adapter's _get_pool(): this worker runs on its own timer independent
    of any request, so it must never contend with or depend on another
    module's pool sizing/lifecycle."""
    global _pool
    if _pool is None:
        import psycopg2.pool
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=1, maxconn=3,
            dsn=os.getenv("DATABASE_URL"),
            options="-c statement_timeout=5000",
        )
    return _pool


def _connect():
    conn = _get_pool().getconn()
    conn.autocommit = True
    return conn


def _release(conn) -> None:
    _get_pool().putconn(conn)


# ----------------------------------------------------------------------------
# Per-user settings -- used by app.py's /settings/nudges GET+POST endpoints.
# Each opens its own connection since these are called per-request, not
# from inside the worker's tight loop (see the checkers below, which read
# settings via a JOIN on the caller's own cursor instead, to avoid nested
# pool checkouts).
# ----------------------------------------------------------------------------

_SETTINGS_FIELDS = (
    "nudges_enabled",
    "cognitive_intelligence_enabled",
    "task_reminder_notifications_enabled",
    "tags_questions_enabled",
    "smart_features_enabled",
)
_DEFAULT_SETTINGS = {f: True for f in _SETTINGS_FIELDS}


def get_user_settings(user_id) -> dict:
    try:
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT {', '.join(_SETTINGS_FIELDS)} FROM nudges.user_settings WHERE user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()
            if row is None:
                return dict(_DEFAULT_SETTINGS)
            return dict(zip(_SETTINGS_FIELDS, row))
        finally:
            _release(conn)
    except Exception as exc:
        _log("get_user_settings", exc)
        return dict(_DEFAULT_SETTINGS)


def set_user_settings(user_id, **fields) -> dict:
    """Upserts only the field(s) actually passed as non-None keyword
    arguments (one per name in _SETTINGS_FIELDS), leaving any other at its
    current -- or default, on first write -- value, so a client can flip
    just one toggle without needing to know or resend the others' current
    state."""
    unknown = set(fields) - set(_SETTINGS_FIELDS)
    if unknown:
        raise ValueError(f"Unknown settings field(s): {unknown}")
    current = get_user_settings(user_id)
    new_values = {f: current[f] if fields.get(f) is None else fields[f] for f in _SETTINGS_FIELDS}
    conn = _connect()
    try:
        cur = conn.cursor()
        cols = ", ".join(_SETTINGS_FIELDS)
        placeholders = ", ".join(["%s"] * len(_SETTINGS_FIELDS))
        set_clause = ", ".join(f"{f} = EXCLUDED.{f}" for f in _SETTINGS_FIELDS)
        cur.execute(
            f"INSERT INTO nudges.user_settings (user_id, {cols}) VALUES (%s, {placeholders}) "
            f"ON CONFLICT (user_id) DO UPDATE SET {set_clause}, updated_at = now()",
            (user_id, *[new_values[f] for f in _SETTINGS_FIELDS]),
        )
        return new_values
    finally:
        _release(conn)


# ----------------------------------------------------------------------------
# Cooldowns -- how long to wait before nudging about the exact same thing
# again, so this doesn't turn into daily spam about one overdue task or one
# quiet friendship. Tunable constants, not settings -- revisit if real usage
# shows these are too chatty or too quiet.
# ----------------------------------------------------------------------------
OVERDUE_TASK_RENUDGE_DAYS = 3
MOOD_TREND_RECENT_DAYS = 3           # "how you've been lately"
MOOD_TREND_BASELINE_DAYS = 7         # the 7 days before that, for comparison
MOOD_TREND_MIN_LOGS = 2              # per window -- avoids one noisy data point triggering a nudge
MOOD_TREND_RENUDGE_DAYS = 4
RELATIONSHIP_TOUCHPOINT_DAYS = 7           # days of silence before this nudge fires at all
RELATIONSHIP_TOUCHPOINT_RENUDGE_DAYS = 7   # days before nudging about the same friend again


def _recently_sent(cur, user_id, nudge_type, reference_id, cooldown_days) -> bool:
    cur.execute(
        "SELECT 1 FROM nudges.sent_log WHERE user_id = %s AND nudge_type = %s AND reference_id = %s "
        "AND sent_at > now() - make_interval(days => %s) LIMIT 1",
        (user_id, nudge_type, str(reference_id), cooldown_days),
    )
    return cur.fetchone() is not None


def _log_sent(cur, user_id, nudge_type, reference_id) -> None:
    cur.execute(
        "INSERT INTO nudges.sent_log (user_id, nudge_type, reference_id) VALUES (%s, %s, %s)",
        (user_id, nudge_type, str(reference_id)),
    )


# ----------------------------------------------------------------------------
# 1. Overdue tasks
# ----------------------------------------------------------------------------

def _check_overdue_tasks(cur) -> list[dict]:
    """tasks.due_date is stored as a free-text display phrase ("Friday",
    "tonight"), not a real date (see schema.sql's own comment on that
    column) -- it can't be compared to now(). reminder_at IS a real
    timestamp and already means "this needs attention by this time", so
    it's reused here as the overdue signal: a task whose reminder has
    passed and is still open has been sitting unresolved since at least
    then. COALESCE(s.nudges_enabled, TRUE) treats a user with no
    nudges.user_settings row yet as enabled (see module docstring)."""
    cur.execute(
        """
        SELECT t.id, t.user_id, t.description
        FROM tasks t
        LEFT JOIN nudges.user_settings s ON s.user_id = t.user_id
        WHERE t.status = 'open' AND t.reminder_at IS NOT NULL AND t.reminder_at < now()
          AND COALESCE(s.nudges_enabled, TRUE) = TRUE
        """
    )
    out = []
    for task_id, user_id, description in cur.fetchall():
        if _recently_sent(cur, user_id, "overdue_task", task_id, OVERDUE_TASK_RENUDGE_DAYS):
            continue
        out.append({
            "user_id": user_id, "nudge_type": "overdue_task", "reference_id": task_id,
            "title": "Still on your list",
            "body": f"\"{description}\" is still open -- want to take care of it or push the reminder?",
            "data": {"type": "nudge_overdue_task", "task_id": task_id},
        })
    return out


# ----------------------------------------------------------------------------
# 2. Mood-trend shift
# ----------------------------------------------------------------------------

def _dominant_mood(cur, user_id, older_bound_days, newer_bound_days):
    """Dominant mood_label (by count) in the half-open window
    (now - older_bound_days, now - newer_bound_days]. older_bound_days
    must be > newer_bound_days (e.g. (10, 3) covers "3 to 10 days ago")."""
    cur.execute(
        """
        SELECT mood_label, count(*) AS n FROM mood_logs
        WHERE user_id = %s
          AND created_at > now() - make_interval(days => %s)
          AND created_at <= now() - make_interval(days => %s)
        GROUP BY mood_label ORDER BY n DESC LIMIT 1
        """,
        (user_id, older_bound_days, newer_bound_days),
    )
    row = cur.fetchone()
    return (row[0], row[1]) if row else (None, 0)


def _check_mood_trends(cur) -> list[dict]:
    """Compares the dominant mood over the last MOOD_TREND_RECENT_DAYS
    against the MOOD_TREND_BASELINE_DAYS before that. Flags a shift in
    EITHER direction (not just toward a "negative" mood) -- deliberately
    simple/symmetric rather than trying to hand-rank which moods count as
    concerning; the message just states what changed."""
    cur.execute(
        """
        SELECT DISTINCT m.user_id FROM mood_logs m
        LEFT JOIN nudges.user_settings s ON s.user_id = m.user_id
        WHERE m.created_at > now() - make_interval(days => %s)
          AND COALESCE(s.nudges_enabled, TRUE) = TRUE
        """,
        (MOOD_TREND_RECENT_DAYS + MOOD_TREND_BASELINE_DAYS,),
    )
    user_ids = [row[0] for row in cur.fetchall()]

    out = []
    for user_id in user_ids:
        recent_label, recent_n = _dominant_mood(cur, user_id, MOOD_TREND_RECENT_DAYS, 0)
        baseline_label, baseline_n = _dominant_mood(
            cur, user_id,
            MOOD_TREND_RECENT_DAYS + MOOD_TREND_BASELINE_DAYS, MOOD_TREND_RECENT_DAYS,
        )
        if not recent_label or not baseline_label:
            continue
        if recent_n < MOOD_TREND_MIN_LOGS or baseline_n < MOOD_TREND_MIN_LOGS:
            continue
        if recent_label == baseline_label:
            continue
        reference_id = f"{baseline_label}->{recent_label}"
        if _recently_sent(cur, user_id, "mood_trend", reference_id, MOOD_TREND_RENUDGE_DAYS):
            continue
        out.append({
            "user_id": user_id, "nudge_type": "mood_trend", "reference_id": reference_id,
            "title": "Noticed a shift",
            "body": f"You've seemed more {recent_label} than usual over the last few days.",
            "data": {"type": "nudge_mood_trend", "from_mood": baseline_label, "to_mood": recent_label},
        })
    return out


# ----------------------------------------------------------------------------
# 3. Relationship touchpoint
# ----------------------------------------------------------------------------

def _check_relationship_touchpoints(cur) -> list[dict]:
    """"Last talked" is measured by shared CALLS specifically (not chat
    messages) -- the nudge is "maybe give them a call", so the signal
    should be the thing being nudged about. joined_at IS NOT NULL (not
    status='joined') for both participants, same fix already applied in
    emotional_intelligence/relationship_batch.py: app.py's leave_call
    overwrites status to 'left' once someone exits, so status='joined'
    only ever matches a still-in-progress call. A friend never called at
    all (last_call_at IS NULL) is treated as eligible, not excluded --
    arguably the strongest case for this nudge, not a reason to skip it."""
    cur.execute(
        """
        SELECT f.user_id, f.friend_id, u.username,
               (SELECT max(c.created_at) FROM calls c
                JOIN call_participants cp1 ON cp1.call_id = c.id
                    AND cp1.user_id = f.user_id AND cp1.joined_at IS NOT NULL
                JOIN call_participants cp2 ON cp2.call_id = c.id
                    AND cp2.user_id = f.friend_id AND cp2.joined_at IS NOT NULL
               ) AS last_call_at
        FROM friendships f
        JOIN users u ON u.id = f.friend_id
        LEFT JOIN nudges.user_settings s ON s.user_id = f.user_id
        WHERE COALESCE(s.nudges_enabled, TRUE) = TRUE
        """
    )
    now = datetime.now(timezone.utc)
    out = []
    for user_id, friend_id, friend_name, last_call_at in cur.fetchall():
        if last_call_at is not None and (now - last_call_at) < timedelta(days=RELATIONSHIP_TOUCHPOINT_DAYS):
            continue
        if _recently_sent(cur, user_id, "relationship_touchpoint", friend_id, RELATIONSHIP_TOUCHPOINT_RENUDGE_DAYS):
            continue
        out.append({
            "user_id": user_id, "nudge_type": "relationship_touchpoint", "reference_id": friend_id,
            "title": "Been a while",
            "body": f"You haven't talked to {friend_name} in a bit -- maybe give them a call?",
            "data": {"type": "nudge_relationship_touchpoint", "friend_id": friend_id},
        })
    return out


# ----------------------------------------------------------------------------
# Cycle runner + worker
# ----------------------------------------------------------------------------

def run_nudge_cycle(push_fn, fcm_fn) -> int:
    """Runs one full pass of all three checks and delivers any nudges due.
    push_fn/fcm_fn are app.py's push_notification/send_fcm_to_user,
    injected rather than imported -- see module docstring. Returns the
    count sent (for the worker's own logging); never raises."""
    if not is_enabled():
        return 0
    sent = 0
    try:
        conn = _connect()
        try:
            cur = conn.cursor()
            candidates = []
            candidates += _check_overdue_tasks(cur)
            candidates += _check_mood_trends(cur)
            candidates += _check_relationship_touchpoints(cur)
            for c in candidates:
                try:
                    push_fn(c["user_id"], {"type": c["data"]["type"], **c["data"]})
                    fcm_fn(
                        c["user_id"], title=c["title"], body=c["body"],
                        data={k: str(v) for k, v in c["data"].items()},
                    )
                    _log_sent(cur, c["user_id"], c["nudge_type"], c["reference_id"])
                    sent += 1
                except Exception as exc:
                    _log(f"delivering {c['nudge_type']} nudge", exc)
        finally:
            _release(conn)
    except Exception as exc:
        _log("run_nudge_cycle", exc)
    return sent


def start_nudge_worker(push_fn, fcm_fn, interval_seconds: int = 3600) -> None:
    """Starts the daemon background thread; call once at app startup (see
    app.py). Runs every interval_seconds regardless of is_enabled() --
    run_nudge_cycle() checks the flag itself and no-ops when off, so the
    thread can always be started unconditionally rather than needing its
    own separate enable/disable wiring in app.py."""

    def _loop():
        while True:
            try:
                n = run_nudge_cycle(push_fn, fcm_fn)
                if n:
                    print(f"[nudge_engine] sent {n} nudge(s) this cycle")
            except Exception as exc:
                _log("worker loop", exc)
            time.sleep(interval_seconds)

    threading.Thread(target=_loop, daemon=True).start()
