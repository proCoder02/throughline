"""Speech-to-text: the upload's real format comes from its bytes (iOS sends
the chat mic's .m4a as application/octet-stream), and a file Groq refuses
(4xx) is retried with Deepgram instead of failing the user's recording.
Mocked HTTP; needs no network or database."""
import os, sys
sys.path.insert(0, os.getcwd())
os.environ.update(CORPORATE_JOBS="false", COSTLENS_SDK="false", PYTHONIOENCODING="utf-8")
import requests
import app

results = []
def check(n, c, x=""):
    results.append(bool(c)); print(("PASS " if c else "FAIL ") + n + (f"  -- {x}" if x != "" and not c else ""))

M4A = b"\x00\x00\x00\x20ftypM4A \x00\x00\x02\x00" + b"\x00" * 64
# --- format from bytes
check("m4a from ftyp", app._sniff_audio_ext(M4A) == "m4a")
check("webm from EBML", app._sniff_audio_ext(b"\x1a\x45\xdf\xa3" + b"\x00" * 16) == "webm")
check("wav from RIFF/WAVE", app._sniff_audio_ext(b"RIFF\x00\x00\x00\x00WAVE") == "wav")
check("ogg", app._sniff_audio_ext(b"OggS" + b"\x00" * 12) == "ogg")
check("mp3 from ID3", app._sniff_audio_ext(b"ID3\x04" + b"\x00" * 12) == "mp3")
check("mp3 from frame sync", app._sniff_audio_ext(b"\xff\xfb\x90\x00" + b"\x00" * 12) == "mp3")
check("ADTS AAC is not mistaken for mp3", app._sniff_audio_ext(b"\xff\xf1\x50\x80" + b"\x00" * 12) is None)
check("unknown bytes give None", app._sniff_audio_ext(b"hello world!") is None)


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, str(body)
    def json(self):
        return self._body
    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Client Error", response=self)


sent = []
real_post = requests.post
def fake(groq_status):
    def post(url, **kw):
        if "groq.com" in url:
            sent.append(("groq", kw["files"]["file"][0]))
            return Resp(groq_status, {"text": "hello from groq"} if groq_status == 200 else {"error": {"message": "could not process file"}})
        sent.append(("deepgram", url))
        return Resp(200, {"results": {"utterances": [{"speaker": 0, "transcript": "hello from deepgram", "start": 0, "end": 1}],
                                      "channels": [{"alternatives": [{"transcript": "hello from deepgram"}]}]}})
    return post

os.environ.update(USE_GROQ_STT="true", GROQ_API_KEY="g", DEEPGRAM_API_KEY="d")
try:
    # an iOS upload: .m4a bytes labelled octet-stream
    requests.post = fake(200); sent.clear()
    text, _ = app.transcribe_audio_bytes(M4A, "application/octet-stream")
    check("octet-stream .m4a is sent to Groq as audio.m4a", sent and sent[0] == ("groq", "audio.m4a"), sent)
    check("Groq's text comes back", "hello from groq" in text, text)

    requests.post = fake(400); sent.clear()
    text, _ = app.transcribe_audio_bytes(M4A, "application/octet-stream")
    check("Groq 400 falls back to Deepgram", [s[0] for s in sent] == ["groq", "deepgram"], sent)
    check("Deepgram's text comes back", "hello from deepgram" in text, text)

    for status in (401, 429, 500):
        requests.post = fake(status); sent.clear()
        try:
            app.transcribe_audio_bytes(M4A, "audio/mp4")
            check(f"Groq {status} is not retried on Deepgram", False, "no error raised")
        except requests.HTTPError:
            check(f"Groq {status} is not retried on Deepgram", [s[0] for s in sent] == ["groq"], sent)

    os.environ.pop("DEEPGRAM_API_KEY")
    requests.post = fake(400); sent.clear()
    try:
        app.transcribe_audio_bytes(M4A, "audio/mp4")
        check("no Deepgram key: Groq's 400 still surfaces", False, "no error raised")
    except requests.HTTPError:
        check("no Deepgram key: Groq's 400 still surfaces", [s[0] for s in sent] == ["groq"], sent)
finally:
    requests.post = real_post

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
