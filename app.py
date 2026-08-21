
import os
import re
import io
import json
import base64
import hashlib
import secrets
import tempfile
import threading
import jwt as pyjwt
import time
import smtplib
import uuid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime, timezone, timedelta
from functools import wraps

from flask import Flask, jsonify, render_template, request, g, session, send_from_directory
from flask_cors import CORS
from flask_sock import Sock
import requests
import websocket as ws_client  # websocket-client package
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
import psycopg2.extras
import psycopg2.pool
import redis
from rq import Queue
import livekit.api as livekit_api
import firebase_admin
from firebase_admin import credentials as firebase_credentials, messaging as firebase_messaging

load_dotenv()

# CostLens usage tracking (costlens_agent/, optional/additive). Previously
# activated via sitecustomize.py at interpreter startup -- that relied on
# our sitecustomize.py winning an import race against the OS's own
# /usr/lib/python3.X/sitecustomize.py (shipped by Ubuntu for apport crash
# reporting), which sits earlier on sys.path and silently wins every time,
# so tracking never actually activated in production despite everything
# else being configured correctly. Forcing our copy to win (via PYTHONPATH)
# was tried and reverted: it made costlens_agent's `import httpx` (-> ssl)
# run before gunicorn's gevent worker calls monkey.patch_all(), which left
# gevent's SSL monkey-patching incomplete and caused real RecursionErrors
# in production. Calling install() here instead is safe: app.py is only
# ever imported *after* the gevent worker has already monkey-patched, so
# there's no ordering conflict, and this must run before db_pool below is
# created (its own psycopg2.connect calls need to already be patched).
try:
    from costlens_agent import install as _install_costlens_tracking
    _install_costlens_tracking()
except Exception:
    pass  # never affects app startup, tracking is best-effort

# Set by worker.py (before importing this module) so the RQ worker process
# doesn't also start the email-reminder thread or the notify pub/sub
# listener -- both are meant to run exactly once per deployment, in the
# actual web process, not duplicated in every process that happens to
# import app.py. Defaults to "web" so the existing Flask-serving paths
# (gunicorn import, or `python app.py` directly) are unaffected.
IS_WEB_ROLE = os.getenv("SPEECH2TEXT_ROLE", "web") == "web"

app = Flask(__name__)
sock = Sock(app)
# Set a real, stable secret in your .env for anything beyond local POC use:
# FLASK_SECRET_KEY=<a long random string>
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")

# Only needed for the Vite dev server (a different origin/port than Flask);
# the production build is served by Flask itself below, so it's same-origin
# there and CORS never comes into play.
CORS(app, supports_credentials=True, origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","))


# ---------------------------------------------------------------------------
# Database helpers (Postgres via psycopg2)
#
# Schema itself lives in schema.sql / setup_postgres.py -- run that once
# before starting the app. This file only ever reads/writes; it doesn't
# create tables at runtime the way the old SQLite version did.
# ---------------------------------------------------------------------------

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. Add it to .env, e.g.:\n"
        "  DATABASE_URL=postgresql://postgres:yourpassword@localhost:5432/speech2text\n"
        "Then run: python setup_postgres.py"
    )

# A pool, not one connection per request -- concurrent users, the email
# worker, and every open live-listening session all want a connection at
# the same time. minconn kept warm, maxconn caps how many Postgres will
# ever be asked to hand out at once.
db_pool = psycopg2.pool.ThreadedConnectionPool(minconn=2, maxconn=100, dsn=DATABASE_URL)


def get_raw_connection():
    """A connection from the pool for use outside a Flask request context
    (background threads: the email worker, live-listening sessions,
    background analysis). Caller is responsible for returning it via
    release_raw_connection() when done -- these are pooled, not opened
    fresh each time like the old sqlite3.connect(DB_PATH) calls were."""
    return db_pool.getconn()


def release_raw_connection(conn):
    db_pool.putconn(conn)


def get_db():
    if "db" not in g:
        g.db = db_pool.getconn()
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        if exception is not None:
            db.rollback()
        db_pool.putconn(db)


# ---------------------------------------------------------------------------
# Redis (cache-aside reads + /ws/notify durability/pub-sub)
#
# Unlike Postgres, Redis is never load-bearing -- Postgres stays the source
# of truth for everything, Redis only ever makes reads faster and
# notifications more durable. Every call below swallows connection errors
# and falls back to "as if there were no cache" (hit Postgres directly, or
# for /ws/notify, behave like the old in-process-only delivery), so a dead
# Redis degrades performance, not correctness.
# ---------------------------------------------------------------------------

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
# protocol=2 (RESP2) rather than the client default -- keeps this working
# against older Redis builds (e.g. the Windows ports, based on Redis 5.x)
# that predate RESP3's HELLO handshake, while still working fine against
# any modern Redis server too.
redis_client = redis.from_url(
    REDIS_URL, decode_responses=True, protocol=2, socket_connect_timeout=1, socket_timeout=1
)
# Separate client for the pub/sub listener below -- it holds one connection
# blocked in .listen() waiting for the next message, which can legitimately
# take longer than 1s of idle time. Reusing redis_client's short
# socket_timeout there made every quiet second look like a dropped
# connection (logged as a TimeoutError, retried every 5s forever) instead
# of actually indicating Redis was unreachable.
redis_pubsub_client = redis.from_url(
    REDIS_URL, decode_responses=True, protocol=2, socket_connect_timeout=1, socket_timeout=None
)
# Separate client for RQ (queue + worker) -- RQ makes many more round-trips
# per operation than a single cache GET/SET (worker registration, job
# bookkeeping, heartbeats), and redis_client's 1s timeout -- tuned for
# "fail fast and fall back to Postgres" cache reads -- was too tight for
# that over a slightly-higher-latency connection (e.g. Redis running under
# WSL rather than natively on the same host), causing spurious
# TimeoutErrors. RQ has no equivalent graceful-degradation path, so this
# client gets a more forgiving timeout instead.
rq_redis_client = redis.from_url(
    REDIS_URL, decode_responses=True, protocol=2, socket_connect_timeout=5, socket_timeout=10
)

# Call-recording processing queue (Deepgram transcription + conversation
# creation + titling/analysis) -- see process_own_call_recording_job below.
# A separate `python worker.py` process (SimpleWorker, no fork -- Windows
# doesn't have os.fork) consumes this; if Redis is unreachable, callers fall
# back to running the job function inline instead of failing outright.
call_queue = Queue("calls", connection=rq_redis_client)


def cache_get_json(key):
    try:
        val = redis_client.get(key)
    except redis.RedisError as e:
        print(f"[redis] GET {key} failed: {e!r}")
        return None
    if val is None:
        return None
    try:
        return json.loads(val)
    except ValueError:
        return None


def cache_set_json(key, value, ttl=None):
    try:
        redis_client.set(key, json.dumps(value), ex=ttl)
    except redis.RedisError as e:
        print(f"[redis] SET {key} failed: {e!r}")


def cache_delete(*keys):
    if not keys:
        return
    try:
        redis_client.delete(*keys)
    except redis.RedisError as e:
        print(f"[redis] DELETE {keys} failed: {e!r}")


EI_RECENT_STATEMENTS_TTL = 86400  # 24 hours -- covers the gap until the
# twice-daily/once-daily scheduled batch has definitely had a chance to run
# and write the permanent fact, not just the much-faster real-time trigger.


def remember_ei_recent_statement(user_id, text):
    """A cheap, LLM-free bridge for the EI feedback loop: the background
    fact-extraction thread (ei_adapter.trigger_chat_feedback_extraction)
    can take several seconds and only ever updates the ONE conversation
    thread it ran for; a correction typed in /chat wouldn't be visible to
    a follow-up asked in /chat/global (a different message history) until
    that background write lands. This caches the user's own raw statement,
    keyed by user_id (not conversation_id), so ANY chat surface for that
    user can see it immediately -- no extraction LLM call needed for that,
    since ei_adapter.py just hands the raw text to the reasoning call and
    lets it read the statement directly, same as it already does with the
    current turn's own message. Purely additive: Redis being unreachable
    just means this cache is empty, never an error."""
    if not (text or "").strip():
        return
    try:
        from emotional_intelligence.ei_adapter import is_closing_acknowledgment
        if is_closing_acknowledgment(text):
            return  # "ok"/"cool"/"thanks" etc. -- nothing worth remembering
    except Exception:
        pass  # EI engine is optional and additive -- never affects this endpoint's own behavior
    key = f"ei_recent_statements:{user_id}"
    existing = cache_get_json(key) or []
    existing.append(text.strip())
    cache_set_json(key, existing[-10:], ttl=EI_RECENT_STATEMENTS_TTL)


def get_ei_recent_statements(user_id):
    return cache_get_json(f"ei_recent_statements:{user_id}") or []


def dict_cursor(conn):
    """RealDictCursor gives dict-style row access (row['email']), the
    equivalent of sqlite3.Row from the old version."""
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def verify_db_connection():
    """Fails fast and clearly at startup if Postgres isn't reachable or the
    schema hasn't been applied yet, rather than surfacing a confusing error
    on the first request."""
    try:
        conn = db_pool.getconn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            )
            tables = {row[0] for row in cur.fetchall()}
            cur.close()
            required = {"users", "conversations", "tasks", "personality_notes", "mood_logs", "friendships", "chat_messages", "speaker_profiles"}
            missing = required - tables
            if missing:
                raise RuntimeError(
                    f"Connected to Postgres, but missing tables: {missing}. "
                    "Run: python setup_postgres.py"
                )
            print(f"[db] Connected to Postgres. Tables present: {sorted(tables)}")
        finally:
            db_pool.putconn(conn)
    except psycopg2.OperationalError as e:
        raise RuntimeError(
            f"Could not connect to Postgres using DATABASE_URL: {e}\n"
            "Check that Postgres is running and DATABASE_URL in .env is correct."
        )


def serialize_row(row):
    """psycopg2 returns native datetime objects for TIMESTAMPTZ columns --
    convert them to ISO strings before jsonify, since the frontend expects
    strings it can pass straight to `new Date(...)` (same as when these
    were stored as ISO text under SQLite)."""
    result = dict(row)
    for key, value in result.items():
        if isinstance(value, datetime):
            result[key] = value.isoformat()
    return result


# ---------------------------------------------------------------------------
# Personalisation modes
#
# One mode per user, stored on users.personalization. It steers both the
# background/analyze LLM prompt (build_analysis_prompt) and the chat system
# prompt (build_chat_system_prompt) toward the kind of tasks/observations
# that mode cares about, without touching the underlying extraction pipeline.
# ---------------------------------------------------------------------------

DEFAULT_PERSONALIZATION = "personal"
PERSONALIZATION_MODES = {"personal", "office", "study"}

PERSONALIZATION_GUIDANCE = {
    "personal": (
        "Personal-use mode: treat tasks as personal to-dos or promises made to "
        "friends/family, and speaker notes as relationship and communication-style "
        "observations."
    ),
    "office": (
        "Office-use mode: treat tasks as work action items -- favor clear owners "
        "and deadlines -- and speaker notes as workplace collaboration behavior "
        "(e.g. decisiveness, follow-through, meeting conduct)."
    ),
    "study": (
        "Study-use mode: treat tasks as study to-dos (topics to review, "
        "assignments, exam prep with deadlines), and speaker notes as learning "
        "behavior (concepts struggled with, questions asked, study habits "
        "mentioned)."
    ),
}


def normalize_personalization(value):
    return value if value in PERSONALIZATION_MODES else DEFAULT_PERSONALIZATION


def get_user_personalization(cur, user_id):
    """Returns the user's current mode as stored -- a built-in one OR a
    custom category -- NOT run through normalize_personalization(), so a
    custom category correctly flows through to new conversations' category
    tagging (e.g. /ws/listen) instead of being coerced back to 'personal'.
    Callers that need LLM guidance text still get a safe fallback: both
    build_chat_system_prompt and build_analysis_prompt call
    normalize_personalization() themselves before indexing
    PERSONALIZATION_GUIDANCE, so a custom category there just falls back to
    the 'personal' guidance copy rather than erroring."""
    cur.execute("SELECT personalization FROM users WHERE id = %s", (user_id,))
    row = cur.fetchone()
    value = row["personalization"] if row else None
    return value or DEFAULT_PERSONALIZATION


def is_valid_category_for_user(cur, user_id, value):
    """A category is either one of the 3 built-in personalization modes, or
    one this specific user created (user_categories) -- lets custom
    categories slot into the exact same personalization/category field
    everywhere without touching how it's stored or how LLM guidance for it
    is picked (normalize_personalization already falls back to 'personal'
    guidance for anything outside the built-in 3, custom categories included
    -- that fallback was already there, nothing new needed for it)."""
    if value in PERSONALIZATION_MODES:
        return True
    cur.execute(
        "SELECT 1 FROM user_categories WHERE user_id = %s AND name = %s",
        (user_id, value),
    )
    return cur.fetchone() is not None


# ---------------------------------------------------------------------------
# Persona onboarding -- captured once right after registration, injected as
# a compact one-line summary into the chat/analysis system prompts so the
# LLM can tailor tone/relevance to the person, not just the personalization
# mode. Option lists are validated app-side, same convention as
# PERSONALIZATION_MODES/category.
# ---------------------------------------------------------------------------

GENDER_OPTIONS = ["Male", "Female", "Non-binary", "Prefer not to say"]
LANGUAGE_OPTIONS = ["English", "Hindi"]
HOBBY_OPTIONS = ["Reading", "Sports & Fitness", "Music", "Gaming", "Travel", "Cooking", "Art & Design"]
INTEREST_OPTIONS = [
    "Technology", "Finance & Business", "Health & Wellness", "Arts & Culture",
    "Sports", "Politics & Current Affairs", "Science",
]


def get_user_persona(cur, user_id):
    cur.execute("SELECT * FROM user_personas WHERE user_id = %s", (user_id,))
    return cur.fetchone()


def build_persona_context(persona):
    """One compact line, not a paragraph -- this gets appended to every
    chat/analysis system prompt, so cost scales with how much we put here."""
    if not persona:
        return ""
    bits = []
    if persona.get("age"):
        bits.append(f"age {persona['age']}")
    if persona.get("gender"):
        bits.append(persona["gender"])
    if persona.get("occupation"):
        bits.append(persona["occupation"])
    if persona.get("language_preference"):
        bits.append(f"prefers {persona['language_preference']}")
    if persona.get("hobbies"):
        bits.append("hobbies: " + ", ".join(persona["hobbies"]))
    if persona.get("interests"):
        bits.append("interests: " + ", ".join(persona["interests"]))
    if persona.get("primary_goal"):
        bits.append(f"goal: {persona['primary_goal']}")
    if not bits:
        return ""
    return "User persona (tailor tone/relevance only, never invent facts from it): " + "; ".join(bits) + "."


# ---------------------------------------------------------------------------
# Friends (mood monitoring)
#
# Each user has a short numeric friend_code; entering someone else's code
# adds both directions of the friendship immediately (no pending/accept
# step, matching how a code is typically shared -- if you have it, you were
# meant to have it). Friendship is required before a user's mood_logs can
# be read by anyone else.
# ---------------------------------------------------------------------------

FRIEND_CODE_LENGTH = 6

# A friend's mood is shown as a single compiled emoji, not a raw log list --
# "compiled every 2 hours" means the displayed value is the dominant mood
# across whichever 2-hour clock-aligned window (00-02, 02-04, ... 22-24) has
# the most recent data, recomputed on read rather than via a scheduled job
# (there's no cron/scheduler in this app, and a read-time aggregate over an
# already-indexed range is cheap enough not to need one).
MOOD_EMOJI = {
    "happy": "😊", "stressed": "😰", "calm": "😌", "frustrated": "😤",
    "excited": "🤩", "neutral": "😐", "sad": "😢", "anxious": "😟",
}
DEFAULT_MOOD_EMOJI = "🙂"
MOOD_BUCKET_HOURS = 2


