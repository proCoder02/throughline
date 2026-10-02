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

IMPORTANT volume-tier limitation (see mco_asmc/README.md Progress Log):
unlike retrieval/classification, this task has only ONE real test case
today -- the single real user (Amit) with seeded EI data. There is no
seeded multi-user EI dataset to give a genuine N=10/100/1,000 volume axis.
This script's --trials runs REPEATED calls against that one real case
(legitimate for measuring LLM-call variance per Phase 3's "run every
configuration multiple times," and volume_n is logged as 1, honestly, not
faked as a larger number).

Usage:
    python mco_asmc/experiments/aggregation.py --trials 5 --username Amit

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
        items.append({"category": "personality", "text": text, "source_text": text})
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


def run(trials: int, username: str) -> None:
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE username = %s", (username,))
    row = cur.fetchone()
    if not row:
        raise SystemExit(f"No user named '{username}' found.")
    user_id = row[0]

    subject_id = _resolve_subject_id(cur, user_id)
    if subject_id is None:
        raise SystemExit(f"No emotional_intelligence subject for user_id {user_id} -- nothing to summarize.")

    import psycopg2.extras
    dict_cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    data = _fetch_recent_ei_data(dict_cur, subject_id)
    relationships = _fetch_recent_relationship_insights(dict_cur, user_id, subject_id)
    conn.close()

    t0 = time.perf_counter()
    deterministic_items = build_deterministic_digest(data, relationships)
    eng_latency = (time.perf_counter() - t0) * 1000
    log(f"Deterministic digest: {len(deterministic_items)} items "
        f"({', '.join(i['category'] for i in deterministic_items)})")

    today = datetime.now().astimezone().date().isoformat()
    rows = [{
        "date": today, "task_pair": "aggregation", "side": "engineered",
        "volume_n": 1, "trial": 0, "tokens": "", "latency_ms": round(eng_latency, 6),
        "cost_usd": 0.0, "cost_usd_paid_tier_sensitivity": round(paid_tier_sensitivity_cost(eng_latency), 12),
        "accuracy_pass": True,
        "notes": f"volume_n=1 is real, not a placeholder -- only one seeded EI test case exists today, "
                 f"see README Progress Log. {len(deterministic_items)} deterministic items built.",
    }]

    log(f"Running {trials} LLM trial(s) against the same real digest request...")
    for trial in range(1, trials + 1):
        t0 = time.perf_counter()
        try:
            cards = generate_weekly_digest(user_id) or []
        except Exception as exc:
            log(f"  trial {trial}: FAILED ({exc})")
            rows.append({
                "date": today, "task_pair": "aggregation", "side": "llm",
                "volume_n": 1, "trial": trial, "tokens": 0, "latency_ms": "",
                "cost_usd": 0.0, "cost_usd_paid_tier_sensitivity": "", "accuracy_pass": False,
                "notes": f"error: {exc}",
            })
            continue
        latency_ms = (time.perf_counter() - t0) * 1000

        covered, missed = check_coverage(deterministic_items, cards)
        rows.append({
            "date": today, "task_pair": "aggregation", "side": "llm",
            "volume_n": 1, "trial": trial,
            # generate_weekly_digest() doesn't return token counts (it
            # discards call_llm's raw response) -- logged as blank rather
            # than a fabricated number. Open item: thread token counts
            # through if precise LLM-side cost is needed here.
            "tokens": "", "latency_ms": round(latency_ms, 3),
            "cost_usd": "", "cost_usd_paid_tier_sensitivity": "",
            "accuracy_pass": covered,
            "notes": f"{len(cards)} cards generated; " + (
                "all deterministic items covered" if covered
                else f"missed: {'; '.join(missed)}"
            ),
        })
        log(f"  trial {trial}: {len(cards)} cards, {latency_ms:.0f}ms, "
            f"{'all covered' if covered else f'{len(missed)} missed'}")

    write_rows(rows)
    log(f"Done. Wrote {len(rows)} rows to {RESULTS_PATH}. "
        f"NOTE: LLM-side cost_usd is blank -- generate_weekly_digest() doesn't "
        f"expose token counts today, see notes above.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trials", type=int, default=5, help="Number of repeated LLM trials (default: 5, per Phase 3's >=5 trials/tier)")
    parser.add_argument("--username", default="Amit", help="Account to generate the digest for (default: Amit)")
    args = parser.parse_args()
    run(args.trials, args.username)


if __name__ == "__main__":
    main()
