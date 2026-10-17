"""
Phase 1 ASMC experiment -- Classification/tagging task pair.

Task definition (locked in mco_asmc/README.md Phase 0): classify a chat
message's "nearby places" category via two paths:

  - engineered: app.py's real, already-deployed _detect_nearby_category()
      (keyword substring match against _NEARBY_CATEGORY_TAGS) -- replicated
      here verbatim rather than importing app.py, since importing the full
      Flask app would run its DB pool / CostLens install / route
      registration side effects just to reach one pure function.
  - llm: Ollama gpt-oss:120b is given the message and the fixed category
      list, asked to pick one.

Accuracy floor (locked): LLM must agree with the rule-based label >= 85%
of the time (the rule-based label IS the ground truth here, per Phase 0 --
this isn't measuring "correctness" against some external truth, it's
measuring LLM-vs-existing-system agreement, which is what actually matters
for "would swapping this for an LLM silently change behavior").

Unlike data_retrieval.py, this task needs no database at all -- test
messages are combinatorially generated from keyword x sentence-frame
templates, so N=10/100/1,000 are all cheap to generate for real (no
seeded-data volume ceiling the way retrieval/aggregation have).

Usage:
    python mco_asmc/experiments/classification.py --n 10 --trial 1

Where this runs: the engineered side is pure in-process regex/string
matching (no DB, no network) -- representative regardless of machine, but
run on production anyway for consistency with the rest of Phase 1 per
explicit research requirement. The LLM side sends only synthetic,
locally-generated template strings to Ollama Cloud -- never real user data,
so (unlike data_retrieval.py) there's no production-content-exfiltration
concern either way.
"""
from __future__ import annotations

import argparse
import csv
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY") or os.getenv("olama_api_key")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:120b")

PRICE_PER_1M_INPUT_TOKENS = 0.15
PRICE_PER_1M_OUTPUT_TOKENS = 0.60
OCI_A1_FLEX_OCPU_HOUR_USD = 0.01  # see data_retrieval.py for the full rate-card note

RESULTS_PATH = Path(__file__).resolve().parent.parent / "results" / "raw_classification.csv"
CSV_COLUMNS = ["date", "task_pair", "side", "volume_n", "trial", "tokens", "latency_ms",
               "cost_usd", "cost_usd_paid_tier_sensitivity", "accuracy_pass", "notes"]

# ============================================================================
# Replicated verbatim from app.py (~lines 2182-2231) -- see that file for the
# full historical comments on why each category/exclusion exists. Kept in
# sync by hand; if app.py's version changes, this one needs updating too.
# ============================================================================
_NEARBY_INTENT_RE = re.compile(
    r"\bnear(?:by)?\s+me\b|\bnear\s+here\b|\baround\s+(?:here|me)\b|\bclose\s+to\s+me\b|\bnearest\b|\bin\s+my\s+area\b",
    re.IGNORECASE,
)

_NEARBY_CATEGORY_TAGS = {
    "trek": '["route"="hiking"]', "hike": '["route"="hiking"]',
    "hiking": '["route"="hiking"]', "trail": '["route"="hiking"]',
    "shop": '["shop"]', "shopping": '["shop"]', "store": '["shop"]',
    "mall": '["shop"="mall"]',
    "restaurant": '["amenity"="restaurant"]',
    "cafe": '["amenity"="cafe"]', "coffee": '["amenity"="cafe"]',
    "park": '["leisure"="park"]',
    "pharmacy": '["amenity"="pharmacy"]',
    "hospital": '["amenity"="hospital"]',
    "atm": '["amenity"="atm"]',
    "gym": '["leisure"="fitness_centre"]',
    "hotel": '["tourism"="hotel"]',
}
_DEFAULT_NEARBY_FILTER = '["name"]["shop"]'

