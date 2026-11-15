"""What to do when something on the dashboard is wrong: likely cause,
steps, the command to run, how to tell it's fixed, and which one-click
fixes (ops/remediations.py ids) apply.

Keyed like the dashboard's attention items: "job:<job_name>",
"task:<task>", "email:failed", "compute:disk", ... A key without its own
entry falls back to the generic one for its area (see lookup()). Fix ids
that aren't installed on this server are dropped by the dashboard."""
from __future__ import annotations

LOGS = "journalctl -u throughline --since '-1h' --no-pager"

RUNBOOKS = {
    # ---------------------------------------------------------------- workers
    "job:rq_worker_calls": {
        "cause": "No RQ worker process is listening on the 'calls' queue, so uploaded call recordings wait forever. "
                 "Prod has no worker service yet, or it crashed.",
        "steps": [
            "Check whether a worker service exists and is running.",
            "If it's stopped, restart it (button below when it's set up for one-click restart).",
            "If there is no service at all, it has to be added (worker.py as a systemd unit).",
            "Once a worker is up, retry the failed jobs so the waiting recordings get processed.",
        ],
        "command": "systemctl list-units 'throughline*' --all; journalctl -u throughline-worker -n 50 --no-pager",
        "fixed_when": "The card shows Healthy with workers ≥ 1 and the queued count falls to 0.",
        "fixes": ["restart_unit:throughline-worker", "rq_requeue_failed:calls", "probe_now:rq_worker_calls"],
    },
    "job:redis": {
        "cause": "Redis isn't answering: the service stopped, ran out of memory, or the app's REDIS_URL is wrong.",
        "steps": [
            "Check the service and its log.",
            "If memory is full, look at the memory figure on this card before restarting -- a restart drops undelivered live events.",
            "Restart Redis if it's down.",
        ],
        "command": "systemctl status redis-server --no-pager; redis-cli ping; redis-cli info memory | head -20",
        "fixed_when": "Card shows Healthy with a ping under ~5 ms.",
        "fixes": ["restart_unit:redis-server", "probe_now:redis"],
    },
    "job:notify_pubsub_listener": {
        "cause": "The live-event listener inside the app lost its Redis subscription or is stuck. It reconnects by itself; "
                 "if it stays red, Redis is usually the real problem.",
        "steps": [
            "Check the Redis card first -- fix that if it's red.",
            "Look for the listener's reconnect errors in the log.",
            "If Redis is fine and this stays red for 5+ minutes, restart the app (sudo systemctl restart throughline).",
        ],
        "command": "journalctl -u throughline -g 'notify pubsub listener' --since '-1h' --no-pager",
        "fixed_when": "Card shows Healthy and seconds since last loop stays under 60.",
        "fixes": ["probe_now:notify_pubsub_listener"],
    },
    "job:costlens_flush": {
        "cause": "Usage records aren't reaching CostLens: the CostLens server (or the tunnel to it) is down, "
                 "or the flush thread died.",
        "steps": [
            "Check last_error on this card -- a connection error means CostLens or its tunnel is down.",
            "Records are kept in memory (up to 20k) and sent once CostLens is back; nothing to do in the app.",
            "If the flush thread is dead, restart the app.",
        ],
        "command": "journalctl -u throughline -g 'CostLens' --since '-1h' --no-pager",
        "fixed_when": "Buffered drops back near 0 and seconds since last OK is under 30.",
        "fixes": ["probe_now:costlens_flush"],
    },
    "job:email_reminder_worker": {
        "cause": "The reminder-email loop inside the app stopped reporting -- it crashed, or the app is stuck or down.",
        "steps": ["Check the app log for its errors.", "Restart the app if the loop has died: sudo systemctl restart throughline."],
        "command": "journalctl -u throughline -g 'email_reminder_worker' --since '-1h' --no-pager",
        "fixed_when": "A new heartbeat within 5 minutes; card shows Healthy.",
        "fixes": [],
    },
    "job:call_ring_timeout_worker": {
        "cause": "The call ring-timeout loop inside the app stopped reporting -- unanswered calls won't end on their own.",
        "steps": ["Check the app log for its errors.", "Restart the app if the loop has died: sudo systemctl restart throughline."],
        "command": "journalctl -u throughline -g 'call_ring_timeout_worker' --since '-1h' --no-pager",
        "fixed_when": "A new heartbeat within 5 minutes; card shows Healthy.",
        "fixes": [],
    },
    "job:custom_job_scheduler": {
        "cause": "The custom-job scheduler loop stopped -- no custom job will fire until it's back.",
        "steps": ["Check the app log.", "Restart the app: sudo systemctl restart throughline."],
        "command": "journalctl -u throughline -g 'ops.scheduler' --since '-1h' --no-pager",
        "fixed_when": "A new heartbeat within 5 minutes.",
        "fixes": [],
    },
    "job:ops_alert_check": {
        "cause": "The alert checker stopped -- nothing will email or push you about failures until it's back.",
        "steps": ["Check the app log for ops_alert_worker errors.", "Restart the app: sudo systemctl restart throughline."],
        "command": "journalctl -u throughline -g 'ops_alert_worker' --since '-1h' --no-pager",
        "fixed_when": "A new check within 10 minutes.",
        "fixes": [],
    },
    # ---------------------------------------------------------------- scheduled
    "job:ei_pipeline": {
        "cause": "The twice-daily EI batch didn't run (root's cron) or failed part-way (LLM or DB error).",
        "steps": [
            "Open today's log file (path in this card) and look at the last error.",
            "If cron didn't fire, check root's crontab.",
            "Run it again now with Run now -- it's safe to repeat.",
        ],
        "command": "sudo crontab -l | grep ei_pipeline; ls -t /opt/throughline/app/emotional_intelligence/logs | head -3",
        "fixed_when": "Next run reports success.",
        "fixes": [],
    },
    "job:nudge_cycle": {
        "cause": "The hourly nudge cycle didn't run or failed -- usually an LLM or DB error in one cycle.",
        "steps": ["Check the log for nudge_engine errors.", "Trigger one cycle with Run now to see if it passes."],
        "command": "journalctl -u throughline -g 'nudge_engine' --since '-3h' --no-pager",
        "fixed_when": "The next cycle reports success.",
        "fixes": [],
    },
    "job:*subprocess": {
        "cause": "This scheduled script didn't run on time or exited with an error.",
        "steps": ["Open its log file (path in this card).", "Fix the script or its inputs, then Run now."],
        "command": "ls -t <log dir> | head -3",
        "fixed_when": "The next run reports success.",
        "fixes": [],
    },
    "job:*": {
        "cause": "This job stopped reporting or its last run failed.",
        "steps": ["Check the log command on this card.", "Restart the app if an in-app loop has died."],
        "command": LOGS,
        "fixed_when": "The next run reports success.",
        "fixes": [],
    },
    # ---------------------------------------------------------------- tasks
    "task:chat_resolver": {
        "cause": "Groq calls for follow-up resolution are timing out or erroring. Chat still works (it falls back), "
                 "but follow-ups are understood less well and replies are slower.",
        "steps": ["Check whether errors are 'timeout' (Groq slow) or auth/429 (key or rate limit).",
                  "Check GROQ_API_KEY and Groq status."],
        "command": "journalctl -u throughline -g 'chat-resolver' --since '-1h' --no-pager",
        "fixed_when": "Failure rate under 20% in the next hour.",
        "fixes": [],
    },
    "task:*": {
        "cause": "This background step is failing for a share of requests -- usually an LLM reply that wasn't valid, "
                 "a timeout, or a DB error. The last error text says which.",
        "steps": ["Read the last error on this row.", "Check the log for the full trace."],
        "command": "journalctl -u throughline -g 'chat-resolver|chat-summary|run_background_analysis|titling|trigger_reminder_extraction' --since '-1h' --no-pager",
        "fixed_when": "Failure rate falls back under 20% (the trend chart shows the last 24h by hour).",
        "fixes": [],
    },
    # ---------------------------------------------------------------- delivery
    "email:failed": {
        "cause": "Emails a user approved failed at the SMTP step (mail server rejected or unreachable) or an attachment "
                 "couldn't be loaded. They're still waiting as drafts.",
        "steps": ["Read the last error on the Failed sends tile.",
                  "If it was a mail-server outage that's over, retry them all.",
                  "If it's an attachment error, the user has to re-attach -- retrying won't help."],
        "command": "journalctl -u throughline -g 'smtp|send_email' --since '-6h' --no-pager",
        "fixed_when": "Failed sends drops to 0.",
        "fixes": ["email_retry_failed"],
    },
    "email:sending": {
        "cause": "An email has been 'sending' for a long time -- the app restarted or crashed mid-send. "
                 "It may or may not have gone out.",
        "steps": ["Release it back to the user's drafts (marked 'may already have gone out').",
                  "The user (or you) should check the Sent folder before sending it again."],
        "command": "journalctl -u throughline -g 'send_email' --since '-6h' --no-pager",
        "fixed_when": "Mid-send drops to 0.",
        "fixes": ["email_unstick_sending"],
    },
    "email:unverified": {
        "cause": "The mail server accepted these emails but they weren't found in the Sent folder -- usually the IMAP "
                 "check couldn't reach the mailbox. Delivery itself is probably fine.",
        "steps": ["Check the IMAP settings (Sent-folder check) and the mailbox.", "Nothing to resend."],
        "command": "journalctl -u throughline -g 'sent_box|imap' --since '-6h' --no-pager",
        "fixed_when": "New sends show as confirmed.",
        "fixes": [],
    },
    "push:errors": {
        "cause": "Firebase rejected some pushes -- a credentials problem, quota, or malformed payload. Pushes can't be "
                 "resent from here (only their outcome is stored).",
        "steps": ["Check the log for the Firebase error.", "Check the Firebase service-account credentials."],
        "command": "journalctl -u throughline -g '\\[fcm\\]' --since '-6h' --no-pager",
        "fixed_when": "Push errors stop growing.",
        "fixes": [],
    },
    "ei:*": {
        "cause": "EI extraction runs are failing -- usually an LLM error or invalid JSON from the model.",
        "steps": ["Check the error in emotional_intelligence.analysis_runs for this run type."],
        "command": "journalctl -u throughline -g 'extraction' --since '-6h' --no-pager",
        "fixed_when": "No new errors in the next hour.",
        "fixes": [],
    },
    # ---------------------------------------------------------------- compute
    "compute:disk": {
        "cause": "The disk is filling up -- usually logs, old backups from deploys, or database growth.",
        "steps": ["Find what's large.", "Delete old /opt/throughline/backup-* folders and vacuum the journal.",
                  "If the database is the cause, see the largest tables below."],
        "command": "sudo du -sh /opt/throughline/backup-* /var/log/journal 2>/dev/null; sudo journalctl --vacuum-time=7d",
        "fixed_when": "Disk use under 80%.",
        "fixes": [],
    },
    "compute:memory": {
        "cause": "Memory is nearly full -- the app, Postgres and Redis share one VM.",
        "steps": ["See which process is largest.", "A restart of the app frees its memory; Redis memory shows on the Redis card."],
        "command": "ps aux --sort=-rss | head -8; free -m",
        "fixed_when": "Memory use under 85%.",
        "fixes": [],
    },
    "compute:cpu": {
        "cause": "Load is above the core count -- a busy batch (EI pipeline), a runaway process, or heavy traffic.",
        "steps": ["See which process is using CPU.", "If it's a batch job, it settles when the job ends."],
        "command": "top -b -n 1 | head -15",
        "fixed_when": "5-min load back under the core count.",
        "fixes": [],
    },
}


def all_runbooks() -> dict:
    return RUNBOOKS
