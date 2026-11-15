"""Sales/BU view of engineering: tickets roll up into client deliverables,
stakeholders follow read-only, client update drafts, the weekly digest, and
promise owners hearing about the tickets behind their promise."""
import os, sys, uuid
sys.path.insert(0, os.getcwd())
os.environ.update(CORPORATE_ENABLED="true", CORPORATE_JOBS="false", PYTHONIOENCODING="utf-8")
import app  # noqa: E402
app._smtp_send = lambda *a, **k: None
import psycopg2.extras
from datetime import date, datetime, timedelta, timezone
from corporate import ai, deps, insights, jobs

results = []
def check(n, c, x=""):
    results.append(bool(c)); print(("PASS " if c else "FAIL ") + n + (f"  -- {x}" if x != "" and not c else ""))
tag = uuid.uuid4().hex[:5]
conn = app.get_raw_connection(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
def q(sql, a=()):
    cur.execute(sql, a); out = cur.fetchall() if cur.description else None; conn.commit(); return out
def person(n):
    c = app.app.test_client(); u = f"{n}{tag}"
    return c, c.post("/register", json={"username": u, "email": f"{u}@example.test", "password": "secret123"}).get_json()["id"]
own, uo = person("own"); sales, us = person("priya"); dev, ud = person("dev"); bu, ub = person("buhead"); out, ux = person("out")
d = own.post("/corp/orgs", json={"name": f"Nodex {tag}", "departments": ["Sales", "Engineering"]}).get_json()
org = d["org"]["id"]; U = {u["name"]: u["id"] for u in d["units"]}
for uid in (us, ud, ub, ux):
    q("INSERT INTO corp.members (org_id, user_id) VALUES (%s,%s)", (org, uid))
own.put(f"/corp/orgs/{org}/units/{U['Sales']}/members/{us}", json={"role": "manager"})
own.put(f"/corp/orgs/{org}/units/{U['Engineering']}/members/{ud}", json={"role": "member"})
B = f"/corp/orgs/{org}"
due = date.today() + timedelta(days=20)

pid = sales.post(B + "/projects", json={"name": "e-Gov portal", "client": "Ministry of IT"}).get_json()["id"]
sales.put(f"{B}/projects/{pid}/ticket-key", json={"key": "GOV"})
sales.put(f"{B}/projects/{pid}/members/{ud}", json={"role": "member"})
contact = sales.post(f"{B}/projects/{pid}/contacts", json={"name": "A. Verma", "position": "Joint Secretary", "email": "verma@gov.example"}).get_json()["contacts"][0]["id"]
promise = sales.post(B + "/commitments", json={"text": "Deliver citizen login module", "unit_id": U["Sales"], "due_date": due.isoformat(),
                                              "project_id": pid, "contact_id": contact}).get_json()["id"]
other_pid = sales.post(B + "/projects", json={"name": "Other"}).get_json()["id"]
other_promise = sales.post(B + "/commitments", json={"text": "Other thing", "unit_id": U["Sales"], "project_id": other_pid}).get_json()["id"]

# --- 2. stakeholder role
check("stakeholder role accepted", sales.put(f"{B}/projects/{pid}/members/{ub}", json={"role": "stakeholder"}).status_code == 200)
check("stakeholder sees the dashboard", bu.get(f"{B}/projects/{pid}").status_code == 200)
check("stakeholder can't create tickets", bu.post(B + "/tickets", json={"project_id": pid, "title": "x"}).status_code == 403)

# --- 1. tickets support the deliverable, roll up
r = dev.post(B + "/tickets", json={"project_id": pid, "title": "OTP login API", "supports_id": promise,
                                   "due_date": (due - timedelta(days=5)).isoformat(), "assignees": [ud]})
check("ticket created supporting the client deliverable", r.status_code == 201 and r.get_json()["supports"]["id"] == promise, r.get_json())
t1 = r.get_json()["ticket"]["id"]
check("can't support a deliverable from another project",
      dev.post(B + "/tickets", json={"project_id": pid, "title": "x", "supports_id": other_promise}).status_code == 400)
t2 = dev.post(B + "/tickets", json={"project_id": pid, "title": "Aadhaar consent screen", "supports_id": promise}).get_json()["ticket"]["id"]
check("stakeholder sees the ticket", bu.get(f"{B}/tickets/{t1}").status_code == 200)
check("stakeholder can't change it", bu.patch(f"{B}/tickets/{t1}", json={"status": "done"}).status_code == 403)
dash = bu.get(f"{B}/projects/{pid}").get_json()
item = next(i for i in dash["deliverables"] if i["id"] == promise)
check("deliverable rolls up its tickets (0 of 2)", item["tickets"] and item["tickets"]["total"] == 2 and item["tickets"]["done"] == 0, item.get("tickets"))
check("not at risk yet", not q("SELECT risk_note FROM corp.commitments WHERE id = %s", (promise,))[0]["risk_note"])

# --- 5. ticket changes reach the promise
dev.patch(f"{B}/tickets/{t1}", json={"status": "blocked"})
note = q("SELECT risk_note FROM corp.commitments WHERE id = %s", (promise,))[0]["risk_note"] or ""
check("blocked ticket puts the client promise at risk", "GOV-1 is blocked" in note, note)
dev.patch(f"{B}/tickets/{t1}", json={"status": "in_progress", "due_date": (due + timedelta(days=3)).isoformat()})
note = q("SELECT risk_note FROM corp.commitments WHERE id = %s", (promise,))[0]["risk_note"] or ""
check("ticket due after the promise date: exact gap", "GOV-1 expects" in note and "3 days after your date" in note, note)
dev.patch(f"{B}/tickets/{t1}", json={"due_date": (due - timedelta(days=1)).isoformat()})
check("back on time: risk cleared", not q("SELECT risk_note FROM corp.commitments WHERE id = %s", (promise,))[0]["risk_note"])
pushed = []
dev.patch(f"{B}/tickets/{t1}", json={"status": "blocked"}); dev.patch(f"{B}/tickets/{t1}", json={"status": "in_progress"})
q("UPDATE corp.commitments SET risk_note = NULL WHERE id = %s", (promise,))
q("UPDATE corp.tickets SET status = 'blocked' WHERE id = %s", (t1,))
deps.evaluate(cur, promise, lambda *a: pushed.append(a), ud); conn.commit()
check("promise owner is told it may slip", pushed and pushed[0][0] == us and "GOV-1 is blocked" in pushed[0][2], pushed)
q("UPDATE corp.tickets SET status = 'in_progress' WHERE id = %s", (t1,)); deps.evaluate(cur, promise, None, ud); conn.commit()
dev.patch(f"{B}/tickets/{t1}", json={"status": "done"})
ev = q("SELECT kind FROM corp.ticket_events WHERE ticket_id = %s AND kind = 'status' ORDER BY id DESC LIMIT 1", (t1,))
item = next(i for i in sales.get(f"{B}/projects/{pid}").get_json()["deliverables"] if i["id"] == promise)
check("done ticket: deliverable shows 1 of 2", item["tickets"]["done"] == 1 and item["tickets"]["total"] == 2)
upd = sales.get(f"{B}/commitments/{promise}/updates").get_json()
check("the promise lists its tickets", upd["tickets"] and [t["key"] for t in upd["tickets"]["tickets"]] == ["GOV-1", "GOV-2"])
check("ticket page offers the project's deliverables", any(x["id"] == promise for x in dev.get(f"{B}/tickets/{t2}").get_json()["deliverables"]))
dev.patch(f"{B}/tickets/{t2}", json={"supports_id": None})
check("unlinking a ticket from the deliverable", next(i for i in sales.get(f"{B}/projects/{pid}").get_json()["deliverables"]
                                                       if i["id"] == promise)["tickets"]["total"] == 1)
dev.patch(f"{B}/tickets/{t2}", json={"supports_id": promise})

# --- 3. client update draft (fake model: check what it was given)
seen = {}
def fake_update(call_llm, project, client, recipient, sender, lines):
    seen.update(project=project, client=client, recipient=recipient, lines=lines)
    return {"subject": "Update", "body": "Dear Joint Secretary, ...", "internal": ["GOV-2 not started"]}
ai.client_update = fake_update
r = bu.post(f"{B}/projects/{pid}/client-update", json={"contact_id": contact})
check("stakeholder can draft a client update", r.status_code == 200 and r.get_json()["body"].startswith("Dear"), r.get_json())
check("draft goes to the contact's email (as a draft)", r.get_json()["to"] == "verma@gov.example")
check("drafted from facts incl. ticket progress", "1 of 2 work items done" in seen["lines"] and "Deliver citizen login module" in seen["lines"], seen.get("lines"))
check("addressed to the contact", seen["recipient"].startswith("A. Verma"))
check("outsider can't draft", out.post(f"{B}/projects/{pid}/client-update", json={}).status_code == 404)

# --- 4. weekly digest
dg = bu.get(B + "/client-digest").get_json()
pj = next((x for x in dg["projects"] if x["id"] == pid), None)
check("stakeholder's digest includes the project", pj is not None, dg)
allp = (pj["at_risk"] + pj["due_soon"] + pj["on_track"]) if pj else []
check("digest shows every open deliverable with progress", allp and allp[0]["id"] == promise and allp[0]["progress"]["done"] == 1, pj)
check("digest counts tickets done this week", pj and pj["tickets_done"] >= 1)
check("outsider's digest is empty", out.get(B + "/client-digest").get_json()["projects"] == [])
pushed = []
class Monday(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 11, 16, 10, 0, tzinfo=tz or timezone.utc)
real = jobs.datetime; jobs.datetime = Monday
n1 = jobs.weekly_digest(cur, lambda *a: pushed.append(a)); conn.commit()
n2 = jobs.weekly_digest(cur, lambda *a: pushed.append(a)); conn.commit()
jobs.datetime = real
mine = [p for p in pushed if p[0] in (us, ub)]
check("Monday digest notifies Sales and the stakeholder", {p[0] for p in mine} >= {us, ub}, [p[:3] for p in pushed][:5])
check("...once a week only", n2 == 0 or not [p for p in pushed[len(pushed) - n2:] if p[0] in (us, ub)])
check("digest text is a summary", mine and "client project" in mine[0][2], mine[:1])
print(f"\n{sum(results)}/{len(results)} passed")
