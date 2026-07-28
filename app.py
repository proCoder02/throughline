
# import os
# import re
# import json
# import sqlite3
# import threading
# import time
# import smtplib
# import uuid
# from email.mime.multipart import MIMEMultipart
# from email.mime.text import MIMEText
# from email.mime.base import MIMEBase
# from email import encoders
# from datetime import datetime, timezone, timedelta
# from functools import wraps

# from flask import Flask, jsonify, render_template, request, g, session
# from flask_sock import Sock
# import requests
# import websocket as ws_client  # websocket-client package
# from dotenv import load_dotenv
# from werkzeug.security import generate_password_hash, check_password_hash

# load_dotenv()

# app = Flask(__name__)
# sock = Sock(app)
# # Set a real, stable secret in your .env for anything beyond local POC use:
# # FLASK_SECRET_KEY=<a long random string>
# app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")

# DB_PATH = os.path.join(os.path.dirname(__file__), "conversations.db")


# # ---------------------------------------------------------------------------
# # Database helpers
# # ---------------------------------------------------------------------------

# def get_db():
#     if "db" not in g:
#         g.db = sqlite3.connect(DB_PATH)
#         g.db.row_factory = sqlite3.Row
#         # WAL mode lets readers and a writer work concurrently instead of
#         # locking the whole file on every write -- meaningfully better
#         # behavior under concurrent users while still being plain SQLite.
#         g.db.execute("PRAGMA journal_mode=WAL")
#         g.db.execute("PRAGMA foreign_keys=ON")
#     return g.db


# @app.teardown_appcontext
# def close_db(exception=None):
#     db = g.pop("db", None)
#     if db is not None:
#         db.close()


# def init_db():
#     conn = sqlite3.connect(DB_PATH)
#     conn.execute("PRAGMA journal_mode=WAL")
#     conn.executescript(
#         """
#         CREATE TABLE IF NOT EXISTS users (
#             id INTEGER PRIMARY KEY AUTOINCREMENT,
#             username TEXT NOT NULL UNIQUE,
#             email TEXT,
#             password_hash TEXT NOT NULL,
#             created_at TEXT NOT NULL
#         );

#         CREATE TABLE IF NOT EXISTS conversations (
#             id INTEGER PRIMARY KEY AUTOINCREMENT,
#             user_id INTEGER NOT NULL,
#             created_at TEXT NOT NULL,
#             title TEXT,
#             raw_transcript TEXT NOT NULL,
#             FOREIGN KEY (user_id) REFERENCES users (id)
#         );

#         CREATE TABLE IF NOT EXISTS tasks (
#             id INTEGER PRIMARY KEY AUTOINCREMENT,
#             user_id INTEGER NOT NULL,
#             conversation_id INTEGER,
#             description TEXT NOT NULL,
#             owner TEXT,
#             due_date TEXT,
#             reminder_at TEXT,
#             reminder_sent INTEGER NOT NULL DEFAULT 0,
#             status TEXT NOT NULL DEFAULT 'open',
#             created_at TEXT NOT NULL,
#             FOREIGN KEY (user_id) REFERENCES users (id),
#             FOREIGN KEY (conversation_id) REFERENCES conversations (id)
#         );

#         CREATE TABLE IF NOT EXISTS personality_notes (
#             id INTEGER PRIMARY KEY AUTOINCREMENT,
#             user_id INTEGER NOT NULL,
#             conversation_id INTEGER,
#             speaker_label TEXT NOT NULL,
#             observation TEXT NOT NULL,
#             created_at TEXT NOT NULL,
#             FOREIGN KEY (user_id) REFERENCES users (id),
#             FOREIGN KEY (conversation_id) REFERENCES conversations (id)
#         );

#         CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations (user_id);
#         CREATE INDEX IF NOT EXISTS idx_tasks_user ON tasks (user_id);
#         CREATE INDEX IF NOT EXISTS idx_notes_user ON personality_notes (user_id);
#         """
#     )
#     conn.commit()

#     # Auto-migrate: add any columns that don't exist yet on an older database
#     # file, so schema changes don't require a manual migration script.
#     _ensure_column(conn, "tasks", "reminder_at", "TEXT")
#     _ensure_column(conn, "tasks", "reminder_sent", "INTEGER NOT NULL DEFAULT 0")
#     _ensure_column(conn, "tasks", "email_sent", "INTEGER NOT NULL DEFAULT 0")
#     _ensure_column(conn, "users", "email", "TEXT")

#     conn.close()


# def _ensure_column(conn, table, column, coltype):
#     existing = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
#     if column not in existing:
#         conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
#         conn.commit()


# def now_iso():
#     return datetime.now(timezone.utc).isoformat()


# # ---------------------------------------------------------------------------
# # Music vs. speech classification (YAMNet)
# #
# # YAMNet is a pretrained audio-event classifier (521 AudioSet classes) run
# # via TensorFlow Hub. All heavy imports (tensorflow, tensorflow_hub, av) are
# # deferred into the functions that use them -- if the dependency isn't
# # installed or the model fails to load, this feature silently no-ops
# # instead of crashing the listening session.
# # ---------------------------------------------------------------------------

# _yamnet_model = None
# _yamnet_class_names = None
# _yamnet_lock = threading.Lock()

# # YAMNet's 521 classes are fine-grained ("Ukulele", "Child speech", etc.) --
# # grouping them into these two coarse buckets by keyword avoids depending on
# # one exact class firing.
# MUSIC_CLASS_KEYWORDS = (
#     "music", "singing", "musical instrument", "guitar", "piano", "drum",
#     "song", "orchestra", "band", "violin", "flute", "trumpet",
# )
# SPEECH_CLASS_KEYWORDS = (
#     "speech", "conversation", "narration", "monologue",
#     "child speech", "male speech", "female speech",
# )

# AUDIO_CLASSIFY_INTERVAL_SECONDS = float(os.getenv("AUDIO_CLASSIFY_INTERVAL_SECONDS", "5"))
# AUDIO_CLASSIFY_TAIL_SECONDS = float(os.getenv("AUDIO_CLASSIFY_TAIL_SECONDS", "3"))


# def get_yamnet_model():
#     """Lazily loads YAMNet once per process. First call downloads the model
#     from TensorFlow Hub and caches it locally (TFHUB_CACHE_DIR) for next time."""
#     global _yamnet_model, _yamnet_class_names
#     if _yamnet_model is None:
#         with _yamnet_lock:
#             if _yamnet_model is None:
#                 import csv
#                 import tensorflow_hub as hub
#                 print("[audio-classify] Loading YAMNet (first run downloads it, cached after)...")
#                 _yamnet_model = hub.load("https://tfhub.dev/google/yamnet/1")
#                 class_map_path = _yamnet_model.class_map_path().numpy().decode("utf-8")
#                 with open(class_map_path) as f:
#                     _yamnet_class_names = [row["display_name"] for row in csv.DictReader(f)]
#                 print("[audio-classify] YAMNet loaded.")
#     return _yamnet_model, _yamnet_class_names


# def decode_webm_to_waveform(raw_bytes, target_sr=16000):
#     """Decodes accumulated webm/opus bytes (what the browser's MediaRecorder
#     produces) into a mono float32 waveform at the sample rate YAMNet expects.
#     Uses PyAV, which bundles its own ffmpeg libs -- no separate ffmpeg
#     install needed on the host machine."""
#     import io
#     import av
#     import numpy as np

#     container = av.open(io.BytesIO(raw_bytes))
#     resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=target_sr)
#     chunks = []
#     for frame in container.decode(audio=0):
#         frame.pts = None
#         for resampled in resampler.resample(frame):
#             chunks.append(resampled.to_ndarray())
#     container.close()
#     if not chunks:
#         return None
#     audio = np.concatenate(chunks, axis=1).flatten().astype(np.float32) / 32768.0
#     return audio, target_sr


# def classify_audio(raw_audio_bytes, tail_seconds=AUDIO_CLASSIFY_TAIL_SECONDS):
#     """Classifies the trailing N seconds of session audio as 'music',
#     'speech', or 'unclear'. Returns None on any failure (missing deps,
#     decode error, not enough audio yet) rather than raising."""
#     try:
#         decoded = decode_webm_to_waveform(raw_audio_bytes)
#         if decoded is None:
#             return None
#         audio, sr = decoded
#         tail_samples = int(tail_seconds * sr)
#         if len(audio) > tail_samples:
#             audio = audio[-tail_samples:]
#         if len(audio) < sr * 1.0:  # need at least ~1s for a stable read
#             return None

#         model, class_names = get_yamnet_model()
#         scores, _embeddings, _spectrogram = model(audio)
#         mean_scores = scores.numpy().mean(axis=0)
#         top_indices = mean_scores.argsort()[-5:][::-1]
#         top_labels = [(class_names[i], float(mean_scores[i])) for i in top_indices]

#         music_score = sum(s for label, s in top_labels if any(k in label.lower() for k in MUSIC_CLASS_KEYWORDS))
#         speech_score = sum(s for label, s in top_labels if any(k in label.lower() for k in SPEECH_CLASS_KEYWORDS))

#         if music_score > speech_score and music_score > 0.15:
#             return "music"
#         if speech_score >= music_score and speech_score > 0.1:
#             return "speech"
#         return "unclear"
#     except Exception as e:
#         print(f"[audio-classify] Failed (skipping this check): {e!r}")
#         return None


# def normalize_reminder_at(raw):
#     """The LLM resolves relative dates ("Friday") against the server's local
#     time but returns a naive string with no timezone. Attach the server's
#     local offset and convert to UTC here, once, so every downstream
#     consumer -- browser notification comparisons, calendar links, the email
#     scheduler -- works of an unambiguous UTC timestamp instead of each
#     guessing the timezone independently."""
#     if not raw:
#         return None
#     try:
#         naive = datetime.fromisoformat(raw)
#     except ValueError:
#         return None
#     local_tz = datetime.now().astimezone().tzinfo
#     aware_local = naive.replace(tzinfo=local_tz)
#     return aware_local.astimezone(timezone.utc).isoformat()


# def build_ics(summary, description, start_utc, duration_minutes=30):
#     """Minimal RFC 5545 VEVENT. Attaching this to the reminder email lets
#     Gmail, Outlook, and Apple Mail all offer to add it directly to whichever
#     calendar the person actually uses -- no Google/Microsoft API needed."""
#     end_utc = start_utc + timedelta(minutes=duration_minutes)
#     fmt = lambda dt: dt.strftime("%Y%m%dT%H%M%SZ")
#     lines = [
#         "BEGIN:VCALENDAR",
#         "VERSION:2.0",
#         "PRODID:-//Throughline//Task Reminder//EN",
#         "BEGIN:VEVENT",
#         f"UID:{uuid.uuid4()}@throughline",
#         f"DTSTAMP:{fmt(datetime.now(timezone.utc))}",
#         f"DTSTART:{fmt(start_utc)}",
#         f"DTEND:{fmt(end_utc)}",
#         f"SUMMARY:{summary}",
#         f"DESCRIPTION:{description}",
#         "END:VEVENT",
#         "END:VCALENDAR",
#     ]
#     return "\r\n".join(lines)


# def send_reminder_email(to_email, description, owner, due_date, reminder_at_utc_iso):
#     """Sends the reminder over SMTP with an .ics attachment. POC note: From
#     and To are both set to the user's own registered email, per how this is
#     being tested -- register using the same address configured as
#     SMTP_USERNAME so the mail is coming from (and going to) one real inbox.
#     Some providers (Gmail included) may rewrite or reject a From header that
#     doesn't match the authenticated SMTP account; for anything beyond a POC,
#     send through a transactional provider (SES/SendGrid/Mailgun) instead."""
#     smtp_host = os.getenv("SMTP_HOST")
#     smtp_port = int(os.getenv("SMTP_PORT", "587"))
#     smtp_username = os.getenv("SMTP_USERNAME")
#     smtp_password = os.getenv("SMTP_PASSWORD")
#     if not all([smtp_host, smtp_username, smtp_password]):
#         print("[email] SMTP not configured (SMTP_HOST/SMTP_USERNAME/SMTP_PASSWORD) -- skipping")
#         return False

