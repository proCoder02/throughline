"""
Optional, additive bridge from the main backend (app.py) into the
emotional_intelligence research schema. This is the ONLY file the
emotional_intelligence engine exposes to app.py -- everything else in this
folder stays exactly as it was, isolated, Friends-transcript research.

Import and call are always safe: every function checks the flag first and
swallows its own errors, so flipping EMOTIONAL_INTELLIGENCE_ENABLED in .env
is the only thing that changes app.py's behavior -- no code path in app.py
itself is rewritten, and a DB hiccup here can never break a real request.

Real app users are looked up as subjects under the namespaced canonical
name "app_user:<user_id>", never by display name -- matching on a plain
name would risk silently pulling in a Friends-TV-show subject (e.g. a real
user named "Ross") instead of that user's own data. This is still true by
default: a real user is NEVER auto-matched to an existing subject by name.

app_user_subject_links is the one deliberate exception: an explicit,
opt-in row that says "treat this real user_id as this specific existing
subject_id", created only when someone chooses to link them (e.g. the
ross_geller/Ross test account, linked on purpose so a test scenario has a
real dataset to work with) -- never inferred automatically from a name
match. Without a link row, resolution falls back to the namespaced
app_user:<id> subject as before.
"""
from __future__ import annotations

import os
import sys

_pool = None  # lazily created -- see _get_pool()


def is_enabled() -> bool:
    return os.getenv("EMOTIONAL_INTELLIGENCE_ENABLED", "false").strip().lower() == "true"


# Bare closing acknowledgments ("ok", "cool", "thanks") carry no extractable
# information -- every one of them was still triggering a full extraction
# LLM call and getting cached as a "recent statement", pure waste. Matched
# as an EXACT match on the whole (normalized) message, not a substring --
# "ok but I also wanted to ask..." must NOT match, only a message that IS
# just the acknowledgment, nothing else.
_CLOSING_ACKNOWLEDGMENTS = {
    "ok", "okay", "k", "kk", "cool", "nice", "great", "awesome", "perfect",
    "thanks", "thank you", "thx", "ty", "got it", "gotcha", "noted",
    "sounds good", "alright", "understood", "fine", "good", "yep", "yup",
    "sure", "bet", "np", "no problem", "welcome", "cheers",
}


def is_closing_acknowledgment(text: str) -> bool:
    return (text or "").strip().lower().rstrip(".!?,;: ") in _CLOSING_ACKNOWLEDGMENTS


# facts.valid_until exists in the schema specifically for this, but nothing
# ever read it before -- a fact like "birthday: today" or "quitting_job:
# tomorrow", extracted from one line of dialogue, was read back literally
# on every single future day, forever (confirmed: Ross's real account,
# merged with the TV subject, was telling the real user "it's your
# birthday today" months after that fact was extracted). Every facts read
# now excludes anything past its valid_until.
NOT_EXPIRED = "(valid_until IS NULL OR valid_until > now())"


def _user_subject_key(user_id) -> str:
    return f"app_user:{user_id}"


def _log_failure(where: str, exc: Exception) -> None:
    # Every call site below swallows exceptions on purpose (a DB hiccup here
    # must never break a real /chat or /analyze request) -- but swallowing
    # silently made a genuine bug indistinguishable from "this user just has
    # no EI data yet." At minimum this needs to be visible in server logs,
    # matching app.py's own [fcm]/[redis] print-based logging convention.
    print(f"[ei_adapter] {where} failed: {exc!r}", file=sys.stderr)