def mood_bucket_bounds(at=None):
    now_local = (at or datetime.now()).astimezone()
    start = now_local.replace(minute=0, second=0, microsecond=0)
    start = start.replace(hour=(start.hour // MOOD_BUCKET_HOURS) * MOOD_BUCKET_HOURS)
    return start, start + timedelta(hours=MOOD_BUCKET_HOURS)


def compute_compiled_mood(cur, user_id, bucket_start, bucket_end):
    """Dominant mood_label in [bucket_start, bucket_end) for user_id, by
    total confidence-weighted score (falls back to count on a tie/NULL
    scores). None if nothing was logged in that window."""
    cur.execute(
        "SELECT mood_label, COALESCE(SUM(mood_score), 0) AS weight, COUNT(*) AS n FROM mood_logs "
        "WHERE user_id = %s AND created_at >= %s AND created_at < %s "
        "GROUP BY mood_label ORDER BY weight DESC, n DESC LIMIT 1",
        (user_id, bucket_start, bucket_end),
    )
    row = cur.fetchone()
    if not row:
        return None
    label = row["mood_label"]
    return {"mood_label": label, "emoji": MOOD_EMOJI.get((label or "").lower(), DEFAULT_MOOD_EMOJI)}


def notify_friends_of_mood_update(user_id, mood_label, friend_ids):
    """Pushed once per 2-hour compiled window per friend (see the
    is-first-in-bucket check at each call site), not on every single
    conversation, so friends get one check-in ping per window instead of
    being spammed as someone's mood log fills up."""
    if not friend_ids or not mood_label:
        return
    emoji = MOOD_EMOJI.get(mood_label.lower(), DEFAULT_MOOD_EMOJI)
    conn = get_raw_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT username FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        name = row[0] if row else "A friend"
    finally:
        release_raw_connection(conn)
    event = {
        "type": "friend_mood_update", "friend_id": user_id, "friend_name": name,
        "mood_label": mood_label, "emoji": emoji,
    }
    for fid in friend_ids:
        push_notification(fid, event)
        send_fcm_to_user(fid, title=f"{name} seems {mood_label} {emoji}", body="Tap to check in", data=event)


def generate_unique_friend_code(cur):
    for _ in range(20):
        code = f"{secrets.randbelow(10 ** FRIEND_CODE_LENGTH):0{FRIEND_CODE_LENGTH}d}"
        cur.execute("SELECT 1 FROM users WHERE friend_code = %s", (code,))
        if not cur.fetchone():
            return code
    raise RuntimeError("Could not generate a unique friend code")


def get_or_create_friend_code(db, cur, user_id):
    cur.execute("SELECT friend_code FROM users WHERE id = %s", (user_id,))
    code = cur.fetchone()["friend_code"]
    if code:
        return code
    code = generate_unique_friend_code(cur)
    cur.execute("UPDATE users SET friend_code = %s WHERE id = %s", (code, user_id))
    db.commit()
    return code


# ---------------------------------------------------------------------------
# Music vs. speech classification (YAMNet)
#
# YAMNet is a pretrained audio-event classifier (521 AudioSet classes) run
# via TensorFlow Hub. All heavy imports (tensorflow, tensorflow_hub, av) are
# deferred into the functions that use them -- if the dependency isn't
# installed or the model fails to load, this feature silently no-ops
# instead of crashing the listening session.
# ---------------------------------------------------------------------------

_yamnet_model = None
_yamnet_class_names = None
_yamnet_lock = threading.Lock()

# YAMNet's 521 classes are fine-grained ("Ukulele", "Child speech", etc.) --
# grouping them into these two coarse buckets by keyword avoids depending on
# one exact class firing.
MUSIC_CLASS_KEYWORDS = (
    "music", "singing", "musical instrument", "guitar", "piano", "drum",
    "song", "orchestra", "band", "violin", "flute", "trumpet",
)
SPEECH_CLASS_KEYWORDS = (
    "speech", "conversation", "narration", "monologue",
    "child speech", "male speech", "female speech",
)

AUDIO_CLASSIFY_INTERVAL_SECONDS = float(os.getenv("AUDIO_CLASSIFY_INTERVAL_SECONDS", "5"))
AUDIO_CLASSIFY_TAIL_SECONDS = float(os.getenv("AUDIO_CLASSIFY_TAIL_SECONDS", "3"))


def get_yamnet_model():
    """Lazily loads YAMNet once per process. First call downloads the model
    from TensorFlow Hub and caches it locally (TFHUB_CACHE_DIR) for next time."""
    global _yamnet_model, _yamnet_class_names
    if _yamnet_model is None:
        with _yamnet_lock:
            if _yamnet_model is None:
                import csv
                import tensorflow_hub as hub
                print("[audio-classify] Loading YAMNet (first run downloads it, cached after)...")
                _yamnet_model = hub.load("https://tfhub.dev/google/yamnet/1")
                class_map_path = _yamnet_model.class_map_path().numpy().decode("utf-8")
                with open(class_map_path) as f:
                    _yamnet_class_names = [row["display_name"] for row in csv.DictReader(f)]
                print("[audio-classify] YAMNet loaded.")
    return _yamnet_model, _yamnet_class_names


def decode_webm_to_waveform(raw_bytes, target_sr=16000):
    """Decodes accumulated webm/opus bytes (what the browser's MediaRecorder
    produces) into a mono float32 waveform at the sample rate YAMNet expects.
    Uses PyAV, which bundles its own ffmpeg libs -- no separate ffmpeg
    install needed on the host machine."""
    import io
    import av
    import numpy as np

    container = av.open(io.BytesIO(raw_bytes))
    resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=target_sr)
    chunks = []
    for frame in container.decode(audio=0):
        frame.pts = None
        for resampled in resampler.resample(frame):
            chunks.append(resampled.to_ndarray())
    container.close()
    if not chunks:
        return None
    audio = np.concatenate(chunks, axis=1).flatten().astype(np.float32) / 32768.0
    return audio, target_sr


def classify_audio(raw_audio_bytes, tail_seconds=AUDIO_CLASSIFY_TAIL_SECONDS):
    """Classifies the trailing N seconds of session audio as 'music',
    'speech', or 'unclear'. Returns None on any failure (missing deps,
    decode error, not enough audio yet) rather than raising."""
    try:
        decoded = decode_webm_to_waveform(raw_audio_bytes)
        if decoded is None:
            return None
        audio, sr = decoded
        tail_samples = int(tail_seconds * sr)
        if len(audio) > tail_samples:
            audio = audio[-tail_samples:]
        if len(audio) < sr * 1.0:  # need at least ~1s for a stable read
            return None

        model, class_names = get_yamnet_model()
        scores, _embeddings, _spectrogram = model(audio)
        mean_scores = scores.numpy().mean(axis=0)
        top_indices = mean_scores.argsort()[-5:][::-1]
        top_labels = [(class_names[i], float(mean_scores[i])) for i in top_indices]

        music_score = sum(s for label, s in top_labels if any(k in label.lower() for k in MUSIC_CLASS_KEYWORDS))
        speech_score = sum(s for label, s in top_labels if any(k in label.lower() for k in SPEECH_CLASS_KEYWORDS))

        if music_score > speech_score and music_score > 0.15:
            return "music"
        if speech_score >= music_score and speech_score > 0.1:
            return "speech"
        return "unclear"
    except Exception as e:
        print(f"[audio-classify] Failed (skipping this check): {e!r}")
        return None


def normalize_reminder_at(raw):
    """The LLM resolves relative dates ("Friday") against the server's local
    time but returns a naive string with no timezone. Attach the server's
    local offset and convert to UTC here, once, so every downstream
    consumer -- browser notification comparisons, calendar links, the email
    scheduler -- works off an unambiguous UTC timestamp instead of each
    guessing the timezone independently. Returns a real datetime object --
    psycopg2 adapts that directly into the TIMESTAMPTZ column."""
    if not raw:
        return None
    try:
        naive = datetime.fromisoformat(raw)
    except ValueError:
        return None
    local_tz = datetime.now().astimezone().tzinfo
    aware_local = naive.replace(tzinfo=local_tz)
    return aware_local.astimezone(timezone.utc)


def build_ics(summary, description, start_utc, duration_minutes=30):
    """Minimal RFC 5545 VEVENT. Attaching this to the reminder email lets
    Gmail, Outlook, and Apple Mail all offer to add it directly to whichever
    calendar the person actually uses -- no Google/Microsoft API needed."""
    end_utc = start_utc + timedelta(minutes=duration_minutes)
    fmt = lambda dt: dt.strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Throughline//Task Reminder//EN",
        "BEGIN:VEVENT",
        f"UID:{uuid.uuid4()}@throughline",
        f"DTSTAMP:{fmt(datetime.now(timezone.utc))}",
        f"DTSTART:{fmt(start_utc)}",
        f"DTEND:{fmt(end_utc)}",
        f"SUMMARY:{summary}",
        f"DESCRIPTION:{description}",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(lines)


def send_reminder_email(to_email, description, owner, due_date, reminder_at):
    """Sends the reminder over SMTP with an .ics attachment. POC note: From
    and To are both set to the user's own registered email, per how this is
    being tested -- register using the same address configured as
    SMTP_USERNAME so the mail is coming from (and going to) one real inbox.
    Some providers (Gmail included) may rewrite or reject a From header that
    doesn't match the authenticated SMTP account; for anything beyond a POC,
    send through a transactional provider (SES/SendGrid/Mailgun) instead."""
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_username = os.getenv("SMTP_USERNAME")
    smtp_password = os.getenv("SMTP_PASSWORD")
    if not all([smtp_host, smtp_username, smtp_password]):
        print("[email] SMTP not configured (SMTP_HOST/SMTP_USERNAME/SMTP_PASSWORD) -- skipping")
        return False

    # reminder_at comes straight from Postgres as a native datetime object
    # (TIMESTAMPTZ column) -- but tolerate a string too, in case this is
    # ever called from somewhere that hasn't made that switch.
    if isinstance(reminder_at, str):
        try:
            start_utc = datetime.fromisoformat(reminder_at)
        except ValueError:
            return False
    else:
        start_utc = reminder_at

    ics_content = build_ics(
        description,
        f"Owner: {owner or 'unspecified'} | Due: {due_date or 'not specified'}",
        start_utc,
    )

    msg = MIMEMultipart()
    msg["From"] = to_email
    msg["To"] = to_email
    msg["Subject"] = f"Reminder: {description}"
    body = (
        f"This is a reminder for a task extracted from your conversation:\n\n"
        f"{description}\n\nOwner: {owner or 'unspecified'}\nDue: {due_date or 'not specified'}\n\n"
        f"An .ics file is attached -- open it to add this to Google Calendar, Outlook, or Apple Calendar."
    )
    msg.attach(MIMEText(body, "plain"))

    ics_part = MIMEBase("text", "calendar", method="REQUEST", name="reminder.ics")
    ics_part.set_payload(ics_content)
    encoders.encode_base64(ics_part)
    ics_part.add_header("Content-Disposition", "attachment", filename="reminder.ics")
    msg.attach(ics_part)

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.sendmail(smtp_username, [to_email], msg.as_string())
        return True
    except Exception as e:
        print(f"[email] Failed to send reminder email to {to_email}: {e!r}")
        return False


def email_reminder_worker():
    """Runs continuously in a background thread, independent of any browser
    tab -- this is what makes email reminders work even if the app isn't
    open anywhere, unlike the browser-notification poll."""
    while True:
        conn = None
        try:
            conn = get_raw_connection()
            cur = dict_cursor(conn)
            now = datetime.now(timezone.utc)
            _BASE_REMINDER_QUERY = (
                "SELECT tasks.id, tasks.user_id, tasks.description, tasks.owner, tasks.due_date, "
                "tasks.reminder_at, users.email FROM tasks "
                "JOIN users ON tasks.user_id = users.id {extra} "
                "WHERE tasks.reminder_at IS NOT NULL AND tasks.email_sent = FALSE "
                "AND tasks.reminder_at <= %s AND users.email IS NOT NULL AND users.email != '' {filter}"
            )
            try:
                # COALESCE(..., TRUE) treats a user with no nudges.user_settings
                # row (or the setting never touched) as still enabled -- see
                # nudge_engine.get_user_settings's identical default.
                cur.execute(
                    _BASE_REMINDER_QUERY.format(
                        extra="LEFT JOIN nudges.user_settings s ON s.user_id = tasks.user_id",
                        filter="AND COALESCE(s.task_reminder_notifications_enabled, TRUE) = TRUE",
                    ),
                    (now,),
                )
            except Exception:
                # nudges.user_settings may not exist yet (schema not applied on
                # this deployment) -- task reminder emails are core, pre-existing
                # functionality and must never break just because that optional
                # table is missing.
                conn.rollback()
                cur.execute(_BASE_REMINDER_QUERY.format(extra="", filter=""), (now,))
            rows = cur.fetchall()
            for row in rows:
                sent = send_reminder_email(
                    row["email"], row["description"], row["owner"], row["due_date"], row["reminder_at"]
                )
                if sent:
                    cur.execute("UPDATE tasks SET email_sent = TRUE WHERE id = %s", (row["id"],))
                    conn.commit()
                    push_notification(row["user_id"], {
                        "type": "reminder_email_sent", "task_id": row["id"], "description": row["description"],
                    })
                    send_fcm_to_user(
                        row["user_id"], title="Reminder sent", body=row["description"],
                        data={"type": "reminder_email_sent", "task_id": row["id"]},
                    )
            cur.close()
        except Exception as e:
            print(f"[email_reminder_worker] error: {e!r}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                release_raw_connection(conn)
        time.sleep(60)


CALL_RING_TIMEOUT_SECONDS = 45


def call_ring_timeout_worker():
    """Runs continuously in a background thread, same pattern as
    email_reminder_worker -- WhatsApp-style: a call nobody answers doesn't
    ring forever. Without this, a call whose invitee never taps
    decline/accept (and whose caller never hangs up either) would stay
    'ringing' in the DB permanently, its incoming-call notification would
    never be told to dismiss, and it would never show up as a clean
    "missed call" in history. After CALL_RING_TIMEOUT_SECONDS: ends the
    call, tells the caller (their "Calling..." screen closes via the same
    call_ended signal leave_call sends), and tells whoever never answered
    both to dismiss their ringing notification (call_ended) and, separately,
    that they missed a call (a visible push, unlike call_ended's silent
    data-only one)."""
    while True:
        conn = None
        to_notify = []  # (call_id, initiator_id, caller_name, missed_user_ids)
        try:
            conn = get_raw_connection()
            cur = dict_cursor(conn)
            cutoff = datetime.now(timezone.utc) - timedelta(seconds=CALL_RING_TIMEOUT_SECONDS)
            cur.execute(
                "SELECT id, initiator_user_id FROM calls WHERE status = 'ringing' AND created_at < %s",
                (cutoff,),
            )
            timed_out = cur.fetchall()
            for call in timed_out:
                call_id = call["id"]
                cur.execute(
                    "UPDATE calls SET status = 'ended', ended_at = now() WHERE id = %s AND status = 'ringing'",
                    (call_id,),
                )
                if cur.rowcount == 0:
                    continue  # raced with a decline/join/leave that already ended it -- nothing to do
                cur.execute(
                    "SELECT user_id FROM call_participants WHERE call_id = %s AND status = 'invited'",
                    (call_id,),
                )
                missed_user_ids = [r["user_id"] for r in cur.fetchall()]
                cur.execute("SELECT username FROM users WHERE id = %s", (call["initiator_user_id"],))
                caller_row = cur.fetchone()
                caller_name = caller_row["username"] if caller_row else "Someone"
                to_notify.append((call_id, call["initiator_user_id"], caller_name, missed_user_ids))
            conn.commit()
            cur.close()
        except Exception as e:
            print(f"[call_ring_timeout_worker] error: {e!r}")
            if conn:
                conn.rollback()
            to_notify = []
        finally:
            if conn:
                release_raw_connection(conn)

        for call_id, initiator_id, caller_name, missed_user_ids in to_notify:
            push_notification(initiator_id, {"type": "call_ended", "call_id": call_id})
            send_fcm_to_user(
                initiator_id, title="Call ended", body="",
                data={"type": "call_ended", "call_id": call_id},
                include_notification=False,
            )
            for uid in missed_user_ids:
                push_notification(uid, {"type": "call_ended", "call_id": call_id})
                send_fcm_to_user(
                    uid, title="Call ended", body="",
                    data={"type": "call_ended", "call_id": call_id},
                    include_notification=False,
                )
                send_fcm_to_user(
                    uid, title="Missed call", body=f"{caller_name} called you",
                    data={
                        "type": "missed_call", "call_id": call_id,
                        "caller_id": initiator_id, "caller_name": caller_name,
                    },
                    include_notification=True,
                )
        time.sleep(10)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Token auth (JWT)
#
# Session cookies work fine for the web app served from this same origin,
# but a Capacitor-wrapped mobile build runs the page from a different
# origin (capacitor://localhost) than the API it calls -- browsers won't
# carry that cross-origin cookie, so cookie-only auth silently breaks
# there. A bearer token sidesteps that entirely and works identically for
# both; the web app keeps using the session cookie it already has, and
# never needs to touch localStorage/tokens itself.
# ---------------------------------------------------------------------------

JWT_ALGORITHM = "HS256"
JWT_EXP_DAYS = 30


def generate_token(user_id, username):
    payload = {
        "user_id": user_id,
        "username": username,
        "exp": datetime.now(timezone.utc) + timedelta(days=JWT_EXP_DAYS),
    }
    return pyjwt.encode(payload, app.secret_key, algorithm=JWT_ALGORITHM)


def decode_token(token):
    try:
        return pyjwt.decode(token, app.secret_key, algorithms=[JWT_ALGORITHM])
    except pyjwt.PyJWTError:
        return None


def authenticated_user_id():
    """Session cookie first (the existing web app), then a bearer token
    (mobile). Returns None if neither is present/valid."""
    if session.get("user_id"):
        return session.get("user_id")
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        payload = decode_token(auth_header[7:])
        if payload:
            return payload.get("user_id")
    return None


def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not authenticated_user_id():
            return jsonify({"error": "Not authenticated"}), 401
        return view_func(*args, **kwargs)
    return wrapped


def current_user_id():
    return authenticated_user_id()


@app.route("/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""
    personalization = normalize_personalization(data.get("personalization"))

    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400
    if not email or "@" not in email or "." not in email.split("@")[-1]:
        return jsonify({"error": "A valid email is required (used for reminder emails)"}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400

    db = get_db()
    cur = dict_cursor(db)
    cur.execute("SELECT id FROM users WHERE username = %s", (username,))
    if cur.fetchone():
        return jsonify({"error": "Username is already taken"}), 409

    password_hash = generate_password_hash(password)
    friend_code = generate_unique_friend_code(cur)
    cur.execute(
        "INSERT INTO users (username, email, password_hash, personalization, friend_code) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (username, email, password_hash, personalization, friend_code),
    )
    user_id = cur.fetchone()["id"]
    db.commit()

    session["user_id"] = user_id
    session["username"] = username
    return jsonify({
        "id": user_id,
        "username": username,
        "email": email,
        "personalization": personalization,
        "friend_code": friend_code,
        "token": generate_token(user_id, username),
    })


@app.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "SELECT id, username, password_hash FROM users WHERE username = %s",
        (username,),
    )
    row = cur.fetchone()

    if not row or not check_password_hash(row["password_hash"], password):
        return jsonify({"error": "Invalid username or password"}), 401

    session["user_id"] = row["id"]
    session["username"] = row["username"]
    return jsonify({
        "id": row["id"],
        "username": row["username"],
        "token": generate_token(row["id"], row["username"]),
    })


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/me", methods=["GET"])
def me():
    user_id = authenticated_user_id()
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401
    username = session.get("username")
    if not username:
        db = get_db()
        cur = dict_cursor(db)
        cur.execute("SELECT username FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        username = row["username"] if row else None
    return jsonify({"id": user_id, "username": username})


@app.route("/settings", methods=["GET"])
@login_required
def get_settings():
    db = get_db()
    cur = dict_cursor(db)
    user_id = current_user_id()
    cur.execute("SELECT personalization FROM users WHERE id = %s", (user_id,))
    personalization = normalize_personalization(cur.fetchone()["personalization"])
    friend_code = get_or_create_friend_code(db, cur, user_id)
    return jsonify({"personalization": personalization, "friend_code": friend_code})


@app.route("/settings", methods=["POST"])
@login_required
def update_settings():
    data = request.get_json(silent=True) or {}
    if "personalization" not in data:
        return jsonify({"error": "personalization is required"}), 400
    personalization = data.get("personalization")
    user_id = current_user_id()
    db = get_db()
    cur = dict_cursor(db)
    if not is_valid_category_for_user(cur, user_id, personalization):
        return jsonify({"error": "personalization must be a built-in mode or one of your own categories"}), 400

    cur.execute(
        "UPDATE users SET personalization = %s WHERE id = %s",
        (personalization, user_id),
    )
    db.commit()
    return jsonify({"ok": True, "personalization": personalization})


@app.route("/settings/nudges", methods=["GET"])
@login_required
def get_nudge_settings():
    """Delegates entirely to nudges/nudge_engine.py -- this endpoint owns no
    logic of its own, matching how every other nudges/emotional_intelligence
    touchpoint in this file works. Not wrapped in a swallow-errors try/except
    like the optional-context injections elsewhere: this endpoint's only job
    IS the nudges module, so a failure here should surface, not be hidden."""
    from nudges.nudge_engine import get_user_settings
    return jsonify(get_user_settings(current_user_id()))


def _notify_setting_toggled(user_id, feature_label, enabled):
    """Confirms a settings toggle via the same push_notification (WS) +
    send_fcm_to_user (real device notification) pair every other
    significant event in this file uses -- e.g. task_created,
    reminder_email_sent. The WS side is a no-op today (notify_provider.dart's
    _handle switch has no case for 'setting_toggled', and has no default
    case either, so an unrecognized type is silently ignored, not an
    error) -- sent anyway for consistency with that pattern; the FCM side
    is what the user actually sees, and needs no client-side handling
    since it's a plain notification with no action buttons or deep link."""
    state = "on" if enabled else "off"
    event = {"type": "setting_toggled", "feature": feature_label, "enabled": enabled}
    push_notification(user_id, event)
    send_fcm_to_user(
        user_id, title="Setting updated", body=f"You've turned {state} {feature_label}.",
        data={"type": "setting_toggled", "feature": feature_label, "enabled": str(enabled)},
    )


_INDIVIDUAL_SMART_FEATURE_FIELDS = (
    "nudges_enabled",
    "cognitive_intelligence_enabled",
    "task_reminder_notifications_enabled",
    "tags_questions_enabled",
)


@app.route("/settings/nudges", methods=["POST"])
@login_required
def update_nudge_settings():
    """Explicit product decision: only the combined "Smart features" master
    toggle (smart_features_enabled) is notification-worthy. The four
    underlying settings it controls (nudges, cognitive intelligence, task
    reminder notifications, tags/questions) never send their own individual
    notification, even if changed directly via this same endpoint -- so a
    granular per-field update stays silent on purpose.

    Standard parent/child toggle semantics: children are only individually
    changeable while the parent is on. The Flutter client already disables
    those switches in the UI when smart_features_enabled is false, but that
    alone is just a UI affordance -- this 400 is what actually enforces it
    (e.g. against a stale client, or a direct API call)."""
    from nudges.nudge_engine import get_user_settings, set_user_settings
    data = request.get_json(silent=True) or {}
    user_id = current_user_id()

    smart_features_enabled = data.get("smart_features_enabled")
    if smart_features_enabled is not None:
        # Cascade DOWN only: the master switch sets all four underlying
        # features to match itself. It deliberately does NOT get recomputed
        # from them the other way -- toggling an individual feature off
        # later (via the branch below) leaves smart_features_enabled exactly
        # as it is here, so it keeps reading as on until explicitly turned
        # off again, regardless of what the individual features are doing.
        result = set_user_settings(
            user_id,
            smart_features_enabled=smart_features_enabled,
            nudges_enabled=smart_features_enabled,
            cognitive_intelligence_enabled=smart_features_enabled,
            task_reminder_notifications_enabled=smart_features_enabled,
            tags_questions_enabled=smart_features_enabled,
        )
        _notify_setting_toggled(user_id, "Smart features", smart_features_enabled)
        return jsonify(result)

    wants_individual_change = any(data.get(f) is not None for f in _INDIVIDUAL_SMART_FEATURE_FIELDS)
    if wants_individual_change and not get_user_settings(user_id)["smart_features_enabled"]:
        return jsonify({"error": "Turn on Smart features before changing an individual feature"}), 400

    result = set_user_settings(
        user_id,
        nudges_enabled=data.get("nudges_enabled"),
        cognitive_intelligence_enabled=data.get("cognitive_intelligence_enabled"),
        task_reminder_notifications_enabled=data.get("task_reminder_notifications_enabled"),
        tags_questions_enabled=data.get("tags_questions_enabled"),
    )
    return jsonify(result)


# ---------------------------------------------------------------------------
# Custom categories -- personal/office/study always exist (built into app
# code); this lets a user add their own on top, usable anywhere a category
# is (conversations.category, users.personalization).
# ---------------------------------------------------------------------------

@app.route("/categories", methods=["GET"])
@login_required
def list_categories():
    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "SELECT name FROM user_categories WHERE user_id = %s ORDER BY name",
        (current_user_id(),),
    )
    custom = [r["name"] for r in cur.fetchall()]
    return jsonify({"builtin": sorted(PERSONALIZATION_MODES), "custom": custom})


@app.route("/categories", methods=["POST"])
@login_required
def create_category():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    if len(name) > 40:
        return jsonify({"error": "name is too long (max 40 characters)"}), 400
    user_id = current_user_id()
    db = get_db()
    cur = dict_cursor(db)
    if name.lower() in {m.lower() for m in PERSONALIZATION_MODES}:
        return jsonify({"error": "That's already a built-in category"}), 409
    cur.execute(
        "SELECT 1 FROM user_categories WHERE user_id = %s AND lower(name) = lower(%s)",
        (user_id, name),
    )
    if cur.fetchone():
        return jsonify({"error": "You already have a category with that name"}), 409
    cur.execute(
        "INSERT INTO user_categories (user_id, name) VALUES (%s, %s)",
        (user_id, name),
    )
    db.commit()
    return jsonify({"ok": True, "name": name})


@app.route("/categories/<name>", methods=["DELETE"])
@login_required
def delete_category(name):
    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "DELETE FROM user_categories WHERE user_id = %s AND name = %s RETURNING id",
        (current_user_id(), name),
    )
    deleted = cur.fetchone()
    db.commit()
    if not deleted:
        return jsonify({"error": "Category not found"}), 404
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Persona onboarding
# ---------------------------------------------------------------------------

@app.route("/persona", methods=["GET"])
@login_required
def get_persona_route():
    db = get_db()
    cur = dict_cursor(db)
    persona = get_user_persona(cur, current_user_id())
    return jsonify({
        "completed": persona is not None,
        "persona": serialize_row(persona) if persona else None,
        "options": {
            "gender": GENDER_OPTIONS,
            "language_preference": LANGUAGE_OPTIONS,
            "hobbies": HOBBY_OPTIONS,
            "interests": INTEREST_OPTIONS,
        },
    })


@app.route("/persona", methods=["POST"])
@login_required
def save_persona_route():
    data = request.get_json(silent=True) or {}
    age = data.get("age")
    gender = (data.get("gender") or "").strip()
    language_preference = (data.get("language_preference") or "").strip()
    hobbies = [h for h in (data.get("hobbies") or []) if h in HOBBY_OPTIONS]
    interests = [i for i in (data.get("interests") or []) if i in INTEREST_OPTIONS]
    occupation = (data.get("occupation") or "").strip()
    primary_goal = (data.get("primary_goal") or "").strip() or None

    # age, gender, language_preference, hobbies, interests, occupation are
    # mandatory; primary_goal is the one optional field (7 total).
    try:
        age = int(age)
    except (TypeError, ValueError):
        return jsonify({"error": "age is required and must be a number"}), 400
    if age < 13 or age > 120:
        return jsonify({"error": "age must be between 13 and 120"}), 400
    if gender not in GENDER_OPTIONS:
        return jsonify({"error": f"gender must be one of {GENDER_OPTIONS}"}), 400
    if language_preference not in LANGUAGE_OPTIONS:
        return jsonify({"error": f"language_preference must be one of {LANGUAGE_OPTIONS}"}), 400
    if not hobbies:
        return jsonify({"error": "Pick at least one hobby"}), 400
    if not interests:
        return jsonify({"error": "Pick at least one interest"}), 400
    if not occupation:
        return jsonify({"error": "occupation is required"}), 400

    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "INSERT INTO user_personas (user_id, age, gender, language_preference, hobbies, interests, occupation, primary_goal) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (user_id) DO UPDATE SET age = EXCLUDED.age, gender = EXCLUDED.gender, "
        "language_preference = EXCLUDED.language_preference, hobbies = EXCLUDED.hobbies, "
        "interests = EXCLUDED.interests, occupation = EXCLUDED.occupation, primary_goal = EXCLUDED.primary_goal",
        (current_user_id(), age, gender, language_preference, hobbies, interests, occupation, primary_goal),
    )
    db.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Speaker parsing
# ---------------------------------------------------------------------------

SPEAKER_LINE_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 _'-]{0,30}):\s+(.*)$")


def remember_speaker_profile(cur, user_id, name):
    """Record `name` as a known speaker for this user, so future "new
    speaker detected" prompts (in this session or a later one) can offer it
    as an existing option instead of relying on the name being retyped
    identically. Safe to call on every rename -- ON CONFLICT DO NOTHING
    means re-using an existing name is a no-op, not a duplicate/error.

    The conflict target is the case-insensitive expression index
    (idx_speaker_profiles_user_name_ci in schema.sql), not the plain
    exact-match UNIQUE(user_id, name) -- naming the exact-match one here
    would only catch identical-case collisions; a name that only matches an
    existing profile case-insensitively (e.g. typing "amit" when "Amit"
    already exists) would still violate the other index during the actual
    insert attempt, and since that index isn't the declared arbiter,
    Postgres raises a hard UniqueViolation instead of silently no-op'ing --
    exactly the bug that let picking/typing an existing speaker's name
    sometimes fail to map back onto their one real profile."""
    cur.execute(
        "INSERT INTO speaker_profiles (user_id, name) VALUES (%s, %s) "
        "ON CONFLICT (user_id, lower(name)) DO NOTHING",
        (user_id, name),
    )


def _first_value(row):
    """Pull the sole column out of a fetchone() result, regardless of
    whether the cursor in use returns plain tuples or RealDictRow dicts."""
    if row is None:
        return None
    if isinstance(row, dict):
        return next(iter(row.values()))
    return row[0]


def get_or_create_profile_id(cur, user_id, name):
    """Resolve `name` to its canonical speaker_profiles.id, matching
    case-insensitively so 'Kunal' and 'kunal' collapse into one profile
    instead of forking into two. This is what links a personality_notes row
    to a real, durable person rather than a free-text label.

    See remember_speaker_profile for why the ON CONFLICT target below must
    be the case-insensitive expression index, not the plain exact-match
    one -- otherwise a case-varying name collision raises instead of
    resolving to the existing profile."""
    cur.execute(
        "SELECT id FROM speaker_profiles WHERE user_id = %s AND lower(name) = lower(%s)",
        (user_id, name),
    )
    row = cur.fetchone()
    if row:
        return _first_value(row)
    cur.execute(
        "INSERT INTO speaker_profiles (user_id, name) VALUES (%s, %s) "
        "ON CONFLICT (user_id, lower(name)) DO NOTHING RETURNING id",
        (user_id, name),
    )
    row = cur.fetchone()
    if row:
        return _first_value(row)
    # Exact-name race: another request inserted this same spelling between
    # our SELECT and INSERT -- look it up again instead of erroring.
    cur.execute(
        "SELECT id FROM speaker_profiles WHERE user_id = %s AND lower(name) = lower(%s)",
        (user_id, name),
    )
    return _first_value(cur.fetchone())


def record_profile_category(cur, profile_id, category):
    """Upsert the (profile, category) many-to-many mapping -- stored
    explicitly rather than only derivable by joining personality_notes ->
    conversations.category, so a person's category history survives even
    after the source conversation is deleted (conversation_id there is ON
    DELETE SET NULL)."""
    if not profile_id or not category:
        return
    cur.execute(
        "INSERT INTO profile_categories (profile_id, category, first_seen_at, last_seen_at) "
        "VALUES (%s, %s, now(), now()) "
        "ON CONFLICT (profile_id, category) DO UPDATE SET last_seen_at = now()",
        (profile_id, category),
    )


def record_profile_conversation(cur, profile_id, conversation_id):
    """Records that this profile appeared in this conversation -- the
    reliable cross-session index /chat/global's retrieval uses, recorded
    unconditionally (not just when the LLM happened to produce a
    behavioral observation that pass, unlike personality_notes)."""
    if not profile_id or not conversation_id:
        return
    cur.execute(
        "INSERT INTO profile_conversations (profile_id, conversation_id) VALUES (%s, %s) "
        "ON CONFLICT (profile_id, conversation_id) DO NOTHING",
        (profile_id, conversation_id),
    )


def parse_speakers(raw_text):
    """Split transcript into (speaker_label, line) pairs.

    Convention: lines prefixed with "Name: ..." are attributed to that
    speaker. Lines with no recognizable prefix are attributed to 'Unknown'.
    """
    segments = []
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        match = SPEAKER_LINE_RE.match(line)
        if match:
            speaker, text = match.group(1).strip(), match.group(2).strip()
            segments.append((speaker, text))
        else:
            segments.append(("Unknown", line))
    return segments


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

def call_llm(messages):
    api_key = os.getenv("OLLAMA_API_KEY") or os.getenv("olama_api_key")
    if not api_key:
        raise RuntimeError("Missing Ollama API key")

    # Ollama Cloud bills by GPU-time/usage-level, not strict token count --
    # lighter models (e.g. gpt-oss:20b) sit in a cheaper usage tier than
    # gpt-oss:120b. Override via .env to test cost/quality tradeoff without
    # touching code: OLLAMA_MODEL=gpt-oss:20b
    payload = {
        "model": os.getenv("OLLAMA_MODEL", "gpt-oss:120b"),
        "messages": messages,
        "stream": False,
    }

    response = requests.post(
        "https://ollama.com/api/chat",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=90,
    )
    response.raise_for_status()
    result = response.json()
    return result.get("message", {}).get("content", "")


# Strips a leading <think>...</think> reasoning block some models (e.g. Groq's
# qwen/qwen3.6-27b, used by call_groq_vision below) prepend to their own
# answer -- confirmed via a live test call that the chain-of-thought lands
# inline in the same content string, not a separate field. Without this, that
# reasoning monologue would end up stored as part of raw_transcript/the chat
# reply instead of just the actual answer.
_THINK_BLOCK_RE = re.compile(r"^\s*<think>.*?</think>\s*", re.DOTALL)


def _strip_think_block(text):
    return _THINK_BLOCK_RE.sub("", text or "").strip()


def call_groq_vision(image_bytes, mimetype, instruction):
    """Vision-capable counterpart to call_llm, above -- deliberately a
    separate function rather than a branch inside call_llm, since the
    provider (Groq, not Ollama Cloud) and request shape (OpenAI-style
    multimodal `content` blocks) both differ. GROQ_API_KEY is already live
    in .env and already proven working for STT (USE_GROQ_STT) -- reusing it
    here needs no new account/billing setup, unlike the earlier xAI/Grok
    attempt that stalled on billing.

    GROQ_VISION_MODEL is overridable via .env for the same reason
    OLLAMA_MODEL is above: Groq's vision-capable models are preview/evaluation
    models that get renamed or deprecated (meta-llama/llama-4-scout-17b-16e-
    instruct, vision-capable, was already deprecated once) -- a future rename
    should be a .env edit, not a code change."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("Missing GROQ_API_KEY")

    b64 = base64.b64encode(image_bytes).decode("ascii")
    payload = {
        "model": os.getenv("GROQ_VISION_MODEL", "qwen/qwen3.6-27b"),
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": instruction},
                {"type": "image_url", "image_url": {"url": f"data:{mimetype};base64,{b64}"}},
            ],
        }],
        "temperature": 0.2,
    }
    response = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=90,
    )
    response.raise_for_status()
    result = response.json()
    content = result["choices"][0]["message"]["content"]
    return _strip_think_block(content)


IMAGE_MAX_UPLOAD_BYTES = 10 * 1024 * 1024
IMAGE_MAX_DIMENSION = 1600


def _prepare_image_for_vision(file_storage):
    """Validates and normalizes an uploaded image before it's sent to Groq.
    Raises ValueError with a user-facing message on anything invalid.

    Saves to a temp file first (same tempfile.mkstemp()+.save() pattern
    already used for audio uploads, e.g. upload_own_call_recording above)
    rather than reading straight into memory, so Pillow can stream from disk.
    The temp file is always removed in `finally` -- raw image bytes are never
    persisted anywhere, matching how audio uploads are consumed then
    discarded rather than stored long-term.

    Client-supplied mimetype can't be trusted alone (a renamed file can claim
    any Content-Type) -- Image.open(...).verify() is what actually rejects
    non-image bytes. verify() invalidates the file object afterward, so the
    image is reopened before any further use."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    fd, tmp_path = tempfile.mkstemp(prefix="img_vision_", suffix=".upload")
    with os.fdopen(fd, "wb") as f:
        file_storage.save(f)
    try:
        try:
            with Image.open(tmp_path) as verify_img:
                verify_img.verify()
        except (UnidentifiedImageError, OSError):
            raise ValueError("Could not read that as an image")

        with Image.open(tmp_path) as img:
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGB")
            img.thumbnail((IMAGE_MAX_DIMENSION, IMAGE_MAX_DIMENSION))
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=85)
            return buffer.getvalue(), "image/jpeg"
    finally:
        os.remove(tmp_path)


