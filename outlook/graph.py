"""
Microsoft identity platform (OAuth 2.0 auth code + PKCE) and Microsoft Graph
mail calls. Kept separate from adapter.py so the protocol plumbing stays
isolated, same split as commerce/oauth.py vs commerce/swiggy_adapter.py.

Requires an Azure app registration (Entra ID -> App registrations):
  - Supported account types: "Accounts in any organizational directory and
    personal Microsoft accounts" (matches the default 'common' tenant)
  - Platform: Web, redirect URI = OUTLOOK_REDIRECT_URI
  - API permissions (delegated): offline_access, User.Read, Mail.Read
  - A client secret -> OUTLOOK_CLIENT_SECRET
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
from urllib.parse import urlencode

import requests

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
# Read-only by design -- Mail.Read only, never Mail.ReadWrite/Mail.Send.
SCOPES = "offline_access User.Read Mail.Read"


class GraphError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _tenant() -> str:
    return os.getenv("OUTLOOK_TENANT", "common")


def _authority() -> str:
    return f"https://login.microsoftonline.com/{_tenant()}/oauth2/v2.0"


def client_id() -> str:
    value = os.getenv("OUTLOOK_CLIENT_ID", "")
    if not value:
        raise RuntimeError("OUTLOOK_CLIENT_ID is not set -- add the Azure app registration's client ID to .env.")
    return value


def _client_secret() -> str:
    value = os.getenv("OUTLOOK_CLIENT_SECRET", "")
    if not value:
        raise RuntimeError("OUTLOOK_CLIENT_SECRET is not set -- add the Azure app registration's client secret to .env.")
    return value


def redirect_uri() -> str:
    uri = os.getenv("OUTLOOK_REDIRECT_URI", "")
    if not uri:
        raise RuntimeError(
            "OUTLOOK_REDIRECT_URI is not set -- add it to .env, e.g. "
            "https://yourdomain.example/integrations/outlook/callback. Must "
            "exactly match a Web redirect URI on the Azure app registration "
            "(HTTPS required for anything other than localhost)."
        )
    return uri


def generate_pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_authorize_url(state: str, code_challenge: str) -> str:
    params = {
        "client_id": client_id(),
        "response_type": "code",
        "redirect_uri": redirect_uri(),
        "response_mode": "query",
        "scope": SCOPES,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        # Always show the account picker -- someone with both a personal
        # and a work Microsoft account would otherwise be silently signed in
        # with whichever one the browser last used.
        "prompt": "select_account",
    }
    return f"{_authority()}/authorize?{urlencode(params)}"


def _token_request(data: dict) -> dict:
    # Microsoft's token endpoint takes form-encoded bodies, not JSON.
    data = {**data, "client_id": client_id(), "client_secret": _client_secret(), "scope": SCOPES}
    resp = requests.post(f"{_authority()}/token", data=data, timeout=15)
    if resp.status_code != 200:
        try:
            body = resp.json()
            message = body.get("error_description") or body.get("error") or resp.text
        except ValueError:
            message = resp.text
        raise GraphError(f"Token request failed: {message}", status=resp.status_code)
    return resp.json()


def exchange_code(code: str, code_verifier: str) -> dict:
    return _token_request({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(),
        "code_verifier": code_verifier,
    })


def refresh_access_token(refresh_token: str) -> dict:
    # Microsoft rotates refresh tokens -- the response's refresh_token
    # replaces the stored one (see adapter._ensure_fresh_token).
    return _token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})


def get(path: str, access_token: str, params: dict | None = None, headers: dict | None = None) -> dict:
    resp = requests.get(
        f"{GRAPH_BASE}{path}",
        params=params,
        headers={"Authorization": f"Bearer {access_token}", **(headers or {})},
        timeout=20,
    )
    if resp.status_code != 200:
        try:
            message = resp.json().get("error", {}).get("message") or resp.text
        except ValueError:
            message = resp.text
        raise GraphError(message, status=resp.status_code)
    return resp.json()
