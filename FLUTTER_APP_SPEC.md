# Throughline — Flutter Client Spec

This is a build spec for a Flutter app (Android + iOS) that is a **visual and functional clone of the existing React web client**, talking to the **same, already-running Flask + Postgres backend** (`app.py`). The backend is complete and stable — this document is scoped to the client only. If something looks missing on the backend side, call it out rather than guessing a fix or adding a new endpoint.

Tagline used in the existing app: *"Listens once. Remembers everything."*

## 0. Ground truth reference

A working WhatsApp-styled implementation of this exact product already exists at `frontend/` (React + Vite, plain JS, no TypeScript). When anything below is ambiguous, that code is the tie-breaker — read the relevant file before guessing:

| Concern | Reference file |
|---|---|
| Color tokens / layout CSS | `frontend/src/theme.css` |
| Icon set (inline SVG) | `frontend/src/icons.jsx` |
| Auth + token storage | `frontend/src/api.js`, `frontend/src/hooks/useAuth.js` |
| Live listening (mic → WebSocket) | `frontend/src/hooks/useLiveSession.js` |
| Push notification badges | `frontend/src/hooks/useNotifications.js` |
| Chats screen | `frontend/src/components/ChatsSection.jsx`, `ChatThread.jsx`, `Composer.jsx`, `MessageBubble.jsx` |
| Tasks screen | `frontend/src/components/TasksSection.jsx` |
| Profiles screen | `frontend/src/components/ProfilesSection.jsx` |
| Friends screen | `frontend/src/components/FriendsSection.jsx` |
| Settings screen | `frontend/src/components/SettingsSection.jsx` |
| Tab navigation | `frontend/src/components/IconRail.jsx` |
| Backend routes/behavior | `app.py` |
| DB schema | `schema.sql` |

## 1. What the app does

Throughline records a conversation (live, diarized by speaker), and turns it into:
- A **transcript** (raw text, speaker-labeled).
- An **LLM Q&A thread** the user can ask questions against ("what did we agree on?") — this is what's rendered as chat bubbles, *not* the raw transcript.
- Auto-extracted **tasks** (owner, due date, reminder).
- Auto-extracted **person profiles** (behavioral observations per named speaker).
- Auto-extracted **mood** for the logged-in user, shareable with friends.

Every conversation is tagged with a **category** (`personal` / `office` / `study`) taken from the user's current "personalization mode" at the time it was recorded. Tasks and profiles inherit that category transitively (a task's category = its source conversation's category; a profile's categories = the set of categories across every conversation that person was ever observed in — a real many-to-many, stored in `profile_categories`, not just derived on the fly).

## 2. Design language — WhatsApp Web, modern (2023+), not the old teal version

No teal top bar. Light gray chrome, white panels, green accent. Pull these directly into a Flutter `ThemeData`:

```
bg-app         #F0F2F5   scaffold/background behind panels
panel          #FFFFFF   list rows, cards, app bars
chat-bg        #EFEAE2   chat thread background (WhatsApp's doodle-wallpaper tone)
bubble-out     #D9FDD3   outgoing message bubble (your own messages, right-aligned)
bubble-in      #FFFFFF   incoming message bubble (assistant, left-aligned)
text           #111B21   primary text
text-soft      #667781   secondary text (subtitles, timestamps)
accent         #00A884   active tab, buttons, links, FAB
accent-dark    #008069   pressed/hover state of accent
border         #E9EDEF   dividers, row separators
danger         #DC3545   delete actions
rail-icon      #54656F   inactive nav icon
rail-icon-active #00A884 active nav icon
unread-badge   #EA0038   unread count badge on nav icons
```

Font stack: system default (`-apple-system, Segoe UI, Helvetica` on web) → just use each platform's system font in Flutter (`Theme.of(context).textTheme` defaults, no custom font needed).

Avatars: solid green circle (`accent`), white uppercase first letter of the name, no photos.

### Layout: web is 3-pane, phone is not

The web app is a permanent 3-pane layout (icon rail | list | detail) because it has the width for it. A phone doesn't — collapse to standard mobile navigation:

