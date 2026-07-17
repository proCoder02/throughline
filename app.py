# import os
# import re
# import json
# import sqlite3
# import threading
# from datetime import datetime, timezone
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
#     conn.close()


# def now_iso():
#     return datetime.now(timezone.utc).isoformat()


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
#     password = data.get("password") or ""

#     if not username or not password:
#         return jsonify({"error": "Username and password are required"}), 400
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
#         "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
#         (username, password_hash, now_iso()),
#     )
#     db.commit()

#     session["user_id"] = cur.lastrowid
#     session["username"] = username
#     return jsonify({"id": cur.lastrowid, "username": username})


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

# ANALYSIS_SYSTEM_PROMPT = """Extract from this conversation transcript:
# 1. "tasks": action items/commitments. Each: description (<20 words, your own words), \
# owner (if stated/implied, else null), due_date (if mentioned, else null).
# 2. "speakers": per speaker label, up to 3 short behavioral observations grounded only in what \
# they said/how they said it (e.g. "proposed the deadline", "hedged twice"). No diagnoses or \
# clinical/mental-health terms, no motive speculation.

# Reply with ONLY this JSON, no preamble/fences:
# {"tasks": [{"description": "", "owner": null, "due_date": null}], \
# "speakers": [{"label": "", "observations": [""]}]}
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
#             {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
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
#                 "INSERT INTO tasks (user_id, conversation_id, description, owner, due_date, status, created_at) "
#                 "VALUES (?, ?, ?, ?, ?, 'open', ?)",
#                 (user_id, conversation_id, description, task.get("owner"), task.get("due_date"), now_iso()),
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
#     separate Analyze click needed."""
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

#     threading.Thread(target=receive_from_deepgram, daemon=True).start()

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
#                 {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
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
#             "INSERT INTO tasks (user_id, conversation_id, description, owner, due_date, status, created_at) "
#             "VALUES (?, ?, ?, ?, ?, 'open', ?)",
#             (user_id, conversation_id, description, task.get("owner"), task.get("due_date"), now_iso()),
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
#     app.run(host="0.0.0.0", port=5000, debug=True, threaded=True)
import os
import re
import json
import sqlite3
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

load_dotenv()

app = Flask(__name__)
sock = Sock(app)
# Set a real, stable secret in your .env for anything beyond local POC use:
# FLASK_SECRET_KEY=<a long random string>
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")

