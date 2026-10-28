"""
Google OAuth 2.0 (auth code + PKCE, offline access) and Gmail API calls.
Kept separate from adapter.py so the protocol plumbing stays isolated --
same split as outlook/graph.py.

Requires an OAuth client in the Google Cloud project (APIs & Services):
  - Gmail API enabled
  - OAuth consent screen with the gmail.readonly scope (a *restricted*
    scope: until Google verifies the app it stays in Testing mode -- max
    100 listed test users, and refresh tokens expire after 7 days)
  - Credentials -> OAuth client ID, type "Web application", authorized
    redirect URI = GMAIL_REDIRECT_URI
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
from urllib.parse import urlencode

import requests

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
# Read-only by design -- never gmail.compose/modify/send.
READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


class GoogleError(Exception):
    def __init__(self, message: str, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


def client_id() -> str:
    value = os.getenv("GMAIL_CLIENT_ID", "")
    if not value:
        raise RuntimeError("GMAIL_CLIENT_ID is not set -- add the Google OAuth client ID to .env.")
    return value


def _client_secret() -> str:
    value = os.getenv("GMAIL_CLIENT_SECRET", "")
    if not value:
        raise RuntimeError("GMAIL_CLIENT_SECRET is not set -- add the Google OAuth client secret to .env.")
    return value


def redirect_uri() -> str:
    uri = os.getenv("GMAIL_REDIRECT_URI", "")
    if not uri:
        raise RuntimeError(
            "GMAIL_REDIRECT_URI is not set -- add it to .env, e.g. "
            "https://yourdomain.example/integrations/gmail/callback. Must exactly "
            "match an authorized redirect URI on the Google OAuth client."
        )
    return uri


def generate_pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_authorize_url(state: str, code_challenge: str, login_hint: str | None = None) -> str:
    params = {
        "client_id": client_id(),
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": READONLY_SCOPE,
        # offline + consent: Google only issues a refresh token on a
        # consent screen, and without one the link dies after an hour.
        "access_type": "offline",
        "prompt": "consent select_account",
        "include_granted_scopes": "true",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if login_hint:
        # Reconnecting one specific mailbox -- pre-selects it on Google's picker.
        params["login_hint"] = login_hint
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def _token_request(data: dict) -> dict:
    data = {**data, "client_id": client_id(), "client_secret": _client_secret()}
    resp = requests.post(TOKEN_URL, data=data, timeout=15)
    if resp.status_code != 200:
        try:
            body = resp.json()
            code = body.get("error")
            message = body.get("error_description") or code or resp.text
        except ValueError:
            code, message = None, resp.text
        raise GoogleError(f"Token request failed: {message}", status=resp.status_code, code=code)
    return resp.json()


def exchange_code(code: str, code_verifier: str) -> dict:
    return _token_request({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(),
        "code_verifier": code_verifier,
    })


def refresh_access_token(refresh_token: str) -> dict:
    # Google doesn't rotate refresh tokens on refresh (the response usually
    # carries none) -- an invalid_grant here means the link is dead
    # (revoked, password change, or the 7-day Testing-mode expiry).
    return _token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})


def revoke(token: str) -> None:
    """Best effort -- disconnect should also drop the grant on Google's side,
    but a failure here must never block deleting our own copy."""
    try:
        requests.post(REVOKE_URL, params={"token": token}, timeout=10)
    except requests.RequestException:
        pass


def get(path: str, access_token: str, params: dict | list | None = None) -> dict:
    resp = requests.get(
        f"{GMAIL_BASE}{path}",
        params=params,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    if resp.status_code != 200:
        try:
            message = resp.json().get("error", {}).get("message") or resp.text
        except ValueError:
            message = resp.text
        raise GoogleError(message, status=resp.status_code)
    return resp.json()
