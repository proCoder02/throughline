"""
Gmail integration. app.py uses it for its /integrations/gmail/* routes; the
chat reaches its tools through mail_common.py, via the provider surface
(PROVIDER, LABEL, is_enabled, TOOLS, get_tool_handler, get_account) --
same surface and same behavior contract as outlook/adapter.py.

Each user links one or more Google accounts via OAuth; the global-chat
agent loop then gets read-only tools (list recent, search, read one)
backed by the Gmail API. List/search cover every linked mailbox unless the
model passes `account`; ids are account-prefixed (mail_common.prefixed_id)
so a read always hits the right mailbox.

Gated behind GMAIL_ENABLED in .env: flag off (the default) -> mail_common
offers none of these tools, every route 404s, /status reports
enabled=false.

Read-only by construction: the only scope requested is gmail.readonly.
That scope is Google-"restricted": until the app passes Google's
verification it runs in Testing mode, where only listed test users can
connect and refresh tokens die after 7 days -- which surfaces here as
needs_reconnect, so users get a Reconnect prompt rather than a silent
failure.
"""
from __future__ import annotations

import base64
import html
import os
import re
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from functools import lru_cache
from urllib.parse import quote

from mail_common import (BULK_HEADER_NAMES, NEEDS_REPLY_GUIDANCE, PURCHASE_GUIDANCE, PURCHASE_WORDS, Mailboxes,
                         TRANSACTIONAL_SUBJECT_RE, account_param, booking_ids, extract_amounts, find_purchases_tool, group_purchases, mark_logged,
                         topic_words_of, is_automated_sender, is_bulk_mail,
                         newest_first, parse_period, prefixed_id)

from . import google
from .google import GoogleError

PROVIDER = "gmail"
LABEL = "Gmail"


class GmailError(Exception):
    """Raised from an explicit user action (connect/callback) -- routes turn
    this into a clear error response, never a silent failure."""


class NotConnectedError(GmailError):
    def __init__(self):
        super().__init__("No linked Gmail account. Connect it in Settings first.")


def is_enabled() -> bool:
    return os.getenv("GMAIL_ENABLED", "false").strip().lower() == "true"


