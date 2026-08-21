# Cognitive Sharing / Common-Ground Intervention — Implementation Plan

## Context

The idea: when two friends are messaging each other, let their private cognitive
memory (facts/preferences/beliefs/memories — the `emotional_intelligence` schema
already built and live in this app) collaborate, under explicit per-relationship
permission, to surface something that helps *both* of them — not just answer
"what should I reply," but "what could help both of these people right now."

Closest mainstream precedents (from market research done before this doc):
Google Messages' contextual suggestions (private to the viewer, not
necessarily inserted as a message — a useful UI precedent) and Microsoft
Teams Copilot's permission-aware grounding (private preview before anything
becomes visible to a group). Neither combines *both* people's private
context with a bilateral, per-relationship consent model — that combination
is the new part here.

Positioning matters: don't market this as "AI that reads both people's
private data." Frame it as *"let your AI collaborate with the AI of people
you trust"* — off by default, tiered, revocable, a first-class relationship
setting rather than a buried privacy checkbox.

## What already exists that this builds on

This is not a new subsystem bolted onto the app — almost every piece already
exists for a different purpose and gets reused here:

- **Per-user private cognitive memory**: `emotional_intelligence.facts/
  preferences/beliefs/memories`, resolved per real user via the
  `app_user:<user_id>` subject convention (`ei_adapter.py`'s
  `_resolve_subject_id`, `real_user_extraction.py`'s `ensure_user_subject`).
  Nothing new needed here — this data already accumulates from every
  dictated conversation and chat correction.
- **Combining two people's memory into one block**: already built —
  `assemble_multi_subject_memory(cur, subject_names, question)`
  (`cognitive_reasoning_demo.py:176-191`), used today by
  `relationship_batch.py` to produce `relationship_profiles` rows. It
  concatenates each subject's own memory block, clearly labeled/separated,
  with an explicit instruction (`REASONING_SYSTEM_PROMPT`) not to invent
  shared history neither side's own memory mentions. This is the *exact*
  "Cognitive A + Cognitive B → combined context" step from the concept
  diagram — reused as-is, not rebuilt.
- **The live A↔B surface**: `direct_messages` (schema.sql:341+) and
  `POST /friends/<friend_id>/messages` (`send_direct_message`, app.py:3483)
  — the real 1:1 chat between friends this app already has.
- **The trust boundary**: `friendships` (schema.sql:89-96) — directional,
  `UNIQUE(user_id, friend_id)`, a real friendship has one row per direction.
  This directionality is a good fit for a *bilateral* permission model: each
  side sets their own sharing level independently, and both sides must
  consent for anything to activate.
- **Fire-and-forget background trigger pattern**: `trigger_chat_feedback_extraction`
  (`ei_adapter.py:649+`) — called from a background thread right after a
  response is already on its way, own DB connection, never raises, never
  blocks the user-facing request. The trigger for this feature follows the
  same shape.
- **Delivery**: `push_notification`/`send_fcm_to_user` with typed events
  (`chat_message`, `task_created`, etc.) — a new event type is the natural
  delivery mechanism, not a message inserted into the DM thread itself
  (matching Google Messages' "visible only to you" precedent cited above).

## New pieces needed

### 1. Bilateral permission table (`public`, next to `friendships`)

```sql
CREATE TABLE IF NOT EXISTS cognitive_sharing_settings (
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    friend_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    level TEXT NOT NULL DEFAULT 'off' CHECK (level IN ('off', 'limited', 'collaborative')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, friend_id),
    CHECK (user_id <> friend_id)
);
```

One row per *direction*, matching `friendships`' own shape — `user_id`'s row
is what `user_id` has permitted for this friendship, independent of what
`friend_id` has set on their own side. Defaults to `'off'` for every
friendship, including existing ones (no row = off; only insert on explicit
opt-in, don't backfill).

- `off` — never included in any cross-user reasoning. (Default.)
- `limited` — this user's cognitive context may be *read* into a
  common-ground reasoning pass involving this friend, but the suggestion
  produced is generic/non-attributable (see prompt design below — never
  quote one side's private fact back verbatim to the other).
- `collaborative` — same as `limited`, plus the suggestion may reference
  *shared, already-mutually-known* context more directly (e.g. things
  already said in the DM thread itself, combined with private memory) to
  produce a more concrete/actionable suggestion.

**Activation rule: both directions must be at least `limited`.** If either
side is `off`, nothing fires for that pair, full stop — this is the
"bilateral" part of the design, and it's a hard gate checked before any LLM
call touches either person's data, not an afterthought filter on the output.

### 2. Suggestion storage/delivery table

```sql
CREATE TABLE IF NOT EXISTS cognitive_suggestions (
    id SERIAL PRIMARY KEY,
    user_a INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    user_b INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    suggestion_text TEXT NOT NULL,
    source_message_id INTEGER REFERENCES direct_messages (id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    shown_to_a_at TIMESTAMPTZ,
    shown_to_b_at TIMESTAMPTZ,
    dismissed_by_a BOOLEAN NOT NULL DEFAULT false,
    dismissed_by_b BOOLEAN NOT NULL DEFAULT false,
    CHECK (user_a < user_b)
);
```

Canonical `user_a < user_b` ordering, matching `relationship_profiles`'
existing convention (`schema_relationship_intelligence.sql`) — one row per
pair per suggestion, not two duplicated rows. This is deliberately a
**separate surface from `direct_messages`**, not a message either party
"sent" — delivered via a new `push_notification` event type
(`"cognitive_suggestion"`), shown as a dismissible card/banner alongside the
DM thread, not inserted into it. Each side can dismiss independently
(`dismissed_by_a`/`dismissed_by_b`) without affecting what the other sees.

### 3. Reasoning function (`emotional_intelligence/cognitive_sharing.py`, new file)

```python
def generate_common_ground_suggestion(cur, user_a_id, user_b_id, recent_dm_context):
    """Only called after the bilateral permission gate has already passed.
    Reuses assemble_multi_subject_memory (cognitive_reasoning_demo.py) for
    the actual cross-person context assembly -- this function is just the
    permission-gated entry point + a purpose-specific prompt."""
```

- Resolve both subjects via the existing `app_user:<id>` convention
  (reuse `real_user_extraction.ensure_user_subject`, read-only lookup here
  — mirrors `relationship_batch.py`'s own `fetch_real_user_subject_map`,
  which deliberately never creates a subject a batch job hasn't earned).
- Call `assemble_multi_subject_memory(cur, [name_a, name_b])` — unchanged,
  as-is.
- New prompt, distinct from `REASONING_SYSTEM_PROMPT` (that one answers a
  *question*; this one looks for an *opportunity*): given both people's
  memory and the recent DM exchange, is there something — a shared
  interest, a scheduling opportunity, a relevant fact one side has that
  would help the other — worth surfacing? Explicit instructions, non-
  negotiable:
  - **Never quote or closely paraphrase one side's private fact back
    verbatim** — e.g. never "B mentioned they're free Friday" if that came
    from B's private memory and wasn't said in this DM thread. Synthesize
    into a *suggestion*, not a leak: "Friday might work well for both of
    you" is fine; "B told their assistant they're free Friday" is not.
  - Return nothing (empty/null) far more often than something — most
    exchanges have no genuine common-ground opportunity, and manufacturing
    one every time is worse than staying silent (mirrors
    `CHAT_FEEDBACK_PROMPT`'s own "when in doubt, extract nothing" instinct,
    which is proven to work well in this codebase already).
  - Ground everything in what's actually in the assembled memory or the DM
    thread — never invent shared history, matching the discipline already
    in `REASONING_SYSTEM_PROMPT`.

### 4. Trigger point

**v1: on-demand only**, not automatic. Either participant in a DM thread can
tap an explicit "find common ground" action. This is the safest possible v1
— no background LLM spend, no risk of an unwanted suggestion appearing
unprompted, and it makes the consent moment doubly explicit (permission
setting *and* an explicit ask). New endpoint:

```
POST /friends/<friend_id>/cognitive-suggestion
```

- Checks both directions of `cognitive_sharing_settings` are ≥ `limited`
  (403 with a clear message if not — "ask them to turn on cognitive sharing"
  / "turn on cognitive sharing yourself" depending on which side is missing).
- Pulls recent `direct_messages` between the pair (same pattern as
  `/chat/global`'s `GLOBAL_CHAT_DM_LIMIT` context, app.py:4518-4534) as
  `recent_dm_context`.
- Calls `generate_common_ground_suggestion`.
- If non-empty, inserts one `cognitive_suggestions` row, pushes
  `"cognitive_suggestion"` to *both* participants via `push_notification`.
- If empty, returns a plain "nothing to suggest right now" response — no
  row inserted, no notification sent, matching the "return nothing most of
  the time" instinct above.

**Deferred to a later phase, not v1**: automatic background triggering
(mirroring `trigger_chat_feedback_extraction`'s fire-after-every-message
pattern) with a cooldown (e.g. at most once per pair per hour) to avoid
spam/cost. Revisit once the on-demand version has real usage data on how
often a genuine opportunity actually exists — building the automatic,
higher-volume version before knowing that would be guessing at a rate limit
with no evidence behind it.

### 5. Permission UX

Surface this as a named, first-class per-friend setting, not a buried
checkbox — matching the "Cognitive Sharing with `<name>`" framing from the
positioning research. Lives on the friend detail/settings view:

- **Off** (default) — "Your private cognitive information stays private."
- **Limited** — "AI may use selected context to find mutually useful outcomes."
- **Collaborative** — "AI can use approved context to actively help both of you coordinate."

Each side only ever sees/controls *their own* row in
`cognitive_sharing_settings` — never shown what level the other side has
set (that itself would leak information about the other person's privacy
posture); the only visible effect is whether the "find common ground" action
is available at all (requires both sides ≥ `limited`).

## Privacy/safety guardrails (non-negotiable, check before shipping any phase)

1. Bilateral gate checked in the endpoint itself, before any subject
   resolution or LLM call — not just filtered out of the final output.
2. The reasoning prompt's "never quote private facts verbatim" rule is the
   single most important constraint in this whole feature — needs real
   adversarial testing before launch (try to get it to leak a specific
   private fact back to the other party) the same way `chat_feedback_extraction.py`'s
   "never trust an LLM-echoed id blindly" rule was validated, not just
   asserted in the prompt and trusted.
3. `dismissed_by_a`/`dismissed_by_b` independent — one person dismissing
   never removes it from the other's view without their own action.
4. Turning sharing to `off` must take effect immediately for *future*
   suggestions; it does not need to (and should not attempt to) retroactively
   delete past `cognitive_suggestions` rows — those already happened with
   consent that was valid at the time.
5. No raw fact/preference/belief/memory row is ever returned by the new
   endpoint's response — only the synthesized `suggestion_text`. The
   assembled memory block used to *generate* it never leaves the server
   process.

## Suggested build order

1. `cognitive_sharing_settings` table + the two settings endpoints
   (get/set own level for a friend) — no reasoning yet, just the permission
   primitive. **Definition of done**: both sides can independently set a
   level; a third endpoint (`GET .../cognitive-sharing-status`) correctly
   reports whether both sides are ≥ `limited` for a pair.
2. `cognitive_suggestions` table + `generate_common_ground_suggestion` +
   the on-demand `POST .../cognitive-suggestion` endpoint, gated on (1).
   **Definition of done**: manual test across a handful of real friend
   pairs with real accumulated cognitive memory — suggestions are either
   genuinely useful or (more often) correctly empty; adversarial prompt
   testing per guardrail #2 above passes.
3. Delivery UX — `push_notification` event type + frontend/Flutter card
   surface, independent dismiss per side. **Definition of done**: a
   suggestion appears to both participants without becoming a DM thread
   message, and dismissing it on one device doesn't affect the other
   participant's view.
4. *(Built, lighter than originally scoped)* Rather than server-side
   time-based cooldown polling (which would spend an LLM call on every
   opted-in pair every N minutes regardless of whether anything changed),
   the client tracks messages sent/received since the last check and
   auto-fires the *same* on-demand `POST .../cognitive-suggestion` endpoint
   once a threshold is crossed (`AUTO_CHECK_THRESHOLD` = 6 in both
   `DirectMessageScreen`/`DirectMessageThread.jsx`), with a lower
   `PULSE_THRESHOLD` (3) driving an attention-grabbing pulse animation on
   the trigger icon beforehand. Cost scales with actual conversation
   activity, not wall-clock time -- no server or schema change needed, since
   it's still the same endpoint, same bilateral gate, same "return nothing
   most of the time" behavior; only the caller became automatic.
