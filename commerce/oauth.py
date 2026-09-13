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
import re
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
    """Real-world MCP servers (confirmed live against mcp.swiggy.com) don't
    publish RFC 8414 metadata at a fixed guessable path on their own base
    URL -- they follow the MCP Authorization spec's actual flow: an
    unauthenticated request to the resource server returns 401 with a
    `WWW-Authenticate: Bearer ... resource_metadata="<url>"` header, which
    points to an RFC 9728 Protected Resource Metadata document; THAT
    document's `authorization_servers` list names the real authorization
    server(s), each of which then publishes RFC 8414 metadata at ITS OWN
    `.well-known/oauth-authorization-server`. Explicit env overrides
    (SWIGGY_<SERVER>_AUTHORIZE_URL / _TOKEN_URL / _REGISTRATION_URL) win
    outright and skip all of this, for when discovery itself is unreliable
    (confirmed live: Swiggy's advertised resource_metadata URL currently
    404s -- see SWIGGY_MCP_COGNITIVE_COMMERCE_PLAN.md)."""
    prefix = f"SWIGGY_{server.upper()}"
    authorize, token = os.getenv(f"{prefix}_AUTHORIZE_URL"), os.getenv(f"{prefix}_TOKEN_URL")
    if authorize and token:
        return {
            "authorization_endpoint": authorize,
            "token_endpoint": token,
            "registration_endpoint": os.getenv(f"{prefix}_REGISTRATION_URL"),
        }

    probe = requests.get(base_url(server), timeout=10)
    if probe.status_code != 401 or "WWW-Authenticate" not in probe.headers:
        raise RuntimeError(
            f"Expected a 401 with a WWW-Authenticate challenge from {base_url(server)}, "
            f"got {probe.status_code}. Set {prefix}_AUTHORIZE_URL/{prefix}_TOKEN_URL "
            f"explicitly instead of relying on discovery."
        )
    match = re.search(r'resource_metadata="([^"]+)"', probe.headers["WWW-Authenticate"])
    if not match:
        raise RuntimeError(f"No resource_metadata in WWW-Authenticate header from {base_url(server)}.")

    resource_resp = requests.get(match.group(1), timeout=10)
    resource_resp.raise_for_status()
    auth_servers = resource_resp.json().get("authorization_servers") or []
    if not auth_servers:
        raise RuntimeError(f"No authorization_servers listed at {match.group(1)}.")

    as_resp = requests.get(f"{auth_servers[0].rstrip('/')}/.well-known/oauth-authorization-server", timeout=10)
    as_resp.raise_for_status()
    meta = as_resp.json()
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
                # RFC 7591 marks these optional, but a real MCP-client bug
                # report (github.com/anthropics/claude-code/issues/52565)
                # shows consent flows breaking when they're absent/null --
                # the consent screen likely renders the requesting app's
                # name/version and silently fails without them, which would
                # surface as exactly the vague "Invalid consent session"
                # error seen live against Swiggy. Cheap to always send.
                "client_uri": "https://throughline.app",
                "software_id": "throughline-swiggy-mcp",
                "software_version": "1.0.0",
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


def build_authorize_url(client_id: str, authorize_endpoint: str, state: str, code_challenge: str, resource: str = None) -> str:
    # Matches Swiggy's own documented example (mcp.swiggy.com/builders/docs/
    # start/authenticate/) exactly -- response_type, client_id, redirect_uri,
    # code_challenge(+method), state, scope. No `resource` param in their
    # docs (the earlier RFC 8707 addition was speculative, based on the
    # general MCP Authorization spec, not Swiggy's actual contract -- kept
    # as an accepted-but-unused arg in case it turns out to matter later,
    # never sent unless explicitly passed).
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri(),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "scope": "mcp:tools",
    }
    if resource:
        params["resource"] = resource
    return f"{authorize_endpoint}?{urlencode(params)}"


def exchange_code(token_endpoint: str, client_id: str, client_secret, code: str, code_verifier: str, resource: str = None) -> dict:
    # Body shape matches Swiggy's documented curl example exactly:
    # {grant_type, code, code_verifier, redirect_uri} -- no client_id/
    # resource in their example. client_secret still included when present
    # (confidential clients) since the doc's example is for a public client.
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(),
        "code_verifier": code_verifier,
    }
    if client_secret:
        data["client_id"] = client_id
        data["client_secret"] = client_secret
    resp = requests.post(token_endpoint, json=data, timeout=10)
    resp.raise_for_status()
    return resp.json()


def refresh_access_token(token_endpoint: str, client_id: str, client_secret, refresh_token: str, resource: str = None) -> dict:
    # NOTE: Swiggy's docs explicitly say refresh_token issuance/exchange is
    # NOT wired in v1.0 despite being advertised in their metadata -- this
    # will currently fail server-side. _ensure_fresh_token treats an absent
    # refresh capability as "re-run full authorization", not a hard error.
    data = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id}
    if client_secret:
        data["client_secret"] = client_secret
    resp = requests.post(token_endpoint, json=data, timeout=10)
    resp.raise_for_status()
    return resp.json()