# ---------------------------------------------------------------------------
# At-rest token encryption -- its own key (GMAIL_TOKEN_ENCRYPTION_KEY),
# never shared with Outlook's or Swiggy's, so rotating one never forces
# rotating another.
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _fernet():
    from cryptography.fernet import Fernet
    key = os.getenv("GMAIL_TOKEN_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("GMAIL_TOKEN_ENCRYPTION_KEY is not set -- required whenever GMAIL_ENABLED=true.")
    return Fernet(key.encode("ascii"))


def _encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def _decrypt(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")


# ---------------------------------------------------------------------------
# Account / token management
# ---------------------------------------------------------------------------

def get_accounts(cur, user_id: int) -> list[dict]:
    """Every linked (not disconnected) Gmail mailbox for this user, in the
    order first linked (by id -- a reconnect doesn't reshuffle the list)."""
    cur.execute(
        "SELECT * FROM gmail_accounts WHERE user_id = %s AND revoked_at IS NULL ORDER BY id",
        (user_id,),
    )
    return cur.fetchall()


def _account_status(a: dict) -> dict:
    return {
        "id": a["id"], "email": a["email"],
        "connected_at": a["connected_at"].isoformat() if a.get("connected_at") else None,
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
        # Until Google verifies the app (restricted scope), refresh tokens
        # expire after 7 days -- the clients say so up front. Set
        # GMAIL_APP_VERIFIED=true once verification is through.
        "weekly_reconnect": os.getenv("GMAIL_APP_VERIFIED", "false").strip().lower() != "true",
    }


def _mark_needs_reconnect(cur, db, account: dict) -> None:
    cur.execute("UPDATE gmail_accounts SET needs_reconnect = TRUE WHERE id = %s", (account["id"],))
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
        token_response = google.refresh_access_token(_decrypt(account["refresh_token_encrypted"]))
    except GoogleError:
        # invalid_grant: revoked, password change, or Testing mode's 7-day
        # refresh-token expiry -- only reconnecting fixes it.
        _mark_needs_reconnect(cur, db, account)
        raise NotConnectedError()
    new_access = token_response["access_token"]
    new_refresh = token_response.get("refresh_token")
    cur.execute(
        "UPDATE gmail_accounts SET access_token_encrypted = %s, "
        "refresh_token_encrypted = COALESCE(%s, refresh_token_encrypted), token_expires_at = %s "
        "WHERE id = %s",
        (_encrypt(new_access), _encrypt(new_refresh) if new_refresh else None, _expiry_from(token_response), account["id"]),
    )
    db.commit()
    return new_access


# ---------------------------------------------------------------------------
# OAuth connect / callback / disconnect
# ---------------------------------------------------------------------------

CLIENTS = ("web", "app")


def get_authorize_url(cur, db, user_id: int, client: str = "web", login_hint: str | None = None) -> str:
    """client decides where the callback lands: 'web' redirects back into
    the SPA, 'app' (Flutter, via the system browser) gets a static page.
    Connecting again with a different Google account ADDS a mailbox;
    login_hint (an already-linked address) is for reconnecting that one."""
    if client not in CLIENTS:
        client = "web"
    try:
        verifier, challenge = google.generate_pkce_pair()
        state = secrets.token_urlsafe(32)
        url = google.build_authorize_url(state, challenge, (login_hint or "").strip() or None)
    except RuntimeError as e:
        raise GmailError(str(e)) from e
    cur.execute("DELETE FROM gmail_oauth_pending WHERE created_at < now() - interval '1 hour'")
    cur.execute(
        "INSERT INTO gmail_oauth_pending (state, user_id, code_verifier, client) VALUES (%s, %s, %s, %s)",
        (state, user_id, verifier, client),
    )
    db.commit()
    return url


def pending_client(cur, state: str | None) -> str:
    if not state:
        return "web"
    cur.execute("SELECT client FROM gmail_oauth_pending WHERE state = %s", (state,))
    row = cur.fetchone()
    return (row and row.get("client")) or "web"


def friendly_callback_error(error: str | None, description: str | None) -> str:
    if error == "access_denied":
        # Also what an unlisted user sees while the app is in Testing mode.
        return ("Google didn't grant access, so nothing was connected. If you didn't cancel, this "
                "Google account may not be on the app's tester list yet.")
    if not error:
        return "Google didn't return an authorization code. Please try connecting again."
    return (description or "").strip()[:200] or f"Google returned an error ({error})."


def handle_callback(cur, db, code: str, state: str) -> dict:
    """Returns {user_id, email}."""
    cur.execute(
        "DELETE FROM gmail_oauth_pending WHERE state = %s AND created_at > now() - interval '1 hour' RETURNING *",
        (state,),
    )
    pending = cur.fetchone()
    db.commit()  # single use, even if the exchange below fails
    if not pending:
        raise GmailError("This Gmail connection attempt has expired or is invalid. Please try connecting again.")
    user_id = pending["user_id"]

    try:
        token_response = google.exchange_code(code, pending["code_verifier"])
    except GoogleError as e:
        raise GmailError(str(e)) from e
    # Google's consent screen lets people untick individual permissions --
    # without gmail.readonly there's nothing to link.
    if google.READONLY_SCOPE not in (token_response.get("scope") or "").split():
        google.revoke(token_response["access_token"])
        raise GmailError("Reading your email wasn't allowed on Google's screen. Connect again and keep that box ticked.")
    access_token = token_response["access_token"]
    refresh_token = token_response.get("refresh_token")

    # The address identifies the mailbox (one row per user + address), so
    # it's required -- /profile only needs the gmail.readonly scope.
    try:
        email = (google.get("/profile", access_token).get("emailAddress") or "").strip().lower() or None
    except GoogleError:
        email = None
    if not email:
        raise GmailError("Couldn't read which Gmail address this is. Please try connecting again.")

    # One row per (user, address): the same address again is a reconnect
    # (refresh in place, even if it was disconnected before); a new address
    # adds a mailbox alongside the others.
    cur.execute(
        "INSERT INTO gmail_accounts (user_id, email, access_token_encrypted, refresh_token_encrypted, token_expires_at) "
        "VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (user_id, email) DO UPDATE SET "
        "access_token_encrypted = EXCLUDED.access_token_encrypted, "
        "refresh_token_encrypted = COALESCE(EXCLUDED.refresh_token_encrypted, gmail_accounts.refresh_token_encrypted), "
        "token_expires_at = EXCLUDED.token_expires_at, connected_at = now(), revoked_at = NULL, "
        "needs_reconnect = FALSE",
        (user_id, email, _encrypt(access_token), _encrypt(refresh_token) if refresh_token else None,
         _expiry_from(token_response)),
    )
    db.commit()
    return {"user_id": user_id, "email": email}


def disconnect(cur, db, user_id: int, account_id: int | None = None) -> None:
    """One mailbox when account_id is given, otherwise all of them (what an
    older client's plain Disconnect means)."""
    accounts = [a for a in get_accounts(cur, user_id) if account_id is None or a["id"] == account_id]
    for account in accounts:
        # Drop the grant on Google's side too (best effort), so the app also
        # disappears from the user's Google "third-party connections".
        try:
            token = account.get("refresh_token_encrypted") or account.get("access_token_encrypted")
            if token:
                google.revoke(_decrypt(token))
        except Exception:
            pass
        cur.execute(
            "UPDATE gmail_accounts SET revoked_at = now(), access_token_encrypted = '', "
            "refresh_token_encrypted = NULL WHERE id = %s",
            (account["id"],),
        )
    db.commit()


# ---------------------------------------------------------------------------
# Agent tools -- reached through mail_common. Handlers return {"error": ...}
# dicts rather than raising, so the model can relay a problem as a reply.
# Results may carry "_activity"/"_mail_sources", which app.py's tool loop
# pops before the model sees them (see mail_common.build_reply_meta).
# ---------------------------------------------------------------------------

_FOLDER_QUERY = {
    "inbox": ("in:inbox", "inbox"),
    "sent": ("in:sent", "sent mail"),
    "drafts": ("in:drafts", "drafts"),
    "starred": ("is:starred", "starred mail"),
    "important": ("is:important", "important mail"),
    "spam": ("in:spam", "spam"),
    "trash": ("in:trash", "trash"),
    "all": ("", "mail"),
}
_MAX_BODY_CHARS = 8000
_METADATA_HEADERS = ["From", "Subject", "Date", "List-Unsubscribe", "List-Id", "Precedence", "Auto-Submitted"]

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "gmail_list_recent_emails",
            "description": (
                "List the user's recent emails from their linked Gmail account (newest first), with sender, "
                "subject, time and a short preview. Use when the user asks to check/review/summarize their "
                "Gmail or inbox. Use gmail_read_email for an email's full text."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "How many days back to look, 1-30. Default 7."},
                    "unread_only": {"type": "boolean", "description": "Only unread emails. Default false."},
                    "folder": {
                        "type": "string",
                        "description": "inbox (default), sent, drafts, starred, important, spam, trash, or all.",
                    },
                    "limit": {"type": "integer", "description": "Max emails to return, 1-25. Default 15."},
                    "account": account_param("Gmail"),
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gmail_search_emails",
            "description": (
                "Search the user's whole Gmail mailbox(es). Accepts plain keywords or Gmail search operators "
                "(from:hr@company.com, subject:offer, has:attachment, after:2026/10/01, is:unread). Use when the "
                "user asks about emails from someone or about a topic."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Keywords and/or Gmail search operators."},
                    "limit": {"type": "integer", "description": "Max emails to return, 1-25. Default 10."},
                    "account": account_param("Gmail"),
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gmail_find_needs_reply",
            "description": (
                "Find recent Gmail conversations still waiting on the user: the last message is from someone "
                "else, in the primary inbox, not from a no-reply sender. Use for 'what do I need to reply to?', "
                "'anything waiting on me?'. Returns candidates with previews -- judge which genuinely ask "
                "something of the user before listing them."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "How far back to look, 1-30. Default 14."},
                    "limit": {"type": "integer", "description": "Max candidates, 1-20. Default 10."},
                    "account": account_param("Gmail"),
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gmail_read_email",
            "description": "Read one Gmail email's full text. message_id must be copied exactly from a list/search result.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_id": {"type": "string", "description": "The email's id, exactly as returned by a list/search tool."},
                },
                "required": ["message_id"],
            },
        },
    },
    find_purchases_tool("gmail", "Gmail"),
]


