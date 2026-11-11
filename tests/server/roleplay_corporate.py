"""ROLE-PLAY: a day at NodexData. Seven people, each doing normal things and
the awkward things real users do. A counting fake AI (no tokens) lets us also
check that the AI is NOT called when it shouldn't be.

Cast
  Amit   owner/admin of NodexData          Khushi  Database Team lead
  Ravi A, Ravi B  two developers with the same first name (name clash)
  Priya  Sales head                         Neha    HR (confidential team)
  Om     works at another company (second org)"""
import os, sys, uuid, re
sys.path.insert(0, os.getcwd())
os.environ.update(CORPORATE_ENABLED="true", CORPORATE_JOBS="false", PYTHONIOENCODING="utf-8")
import app  # noqa: E402
app._smtp_send = lambda *a, **k: None  # never send real email from tests
import psycopg2.extras
from datetime import date, timedelta
from corporate import ai, jobs, insights

CALLS = {"n": 0}
CTX = {}
def fake_ask(call_llm, system, user, task=None):
    CALLS["n"] += 1
    if "Extract from a work discussion" in system:
        if "GARBAGE" in user: return None
        out = {"summary": "s", "decisions": [], "commitments": [], "blockers": []}
        if "MIGRATION" in user:
            out["decisions"].append({"title": "Target PostgreSQL 17", "text": "Use PostgreSQL 17", "quote": "go with 17"})
            out["commitments"].append({"text": "Prepare migration scripts", "owner": "Khushi", "owed_to": "Amit", "due": "2030-01-10", "quote": "I'll do it"})
            out["commitments"].append({"text": "Review the scripts", "owner": "Ravi", "owed_to": "Khushi", "due": "next friday-ish"})
        if "CROSSUNIT" in user:
            out["decisions"].append({"title": "Hijack", "text": "Overwrite other team", "updates_id": CTX.get("foreign_decision")})
        if "BLOCKER" in user:
            out["blockers"].append({"text": "Payment gateway outage blocks invoice export"})
        if "SALESPROMISE" in user:
            out["commitments"].append({"text": "Deliver invoice export to Acme customer", "owner": "Priya", "due": "2030-02-01"})
        return out
    if "fulfilled" in system:
        head = user.split(chr(10) * 2)[0]  # "Commitment (owner): text" -- judge each commitment on its own
        return {"done": "DELIVERED" in user and "Prepare migration scripts" in head, "quote": "DELIVERED the scripts"}
    if "blocker delay" in system:
        return {"ids": [int(i) for i in re.findall(r"\[(\d+)\]", user)], "why": "same invoice export"}
    if "common ground" in system:
        return {"positions": [], "agreed": ["Ship after staging"], "open": [], "options": []}
    return None
ai._ask = fake_ask
def fake_answer(call_llm, q, sources):
    CALLS["n"] += 1
    m = re.search(r"\[([MDCT]\d+)\]", sources)
    return f"answer [{m.group(1)}]" if m else "answer"
ai.answer = fake_answer
pushed = []
push = lambda uid, t, b, d=None: pushed.append((uid, t, b))

results, findings = [], []
def check(n, c, x=""):
    results.append(bool(c)); print(("PASS " if c else "FAIL ") + n + (f"  -- {x}" if x != "" and not c else ""), flush=True)
def scene(t): print(f"\n=== {t}", flush=True)

