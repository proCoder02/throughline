"""
OAuth 2.1 + PKCE mechanics for Swiggy's MCP servers (Food/Instamart/Dineout),
each an independent OAuth resource per https://mcp.swiggy.com/builders/docs/.

Kept separate from swiggy_adapter.py (the only file app.py imports from) so
the raw protocol plumbing -- PKCE, RFC 8414 discovery, RFC 7591 Dynamic
Client Registration -- stays isolated and swappable on its own.

IMPORTANT -- verify before enabling in any real environment: Swiggy's public
docs confirm OAuth 2.1 + PKCE and per-server independence, but do not fully
publish discovery/registration endpoint shapes. This module implements the
standard RFC 8414 / RFC 7591 flow those docs point at, with every endpoint
and every tool name overridable via .env so a documentation gap is a config
change, not a code change. See SWIGGY_MCP_COGNITIVE_COMMERCE_PLAN.md.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
from urllib.parse import urlencode

import requests

SERVERS = ("food", "im", "dineout")


def base_url(server: str) -> str:
    return os.getenv(f"SWIGGY_{server.upper()}_MCP_URL", f"https://mcp.swiggy.com/{server}").rstrip("/")


def redirect_uri() -> str:
    uri = os.getenv("SWIGGY_REDIRECT_URI", "")
    if not uri:
        raise RuntimeError(
            "SWIGGY_REDIRECT_URI is not set -- add it to .env, e.g. "
            "https://yourdomain.example/integrations/swiggy/callback. Must be "
            "a real HTTPS domain Swiggy's OAuth server will accept for "
            "production Dynamic Client Registration -- a bare IP is unlikely "
            "to be accepted (see the plan doc's 'blocking prerequisite')."
        )
    return uri


def generate_pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return verifier, challenge


def discover_metadata(server: str) -> dict:
    """RFC 8414 authorization server metadata. Explicit env overrides
    (SWIGGY_<SERVER>_AUTHORIZE_URL / _TOKEN_URL / _REGISTRATION_URL) win
    outright, in case a deployment needs to skip discovery entirely."""
    prefix = f"SWIGGY_{server.upper()}"
    authorize, token = os.getenv(f"{prefix}_AUTHORIZE_URL"), os.getenv(f"{prefix}_TOKEN_URL")
    if authorize and token:
        return {
            "authorization_endpoint": authorize,
            "token_endpoint": token,
            "registration_endpoint": os.getenv(f"{prefix}_REGISTRATION_URL"),
        }
    resp = requests.get(f"{base_url(server)}/.well-known/oauth-authorization-server", timeout=10)
    resp.raise_for_status()
    meta = resp.json()
    return {
        "authorization_endpoint": meta["authorization_endpoint"],
        "token_endpoint": meta["token_endpoint"],
        "registration_endpoint": meta.get("registration_endpoint"),
    }


def get_or_register_client(cur, server: str) -> dict:
    """Returns {client_id, client_secret, authorize_endpoint, token_endpoint}.
    Reuses a cached registration (swiggy_oauth_clients) when one exists --
    registration happens at most once per server, not per user. Prefers
    SWIGGY_<SERVER>_CLIENT_ID/_CLIENT_SECRET from .env if set, in case Swiggy
    issues credentials via their developer portal instead of pure Dynamic
    Client Registration; falls back to registering dynamically otherwise."""
    cur.execute("SELECT * FROM swiggy_oauth_clients WHERE server = %s", (server,))
    row = cur.fetchone()
    if row:
        return {
            "client_id": row["client_id"],
            "client_secret": _decrypt_or_none(row["client_secret_encrypted"]),
            "authorize_endpoint": row["authorize_endpoint"],
            "token_endpoint": row["token_endpoint"],
        }

    from . import crypto_utils

    prefix = f"SWIGGY_{server.upper()}"
    meta = discover_metadata(server)
    env_client_id = os.getenv(f"{prefix}_CLIENT_ID")
    if env_client_id:
        client_id, client_secret = env_client_id, os.getenv(f"{prefix}_CLIENT_SECRET")
    elif meta.get("registration_endpoint"):
        reg = requests.post(
            meta["registration_endpoint"],
            json={
                "redirect_uris": [redirect_uri()],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "client_name": "Throughline",
            },
            timeout=10,
        )
        reg.raise_for_status()
        body = reg.json()
        client_id, client_secret = body["client_id"], body.get("client_secret")
    else:
        raise RuntimeError(
            f"No client credentials available for Swiggy '{server}' -- set "
            f"{prefix}_CLIENT_ID (and _CLIENT_SECRET if issued) in .env, or "
            f"confirm the server exposes an OAuth Dynamic Client "
            f"Registration endpoint."
        )

    cur.execute(
        "INSERT INTO swiggy_oauth_clients "
        "(server, client_id, client_secret_encrypted, authorize_endpoint, token_endpoint, registration_endpoint) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (server) DO UPDATE SET client_id = EXCLUDED.client_id, "
        "client_secret_encrypted = EXCLUDED.client_secret_encrypted",
        (
            server, client_id,
            crypto_utils.encrypt(client_secret) if client_secret else None,
            meta["authorization_endpoint"], meta["token_endpoint"], meta.get("registration_endpoint"),
        ),
    )
    return {
        "client_id": client_id, "client_secret": client_secret,
        "authorize_endpoint": meta["authorization_endpoint"], "token_endpoint": meta["token_endpoint"],
    }


def _decrypt_or_none(value):
    if not value:
        return None
    from . import crypto_utils
    return crypto_utils.decrypt(value)


def build_authorize_url(client_id: str, authorize_endpoint: str, state: str, code_challenge: str) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri(),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{authorize_endpoint}?{urlencode(params)}"


def exchange_code(token_endpoint: str, client_id: str, client_secret, code: str, code_verifier: str) -> dict:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(),
        "client_id": client_id,
        "code_verifier": code_verifier,
    }
    if client_secret:
        data["client_secret"] = client_secret
    resp = requests.post(token_endpoint, data=data, timeout=10)
    resp.raise_for_status()
    return resp.json()


def refresh_access_token(token_endpoint: str, client_id: str, client_secret, refresh_token: str) -> dict:
    data = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id}
    if client_secret:
        data["client_secret"] = client_secret
    resp = requests.post(token_endpoint, data=data, timeout=10)
    resp.raise_for_status()
    return resp.json()
