"""
Phase 1 ASMC experiment -- Aggregation/transformation task pair.

Task definition (locked in mco_asmc/README.md Phase 0): produce a weekly
personal-insight digest from a user's emotional_intelligence.* rows, via
two paths:

  - engineered (built here, didn't exist before): for each EI category with
      data in the lookback window, deterministically pick ONE item --
      most-recent fact, most-recent belief, highest-importance memory,
      most-recently-updated preference, latest personality snapshot, all
      active-this-week relationships -- and render each as a plain
      templated sentence. No LLM involved.
  - llm: emotional_intelligence/weekly_digest.py's real, already-deployed
      generate_weekly_digest() -- imported directly (not reimplemented) so
      both paths summarize the EXACT same underlying rows, fetched via the
      same queries (_fetch_recent_ei_data/_fetch_recent_relationship_insights).

Accuracy floor (locked): every category the deterministic path found data
for must have >=1 LLM card whose headline+body text references that same
item (checked via keyword/substring overlap against the item's own raw
text -- same known-fragile methodology as data_retrieval.py's grader).

LLM-side cost (fixed 2026-10-03): weekly_digest.py's generate_weekly_digest()
now accepts an additive, opt-in return_usage=True parameter (every other
caller is unaffected -- see that function's own docstring) that returns
real token counts alongside the cards, priced against the same locked rate
card as the other two task pairs. No longer a blank placeholder.

Volume axis (fixed 2026-10-03, see mco_asmc/README.md's "Fixes Required"
list): originally this task had exactly ONE real test case (Amit's own
seeded EI data) -- none of the 110 seeded friends had their own facts/
beliefs/memories, so there was no genuine N=10/100/1,000 axis, only
repeated trials against that single case. seed_demo_data.py now gives every
one of the 110 friends their own lightweight, templated EI data
(seed_friend_own_ei_data()), so each friend is a real, independent test
case -- generate_weekly_digest(friend_user_id) summarizes that specific
friend's own data, not Amit's. --n selects N of Amit's friends (cycling if
N exceeds 110) as test cases, matching data_retrieval.py/classification.py's
own --n/--trial convention.

Usage:
    python mco_asmc/experiments/aggregation.py --n 10 --trial 1 --username Amit

Where this runs: like data_retrieval.py, the LLM call here reads real
production EI data (facts/beliefs/memories about a real user) and sends it
to Ollama Cloud -- same production-content-exfiltration consideration as
retrieval. Run per the same workflow (user runs it directly via SSH on
production, not the agent).
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    raise SystemExit("DATABASE_URL is required (set it in .env)")
if "localhost" not in DB_URL and "127.0.0.1" not in DB_URL:
    raise SystemExit(
        "DATABASE_URL doesn't point at a database local to wherever this is "
        "running. Run this script ON the target host so DATABASE_URL is that "
        "host's own localhost connection."
    )

_ei_folder = str(Path(__file__).resolve().parent.parent.parent / "emotional_intelligence")
if _ei_folder not in sys.path:
    sys.path.insert(0, _ei_folder)

from ei_adapter import _resolve_subject_id  # noqa: E402
from weekly_digest import (  # noqa: E402
    LOOKBACK_DAYS, _fetch_recent_ei_data, _fetch_recent_relationship_insights, generate_weekly_digest,
)

RESULTS_PATH = Path(__file__).resolve().parent.parent / "results" / "raw_aggregation.csv"
CSV_COLUMNS = ["date", "task_pair", "side", "volume_n", "trial", "tokens", "latency_ms",
               "cost_usd", "cost_usd_paid_tier_sensitivity", "accuracy_pass", "notes"]

OCI_A1_FLEX_OCPU_HOUR_USD = 0.01
PRICE_PER_1M_INPUT_TOKENS = 0.15   # same locked rate card as data_retrieval.py/classification.py
PRICE_PER_1M_OUTPUT_TOKENS = 0.60


def log(msg: str) -> None:
    print(f"[aggregation] {msg}", flush=True)


def build_deterministic_digest(data: dict, relationships: list[dict]) -> list[dict]:
    """One templated sentence per category with data -- no LLM. Returns
    [{"category": ..., "text": ..., "source_text": ...}] where source_text
    is the raw underlying value the LLM-generated digest must be checked
    against for coverage."""
    items = []
    if data["facts"]:
        f = data["facts"][0]
        items.append({"category": "fact", "text": f"{f['predicate']}: {f['object']}", "source_text": f["object"]})
    if data["beliefs"]:
        b = data["beliefs"][0]
        items.append({"category": "fact", "text": f"Belief about {b['topic']}: {b['belief']}", "source_text": b["belief"]})
    if data["memories"]:
        m = data["memories"][0]
        items.append({"category": "fact", "text": f"Notable this week: {m['summary']} (felt {m['emotion']})", "source_text": m["summary"]})
    if data["preferences"]:
        p = data["preferences"][0]
        items.append({"category": "preference", "text": f"Preference -- {p['category']}: {p['item']}", "source_text": p["item"]})
    if data["personality_snapshots"]:
        s = data["personality_snapshots"][0]
        text = (f"Personality: openness={s['openness']}, conscientiousness={s['conscientiousness']}, "
                f"extraversion={s['extraversion']}, agreeableness={s['agreeableness']}, neuroticism={s['neuroticism']}")
        # source_text deliberately differs from the display text (fixed
        # 2026-10-03, see README "Fixes Required" -- found via a real N=10
        # run where EVERY case marked personality "missed"): "openness=0.58,"
        # is not a word an LLM card would ever write verbatim, so matching
        # against the raw key=value string guaranteed a miss regardless of
        # what the LLM actually said. Match on the trait NAMES instead --
        # real words a card discussing personality would plausibly use.
        items.append({"category": "personality", "text": text,
                       "source_text": "openness conscientiousness extraversion agreeableness neuroticism"})
    for r in relationships:
        text = f"{r['friend_name']}: trust={r['trust_score']}, support={r['emotional_support']} -- {r['relationship_summary']}"
        items.append({"category": "relationship", "text": text, "source_text": r["relationship_summary"]})
    return items


def check_coverage(deterministic_items: list[dict], llm_cards: list[dict]) -> tuple[bool, list[str]]:
    """True if every deterministic item's source_text is referenced
    (substring overlap on a few significant words) by at least one LLM
    card. Returns (all_covered, list of missed item descriptions)."""
    card_text = " ".join(f"{c.get('headline', '')} {c.get('body', '')}" for c in llm_cards).lower()
    missed = []
    for item in deterministic_items:
        # Loose check: any word of >=5 chars from source_text appears in the
        # combined card text. Crude (same known fragility as other graders
        # in this project), but avoids requiring exact phrasing.
        words = [w.strip(".,!?").lower() for w in item["source_text"].split() if len(w.strip(".,!?")) >= 5]
        if not words or not any(w in card_text for w in words):
            missed.append(item["text"])
    return (len(missed) == 0, missed)


def paid_tier_sensitivity_cost(latency_ms: float) -> float:
    hours = (latency_ms / 1000) / 3600
    return hours * OCI_A1_FLEX_OCPU_HOUR_USD


def write_rows(rows: list[dict]) -> None:
    is_new = not RESULTS_PATH.exists()
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if is_new:
            writer.writeheader()
        writer.writerows(rows)


def load_test_users(cur, amit_id: int, n: int) -> list[tuple]:
    """N of Amit's friends (cycling if N exceeds the friend count), each now
    a real, independent test case since seed_demo_data.py gives every friend
    their own EI data. Ordered by friend_id so repeated runs are
    reproducible, same convention as data_retrieval.py's load_test_cases."""
    cur.execute(
        "SELECT f.friend_id, COALESCE(f.nickname, u.username) AS name "
        "FROM friendships f JOIN users u ON u.id = f.friend_id "
        "WHERE f.user_id = %s ORDER BY f.friend_id",
        (amit_id,),
    )
    friends = cur.fetchall()
    if not friends:
        raise SystemExit(f"No friends found for user_id {amit_id} -- run seed_demo_data.py first.")
    return [friends[i % len(friends)] for i in range(n)]


