"""Work memory checks with a fake AI (no tokens, deterministic): chat, meetings ->
ledger, permission filter, commitment actions + history, decisions + versions,
common ground, ask, evidence check, overdue, risk radar, brief, chat extraction."""
import os, sys, uuid, time, re
sys.path.insert(0, os.getcwd())
os.environ.update(CORPORATE_ENABLED="true", CORPORATE_JOBS="false", PYTHONIOENCODING="utf-8")
import app  # noqa: E402
import psycopg2.extras
from corporate import ai, jobs

def fake_ask(call_llm, system, user, task=None):
    if "Extract from a work discussion" in system:
        if "Discussion:\n" in user and "go with 17" in user:
            return {"summary": "Agreed to migrate to PostgreSQL 17.",
                    "decisions": [{"title": "Target PostgreSQL 17", "text": "Migrate to PostgreSQL 17", "made_by": OWNER, "quote": "go with 17"}],
                    "commitments": [{"text": "Prepare migration scripts", "owner": DEV, "owed_to": OWNER, "due": "2030-01-15", "quote": "I'll prepare"}],
                    "blockers": [{"text": "API rate limit blocks the checkout feature", "quote": "rate limit"}]}
        if "chat-promise" in user:
            return {"decisions": [], "commitments": [{"text": "Write the runbook", "owner": DEV, "owed_to": None, "due": None, "quote": "chat-promise"}], "blockers": []}
        return {"summary": "", "decisions": [], "commitments": [], "blockers": []}
    if "fulfilled" in system:
        return {"done": "sent the migration scripts" in user, "quote": "sent the migration scripts"}
    if "blocker delay" in system:
        return {"ids": [int(i) for i in re.findall(r"\[(\d+)\]", user)], "why": "same checkout feature"}
    if "common ground" in system:
        return {"positions": [{"name": DEV, "position": "migrate now"}], "agreed": ["Migrate before release"], "open": ["Rollback timing"], "options": ["Test rollback in week 1"]}
    return None
ai._ask = fake_ask
ai.answer = lambda call_llm, q, sources, history=None: "It targets 17 " + (re.search(r"\[(D\d+)\]", sources).group(0) if re.search(r"\[(D\d+)\]", sources) else "")
pushed = []
fake_push = lambda uid, t, b, d=None: pushed.append((uid, t, b))

results = []
def check(n, c, extra=""):
    results.append(bool(c)); print(("PASS " if c else "FAIL ") + n + (f"  -- {extra}" if extra != "" and not c else ""))

