# Throughline — Flutter Client Spec (updated for the live production API)

This targets the **already-running production backend** at:

```
https://rapexapi.nodexdata.click
```

Everything below reflects what's actually deployed there right now, not a local dev setup — no need to run anything yourself to start building against it. The backend is complete and stable; this doc describes it, it isn't a proposal. If something looks missing, flag it back rather than guessing a fix or adding a new endpoint.

A previous version of this spec exists in the repo history — it's substantially out of date. Persona onboarding, custom categories, task/profile/friend editing, cross-session global chat, and voice calling (all covered below) didn't exist yet when it was written.

## 0. Ground truth reference

A working reference implementation of this whole product (WhatsApp-styled web client, React + Vite) lives at `frontend/` in the same repo and is what's actually running in production today. When anything below is ambiguous, that code is the tie-breaker:

| Concern | Reference file |
|---|---|
| Color tokens / layout CSS | `frontend/src/theme.css` |
| Auth + token storage | `frontend/src/api.js`, `frontend/src/hooks/useAuth.js` |
| Persona onboarding gate | `frontend/src/App.jsx`, `frontend/src/components/PersonaOnboarding.jsx` |
| Live listening (mic → WebSocket) | `frontend/src/hooks/useLiveSession.js` |
| Push notifications + incoming calls | `frontend/src/hooks/useNotifications.js` |
| Voice calling (LiveKit) | `frontend/src/hooks/useCall.js`, `frontend/src/components/CallOverlay.jsx` |
| Chats / per-conversation chat | `frontend/src/components/ChatsSection.jsx`, `ChatThread.jsx` |
| Cross-session global chat | same file — `sendGlobalMessage`/`/chat/global` calls in `ChatsSection.jsx` |
| Tasks (incl. inline edit) | `frontend/src/components/TasksSection.jsx` |
| Profiles (incl. rename/delete) | `frontend/src/components/ProfilesSection.jsx` |
| Friends (incl. nickname) | `frontend/src/components/FriendsSection.jsx` |
| Settings + category management | `frontend/src/components/SettingsSection.jsx` |
| Backend routes/behavior | `app.py` |
| DB schema | `schema.sql` |

## 1. What the app does

