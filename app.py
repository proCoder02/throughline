
import os
import re
import json
import secrets
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
import livekit.api as livekit_api

load_dotenv()

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
db_pool = psycopg2.pool.ThreadedConnectionPool(minconn=2, maxconn=20, dsn=DATABASE_URL)


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


def now_iso():
    return datetime.now(timezone.utc).isoformat()


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
LANGUAGE_OPTIONS = ["English", "Hindi", "Spanish", "French", "German", "Mandarin", "Arabic"]
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
            cur.execute(
                "SELECT tasks.id, tasks.description, tasks.owner, tasks.due_date, "
                "tasks.reminder_at, users.email FROM tasks "
                "JOIN users ON tasks.user_id = users.id "
                "WHERE tasks.reminder_at IS NOT NULL AND tasks.email_sent = FALSE "
                "AND tasks.reminder_at <= %s AND users.email IS NOT NULL AND users.email != ''",
                (now,),
            )
            rows = cur.fetchall()
            for row in rows:
                sent = send_reminder_email(
                    row["email"], row["description"], row["owner"], row["due_date"], row["reminder_at"]
                )
                if sent:
                    cur.execute("UPDATE tasks SET email_sent = TRUE WHERE id = %s", (row["id"],))
                    conn.commit()
            cur.close()
        except Exception as e:
            print(f"[email_reminder_worker] error: {e!r}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                release_raw_connection(conn)
        time.sleep(60)


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
    means re-using an existing name is a no-op, not a duplicate/error."""
    cur.execute(
        "INSERT INTO speaker_profiles (user_id, name) VALUES (%s, %s) "
        "ON CONFLICT (user_id, name) DO NOTHING",
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
    to a real, durable person rather than a free-text label."""
    cur.execute(
        "SELECT id FROM speaker_profiles WHERE user_id = %s AND lower(name) = lower(%s)",
        (user_id, name),
    )
    row = cur.fetchone()
    if row:
        return _first_value(row)
    cur.execute(
        "INSERT INTO speaker_profiles (user_id, name) VALUES (%s, %s) "
        "ON CONFLICT (user_id, name) DO NOTHING RETURNING id",
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
4. "tags": 2-5 short (1-4 word) topics from THIS transcript worth asking the assistant more \
about, matching the mode above (e.g. office: "budget approval"; study: "midterm scope"). Empty \
list if nothing distinct came up.

Reply with ONLY this JSON, no preamble/fences:
{{"tasks": [{{"description": "", "owner": null, "due_date": null, "reminder_at": null}}], \
"speakers": [{{"label": "", "observations": [""]}}], \
"mood": {{"label": "neutral", "score": 0.5}}, "tags": [""]}}
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


def run_conversation_titling(conversation_id, raw_transcript):
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
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"[run_conversation_titling] DB error: {e!r}")
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


@app.route("/transcribe", methods=["POST"])
@login_required
def transcribe_audio():
    """Send a recorded audio clip to Deepgram for diarized transcription and
    return a speaker-labeled transcript in the "Name: text" convention used
    elsewhere in this app. Deepgram handles concurrent requests from many
    users on its own infrastructure -- nothing to scale on our side."""
    if "audio" not in request.files:
        return jsonify({"error": "No audio file uploaded"}), 400

    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        return jsonify({"error": "Missing DEEPGRAM_API_KEY"}), 500

    audio_file = request.files["audio"]
    audio_bytes = audio_file.read()
    content_type = audio_file.mimetype or "audio/webm"

    try:
        resp = requests.post(
            "https://api.deepgram.com/v1/listen",
            params={
                "diarize": "true",
                "punctuate": "true",
                "utterances": "true",
                "model": "nova-2",
            },
            headers={
                "Authorization": f"Token {api_key}",
                "Content-Type": content_type,
            },
            data=audio_bytes,
            timeout=120,
        )
        resp.raise_for_status()
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else 500
        return jsonify({"error": f"Deepgram error: {e}"}), status
    except requests.RequestException as e:
        return jsonify({"error": f"Could not reach Deepgram: {e}"}), 502

    data = resp.json()
    utterances = (data.get("results") or {}).get("utterances") or []

    lines = []
    segments = []
    for utt in utterances:
        speaker_label = f"Speaker {utt.get('speaker', 0)}"
        text = (utt.get("transcript") or "").strip()
        if not text:
            continue
        lines.append(f"{speaker_label}: {text}")
        segments.append({
            "speaker": speaker_label,
            "text": text,
            "start": utt.get("start"),
            "end": utt.get("end"),
        })

    transcript_text = "\n".join(lines)
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
    cur.execute(
        "INSERT INTO conversations (user_id, title, raw_transcript) "
        "VALUES (%s, %s, %s) RETURNING id",
        (current_user_id(), title, raw_transcript),
    )
    conversation_id = cur.fetchone()["id"]
    db.commit()
    return jsonify({"conversation_id": conversation_id})


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
    tags = [t for t in (parsed.get("tags") or []) if isinstance(t, str) and t.strip()][:5]

    conn = get_raw_connection()
    created_tasks = []
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
        mood_label = (mood.get("label") or "").strip()
        if mood_label:
            cur.execute(
                "INSERT INTO mood_logs (user_id, conversation_id, mood_label, mood_score) "
                "VALUES (%s, %s, %s, %s)",
                (user_id, conversation_id, mood_label, mood.get("score")),
            )
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"[run_background_analysis] DB error: {e!r}")
        conn.rollback()
        created_tasks = []
    finally:
        release_raw_connection(conn)

    for task_id, description in created_tasks:
        push_notification(user_id, {"type": "task_created", "task_id": task_id, "description": description})

    push({"type": "background_update", "tasks_found": len(tasks), "speakers_found": len(speakers), "tags": tags})


