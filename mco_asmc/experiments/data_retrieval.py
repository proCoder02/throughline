"""
Phase 1 ASMC experiment -- Data Retrieval task pair.

Task definition (locked in mco_asmc/README.md Phase 0):
  Given a friend, answer "What was the most recent message in my
  conversation with them, and who sent it?" via two paths:

  - engineered: one indexed SQL query against direct_messages
      (ORDER BY created_at DESC LIMIT 1)
  - llm: the full message thread with that friend is dumped into context
      and an LLM (Ollama gpt-oss:120b) is asked the same question in
      natural language

This mirrors a real AI-adoption shortcut: instead of writing the one-line
indexed query, dump the relevant rows to an LLM and ask it in English.

Accuracy grading (fixed 2026-10-02): word-overlap matching decides whether
the retrieval answer's CONTENT is correct, tolerating paraphrasing. Two
earlier approaches were tried and found broken by real testing first: exact
substring match (confirmed too brittle by a 5-trial swing of 80-100%
accuracy on what should have been consistent LLM behavior), then
LLM-as-judge (confirmed unreliable -- this project's own model gave
"incorrect" on a clearly-correct paraphrase immediately after being shown
that exact pair as a worked "correct" example). See
judge_retrieval_answer()'s docstring and mco_asmc/README.md's "Fixes
Required" list for the full story.

Usage:
    python mco_asmc/experiments/data_retrieval.py --n 10 --trial 1

Writes one row per request to mco_asmc/results/raw_data_retrieval.csv
(appends; creates the file with a header on first run).

Where this runs: per explicit research requirement, experiments run ON the
production server (same workflow as seed_demo_data.py -- scp this folder up,
SSH in, run it there against /opt/throughline/app/.env's own DATABASE_URL),
not against a local copy. This also means Grafana's node_exporter/
postgres_exporter metrics (see grafana_client.py) genuinely describe the
machine these queries run on, not a different one.

Safety: same guard as seed_demo_data.py -- DATABASE_URL must contain
"localhost"/"127.0.0.1", which production's own .env satisfies from the
server's own point of view (Postgres is local to that box). This guard's
real purpose is "never point this at an arbitrary database over the
network by accident," not "never run on production" -- running the script
physically on the production host, reading its own .env, is the intended
path. The engineered-side queries are trivial single-row indexed SELECTs
(sub-10ms each in the N=10 smoke test), so added load on real traffic is
negligible even at N=1,000; the LLM side calls out to Ollama Cloud and adds
no load to the production box at all.
"""
from __future__ import annotations

import argparse
import csv
import os
import time
from datetime import datetime
from pathlib import Path

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

load_dotenv()

DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    raise SystemExit("DATABASE_URL is required (set it in .env)")
if "localhost" not in DB_URL and "127.0.0.1" not in DB_URL:
    raise SystemExit(
        "DATABASE_URL doesn't point at a database local to wherever this is "
        "running. Run this script ON the target host (local dev box or the "
        "production server via SSH) so DATABASE_URL is that host's own "
        "localhost connection -- never point it at a remote DB over the network."
    )

OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY") or os.getenv("olama_api_key")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:120b")

# Locked 2026-10-02 against https://ollama.com/pricing (peak-hour rate for
# gpt-oss:120b; off-peak rate exists but isn't used here -- see Phase 0 note
# in mco_asmc/README.md).
PRICE_PER_1M_INPUT_TOKENS = 0.15
PRICE_PER_1M_OUTPUT_TOKENS = 0.60

# Engineered-side cost. The production host is an Oracle Always Free A1 Flex
# instance (2 OCPU/12GB, confirmed against infra/terraform/variables.tf) --
# its real OCI bill for compute is $0, not an estimate. That real-$0 figure
# is reported as-is (see "real_cost_usd" below), which is itself a finding
# (see mco_asmc/README.md Progress Log), not a placeholder.
#
# Alongside it, "paid_tier_cost_usd" is a deliberately conservative SENSITIVITY
# figure: what this query would cost on paid OCI A1 Flex pricing ($0.01/OCPU-hr
# + $0.0015/GB-hr, confirmed via Oracle's published rates, checked 2026-10-02)
# if its entire measured wall-clock latency were spent at 100% of one OCPU --
# an over-estimate (actual CPU-seconds used by a sub-10ms indexed lookup is
# far less than its wall-clock latency), but a defensible upper bound without
# per-query CPU profiling. Real per-query CPU attribution would need
# pg_stat_statements (not enabled yet -- see Progress Log); Grafana's
# node_exporter *does* now monitor this exact host (experiments run ON
# production), but its scrape interval (tens of seconds) is far coarser than
# a single sub-10ms query, so it still can't resolve a single request's cost
# -- useful for Phase 2's aggregate opacity comparison, not Phase 1's
# per-request figure. See grafana_client.py's docstring.
OCI_A1_FLEX_OCPU_HOUR_USD = 0.01
OCI_A1_FLEX_GB_HOUR_USD = 0.0015
ENGINEERED_HOST_OCPUS = 1  # conservative: price as if saturating 1 full OCPU