- **Bottom navigation bar** (5 items) replacing the icon rail: **Chats, Tasks, Profiles, Friends, Settings**. Badge counts (unread chats, new tasks) render as a small red dot/number on the tab icon — see §7.
- Each tab's **list view is its own screen**; tapping a row **pushes** a detail screen (conversation thread / task's parent view / profile detail / friend's mood) rather than showing a second pane. Use standard `Navigator.push` / an existing router (go_router is fine) with the item's id as the route argument.
- Everything else (row styling, colors, bubbles, filters) carries over 1:1 from the web version.

## 3. Auth

JWT bearer token, **not cookies** — this matters, it's not a stylistic choice. The backend comment (`app.py`, near `generate_token`) is explicit that mobile/non-browser clients must use the bearer token path because cross-origin cookies don't survive a native app's request context. Always send:

```
Authorization: Bearer <token>
```

### Endpoints

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/register` | `{username, email, password, personalization?}` | `{id, username, email, personalization, friend_code, token}` |
| POST | `/login` | `{username, password}` | `{id, username, token}` |
| POST | `/logout` | — | `{ok: true}` (safe to call, but on mobile just discarding the local token is equivalent) |
| GET | `/me` | — | `{id, username}` — use to validate a stored token on app launch |

- `personalization` must be one of `personal` / `office` / `study` (defaults to `personal`).
- Email is **required** at registration (used for task reminder emails) and validated loosely server-side (`@` + a `.` after it).
- Password minimum length: 6.
- Token expiry: 30 days (`JWT_EXP_DAYS` in `app.py`). No refresh endpoint exists — on expiry the user just logs in again (a 401 anywhere should clear the stored token and route to the login screen, mirroring `api.js`'s `setUnauthorizedHandler`).

Store the token with `flutter_secure_storage`. Attach it to every request; on any `401` response, clear it and pop back to the auth screen.

### Auth screen

Simple centered card (WhatsApp's real login is a QR scan, which doesn't apply here — this is a deliberate, acknowledged deviation, not a gap): app name, tagline, username field, email field (register mode only), password field, primary button, and a text link toggling between "Log in" / "Register".

## 4. Network setup

- Backend runs on `0.0.0.0:5000` (Flask dev server, `debug=True`). From a physical phone on the same LAN, use the computer's LAN IP (e.g. `http://192.168.x.x:5000`), not `localhost`. From the Android emulator, `localhost` on the host machine is reachable at `10.0.2.2`.
- CORS is irrelevant for a native app (`flask_cors` only matters to browsers) — nothing to configure there.
- HTTPS: only relevant if `USE_HTTPS=true` is set in the backend `.env`. Default is plain HTTP/WS for local dev, which is fine for a native app (no browser "secure context" restriction the way `getUserMedia` has on web).

## 5. Data models

### Conversation
```jsonc
// GET /conversations (list) — no raw_transcript
{ "id": 28, "created_at": "2026-07-30T14:34:39+05:30", "title": "Standup sync" /* or null */, "category": "office" }

// GET /conversations/<id> — includes raw_transcript
{ "id": 28, "created_at": "...", "title": "...", "category": "office", "raw_transcript": "Amit: ...\nkunal: ..." }
```
`title` is `null` until the backend auto-generates one shortly after the live session ends — render `"Untitled conversation"` in the meantime (matches web behavior).

### Chat message (the Q&A thread rendered as bubbles)
```jsonc
// GET /conversations/<id>/chat
[{ "role": "user", "content": "...", "created_at": "..." }, { "role": "assistant", "content": "...", "created_at": "..." }]
```

### Task
```jsonc
// GET /tasks?status=open|done|all
{
  "id": 14, "conversation_id": 28, "description": "Verify all happy scenarios across categories",
  "owner": "Amit", "due_date": "Friday",        // free-text phrase, NOT a parseable date — don't try to sort by it as a real date
  "reminder_at": "2026-08-01T09:00:00+05:30",   // nullable
  "reminder_sent": false, "email_sent": true,   // email_sent -> show a small mail icon on the row
  "status": "open",                             // "open" | "done"
  "created_at": "...",
  "category": "office"                          // derived from the source conversation, defaults to "personal" if the conversation was deleted
}
```