# ---------------------------------------------------------------------------
# Live notifications (WebSocket + server-side pending queue)
#
# Mirrors WhatsApp's shape at a scale that doesn't need a broker: a per-user
# queue on the server, drained into any live connection immediately, or held
# until the user's next connection if none is open right now. This is what
# lets a second open tab/device for the same account pick up a new task or
# chat reply live -- the same-tab case that triggered the event already has
# it from its own request response.
# ---------------------------------------------------------------------------

_notify_lock = threading.Lock()
_notify_connections = {}  # user_id -> list[ws]
_pending_events = {}      # user_id -> list[event dict], capped, drained on connect


def push_notification(user_id, event):
    with _notify_lock:
        conns = list(_notify_connections.get(user_id, []))
        if not conns:
            queue = _pending_events.setdefault(user_id, [])
            queue.append(event)
            del queue[:-50]  # cap so a long-offline user's queue can't grow unbounded
            return
    for ws in conns:
        try:
            ws.send(json.dumps(event))
        except Exception:
            pass


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
        pending = _pending_events.pop(user_id, [])
    for event in pending:
        try:
            ws.send(json.dumps(event))
        except Exception:
            pass

    try:
        while True:
            # No client->server messages expected on this channel; this
            # just blocks until the browser closes the tab/connection.
            if ws.receive() is None:
                break
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
            finally:
                release_raw_connection(conn)
            # The conversation just ended -- generate its title now rather
            # than leaving it null (shown as "Untitled conversation" in the
            # Conversations tab) forever.
            threading.Thread(
                target=run_conversation_titling,
                args=(conversation_id, full_transcript),
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
    try:
        content = call_llm(
            [
                {"role": "system", "content": build_analysis_prompt(mode, persona_context)},
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
    tags = [t for t in (parsed.get("tags") or []) if isinstance(t, str) and t.strip()][:5]

    for task in tasks:
        description = (task.get("description") or "").strip()
        if not description:
            continue
        cur.execute(
            "INSERT INTO tasks (user_id, conversation_id, description, owner, due_date, reminder_at, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'open')",
            (user_id, conversation_id, description, task.get("owner"), task.get("due_date"),
             normalize_reminder_at(task.get("reminder_at"))),
        )

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
    if mood_label:
        cur.execute(
            "INSERT INTO mood_logs (user_id, conversation_id, mood_label, mood_score) "
            "VALUES (%s, %s, %s, %s)",
            (user_id, conversation_id, mood_label, mood.get("score")),
        )

    db.commit()

    return jsonify(
        {
            "conversation_id": conversation_id,
            "tasks": tasks,
            "speakers": speakers,
            "mood": mood,
            "tags": tags,
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
    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "SELECT users.id, users.username, friendships.nickname FROM friendships "
        "JOIN users ON users.id = friendships.friend_id "
        "WHERE friendships.user_id = %s ORDER BY COALESCE(friendships.nickname, users.username)",
        (current_user_id(),),
    )
    return jsonify([dict(r) for r in cur.fetchall()])


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

    # "Through the day" -- everything logged since local midnight, oldest
    # first, so the frontend can render it as a timeline rather than just
    # the latest snapshot.
    since_local_midnight = datetime.now().astimezone().replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    cur.execute(
        "SELECT mood_label, mood_score, created_at FROM mood_logs "
        "WHERE user_id = %s AND created_at >= %s ORDER BY created_at ASC",
        (friend_id, since_local_midnight),
    )
    entries = [serialize_row(r) for r in cur.fetchall()]
    return jsonify({"friend_id": friend_id, "entries": entries})


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
        push_notification(fid, {
            "type": "incoming_call", "call_id": call_id, "room_name": room_name,
            "caller_id": user_id, "caller_name": username,
        })

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
    db.commit()
    if not updated:
        return jsonify({"error": "Call not found"}), 404
    return jsonify({"ok": True})


@app.route("/calls/<int:call_id>/leave", methods=["POST"])
@login_required
def leave_call(call_id):
    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "UPDATE call_participants SET status = 'left', left_at = now() "
        "WHERE call_id = %s AND user_id = %s RETURNING id",
        (call_id, current_user_id()),
    )
    if not cur.fetchone():
        return jsonify({"error": "Call not found"}), 404

    cur.execute(
        "SELECT count(*) AS n FROM call_participants WHERE call_id = %s AND status = 'joined'",
        (call_id,),
    )
    call_ended = cur.fetchone()["n"] == 0
    if call_ended:
        cur.execute(
            "UPDATE calls SET status = 'ended', ended_at = now() WHERE id = %s AND status != 'ended'",
            (call_id,),
        )
    db.commit()
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

    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        return jsonify({"error": "Missing DEEPGRAM_API_KEY"}), 500
    audio_file = request.files["audio"]
    audio_bytes = audio_file.read()
    content_type = audio_file.mimetype or "audio/webm"

    try:
        resp = requests.post(
            "https://api.deepgram.com/v1/listen",
            params={"diarize": "true", "punctuate": "true", "utterances": "true", "model": "nova-2"},
            headers={"Authorization": f"Token {api_key}", "Content-Type": content_type},
            data=audio_bytes,
            timeout=120,
        )
        resp.raise_for_status()
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else 500
        return jsonify({"error": f"Deepgram error: {e}"}), status
    except requests.RequestException as e:
        return jsonify({"error": f"Could not reach Deepgram: {e}"}), 502

    utterances = (resp.json().get("results") or {}).get("utterances") or []
    lines = []
    for utt in utterances:
        text = (utt.get("transcript") or "").strip()
        if text:
            lines.append(f"Speaker {utt.get('speaker', 0)}: {text}")
    raw_transcript = "\n".join(lines)

    if not raw_transcript.strip():
        # No speech detected (silence, a call that ended before anyone said
        # anything, etc.) -- don't create a conversation at all. One would
        # otherwise sit in everyone's Chats list as permanent, useless
        # clutter, and opening an empty one to ask a question would burn a
        # real LLM call against nothing. calls.conversation_id stays NULL;
        # a later recording upload for this same call (if it ever happened)
        # would still be processed rather than silently no-op'd forever.
        return jsonify({"conversation_id": None, "skipped": "no_speech_detected"})

    cur.execute("SELECT user_id FROM call_participants WHERE call_id = %s", (call_id,))
    participant_ids = [r["user_id"] for r in cur.fetchall()]

    # One conversation row per participant, not just the uploader --
    # conversations.user_id is a single owner (same as every solo-dictation
    # conversation; there's no shared-ownership concept elsewhere in the
    # app), so without this only the uploader's own GET /conversations
    # would ever return it. Same transcript/category copied to each; each
    # person's title/task/profile/mood extraction still runs independently
    # via the existing per-user pipeline, so it stays consistent with how
    # every other conversation in the app is attributed.
    conversation_ids = {}
    for pid in participant_ids:
        cur.execute(
            "INSERT INTO conversations (user_id, title, raw_transcript, category) VALUES (%s, %s, %s, %s) RETURNING id",
            (pid, None, raw_transcript, call["category"]),
        )
        conversation_ids[pid] = cur.fetchone()["id"]

    primary_conversation_id = conversation_ids[user_id]
    cur.execute("UPDATE calls SET conversation_id = %s WHERE id = %s", (primary_conversation_id, call_id))
    db.commit()

    # raw_transcript is guaranteed non-empty here (checked above).
    for pid, conv_id in conversation_ids.items():
        threading.Thread(target=run_conversation_titling, args=(conv_id, raw_transcript), daemon=True).start()
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

    return jsonify({"conversation_id": primary_conversation_id})


def _upload_own_call_recording(call_id):
    """scope=own branch of upload_call_recording: transcribes and attaches
    a conversation for just the uploading participant, independent of
    every other participant's own upload. Idempotent per
    (call_id, user_id) via call_participants.recording_uploaded_at, not
    calls.conversation_id (that column is reserved for the legacy
    mixed-upload flow above)."""
    user_id = current_user_id()
    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "SELECT c.category, cp.recording_uploaded_at, cp.conversation_id FROM calls c "
        "JOIN call_participants cp ON cp.call_id = c.id "
        "WHERE c.id = %s AND cp.user_id = %s",
        (call_id, user_id),
    )
    row = cur.fetchone()
    if not row:
        return jsonify({"error": "Call not found"}), 404
    if row["recording_uploaded_at"]:
        return jsonify({"conversation_id": row["conversation_id"], "already_processed": True})

    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        return jsonify({"error": "Missing DEEPGRAM_API_KEY"}), 500
    audio_file = request.files["audio"]
    audio_bytes = audio_file.read()
    content_type = audio_file.mimetype or "audio/webm"

    try:
        resp = requests.post(
            "https://api.deepgram.com/v1/listen",
            params={"diarize": "true", "punctuate": "true", "utterances": "true", "model": "nova-2"},
            headers={"Authorization": f"Token {api_key}", "Content-Type": content_type},
            data=audio_bytes,
            timeout=120,
        )
        resp.raise_for_status()
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else 500
        return jsonify({"error": f"Deepgram error: {e}"}), status
    except requests.RequestException as e:
        return jsonify({"error": f"Could not reach Deepgram: {e}"}), 502

    utterances = (resp.json().get("results") or {}).get("utterances") or []
    lines = []
    for utt in utterances:
        text = (utt.get("transcript") or "").strip()
        if text:
            lines.append(f"Speaker {utt.get('speaker', 0)}: {text}")
    raw_transcript = "\n".join(lines)

    if not raw_transcript.strip():
        cur.execute(
            "UPDATE call_participants SET recording_uploaded_at = now() WHERE call_id = %s AND user_id = %s",
            (call_id, user_id),
        )
        db.commit()
        return jsonify({"conversation_id": None, "skipped": "no_speech_detected"})

    cur.execute(
        "INSERT INTO conversations (user_id, title, raw_transcript, category) VALUES (%s, %s, %s, %s) RETURNING id",
        (user_id, None, raw_transcript, row["category"]),
    )
    conversation_id = cur.fetchone()["id"]
    cur.execute(
        "UPDATE call_participants SET recording_uploaded_at = now(), conversation_id = %s "
        "WHERE call_id = %s AND user_id = %s",
        (conversation_id, call_id, user_id),
    )
    db.commit()

    threading.Thread(target=run_conversation_titling, args=(conversation_id, raw_transcript), daemon=True).start()
    threading.Thread(
        target=run_background_analysis,
        args=(user_id, conversation_id, raw_transcript, lambda *a, **k: None),
        daemon=True,
    ).start()
    push_notification(user_id, {
        "type": "call_conversation_ready", "call_id": call_id, "conversation_id": conversation_id,
    })

    return jsonify({"conversation_id": conversation_id})


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
        "answer isn't there, say so briefly rather than guessing. Be concise, no preamble."
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


def load_chat_context(cur, user_id, conversation_id):
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


def load_global_chat_context(cur, user_id):
    """Same idea as load_chat_context, but for /chat/global's thread --
    conversation_id IS NULL there, and plain `= NULL` never matches in SQL
    (three-valued logic), so this needs its own query rather than reusing
    load_chat_context with conversation_id=None."""
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
    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "SELECT id, created_at, title, category FROM conversations WHERE user_id = %s ORDER BY created_at DESC",
        (current_user_id(),),
    )
    return jsonify([serialize_row(r) for r in cur.fetchall()])


@app.route("/conversations/<int:conversation_id>", methods=["GET"])
@login_required
def get_conversation(conversation_id):
    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "SELECT id, created_at, title, raw_transcript, category FROM conversations WHERE id = %s AND user_id = %s",
        (conversation_id, current_user_id()),
    )
    row = cur.fetchone()
    if not row:
        return jsonify({"error": "Conversation not found"}), 404
    return jsonify(serialize_row(row))


