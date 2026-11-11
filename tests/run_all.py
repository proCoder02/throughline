"""Runs the test suites and prints one summary line per suite.

    python tests/run_all.py              # server suites (need the local Postgres + Redis from .env)
    python tests/run_all.py --browser    # + browser suites (need `python app.py` running on :5000
                                         #   and Playwright: pip install playwright; uses installed Chrome)
    python tests/run_all.py --ai         # + suites that call the real AI (cost tokens, slower)

Run from the repo root. Exit code is non-zero if any suite fails.
Each suite is a plain script that prints PASS/FAIL lines and "N/M passed"."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = [
    "test_corporate.py", "test_corp_work.py", "test_corp_onboarding.py", "test_corp_projects.py",
    "test_corp_tickets.py", "test_corp_ask_threads.py", "test_corp_client_view.py", "test_corp_identity.py",
    "test_ai_quota.py", "test_legal.py",
]
AI = ["roleplay_corporate.py"]  # real model calls
BROWSER = [
    "e2e_corporate.py", "e2e_corp_work.py", "e2e_tickets.py", "e2e_ask_threads.py", "e2e_client_view.py",
    "e2e_home_ask.py", "e2e_invite_search.py", "e2e_legal.py", "e2e_ux.py", "e2e_corp_alerts.py",
]


def run(path: str) -> tuple[bool, str, float]:
    started = time.time()
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run([sys.executable, path], cwd=ROOT, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=1800)
    out = proc.stdout + proc.stderr
    m = None
    for m in re.finditer(r"(\d+)/(\d+) passed", out):
        pass
    fails = [line for line in out.splitlines() if line.startswith("FAIL")]
    ok = proc.returncode == 0 and m is not None and m.group(1) == m.group(2) and not fails
    summary = f"{m.group(1)}/{m.group(2)} passed" if m else "no result (crashed?)"
    if not ok:
        tail = "\n".join(fails[:5] or out.strip().splitlines()[-8:])
        summary += "\n      " + tail.replace("\n", "\n      ")
    return ok, summary, time.time() - started


def main() -> int:
    suites = [os.path.join("tests", "server", s) for s in SERVER]
    if "--ai" in sys.argv:
        suites += [os.path.join("tests", "server", s) for s in AI]
    if "--browser" in sys.argv:
        suites += [os.path.join("tests", "browser", s) for s in BROWSER]
    failed = 0
    for s in suites:
        if not os.path.exists(os.path.join(ROOT, s)):
            print(f"SKIP {s} (missing)")
            continue
        ok, summary, secs = run(s)
        failed += not ok
        print(f"{'OK  ' if ok else 'FAIL'} {s:44} {summary}  ({secs:.0f}s)", flush=True)
    print(f"\n{len(suites) - failed}/{len(suites)} suites passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