Record a conversation — either solo dictation (live mic) or an actual **voice call** placed through the app to a friend (1:1 or group) — and it becomes:
- A **transcript** (diarized by speaker).
- An **LLM Q&A thread** scoped to that one conversation (`/chat`) — this is what renders as chat bubbles, not the raw transcript.
- A **cross-session global thread** (`/chat/global`) that answers questions across your *entire* conversation history — "what did I discuss with Rahul last week?" — automatically scoping retrieval to whichever past conversations involve the person named in the question.
- Auto-extracted **tasks**, **person profiles** (behavioral notes per speaker, accumulated across every conversation they've ever appeared in), and **mood**.

Every conversation is tagged with a **category**: `personal` / `office` / `study` (built-in) or a user-created custom one. A task's category and a profile's categories are derived transitively from the conversations they came from.

**New user gate**: every account (new signups and pre-existing ones alike) must complete a one-time persona form (`GET`/`POST /persona`) before reaching the main app — see §5.

## 2. Auth (unchanged from before)

JWT bearer token — required for a native app (cookies don't survive a mobile app's request context).
```
Authorization: Bearer <token>
```

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/register` | `{username, email, password, personalization?}` | `{id, username, email, personalization, friend_code, token}` |
| POST | `/login` | `{username, password}` | `{id, username, token}` |
| POST | `/logout` | — | `{ok: true}` (mobile: just drop the local token) |
| GET | `/me` | — | `{id, username}` — validate a stored token on launch |

30-day token expiry, no refresh endpoint — a 401 anywhere should clear the stored token and route back to login.

## 3. Custom categories

`personal`/`office`/`study` always exist; a user can add their own on top, usable anywhere a category appears (a conversation's category, a user's active `personalization`).

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/categories` | — | `{"builtin": ["office","personal","study"], "custom": ["Family", ...]}` |
| POST | `/categories` | `{name}` | `{ok, name}` — 409 if it collides (case-insensitively) with a built-in or an existing custom one |
| DELETE | `/categories/<name>` | — | `{ok: true}` |
| POST | `/settings` | `{personalization}` | Sets the user's *active* category — accepts a built-in or any of their own custom categories (400 otherwise). Determines the category tag on conversations created going forward. |
| GET | `/settings` | — | `{personalization, friend_code}` |

## 4. Persona onboarding

One row per user; its existence is the "has this user onboarded" flag. Feeds a compact one-line summary into every chat/analysis LLM call to tailor tone/relevance (not literal facts) — you don't need to do anything with this beyond collecting it.

```jsonc
// GET /persona
{
  "completed": false,
  "persona": null, // or the saved persona object once completed
  "options": {
    "gender": ["Male", "Female", "Non-binary", "Prefer not to say"],
    "language_preference": ["English", "Hindi", "Spanish", "French", "German", "Mandarin", "Arabic"],
    "hobbies": ["Reading", "Sports & Fitness", "Music", "Gaming", "Travel", "Cooking", "Art & Design"],
    "interests": ["Technology", "Finance & Business", "Health & Wellness", "Arts & Culture", "Sports", "Politics & Current Affairs", "Science"]
  }
}
```
```jsonc
// POST /persona
// age, gender, language_preference, hobbies, interests, occupation are mandatory; primary_goal is optional (7 fields total)
{
  "age": 29, "gender": "Male", "language_preference": "English",
  "hobbies": ["Reading", "Travel"], "interests": ["Technology"],
  "occupation": "Software Engineer", "primary_goal": "stay organized"
}
// -> {"ok": true}. 400 with a field-specific message if a mandatory field is missing/invalid.
```
**Flow**: on launch, after a valid token is confirmed, call `GET /persona`. If `completed: false`, show the onboarding form (blocking — no way to skip) before the main app shell. Include a short note near the form: *"We use this information to make your experience more personalized and better tailored to you."*

## 5. Data models

### Conversation
```jsonc
// GET /conversations (list) — no raw_transcript
{ "id": 28, "created_at": "2026-08-01T14:34:39+05:30", "title": "Standup sync", "category": "office" }
// GET /conversations/<id> — includes raw_transcript
{ "id": 28, "created_at": "...", "title": "...", "category": "office", "raw_transcript": "Speaker 0: ...\nSpeaker 1: ..." }
```
`title` is `null` until auto-generated shortly after the session/call ends — show "Untitled conversation" meanwhile.

### Chat message
```jsonc
// GET /conversations/<id>/chat
[{ "role": "user", "content": "...", "created_at": "..." }, { "role": "assistant", "content": "...", "created_at": "..." }]
```

### Task
```jsonc
// GET /tasks?status=open|done|all
{
  "id": 14, "conversation_id": 28, "description": "Finalize the API contract",
  "owner": "Rahul", "due_date": "Friday",           // free-text phrase, not a real date
  "reminder_at": "2026-08-01T09:00:00+05:30", "reminder_sent": false, "email_sent": true,
  "status": "open", "created_at": "...", "updated_at": null,  // set whenever edited
  "category": "office"                               // derived from the source conversation
}
```
| Method | Path | Body |
|---|---|---|
| POST | `/tasks/<id>/complete` \| `/reopen` | — |
| POST | `/tasks/<id>/edit` | `{description?, due_date?}` — at least one; updates in place |

### Profiles (people extracted from conversations — keyed by canonical name, not an array)
```jsonc
// GET /profiles
{
  "Rahul": {
    "profile_id": 12,
    "categories": ["office", "personal"],          // every category this person has ever been seen under (real many-to-many)
    "last_seen": "2026-08-01T14:34:39+05:30",
    "notes": [
      { "observation": "Proposed the API deadline", "created_at": "...", "conversation_id": 28, "category": "office" }
    ]
  }
}
```
| Method | Path | Body |
|---|---|---|
| DELETE | `/profiles/<profile_id>` | — deletes the person + every observation/category link about them |
| POST | `/profiles/<profile_id>/rename` | `{name}` — 409 if it collides with an existing different profile (no silent merge) |

Name matching is case-insensitive server-side ("Kunal"/"kunal" always collapse into one profile) — never de-dupe client-side.

### Friends
```jsonc
// GET /friends
[{ "id": 3, "username": "kunal", "nickname": "Buddy" }]  // nickname is your own private alias for them, may be null
```
| Method | Path | Body |
|---|---|---|
| POST | `/friends/add` | `{friend_code}` |
| DELETE | `/friends/<id>` | — unfriend, both directions |
| POST | `/friends/<id>/nickname` | `{nickname}` — empty string clears it |
| GET | `/friends/<id>/mood` | — today-only timeline, oldest first: `{friend_id, entries: [{mood_label, mood_score, created_at}]}` |

### Speakers (autocomplete for "who is this?" during a live session)
```jsonc
// GET /speakers
[{ "id": 12, "name": "kunal" }]
```

## 6. Chat endpoints

| Method | Path | Body | Notes |
|---|---|---|---|
| POST | `/chat` | `{prompt, conversation_id?, transcript?}` | Scoped to one conversation. `transcript` is only for an in-progress live session not yet saved. |
| GET | `/chat/global` | — | Full history of the persistent cross-session thread (survives logout — it's DB-backed, not session state) |
| POST | `/chat/global` | `{prompt}` | If the prompt names a known person (matched against your `/speakers` list), retrieval scopes to every conversation they've appeared in (up to 6 most recent); otherwise falls back to your 6 most recent conversations overall. Response: `{reply, matched_speaker, conversations_used}` |

`/chat/global` is a single persistent thread per user, not per-conversation — there's exactly one, always available, not tied to any specific call/session.

## 7. Voice calling

Real-time 1:1 and group voice calls via **LiveKit** (LiveKit Cloud — no self-hosted media server involved). Calling is **friends-only**, capped at **8 participants** per call.

### Lifecycle
| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/calls` | `{friend_ids: [...]}` (1 for 1:1, up to 7 for group) | `{call_id, room_name, token, livekit_url}` — you're auto-joined as `joined`; each invitee gets pushed an `incoming_call` event (see §8) |
| POST | `/calls/<id>/join` | — | Same shape as above, with your own token |
| POST | `/calls/<id>/decline` | — | `{ok: true}` |
| POST | `/calls/<id>/leave` | — | `{ok: true, call_ended: bool}` — `call_ended` is true once nobody remains `joined` |
| POST | `/calls/<id>/recording` | multipart `audio` file | See below |

`token` is a LiveKit access token (JWT) — pass it straight to the LiveKit Flutter SDK's room-connect call along with `livekit_url`. 4-hour TTL.

### Recording → transcript (important architectural note)
The web client does **not** use LiveKit's server-side Egress feature for recording (that requires S3/GCS storage + IAM credentials, deliberately avoided). Instead: **the call initiator's browser mixes its own mic with every remote participant's audio into one combined stream client-side, records it, and uploads the result** to `/calls/<id>/recording` when the call ends. The backend runs that upload through the same Deepgram diarized-transcription path as `/transcribe`, then creates **one conversation per call participant** (not just the uploader — `conversations.user_id` is single-owner, so without this only the uploader would see it), each independently titled and analyzed. If Deepgram detects no speech at all, nothing is created (`{"conversation_id": null, "skipped": "no_speech_detected"}`) — no empty junk conversations.

**This client-side mixing step has no clean Flutter equivalent** — the web app leans on the browser's Web Audio API (`AudioContext` + `MediaStreamAudioDestinationNode`) to combine multiple WebRTC audio tracks into one recordable stream, which doesn't have a direct native-mobile counterpart via `livekit_client`. Two ways to handle this — **pick one deliberately, don't guess**:
1. **Simplest, recommended**: skip mixing entirely — have *every* participant's device record only its own local mic (any standard Flutter audio recorder), and have *every* participant upload their own recording to `/calls/<id>/recording` instead of just the initiator. This needs a **small, explicit backend change** (currently the endpoint accepts one upload per call, first one wins via the `already_processed`/`calls.conversation_id` guard) to instead accept one recording per participant and either transcribe each separately or concatenate them before the single Deepgram call. Flag this back rather than silently working around the existing one-upload assumption.
2. **Matches the web app exactly**: find a Flutter/native audio-mixing approach (e.g. platform channels doing the mixing natively per-OS) to replicate combined-stream recording. More faithful to today's backend contract, meaningfully more implementation work.

Given the backend already tolerates "no speech detected → skip," option 1 degrades gracefully even if only some participants' recordings have content.

### Recording consent
Every participant must see a clear "this call is being recorded" notice before/while connected — matches the web client's `CallOverlay`. Not enforced server-side; it's a client UX requirement.

### Audio playback (a real bug hit and fixed in the web client — avoid repeating it)
Remote participants' audio tracks must be explicitly attached to playback for **every** participant, not just whoever's doing the mixing/recording — those are two separate concerns. In `livekit_client` terms: on every subscribed remote audio track, actually render/play it (the Flutter SDK's video/audio renderer widgets handle this) regardless of whether that device is also recording for transcription purposes. Also test on iOS specifically — browsers (and by extension WebView-based flows) on iOS block audio that isn't tied closely enough to a real user gesture; native Flutter audio playback via `livekit_client` on a real device should not have this issue the way the web client did, but verify.

### Suggested UX (matches the web client, adapt freely)
- Call button per friend (1:1) + a multi-select "start group call" flow, both from a Friends screen.
- Incoming call: global overlay/screen with Accept/Decline, visible regardless of what screen is active — driven by the `incoming_call` push (§8). Play a ringtone while it's pending.
- In-call screen: participant count + names, mute toggle, leave button, the recording notice. Show a transient "X dropped from the call" notice when someone disconnects (auto-expire after a few seconds).
- **Auto end for 1:1**: if the only other participant leaves, automatically hang up your own side too rather than lingering alone in an empty room.
- After the call: nothing extra to build — the resulting conversation(s) just appear in the normal Chats list via the `call_conversation_ready` push.

## 8. WebSocket: `/ws/notify` (badges + call signaling, connect once, app-wide)

```
ws(s)://rapexapi.nodexdata.click/ws/notify?token=<jwt>
```
No client→server messages. Server pushes (queued up to 50 events per user while disconnected, flushed on reconnect):
```jsonc
{ "type": "task_created", "task_id": 15, "description": "..." }
{ "type": "chat_message", "conversation_id": 28 }                 // new message in a per-conversation thread
{ "type": "call_conversation_ready", "call_id": 4, "conversation_id": 29 }  // your copy of a call's transcript is ready — treat identically to chat_message
{ "type": "incoming_call", "call_id": 4, "room_name": "call-...", "caller_id": 8, "caller_name": "amit" }
```

## 9. WebSocket: `/ws/listen` (solo live dictation — unchanged)

```
ws(s)://rapexapi.nodexdata.click/ws/listen?token=<jwt>[&conversation_id=<id>]
```
Client→server: raw binary audio chunks, streamed continuously; optional `{"type": "rename_speaker", "speaker_index": 0, "name": "Amit"}`.

Server→client:
| type | fields |
|---|---|
| `session_started` | `conversation_id` |
| `transcript` | `speaker_index`, `line` |
| `new_speaker` | `speaker_index` |
| `speaker_renamed` | `speaker_index`, `name` |
| `background_update` | `tasks_found`, `speakers_found`, `tags` (array of short topic strings — see below) |
| `audio_classification` | `label` (`music`\|`speech`\|`unclear`) |
| `error` | `message` |

`tags` on `background_update`: short (1-4 word) topic suggestions extracted from the recent transcript, meant as tappable chips that pre-fill a `/chat` question when tapped (dismissible without sending). No extra request needed — piggybacks on the same analysis pass.

⚠️ **Same codec risk as before**: the backend relays audio straight to Deepgram assuming WebM/Opus (matching browser `MediaRecorder` defaults) with no explicit encoding param. A Flutter recorder may not produce that by default — either find an Opus-in-WebM/Ogg-capable recorder, or flag back for a small backend change (`encoding=`/`sample_rate=` query params) rather than guessing. This is the same open question the original spec raised — still unresolved, still worth deciding deliberately.

## 10. Design tokens (unchanged)

Modern WhatsApp Web palette — `frontend/src/theme.css` is the source of truth:
```
bg-app #F0F2F5 · panel #FFFFFF · chat-bg #EFEAE2 · bubble-out #D9FDD3 · bubble-in #FFFFFF
text #111B21 · text-soft #667781 · accent #00A884 · accent-dark #008069
border #E9EDEF · danger #DC3545
```
System font stack, solid-color circular avatars with initials (no photos).

## 11. Screens (add to the original 6 — Chats, Chat Thread, Tasks, Profiles, Friends, Settings)

- **Persona onboarding** — blocking, one-time per account (§4).
- **Incoming call overlay** — global, §7.
- **In-call screen** — global, §7.
- **Global chat** — a persistent thread, accessible from Chats (e.g. a chat-bubble icon near wherever "start a new recording" lives). Same bubble UI as per-conversation chat, backed by §6's `/chat/global`.
- **Settings** gains: custom category management (list + add + delete, §3).
- **Tasks** rows get an inline edit affordance (description/due_date) and a small icon deep-linking to the source conversation, in addition to complete/reopen.
- **Profiles** detail view gets rename + delete, and each observation deep-links to its source conversation.
- **Friends** rows get a call button, a nickname edit affordance, and remove/unfriend.

## 12. Explicit non-goals / don't touch

- Don't add, rename, or change any backend route/shape without flagging it back first.
- Don't replicate the web app's IndexedDB offline-search cache unless asked.
- `due_date` on tasks is intentionally free text — never build a date picker or try to parse/sort it as a real date.
- Don't try to solve the call-recording mixing question by guessing — it's flagged explicitly in §7 because it's a real fork with no single obviously-right answer for a native app.