def extract_json(text):
    """Best-effort extraction of a JSON object from an LLM text response."""
    text = text.strip()
    text = re.sub(r"^```json\s*|^```\s*|```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except ValueError:
            return None
    return None


# Cost knobs: larger batch = fewer Ollama calls (less repeated system-prompt
# overhead); higher word floor = skip calls on trivially short/filler batches.
BACKGROUND_ANALYSIS_BATCH_SIZE = int(os.getenv("BACKGROUND_ANALYSIS_BATCH_SIZE", "12"))
MIN_WORDS_FOR_ANALYSIS = int(os.getenv("MIN_WORDS_FOR_ANALYSIS", "8"))

def build_analysis_prompt(personalization=DEFAULT_PERSONALIZATION, persona_context=""):
    # Injecting the real current time lets the model resolve relative phrases
    # ("Friday", "tomorrow", "in an hour") into an actual timestamp we can
    # schedule a browser reminder against -- without this, "due_date" is just
    # a display string with nothing a scheduler could act on.
    now_local = datetime.now().astimezone()
    mode_guidance = PERSONALIZATION_GUIDANCE[normalize_personalization(personalization)]
    persona_line = f"\n{persona_context}\n" if persona_context else ""
    return f"""Today is {now_local.strftime('%A, %Y-%m-%d')}, current time {now_local.strftime('%H:%M %Z')}.

{mode_guidance}
{persona_line}
Extract from this conversation transcript:
1. "tasks": action items/commitments. Each:
   - description (<20 words, your own words)
   - owner (if stated/implied, else null)
   - due_date: short human phrase as said/implied (e.g. "Friday", "tonight"), else null
   - reminder_at: ONLY if a specific date and/or time is stated or clearly implied, resolve it \
against today's date above and return exact ISO 8601 "YYYY-MM-DDTHH:MM:SS" (assume 09:00:00 if a \
day is given with no time). Else null. Do not guess if nothing time-related was said.
2. "speakers": per speaker label, up to 3 short behavioral observations grounded only in what \
they said/how they said it (e.g. "proposed the deadline", "hedged twice"). No diagnoses or \
clinical/mental-health terms, no motive speculation.
3. "mood": one object describing the overall emotional tone of THIS transcript for the person \
recording it -- "label" (a single plain word, e.g. "happy", "stressed", "calm", "frustrated", \
"excited", "neutral", "sad", "anxious") and "score" (0.0-1.0 intensity/confidence). Base this \
only on tone and word choice actually present, no diagnoses.
4. "topics": at most 5 short (1-4 word) topic keywords covering what THIS transcript was actually \
about (e.g. "budget approval", "kitchen sink", "weekend plans") -- always extracted independently \
of "questions" below, not a fallback for when there's no question. Empty list if nothing distinct \
came up.
5. "questions": at most 5 short, tappable follow-up questions worth asking the assistant next, \
matching the mode above -- a clear, directly-answerable question actually raised or clearly \
implied in THIS transcript, phrased as that question (e.g. "What's the budget cap?"). Empty list \
if none are clearly raised/implied -- do not invent generic questions just to fill this.

Reply with ONLY this JSON, no preamble/fences:
{{"tasks": [{{"description": "", "owner": null, "due_date": null, "reminder_at": null}}], \
"speakers": [{{"label": "", "observations": [""]}}], \
"mood": {{"label": "neutral", "score": 0.5}}, "topics": [""], "questions": [""]}}
Empty lists if none found. "mood" is always required."""


# Cap how much transcript gets sent for titling -- a topic is normally clear
# from the opening portion, and this keeps the call cheap even for a very
# long session.
TITLE_MAX_CHARS = 6000


def generate_conversation_title(raw_transcript):
    """Asks the LLM for a short (3-4 word) title. Returns None on any
    failure (missing key, empty transcript, unparseable reply) so the
    caller can simply leave the existing title (typically null/"Untitled
    conversation" in the UI) untouched rather than erroring."""
    text = (raw_transcript or "").strip()
    if not text:
        return None
    try:
        content = call_llm([
            {"role": "system", "content": (
                "Reply with ONLY a short title for this conversation transcript -- "
                "3 to 4 words maximum, no quotes, no trailing punctuation, no preamble. "
                "Capture its main topic or purpose."
            )},
            {"role": "user", "content": text[:TITLE_MAX_CHARS]},
        ])
    except Exception:
        return None
    title = (content or "").strip().strip('"').strip("'")
    title = title.splitlines()[0].strip() if title else ""
    return title[:80] or None


def run_conversation_titling(user_id, conversation_id, raw_transcript):
    """Runs in its own thread with its own pooled connection, same pattern
    as run_background_analysis -- generating the title is an LLM call and
    shouldn't hold up the websocket teardown it's triggered from."""
    title = generate_conversation_title(raw_transcript)
    if not title:
        return
    conn = get_raw_connection()
    try:
        cur = conn.cursor()
        # title IS NULL guards against overwriting a title this ran on
        # before (or one a user renames, if that's ever added later).
        cur.execute(
            "UPDATE conversations SET title = %s WHERE id = %s AND title IS NULL",
            (title, conversation_id),
        )
        updated = cur.rowcount > 0
        conn.commit()
        cur.close()
        if updated:
            cache_delete(f"user:{user_id}:conversation:{conversation_id}", f"user:{user_id}:conversations")
    except Exception as e:
        print(f"[run_conversation_titling] DB error: {e!r}")
        conn.rollback()
    finally:
        release_raw_connection(conn)


def assign_shared_call_title(call_id, user_id, conversation_id, raw_transcript):
    """Call-flow equivalent of run_conversation_titling, but every
    participant in a call shares one title instead of each independently
    asking the LLM (and getting different wording for what's the same
    conversation). Whichever participant's own scope=own recording finishes
    processing first generates the title and claims it on calls.shared_title
    (the `WHERE shared_title IS NULL` guard means only one caller's UPDATE
    can ever actually claim it, even if two arrive at nearly the same time);
    everyone after just reuses whatever's already claimed there."""
    conn = get_raw_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT shared_title FROM calls WHERE id = %s", (call_id,))
        row = cur.fetchone()
        title = row[0] if row else None

        if not title:
            title = generate_conversation_title(raw_transcript)
            if not title:
                return
            cur.execute(
                "UPDATE calls SET shared_title = %s WHERE id = %s AND shared_title IS NULL RETURNING id",
                (title, call_id),
            )
            if cur.fetchone() is None:
                # Someone else claimed it between our SELECT and UPDATE --
                # use theirs instead of forking into two different titles.
                cur.execute("SELECT shared_title FROM calls WHERE id = %s", (call_id,))
                title = cur.fetchone()[0]

        cur.execute(
            "UPDATE conversations SET title = %s WHERE id = %s AND title IS NULL",
            (title, conversation_id),
        )
        updated = cur.rowcount > 0
        conn.commit()
        if updated:
            cache_delete(f"user:{user_id}:conversation:{conversation_id}", f"user:{user_id}:conversations")
    except Exception as e:
        print(f"[assign_shared_call_title] DB error: {e!r}")
        conn.rollback()
    finally:
        release_raw_connection(conn)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    # Serves the built React app (frontend/, built via `npm run build` into
    # static/app/). Built JS/CSS live under /static/app/assets and are
    # already served by Flask's default static handling -- only
    # index.html itself needs this dedicated route.
    react_index = os.path.join(app.static_folder, "app", "index.html")
    if os.path.exists(react_index):
        return send_from_directory(os.path.join(app.static_folder, "app"), "index.html")
    return render_template("index.html")


@app.route("/sw.js")
def service_worker():
    # Served from root (not /static/sw.js) so its default scope covers the
    # whole app -- a service worker's scope is capped at its own URL path.
    return send_from_directory(
        app.static_folder, "sw.js", mimetype="application/javascript"
    )


def _transcribe_with_deepgram(audio_bytes, content_type):
    """Original transcription path -- unchanged from before USE_GROQ_STT
    existed. Diarized, so returns "Speaker N: text" lines plus per-line
    segments with timing."""
    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        raise RuntimeError("Missing DEEPGRAM_API_KEY")
    resp = requests.post(
        "https://api.deepgram.com/v1/listen",
        params={"diarize": "true", "punctuate": "true", "utterances": "true", "model": "nova-2"},
        headers={"Authorization": f"Token {api_key}", "Content-Type": content_type or "audio/webm"},
        data=audio_bytes,
        timeout=120,
    )
    resp.raise_for_status()
    utterances = (resp.json().get("results") or {}).get("utterances") or []
    lines, segments = [], []
    for utt in utterances:
        text = (utt.get("transcript") or "").strip()
        if not text:
            continue
        speaker_label = f"Speaker {utt.get('speaker', 0)}"
        lines.append(f"{speaker_label}: {text}")
        segments.append({"speaker": speaker_label, "text": text, "start": utt.get("start"), "end": utt.get("end")})
    return "\n".join(lines), segments


_GROQ_EXT_BY_MIMETYPE = {
    "audio/webm": "webm",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/m4a": "m4a",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/ogg": "ogg",
}


def _transcribe_with_groq(audio_bytes, content_type):
    """Groq's Whisper endpoint has no speaker diarization. The mobile call-
    recording path this matters most for (process_own_call_recording_job)
    already uploads one file per participant -- each file is single-speaker
    to begin with, so diarization was never actually needed there. Every
    result is attributed to a single "Speaker 0" label so downstream code
    (which expects the "Speaker N: text" convention everywhere) keeps
    working unchanged; the legacy multi-speaker mixed-stream web path
    (upload_call_recording) does lose real speaker separation when this
    flag is on, since Groq has no way to tell speakers apart in one file."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("Missing GROQ_API_KEY")
    # Groq (like the OpenAI Whisper API it mirrors) infers the audio format
    # from the uploaded filename's extension -- a bare filename with no
    # extension gets rejected outright with a 400, so this can't just be a
    # fixed placeholder like the other providers tolerate.
    mimetype = (content_type or "audio/webm").split(";")[0].strip().lower()
    ext = _GROQ_EXT_BY_MIMETYPE.get(mimetype, "webm")
    resp = requests.post(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {api_key}"},
        files={"file": (f"audio.{ext}", io.BytesIO(audio_bytes), mimetype)},
        data={"model": "whisper-large-v3-turbo", "response_format": "json"},
        timeout=120,
    )
    resp.raise_for_status()
    text = (resp.json().get("text") or "").strip()
    if not text:
        return "", []
    return f"Speaker 0: {text}", [{"speaker": "Speaker 0", "text": text, "start": None, "end": None}]


def transcribe_audio_bytes(audio_bytes, content_type):
    """Central transcription entrypoint -- every audio-to-text call site in
    this app routes through here. USE_GROQ_STT=true in .env sends audio to
    Groq's Whisper API (multilingual/Hinglish/code-switch friendly, no
    diarization); false or unset reverts to the original Deepgram nova-2
    path with no other code changes needed. Returns (raw_transcript,
    segments); raises RuntimeError for a missing API key or lets
    requests.RequestException/HTTPError propagate for the caller to handle."""
    if os.getenv("USE_GROQ_STT", "false").strip().lower() == "true":
        return _transcribe_with_groq(audio_bytes, content_type)
    return _transcribe_with_deepgram(audio_bytes, content_type)


@app.route("/transcribe", methods=["POST"])
@login_required
def transcribe_audio():
    """Send a recorded audio clip for transcription (Groq or Deepgram, see
    transcribe_audio_bytes/USE_GROQ_STT) and return a speaker-labeled
    transcript in the "Name: text" convention used elsewhere in this app."""
    if "audio" not in request.files:
        return jsonify({"error": "No audio file uploaded"}), 400

    audio_file = request.files["audio"]
    audio_bytes = audio_file.read()
    content_type = audio_file.mimetype or "audio/webm"

    try:
        transcript_text, segments = transcribe_audio_bytes(audio_bytes, content_type)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else 500
        return jsonify({"error": f"Transcription error: {e}"}), status
    except requests.RequestException as e:
        return jsonify({"error": f"Could not reach transcription service: {e}"}), 502

    return jsonify({"transcript": transcript_text, "segments": segments})


@app.route("/save", methods=["POST"])
@login_required
def save_conversation():
    data = request.get_json(silent=True) or {}
    raw_transcript = (data.get("transcript") or "").strip()
    title = (data.get("title") or "").strip() or None

    if not raw_transcript:
        return jsonify({"error": "Transcript is required"}), 400

    db = get_db()
    cur = dict_cursor(db)
    user_id = current_user_id()
    cur.execute(
        "INSERT INTO conversations (user_id, title, raw_transcript) "
        "VALUES (%s, %s, %s) RETURNING id",
        (user_id, title, raw_transcript),
    )
    conversation_id = cur.fetchone()["id"]
    db.commit()
    cache_delete(f"user:{user_id}:conversations")
    return jsonify({"conversation_id": conversation_id})


def _apply_tags_questions_setting(user_id, topics, questions):
    """Blanks topics/questions if this user has turned off "tags/questions
    in conversations" -- suppresses what's SHOWN, not the extraction itself
    (topics/questions come from the same shared LLM call as tasks/speakers/
    mood in both call sites below, so there's no separate call to skip).
    Defaults to showing them if the nudges module/table isn't available for
    any reason -- this is a pre-existing, user-visible feature, so its
    absence must never look like a bug caused by an unrelated optional
    module. Shared by run_background_analysis and /analyze -- both surface
    topics/questions to the user, so both respect this setting."""
    try:
        from nudges.nudge_engine import get_user_settings
        if not get_user_settings(user_id)["tags_questions_enabled"]:
            return [], []
    except Exception:
        pass
    return topics, questions


def run_background_analysis(user_id, conversation_id, delta_text, push):
    """Runs in its own thread with its own pooled connection -- Flask's
    request-scoped `g` connection can't be shared across threads. Extracts
    tasks + behavioral observations from just the new lines since the last
    pass (not the whole conversation), then pushes a summary back over the
    websocket so the UI can show it without the user clicking Analyze."""
    if not delta_text.strip():
        return

    conn = get_raw_connection()
    try:
        pcur = dict_cursor(conn)
        mode = get_user_personalization(pcur, user_id)
        persona_context = build_persona_context(get_user_persona(pcur, user_id))
    finally:
        release_raw_connection(conn)

    try:
        content = call_llm([
            {"role": "system", "content": build_analysis_prompt(mode, persona_context)},
            {"role": "user", "content": delta_text},
        ])
    except Exception:
        return

    parsed = extract_json(content)
    if not parsed:
        return

    tasks = parsed.get("tasks") or []
    speakers = parsed.get("speakers") or []
    mood = parsed.get("mood") or {}
    topics = [t for t in (parsed.get("topics") or []) if isinstance(t, str) and t.strip()][:5]
    questions = [q for q in (parsed.get("questions") or []) if isinstance(q, str) and q.strip()][:5]
    topics, questions = _apply_tags_questions_setting(user_id, topics, questions)

    # TEMPORARY diagnostic -- why aren't topics/questions showing up even
    # when tasks are, for the live background_update path. Remove once
    # resolved.
    try:
        with open("topics_debug.log", "a", encoding="utf-8") as f:
            f.write(
                f"\n--- {datetime.now().isoformat()} user={user_id} conv={conversation_id} ---\n"
                f"delta_text={delta_text!r}\n"
                f"raw_llm_content={content!r}\n"
                f"parsed_topics={parsed.get('topics')!r} parsed_questions={parsed.get('questions')!r}\n"
                f"final_topics={topics!r} final_questions={questions!r}\n"
            )
    except Exception as e:
        print(f"[topics_debug] failed to write log: {e!r}")

    mood_label = (mood.get("label") or "").strip()

    conn = get_raw_connection()
    created_tasks = []
    notify_mood_friend_ids = []
    try:
        cur = conn.cursor()
        cur.execute("SELECT category FROM conversations WHERE id = %s", (conversation_id,))
        category_row = cur.fetchone()
        category = _first_value(category_row) or "personal"
        for task in tasks:
            description = (task.get("description") or "").strip()
            if not description:
                continue
            cur.execute(
                "INSERT INTO tasks (user_id, conversation_id, description, owner, due_date, reminder_at, status) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'open') RETURNING id",
                (user_id, conversation_id, description, task.get("owner"), task.get("due_date"),
                 normalize_reminder_at(task.get("reminder_at"))),
            )
            created_tasks.append((cur.fetchone()[0], description))
        for speaker in speakers:
            label = (speaker.get("label") or "").strip()
            if not label:
                continue
            profile_id = get_or_create_profile_id(cur, user_id, label)
            record_profile_category(cur, profile_id, category)
            record_profile_conversation(cur, profile_id, conversation_id)
            for obs in speaker.get("observations") or []:
                obs = (obs or "").strip()
                if not obs:
                    continue
                cur.execute(
                    "INSERT INTO personality_notes (user_id, conversation_id, speaker_label, profile_id, observation) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (user_id, conversation_id, label, profile_id, obs),
                )
        if mood_label:
            bucket_start, bucket_end = mood_bucket_bounds()
            cur.execute(
                "SELECT 1 FROM mood_logs WHERE user_id = %s AND created_at >= %s AND created_at < %s LIMIT 1",
                (user_id, bucket_start, bucket_end),
            )
            is_first_in_bucket = cur.fetchone() is None
            cur.execute(
                "INSERT INTO mood_logs (user_id, conversation_id, mood_label, mood_score) "
                "VALUES (%s, %s, %s, %s)",
                (user_id, conversation_id, mood_label, mood.get("score")),
            )
            if is_first_in_bucket:
                cur.execute("SELECT friend_id FROM friendships WHERE user_id = %s", (user_id,))
                notify_mood_friend_ids = [r[0] for r in cur.fetchall()]
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"[run_background_analysis] DB error: {e!r}")
        conn.rollback()
        created_tasks = []
        notify_mood_friend_ids = []
    finally:
        release_raw_connection(conn)

    for task_id, description in created_tasks:
        push_notification(user_id, {
            "type": "task_created", "task_id": task_id, "description": description,
            "conversation_id": conversation_id,
        })
        send_fcm_to_user(
            user_id, title="New task", body=description,
            data={"type": "task_created", "task_id": task_id, "conversation_id": conversation_id},
        )

    if notify_mood_friend_ids:
        notify_friends_of_mood_update(user_id, mood_label, notify_mood_friend_ids)

    push({
        "type": "background_update", "tasks_found": len(tasks), "speakers_found": len(speakers),
        "topics": topics, "questions": questions,
    })


# Cheap pre-filter before spending an LLM call on reminder detection below --
# most chat messages aren't a reminder request at all, so this must be cheap
# and skip the vast majority of traffic (same role as ei_adapter.py's
# is_closing_acknowledgment/is_probably_just_a_question).
_REMINDER_INTENT_RE = re.compile(
    r"\bremind(?:er|ers)?\b|\bdon'?t let me forget\b", re.IGNORECASE
)

REMINDER_EXTRACTION_PROMPT_TEMPLATE = """Today is {today}, current time {now}.

The user just said this in chat. Determine whether they're asking to be reminded of one or more things (tasks, events, commitments, deadlines) -- not just using the word "remind" in passing. There can be MORE than one -- e.g. a list of exams/classes/bills where they asked for a reminder for each -- extract one entry per distinct thing to remind about, never one combined summary covering several of them.

For each one, extract:
- description: short (<20 words) description of what to be reminded about, in your own words
- due_date: short human phrase as said/implied (e.g. "Saturday", "tonight"), else null
- reminder_at: resolve any date/time phrase against today's date above into exact ISO 8601 "YYYY-MM-DDTHH:MM:SS" (assume 09:00:00 if only a day is given, no time). Else null. Do not guess if nothing time-related was said.

If this is not a genuine reminder request, or you can't identify any specific things to remind about, reply with {{"reminders": []}}.

The user's message:
{message}

Reply with ONLY this JSON, no preamble/fences:
{{"reminders": [{{"description": "", "due_date": null, "reminder_at": null}}]}}"""


def trigger_reminder_extraction(user_id, message_content) -> None:
    """Fire-and-forget, mirrors trigger_chat_feedback_extraction exactly --
    call from a background thread right after /chat or /chat/global has
    already sent its reply, so "remind me to X on Saturday" typed in chat
    becomes a real task with a resolved reminder_at, without adding latency
    to the user-facing response. Once inserted, _check_overdue_tasks
    (nudges/nudge_engine.py) and email_reminder_worker (above) already pick
    it up automatically off reminder_at/status -- nothing else needed.

    Extracts a LIST of reminders, not just one -- e.g. a photo of a class/exam
    schedule with "remind me about each of these" needs one task per item, not
    one task summarizing all of them. Ordinary single-reminder chat messages
    just produce a one-item list, so this is a strict generalization, not a
    behavior change for existing callers.

    Never raises and never blocks -- same reasoning as
    trigger_chat_feedback_extraction: this always runs after the chat
    response is already on its way, so a failure here must never surface to
    the user."""
    message_content = (message_content or "").strip()
    if not user_id or not message_content:
        return
    if not _REMINDER_INTENT_RE.search(message_content):
        return

    try:
        now_local = datetime.now().astimezone()
        prompt = REMINDER_EXTRACTION_PROMPT_TEMPLATE.format(
            today=now_local.strftime("%A, %Y-%m-%d"),
            now=now_local.strftime("%H:%M %Z"),
            message=message_content,
        )
        content = call_llm([{"role": "user", "content": prompt}])
        parsed = extract_json(content)
        reminders = (parsed or {}).get("reminders") or []
        if not reminders:
            return

        conn = get_raw_connection()
        created = []
        try:
            cur = conn.cursor()
            for item in reminders:
                description = (item.get("description") or "").strip()
                if not description:
                    continue
                cur.execute(
                    "INSERT INTO tasks (user_id, conversation_id, description, owner, due_date, reminder_at, status) "
                    "VALUES (%s, NULL, %s, NULL, %s, %s, 'open') RETURNING id",
                    (user_id, description, item.get("due_date"), normalize_reminder_at(item.get("reminder_at"))),
                )
                created.append((cur.fetchone()[0], description))
            conn.commit()
        except Exception as e:
            print(f"[trigger_reminder_extraction] DB error: {e!r}")
            conn.rollback()
            return
        finally:
            release_raw_connection(conn)

        # Identical event shape to run_background_analysis's task_created
        # push above -- existing client handling (notify_provider.dart's
        # case 'task_created', and the React equivalent) picks this up with
        # no changes needed. One push per task, same as if each had been
        # typed as a separate reminder request.
        for task_id, description in created:
            push_notification(user_id, {
                "type": "task_created", "task_id": task_id, "description": description,
                "conversation_id": None,
            })
            send_fcm_to_user(
                user_id, title="New task", body=description,
                data={"type": "task_created", "task_id": task_id, "conversation_id": ""},
            )
    except Exception as e:
        print(f"[trigger_reminder_extraction] failed: {e!r}")


# ---------------------------------------------------------------------------
# Live notifications (WebSocket + Redis-backed pending queue + pub/sub)
#
# Mirrors WhatsApp's shape: a per-user queue, drained into any live
# connection immediately, or held until the user's next connection if none
# is open right now. The queue itself lives in Redis (pending_events:{id})
# so it survives a server restart instead of being lost, and every push is
# also published on a Redis channel so a second worker process (this app
# runs single-process today, but isn't guaranteed to forever) would still
# deliver live to a connection it holds, rather than silently missing it.
#
# If Redis is unreachable, this falls back to exactly the old in-process-only
# behavior (the _pending_events dict below), so a dead cache never breaks
# notification delivery within this one process -- it only loses the
# "survives a restart" and "other worker processes" guarantees.
# ---------------------------------------------------------------------------

_notify_lock = threading.Lock()
_notify_connections = {}  # user_id -> list[ws]
_pending_events = {}      # user_id -> list[event dict]; Redis-down fallback only
_NOTIFY_CHANNEL = "notify_events"
_PROCESS_ID = uuid.uuid4().hex


def _deliver_to_local_connections(user_id, event):
    with _notify_lock:
        conns = list(_notify_connections.get(user_id, []))
    delivered = False
    for ws in conns:
        try:
            ws.send(json.dumps(event))
            delivered = True
        except Exception:
            pass
    return delivered


def _queue_pending_event(user_id, event):
    key = f"pending_events:{user_id}"
    try:
        redis_client.rpush(key, json.dumps(event))
        redis_client.ltrim(key, -50, -1)  # cap so a long-offline user's queue can't grow unbounded
        return
    except redis.RedisError:
        pass
    with _notify_lock:
        queue = _pending_events.setdefault(user_id, [])
        queue.append(event)
        del queue[:-50]


def push_notification(user_id, event):
    delivered = _deliver_to_local_connections(user_id, event)
    try:
        redis_client.publish(
            _NOTIFY_CHANNEL,
            json.dumps({"user_id": user_id, "event": event, "origin": _PROCESS_ID}),
        )
    except redis.RedisError:
        pass
    if not delivered:
        _queue_pending_event(user_id, event)


def relay_ephemeral(user_id, event):
    """Same live-delivery path as push_notification (local connection first,
    ALWAYS followed by cross-process pub/sub too -- covers a user with a
    second connection held by a different worker process, same reasoning
    push_notification's own unconditional publish already has), but
    deliberately skips the durable pending queue -- used for typing
    indicators, which are meaningless once stale (unlike a task/message
    notification, "was typing 10 minutes ago" isn't worth surfacing the
    next time the recipient reconnects)."""
    _deliver_to_local_connections(user_id, event)
    try:
        redis_client.publish(
            _NOTIFY_CHANNEL,
            json.dumps({"user_id": user_id, "event": event, "origin": _PROCESS_ID}),
        )
    except redis.RedisError:
        pass


def _notify_pubsub_listener():
    """Cross-process delivery: if some other worker process holds the live
    connection for a user an event was just published for, this delivers it
    there. In today's single-process deployment this never fires for a
    connection this same process already handled synchronously above (the
    `origin` check skips those) -- it only matters once/if this ever runs
    with more than one worker."""
    while True:
        try:
            pubsub = redis_pubsub_client.pubsub()
            pubsub.subscribe(_NOTIFY_CHANNEL)
            for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    payload = json.loads(message["data"])
                except ValueError:
                    continue
                if payload.get("origin") == _PROCESS_ID:
                    continue
                user_id = payload.get("user_id")
                event = payload.get("event")
                with _notify_lock:
                    conns = list(_notify_connections.get(user_id, []))
                if not conns:
                    continue
                for ws in conns:
                    try:
                        ws.send(json.dumps(event))
                    except Exception:
                        pass
                try:
                    redis_client.lrem(f"pending_events:{user_id}", 1, json.dumps(event))
                except redis.RedisError:
                    pass
        except redis.RedisError as e:
            print(f"[redis] notify pubsub listener error: {e!r}; retrying in 5s")
            time.sleep(5)
        except Exception as e:
            print(f"[redis] notify pubsub listener crashed: {e!r}; retrying in 5s")
            time.sleep(5)


if IS_WEB_ROLE:
    threading.Thread(target=_notify_pubsub_listener, daemon=True).start()


def _filter_stale_call_events(events):
    """A queued incoming_call event replayed after a gap (client reconnects,
    or opens the app much later) is actively misleading if that call isn't
    actually ringing anymore -- it could have since been answered elsewhere,
    declined, cancelled, or timed out. Without this, a phone that was
    offline while a call came and went would show a phantom "incoming call"
    for something that's long over the moment it reconnects. Drop any
    incoming_call whose call has already moved past 'ringing'; every other
    event type is left alone (a stale chat_message/task_created is still
    valid to review, just late)."""
    call_ids = [e["call_id"] for e in events if e.get("type") == "incoming_call" and e.get("call_id")]
    if not call_ids:
        return events
    still_ringing = set()
    conn = get_raw_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM calls WHERE id = ANY(%s) AND status = 'ringing'", (call_ids,))
        still_ringing = {r[0] for r in cur.fetchall()}
    except Exception as e:
        print(f"[_filter_stale_call_events] DB error: {e!r}")
        return events  # fail open -- a rare stale replay beats losing a legit queued event to a bug here
    finally:
        release_raw_connection(conn)
    return [e for e in events if e.get("type") != "incoming_call" or e.get("call_id") in still_ringing]


def _drain_pending_events(user_id):
    key = f"pending_events:{user_id}"
    events = []
    try:
        pipe = redis_client.pipeline()
        pipe.lrange(key, 0, -1)
        pipe.delete(key)
        raw_events, _ = pipe.execute()
        for raw in raw_events:
            try:
                events.append(json.loads(raw))
            except ValueError:
                pass
    except redis.RedisError as e:
        print(f"[redis] drain pending_events failed for user {user_id}: {e!r}")
    with _notify_lock:
        events.extend(_pending_events.pop(user_id, []))
    return _filter_stale_call_events(events)


# ---------------------------------------------------------------------------
# Mobile push (Firebase Cloud Messaging)
#
# push_notification() above only reaches a client with an open /ws/notify
# socket -- fine for a browser tab, useless for a native app that's
# backgrounded or killed. send_fcm_to_user() is the mobile equivalent: it
# reaches every device the user has registered via POST /devices/register,
# regardless of whether the app is open. Both are called side by side at
# each notification site rather than one replacing the other.
#
# Silently no-ops (never raises) if FIREBASE_CREDENTIALS_PATH isn't
# configured -- same "not set up yet" pattern as SMTP_HOST for email, so the
# app works before/without a Firebase project existing.
# ---------------------------------------------------------------------------

_firebase_app = None
_firebase_init_attempted = False


def get_firebase_app():
    global _firebase_app, _firebase_init_attempted
    if _firebase_app is not None:
        return _firebase_app
    if _firebase_init_attempted:
        return None
    _firebase_init_attempted = True
    cred_path = os.getenv("FIREBASE_CREDENTIALS_PATH")
    if not cred_path or not os.path.exists(cred_path):
        print("[fcm] FIREBASE_CREDENTIALS_PATH not set/found -- push notifications disabled")
        return None
    try:
        _firebase_app = firebase_admin.initialize_app(firebase_credentials.Certificate(cred_path))
        print(f"[fcm] Firebase Admin initialized (project: {_firebase_app.project_id})")
    except ValueError:
        # Already initialized in this process (e.g. a second gunicorn worker
        # re-imported this module) -- reuse the existing default app.
        _firebase_app = firebase_admin.get_app()
    except Exception as e:
        # A bad/misconfigured credential must disable push, not take down
        # every endpoint that happens to call send_fcm_to_user with it.
        print(f"[fcm] Firebase Admin init failed, push notifications disabled: {e!r}")
        _firebase_app = None
    return _firebase_app


def send_fcm_to_user(user_id, title, body, data=None, include_notification=True):
    """Best-effort push to every device this user has registered. Never
    raises -- called from background threads (mood/task extraction) and
    after the DB work it's reporting on is already committed, so a
    delivery failure here must never affect anything else.

    include_notification=False sends a data-only message (no `notification`
    block, so the OS/Firebase SDK doesn't auto-post anything) -- used for
    incoming_call, where the Flutter client shows its own full-screen-intent
    notification instead. Every other caller keeps the default (unchanged)
    behavior."""
    app_ = get_firebase_app()
    if not app_:
        return
    conn = get_raw_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT token FROM push_tokens WHERE user_id = %s", (user_id,))
        tokens = [r[0] for r in cur.fetchall()]
    finally:
        release_raw_connection(conn)
    if not tokens:
        print(f"[fcm] user {user_id} has no registered device tokens -- skipping push")
        return

    data_str = {k: str(v) for k, v in (data or {}).items()}
    stale_tokens = []
    for token in tokens:
        message = firebase_messaging.Message(
            token=token,
            notification=firebase_messaging.Notification(title=title, body=body) if include_notification else None,
            data=data_str,
            android=firebase_messaging.AndroidConfig(priority="high"),
            apns=firebase_messaging.APNSConfig(headers={"apns-priority": "10"}),
        )
        try:
            firebase_messaging.send(message, app=app_)
            print(f"[fcm] sent '{data_str.get('type')}' to user {user_id} ({token[:12]}...)")
        except firebase_messaging.UnregisteredError:
            stale_tokens.append(token)
            print(f"[fcm] token for user {user_id} is stale/unregistered, removing ({token[:12]}...)")
        except Exception as e:
            print(f"[fcm] send failed for user {user_id}: {e!r}")

    if stale_tokens:
        conn = get_raw_connection()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM push_tokens WHERE token = ANY(%s)", (stale_tokens,))
            conn.commit()
        finally:
            release_raw_connection(conn)


@app.route("/devices/register", methods=["POST"])
@login_required
def register_device():
    """Flutter calls this once it has an FCM token (on launch, and again
    whenever the token refreshes -- the ON CONFLICT keeps this idempotent
    either way, and reassigns a token to a new user if the same device logs
    into a different account)."""
    data = request.get_json(silent=True) or {}
    token = (data.get("token") or "").strip()
    platform = (data.get("platform") or "").strip().lower()
    if not token:
        return jsonify({"error": "token is required"}), 400
    if platform not in ("android", "ios"):
        return jsonify({"error": "platform must be 'android' or 'ios'"}), 400
    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "INSERT INTO push_tokens (user_id, token, platform) VALUES (%s, %s, %s) "
        "ON CONFLICT (token) DO UPDATE SET user_id = EXCLUDED.user_id, platform = EXCLUDED.platform, updated_at = now()",
        (current_user_id(), token, platform),
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/devices/unregister", methods=["POST"])
@login_required
def unregister_device():
    """Called on logout so a signed-out device stops receiving pushes for
    the account it just left."""
    data = request.get_json(silent=True) or {}
    token = (data.get("token") or "").strip()
    if not token:
        return jsonify({"error": "token is required"}), 400
    db = get_db()
    cur = dict_cursor(db)
    cur.execute("DELETE FROM push_tokens WHERE token = %s AND user_id = %s", (token, current_user_id()))
    db.commit()
    return jsonify({"ok": True})


def _handle_notify_client_message(user_id, msg):
    """Direct-message delivery/read receipts, WhatsApp-style: they ride this
    same persistent connection as an inbound message on it, rather than a
    separate REST call per ack -- no per-tick HTTP connection setup, and it
    reuses the exact channel that's already open for everything else. Each
    ack batches every currently-pending message from that friend in one
    UPDATE (an implicit watermark -- "everything not yet delivered/read, as
    of now" -- rather than needing the client to track/send individual
    message ids), the same batching a real client does by sending one
    "read up to X" marker instead of N separate acks.

    Also handles 'typing' -- a pure relay (see relay_ephemeral), never
    written to the database at all. Not batched/watermarked like the acks
    above since there's nothing to batch: each keystroke-driven client send
    is already its own complete, disposable signal."""
    msg_type = msg.get("type")
    if msg_type not in ("ack_delivered", "ack_read", "typing"):
        return
    friend_id = msg.get("friend_id")
    if not isinstance(friend_id, int):
        return

    if msg_type == "typing":
        relay_ephemeral(friend_id, {"type": "friend_typing", "friend_id": user_id})
        return

    conn = get_raw_connection()
    try:
        cur = conn.cursor()
        if msg_type == "ack_delivered":
            cur.execute(
                "UPDATE direct_messages SET delivered_at = now() "
                "WHERE sender_id = %s AND recipient_id = %s AND delivered_at IS NULL "
                "RETURNING id",
                (friend_id, user_id),
            )
            event_type = "direct_messages_delivered"
        else:
            cur.execute(
                "UPDATE direct_messages SET read_at = now(), delivered_at = COALESCE(delivered_at, now()) "
                "WHERE sender_id = %s AND recipient_id = %s AND read_at IS NULL "
                "RETURNING id",
                (friend_id, user_id),
            )
            event_type = "direct_messages_read"
        ids = [row[0] for row in cur.fetchall()]
        conn.commit()
    except Exception as e:
        print(f"[ws_notify] ack handling error: {e!r}")
        conn.rollback()
        ids = []
    finally:
        release_raw_connection(conn)

    if ids:
        push_notification(friend_id, {"type": event_type, "friend_id": user_id, "message_ids": ids})


@sock.route("/ws/notify")
def ws_notify(ws):
    user_id = session.get("user_id")
    if not user_id:
        payload = decode_token(request.args.get("token", ""))
        user_id = payload.get("user_id") if payload else None
    if not user_id:
        ws.close()
        return

    with _notify_lock:
        _notify_connections.setdefault(user_id, []).append(ws)
    for event in _drain_pending_events(user_id):
        try:
            ws.send(json.dumps(event))
        except Exception:
            pass

    try:
        while True:
            raw = ws.receive()
            if raw is None:
                break
            # Only direct-message delivery/read acks are expected inbound on
            # this channel (see _handle_notify_client_message) -- anything
            # else (malformed JSON, an unrecognized type) is just ignored,
            # never lets a bad client message kill the connection.
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            try:
                _handle_notify_client_message(user_id, msg)
            except Exception as e:
                print(f"[ws_notify] client message handling failed: {e!r}")
    finally:
        with _notify_lock:
            conns = _notify_connections.get(user_id, [])
            if ws in conns:
                conns.remove(ws)
            if not conns:
                _notify_connections.pop(user_id, None)


@sock.route("/ws/listen")
def ws_listen(ws):
    """Live listening session: browser streams mic audio in over this
    websocket, we relay it to Deepgram's streaming API, and stream
    diarized transcript lines + new-speaker notices back to the browser.
    Task/profile extraction runs automatically in the background -- no
    separate Analyze click needed. A second background thread periodically
    classifies the recent audio as music or speech via YAMNet and pushes a
    notice to the frontend whenever that classification changes."""
    # The browser WebSocket API can't send an Authorization header, so a
    # mobile (token-auth) client passes it as ?token=... instead; the web
    # app keeps relying on the session cookie and never needs this.
    user_id = session.get("user_id")
    if not user_id:
        payload = decode_token(request.args.get("token", ""))
        user_id = payload.get("user_id") if payload else None
    if not user_id:
        ws.close()
        return

    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        ws.send(json.dumps({"type": "error", "message": "Missing DEEPGRAM_API_KEY"}))
        return

    db = get_db()
    cur = dict_cursor(db)
    # ?conversation_id=... lets the user resume a conversation that ended
    # mid-way instead of always starting a new one -- new transcript lines
    # get appended to the existing raw_transcript rather than overwriting it.
    resume_id = request.args.get("conversation_id", type=int)
    existing_transcript = ""
    if resume_id:
        cur.execute(
            "SELECT raw_transcript FROM conversations WHERE id = %s AND user_id = %s",
            (resume_id, user_id),
        )
        row = cur.fetchone()
        if row:
            conversation_id = resume_id
            existing_transcript = row["raw_transcript"] or ""
        else:
            resume_id = None  # not found / not owned -- fall through to creating a new one
    if not resume_id:
        # New conversations are tagged with whatever personalization mode
        # is active right now, so the Chats list can later be filtered down
        # to just Personal/Office/Study instead of always showing everything.
        category = get_user_personalization(cur, user_id)
        cur.execute(
            "INSERT INTO conversations (user_id, title, raw_transcript, category) VALUES (%s, %s, %s, %s) RETURNING id",
            (user_id, None, "", category),
        )
        conversation_id = cur.fetchone()["id"]
    db.commit()

    # Tell the frontend which conversation this session writes to, so chat
    # messages sent during this session get tied to (and permanently stored
    # under) the right conversation_id instead of going nowhere.
    ws.send(json.dumps({"type": "session_started", "conversation_id": conversation_id}))

    dg_url = (
        "wss://api.deepgram.com/v1/listen"
        "?diarize=true&punctuate=true&interim_results=false&model=nova-2"
    )
    # Optional, mobile-client-only: the browser always sends WebM/Opus
    # (Deepgram auto-detects that container), but a native recorder is
    # simplest streaming raw linear16 PCM, which Deepgram can't
    # auto-detect -- it must be told explicitly via these params. Absent
    # (the web client never sends them) -> identical to previous behavior.
    encoding = request.args.get("encoding")
    if encoding:
        dg_url += f"&encoding={encoding}"
        sample_rate = request.args.get("sample_rate", type=int)
        if sample_rate:
            dg_url += f"&sample_rate={sample_rate}"
    try:
        dg_ws = ws_client.create_connection(dg_url, header=[f"Authorization: Token {api_key}"])
    except Exception as e:
        print(f"[ws_listen] Failed to connect to Deepgram: {e!r}")
        ws.send(json.dumps({"type": "error", "message": f"Could not connect to Deepgram: {e}"}))
        return

    lock = threading.Lock()
    state = {
        "known_speakers": {},   # speaker index -> chosen name, or None if unnamed
        "transcript_lines": [], # (speaker_index, "Label: text") for every final line
        "pending_lines": [],    # lines not yet sent for background analysis
        "audio_buffer": bytearray(),  # raw session audio, for music/speech classification
        "last_audio_class": None,     # last pushed classification, to only notify on change
        "stop": False,
    }

    def push(payload):
        try:
            ws.send(json.dumps(payload))
        except Exception:
            pass

    def label_for(speaker_idx):
        name = state["known_speakers"].get(speaker_idx)
        return name if name else f"Speaker {speaker_idx}"

    def receive_from_deepgram():
        while not state["stop"]:
            try:
                message = dg_ws.recv()
            except Exception as e:
                print(f"[ws_listen] Deepgram recv stopped: {e!r}")
                break
            if not message:
                continue
            try:
                data = json.loads(message)
            except ValueError:
                continue

            if not data.get("is_final"):
                continue
            alt = ((data.get("channel") or {}).get("alternatives") or [None])[0]
            if not alt:
                continue
            text = (alt.get("transcript") or "").strip()
            if not text:
                continue
            words = alt.get("words") or []
            speaker_idx = words[0].get("speaker", 0) if words else 0

            with lock:
                is_new_speaker = speaker_idx not in state["known_speakers"]
                if is_new_speaker:
                    state["known_speakers"][speaker_idx] = None
                line = f"{label_for(speaker_idx)}: {text}"
                state["transcript_lines"].append((speaker_idx, line))
                state["pending_lines"].append(line)
                # Batching more lines per call means fewer Ollama calls overall,
                # and since the system prompt is billed on every call, fewer
                # calls = less repeated overhead for the same coverage.
                should_analyze = len(state["pending_lines"]) >= BACKGROUND_ANALYSIS_BATCH_SIZE
                delta_text = None
                if should_analyze:
                    candidate = "\n".join(state["pending_lines"])
                    # Skip calling the LLM on batches that are almost
                    # certainly just filler ("yeah", "okay", "mm-hmm") --
                    # not worth a full call.
                    if len(candidate.split()) >= MIN_WORDS_FOR_ANALYSIS:
                        delta_text = candidate
                    state["pending_lines"] = []

            push({
                "type": "transcript",
                "speaker_index": speaker_idx,
                "line": line,
            })
            if is_new_speaker:
                push({"type": "new_speaker", "speaker_index": speaker_idx})
            if delta_text:
                threading.Thread(
                    target=run_background_analysis,
                    args=(user_id, conversation_id, delta_text, push),
                    daemon=True,
                ).start()

    def audio_classifier_worker():
        """Periodically checks whether the recent audio sounds like music or
        speech, and pushes a notice to the frontend only when that changes
        -- so this doesn't spam a popup every few seconds during a normal
        conversation, only when something actually shifts."""
        while not state["stop"]:
            time.sleep(AUDIO_CLASSIFY_INTERVAL_SECONDS)
            if state["stop"]:
                break
            with lock:
                snapshot = bytes(state["audio_buffer"])
            if not snapshot:
                continue
            label = classify_audio(snapshot)
            if label and label != state.get("last_audio_class"):
                state["last_audio_class"] = label
                push({"type": "audio_classification", "label": label})

    threading.Thread(target=receive_from_deepgram, daemon=True).start()
    threading.Thread(target=audio_classifier_worker, daemon=True).start()

    try:
        while True:
            chunk = ws.receive()
            if chunk is None:
                break
            if isinstance(chunk, str):
                try:
                    msg = json.loads(chunk)
                except ValueError:
                    continue
                if msg.get("type") == "rename_speaker":
                    idx = msg.get("speaker_index")
                    name = (msg.get("name") or "").strip()
                    if name:
                        with lock:
                            state["known_speakers"][idx] = name
                        try:
                            remember_speaker_profile(dict_cursor(db), user_id, name)
                            db.commit()
                        except Exception as e:
                            print(f"[ws_listen] Could not save speaker profile: {e!r}")
                            db.rollback()
                        push({"type": "speaker_renamed", "speaker_index": idx, "name": name})
                continue
            try:
                dg_ws.send_binary(chunk)
                with lock:
                    state["audio_buffer"].extend(chunk)
                    # Cap buffer growth on long sessions -- keep roughly the
                    # last couple minutes, plenty for trailing-window
                    # classification without memory growing unbounded.
                    max_bytes = 2_000_000
                    if len(state["audio_buffer"]) > max_bytes:
                        del state["audio_buffer"][: len(state["audio_buffer"]) - max_bytes]
            except Exception:
                break
    finally:
        state["stop"] = True
        try:
            dg_ws.close()
        except Exception:
            pass

        with lock:
            # Re-label every stored line with whatever name each speaker
            # ended up with, even ones spoken before they were named.
            final_lines = [
                f"{label_for(idx)}: {line.split(': ', 1)[1]}"
                for idx, line in state["transcript_lines"]
            ]
            leftover = "\n".join(state["pending_lines"])

        if final_lines:
            new_transcript = "\n".join(final_lines)
            full_transcript = f"{existing_transcript}\n{new_transcript}" if existing_transcript else new_transcript
            conn = get_raw_connection()
            try:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE conversations SET raw_transcript = %s WHERE id = %s",
                    (full_transcript, conversation_id),
                )
                conn.commit()
                cur.close()
                cache_delete(f"user:{user_id}:conversation:{conversation_id}", f"user:{user_id}:conversations")
            finally:
                release_raw_connection(conn)

            if not resume_id:
                # Only for a genuinely new conversation (not one resumed via
                # ?conversation_id=...) -- this is the one live-listen path
                # worth a push for: by the time this fires the user has
                # already stopped/backgrounded the session, so unlike the
                # synchronous /save and /analyze endpoints (where the caller
                # gets the result directly in the response and a push would
                # just be redundant noise), this is genuinely async from
                # their perspective. Sent before titling below rather than
                # waiting on it, matching call_conversation_ready's existing
                # pattern of a generic body rather than coordinating with a
                # second async LLM call.
                push_notification(user_id, {"type": "conversation_created", "conversation_id": conversation_id})
                send_fcm_to_user(
                    user_id, title="Conversation saved",
                    body="Your conversation has been saved and transcribed.",
                    data={"type": "conversation_created", "conversation_id": conversation_id},
                )

            # The conversation just ended -- generate its title now rather
            # than leaving it null (shown as "Untitled conversation" in the
            # Conversations tab) forever.
            threading.Thread(
                target=run_conversation_titling,
                args=(user_id, conversation_id, full_transcript),
                daemon=True,
            ).start()
            if leftover.strip():
                threading.Thread(
                    target=run_background_analysis,
                    args=(user_id, conversation_id, leftover, push),
                    daemon=True,
                ).start()


@app.route("/analyze", methods=["POST"])
@login_required
def analyze_conversation():
    data = request.get_json(silent=True) or {}
    conversation_id = data.get("conversation_id")
    raw_transcript = (data.get("transcript") or "").strip()
    user_id = current_user_id()

    db = get_db()
    cur = dict_cursor(db)

    if conversation_id:
        cur.execute(
            "SELECT * FROM conversations WHERE id = %s AND user_id = %s",
            (conversation_id, user_id),
        )
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Conversation not found"}), 404
        raw_transcript = row["raw_transcript"]
    elif raw_transcript:
        cur.execute(
            "INSERT INTO conversations (user_id, title, raw_transcript) "
            "VALUES (%s, %s, %s) RETURNING id",
            (user_id, None, raw_transcript),
        )
        conversation_id = cur.fetchone()["id"]
        db.commit()
    else:
        return jsonify({"error": "transcript or conversation_id is required"}), 400

    mode = get_user_personalization(cur, user_id)
    persona_context = build_persona_context(get_user_persona(cur, user_id))
    analysis_system_prompt = build_analysis_prompt(mode, persona_context)
    try:
        from emotional_intelligence.ei_adapter import get_relevant_knowledge_cards, get_user_cognitive_context
        ei_context = get_user_cognitive_context(user_id)
        knowledge_context = get_relevant_knowledge_cards(user_id)
        if knowledge_context:
            ei_context = f"{ei_context}\n\n{knowledge_context}" if ei_context else knowledge_context
        if ei_context:
            analysis_system_prompt = f"{analysis_system_prompt}\n\n{ei_context}"
    except Exception:
        pass  # EI engine is optional and additive -- never affects this endpoint's own behavior
    try:
        content = call_llm(
            [
                {"role": "system", "content": analysis_system_prompt},
                {"role": "user", "content": raw_transcript},
            ]
        )
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else 500
        if status == 429:
            return jsonify({
                "error": "Ollama quota is currently exhausted or rate limited. Please try again later."
            }), 429
        return jsonify({"error": str(e)}), status
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500

    parsed = extract_json(content)
    if parsed is None:
        return jsonify({"error": "Could not parse analysis result", "raw": content}), 502

    tasks = parsed.get("tasks") or []
    speakers = parsed.get("speakers") or []
    mood = parsed.get("mood") or {}
    topics = [t for t in (parsed.get("topics") or []) if isinstance(t, str) and t.strip()][:5]
    questions = [q for q in (parsed.get("questions") or []) if isinstance(q, str) and q.strip()][:5]
    topics, questions = _apply_tags_questions_setting(user_id, topics, questions)

    created_tasks = []
    for task in tasks:
        description = (task.get("description") or "").strip()
        if not description:
            continue
        cur.execute(
            "INSERT INTO tasks (user_id, conversation_id, description, owner, due_date, reminder_at, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'open') RETURNING id",
            (user_id, conversation_id, description, task.get("owner"), task.get("due_date"),
             normalize_reminder_at(task.get("reminder_at"))),
        )
        created_tasks.append((cur.fetchone()["id"], description))

    cur.execute("SELECT category FROM conversations WHERE id = %s", (conversation_id,))
    category_row = cur.fetchone()
    category = _first_value(category_row) or "personal"

    for speaker in speakers:
        label = (speaker.get("label") or "").strip()
        if not label:
            continue
        profile_id = get_or_create_profile_id(cur, user_id, label)
        record_profile_category(cur, profile_id, category)
        record_profile_conversation(cur, profile_id, conversation_id)
        for obs in speaker.get("observations") or []:
            obs = (obs or "").strip()
            if not obs:
                continue
            cur.execute(
                "INSERT INTO personality_notes (user_id, conversation_id, speaker_label, profile_id, observation) "
                "VALUES (%s, %s, %s, %s, %s)",
                (user_id, conversation_id, label, profile_id, obs),
            )

    mood_label = (mood.get("label") or "").strip()
    notify_mood_friend_ids = []
    if mood_label:
        bucket_start, bucket_end = mood_bucket_bounds()
        cur.execute(
            "SELECT 1 FROM mood_logs WHERE user_id = %s AND created_at >= %s AND created_at < %s LIMIT 1",
            (user_id, bucket_start, bucket_end),
        )
        is_first_in_bucket = cur.fetchone() is None
        cur.execute(
            "INSERT INTO mood_logs (user_id, conversation_id, mood_label, mood_score) "
            "VALUES (%s, %s, %s, %s)",
            (user_id, conversation_id, mood_label, mood.get("score")),
        )
        if is_first_in_bucket:
            cur.execute("SELECT friend_id FROM friendships WHERE user_id = %s", (user_id,))
            notify_mood_friend_ids = [r["friend_id"] for r in cur.fetchall()]

    db.commit()

    for task_id, description in created_tasks:
        push_notification(user_id, {
            "type": "task_created", "task_id": task_id, "description": description,
            "conversation_id": conversation_id,
        })
        send_fcm_to_user(
            user_id, title="New task", body=description,
            data={"type": "task_created", "task_id": task_id, "conversation_id": conversation_id},
        )

    notify_friends_of_mood_update(user_id, mood_label, notify_mood_friend_ids)

    return jsonify(
        {
            "conversation_id": conversation_id,
            "tasks": tasks,
            "speakers": speakers,
            "mood": mood,
            "topics": topics,
            "questions": questions,
        }
    )


@app.route("/tasks", methods=["GET"])
@login_required
def list_tasks():
    status = request.args.get("status", "open")
    user_id = current_user_id()
    db = get_db()
    cur = dict_cursor(db)
    # category comes from the source conversation, not the task row itself --
    # tasks are always extracted from a conversation, so this mirrors the
    # same personal/office/study tagging the Chats list filters on.
    if status == "all":
        cur.execute(
            "SELECT t.*, COALESCE(c.category, 'personal') AS category "
            "FROM tasks t LEFT JOIN conversations c ON c.id = t.conversation_id "
            "WHERE t.user_id = %s ORDER BY t.created_at DESC",
            (user_id,),
        )
    else:
        cur.execute(
            "SELECT t.*, COALESCE(c.category, 'personal') AS category "
            "FROM tasks t LEFT JOIN conversations c ON c.id = t.conversation_id "
            "WHERE t.user_id = %s AND t.status = %s ORDER BY t.created_at DESC",
            (user_id, status),
        )
    rows = cur.fetchall()
    return jsonify([serialize_row(r) for r in rows])


@app.route("/tasks/<int:task_id>/complete", methods=["POST"])
@login_required
def complete_task(task_id):
    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "SELECT id FROM tasks WHERE id = %s AND user_id = %s", (task_id, current_user_id())
    )
    if not cur.fetchone():
        return jsonify({"error": "Task not found"}), 404
    cur.execute("UPDATE tasks SET status = 'done' WHERE id = %s", (task_id,))
    db.commit()
    return jsonify({"ok": True})


@app.route("/tasks/<int:task_id>/reopen", methods=["POST"])
@login_required
def reopen_task(task_id):
    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "SELECT id FROM tasks WHERE id = %s AND user_id = %s", (task_id, current_user_id())
    )
    if not cur.fetchone():
        return jsonify({"error": "Task not found"}), 404
    cur.execute("UPDATE tasks SET status = 'open' WHERE id = %s", (task_id,))
    db.commit()
    return jsonify({"ok": True})


@app.route("/tasks/<int:task_id>", methods=["DELETE"])
@login_required
def delete_task(task_id):
    """Permanently removes a task -- unlike completing/reopening, this is
    not reversible from the UI. Matches delete_conversation's shape (DELETE
    verb, id + ownership check, RETURNING id to detect a no-op)."""
    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "DELETE FROM tasks WHERE id = %s AND user_id = %s RETURNING id",
        (task_id, current_user_id()),
    )
    deleted = cur.fetchone()
    db.commit()
    if not deleted:
        return jsonify({"error": "Task not found"}), 404
    return jsonify({"ok": True})


@app.route("/tasks/<int:task_id>/edit", methods=["POST"])
@login_required
def edit_task(task_id):
    """Corrects a task's description/due_date after extraction -- the LLM
    sometimes mishears/misparses. Update in place (not delete+recreate) so
    reminder_at/email_sent/status history isn't lost."""
    data = request.get_json(silent=True) or {}
    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "SELECT id FROM tasks WHERE id = %s AND user_id = %s", (task_id, current_user_id())
    )
    if not cur.fetchone():
        return jsonify({"error": "Task not found"}), 404

    fields, params = [], []
    if "description" in data:
        description = (data.get("description") or "").strip()
        if not description:
            return jsonify({"error": "description cannot be empty"}), 400
        fields.append("description = %s")
        params.append(description)
    if "due_date" in data:
        fields.append("due_date = %s")
        params.append((data.get("due_date") or "").strip() or None)
    if not fields:
        return jsonify({"error": "Nothing to update -- provide description and/or due_date"}), 400

    fields.append("updated_at = now()")
    params.append(task_id)
    cur.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id = %s", params)
    db.commit()
    return jsonify({"ok": True})


@app.route("/tasks/<int:task_id>/mark_reminded", methods=["POST"])
@login_required
def mark_reminded(task_id):
    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "SELECT id FROM tasks WHERE id = %s AND user_id = %s", (task_id, current_user_id())
    )
    if not cur.fetchone():
        return jsonify({"error": "Task not found"}), 404
    cur.execute("UPDATE tasks SET reminder_sent = TRUE WHERE id = %s", (task_id,))
    db.commit()
    return jsonify({"ok": True})