def _clamp(value, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _plural(n: int, word: str) -> str:
    if n == 1:
        return f"{n} {word}"
    return f"{n} {word}{'es' if word.endswith(('ch', 'sh', 's', 'x')) else 's'}"


def _headers(msg: dict) -> dict:
    return {h.get("name", "").lower(): h.get("value", "") for h in (msg.get("payload") or {}).get("headers") or []}


def _sender(raw_from: str) -> str:
    name, email = parseaddr(raw_from or "")
    if name and email and name != email:
        return f"{name} <{email}>"
    return email or name or "unknown sender"


def _received(msg: dict) -> str | None:
    try:
        return datetime.fromtimestamp(int(msg["internalDate"]) / 1000, tz=timezone.utc).isoformat()
    except (KeyError, TypeError, ValueError):
        return None


def _web_link(msg: dict, account_email: str | None) -> str:
    # Gmail's web UI addresses conversations, so link the THREAD id -- a
    # later message's own id doesn't open anything there. The account goes
    # in the path (/u/<address>/), not ?authuser=: Gmail's authuser redirect
    # can drop the #fragment and strand the user in the inbox. #all/ opens
    # the thread whichever label it's under.
    thread_id = msg.get("threadId") or msg.get("id")
    account = f"{quote(account_email, safe='@')}/" if account_email else "0/"
    return f"https://mail.google.com/mail/u/{account}#all/{thread_id}"


# Bulk-sent mail that is still a real transaction (e-tickets, invoices and
# receipts usually carry List-Unsubscribe too) -- not promotional.
_TRANSACTIONAL_RE = re.compile(
    r"e-?ticket|invoice|receipt|booking (?:id|confirm)|confirmed|payment (?:of|received|successful)|order (?:#|id|confirm)|itinerary",
    re.IGNORECASE)


def _summarize(msg: dict, account: dict) -> dict:
    h = _headers(msg)
    labels = set(msg.get("labelIds") or [])
    return {
        "id": prefixed_id(account, msg.get("id")),
        "account": account.get("email"),
        "from": _sender(h.get("from")),
        "subject": h.get("subject") or "(no subject)",
        "received": _received(msg),
        "preview": html.unescape(msg.get("snippet") or ""),
        "unread": "UNREAD" in labels,
        "important": "IMPORTANT" in labels,
        # Marketing/bulk mail: never evidence of money spent (confirmed live:
        # a tour-package ad's "price starting at" was counted as a purchase).
        "promotional": bool(labels & {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_FORUMS"})
                       or is_bulk_mail(h) and not _TRANSACTIONAL_RE.search(h.get("subject") or ""),
        "web_link": _web_link(msg, account.get("email")),
    }


def _source(email: dict) -> dict:
    return {"provider": PROVIDER, **{k: email.get(k) for k in ("id", "account", "from", "subject", "received", "web_link")}}


def _fetch_metadata(token: str, ids: list[str]) -> list[dict]:
    """Gmail's list endpoint returns ids only -- fetch each message's
    headers in parallel (cooperative greenlets under gunicorn's gevent
    worker), keeping the list's newest-first order."""
    def one(message_id):
        return google.get(
            f"/messages/{quote(message_id, safe='')}", token,
            params=[("format", "metadata"), *[("metadataHeaders", h) for h in _METADATA_HEADERS]],
        )
    if not ids:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(ids))) as pool:
        return list(pool.map(one, ids))


_MB = Mailboxes(
    provider=PROVIDER, label=LABEL, get_accounts=lambda cur, uid: get_accounts(cur, uid),
    ensure_token=lambda cur, db, a: _ensure_fresh_token(cur, db, a),
    mark_needs_reconnect=lambda cur, db, a: _mark_needs_reconnect(cur, db, a),
    api_error=GoogleError, not_connected=NotConnectedError,
)


def _list_ids(token: str, query: str, limit: int) -> list[str]:
    data = google.get("/messages", token, params={"q": query, "maxResults": limit})
    return [m["id"] for m in data.get("messages") or [] if m.get("id")]


def _mailboxes(n: int) -> str:
    return "your Gmail" if n == 1 else f"{n} Gmail mailboxes"


@_MB.tool
def _list_recent(cur, db, accounts, args) -> dict:
    days = _clamp(args.get("days"), 7, 1, 30)
    limit = _clamp(args.get("limit"), 15, 1, 25)
    folder = (args.get("folder") or "inbox").strip().lower()
    if folder not in _FOLDER_QUERY:
        folder = "inbox"
    folder_q, where = _FOLDER_QUERY[folder]
    query = " ".join(p for p in (folder_q, f"newer_than:{days}d", "is:unread" if args.get("unread_only") else "") if p)
    done, broken = _MB.across(cur, db, accounts, lambda token, a: [
        _summarize(m, a) for m in _fetch_metadata(token, _list_ids(token, query, limit))])
    if not done:
        return _MB.with_failures({}, broken, None)
    emails = newest_first([e for _, found in done for e in found], limit)
    label = (f"Checked {_mailboxes(len(done))} ({where}) · "
             f"{_plural(len(emails), 'unread email' if args.get('unread_only') else 'email')} from the last {_plural(days, 'day')}")
    return _MB.with_failures({
        "ok": True, "folder": folder, "days": days, "count": len(emails), "emails": emails,
        "mailboxes": [a.get("email") for a, _ in done],
        "_mail_sources": [_source(e) for e in emails],
    }, broken, {"tool": "gmail_list_recent_emails", "provider": PROVIDER, "label": label})


@_MB.tool
def _search(cur, db, accounts, args) -> dict:
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}
    limit = _clamp(args.get("limit"), 10, 1, 25)
    done, broken = _MB.across(cur, db, accounts, lambda token, a: [
        _summarize(m, a) for m in _fetch_metadata(token, _list_ids(token, query, limit))])
    if not done:
        return _MB.with_failures({}, broken, None)
    emails = newest_first([e for _, found in done for e in found], limit)
    where = "Gmail" if len(done) == 1 else f"{len(done)} Gmail mailboxes"
    return _MB.with_failures({
        "ok": True, "query": query, "count": len(emails), "emails": emails,
        "mailboxes": [a.get("email") for a, _ in done],
        "_mail_sources": [_source(e) for e in emails],
    }, broken, {"tool": "gmail_search_emails", "provider": PROVIDER,
                "label": f"Searched {where} for “{query}” · {_plural(len(emails), 'match')}"})


