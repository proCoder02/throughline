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
                   select_cols: str, recency_col: str, limit: int) -> list:
    """Full-text search first, against a precomputed+indexed search_tsv
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
    ts_rank still ranks fuller matches higher."""
    if question:
        or_question = " OR ".join(question.split())
        cur.execute(
            f"""
            SELECT {select_cols} FROM emotional_intelligence.{table}
            WHERE subject_id = %s
              AND search_tsv @@ websearch_to_tsquery('english', %s)
            ORDER BY ts_rank(search_tsv, websearch_to_tsquery('english', %s)) DESC
            LIMIT %s
            """,
            (subject_id, or_question, or_question, limit),
        )
        rows = cur.fetchall()
        if rows:
            return rows
    cur.execute(
        f"SELECT {select_cols} FROM emotional_intelligence.{table} "
        f"WHERE subject_id = %s ORDER BY {recency_col} DESC LIMIT %s",
        (subject_id, limit),
    )
    return cur.fetchall()


def get_user_cognitive_context(user_id, question: str | None = None) -> str:
    """Extra system-prompt text for app.py's /analyze, /chat, and
    /chat/global calls, built from this user's EI cognitive memory if a
    matching subject exists. Passing the user's actual question lets each
    table's search be scoped to what's relevant to it (e.g. "Emma" surfaces
    "has_child: Emma" even if that fact is one of a hundred, buried outside
    any fixed recency cutoff); omit it (as /analyze does -- there's no
    natural question there) to fall back to plain recency. Returns "" if
    disabled, no subject yet, or on any failure -- callers append this
    directly to their existing prompt string; no other change required."""
    if not is_enabled() or not user_id:
        return ""
    try:
        conn = _connect()
        try:
            cur = conn.cursor()
            subject_id = _resolve_subject_id(cur, user_id)
            if subject_id is None:
                return ""

            facts = _search_table(cur, "facts", subject_id, question,
                                   "predicate, object", "created_at", 15)
            preferences = _search_table(cur, "preferences", subject_id, question,
                                         "category, item", "updated_at", 10)
            beliefs = _search_table(cur, "beliefs", subject_id, question,
                                     "topic, belief", "created_at", 10)
            memories = _search_table(cur, "memories", subject_id, question,
                                      "summary, emotion", "created_at", 10)
        finally:
            _release(conn)
    except Exception as exc:
        _log_failure("get_user_cognitive_context", exc)
        return ""

    if not facts and not preferences and not beliefs and not memories:
        return ""

    lines = [
        "Additional context -- this user's own accumulated cognitive memory "
        "(facts, preferences, beliefs, and memories extracted from their past conversations):"
    ]
    for predicate, obj in facts:
        lines.append(f"- {predicate}: {obj}")
    for category, item in preferences:
        lines.append(f"- prefers ({category}): {item}")
    for topic, belief in beliefs:
        lines.append(f"- believes ({topic}): {belief}")
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
