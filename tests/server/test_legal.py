"""Server checks for legal/: pages, consents, sign-up agreement, and
account deletion (everything gone for the deleted user, nothing touched for
anyone else, files + cache cleaned, wrong-password throttling)."""
import json, os, sys, uuid
sys.path.insert(0, os.getcwd())
os.environ["PYTHONIOENCODING"] = "utf-8"
import app  # noqa: E402
import legal, storage
import psycopg2.extras

results = []
def check(n, c, extra=""):
    results.append(bool(c)); print(("PASS " if c else "FAIL ") + n + (f"  -- {extra}" if extra and not c else ""))

tag = uuid.uuid4().hex[:6]
conn = app.get_raw_connection()
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
def q(sql, args=()):
    cur.execute(sql, args)
    out = cur.fetchall() if cur.description else None
    conn.commit()
    return out

def client_for(user=None, pw="secret123", agree=None):
    c = app.app.test_client()
    if user:
        body = {"username": user, "email": f"{user}@example.test", "password": pw}
        if agree is not None:
            body["accepted_terms"] = agree
        r = c.post("/register", json=body)
        assert r.status_code == 200, r.get_json()
        return c, r.get_json()["id"]
    return c, None

# ---------------------------------------------------------------- pages
anon, _ = client_for()
for path, marker in [("/privacy", "Privacy Policy"), ("/terms", "Terms of Service"), ("/account/delete", "Delete your Throughline account")]:
    r = anon.get(path)
    body = r.get_data(as_text=True)
    check(f"{path} is public and renders", r.status_code == 200 and marker in body, r.status_code)
check("unset company details show a visible 'to be added' marker", "to be added" in anon.get("/privacy").get_data(as_text=True))
os.environ["LEGAL_ENTITY_NAME"] = "Example Tech Pvt Ltd"
check("company details come from .env when set", "Example Tech Pvt Ltd" in anon.get("/terms").get_data(as_text=True))
del os.environ["LEGAL_ENTITY_NAME"]
priv = anon.get("/privacy").get_data(as_text=True)
check("policy names every processor", all(p in priv for p in ["Deepgram", "Groq", "Ollama", "Cloudflare", "LiveKit", "Firebase", "Hostinger", "Oracle", "Overpass"]))
check("policy says how calls are recorded (web = whole call)", "records the whole call" in priv)
check("SPA routes unaffected", anon.get("/app").status_code == 200)

# ---------------------------------------------------------------- consents
check("consents need sign-in", anon.get("/consents").status_code == 401)
c1, u1 = client_for(f"lg{tag}a", agree=True)
s = c1.get("/consents").get_json()
check("sign-up with the box ticked records Terms", s["terms"]["needed"] is False and s["terms"]["accepted_version"] == legal.TERMS_VERSION, s)
check("recording notice still needed for a new user", s["recording_notice"]["needed"] is True)
c0, u0 = client_for(f"lg{tag}n")  # old app: no field sent
check("sign-up without the field still works and Terms stays needed", c0.get("/consents").get_json()["terms"]["needed"] is True)
r = c1.post("/consents", json={"kind": "recording_notice"}).get_json()
check("accepting the recording notice", r["ok"] and r["recording_notice"]["needed"] is False)
check("bad consent kind refused", c1.post("/consents", json={"kind": "everything"}).status_code == 400)
legal.CONSENT_VERSIONS["recording_notice"] = legal.RECORDING_NOTICE_VERSION + 1
check("a new notice version asks again", c1.get("/consents").get_json()["recording_notice"]["needed"] is True)
legal.CONSENT_VERSIONS["recording_notice"] = legal.RECORDING_NOTICE_VERSION