DB_PATH = os.path.join(os.path.dirname(__file__), "conversations.db")


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        # WAL mode lets readers and a writer work concurrently instead of
        # locking the whole file on every write -- meaningfully better
        # behavior under concurrent users while still being plain SQLite.
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            email TEXT,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            title TEXT,
            raw_transcript TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id)
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            conversation_id INTEGER,
            description TEXT NOT NULL,
            owner TEXT,
            due_date TEXT,
            reminder_at TEXT,
            reminder_sent INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (conversation_id) REFERENCES conversations (id)
        );

        CREATE TABLE IF NOT EXISTS personality_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            conversation_id INTEGER,
            speaker_label TEXT NOT NULL,
            observation TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (conversation_id) REFERENCES conversations (id)
        );

        CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations (user_id);
        CREATE INDEX IF NOT EXISTS idx_tasks_user ON tasks (user_id);
        CREATE INDEX IF NOT EXISTS idx_notes_user ON personality_notes (user_id);
        """
    )
    conn.commit()

    # Auto-migrate: add any columns that don't exist yet on an older database
    # file, so schema changes don't require a manual migration script.
    _ensure_column(conn, "tasks", "reminder_at", "TEXT")
    _ensure_column(conn, "tasks", "reminder_sent", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "tasks", "email_sent", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "users", "email", "TEXT")

    conn.close()


def _ensure_column(conn, table, column, coltype):
    existing = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
        conn.commit()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def normalize_reminder_at(raw):
    """The LLM resolves relative dates ("Friday") against the server's local
    time but returns a naive string with no timezone. Attach the server's
    local offset and convert to UTC here, once, so every downstream
    consumer -- browser notification comparisons, calendar links, the email
    scheduler -- works of an unambiguous UTC timestamp instead of each
    guessing the timezone independently."""
    if not raw:
        return None
    try:
        naive = datetime.fromisoformat(raw)
    except ValueError:
        return None
    local_tz = datetime.now().astimezone().tzinfo
    aware_local = naive.replace(tzinfo=local_tz)
    return aware_local.astimezone(timezone.utc).isoformat()


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


def send_reminder_email(to_email, description, owner, due_date, reminder_at_utc_iso):
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

    try:
        start_utc = datetime.fromisoformat(reminder_at_utc_iso)
    except ValueError:
        return False

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
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            now = datetime.now(timezone.utc).isoformat()
            rows = conn.execute(
                "SELECT tasks.id, tasks.description, tasks.owner, tasks.due_date, "
                "tasks.reminder_at, users.email FROM tasks "
                "JOIN users ON tasks.user_id = users.id "
                "WHERE tasks.reminder_at IS NOT NULL AND tasks.email_sent = 0 "
                "AND tasks.reminder_at <= ? AND users.email IS NOT NULL AND users.email != ''",
                (now,),
            ).fetchall()
            for row in rows:
                sent = send_reminder_email(
                    row["email"], row["description"], row["owner"], row["due_date"], row["reminder_at"]
                )
                if sent:
                    conn.execute("UPDATE tasks SET email_sent = 1 WHERE id = ?", (row["id"],))
                    conn.commit()
            conn.close()
        except Exception as e:
            print(f"[email_reminder_worker] error: {e!r}")
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

    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400
    if not email or "@" not in email or "." not in email.split("@")[-1]:
        return jsonify({"error": "A valid email is required (used for reminder emails)"}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400

    db = get_db()
    existing = db.execute(
        "SELECT id FROM users WHERE username = ?", (username,)
    ).fetchone()
    if existing:
        return jsonify({"error": "Username is already taken"}), 409

    password_hash = generate_password_hash(password)
    cur = db.execute(
        "INSERT INTO users (username, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
        (username, email, password_hash, now_iso()),
    )
    db.commit()

    session["user_id"] = cur.lastrowid
    session["username"] = username
    return jsonify({"id": cur.lastrowid, "username": username, "email": email})


@app.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    db = get_db()
    row = db.execute(
        "SELECT id, username, password_hash FROM users WHERE username = ?",
        (username,),
    ).fetchone()

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


# ---------------------------------------------------------------------------
# Speaker parsing
# ---------------------------------------------------------------------------

SPEAKER_LINE_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 _'-]{0,30}):\s+(.*)$")


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

def build_analysis_prompt():
    # Injecting the real current time lets the model resolve relative phrases
    # ("Friday", "tomorrow", "in an hour") into an actual timestamp we can
    # schedule a browser reminder against -- without this, "due_date" is just
    # a display string with nothing a scheduler could act on.
    now_local = datetime.now().astimezone()
    return f"""Today is {now_local.strftime('%A, %Y-%m-%d')}, current time {now_local.strftime('%H:%M %Z')}.

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