@app.route("/profiles", methods=["GET"])
@login_required
def list_profiles():
    """Groups observations by canonical profile (not raw speaker_label text
    -- 'Kunal' and 'kunal' notes land under the same person), and reports
    every category each profile has ever been seen under via the stored
    profile_categories mapping rather than re-deriving it from conversations
    on every request."""
    db = get_db()
    cur = dict_cursor(db)
    user_id = current_user_id()

    cur.execute(
        "SELECT n.profile_id, COALESCE(sp.name, n.speaker_label) AS display_name, "
        "n.observation, n.created_at, n.conversation_id, "
        "COALESCE(c.category, 'personal') AS category "
        "FROM personality_notes n "
        "LEFT JOIN conversations c ON c.id = n.conversation_id "
        "LEFT JOIN speaker_profiles sp ON sp.id = n.profile_id "
        "WHERE n.user_id = %s ORDER BY display_name, n.created_at DESC",
        (user_id,),
    )
    rows = cur.fetchall()

    cur.execute(
        "SELECT pc.profile_id, pc.category, pc.last_seen_at "
        "FROM profile_categories pc "
        "JOIN speaker_profiles sp ON sp.id = pc.profile_id "
        "WHERE sp.user_id = %s",
        (user_id,),
    )
    categories_by_profile = {}
    for r in cur.fetchall():
        categories_by_profile.setdefault(r["profile_id"], []).append(
            (r["category"], r["last_seen_at"])
        )

    profiles = {}
    for r in rows:
        label = r["display_name"]
        entry = profiles.setdefault(label, {"profile_id": r["profile_id"], "notes": []})
        entry["notes"].append(
            {
                "observation": r["observation"],
                "created_at": r["created_at"].isoformat() if isinstance(r["created_at"], datetime) else r["created_at"],
                "conversation_id": r["conversation_id"],
                "category": r["category"],
            }
        )

    for label, entry in profiles.items():
        pid = entry["profile_id"]
        cats = categories_by_profile.get(pid)
        if cats:
            cats_sorted = sorted(cats, key=lambda c: c[1], reverse=True)
            entry["categories"] = [c[0] for c in cats_sorted]
            entry["last_seen"] = cats_sorted[0][1].isoformat()
        else:
            # Legacy rows from before profile_id/profile_categories existed
            # (or a profile whose notes predate the migration) -- fall back
            # to deriving straight from this profile's own notes.
            seen = []
            for n in entry["notes"]:
                if n["category"] not in seen:
                    seen.append(n["category"])
            entry["categories"] = seen
            entry["last_seen"] = entry["notes"][0]["created_at"] if entry["notes"] else None

    return jsonify(profiles)


