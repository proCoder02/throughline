"""RQ worker for the `calls` queue (see app.py: call_queue,
process_own_call_recording_job). Run this alongside app.py whenever the
backend is up -- call recordings are only ever processed by this process
(or, if Redis/RQ is unreachable, inline as a fallback in the request
itself; see _upload_own_call_recording).

Windows has no os.fork(), so this uses SimpleWorker (runs jobs sequentially
in this same process, no per-job subprocess) instead of RQ's default
Worker. Fully sufficient at this app's call volume; scale by running more
`python worker.py` processes if it ever isn't, not by switching worker
classes.

Usage:
    python worker.py
"""

import os

os.environ["SPEECH2TEXT_ROLE"] = "worker"

from rq.worker import SimpleWorker

from app import call_queue, rq_redis_client

if __name__ == "__main__":
    print("[worker] listening on queue 'calls'...")
    SimpleWorker([call_queue], connection=rq_redis_client).work()
