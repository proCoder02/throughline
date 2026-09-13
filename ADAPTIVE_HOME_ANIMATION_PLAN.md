# Adaptive Home-Screen Motion (Flutter)

## Status: v1 + v2 implemented, v3 proposed

- **v1** (`time_bucket` + `mood_bucket` → `MotionProfile`): done. Backend:
  `mood_label_to_bucket()` + `GET /me/home-signals` in [app.py](app.py).
  Flutter: `lib/motion/motion_profile.dart` (the enums, the `profileFor`
  table, the `motionSpecs` constants), `HomeSignalsService`, `FadeSlideIn`
  now takes an optional `profile` param (defaults to `MotionProfile.balanced`,
  whose spec is deliberately identical to the widget's original hardcoded
  constants), wired into `ChatsScreen` (the real home screen).
- **v2** (`chat_tone`): done. Backend: `POST /chat/global/wrap-up` +
  `users.last_chat_tone`/`last_chat_tone_at` (see schema.sql), folded into
  the same `GET /me/home-signals` response with a 3-hour recency cutoff.
  Flutter: `GlobalChatScreen` calls `HomeSignalsService.wrapUpGlobalChat()`
  from `dispose()`, fire-and-forget, only when at least one text exchange
  happened this visit (`_hadExchange`). Not yet covered: the image-description
  exchange path (`_sendImage`) doesn't set `_hadExchange` — scoped out for
  now since the classification prompt is written for a conversational
  back-and-forth, not an image caption.
- **v3** (deterministic priority slot): proposed, not started — see below.
- **Sandbox-only additions** (Settings → Experimental → Home screen
  concept), not applied to the real `ChatsScreen`:
  - **Mood-companion face images**: `throughline/assets/mood_faces/*.png`,
    7 files from Google's Noto Emoji (Apache 2.0, see that folder's
    `NOTICE`), mapped from `chat_tone`/`mood_bucket` by `_faceAssetFor()`
    in `home_preview_screen.dart`. Answers "can there be an image/GIF
    based on the JSON signal" — yes, and the mechanism is the same
    discipline as everything else here: the signal stays a plain string,
    the string-to-asset mapping is a local Dart table, never a server-sent
    image URL or spec.
  - **Time-period background theme**: a `HomeThemeSpec` table
    (background gradient + ambient-motion-intensity base + greeting
    style/copy) keyed by `TimeBucket`, with `MoodBucket` multiplying the
    ambient intensity on top — the buildable subset of a much larger
    "theme engine" proposal (background/surface/blur/accent/content-
    density/weather/day-of-week, all LLM-selected from presets). Kept to
    background gradient + greeting text + motion intensity only, and
    deliberately confined to this sandbox screen: the real `ChatsScreen`
    already has a shipped, hand-designed warm-gradient brand look
    (`AppColors.dmGradient`) used consistently across every main-tab
    screen, and swapping it for a cycling per-time-period palette is a
    real product-identity decision that shouldn't happen as a side effect
    of a motion feature. Explicitly not built: `cognitive_load` (still no
    data source), weather/day-of-week (need new external
    dependencies/decisions of their own), content density/priority
    reordering (that's v3's job, and v3 itself is intentionally a fixed
    rule list, not a themed layout system).

This started as a pure design decision record; v1/v2 above are now actually
built (see the Status section). v3 below is still just a proposal. The
whole thing responds to a much larger original proposal (a fully server-driven
JSON animation DSL keyed on time-of-day + mood + cognitive load + user
activity + event type) with a deliberately smaller design that keeps the
same philosophy — **motion that recedes as the user's state gets busier or
harder, instead of a flat "morning vs. night" switch** — without the
infrastructure a generic animation interpreter would need.

## Why not the full JSON animation engine

The original proposal is a real, coherent architecture (context → animation
policy JSON → a Flutter interpreter that doesn't know *why* it's animating a
certain way). It's the right shape for a team maintaining many screens with
a dedicated motion/design system owner. For this app specifically, three
things make it the wrong scope right now:

- **"Cognitive load" doesn't exist anywhere in the data model yet.** Mood
  does (`mood_logs`, `compute_compiled_mood` in [app.py](app.py)) — cognitive
  load would need its own heuristic or classifier built from scratch (message
  frequency? task backlog? something else?) before it could drive anything.
  That's a separate project with its own open questions, not a free extra
  dimension to slot into an animation schema.
- **A generic interpreter is a lot of surface for ~10 animations.** Versioned
  JSON schema, a Flutter-side interpreter for `fade`/`slide_fade`/`soft_pop`/
  `slow_draw`/etc., a combinatorial test matrix across
  time × mood × load × activity × event — that's real ongoing maintenance
  for something users will only ever perceive as "feels calm" or "feels
  lively." Apple/Google-level restraint usually comes from a handful of
  fixed curves and durations chosen once, not from remote-controlled
  animation specs recomputed per render.
- **The app already has zero network dependency in its motion**, and that's
  worth keeping. [`FadeSlideIn`](../throughline/lib/widgets/fade_slide_in.dart)
  (chats/tasks list entrance) and the DM/friend-profile screens' gradient
  "contact-card" restyle are both plain local Flutter widgets today — no
  round trip, nothing to cache-and-fall-back-on because there's nothing to
  fetch. A server-driven engine would need to re-introduce exactly the
  fallback/caching discipline the proposal itself calls out as a hard rule
  ("never make the user wait for the server") — simplest way to satisfy that
  rule is to not have the render path depend on the network in the first
  place.

## The scoped version: signals, not specs

Keep the "why" server-side and the "how" entirely client-side:

- The backend exposes a small number of **coarse, cheap signals** it can
  already compute or trivially derive — not animation instructions.
- Flutter maps those signals to a **small, fixed set of local presets**
  (plain `AnimationController`/`Tween` widgets, same style as
  `FadeSlideIn`) — never anything parsed from a JSON animation vocabulary.