_THREAD_HEADERS = list(dict.fromkeys(_METADATA_HEADERS + list(BULK_HEADER_NAMES)))
# Gmail's own non-primary tabs -- belt and braces with category:primary.
_BULK_LABELS = {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_UPDATES", "CATEGORY_FORUMS", "SPAM"}


def _fetch_threads(token: str, ids: list[str]) -> list[dict]:
    def one(thread_id):
        return google.get(
            f"/threads/{quote(thread_id, safe='')}", token,
            params=[("format", "metadata"), *[("metadataHeaders", h) for h in _THREAD_HEADERS]],
        )
    if not ids:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(ids))) as pool:
        return list(pool.map(one, ids))


@_MB.tool
def _needs_reply(cur, db, accounts, args) -> dict:
    days = _clamp(args.get("days"), 14, 1, 30)
    limit = _clamp(args.get("limit"), 10, 1, 20)
    # Primary inbox only (skips promotions/social/updates), not sent by the
    # user, recent. Over-fetch: some threads turn out to be already answered.
    query = f"in:inbox category:primary newer_than:{days}d -from:me"

    def work(token, account):
        data = google.get("/threads", token, params={"q": query, "maxResults": min(50, limit * 3)})
        waiting = []
        for thread in _fetch_threads(token, [t["id"] for t in data.get("threads") or [] if t.get("id")]):
            messages = thread.get("messages") or []
            if not messages:
                continue
            last = messages[-1]
            labels = set(last.get("labelIds") or [])
            if "SENT" in labels:
                continue  # the user had the last word
            if labels & _BULK_LABELS:
                continue
            headers = _headers(last)
            if is_automated_sender(headers.get("from", "")) or is_bulk_mail(headers):
                continue
            waiting.append({**_summarize(last, account), "messages_in_thread": len(messages)})
        return waiting

    done, broken = _MB.across(cur, db, accounts, work)
    if not done:
        return _MB.with_failures({}, broken, None)
    emails = newest_first([e for _, found in done for e in found], limit)
    return _MB.with_failures({
        "ok": True, "days": days, "count": len(emails), "emails": emails,
        "mailboxes": [a.get("email") for a, _ in done],
        "guidance": NEEDS_REPLY_GUIDANCE,
        "_mail_sources": [_source(e) for e in emails],
    }, broken, {"tool": "gmail_find_needs_reply", "provider": PROVIDER,
                "label": f"Looked for Gmail threads awaiting your reply · {_plural(len(emails), 'candidate')} from the last {_plural(days, 'day')}"})


