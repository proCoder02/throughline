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
import sys
import time
import json
import atexit
import logging
import inspect
import threading
import contextvars
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

# ---------------------------------------------------------------------------
# Per-endpoint / per-feature attribution
#
# Deliberately kept entirely inside this vendored SDK rather than requiring
# edits scattered across app.py, ei_adapter.py, and every EI batch script --
# the goal was minimum touch-point to the actual speech2text backend code.
# Two mechanisms do the work:
#
# 1. `source_route` (which Flask route triggered a call) -- set once, in a
#    single app.py before_request hook, into the contextvar below. Read back
#    here when an outbound call happens, on whichever thread it happens on
#    (see _patch_threading, since a plain threading.Thread does NOT inherit
#    the parent's contextvars.Context on its own -- that's only automatic
#    for asyncio tasks / concurrent.futures).
# 2. `feature_tag` (which product feature the call belongs to) -- inferred
#    by walking the Python call stack at the moment of the outbound call
#    (see _infer_feature_tag) and matching either the calling *file* (for
#    the standalone EI batch scripts, where one file == one feature) or the
#    calling *function name* (for app.py, where several features share one
#    file). Falls back to a route->feature map for anything else synchronous.
# ---------------------------------------------------------------------------

_current_route: "contextvars.ContextVar[str]" = contextvars.ContextVar(
    "costlens_current_route", default=""
)


def set_current_route(route: str) -> None:
    """Call once per request (e.g. a Flask before_request hook) with the
    route PATTERN (request.url_rule.rule), not the resolved path with real
    IDs substituted in -- keeps cardinality bounded to the known routes."""
    _current_route.set(route or "")


def _patch_threading() -> None:
    """threading.Thread does not automatically copy the spawning thread's
    contextvars.Context (unlike asyncio tasks) -- a plain background thread
    (the pattern already used throughout app.py for trigger_chat_feedback_
    extraction/trigger_reminder_extraction) starts with a fresh, empty
    context, so set_current_route() above would silently read back "" on
    those threads without this. Patching Thread.start once, here, to copy
    the context is the same technique concurrent.futures uses internally --
    it makes source_route (and any other contextvar) transparently follow a
    request into any background thread it spawns, with no changes needed at
    any of app.py's existing threading.Thread(...).start() call sites."""
    original_start = threading.Thread.start

    def patched_start(self):
        ctx = contextvars.copy_context()
        original_run = self.run

        def run_with_context(*args, **kwargs):
            return ctx.run(original_run, *args, **kwargs)

        self.run = run_with_context
        original_start(self)

    threading.Thread.start = patched_start


# One entry per standalone emotional_intelligence/ script that makes its own
# LLM calls -- these run as separate Python processes (see
# run_daily_ei_pipeline.py), so there's never any ambiguity about which
# feature a call inside one of them belongs to.
#
# extraction_pipeline.py is deliberately NOT listed here even though it's
# the Friends-TV research corpus script (research.friends_corpus, handled
# separately below) -- it also defines call_llm/extract_json/PROMPT_VERSION,
# imported and called BY every other script in this table. Since that
# shared call_llm frame sits on the call stack for all of them, matching on
# its filename here would mislabel every other script's calls as
# research.friends_corpus too (the actual bug this comment is warning
# against -- caught via a real test run before this was fixed).
_MODULE_FEATURE_MAP = {
    "chat_feedback_extraction.py": "ei.fact_extraction",
    "real_user_extraction.py": "ei.fact_extraction",
    "fact_dedup.py": "ei.fact_dedup",
    "personality_batch.py": "ei.personality",
    "relationship_batch.py": "ei.relationships",
    "cognitive_sharing.py": "ei.cognitive_sharing",
    "weekly_digest.py": "ei.weekly_digest",
}

# For calls made directly from app.py, several features share that one file
# -- matched by the enclosing function's name instead, so e.g. the reminder
# extraction fired from inside chat_global_image's background thread is
# still tagged "reminders", not "chat.image" just because that's the route
# that happened to trigger it.
_FUNCTION_FEATURE_MAP = {
    "chat": "chat.text",
    "chat_global": "chat.text",
    "chat_global_image": "chat.image",
    "trigger_reminder_extraction": "reminders",
    "request_cognitive_suggestion": "ei.cognitive_sharing",
    "send_direct_message": "chat.text",
}

# Last-resort fallback when neither of the above matches (e.g. a future call
# site nobody's tagged yet) -- keyed by the same route pattern source_route
# carries, so at minimum the call still lands under the right endpoint.
_ROUTE_FEATURE_MAP = {
    "/chat": "chat.text",
    "/chat/global": "chat.text",
    "/chat/global/image": "chat.image",
    "/friends/<int:friend_id>/cognitive-suggestion": "ei.cognitive_sharing",
}


