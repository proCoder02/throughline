"""
Outlook mail integration. app.py uses it for its /integrations/outlook/*
routes; the chat reaches its tools through mail_common.py, which needs the
provider surface below (PROVIDER, LABEL, is_enabled, TOOLS,
get_tool_handler, get_accounts) -- same surface as gmail/adapter.py. A
user may link several Outlook mailboxes; list/search cover all of them
unless the model passes `account`.

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
mail_common.UNTRUSTED_NOTE.
"""
from __future__ import annotations

import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from urllib.parse import quote

from mail_common import (NEEDS_REPLY_GUIDANCE, Mailboxes, account_param, is_automated_sender, is_bulk_mail,
                         newest_first, prefixed_id)

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

def get_accounts(cur, user_id: int) -> list[dict]:
    """Every linked (not disconnected) Outlook mailbox for this user (e.g.
    personal + work), in the order first linked -- by id, so a reconnect
    doesn't reshuffle the list."""
    cur.execute(
        "SELECT * FROM outlook_accounts WHERE user_id = %s AND revoked_at IS NULL ORDER BY id",
        (user_id,),
    )
    return cur.fetchall()


def _account_status(a: dict) -> dict:
    return {
        "id": a["id"], "email": a["email"],
        "connected_at": a["connected_at"].isoformat() if a.get("connected_at") else None,
        # Still linked, but Microsoft stopped honoring the refresh token
        # (password change, revoked consent, ~90 days idle) -- the client
        # shows a Reconnect state instead of a misleading "Connected".
        "needs_reconnect": bool(a.get("needs_reconnect")),
    }


def get_status(cur, user_id: int) -> dict:
    if not is_enabled():
        return {"enabled": False}
    accounts = [_account_status(a) for a in get_accounts(cur, user_id)]
    first = accounts[0] if accounts else None
    return {
        "enabled": True,
        "accounts": accounts,
        # Single-account fields (first linked mailbox), kept for app builds
        # that predate multi-account support.
        "connected": first is not None,
        "needs_reconnect": bool(first and first["needs_reconnect"]),
        "email": first["email"] if first else None,
        "connected_at": first["connected_at"] if first else None,
    }


def _mark_needs_reconnect(cur, db, account: dict) -> None:
    cur.execute("UPDATE outlook_accounts SET needs_reconnect = TRUE WHERE id = %s", (account["id"],))
    db.commit()


def _expiry_from(token_response: dict) -> datetime | None:
    expires_in = token_response.get("expires_in")
    return datetime.now(timezone.utc) + timedelta(seconds=int(expires_in)) if expires_in else None


def _ensure_fresh_token(cur, db, account: dict) -> str:
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
        _mark_needs_reconnect(cur, db, account)
        raise NotConnectedError()
    new_access = token_response["access_token"]
    new_refresh = token_response.get("refresh_token")
    cur.execute(
        "UPDATE outlook_accounts SET access_token_encrypted = %s, "
        "refresh_token_encrypted = COALESCE(%s, refresh_token_encrypted), token_expires_at = %s "
        "WHERE id = %s",
        (_encrypt(new_access), _encrypt(new_refresh) if new_refresh else None,
         _expiry_from(token_response), account["id"]),
    )
    db.commit()
    return new_access


# ---------------------------------------------------------------------------
# OAuth connect / callback / disconnect
# ---------------------------------------------------------------------------

CLIENTS = ("web", "app")