_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_TAG_RE = re.compile(r"(?i)<\s*(br|/p|/div|/tr|/li|/h\d)\s*/?>")
_STYLE_SCRIPT_RE = re.compile(r"(?is)<(style|script)[^>]*>.*?</\1>")


def _decode(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="replace")


def _html_to_text(markup: str) -> str:
    # Whitespace in HTML source (incl. newlines) means nothing -- collapse it
    # FIRST, then let real block tags make the line breaks. Confirmed live:
    # keeping source newlines split "payment of Rs." and "8128" onto
    # different lines, so no amount was found in ixigo's invoices.
    text = _STYLE_SCRIPT_RE.sub("", markup)
    text = re.sub(r"\s+", " ", text)
    text = _BLOCK_TAG_RE.sub("\n", text)
    text = html.unescape(_TAG_RE.sub("", text))
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _body_and_attachments(payload: dict) -> tuple[str, list[str]]:
    """Prefers the text/plain part; falls back to text/html stripped to
    text. Attachments are listed by filename only, never downloaded."""
    plain, rich, attachments = [], [], []

    def walk(part):
        if part.get("filename"):
            attachments.append(part["filename"])
            return
        mime = part.get("mimeType", "")
        data = (part.get("body") or {}).get("data")
        if data and mime == "text/plain":
            plain.append(_decode(data))
        elif data and mime == "text/html":
            rich.append(_decode(data))
        for child in part.get("parts") or []:
            walk(child)

    walk(payload or {})
    if plain:
        return "\n".join(plain).strip(), attachments
    return _html_to_text("\n".join(rich)), attachments


