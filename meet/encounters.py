"""Codes, sightings and encounters.

A phone broadcasts a random code (inside a Bluetooth service UUID). When two
phones are touching, each reports "my code is X, I see code Y". The pair is
matched only when BOTH directions arrive within MATCH_WINDOW_SECONDS --
one phone claiming to see another proves nothing on its own. Cards are
released only after both people confirm."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from . import config

_CODE_KEY = "meet:code:{}"
_SEEN_KEY = "meet:seen:{}:{}"


def new_code() -> str:
    return secrets.token_hex(12)  # 96 random bits; 24 hex digits fill the rest of the UUID


def service_uuid(code: str) -> str:
    h = config.UUID_PREFIX + code
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def is_code(value) -> bool:
    return isinstance(value, str) and len(value) == 24 and all(c in "0123456789abcdef" for c in value)


def start_session(store, user_id: int) -> dict:
    code = new_code()
    store.set(_CODE_KEY.format(code), str(user_id), config.CODE_TTL_SECONDS)
    return {
        "code": code,
        "service_uuid": service_uuid(code),
        "uuid_prefix": config.UUID_PREFIX,
        "expires_in": config.CODE_TTL_SECONDS,
        "rotate_after": config.ROTATE_SECONDS,
        "rssi_threshold": config.RSSI_THRESHOLD,
        "touch_ms": config.TOUCH_MS,
    }


def owner_of(store, code) -> int | None:
    if not is_code(code):
        return None
    value = store.get(_CODE_KEY.format(code))
    return int(value) if value and value.isdigit() else None


def _pair(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def status_of(row: dict) -> str:
    if row["removed_at"]:
        return "removed"
    if row["declined_at"]:
        return "declined"
    if row["lo_confirmed_at"] and row["hi_confirmed_at"]:
        return "shared"
    if row["created_at"] < _now() - timedelta(seconds=config.PENDING_SECONDS):
        return "expired"
    return "pending"


def recent_count(cur, user_id: int) -> int:
    cur.execute(
        "SELECT count(*) AS n FROM meet_encounters WHERE (user_lo = %s OR user_hi = %s) AND created_at > now() - interval '1 hour'",
        (user_id, user_id),
    )
    return cur.fetchone()["n"]


def report_sighting(cur, conn, store, user_id: int, my_code, seen_code) -> dict:
    """Returns {"status": "waiting" | "matched" | "unknown" | "invalid" | "limited", ...}."""
    if owner_of(store, my_code) != user_id:
        return {"status": "invalid", "error": "Your Meet code has expired -- start Meet again."}
    other = owner_of(store, seen_code)
    if other is None or other == user_id:
        return {"status": "unknown"}
    store.set(_SEEN_KEY.format(user_id, other), "1", config.MATCH_WINDOW_SECONDS)
    if not store.get(_SEEN_KEY.format(other, user_id)):
        return {"status": "waiting"}

    lo, hi = _pair(user_id, other)
    # Both phones usually report within the same instant; the lock makes
    # them agree on one encounter row instead of creating two.
    cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", (lo, hi))
    cur.execute(
        "SELECT * FROM meet_encounters WHERE user_lo = %s AND user_hi = %s AND removed_at IS NULL AND declined_at IS NULL "
        "AND created_at > now() - make_interval(secs => %s) ORDER BY created_at DESC LIMIT 1",
        (lo, hi, config.PENDING_SECONDS),
    )
    row = cur.fetchone()
    if not row:
        # Either person over the limit blocks it -- otherwise a scraper only
        # needs the other phone to be the one that reports second.
        if max(recent_count(cur, user_id), recent_count(cur, other)) >= config.MAX_ENCOUNTERS_PER_HOUR:
            conn.rollback()
            return {"status": "limited", "error": "Too many meetings in the last hour -- try again later."}
        cur.execute("INSERT INTO meet_encounters (user_lo, user_hi) VALUES (%s, %s) RETURNING *", (lo, hi))
        row = cur.fetchone()
    conn.commit()
    return {"status": "matched", "encounter_id": row["id"]}


def get_for(cur, encounter_id: int, user_id: int) -> dict | None:
    """The encounter, only if this user is one of the two people in it."""
    cur.execute("SELECT * FROM meet_encounters WHERE id = %s AND (user_lo = %s OR user_hi = %s)",
                (encounter_id, user_id, user_id))
    return cur.fetchone()


def other_side(row: dict, user_id: int) -> int:
    return row["user_hi"] if row["user_lo"] == user_id else row["user_lo"]


def i_confirmed(row: dict, user_id: int) -> bool:
    return bool(row["lo_confirmed_at"] if row["user_lo"] == user_id else row["hi_confirmed_at"])


def they_confirmed(row: dict, user_id: int) -> bool:
    return bool(row["hi_confirmed_at"] if row["user_lo"] == user_id else row["lo_confirmed_at"])


def confirm(cur, row: dict, user_id: int, accept: bool) -> None:
    if accept:
        col = "lo_confirmed_at" if row["user_lo"] == user_id else "hi_confirmed_at"
        cur.execute(f"UPDATE meet_encounters SET {col} = COALESCE({col}, now()) WHERE id = %s", (row["id"],))
    else:
        cur.execute("UPDATE meet_encounters SET declined_by = %s, declined_at = now() WHERE id = %s AND declined_at IS NULL",
                    (user_id, row["id"]))


def remove(cur, row: dict, user_id: int) -> None:
    cur.execute("UPDATE meet_encounters SET removed_by = %s, removed_at = now() WHERE id = %s AND removed_at IS NULL",
                (user_id, row["id"]))


def list_for(cur, user_id: int, limit: int = 50) -> list[dict]:
    cur.execute(
        "SELECT * FROM meet_encounters WHERE (user_lo = %s OR user_hi = %s) ORDER BY created_at DESC LIMIT %s",
        (user_id, user_id, limit),
    )
    return cur.fetchall()