@app.route("/profiles/<int:profile_id>", methods=["DELETE"])
@login_required
def delete_profile(profile_id):
    """Deletes a person's profile entirely -- their identity row plus every
    observation and category association tied to it (both FKs are ON DELETE
    CASCADE), not just a rename/unlink. Deleting only the identity and
    leaving notes behind would let the free-text speaker_label resurface
    the same person right back into the list via list_profiles()'s legacy
    fallback grouping."""
    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "DELETE FROM speaker_profiles WHERE id = %s AND user_id = %s RETURNING id",
        (profile_id, current_user_id()),
    )
    deleted = cur.fetchone()
    db.commit()
    if not deleted:
        return jsonify({"error": "Profile not found"}), 404
    return jsonify({"ok": True})


@app.route("/profiles/<int:profile_id>/rename", methods=["POST"])
@login_required
def rename_profile(profile_id):
    """Fixes a misheard/misspelled name. Rejects (409) instead of silently
    merging if the new name collides with a different existing profile --
    a rename shouldn't surprise-merge two people's histories together."""
    data = request.get_json(silent=True) or {}
    new_name = (data.get("name") or "").strip()
    if not new_name:
        return jsonify({"error": "name is required"}), 400

    user_id = current_user_id()
    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "SELECT id FROM speaker_profiles WHERE id = %s AND user_id = %s", (profile_id, user_id)
    )
    if not cur.fetchone():
        return jsonify({"error": "Profile not found"}), 404

    cur.execute(
        "SELECT id FROM speaker_profiles WHERE user_id = %s AND lower(name) = lower(%s) AND id != %s",
        (user_id, new_name, profile_id),
    )
    if cur.fetchone():
        return jsonify({"error": "A profile with that name already exists"}), 409

    cur.execute("UPDATE speaker_profiles SET name = %s WHERE id = %s", (new_name, profile_id))
    db.commit()
    return jsonify({"ok": True, "name": new_name})


@app.route("/speakers", methods=["GET"])
@login_required
def list_speakers():
    """Every person this user has ever named a speaker as, across all their
    conversations -- the pick-list the "new speaker detected" prompt (and
    mid-session remapping) draws from, so diarization mistakenly splitting
    one real person into a second session-local index doesn't require
    retyping their name from scratch."""
    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "SELECT id, name FROM speaker_profiles WHERE user_id = %s ORDER BY name",
        (current_user_id(),),
    )
    return jsonify([dict(r) for r in cur.fetchall()])


# ---------------------------------------------------------------------------
# Friends
# ---------------------------------------------------------------------------

@app.route("/friends", methods=["GET"])
@login_required
def list_friends():
    user_id = current_user_id()
    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "SELECT users.id, users.username, friendships.nickname FROM friendships "
        "JOIN users ON users.id = friendships.friend_id "
        "WHERE friendships.user_id = %s ORDER BY COALESCE(friendships.nickname, users.username)",
        (user_id,),
    )
    friends = [dict(r) for r in cur.fetchall()]
    friend_ids = [f["id"] for f in friends]

    # Last-call summary per friend, for the WhatsApp/Telegram-style call-log
    # subtitle in the friends list -- a group call counts toward EVERY
    # friend who was also a participant (the cp_friend join naturally
    # produces one row per other participant, not just one overall).
    last_calls = {}
    call_counts = {}
    if friend_ids:
        cur.execute(
            "SELECT DISTINCT ON (cp_friend.user_id) "
            "cp_friend.user_id AS friend_id, calls.created_at AS last_call_at, "
            "(calls.initiator_user_id = %(me)s) AS outgoing "
            "FROM calls "
            "JOIN call_participants cp_me ON cp_me.call_id = calls.id AND cp_me.user_id = %(me)s "
            "JOIN call_participants cp_friend ON cp_friend.call_id = calls.id AND cp_friend.user_id != %(me)s "
            "WHERE cp_friend.user_id = ANY(%(friend_ids)s) "
            "ORDER BY cp_friend.user_id, calls.created_at DESC",
            {"me": user_id, "friend_ids": friend_ids},
        )
        last_calls = {r["friend_id"]: r for r in cur.fetchall()}

        cur.execute(
            "SELECT cp_friend.user_id AS friend_id, COUNT(*) AS call_count "
            "FROM calls "
            "JOIN call_participants cp_me ON cp_me.call_id = calls.id AND cp_me.user_id = %(me)s "
            "JOIN call_participants cp_friend ON cp_friend.call_id = calls.id AND cp_friend.user_id != %(me)s "
            "WHERE cp_friend.user_id = ANY(%(friend_ids)s) "
            "GROUP BY cp_friend.user_id",
            {"me": user_id, "friend_ids": friend_ids},
        )
        call_counts = {r["friend_id"]: r["call_count"] for r in cur.fetchall()}

    for f in friends:
        last = last_calls.get(f["id"])
        f["last_call_at"] = last["last_call_at"].isoformat() if last else None
        f["last_call_outgoing"] = last["outgoing"] if last else None
        f["call_count"] = call_counts.get(f["id"], 0)

    return jsonify(friends)


@app.route("/friends/<int:friend_id>/calls", methods=["GET"])
@login_required
def friend_call_history(friend_id):
    """Full call log with one friend, newest first -- what the (i)/detail
    view drills into from the summary shown in GET /friends."""
    user_id = current_user_id()
    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "SELECT 1 FROM friendships WHERE user_id = %s AND friend_id = %s",
        (user_id, friend_id),
    )
    if not cur.fetchone():
        return jsonify({"error": "Not friends with that user"}), 403

    cur.execute(
        "SELECT calls.id AS call_id, calls.created_at, calls.ended_at, calls.status, "
        "(calls.initiator_user_id = %(me)s) AS outgoing, "
        "cp_me.status AS my_status "
        "FROM calls "
        "JOIN call_participants cp_me ON cp_me.call_id = calls.id AND cp_me.user_id = %(me)s "
        "JOIN call_participants cp_friend ON cp_friend.call_id = calls.id AND cp_friend.user_id = %(friend_id)s "
        "ORDER BY calls.created_at DESC",
        {"me": user_id, "friend_id": friend_id},
    )
    return jsonify([serialize_row(r) for r in cur.fetchall()])


@app.route("/friends/add", methods=["POST"])
@login_required
def add_friend():
    data = request.get_json(silent=True) or {}
    friend_code = (data.get("friend_code") or "").strip()
    if not friend_code:
        return jsonify({"error": "friend_code is required"}), 400

    user_id = current_user_id()
    db = get_db()
    cur = dict_cursor(db)
    cur.execute("SELECT id, username FROM users WHERE friend_code = %s", (friend_code,))
    target = cur.fetchone()
    if not target:
        return jsonify({"error": "No user found with that friend code"}), 404
    if target["id"] == user_id:
        return jsonify({"error": "You can't add yourself as a friend"}), 400

    cur.execute(
        "SELECT 1 FROM friendships WHERE user_id = %s AND friend_id = %s",
        (user_id, target["id"]),
    )
    if cur.fetchone():
        return jsonify({"error": "Already friends"}), 409

    # Both directions, in one transaction -- either both rows land or neither does.
    cur.execute(
        "INSERT INTO friendships (user_id, friend_id) VALUES (%s, %s), (%s, %s)",
        (user_id, target["id"], target["id"], user_id),
    )
    db.commit()
    return jsonify({"ok": True, "friend": {"id": target["id"], "username": target["username"]}})


@app.route("/friends/<int:friend_id>", methods=["DELETE"])
@login_required
def remove_friend(friend_id):
    """Unfriend -- removes both directions of the relationship, same
    transaction, mirroring how add_friend() creates both at once."""
    user_id = current_user_id()
    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "DELETE FROM friendships WHERE (user_id = %s AND friend_id = %s) "
        "OR (user_id = %s AND friend_id = %s) RETURNING id",
        (user_id, friend_id, friend_id, user_id),
    )
    deleted = cur.fetchall()
    db.commit()
    if not deleted:
        return jsonify({"error": "Not friends with that user"}), 404
    return jsonify({"ok": True})


@app.route("/friends/<int:friend_id>/nickname", methods=["POST"])
@login_required
def set_friend_nickname(friend_id):
    """Sets *my* local name for this friend -- can't rename their actual
    account, so this lives on my direction of the friendship row only (each
    direction is already its own row, see add_friend)."""
    data = request.get_json(silent=True) or {}
    nickname = (data.get("nickname") or "").strip() or None  # empty clears it
    user_id = current_user_id()
    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "UPDATE friendships SET nickname = %s WHERE user_id = %s AND friend_id = %s RETURNING id",
        (nickname, user_id, friend_id),
    )
    updated = cur.fetchone()
    db.commit()
    if not updated:
        return jsonify({"error": "Not friends with that user"}), 404
    return jsonify({"ok": True, "nickname": nickname})