tag = uuid.uuid4().hex[:6]
conn = app.get_raw_connection(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
def q(sql, a=()):
    cur.execute(sql, a); out = cur.fetchall() if cur.description else None; conn.commit(); return out
def user(n):
    c = app.app.test_client()
    r = c.post("/register", json={"username": f"w{tag}{n}", "email": f"w{tag}{n}@example.test", "password": "secret123"})
    return c, r.get_json()["id"], f"w{tag}{n}"
own, uo, OWNER = user("own"); dev, ud, DEV = user("dev"); sal, us_, SAL = user("sal"); out, ux, _ = user("out")

d = own.post("/corp/orgs", json={"name": f"W {tag}", "departments": ["Engineering", "Sales"]}).get_json()
org = d["org"]["id"]; eng = next(u["id"] for u in d["units"] if u["name"] == "Engineering"); sales = next(u["id"] for u in d["units"] if u["name"] == "Sales")
be = own.post(f"/corp/orgs/{org}/units", json={"name": "Backend", "kind": "team", "parent_id": eng}).get_json()["id"]
for uid in (ud, us_, ux):
    q("INSERT INTO corp.members (org_id, user_id) VALUES (%s,%s)", (org, uid))
for uid, unit in ((uo, be), (ud, be), (us_, sales)):
    own.put(f"/corp/orgs/{org}/units/{unit}/members/{uid}", json={"role": "member"})
B = f"/corp/orgs/{org}"

# chat
t = dev.get(B + "/threads").get_json()["threads"]
bt = next(x["id"] for x in t if x["unit_id"] == be)
check("team thread exists for each of my teams", any(x["unit_id"] == be and x["name"] == "Backend" for x in t))
check("outsider can't read the team thread", out.get(f"{B}/threads/{bt}/messages").status_code == 404)
check("Sales member can't post in Backend", sal.post(f"{B}/threads/{bt}/messages", json={"body": "hi"}).status_code == 404)
r = dev.post(f"{B}/threads/{bt}/messages", json={"body": "Starting the migration work"})
check("member posts in team thread", r.status_code == 201)
th = own.get(B + "/threads").get_json()["threads"]
check("teammate sees it unread", next(x for x in th if x["id"] == bt)["unread"] == 1)
dm = own.post(B + "/threads/direct", json={"user_id": ud}).get_json()["thread_id"]
check("direct thread is the same both ways", dev.post(B + "/threads/direct", json={"user_id": uo}).get_json()["thread_id"] == dm)
check("third person can't read a direct thread", sal.get(f"{B}/threads/{dm}/messages").status_code == 404)
check("can't DM someone outside the org", own.post(B + "/threads/direct", json={"user_id": 999999999}).status_code == 404)

# meeting -> ledger
r = own.post(B + "/meetings", json={"title": "DB migration", "unit_id": be, "text": f"{OWNER}: let's go with 17. {DEV}: I'll prepare the scripts."})
mid = r.get_json()["id"]
for _ in range(40):
    m = own.get(f"{B}/meetings/{mid}").get_json()
    if m["status"] != "processing": break
    time.sleep(0.25)
check("meeting processed", m["status"] == "ready" and m["summary"], m.get("error"))
check("decision, commitment and blocker extracted", len(m["decisions"]) == 1 and len(m["commitments"]) == 1 and len(m["blockers"]) == 1)
c = m["commitments"][0]
check("spoken names matched to members", c["owner_id"] == ud and c["owed_to_id"] == uo and c["status"] == "detected", c)
check("Sales member can't see the Backend meeting", sal.get(f"{B}/meetings/{mid}").status_code == 404)
check("not a member of Backend: can't add a meeting there", sal.post(B + "/meetings", json={"title": "x", "unit_id": be, "text": "x" * 30}).status_code == 403)
check("meeting list filtered", not any(x["id"] == mid for x in sal.get(B + "/meetings").get_json()["meetings"]))

# commitments
cid = c["id"]
check("Sales can't see or change it", sal.post(f"{B}/commitments/{cid}/confirm").status_code == 404)
check("outsider in org can't either", out.get(f"{B}/commitments/{cid}").status_code == 404)
r = dev.post(f"{B}/commitments/{cid}/confirm").get_json()
check("owner confirms -> open", r["commitment"]["status"] == "open")
check("drop needs a reason", dev.post(f"{B}/commitments/{cid}/drop", json={}).status_code == 400)
r = dev.post(f"{B}/commitments/{cid}/edit", json={"due_date": "2030-02-01"}).get_json()
check("edit keeps history", [e["kind"] for e in r["history"]] == ["created", "confirm", "edit"], r["history"])
check("mine / owed scopes", any(x["id"] == cid for x in dev.get(B + "/commitments?scope=mine").get_json()["commitments"])
      and any(x["id"] == cid for x in own.get(B + "/commitments?scope=owed").get_json()["commitments"]))

# decisions
dl = own.get(B + "/decisions").get_json()["decisions"]
did = dl[0]["id"]
r = own.post(f"{B}/decisions/{did}/versions", json={"text": "Migrate to PostgreSQL 17 after staging sign-off"}).get_json()
check("new decision version, old one kept", r["decision"]["version"] == 2 and len(r["versions"]) == 2 and r["versions"][1]["text"] == "Migrate to PostgreSQL 17")
check("Sales can't see Backend decisions", not sal.get(B + "/decisions").get_json()["decisions"])
check("Sales can't add a version", sal.post(f"{B}/decisions/{did}/versions", json={"text": "x"}).status_code == 404)

# common ground
for i, who in enumerate((dev, own, dev)):
    who.post(f"{B}/threads/{bt}/messages", json={"body": f"position {i}: we should migrate before the release, rollback first?"})
r = dev.post(f"{B}/threads/{bt}/common-ground").get_json()
check("common ground from the thread", r["result"]["agreed"] == ["Migrate before release"] and r["used_notes"] is False)
msgs = own.get(f"{B}/threads/{bt}/messages").get_json()["messages"]
check("posted for everyone in the thread", msgs[-1]["kind"] == "common_ground")
gid = r["id"]
check("log an agreed point as a decision", own.post(f"{B}/common-ground/{gid}/decision", json={"text": "Migrate before release"}).status_code == 201)
own.put(B + "/work-note", json={"note": "On leave next week", "sharing": True})
r = dev.post(f"{B}/threads/{bt}/common-ground").get_json()
check("work notes not used unless everyone shares", r["used_notes"] is False)
dev.put(B + "/work-note", json={"note": "Prefer async", "sharing": True})
r = dev.post(f"{B}/threads/{bt}/common-ground").get_json()
check("work notes used once everyone shares", r["used_notes"] is True)

# ask
r = dev.post(B + "/ask", json={"question": "what did we decide about the database"}).get_json()
check("ask answers with a decision source", r["sources"] and r["sources"][0]["type"] == "decision", r)
r = sal.post(B + "/ask", json={"question": "what did we decide about the database"}).get_json()
check("ask never uses teams you're not in", not any(s["type"] in ("meeting", "decision") for s in r["sources"]), r)
check("ask about a team you're not in refused", sal.post(B + "/ask", json={"question": "x", "unit_id": be}).status_code == 403)

# radar
sc = sal.post(B + "/commitments", json={"text": "Ship the checkout feature to the customer", "unit_id": sales, "due_date": "2030-01-20"}).get_json()["id"]
q("UPDATE corp.blockers SET scanned_at = NULL WHERE org_id = %s", (org,))
n = jobs.radar(cur, None, fake_push); conn.commit()
check("radar links Backend blocker to Sales commitment", n == 1)
bs = sal.get(B + "/brief").get_json()["risks"]
check("Sales sees own item, only the other team's name", bs and bs[0]["commitment"] and bs[0]["blocker"] is None and bs[0]["blocker_unit"] == "Backend", bs)
bd = dev.get(B + "/brief").get_json()["risks"]
check("Backend sees its blocker, not Sales' text", bd and bd[0]["blocker"] and bd[0]["commitment"] is None, bd)
check("radar pushed both sides", any(p[0] == us_ for p in pushed))

# evidence + overdue + chat extraction
dev.post(f"{B}/threads/{bt}/messages", json={"body": "I sent the migration scripts to the team"})
q("UPDATE corp.commitments SET checked_at = now() - interval '1 hour' WHERE id = %s", (cid,))
q("UPDATE corp.commitments SET checked_at = now() WHERE org_id = %s AND id <> %s", (org, cid))
jobs.check_evidence(cur, None, fake_push); conn.commit()
r = dev.get(f"{B}/commitments/{cid}").get_json()
check("evidence closes the commitment", r["commitment"]["status"] == "done" and "sent the migration" in r["commitment"]["evidence"], r["commitment"])
od = dev.post(B + "/commitments", json={"text": "Old promise", "unit_id": be, "due_date": "2020-01-01"}).get_json()["id"]
jobs.mark_overdue(cur); conn.commit()
check("past due -> overdue", dev.get(f"{B}/commitments/{od}").get_json()["commitment"]["status"] == "overdue")
for i in range(5):
    dev.post(f"{B}/threads/{bt}/messages", json={"body": f"note {i} chat-promise"})
jobs.extract_chats(cur, None); conn.commit()
check("commitments found in chat", any(x["text"] == "Write the runbook" for x in dev.get(B + "/commitments?scope=mine").get_json()["commitments"]))
check("brief lists what I owe", any(x["id"] == od for x in dev.get(B + "/brief").get_json()["owe"]))
q("DELETE FROM corp.job_state WHERE key = 'brief_day'")
check("meeting delete: only creator or manager", sal.delete(f"{B}/meetings/{mid}").status_code == 404 and own.delete(f"{B}/meetings/{mid}").status_code == 200)

q("DELETE FROM corp.orgs WHERE id = %s", (org,)); q("DELETE FROM users WHERE username LIKE %s", (f"w{tag}%",))
app.release_raw_connection(conn)
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
