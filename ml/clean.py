"""Step 2: clean the collected examples and split them.

Keeps only answers that are valid JSON with the expected keys, drops
duplicates and anything that looks like an email address or phone number,
then writes ml/data/train.jsonl (90%) and ml/data/test.jsonl (10%), all tasks mixed.

    python ml/clean.py
"""
import json
import os
import random
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
RAW, OUT = os.path.join(ROOT, "data", "raw"), os.path.join(ROOT, "data")
KEYS = {"extract": {"decisions", "commitments", "blockers"}, "evidence": {"done"}, "radar": {"ids"},
        "common_ground": {"agreed", "open", "options"}}
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE = re.compile(r"(?<!\d)\+?\d[\d\s-]{8,13}\d(?!\d)")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")  # dates look like phone numbers to PHONE; ignore them


def has_pii(text):
    return bool(EMAIL.search(text) or PHONE.search(DATE.sub(" ", text)))


def answer_ok(task, text):
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return False
    try:
        obj = json.loads(m.group(0))
    except ValueError:
        return False
    return isinstance(obj, dict) and KEYS[task] <= set(obj)


def main():
    rows, seen, dropped = [], set(), {"bad": 0, "dup": 0, "pii": 0}
    for task in KEYS:
        path = os.path.join(RAW, f"{task}.jsonl")
        if not os.path.exists(path):
            continue
        for line in open(path, encoding="utf-8"):
            ex = json.loads(line)
            msgs = ex["messages"]
            if not answer_ok(task, msgs[-1]["content"]):
                dropped["bad"] += 1; continue
            key = (task, msgs[1]["content"])
            if key in seen:
                dropped["dup"] += 1; continue
            if any(has_pii(m["content"]) for m in msgs):
                dropped["pii"] += 1; continue
            seen.add(key)
            # Store just the JSON as the target: the small model learns to answer with JSON only.
            msgs[-1]["content"] = re.search(r"\{.*\}", msgs[-1]["content"], re.DOTALL).group(0)
            rows.append({"task": task, "messages": msgs})
    random.Random(1).shuffle(rows)
    cut = max(1, len(rows) // 10)
    for name, part in (("test", rows[:cut]), ("train", rows[cut:])):
        with open(os.path.join(OUT, f"{name}.jsonl"), "w", encoding="utf-8") as f:
            for r in part:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    by_task = {t: sum(r["task"] == t for r in rows) for t in KEYS}
    print(f"kept {len(rows)} ({by_task}); dropped {dropped}; train {len(rows) - cut}, test {cut}")
    if len(rows) < 200:
        print("note: under 200 examples -- fine for a trial run; aim for 300-1,000 before relying on it", file=sys.stderr)


if __name__ == "__main__":
    main()