def get_authorize_url(cur, db, user_id: int, client: str = "web", login_hint: str | None = None) -> str:
    """client decides where the callback lands afterwards: 'web' redirects
    back into the SPA, 'app' (Flutter, signed in via the system browser)
    gets a static "you can close this window" page. Signing in with a
    different Microsoft account ADDS a mailbox; login_hint (an already-
    linked address) is for reconnecting that one."""
    if client not in CLIENTS:
        client = "web"
    try:
        verifier, challenge = graph.generate_pkce_pair()
        state = secrets.token_urlsafe(32)
        url = graph.build_authorize_url(state, challenge, (login_hint or "").strip() or None)
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

    # The address identifies the mailbox (one row per user + address), so
    # it's required.
    try:
        me = graph.get("/me", access_token, params={"$select": "mail,userPrincipalName"})
        email = ((me.get("mail") or me.get("userPrincipalName") or "").strip().lower()) or None
    except GraphError:
        email = None
    if not email:
        raise OutlookError("Couldn't read which Outlook address this is. Please try connecting again.")

    # Same address again = reconnect in place (even after a disconnect);
    # a new address adds a mailbox alongside the others.
    cur.execute(
        "INSERT INTO outlook_accounts (user_id, email, access_token_encrypted, refresh_token_encrypted, token_expires_at) "
        "VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (user_id, email) DO UPDATE SET "
        "access_token_encrypted = EXCLUDED.access_token_encrypted, "
        "refresh_token_encrypted = EXCLUDED.refresh_token_encrypted, "
        "token_expires_at = EXCLUDED.token_expires_at, connected_at = now(), revoked_at = NULL, "
        "needs_reconnect = FALSE",
        (user_id, email, _encrypt(access_token), _encrypt(refresh_token) if refresh_token else None,
         _expiry_from(token_response)),
    )
    db.commit()
    return {"user_id": user_id, "email": email}


def disconnect(cur, db, user_id: int, account_id: int | None = None) -> None:
    """One mailbox when account_id is given, otherwise all of them (what an
    older client's plain Disconnect means). Tokens are wiped, not just
    flagged -- there's nothing to keep them for."""
    if account_id is None:
        cur.execute(
            "UPDATE outlook_accounts SET revoked_at = now(), access_token_encrypted = '', "
            "refresh_token_encrypted = NULL WHERE user_id = %s AND revoked_at IS NULL",
            (user_id,),
        )
    else:
        cur.execute(
            "UPDATE outlook_accounts SET revoked_at = now(), access_token_encrypted = '', "
            "refresh_token_encrypted = NULL WHERE user_id = %s AND id = %s AND revoked_at IS NULL",
            (user_id, account_id),
        )
    db.commit()


# ---------------------------------------------------------------------------
# Agent tools -- plugged into app.py's run_agent_tool_loop via agent_tools()
# and get_tool_handler(). Handlers return {"error": ...} dicts rather than
# raising, same convention as the other _tool_* handlers in app.py, so the
# model can relay a problem (not connected, bad id) as a normal reply.
# ---------------------------------------------------------------------------

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
                    "account": account_param("Outlook"),
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "outlook_search_emails",
            "description": (
                "Search the user's whole Outlook mailbox(es) by keyword -- matches sender, subject and body. "
                "Use when the user asks about emails from someone or about a topic "
                "(e.g. 'did HR email me about the offer?')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Keywords, a sender name, or an email address."},
                    "limit": {"type": "integer", "description": "Max emails to return, 1-25. Default 10."},
                    "account": account_param("Outlook"),
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "outlook_find_needs_reply",
            "description": (
                "Find recent Outlook emails still waiting on the user: in the Focused inbox, from a real person "
                "(not no-reply), with no reply from the user in that conversation since. Use for 'what do I "
                "need to reply to?', 'anything waiting on me?'. Returns candidates with previews -- judge which "
                "genuinely ask something of the user before listing them."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "How far back to look, 1-30. Default 14."},
                    "limit": {"type": "integer", "description": "Max candidates, 1-20. Default 10."},
                    "account": account_param("Outlook"),
                },
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