# tag_filter -> one canonical human-readable label, for grading (several
# keywords collapse to the same underlying filter, e.g. trek/hike/hiking/
# trail all mean "hiking").
_CANONICAL_LABELS = {
    '["route"="hiking"]': "hiking", '["shop"]': "shop", '["shop"="mall"]': "mall",
    '["amenity"="restaurant"]': "restaurant", '["amenity"="cafe"]': "cafe",
    '["leisure"="park"]': "park", '["amenity"="pharmacy"]': "pharmacy",
    '["amenity"="hospital"]': "hospital", '["amenity"="atm"]': "atm",
    '["leisure"="fitness_centre"]': "gym", '["tourism"="hotel"]': "hotel",
    _DEFAULT_NEARBY_FILTER: "generic_nearby",
}
ALL_LABELS = sorted(set(_CANONICAL_LABELS.values())) + ["none"]


def detect_nearby_category(message: str) -> str:
    """Same logic as app.py's _detect_nearby_category + its caller's
    fallback chain, collapsed into one function returning a canonical
    label string (including "none" for not-a-nearby-request)."""
    prompt_lower = message.lower()
    for keyword, tag_filter in _NEARBY_CATEGORY_TAGS.items():
        if re.search(r"\b" + keyword + r"\b", prompt_lower):
            return _CANONICAL_LABELS[tag_filter]
    if _NEARBY_INTENT_RE.search(prompt_lower):
        return _CANONICAL_LABELS[_DEFAULT_NEARBY_FILTER]
    return "none"


# ============================================================================
# Test case generation -- keyword x sentence-frame combinatorics, no DB
# needed. A minority of cases are generic-nearby or true negatives, for a
# realistic label distribution rather than only clean positives.
# ============================================================================
_FRAMES = [
    "any good {kw} near me?", "where's the nearest {kw}?",
    "looking for a {kw} nearby", "is there a {kw} around here",
    "need to find a {kw} close to me", "best {kw} in my area?",
    "can you suggest a {kw} near here", "{kw} nearby please",
]
_GENERIC_NEARBY_MESSAGES = [
    "what's around me?", "anything interesting near here?",
    "show me what's close by", "what's in my area right now?",
]
_NEGATIVE_MESSAGES = [
    "how's the weather today", "tell me a joke", "what time is it",
    "can you summarize my last conversation", "remind me to call mom later",
    "what's my mood been like this week",
]


