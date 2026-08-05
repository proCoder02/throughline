# Cognitive Extraction Pipeline — Implementation Plan

Derived from `AI_Relationship_Intelligence_Analysis.md`. This file originally
broke that vision into ordered, independently-shippable phases against the
main app's own users/conversations. **That target has since changed** — see
"Implementation status" immediately below before reading the phases.

## Implementation status (as of this writing)

The phases below were written for extending the *main app's* existing
per-conversation LLM call against real users. Since then, the research track
pivoted to a standalone dataset (Friends episode transcripts) and a working
prototype now exists in this folder, **entirely decoupled from `app.py` and
the phase numbering below** (per the standing "no backend/Flutter changes"
instruction). What's built and verified working:

- **Schema**: `emotional_intelligence_schema.sql` — all `CREATE TABLE
  IF NOT EXISTS` (idempotent, never drops anything; see the safety note
  inside that file about why). Identity model is schema-local, not tied to
  `public.users`/`public.conversations`: `subjects` (research-local
  character identity) and `episodes` (research-local "conversation" unit)
  replace those references entirely, because a TV episode has no
  corresponding app-user row and — unlike solo dictation — one scene has
  *many* speakers, not one owner. Tables are split accordingly:
  `conversation_summaries`/`topics`/`entities`/`events` are EPISODE-scoped;
  `facts`/`preferences`/`beliefs`/`memories`/`personality_snapshots` are
  SUBJECT-scoped. `analysis_runs` (extraction-run log: model, prompt_version,
  latency, tokens, status/error) and universal `analysis_run_id` provenance
  on every extracted row are both in from the start, addressing the
  "remaining gap" items (analysis log, prompt versioning, provenance) up
  front rather than retrofitted later.
- **Normalizer**: `normalize_speakers.py` — deterministic (strips stage-
  direction parentheticals, groups case/whitespace variants), writes an
  inspectable `speaker_aliases` mapping table rather than hiding name-
  matching in code. Collapsed 838 raw speaker labels to 776 subjects; the 6
  main characters correctly dominate by volume. A meaningful noisy tail
  remains (likely multi-speaker labels, typos) — a fuzzy/LLM-based second
  pass is a worthwhile follow-up, not yet built.
- **Extraction pipeline**: `extraction_pipeline.py` — the production-shaped
  batch job. Finds episodes with no successful `analysis_runs` row, assembles
  each one's multi-speaker transcript, runs one LLM call per episode against
  a schema covering summary/key_points/action_items/sentiment/topics/
  entities/events (episode-scoped) plus per-character
  facts/preferences/beliefs/memories (subject-scoped, resolved against the
  normalized `subjects` table — an LLM-mentioned name not found there is
  skipped, not guessed-into a new row). One episode failing never crashes
  the batch (logged to `analysis_runs` with `status='error'`, pipeline moves
  on). Verified end-to-end on one real episode: all 8 target tables
  populated correctly, facts correctly attributed across distinct resolved
  subjects.
- **Cognitive-memory reasoning demo**: `cognitive_reasoning_demo.py` —
  the consumption side. Assembles one subject's accumulated structured
  memory (facts/preferences/beliefs/memories/personality/relationships,
  whatever exists) into a compact context block and has the LLM answer a
  question grounded in *that*, not raw transcript search — the "Cognitive
  RAG" pattern from the roadmap docs. Verified: the LLM correctly refused to
  invent an answer for something the assembled memory didn't cover, instead
  saying so explicitly, rather than hallucinating.
- **Not yet built**: `personality_snapshots`/`relationship_profiles`
  population (both need the batch/aggregation treatment described in
  Phases 9-10 below, adapted to subjects instead of users), the fuzzy
  normalizer follow-up, and a full-corpus batch run (only 1 of 227 episodes
  processed so far, deliberately, per "test on one episode before proposing
  a full-corpus run").

**One incident worth recording**: an early revision of the schema file used
`DROP SCHEMA ... CASCADE`, wrongly assuming this schema held nothing but
this file's own empty tables. It didn't — the transcript source tables
(populated separately, 56,437 rows) lived in the same schema and were
destroyed along with everything else. Fully recovered by re-running the
original ingestion scripts. The schema file no longer drops anything under
any circumstance; every statement is `IF NOT EXISTS`. Lesson: never assume
exclusive ownership of a shared schema/namespace without checking first.

---

The phases below are the *original* plan, written before the pivot above.
Kept for reference and because the underlying reasoning (cheap fields on a
shared schema before expensive dedup problems, batch jobs for things that
aren't per-conversation, etc.) still applies — just re-target `subjects`/
`episodes` instead of `users`/`conversations` if resuming this exact
sequence against the Friends dataset, or `public.users`/`public.conversations`
if this ever actually gets adapted for the main app per the original intent.

## Architectural decision: extend the existing pipeline, don't replace it

The analysis doc proposes a standalone "Memory Extraction Pipeline" running
after every conversation. That pipeline already effectively exists:
`build_analysis_prompt()` (app.py:1279) sends the transcript to the LLM once
per conversation/call and gets back one JSON object, currently:

```
{"tasks": [...], "speakers": [...], "mood": {...}, "topics": [...], "questions": [...]}
```

`run_background_analysis()` (WS sessions) and `analyze_conversation()`
(REST `/analyze`) both parse this same shape and write it out. Most of the
new tables are just **more fields on this same JSON object** — not a new
LLM call, not a new pipeline. Only two of the thirteen tables (see Phase 8
and Phase 9) genuinely need something different: they depend on data across
*multiple* conversations/people, not one transcript, so they can't be
extracted from a single conversation's analysis pass at all.

Keeping everything else on the existing single-call pattern matters because
it's the difference between "one more field in a JSON schema" (cheap, low
risk, reuses code already proven correct) and "N new LLM calls per
conversation" (N× the cost, N× the failure surface, on a pipeline that's
already had real bugs this session).