@_MB.tool
def _read(cur, db, accounts, args) -> dict:
    given = (args.get("message_id") or "").strip()
    if not given:
        return {"error": "message_id is required"}
    # The id's prefix says which mailbox to open.
    account, msg, message_id = _MB.read_one(cur, db, accounts, given, lambda token, raw: google.get(
        f"/messages/{quote(raw, safe='')}", token, params={"format": "full"}))
    if account is None:
        return msg  # an error result
    h = _headers(msg)
    body, attachments = _body_and_attachments(msg.get("payload"))
    subject = h.get("subject") or "(no subject)"
    source = {
        "provider": PROVIDER, "id": prefixed_id(account, msg.get("id") or message_id), "account": account.get("email"),
        "from": _sender(h.get("from")), "subject": subject, "received": _received(msg),
        "web_link": _web_link({"id": message_id, **msg}, account.get("email")),
    }
    return {
        "ok": True,
        "account": account.get("email"),
        "from": source["from"],
        "to": [a.strip() for a in (h.get("to") or "").split(",") if a.strip()],
        "cc": [a.strip() for a in (h.get("cc") or "").split(",") if a.strip()],
        "subject": subject,
        "received": source["received"],
        "attachments": attachments,
        "body": body[:_MAX_BODY_CHARS],
        "body_truncated": len(body) > _MAX_BODY_CHARS,
        "_activity": [{"tool": "gmail_read_email", "provider": PROVIDER, "label": f"Read “{subject}”", "read_id": source["id"]}],
        "_mail_sources": [source],
    }


