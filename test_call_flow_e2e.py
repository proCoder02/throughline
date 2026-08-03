"""End-to-end simulation: two users call each other, end the call, and both
verify a chat-ready conversation was created from it. Exercises the full
pipeline for real against a live backend: notification arrival -> call ->
join -> leave -> RQ-queued Deepgram transcription -> conversation creation
-> call_conversation_ready push -> chat.

Prerequisites (must already be running):
    - Postgres
    - Redis
    - python app.py     (in this directory)
    - python worker.py  (in this directory -- the `calls` RQ queue consumer)

Usage:
    python test_call_flow_e2e.py [--base-url http://127.0.0.1:5000]
"""

import argparse
import json
import os
import sys
import subprocess
import tempfile
import time
import uuid

import requests
import websocket  # websocket-client
from dotenv import load_dotenv

load_dotenv()

_results = []


def step(name, ok, detail=""):
    _results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    return ok


def fatal(name, detail=""):
    step(name, False, detail)
    print_summary()
    sys.exit(1)


def print_summary():
    passed = sum(1 for _, ok, _ in _results if ok)
    print(f"\n{passed}/{len(_results)} steps passed")


def synthesize_speech_wav(text, path):
    """Windows' built-in TTS (System.Speech) -- no new tooling required.
    Gives Deepgram real, recognizable speech to transcribe instead of
    silence, so the test proves the pipeline actually works end to end."""
    ps_script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SetOutputToWaveFile('{path}'); "
        f"$s.Speak('{text}'); "
        "$s.Dispose()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_script],
        check=True, capture_output=True, text=True,
    )


class ApiClient:
    def __init__(self, base_url):
        self.base_url = base_url
        self.token = None
        self.user_id = None
        self.username = None

    def _headers(self):
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def post(self, path, timeout=30, **kwargs):
        return requests.post(self.base_url + path, headers=self._headers(), timeout=timeout, **kwargs)

    def get(self, path, timeout=30, **kwargs):
        return requests.get(self.base_url + path, headers=self._headers(), timeout=timeout, **kwargs)

    def register(self, username, email, password):
        r = self.post("/register", json={"username": username, "email": email, "password": password})
        r.raise_for_status()
        data = r.json()
        self.token, self.user_id, self.username = data["token"], data["id"], data["username"]
        return data


def open_notify_socket(base_url, token):
    ws_url = base_url.replace("http://", "ws://").replace("https://", "wss://") + f"/ws/notify?token={token}"
    return websocket.create_connection(ws_url, timeout=15)