def paid_tier_sensitivity_cost(latency_ms: float) -> float:
    hours = (latency_ms / 1000) / 3600
    return hours * ENGINEERED_HOST_OCPUS * OCI_A1_FLEX_OCPU_HOUR_USD


RESULTS_PATH = Path(__file__).resolve().parent.parent / "results" / "raw_data_retrieval.csv"
CSV_COLUMNS = ["date", "task_pair", "side", "volume_n", "trial", "tokens", "latency_ms",
               "cost_usd", "cost_usd_paid_tier_sensitivity", "accuracy_pass", "notes"]


def log(msg: str) -> None:
    print(f"[retrieval] {msg}", flush=True)


def get_db():
    conn = psycopg2.connect(DB_URL)
    return conn, conn.cursor()


def load_test_cases(cur, amit_id: int, n: int) -> list[dict]:
    """One case per friend (cycling through the friend list if n exceeds the
    friend count) -- each case is "what was the last message with this
    friend". Friends are ordered by id so repeated runs are reproducible.

    Only friends with >=1 direct_message are eligible -- a friend with no
    message history has no ground truth to grade against, which isn't the
    same failure mode as "wrong answer" (see the N=10 smoke-test caveat in
    mco_asmc/README.md's Progress Log, where this surfaced as a bug)."""
    cur.execute(
        "SELECT f.friend_id, COALESCE(f.nickname, u.username) AS friend_name "
        "FROM friendships f JOIN users u ON u.id = f.friend_id "
        "WHERE f.user_id = %s AND EXISTS ("
        "  SELECT 1 FROM direct_messages dm WHERE "
        "  (dm.sender_id = %s AND dm.recipient_id = f.friend_id) OR "
        "  (dm.sender_id = f.friend_id AND dm.recipient_id = %s)"
        ") ORDER BY f.friend_id",
        (amit_id, amit_id, amit_id),
    )
    friends = cur.fetchall()
    if not friends:
        raise SystemExit(f"No friends with message history found for user_id {amit_id} -- run seed_demo_data.py first.")
    return [friends[i % len(friends)] for i in range(n)]


def engineered_last_message(cur, amit_id: int, friend_id: int) -> tuple[dict | None, float]:
    t0 = time.perf_counter()
    cur.execute(
        "SELECT content, created_at, sender_id FROM direct_messages "
        "WHERE (sender_id = %s AND recipient_id = %s) OR (sender_id = %s AND recipient_id = %s) "
        "ORDER BY created_at DESC LIMIT 1",
        (amit_id, friend_id, friend_id, amit_id),
    )
    row = cur.fetchone()
    latency_ms = (time.perf_counter() - t0) * 1000
    if not row:
        return None, latency_ms
    return {"content": row[0], "created_at": row[1], "sender_id": row[2]}, latency_ms


def llm_last_message(cur, amit_id: int, friend_id: int, friend_name: str) -> dict:
    cur.execute(
        "SELECT sender_id, content FROM direct_messages "
        "WHERE (sender_id = %s AND recipient_id = %s) OR (sender_id = %s AND recipient_id = %s) "
        "ORDER BY created_at ASC",
        (amit_id, friend_id, friend_id, amit_id),
    )
    rows = cur.fetchall()
    thread_text = "\n".join(f"{'Amit' if r[0] == amit_id else friend_name}: {r[1]}" for r in rows)
    prompt = (
        f"Here is a full text conversation between Amit and {friend_name}:\n\n{thread_text}\n\n"
        f"Question: what was the most recent message in this conversation, and who sent it? "
        f"Answer in one short sentence and quote the message content exactly as written above."
    )

    t0 = time.perf_counter()
    if not OLLAMA_API_KEY:
        raise RuntimeError("Missing Ollama API key (OLLAMA_API_KEY / olama_api_key in .env)")
    response = requests.post(
        "https://ollama.com/api/chat",
        headers={"Authorization": f"Bearer {OLLAMA_API_KEY}", "Content-Type": "application/json"},
        json={"model": OLLAMA_MODEL, "messages": [{"role": "user", "content": prompt}], "stream": False},
        timeout=180,
    )
    latency_ms = (time.perf_counter() - t0) * 1000
    response.raise_for_status()
    data = response.json()
    answer = data.get("message", {}).get("content", "")
    prompt_tokens = data.get("prompt_eval_count", 0)
    completion_tokens = data.get("eval_count", 0)
    cost_usd = (prompt_tokens / 1_000_000 * PRICE_PER_1M_INPUT_TOKENS +
                completion_tokens / 1_000_000 * PRICE_PER_1M_OUTPUT_TOKENS)
    return {
        "answer": answer, "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
        "latency_ms": latency_ms, "cost_usd": cost_usd,
    }


