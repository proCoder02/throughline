"""Feature flag and tunables for Corporate."""
from __future__ import annotations

import os


def is_enabled() -> bool:
    return os.getenv("CORPORATE_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


# How long an invitation link stays valid.
INVITE_DAYS = int(os.getenv("CORPORATE_INVITE_DAYS", "14"))
# Most invitations accepted in one request (a pasted list / CSV).
MAX_INVITES_PER_REQUEST = 200
NAME_MAX = 120
TITLE_MAX = 120


def creation_allowed(email: str) -> bool:
    """Who may create a new organisation. CORPORATE_ORG_CREATION=open (default):
    anyone. =closed: only the emails in CORPORATE_CREATORS (comma separated) --
    for selling Throughline to companies yourself and inviting each owner."""
    if os.getenv("CORPORATE_ORG_CREATION", "open").strip().lower() != "closed":
        return True
    allowed = {e.strip().lower() for e in os.getenv("CORPORATE_CREATORS", "").split(",") if e.strip()}
    return (email or "").strip().lower() in allowed
