"""Step 6: score a running Ollama model on the held-back examples before
switching the app to it. Same scoring as the Kaggle notebook.

    python ml/check_model.py http://10.0.0.12:11434 throughline-3b
Ship only if every task shows valid JSON >= 95% and agreement >= 85%."""
import json
import os
import re
import sys
import time

import requests

URL, MODEL = (sys.argv[1].rstrip("/"), sys.argv[2]) if len(sys.argv) > 2 else (None, None)
if not URL:
    sys.exit(__doc__)
TEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "test.jsonl")


def js(t):
    m = re.search(r"\{.*\}", t or "", re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except ValueError:
        return None


def same(task, a, b):
    if task == "extract":
        return len(a.get("commitments") or []) == len(b.get("commitments") or []) and \
            len(a.get("decisions") or []) == len(b.get("decisions") or [])
    if task == "evidence":
        return bool(a.get("done")) == bool(b.get("done"))
    if task == "radar":
        return set(a.get("ids") or []) == set(b.get("ids") or [])
    return all(k in a for k in ("agreed", "open", "options"))


res, secs = {}, []
for line in open(TEST, encoding="utf-8"):
    ex = json.loads(line)
    t0 = time.time()
    out = requests.post(f"{URL}/api/chat", timeout=600, json={"model": MODEL, "messages": ex["messages"][:-1], "stream": False,
                                                              "options": {"temperature": 0.1}}).json().get("message", {}).get("content", "")
    secs.append(time.time() - t0)
    got, want = js(out), js(ex["messages"][-1]["content"])
    r = res.setdefault(ex["task"], [0, 0, 0])
    r[0] += 1; r[1] += got is not None; r[2] += bool(got and same(ex["task"], got, want))
ok = True
for t, (n, valid, agree) in res.items():
    print(f"{t:14} valid JSON {valid / n:5.0%}   agrees with teacher {agree / n:5.0%}   (n={n})")
    ok &= valid / n >= 0.95 and agree / n >= 0.85
secs.sort()
print(f"speed: median {secs[len(secs) // 2]:.0f}s, slowest {secs[-1]:.0f}s per answer")
print("PASS: safe to set LOCAL_LLM_URL" if ok else "NOT READY: collect more examples and retrain")
sys.exit(0 if ok else 1)