#     try:
#         start_utc = datetime.fromisoformat(reminder_at_utc_iso)
#     except ValueError:
#         return False

#     ics_content = build_ics(
#         description,
#         f"Owner: {owner or 'unspecified'} | Due: {due_date or 'not specified'}",
#         start_utc,
#     )

#     msg = MIMEMultipart()
#     msg["From"] = to_email
#     msg["To"] = to_email
#     msg["Subject"] = f"Reminder: {description}"
#     body = (
#         f"This is a reminder for a task extracted from your conversation:\n\n"
#         f"{description}\n\nOwner: {owner or 'unspecified'}\nDue: {due_date or 'not specified'}\n\n"
#         f"An .ics file is attached -- open it to add this to Google Calendar, Outlook, or Apple Calendar."
#     )
#     msg.attach(MIMEText(body, "plain"))

#     ics_part = MIMEBase("text", "calendar", method="REQUEST", name="reminder.ics")
#     ics_part.set_payload(ics_content)
#     encoders.encode_base64(ics_part)
#     ics_part.add_header("Content-Disposition", "attachment", filename="reminder.ics")
#     msg.attach(ics_part)

#     try:
#         with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
#             server.starttls()
#             server.login(smtp_username, smtp_password)
#             server.sendmail(smtp_username, [to_email], msg.as_string())
#         return True
#     except Exception as e:
#         print(f"[email] Failed to send reminder email to {to_email}: {e!r}")
#         return False


# def email_reminder_worker():
#     """Runs continuously in a background thread, independent of any browser
#     tab -- this is what makes email reminders work even if the app isn't
#     open anywhere, unlike the browser-notification poll."""
#     while True:
#         try:
#             conn = sqlite3.connect(DB_PATH)
#             conn.row_factory = sqlite3.Row
#             now = datetime.now(timezone.utc).isoformat()
#             rows = conn.execute(
#                 "SELECT tasks.id, tasks.description, tasks.owner, tasks.due_date, "
#                 "tasks.reminder_at, users.email FROM tasks "
#                 "JOIN users ON tasks.user_id = users.id "
#                 "WHERE tasks.reminder_at IS NOT NULL AND tasks.email_sent = 0 "
#                 "AND tasks.reminder_at <= ? AND users.email IS NOT NULL AND users.email != ''",
#                 (now,),
#             ).fetchall()
#             for row in rows:
#                 sent = send_reminder_email(
#                     row["email"], row["description"], row["owner"], row["due_date"], row["reminder_at"]
#                 )
#                 if sent:
#                     conn.execute("UPDATE tasks SET email_sent = 1 WHERE id = ?", (row["id"],))
#                     conn.commit()
#             conn.close()
#         except Exception as e:
#             print(f"[email_reminder_worker] error: {e!r}")
#         time.sleep(60)


# # ---------------------------------------------------------------------------
# # Auth helpers
# # ---------------------------------------------------------------------------

# def login_required(view_func):
#     @wraps(view_func)
#     def wrapped(*args, **kwargs):
#         if not session.get("user_id"):
#             return jsonify({"error": "Not authenticated"}), 401
#         return view_func(*args, **kwargs)
#     return wrapped


# def current_user_id():
#     return session.get("user_id")


# @app.route("/register", methods=["POST"])
# def register():
#     data = request.get_json(silent=True) or {}
#     username = (data.get("username") or "").strip()
#     email = (data.get("email") or "").strip()
#     password = data.get("password") or ""

#     if not username or not password:
#         return jsonify({"error": "Username and password are required"}), 400
#     if not email or "@" not in email or "." not in email.split("@")[-1]:
#         return jsonify({"error": "A valid email is required (used for reminder emails)"}), 400
#     if len(password) < 6:
#         return jsonify({"error": "Password must be at least 6 characters"}), 400

#     db = get_db()
#     existing = db.execute(
#         "SELECT id FROM users WHERE username = ?", (username,)
#     ).fetchone()
#     if existing:
#         return jsonify({"error": "Username is already taken"}), 409

#     password_hash = generate_password_hash(password)
#     cur = db.execute(
#         "INSERT INTO users (username, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
#         (username, email, password_hash, now_iso()),
#     )
#     db.commit()

#     session["user_id"] = cur.lastrowid
#     session["username"] = username
#     return jsonify({"id": cur.lastrowid, "username": username, "email": email})


# @app.route("/login", methods=["POST"])
# def login():
#     data = request.get_json(silent=True) or {}
#     username = (data.get("username") or "").strip()
#     password = data.get("password") or ""

#     db = get_db()
#     row = db.execute(
#         "SELECT id, username, password_hash FROM users WHERE username = ?",
#         (username,),
#     ).fetchone()

#     if not row or not check_password_hash(row["password_hash"], password):
#         return jsonify({"error": "Invalid username or password"}), 401

#     session["user_id"] = row["id"]
#     session["username"] = row["username"]
#     return jsonify({"id": row["id"], "username": row["username"]})


# @app.route("/logout", methods=["POST"])
# def logout():
#     session.clear()
#     return jsonify({"ok": True})


# @app.route("/me", methods=["GET"])
# def me():
#     if not session.get("user_id"):
#         return jsonify({"error": "Not authenticated"}), 401
#     return jsonify({"id": session["user_id"], "username": session.get("username")})


# # ---------------------------------------------------------------------------
# # Speaker parsing
# # ---------------------------------------------------------------------------

# SPEAKER_LINE_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 _'-]{0,30}):\s+(.*)$")


# def parse_speakers(raw_text):
#     """Split transcript into (speaker_label, line) pairs.

#     Convention: lines prefixed with "Name: ..." are attributed to that
#     speaker. Lines with no recognizable prefix are attributed to 'Unknown'.
#     """
#     segments = []
#     for line in raw_text.splitlines():
#         line = line.strip()
#         if not line:
#             continue
#         match = SPEAKER_LINE_RE.match(line)
#         if match:
#             speaker, text = match.group(1).strip(), match.group(2).strip()
#             segments.append((speaker, text))
#         else:
#             segments.append(("Unknown", line))
#     return segments


# # ---------------------------------------------------------------------------
# # LLM call
# # ---------------------------------------------------------------------------

# def call_llm(messages):
#     api_key = os.getenv("OLLAMA_API_KEY") or os.getenv("olama_api_key")
#     if not api_key:
#         raise RuntimeError("Missing Ollama API key")

#     # Ollama Cloud bills by GPU-time/usage-level, not strict token count --
#     # lighter models (e.g. gpt-oss:20b) sit in a cheaper usage tier than
#     # gpt-oss:120b. Override via .env to test cost/quality tradeoff without
#     # touching code: OLLAMA_MODEL=gpt-oss:20b
#     payload = {
#         "model": os.getenv("OLLAMA_MODEL", "gpt-oss:120b"),
#         "messages": messages,
#         "stream": False,
#     }

#     response = requests.post(
#         "https://ollama.com/api/chat",
#         headers={
#             "Authorization": f"Bearer {api_key}",
#             "Content-Type": "application/json",
#         },
#         json=payload,
#         timeout=90,
#     )
#     response.raise_for_status()
#     result = response.json()
#     return result.get("message", {}).get("content", "")


# def extract_json(text):
#     """Best-effort extraction of a JSON object from an LLM text response."""
#     text = text.strip()
#     text = re.sub(r"^```json\s*|^```\s*|```$", "", text, flags=re.MULTILINE).strip()
#     try:
#         return json.loads(text)
#     except ValueError:
#         pass
#     match = re.search(r"\{.*\}", text, re.DOTALL)
#     if match:
#         try:
#             return json.loads(match.group(0))
#         except ValueError:
#             return None
#     return None


# # Cost knobs: larger batch = fewer Ollama calls (less repeated system-prompt
# # overhead); higher word floor = skip calls on trivially short/filler batches.
# BACKGROUND_ANALYSIS_BATCH_SIZE = int(os.getenv("BACKGROUND_ANALYSIS_BATCH_SIZE", "12"))
# MIN_WORDS_FOR_ANALYSIS = int(os.getenv("MIN_WORDS_FOR_ANALYSIS", "8"))

# def build_analysis_prompt():
#     # Injecting the real current time lets the model resolve relative phrases
#     # ("Friday", "tomorrow", "in an hour") into an actual timestamp we can
#     # schedule a browser reminder against -- without this, "due_date" is just
#     # a display string with nothing a scheduler could act on.
#     now_local = datetime.now().astimezone()
#     return f"""Today is {now_local.strftime('%A, %Y-%m-%d')}, current time {now_local.strftime('%H:%M %Z')}.

# Extract from this conversation transcript:
# 1. "tasks": action items/commitments. Each:
#    - description (<20 words, your own words)
#    - owner (if stated/implied, else null)
#    - due_date: short human phrase as said/implied (e.g. "Friday", "tonight"), else null
#    - reminder_at: ONLY if a specific date and/or time is stated or clearly implied, resolve it \
# against today's date above and return exact ISO 8601 "YYYY-MM-DDTHH:MM:SS" (assume 09:00:00 if a \
# day is given with no time). Else null. Do not guess if nothing time-related was said.
# 2. "speakers": per speaker label, up to 3 short behavioral observations grounded only in what \
# they said/how they said it (e.g. "proposed the deadline", "hedged twice"). No diagnoses or \
# clinical/mental-health terms, no motive speculation.

# Reply with ONLY this JSON, no preamble/fences:
# {{"tasks": [{{"description": "", "owner": null, "due_date": null, "reminder_at": null}}], \
# "speakers": [{{"label": "", "observations": [""]}}]}}
# Empty lists if none found."""


# # ---------------------------------------------------------------------------
# # Routes
# # ---------------------------------------------------------------------------

# @app.route("/")
# def index():
#     return render_template("index.html")


# @app.route("/transcribe", methods=["POST"])
# @login_required
# def transcribe_audio():
#     """Send a recorded audio clip to Deepgram for diarized transcription and
#     return a speaker-labeled transcript in the "Name: text" convention used
#     elsewhere in this app. Deepgram handles concurrent requests from many
#     users on its own infrastructure -- nothing to scale on our side."""
#     if "audio" not in request.files:
#         return jsonify({"error": "No audio file uploaded"}), 400

#     api_key = os.getenv("DEEPGRAM_API_KEY")
#     if not api_key:
#         return jsonify({"error": "Missing DEEPGRAM_API_KEY"}), 500

#     audio_file = request.files["audio"]
#     audio_bytes = audio_file.read()
#     content_type = audio_file.mimetype or "audio/webm"

#     try:
#         resp = requests.post(
#             "https://api.deepgram.com/v1/listen",
#             params={
#                 "diarize": "true",
#                 "punctuate": "true",
#                 "utterances": "true",
#                 "model": "nova-2",
#             },
#             headers={
#                 "Authorization": f"Token {api_key}",
#                 "Content-Type": content_type,
#             },
#             data=audio_bytes,
#             timeout=120,
#         )
#         resp.raise_for_status()
#     except requests.HTTPError as e:
#         status = e.response.status_code if e.response is not None else 500
#         return jsonify({"error": f"Deepgram error: {e}"}), status
#     except requests.RequestException as e:
#         return jsonify({"error": f"Could not reach Deepgram: {e}"}), 502

#     data = resp.json()
#     utterances = (data.get("results") or {}).get("utterances") or []

#     lines = []
#     segments = []
#     for utt in utterances:
#         speaker_label = f"Speaker {utt.get('speaker', 0)}"
#         text = (utt.get("transcript") or "").strip()
#         if not text:
#             continue
#         lines.append(f"{speaker_label}: {text}")
#         segments.append({
#             "speaker": speaker_label,
#             "text": text,
#             "start": utt.get("start"),
#             "end": utt.get("end"),
#         })

