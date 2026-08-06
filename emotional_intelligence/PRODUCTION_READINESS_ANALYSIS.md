# EI Engine — Production Readiness Analysis

Scope: `ei_adapter.py`, `real_user_extraction.py`, and the `/analyze`, `/chat`,
`/chat/global`, `/friends/<id>/mood` hooks in `app.py` — the path that now
touches real user data, not the isolated Friends-transcript research corpus.
Everything below was verified against the actual current code/schema, not
inferred generically.

## Critical

**1. `ei_adapter.py` never uses `app.py`'s connection pool.**
`_connect()` calls `psycopg2.connect(...)` directly on every single call from
`get_user_cognitive_context()` / `get_friend_relationship_insight()`. `app.py`
itself uses a real `ThreadedConnectionPool(minconn=2, maxconn=20)` for exactly
this reason. Under real concurrent traffic, every EI-enriched `/chat`,
`/chat/global`, or `/analyze` request opens and tears down a brand-new
Postgres connection (TCP handshake + auth) instead of borrowing from the pool
— this adds latency per request and can exhaust Postgres's `max_connections`
under load, independent of `app.py`'s own pool sizing.
*Fix*: have `ei_adapter.py` borrow/return connections via `app.py`'s
`db_pool`/`get_db()`, or at minimum use its own small dedicated pool.

**2. No statement timeout on any EI query.**
If a query is slow (lock contention, a bad plan once tables grow, a stuck
autovacuum), `/chat`, `/chat/global`, and `/analyze` will all hang waiting on
it — the EI lookup happens synchronously, inline, *before* the LLM call.
*Fix*: set `options="-c statement_timeout=1500ms"` (or similar) on the EI
adapter's connection, matching the "EI is optional, never blocking" design
intent already established for its `try/except` wrapping.