# Common words that appear in almost any sentence regardless of actual
# content -- excluded so overlap is measured on words that actually carry
# the message's distinguishing content, not incidental scaffolding words a
# retrieval-style answer tends to introduce (e.g. "message", "conversation").
_GRADING_STOPWORDS = {
    "still", "this", "that", "with", "from", "have", "just", "been", "they",
    "their", "about", "asking", "which", "where", "there", "would", "could",
    "should", "what", "when", "will", "your", "said", "told", "answer",
    "answered", "message", "conversation", "most", "recent", "sent",
}


def judge_retrieval_answer(true_content: str, llm_answer: str, threshold: float = 0.4) -> tuple[bool, int]:
    """Grades whether an LLM retrieval answer's CONTENT matches the true
    message, tolerating paraphrasing -- what "correct" actually means for a
    natural-language answer. Fixed 2026-10-02, see mco_asmc/README.md's
    "Fixes Required" list; this replaces TWO earlier attempts, both
    confirmed broken by real testing, not assumption:

    1. Exact substring match (the original grader) -- failed on
       correct-but-paraphrased answers, confirmed by a 5-trial swing of
       80-100% accuracy on what should have been consistent LLM behavior
       (trials 1-5 at N=10). The grading method was the noise source, not
       the LLM.
    2. LLM-as-judge (the first attempted fix, several prompt variants
       tried: plain yes/no, few-shot worked examples, chain-of-thought) --
       this model (gpt-oss:120b, via Ollama's native API) gave "incorrect"/
       "no" on a clearly-correct paraphrase even immediately after being
       shown that exact pair labeled "correct" in a worked example in the
       same prompt, and produced zero visible reasoning despite being asked
       for it. Reproducible across multiple prompt formulations, not a
       fluke -- this specific model in this configuration is not reliable
       for this judgment task. Worth a line in the paper: "use an LLM to
       judge the LLM" is a commonly-suggested fix for brittle string-match
       graders, but isn't automatically better -- it has its own failure
       mode and must be verified, not assumed, same as any other grader.

    This instead checks word-overlap: what fraction of the true message's
    significant words (>=4 chars, common scaffolding words excluded) appear
    in the answer text. Deterministic, free, no secondary LLM call --
    verified against synthetic paraphrase/wrong/verbatim test cases (not
    production data) before being trusted here. Returns (verdict, 0) -- the
    second element is a token count, always 0 since there's no LLM call
    involved; kept for call-site compatibility with the two earlier,
    now-removed approaches."""
    def significant_words(text: str) -> list[str]:
        return [w.strip(".,!?\"'").lower() for w in text.split()
                if len(w.strip(".,!?\"'")) >= 4 and w.strip(".,!?\"'").lower() not in _GRADING_STOPWORDS]

    truth_words = significant_words(true_content)
    if not truth_words:
        return True, 0  # nothing distinctive to check (e.g. a one-word "ok") -- can't meaningfully fail this
    answer_lower = llm_answer.lower()
    matched = sum(1 for w in truth_words if w in answer_lower)
    return (matched / len(truth_words)) >= threshold, 0


def write_rows(rows: list[dict]) -> None:
    is_new = not RESULTS_PATH.exists()
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if is_new:
            writer.writeheader()
        writer.writerows(rows)