@app.route("/conversations/<int:conversation_id>/chat", methods=["GET"])
@login_required
def get_conversation_chat(conversation_id):
    """Full, untrimmed chat history for one conversation -- separate from
    the LLM-context slice /chat uses, so old messages remain readable here
    no matter how long the chat has run."""
    db = get_db()
    cur = dict_cursor(db)
    user_id = current_user_id()
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
    return jsonify([serialize_row(r) for r in cur.fetchall()])


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
    cur.execute(
        "DELETE FROM conversations WHERE id = %s AND user_id = %s RETURNING id",
        (conversation_id, current_user_id()),
    )
    deleted = cur.fetchone()
    db.commit()
    if not deleted:
        return jsonify({"error": "Conversation not found"}), 404
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
    messages = [{"role": "system", "content": build_chat_system_prompt(mode, persona_context)}]
    messages.extend(history)
    messages.append({"role": "user", "content": current_turn_content})

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
    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "SELECT role, content, created_at FROM chat_messages "
        "WHERE user_id = %s AND conversation_id IS NULL ORDER BY created_at ASC",
        (current_user_id(),),
    )
    return jsonify([serialize_row(r) for r in cur.fetchall()])


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
    # LLM call just to figure out who the question is about.
    cur.execute("SELECT id, name FROM speaker_profiles WHERE user_id = %s", (user_id,))
    matched = None
    prompt_lower = prompt.lower()
    for p in cur.fetchall():
        if re.search(r"\b" + re.escape(p["name"].lower()) + r"\b", prompt_lower):
            matched = p
            break

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

    if not conversations:
        reply = "I don't have any past conversations to draw from yet."
        append_chat_messages(cur, user_id, None, prompt, reply)
        db.commit()
        return jsonify({"reply": reply, "matched_speaker": None, "conversations_used": 0})

    blocks = []
    for c in conversations:
        excerpt = (c["raw_transcript"] or "")[:GLOBAL_CHAT_CHARS_PER_CONVERSATION]
        when = c["created_at"].strftime("%A, %Y-%m-%d %H:%M") if isinstance(c["created_at"], datetime) else str(c["created_at"])
        blocks.append(f"[{when} - {c['title'] or 'Untitled'} ({c['category']})]\n{excerpt}")

    # Prior exchanges in this thread, read back from the DB -- same
    # follow-up-resolution role load_chat_context plays for /chat.
    history = load_global_chat_context(cur, user_id)

    user_content = "\n\n---\n\n".join(blocks) + f"\n\n---\n\nUser question:\n{prompt}"
    messages = [{"role": "system", "content": build_global_chat_system_prompt(persona_context, matched["name"] if matched else None)}]
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

    return jsonify({
        "reply": reply,
        "matched_speaker": matched["name"] if matched else None,
        "conversations_used": len(conversations),
    })


verify_db_connection()

# Under a WSGI server (gunicorn etc.) this module is imported, not run as
# __main__ -- the dev-server startup block below never executes there, so
# the reminder-email worker needs its own start path here instead. Only
# safe with a single worker process; running multiple gunicorn workers
# would start one thread per worker and send duplicate reminder emails.
if __name__ != "__main__":
    threading.Thread(target=email_reminder_worker, daemon=True).start()

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
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        threading.Thread(target=email_reminder_worker, daemon=True).start()

    app.run(host="0.0.0.0", port=port, debug=True, threaded=True, ssl_context=ssl_context)