#     transcript_text = "\n".join(lines)
#     return jsonify({"transcript": transcript_text, "segments": segments})


# @app.route("/save", methods=["POST"])
# @login_required
# def save_conversation():
#     data = request.get_json(silent=True) or {}
#     raw_transcript = (data.get("transcript") or "").strip()
#     title = (data.get("title") or "").strip() or None

#     if not raw_transcript:
#         return jsonify({"error": "Transcript is required"}), 400

#     db = get_db()
#     cur = db.execute(
#         "INSERT INTO conversations (user_id, created_at, title, raw_transcript) "
#         "VALUES (?, ?, ?, ?)",
#         (current_user_id(), now_iso(), title, raw_transcript),
#     )
#     db.commit()
#     return jsonify({"conversation_id": cur.lastrowid})


# def run_background_analysis(user_id, conversation_id, delta_text, push):
#     """Runs in its own thread with its own sqlite connection -- Flask's
#     request-scoped `g` connection can't be shared across threads. Extracts
#     tasks + behavioral observations from just the new lines since the last
#     pass (not the whole conversation), then pushes a summary back over the
#     websocket so the UI can show it without the user clicking Analyze."""
#     if not delta_text.strip():
#         return
#     try:
#         content = call_llm([
#             {"role": "system", "content": build_analysis_prompt()},
#             {"role": "user", "content": delta_text},
#         ])
#     except Exception:
#         return

#     parsed = extract_json(content)
#     if not parsed:
#         return

#     tasks = parsed.get("tasks") or []
#     speakers = parsed.get("speakers") or []

#     conn = sqlite3.connect(DB_PATH)
#     try:
#         for task in tasks:
#             description = (task.get("description") or "").strip()
#             if not description:
#                 continue
#             conn.execute(
#                 "INSERT INTO tasks (user_id, conversation_id, description, owner, due_date, reminder_at, status, created_at) "
#                 "VALUES (?, ?, ?, ?, ?, ?, 'open', ?)",
#                 (user_id, conversation_id, description, task.get("owner"), task.get("due_date"),
#                  normalize_reminder_at(task.get("reminder_at")), now_iso()),
#             )
#         for speaker in speakers:
#             label = (speaker.get("label") or "").strip()
#             if not label:
#                 continue
#             for obs in speaker.get("observations") or []:
#                 obs = (obs or "").strip()
#                 if not obs:
#                     continue
#                 conn.execute(
#                     "INSERT INTO personality_notes (user_id, conversation_id, speaker_label, observation, created_at) "
#                     "VALUES (?, ?, ?, ?, ?)",
#                     (user_id, conversation_id, label, obs, now_iso()),
#                 )
#         conn.commit()
#     finally:
#         conn.close()

#     push({"type": "background_update", "tasks_found": len(tasks), "speakers_found": len(speakers)})


# @sock.route("/ws/listen")
# def ws_listen(ws):
#     """Live listening session: browser streams mic audio in over this
#     websocket, we relay it to Deepgram's streaming API, and stream
#     diarized transcript lines + new-speaker notices back to the browser.
#     Task/profile extraction runs automatically in the background -- no
#     separate Analyze click needed. A second background thread periodically
#     classifies the recent audio as music or speech via YAMNet and pushes a
#     notice to the frontend whenever that classification changes."""
#     user_id = session.get("user_id")
#     if not user_id:
#         ws.close()
#         return

#     api_key = os.getenv("DEEPGRAM_API_KEY")
#     if not api_key:
#         ws.send(json.dumps({"type": "error", "message": "Missing DEEPGRAM_API_KEY"}))
#         return

#     db = get_db()
#     cur = db.execute(
#         "INSERT INTO conversations (user_id, created_at, title, raw_transcript) VALUES (?, ?, ?, ?)",
#         (user_id, now_iso(), None, ""),
#     )
#     db.commit()
#     conversation_id = cur.lastrowid

#     dg_url = (
#         "wss://api.deepgram.com/v1/listen"
#         "?diarize=true&punctuate=true&interim_results=false&model=nova-2"
#     )
#     try:
#         dg_ws = ws_client.create_connection(dg_url, header=[f"Authorization: Token {api_key}"])
#     except Exception as e:
#         print(f"[ws_listen] Failed to connect to Deepgram: {e!r}")
#         ws.send(json.dumps({"type": "error", "message": f"Could not connect to Deepgram: {e}"}))
#         return

#     lock = threading.Lock()
#     state = {
#         "known_speakers": {},   # speaker index -> chosen name, or None if unnamed
#         "transcript_lines": [], # (speaker_index, "Label: text") for every final line
#         "pending_lines": [],    # lines not yet sent for background analysis
#         "audio_buffer": bytearray(),  # raw session audio, for music/speech classification
#         "last_audio_class": None,     # last pushed classification, to only notify on change
#         "stop": False,
#     }

#     def push(payload):
#         try:
#             ws.send(json.dumps(payload))
#         except Exception:
#             pass

#     def label_for(speaker_idx):
#         name = state["known_speakers"].get(speaker_idx)
#         return name if name else f"Speaker {speaker_idx}"

#     def receive_from_deepgram():
#         while not state["stop"]:
#             try:
#                 message = dg_ws.recv()
#             except Exception as e:
#                 print(f"[ws_listen] Deepgram recv stopped: {e!r}")
#                 break
#             if not message:
#                 continue
#             try:
#                 data = json.loads(message)
#             except ValueError:
#                 continue

#             if not data.get("is_final"):
#                 continue
#             alt = ((data.get("channel") or {}).get("alternatives") or [None])[0]
#             if not alt:
#                 continue
#             text = (alt.get("transcript") or "").strip()
#             if not text:
#                 continue
#             words = alt.get("words") or []
#             speaker_idx = words[0].get("speaker", 0) if words else 0

#             with lock:
#                 is_new_speaker = speaker_idx not in state["known_speakers"]
#                 if is_new_speaker:
#                     state["known_speakers"][speaker_idx] = None
#                 line = f"{label_for(speaker_idx)}: {text}"
#                 state["transcript_lines"].append((speaker_idx, line))
#                 state["pending_lines"].append(line)
#                 # Batching more lines per call means fewer Ollama calls overall,
#                 # and since the system prompt is billed on every call, fewer
#                 # calls = less repeated overhead for the same coverage.
#                 should_analyze = len(state["pending_lines"]) >= BACKGROUND_ANALYSIS_BATCH_SIZE
#                 delta_text = None
#                 if should_analyze:
#                     candidate = "\n".join(state["pending_lines"])
#                     # Skip calling the LLM on batches that are almost
#                     # certainly just filler ("yeah", "okay", "mm-hmm") --
#                     # not worth a full call.
#                     if len(candidate.split()) >= MIN_WORDS_FOR_ANALYSIS:
#                         delta_text = candidate
#                     state["pending_lines"] = []

#             push({
#                 "type": "transcript",
#                 "speaker_index": speaker_idx,
#                 "line": line,
#             })
#             if is_new_speaker:
#                 push({"type": "new_speaker", "speaker_index": speaker_idx})
#             if delta_text:
#                 threading.Thread(
#                     target=run_background_analysis,
#                     args=(user_id, conversation_id, delta_text, push),
#                     daemon=True,
#                 ).start()

#     def audio_classifier_worker():
#         """Periodically checks whether the recent audio sounds like music or
#         speech, and pushes a notice to the frontend only when that changes
#         -- so this doesn't spam a popup every few seconds during a normal
#         conversation, only when something actually shifts."""
#         while not state["stop"]:
#             time.sleep(AUDIO_CLASSIFY_INTERVAL_SECONDS)
#             if state["stop"]:
#                 break
#             with lock:
#                 snapshot = bytes(state["audio_buffer"])
#             if not snapshot:
#                 continue
#             label = classify_audio(snapshot)
#             if label and label != state.get("last_audio_class"):
#                 state["last_audio_class"] = label
#                 push({"type": "audio_classification", "label": label})

#     threading.Thread(target=receive_from_deepgram, daemon=True).start()
#     threading.Thread(target=audio_classifier_worker, daemon=True).start()

#     try:
#         while True:
#             chunk = ws.receive()
#             if chunk is None:
#                 break
#             if isinstance(chunk, str):
#                 try:
#                     msg = json.loads(chunk)
#                 except ValueError:
#                     continue
#                 if msg.get("type") == "rename_speaker":
#                     idx = msg.get("speaker_index")
#                     name = (msg.get("name") or "").strip()
#                     if name:
#                         with lock:
#                             state["known_speakers"][idx] = name
#                         push({"type": "speaker_renamed", "speaker_index": idx, "name": name})
#                 continue
#             try:
#                 dg_ws.send_binary(chunk)
#                 with lock:
#                     state["audio_buffer"].extend(chunk)
#                     # Cap buffer growth on long sessions -- keep roughly the
#                     # last couple minutes, plenty for trailing-window
#                     # classification without memory growing unbounded.
#                     max_bytes = 2_000_000
#                     if len(state["audio_buffer"]) > max_bytes:
#                         del state["audio_buffer"][: len(state["audio_buffer"]) - max_bytes]
#             except Exception:
#                 break
#     finally:
#         state["stop"] = True
#         try:
#             dg_ws.close()
#         except Exception:
#             pass

#         with lock:
#             # Re-label every stored line with whatever name each speaker
#             # ended up with, even ones spoken before they were named.
#             final_lines = [
#                 f"{label_for(idx)}: {line.split(': ', 1)[1]}"
#                 for idx, line in state["transcript_lines"]
#             ]
#             leftover = "\n".join(state["pending_lines"])

#         if final_lines:
#             conn = sqlite3.connect(DB_PATH)
#             conn.execute(
#                 "UPDATE conversations SET raw_transcript = ? WHERE id = ?",
#                 ("\n".join(final_lines), conversation_id),
#             )
#             conn.commit()
#             conn.close()
#             if leftover.strip():
#                 threading.Thread(
#                     target=run_background_analysis,
#                     args=(user_id, conversation_id, leftover, push),
#                     daemon=True,
#                 ).start()


# @app.route("/analyze", methods=["POST"])
# @login_required
# def analyze_conversation():
#     data = request.get_json(silent=True) or {}
#     conversation_id = data.get("conversation_id")
#     raw_transcript = (data.get("transcript") or "").strip()
#     user_id = current_user_id()

#     db = get_db()

#     if conversation_id:
#         row = db.execute(
#             "SELECT * FROM conversations WHERE id = ? AND user_id = ?",
#             (conversation_id, user_id),
#         ).fetchone()
#         if not row:
#             return jsonify({"error": "Conversation not found"}), 404
#         raw_transcript = row["raw_transcript"]
#     elif raw_transcript:
#         cur = db.execute(
#             "INSERT INTO conversations (user_id, created_at, title, raw_transcript) "
#             "VALUES (?, ?, ?, ?)",
#             (user_id, now_iso(), None, raw_transcript),
#         )
#         db.commit()
#         conversation_id = cur.lastrowid
#     else:
#         return jsonify({"error": "transcript or conversation_id is required"}), 400

#     try:
#         content = call_llm(
#             [
#                 {"role": "system", "content": build_analysis_prompt()},
#                 {"role": "user", "content": raw_transcript},
#             ]
#         )
#     except requests.HTTPError as e:
#         status = e.response.status_code if e.response is not None else 500
#         if status == 429:
#             return jsonify({
#                 "error": "Ollama quota is currently exhausted or rate limited. Please try again later."
#             }), 429
#         return jsonify({"error": str(e)}), status
#     except RuntimeError as e:
#         return jsonify({"error": str(e)}), 500

#     parsed = extract_json(content)
#     if parsed is None:
#         return jsonify({"error": "Could not parse analysis result", "raw": content}), 502

#     tasks = parsed.get("tasks") or []
#     speakers = parsed.get("speakers") or []

