"""
Phase 2 of COGNITIVE_SHARING_INTERVENTION_PLAN.md -- the actual "common
ground" reasoning step. generate_common_ground_suggestion() is only ever
called after the bilateral permission gate (both cognitive_sharing_settings
rows for a pair are >= 'limited') has already passed in app.py -- this
module trusts that check happened and does not repeat it.

Usage (ad-hoc manual testing, mirrors cognitive_reasoning_demo.py's own
--subject/--question CLI shape):
    python cognitive_sharing.py --user-a 1 --user-b 2
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

DB_URL = os.getenv("DATABASE_URL")

# Every other script in this folder bare-imports its siblings (e.g. "from
# cognitive_reasoning_demo import ...") which only resolves when this
# folder itself is on sys.path -- true when run/imported directly from
# inside this folder, NOT when app.py imports it as the package-qualified
# "emotional_intelligence.cognitive_sharing" (same fix already needed once
# in ei_adapter.py's trigger_chat_feedback_extraction -- see its docstring).
_EI_FOLDER = str(Path(__file__).resolve().parent)
if _EI_FOLDER not in sys.path:
    sys.path.insert(0, _EI_FOLDER)

from cognitive_reasoning_demo import assemble_multi_subject_memory, call_llm  # noqa: E402

COMMON_GROUND_PROMPT = """You are looking for a genuine "common ground" opportunity between two people, using ONLY their individual cognitive memory (each person's own facts/preferences/beliefs/memories) provided below, plus their recent direct-message conversation. This is NOT a question-answering task -- nobody asked you anything. You are deciding whether there is something worth proactively surfacing that would help BOTH of them: a shared interest neither has mentioned to the other, a scheduling opportunity, a relevant fact one side has that the other would want to know.

Reply with the single word NONE (nothing else) unless you find a genuine, specific, well-grounded opportunity. Expect to reply NONE most of the time -- most conversations have no real opportunity, and manufacturing one when there isn't a clear one is worse than staying silent. Only write an actual suggestion when it's concrete and clearly grounded in what's actually present below.

CRITICAL, non-negotiable rule: never quote or closely paraphrase one side's private memory back verbatim to the other, and never reveal that you read anyone's private data at all. For example, if person B's private memory says they are free Friday but B never said that in the DM thread, do NOT write "B mentioned they're free Friday" -- that leaks a private fact as if it were shared knowledge. Instead synthesize into a neutral suggestion neither side could trace back to the other's private data, e.g. "Friday might be a good day for you two to connect" is fine; "B told their assistant they're free Friday" or any sentence that reveals WHERE the information came from is not.

Ground everything in what is actually stated in the memory blocks or the DM thread below -- never invent a shared interest, event, or fact that isn't explicitly present in at least one side's own data. If the DM thread already covers everything relevant (e.g. they already agreed on a time), there is nothing to add -- reply NONE.

If you do find something, write it as a single short, casual sentence or two -- like a friend gently pointing something out, not a labeled report. No headers, no "Suggestion:", no bullet points, no confidence scores or numbers of any kind."""


def _resolve_subject_name(cur, user_id: int) -> str | None:
    """Read-only equivalent of real_user_extraction.ensure_user_subject for
    a single user -- deliberately never creates a subject, mirroring
    relationship_batch.py's fetch_real_user_subject_map: a user who has
    never actually been processed by real_user_extraction.py has no
    grounded cognitive memory yet, and manufacturing an empty subject here
    would make generate_common_ground_suggestion silently reason from
    nothing instead of correctly finding no subject to work with."""
    cur.execute(
        "SELECT s.canonical_name FROM emotional_intelligence.app_user_subject_links l "
        "JOIN emotional_intelligence.subjects s ON s.id = l.subject_id "
        "WHERE l.user_id = %s",
        (user_id,),
    )
    row = cur.fetchone()
    if row:
        return row["canonical_name"]
    cur.execute(
        "SELECT canonical_name FROM emotional_intelligence.subjects WHERE canonical_name = %s",
        (f"app_user:{user_id}",),
    )
    row = cur.fetchone()
    return row["canonical_name"] if row else None


def generate_common_ground_suggestion(cur, user_a_id: int, user_b_id: int, recent_dm_context: str) -> str | None:
    """Only call this after the bilateral cognitive_sharing_settings gate
    has already passed (both directions >= 'limited') -- see app.py's
    request_cognitive_suggestion, which is the sole caller in production.
    Returns None far more often than not -- both because there's usually
    no genuine opportunity, and because one or both people may not have
    accumulated cognitive memory yet. Never returns a raw fact/preference/
    memory row, only the LLM's synthesized suggestion text (or None)."""
    name_a = _resolve_subject_name(cur, user_a_id)
    name_b = _resolve_subject_name(cur, user_b_id)
    if not name_a or not name_b:
        return None

    memory_block, _stats = assemble_multi_subject_memory(cur, [name_a, name_b])
    if not memory_block.strip():
        return None

    user_content = (
        f"{memory_block}\n\n"
        f"=== Recent direct-message conversation between these two people ===\n"
        f"{recent_dm_context.strip() if recent_dm_context else '(no recent messages)'}"
    )
    messages = [
        {"role": "system", "content": COMMON_GROUND_PROMPT},
        {"role": "user", "content": user_content},
    ]
    try:
        reply = call_llm(messages).strip()
    except Exception:
        return None

    if not reply or reply.strip().upper().rstrip(".") == "NONE":
        return None
    return reply


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-a", type=int, required=True)
    parser.add_argument("--user-b", type=int, required=True)
    parser.add_argument("--dm-context", default="", help="Recent DM text to include, if any")
    args = parser.parse_args()

    if not DB_URL:
        raise SystemExit("DATABASE_URL is required")
    conn = psycopg2.connect(DB_URL)
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        suggestion = generate_common_ground_suggestion(cur, args.user_a, args.user_b, args.dm_context)
        print(suggestion or "(no suggestion -- NONE)")
    finally:
        conn.close()
