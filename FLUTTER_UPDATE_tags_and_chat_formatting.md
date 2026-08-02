# Throughline — Flutter update: live tags + chat reply formatting

Delta on top of `FLUTTER_APP_SPEC.md` (§6 Chat endpoints, §9 `/ws/listen`). Five related changes just shipped to the live backend at `https://rapexapi.nodexdata.click` — **no new/changed endpoints, no schema changes, no `/ws/listen` message-shape changes**. Everything below is client-side behavior to match, plus one prompt-driven change in what `reply` strings now contain.

## 1. Live topic tags — cap at 5, expire after 20s

`background_update` messages on `/ws/listen` still look the same:
```jsonc
{ "type": "background_update", "tasks_found": 2, "speakers_found": 1, "tags": ["What about the marketing spend?", "budget approval"] }
```
The server already sends at most 5 tags *per message*, but a session gets multiple `background_update` pushes over time, and the client accumulates tags across all of them. The Flutter client must independently:
- **Cap the displayed/accumulated set at 5** — when a new batch of tags arrives, append the new (deduped, case-insensitive) ones and drop the oldest beyond 5, e.g. `[...prev, ...fresh].slice(-5)` equivalent.
- **Auto-expire each tag 20 seconds after it appears**, even if the user never taps or dismisses it — schedule a timer per tag on arrival that removes it from the visible set after 20000ms (a no-op if it was already removed by a tap/dismiss/cap-eviction by then).

Tapping a tag still works exactly as documented — send the tag's text as the chat prompt with the usual conversation context (no API change).

## 2. Tag content now prefers direct questions

No shape change, but expect different *content*: the server now prioritizes an actual question raised in the transcript (e.g. `"What about the marketing spend?"`) over an abstract topic phrase (e.g. `"marketing spend"`), when the transcript contains one. Nothing to build differently — just don't assume tags are always short noun phrases; they may be full questions, so give the tag chip enough width/wrapping to show one.

## 3. Don't show the raw transcript in the chat screen

The web app removed its "view transcript" panel from the chat/conversation screen entirely — it was judged unnecessary now that relevant transcript lines get quoted directly in chat replies (see #4). **Do not build a "view transcript" affordance inside the chat thread UI.**

This is purely a UI decision, not an API one: `GET /conversations/<id>` still returns `raw_transcript` (still needed as LLM context when calling `/chat` with `conversation_id` unset, or resuming a live session) — just don't surface it as a standalone transcript view in the chat screen. If you want a transcript view for some other reason (e.g. a dedicated "details" screen, export, search), that's a legitimate independent choice — just keep it out of the chat thread itself.

## 4. Chat replies: verbatim quotes + markdown formatting

Both `POST /chat` and `POST /chat/global` now instruct the LLM to:
- **Quote a transcript line verbatim** when it directly supports the answer, attributed to the speaker: `Speaker said: "the exact line"` (uses whatever name/label the transcript uses — a real name if the speaker was identified, otherwise `Speaker 0`/`Speaker 1` etc.).
- **Format the reply as markdown**: `**bold**` for key terms/names/numbers, and `- ` bullet lines when listing more than one item. Short, single-fact answers stay as plain prose (no forced bullets).

Example live `reply` string today:
```
The agreed **budget cap** was **$50,000**.

Other points discussed:

- **Hiring** – Speaker 1 suggested "revisit hiring."
- **Marketing spend** – Speaker 1 said it should stay flat at **$10,000**.
```

**The client must render this markdown** — at minimum `**bold**` and `- ` bullet lists — instead of showing the raw asterisks/dashes as literal text. The web client wrote a tiny ~30-line custom renderer for just these two constructs (`frontend/src/components/FormattedText.jsx`) to avoid a new dependency, since that was a bundle-size concern there. That constraint doesn't really apply to Flutter — using an existing markdown package (e.g. `flutter_markdown` on pub.dev) is the simpler, more robust choice and is fine even though it supports more than these two constructs; a small custom parser mirroring the same two rules also works if you'd rather avoid the dependency. Either way, the *rules to support* are just bold and bullets — nothing more elaborate is being sent.

Applies to both the per-conversation chat thread and the global cross-session chat thread (`/chat/global`) — same formatting instruction on both.

## Nothing else changed

No new fields on `/persona`, `/categories`, `/tasks`, `/profiles`, `/friends`, `/calls`, or their WebSocket events — everything else in `FLUTTER_APP_SPEC.md` still applies as-is.