#     for task in tasks:
#         description = (task.get("description") or "").strip()
#         if not description:
#             continue
#         db.execute(
#             "INSERT INTO tasks (user_id, conversation_id, description, owner, due_date, reminder_at, status, created_at) "
#             "VALUES (?, ?, ?, ?, ?, ?, 'open', ?)",
#             (user_id, conversation_id, description, task.get("owner"), task.get("due_date"),
#              normalize_reminder_at(task.get("reminder_at")), now_iso()),
#         )

#     for speaker in speakers:
#         label = (speaker.get("label") or "").strip()
#         if not label:
#             continue
#         for obs in speaker.get("observations") or []:
#             obs = (obs or "").strip()
#             if not obs:
#                 continue
#             db.execute(
#                 "INSERT INTO personality_notes (user_id, conversation_id, speaker_label, observation, created_at) "
#                 "VALUES (?, ?, ?, ?, ?)",
#                 (user_id, conversation_id, label, obs, now_iso()),
#             )

#     db.commit()

#     return jsonify(
#         {
#             "conversation_id": conversation_id,
#             "tasks": tasks,
#             "speakers": speakers,
#         }
#     )


# @app.route("/tasks", methods=["GET"])
# @login_required
# def list_tasks():
#     status = request.args.get("status", "open")
#     user_id = current_user_id()
#     db = get_db()
#     if status == "all":
#         rows = db.execute(
#             "SELECT * FROM tasks WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
#         ).fetchall()
#     else:
#         rows = db.execute(
#             "SELECT * FROM tasks WHERE user_id = ? AND status = ? ORDER BY created_at DESC",
#             (user_id, status),
#         ).fetchall()
#     return jsonify([dict(r) for r in rows])


# @app.route("/tasks/<int:task_id>/complete", methods=["POST"])
# @login_required
# def complete_task(task_id):
#     db = get_db()
#     row = db.execute(
#         "SELECT id FROM tasks WHERE id = ? AND user_id = ?", (task_id, current_user_id())
#     ).fetchone()
#     if not row:
#         return jsonify({"error": "Task not found"}), 404
#     db.execute("UPDATE tasks SET status = 'done' WHERE id = ?", (task_id,))
#     db.commit()
#     return jsonify({"ok": True})


# @app.route("/tasks/<int:task_id>/reopen", methods=["POST"])
# @login_required
# def reopen_task(task_id):
#     db = get_db()
#     row = db.execute(
#         "SELECT id FROM tasks WHERE id = ? AND user_id = ?", (task_id, current_user_id())
#     ).fetchone()
#     if not row:
#         return jsonify({"error": "Task not found"}), 404
#     db.execute("UPDATE tasks SET status = 'open' WHERE id = ?", (task_id,))
#     db.commit()
#     return jsonify({"ok": True})


# @app.route("/tasks/<int:task_id>/mark_reminded", methods=["POST"])
# @login_required
# def mark_reminded(task_id):
#     db = get_db()
#     row = db.execute(
#         "SELECT id FROM tasks WHERE id = ? AND user_id = ?", (task_id, current_user_id())
#     ).fetchone()
#     if not row:
#         return jsonify({"error": "Task not found"}), 404
#     db.execute("UPDATE tasks SET reminder_sent = 1 WHERE id = ?", (task_id,))
#     db.commit()
#     return jsonify({"ok": True})


# @app.route("/profiles", methods=["GET"])
# @login_required
# def list_profiles():
#     db = get_db()
#     rows = db.execute(
#         "SELECT speaker_label, observation, created_at, conversation_id "
#         "FROM personality_notes WHERE user_id = ? ORDER BY speaker_label, created_at DESC",
#         (current_user_id(),),
#     ).fetchall()

#     profiles = {}
#     for r in rows:
#         label = r["speaker_label"]
#         profiles.setdefault(label, []).append(
#             {
#                 "observation": r["observation"],
#                 "created_at": r["created_at"],
#                 "conversation_id": r["conversation_id"],
#             }
#         )
#     return jsonify(profiles)


# @app.route("/chat", methods=["POST"])
# @login_required
# def chat():
#     data = request.get_json(silent=True) or {}
#     prompt = (data.get("prompt") or "").strip()
#     transcript = (data.get("transcript") or "").strip()
#     uploaded_text = (data.get("uploadedText") or "").strip()
#     conversation_id = data.get("conversation_id")
#     user_id = current_user_id()

#     if not prompt:
#         return jsonify({"error": "Prompt is required"}), 400

#     parts = []

#     # Keep chat context bounded to the most recent portion of the
#     # conversation rather than sending the whole (potentially long-running)
#     # transcript on every single chat message -- otherwise cost per chat
#     # message grows the longer the conversation runs.
#     MAX_CONTEXT_CHARS = 4000
#     def recent(text):
#         return text[-MAX_CONTEXT_CHARS:] if len(text) > MAX_CONTEXT_CHARS else text

#     if conversation_id:
#         db = get_db()
#         row = db.execute(
#             "SELECT raw_transcript FROM conversations WHERE id = ? AND user_id = ?",
#             (conversation_id, user_id),
#         ).fetchone()
#         if row:
#             parts.append(f"Saved conversation transcript (most recent portion):\n{recent(row['raw_transcript'])}")

#     if transcript:
#         parts.append(f"Live transcript from speech (most recent portion):\n{recent(transcript)}")
#     if uploaded_text:
#         parts.append(f"Uploaded text:\n{uploaded_text}")
#     parts.append(f"User question:\n{prompt}")

#     try:
#         reply = call_llm([{"role": "user", "content": "\n\n".join(parts)}])
#     except requests.HTTPError as e:
#         status = e.response.status_code if e.response is not None else 500
#         if status == 429:
#             return jsonify({
#                 "reply": "Ollama quota is currently exhausted or rate limited. Please try again later or use a different API key."
#             })
#         try:
#             error_json = e.response.json()
#         except Exception:
#             error_json = {"error": str(e)}
#         return jsonify({"error": error_json}), status
#     except RuntimeError as e:
#         return jsonify({"error": str(e)}), 500

#     if not reply:
#         return jsonify({"reply": ""})
#     return jsonify({"reply": reply})


# init_db()

# if __name__ == "__main__":
#     # Mic access (getUserMedia) only works over "secure contexts" -- https,
#     # or localhost. Testing from other devices on your LAN needs https even
#     # with a self-signed cert; set USE_HTTPS=true in .env to turn it on.
#     # Each device will need to click through one browser warning the first
#     # time (the cert isn't from a trusted authority, but that's fine for
#     # local testing -- the browser still treats it as a secure context).
#     use_https = os.getenv("USE_HTTPS", "false").lower() == "true"
#     ssl_context = "adhoc" if use_https else None
#     port = int(os.getenv("PORT", "5000"))

#     # debug=True runs Flask's reloader, which re-executes this module in a
#     # child process -- WERKZEUG_RUN_MAIN is only set in that actual running
#     # child, so checking it avoids starting two copies of the email worker.
#     if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
#         threading.Thread(target=email_reminder_worker, daemon=True).start()

#     app.run(host="0.0.0.0", port=port, debug=True, threaded=True, ssl_context=ssl_context)

###########-------------- working below code---------------######

# import os
# import re
# import json
# import sqlite3
# import threading
# import time
# import smtplib
# import uuid
# from email.mime.multipart import MIMEMultipart
# from email.mime.text import MIMEText
# from email.mime.base import MIMEBase
# from email import encoders
# from datetime import datetime, timezone, timedelta
# from functools import wraps

# from flask import Flask, jsonify, render_template, request, g, session
# from flask_sock import Sock
# import requests
# import websocket as ws_client  # websocket-client package
# from dotenv import load_dotenv
# from werkzeug.security import generate_password_hash, check_password_hash

# load_dotenv()

# app = Flask(__name__)
# sock = Sock(app)
# # Set a real, stable secret in your .env for anything beyond local POC use:
# # FLASK_SECRET_KEY=<a long random string>
# app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")

# DB_PATH = os.path.join(os.path.dirname(__file__), "conversations.db")
# CHAT_HISTORY_DIR = os.path.join(os.path.dirname(__file__), "chat_history")
# os.makedirs(CHAT_HISTORY_DIR, exist_ok=True)


# # ---------------------------------------------------------------------------
# # Database helpers
# # ---------------------------------------------------------------------------

# def get_db():
#     if "db" not in g:
#         g.db = sqlite3.connect(DB_PATH)
#         g.db.row_factory = sqlite3.Row
#         # WAL mode lets readers and a writer work concurrently instead of
#         # locking the whole file on every write -- meaningfully better
#         # behavior under concurrent users while still being plain SQLite.
#         g.db.execute("PRAGMA journal_mode=WAL")
#         g.db.execute("PRAGMA foreign_keys=ON")
#     return g.db


# @app.teardown_appcontext
# def close_db(exception=None):
#     db = g.pop("db", None)
#     if db is not None:
#         db.close()


# def init_db():
#     conn = sqlite3.connect(DB_PATH)
#     conn.execute("PRAGMA journal_mode=WAL")
#     conn.executescript(
#         """
#         CREATE TABLE IF NOT EXISTS users (
#             id INTEGER PRIMARY KEY AUTOINCREMENT,
#             username TEXT NOT NULL UNIQUE,
#             email TEXT,
#             password_hash TEXT NOT NULL,
#             created_at TEXT NOT NULL
#         );

#         CREATE TABLE IF NOT EXISTS conversations (
#             id INTEGER PRIMARY KEY AUTOINCREMENT,
#             user_id INTEGER NOT NULL,
#             created_at TEXT NOT NULL,
#             title TEXT,
#             raw_transcript TEXT NOT NULL,
#             FOREIGN KEY (user_id) REFERENCES users (id)
#         );

#         CREATE TABLE IF NOT EXISTS tasks (
#             id INTEGER PRIMARY KEY AUTOINCREMENT,
#             user_id INTEGER NOT NULL,
#             conversation_id INTEGER,
#             description TEXT NOT NULL,
#             owner TEXT,
#             due_date TEXT,
#             reminder_at TEXT,
#             reminder_sent INTEGER NOT NULL DEFAULT 0,
#             status TEXT NOT NULL DEFAULT 'open',
#             created_at TEXT NOT NULL,
#             FOREIGN KEY (user_id) REFERENCES users (id),
#             FOREIGN KEY (conversation_id) REFERENCES conversations (id)
#         );

#         CREATE TABLE IF NOT EXISTS personality_notes (
#             id INTEGER PRIMARY KEY AUTOINCREMENT,
#             user_id INTEGER NOT NULL,
#             conversation_id INTEGER,
#             speaker_label TEXT NOT NULL,
#             observation TEXT NOT NULL,
#             created_at TEXT NOT NULL,
#             FOREIGN KEY (user_id) REFERENCES users (id),
#             FOREIGN KEY (conversation_id) REFERENCES conversations (id)
#         );

#         CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations (user_id);
#         CREATE INDEX IF NOT EXISTS idx_tasks_user ON tasks (user_id);
#         CREATE INDEX IF NOT EXISTS idx_notes_user ON personality_notes (user_id);
#         """
#     )
#     conn.commit()

#     # Auto-migrate: add any columns that don't exist yet on an older database
#     # file, so schema changes don't require a manual migration script.
#     _ensure_column(conn, "tasks", "reminder_at", "TEXT")
#     _ensure_column(conn, "tasks", "reminder_sent", "INTEGER NOT NULL DEFAULT 0")
#     _ensure_column(conn, "tasks", "email_sent", "INTEGER NOT NULL DEFAULT 0")
#     _ensure_column(conn, "users", "email", "TEXT")

#     conn.close()


# def _ensure_column(conn, table, column, coltype):
#     existing = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
#     if column not in existing:
#         conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
#         conn.commit()


# def now_iso():
#     return datetime.now(timezone.utc).isoformat()