@_MB.tool
def _find_purchases(cur, db, accounts, args) -> dict:
    start, end, err = parse_period(args)
    if err:
        return {"error": err}
    topic = (args.get("query") or "").strip()
    query = (f"({topic}) " if topic else "") + f"({' OR '.join(PURCHASE_WORDS)}) after:{start:%Y/%m/%d} before:{end:%Y/%m/%d}"

    def work(token, account):
        # Stage 1 -- headers only, wide net (cheap): with lots of flight
        # mail, the newest 20 were mostly promos and real e-tickets from
        # months earlier fell off the end (confirmed live).
        ids = _list_ids(token, query, 60)
        if not ids:
            return []
        metas = [_summarize(m, account) for m in _fetch_metadata(token, ids)]
        real = [x for x in metas if not x["promotional"]]
        real.sort(key=lambda x: not TRANSACTIONAL_SUBJECT_RE.search(x["subject"]))  # stable: newest first within each group
        picked = real[:25]
        if not picked:
            return []

        # Stage 2 -- open just those and read the amounts.
        def full(summary):
            raw = summary["id"].split(":", 1)[1]
            return google.get(f"/messages/{quote(raw, safe='')}", token, params={"format": "full"})
        with ThreadPoolExecutor(max_workers=min(8, len(picked))) as pool:
            messages = list(pool.map(full, picked))
        found = []
        for msg in messages:
            summary = _summarize(msg, account)
            body, _ = _body_and_attachments(msg.get("payload"))
            # Gmail's snippet is a one-line copy of the opening text -- a
            # backup source of amounts if the body parses oddly.
            text = summary["subject"] + "\n" + body + "\n" + (summary.get("preview") or "")
            found.append({**{k: v for k, v in summary.items() if k != "preview"},
                          "booking_ids": booking_ids(text), "amounts": extract_amounts(text)})
        return found

    done, broken = _MB.across(cur, db, accounts, work)
    if not done:
        return _MB.with_failures({}, broken, None)
    emails = newest_first([e for _, found in done for e in found], 40)
    with_amounts = sum(1 for e in emails if e["amounts"] and not e["promotional"])
    last_day = end - timedelta(days=1)
    grouped = mark_logged(cur, accounts[0]["user_id"], group_purchases(emails, topic_words_of(topic)))
    return _MB.with_failures({
        "ok": True, "emails_scanned": len(emails), **grouped,
        "mailboxes": [a.get("email") for a, _ in done], "guidance": PURCHASE_GUIDANCE,
        "_mail_sources": [_source(e) for e in emails if e["amounts"] and not e["promotional"]],
    }, broken, {"tool": "gmail_find_purchases", "provider": PROVIDER,
                "label": (f"Scanned {_plural(len(emails), 'Gmail email')} {start:%Y-%m-%d} to {last_day:%Y-%m-%d} for purchases"
                          + (f" ({topic})" if topic else "")
                          + f" · {len(grouped['purchases'])} purchase{'' if len(grouped['purchases']) == 1 else 's'}"
                          + (f" · Rs {grouped['total']:,.0f}" if grouped['purchases'] else ""))})


_DISPATCH = {
    "gmail_find_purchases": _find_purchases,
    "gmail_list_recent_emails": _list_recent,
    "gmail_search_emails": _search,
    "gmail_find_needs_reply": _needs_reply,
    "gmail_read_email": _read,
}


def get_tool_handler(name: str):
    return _DISPATCH.get(name) if is_enabled() else None