def _get_pool():
    """This module's own small connection pool -- deliberately separate
    from app.py's db_pool, not shared or imported either direction, so
    ei_adapter.py stays the single self-contained file app.py depends on.
    Previously opened+closed a brand-new psycopg2.connect() on every single
    call, which under real concurrent traffic adds a full TCP+auth
    round-trip per request and can exhaust Postgres's max_connections
    independently of app.py's own pool sizing. statement_timeout is set on
    every connection this pool hands out, so a slow/locked query can never
    hang the calling /chat, /chat/global, or /analyze request indefinitely
    -- it raises instead, which every caller here already treats as "no EI
    context available" via the existing try/except."""
    global _pool
    if _pool is None:
        import psycopg2.pool
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=1, maxconn=5,
            dsn=os.getenv("DATABASE_URL"),
            options="-c statement_timeout=1500",
        )
    return _pool


def _connect():
    conn = _get_pool().getconn()
    # Autocommit -- every function here only ever reads (plus one idempotent
    # CREATE TABLE IF NOT EXISTS). Without this, a connection returned to
    # the pool mid-transaction (no commit/rollback) would carry that open
    # transaction into whichever request borrows it next -- the same class
    # of cross-request state leak already found and fixed once in app.py's
    # own get_db()/close_db() this session; not repeating it here.
    conn.autocommit = True
    return conn


def _release(conn) -> None:
    _get_pool().putconn(conn)


def _resolve_subject_id(cur, user_id) -> int | None:
    cur.execute(
        "CREATE TABLE IF NOT EXISTS emotional_intelligence.app_user_subject_links ("
        "user_id INTEGER PRIMARY KEY, "
        "subject_id INTEGER NOT NULL REFERENCES emotional_intelligence.subjects (id) ON DELETE CASCADE, "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT now())"
    )
    cur.execute(
        "SELECT subject_id FROM emotional_intelligence.app_user_subject_links WHERE user_id = %s",
        (user_id,),
    )
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "SELECT id FROM emotional_intelligence.subjects WHERE canonical_name = %s",
        (_user_subject_key(user_id),),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _search_table(cur, table: str, subject_id: int, question: str | None,
                   select_cols: str, order_by: str, limit: int, extra_where: str = "",
                   tiebreak_col: str | None = None) -> list:
    """Shared by ei_adapter.py (app.py's /chat, /chat/global, /analyze) and
    cognitive_reasoning_demo.py (the CLI demo + chat_server.py's standalone
    UI) -- there used to be two separate, hand-copied versions of this
    query, one per file. That drift is exactly what caused a real bug: this
    file's version got fixed to search by relevance, the other file's
    didn't, and a "what's Joey's favorite food" question confidently
    answered "pizza" when the actual highest-weighted preference ("best
    sandwich", weight 1.0) had simply aged out of a recency-only cutoff the
    fix never reached. One implementation now; fixing it here fixes both.

    order_by is a full ORDER BY expression (e.g. "created_at DESC",
    "importance DESC NULLS LAST"), not just a column name, so callers with
    different tie-breaking needs (memories ranks by importance, not
    recency) don't need their own copy of this function to express that.
    extra_where is an optional additional SQL condition (e.g. "archived =
    FALSE" for memories) -- always a caller-supplied literal from this
    package's own fixed set of call sites, never external input.

    Full-text search first, against a precomputed+indexed search_tsv
    column (see ensure_search_indexes.py) rather than computing
    to_tsvector(...) fresh per row on every call -- fine at the ~100
    facts/subject this was built and tested against, but a sequential scan
    doing text-search tokenization on every row, on every chat message,
    degrades badly the moment real usage pushes a subject into the
    thousands of facts. The GIN index on search_tsv is what makes this an
    index lookup instead.

    websearch_to_tsquery is tolerant of free-form natural-language
    questions (no operator syntax required) and safe from injection since
    the question is always a bound parameter, never concatenated SQL.
    Falls back to plain recency if the question doesn't match anything (or
    there's no question at all, e.g. /analyze), so a generic question
    still gets *something* instead of nothing.

    Words are OR'd together, not AND'd (websearch_to_tsquery's default for
    space-separated terms) -- a natural question almost always carries an
    action word ("buy", "plan", "feel") that will never appear in a stored
    fact, so requiring every word to match killed nearly every real
    question (verified: "what should I buy for Ben" only matched because
    "buy" was required AND absent from "has_son: Ben", a false negative).
    OR'ing lets a fact matching even one meaningful term surface, while
    ts_rank still ranks fuller matches higher.

    tiebreak_col breaks ties in ts_rank by that column (e.g. "weight" for
    preferences, "importance" for memories) -- needed because every row of
    a given category (e.g. every one of Joey's 28 "food" preferences)
    matches a broad question term like "food" identically, so ts_rank alone
    ranks them all the same and the LIMIT cutoff among the tied rows was
    effectively arbitrary again, just arbitrary-by-tie-break instead of
    arbitrary-by-recency (verified: "best sandwich", weight 1.0, still lost
    to "pizza", weight 0.9, purely because of tie order, even after
    switching to relevance search)."""
    where_extra = f" AND {extra_where}" if extra_where else ""
    tiebreak = f", {tiebreak_col} DESC NULLS LAST" if tiebreak_col else ""
    if question:
        or_question = " OR ".join(question.split())
        cur.execute(
            f"""
            SELECT {select_cols} FROM emotional_intelligence.{table}
            WHERE subject_id = %s{where_extra}
              AND search_tsv @@ websearch_to_tsquery('english', %s)
            ORDER BY ts_rank(search_tsv, websearch_to_tsquery('english', %s)) DESC{tiebreak}
            LIMIT %s
            """,
            (subject_id, or_question, or_question, limit),
        )
        rows = cur.fetchall()
        if rows:
            return rows
    cur.execute(
        f"SELECT {select_cols} FROM emotional_intelligence.{table} "
        f"WHERE subject_id = %s{where_extra} ORDER BY {order_by} LIMIT %s",
        (subject_id, limit),
    )
    return cur.fetchall()