### Profiles (people extracted from conversations)
```jsonc
// GET /profiles — object keyed by canonical display name, NOT an array
{
  "Amit": {
    "categories": ["office", "personal"],       // every category this person has ever been seen under (many-to-many)
    "last_seen": "2026-07-30T14:34:39+05:30",   // most recent conversation they appeared in, across all categories
    "notes": [
      { "observation": "Introduced an office category", "created_at": "...", "conversation_id": 28, "category": "office" },
      { "observation": "...", "created_at": "...", "conversation_id": 13, "category": "personal" }
    ]
  }
}
```
Name matching is case-insensitive server-side ("Kunal" and "kunal" always collapse into one profile) — the client never needs to de-dupe names itself.

### Friend / mood
```jsonc
// GET /friends
[{ "id": 3, "username": "kunal" }]

// GET /friends/<id>/mood  (403 if not friends)
{ "friend_id": 3, "entries": [{ "mood_label": "focused", "mood_score": 0.8, "created_at": "..." }] }
```
Entries are today-only (since local midnight), oldest first — render as a timeline, not just the latest value.

### Known speakers (autocomplete list for the "who is this?" prompt)
```jsonc
// GET /speakers
[{ "id": 12, "name": "kunal" }]
```

### Settings
```jsonc
// GET /settings
{ "personalization": "personal", "friend_code": "AB12CD" }
```

## 6. Full REST endpoint reference

All routes below (except `/register`, `/login`) require `Authorization: Bearer <token>`.

| Method | Path | Purpose |
|---|---|---|
| POST | `/register` | Create account, returns token |
| POST | `/login` | Returns token |
| POST | `/logout` | Clears server session (mobile: just drop the local token) |
| GET | `/me` | Validate current token |
| GET | `/settings` | `{personalization, friend_code}` |
| POST | `/settings` | Body `{personalization}` — must be personal/office/study |
| POST | `/transcribe` | Multipart `audio` file → one-shot diarized transcript (see §8, non-live alternative) |
| POST | `/save` | Body `{transcript, title?}` → creates a conversation, returns `{conversation_id}` |
| POST | `/analyze` | Body `{conversation_id}` **or** `{transcript}` → runs LLM extraction (tasks/profiles/mood) against it, returns `{conversation_id, tasks, speakers, mood}` |
| GET | `/tasks?status=open\|done\|all` | List tasks |
| POST | `/tasks/<id>/complete` | Mark done |
| POST | `/tasks/<id>/reopen` | Mark open |
| POST | `/tasks/<id>/mark_reminded` | Internal bookkeeping — client shouldn't need this |
| GET | `/profiles` | See §5 shape |
| GET | `/speakers` | Known-speaker pick list |
| GET | `/friends` | Friend list |
| POST | `/friends/add` | Body `{friend_code}` |
| GET | `/friends/<id>/mood` | Today's mood timeline |
| GET | `/conversations` | List (no transcript) |
| GET | `/conversations/<id>` | Single, with transcript |
| GET | `/conversations/<id>/chat` | Full Q&A history for that conversation |
| DELETE | `/conversations/<id>` | Deletes the conversation + its chat history (cascades). Tasks/profiles/mood tied to it are **kept**, just lose the conversation link (`conversation_id` → null) |
| POST | `/chat` | Body `{prompt, conversation_id?, transcript?}` → `{reply}`. This is what powers the message bubbles |

## 7. Realtime: two WebSocket channels

### `/ws/notify` — badge counts (connect once, app-wide)

Connect on app start (while authenticated), keep open for the app's lifetime:
```
ws(s)://<host>/ws/notify?token=<jwt>
```
No client→server messages. Server pushes:
```jsonc
{ "type": "task_created", "task_id": 15, "description": "..." }   // increment Tasks tab badge
{ "type": "chat_message", "conversation_id": 28 }                  // add 28 to the unread-chats set; clear it when that conversation is opened
```
The server queues up to 50 events per user while disconnected and flushes them on reconnect — no gap-filling logic needed client-side beyond just handling whatever arrives.