def recv_event(ws, expected_type, timeout=90):
    """Polls the socket for a specific event type, ignoring anything else
    that arrives first, up to `timeout` seconds total."""
    deadline = time.time() + timeout
    ws.settimeout(2)
    while time.time() < deadline:
        try:
            raw = ws.recv()
        except Exception:
            continue
        if not raw:
            continue
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        if event.get("type") == expected_type:
            return event
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    base_url = parser.parse_args().base_url.rstrip("/")

    ts = uuid.uuid4().hex[:8]
    caller, callee = ApiClient(base_url), ApiClient(base_url)

    try:
        caller.register(f"e2e_caller_{ts}", f"caller_{ts}@example.com", "testpass123")
        step("register caller", True, f"user_id={caller.user_id}")
    except Exception as e:
        fatal("register caller", str(e))

    try:
        callee_data = callee.register(f"e2e_callee_{ts}", f"callee_{ts}@example.com", "testpass123")
        step("register callee", True, f"user_id={callee.user_id}")
    except Exception as e:
        fatal("register callee", str(e))

    try:
        r = caller.post("/friends/add", json={"friend_code": callee_data["friend_code"]})
        r.raise_for_status()
        step("friend caller<->callee", True)
    except Exception as e:
        fatal("friend caller<->callee", str(e))

    sentence = "We agreed to meet on Friday to discuss the budget review."
    wav_path = os.path.join(tempfile.gettempdir(), f"e2e_call_test_{ts}.wav")
    try:
        synthesize_speech_wav(sentence, wav_path)
        step("synthesize speech audio", os.path.exists(wav_path), wav_path)
    except Exception as e:
        fatal("synthesize speech audio", str(e))

    try:
        ws_caller = open_notify_socket(base_url, caller.token)
        ws_callee = open_notify_socket(base_url, callee.token)
        step("open /ws/notify sockets", True)
    except Exception as e:
        fatal("open /ws/notify sockets", str(e))

    try:
        r = caller.post("/calls", json={"friend_ids": [callee.user_id]})
        r.raise_for_status()
        call_id = r.json()["call_id"]
        step("create call", True, f"call_id={call_id}")
    except Exception as e:
        fatal("create call", str(e))

    event = recv_event(ws_callee, "incoming_call", timeout=15)
    step(
        "incoming_call notification arrives",
        bool(event and event.get("call_id") == call_id),
        str(event),
    )

    try:
        r = callee.post(f"/calls/{call_id}/join")
        r.raise_for_status()
        step("callee joins call", True)
    except Exception as e:
        fatal("callee joins call", str(e))

    try:
        caller.post(f"/calls/{call_id}/leave").raise_for_status()
        r2 = callee.post(f"/calls/{call_id}/leave")
        r2.raise_for_status()
        call_ended = r2.json().get("call_ended")
        step("call ends when both leave", call_ended is True, f"call_ended={call_ended}")
    except Exception as e:
        fatal("both leave call", str(e))

    job_ids = {}
    for label, client in (("caller", caller), ("callee", callee)):
        try:
            with open(wav_path, "rb") as f:
                r = client.post(
                    f"/calls/{call_id}/recording",
                    data={"scope": "own"},
                    files={"audio": ("call.wav", f, "audio/wav")},
                    timeout=30,
                )
            r.raise_for_status()
            body = r.json()
            queued = body.get("status") == "queued" and "job_id" in body
            step(f"{label} upload recording queued (request didn't block)", queued, str(body))
            if queued:
                job_ids[label] = body["job_id"]
        except Exception as e:
            step(f"{label} upload recording queued", False, str(e))

    from rq.job import Job
    import redis as redis_lib

    redis_conn = redis_lib.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=True, protocol=2,
    )
    for label, job_id in job_ids.items():
        deadline = time.time() + 60
        final_status = None
        while time.time() < deadline:
            final_status = Job.fetch(job_id, connection=redis_conn).get_status()
            if final_status in ("finished", "failed"):
                break
            time.sleep(1)
        step(f"{label} RQ job finished", final_status == "finished", f"status={final_status}")

    conversation_ids = {}
    for label, ws in (("caller", ws_caller), ("callee", ws_callee)):
        event = recv_event(ws, "call_conversation_ready", timeout=60)
        ok = bool(event and event.get("call_id") == call_id and event.get("conversation_id"))
        step(f"{label} receives call_conversation_ready", ok, str(event))
        if ok:
            conversation_ids[label] = event["conversation_id"]

    for label, client in (("caller", caller), ("callee", callee)):
        conv_id = conversation_ids.get(label)
        if not conv_id:
            step(f"{label} conversation appears in list", False, "no conversation_id from notification")
            continue
        try:
            r = client.get("/conversations")
            r.raise_for_status()
            ids = [c["id"] for c in r.json()]
            step(f"{label} conversation appears in list", conv_id in ids, f"ids={ids}")

            r2 = client.get(f"/conversations/{conv_id}")
            r2.raise_for_status()
            transcript = (r2.json().get("raw_transcript") or "").lower()
            has_content = "friday" in transcript and "budget" in transcript
            step(f"{label} transcript contains synthesized speech", has_content, transcript[:200])
        except Exception as e:
            step(f"{label} conversation fetch", False, str(e))

    for label, client in (("caller", caller), ("callee", callee)):
        conv_id = conversation_ids.get(label)
        if not conv_id:
            continue
        try:
            r = client.post(
                "/chat",
                json={"prompt": "What day did we agree to meet?", "conversation_id": conv_id},
                timeout=60,
            )
            r.raise_for_status()
            reply = r.json().get("reply", "")
            step(f"{label} /chat responds", bool(reply), reply[:200])
            if "friday" not in reply.lower():
                print(f"    (soft warning: {label}'s reply didn't mention Friday -- LLM wording, not a pipeline failure)")

            r2 = client.get(f"/conversations/{conv_id}/chat")
            r2.raise_for_status()
            messages = r2.json()
            step(f"{label} chat message persisted", len(messages) >= 2, f"{len(messages)} messages")
        except Exception as e:
            step(f"{label} chat flow", False, str(e))

    for ws in (ws_caller, ws_callee):
        try:
            ws.close()
        except Exception:
            pass
    try:
        os.remove(wav_path)
    except OSError:
        pass

    print_summary()
    sys.exit(1 if any(not ok for _, ok, _ in _results) else 0)


if __name__ == "__main__":
    main()