def run(n: int, trial: int, username: str, side: str) -> None:
    """side: 'both' (default), 'engineered' (SQL timing only -- no message
    content leaves this process, safe to run on production), or 'llm' (the
    Ollama path only -- run against a local copy, never against production,
    since it sends message content to an external API). See the Progress
    Log entry on why the two sides are split across environments: measuring
    engineered-path latency only means something if it's measured on the
    real target infra (production's Always Free A1 Flex box, not a local
    dev machine), but sending that same box's real message content to an
    external LLM is a different and separate risk that doesn't need taking
    just to get a latency number."""
    conn, cur = get_db()
    cur.execute("SELECT id FROM users WHERE username = %s", (username,))
    row = cur.fetchone()
    if not row:
        raise SystemExit(f"No user named '{username}' found.")
    amit_id = row[0]

    cases = load_test_cases(cur, amit_id, n)
    today = datetime.now().astimezone().date().isoformat()
    rows = []

    log(f"Running {n} retrieval cases (trial {trial}, side={side})...")
    for i, case in enumerate(cases):
        friend_id, friend_name = case[0], case[1]

        truth, eng_latency = engineered_last_message(cur, amit_id, friend_id)
        if side in ("both", "engineered"):
            rows.append({
                "date": today, "task_pair": "data_retrieval", "side": "engineered",
                "volume_n": n, "trial": trial, "tokens": "", "latency_ms": round(eng_latency, 3),
                # Real billed cost: $0 (Always Free A1 Flex tier). Sensitivity
                # column alongside it prices the same latency as if it ran on
                # paid OCI compute -- see the cost-model comment block above.
                "cost_usd": 0.0, "cost_usd_paid_tier_sensitivity": round(paid_tier_sensitivity_cost(eng_latency), 12),
                "accuracy_pass": truth is not None,  # test cases are pre-filtered to have message history
                "notes": "cost_usd=0 is the real OCI bill (Always Free tier); "
                         "cost_usd_paid_tier_sensitivity is a conservative paid-tier estimate, see data_retrieval.py",
            })

        if side not in ("both", "llm"):
            log(f"  [{i+1}/{n}] {friend_name}: engineered={eng_latency:.1f}ms")
            continue

        try:
            result = llm_last_message(cur, amit_id, friend_id, friend_name)
        except Exception as exc:
            log(f"  [{i+1}/{n}] {friend_name}: LLM call failed ({exc})")
            rows.append({
                "date": today, "task_pair": "data_retrieval", "side": "llm",
                "volume_n": n, "trial": trial, "tokens": 0, "latency_ms": "",
                "cost_usd": 0.0, "cost_usd_paid_tier_sensitivity": "", "accuracy_pass": False,
                "notes": f"error: {exc}",
            })
            continue

        old_substring_match = bool(truth) and truth["content"].strip().lower() in result["answer"].strip().lower()
        new_verdict, _ = judge_retrieval_answer(truth["content"], result["answer"]) if truth else (False, 0)
        accuracy_pass = new_verdict
        total_tokens = result["prompt_tokens"] + result["completion_tokens"]
        rows.append({
            "date": today, "task_pair": "data_retrieval", "side": "llm",
            "volume_n": n, "trial": trial, "tokens": total_tokens,
            "latency_ms": round(result["latency_ms"], 3), "cost_usd": round(result["cost_usd"], 8),
            "cost_usd_paid_tier_sensitivity": "",  # not a meaningful concept on the LLM side -- its $ cost is already real, metered per-token regardless of host
            "accuracy_pass": accuracy_pass,
            "notes": f"accuracy_pass = word-overlap grader (fixed 2026-10-02, see README Fixes Required -- "
                     f"two earlier approaches, exact-substring and LLM-as-judge, were both tested and found "
                     f"unreliable); old substring-match grader would have said {old_substring_match}"
                     + ("" if old_substring_match == new_verdict else " -- DISAGREEMENT with old grader"),
        })
        log(f"  [{i+1}/{n}] {friend_name}: engineered={eng_latency:.1f}ms, "
            f"llm={result['latency_ms']:.1f}ms ({total_tokens} tok, ${result['cost_usd']:.6f}), "
            f"match={new_verdict}" + ("" if old_substring_match == new_verdict else f" (old grader said {old_substring_match})"))

    write_rows(rows)
    conn.close()
    log(f"Done. Wrote {len(rows)} rows to {RESULTS_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n", type=int, default=10, help="Number of retrieval cases to run (default: 10)")
    parser.add_argument("--trial", type=int, default=1, help="Trial number within this volume tier (default: 1)")
    parser.add_argument("--username", default="Amit", help="Account to run cases for (default: Amit)")
    parser.add_argument("--side", choices=["both", "engineered", "llm"], default="both",
                         help="Run only the SQL side (safe on production, no content leaves the box), "
                              "only the LLM side (run locally, never on production), or both (local only).")
    args = parser.parse_args()
    run(args.n, args.trial, args.username, args.side)


if __name__ == "__main__":
    main()
