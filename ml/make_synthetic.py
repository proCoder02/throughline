"""Step 1 (synthetic): invent realistic work meetings and chats with the
teacher model (Groq, free tier), then run Corporate's REAL prompts on them
(corporate/ai.py) with the example collector on. Output: ml/data/raw/<task>.jsonl
in exactly the format production uses. No user data is involved.

    python ml/make_synthetic.py --n 40          # ~40 meetings -> extract, evidence, radar, common_ground
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))
os.environ["CORPORATE_COLLECT_DIR"] = os.path.join(ROOT, "ml", "data", "raw")
from corporate import ai, llm  # noqa: E402

TEAMS = ["Backend", "Database", "Mobile app", "Sales", "Finance", "Marketing", "Customer support", "DevOps", "Product", "Design"]
TOPICS = ["a database migration", "a pricing change", "a customer escalation", "the Q3 roadmap", "a security audit",
          "an app release", "hiring for an open role", "a vendor contract renewal", "an outage post-mortem",
          "a marketing campaign launch", "invoice export for a big customer", "moving to a new cloud region"]
NAMES = ["Amit", "Khushi", "Ravi", "Priya", "Neha", "Arjun", "Sara", "Vikram", "Ananya", "Rahul", "Meera", "John", "Fatima"]
STYLES = ["plain English", "Indian English with some Hinglish", "short and informal", "formal and detailed"]


def teacher(messages, retries=6):
    for i in range(retries):
        try:
            return llm.groq_chat(messages, model=os.getenv("TEACHER_MODEL", "openai/gpt-oss-120b"), timeout=120)
        except Exception as e:  # noqa: BLE001 -- free tier: wait out rate limits
            if "429" in str(e) and i < retries - 1:
                time.sleep(20 * (i + 1))
                continue
            raise


def write(prompt: str) -> str:
    return teacher([{"role": "user", "content": prompt}]).strip()


def main(n: int, seed: int) -> None:
    rnd = random.Random(seed)
    pool = []  # commitments from earlier meetings: other teams' work for the radar
    for i in range(n):
        team, topic, style = rnd.choice(TEAMS), rnd.choice(TOPICS), rnd.choice(STYLES)
        people = rnd.sample(NAMES, rnd.randint(2, 5))
        t = write(f"Write a realistic transcript of a {team} team meeting about {topic}, in {style}. "
                  f"Speakers: {', '.join(people)}. 12-30 lines, 'Name: text' per line. Include "
                  f"{rnd.randint(0, 2)} clear decisions, {rnd.randint(1, 3)} promises with due dates said casually "
                  f"(e.g. 'by Friday', 'end of month', 'on the 20th'), and {rnd.choice(['no', 'one'])} blocker. "
                  "Sometimes leave an owner or date vague. Transcript only.")
        try:
            r = ai.extract(teacher, t, [p.lower() for p in people], [])
        except ai.AIUnavailable as e:
            print("teacher unavailable:", e.cause); break
        for c in r["commitments"][:2]:
            if not isinstance(c, dict) or not c.get("text"):
                continue
            pool.append({"id": len(pool) + 1, "text": c["text"], "due_date": c.get("due") or "2030-01-31", "team": team})
            done = rnd.random() < 0.5
            act = write(f"Write 4-8 lines of a work chat ('Name: text') among {', '.join(people)} a few days after this promise: "
                        f"\"{c['text']}\" (owner {c.get('owner') or 'unclear'}). "
                        + ("Make it clearly DONE (e.g. it was sent, shipped, shared)." if done else
                           "Mention related work but it is NOT done yet; maybe a delay.") + " Chat only.")
            ai.evidence(teacher, c["text"], c.get("owner") or "", act)
        for b in r["blockers"][:1]:
            others = [p for p in pool if p["team"] != team]
            if isinstance(b, dict) and b.get("text") and others:
                ai.radar(teacher, b["text"], rnd.sample(others, min(len(others), rnd.randint(3, 8))))
        if i % 3 == 0:
            d = write(f"Write 6-12 chat lines ('Name: text') where {', '.join(people[:3])} disagree politely about {topic} "
                      f"in {style}, each with a different priority. Chat only.")
            ai.common_ground(teacher, d, [])
        print(f"[{i + 1}/{n}] {team} / {topic}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    main(a.n, a.seed)