### `/ws/listen` — one connection per live-recording session

```
ws(s)://<host>/ws/listen?token=<jwt>[&conversation_id=<id>]
```
Include `conversation_id` only to **resume** a conversation that was previously stopped mid-way (new lines append to its existing transcript instead of starting a new conversation). Omit it to start fresh.

**Client → server:**
- Raw binary audio chunks, streamed continuously while recording (not batched into one file at the end).
- JSON text message to rename a detected speaker: `{"type": "rename_speaker", "speaker_index": 0, "name": "Amit"}`

**Server → client (JSON):**
| type | fields | meaning |
|---|---|---|
| `session_started` | `conversation_id` | the conversation this session is writing to — store it, needed for `/chat` and for resuming later |
| `transcript` | `speaker_index`, `line` (`"Speaker 0: hello"`) | one finalized utterance |
| `new_speaker` | `speaker_index` | first time this index has spoken — client should prompt "who is this?" |
| `speaker_renamed` | `speaker_index`, `name` | ack of a rename the client sent |
| `background_update` | `tasks_found`, `speakers_found` | periodic auto-analysis ran on recent lines; show a toast like *"Auto-extracted N task(s), notes on M speaker(s)"* |
| `audio_classification` | `label` (`"music"` \| `"speech"` \| `"unclear"`) | only pushed when it *changes* — not spammy |
| `error` | `message` | e.g. missing API key, Deepgram connect failure |

Closing the socket ends the session; the backend finalizes the transcript, generates a title in the background, and any leftover unanalyzed lines get one last analysis pass automatically. Nothing extra to call client-side on stop besides just closing the connection.

⚠️ **Integration risk worth flagging back before building against this literally, not silently working around:** the backend relays your raw audio bytes straight through to Deepgram's streaming endpoint with no explicit `encoding=`/`sample_rate=` query params — it currently works because the web client always records `audio/webm;codecs=opus` (Chrome/Edge `MediaRecorder` default) and Deepgram auto-detects that container. A Flutter mic-streaming package may not produce webm/opus by default (this is also the format `decode_webm_to_waveform` on the backend assumes for the music/speech classifier). Two ways to handle this, pick one deliberately rather than guessing:
1. Find/configure a Flutter recording package that can stream **Opus-in-WebM** (or Ogg/Opus, which Deepgram also auto-detects) — keeps the backend untouched.
2. If that's not practical cross-platform, record in whatever format is natural (e.g. linear PCM) and have the backend add `encoding=linear16&sample_rate=16000` (or whatever matches) to the Deepgram URL for a `?client=mobile`-style flag — a small, explicit backend change, not a silent one.

Don't ship a version that streams a mismatched codec and just silently gets empty transcripts.

## 8. Two ways to implement "listen" — pick one, or offer both

**A. Live streaming (what the web app does)** — mic audio streams continuously over `/ws/listen`, transcript lines and speaker prompts appear in real time while recording. Higher fidelity, matches the reference implementation, but carries the codec risk above and is more moving parts (persistent socket, binary streaming, background reconnect handling).

**B. Record-then-upload (simpler, robust, already supported by the backend)** — record locally to a file in whatever format the platform handles best, then on stop:
1. `POST /transcribe` (multipart `audio` field) → get back a full diarized transcript in one shot.
2. `POST /save` with that transcript → get a `conversation_id`.
3. `POST /analyze` with that `conversation_id` → triggers task/profile/mood extraction.

Path B loses the "watch it transcribe live" UX and per-utterance speaker-rename-while-recording flow, but sidesteps all live-socket/codec concerns and is far less code. Worth explicitly deciding between A and B before writing the recording layer rather than defaulting to A by inertia.

## 9. Screens

