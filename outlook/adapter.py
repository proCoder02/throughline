"""
Outlook mail integration. app.py uses it for its /integrations/outlook/*
routes; the chat reaches its tools through mail_common.py, which needs the
provider surface below (PROVIDER, LABEL, is_enabled, TOOLS,
get_tool_handler, get_account) -- same surface as gmail/adapter.py.

Each user links their own Microsoft account (personal or work/school) via
OAuth; the global-chat agent loop then gets read-only mail tools (list
recent, search, read one) backed by Microsoft Graph with that user's token.

Gated behind OUTLOOK_ENABLED in .env, same contract as
commerce/swiggy_adapter.py's SWIGGY_MCP_ENABLED: with the flag off (the
default), mail_common offers none of these tools so the chat's tool list is
unchanged, every route 404s, and /status reports enabled=false so the
clients hide the feature.

Read-only by construction: the only scope requested is Mail.Read, so even a
prompt-injected email can't make these tools change or send anything in the
mailbox itself. Email content IS untrusted input to the model though -- see
_UNTRUSTED_NOTE.
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from urllib.parse import quote

from mail_common import UNTRUSTED_NOTE

from . import graph
from .graph import GraphError

PROVIDER = "outlook"
LABEL = "Outlook"


class OutlookError(Exception):
    """Raised from an explicit user action (connect/callback) -- routes turn
    this into a clear error response, never a silent failure."""


class NotConnectedError(OutlookError):
    def __init__(self):
        super().__init__("No linked Outlook account. Connect it in Settings first.")


def is_enabled() -> bool:
    return os.getenv("OUTLOOK_ENABLED", "false").strip().lower() == "true"


# ---------------------------------------------------------------------------
# At-rest token encryption. Its own key, deliberately not shared with
# SWIGGY_TOKEN_ENCRYPTION_KEY -- rotating one must never force-rotate the
# other (same reasoning as commerce/crypto_utils.py). Generate with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _fernet():
    from cryptography.fernet import Fernet
    key = os.getenv("OUTLOOK_TOKEN_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("OUTLOOK_TOKEN_ENCRYPTION_KEY is not set -- required whenever OUTLOOK_ENABLED=true.")
    return Fernet(key.encode("ascii"))


def _encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def _decrypt(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")


# ---------------------------------------------------------------------------
# Account / token management
# ---------------------------------------------------------------------------

def _get_account(cur, user_id: int) -> dict | None:
    cur.execute("SELECT * FROM outlook_accounts WHERE user_id = %s AND revoked_at IS NULL", (user_id,))
    return cur.fetchone()


get_account = _get_account  # provider surface for mail_common


def get_status(cur, user_id: int) -> dict:
    if not is_enabled():
        return {"enabled": False}
    account = _get_account(cur, user_id)
    return {
        "enabled": True,
        "connected": account is not None,
        # Still linked, but Microsoft stopped honoring the refresh token
        # (password change, revoked consent, ~90 days idle) -- the client
        # shows a Reconnect state instead of a misleading "Connected".
        "needs_reconnect": bool(account and account.get("needs_reconnect")),
        "email": account["email"] if account else None,
        "connected_at": account["connected_at"].isoformat() if account else None,
    }


def _mark_needs_reconnect(cur, db, user_id: int) -> None:
    cur.execute("UPDATE outlook_accounts SET needs_reconnect = TRUE WHERE user_id = %s", (user_id,))
    db.commit()


def _expiry_from(token_response: dict) -> datetime | None:
    expires_in = token_response.get("expires_in")
    return datetime.now(timezone.utc) + timedelta(seconds=int(expires_in)) if expires_in else None


def _ensure_fresh_token(cur, db, user_id: int) -> str:
    account = _get_account(cur, user_id)
    if not account:
        raise NotConnectedError()
    expires_at = account["token_expires_at"]
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at > datetime.now(timezone.utc) + timedelta(seconds=60):
            return _decrypt(account["access_token_encrypted"])
    if not account["refresh_token_encrypted"]:
        raise NotConnectedError()

    try:
        token_response = graph.refresh_access_token(_decrypt(account["refresh_token_encrypted"]))
    except GraphError:
        # Refresh token revoked/expired (password change, admin revoke, 90
        # days idle) -- the only fix is the user reconnecting.
        _mark_needs_reconnect(cur, db, user_id)
        raise NotConnectedError()
    new_access = token_response["access_token"]
    new_refresh = token_response.get("refresh_token")
    cur.execute(
        "UPDATE outlook_accounts SET access_token_encrypted = %s, "
        "refresh_token_encrypted = COALESCE(%s, refresh_token_encrypted), token_expires_at = %s "
        "WHERE user_id = %s",
        (_encrypt(new_access), _encrypt(new_refresh) if new_refresh else None,
         _expiry_from(token_response), user_id),
    )
    db.commit()
    return new_access


# ---------------------------------------------------------------------------
# OAuth connect / callback / disconnect
# ---------------------------------------------------------------------------

CLIENTS = ("web", "app")


def get_authorize_url(cur, db, user_id: int, client: str = "web") -> str:
    """client decides where the callback lands afterwards: 'web' redirects
    back into the SPA, 'app' (Flutter, signed in via the system browser)
    gets a static "you can close this window" page."""
    if client not in CLIENTS:
        client = "web"
    try:
        verifier, challenge = graph.generate_pkce_pair()
        state = secrets.token_urlsafe(32)
        url = graph.build_authorize_url(state, challenge)
    except RuntimeError as e:
        raise OutlookError(str(e)) from e
    # Abandoned attempts never reach the callback that deletes them.
    cur.execute("DELETE FROM outlook_oauth_pending WHERE created_at < now() - interval '1 hour'")
    cur.execute(
        "INSERT INTO outlook_oauth_pending (state, user_id, code_verifier, client) VALUES (%s, %s, %s, %s)",
        (state, user_id, verifier, client),
    )
    db.commit()
    return url


def pending_client(cur, state: str | None) -> str:
    """Which client started this attempt -- read without consuming it, so
    the callback can pick its response style even on an error redirect."""
    if not state:
        return "web"
    cur.execute("SELECT client FROM outlook_oauth_pending WHERE state = %s", (state,))
    row = cur.fetchone()
    return (row and row.get("client")) or "web"


def friendly_callback_error(error: str | None, description: str | None) -> str:
    """Microsoft's error_description is a long AADSTS dump with trace ids --
    map the cases a real user hits to something they can act on."""
    description = description or ""
    # AADSTS65004 = the user declined the consent screen.
    if error == "access_denied" and ("AADSTS65004" in description or not description):
        return "You cancelled the Microsoft sign-in, so nothing was connected."
    if "AADSTS90094" in description or "AADSTS65001" in description or "admin" in description.lower():
        return ("Your organization requires an admin to approve this app before you can connect "
                "a work or school account. A personal Outlook account works without approval.")
    if error == "access_denied":
        return "Microsoft didn't grant access, so nothing was connected."
    if not error:
        return "Microsoft didn't return an authorization code. Please try connecting again."
    first_line = description.split("\r")[0].split("\n")[0].strip()
    return first_line[:200] or f"Microsoft returned an error ({error})."


def handle_callback(cur, db, code: str, state: str) -> dict:
    """Returns {user_id, email}."""
    cur.execute(
        "DELETE FROM outlook_oauth_pending WHERE state = %s AND created_at > now() - interval '1 hour' RETURNING *",
        (state,),
    )
    pending = cur.fetchone()
    db.commit()  # single use, even if the exchange below fails
    if not pending:
        raise OutlookError("This Outlook connection attempt has expired or is invalid. Please try connecting again.")
    user_id = pending["user_id"]

    try:
        token_response = graph.exchange_code(code, pending["code_verifier"])
    except GraphError as e:
        raise OutlookError(str(e)) from e
    access_token = token_response["access_token"]
    refresh_token = token_response.get("refresh_token")

    email = None
    try:
        me = graph.get("/me", access_token, params={"$select": "mail,userPrincipalName"})
        email = me.get("mail") or me.get("userPrincipalName")
    except GraphError:
        pass  # display-only, not required to complete linking

    cur.execute(
        "INSERT INTO outlook_accounts (user_id, email, access_token_encrypted, refresh_token_encrypted, token_expires_at) "
        "VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (user_id) DO UPDATE SET email = EXCLUDED.email, "
        "access_token_encrypted = EXCLUDED.access_token_encrypted, "
        "refresh_token_encrypted = EXCLUDED.refresh_token_encrypted, "
        "token_expires_at = EXCLUDED.token_expires_at, connected_at = now(), revoked_at = NULL, "
        "needs_reconnect = FALSE",
        (user_id, email, _encrypt(access_token), _encrypt(refresh_token) if refresh_token else None,
         _expiry_from(token_response)),
    )
    db.commit()
    return {"user_id": user_id, "email": email}


def disconnect(cur, db, user_id: int) -> None:
    # Tokens are wiped, not just flagged -- there's nothing to keep them for.
    cur.execute(
        "UPDATE outlook_accounts SET revoked_at = now(), access_token_encrypted = '', "
        "refresh_token_encrypted = NULL WHERE user_id = %s AND revoked_at IS NULL",
        (user_id,),
    )
    db.commit()


# ---------------------------------------------------------------------------
# Agent tools -- plugged into app.py's run_agent_tool_loop via agent_tools()
# and get_tool_handler(). Handlers return {"error": ...} dicts rather than
# raising, same convention as the other _tool_* handlers in app.py, so the
# model can relay a problem (not connected, bad id) as a normal reply.
# ---------------------------------------------------------------------------

_UNTRUSTED_NOTE = UNTRUSTED_NOTE  # every tool result carries it -- see mail_common

_FOLDER_LABELS = {
    "inbox": "inbox", "sentitems": "sent mail", "drafts": "drafts",
    "archive": "archive", "junkemail": "junk mail", "deleteditems": "deleted items",
}
_FOLDERS = set(_FOLDER_LABELS)
_MAX_BODY_CHARS = 8000

_LIST_SELECT = "id,subject,from,receivedDateTime,bodyPreview,isRead,hasAttachments,importance,webLink"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "outlook_list_recent_emails",
            "description": (
                "List the user's recent emails from their linked Outlook account (newest first), with "
                "sender, subject, time and a short preview. Use when the user asks to check/review/"
                "summarize their email or inbox. Use outlook_read_email for an email's full text."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "How many days back to look, 1-30. Default 7."},
                    "unread_only": {"type": "boolean", "description": "Only unread emails. Default false."},
                    "folder": {
                        "type": "string",
                        "description": "inbox (default), sentitems, drafts, archive, junkemail, or deleteditems.",
                    },
                    "limit": {"type": "integer", "description": "Max emails to return, 1-25. Default 15."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "outlook_search_emails",
            "description": (
                "Search the user's whole Outlook mailbox by keyword -- matches sender, subject and body. "
                "Use when the user asks about emails from someone or about a topic "
                "(e.g. 'did HR email me about the offer?')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Keywords, a sender name, or an email address."},
                    "limit": {"type": "integer", "description": "Max emails to return, 1-25. Default 10."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "outlook_read_email",
            "description": "Read one email's full text. message_id must be copied exactly from a list/search result.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_id": {"type": "string", "description": "The email's id, exactly as returned by a list/search tool."},
                },
                "required": ["message_id"],
            },
        },
    },
]


def _clamp(value, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _sender(msg: dict) -> str:
    addr = (msg.get("from") or {}).get("emailAddress") or {}
    name, email = addr.get("name"), addr.get("address")
    if name and email and name != email:
        return f"{name} <{email}>"
    return email or name or "unknown sender"


def _summarize(msg: dict) -> dict:
    return {
        "id": msg.get("id"),
        "from": _sender(msg),
        "subject": msg.get("subject") or "(no subject)",
        "received": msg.get("receivedDateTime"),
        "preview": msg.get("bodyPreview") or "",
        "unread": not msg.get("isRead", True),
        "has_attachments": bool(msg.get("hasAttachments")),
        "important": msg.get("importance") == "high",
        "web_link": msg.get("webLink"),
    }


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _source(email: dict) -> dict:
    """The slice of an email the client shows as a clickable source chip --
    never the preview/body, which only the model needs."""
    return {"provider": PROVIDER, **{k: email.get(k) for k in ("id", "from", "subject", "received", "web_link")}}


_RECONNECT_ACTIVITY = {
    "tool": "outlook", "provider": PROVIDER, "error": True,
    "label": "Couldn't reach your Outlook -- reconnect it in Settings",
}


def _with_token(fn):
    """Wraps a handler's Graph work with token lookup and maps the expected
    failures to model-relayable {"error": ...} results. Successful results
    may carry "_activity" (one human-readable line, e.g. "Searched your
    inbox for 'offer'") and "_mail_sources" (emails it surfaced) -- app.py's
    tool loop pops both before the result goes back to the model and hands
    them to the client instead."""
    def handler(cur, db, user_id, args):
        try:
            token = _ensure_fresh_token(cur, db, user_id)
            return {**fn(token, args), "note": _UNTRUSTED_NOTE}
        except NotConnectedError:
            return {
                "error": "The user's Outlook account isn't connected, or needs reconnecting -- tell them to "
                         "reconnect it in Settings.",
                "_activity": _RECONNECT_ACTIVITY,
            }
        except GraphError as e:
            if e.status == 401:
                _mark_needs_reconnect(cur, db, user_id)
                return {
                    "error": "Microsoft rejected the saved sign-in -- tell the user to reconnect Outlook in Settings.",
                    "_activity": _RECONNECT_ACTIVITY,
                }
            if e.status in (400, 404):
                return {"error": f"Outlook couldn't find that: {e}. If using a message_id, re-run the list/search tool and copy the id exactly."}
            return {"error": f"Outlook request failed: {e}"}
    return handler


@_with_token
def _list_recent(token: str, args: dict) -> dict:
    days = _clamp(args.get("days"), 7, 1, 30)
    limit = _clamp(args.get("limit"), 15, 1, 25)
    folder = (args.get("folder") or "inbox").strip().lower()
    if folder not in _FOLDERS:
        folder = "inbox"
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Graph requires an $orderby property to also lead the $filter.
    filter_expr = f"receivedDateTime ge {since}"
    if args.get("unread_only"):
        filter_expr += " and isRead eq false"
    data = graph.get(
        f"/me/mailFolders/{folder}/messages", token,
        params={"$filter": filter_expr, "$orderby": "receivedDateTime desc", "$top": limit, "$select": _LIST_SELECT},
    )
    emails = [_summarize(m) for m in data.get("value", [])]
    where = _FOLDER_LABELS.get(folder, folder)
    label = f"Checked your {where} · {_plural(len(emails), 'unread email' if args.get('unread_only') else 'email')} from the last {_plural(days, 'day')}"
    return {
        "ok": True, "folder": folder, "days": days, "count": len(emails), "emails": emails,
        "_activity": {"tool": "outlook_list_recent_emails", "provider": PROVIDER, "label": label},
        "_mail_sources": [_source(e) for e in emails],
    }


@_with_token
def _search(token: str, args: dict) -> dict:
    query = (args.get("query") or "").replace('"', " ").strip()
    if not query:
        return {"error": "query is required"}
    limit = _clamp(args.get("limit"), 10, 1, 25)
    # $search can't be combined with $orderby -- Graph returns by relevance.
    data = graph.get(
        "/me/messages", token,
        params={"$search": f'"{query}"', "$top": limit, "$select": _LIST_SELECT},
    )
    emails = [_summarize(m) for m in data.get("value", [])]
    return {
        "ok": True, "query": query, "count": len(emails), "emails": emails,
        "_activity": {"tool": "outlook_search_emails", "provider": PROVIDER,
                      "label": f"Searched Outlook for “{query}” · {_plural(len(emails), 'match')}"},
        "_mail_sources": [_source(e) for e in emails],
    }


@_with_token
def _read(token: str, args: dict) -> dict:
    message_id = (args.get("message_id") or "").strip()
    if not message_id:
        return {"error": "message_id is required"}
    msg = graph.get(
        f"/me/messages/{quote(message_id, safe='')}", token,
        params={"$select": "id,subject,from,toRecipients,ccRecipients,receivedDateTime,body,hasAttachments,webLink"},
        headers={"Prefer": 'outlook.body-content-type="text"'},
    )
    body = ((msg.get("body") or {}).get("content") or "").strip()
    truncated = len(body) > _MAX_BODY_CHARS

    def _addresses(key):
        return [(r.get("emailAddress") or {}).get("address") for r in msg.get(key) or []]

    subject = msg.get("subject") or "(no subject)"
    source = {
        "provider": PROVIDER, "id": msg.get("id") or message_id, "from": _sender(msg), "subject": subject,
        "received": msg.get("receivedDateTime"), "web_link": msg.get("webLink"),
    }
    return {
        "ok": True,
        "from": source["from"],
        "to": _addresses("toRecipients"),
        "cc": _addresses("ccRecipients"),
        "subject": subject,
        "received": source["received"],
        "has_attachments": bool(msg.get("hasAttachments")),
        "body": body[:_MAX_BODY_CHARS],
        "body_truncated": truncated,
        "_activity": {"tool": "outlook_read_email", "provider": PROVIDER, "label": f"Read “{subject}”", "read_id": source["id"]},
        "_mail_sources": [source],
    }


_DISPATCH = {
    "outlook_list_recent_emails": _list_recent,
    "outlook_search_emails": _search,
    "outlook_read_email": _read,
}


def get_tool_handler(name: str):
    return _DISPATCH.get(name) if is_enabled() else None