# # ---------------------------------------------------------------------------
# # Music vs. speech classification (YAMNet)
# #
# # YAMNet is a pretrained audio-event classifier (521 AudioSet classes) run
# # via TensorFlow Hub. All heavy imports (tensorflow, tensorflow_hub, av) are
# # deferred into the functions that use them -- if the dependency isn't
# # installed or the model fails to load, this feature silently no-ops
# # instead of crashing the listening session.
# # ---------------------------------------------------------------------------

# _yamnet_model = None
# _yamnet_class_names = None
# _yamnet_lock = threading.Lock()

# # YAMNet's 521 classes are fine-grained ("Ukulele", "Child speech", etc.) --
# # grouping them into these two coarse buckets by keyword avoids depending on
# # one exact class firing.
# MUSIC_CLASS_KEYWORDS = (
#     "music", "singing", "musical instrument", "guitar", "piano", "drum",
#     "song", "orchestra", "band", "violin", "flute", "trumpet",
# )
# SPEECH_CLASS_KEYWORDS = (
#     "speech", "conversation", "narration", "monologue",
#     "child speech", "male speech", "female speech",
# )

# AUDIO_CLASSIFY_INTERVAL_SECONDS = float(os.getenv("AUDIO_CLASSIFY_INTERVAL_SECONDS", "5"))
# AUDIO_CLASSIFY_TAIL_SECONDS = float(os.getenv("AUDIO_CLASSIFY_TAIL_SECONDS", "3"))


# def get_yamnet_model():
#     """Lazily loads YAMNet once per process. First call downloads the model
#     from TensorFlow Hub and caches it locally (TFHUB_CACHE_DIR) for next time."""
#     global _yamnet_model, _yamnet_class_names
#     if _yamnet_model is None:
#         with _yamnet_lock:
#             if _yamnet_model is None:
#                 import csv
#                 import tensorflow_hub as hub
#                 print("[audio-classify] Loading YAMNet (first run downloads it, cached after)...")
#                 _yamnet_model = hub.load("https://tfhub.dev/google/yamnet/1")
#                 class_map_path = _yamnet_model.class_map_path().numpy().decode("utf-8")
#                 with open(class_map_path) as f:
#                     _yamnet_class_names = [row["display_name"] for row in csv.DictReader(f)]
#                 print("[audio-classify] YAMNet loaded.")
#     return _yamnet_model, _yamnet_class_names


# def decode_webm_to_waveform(raw_bytes, target_sr=16000):
#     """Decodes accumulated webm/opus bytes (what the browser's MediaRecorder
#     produces) into a mono float32 waveform at the sample rate YAMNet expects.
#     Uses PyAV, which bundles its own ffmpeg libs -- no separate ffmpeg
#     install needed on the host machine."""
#     import io
#     import av
#     import numpy as np

#     container = av.open(io.BytesIO(raw_bytes))
#     resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=target_sr)
#     chunks = []
#     for frame in container.decode(audio=0):
#         frame.pts = None
#         for resampled in resampler.resample(frame):
#             chunks.append(resampled.to_ndarray())
#     container.close()
#     if not chunks:
#         return None
#     audio = np.concatenate(chunks, axis=1).flatten().astype(np.float32) / 32768.0
#     return audio, target_sr


# def classify_audio(raw_audio_bytes, tail_seconds=AUDIO_CLASSIFY_TAIL_SECONDS):
#     """Classifies the trailing N seconds of session audio as 'music',
#     'speech', or 'unclear'. Returns None on any failure (missing deps,
#     decode error, not enough audio yet) rather than raising."""
#     try:
#         decoded = decode_webm_to_waveform(raw_audio_bytes)
#         if decoded is None:
#             return None
#         audio, sr = decoded
#         tail_samples = int(tail_seconds * sr)
#         if len(audio) > tail_samples:
#             audio = audio[-tail_samples:]
#         if len(audio) < sr * 1.0:  # need at least ~1s for a stable read
#             return None

#         model, class_names = get_yamnet_model()
#         scores, _embeddings, _spectrogram = model(audio)
#         mean_scores = scores.numpy().mean(axis=0)
#         top_indices = mean_scores.argsort()[-5:][::-1]
#         top_labels = [(class_names[i], float(mean_scores[i])) for i in top_indices]

#         music_score = sum(s for label, s in top_labels if any(k in label.lower() for k in MUSIC_CLASS_KEYWORDS))
#         speech_score = sum(s for label, s in top_labels if any(k in label.lower() for k in SPEECH_CLASS_KEYWORDS))

#         if music_score > speech_score and music_score > 0.15:
#             return "music"
#         if speech_score >= music_score and speech_score > 0.1:
#             return "speech"
#         return "unclear"
#     except Exception as e:
#         print(f"[audio-classify] Failed (skipping this check): {e!r}")
#         return None


# def normalize_reminder_at(raw):
#     """The LLM resolves relative dates ("Friday") against the server's local
#     time but returns a naive string with no timezone. Attach the server's
#     local offset and convert to UTC here, once, so every downstream
#     consumer -- browser notification comparisons, calendar links, the email
#     scheduler -- works of an unambiguous UTC timestamp instead of each
#     guessing the timezone independently."""
#     if not raw:
#         return None
#     try:
#         naive = datetime.fromisoformat(raw)
#     except ValueError:
#         return None
#     local_tz = datetime.now().astimezone().tzinfo
#     aware_local = naive.replace(tzinfo=local_tz)
#     return aware_local.astimezone(timezone.utc).isoformat()


# def build_ics(summary, description, start_utc, duration_minutes=30):
#     """Minimal RFC 5545 VEVENT. Attaching this to the reminder email lets
#     Gmail, Outlook, and Apple Mail all offer to add it directly to whichever
#     calendar the person actually uses -- no Google/Microsoft API needed."""
#     end_utc = start_utc + timedelta(minutes=duration_minutes)
#     fmt = lambda dt: dt.strftime("%Y%m%dT%H%M%SZ")
#     lines = [
#         "BEGIN:VCALENDAR",
#         "VERSION:2.0",
#         "PRODID:-//Throughline//Task Reminder//EN",
#         "BEGIN:VEVENT",
#         f"UID:{uuid.uuid4()}@throughline",
#         f"DTSTAMP:{fmt(datetime.now(timezone.utc))}",
#         f"DTSTART:{fmt(start_utc)}",
#         f"DTEND:{fmt(end_utc)}",
#         f"SUMMARY:{summary}",
#         f"DESCRIPTION:{description}",
#         "END:VEVENT",
#         "END:VCALENDAR",
#     ]
#     return "\r\n".join(lines)


# def send_reminder_email(to_email, description, owner, due_date, reminder_at_utc_iso):
#     """Sends the reminder over SMTP with an .ics attachment. POC note: From
#     and To are both set to the user's own registered email, per how this is
#     being tested -- register using the same address configured as
#     SMTP_USERNAME so the mail is coming from (and going to) one real inbox.
#     Some providers (Gmail included) may rewrite or reject a From header that
#     doesn't match the authenticated SMTP account; for anything beyond a POC,
#     send through a transactional provider (SES/SendGrid/Mailgun) instead."""
#     smtp_host = os.getenv("SMTP_HOST")
#     smtp_port = int(os.getenv("SMTP_PORT", "587"))
#     smtp_username = os.getenv("SMTP_USERNAME")
#     smtp_password = os.getenv("SMTP_PASSWORD")
#     if not all([smtp_host, smtp_username, smtp_password]):
#         print("[email] SMTP not configured (SMTP_HOST/SMTP_USERNAME/SMTP_PASSWORD) -- skipping")
#         return False

#     try:
#         start_utc = datetime.fromisoformat(reminder_at_utc_iso)
#     except ValueError:
#         return False

#     ics_content = build_ics(
#         description,
#         f"Owner: {owner or 'unspecified'} | Due: {due_date or 'not specified'}",
#         start_utc,
#     )

#     msg = MIMEMultipart()
#     msg["From"] = to_email
#     msg["To"] = to_email
#     msg["Subject"] = f"Reminder: {description}"
#     body = (
#         f"This is a reminder for a task extracted from your conversation:\n\n"
#         f"{description}\n\nOwner: {owner or 'unspecified'}\nDue: {due_date or 'not specified'}\n\n"
#         f"An .ics file is attached -- open it to add this to Google Calendar, Outlook, or Apple Calendar."
#     )
#     msg.attach(MIMEText(body, "plain"))

#     ics_part = MIMEBase("text", "calendar", method="REQUEST", name="reminder.ics")
#     ics_part.set_payload(ics_content)
#     encoders.encode_base64(ics_part)
#     ics_part.add_header("Content-Disposition", "attachment", filename="reminder.ics")
#     msg.attach(ics_part)

#     try:
#         with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
#             server.starttls()
#             server.login(smtp_username, smtp_password)
#             server.sendmail(smtp_username, [to_email], msg.as_string())
#         return True
#     except Exception as e:
#         print(f"[email] Failed to send reminder email to {to_email}: {e!r}")
#         return False


# def email_reminder_worker():
#     """Runs continuously in a background thread, independent of any browser
#     tab -- this is what makes email reminders work even if the app isn't
#     open anywhere, unlike the browser-notification poll."""
#     while True:
#         try:
#             conn = sqlite3.connect(DB_PATH)
#             conn.row_factory = sqlite3.Row
#             now = datetime.now(timezone.utc).isoformat()
#             rows = conn.execute(
#                 "SELECT tasks.id, tasks.description, tasks.owner, tasks.due_date, "
#                 "tasks.reminder_at, users.email FROM tasks "
#                 "JOIN users ON tasks.user_id = users.id "
#                 "WHERE tasks.reminder_at IS NOT NULL AND tasks.email_sent = 0 "
#                 "AND tasks.reminder_at <= ? AND users.email IS NOT NULL AND users.email != ''",
#                 (now,),
#             ).fetchall()
#             for row in rows:
#                 sent = send_reminder_email(
#                     row["email"], row["description"], row["owner"], row["due_date"], row["reminder_at"]
#                 )
#                 if sent:
#                     conn.execute("UPDATE tasks SET email_sent = 1 WHERE id = ?", (row["id"],))
#                     conn.commit()
#             conn.close()
#         except Exception as e:
#             print(f"[email_reminder_worker] error: {e!r}")
#         time.sleep(60)


# # ---------------------------------------------------------------------------
# # Auth helpers
# # ---------------------------------------------------------------------------

# def login_required(view_func):
#     @wraps(view_func)
#     def wrapped(*args, **kwargs):
#         if not session.get("user_id"):
#             return jsonify({"error": "Not authenticated"}), 401
#         return view_func(*args, **kwargs)
#     return wrapped


# def current_user_id():
#     return session.get("user_id")


# @app.route("/register", methods=["POST"])
# def register():
#     data = request.get_json(silent=True) or {}
#     username = (data.get("username") or "").strip()
#     email = (data.get("email") or "").strip()
#     password = data.get("password") or ""

#     if not username or not password:
#         return jsonify({"error": "Username and password are required"}), 400
#     if not email or "@" not in email or "." not in email.split("@")[-1]:
#         return jsonify({"error": "A valid email is required (used for reminder emails)"}), 400
#     if len(password) < 6:
#         return jsonify({"error": "Password must be at least 6 characters"}), 400

#     db = get_db()
#     existing = db.execute(
#         "SELECT id FROM users WHERE username = ?", (username,)
#     ).fetchone()
#     if existing:
#         return jsonify({"error": "Username is already taken"}), 409

#     password_hash = generate_password_hash(password)
#     cur = db.execute(
#         "INSERT INTO users (username, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
#         (username, email, password_hash, now_iso()),
#     )
#     db.commit()

#     session["user_id"] = cur.lastrowid
#     session["username"] = username
#     return jsonify({"id": cur.lastrowid, "username": username, "email": email})


# @app.route("/login", methods=["POST"])
# def login():
#     data = request.get_json(silent=True) or {}
#     username = (data.get("username") or "").strip()
#     password = data.get("password") or ""