## Phase 0 — Decisions to make before writing any code

These aren't implementation steps; they're open questions from the original
doc that need an answer first, because getting them wrong means redoing
later phases.

1. **`emotion_timeline` vs `mood_logs` — DECIDED: use `mood_logs`.** These
   overlapped almost completely (user_id, conversation_id, a label, a
   score/intensity, created_at), and `mood_logs` is already live in
   production (mood trend calendar heatmap, `friend_mood_update` pushes,
   `/mood/history`). `emotion_timeline` is dropped from this plan entirely —
   removed from `schema_relationship_intelligence.sql`. The one field it
   would have added, `cause`, goes on `mood_logs` instead:
   `ALTER TABLE mood_logs ADD COLUMN IF NOT EXISTS cause TEXT;` — not run
   yet, add it whenever a phase actually populates it (natural point: when
   extending the existing `"mood"` object in `build_analysis_prompt()`'s
   schema with an optional `"cause"` field — a one-column, one-field
   widening of an already-working feature, not new pipeline work, so it
   doesn't get its own numbered phase below). If you already applied the
   original schema file, drop the now-unused table:
   `DROP TABLE IF EXISTS emotion_timeline;`
2. **Confidence scale.** Every table has a `confidence`/`weight`/`importance`
   REAL column with no defined range. Pick one convention now (recommend
   0.0–1.0 throughout, matching `mood_score`'s existing convention) so
   prompts and any future "only show high-confidence facts" query don't have
   to special-case per table.
3. **Embeddings.** Deliberately deferred (see the schema file's header
   comment). Trigger to revisit: once Phase 5 (facts) or Phase 6 (memories)
   has enough real rows that exact/substring matching for dedup or retrieval
   stops being good enough. Don't add `pgvector` speculatively before that.
4. **Extraction failure behavior.** The existing pattern (see
   `extract_json`/the try/except around the LLM call) is "log and skip,
   never break the rest of the request." Every new field added to the shared
   JSON schema must fail the same way — a malformed `"facts"` array must
   never take down task/mood/topic extraction for the same transcript.

## Phase 1 — `conversation_summaries` (start here)

**Why first:** the doc's own stated prerequisite ("never re-read thousands
of messages"). Cheapest possible addition (one more JSON field on the
existing call) and immediately measurable value.

- Add item 6 to `build_analysis_prompt()`'s schema: `"summary"` (2-3
  sentences), `"key_points"` (list, ≤5), `"action_items"` (list, ≤5 —
  distinct from `tasks`: tasks are structured/reminder-able, action_items is
  the plain-language recap), `"sentiment"` (single word).
- Mirror the same fields in `analyze_conversation()`'s prompt (the `/analyze`
  REST path currently duplicates `build_analysis_prompt`'s shape — keep them
  in sync the same way tasks/topics/questions already are).