Reply with ONLY this JSON, no preamble/fences:
{{"tasks": [{{"description": "", "owner": null, "due_date": null, "reminder_at": null}}], \
"speakers": [{{"label": "", "observations": [""]}}]}}
Empty lists if none found."""


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
    cur = db.execute(
        "INSERT INTO conversations (user_id, created_at, title, raw_transcript) "
        "VALUES (?, ?, ?, ?)",
        (current_user_id(), now_iso(), title, raw_transcript),
    )
    db.commit()
    return jsonify({"conversation_id": cur.lastrowid})


def run_background_analysis(user_id, conversation_id, delta_text, push):
    """Runs in its own thread with its own sqlite connection -- Flask's
    request-scoped `g` connection can't be shared across threads. Extracts
    tasks + behavioral observations from just the new lines since the last
    pass (not the whole conversation), then pushes a summary back over the
    websocket so the UI can show it without the user clicking Analyze."""
    if not delta_text.strip():
        return
    try:
        content = call_llm([
            {"role": "system", "content": build_analysis_prompt()},
            {"role": "user", "content": delta_text},
        ])
    except Exception:
        return

    parsed = extract_json(content)
    if not parsed:
        return

    tasks = parsed.get("tasks") or []
    speakers = parsed.get("speakers") or []

    conn = sqlite3.connect(DB_PATH)
    try:
        for task in tasks:
            description = (task.get("description") or "").strip()
            if not description:
                continue
            conn.execute(
                "INSERT INTO tasks (user_id, conversation_id, description, owner, due_date, reminder_at, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'open', ?)",
                (user_id, conversation_id, description, task.get("owner"), task.get("due_date"),
                 normalize_reminder_at(task.get("reminder_at")), now_iso()),
            )
        for speaker in speakers:
            label = (speaker.get("label") or "").strip()
            if not label:
                continue
            for obs in speaker.get("observations") or []:
                obs = (obs or "").strip()
                if not obs:
                    continue
                conn.execute(
                    "INSERT INTO personality_notes (user_id, conversation_id, speaker_label, observation, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (user_id, conversation_id, label, obs, now_iso()),
                )
        conn.commit()
    finally:
        conn.close()

    push({"type": "background_update", "tasks_found": len(tasks), "speakers_found": len(speakers)})


@sock.route("/ws/listen")
def ws_listen(ws):
    """Live listening session: browser streams mic audio in over this
    websocket, we relay it to Deepgram's streaming API, and stream
    diarized transcript lines + new-speaker notices back to the browser.
    Task/profile extraction runs automatically in the background -- no
    separate Analyze click needed."""
    user_id = session.get("user_id")
    if not user_id:
        ws.close()
        return

    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        ws.send(json.dumps({"type": "error", "message": "Missing DEEPGRAM_API_KEY"}))
        return

    db = get_db()
    cur = db.execute(
        "INSERT INTO conversations (user_id, created_at, title, raw_transcript) VALUES (?, ?, ?, ?)",
        (user_id, now_iso(), None, ""),
    )
    db.commit()
    conversation_id = cur.lastrowid

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

    threading.Thread(target=receive_from_deepgram, daemon=True).start()

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
                        push({"type": "speaker_renamed", "speaker_index": idx, "name": name})
                continue
            try:
                dg_ws.send_binary(chunk)
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
            conn = sqlite3.connect(DB_PATH)
            conn.execute(
                "UPDATE conversations SET raw_transcript = ? WHERE id = ?",
                ("\n".join(final_lines), conversation_id),
            )
            conn.commit()
            conn.close()
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

    if conversation_id:
        row = db.execute(
            "SELECT * FROM conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user_id),
        ).fetchone()
        if not row:
            return jsonify({"error": "Conversation not found"}), 404
        raw_transcript = row["raw_transcript"]
    elif raw_transcript:
        cur = db.execute(
            "INSERT INTO conversations (user_id, created_at, title, raw_transcript) "
            "VALUES (?, ?, ?, ?)",
            (user_id, now_iso(), None, raw_transcript),
        )
        db.commit()
        conversation_id = cur.lastrowid
    else:
        return jsonify({"error": "transcript or conversation_id is required"}), 400

    try:
        content = call_llm(
            [
                {"role": "system", "content": build_analysis_prompt()},
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

    for task in tasks:
        description = (task.get("description") or "").strip()
        if not description:
            continue
        db.execute(
            "INSERT INTO tasks (user_id, conversation_id, description, owner, due_date, reminder_at, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'open', ?)",
            (user_id, conversation_id, description, task.get("owner"), task.get("due_date"),
             normalize_reminder_at(task.get("reminder_at")), now_iso()),
        )

    for speaker in speakers:
        label = (speaker.get("label") or "").strip()
        if not label:
            continue
        for obs in speaker.get("observations") or []:
            obs = (obs or "").strip()
            if not obs:
                continue
            db.execute(
                "INSERT INTO personality_notes (user_id, conversation_id, speaker_label, observation, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, conversation_id, label, obs, now_iso()),
            )

    db.commit()

    return jsonify(
        {
            "conversation_id": conversation_id,
            "tasks": tasks,
            "speakers": speakers,
        }
    )


@app.route("/tasks", methods=["GET"])
@login_required
def list_tasks():
    status = request.args.get("status", "open")
    user_id = current_user_id()
    db = get_db()
    if status == "all":
        rows = db.execute(
            "SELECT * FROM tasks WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM tasks WHERE user_id = ? AND status = ? ORDER BY created_at DESC",
            (user_id, status),
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/tasks/<int:task_id>/complete", methods=["POST"])
@login_required
def complete_task(task_id):
    db = get_db()
    row = db.execute(
        "SELECT id FROM tasks WHERE id = ? AND user_id = ?", (task_id, current_user_id())
    ).fetchone()
    if not row:
        return jsonify({"error": "Task not found"}), 404
    db.execute("UPDATE tasks SET status = 'done' WHERE id = ?", (task_id,))
    db.commit()
    return jsonify({"ok": True})


@app.route("/tasks/<int:task_id>/reopen", methods=["POST"])
@login_required
def reopen_task(task_id):
    db = get_db()
    row = db.execute(
        "SELECT id FROM tasks WHERE id = ? AND user_id = ?", (task_id, current_user_id())
    ).fetchone()
    if not row:
        return jsonify({"error": "Task not found"}), 404
    db.execute("UPDATE tasks SET status = 'open' WHERE id = ?", (task_id,))
    db.commit()
    return jsonify({"ok": True})


@app.route("/tasks/<int:task_id>/mark_reminded", methods=["POST"])
@login_required
def mark_reminded(task_id):
    db = get_db()
    row = db.execute(
        "SELECT id FROM tasks WHERE id = ? AND user_id = ?", (task_id, current_user_id())
    ).fetchone()
    if not row:
        return jsonify({"error": "Task not found"}), 404
    db.execute("UPDATE tasks SET reminder_sent = 1 WHERE id = ?", (task_id,))
    db.commit()
    return jsonify({"ok": True})


@app.route("/profiles", methods=["GET"])
@login_required
def list_profiles():
    db = get_db()
    rows = db.execute(
        "SELECT speaker_label, observation, created_at, conversation_id "
        "FROM personality_notes WHERE user_id = ? ORDER BY speaker_label, created_at DESC",
        (current_user_id(),),
    ).fetchall()

    profiles = {}
    for r in rows:
        label = r["speaker_label"]
        profiles.setdefault(label, []).append(
            {
                "observation": r["observation"],
                "created_at": r["created_at"],
                "conversation_id": r["conversation_id"],
            }
        )
    return jsonify(profiles)


@app.route("/chat", methods=["POST"])
@login_required
def chat():
    data = request.get_json(silent=True) or {}
    prompt = (data.get("prompt") or "").strip()
    transcript = (data.get("transcript") or "").strip()
    uploaded_text = (data.get("uploadedText") or "").strip()
    conversation_id = data.get("conversation_id")
    user_id = current_user_id()

    if not prompt:
        return jsonify({"error": "Prompt is required"}), 400

    parts = []

    # Keep chat context bounded to the most recent portion of the
    # conversation rather than sending the whole (potentially long-running)
    # transcript on every single chat message -- otherwise cost per chat
    # message grows the longer the conversation runs.
    MAX_CONTEXT_CHARS = 4000
    def recent(text):
        return text[-MAX_CONTEXT_CHARS:] if len(text) > MAX_CONTEXT_CHARS else text

    if conversation_id:
        db = get_db()
        row = db.execute(
            "SELECT raw_transcript FROM conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user_id),
        ).fetchone()
        if row:
            parts.append(f"Saved conversation transcript (most recent portion):\n{recent(row['raw_transcript'])}")

    if transcript:
        parts.append(f"Live transcript from speech (most recent portion):\n{recent(transcript)}")
    if uploaded_text:
        parts.append(f"Uploaded text:\n{uploaded_text}")
    parts.append(f"User question:\n{prompt}")

    try:
        reply = call_llm([{"role": "user", "content": "\n\n".join(parts)}])
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
    return jsonify({"reply": reply})


init_db()

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