#     db = get_db()
#     row = db.execute(
#         "SELECT id, username, password_hash FROM users WHERE username = ?",
#         (username,),
#     ).fetchone()

#     if not row or not check_password_hash(row["password_hash"], password):
#         return jsonify({"error": "Invalid username or password"}), 401

#     session["user_id"] = row["id"]
#     session["username"] = row["username"]
#     return jsonify({"id": row["id"], "username": row["username"]})


# @app.route("/logout", methods=["POST"])
# def logout():
#     session.clear()
#     return jsonify({"ok": True})


# @app.route("/me", methods=["GET"])
# def me():
#     if not session.get("user_id"):
#         return jsonify({"error": "Not authenticated"}), 401
#     return jsonify({"id": session["user_id"], "username": session.get("username")})


# # ---------------------------------------------------------------------------
# # Speaker parsing
# # ---------------------------------------------------------------------------

# SPEAKER_LINE_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 _'-]{0,30}):\s+(.*)$")


# def parse_speakers(raw_text):
#     """Split transcript into (speaker_label, line) pairs.

#     Convention: lines prefixed with "Name: ..." are attributed to that
#     speaker. Lines with no recognizable prefix are attributed to 'Unknown'.
#     """
#     segments = []
#     for line in raw_text.splitlines():
#         line = line.strip()
#         if not line:
#             continue
#         match = SPEAKER_LINE_RE.match(line)
#         if match:
#             speaker, text = match.group(1).strip(), match.group(2).strip()
#             segments.append((speaker, text))
#         else:
#             segments.append(("Unknown", line))
#     return segments


# # ---------------------------------------------------------------------------
# # LLM call
# # ---------------------------------------------------------------------------

# def call_llm(messages):
#     api_key = os.getenv("OLLAMA_API_KEY") or os.getenv("olama_api_key")
#     if not api_key:
#         raise RuntimeError("Missing Ollama API key")

#     # Ollama Cloud bills by GPU-time/usage-level, not strict token count --
#     # lighter models (e.g. gpt-oss:20b) sit in a cheaper usage tier than
#     # gpt-oss:120b. Override via .env to test cost/quality tradeoff without
#     # touching code: OLLAMA_MODEL=gpt-oss:20b
#     payload = {
#         "model": os.getenv("OLLAMA_MODEL", "gpt-oss:120b"),
#         "messages": messages,
#         "stream": False,
#     }

#     response = requests.post(
#         "https://ollama.com/api/chat",
#         headers={
#             "Authorization": f"Bearer {api_key}",
#             "Content-Type": "application/json",
#         },
#         json=payload,
#         timeout=90,
#     )
#     response.raise_for_status()
#     result = response.json()
#     return result.get("message", {}).get("content", "")


# def extract_json(text):
#     """Best-effort extraction of a JSON object from an LLM text response."""
#     text = text.strip()
#     text = re.sub(r"^```json\s*|^```\s*|```$", "", text, flags=re.MULTILINE).strip()
#     try:
#         return json.loads(text)
#     except ValueError:
#         pass
#     match = re.search(r"\{.*\}", text, re.DOTALL)
#     if match:
#         try:
#             return json.loads(match.group(0))
#         except ValueError:
#             return None
#     return None


# # Cost knobs: larger batch = fewer Ollama calls (less repeated system-prompt
# # overhead); higher word floor = skip calls on trivially short/filler batches.
# BACKGROUND_ANALYSIS_BATCH_SIZE = int(os.getenv("BACKGROUND_ANALYSIS_BATCH_SIZE", "12"))
# MIN_WORDS_FOR_ANALYSIS = int(os.getenv("MIN_WORDS_FOR_ANALYSIS", "8"))

# def build_analysis_prompt():
#     # Injecting the real current time lets the model resolve relative phrases
#     # ("Friday", "tomorrow", "in an hour") into an actual timestamp we can
#     # schedule a browser reminder against -- without this, "due_date" is just
#     # a display string with nothing a scheduler could act on.
#     now_local = datetime.now().astimezone()
#     return f"""Today is {now_local.strftime('%A, %Y-%m-%d')}, current time {now_local.strftime('%H:%M %Z')}.

# Extract from this conversation transcript:
# 1. "tasks": action items/commitments. Each:
#    - description (<20 words, your own words)
#    - owner (if stated/implied, else null)
#    - due_date: short human phrase as said/implied (e.g. "Friday", "tonight"), else null
#    - reminder_at: ONLY if a specific date and/or time is stated or clearly implied, resolve it \
# against today's date above and return exact ISO 8601 "YYYY-MM-DDTHH:MM:SS" (assume 09:00:00 if a \
# day is given with no time). Else null. Do not guess if nothing time-related was said.
# 2. "speakers": per speaker label, up to 3 short behavioral observations grounded only in what \
# they said/how they said it (e.g. "proposed the deadline", "hedged twice"). No diagnoses or \
# clinical/mental-health terms, no motive speculation.

# Reply with ONLY this JSON, no preamble/fences:
# {{"tasks": [{{"description": "", "owner": null, "due_date": null, "reminder_at": null}}], \
# "speakers": [{{"label": "", "observations": [""]}}]}}
# Empty lists if none found."""


# # ---------------------------------------------------------------------------
# # Routes
# # ---------------------------------------------------------------------------

# @app.route("/")
# def index():
#     return render_template("index.html")


# @app.route("/transcribe", methods=["POST"])
# @login_required
# def transcribe_audio():
#     """Send a recorded audio clip to Deepgram for diarized transcription and
#     return a speaker-labeled transcript in the "Name: text" convention used
#     elsewhere in this app. Deepgram handles concurrent requests from many
#     users on its own infrastructure -- nothing to scale on our side."""
#     if "audio" not in request.files:
#         return jsonify({"error": "No audio file uploaded"}), 400

#     api_key = os.getenv("DEEPGRAM_API_KEY")
#     if not api_key:
#         return jsonify({"error": "Missing DEEPGRAM_API_KEY"}), 500

#     audio_file = request.files["audio"]
#     audio_bytes = audio_file.read()
#     content_type = audio_file.mimetype or "audio/webm"

#     try:
#         resp = requests.post(
#             "https://api.deepgram.com/v1/listen",
#             params={
#                 "diarize": "true",
#                 "punctuate": "true",
#                 "utterances": "true",
#                 "model": "nova-2",
#             },
#             headers={
#                 "Authorization": f"Token {api_key}",
#                 "Content-Type": content_type,
#             },
#             data=audio_bytes,
#             timeout=120,
#         )
#         resp.raise_for_status()
#     except requests.HTTPError as e:
#         status = e.response.status_code if e.response is not None else 500
#         return jsonify({"error": f"Deepgram error: {e}"}), status
#     except requests.RequestException as e:
#         return jsonify({"error": f"Could not reach Deepgram: {e}"}), 502

#     data = resp.json()
#     utterances = (data.get("results") or {}).get("utterances") or []

#     lines = []
#     segments = []
#     for utt in utterances:
#         speaker_label = f"Speaker {utt.get('speaker', 0)}"
#         text = (utt.get("transcript") or "").strip()
#         if not text:
#             continue
#         lines.append(f"{speaker_label}: {text}")
#         segments.append({
#             "speaker": speaker_label,
#             "text": text,
#             "start": utt.get("start"),
#             "end": utt.get("end"),
#         })

#     transcript_text = "\n".join(lines)
#     return jsonify({"transcript": transcript_text, "segments": segments})


# @app.route("/save", methods=["POST"])
# @login_required
# def save_conversation():
#     data = request.get_json(silent=True) or {}
#     raw_transcript = (data.get("transcript") or "").strip()
#     title = (data.get("title") or "").strip() or None

#     if not raw_transcript:
#         return jsonify({"error": "Transcript is required"}), 400

#     db = get_db()
#     cur = db.execute(
#         "INSERT INTO conversations (user_id, created_at, title, raw_transcript) "
#         "VALUES (?, ?, ?, ?)",
#         (current_user_id(), now_iso(), title, raw_transcript),
#     )
#     db.commit()
#     return jsonify({"conversation_id": cur.lastrowid})


# def run_background_analysis(user_id, conversation_id, delta_text, push):
#     """Runs in its own thread with its own sqlite connection -- Flask's
#     request-scoped `g` connection can't be shared across threads. Extracts
#     tasks + behavioral observations from just the new lines since the last
#     pass (not the whole conversation), then pushes a summary back over the
#     websocket so the UI can show it without the user clicking Analyze."""
#     if not delta_text.strip():
#         return
#     try:
#         content = call_llm([
#             {"role": "system", "content": build_analysis_prompt()},
#             {"role": "user", "content": delta_text},
#         ])
#     except Exception:
#         return

#     parsed = extract_json(content)
#     if not parsed:
#         return

#     tasks = parsed.get("tasks") or []
#     speakers = parsed.get("speakers") or []

#     conn = sqlite3.connect(DB_PATH)
#     try:
#         for task in tasks:
#             description = (task.get("description") or "").strip()
#             if not description:
#                 continue
#             conn.execute(
#                 "INSERT INTO tasks (user_id, conversation_id, description, owner, due_date, reminder_at, status, created_at) "
#                 "VALUES (?, ?, ?, ?, ?, ?, 'open', ?)",
#                 (user_id, conversation_id, description, task.get("owner"), task.get("due_date"),
#                  normalize_reminder_at(task.get("reminder_at")), now_iso()),
#             )
#         for speaker in speakers:
#             label = (speaker.get("label") or "").strip()
#             if not label:
#                 continue
#             for obs in speaker.get("observations") or []:
#                 obs = (obs or "").strip()
#                 if not obs:
#                     continue
#                 conn.execute(
#                     "INSERT INTO personality_notes (user_id, conversation_id, speaker_label, observation, created_at) "
#                     "VALUES (?, ?, ?, ?, ?)",
#                     (user_id, conversation_id, label, obs, now_iso()),
#                 )
#         conn.commit()
#     finally:
#         conn.close()

#     push({"type": "background_update", "tasks_found": len(tasks), "speakers_found": len(speakers)})


# @sock.route("/ws/listen")
# def ws_listen(ws):
#     """Live listening session: browser streams mic audio in over this
#     websocket, we relay it to Deepgram's streaming API, and stream
#     diarized transcript lines + new-speaker notices back to the browser.
#     Task/profile extraction runs automatically in the background -- no
#     separate Analyze click needed. A second background thread periodically
#     classifies the recent audio as music or speech via YAMNet and pushes a
#     notice to the frontend whenever that classification changes."""
#     user_id = session.get("user_id")
#     if not user_id:
#         ws.close()
#         return

#     api_key = os.getenv("DEEPGRAM_API_KEY")
#     if not api_key:
#         ws.send(json.dumps({"type": "error", "message": "Missing DEEPGRAM_API_KEY"}))
#         return

#     db = get_db()
#     cur = db.execute(
#         "INSERT INTO conversations (user_id, created_at, title, raw_transcript) VALUES (?, ?, ?, ?)",
#         (user_id, now_iso(), None, ""),
#     )
#     db.commit()
#     conversation_id = cur.lastrowid

#     dg_url = (
#         "wss://api.deepgram.com/v1/listen"
#         "?diarize=true&punctuate=true&interim_results=false&model=nova-2"
#     )
#     try:
#         dg_ws = ws_client.create_connection(dg_url, header=[f"Authorization: Token {api_key}"])
#     except Exception as e:
#         print(f"[ws_listen] Failed to connect to Deepgram: {e!r}")
#         ws.send(json.dumps({"type": "error", "message": f"Could not connect to Deepgram: {e}"}))
#         return

#     lock = threading.Lock()
#     state = {
#         "known_speakers": {},   # speaker index -> chosen name, or None if unnamed
#         "transcript_lines": [], # (speaker_index, "Label: text") for every final line
#         "pending_lines": [],    # lines not yet sent for background analysis
#         "audio_buffer": bytearray(),  # raw session audio, for music/speech classification
#         "last_audio_class": None,     # last pushed classification, to only notify on change
#         "stop": False,
#     }