### 9.1 Chats (default tab)
- List: avatar (first letter) + title (or "Untitled conversation") + date/time, sorted newest first. Unread ones (from `/ws/notify`) get a small red dot.
- Top bar: title "Chats", a 3-dot **category filter** menu (All categories / Personal / Office / Study — filters the list client-side by `category`), and a search field (simple client-side substring match on title is enough; the web version additionally caches into IndexedDB for offline search — optional, skip for v1).
- Floating "Listen" button (mic icon) starts a brand-new conversation (§8).
- Tapping a row pushes the **Chat Thread** screen, opening that conversation (`GET /conversations/<id>` + `GET /conversations/<id>/chat`).

### 9.2 Chat Thread
- App bar: avatar, title, subtitle (`"Listening..."` with a pulsing dot while live, else `"Ask about this conversation"`), and header actions: an info icon (opens the raw transcript in a slide-over/bottom sheet — **the raw transcript is never rendered as chat bubbles**, only the Q&A is) and a delete icon.
- While live and a new speaker is detected: show a small prompt to name them (text field + "known speakers" picker from `GET /speakers`) or skip.
- Body: message bubbles from `GET /conversations/<id>/chat` — outgoing (`role: user`) right-aligned green, incoming (`role: assistant`) left-aligned white.
- Composer: text field + send button (`POST /chat`) + a mic button that **resumes** live listening on *this* conversation (`/ws/listen?conversation_id=<id>`) if it isn't already live, or stops it if it is (red while live).

### 9.3 Tasks
- Top bar: status filter (Open / Done / All) and the same category filter menu as Chats.
- Row: description, a small mail icon **only if `email_sent` is true** (reminder email already went out), a small chat-bubble icon **only if `conversation_id` is present** that deep-links to that conversation's Chat Thread screen, subtitle = `Owner: X · Due: Y` (whatever's present) or "No details".
- Tapping the row body (not the deep-link icon) toggles complete/reopen (`POST /tasks/<id>/complete` or `/reopen`).

### 9.4 Profiles
- Top bar: title "Profiles" + a **search field** filtering the list by name (client-side substring match — there is no server-side search endpoint for this).
- Row: avatar + name + subtitle = comma-joined `categories` (capitalized) + `· last_seen` formatted as date + time.
- Tapping a row pushes a detail screen: list of that person's `notes`, each showing `Category · timestamp — observation`, with a small deep-link icon (if `conversation_id` present) to that note's source conversation's Chat Thread.

### 9.5 Friends
- Top: add-by-code row (text field + "Add" button → `POST /friends/add`).
- List: friend rows with a status dot + "Mood tracked today" subtitle.
- Tapping a friend pushes their mood timeline for today (`GET /friends/<id>/mood`), rendered as a simple time-ordered list (`HH:MM — mood_label`).

### 9.6 Settings
- "Signed in as {username}".
- Personalization mode picker (Personal/Office/Study) → `POST /settings`. This is what tags *new* conversations' category going forward — changing it doesn't retag past conversations.
- Friend code display (monospace, shareable).
- Log out button → clear stored token, return to Auth screen.

## 10. Suggested Flutter package choices (non-binding, pick what the build environment already favors)

- HTTP: `dio` or `http` — either is fine; the API surface here is simple JSON in/out plus one multipart upload (`/transcribe`).
- WebSockets: `web_socket_channel`.
- Secure token storage: `flutter_secure_storage`.
- State management: whatever this Flutter project already standardizes on (Provider/Riverpod/Bloc) — no backend-driven constraint here.
- Navigation: standard `Navigator` or `go_router`; 5 top-level tabs + pushed detail screens per §2.
- Audio recording: depends on the §8 decision — a package supporting Opus/WebM streaming for path A, or any recorder (e.g. `record`) for path B.

## 11. Explicit non-goals / don't touch

- Don't add, rename, or change any backend route, request shape, or response shape without flagging it back — this spec is a description of what already exists and works for the web client, not a proposal.
- Don't try to replicate the IndexedDB-backed offline search cache from the web app unless asked — it's a nice-to-have optimization, not core behavior.
- `due_date` on tasks is intentionally free text (not a real date) — don't build a date picker around it or try to parse/sort it as one.