def _infer_feature_tag(source_route: str) -> str:
    try:
        # Checked first, via the process entrypoint rather than the call
        # stack -- extraction_pipeline.py run standalone (python
        # extraction_pipeline.py ...) IS the Friends-TV research corpus
        # pipeline; extraction_pipeline.py merely *imported* by another
        # script (for its shared call_llm helper) is not, and must not be
        # mistaken for it -- see _MODULE_FEATURE_MAP's docstring above.
        if sys.argv and os.path.basename(sys.argv[0]) == "extraction_pipeline.py":
            return "research.friends_corpus"
        for frame_info in inspect.stack():
            filename = os.path.basename(frame_info.filename)
            if filename in _MODULE_FEATURE_MAP:
                return _MODULE_FEATURE_MAP[filename]
            if filename == "app.py" and frame_info.function in _FUNCTION_FEATURE_MAP:
                return _FUNCTION_FEATURE_MAP[frame_info.function]
    except Exception:
        pass
    return _ROUTE_FEATURE_MAP.get(source_route, "untagged")


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
    _patch_threading()

    # Without this, a short-lived process (any of the emotional_intelligence/
    # batch scripts run standalone, e.g. "real_user_extraction.py --limit 1"
    # finishing in well under a second) can exit before the tracker's
    # periodic flush thread ever wakes up (it sleeps flush_interval_seconds,
    # default 5s, before its first flush) -- daemon threads are killed
    # immediately when the process exits, so every buffered record from that
    # run would be silently lost, every time, for any script fast enough to
    # beat the interval. app.py itself never hits this (it runs for hours),
    # which is exactly why this gap went unnoticed until a real batch-script
    # test caught it with zero rows tracked.
    atexit.register(tracker._flush)

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


# Estimated per-million-token USD pricing, keyed by (provider, model).
# NOT verified against each provider's actual bill -- explicitly a
# best-effort stand-in, per the user's own choice when this was added,
# because real rates aren't available for the models actually in use here:
#   - Ollama Cloud bills GPU-time against session/weekly quotas, not a
#     stable per-token rate at all. The estimate below uses Groq's own
#     published rate for gpt-oss-120b -- the literal same open-weight model,
#     just hosted differently -- as the closest available comparable.
#   - qwen/qwen3.6-27b (Groq's vision model, see GROQ_VISION_MODEL in
#     app.py) has no published Groq rate of its own (preview model). The
#     estimate below uses Qwen3 32B's published Groq rate -- same model
#     family, closest available size class.
# Verify/replace with real rates before trusting this for anything beyond a
# rough directional signal. (input_per_million, output_per_million) in USD.
_MODEL_PRICING_PER_MILLION_TOKENS = {
    ("ollama", "gpt-oss:120b"): (0.15, 0.60),
    ("groq", "qwen/qwen3.6-27b"): (0.29, 0.59),
}


def _estimate_cost(provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
    rates = _MODEL_PRICING_PER_MILLION_TOKENS.get((provider, model))
    if not rates:
        return 0.0
    input_rate, output_rate = rates
    return round((input_tokens * input_rate + output_tokens * output_rate) / 1_000_000, 6)


def _extract_usage(response, provider: str):
    """Best-effort (input_tokens, output_tokens, model, cost). Never raises
    -- returns zeros on anything unusual, e.g. Groq's Whisper transcription
    responses, which have no usage/model fields at all (app.py requests
    response_format="json", not "verbose_json", so there's no audio-duration
    field to cost from either -- STT calls are tracked for latency/count
    only, cost stays 0 rather than a fabricated number).

    Two different response shapes in play here, not one -- confirmed via a
    live call, initially missed (shipped as 0/0/0 for every Ollama call
    until caught): Groq's endpoints are OpenAI-compatible
    (`usage.prompt_tokens`/`usage.completion_tokens`), but app.py's call_llm
    hits Ollama's own *native* /api/chat endpoint, not an OpenAI-compatible
    /v1/chat/completions path -- its token counts are top-level
    `prompt_eval_count`/`eval_count` fields, no nested `usage` object at
    all (same field names chat_feedback_extraction.py already reads
    elsewhere in this codebase for the same reason)."""
    try:
        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type:
            return 0, 0, "", 0.0
        data = response.json()
        model = data.get("model") or ""
        if "usage" in data:  # OpenAI-compatible shape (Groq)
            usage = data.get("usage") or {}
            input_tokens = int(usage.get("prompt_tokens", 0))
            output_tokens = int(usage.get("completion_tokens", 0))
        else:  # Ollama's native /api/chat shape
            input_tokens = int(data.get("prompt_eval_count", 0))
            output_tokens = int(data.get("eval_count", 0))
        cost = _estimate_cost(provider, model, input_tokens, output_tokens)
        return input_tokens, output_tokens, model, cost
    except Exception:
        return 0, 0, "", 0.0


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
            input_tokens, output_tokens, model, cost = _extract_usage(response, provider)
            source_route = _current_route.get()
            tracker.log(
                provider=provider,
                endpoint=urlparse(request.url).path,
                method=request.method,
                source_route=source_route,
                feature_tag=_infer_feature_tag(source_route),
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                tokens_used=input_tokens + output_tokens,
                cost=cost,
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