def generate_test_messages(n: int) -> list[str]:
    keywords = list(_NEARBY_CATEGORY_TAGS.keys())
    pool = []
    for kw in keywords:
        for frame in _FRAMES:
            pool.append(frame.format(kw=kw))
    random.shuffle(pool)
    # ~10% generic-nearby, ~10% true negative, rest category-specific positives
    n_negative = max(1, n // 10)
    n_generic = max(1, n // 10)
    n_positive = n - n_negative - n_generic
    messages = (
        [pool[i % len(pool)] for i in range(n_positive)]
        + [_GENERIC_NEARBY_MESSAGES[i % len(_GENERIC_NEARBY_MESSAGES)] for i in range(n_generic)]
        + [_NEGATIVE_MESSAGES[i % len(_NEGATIVE_MESSAGES)] for i in range(n_negative)]
    )
    random.shuffle(messages)
    return messages[:n]


def llm_classify(message: str) -> dict:
    prompt = (
        f"Classify this chat message into exactly one of these categories: "
        f"{', '.join(ALL_LABELS)}.\n\n"
        f'Message: "{message}"\n\n'
        f'Reply with ONLY the single category name, nothing else. Use "none" if the '
        f'message isn\'t asking about a nearby place at all, and "generic_nearby" if '
        f"it's asking about nearby places in general without a specific category."
    )
    if not OLLAMA_API_KEY:
        raise RuntimeError("Missing Ollama API key (OLLAMA_API_KEY / olama_api_key in .env)")
    t0 = time.perf_counter()
    response = requests.post(
        "https://ollama.com/api/chat",
        headers={"Authorization": f"Bearer {OLLAMA_API_KEY}", "Content-Type": "application/json"},
        json={"model": OLLAMA_MODEL, "messages": [{"role": "user", "content": prompt}], "stream": False},
        timeout=180,
    )
    latency_ms = (time.perf_counter() - t0) * 1000
    response.raise_for_status()
    data = response.json()
    answer = (data.get("message", {}).get("content", "") or "").strip().lower()
    prompt_tokens = data.get("prompt_eval_count", 0)
    completion_tokens = data.get("eval_count", 0)
    cost_usd = (prompt_tokens / 1_000_000 * PRICE_PER_1M_INPUT_TOKENS +
                completion_tokens / 1_000_000 * PRICE_PER_1M_OUTPUT_TOKENS)
    # Tolerant match -- the LLM sometimes wraps the label in a short phrase
    # despite instructions; take the first known label that appears in the
    # answer rather than requiring an exact full-string match.
    matched_label = next((lbl for lbl in ALL_LABELS if lbl in answer), answer)
    return {
        "label": matched_label, "raw_answer": answer, "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens, "latency_ms": latency_ms, "cost_usd": cost_usd,
    }


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


def log(msg: str) -> None:
    print(f"[classification] {msg}", flush=True)


def run(n: int, trial: int) -> None:
    messages = generate_test_messages(n)
    today = datetime.now().astimezone().date().isoformat()
    rows = []
    n_agree = 0

    log(f"Running {n} classification cases (trial {trial})...")
    for i, message in enumerate(messages):
        t0 = time.perf_counter()
        true_label = detect_nearby_category(message)
        eng_latency = (time.perf_counter() - t0) * 1000
        rows.append({
            "date": today, "task_pair": "classification", "side": "engineered",
            "volume_n": n, "trial": trial, "tokens": "", "latency_ms": round(eng_latency, 6),
            "cost_usd": 0.0, "cost_usd_paid_tier_sensitivity": round(paid_tier_sensitivity_cost(eng_latency), 12),
            "accuracy_pass": True,  # the rule-based output IS the ground truth by definition
            "notes": f"true_label={true_label}" if i == 0 else "",
        })

        try:
            result = llm_classify(message)
        except Exception as exc:
            log(f"  [{i+1}/{n}] FAILED: {exc}")
            rows.append({
                "date": today, "task_pair": "classification", "side": "llm",
                "volume_n": n, "trial": trial, "tokens": 0, "latency_ms": "",
                "cost_usd": 0.0, "cost_usd_paid_tier_sensitivity": "", "accuracy_pass": False,
                "notes": f"error: {exc}",
            })
            continue

        agree = result["label"] == true_label
        n_agree += agree
        total_tokens = result["prompt_tokens"] + result["completion_tokens"]
        rows.append({
            "date": today, "task_pair": "classification", "side": "llm",
            "volume_n": n, "trial": trial, "tokens": total_tokens,
            "latency_ms": round(result["latency_ms"], 3), "cost_usd": round(result["cost_usd"], 8),
            "cost_usd_paid_tier_sensitivity": "", "accuracy_pass": agree,
            "notes": f"true={true_label} llm={result['label']}" + ("" if agree else " MISMATCH"),
        })
        log(f"  [{i+1}/{n}] \"{message[:50]}\" true={true_label} llm={result['label']} "
            f"({total_tokens} tok, {result['latency_ms']:.0f}ms) {'OK' if agree else 'MISMATCH'}")

    write_rows(rows)
    accuracy = n_agree / n if n else 0
    floor_pass = accuracy >= 0.85
    log(f"Done. {n_agree}/{n} agreement ({accuracy:.1%}) -- "
        f"{'PASSES' if floor_pass else 'FAILS'} the locked 85% accuracy floor. "
        f"Wrote {len(rows)} rows to {RESULTS_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n", type=int, default=10, help="Number of classification cases to run (default: 10)")
    parser.add_argument("--trial", type=int, default=1, help="Trial number within this volume tier (default: 1)")
    args = parser.parse_args()
    run(args.n, args.trial)


if __name__ == "__main__":
    main()
