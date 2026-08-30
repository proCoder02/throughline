"""
Cognitive Commerce: the ONLY file app.py imports from for the Swiggy MCP
integration -- everything else in commerce/ (oauth.py, mcp_client.py,
crypto_utils.py) is internal plumbing this module composes.

Every public function here checks is_enabled() (or is called only from a
route that already checked it) and the chat-context path swallows its own
errors, matching emotional_intelligence/ei_adapter.py's own contract:
flipping SWIGGY_MCP_ENABLED in .env is the only thing that changes app.py's
behavior -- no code path in app.py itself needs rewriting, and a Swiggy/DB
hiccup in the passive suggestion path can never break a real chat request.

The explicit-action paths (connect/callback/disconnect/confirm) do NOT
swallow errors -- a user who just tapped "Connect" or "Order this" needs to
see a real failure, not a silent no-op.

See SWIGGY_MCP_COGNITIVE_COMMERCE_PLAN.md for the full design and the list
of things (exact tool names/argument shapes, production access, a real
domain for the OAuth redirect URI) that still need verifying live before
this should ever be turned on against a real Swiggy account.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone

from . import mcp_client, oauth
from .crypto_utils import decrypt, encrypt
from .mcp_client import MCPToolError
from .oauth import SERVERS


class CommerceError(Exception):
    """Raised from an explicit user action (connect/confirm/disconnect) --
    routes turn this into a clear error response, never a silent failure."""


class NotConnectedError(CommerceError):
    def __init__(self, server: str):
        super().__init__(f"No linked Swiggy account for '{server}'. Connect it in Settings first.")
        self.server = server


def is_enabled() -> bool:
    return os.getenv("SWIGGY_MCP_ENABLED", "false").strip().lower() == "true"


# ---------------------------------------------------------------------------
# Intent detection -- cheap regex gate, same shape as _NEARBY_INTENT_RE /
# _detect_nearby_category in app.py. Checked only after is_enabled(), so a
# disabled deployment pays zero cost (not even the regex compile is touched
# on the hot path, since app.py only calls into this module at all when the
# flag is on).
# ---------------------------------------------------------------------------
_FOOD_INTENT_RE = re.compile(
    r"\border\s+(?:food|something|dinner|lunch|breakfast|in)\b|"
    r"\b(?:i'?m|im)\s+hungry\b|\bfeel like eating\b|\bswiggy\b",
    re.IGNORECASE,
)
_INSTAMART_INTENT_RE = re.compile(
    r"\binstamart\b|\border\s+groceries\b|\bquick\s+commerce\b|\border\s+snacks\b",
    re.IGNORECASE,
)
_DINEOUT_INTENT_RE = re.compile(
    r"\bdineout\b|\bbook\s+a\s+table\b|\btable\s+reservation\b|\breserve\s+a\s+table\b|\bdine\s+out\b",
    re.IGNORECASE,
)


def detect_intent(prompt_lower: str) -> str | None:
    """Returns 'food' | 'im' | 'dineout' | None. Checked in this order --
    Instamart/Dineout keywords are more specific and should win over the
    generic food-intent regex when both could plausibly match."""
    if _INSTAMART_INTENT_RE.search(prompt_lower):
        return "im"
    if _DINEOUT_INTENT_RE.search(prompt_lower):
        return "dineout"
    if _FOOD_INTENT_RE.search(prompt_lower):
        return "food"
    return None


_SERVER_LABEL = {"food": "Swiggy Food", "im": "Swiggy Instamart", "dineout": "Swiggy Dineout"}


def _tool_name(server: str, kind: str, default: str) -> str:
    """Tool names aren't fully published (see plan doc) -- every one is a
    .env override so a documentation gap or a future rename is a config
    change, not a code change. kind is 'SEARCH' | 'ORDER' | 'ADDRESSES'."""
    return os.getenv(f"SWIGGY_{server.upper()}_{kind}_TOOL", default)


_DEFAULT_SEARCH_TOOL = {"food": "search_restaurants", "im": "search_items", "dineout": "search_availability"}
_DEFAULT_ORDER_TOOL = {"food": "place_order", "im": "place_order", "dineout": "create_reservation"}


# ---------------------------------------------------------------------------
# Account / token management
# ---------------------------------------------------------------------------

def _get_account(cur, user_id: int, server: str) -> dict | None:
    cur.execute(
        "SELECT * FROM swiggy_accounts WHERE user_id = %s AND server = %s AND revoked_at IS NULL",
        (user_id, server),
    )
    return cur.fetchone()


def get_status(cur, user_id: int) -> dict:
    """Status per server, for the Settings screen. enabled=False short-
    circuits everything else -- both frontends hide the whole feature when
    this comes back false, so a deployment with the flag off shows nothing
    new to any user."""
    if not is_enabled():
        return {"enabled": False, "accounts": {}}
    accounts = {}
    for server in SERVERS:
        account = _get_account(cur, user_id, server)
        accounts[server] = {
            "connected": account is not None,
            "connected_at": account["connected_at"].isoformat() if account else None,
        }
    return {"enabled": True, "accounts": accounts}


def _ensure_fresh_token(cur, db, user_id: int, server: str, account: dict) -> str:
    expires_at = account["token_expires_at"]
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at > datetime.now(timezone.utc) + timedelta(seconds=60):
            return decrypt(account["access_token_encrypted"])
    if not account["refresh_token_encrypted"]:
        return decrypt(account["access_token_encrypted"])  # no refresh token -- best effort with what we have

    client = oauth.get_or_register_client(cur, server)
    token_response = oauth.refresh_access_token(
        client["token_endpoint"], client["client_id"], client["client_secret"],
        decrypt(account["refresh_token_encrypted"]),
    )
    new_access = token_response["access_token"]
    new_refresh = token_response.get("refresh_token")
    expires_in = token_response.get("expires_in")
    new_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in) if expires_in else None
    cur.execute(
        "UPDATE swiggy_accounts SET access_token_encrypted = %s, "
        "refresh_token_encrypted = COALESCE(%s, refresh_token_encrypted), token_expires_at = %s "
        "WHERE user_id = %s AND server = %s",
        (encrypt(new_access), encrypt(new_refresh) if new_refresh else None, new_expiry, user_id, server),
    )
    db.commit()
    return new_access


# ---------------------------------------------------------------------------
# OAuth connect / callback / disconnect -- explicit user actions, errors
# propagate as CommerceError rather than being swallowed.
# ---------------------------------------------------------------------------

def get_authorize_url(cur, db, user_id: int, server: str) -> str:
    if server not in SERVERS:
        raise CommerceError(f"Unknown Swiggy server '{server}'.")
    client = oauth.get_or_register_client(cur, server)
    db.commit()  # persist a freshly-registered client before redirecting away
    verifier, challenge = oauth.generate_pkce_pair()
    state = encrypt(f"{user_id}:{server}:{os.urandom(8).hex()}")  # opaque, unguessable state token
    cur.execute(
        "INSERT INTO swiggy_oauth_pending (state, user_id, server, code_verifier) VALUES (%s, %s, %s, %s)",
        (state, user_id, server, verifier),
    )
    db.commit()
    return oauth.build_authorize_url(client["client_id"], client["authorize_endpoint"], state, challenge)


def handle_callback(cur, db, code: str, state: str) -> dict:
    cur.execute("SELECT * FROM swiggy_oauth_pending WHERE state = %s", (state,))
    pending = cur.fetchone()
    if not pending:
        raise CommerceError("This Swiggy connection attempt has expired or is invalid. Please try connecting again.")
    cur.execute("DELETE FROM swiggy_oauth_pending WHERE state = %s", (state,))  # single use

    server, user_id = pending["server"], pending["user_id"]
    client = oauth.get_or_register_client(cur, server)
    token_response = oauth.exchange_code(
        client["token_endpoint"], client["client_id"], client["client_secret"],
        code, pending["code_verifier"],
    )
    access_token = token_response["access_token"]
    refresh_token = token_response.get("refresh_token")
    expires_in = token_response.get("expires_in")
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in) if expires_in else None

    default_address_id = None
    try:
        addresses = mcp_client.call_tool(
            server, _tool_name(server, "ADDRESSES", "get_addresses"), {}, access_token
        )
        default_address_id = _first_address_id(addresses)
    except Exception:
        pass  # address prefetch is a convenience, not required to complete linking

    cur.execute(
        "INSERT INTO swiggy_accounts (user_id, server, access_token_encrypted, refresh_token_encrypted, "
        "token_expires_at, default_address_id) VALUES (%s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (user_id, server) DO UPDATE SET access_token_encrypted = EXCLUDED.access_token_encrypted, "
        "refresh_token_encrypted = EXCLUDED.refresh_token_encrypted, token_expires_at = EXCLUDED.token_expires_at, "
        "default_address_id = COALESCE(EXCLUDED.default_address_id, swiggy_accounts.default_address_id), "
        "revoked_at = NULL",
        (user_id, server, encrypt(access_token), encrypt(refresh_token) if refresh_token else None,
         expires_at, default_address_id),
    )
    db.commit()
    return {"user_id": user_id, "server": server}


def disconnect(cur, db, user_id: int, server: str) -> None:
    cur.execute(
        "UPDATE swiggy_accounts SET revoked_at = now() WHERE user_id = %s AND server = %s AND revoked_at IS NULL",
        (user_id, server),
    )
    db.commit()


def _first_address_id(tool_result: dict) -> str | None:
    for item in _extract_items(tool_result):
        for key in ("id", "addressId", "address_id"):
            if isinstance(item, dict) and item.get(key):
                return str(item[key])
    return None


# ---------------------------------------------------------------------------
# Chat integration -- passive path, called from chat_global(). Swallows its
# own errors: a flaky Swiggy call must never break the rest of the chat
# response, exactly like ei_context/nearby_context already guarantee.
# ---------------------------------------------------------------------------

def build_commerce_context(cur, db, user_id: int, prompt: str, server: str, ei_context: str) -> tuple[str, dict | None]:
    try:
        return _build_commerce_context_unsafe(cur, db, user_id, prompt, server, ei_context)
    except Exception:
        return "", None


def _hour_bucket(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 22:
        return "evening"
    return "late night"


def _build_commerce_context_unsafe(cur, db, user_id: int, prompt: str, server: str, ei_context: str) -> tuple[str, dict | None]:
    label = _SERVER_LABEL[server]
    account = _get_account(cur, user_id, server)
    if not account:
        text = (
            f"The user's message suggests they might want to use {label}, but they haven't connected "
            f"their Swiggy account yet. Mention that connecting it in Settings would let you actually "
            f"look up and suggest real options -- don't invent restaurants or items."
        )
        return text, None

    access_token = _ensure_fresh_token(cur, db, user_id, server, account)
    hour = datetime.now().astimezone().hour
    need_bits = [f"time of day: {_hour_bucket(hour)}"]
    if ei_context:
        need_bits.append(f"mood/context signal: {ei_context.strip()[:300]}")
    inferred_need = "; ".join(need_bits)

    search_args = {"query": prompt.strip()}
    if account["default_address_id"]:
        search_args["addressId"] = account["default_address_id"]
    raw_result = mcp_client.call_tool(server, _tool_name(server, "SEARCH", _DEFAULT_SEARCH_TOOL[server]), search_args, access_token)
    items = _summarize_items(raw_result, limit=3)
    if not items:
        text = (
            f"Searched {label} based on the user's message, but found no matching results. "
            f"Say so plainly rather than inventing an option."
        )
        return text, None

    cur.execute(
        "INSERT INTO commerce_actions (user_id, provider, server, action, inferred_need, item_summary, item_ref) "
        "VALUES (%s, 'swiggy', %s, 'suggested', %s, %s, %s) RETURNING id",
        (user_id, server, inferred_need, "; ".join(i["label"] for i in items), json.dumps({"items": items, "search_args": search_args})),
    )
    action_id = cur.fetchone()["id"]
    db.commit()

    lines = [
        f"Real {label} results found for this request (inferred need: {inferred_need}). "
        f"Explain briefly WHY these fit (time of day / mood, if relevant), then let the app's own "
        f"action card show the actual options and an order/confirm button -- don't repeat every "
        f"detail as plain text, and don't claim an order was placed (nothing is ordered until the "
        f"user explicitly confirms):",
    ]
    for item in items:
        lines.append(f"- {item['label']}")
    context_text = "\n".join(lines)

    action_card = {"id": action_id, "server": server, "need": inferred_need, "items": items}
    return context_text, action_card


def _extract_items(tool_result: dict) -> list:
    """MCP tool result content isn't uniformly shaped across servers/tools
    (unconfirmed in the public docs -- see plan doc). Defensively looks for
    the first list-of-dicts it can find, in the MCP 'content' blocks or a
    couple of common top-level keys, rather than assuming one exact shape."""
    content = tool_result.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                try:
                    parsed = json.loads(block["text"])
                except (json.JSONDecodeError, TypeError, KeyError):
                    continue
                found = _find_list_of_dicts(parsed)
                if found:
                    return found
    return _find_list_of_dicts(tool_result) or []


def _find_list_of_dicts(value, depth: int = 0) -> list | None:
    if depth > 3:
        return None
    if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
        return value
    if isinstance(value, dict):
        for v in value.values():
            found = _find_list_of_dicts(v, depth + 1)
            if found:
                return found
    return None


def _summarize_items(tool_result: dict, limit: int) -> list[dict]:
    items = _extract_items(tool_result)[:limit]
    summarized = []
    for item in items:
        name = item.get("name") or item.get("title") or item.get("restaurantName") or str(item)[:60]
        price = item.get("price") or item.get("cost") or item.get("priceForTwo")
        rating = item.get("rating") or item.get("avgRating")
        label_bits = [str(name)]
        if price is not None:
            label_bits.append(f"₹{price}")
        if rating is not None:
            label_bits.append(f"{rating}★")
        summarized.append({"label": " -- ".join(label_bits), "raw": item})
    return summarized


# ---------------------------------------------------------------------------
# Confirm -- the ONLY code path allowed to call an order-placing tool.
# Explicit action: errors propagate, never swallowed.
# ---------------------------------------------------------------------------

def dismiss_action(cur, db, user_id: int, action_id: int) -> None:
    cur.execute(
        "UPDATE commerce_actions SET action = 'dismissed', resolved_at = now() "
        "WHERE id = %s AND user_id = %s AND action = 'suggested' AND resolved_at IS NULL",
        (action_id, user_id),
    )
    db.commit()


def confirm_action(cur, db, user_id: int, action_id: int) -> dict:
    # Atomic claim: if this returns no row, the suggestion was already
    # confirmed or dismissed (or never existed / belongs to someone else) --
    # guarantees a double-tap or a replayed action_card id can never place
    # two orders from the same suggestion.
    cur.execute(
        "UPDATE commerce_actions SET resolved_at = now() "
        "WHERE id = %s AND user_id = %s AND action = 'suggested' AND resolved_at IS NULL "
        "RETURNING *",
        (action_id, user_id),
    )
    row = cur.fetchone()
    db.commit()
    if not row:
        raise CommerceError("This suggestion is no longer actionable (already ordered, dismissed, or expired).")

    server = row["server"]
    account = _get_account(cur, user_id, server)
    if not account:
        raise NotConnectedError(server)

    item_ref = row["item_ref"] or {}
    try:
        access_token = _ensure_fresh_token(cur, db, user_id, server, account)
        # NOTE: the real Swiggy "order food end-to-end" flow reportedly
        # chains ~7 tools (cart -> checkout -> order per the plan doc) --
        # exact chain unconfirmed. This calls a single configurable "order"
        # tool with the stored search args as a starting point; once the
        # real multi-step chain is confirmed via a live tools/list call,
        # extend this function to call them in sequence rather than
        # widening its contract speculatively now.
        order_args = dict(item_ref.get("search_args") or {})
        result = mcp_client.call_tool(server, _tool_name(server, "ORDER", _DEFAULT_ORDER_TOOL[server]), order_args, access_token)
        external_order_id = _extract_order_id(result)
    except Exception as e:
        cur.execute(
            "INSERT INTO commerce_actions (user_id, provider, server, action, inferred_need, item_summary, item_ref, resolved_at) "
            "VALUES (%s, 'swiggy', %s, 'order_failed', %s, %s, %s, now())",
            (user_id, server, row["inferred_need"], row["item_summary"], json.dumps(item_ref)),
        )
        db.commit()
        raise CommerceError(f"Order could not be placed: {e}") from e

    cur.execute(
        "INSERT INTO commerce_actions (user_id, provider, server, action, inferred_need, item_summary, item_ref, external_order_id, resolved_at) "
        "VALUES (%s, 'swiggy', %s, 'order_placed', %s, %s, %s, %s, now())",
        (user_id, server, row["inferred_need"], row["item_summary"], json.dumps(item_ref), external_order_id),
    )
    db.commit()
    return {"status": "order_placed", "external_order_id": external_order_id, "item_summary": row["item_summary"]}


def _extract_order_id(tool_result: dict) -> str | None:
    for item in [tool_result] + _extract_items(tool_result):
        if isinstance(item, dict):
            for key in ("orderId", "order_id", "id"):
                if item.get(key):
                    return str(item[key])
    return None