- If the signal fetch fails or hasn't returned yet, Flutter just uses its
  existing default motion (today's behavior) — there is no "broken" state
  to design around, because the fallback *is* the current app.

### Signals (v1)

| Signal | Values | Source |
|---|---|---|
| `time_bucket` | `morning` \| `afternoon` \| `evening` \| `night` | Computed **locally** in Flutter from the device clock — no backend round trip needed for this one at all; timezone-correct by construction. |
| `mood_bucket` | `positive` \| `neutral` \| `low` \| `stressed` | Derived server-side from the current 2-hour compiled mood window (`mood_bucket_bounds`/`compute_compiled_mood`, already used by `/friends/<id>/mood`), collapsing the existing 8 raw `mood_label` values (`happy`, `excited`, `calm` → `positive`; `neutral` → `neutral`; `sad` → `low`; `stressed`, `anxious`, `frustrated` → `stressed`). No new table — a `null` bucket (nothing logged yet this window) just means "use the default preset," same as today. |
| `returning` | `true` \| `false` | Client-computed: was the app backgrounded for >N minutes before this resume? No backend involvement. |

`cognitive_load` and generic `event`-keyed overrides (new message / new
insight / task / recommendation) are explicitly **out of scope for v1** —
see below.

### Signal (v2, proposed): `chat_tone` — from a chat that just ended

This answers a specific follow-up idea: have the LLM react to a finished
chat conversation and reflect that on the home screen. The mechanism
matters more than the idea here — the wrong version of this asks the LLM
for raw animation parameters (duration/curve/scale); the version below asks
it for one more small enum, same discipline as `mood_bucket` above.

**What "a conversation that got over" means here (a decision, not left
open):** the **global assistant Q&A chat** (`/chat/global`, `chat_messages`
table) — not a DM (two people, never really "ends") and not a Listen
recording session (`run_background_analysis` already fires repeatedly as a
transcript streams in, so it has no single clean end to hook). The global
chat screen being closed/backgrounded is a clean, purely client-side signal
that an exchange is done, with no new session-lifecycle concept needed
server-side.

**Mechanism:**
1. New endpoint `POST /chat/global/wrap-up`. Client calls it once,
   best-effort/fire-and-forget (failure is silently ignored, same as
   `trigger_chat_feedback_extraction`), when leaving the chat screen — only
   if at least one exchange happened, skipping entirely on an empty visit
   (same "don't spend a call on nothing" discipline as that function's
   closing-acknowledgment/just-a-question skips).
2. Server takes the last ~8 messages of that thread and asks the LLM
   (`call_llm`, already used everywhere else in app.py) for exactly one
   field, same "reply with ONLY this JSON" shape discipline as
   `build_analysis_prompt`:
   ```
   {"tone": "resolved" | "celebratory" | "heavy" | "routine"}
   ```
3. **Validate hard.** The reply must be exactly one of those four strings —
   anything else (malformed JSON, an invented fifth value, empty reply) is
   discarded and nothing is stored. A bad classification is a silent
   no-op, never a broken or garbled home screen. This is the actual fix for
   the "LLM emits raw animation JSON" risk: constrain the output space to
   four known-safe strings instead of open-ended parameters.
4. Store only the latest value — a `last_chat_tone` / `last_chat_tone_at`
   pair is enough (single fact per user, same shape as how
   `profile_picture_url` was added directly to `users` rather than its own
   table). Expose it with a recency cutoff (e.g. 3 hours), same discipline
   as `compute_compiled_mood`'s bucket window — a heavy conversation from
   yesterday must not still be coloring today's home screen.
5. Fold it into the same signal endpoint as `mood_bucket` above (or a
   sibling `GET /me/chat-tone`) — still just a string, never a spec.

**Flutter mapping — reuses `MotionProfile`, doesn't add a second
vocabulary:**

| `tone` | Effect |
|---|---|
| `celebratory` | `MotionProfile.lively` for this load; hero card gets one unprompted "pop" (the mood-companion tap-reaction, played once automatically instead of waiting for a tap) |
| `heavy` | `MotionProfile.gentle`; hero copy softens ("Whenever you're ready" instead of a prompt) |
| `resolved` | `MotionProfile.balanced`; one small checkmark-style settle on the hero card |
| `routine` | No override — falls through to `mood_bucket`/`time_bucket` exactly as v1 |

Cost is bounded by how often someone actually finishes a chat with content
in it, not by message volume — one extra `call_llm` per such session, not
per message.

### Backend surface

One new lightweight endpoint (or folded into an existing one the home
screen already calls, if there is one — worth checking before adding a new
route): `GET /me/mood-bucket` → `{"mood_bucket": "positive" | "neutral" |
"low" | "stressed" | null}`. Deliberately minimal: a plain string, not a
JSON object describing motion — the client decides what a "positive" home
screen looks like, the server only ever says what mood bucket the user is
compiled into right now. This mirrors `/friends/<id>/mood`'s existing
`compute_compiled_mood` call, just for the signed-in user instead of a
friend.

### Flutter mapping (illustrative, not final)

```dart
// Plain Dart, no JSON parsing involved -- this table lives in code review,
// not a schema a server can silently change underneath the app.
MotionProfile profileFor(TimeBucket time, MoodBucket mood) {
  if (mood == MoodBucket.stressed) return MotionProfile.minimal;   // calm down, not up
  if (time == TimeBucket.night) return MotionProfile.minimal;
  if (mood == MoodBucket.low) return MotionProfile.gentle;
  if (time == TimeBucket.morning && mood == MoodBucket.positive) return MotionProfile.lively;
  return MotionProfile.balanced; // the current, always-safe default
}
```

`MotionProfile` is 3-4 named presets (`lively`, `balanced`, `gentle`,
`minimal`), each just a small bundle of duration/curve/stagger constants
consumed by widgets like `FadeSlideIn` (e.g. `lively` staggers list rows
with a small overshoot curve; `minimal` is a plain fast fade, no stagger,
no scale). No new widget vocabulary — existing entrance animations gain a
`MotionProfile` parameter instead of their currently-hardcoded constants.

## v3 (proposed): a deterministic priority slot — not a ranking engine

Responds to a further escalation of the original proposal: a full "Home
Experience Engine" that scores every possible card (`relevance + recency +
importance + unfinished_action + contextual_fit - cognitive_cost`), ranks
them, and reorders/recomposes the whole home screen per load. That's a real
recommendation-system project — every term in that formula needs its own
definition and data source before it means anything, tuning weights without
real usage data is guessing, and reordering an AI-companion app's home
screen unpredictably risks feeling unsettling rather than delightful (this
app's whole value is calm, not a feed). None of that is v3.

**What v3 actually is**: one optional, deterministic slot on the home
screen, filled by a **fixed rule list**, never a scored/weighted formula —
same "table, not a formula" discipline as `MotionProfile` above:

```dart
// Plain Dart, checked top to bottom, first match wins -- no scores, no
// weights, nothing tuned. Returns null (slot stays empty) if nothing
// currently qualifies -- an empty slot is a fine, safe outcome.
HomeSlotContent? prioritySlotFor(HomeSignals s) {
  if (s.hasUnfinishedTask) return HomeSlotContent.unfinishedTask;
  if (s.chatTone != null && s.chatTone != ChatTone.routine) return HomeSlotContent.conversationMoment;
  if (s.hasFreshWeeklyDigest) return HomeSlotContent.weeklyInsight;
  if (s.mood != null) return HomeSlotContent.moodTrend;
  return null;
}
```

- **Card order everywhere else on the home screen stays exactly as it is
  today** — this only ever decides what fills *one* optional slot, never
  reshuffles the rest of the screen. That's the direct fix for the
  unpredictability concern above.
- Every input (`hasUnfinishedTask`, `hasFreshWeeklyDigest`, etc.) must
  already be a real, cheap, existing query — `tasks` (status = 'open'),
  the weekly digest's own "not yet viewed" flag (`get_and_mark_weekly_digest_viewed`),
  `mood_bucket`/`chat_tone` from v1/v2. No new signal category is invented
  just for this — v3 is a consumer of what already exists, not a reason to
  build `cognitive_load` or behavioral tracking.
- "Home Moments" style generated copy ("You've been discussing Project
  Phoenix quite a bit") stays explicitly out of scope — it needs real
  cross-conversation topic clustering, a separate feature with its own
  design doc, not something v3's rule list can produce.
- Diffing/animate-only-what-changed (the other genuinely good idea from
  the escalated proposal) applies naturally here once there's an actual
  slot that can change contents between loads — the slot's `HomeSlotContent`
  changing is itself the `chat_tone`-style "what changed" signal that
  decides whether to play an entrance animation for it at all.

Not started. Worth doing only once v1/v2 have been used for real and it's
clear a single contextual slot would add something the always-visible mood
trend/weekly insight cards don't already cover.

## Explicit non-goals for v1

- No server-authored JSON animation schema/DSL, no Flutter-side interpreter.
- No `cognitive_load` signal — needs its own design doc once there's an
  actual definition and data source for it.
- No open-ended per-event animation overrides (new message / new insight /
  task / recommendation reacting individually) — v1 only touches
  whole-screen entrance motion (list stagger, hero fade-in). The one
  event-driven exception is the bounded, hard-validated `chat_tone` signal
  above (v2) — a single four-value enum through the existing `MotionProfile`
  table, not a general per-event animation system.
- No animation behavior that can silently change without a client app
  update — the mapping table lives in Dart, reviewed like any other code
  change, not fetched as data.

## If this direction is confirmed, suggested order of work

1. Add `mood_bucket` derivation + `GET /me/mood-bucket` (small, additive,
   mirrors existing `/friends/<id>/mood` code).
2. Add `MotionProfile` + the 3-4 presets in Flutter, wire `time_bucket`
   (local-only, no backend dependency) into `FadeSlideIn` first, since it's
   the one entrance animation already shared across screens.
3. Wire `mood_bucket` in once (1) exists; verify the "stressed → calmer,
   not louder" case specifically, since that's the one behavior this whole
   design exists to produce.
4. Revisit `cognitive_load` and open-ended per-event motion only if (1)-(3)
   actually feel meaningfully better in real use — not before.
5. If `chat_tone` (v2) is worth building: add `/chat/global/wrap-up` +
   the strict 4-value validation, store only the latest value, surface it
   through the same signal endpoint as `mood_bucket`, and map it through
   the existing `MotionProfile` table — no new rendering vocabulary, no
   raw animation parameters ever leaving the LLM call.