#     def push(payload):
#         try:
#             ws.send(json.dumps(payload))
#         except Exception:
#             pass

#     def label_for(speaker_idx):
#         name = state["known_speakers"].get(speaker_idx)
#         return name if name else f"Speaker {speaker_idx}"

#     def receive_from_deepgram():
#         while not state["stop"]:
#             try:
#                 message = dg_ws.recv()
#             except Exception as e:
#                 print(f"[ws_listen] Deepgram recv stopped: {e!r}")
#                 break
#             if not message:
#                 continue
#             try:
#                 data = json.loads(message)
#             except ValueError:
#                 continue

#             if not data.get("is_final"):
#                 continue
#             alt = ((data.get("channel") or {}).get("alternatives") or [None])[0]
#             if not alt:
#                 continue
#             text = (alt.get("transcript") or "").strip()
#             if not text:
#                 continue
#             words = alt.get("words") or []
#             speaker_idx = words[0].get("speaker", 0) if words else 0

#             with lock:
#                 is_new_speaker = speaker_idx not in state["known_speakers"]
#                 if is_new_speaker:
#                     state["known_speakers"][speaker_idx] = None
#                 line = f"{label_for(speaker_idx)}: {text}"
#                 state["transcript_lines"].append((speaker_idx, line))
#                 state["pending_lines"].append(line)
#                 # Batching more lines per call means fewer Ollama calls overall,
#                 # and since the system prompt is billed on every call, fewer
#                 # calls = less repeated overhead for the same coverage.
#                 should_analyze = len(state["pending_lines"]) >= BACKGROUND_ANALYSIS_BATCH_SIZE
#                 delta_text = None
#                 if should_analyze:
#                     candidate = "\n".join(state["pending_lines"])
#                     # Skip calling the LLM on batches that are almost
#                     # certainly just filler ("yeah", "okay", "mm-hmm") --
#                     # not worth a full call.
#                     if len(candidate.split()) >= MIN_WORDS_FOR_ANALYSIS:
#                         delta_text = candidate
#                     state["pending_lines"] = []

#             push({
#                 "type": "transcript",
#                 "speaker_index": speaker_idx,
#                 "line": line,
#             })
#             if is_new_speaker:
#                 push({"type": "new_speaker", "speaker_index": speaker_idx})
#             if delta_text:
#                 threading.Thread(
#                     target=run_background_analysis,
#                     args=(user_id, conversation_id, delta_text, push),
#                     daemon=True,
#                 ).start()

#     def audio_classifier_worker():
#         """Periodically checks whether the recent audio sounds like music or
#         speech, and pushes a notice to the frontend only when that changes
#         -- so this doesn't spam a popup every few seconds during a normal
#         conversation, only when something actually shifts."""
#         while not state["stop"]:
#             time.sleep(AUDIO_CLASSIFY_INTERVAL_SECONDS)
#             if state["stop"]:
#                 break
#             with lock:
#                 snapshot = bytes(state["audio_buffer"])
#             if not snapshot:
#                 continue
#             label = classify_audio(snapshot)
#             if label and label != state.get("last_audio_class"):
#                 state["last_audio_class"] = label
#                 push({"type": "audio_classification", "label": label})

#     threading.Thread(target=receive_from_deepgram, daemon=True).start()
#     threading.Thread(target=audio_classifier_worker, daemon=True).start()

#     try:
#         while True:
#             chunk = ws.receive()
#             if chunk is None:
#                 break
#             if isinstance(chunk, str):
#                 try:
#                     msg = json.loads(chunk)
#                 except ValueError:
#                     continue
#                 if msg.get("type") == "rename_speaker":
#                     idx = msg.get("speaker_index")
#                     name = (msg.get("name") or "").strip()
#                     if name:
#                         with lock:
#                             state["known_speakers"][idx] = name
#                         push({"type": "speaker_renamed", "speaker_index": idx, "name": name})
#                 continue
#             try:
#                 dg_ws.send_binary(chunk)
#                 with lock:
#                     state["audio_buffer"].extend(chunk)
#                     # Cap buffer growth on long sessions -- keep roughly the
#                     # last couple minutes, plenty for trailing-window
#                     # classification without memory growing unbounded.
#                     max_bytes = 2_000_000
#                     if len(state["audio_buffer"]) > max_bytes:
#                         del state["audio_buffer"][: len(state["audio_buffer"]) - max_bytes]
#             except Exception:
#                 break
#     finally:
#         state["stop"] = True
#         try:
#             dg_ws.close()
#         except Exception:
#             pass

#         with lock:
#             # Re-label every stored line with whatever name each speaker
#             # ended up with, even ones spoken before they were named.
#             final_lines = [
#                 f"{label_for(idx)}: {line.split(': ', 1)[1]}"
#                 for idx, line in state["transcript_lines"]
#             ]
#             leftover = "\n".join(state["pending_lines"])

#         if final_lines:
#             conn = sqlite3.connect(DB_PATH)
#             conn.execute(
#                 "UPDATE conversations SET raw_transcript = ? WHERE id = ?",
#                 ("\n".join(final_lines), conversation_id),
#             )
#             conn.commit()
#             conn.close()
#             if leftover.strip():
#                 threading.Thread(
#                     target=run_background_analysis,
#                     args=(user_id, conversation_id, leftover, push),
#                     daemon=True,
#                 ).start()


# @app.route("/analyze", methods=["POST"])
# @login_required
# def analyze_conversation():
#     data = request.get_json(silent=True) or {}
#     conversation_id = data.get("conversation_id")
#     raw_transcript = (data.get("transcript") or "").strip()
#     user_id = current_user_id()

#     db = get_db()

#     if conversation_id:
#         row = db.execute(
#             "SELECT * FROM conversations WHERE id = ? AND user_id = ?",
#             (conversation_id, user_id),
#         ).fetchone()
#         if not row:
#             return jsonify({"error": "Conversation not found"}), 404
#         raw_transcript = row["raw_transcript"]
#     elif raw_transcript:
#         cur = db.execute(
#             "INSERT INTO conversations (user_id, created_at, title, raw_transcript) "
#             "VALUES (?, ?, ?, ?)",
#             (user_id, now_iso(), None, raw_transcript),
#         )
#         db.commit()
#         conversation_id = cur.lastrowid
#     else:
#         return jsonify({"error": "transcript or conversation_id is required"}), 400

#     try:
#         content = call_llm(
#             [
#                 {"role": "system", "content": build_analysis_prompt()},
#                 {"role": "user", "content": raw_transcript},
#             ]
#         )
#     except requests.HTTPError as e:
#         status = e.response.status_code if e.response is not None else 500
#         if status == 429:
#             return jsonify({
#                 "error": "Ollama quota is currently exhausted or rate limited. Please try again later."
#             }), 429
#         return jsonify({"error": str(e)}), status
#     except RuntimeError as e:
#         return jsonify({"error": str(e)}), 500

#     parsed = extract_json(content)
#     if parsed is None:
#         return jsonify({"error": "Could not parse analysis result", "raw": content}), 502

#     tasks = parsed.get("tasks") or []
#     speakers = parsed.get("speakers") or []

#     for task in tasks:
#         description = (task.get("description") or "").strip()
#         if not description:
#             continue
#         db.execute(
#             "INSERT INTO tasks (user_id, conversation_id, description, owner, due_date, reminder_at, status, created_at) "
#             "VALUES (?, ?, ?, ?, ?, ?, 'open', ?)",
#             (user_id, conversation_id, description, task.get("owner"), task.get("due_date"),
#              normalize_reminder_at(task.get("reminder_at")), now_iso()),
#         )

#     for speaker in speakers:
#         label = (speaker.get("label") or "").strip()
#         if not label:
#             continue
#         for obs in speaker.get("observations") or []:
#             obs = (obs or "").strip()
#             if not obs:
#                 continue
#             db.execute(
#                 "INSERT INTO personality_notes (user_id, conversation_id, speaker_label, observation, created_at) "
#                 "VALUES (?, ?, ?, ?, ?)",
#                 (user_id, conversation_id, label, obs, now_iso()),
#             )

#     db.commit()

#     return jsonify(
#         {
#             "conversation_id": conversation_id,
#             "tasks": tasks,
#             "speakers": speakers,
#         }
#     )


# @app.route("/tasks", methods=["GET"])
# @login_required
# def list_tasks():
#     status = request.args.get("status", "open")
#     user_id = current_user_id()
#     db = get_db()
#     if status == "all":
#         rows = db.execute(
#             "SELECT * FROM tasks WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
#         ).fetchall()
#     else:
#         rows = db.execute(
#             "SELECT * FROM tasks WHERE user_id = ? AND status = ? ORDER BY created_at DESC",
#             (user_id, status),
#         ).fetchall()
#     return jsonify([dict(r) for r in rows])


# @app.route("/tasks/<int:task_id>/complete", methods=["POST"])
# @login_required
# def complete_task(task_id):
#     db = get_db()
#     row = db.execute(
#         "SELECT id FROM tasks WHERE id = ? AND user_id = ?", (task_id, current_user_id())
#     ).fetchone()
#     if not row:
#         return jsonify({"error": "Task not found"}), 404
#     db.execute("UPDATE tasks SET status = 'done' WHERE id = ?", (task_id,))
#     db.commit()
#     return jsonify({"ok": True})


# @app.route("/tasks/<int:task_id>/reopen", methods=["POST"])
# @login_required
# def reopen_task(task_id):
#     db = get_db()
#     row = db.execute(
#         "SELECT id FROM tasks WHERE id = ? AND user_id = ?", (task_id, current_user_id())
#     ).fetchone()
#     if not row:
#         return jsonify({"error": "Task not found"}), 404
#     db.execute("UPDATE tasks SET status = 'open' WHERE id = ?", (task_id,))
#     db.commit()
#     return jsonify({"ok": True})


# @app.route("/tasks/<int:task_id>/mark_reminded", methods=["POST"])
# @login_required
# def mark_reminded(task_id):
#     db = get_db()
#     row = db.execute(
#         "SELECT id FROM tasks WHERE id = ? AND user_id = ?", (task_id, current_user_id())
#     ).fetchone()
#     if not row:
#         return jsonify({"error": "Task not found"}), 404
#     db.execute("UPDATE tasks SET reminder_sent = 1 WHERE id = ?", (task_id,))
#     db.commit()
#     return jsonify({"ok": True})


# @app.route("/profiles", methods=["GET"])
# @login_required
# def list_profiles():
#     db = get_db()
#     rows = db.execute(
#         "SELECT speaker_label, observation, created_at, conversation_id "
#         "FROM personality_notes WHERE user_id = ? ORDER BY speaker_label, created_at DESC",
#         (current_user_id(),),
#     ).fetchall()

#     profiles = {}
#     for r in rows:
#         label = r["speaker_label"]
#         profiles.setdefault(label, []).append(
#             {
#                 "observation": r["observation"],
#                 "created_at": r["created_at"],
#                 "conversation_id": r["conversation_id"],
#             }
#         )
#     return jsonify(profiles)


# CHAT_SYSTEM_PROMPT = (
#     "You are a concise assistant answering questions about a live or saved "
#     "conversation transcript. Be direct: answer what was asked, no restating "
#     "the question, no unnecessary preamble or filler, no padding a short "
#     "answer to sound thorough. Ground every answer in the transcript/context "
#     "provided; if something isn't in it, say so briefly rather than "
#     "guessing. Use the prior exchanges in this chat to resolve follow-up "
#     "references ('he', 'that', 'earlier') instead of asking for "
#     "clarification when it's inferable from context."
# )

# # Keep only the last N exchanges per conversation -- bounds both the file
# # size and the tokens sent to the LLM on every turn as a chat runs long.
# CHAT_HISTORY_MAX_TURNS = int(os.getenv("CHAT_HISTORY_MAX_TURNS", "6"))
# _chat_history_lock = threading.Lock()