**3. Full-text search is completely unindexed.**
`to_tsvector('english', predicate || ' ' || object)` is computed fresh on
every query, over every row for that `subject_id`, on every chat message.
Fine at ~100 rows/subject (today's test data); will degrade linearly as real
usage accumulates thousands of facts per subject. No GIN index, no stored
`tsvector` column, anywhere in `emotional_intelligence_schema.sql`.
*Fix*: add a generated `tsvector` column + GIN index per searchable table
(`facts`, `preferences`, `beliefs`, `memories`) before this sees real volume.

**4. `FLASK_SECRET_KEY` is still unset.**
Both Flask sessions and JWT bearer tokens (`app.py:51`, `:810`) fall back to
the hardcoded `"dev-secret-change-me"`. Anyone who has seen this source file
can mint a valid login token for any user id. Flagged earlier this session,
still unresolved — worth fixing before any of this goes further than the 4
test accounts.

## High

**5. `real_user_extraction.py` defaults to processing every user.**
Already caused a real incident this session (it silently processed
Abhishek/Kunal/Neha's private conversations alongside the 4 test accounts).
There is still no per-user consent/opt-in gate — anyone running this script
in production without `--user-id` will extract personal facts about every
real user with zero notice or consent.
*Fix*: require an explicit allowlist (e.g. a `users.ei_enabled` flag) before
a user's conversations are ever eligible, rather than "everyone unless
excluded."

**6. No data-deletion path.**
Deleting a user or a conversation in `public` does not cascade into
`emotional_intelligence.facts/preferences/beliefs/memories`. A user who
deletes their account (or a specific conversation) keeps a durable, orphaned
personal profile in the EI schema indefinitely. This is a real compliance
gap the moment this touches actual users instead of test accounts.

**7. Friend-name resolution can match the wrong person.**
In `/chat/global` (`app.py`, the `friend_row` lookup): `lower(u.username)
LIKE lower('%' || matched_name || '%')` is an unescaped substring match. A
speaker named "Ben" would match usernames "ben", "benjamin", "urban93" —
whichever friend happens to come back first from the query — and the
relationship insight for *that* wrong pair gets surfaced. Not hypothetical:
your own test accounts (`ross_geller`, `monica_geller`, ...) already share
enough substring overlap that this could misfire once more test users exist.
*Fix*: match against exact username, or a proper name-to-user mapping table,
not `LIKE`.

**8. Every failure in `ei_adapter.py` is swallowed identically to "no data."**
`except Exception: return ""` / `return None` means a genuine bug (a typo'd
column name in a future edit, a permissions change, a schema drift) looks
*exactly* like "this user just has no EI data yet." Nothing is logged. In
production this could silently stop working for weeks with no signal.
*Fix*: log the exception (even just `print()` to stderr, matching `app.py`'s
own `[fcm]`/`[redis]` logging convention) before returning the safe default.

## Medium

**9. No caching of EI context.**
Every chat message re-runs the full search + fallback queries from scratch,
even for consecutive messages in the same conversation where nothing in the
user's EI data has changed. `app.py` already has a Redis cache-aside pattern
(`cache_get_json`/`cache_set_json`) used elsewhere; EI context isn't using it.

**10. Check-then-insert race in subject creation.**
`ensure_user_subject()` / `_resolve_subject_id()` do a `SELECT` then
`INSERT` with no advisory lock. If the batch job ever runs concurrently with
itself (an overlapping cron invocation, or two workers), two processes could
both miss the same not-yet-existing `app_user:<id>` subject and race on
insert — one would fail on `subjects.canonical_name`'s unique constraint,
uncaught, and abort that conversation's processing.

**11. `preferences`/`beliefs`/`memories` have no `source` column.**
Only `facts` distinguishes provenance (`'app_conversation'` vs Friends-corpus
rows). After a character merge (like Ross ↔ TV-Ross), there's no way to tell
which preference/belief/memory row came from a real conversation versus the
original show transcript — full provenance was a stated design goal
(`analysis_runs` + per-row `source`) but wasn't applied consistently.

**12. `PROMPT_VERSION` is shared between two different prompts.**
`real_user_extraction.py` imports `PROMPT_VERSION` from `extraction_pipeline.py`
and reuses it as-is, even though its `EXTRACTION_PROMPT` is a completely
different template. If either prompt changes independently later, historical
`analysis_runs` rows can't be trusted to tell you which actual prompt
template produced a given row — defeats the point of versioning it at all.

**13. Silent transcript truncation.**
`real_user_extraction.py` truncates each conversation to `[:8000]` characters
with no logging when truncation actually happens. A long real dictation
session could lose facts from its back half with no trace that it occurred.

**14. The flag requires a process restart to take effect.**
`is_enabled()` reads `os.environ` (populated once at startup by
`load_dotenv()`). Editing `EMOTIONAL_INTELLIGENCE_ENABLED` in `.env` while
the server is running does nothing until the process restarts — confirmed
directly this session (test scripts had to set `os.environ` explicitly to
see a change take effect). Worth documenting so a future "I flipped the flag
and nothing changed" isn't mistaken for a code bug.

## Low / Maintainability

**15. The character-merge logic only exists as a scratch script.**
`merge_all_characters.py` (the Ross/Monica/Chandler/Joey ↔ TV-character
merge) lives only in the session scratchpad, not in `emotional_intelligence/`.
If this pattern needs repeating (a 5th test account, a re-run after a data
reset), there's nothing checked in to reuse — it'd need to be rewritten from
this report.

**16. No automated tests anywhere in this pipeline.**
Every bug found and fixed this session — the `.env` BOM corruption (twice),
the AND-vs-OR full-text search bug, the missing `personality_notes` wiring,
the Ross-own-birthday hallucination — was caught by hand, via one-off
scratch scripts. Nothing guards against any of them regressing silently in
a future edit.

**17. `.env` encoding fragility (already hit twice this session).**
A stray UTF-8 BOM (introduced by `-Encoding utf8` in Windows PowerShell 5.1,
and separately by a plain editor re-save) silently broke `olama_api_key`
twice, taking down real LLM calls app-wide both times. Worth a documented
convention (e.g. verify with `dotenv_values()` after any manual edit) so it
doesn't recur in a real deployment where nobody's watching for it.

---

## Suggested priority if patching now
1. Connection pooling + statement timeout (1, 2) — correctness/availability under any real load.
2. Friend-name exact-match fix (7) — actual data-leak-to-wrong-person risk.
3. Exception logging (8) — you can't fix what you can't see failing.
4. Consent gate on `real_user_extraction.py` (5) — already bit you once.
5. Full-text search indexing (3) — fine today, becomes a real problem the moment usage grows.
6. Everything else, opportunistically.
