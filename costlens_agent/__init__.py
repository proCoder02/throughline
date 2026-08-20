"""
costlens_agent – usage tracking for speech2text, activated purely via env
vars (COSTLENS_SDK=true, COSTLENS_API_KEY, COSTLENS_URL).

install() is called explicitly from app.py, right after load_dotenv() and
before db_pool is created. This used to be done via a root-level
sitecustomize.py instead (auto-imported by Python at interpreter startup,
before any app code runs at all) specifically to avoid touching app.py --
but that broke in production two different ways: Ubuntu ships its own
no-op sitecustomize.py in /usr/lib/python3.X/ (for apport crash reporting),
which sits earlier on sys.path and silently wins the import every time, so
tracking never actually activated; forcing ours to win instead (via
PYTHONPATH) made this module's `import httpx` (-> ssl) run before
gunicorn's gevent worker calls monkey.patch_all(), leaving gevent's SSL
patching incomplete and causing real RecursionErrors elsewhere in the app.
Calling install() from app.py sidesteps both: app.py is only ever imported
*after* the gevent worker has already monkey-patched, and there's no
sys.path race to lose.

When COSTLENS_SDK is not "true", install() is a no-op: nothing is patched,
so there's zero overhead and zero behavior change.
"""

import os
import time
import json
import logging
from urllib.parse import urlparse

logger = logging.getLogger("costlens-agent")

_installed = False

# Host -> CostLens provider name. Only these hosts are intercepted; anything
# else (Firebase, SMTP, LiveKit signaling, etc.) passes through untouched.
_PROVIDER_HOSTS = {
    "api.groq.com": "groq",
    "api.deepgram.com": "deepgram",
    "api.sarvam.ai": "sarvam",
    "ollama.com": "ollama",
    "api.ollama.com": "ollama",
}


def install():
    global _installed
    if _installed:
        return
    if os.getenv("COSTLENS_SDK", "false").lower() != "true":
        return

    api_key = os.getenv("COSTLENS_API_KEY", "")
    costlens_url = os.getenv("COSTLENS_URL", "")
    if not api_key or not costlens_url:
        logger.warning(
            "COSTLENS_SDK=true but COSTLENS_API_KEY/COSTLENS_URL are not set — skipping."
        )
        return

    from .tracker import CostLensTracker

    tracker = CostLensTracker(api_key=api_key, costlens_url=costlens_url)

    _patch_requests(tracker)
    _patch_psycopg2(tracker)
    _patch_redis(tracker)

    _installed = True
    logger.info("costlens_agent installed (provider hosts: %s)", list(_PROVIDER_HOSTS))


def _safe(fn):
    """A tracking hook must never break the real call it's wrapping."""
    def wrapped(*args, **kwargs):
        try:
            fn(*args, **kwargs)
        except Exception:
            logger.exception("costlens_agent tracking hook failed")
    return wrapped


def _classify(url: str):
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return None
    return _PROVIDER_HOSTS.get(host)


def _extract_tokens(response) -> int:
    """Best-effort token count from an OpenAI-schema-compatible JSON body
    (Groq and Ollama both are). Never raises — returns 0 on anything unusual."""
    try:
        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type:
            return 0
        data = response.json()
        return int(data.get("usage", {}).get("total_tokens", 0))
    except Exception:
        return 0


def _patch_requests(tracker):
    import requests

    original_send = requests.Session.send

    def patched_send(self, request, **kwargs):
        provider = _classify(request.url)
        if provider is None:
            return original_send(self, request, **kwargs)

        start = time.monotonic()
        response = original_send(self, request, **kwargs)
        elapsed_ms = int((time.monotonic() - start) * 1000)

        @_safe
        def report():
            tracker.log(
                provider=provider,
                endpoint=urlparse(request.url).path,
                method=request.method,
                tokens_used=_extract_tokens(response),
                latency_ms=elapsed_ms,
                status_code=response.status_code,
            )
        report()

        return response

    requests.Session.send = patched_send


def _patch_psycopg2(tracker):
    try:
        import psycopg2
        import psycopg2.extensions
    except ImportError:
        return

    # psycopg2's cursor/connection classes are C extension types — attributes
    # like `.execute` can't be reassigned on them directly (raises TypeError:
    # "cannot set ... attribute of immutable type"). The supported extension
    # point instead is subclassing + passing `cursor_factory`/
    # `connection_factory` to psycopg2.connect(), which psycopg2.pool also
    # goes through internally, so this covers app.py's pooled connections too.

    class _TrackingCursorMixin:
        def execute(self, query, vars=None):
            start = time.monotonic()
            try:
                return super().execute(query, vars)
            finally:
                elapsed_ms = int((time.monotonic() - start) * 1000)

                @_safe
                def report():
                    tracker.log(
                        provider="database",
                        endpoint="psycopg2.execute",
                        latency_ms=elapsed_ms,
                    )
                report()

    _cursor_class_cache = {}

    def _tracking_cursor_class(base):
        if base not in _cursor_class_cache:
            _cursor_class_cache[base] = type(
                f"CostLensTracking{base.__name__}", (_TrackingCursorMixin, base), {}
            )
        return _cursor_class_cache[base]

    class _TrackingConnection(psycopg2.extensions.connection):
        def cursor(self, *args, **kwargs):
            # Respects whatever cursor_factory the caller asked for (e.g.
            # psycopg2.extras.RealDictCursor) by wrapping it, instead of
            # forcing a specific cursor type.
            base = kwargs.get("cursor_factory") or getattr(self, "cursor_factory", None) or psycopg2.extensions.cursor
            kwargs["cursor_factory"] = _tracking_cursor_class(base)
            return super().cursor(*args, **kwargs)

    _connection_class_cache = {}

    def _tracking_connection_class(base):
        if base not in _connection_class_cache:
            _connection_class_cache[base] = type(
                f"CostLensTracking{base.__name__}", (_TrackingConnection,) if base is psycopg2.extensions.connection
                else (base, _TrackingConnection), {}
            )
        return _connection_class_cache[base]

    original_connect = psycopg2.connect

    def patched_connect(*args, **kwargs):
        base = kwargs.get("connection_factory") or psycopg2.extensions.connection
        kwargs["connection_factory"] = _tracking_connection_class(base)
        return original_connect(*args, **kwargs)

    psycopg2.connect = patched_connect


def _patch_redis(tracker):
    try:
        import redis
    except ImportError:
        return

    original_execute_command = redis.Redis.execute_command

    def patched_execute_command(self, *args, **kwargs):
        start = time.monotonic()
        try:
            return original_execute_command(self, *args, **kwargs)
        finally:
            elapsed_ms = int((time.monotonic() - start) * 1000)

            @_safe
            def report():
                tracker.log(
                    provider="redis",
                    endpoint=str(args[0]) if args else "redis.command",
                    latency_ms=elapsed_ms,
                )
            report()

    redis.Redis.execute_command = patched_execute_command
