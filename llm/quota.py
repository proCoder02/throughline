"""Per-person daily AI limit, so one heavy user can't run up the model bill.

One check before every request that asks the AI something on a person's
behalf (Ask, chat, catch-up, client updates, ticket briefs, image and PDF
reading). Each counts as one use; background jobs and recording processing
are not counted. Counted per person per day in Redis; if Redis is unreachable
the request goes through (a limit must never take the product down).

    from llm import quota
    quota.register(app, redis_client, authenticated_user_id)

Env: LLM_DAILY_LIMIT (default 200 a day; 0 turns the limit off),
     LLM_QUOTA_UTC_OFFSET_MINUTES (when the day resets; default 330 = midnight IST)."""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

# POST routes that ask the AI for a person (method + path pattern).
LIMITED = [re.compile(p) for p in (
    r"^/chat$",
    r"^/chat/global$",
    r"^/chat/global/image$",
    r"^/vehicle-policies/extract-pdf$",
    r"^/friends/\d+/cognitive-suggestion$",
    r"^/corp/orgs/\d+/ask$",
    r"^/corp/orgs/\d+/catch-up$",
    r"^/corp/orgs/\d+/projects/\d+/client-update$",
    r"^/corp/orgs/\d+/(threads|meetings)/\d+/common-ground$",
    r"^/corp/orgs/\d+/tickets/\d+/brief$",
)]


def limit() -> int:
    try:
        return max(0, int(os.getenv("LLM_DAILY_LIMIT", "200")))
    except ValueError:
        return 200


def _day(now=None) -> str:
    offset = int(os.getenv("LLM_QUOTA_UTC_OFFSET_MINUTES", "330") or 0)
    now = (now or datetime.now(timezone.utc)) + timedelta(minutes=offset)
    return now.strftime("%Y-%m-%d")


def _key(user_id) -> str:
    return f"llm:quota:{user_id}:{_day()}"


def limited_path(method: str, path: str) -> bool:
    return method == "POST" and any(p.match(path) for p in LIMITED)


def take(redis_client, user_id) -> tuple[bool, int]:
    """Counts one use. Returns (allowed, remaining today). Fails open."""
    cap = limit()
    if not cap or user_id is None:
        return True, -1
    try:
        key = _key(user_id)
        n = redis_client.incr(key)
        if n == 1:
            redis_client.expire(key, 2 * 24 * 3600)
        return n <= cap, max(0, cap - n)
    except Exception as e:  # noqa: BLE001 -- Redis down: don't block anyone
        print(f"[llm.quota] not counted ({e!r})")
        return True, -1


def used_today(redis_client, user_id) -> int:
    try:
        return int(redis_client.get(_key(user_id)) or 0)
    except Exception:  # noqa: BLE001
        return 0


def register(app, redis_client, user_id_for_request) -> None:
    from flask import jsonify, request

    @app.before_request
    def _llm_daily_limit():
        if not limited_path(request.method, request.path):
            return None
        allowed, remaining = take(redis_client, user_id_for_request())
        if allowed:
            return None
        cap = limit()
        return jsonify({"error": f"You've used today's {cap} AI requests. The limit resets at midnight.",
                        "limit": cap, "remaining": 0}), 429