- On parse, `INSERT ... ON CONFLICT (conversation_id) DO UPDATE` into
  `conversation_summaries` (it's a 1:1 table, PK is `conversation_id`).
- Consumption: swap (or augment) the raw-transcript excerpts
  `build_global_chat_system_prompt`/`/chat/global` currently builds with
  summaries instead — cuts tokens per excerpt and should be a visible
  quality/cost win.
- **Definition of done:** every new conversation gets a summary row within
  one background-analysis pass; `/chat/global` answer quality holds or
  improves with a measurably smaller prompt.

## Phase 2 — `topics` / `conversation_topics` persistence

**Why second:** topics are *already* extracted (item 4 in the current
schema) — right now they're pushed once over `background_update` and never
stored. This is pure persistence, no new prompt field.

- After parsing `topics` in `run_background_analysis`/`analyze_conversation`,
  for each topic string: `INSERT ... ON CONFLICT (user_id, name) DO NOTHING`
  into `topics`, then link via `conversation_topics`.
  Case-insensitive matching the same way `speaker_profiles` already does
  (`lower(name)`) — otherwise "Budget" and "budget" fork into two rows.
- Consumption: enables a "browse by topic" view later (not required for this
  phase) and gives `/chat/global` a structural way to find related past
  conversations instead of only full-text search.
- **Definition of done:** topic chips already shown in the app now have a
  durable row behind them; re-querying `conversation_topics` for a user
  shows accumulating, deduped topics over time.

## Phase 3 — `entities` / `conversation_entities`

**Why third:** first genuinely *new* prompt field, but cheap and low-risk —
same shape of work as topics, just a different extraction target.

- Add item 7: `"entities"`: list of `{"name": "", "type": ""}` — people,
  organizations, technologies, places actually named in the transcript.
  Explicit instruction: only concrete named entities, not generic nouns
  ("the meeting" is not an entity; "Google Meet" is).
  Give the LLM a fixed `type` enum in the prompt (`person`, `organization`,
  `technology`, `place`) so `entity_type` doesn't fragment into synonyms.
- Persist like topics: `INSERT ... ON CONFLICT (user_id, name, entity_type)
  DO NOTHING`, link via `conversation_entities`.
- Consumption: profile/person detail screens could eventually show "entities
  mentioned around this person"; not required for this phase.
- **Definition of done:** entities extracted from a handful of real
  conversations look sane on manual review (no generic-noun noise); dedup
  across sessions works for the same named thing.

## Phase 4 — `preferences`

- Add item 8: `"preferences"`: list of `{"category": "", "item": "",
  "weight": 0.0-1.0}` — only extract when a preference is stated or clearly
  implied ("I love the new office coffee" → `{food, "coffee", 0.8}`), not
  guessed.
- Upsert: `INSERT ... ON CONFLICT (user_id, category, item) DO UPDATE SET
  weight = ..., confidence = ..., updated_at = now()` — repeated mentions
  should strengthen weight/confidence, not create duplicate rows. Decide the
  exact strengthening formula now (simplest: `GREATEST(existing, new)`, or a
  running average) rather than leaving it ambiguous at implementation time.
- Consumption: could eventually feed chat personalization the same way
  `user_personas` already does; not required for this phase.
- **Definition of done:** re-mentioning the same preference across two
  separate conversations updates one row, not two.

## Phase 5 — `events`

- Add item 9: `"events"`: list of `{"title": "", "description": "",
  "event_date": null, "importance": 0.0-1.0}` — only significant,
  named-moment events (promotion, exam, trip), not routine plans (those are
  already `tasks`). `event_date`: same "resolve against today's date, else
  null" instruction already used for `reminder_at`, reused here since the
  parsing problem is identical.
- Straight insert (no natural dedup key like topics/entities have — a
  "promotion" event genuinely can repeat).
- **Definition of done:** a handful of test conversations describing a real
  life event produce one correctly-dated `events` row each.

## Phase 6 — `beliefs`

- Add item 10: `"beliefs"`: list of `{"topic": "", "belief": "",
  "confidence": 0.0-1.0}`. Needs an explicit distinguishing rule in the
  prompt so the model doesn't conflate this with `facts` (Phase 7): *belief*
  = a stated opinion/stance ("thinks remote work is more productive"),
  *fact* = a verifiable/stated life detail ("works at Morgan Stanley"). Write
  that distinction into the prompt text itself, with one example of each.
- Straight insert; no dedup logic yet (defer to whatever dedup approach
  Phase 7 lands on, then apply the same one here).
- **Definition of done:** manual review of extracted beliefs vs. facts from
  the same test transcripts shows the model actually respecting the
  distinction, not dumping everything into one or the other.

## Phase 7 — `facts` + `fact_versions` (higher risk — budget more review time)

**Why here, not earlier:** this is the one the original doc's pipeline
diagram treats as step one, but it's actually the highest-risk table —
dedup and contradiction handling are real unsolved problems, not schema
questions. Doing it after Phases 1-6 means the extraction-field-on-shared-
schema mechanics are already proven before tackling the hard part.

- Add item 11: `"facts"`: list of `{"subject": "", "predicate": "",
  "object": "", "confidence": 0.0-1.0}`.
- **Step 1 (data collection only):** naive insert, no dedup, no
  supersession. Ship this alone first and let it run for a while — you need
  to see what the model actually produces (subject/predicate phrasing
  consistency, volume, noise) before designing matching logic against
  fiction.
- **Step 2 (dedup/supersession), only after reviewing Step 1's real output:**
  before insert, look for an existing `facts` row for the same `user_id` +
  `subject` (exact match first — no embeddings yet per Phase 0). If found
  and `object` differs: copy the old row's subject/predicate/object/
  confidence into `fact_versions` (stamped `superseded_at = now()`), then
  update the existing `facts` row in place (or set `valid_until` on the old
  and insert a new row — pick one now, don't leave both patterns in the
  codebase). If found and `object` matches: just bump `confidence`/
  `valid_from` treatment, don't duplicate.
- **Definition of done:** re-stating a changed fact across two sessions
  produces exactly one current `facts` row plus one `fact_versions` history
  row, not two live rows silently disagreeing.

## Phase 8 — `memories`

**Depends on Phase 1 (summaries) and Phase 7 (facts) existing** — "memory
worthy" is easiest to define in contrast to what's already captured as a
routine fact/summary, otherwise every conversation's summary just becomes a
duplicate memory.

- Add item 12: `"memories"`: list of `{"summary": "", "importance":
  0.0-1.0, "emotion": ""}` — explicit instruction: only for a distinct,
  notable episodic moment ("bought my first motorcycle"), not a recap of the
  whole conversation (that's what `conversation_summaries` is for) and not
  every task/fact restated as a memory.
- Insert with `recall_count = 0`, `decay_score = null`, `archived = false`.
  **Leave `decay_score`/`last_accessed` unused until there's an actual
  retrieval feature that reads memories** — don't invent a decay formula
  with nothing consuming it yet; that's speculative and untestable.
- **Definition of done:** memories extracted from test conversations are
  genuinely distinct from that same conversation's summary, not a
  near-duplicate of it.

## Phase 9 — `personality_snapshots` (batch, not per-conversation)

**This does not fit the shared per-conversation JSON schema.** Big Five
traits aren't reliably inferable from one short transcript — this needs to
look at a *window* of recent signal (recent `personality_notes`, `facts`,
`preferences`) and produce one snapshot, on its own cadence.

- New function, not a new field: e.g. `generate_personality_snapshot(user_id)`
  run periodically (candidate trigger: every N new conversations for that
  user, or a nightly job — decide based on how often personality data
  actually needs to feel fresh, this doesn't need to be real-time) — reads
  recent rows across `personality_notes`/`facts`/`preferences`, asks the LLM
  for one Big Five estimate + confidence, inserts one `personality_snapshots`
  row.
- **Definition of done:** snapshots accumulate over time per user without
  firing on every single conversation (that would be noise, not signal, for
  a trait that shouldn't visibly swing session to session).

## Phase 10 — `relationship_profiles` (last — most design work, least defined)

**Why last:** the original doc's `trust_score`/`conflict_score`/etc. are
currently just column names with no defined computation — this is the one
phase that's still genuinely a design problem, not an implementation one.

- Not per-conversation either — needs to look at the *pair* of users: shared
  call history (`calls`/`call_participants`), shared conversation
  involvement, `friendships` duration, message frequency between them.
- Before writing extraction code, define concretely: is
  `communication_frequency` computed directly from call/message counts
  (a real, cheap, non-LLM number) or LLM-estimated from transcript tone?
  Recommendation: compute what's mechanically countable
  (`communication_frequency`) directly from existing tables; reserve the LLM
  for what's actually qualitative (`trust_score`, `emotional_support`,
  `relationship_summary`) — don't ask the LLM to guess a number that SQL can
  already produce exactly.
- Trigger: candidate is "after a call between two friends ends" (reuses the
  same post-call hook that already exists for conversation-ready
  processing), covering the pair via
  `user_a < user_b` per the schema's canonical-ordering constraint.
- **Definition of done:** a real call between two friends produces/updates
  one `relationship_profiles` row with at least the mechanically-computed
  fields populated correctly; qualitative fields reviewed manually for
  sanity before trusting them.

## Suggested order recap

1. `conversation_summaries` — foundation, cheap, high value
2. `topics` persistence — already extracted, free
3. `entities` — cheap new field
4. `preferences` — cheap new field
5. `events` — cheap new field, reuses existing date-parsing pattern
6. `beliefs` — cheap new field, needs a fact/belief distinction rule
7. `facts` + `fact_versions` — higher risk, do dedup as its own sub-step
8. `memories` — depends on 1 and 7 existing to avoid duplicating them
9. `personality_snapshots` — batch job, not per-conversation
10. `relationship_profiles` — most design work, define scoring before coding

`emotion_timeline` is not built — decided (Phase 0, decision 1) in favor of
extending the existing, already-live `mood_logs` table with a `cause`
column instead. That table has been removed from
`schema_relationship_intelligence.sql`; drop it from the DB if you already
applied the earlier version of that file.
