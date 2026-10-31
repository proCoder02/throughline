"""Tunables, all overridable from .env so the touch threshold can be adjusted
on real phones without shipping a new app build (the app reads them from
POST /meet/session)."""
from __future__ import annotations

import os


def is_enabled() -> bool:
    return os.getenv("MEET_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


# First 8 hex digits of every broadcast service UUID -- how a scanning phone
# picks Throughline phones out of everything else nearby. The remaining 24
# hex digits are the rotating code itself.
UUID_PREFIX = (os.getenv("MEET_UUID_PREFIX") or "7e1a0c4e").lower()[:8]

# "Touching": signal at least this strong (dBm, closer to 0 = nearer) ...
RSSI_THRESHOLD = _int("MEET_RSSI_THRESHOLD", -40)
# ... held for this long.
TOUCH_MS = _int("MEET_TOUCH_MS", 800)

CODE_TTL_SECONDS = _int("MEET_CODE_TTL_SECONDS", 300)  # a code outlives its rotation, so late sightings still resolve
ROTATE_SECONDS = _int("MEET_ROTATE_SECONDS", 120)
MATCH_WINDOW_SECONDS = _int("MEET_MATCH_WINDOW_SECONDS", 10)  # both sides must report each other within this
PENDING_SECONDS = _int("MEET_PENDING_SECONDS", 120)  # time both people have to tap Share
MAX_ENCOUNTERS_PER_HOUR = _int("MEET_MAX_ENCOUNTERS_PER_HOUR", 30)  # stops card scraping