def _count_table(cur, table: str, subject_id: int, extra_where: str = "") -> int:
    """The true row count for a table/subject, independent of whatever
    sample LIMIT _search_table applies. Needed because a quantity question
    ("who has more memories, A or B?") was being answered by comparing the
    *lengths of two equally-capped samples* (both capped at the same LIMIT)
    instead of the real totals -- confirmed: asked to compare Monica's and
    Rachel's memory counts, the model said "both have 10, so neither has
    more" when the real counts are 191 and 179. The sample is for
    *content*; only a real COUNT(*) answers a question about *quantity*."""
    where_extra = f" AND {extra_where}" if extra_where else ""
    cur.execute(
        f"SELECT count(*) AS cnt FROM emotional_intelligence.{table} WHERE subject_id = %s{where_extra}",
        (subject_id,),
    )
    row = cur.fetchone()
    # Works with both a plain cursor (ei_adapter.py's own calls -- row[0])
    # and cognitive_reasoning_demo.py's RealDictCursor (row["cnt"], since a
    # dict-like row has no integer key 0 and raises KeyError on row[0] --
    # the exact bug already hit and fixed 4+ times elsewhere in this
    # project from assuming one cursor type while actually getting another).
    try:
        return row[0]
    except (KeyError, TypeError):
        return row["cnt"]