_COGNITIVE_SHARING_LEVELS = ("off", "limited", "collaborative")
_COGNITIVE_SHARING_RANK = {level: i for i, level in enumerate(_COGNITIVE_SHARING_LEVELS)}


@app.route("/friends/<int:friend_id>/cognitive-sharing", methods=["GET"])
@login_required
def get_cognitive_sharing(friend_id):
    """My own sharing level for this friend, plus whether BOTH directions
    are currently >= 'limited' (the bilateral gate the on-demand suggestion
    feature is gated on -- see emotional_intelligence/
    COGNITIVE_SHARING_INTERVENTION_PLAN.md). Deliberately never returns the
    other side's actual level -- that itself would leak their privacy
    posture; the UI only ever learns whether the feature is available."""
    user_id = current_user_id()
    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "SELECT 1 FROM friendships WHERE user_id = %s AND friend_id = %s",
        (user_id, friend_id),
    )
    if not cur.fetchone():
        return jsonify({"error": "Not friends with that user"}), 403

    cur.execute(
        "SELECT user_id, level FROM cognitive_sharing_settings "
        "WHERE (user_id = %s AND friend_id = %s) OR (user_id = %s AND friend_id = %s)",
        (user_id, friend_id, friend_id, user_id),
    )
    levels_by_user = {row["user_id"]: row["level"] for row in cur.fetchall()}
    my_level = levels_by_user.get(user_id, "off")
    their_level = levels_by_user.get(friend_id, "off")
    both_enabled = (
        _COGNITIVE_SHARING_RANK[my_level] >= _COGNITIVE_SHARING_RANK["limited"]
        and _COGNITIVE_SHARING_RANK[their_level] >= _COGNITIVE_SHARING_RANK["limited"]
    )
    return jsonify({"my_level": my_level, "both_enabled": both_enabled})


@app.route("/friends/<int:friend_id>/cognitive-sharing", methods=["POST"])
@login_required
def set_cognitive_sharing(friend_id):
    data = request.get_json(silent=True) or {}
    level = (data.get("level") or "").strip().lower()
    if level not in _COGNITIVE_SHARING_LEVELS:
        return jsonify({"error": f"level must be one of {_COGNITIVE_SHARING_LEVELS}"}), 400

    user_id = current_user_id()
    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "SELECT 1 FROM friendships WHERE user_id = %s AND friend_id = %s",
        (user_id, friend_id),
    )
    if not cur.fetchone():
        return jsonify({"error": "Not friends with that user"}), 403

    cur.execute(
        "INSERT INTO cognitive_sharing_settings (user_id, friend_id, level) "
        "VALUES (%s, %s, %s) "
        "ON CONFLICT (user_id, friend_id) DO UPDATE SET level = EXCLUDED.level, updated_at = now()",
        (user_id, friend_id, level),
    )
    db.commit()
    return jsonify({"ok": True, "my_level": level})


COGNITIVE_SUGGESTION_DM_LIMIT = 30


def _cognitive_sharing_levels(cur, user_id, friend_id):
    """Both directions' levels for a pair -- shared by the on-demand
    suggestion endpoint below and get_cognitive_sharing above (kept as a
    separate small helper rather than refactoring get_cognitive_sharing
    itself, so that already-shipped/verified endpoint's behavior can't
    regress from a change made for this new one)."""
    cur.execute(
        "SELECT user_id, level FROM cognitive_sharing_settings "
        "WHERE (user_id = %s AND friend_id = %s) OR (user_id = %s AND friend_id = %s)",
        (user_id, friend_id, friend_id, user_id),
    )
    levels_by_user = {row["user_id"]: row["level"] for row in cur.fetchall()}
    return levels_by_user.get(user_id, "off"), levels_by_user.get(friend_id, "off")


def _serialize_cognitive_suggestion(row, viewer_id):
    """Never includes user_a/user_b's raw memory -- only the already-
    synthesized suggestion_text -- and reports dismissed/shown from the
    viewer's own side only, mirroring get_cognitive_sharing's own
    never-leak-the-other-side's-state discipline."""
    is_a = row["user_a"] == viewer_id
    return {
        "id": row["id"],
        "suggestion_text": row["suggestion_text"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "dismissed": row["dismissed_by_a"] if is_a else row["dismissed_by_b"],
    }


@app.route("/friends/<int:friend_id>/cognitive-suggestion", methods=["POST"])
@login_required
def request_cognitive_suggestion(friend_id):
    """On-demand 'find common ground' trigger -- v1 per
    COGNITIVE_SHARING_INTERVENTION_PLAN.md is deliberately on-demand only,
    not automatic background triggering, to avoid unwanted LLM spend/spam
    until there's real usage data. Bilateral gate is checked here, before
    any subject resolution or LLM call touches either person's data -- not
    an afterthought filter on the output."""
    user_id = current_user_id()
    db = get_db()
    cur = dict_cursor(db)
    cur.execute("SELECT 1 FROM friendships WHERE user_id = %s AND friend_id = %s", (user_id, friend_id))
    if not cur.fetchone():
        return jsonify({"error": "Not friends with that user"}), 403

    my_level, their_level = _cognitive_sharing_levels(cur, user_id, friend_id)
    if _COGNITIVE_SHARING_RANK[my_level] < _COGNITIVE_SHARING_RANK["limited"]:
        return jsonify({"error": "Turn on Cognitive Sharing for this friend first."}), 403
    if _COGNITIVE_SHARING_RANK[their_level] < _COGNITIVE_SHARING_RANK["limited"]:
        return jsonify({"error": "Ask them to turn on Cognitive Sharing too -- both sides need it on."}), 403

    cur.execute(
        "SELECT id, sender_id, content FROM direct_messages "
        "WHERE (sender_id = %(me)s AND recipient_id = %(friend)s) "
        "OR (sender_id = %(friend)s AND recipient_id = %(me)s) "
        "ORDER BY id DESC LIMIT %(limit)s",
        {"me": user_id, "friend": friend_id, "limit": COGNITIVE_SUGGESTION_DM_LIMIT},
    )
    dm_rows = list(reversed(cur.fetchall()))
    last_message_id = dm_rows[-1]["id"] if dm_rows else None
    recent_dm_context = "\n".join(
        f"- {'Them' if r['sender_id'] == friend_id else 'Me'}: {r['content']}" for r in dm_rows
    )

    try:
        from emotional_intelligence.cognitive_sharing import generate_common_ground_suggestion
        suggestion_text = generate_common_ground_suggestion(cur, user_id, friend_id, recent_dm_context)
    except Exception as exc:
        print(f"[cognitive_sharing] request_cognitive_suggestion failed: {exc!r}")
        suggestion_text = None

    if not suggestion_text:
        return jsonify({"suggestion": None, "message": "Nothing to suggest right now."})

    user_a, user_b = (user_id, friend_id) if user_id < friend_id else (friend_id, user_id)
    shown_column = "shown_to_a_at" if user_id == user_a else "shown_to_b_at"
    cur.execute(
        f"INSERT INTO cognitive_suggestions (user_a, user_b, suggestion_text, source_message_id, {shown_column}) "
        f"VALUES (%s, %s, %s, %s, now()) "
        f"RETURNING id, user_a, user_b, suggestion_text, created_at, dismissed_by_a, dismissed_by_b",
        (user_a, user_b, suggestion_text, last_message_id),
    )
    row = cur.fetchone()
    db.commit()

    cur.execute("SELECT username FROM users WHERE id = %s", (user_id,))
    requester_name = cur.fetchone()["username"]
    push_notification(friend_id, {
        "type": "cognitive_suggestion", "suggestion_id": row["id"], "friend_id": user_id,
    })
    send_fcm_to_user(
        friend_id, title="Cognitive Sharing", body=f"A suggestion for you and {requester_name}",
        data={"type": "cognitive_suggestion", "suggestion_id": str(row["id"]), "friend_id": str(user_id)},
    )

    return jsonify({"suggestion": _serialize_cognitive_suggestion(row, user_id)})


@app.route("/friends/<int:friend_id>/cognitive-suggestion", methods=["GET"])
@login_required
def get_latest_cognitive_suggestion(friend_id):
    """Latest suggestion for this pair not yet dismissed by me -- used for
    initial screen load and by whichever side didn't request it, after
    they've been notified via push_notification/FCM (the notification
    payload only carries an id; this is what actually loads the text).
    Marks shown_to_<me>_at on first fetch."""
    user_id = current_user_id()
    db = get_db()
    cur = dict_cursor(db)
    cur.execute("SELECT 1 FROM friendships WHERE user_id = %s AND friend_id = %s", (user_id, friend_id))
    if not cur.fetchone():
        return jsonify({"error": "Not friends with that user"}), 403

    user_a, user_b = (user_id, friend_id) if user_id < friend_id else (friend_id, user_id)
    dismissed_column = "dismissed_by_a" if user_id == user_a else "dismissed_by_b"
    shown_column = "shown_to_a_at" if user_id == user_a else "shown_to_b_at"
    cur.execute(
        f"SELECT id, user_a, user_b, suggestion_text, created_at, dismissed_by_a, dismissed_by_b "
        f"FROM cognitive_suggestions WHERE user_a = %s AND user_b = %s AND {dismissed_column} = false "
        f"ORDER BY id DESC LIMIT 1",
        (user_a, user_b),
    )
    row = cur.fetchone()
    if not row:
        return jsonify({"suggestion": None})

    cur.execute(f"UPDATE cognitive_suggestions SET {shown_column} = COALESCE({shown_column}, now()) WHERE id = %s", (row["id"],))
    db.commit()
    return jsonify({"suggestion": _serialize_cognitive_suggestion(row, user_id)})


@app.route("/friends/<int:friend_id>/cognitive-suggestion/<int:suggestion_id>/dismiss", methods=["POST"])
@login_required
def dismiss_cognitive_suggestion(friend_id, suggestion_id):
    """Each side dismisses independently -- dismissing on my device must
    never affect what the other participant still sees (guardrail #3 in
    COGNITIVE_SHARING_INTERVENTION_PLAN.md)."""
    user_id = current_user_id()
    db = get_db()
    cur = dict_cursor(db)
    user_a, user_b = (user_id, friend_id) if user_id < friend_id else (friend_id, user_id)
    dismissed_column = "dismissed_by_a" if user_id == user_a else "dismissed_by_b"
    cur.execute(
        f"UPDATE cognitive_suggestions SET {dismissed_column} = true "
        f"WHERE id = %s AND user_a = %s AND user_b = %s RETURNING id",
        (suggestion_id, user_a, user_b),
    )
    updated = cur.fetchone()
    db.commit()
    if not updated:
        return jsonify({"error": "Suggestion not found"}), 404
    return jsonify({"ok": True})


@app.route("/friends/<int:friend_id>/mood", methods=["GET"])
@login_required
def friend_mood(friend_id):
    user_id = current_user_id()
    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "SELECT 1 FROM friendships WHERE user_id = %s AND friend_id = %s",
        (user_id, friend_id),
    )
    if not cur.fetchone():
        return jsonify({"error": "Not friends with that user"}), 403

    # A single compiled emoji for the current 2-hour window, not a raw
    # timeline -- see compute_compiled_mood/mood_bucket_bounds. Falls back
    # to the most recent earlier window today if nothing's logged yet in
    # the current one.
    bucket_start, bucket_end = mood_bucket_bounds()
    compiled = compute_compiled_mood(cur, friend_id, bucket_start, bucket_end)

    if not compiled:
        since_local_midnight = datetime.now().astimezone().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        cur.execute(
            "SELECT created_at FROM mood_logs WHERE user_id = %s AND created_at >= %s "
            "ORDER BY created_at DESC LIMIT 1",
            (friend_id, since_local_midnight),
        )
        last = cur.fetchone()
        if last:
            bucket_start, bucket_end = mood_bucket_bounds(last["created_at"])
            compiled = compute_compiled_mood(cur, friend_id, bucket_start, bucket_end)

    response = {
        "friend_id": friend_id,
        "window_start": bucket_start.isoformat(),
        "window_end": bucket_end.isoformat(),
        "mood_label": compiled["mood_label"] if compiled else None,
        "emoji": compiled["emoji"] if compiled else None,
    }
    try:
        from emotional_intelligence.ei_adapter import get_friend_relationship_insight
        insight = get_friend_relationship_insight(user_id, friend_id)
        if insight:
            response["ei_relationship_insight"] = insight
    except Exception:
        pass  # EI engine is optional and additive -- never affects this endpoint's own behavior

    return jsonify(response)


@app.route("/insights/digest", methods=["GET"])
@login_required
def latest_digest():
    """Returns the current user's most recent weekly digest (a JSON array
    of insight cards -- see emotional_intelligence/weekly_digest.py and
    nudges/nudge_engine.py's _check_weekly_digest, which writes these).
    Marks it viewed on first fetch. Empty response (not an error) if none
    exists yet -- a brand-new account, or one with no EI data yet, simply
    hasn't had a digest generated."""
    user_id = current_user_id()
    digest = None
    try:
        from emotional_intelligence.ei_adapter import get_and_mark_weekly_digest_viewed
        digest = get_and_mark_weekly_digest_viewed(user_id)
    except Exception:
        pass  # EI engine is optional and additive -- never affects this endpoint's own behavior
    return jsonify({"digest": digest})


@app.route("/friends/<int:friend_id>/messages", methods=["GET"])
@login_required
def list_direct_messages(friend_id):
    """Message history with one friend, oldest-first (matching every other
    message list in this app), paginated via ?before_id= for loading older
    messages on scroll-up -- the client's LocalCache keeps the most recent
    page on-device, so this is only hit for a thread's first load or when
    scrolling past what's cached."""
    user_id = current_user_id()
    db = get_db()
    cur = dict_cursor(db)
    cur.execute("SELECT 1 FROM friendships WHERE user_id = %s AND friend_id = %s", (user_id, friend_id))
    if not cur.fetchone():
        return jsonify({"error": "Not friends with that user"}), 403

    limit = min(request.args.get("limit", type=int) or 50, 100)
    before_id = request.args.get("before_id", type=int)
    params = {"me": user_id, "friend": friend_id, "limit": limit}
    extra = ""
    if before_id:
        extra = "AND direct_messages.id < %(before_id)s"
        params["before_id"] = before_id
    cur.execute(
        f"SELECT id, sender_id, recipient_id, content, created_at, delivered_at, read_at FROM direct_messages "
        f"WHERE ((sender_id = %(me)s AND recipient_id = %(friend)s) "
        f"OR (sender_id = %(friend)s AND recipient_id = %(me)s)) {extra} "
        f"ORDER BY id DESC LIMIT %(limit)s",
        params,
    )
    messages = [serialize_row(r) for r in cur.fetchall()]
    messages.reverse()
    return jsonify(messages)


@app.route("/friends/<int:friend_id>/messages", methods=["POST"])
@login_required
def send_direct_message(friend_id):
    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"error": "content is required"}), 400

    user_id = current_user_id()
    db = get_db()
    cur = dict_cursor(db)
    cur.execute("SELECT 1 FROM friendships WHERE user_id = %s AND friend_id = %s", (user_id, friend_id))
    if not cur.fetchone():
        return jsonify({"error": "Not friends with that user"}), 403

    cur.execute(
        "INSERT INTO direct_messages (sender_id, recipient_id, content) VALUES (%s, %s, %s) "
        "RETURNING id, sender_id, recipient_id, content, created_at, delivered_at, read_at",
        (user_id, friend_id, content),
    )
    message = serialize_row(cur.fetchone())
    db.commit()

    cur.execute("SELECT username FROM users WHERE id = %s", (user_id,))
    sender_name = cur.fetchone()["username"]

    # Same real-time transport every other push-worthy event in this app
    # already uses (task_created, call_conversation_ready, ...) -- no new
    # WS route or delivery mechanism needed. sender_username rides along so
    # the client can show a real name in a foreground local notification
    # without a separate lookup (mirrors the FCM title below).
    push_notification(friend_id, {"type": "direct_message", "message": message, "sender_username": sender_name})
    send_fcm_to_user(
        friend_id, title=sender_name, body=content,
        data={"type": "direct_message", "sender_id": str(user_id), "message_id": str(message["id"])},
    )

    try:
        # Feeds the same cognitive-intelligence pipeline /chat and
        # /chat/global already trigger after every message -- a correction
        # or new fact stated to a friend ("actually I start the new job
        # Monday, not next week") is exactly the kind of thing that pipeline
        # already looks for, regardless of which surface it was said on.
        from emotional_intelligence.ei_adapter import trigger_chat_feedback_extraction
        threading.Thread(target=trigger_chat_feedback_extraction, args=(user_id, content), daemon=True).start()
    except Exception:
        pass  # EI engine is optional and additive -- never affects this endpoint's own behavior

    return jsonify(message)


@app.route("/friends/unread_message_counts", methods=["GET"])
@login_required
def unread_message_counts():
    """Per-friend unread count for the Friends-list badge -- same shape as
    the task/chat badge counts NotifyProvider already tracks."""
    user_id = current_user_id()
    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "SELECT sender_id AS friend_id, count(*) AS unread FROM direct_messages "
        "WHERE recipient_id = %s AND read_at IS NULL GROUP BY sender_id",
        (user_id,),
    )
    return jsonify({str(r["friend_id"]): r["unread"] for r in cur.fetchall()})


@app.route("/mood/history", methods=["GET"])
@login_required
def mood_history():
    """Per-day dominant mood for the caller's own last `days` days (default
    30, capped at 90) -- feeds the mood-trend calendar heatmap. Dominant
    mood per day picked the same way compute_compiled_mood picks it per
    2-hour bucket: highest confidence-weighted score, falling back to count
    on a tie. Grouped by LOCAL calendar date (matching mood_bucket_bounds'
    existing local-time convention elsewhere in this file), not UTC date."""
    user_id = current_user_id()
    days = max(1, min(request.args.get("days", 30, type=int) or 30, 90))
    since = datetime.now().astimezone() - timedelta(days=days)
    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "SELECT created_at, mood_label, mood_score FROM mood_logs "
        "WHERE user_id = %s AND created_at >= %s ORDER BY created_at",
        (user_id, since),
    )
    by_day = {}
    for row in cur.fetchall():
        day = row["created_at"].astimezone().date().isoformat()
        bucket = by_day.setdefault(day, {})
        label = row["mood_label"]
        agg = bucket.get(label, {"weight": 0.0, "n": 0})
        agg["weight"] += float(row["mood_score"] or 0)
        agg["n"] += 1
        bucket[label] = agg

    days_out = []
    for day, labels in sorted(by_day.items()):
        dominant = max(labels.items(), key=lambda kv: (kv[1]["weight"], kv[1]["n"]))[0]
        days_out.append({
            "date": day, "mood_label": dominant,
            "emoji": MOOD_EMOJI.get((dominant or "").lower(), DEFAULT_MOOD_EMOJI),
        })

    # Consecutive days up to and including today with at least one mood log
    # -- a simple, honest "N-day streak" count, not stored separately since
    # it's fully derivable from the same data every time.
    today = datetime.now().astimezone().date()
    logged_days = {d["date"] for d in days_out}
    streak = 0
    cursor_day = today
    while cursor_day.isoformat() in logged_days:
        streak += 1
        cursor_day -= timedelta(days=1)

    return jsonify({"days": days_out, "streak": streak})


# ---------------------------------------------------------------------------
# Voice calling (1:1 + group), via LiveKit Cloud
#
# Calling is friends-only (same trust boundary as mood-sharing above). One
# call_participants row shape covers both 1:1 (2 rows) and group (N rows) --
# no special-casing. LiveKit's own SFU/room hosting means the app never
# touches raw media -- this server only ever signs short-lived join tokens
# and tracks call/participant state; the actual audio never passes through
# Flask. Recording deliberately does NOT use LiveKit's server-side Egress
# (that requires S3/GCS/Azure storage + IAM credentials on top of LiveKit
# Cloud itself) -- instead the call initiator's browser mixes local + remote
# audio client-side and uploads the result to /calls/<id>/recording, which
# reuses the exact same Deepgram diarization call as /transcribe.
# ---------------------------------------------------------------------------

CALL_MAX_PARTICIPANTS = 8  # including the initiator


def generate_livekit_token(user_id, username, room_name):
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")
    if not api_key or not api_secret:
        raise RuntimeError("Missing LIVEKIT_API_KEY/LIVEKIT_API_SECRET")
    grants = livekit_api.VideoGrants(room_join=True, room=room_name, can_publish=True, can_subscribe=True)
    token = (
        livekit_api.AccessToken(api_key, api_secret)
        .with_identity(str(user_id))
        .with_name(username)
        .with_grants(grants)
        .with_ttl(timedelta(hours=4))
    )
    return token.to_jwt()


@app.route("/calls", methods=["POST"])
@login_required
def create_call():
    if not os.getenv("LIVEKIT_API_KEY") or not os.getenv("LIVEKIT_API_SECRET"):
        return jsonify({"error": "Calling is not configured (missing LIVEKIT_API_KEY/LIVEKIT_API_SECRET)"}), 500

    data = request.get_json(silent=True) or {}
    try:
        friend_ids = list({int(f) for f in (data.get("friend_ids") or [])})
    except (TypeError, ValueError):
        return jsonify({"error": "friend_ids must be a list of user ids"}), 400
    if not friend_ids:
        return jsonify({"error": "friend_ids is required"}), 400

    user_id = current_user_id()
    if user_id in friend_ids:
        return jsonify({"error": "You can't call yourself"}), 400
    if len(friend_ids) + 1 > CALL_MAX_PARTICIPANTS:
        return jsonify({"error": f"Group calls are capped at {CALL_MAX_PARTICIPANTS} participants"}), 400

    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "SELECT friend_id FROM friendships WHERE user_id = %s AND friend_id = ANY(%s)",
        (user_id, friend_ids),
    )
    valid_friend_ids = {r["friend_id"] for r in cur.fetchall()}
    if set(friend_ids) - valid_friend_ids:
        return jsonify({"error": "You can only call your friends"}), 400

    cur.execute("SELECT username FROM users WHERE id = %s", (user_id,))
    username = cur.fetchone()["username"]
    category = get_user_personalization(cur, user_id)
    room_name = f"call-{uuid.uuid4().hex[:16]}"

    cur.execute(
        "INSERT INTO calls (initiator_user_id, room_name, category) VALUES (%s, %s, %s) RETURNING id",
        (user_id, room_name, category),
    )
    call_id = cur.fetchone()["id"]
    cur.execute(
        "INSERT INTO call_participants (call_id, user_id, status, joined_at) VALUES (%s, %s, 'joined', now())",
        (call_id, user_id),
    )
    for fid in friend_ids:
        cur.execute(
            "INSERT INTO call_participants (call_id, user_id, status) VALUES (%s, %s, 'invited')",
            (call_id, fid),
        )
    db.commit()

    token = generate_livekit_token(user_id, username, room_name)
    for fid in friend_ids:
        call_event = {
            "type": "incoming_call", "call_id": call_id, "room_name": room_name,
            "caller_id": user_id, "caller_name": username,
        }
        push_notification(fid, call_event)
        send_fcm_to_user(
            fid, title="Incoming call", body=f"{username} is calling...",
            data=call_event, include_notification=False,
        )

    return jsonify({
        "call_id": call_id, "room_name": room_name, "token": token,
        "livekit_url": os.getenv("LIVEKIT_URL"),
    })


@app.route("/calls/<int:call_id>/join", methods=["POST"])
@login_required
def join_call(call_id):
    if not os.getenv("LIVEKIT_API_KEY") or not os.getenv("LIVEKIT_API_SECRET"):
        return jsonify({"error": "Calling is not configured (missing LIVEKIT_API_KEY/LIVEKIT_API_SECRET)"}), 500

    user_id = current_user_id()
    db = get_db()
    cur = dict_cursor(db)
    cur.execute("SELECT status FROM calls WHERE id = %s", (call_id,))
    call = cur.fetchone()
    if not call:
        return jsonify({"error": "Call not found"}), 404
    if call["status"] == "ended":
        return jsonify({"error": "This call has already ended"}), 400
    cur.execute(
        "UPDATE call_participants SET status = 'joined', joined_at = now() "
        "WHERE call_id = %s AND user_id = %s RETURNING id",
        (call_id, user_id),
    )
    if not cur.fetchone():
        return jsonify({"error": "Call not found"}), 404
    cur.execute("UPDATE calls SET status = 'active' WHERE id = %s AND status = 'ringing'", (call_id,))
    cur.execute("SELECT room_name FROM calls WHERE id = %s", (call_id,))
    room_name = cur.fetchone()["room_name"]
    db.commit()

    cur.execute("SELECT username FROM users WHERE id = %s", (user_id,))
    username = cur.fetchone()["username"]
    token = generate_livekit_token(user_id, username, room_name)
    return jsonify({
        "call_id": call_id, "room_name": room_name, "token": token,
        "livekit_url": os.getenv("LIVEKIT_URL"),
    })