tag = uuid.uuid4().hex[:5]
conn = app.get_raw_connection(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
def q(sql, a=()):
    cur.execute(sql, a); out = cur.fetchall() if cur.description else None; conn.commit(); return out
def person(first, suffix=""):
    c = app.app.test_client(); u = f"{first}{suffix}{tag}"
    r = c.post("/register", json={"username": u, "email": f"{u}@example.test", "password": "secret123"})
    return c, r.get_json()["id"], u
amit, ua, A = person("amit"); khushi, uk, K = person("khushi"); ravia, ura, _ = person("ravi", "a"); ravib, urb, _ = person("ravi", "b")
priya, up, P = person("priya"); neha, un, N = person("neha"); om, uom, OM = person("om")

scene("1. Amit sets up NodexData")
d = amit.post("/corp/orgs", json={"name": f"NodexData {tag}", "departments": ["Technology", "Sales", "HR"]}).get_json()
org = d["org"]["id"]; U = {u["name"]: u["id"] for u in d["units"]}
dbt = amit.post(f"/corp/orgs/{org}/units", json={"name": "Database Team", "kind": "team", "parent_id": U["Technology"]}).get_json()["id"]
B = f"/corp/orgs/{org}"
def invite(email, **kw):
    r = amit.post(B + "/invites", json={"emails": email, **kw}).get_json()
    return r["created"][0]["link"].rsplit("/", 1)[1] if r.get("created") else None
tok = invite(f"  {K.upper()}@EXAMPLE.TEST ", unit_id=dbt, unit_role="manager")
check("invite with odd spacing/case still matches Khushi", khushi.post(f"/corp/invites/{tok}/accept").status_code == 200)
for c, u, unit in ((ravia, f"ravia{tag}", dbt), (ravib, f"ravib{tag}", dbt), (priya, P, U["Sales"]), (neha, N, U["HR"])):
    t = invite(f"{u}@example.test", unit_id=unit, unit_role="manager" if unit in (U["Sales"], U["HR"]) else "member")
    c.post(f"/corp/invites/{t}/accept")
check("everyone joined", len(amit.get(B + "/members").get_json()["members"]) == 6)
tok = invite(f"{OM}@example.test")
amit.post(B + f"/invites", json={"emails": f"{OM}@example.test"})  # re-invite replaces the first link
check("an old link stops working after a re-invite", om.post(f"/corp/invites/{tok}/accept").status_code == 410)
om_org = om.post("/corp/orgs", json={"name": f"OtherCo {tag}"}).get_json()["org"]["id"]

scene("2. Chat: normal use and abuse")
dm = amit.post(B + "/threads/direct", json={"user_id": uk}).get_json()["thread_id"]
check("can't DM yourself", amit.post(B + "/threads/direct", json={"user_id": ua}).status_code == 400)
check("can't DM someone from another company", amit.post(B + "/threads/direct", json={"user_id": uom}).status_code == 404)
check("whitespace-only message refused", amit.post(f"{B}/threads/{dm}/messages", json={"body": "   "}).status_code == 400)
r = amit.post(f"{B}/threads/{dm}/messages", json={"body": "<script>alert(1)</script> hi"}).get_json()
check("HTML is stored as plain text (the page escapes it)", r["messages"][-1]["body"].startswith("<script>"))
r = amit.post(f"{B}/threads/{dm}/messages", json={"body": "x" * 9000}).get_json()
check("very long message is cut to 4000 characters", len(r["messages"][-1]["body"]) == 4000)
check("Om (other company) can't read it via his own org's URL", om.get(f"/corp/orgs/{om_org}/threads/{dm}/messages").status_code == 404)
check("Priya can't read Amit and Khushi's DM", priya.get(f"{B}/threads/{dm}/messages").status_code == 404)
u = khushi.get("/corp/unread").get_json()
check("Khushi has 2 unread; Amit's own messages don't count for him", u["total"] == 2 and amit.get("/corp/unread").get_json()["total"] == 0)
check("latest unread names the sender", u["latest"]["sender"] == A)
last = khushi.get(f"{B}/threads/{dm}/messages").get_json()["messages"][-1]["id"]
khushi.post(f"{B}/threads/{dm}/read", json={"upto": last})
check("reading clears unread", khushi.get("/corp/unread").get_json()["total"] == 0)

scene("3. Database Team meeting (names, bad dates, an ambiguous Ravi)")
mid = khushi.post(B + "/meetings", json={"title": "Migration", "unit_id": dbt, "text": "MIGRATION plan BLOCKER discussed at length"}).get_json()["id"]
import time
for _ in range(40):
    m = khushi.get(f"{B}/meetings/{mid}").get_json()
    if m["status"] != "processing": break
    time.sleep(0.2)
cs = {c["text"]: c for c in m["commitments"]}
check("Khushi matched from a first name", cs["Prepare migration scripts"]["owner_id"] == uk)
check("'Ravi' is ambiguous: no owner guessed, the name is kept", cs["Review the scripts"]["owner_id"] is None and cs["Review the scripts"]["owner_name"] == "Ravi")
check("a nonsense due date becomes no due date", cs["Review the scripts"]["due_date"] is None)
check("Amit (owner, not in the team) still sees what's owed to him", amit.get(f"{B}/commitments/{cs['Prepare migration scripts']['id']}").status_code == 200)
check("Amit can't open the team meeting itself", amit.get(f"{B}/meetings/{mid}").status_code == 404)
check("too-short notes refused", khushi.post(B + "/meetings", json={"unit_id": dbt, "text": "hi"}).status_code == 400)
check("can't import someone else's conversation", khushi.post(B + "/meetings", json={"unit_id": dbt, "conversation_id": 1}).status_code == 404)
check("participants from another company refused", amit.post(B + "/meetings", json={"text": "x" * 40, "participants": [uom]}).status_code == 400)
g = khushi.post(B + "/meetings", json={"title": "Bad AI day", "unit_id": dbt, "text": "GARBAGE GARBAGE GARBAGE GARBAGE"}).get_json()["id"]
time.sleep(1.2)
gm = khushi.get(f"{B}/meetings/{g}").get_json()
check("broken AI reply: meeting still saved, nothing invented", gm["status"] == "ready" and not gm["commitments"] and not gm["decisions"])

scene("4. Private meeting and the confidential HR team")
pm = amit.post(B + "/meetings", json={"title": "1:1 with Priya", "text": "private discussion about targets " * 3, "participants": [up]}).get_json()["id"]
time.sleep(1)
check("1:1 visible to its participant", priya.get(f"{B}/meetings/{pm}").status_code == 200)
check("1:1 hidden from everyone else", khushi.get(f"{B}/meetings/{pm}").status_code == 404)
hm = neha.post(B + "/meetings", json={"title": "Salary review", "unit_id": U["HR"], "text": "confidential compensation review notes"}).get_json()["id"]
time.sleep(1)
check("HR meeting hidden from the org owner (seniority grants nothing)", amit.get(f"{B}/meetings/{hm}").status_code == 404)
r = amit.post(B + "/ask", json={"question": "salary review compensation"}).get_json()
check("Ask never surfaces HR content to the owner", not any(s["type"] == "meeting" and s["id"] == hm for s in r["sources"]))
check("asking about HR by id is refused", amit.post(B + "/ask", json={"question": "x", "unit_id": U["HR"]}).status_code == 403)

scene("5. Commitment life: confirm, done, reopen, drop, bad edits")
cid = cs["Prepare migration scripts"]["id"]
check("Priya (Sales) can't touch a Database Team commitment", priya.post(f"{B}/commitments/{cid}/confirm").status_code == 404)
check("Khushi confirms", khushi.post(f"{B}/commitments/{cid}/confirm").get_json()["commitment"]["status"] == "open")
check("confirming twice refused", khushi.post(f"{B}/commitments/{cid}/confirm").status_code == 400)
check("edit with nothing to change refused", khushi.post(f"{B}/commitments/{cid}/edit", json={}).status_code == 400)
check("owner can't be someone from another company", khushi.post(f"{B}/commitments/{cid}/edit", json={"owner_id": uom}).status_code == 400)
check("unknown action refused", khushi.post(f"{B}/commitments/{cid}/explode").status_code == 400)
rid = cs["Review the scripts"]["id"]
check("drop needs a reason", khushi.post(f"{B}/commitments/{rid}/drop", json={"reason": " "}).status_code == 400)
khushi.post(f"{B}/commitments/{rid}/edit", json={"owner_id": ura})
check("team lead assigns the ambiguous one to Ravi A", ravia.get(f"{B}/commitments?scope=mine").get_json()["commitments"][0]["id"] == rid)
khushi.post(f"{B}/commitments/{rid}/done", json={})
r = khushi.post(f"{B}/commitments/{rid}/reopen").get_json()
check("done then reopened: evidence cleared, history has both", r["commitment"]["status"] == "open" and r["commitment"]["evidence"] is None
      and [e["kind"] for e in r["history"]][-2:] == ["done", "reopen"])
past = khushi.post(B + "/commitments", json={"text": "Old promise", "unit_id": dbt, "due_date": (date.today() - timedelta(days=2)).isoformat()}).get_json()["id"]
q("UPDATE corp.commitments SET status = 'detected' WHERE id = %s", (past,))
check("confirming something already late makes it overdue straight away",
      khushi.post(f"{B}/commitments/{past}/confirm").get_json()["commitment"]["status"] == "overdue")

scene("6. Decisions: versions, and a cross-team overwrite attempt")
dl = khushi.get(B + "/decisions").get_json()["decisions"]
db_dec = next(x for x in dl if x["title"] == "Target PostgreSQL 17")["id"]
CTX["foreign_decision"] = db_dec
priya.post(B + "/meetings", json={"title": "Sales sync", "unit_id": U["Sales"], "text": "CROSSUNIT talk " * 3}).get_json()
time.sleep(1)
r = khushi.get(f"{B}/decisions/{db_dec}").get_json()
check("Sales' AI output can't rewrite a Database Team decision", r["decision"]["version"] == 1)
check("Priya can't add a version to it", priya.post(f"{B}/decisions/{db_dec}/versions", json={"text": "x"}).status_code == 404)
check("empty new version refused", khushi.post(f"{B}/decisions/{db_dec}/versions", json={"text": " "}).status_code == 400)

scene("7. Common ground edge cases")
tt = next(t["id"] for t in khushi.get(B + "/threads").get_json()["threads"] if t["unit_id"] == dbt)
check("not enough discussion yet", khushi.post(f"{B}/threads/{tt}/common-ground").status_code == 400)
for c, msg in ((khushi, "we should ship after staging is green"), (ravia, "I think we can ship tomorrow honestly")):
    c.post(f"{B}/threads/{tt}/messages", json={"body": msg})
khushi.put(B + "/work-note", json={"note": "Out Friday", "sharing": True})
ravia.put(B + "/work-note", json={"note": "", "sharing": True})
r = khushi.post(f"{B}/threads/{tt}/common-ground").get_json()
check("an empty shared note counts as not sharing", r["used_notes"] is False)
gid = r["id"]
check("Priya can't log it as a Database Team decision", priya.post(f"{B}/common-ground/{gid}/decision", json={"text": "Ship after staging"}).status_code == 400)
dmg = amit.post(f"{B}/threads/{dm}/messages", json={"body": "long enough discussion about the launch plan and timing"})
r = amit.post(f"{B}/threads/{dm}/common-ground").get_json()
check("a DM's agreement can't become a team decision", amit.post(f"{B}/common-ground/{r['id']}/decision", json={"text": "x"}).status_code == 400)

scene("8. Risk radar across Sales and Database Team")
sm = priya.post(B + "/meetings", json={"title": "Acme call", "unit_id": U["Sales"], "text": "SALESPROMISE to the customer"}).get_json()["id"]
time.sleep(1)
sc = next(c for c in priya.get(B + "/commitments?scope=team&unit_id=%d" % U["Sales"]).get_json()["commitments"] if "invoice" in c["text"])
priya.post(f"{B}/commitments/{sc['id']}/confirm")
q("UPDATE corp.blockers SET scanned_at = NULL WHERE org_id = %s", (org,))
n0 = CALLS["n"]; jobs.radar(cur, None, push); conn.commit()
check("radar links the Database blocker to the Sales promise", q("SELECT count(*) AS n FROM corp.risks WHERE commitment_id = %s", (sc["id"],))[0]["n"] == 1)
check("no AI call for blockers with no overlapping work", CALLS["n"] - n0 == 1)
jobs.radar(cur, None, push); conn.commit()
check("re-running the radar doesn't duplicate the risk or re-ask the AI",
      q("SELECT count(*) AS n FROM corp.risks WHERE commitment_id = %s", (sc["id"],))[0]["n"] == 1 and CALLS["n"] - n0 == 1)
bp = priya.get(B + "/brief").get_json()["risks"]
check("Priya sees her promise is at risk, not the other team's details", bp and bp[0]["blocker"] is None and bp[0]["blocker_unit"] == "Database Team")
priya.post(f"{B}/commitments/{sc['id']}/done", json={"note": "shipped"})
check("finishing the commitment clears the risk", not priya.get(B + "/brief").get_json()["risks"])

scene("9. Background jobs don't waste AI calls")
q("UPDATE corp.commitments SET checked_at = now() WHERE org_id = %s", (org,))
n0 = CALLS["n"]; jobs.check_evidence(cur, None, push); conn.commit()
check("nothing new happened -> no AI call", CALLS["n"] == n0)
khushi.post(f"{B}/threads/{tt}/messages", json={"body": "DELIVERED the scripts to Amit"})
q("UPDATE corp.commitments SET checked_at = now() - interval '1 minute' WHERE id = %s", (cid,))
jobs.check_evidence(cur, None, push); conn.commit()
check("evidence closes only the matching commitment, not the rest of the team's",
      khushi.get(f"{B}/commitments/{past}").get_json()["commitment"]["status"] == "overdue")
check("evidence closes Khushi's commitment and tells Amit",
      amit.get(f"{B}/commitments/{cid}").get_json()["commitment"]["status"] == "done" and any(p[0] == ua for p in pushed))
q("DELETE FROM corp.job_state WHERE key = 'brief_day'")
before = len(pushed)
from datetime import datetime, timezone as _tz
jobs.IST = _tz(timedelta(hours=(12 - datetime.now(_tz.utc).hour) % 24))  # pretend it's noon
rv = jobs.daily_brief(cur, push); conn.commit(); first = len(pushed) - before
jobs.daily_brief(cur, push); conn.commit()
check("daily brief goes out once a day, not twice", first > 0 and len(pushed) - before == first, f"first={first} total={len(pushed)-before} hour={datetime.now(jobs.IST).hour}")

scene("10. The AI service runs out of credit")
real_ask, real_answer = ai._ask, ai.answer
def down(*a, **k): raise ai.AIUnavailable(Exception("402 Client Error: Payment Required"))
ai._ask = lambda call_llm, s_, u_, task=None: ai._call(lambda m: down(), [])
ai.answer = lambda call_llm, q_, s_: ai._call(lambda m: down(), [])
r = khushi.post(f"{B}/threads/{tt}/common-ground")
check("common ground says the AI hit its limit (not a vague error)", r.status_code == 503 and "usage limit" in r.get_json()["error"], r.get_json())
r = khushi.post(B + "/ask", json={"question": "migration"})
check("Ask says the same", r.status_code == 503 and "usage limit" in r.get_json()["error"])
dm2 = khushi.post(B + "/meetings", json={"title": "While AI down", "unit_id": dbt, "text": "MIGRATION notes again and again"}).get_json()["id"]
time.sleep(1.2)
r = khushi.get(f"{B}/meetings/{dm2}").get_json()
check("a meeting added while the AI is down is marked failed, not silently empty", r["status"] == "failed" and "usage limit" in (r["error"] or ""), r.get("error"))
ai._ask, ai.answer = real_ask, real_answer

scene("11. People leave")
check("Om's org is invisible to NodexData people", amit.get(f"/corp/orgs/{om_org}").status_code == 404)
amit.delete(f"{B}/members/{urb}")
check("removed Ravi B loses the team thread", ravib.get(f"{B}/threads/{tt}/messages").status_code == 404)
khushi.delete(f"{B}/units/{dbt}/members/{ura}")
check("Ravi A moved off the team loses its meetings", ravia.get(f"{B}/meetings/{mid}").status_code == 404)
check("...but still sees his own commitment", ravia.get(f"{B}/commitments/{rid}").status_code == 200)
q("DELETE FROM users WHERE id = %s", (uk,))
r = amit.get(f"{B}/threads").get_json()["threads"]
check("a deleted colleague's DM doesn't break the list", any(t["id"] == dm for t in r))
check("their old messages still load", amit.get(f"{B}/threads/{dm}/messages").status_code == 200)
r = amit.post("/account/delete", json={"password": "secret123", "confirm": "DELETE"})
check("Amit (the owner) deletes his account", r.status_code == 200, r.get_json())
heir = q("SELECT u.username FROM corp.members m JOIN users u ON u.id = m.user_id WHERE m.org_id = %s AND m.role = 'owner'", (org,))
check("ownership passes to someone still there, not left empty", len(heir) == 1, heir)
r = om.post("/account/delete", json={"password": "secret123", "confirm": "DELETE"})
check("a one-person organisation is removed with its owner", r.status_code == 200 and not q("SELECT 1 FROM corp.orgs WHERE id = %s", (om_org,)))

# cleanup
q("DELETE FROM corp.orgs WHERE id IN (%s, %s)", (org, om_org)); q("DELETE FROM users WHERE username LIKE %s", (f"%{tag}",))
app.release_raw_connection(conn)
print(f"\n{sum(results)}/{len(results)} passed; AI calls: {CALLS['n']}")
for f in findings: print("FINDING", f)
sys.exit(0 if all(results) else 1)