def get_user_cognitive_context(user_id, question: str | None = None,
                                recent_statements: list[str] | None = None) -> str:
    """Extra system-prompt text for app.py's /analyze, /chat, and
    /chat/global calls, built from this user's EI cognitive memory if a
    matching subject exists. Passing the user's actual question lets each
    table's search be scoped to what's relevant to it (e.g. "Emma" surfaces
    "has_child: Emma" even if that fact is one of a hundred, buried outside
    any fixed recency cutoff); omit it (as /analyze does -- there's no
    natural question there) to fall back to plain recency. Returns "" if
    disabled, no subject yet AND no recent_statements, or on any failure --
    callers append this directly to their existing prompt string; no other
    change required.

    recent_statements is app.py's cheap, LLM-free bridge (see
    remember_ei_recent_statement there): the background fact-extraction
    thread can take several seconds and only ever updates the ONE
    conversation thread it ran for, so a correction typed in /chat
    wouldn't be visible to a follow-up asked in /chat/global (a different
    message history) until that write actually lands. These are the raw,
    not-yet-processed statements, shown ahead of and explicitly trusted
    over the structured sections below, which can lag behind by design."""
    if not is_enabled() or not user_id:
        return ""

    recent_lines = []
    if recent_statements:
        recent_lines = [
            "\nWhat this user said very recently, in this or another active session, not yet folded "
            "into the structured sections below (that takes a short while to process in the "
            "background) -- TRUST THESE over the structured facts/preferences/beliefs/memories below "
            "if they conflict, since these are strictly more current:"
        ]
        recent_lines += [f"- {s}" for s in recent_statements]

    try:
        conn = _connect()
        try:
            cur = conn.cursor()
            subject_id = _resolve_subject_id(cur, user_id)
            if subject_id is None:
                return "\n".join(recent_lines) if recent_lines else ""

            facts = _search_table(cur, "facts", subject_id, question,
                                   "predicate, object", "created_at DESC", 15, tiebreak_col="confidence",
                                   extra_where=NOT_EXPIRED)
            preferences = _search_table(cur, "preferences", subject_id, question,
                                         "category, item", "updated_at DESC", 10, tiebreak_col="weight")
            beliefs = _search_table(cur, "beliefs", subject_id, question,
                                     "topic, belief", "created_at DESC", 10, tiebreak_col="confidence")
            memories = _search_table(cur, "memories", subject_id, question,
                                      "summary, emotion", "created_at DESC", 10, tiebreak_col="importance")

            total_facts = _count_table(cur, "facts", subject_id, extra_where=NOT_EXPIRED)
            total_preferences = _count_table(cur, "preferences", subject_id)
            total_beliefs = _count_table(cur, "beliefs", subject_id)
            total_memories = _count_table(cur, "memories", subject_id)
        finally:
            _release(conn)
    except Exception as exc:
        _log_failure("get_user_cognitive_context", exc)
        return "\n".join(recent_lines) if recent_lines else ""

    if not facts and not preferences and not beliefs and not memories:
        return "\n".join(recent_lines) if recent_lines else ""

    lines = list(recent_lines)
    lines += [
        "Additional context -- this user's own accumulated cognitive memory "
        "(facts, preferences, beliefs, and memories extracted from their past conversations). "
        "Each section is labeled 'showing X of Y total' -- Y is the TRUE total count; X is just "
        "how many of the most relevant ones are listed below. For any question about quantity or "
        "frequency (\"how many times...\", \"who has more...\"), always use the TRUE TOTAL (Y), "
        "never the number of items actually listed, which is just a relevance-filtered sample:"
    ]
    lines.append(f"\nFacts (showing {len(facts)} of {total_facts} total):")
    for predicate, obj in facts:
        lines.append(f"- {predicate}: {obj}")
    lines.append(f"\nPreferences (showing {len(preferences)} of {total_preferences} total):")
    for category, item in preferences:
        lines.append(f"- prefers ({category}): {item}")
    lines.append(f"\nBeliefs (showing {len(beliefs)} of {total_beliefs} total):")
    for topic, belief in beliefs:
        lines.append(f"- believes ({topic}): {belief}")
    lines.append(f"\nMemories (showing {len(memories)} of {total_memories} total):")
    for summary, emotion in memories:
        lines.append(f"- memory ({emotion}): {summary}")
    lines.append(
        "\nEven a single relevant item above is enough to reason from -- give a decisive, concrete "
        "answer rather than defaulting to \"I don't have enough information\" just because the record "
        "is short. Reason forward from what IS known (e.g. a fact naming a family member is enough "
        "grounds for an age-appropriate suggestion); briefly flag any inferential leap in one short "
        "phrase rather than refusing to answer. Only decline outright if NONE of the above is relevant "
        "to the question at all."
    )
    return "\n".join(lines)