@app.route("/calls/<int:call_id>/decline", methods=["POST"])
@login_required
def decline_call(call_id):
    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "UPDATE call_participants SET status = 'declined' WHERE call_id = %s AND user_id = %s RETURNING id",
        (call_id, current_user_id()),
    )
    updated = cur.fetchone()
    if not updated:
        db.commit()
        return jsonify({"error": "Call not found"}), 404

    # If every other invitee has now declined/left and nobody has joined
    # yet, the call can never proceed -- tell the initiator so their client
    # can hang up instead of sitting in "Call in progress" forever (LiveKit
    # never fires a disconnect event for someone who never connected).
    cur.execute("SELECT initiator_user_id, status FROM calls WHERE id = %s", (call_id,))
    call = cur.fetchone()
    if call["status"] == "ringing":
        cur.execute(
            "SELECT count(*) AS n FROM call_participants "
            "WHERE call_id = %s AND user_id != %s AND status IN ('invited', 'joined')",
            (call_id, call["initiator_user_id"]),
        )
        if cur.fetchone()["n"] == 0:
            cur.execute("UPDATE calls SET status = 'ended', ended_at = now() WHERE id = %s", (call_id,))
            push_notification(call["initiator_user_id"], {"type": "call_declined", "call_id": call_id})
    db.commit()
    return jsonify({"ok": True})


@app.route("/calls/<int:call_id>/leave", methods=["POST"])
@login_required
def leave_call(call_id):
    user_id = current_user_id()
    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "UPDATE call_participants SET status = 'left', left_at = now() "
        "WHERE call_id = %s AND user_id = %s RETURNING id",
        (call_id, user_id),
    )
    if not cur.fetchone():
        return jsonify({"error": "Call not found"}), 404

    cur.execute(
        "SELECT count(*) AS n FROM call_participants WHERE call_id = %s AND status = 'joined'",
        (call_id,),
    )
    call_ended = cur.fetchone()["n"] == 0
    other_ids = []
    if call_ended:
        cur.execute(
            "UPDATE calls SET status = 'ended', ended_at = now() WHERE id = %s AND status != 'ended'",
            (call_id,),
        )
        # Anyone else still 'invited' (never joined -- i.e. still ringing)
        # needs to hear this explicitly: their incoming-call notification is
        # an `ongoing: true` Android notification that never auto-expires,
        # and they have no live LiveKit connection of their own to notice
        # the call ended any other way. (Nobody can still be 'joined' here --
        # call_ended already means the joined count just hit zero -- so an
        # already-connected other party always learns of this via LiveKit's
        # own real-time disconnect event instead, same as WhatsApp/Telegram
        # ending the call screen on both sides the instant either hangs up.)
        cur.execute(
            "SELECT user_id FROM call_participants WHERE call_id = %s AND user_id != %s AND status = 'invited'",
            (call_id, user_id),
        )
        other_ids = [r["user_id"] for r in cur.fetchall()]
    db.commit()

    for pid in other_ids:
        push_notification(pid, {"type": "call_ended", "call_id": call_id})
        send_fcm_to_user(
            pid, title="Call ended", body="",
            data={"type": "call_ended", "call_id": call_id},
            include_notification=False,
        )

    return jsonify({"ok": True, "call_ended": call_ended})


@app.route("/calls/<int:call_id>/recording", methods=["POST"])
@login_required
def upload_call_recording(call_id):
    """Receives the initiator's client-side mixed-audio recording once the
    call ends. Same Deepgram diarized-transcription call as /transcribe,
    then reuses the existing conversation-creation + analysis pipeline --
    the resulting conversation is indistinguishable from a solo dictation
    one, just tagged via calls.conversation_id. If Deepgram detected no
    speech at all, nothing is created -- see the no_speech_detected check
    below."""
    if "audio" not in request.files:
        return jsonify({"error": "No audio file uploaded"}), 400

    # `scope=own` (mobile clients only): each participant uploads just their
    # own local mic recording, independently of everyone else, instead of
    # the web client's single mixed-stream upload from the initiator. Kept
    # as a fully separate branch/idempotency key (call_participants row, not
    # calls.conversation_id) so the legacy web upload path below is
    # completely untouched.
    if request.form.get("scope") == "own":
        return _upload_own_call_recording(call_id)

    user_id = current_user_id()
    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "SELECT c.category, c.conversation_id FROM calls c "
        "JOIN call_participants cp ON cp.call_id = c.id "
        "WHERE c.id = %s AND cp.user_id = %s",
        (call_id, user_id),
    )
    call = cur.fetchone()
    if not call:
        return jsonify({"error": "Call not found"}), 404
    if call["conversation_id"]:
        return jsonify({"conversation_id": call["conversation_id"], "already_processed": True})

    audio_file = request.files["audio"]
    audio_bytes = audio_file.read()
    content_type = audio_file.mimetype or "audio/webm"

    try:
        raw_transcript, _segments = transcribe_audio_bytes(audio_bytes, content_type)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else 500
        return jsonify({"error": f"Transcription error: {e}"}), status
    except requests.RequestException as e:
        return jsonify({"error": f"Could not reach transcription service: {e}"}), 502

    if not raw_transcript.strip():
        # No speech detected (silence, a call that ended before anyone said
        # anything, etc.) -- don't create a conversation at all. One would
        # otherwise sit in everyone's Chats list as permanent, useless
        # clutter, and opening an empty one to ask a question would burn a
        # real LLM call against nothing. calls.conversation_id stays NULL;
        # a later recording upload for this same call (if it ever happened)
        # would still be processed rather than silently no-op'd forever.
        return jsonify({"conversation_id": None, "skipped": "no_speech_detected"})

    # Only participants who actually joined -- someone merely invited (never
    # answered) or who declined was never actually part of this audio, and
    # giving them a full conversation created from other people's transcript
    # plus a "conversation ready" push was a real privacy leak whenever a
    # group call included someone who didn't pick up.
    cur.execute(
        "SELECT user_id FROM call_participants WHERE call_id = %s AND status IN ('joined', 'left')",
        (call_id,),
    )
    participant_ids = [r["user_id"] for r in cur.fetchall()]

    # One conversation row per participant, not just the uploader --
    # conversations.user_id is a single owner (same as every solo-dictation
    # conversation; there's no shared-ownership concept elsewhere in the
    # app), so without this only the uploader's own GET /conversations
    # would ever return it. Same transcript copied to each (one shared
    # mixed-stream recording); each person's title/task/profile/mood
    # extraction still runs independently via the existing per-user
    # pipeline. Category is NOT copied from calls.category (that's just
    # whoever initiated the call's mode at that moment) -- each participant
    # gets tagged with their OWN current category, same as every other
    # conversation-creation path in the app, so this doesn't leak one
    # person's (possibly custom, possibly meaningless to others) category
    # onto everyone else's Chats list.
    conversation_ids = {}
    for pid in participant_ids:
        pid_category = get_user_personalization(cur, pid)
        cur.execute(
            "INSERT INTO conversations (user_id, title, raw_transcript, category) VALUES (%s, %s, %s, %s) RETURNING id",
            (pid, None, raw_transcript, pid_category),
        )
        conversation_ids[pid] = cur.fetchone()["id"]

    primary_conversation_id = conversation_ids[user_id]
    cur.execute("UPDATE calls SET conversation_id = %s WHERE id = %s", (primary_conversation_id, call_id))
    db.commit()
    cache_delete(*(f"user:{pid}:conversations" for pid in conversation_ids))

    # raw_transcript is guaranteed non-empty here (checked above), and
    # identical for every participant (one mixed-stream upload) -- so unlike
    # the per-participant scope=own path, one shared LLM call for the title
    # is enough; no claim/race handling needed since this all happens
    # synchronously in one request rather than N independent uploads.
    threading.Thread(target=_title_all_call_conversations, args=(dict(conversation_ids), raw_transcript), daemon=True).start()
    for pid, conv_id in conversation_ids.items():
        # No live websocket to push background_update to here (this is a
        # REST upload, not a /ws/listen session) -- a no-op push is a
        # valid, unmodified use of the existing function.
        threading.Thread(
            target=run_background_analysis,
            args=(pid, conv_id, raw_transcript, lambda *a, **k: None),
            daemon=True,
        ).start()

    for pid, conv_id in conversation_ids.items():
        push_notification(pid, {
            "type": "call_conversation_ready", "call_id": call_id, "conversation_id": conv_id,
        })
        send_fcm_to_user(
            pid, title="Conversation ready",
            body="Your call has been transcribed into a new conversation.",
            data={"type": "call_conversation_ready", "call_id": call_id, "conversation_id": conv_id},
        )

    return jsonify({"conversation_id": primary_conversation_id})


def _title_all_call_conversations(conversation_ids_by_user, raw_transcript):
    """Legacy web mixed-stream call path: every participant's conversation
    holds the exact same raw_transcript already, so generate its title once
    and apply it to all of them, instead of each participant's
    run_conversation_titling asking the LLM separately and getting
    different wording for what's the same conversation."""
    title = generate_conversation_title(raw_transcript)
    if not title:
        return
    conn = get_raw_connection()
    try:
        cur = conn.cursor()
        for conv_id in conversation_ids_by_user.values():
            cur.execute("UPDATE conversations SET title = %s WHERE id = %s AND title IS NULL", (title, conv_id))
        conn.commit()
    except Exception as e:
        print(f"[_title_all_call_conversations] DB error: {e!r}")
        conn.rollback()
        return
    finally:
        release_raw_connection(conn)
    for pid, conv_id in conversation_ids_by_user.items():
        cache_delete(f"user:{pid}:conversation:{conv_id}", f"user:{pid}:conversations")


def process_own_call_recording_job(call_id, user_id, audio_path, content_type, category):
    """RQ job (queued from _upload_own_call_recording below, or called
    inline as a fallback if Redis/RQ is unreachable): transcribes via
    Deepgram, creates the conversation, runs titling + analysis directly
    (no threading.Thread -- this already runs off the Flask request thread,
    in the worker process), then pushes call_conversation_ready. Runs in a
    separate process/thread from any Flask request, so it uses the pooled
    get_raw_connection()/release_raw_connection() rather than Flask's
    request-scoped get_db()."""
    try:
        with open(audio_path, "rb") as f:
            audio_bytes = f.read()
    finally:
        try:
            os.remove(audio_path)
        except OSError:
            pass

    try:
        raw_transcript, _segments = transcribe_audio_bytes(audio_bytes, content_type)
    except RuntimeError as e:
        return {"error": str(e)}
    except requests.RequestException as e:
        return {"error": f"Transcription request failed: {e}"}

    conn = get_raw_connection()
    try:
        cur = dict_cursor(conn)
        if not raw_transcript.strip():
            cur.execute(
                "UPDATE call_participants SET recording_uploaded_at = now() WHERE call_id = %s AND user_id = %s",
                (call_id, user_id),
            )
            conn.commit()
            return {"conversation_id": None, "skipped": "no_speech_detected"}

        cur.execute(
            "INSERT INTO conversations (user_id, title, raw_transcript, category) VALUES (%s, %s, %s, %s) RETURNING id",
            (user_id, None, raw_transcript, category),
        )
        conversation_id = cur.fetchone()["id"]
        cur.execute(
            "UPDATE call_participants SET recording_uploaded_at = now(), conversation_id = %s "
            "WHERE call_id = %s AND user_id = %s",
            (conversation_id, call_id, user_id),
        )
        conn.commit()
    finally:
        release_raw_connection(conn)

    cache_delete(f"user:{user_id}:conversations")
    assign_shared_call_title(call_id, user_id, conversation_id, raw_transcript)
    run_background_analysis(user_id, conversation_id, raw_transcript, lambda *a, **k: None)
    push_notification(user_id, {
        "type": "call_conversation_ready", "call_id": call_id, "conversation_id": conversation_id,
    })
    send_fcm_to_user(
        user_id, title="Conversation ready",
        body="Your call has been transcribed into a new conversation.",
        data={"type": "call_conversation_ready", "call_id": call_id, "conversation_id": conversation_id},
    )

    return {"conversation_id": conversation_id}


def _upload_own_call_recording(call_id):
    """scope=own branch of upload_call_recording: queues transcription +
    conversation-creation for just the uploading participant, independent
    of every other participant's own upload. Idempotent per (call_id,
    user_id) via call_participants.recording_uploaded_at, not
    calls.conversation_id (that column is reserved for the legacy
    mixed-upload flow above). The actual work happens in
    process_own_call_recording_job, run on the `calls` RQ queue (worker.py)
    so this request doesn't block on Deepgram."""
    user_id = current_user_id()
    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "SELECT cp.recording_uploaded_at, cp.conversation_id FROM calls c "
        "JOIN call_participants cp ON cp.call_id = c.id "
        "WHERE c.id = %s AND cp.user_id = %s",
        (call_id, user_id),
    )
    row = cur.fetchone()
    if not row:
        return jsonify({"error": "Call not found"}), 404
    if row["recording_uploaded_at"]:
        return jsonify({"conversation_id": row["conversation_id"], "already_processed": True})

    required_key = "GROQ_API_KEY" if os.getenv("USE_GROQ_STT", "false").strip().lower() == "true" else "DEEPGRAM_API_KEY"
    if not os.getenv(required_key):
        return jsonify({"error": f"Missing {required_key}"}), 500
    # This participant's OWN active category, not the call's shared
    # `calls.category` column (set once from whoever initiated the call) --
    # otherwise every other participant's resulting conversation gets
    # tagged with a category that's meaningless (or, if custom, doesn't
    # even exist) to them. Matches how every other conversation-creation
    # path (e.g. /ws/listen) already tags by the current user's own mode.
    category = get_user_personalization(cur, user_id)
    audio_file = request.files["audio"]
    content_type = audio_file.mimetype or "audio/webm"
    fd, tmp_path = tempfile.mkstemp(prefix=f"call_{call_id}_{user_id}_", suffix=".audio")
    with os.fdopen(fd, "wb") as f:
        audio_file.save(f)

    try:
        job = call_queue.enqueue(
            process_own_call_recording_job, call_id, user_id, tmp_path, content_type, category,
            job_timeout=240,
        )
        return jsonify({"status": "queued", "job_id": job.id})
    except Exception as e:
        # Falls back to processing inline -- covers Redis/RQ being
        # unreachable (redis.RedisError) *and* RQ's own hard refusal to
        # enqueue a function defined in the __main__ module (a plain
        # ValueError, not a RedisError, raised whenever this file is run
        # directly via `python app.py` rather than imported by a separate
        # runner) -- either way a queueing failure must never mean a lost
        # recording, exactly like this endpoint always worked before the
        # queue existed.
        print(f"[_upload_own_call_recording] enqueue failed ({e!r}), falling back to inline processing")
        result = process_own_call_recording_job(call_id, user_id, tmp_path, content_type, category)
        status = 500 if "error" in result else 200
        return jsonify(result), status


CHAT_FORMATTING_GUIDANCE = (
    "If a specific line in the transcript directly answers or is clearly relevant to the "
    "question, quote it verbatim in your reply, attributed to who said it, like: "
    'Rahul said: "the exact line". Only quote when it genuinely supports the answer -- never '
    "invent or paraphrase a quote. Format the reply in markdown: **bold** key terms, names, or "
    "numbers, and use \"- \" bullet points when listing more than one item; keep a short direct "
    "answer as plain prose instead of forcing it into a list."
)

CHAT_CLOSING_GUIDANCE = (
    "If the user's message is just a brief closing acknowledgment (\"ok\", \"cool\", \"thanks\", "
    "\"got it\", \"sounds good\", and similar) rather than a question or new statement, don't treat "
    "it as an opening to keep going -- no follow-up questions, no extra elaboration, no summarizing "
    "what was just discussed. Give a short, warm acknowledgment (a few words) and let the "
    "conversation rest there."
)

# This reply and the actual reminder-scheduling (trigger_reminder_extraction,
# a separate background LLM call fired after this reply is already sent) are
# fully decoupled -- without this guidance the model has no idea reminders
# are a real, supported capability and defaults to denying it ("I can't set
# reminders, but a calendar alarm would work"), directly contradicting the
# task that silently gets created moments later. This just keeps the visible
# reply honest/consistent; it has no effect on whether a task actually gets
# created, which _REMINDER_INTENT_RE + the LLM extraction below decide
# independently of what this reply says.
CHAT_REMINDER_GUIDANCE = (
    "If the user asks to be reminded about something (e.g. \"remind me to X on Saturday\"), "
    "acknowledge it naturally and confirm you'll remind them -- reminders ARE a real, supported "
    "feature here and get scheduled automatically from this conversation. Never say you can't set "
    "reminders, and never suggest a calendar app or phone alarm instead."
)


def build_chat_system_prompt(personalization=DEFAULT_PERSONALIZATION, persona_context=""):
    base = (
        "You are a concise assistant answering questions about a live or saved "
        "conversation transcript. Be direct: answer what was asked, no restating "
        "the question, no unnecessary preamble or filler, no padding a short "
        "answer to sound thorough. Ground every answer in the transcript/context "
        "provided; if something isn't in it, say so briefly rather than "
        "guessing. Use the prior exchanges in this chat to resolve follow-up "
        "references ('he', 'that', 'earlier') instead of asking for "
        "clarification when it's inferable from context. "
        + PERSONALIZATION_GUIDANCE[normalize_personalization(personalization)]
        + " " + CHAT_FORMATTING_GUIDANCE
        + " " + CHAT_CLOSING_GUIDANCE
        + " " + CHAT_REMINDER_GUIDANCE
    )
    return base + (f" {persona_context}" if persona_context else "")


def build_global_chat_system_prompt(persona_context="", speaker_name=None):
    """For /chat/global -- answering from EXCERPTS OF SEVERAL past
    conversations (not one), each labeled with its own date, so the model
    needs to reason across sessions instead of treating everything as one
    contiguous transcript."""
    now_local = datetime.now().astimezone()
    base = (
        f"Today is {now_local.strftime('%A, %Y-%m-%d')}. You are answering a question using "
        "excerpts from several of the user's own past recorded conversations, each labeled with "
        "its own date/title/category. Use those dates to resolve relative time references "
        "('last week', 'a few days ago'). Treat each excerpt as its own session, not one "
        "continuous conversation. Ground your answer only in what's in these excerpts; if the "
        "answer isn't there, say so briefly rather than guessing. Be concise, no preamble. "
        + CHAT_FORMATTING_GUIDANCE + " " + CHAT_CLOSING_GUIDANCE + " " + CHAT_REMINDER_GUIDANCE
    )
    if speaker_name:
        base += f" These excerpts were chosen because they involve {speaker_name} -- focus on that person."
    return base + (f" {persona_context}" if persona_context else "")


# Chat history lives permanently in the chat_messages table -- nothing here
# ever deletes a row. CHAT_HISTORY_MAX_TURNS only bounds how many of the
# most recent messages get re-sent to the LLM as context on each new turn
# (cost control), completely separate from what GET /conversations/<id>/chat
# can read back, which is always the full, untrimmed history.
CHAT_HISTORY_MAX_TURNS = int(os.getenv("CHAT_HISTORY_MAX_TURNS", "6"))


def _messages_cache_key(user_id, conversation_id):
    if conversation_id:
        return f"user:{user_id}:conversation:{conversation_id}:messages"
    return f"user:{user_id}:global_chat"


def load_chat_context(cur, user_id, conversation_id):
    # Same data GET /conversations/<id>/chat serves, just the tail of it --
    # reuse that cache instead of re-querying Postgres on every /chat turn.
    cached = cache_get_json(_messages_cache_key(user_id, conversation_id))
    if cached is not None:
        tail = cached[-(CHAT_HISTORY_MAX_TURNS * 2):]
        return [{"role": m["role"], "content": m["content"]} for m in tail]
    cur.execute(
        "SELECT role, content FROM chat_messages "
        "WHERE user_id = %s AND conversation_id = %s "
        "ORDER BY created_at DESC LIMIT %s",
        (user_id, conversation_id, CHAT_HISTORY_MAX_TURNS * 2),
    )
    rows = cur.fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def append_chat_messages(cur, user_id, conversation_id, prompt, reply):
    cur.execute(
        "INSERT INTO chat_messages (user_id, conversation_id, role, content) "
        "VALUES (%s, %s, 'user', %s), (%s, %s, 'assistant', %s)",
        (user_id, conversation_id, prompt, user_id, conversation_id, reply),
    )
    # Keep the message-list cache (if any is warm) up to date in place rather
    # than invalidating it -- this is the highest-frequency write path, and
    # re-fetching the full history from Postgres on every single chat turn
    # would defeat the point of caching it.
    cache_key = _messages_cache_key(user_id, conversation_id)
    cached = cache_get_json(cache_key)
    if cached is not None:
        ts = datetime.now(timezone.utc).isoformat()
        cached = cached + [
            {"role": "user", "content": prompt, "created_at": ts},
            {"role": "assistant", "content": reply, "created_at": ts},
        ]
        cache_set_json(cache_key, cached, ttl=3600)


def load_global_chat_context(cur, user_id):
    """Same idea as load_chat_context, but for /chat/global's thread --
    conversation_id IS NULL there, and plain `= NULL` never matches in SQL
    (three-valued logic), so this needs its own query rather than reusing
    load_chat_context with conversation_id=None."""
    cached = cache_get_json(_messages_cache_key(user_id, None))
    if cached is not None:
        tail = cached[-(CHAT_HISTORY_MAX_TURNS * 2):]
        return [{"role": m["role"], "content": m["content"]} for m in tail]
    cur.execute(
        "SELECT role, content FROM chat_messages "
        "WHERE user_id = %s AND conversation_id IS NULL "
        "ORDER BY created_at DESC LIMIT %s",
        (user_id, CHAT_HISTORY_MAX_TURNS * 2),
    )
    rows = cur.fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


@app.route("/conversations", methods=["GET"])
@login_required
def list_conversations():
    """Past sessions/chats, newest first -- lets the frontend offer a
    ChatGPT-style history list instead of losing access to a chat the moment
    a new live-listening session starts (each session is its own
    conversation row; none of them are ever deleted here)."""
    user_id = current_user_id()
    cache_key = f"user:{user_id}:conversations"
    cached = cache_get_json(cache_key)
    if cached is not None:
        return jsonify(cached)
    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "SELECT id, created_at, title, category FROM conversations WHERE user_id = %s ORDER BY created_at DESC",
        (user_id,),
    )
    result = [serialize_row(r) for r in cur.fetchall()]
    cache_set_json(cache_key, result, ttl=600)
    return jsonify(result)


@app.route("/conversations/<int:conversation_id>", methods=["GET"])
@login_required
def get_conversation(conversation_id):
    user_id = current_user_id()
    cache_key = f"user:{user_id}:conversation:{conversation_id}"
    cached = cache_get_json(cache_key)
    if cached is not None:
        return jsonify(cached)
    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "SELECT id, created_at, title, raw_transcript, category FROM conversations WHERE id = %s AND user_id = %s",
        (conversation_id, user_id),
    )
    row = cur.fetchone()
    if not row:
        return jsonify({"error": "Conversation not found"}), 404
    result = serialize_row(row)
    cache_set_json(cache_key, result, ttl=3600)
    return jsonify(result)


@app.route("/conversations/<int:conversation_id>/chat", methods=["GET"])
@login_required
def get_conversation_chat(conversation_id):
    """Full, untrimmed chat history for one conversation -- separate from
    the LLM-context slice /chat uses, so old messages remain readable here
    no matter how long the chat has run."""
    user_id = current_user_id()
    cache_key = _messages_cache_key(user_id, conversation_id)
    cached = cache_get_json(cache_key)
    if cached is not None:
        return jsonify(cached)
    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "SELECT 1 FROM conversations WHERE id = %s AND user_id = %s",
        (conversation_id, user_id),
    )
    if not cur.fetchone():
        return jsonify({"error": "Conversation not found"}), 404
    cur.execute(
        "SELECT role, content, created_at FROM chat_messages "
        "WHERE user_id = %s AND conversation_id = %s ORDER BY created_at ASC",
        (user_id, conversation_id),
    )
    result = [serialize_row(r) for r in cur.fetchall()]
    cache_set_json(cache_key, result, ttl=3600)
    return jsonify(result)