def _summarize(msg: dict, account: dict) -> dict:
    return {
        "id": prefixed_id(account, msg.get("id")),
        "account": account.get("email"),
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
    if n == 1:
        return f"{n} {word}"
    return f"{n} {word}{'es' if word.endswith(('ch', 'sh', 's', 'x')) else 's'}"


def _source(email: dict) -> dict:
    """The slice of an email the client shows as a clickable source chip --
    never the preview/body, which only the model needs."""
    return {"provider": PROVIDER, **{k: email.get(k) for k in ("id", "account", "from", "subject", "received", "web_link")}}


# Multi-account mechanics (run across mailboxes, per-mailbox reconnect,
# result shaping) shared with gmail/ -- see mail_common.Mailboxes. Results
# may carry "_activity" (human-readable lines) and "_mail_sources" (emails
# surfaced), which app.py's tool loop pops before the model sees them.
_MB = Mailboxes(
    provider=PROVIDER, label=LABEL, get_accounts=lambda cur, uid: get_accounts(cur, uid),
    ensure_token=lambda cur, db, a: _ensure_fresh_token(cur, db, a),
    mark_needs_reconnect=lambda cur, db, a: _mark_needs_reconnect(cur, db, a),
    api_error=GraphError, not_connected=NotConnectedError,
)


def _mailboxes(n: int, where: str) -> str:
    return f"your {where}" if n == 1 else f"{n} Outlook mailboxes ({where})"


@_MB.tool
def _list_recent(cur, db, accounts, args) -> dict:
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
    done, broken = _MB.across(cur, db, accounts, lambda token, a: [_summarize(m, a) for m in graph.get(
        f"/me/mailFolders/{folder}/messages", token,
        params={"$filter": filter_expr, "$orderby": "receivedDateTime desc", "$top": limit, "$select": _LIST_SELECT},
    ).get("value", [])])
    if not done:
        return _MB.with_failures({}, broken, None)
    emails = newest_first([e for _, found in done for e in found], limit)
    where = _FOLDER_LABELS.get(folder, folder)
    label = (f"Checked {_mailboxes(len(done), where)} · "
             f"{_plural(len(emails), 'unread email' if args.get('unread_only') else 'email')} from the last {_plural(days, 'day')}")
    return _MB.with_failures({
        "ok": True, "folder": folder, "days": days, "count": len(emails), "emails": emails,
        "mailboxes": [a.get("email") for a, _ in done],
        "_mail_sources": [_source(e) for e in emails],
    }, broken, {"tool": "outlook_list_recent_emails", "provider": PROVIDER, "label": label})


@_MB.tool
def _search(cur, db, accounts, args) -> dict:
    query = (args.get("query") or "").replace('"', " ").strip()
    if not query:
        return {"error": "query is required"}
    limit = _clamp(args.get("limit"), 10, 1, 25)
    # $search can't be combined with $orderby -- Graph returns by relevance;
    # merged across mailboxes, newest first.
    done, broken = _MB.across(cur, db, accounts, lambda token, a: [_summarize(m, a) for m in graph.get(
        "/me/messages", token,
        params={"$search": f'"{query}"', "$top": limit, "$select": _LIST_SELECT},
    ).get("value", [])])
    if not done:
        return _MB.with_failures({}, broken, None)
    emails = newest_first([e for _, found in done for e in found], limit)
    where = "Outlook" if len(done) == 1 else f"{len(done)} Outlook mailboxes"
    return _MB.with_failures({
        "ok": True, "query": query, "count": len(emails), "emails": emails,
        "mailboxes": [a.get("email") for a, _ in done],
        "_mail_sources": [_source(e) for e in emails],
    }, broken, {"tool": "outlook_search_emails", "provider": PROVIDER,
                "label": f"Searched {where} for “{query}” · {_plural(len(emails), 'match')}"})


@_MB.tool
def _needs_reply(cur, db, accounts, args) -> dict:
    days = _clamp(args.get("days"), 14, 1, 30)
    limit = _clamp(args.get("limit"), 10, 1, 20)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def work(token, account):
        # Two plain queries rather than one clever one: Graph rejects many
        # filter/sort combinations, but date filter + date sort is always
        # accepted. Focused/Other is checked on the results instead.
        inbox = graph.get(
            "/me/mailFolders/inbox/messages", token,
            params={"$filter": f"receivedDateTime ge {since}", "$orderby": "receivedDateTime desc", "$top": 50,
                    "$select": _LIST_SELECT + ",conversationId,inferenceClassification"},
        ).get("value", [])
        sent = graph.get(
            "/me/mailFolders/sentitems/messages", token,
            params={"$filter": f"sentDateTime ge {since}", "$orderby": "sentDateTime desc", "$top": 100,
                    "$select": "conversationId,sentDateTime"},
        ).get("value", [])
        last_sent = {}
        for m in sent:
            conv, at = m.get("conversationId"), m.get("sentDateTime") or ""
            if conv and at > last_sent.get(conv, ""):
                last_sent[conv] = at
        waiting, seen_conversations = [], set()
        for m in inbox:  # newest first: the first one seen per conversation is its latest
            conv = m.get("conversationId")
            if conv in seen_conversations:
                continue
            seen_conversations.add(conv)
            if m.get("inferenceClassification") == "other":
                continue
            if conv and last_sent.get(conv, "") > (m.get("receivedDateTime") or ""):
                continue  # the user replied after it
            sender = _sender(m)
            if is_automated_sender(sender) or (account.get("email") and account["email"] in sender.lower()):
                continue
            waiting.append((m.get("id"), _summarize(m, account)))
        # Graph's list endpoint can't return headers, so check the newest
        # few candidates one by one (in parallel) for bulk-mail markers --
        # Focused Inbox alone let tour-package promos through (confirmed live).
        waiting = waiting[: limit * 2]

        def headers_of(raw_id):
            try:
                msg = graph.get(f"/me/messages/{quote(raw_id, safe='')}", token, params={"$select": "internetMessageHeaders"})
            except GraphError:
                return {}
            return {(h.get("name") or "").lower(): h.get("value") or "" for h in msg.get("internetMessageHeaders") or []}

        if not waiting:
            return []
        with ThreadPoolExecutor(max_workers=min(8, len(waiting))) as pool:
            headers = list(pool.map(headers_of, [raw for raw, _ in waiting]))
        return [summary for (raw, summary), h in zip(waiting, headers) if not is_bulk_mail(h)]

    done, broken = _MB.across(cur, db, accounts, work)
    if not done:
        return _MB.with_failures({}, broken, None)
    emails = newest_first([e for _, found in done for e in found], limit)
    return _MB.with_failures({
        "ok": True, "days": days, "count": len(emails), "emails": emails,
        "mailboxes": [a.get("email") for a, _ in done],
        "guidance": NEEDS_REPLY_GUIDANCE,
        "_mail_sources": [_source(e) for e in emails],
    }, broken, {"tool": "outlook_find_needs_reply", "provider": PROVIDER,
                "label": f"Looked for Outlook emails awaiting your reply · {_plural(len(emails), 'candidate')} from the last {_plural(days, 'day')}"})


@_MB.tool
def _read(cur, db, accounts, args) -> dict:
    given = (args.get("message_id") or "").strip()
    if not given:
        return {"error": "message_id is required"}
    # The id's prefix says which mailbox to open.
    account, msg, message_id = _MB.read_one(cur, db, accounts, given, lambda token, raw: graph.get(
        f"/me/messages/{quote(raw, safe='')}", token,
        params={"$select": "id,subject,from,toRecipients,ccRecipients,receivedDateTime,body,hasAttachments,webLink"},
        headers={"Prefer": 'outlook.body-content-type="text"'},
    ))
    if account is None:
        return msg  # an error result
    body = ((msg.get("body") or {}).get("content") or "").strip()
    truncated = len(body) > _MAX_BODY_CHARS

    def _addresses(key):
        return [(r.get("emailAddress") or {}).get("address") for r in msg.get(key) or []]

    subject = msg.get("subject") or "(no subject)"
    source = {
        "provider": PROVIDER, "id": prefixed_id(account, msg.get("id") or message_id), "account": account.get("email"),
        "from": _sender(msg), "subject": subject,
        "received": msg.get("receivedDateTime"), "web_link": msg.get("webLink"),
    }
    return {
        "ok": True,
        "account": account.get("email"),
        "from": source["from"],
        "to": _addresses("toRecipients"),
        "cc": _addresses("ccRecipients"),
        "subject": subject,
        "received": source["received"],
        "has_attachments": bool(msg.get("hasAttachments")),
        "body": body[:_MAX_BODY_CHARS],
        "body_truncated": truncated,
        "_activity": [{"tool": "outlook_read_email", "provider": PROVIDER, "label": f"Read “{subject}”", "read_id": source["id"]}],
        "_mail_sources": [source],
    }


_DISPATCH = {
    "outlook_list_recent_emails": _list_recent,
    "outlook_search_emails": _search,
    "outlook_find_needs_reply": _needs_reply,
    "outlook_read_email": _read,
}


def get_tool_handler(name: str):
    return _DISPATCH.get(name) if is_enabled() else None