def get_friend_relationship_insight(user_id, friend_id) -> dict | None:
    """Trust/conflict/emotional-support insight for a user/friend pair, if
    both have EI subjects and a relationship_profiles row links them.
    Returns None if disabled, either side has no subject yet, or on any
    failure -- /friends/<id>/mood callers treat None exactly like "no
    extra insight", identical to today's response shape."""
    if not is_enabled() or not user_id or not friend_id:
        return None
    try:
        conn = _connect()
        try:
            cur = conn.cursor()
            subject_a_raw = _resolve_subject_id(cur, user_id)
            subject_b_raw = _resolve_subject_id(cur, friend_id)
            if subject_a_raw is None or subject_b_raw is None:
                return None
            subject_a, subject_b = sorted([subject_a_raw, subject_b_raw])

            cur.execute(
                "SELECT trust_score, conflict_score, emotional_support, relationship_summary "
                "FROM emotional_intelligence.relationship_profiles WHERE subject_a = %s AND subject_b = %s",
                (subject_a, subject_b),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "trust_score": row[0],
                "conflict_score": row[1],
                "emotional_support": row[2],
                "relationship_summary": row[3],
            }
        finally:
            _release(conn)
    except Exception as exc:
        _log_failure("get_friend_relationship_insight", exc)
        return None


def trigger_chat_feedback_extraction(user_id, message_content) -> None:
    """Fire-and-forget: call this from a background thread right AFTER
    /chat or /chat/global has already sent its reply (same pattern app.py
    already uses for run_background_analysis) -- so a correction typed in
    chat (e.g. "actually Emma is my niece, not my daughter") lands in the
    facts table within seconds, not on the next scheduled twice-daily
    pipeline run. This is the ChatGPT-like piece: fast reply, near-instant
    persistent memory, without adding latency to the user-facing response.

    Tagged with a distinct run_type so it never advances chat_feedback_
    extraction.py's own batch watermark -- see process_batch()'s docstring
    for why that separation matters (a silently-failed real-time call must
    never cause the scheduled batch to skip that message forever). The
    tradeoff: a message this handles successfully also gets re-checked by
    the next scheduled run -- redundant, not incorrect.

    Never raises and never blocks -- this always runs after the chat
    response has already been sent, so a failure here must never surface
    to the user, and doing this for every chat message roughly doubles
    the LLM call volume for chat traffic specifically (an extra
    extraction check alongside every reply) -- a deliberate, known cost
    for near-real-time correction, not an oversight. Bare closing
    acknowledgments ("ok", "cool", "thanks") are the one case skipped
    outright, before spending a call to learn they had nothing to extract."""
    if not is_enabled() or not user_id or not (message_content or "").strip():
        return
    if is_closing_acknowledgment(message_content):
        return
    try:
        import sys
        from pathlib import Path

        import psycopg2.extras

        # Every other script in this folder bare-imports its siblings
        # (e.g. "from real_user_extraction import ..."), which only
        # resolves when this folder itself is on sys.path. That's true
        # when ei_adapter.py is run/imported directly from inside this
        # folder, but NOT when app.py imports it as the package-qualified
        # "emotional_intelligence.ei_adapter" -- this is the first function
        # in this file that needed a sibling import, which is what exposed
        # this (confirmed: raised ModuleNotFoundError from within app.py's
        # own process before this fix).
        ei_folder = str(Path(__file__).resolve().parent)
        if ei_folder not in sys.path:
            sys.path.insert(0, ei_folder)

        from chat_feedback_extraction import process_batch
        from real_user_extraction import ensure_user_subject

        conn = _connect()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            subject_id = ensure_user_subject(cur, user_id)
            process_batch(cur, subject_id, [{"content": message_content}],
                           run_type="chat_feedback_extraction_realtime")
        finally:
            _release(conn)
    except Exception as exc:
        _log_failure("trigger_chat_feedback_extraction", exc)
