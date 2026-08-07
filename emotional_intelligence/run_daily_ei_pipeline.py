"""
Scheduled driver for the full real-user EI pipeline -- run this on a
schedule (Windows Task Scheduler, cron, etc.) twice a day so new
conversations get folded into emotional_intelligence as they accumulate,
matching the original "runs twice/thrice a day" production design.

Runs five stages in sequence, each a subprocess call to the existing,
already-tested script -- this file doesn't duplicate any extraction logic,
so a fix to any one stage's script doesn't need a matching fix here:

  1. real_user_extraction.py     -- facts/preferences/beliefs/memories from
     each user's own new DICTATED conversations. Works for real users today.
  2. chat_feedback_extraction.py -- facts/corrections from each user's own
     new CHAT messages (the feedback loop: a correction typed into chat,
     e.g. "actually Emma is my niece, not my daughter", was previously
     never captured anywhere -- only dictated conversations were ever read).
     Resolves corrections itself, directly: it full-text-searches the
     subject's own existing facts for ones the new message might contradict
     and asks the LLM to mark supersedes_fact_id explicitly. (The first
     version of this deferred that to fact_dedup.py's bulk clustering --
     tested against a real correction and it missed it twice, even after
     fixing the extracted wording, because bulk-clustering ~100 facts at
     once isn't reliable for spotting one specific contradiction. Targeted,
     small-context supersession is what actually works.)
  3. fact_dedup.py               -- still runs after, for the thing it's
     actually good at: consolidating near-duplicate wordings of the same
     fact across many independent observations (proven on the Friends
     corpus -- e.g. ten separately-extracted "engaged_to: Monica" mentions
     merged into one canonical fact). Not relied on for corrections anymore.
  4. personality_batch.py        -- Big Five snapshot per qualifying subject.
     Its qualifying query is generic (facts+beliefs+memories count, no
     Friends-specific dependency), so it genuinely works for real users too.
  5. relationship_batch.py       -- trust/conflict/support per qualifying pair.
     NOTE: its co-occurrence logic (fetch_subject_episode_sets) is built
     entirely on Friends-transcript tables (speaker_aliases +
     episode_speaker_transcript), which real users have zero rows in. This
     stage will legitimately find 0 qualifying real-user pairs every run
     until a real-user co-occurrence source (e.g. shared calls/friendships)
     is built -- that's expected today, not a bug, and this wrapper logs it
     as a normal "0 pairs" outcome rather than a failure. Included now,
     as requested, so the pipeline's shape is already in place for when
     that real-user co-occurrence logic exists.

Each stage's failure is logged but does NOT block the later stages -- they
work off whatever data already exists, independent of whether this run's
extraction step succeeded.

Usage:
    python run_daily_ei_pipeline.py                # all users, all new conversations
    python run_daily_ei_pipeline.py --user-id 33    # scope extraction to one user (testing/simulation)
    python run_daily_ei_pipeline.py --limit 20      # cap how many conversations the extraction stage processes

Scheduling (Windows Task Scheduler, twice daily at 8am and 8pm):
    schtasks /create /tn "EI Pipeline AM" /tr "\"<path to venv python.exe>\" \"<path to this file>\"" /sc daily /st 08:00
    schtasks /create /tn "EI Pipeline PM" /tr "\"<path to venv python.exe>\" \"<path to this file>\"" /sc daily /st 20:00
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG_DIR = HERE / "logs"


def run_stage(name: str, cmd: list[str], log_file) -> int:
    started = datetime.now()
    result = subprocess.run(cmd, cwd=str(HERE), capture_output=True, text=True)
    finished = datetime.now()

    log_file.write(f"\n--- stage: {name} ---\n")
    log_file.write(f"started={started.isoformat()} finished={finished.isoformat()} "
                    f"duration={(finished - started).total_seconds():.1f}s exit_code={result.returncode}\n")
    log_file.write(f"command={' '.join(cmd)}\n")
    log_file.write("stdout:\n" + result.stdout)
    if result.stderr:
        log_file.write("stderr:\n" + result.stderr)

    status = "OK" if result.returncode == 0 else "FAILED"
    print(f"[{status}] {name} finished in {(finished - started).total_seconds():.1f}s (exit_code={result.returncode})")
    print(result.stdout[-800:])
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the full real-user EI pipeline (extraction + personality + relationship) and log the outcome.")
    parser.add_argument("--user-id", type=int, default=None, help="Scope the extraction stage to one user (default: all users).")
    parser.add_argument("--limit", type=int, default=0, help="Max conversations the extraction stage processes (0 = all unprocessed).")
    args = parser.parse_args()

    LOG_DIR.mkdir(exist_ok=True)
    log_path = LOG_DIR / f"ei_pipeline_{datetime.now().strftime('%Y-%m-%d')}.log"

    extraction_cmd = [sys.executable, str(HERE / "real_user_extraction.py")]
    if args.user_id is not None:
        extraction_cmd += ["--user-id", str(args.user_id)]
    if args.limit:
        extraction_cmd += ["--limit", str(args.limit)]

    exit_codes = {}
    with open(log_path, "a", encoding="utf-8") as log_file:
        log_file.write(f"\n{'=' * 70}\nrun begins {datetime.now().isoformat()}\n")

        chat_feedback_cmd = [sys.executable, str(HERE / "chat_feedback_extraction.py")]
        if args.user_id is not None:
            chat_feedback_cmd += ["--user-id", str(args.user_id)]

        exit_codes["extraction"] = run_stage("real_user_extraction", extraction_cmd, log_file)
        exit_codes["chat_feedback"] = run_stage("chat_feedback_extraction", chat_feedback_cmd, log_file)
        exit_codes["fact_dedup"] = run_stage(
            "fact_dedup", [sys.executable, str(HERE / "fact_dedup.py")], log_file
        )
        exit_codes["personality"] = run_stage(
            "personality_batch", [sys.executable, str(HERE / "personality_batch.py")], log_file
        )
        exit_codes["relationship"] = run_stage(
            "relationship_batch", [sys.executable, str(HERE / "relationship_batch.py")], log_file
        )

        log_file.write(f"\nrun ends {datetime.now().isoformat()} exit_codes={exit_codes}\n")

    overall_ok = all(code == 0 for code in exit_codes.values())
    print(f"\n{'ALL STAGES OK' if overall_ok else 'ONE OR MORE STAGES FAILED'} -- see {log_path}")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