SEARCH_MAX_RESULTS = 20


@app.route("/search", methods=["GET"])
@login_required
def search_conversations():
    """Full-text search across a user's own conversation titles/transcripts
    and chat messages -- lets 'what did I say about the Q3 budget' find the
    actual conversation instead of only matching a title substring the way
    the Chats screen's client-side filter does. search_tsv/content_tsv are
    precomputed (GENERATED ... STORED, see schema.sql) so this is a plain
    GIN index lookup, not a live re-tokenize of every transcript."""
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify([])

    user_id = current_user_id()
    cache_key = f"user:{user_id}:search:{hashlib.md5(q.encode('utf-8')).hexdigest()}"
    cached = cache_get_json(cache_key)
    if cached is not None:
        return jsonify(cached)

    db = get_db()
    cur = dict_cursor(db)

    # Two separate matches per conversation are possible (its own
    # title/transcript, and/or one of its chat messages) -- keep whichever
    # ranks higher rather than returning the same conversation twice.
    matches = {}

    cur.execute(
        "SELECT c.id, c.created_at, c.title, c.category, "
        "ts_rank(c.search_tsv, query) AS rank, "
        "ts_headline('english', coalesce(c.raw_transcript, ''), query, "
        "'MaxWords=20, MinWords=5, MaxFragments=1') AS snippet "
        "FROM conversations c, websearch_to_tsquery('english', %s) query "
        "WHERE c.user_id = %s AND c.search_tsv @@ query "
        "ORDER BY rank DESC LIMIT %s",
        (q, user_id, SEARCH_MAX_RESULTS),
    )
    for r in cur.fetchall():
        matches[r["id"]] = r

    cur.execute(
        "SELECT DISTINCT ON (m.conversation_id) m.conversation_id AS id, "
        "c.created_at, c.title, c.category, "
        "ts_rank(m.content_tsv, query) AS rank, "
        "ts_headline('english', m.content, query, "
        "'MaxWords=20, MinWords=5, MaxFragments=1') AS snippet "
        "FROM chat_messages m "
        "JOIN conversations c ON c.id = m.conversation_id "
        "CROSS JOIN websearch_to_tsquery('english', %s) query "
        "WHERE m.user_id = %s AND m.conversation_id IS NOT NULL AND m.content_tsv @@ query "
        "ORDER BY m.conversation_id, rank DESC LIMIT %s",
        (q, user_id, SEARCH_MAX_RESULTS),
    )
    for r in cur.fetchall():
        existing = matches.get(r["id"])
        if existing is None or r["rank"] > existing["rank"]:
            matches[r["id"]] = r

    ranked = sorted(matches.values(), key=lambda r: r["rank"], reverse=True)[:SEARCH_MAX_RESULTS]
    result = [
        {
            "id": r["id"],
            "created_at": r["created_at"].isoformat() if hasattr(r["created_at"], "isoformat") else r["created_at"],
            "title": r["title"],
            "category": r["category"],
            "snippet": r["snippet"],
        }
        for r in ranked
    ]
    cache_set_json(cache_key, result, ttl=90)
    return jsonify(result)


@app.route("/conversations/<int:conversation_id>", methods=["DELETE"])
@login_required
def delete_conversation(conversation_id):
    """Permanently deletes a conversation and everything scoped to it.
    chat_messages cascade automatically (FK ON DELETE CASCADE); tasks,
    personality_notes, and mood_logs keep existing (their conversation_id
    just goes NULL via ON DELETE SET NULL) since those are meant to
    outlive any one conversation -- only the conversation/transcript/chat
    itself goes away."""
    db = get_db()
    cur = dict_cursor(db)
    user_id = current_user_id()
    cur.execute(
        "DELETE FROM conversations WHERE id = %s AND user_id = %s RETURNING id",
        (conversation_id, user_id),
    )
    deleted = cur.fetchone()
    db.commit()
    if not deleted:
        return jsonify({"error": "Conversation not found"}), 404
    cache_delete(
        f"user:{user_id}:conversation:{conversation_id}",
        f"user:{user_id}:conversation:{conversation_id}:messages",
        f"user:{user_id}:conversations",
    )
    return jsonify({"ok": True})


@app.route("/chat", methods=["POST"])
@login_required
def chat():
    data = request.get_json(silent=True) or {}
    prompt = (data.get("prompt") or "").strip()
    transcript = (data.get("transcript") or "").strip()
    conversation_id = data.get("conversation_id")
    user_id = current_user_id()

    if not prompt:
        return jsonify({"error": "Prompt is required"}), 400

    # Keep chat context bounded to the most recent portion of the
    # conversation rather than sending the whole (potentially long-running)
    # transcript on every single chat message -- otherwise cost per chat
    # message grows the longer the conversation runs.
    MAX_CONTEXT_CHARS = 4000
    def recent(text):
        return text[-MAX_CONTEXT_CHARS:] if len(text) > MAX_CONTEXT_CHARS else text

    db = get_db()
    cur = dict_cursor(db)
    mode = get_user_personalization(cur, user_id)

    context_parts = []
    if conversation_id:
        cur.execute(
            "SELECT raw_transcript FROM conversations WHERE id = %s AND user_id = %s",
            (conversation_id, user_id),
        )
        row = cur.fetchone()
        if row:
            context_parts.append(f"Saved conversation transcript (most recent portion):\n{recent(row['raw_transcript'])}")

    if transcript:
        context_parts.append(f"Live transcript from speech (most recent portion):\n{recent(transcript)}")
    context_parts.append(f"User question:\n{prompt}")
    current_turn_content = "\n\n".join(context_parts)

    # Prior exchanges for this conversation, read back from the DB -- this is
    # what lets the model resolve "what did he mean by that" style follow-ups
    # instead of treating every message as a fresh, isolated call.
    history = load_chat_context(cur, user_id, conversation_id) if conversation_id else []

    persona_context = build_persona_context(get_user_persona(cur, user_id))
    chat_system_prompt = build_chat_system_prompt(mode, persona_context)
    try:
        from emotional_intelligence.ei_adapter import get_relevant_knowledge_cards, get_user_cognitive_context
        ei_context = get_user_cognitive_context(
            user_id, question=prompt, recent_statements=get_ei_recent_statements(user_id)
        )
        knowledge_context = get_relevant_knowledge_cards(user_id, question=prompt)
        if knowledge_context:
            ei_context = f"{ei_context}\n\n{knowledge_context}" if ei_context else knowledge_context
        if ei_context:
            chat_system_prompt = f"{chat_system_prompt}\n\n{ei_context}"
    except Exception:
        pass  # EI engine is optional and additive -- never affects this endpoint's own behavior
    messages = [{"role": "system", "content": chat_system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": current_turn_content})

    remember_ei_recent_statement(user_id, prompt)

    try:
        reply = call_llm(messages)
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else 500
        if status == 429:
            return jsonify({
                "reply": "Ollama quota is currently exhausted or rate limited. Please try again later or use a different API key."
            })
        try:
            error_json = e.response.json()
        except Exception:
            error_json = {"error": str(e)}
        return jsonify({"error": error_json}), status
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500

    if not reply:
        return jsonify({"reply": ""})

    if conversation_id:
        # Store the clean question, not the transcript-laden context blob --
        # the transcript gets freshly re-attached to every call already, so
        # saving it into history too would just duplicate it on every turn.
        # This insert is permanent; nothing ever prunes chat_messages.
        append_chat_messages(cur, user_id, conversation_id, prompt, reply)
        db.commit()
        push_notification(user_id, {"type": "chat_message", "conversation_id": conversation_id})

    try:
        from emotional_intelligence.ei_adapter import trigger_chat_feedback_extraction
        threading.Thread(target=trigger_chat_feedback_extraction, args=(user_id, prompt), daemon=True).start()
    except Exception:
        pass  # EI engine is optional and additive -- never affects this endpoint's own behavior, and this
              # runs in the background after the reply below is already on its way -- never blocks the user.
    threading.Thread(target=trigger_reminder_extraction, args=(user_id, prompt), daemon=True).start()

    return jsonify({"reply": reply})


# How many past conversations to pull into one /chat/global answer, and how
# much of each to send -- keeps a multi-session answer's cost in the same
# ballpark as a single-conversation /chat call instead of scaling with the
# user's entire history.
GLOBAL_CHAT_MAX_CONVERSATIONS = 6
GLOBAL_CHAT_CHARS_PER_CONVERSATION = 1200


@app.route("/chat/global", methods=["GET"])
@login_required
def get_global_chat_history():
    user_id = current_user_id()
    cache_key = _messages_cache_key(user_id, None)
    cached = cache_get_json(cache_key)
    if cached is not None:
        return jsonify(cached)
    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "SELECT role, content, created_at FROM chat_messages "
        "WHERE user_id = %s AND conversation_id IS NULL ORDER BY created_at ASC",
        (user_id,),
    )
    result = [serialize_row(r) for r in cur.fetchall()]
    cache_set_json(cache_key, result, ttl=3600)
    return jsonify(result)


@app.route("/chat/global", methods=["POST"])
@login_required
def chat_global():
    """Cross-session Q&A -- e.g. "What was the topic of discussion with
    Rahul last week?" Not tied to one conversation_id: if the question
    names a known person, retrieval is scoped to every conversation they've
    ever appeared in (via profile_conversations); otherwise it falls back
    to the user's most recent conversations overall. Persisted permanently
    (conversation_id NULL, same table as per-conversation chat) so this
    thread survives closing the panel, reloading, or logging out -- same
    durability /chat already has, just not scoped to one conversation."""
    data = request.get_json(silent=True) or {}
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "Prompt is required"}), 400

    user_id = current_user_id()
    db = get_db()
    cur = dict_cursor(db)
    persona_context = build_persona_context(get_user_persona(cur, user_id))

    # Cheap local keyword match against this user's known people -- no extra
    # LLM call just to figure out who the question is about. Two separate
    # identity spaces, checked independently: speaker_profiles only covers
    # people labeled in a Listen transcript (a friend you've only ever
    # messaged/called, never dictated a conversation naming, would never
    # appear there) -- matched_friend below covers real friendships instead,
    # which is what unlocks the direct-message content further down.
    cur.execute("SELECT id, name FROM speaker_profiles WHERE user_id = %s", (user_id,))
    matched = None
    prompt_lower = prompt.lower()
    for p in cur.fetchall():
        if re.search(r"\b" + re.escape(p["name"].lower()) + r"\b", prompt_lower):
            matched = p
            break

    cur.execute(
        "SELECT u.id, COALESCE(f.nickname, u.username) AS name FROM friendships f "
        "JOIN users u ON u.id = f.friend_id WHERE f.user_id = %s",
        (user_id,),
    )
    matched_friend = None
    for f in cur.fetchall():
        if re.search(r"\b" + re.escape(f["name"].lower()) + r"\b", prompt_lower):
            matched_friend = f
            break

    remember_ei_recent_statement(user_id, prompt)

    ei_context = ""
    try:
        from emotional_intelligence.ei_adapter import (
            get_friend_relationship_insight,
            get_relevant_knowledge_cards,
            get_user_cognitive_context,
        )
        ei_context = get_user_cognitive_context(
            user_id, question=prompt, recent_statements=get_ei_recent_statements(user_id)
        )
        knowledge_context = get_relevant_knowledge_cards(user_id, question=prompt)
        if knowledge_context:
            ei_context = f"{ei_context}\n\n{knowledge_context}" if ei_context else knowledge_context
        if matched_friend:
            insight = get_friend_relationship_insight(user_id, matched_friend["id"])
            if insight:
                insight_text = (
                    f"Relationship insight with {matched_friend['name']}: trust={insight['trust_score']}, "
                    f"conflict={insight['conflict_score']}, emotional_support={insight['emotional_support']}. "
                    f"{insight['relationship_summary'] or ''}"
                )
                ei_context = f"{ei_context}\n\n{insight_text}" if ei_context else insight_text
    except Exception:
        ei_context = ""  # EI engine is optional and additive -- never affects this endpoint's own behavior

    # Recent direct-message history with a matched friend -- the gap this
    # was added to close: /chat/global previously had no path to this table
    # at all, so "what did Kunal and I talk about" could never be answered
    # even though the messages exist, because retrieval below only ever
    # looked at Listen-derived conversations.
    GLOBAL_CHAT_DM_LIMIT = 30
    direct_messages_context = ""
    if matched_friend:
        cur.execute(
            "SELECT sender_id, content, created_at FROM direct_messages "
            "WHERE (sender_id = %(me)s AND recipient_id = %(friend)s) "
            "OR (sender_id = %(friend)s AND recipient_id = %(me)s) "
            "ORDER BY id DESC LIMIT %(limit)s",
            {"me": user_id, "friend": matched_friend["id"], "limit": GLOBAL_CHAT_DM_LIMIT},
        )
        dm_rows = list(reversed(cur.fetchall()))
        if dm_rows:
            dm_lines = [f"Your direct-message chat with {matched_friend['name']} (most recent {len(dm_rows)} messages):"]
            for r in dm_rows:
                who = "You" if r["sender_id"] == user_id else matched_friend["name"]
                dm_lines.append(f"- {who}: {r['content']}")
            direct_messages_context = "\n".join(dm_lines)

    # Your own past observations about this person (from personality_notes,
    # written by /analyze whenever they showed up as a speaker in one of
    # YOUR conversations) -- this is your own recorded data, not anything
    # pulled from the other person's own account, so it's safe to surface
    # freely and doesn't depend on the emotional_intelligence flag at all.
    personal_notes_context = ""
    if matched:
        cur.execute(
            "SELECT observation FROM personality_notes WHERE profile_id = %s ORDER BY created_at DESC LIMIT 20",
            (matched["id"],),
        )
        observations = [r["observation"] for r in cur.fetchall() if r["observation"]]
        if observations:
            personal_notes_context = (
                f"What you've personally noted about {matched['name']} across your own past conversations:\n"
                + "\n".join(f"- {o}" for o in observations)
            )

    if matched:
        cur.execute(
            "SELECT DISTINCT c.id, c.title, c.created_at, c.category, c.raw_transcript "
            "FROM profile_conversations pc JOIN conversations c ON c.id = pc.conversation_id "
            "WHERE pc.profile_id = %s ORDER BY c.created_at DESC LIMIT %s",
            (matched["id"], GLOBAL_CHAT_MAX_CONVERSATIONS),
        )
    else:
        cur.execute(
            "SELECT id, title, created_at, category, raw_transcript FROM conversations "
            "WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
            (user_id, GLOBAL_CHAT_MAX_CONVERSATIONS),
        )
    conversations = cur.fetchall()

    if not conversations and not ei_context and not personal_notes_context and not direct_messages_context:
        reply = "I don't have any past conversations to draw from yet."
        append_chat_messages(cur, user_id, None, prompt, reply)
        db.commit()
        try:
            from emotional_intelligence.ei_adapter import trigger_chat_feedback_extraction
            threading.Thread(target=trigger_chat_feedback_extraction, args=(user_id, prompt), daemon=True).start()
        except Exception:
            pass  # EI engine is optional and additive -- never affects this endpoint's own behavior
        return jsonify({"reply": reply, "matched_speaker": None, "conversations_used": 0})

    blocks = []
    for c in conversations:
        excerpt = (c["raw_transcript"] or "")[:GLOBAL_CHAT_CHARS_PER_CONVERSATION]
        when = c["created_at"].strftime("%A, %Y-%m-%d %H:%M") if isinstance(c["created_at"], datetime) else str(c["created_at"])
        blocks.append(f"[{when} - {c['title'] or 'Untitled'} ({c['category']})]\n{excerpt}")

    # Prior exchanges in this thread, read back from the DB -- same
    # follow-up-resolution role load_chat_context plays for /chat.
    history = load_global_chat_context(cur, user_id)

    transcripts_section = "\n\n---\n\n".join(blocks) if blocks else "(No saved conversation transcripts available.)"
    if personal_notes_context:
        transcripts_section = f"{personal_notes_context}\n\n---\n\n{transcripts_section}"
    if direct_messages_context:
        transcripts_section = f"{direct_messages_context}\n\n---\n\n{transcripts_section}"
    user_content = f"{transcripts_section}\n\n---\n\nUser question:\n{prompt}"
    matched_name = matched["name"] if matched else (matched_friend["name"] if matched_friend else None)
    global_chat_system_prompt = build_global_chat_system_prompt(persona_context, matched_name)
    if ei_context:
        global_chat_system_prompt = f"{global_chat_system_prompt}\n\n{ei_context}"
    messages = [{"role": "system", "content": global_chat_system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_content})

    try:
        reply = call_llm(messages)
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else 500
        if status == 429:
            return jsonify({
                "reply": "Ollama quota is currently exhausted or rate limited. Please try again later or use a different API key."
            })
        try:
            error_json = e.response.json()
        except Exception:
            error_json = {"error": str(e)}
        return jsonify({"error": error_json}), status
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500

    if reply:
        # Store the clean question/reply, not the retrieval blob -- that
        # gets freshly rebuilt from profile_conversations/conversations on
        # every turn already, so saving it too would duplicate it forever.
        append_chat_messages(cur, user_id, None, prompt, reply)
        db.commit()

    try:
        from emotional_intelligence.ei_adapter import trigger_chat_feedback_extraction
        threading.Thread(target=trigger_chat_feedback_extraction, args=(user_id, prompt), daemon=True).start()
    except Exception:
        pass  # EI engine is optional and additive -- never affects this endpoint's own behavior
    threading.Thread(target=trigger_reminder_extraction, args=(user_id, prompt), daemon=True).start()

    return jsonify({
        "reply": reply,
        "matched_speaker": matched["name"] if matched else None,
        "conversations_used": len(conversations),
    })


IMAGE_UPLOAD_PLACEHOLDER = "[Image uploaded]"


@app.route("/chat/global/image", methods=["POST"])
@login_required
def chat_global_image():
    """Companion to chat_global above, for sending an image (e.g. a photo of
    a bill or a handwritten note) into the global chat. Reuses the existing
    machinery rather than building a parallel image-memory system: the
    vision-extracted text is inserted as a normal `conversations` row, so
    chat_global's own retrieval query (above) picks it up on the very next
    question with zero new retrieval code, and real_user_extraction.py's
    nightly batch turns it into structured facts for free, same as any other
    conversation. `description` (the user-supplied caption, mandatory in
    both frontends' UI, defensively optional here) is what
    trigger_reminder_extraction/trigger_chat_feedback_extraction actually
    run against below -- so "remind me to pay this by Friday" as the
    description is what makes a real reminder get created, exactly like
    typing the same words into a normal chat message already does."""
    if "image" not in request.files:
        return jsonify({"error": "No image file uploaded"}), 400

    image_file = request.files["image"]
    content_length = request.content_length or 0
    if content_length > IMAGE_MAX_UPLOAD_BYTES:
        return jsonify({"error": "Image is too large (max 10MB)"}), 400

    description = (request.form.get("description") or "").strip()
    user_id = current_user_id()
    db = get_db()
    cur = dict_cursor(db)

    try:
        image_bytes, mimetype = _prepare_image_for_vision(image_file)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    instruction = (
        "This image was shared in a personal chat assistant. Extract ALL text and structured "
        "information visible in it, thoroughly and literally (amounts, dates, merchant/sender names, "
        "line items if it's a bill/receipt; the full text if it's a handwritten note) -- this will be "
        "stored as searchable text, not shown directly to the user, so be exhaustive rather than "
        "concise. The user's own description of the image: "
        f"{description or '(none given)'}\n\n"
        "Reply with exactly two labeled sections, nothing else:\n"
        "EXTRACTED: <the thorough, literal extraction described above>\n"
        "SUMMARY: <a brief, natural 1-2 sentence acknowledgment of what this image is, for a chat reply "
        "-- don't repeat the extraction verbatim>"
    )
    try:
        vision_reply = call_groq_vision(image_bytes, mimetype, instruction)
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else 500
        try:
            error_json = e.response.json()
        except Exception:
            error_json = {"error": str(e)}
        return jsonify({"error": error_json}), status
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500

    extracted_match = re.search(r"EXTRACTED:\s*(.*?)(?=\nSUMMARY:|\Z)", vision_reply, re.DOTALL)
    summary_match = re.search(r"SUMMARY:\s*(.*)", vision_reply, re.DOTALL)
    extracted_text = (extracted_match.group(1) if extracted_match else vision_reply).strip()
    reply = (summary_match.group(1) if summary_match else vision_reply).strip()

    if not extracted_text:
        return jsonify({"error": "Could not extract any text from that image"}), 422

    category = get_user_personalization(cur, user_id)
    title = generate_conversation_title(extracted_text)
    cur.execute(
        "INSERT INTO conversations (user_id, title, raw_transcript, category) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (user_id, title, extracted_text, category),
    )
    conversation_id = cur.fetchone()["id"]

    user_message_content = description or IMAGE_UPLOAD_PLACEHOLDER
    append_chat_messages(cur, user_id, None, user_message_content, reply)
    db.commit()

    if description:
        try:
            from emotional_intelligence.ei_adapter import trigger_chat_feedback_extraction
            # description only, not extracted_text -- real_user_extraction.py's
            # nightly batch already turns the conversations row inserted above
            # into facts for free, so there's no gap here to fix.
            threading.Thread(target=trigger_chat_feedback_extraction, args=(user_id, description), daemon=True).start()
        except Exception:
            pass  # EI engine is optional and additive -- never affects this endpoint's own behavior
        # Unlike trigger_chat_feedback_extraction above, this one has no
        # nightly-batch fallback -- reminders need to exist right away, so it
        # needs the actual image content, not just the instruction. Passing
        # description alone (the original version of this code) meant "create
        # a reminder for each exam and class" had no exams/classes to work
        # from -- it could only ever produce one vague catch-all task.
        reminder_content = f"{description}\n\nContent extracted from the image:\n{extracted_text}"
        threading.Thread(target=trigger_reminder_extraction, args=(user_id, reminder_content), daemon=True).start()

    return jsonify({
        "reply": reply,
        "conversation_id": conversation_id,
        "title": title,
        "category": category,
    })


verify_db_connection()

def _start_nudge_worker_safely():
    """The nudges/ module is optional and additive, same convention as
    emotional_intelligence -- an import failure here (missing dependency,
    schema not yet applied, etc.) must never prevent the main app from
    starting."""
    try:
        from nudges.nudge_engine import start_nudge_worker
        start_nudge_worker(push_notification, send_fcm_to_user)
    except Exception as e:
        print(f"[nudges] worker not started: {e!r}")


# Under a WSGI server (gunicorn etc.) this module is imported, not run as
# __main__ -- the dev-server startup block below never executes there, so
# the reminder-email worker needs its own start path here instead. Only
# safe with a single worker process; running multiple gunicorn workers
# would start one thread per worker and send duplicate reminder emails.
if __name__ != "__main__" and IS_WEB_ROLE:
    threading.Thread(target=email_reminder_worker, daemon=True).start()
    threading.Thread(target=call_ring_timeout_worker, daemon=True).start()
    _start_nudge_worker_safely()

if __name__ == "__main__":
    # Mic access (getUserMedia) only works over "secure contexts" -- https,
    # or localhost. Testing from other devices on your LAN needs https even
    # with a self-signed cert; set USE_HTTPS=true in .env to turn it on.
    # Each device will need to click through one browser warning the first
    # time (the cert isn't from a trusted authority, but that's fine for
    # local testing -- the browser still treats it as a secure context).
    use_https = os.getenv("USE_HTTPS", "false").lower() == "true"
    ssl_context = "adhoc" if use_https else None
    port = int(os.getenv("PORT", "5000"))

    # debug=True runs Flask's reloader, which re-executes this module in a
    # child process -- WERKZEUG_RUN_MAIN is only set in that actual running
    # child, so checking it avoids starting two copies of the email worker.
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" and IS_WEB_ROLE:
        threading.Thread(target=email_reminder_worker, daemon=True).start()
        threading.Thread(target=call_ring_timeout_worker, daemon=True).start()
        _start_nudge_worker_safely()

    app.run(host="0.0.0.0", port=port, debug=True, threaded=True, ssl_context=ssl_context)