# def _chat_history_path(user_id, conversation_id):
#     # Namespaced by user_id so one user can never read or overwrite
#     # another's history file, even if they passed someone else's
#     # conversation_id (the route-level query already blocks reading their
#     # transcript; this just makes the same guarantee hold for history too).
#     return os.path.join(CHAT_HISTORY_DIR, f"{user_id}_{conversation_id}.json")


# def load_chat_history(user_id, conversation_id):
#     path = _chat_history_path(user_id, conversation_id)
#     if not os.path.exists(path):
#         return []
#     try:
#         with open(path, "r", encoding="utf-8") as f:
#             return json.load(f)
#     except (json.JSONDecodeError, OSError) as e:
#         print(f"[chat-history] Could not read {path}: {e!r}")
#         return []


# def save_chat_history(user_id, conversation_id, history):
#     path = _chat_history_path(user_id, conversation_id)
#     trimmed = history[-(CHAT_HISTORY_MAX_TURNS * 2):]  # 2 messages (user+assistant) per turn
#     with _chat_history_lock:
#         try:
#             with open(path, "w", encoding="utf-8") as f:
#                 json.dump(trimmed, f)
#         except OSError as e:
#             print(f"[chat-history] Could not write {path}: {e!r}")


# @app.route("/chat", methods=["POST"])
# @login_required
# def chat():
#     data = request.get_json(silent=True) or {}
#     prompt = (data.get("prompt") or "").strip()
#     transcript = (data.get("transcript") or "").strip()
#     uploaded_text = (data.get("uploadedText") or "").strip()
#     conversation_id = data.get("conversation_id")
#     user_id = current_user_id()

#     if not prompt:
#         return jsonify({"error": "Prompt is required"}), 400

#     # Keep chat context bounded to the most recent portion of the
#     # conversation rather than sending the whole (potentially long-running)
#     # transcript on every single chat message -- otherwise cost per chat
#     # message grows the longer the conversation runs.
#     MAX_CONTEXT_CHARS = 4000
#     def recent(text):
#         return text[-MAX_CONTEXT_CHARS:] if len(text) > MAX_CONTEXT_CHARS else text

#     context_parts = []
#     if conversation_id:
#         db = get_db()
#         row = db.execute(
#             "SELECT raw_transcript FROM conversations WHERE id = ? AND user_id = ?",
#             (conversation_id, user_id),
#         ).fetchone()
#         if row:
#             context_parts.append(f"Saved conversation transcript (most recent portion):\n{recent(row['raw_transcript'])}")

#     if transcript:
#         context_parts.append(f"Live transcript from speech (most recent portion):\n{recent(transcript)}")
#     if uploaded_text:
#         context_parts.append(f"Uploaded text:\n{uploaded_text}")
#     context_parts.append(f"User question:\n{prompt}")
#     current_turn_content = "\n\n".join(context_parts)

#     # Prior exchanges for this conversation, read back from its JSON file --
#     # this is what lets the model resolve "what did he mean by that" style
#     # follow-ups instead of treating every message as a fresh, isolated call.
#     history = load_chat_history(user_id, conversation_id) if conversation_id else []

#     messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
#     messages.extend(history)
#     messages.append({"role": "user", "content": current_turn_content})

#     try:
#         reply = call_llm(messages)
#     except requests.HTTPError as e:
#         status = e.response.status_code if e.response is not None else 500
#         if status == 429:
#             return jsonify({
#                 "reply": "Ollama quota is currently exhausted or rate limited. Please try again later or use a different API key."
#             })
#         try:
#             error_json = e.response.json()
#         except Exception:
#             error_json = {"error": str(e)}
#         return jsonify({"error": error_json}), status
#     except RuntimeError as e:
#         return jsonify({"error": str(e)}), 500

#     if not reply:
#         return jsonify({"reply": ""})

#     if conversation_id:
#         # Store the clean question, not the transcript-laden context blob --
#         # the transcript gets freshly re-attached to every call already, so
#         # saving it into history too would just duplicate it on every turn.
#         history.append({"role": "user", "content": prompt})
#         history.append({"role": "assistant", "content": reply})
#         save_chat_history(user_id, conversation_id, history)

#     return jsonify({"reply": reply})


# init_db()

# if __name__ == "__main__":
#     # Mic access (getUserMedia) only works over "secure contexts" -- https,
#     # or localhost. Testing from other devices on your LAN needs https even
#     # with a self-signed cert; set USE_HTTPS=true in .env to turn it on.
#     # Each device will need to click through one browser warning the first
#     # time (the cert isn't from a trusted authority, but that's fine for
#     # local testing -- the browser still treats it as a secure context).
#     use_https = os.getenv("USE_HTTPS", "false").lower() == "true"
#     ssl_context = "adhoc" if use_https else None
#     port = int(os.getenv("PORT", "5000"))

#     # debug=True runs Flask's reloader, which re-executes this module in a
#     # child process -- WERKZEUG_RUN_MAIN is only set in that actual running
#     # child, so checking it avoids starting two copies of the email worker.
#     if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
#         threading.Thread(target=email_reminder_worker, daemon=True).start()

#     app.run(host="0.0.0.0", port=port, debug=True, threaded=True, ssl_context=ssl_context)


import os
import re
import json
import secrets
import threading
import time
import smtplib
import uuid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime, timezone, timedelta
from functools import wraps

from flask import Flask, jsonify, render_template, request, g, session
from flask_sock import Sock
import requests
import websocket as ws_client  # websocket-client package
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
import psycopg2.extras
import psycopg2.pool

load_dotenv()

app = Flask(__name__)
sock = Sock(app)
# Set a real, stable secret in your .env for anything beyond local POC use:
# FLASK_SECRET_KEY=<a long random string>
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")


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
    cur.execute("SELECT personalization FROM users WHERE id = %s", (user_id,))
    row = cur.fetchone()
    mode = row["personalization"] if row else None
    return normalize_personalization(mode)


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

def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify({"error": "Not authenticated"}), 401
        return view_func(*args, **kwargs)
    return wrapped


def current_user_id():
    return session.get("user_id")


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
    return jsonify({"id": row["id"], "username": row["username"]})


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/me", methods=["GET"])
def me():
    if not session.get("user_id"):
        return jsonify({"error": "Not authenticated"}), 401
    return jsonify({"id": session["user_id"], "username": session.get("username")})


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
    if personalization not in PERSONALIZATION_MODES:
        return jsonify({"error": f"personalization must be one of {sorted(PERSONALIZATION_MODES)}"}), 400

    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "UPDATE users SET personalization = %s WHERE id = %s",
        (personalization, current_user_id()),
    )
    db.commit()
    return jsonify({"ok": True, "personalization": personalization})


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

def build_analysis_prompt(personalization=DEFAULT_PERSONALIZATION):
    # Injecting the real current time lets the model resolve relative phrases
    # ("Friday", "tomorrow", "in an hour") into an actual timestamp we can
    # schedule a browser reminder against -- without this, "due_date" is just
    # a display string with nothing a scheduler could act on.
    now_local = datetime.now().astimezone()
    mode_guidance = PERSONALIZATION_GUIDANCE[normalize_personalization(personalization)]
    return f"""Today is {now_local.strftime('%A, %Y-%m-%d')}, current time {now_local.strftime('%H:%M %Z')}.

{mode_guidance}

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

Reply with ONLY this JSON, no preamble/fences:
{{"tasks": [{{"description": "", "owner": null, "due_date": null, "reminder_at": null}}], \
"speakers": [{{"label": "", "observations": [""]}}], \
"mood": {{"label": "neutral", "score": 0.5}}}}
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
    return render_template("index.html")


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
        mode = get_user_personalization(dict_cursor(conn), user_id)
    finally:
        release_raw_connection(conn)

    try:
        content = call_llm([
            {"role": "system", "content": build_analysis_prompt(mode)},
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

    conn = get_raw_connection()
    try:
        cur = conn.cursor()
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
        for speaker in speakers:
            label = (speaker.get("label") or "").strip()
            if not label:
                continue
            for obs in speaker.get("observations") or []:
                obs = (obs or "").strip()
                if not obs:
                    continue
                cur.execute(
                    "INSERT INTO personality_notes (user_id, conversation_id, speaker_label, observation) "
                    "VALUES (%s, %s, %s, %s)",
                    (user_id, conversation_id, label, obs),
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
    finally:
        release_raw_connection(conn)

    push({"type": "background_update", "tasks_found": len(tasks), "speakers_found": len(speakers)})


@sock.route("/ws/listen")
def ws_listen(ws):
    """Live listening session: browser streams mic audio in over this
    websocket, we relay it to Deepgram's streaming API, and stream
    diarized transcript lines + new-speaker notices back to the browser.
    Task/profile extraction runs automatically in the background -- no
    separate Analyze click needed. A second background thread periodically
    classifies the recent audio as music or speech via YAMNet and pushes a
    notice to the frontend whenever that classification changes."""
    user_id = session.get("user_id")
    if not user_id:
        ws.close()
        return

    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        ws.send(json.dumps({"type": "error", "message": "Missing DEEPGRAM_API_KEY"}))
        return

    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "INSERT INTO conversations (user_id, title, raw_transcript) VALUES (%s, %s, %s) RETURNING id",
        (user_id, None, ""),
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
            full_transcript = "\n".join(final_lines)
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
    try:
        content = call_llm(
            [
                {"role": "system", "content": build_analysis_prompt(mode)},
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

    for speaker in speakers:
        label = (speaker.get("label") or "").strip()
        if not label:
            continue
        for obs in speaker.get("observations") or []:
            obs = (obs or "").strip()
            if not obs:
                continue
            cur.execute(
                "INSERT INTO personality_notes (user_id, conversation_id, speaker_label, observation) "
                "VALUES (%s, %s, %s, %s)",
                (user_id, conversation_id, label, obs),
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
        }
    )


@app.route("/tasks", methods=["GET"])
@login_required
def list_tasks():
    status = request.args.get("status", "open")
    user_id = current_user_id()
    db = get_db()
    cur = dict_cursor(db)
    if status == "all":
        cur.execute(
            "SELECT * FROM tasks WHERE user_id = %s ORDER BY created_at DESC", (user_id,)
        )
    else:
        cur.execute(
            "SELECT * FROM tasks WHERE user_id = %s AND status = %s ORDER BY created_at DESC",
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
    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "SELECT speaker_label, observation, created_at, conversation_id "
        "FROM personality_notes WHERE user_id = %s ORDER BY speaker_label, created_at DESC",
        (current_user_id(),),
    )
    rows = cur.fetchall()

    profiles = {}
    for r in rows:
        label = r["speaker_label"]
        profiles.setdefault(label, []).append(
            {
                "observation": r["observation"],
                "created_at": r["created_at"].isoformat() if isinstance(r["created_at"], datetime) else r["created_at"],
                "conversation_id": r["conversation_id"],
            }
        )
    return jsonify(profiles)


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
        "SELECT users.id, users.username FROM friendships "
        "JOIN users ON users.id = friendships.friend_id "
        "WHERE friendships.user_id = %s ORDER BY users.username",
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


def build_chat_system_prompt(personalization=DEFAULT_PERSONALIZATION):
    return (
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
        "SELECT id, created_at, title FROM conversations WHERE user_id = %s ORDER BY created_at DESC",
        (current_user_id(),),
    )
    return jsonify([serialize_row(r) for r in cur.fetchall()])


@app.route("/conversations/<int:conversation_id>", methods=["GET"])
@login_required
def get_conversation(conversation_id):
    db = get_db()
    cur = dict_cursor(db)
    cur.execute(
        "SELECT id, created_at, title, raw_transcript FROM conversations WHERE id = %s AND user_id = %s",
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

    messages = [{"role": "system", "content": build_chat_system_prompt(mode)}]
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

    return jsonify({"reply": reply})


verify_db_connection()

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