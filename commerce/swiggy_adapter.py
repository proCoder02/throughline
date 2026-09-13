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
    # "order me X" / "get me X" / "bring me X" -- confirmed live that plain
    # "order food" alone missed natural phrasing real users actually type
    # ("order me icecream"). Deliberately NOT matching a bare "i want X"
    # here: that phrase is far too common in ordinary conversation and
    # would fire on unrelated messages ("I want to talk about my day"),
    # unlike "order/get/bring me/us ___" which is unambiguously about
    # acquiring something.
    r"\b(?:order|get|bring)\s+(?:me|us)\s+\S|"
    r"\b(?:i'?m|im)\s+hungry\b|\bfeel like eating\b|\bswiggy\b|\bcraving\b",
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
_TRACK_ORDER_INTENT_RE = re.compile(
    r"\btrack\s+(?:my\s+)?order\b|\bwhere\s+is\s+my\s+order\b|\border\s+status\b|"
    r"\bis\s+my\s+order\b|\bhow('?s| is)\s+my\s+(?:food\s+)?order\b",
    re.IGNORECASE,
)


def is_track_order_intent(prompt_lower: str) -> bool:
    return bool(_TRACK_ORDER_INTENT_RE.search(prompt_lower))


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
# Confirmed live 2026-08-30 via a real MCP client (Claude Desktop connector)
# against the Food server's actual tools/list -- 20 tools total, real flow
# is a genuine cart/checkout chain (search restaurant -> browse its menu ->
# add item(s) to cart -> place order), not a single "order this item" call.
# Instamart/Dineout tool names below are still unconfirmed best-effort
# guesses (see plan doc) -- only Food has been verified against a live
# tools/list response.
_DEFAULT_MENU_TOOL = {"food": "get_restaurant_menu"}
_DEFAULT_CART_TOOL = {"food": "update_food_cart"}
_DEFAULT_ORDER_TOOL = {"food": "place_food_order", "im": "place_order", "dineout": "create_reservation"}
# UPI path only (Cash is unavailable on this account -- confirmed live, see
# plan doc): payment_status polls Swiggy after the user scans/pays, and
# confirm_order finalizes once payment succeeds. Argument names are per
# their docs' order-food-recipe tool list; exact response shape for the
# QR/payment reference is NOT yet confirmed live (see check_payment_status
# below) -- defensive extraction, refine once a real attempt reveals it.
_DEFAULT_PAYMENT_STATUS_TOOL = {"food": "check_payment_status"}
_DEFAULT_CONFIRM_ORDER_TOOL = {"food": "confirm_order"}
_DEFAULT_TRACK_ORDER_TOOL = {"food": "track_food_order"}


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

    # Swiggy's docs explicitly state refresh_token issuance/exchange isn't
    # wired in v1.0 (advertised in metadata but /auth/token only accepts
    # authorization_code) -- attempting it would just fail server-side.
    # Access tokens last 5 days; treat an expired one as needing a full
    # reconnect rather than a silent refresh.
    try:
        client = oauth.get_or_register_client(cur, server)
        token_response = oauth.refresh_access_token(
            client["token_endpoint"], client["client_id"], client["client_secret"],
            decrypt(account["refresh_token_encrypted"]),
        )
    except Exception:
        raise NotConnectedError(server)
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
    try:
        client = oauth.get_or_register_client(cur, server)
    except CommerceError:
        raise
    except Exception as e:
        # OAuth discovery/registration hits a real external service whose
        # exact endpoint shapes aren't fully documented (see plan doc) --
        # never let a raw connection/HTTP error surface as an unhandled 500;
        # the user tapped a button and needs SOME clear answer.
        raise CommerceError(f"Couldn't reach Swiggy's authorization service: {e}") from e
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
    except Exception as e:
        # Never break the chat response over this -- but a silently
        # swallowed exception here is indistinguishable from a genuine
        # "no results" outcome from the user's side, which made a real
        # intermittent failure impossible to diagnose. Print, don't raise.
        print(f"[commerce] build_commerce_context failed for user={user_id} server={server}: {e!r}")
        return "", None