# ---------------------------------------------------------------- deletion: build a user with data everywhere
victim, vid = client_for(f"lg{tag}v", agree=True)
other, oid = client_for(f"lg{tag}o", agree=True)
q("INSERT INTO conversations (user_id) VALUES (%s), (%s)", (vid, oid))
q("INSERT INTO tasks (user_id, description) VALUES (%s, 'v task'), (%s, 'o task')", (vid, oid))
q("INSERT INTO chat_messages (user_id, role, content) VALUES (%s, 'user', 'hi')", (vid,))
q("INSERT INTO push_tokens (user_id, token, platform) VALUES (%s, %s, 'android')", (vid, f"tok-{tag}"))
q("INSERT INTO friendships (user_id, friend_id) VALUES (%s, %s), (%s, %s)", (vid, oid, oid, vid))
q("INSERT INTO direct_messages (sender_id, recipient_id, content) VALUES (%s, %s, 'a'), (%s, %s, 'b')", (vid, oid, oid, vid))
q("INSERT INTO email_drafts (user_id, to_email, subject, body) VALUES (%s, 'x@example.test', 's', 'b')", (vid,))
q("INSERT INTO speaker_profiles (user_id, name) VALUES (%s, 'Ravi')", (vid,))
sub_own = q("INSERT INTO emotional_intelligence.subjects (canonical_name) VALUES (%s) RETURNING id", (f"own-{tag}",))[0]["id"]
sub_shared = q("INSERT INTO emotional_intelligence.subjects (canonical_name) VALUES (%s) RETURNING id", (f"shared-{tag}",))[0]["id"]
third, tid = client_for(f"lg{tag}t", agree=True)
q("INSERT INTO emotional_intelligence.app_user_subject_links (user_id, subject_id) VALUES (%s,%s),(%s,%s),(%s,%s)", (vid, sub_own, oid, sub_shared, tid, sub_shared))
q("INSERT INTO emotional_intelligence.facts (subject_id, subject, predicate, object) VALUES (%s,'me','likes','tea')", (sub_own,))
q("INSERT INTO emotional_intelligence.weekly_digests (user_id, content) VALUES (%s, '{}')", (vid,))
q("INSERT INTO nudges.user_settings (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (vid,))
q("INSERT INTO nudges.sent_log (user_id, nudge_type, reference_id) VALUES (%s, 't', 'r')", (vid,))
q("INSERT INTO ops.fcm_sends (user_id, status) VALUES (%s, 'sent')", (vid,))
victim.post("/consents", json={"kind": "recording_notice"})
app.redis_client.set(f"user:{vid}:global_chat", "x"); app.redis_client.set(f"user:{vid}:conversations", "x")
app.redis_client.rpush(f"pending_events:{vid}", "e"); app.redis_client.set(f"user:{oid}:global_chat", "keep")

# fake file storage
deleted_prefixes = []
class FakeS3:
    def list_objects_v2(self, **kw):
        return {"Contents": [{"Key": kw["Prefix"] + "a.jpg"}], "IsTruncated": False}
    def delete_objects(self, Bucket, Delete):
        deleted_prefixes.extend(o["Key"] for o in Delete["Objects"])
storage._client = lambda: FakeS3()
storage.is_enabled = lambda: True
os.environ.setdefault("R2_BUCKET_NAME", "test")

sent = []
# swap the email sender the blueprint captured: re-register isn't possible, so patch the closure target via legal module hook
import threading
real_thread = threading.Thread

# ---------------------------------------------------------------- deletion: guards
check("missing DELETE confirmation refused", victim.post("/account/delete", json={"password": "secret123"}).status_code == 400)
check("wrong password refused", victim.post("/account/delete", json={"password": "nope", "confirm": "DELETE"}).status_code == 403)
check("nothing deleted after refusals", q("SELECT 1 FROM users WHERE id=%s", (vid,)) != [])
# a typed username decides the account, not the signed-in session
r = victim.post("/account/delete", json={"username": f"lg{tag}o", "password": "secret123x", "confirm": "DELETE"})
check("typed username + wrong password for it: refused, signed-in user untouched", r.status_code == 403 and q("SELECT 1 FROM users WHERE id=%s", (vid,)) != [])

# ---------------------------------------------------------------- deletion: do it
r = victim.post("/account/delete", json={"password": "secret123", "confirm": "DELETE"})
check("delete succeeds", r.status_code == 200 and r.get_json()["ok"], r.get_json())

# every table that references users, plus the explicit ones, has nothing left for the victim
cur.execute("""SELECT tc.table_schema||'.'||tc.table_name AS t, kcu.column_name AS c
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu ON tc.constraint_name=kcu.constraint_name AND tc.table_schema=kcu.table_schema
JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name=tc.constraint_name AND ccu.constraint_schema=tc.table_schema
WHERE tc.constraint_type='FOREIGN KEY' AND ccu.table_name='users' AND ccu.table_schema='public'""")
refs = [(r["t"], r["c"]) for r in cur.fetchall()] + [
    ("emotional_intelligence.app_user_subject_links", "user_id"), ("emotional_intelligence.weekly_digests", "user_id"),
    ("nudges.user_settings", "user_id"), ("nudges.sent_log", "user_id"), ("ops.fcm_sends", "user_id")]
left = []
for t, c in refs:
    cur.execute(f"SELECT count(*) AS n FROM {t} WHERE {c} = %s", (vid,))
    n = cur.fetchone()["n"]
    if n:
        left.append((t, c, n))
conn.commit()
check("no row anywhere still points at the deleted user", not left and q("SELECT 1 FROM users WHERE id=%s", (vid,)) == [], left)
check("user's own EI subject (and its facts) gone", q("SELECT 1 FROM emotional_intelligence.subjects WHERE id=%s", (sub_own,)) == []
      and q("SELECT 1 FROM emotional_intelligence.facts WHERE subject_id=%s", (sub_own,)) == [])
check("other users' EI subject untouched", q("SELECT 1 FROM emotional_intelligence.subjects WHERE id=%s", (sub_shared,)) != [])
check("other user's account, conversation and task untouched",
      q("SELECT 1 FROM users WHERE id=%s", (oid,)) != [] and q("SELECT 1 FROM tasks WHERE user_id=%s", (oid,)) != []
      and q("SELECT 1 FROM conversations WHERE user_id=%s", (oid,)) != [])
check("messages between them are gone for both", q("SELECT 1 FROM direct_messages WHERE sender_id=%s OR recipient_id=%s", (vid, vid)) == [])
check("user's files deleted from every upload folder", sorted({k.split('/')[0] for k in deleted_prefixes}) == ["chat-media", "profile-pictures"]
      and len(deleted_prefixes) == 4 and all(f"/{vid}/" in k for k in deleted_prefixes), deleted_prefixes)
check("user's cache keys deleted", not app.redis_client.exists(f"user:{vid}:global_chat", f"user:{vid}:conversations", f"pending_events:{vid}"))
check("other user's cache untouched", app.redis_client.get(f"user:{oid}:global_chat") in ("keep", b"keep"))
check("signed out after deletion", victim.get("/consents").status_code == 401)
check("can't log in any more", anon.post("/login", json={"username": f"lg{tag}v", "password": "secret123"}).status_code == 401)

# ---------------------------------------------------------------- logged-out page path + throttle
pub, _ = client_for()
for _ in range(5):
    pub.post("/account/delete", json={"username": f"lg{tag}o", "password": "bad", "confirm": "DELETE"})
r = pub.post("/account/delete", json={"username": f"lg{tag}o", "password": "secret123", "confirm": "DELETE"})
check("after 5 wrong passwords: refused for 15 min, even with the right one", r.status_code == 429 and q("SELECT 1 FROM users WHERE id=%s", (oid,)) != [])
legal._failed_deletes.clear()
r = pub.post("/account/delete", json={"username": f"lg{tag}o", "password": "secret123", "confirm": "DELETE"})
check("delete from the public page by username + password", r.status_code == 200 and q("SELECT 1 FROM users WHERE id=%s", (oid,)) == [])
check("EI subject still linked to another user is kept",
      q("SELECT 1 FROM emotional_intelligence.subjects WHERE id=%s", (sub_shared,)) != [])

# cleanup
for uid in (u1, u0):
    legal_c = app.app.test_client()
q("DELETE FROM users WHERE id = ANY(%s)", ([u1, u0, tid],))
q("DELETE FROM emotional_intelligence.app_user_subject_links WHERE user_id=%s", (tid,))
q("DELETE FROM emotional_intelligence.subjects WHERE id=%s", (sub_shared,))
app.redis_client.delete(f"user:{oid}:global_chat")
app.release_raw_connection(conn)
print(f"\n{sum(results)}/{len(results)} passed")