def run(n: int, trial: int, username: str) -> None:
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE username = %s", (username,))
    row = cur.fetchone()
    if not row:
        raise SystemExit(f"No user named '{username}' found.")
    amit_id = row[0]

    cases = load_test_users(cur, amit_id, n)

    dict_cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    today = datetime.now().astimezone().date().isoformat()
    rows = []

    log(f"Running {n} aggregation cases (trial {trial})...")
    for i, (user_id, name) in enumerate(cases):
        subject_id = _resolve_subject_id(cur, user_id)
        if subject_id is None:
            log(f"  [{i+1}/{n}] {name}: no EI subject, skipping")
            continue

        data = _fetch_recent_ei_data(dict_cur, subject_id)
        relationships = _fetch_recent_relationship_insights(dict_cur, user_id, subject_id)

        t0 = time.perf_counter()
        deterministic_items = build_deterministic_digest(data, relationships)
        eng_latency = (time.perf_counter() - t0) * 1000
        rows.append({
            "date": today, "task_pair": "aggregation", "side": "engineered",
            "volume_n": n, "trial": trial, "tokens": "", "latency_ms": round(eng_latency, 6),
            "cost_usd": 0.0, "cost_usd_paid_tier_sensitivity": round(paid_tier_sensitivity_cost(eng_latency), 12),
            "accuracy_pass": True,
            "notes": f"{len(deterministic_items)} deterministic items for {name}" if i == 0 else "",
        })

        try:
            # return_usage=True (added 2026-10-03 to weekly_digest.py itself,
            # additive/opt-in -- every other caller is unaffected, see that
            # file's docstring) -- real token counts, not a blank placeholder.
            cards, usage = generate_weekly_digest(user_id, return_usage=True)
            cards = cards or []
        except Exception as exc:
            log(f"  [{i+1}/{n}] {name}: FAILED ({exc})")
            rows.append({
                "date": today, "task_pair": "aggregation", "side": "llm",
                "volume_n": n, "trial": trial, "tokens": 0, "latency_ms": "",
                "cost_usd": 0.0, "cost_usd_paid_tier_sensitivity": "", "accuracy_pass": False,
                "notes": f"error: {exc}",
            })
            continue
        latency_ms = (time.perf_counter() - t0) * 1000

        total_tokens = usage["total_tokens"] if usage else 0
        cost_usd = ((usage["prompt_tokens"] / 1_000_000 * PRICE_PER_1M_INPUT_TOKENS +
                     usage["completion_tokens"] / 1_000_000 * PRICE_PER_1M_OUTPUT_TOKENS)
                    if usage else 0.0)

        covered, missed = check_coverage(deterministic_items, cards)
        rows.append({
            "date": today, "task_pair": "aggregation", "side": "llm",
            "volume_n": n, "trial": trial,
            "tokens": total_tokens, "latency_ms": round(latency_ms, 3),
            "cost_usd": round(cost_usd, 8), "cost_usd_paid_tier_sensitivity": "",
            "accuracy_pass": covered,
            "notes": f"{len(cards)} cards generated; " + (
                "all deterministic items covered" if covered
                else f"missed: {'; '.join(missed)}"
            ),
        })
        log(f"  [{i+1}/{n}] {name}: {len(cards)} cards, {latency_ms:.0f}ms, {total_tokens} tok, "
            f"${cost_usd:.6f}, {'covered' if covered else f'{len(missed)} missed'}")

    conn.close()
    write_rows(rows)
    log(f"Done. Wrote {len(rows)} rows to {RESULTS_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n", type=int, default=10, help="Number of friend test cases to run (default: 10)")
    parser.add_argument("--trial", type=int, default=1, help="Trial number within this volume tier (default: 1)")
    parser.add_argument("--username", default="Amit", help="Account whose friends to generate digests for (default: Amit)")
    args = parser.parse_args()
    run(args.n, args.trial, args.username)


if __name__ == "__main__":
    main()
