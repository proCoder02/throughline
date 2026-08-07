# Cognitive Intelligence — Further Development

Gaps in what the system is actually capable of, as distinct from
`PRODUCTION_READINESS_ANALYSIS.md` (which covers scaling/security/reliability
issues in the *existing* pipeline). These are about the pipeline not yet
doing something it should, not about something it does incorrectly.

---

## 1. Personality/relationship snapshots never refresh (highest priority)

A snapshot is a computed judgment, not a memory — the batch job reads a
person's entire accumulated facts/beliefs/memories at one point in time and
condenses them into Big Five scores (personality) or trust/conflict/support
scores (relationship). It's a photograph of "here's my best read on this
person, based on everything known so far."

**The gap**: `personality_batch.py` and `relationship_batch.py` both skip
anyone who already has a successful run — forever. A snapshot taken on day
one with the bare minimum qualifying data (5 facts) is never retaken, even
after that person accumulates hundreds more facts. The schema already
anticipates multiple snapshots over time (`created_at` per row,
`fetch_subject_persona` explicitly asks for "the most recent" one, implying
older ones could coexist) — but in practice only one is ever produced.

**Why it matters**: this directly undermines the project's own premise —
"persistent memory which develops persona and relationship" (the original
ask that started this whole pipeline). Without periodic re-snapshotting,
there's no way to see whether someone's inferred personality is becoming
clearer, or whether a relationship's trust score is actually shifting, the
way a real evolving relationship would.

**What it needs**: change the qualifying query from "has never been
snapshotted" to "hasn't been snapshotted since N new facts/memories arrived"
(or a fixed time interval, e.g. re-run monthly regardless), so a real
timeline of snapshots accumulates instead of one frozen photo. Comparing
snapshot N to snapshot N-1 over time is itself a new capability worth
having (e.g. "trust with this person has been declining over the last 3
snapshots").

---

## 2. No semantic (embedding-based) retrieval yet  (pg_vector embedding required on production)

Full-text search (fixed this session — see `ei_adapter.py`'s `_search_table`)
works on shared keywords via Postgres `websearch_to_tsquery`. It correctly
finds a fact if the question and the fact share a literal word ("Emma"
matches "has_child: Emma"). It will **not** find semantically related
content phrased differently — "gift for my daughter" won't match "has_child:
Emma" unless "daughter" and "child" happen to stem to the same lexeme
(they don't, in Postgres's English config).

This is fine at today's scale (~100-200 facts/subject, mostly overlapping
vocabulary) but will degrade as real usage grows and real users phrase
questions less predictably than a test script does.

**What it needs**: the schema already has commented-out placeholders for
this (`-- embedding vector(1536) -- add once semantic fact retrieval is
implemented`, in both `facts` and `memories`). Building it means: an
embedding call per fact/preference/belief/memory at write time (or a
backfill batch), a `pgvector` column + index, and a retrieval path that
tries semantic similarity when full-text search comes up short — the same
"search first, fall back" shape `_search_table` already has, just with a
different search mechanism.

---

## 3. No feedback loop from chat back into the EI schema

The pipeline is currently one-directional: dictated conversations →
`real_user_extraction.py` → `emotional_intelligence` schema → enriches
future `/chat`/`/chat/global` answers. The chat interactions themselves are
a real signal source that's currently discarded — if a user corrects the
assistant ("actually I don't like that anymore," "no, that's my brother not
my father" — literally the kind of correction that would have fixed the
Frank data-quality bug), nothing captures that as an updated fact or a
correction to an existing one.

**What it needs**: a lightweight extraction pass over `chat_messages`
(similar in shape to `real_user_extraction.py`, but reading chat turns
instead of raw conversation transcripts), specifically looking for
corrections/updates to existing facts rather than net-new ones — this is a
different, narrower extraction task than the main pipeline's, since most
chat turns won't contain new factual information at all.

---

## 4. Related, already-scoped work (tracked elsewhere, listed here for completeness)

- **Real-user relationship co-occurrence** (deferred to next session, see
  project memory): `relationship_batch.py` only works for the Friends
  corpus today; a real-user version needs to source co-occurrence from
  `public.call_participants` instead of `episode_speaker_transcript`.
- **Data quality**: the Frank/`father_of_phoebe` extraction error and
  broader un-audited corpus quality (only ad-hoc spot checks done so far)
  — see `PRODUCTION_READINESS_ANALYSIS.md`.
- **Consent/deletion, security, scaling items** — connection pooling,
  indexing, and exception logging were fixed this session; data deletion
  and `FLASK_SECRET_KEY` are still open — see
  `PRODUCTION_READINESS_ANALYSIS.md` for the full list.

---

## Suggested priority

1. Snapshot refresh/timeline (#1) — directly serves the project's core premise, no external dependency.
2. Real-user relationship co-occurrence (tomorrow's planned work) — unblocks real-user relationship intelligence entirely, not just refresh.
3. Chat feedback loop (#3) — meaningfully improves data quality going forward, including fixing exactly the kind of error found in #4.
4. Semantic retrieval (#2) — build when keyword search actually starts missing things in practice, not preemptively.