def _hour_bucket(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 22:
        return "evening"
    return "late night"


# search_restaurants matches much better against a clean keyword ("biryani",
# "dessert") than a full sentence -- confirmed live: "i want to eat some
# deserts" as a raw query returned nothing, while "dessert" alone works.
# Deliberately conservative: strips common filler phrasing rather than
# attempting real NLP extraction, and always falls back to the original
# prompt if stripping leaves nothing usable.
_FOOD_QUERY_FILLER_RE = re.compile(
    r"\b(i want to|i'd like to|i would like to|can you|could you|please|"
    r"order (?:me |us )?(?:some |something )?|get (?:me |us )?(?:some |something )?|"
    r"i'?m hungry,?|i am hungry,?|feel like eating|eat some|eat something|"
    r"something|some|for me|to eat|to order)\b",
    re.IGNORECASE,
)


def _clean_food_query(prompt: str) -> str:
    cleaned = _FOOD_QUERY_FILLER_RE.sub(" ", prompt)
    cleaned = re.sub(r"\bdeserts?\b", "dessert", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.")
    return cleaned or prompt.strip() or "food"


def _first_present(d: dict, keys: tuple[str, ...]):
    for k in keys:
        if d.get(k) is not None:
            return d[k]
    return None


def _search_food(access_token: str, address_id: str | None, prompt: str) -> tuple[list[dict], dict | None]:
    """Real 2-step Food flow, confirmed live via a real tools/list call
    (Claude Desktop connector, 2026-08-30): search_restaurants finds
    candidate restaurants for a query, get_restaurant_menu lists a specific
    restaurant's items. Returns (display_items, order_ref) -- each item in
    display_items carries its own menu_item_id so the user can choose which
    one to order (not just the top result); order_ref carries the shared
    addressId/restaurantId/restaurantName confirm_action needs alongside
    whichever menu_item_id the user actually picks."""
    if not address_id:
        return [], None
    query = _clean_food_query(prompt)
    restaurants_raw = mcp_client.call_tool(
        "food", _tool_name("food", "SEARCH", _DEFAULT_SEARCH_TOOL["food"]),
        {"addressId": address_id, "query": query}, access_token,
    )
    restaurants = _extract_items(restaurants_raw)
    # Confirmed live: restaurants carry availabilityStatus -- their own
    # "order food end-to-end" recipe doc explicitly says to only recommend
    # ones marked OPEN. Missing status is kept (fail open) rather than
    # dropped, in case a field is absent on some response variant.
    restaurants = [r for r in restaurants if r.get("availabilityStatus", "OPEN") == "OPEN"]
    # Confirmed live: a generic category query (e.g. "dessert") can surface
    # individual product/item-catalog entries mixed in with genuine
    # restaurants -- those carry only {id, name, cuisines: []}, no rating/
    # cuisine data at all, and their id isn't a valid restaurantId for
    # get_restaurant_menu (a specific dish name like "biryani" doesn't hit
    # this). Require at least one sign of being a real restaurant entry.
    restaurants = [r for r in restaurants if r.get("cuisines") or r.get("avgRating") is not None]
    if not restaurants:
        return [], None
    top = restaurants[0]
    restaurant_id = _first_present(top, ("restaurantId", "id", "restaurant_id"))
    restaurant_name = _first_present(top, ("name", "restaurantName")) or "the restaurant"
    if not restaurant_id:
        return [], None

    menu_raw = mcp_client.call_tool(
        "food", _tool_name("food", "MENU", _DEFAULT_MENU_TOOL["food"]),
        {"addressId": address_id, "restaurantId": restaurant_id, "pageSize": 5}, access_token,
    )
    # Confirmed live: get_restaurant_menu returns CATEGORIES
    # ({title, categoryId, items: [...]}), not a flat item list -- the
    # generic list-of-dicts extraction correctly finds the categories
    # array, but the real orderable items are one level deeper, inside
    # each category's own `items`.
    categories = _extract_items(menu_raw)
    menu_items = [item for cat in categories for item in (cat.get("items") or []) if item.get("inStock", 1)]
    if not menu_items:
        return [], None

    # Each item carries its OWN menu_item_id so the user can pick which one
    # to order, rather than "Order this" always meaning "order the top
    # result" -- confirmed live via user feedback that a single blanket
    # button with no choice was a real usability gap once 3 distinct real
    # options were being shown.
    display_items = []
    for item in menu_items[:3]:
        item_id = _first_present(item, ("menu_item_id", "id", "itemId"))
        if not item_id:
            continue
        name = _first_present(item, ("name", "itemName")) or "item"
        price = _first_present(item, ("price", "finalPrice"))
        rating = _first_present(item, ("rating", "avgRating"))
        bits = [f"{name} from {restaurant_name}"]
        if price is not None:
            bits.append(f"₹{price}")
        if rating is not None:
            bits.append(f"{rating}★")
        display_items.append({"label": " -- ".join(bits), "menu_item_id": item_id})

    if not display_items:
        return [], None
    order_ref = {
        "addressId": address_id,
        "restaurantId": restaurant_id,
        "restaurantName": restaurant_name,
    }
    return display_items, order_ref


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

    if server == "food":
        items, order_ref = _search_food(access_token, account["default_address_id"], prompt)
        # item_ref stores the shared restaurant/address info AND the full
        # items list (each with its own menu_item_id) together -- confirm
        # time needs to know both which restaurant/address AND which of
        # the several shown items the user actually picked.
        item_ref = {**order_ref, "items": items} if order_ref else None
    else:
        # Instamart/Dineout: single best-effort call -- tool names AND
        # argument shapes are still unconfirmed guesses (only Food's has
        # been verified against a live tools/list response; see plan doc).
        search_args = {"query": prompt.strip()}
        if account["default_address_id"]:
            search_args["addressId"] = account["default_address_id"]
        raw_result = mcp_client.call_tool(server, _tool_name(server, "SEARCH", _DEFAULT_SEARCH_TOOL[server]), search_args, access_token)
        items = _summarize_items(raw_result, limit=3)
        item_ref = {"search_args": search_args} if items else None

    if not items or not item_ref:
        text = (
            f"Searched {label} based on the user's message, but found no matching results. "
            f"Say so plainly rather than inventing an option."
        )
        return text, None

    cur.execute(
        "INSERT INTO commerce_actions (user_id, provider, server, action, inferred_need, item_summary, item_ref) "
        "VALUES (%s, 'swiggy', %s, 'suggested', %s, %s, %s) RETURNING id",
        (user_id, server, inferred_need, "; ".join(i["label"] for i in items), json.dumps(item_ref)),
    )
    action_id = cur.fetchone()["id"]
    db.commit()

    lines = [
        f"Real {label} results found for this request (inferred need: {inferred_need}). "
        f"Write ONLY a brief sentence explaining why these fit (time of day / mood, if relevant) "
        f"-- do not list the items, do not write their names/prices/ratings again, and do not "
        f"describe, name, or format any button, link, or UI control of any kind (no '[Order]', "
        f"no 'tap the card', no brackets or button-like text at all). The app itself renders a "
        f"real, separate, already-interactive card with the items and the actual buttons right "
        f"after your reply -- your only job is the one-sentence 'why', nothing else. Never claim "
        f"an order was placed (nothing is ordered until the user taps that real card):",
    ]
    for item in items:
        lines.append(f"- {item['label']}")
    context_text = "\n".join(lines)

    action_card = {"id": action_id, "server": server, "need": inferred_need, "items": items}
    return context_text, action_card


def _extract_items(tool_result: dict) -> list:
    """Confirmed live against real Swiggy Food responses (get_addresses,
    2026-08-30): the actual machine-readable data lives in
    `structuredContent`, separate from `content`, which carries a
    human-readable text summary meant for direct LLM/user display (e.g.
    "Found 16 saved addresses...") -- NOT parseable JSON, and NOT safe to
    recurse into generically, since content's own [{type, text}, ...]
    wrapper list itself looks like "a list of dicts" and was being matched
    by mistake before structuredContent was ever checked. Always prefer
    structuredContent; content-as-embedded-JSON is a defensive fallback
    only, for any tool that might not follow this shape."""
    structured = tool_result.get("structuredContent")
    if isinstance(structured, dict):
        found = _find_list_of_dicts(structured)
        if found:
            return found

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
    return []


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


def _find_value_anywhere(value, key_predicate, depth: int = 0):
    """Recursively search a nested MCP tool result for the first scalar
    value whose key matches key_predicate -- used for the UPI payment
    reference/QR fields, whose exact location in place_food_order's
    response isn't confirmed live yet (see _place_food_order)."""
    if depth > 4:
        return None
    if isinstance(value, dict):
        for k, v in value.items():
            if key_predicate(k) and v is not None and not isinstance(v, (dict, list)):
                return v
        for v in value.values():
            found = _find_value_anywhere(v, key_predicate, depth + 1)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_value_anywhere(item, key_predicate, depth + 1)
            if found is not None:
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


def confirm_action(cur, db, user_id: int, action_id: int, menu_item_id: str | None = None) -> dict:
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
    # The card showed up to 3 real items -- the user picks exactly one via
    # menu_item_id; only trust an id that was actually offered on this
    # suggestion (never take the client's word for an arbitrary id), and
    # fall back to the first/top item if none was specified (keeps the
    # single-button UI path working unchanged).
    offered_items = item_ref.get("items") or []
    chosen_item = None
    if server == "food" and offered_items:
        if menu_item_id is not None:
            chosen_item = next((i for i in offered_items if str(i.get("menu_item_id")) == str(menu_item_id)), None)
            if chosen_item is None:
                raise CommerceError("That item wasn't one of the options offered -- ask again to get a fresh suggestion.")
        else:
            chosen_item = offered_items[0]

    item_summary = chosen_item["label"] if chosen_item else row["item_summary"]
    try:
        access_token = _ensure_fresh_token(cur, db, user_id, server, account)
        if server == "food":
            outcome = _place_food_order(access_token, item_ref, chosen_item)
        else:
            # Instamart/Dineout: single best-effort call -- unconfirmed,
            # see the SEARCH-side note in _build_commerce_context_unsafe.
            order_args = dict(item_ref.get("search_args") or {})
            result = mcp_client.call_tool(server, _tool_name(server, "ORDER", _DEFAULT_ORDER_TOOL[server]), order_args, access_token)
            outcome = {"status": "order_placed", "order_id": _extract_order_id(result)}
    except Exception as e:
        cur.execute(
            "INSERT INTO commerce_actions (user_id, provider, server, action, inferred_need, item_summary, item_ref, resolved_at) "
            "VALUES (%s, 'swiggy', %s, 'order_failed', %s, %s, %s, now())",
            (user_id, server, row["inferred_need"], item_summary, json.dumps(item_ref)),
        )
        db.commit()
        raise CommerceError(f"Order could not be placed: {e}") from e

    if outcome["status"] == "awaiting_payment":
        # UPI needs a real payment step before an order actually exists --
        # write a distinct row (not order_placed) and hand back the real
        # payment link Swiggy gave us; the client polls
        # /commerce/swiggy/payment-status against THIS row's id until it
        # resolves. Never claim success here -- nothing has happened yet.
        # confirm_context (orderId/lat/lng) is folded into item_ref now --
        # confirm_order needs those echoed back later, and this is the one
        # row check_payment_status will read them from.
        item_ref_with_payment = {**item_ref, **(outcome.get("confirm_context") or {})}
        cur.execute(
            "INSERT INTO commerce_actions (user_id, provider, server, action, inferred_need, item_summary, item_ref, payment_ref) "
            "VALUES (%s, 'swiggy', %s, 'awaiting_payment', %s, %s, %s, %s) RETURNING id",
            (user_id, server, row["inferred_need"], item_summary, json.dumps(item_ref_with_payment), outcome["payment_ref"]),
        )
        payment_action_id = cur.fetchone()["id"]
        db.commit()
        return {
            "status": "awaiting_payment",
            "payment_action_id": payment_action_id,
            "payment_link": outcome.get("payment_link"),
            "item_summary": item_summary,
        }

    external_order_id = outcome.get("order_id")
    cur.execute(
        "INSERT INTO commerce_actions (user_id, provider, server, action, inferred_need, item_summary, item_ref, external_order_id, resolved_at) "
        "VALUES (%s, 'swiggy', %s, 'order_placed', %s, %s, %s, %s, now()) RETURNING id",
        (user_id, server, row["inferred_need"], item_summary, json.dumps(item_ref), external_order_id),
    )
    order_row_id = cur.fetchone()["id"]
    db.commit()
    return {
        "status": "order_placed",
        "action_id": order_row_id,  # for track_order -- id of the 'order_placed' row itself
        "external_order_id": external_order_id,
        "item_summary": item_summary,
    }


def check_payment_status(cur, db, user_id: int, payment_action_id: int) -> dict:
    """Polled by the client every ~10s+ (matching their own docs' polling
    guidance for track_food_order) while a QR/payment-link is shown.
    Finalizes with confirm_order the moment Swiggy reports success --
    exact field names for both the status value and confirm_order's
    response are unconfirmed live (COD was the only path actually
    completed before this was written); defensive extraction throughout,
    refine once a real UPI attempt reveals the true shape."""
    cur.execute(
        "SELECT * FROM commerce_actions WHERE id = %s AND user_id = %s",
        (payment_action_id, user_id),
    )
    row = cur.fetchone()
    if not row:
        raise CommerceError("No pending payment found for this reference.")
    if row["action"] != "awaiting_payment":
        # Already finalized -- e.g. a second poll arriving just after a
        # concurrent one completed it. Report the real outcome rather than
        # erroring, so the client's poll loop just sees its terminal state.
        return {"status": row["action"], "action_id": payment_action_id, "external_order_id": row["external_order_id"], "item_summary": row["item_summary"]}

    server = row["server"]
    account = _get_account(cur, user_id, server)
    if not account:
        raise NotConnectedError(server)
    access_token = _ensure_fresh_token(cur, db, user_id, server, account)
    item_ref = row["item_ref"] or {}
    address_id = item_ref.get("addressId")

    status_result = mcp_client.call_tool(
        "food", _tool_name("food", "PAYMENT_STATUS", _DEFAULT_PAYMENT_STATUS_TOOL["food"]),
        {"paasId": row["payment_ref"], "addressId": address_id}, access_token,
    )
    # Confirmed live against Swiggy's own reference docs
    # (mcp.swiggy.com/builders/docs/reference/food/check_payment_status/):
    # the docs explicitly say to check these two boolean flags rather than
    # match against an enumerated `status` string, which they don't fully
    # document anyway.
    is_success = bool(_find_value_anywhere(status_result, lambda k: k == "isTerminalSuccess"))
    is_failure = bool(_find_value_anywhere(status_result, lambda k: k == "isTerminalFailure"))

    if is_success:
        # Atomic claim before calling confirm_order -- if concurrent polls
        # both observe "success" (e.g. two browser tabs), only one may
        # proceed to actually finalize; the other sees 0 rows updated and
        # falls through to re-reporting the now-finalized state below.
        cur.execute(
            "UPDATE commerce_actions SET resolved_at = now() "
            "WHERE id = %s AND action = 'awaiting_payment' AND resolved_at IS NULL",
            (payment_action_id,),
        )
        claimed = cur.rowcount > 0
        db.commit()
        if not claimed:
            cur.execute("SELECT action, external_order_id, item_summary FROM commerce_actions WHERE id = %s", (payment_action_id,))
            latest = cur.fetchone()
            return {"status": latest["action"], "action_id": payment_action_id, "external_order_id": latest["external_order_id"], "item_summary": latest["item_summary"]}

        # Food confirm_order does NOT take paasId at all (that's IM/Dineout
        # only, per its own docs) -- it needs orderId + lat + lng echoed
        # back from place_food_order's response, stashed into item_ref
        # when the awaiting_payment row was created.
        confirm_result = mcp_client.call_tool(
            "food", _tool_name("food", "CONFIRM_ORDER", _DEFAULT_CONFIRM_ORDER_TOOL["food"]),
            {
                "orderId": item_ref.get("orderId"),
                "addressId": address_id,
                "lat": item_ref.get("lat"),
                "lng": item_ref.get("lng"),
            },
            access_token,
        )
        order_id = _find_value_anywhere(confirm_result, lambda k: k == "orderId") or _extract_order_id(confirm_result)
        cur.execute(
            "UPDATE commerce_actions SET action = 'order_placed', external_order_id = %s WHERE id = %s",
            (order_id, payment_action_id),
        )
        db.commit()
        return {
            "status": "order_placed",
            "action_id": payment_action_id,  # same row, now finalized -- for track_order
            "external_order_id": order_id,
            "item_summary": row["item_summary"],
        }

    if is_failure:
        cur.execute(
            "UPDATE commerce_actions SET action = 'order_failed', resolved_at = now() "
            "WHERE id = %s AND resolved_at IS NULL",
            (payment_action_id,),
        )
        db.commit()
        return {"status": "order_failed", "item_summary": row["item_summary"]}

    return {"status": "awaiting_payment", "item_summary": row["item_summary"]}


def track_order(cur, db, user_id: int, action_id: int) -> dict:
    """Live delivery status for an already-placed order -- confirmed live
    against a real order (2026-08-30): title/subtitle/etaText/orderStatus/
    progressPercentage/pollingDuration all real fields. Polled by the
    client at whatever pollingDuration Swiggy suggests (their docs:
    'use the pollingDuration field when present', no fixed mandated
    interval)."""
    cur.execute(
        "SELECT * FROM commerce_actions WHERE id = %s AND user_id = %s AND action = 'order_placed'",
        (action_id, user_id),
    )
    row = cur.fetchone()
    if not row or not row["external_order_id"]:
        raise CommerceError("No placed order found for this reference.")

    server = row["server"]
    account = _get_account(cur, user_id, server)
    if not account:
        raise NotConnectedError(server)
    access_token = _ensure_fresh_token(cur, db, user_id, server, account)

    result = mcp_client.call_tool(
        "food", _tool_name("food", "TRACK_ORDER", _DEFAULT_TRACK_ORDER_TOOL["food"]),
        {"orderId": row["external_order_id"]}, access_token,
    )
    orders = _extract_items(result)
    order_info = next(
        (o for o in orders if str(o.get("orderId")) == str(row["external_order_id"])),
        orders[0] if orders else None,
    )
    if not order_info:
        return {"status": "unknown", "item_summary": row["item_summary"]}
    return {
        "status": "tracking",
        "order_status": order_info.get("orderStatus"),
        "title": order_info.get("title"),
        "subtitle": order_info.get("subtitle"),
        "eta_text": order_info.get("etaText"),
        "progress_percentage": order_info.get("progressPercentage"),
        "polling_duration": order_info.get("pollingDuration"),
        "item_summary": row["item_summary"],
    }


def build_tracking_context(cur, db, user_id: int) -> tuple[str, dict | None]:
    """Passive chat path for 'where is my order' style messages -- finds
    the user's most recent real placed order and returns a live-tracking
    action_card (mode='tracking') the frontend renders directly in its
    tracking view rather than the usual pending/confirm one. Swallows its
    own errors, same contract as build_commerce_context."""
    try:
        cur.execute(
            "SELECT id, server, external_order_id FROM commerce_actions "
            "WHERE user_id = %s AND action = 'order_placed' ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        )
        row = cur.fetchone()
        if not row:
            return (
                "The user asked to track an order, but no placed order was found for them. "
                "Say so plainly rather than inventing a status.",
                None,
            )
        info = track_order(cur, db, user_id, row["id"])
        text = (
            f"Real live tracking info for the user's most recent order: {info.get('title')} "
            f"({info.get('subtitle')}), ETA {info.get('eta_text')}, {info.get('progress_percentage')}% done. "
            f"Mention this briefly in one sentence -- the app shows a live-updating card with the full "
            f"details separately, don't repeat every field as plain text."
        )
        action_card = {
            "id": row["id"], "server": row["server"], "mode": "tracking", "need": "", "items": [], **info,
            "external_order_id": row["external_order_id"],
        }
        return text, action_card
    except Exception:
        return "", None


def _place_food_order(access_token: str, item_ref: dict, chosen_item: dict | None) -> dict:
    """Real Food order chain, confirmed live via tools/list: add the chosen
    item to the cart, then place the order. Defaults to UPI -- Cash-on-
    delivery is confirmed unavailable on this account/region ("cash option
    is temporarily disabled", real live error), so completing an actual
    order requires the user to pay for real. Override
    SWIGGY_FOOD_PAYMENT_METHOD=Cash in .env if it becomes available again
    elsewhere.

    Confirmed live against Swiggy's own reference docs
    (mcp.swiggy.com/builders/docs/reference/food/place_food_order/): there
    is NO QR image in the response at all (an earlier assumption here was
    wrong and matched the unrelated boolean isQrFlow field by accident).
    The real UPI-pending response carries orderId/paasId, a bridgeUrl
    (Swiggy's own hosted payment page, with whatever real QR/UPI UI it
    renders) and an upiIntentUrl (a raw upi:// deep link a mobile OS can
    open directly). confirm_order for Food additionally needs orderId/
    lat/lng echoed back (paasId is NOT accepted there for Food, per its
    own docs) -- these are carried forward via confirm_context.

    Returns {"status": "order_placed", "order_id": ...} for the synchronous
    Cash path, or {"status": "awaiting_payment", "payment_ref": ...,
    "payment_link": ..., "confirm_context": {...}} for UPI."""
    address_id = item_ref.get("addressId")
    restaurant_id = item_ref.get("restaurantId")
    menu_item_id = (chosen_item or {}).get("menu_item_id")
    if not (address_id and restaurant_id and menu_item_id):
        raise RuntimeError("Missing restaurant/item reference -- ask again to get a fresh suggestion.")
    mcp_client.call_tool(
        "food", _tool_name("food", "CART", _DEFAULT_CART_TOOL["food"]),
        {
            "addressId": address_id,
            "restaurantId": restaurant_id,
            "cartItems": [{"menu_item_id": menu_item_id, "quantity": 1}],
        },
        access_token,
    )
    payment_method = os.getenv("SWIGGY_FOOD_PAYMENT_METHOD", "UPI")
    order_args = {"addressId": address_id, "paymentMethod": payment_method}
    if payment_method == "UPI":
        order_args["generateUPIQR"] = True
    result = mcp_client.call_tool(
        "food", _tool_name("food", "ORDER", _DEFAULT_ORDER_TOOL["food"]), order_args, access_token,
    )

    payment_ref = _find_value_anywhere(result, lambda k: k == "paasId")
    if payment_ref:
        bridge_url = _find_value_anywhere(result, lambda k: k == "bridgeUrl")
        upi_intent_url = _find_value_anywhere(result, lambda k: k == "upiIntentUrl")
        return {
            "status": "awaiting_payment",
            "payment_ref": str(payment_ref),
            # bridgeUrl first -- a normal https:// page any browser can
            # open; upiIntentUrl (a upi:// scheme link) only does anything
            # useful on a mobile OS with a UPI app installed.
            "payment_link": bridge_url or upi_intent_url,
            "confirm_context": {
                "orderId": _find_value_anywhere(result, lambda k: k == "orderId"),
                "lat": _find_value_anywhere(result, lambda k: k == "lat"),
                "lng": _find_value_anywhere(result, lambda k: k == "lng"),
            },
        }

    return {"status": "order_placed", "order_id": _extract_order_id(result)}


def _extract_order_id(tool_result: dict) -> str | None:
    for item in [tool_result] + _extract_items(tool_result):
        if isinstance(item, dict):
            for key in ("orderId", "order_id", "id"):
                if item.get(key):
                    return str(item[key])
    return None
