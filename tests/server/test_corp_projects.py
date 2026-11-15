"""Sales -> client promise -> Engineering request -> slip/blocker -> Sales told.
Plus projects: stakeholder map, deliverables, health, and permissions."""
import os, sys, uuid
sys.path.insert(0, os.getcwd())
os.environ.update(CORPORATE_ENABLED="true", CORPORATE_JOBS="false", PYTHONIOENCODING="utf-8")
import app  # noqa: E402
app._smtp_send = lambda *a, **k: None
import psycopg2.extras
from datetime import date, timedelta
from corporate import jobs

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
own, uo = person("own"); priya, up = person("priya"); eng, ue = person("englead"); dev, ud = person("dev"); out, ux = person("out")
d = own.post("/corp/orgs", json={"name": f"Nodex {tag}", "departments": ["Sales", "Engineering"]}).get_json()
org = d["org"]["id"]; U = {u["name"]: u["id"] for u in d["units"]}
for uid in (up, ue, ud, ux):
    q("INSERT INTO corp.members (org_id, user_id) VALUES (%s,%s)", (org, uid))
own.put(f"/corp/orgs/{org}/units/{U['Sales']}/members/{up}", json={"role": "manager"})
own.put(f"/corp/orgs/{org}/units/{U['Engineering']}/members/{ue}", json={"role": "manager"})
own.put(f"/corp/orgs/{org}/units/{U['Engineering']}/members/{ud}", json={"role": "member"})
B = f"/corp/orgs/{org}"
end = date.today() + timedelta(days=20)

# --- project + stakeholder map
pid = priya.post(B + "/projects", json={"name": "e-Gov portal", "client": "Ministry of IT", "end_date": end.isoformat()}).get_json()["id"]
check("creator is the project lead", priya.get(f"{B}/projects/{pid}").get_json()["project"]["can_edit"] is True)
check("non-member can't see the project", out.get(f"{B}/projects/{pid}").status_code == 404)
sec = priya.post(f"{B}/projects/{pid}/contacts", json={"name": "R. Sharma", "position": "Secretary", "organisation": "Ministry of IT", "department": "Office of the Secretary"}).get_json()
sec_id = sec["contacts"][0]["id"]
r = priya.post(f"{B}/projects/{pid}/contacts", json={"name": "A. Verma", "position": "Joint Secretary", "organisation": "Ministry of IT",
                                                   "department": "e-Governance", "reports_to": sec_id}).get_json()
js_id = next(c["id"] for c in r["contacts"] if c["name"] == "A. Verma")
check("hierarchy stored (Joint Secretary reports to Secretary)", next(c for c in r["contacts"] if c["id"] == js_id)["reports_to"] == sec_id)
check("can't report to yourself", priya.patch(f"{B}/projects/{pid}/contacts/{js_id}", json={"reports_to": js_id}).status_code == 400)
check("can't report to someone outside the project", priya.post(f"{B}/projects/{pid}/contacts", json={"name": "X", "reports_to": 999999999}).status_code == 400)
priya.put(f"{B}/projects/{pid}/members/{ue}", json={"role": "member"})
check("project needs a lead", priya.put(f"{B}/projects/{pid}/members/{up}", json={"role": "member"}).status_code == 400)

# --- Sales promise to the client
promise = priya.post(B + "/commitments", json={"text": "Deliver citizen login module", "unit_id": U["Sales"], "due_date": end.isoformat(),
                                              "project_id": pid, "contact_id": js_id}).get_json()["id"]
# --- request to Engineering
r = priya.post(B + "/requests", json={"text": "Build citizen login (OTP + Aadhaar)", "unit_id": U["Engineering"], "due_date": (end - timedelta(days=5)).isoformat(),
                                     "supports_id": promise, "project_id": pid})
req = r.get_json()["id"]
check("Sales asks Engineering", r.status_code == 201)
inc = eng.get(B + "/requests/incoming").get_json()["requests"]
check("Engineering lead sees it in incoming, with what it supports", inc and inc[0]["id"] == req and inc[0]["supports_text"] == "Deliver citizen login module")
check("a non-lead in Engineering can't accept", dev.post(f"{B}/requests/{req}/accept").status_code == 403)
check("the requester can't accept their own request", priya.post(f"{B}/requests/{req}/accept").status_code == 403)
check("requester can't 'confirm' it as if it were theirs", priya.post(f"{B}/commitments/{req}/confirm").status_code == 400)
late = end + timedelta(days=4)
r = eng.post(f"{B}/requests/{req}/accept", json={"owner_id": ud, "due_date": late.isoformat()}).get_json()
check("lead accepts with a later date (a counter-proposal)", r["later_than_asked"] is True)
p = priya.get(f"{B}/commitments/{promise}/updates").get_json()
promise_row = q("SELECT risk_note FROM corp.commitments WHERE id = %s", (promise,))[0]
check("Sales' promise is now at risk, with the exact gap", promise_row["risk_note"] and "4 days after your date" in promise_row["risk_note"], promise_row)
check("Sales sees Engineering's status, owner and date", p["supporting"][0]["owner"] == f"dev{tag}" and p["supporting"][0]["status"] == "open")
check("but not Engineering's private chat/meetings (only shared updates)", "updates" in p and p["supporting"][0].get("blocker") is None)

# --- Engineering pulls in, then gets blocked
dev.post(f"{B}/commitments/{req}/edit", json={"due_date": (end - timedelta(days=2)).isoformat()})
check("date pulled back in: risk clears", q("SELECT risk_note FROM corp.commitments WHERE id = %s", (promise,))[0]["risk_note"] is None)
dev.post(f"{B}/commitments/{req}/updates", json={"kind": "blocker", "text": "Waiting on UIDAI sandbox API keys from the ministry"})
note = q("SELECT risk_note FROM corp.commitments WHERE id = %s", (promise,))[0]["risk_note"]
check("a blocker puts the promise at risk with the reason", note and "UIDAI sandbox" in note, note)
p = priya.get(f"{B}/commitments/{promise}/updates").get_json()
check("Sales sees where Engineering is stuck", p["supporting"][0]["blocker"].startswith("Waiting on UIDAI"))
check("outsider can't read the updates", out.get(f"{B}/commitments/{req}/updates").status_code == 404)
dev.post(f"{B}/commitments/{req}/updates", json={"kind": "unblocked", "text": "Keys received"})
check("unblocked: risk clears", q("SELECT risk_note FROM corp.commitments WHERE id = %s", (promise,))[0]["risk_note"] is None)
q("UPDATE corp.commitments SET due_date = %s WHERE id = %s", (date.today() - timedelta(days=1), req))
jobs.mark_overdue(cur); conn.commit()
check("supporting work goes overdue: promise at risk", "overdue" in (q("SELECT risk_note FROM corp.commitments WHERE id = %s", (promise,))[0]["risk_note"] or ""))
dev.post(f"{B}/commitments/{req}/done", json={"note": "Shipped to staging"})
check("supporting work done: risk clears", q("SELECT risk_note FROM corp.commitments WHERE id = %s", (promise,))[0]["risk_note"] is None)

# --- a declined request
r2 = priya.post(B + "/requests", json={"text": "Mobile app too", "unit_id": U["Engineering"], "due_date": end.isoformat(), "supports_id": promise}).get_json()["id"]
check("decline needs a reason", eng.post(f"{B}/requests/{r2}/decline", json={}).status_code == 400)
eng.post(f"{B}/requests/{r2}/decline", json={"reason": "No mobile capacity this quarter"})
check("declined request puts the promise at risk", "declined" in (q("SELECT risk_note FROM corp.commitments WHERE id = %s", (promise,))[0]["risk_note"] or ""))
check("answered requests can't be answered again", eng.post(f"{B}/requests/{r2}/accept").status_code == 400)

# --- dashboard
dash = priya.get(f"{B}/projects/{pid}").get_json()
check("dashboard: contacts, deliverables, health", len(dash["contacts"]) == 2 and len(dash["deliverables"]) >= 2 and dash["health"] in ("at_risk", "late"), dash["health"])
check("deliverable shows its client counterpart", next(x for x in dash["deliverables"] if x["id"] == promise)["contact_name"] == "A. Verma")
check("project members see project deliverables (Engineering lead added)", any(x["id"] == promise for x in eng.get(f"{B}/projects/{pid}").get_json()["deliverables"]))
check("projects list shows risk count", priya.get(B + "/projects").get_json()["projects"][0]["at_risk"] >= 1)
check("admin (not a member) can see it in the list", any(x["id"] == pid for x in own.get(B + "/projects").get_json()["projects"]))
check("member can't delete the project", eng.delete(f"{B}/projects/{pid}").status_code == 403)
check("lead deletes; deliverables stay", priya.delete(f"{B}/projects/{pid}").status_code == 200 and q("SELECT 1 FROM corp.commitments WHERE id = %s", (promise,)))

q("DELETE FROM corp.orgs WHERE id = %s", (org,)); q("DELETE FROM users WHERE username LIKE %s", (f"%{tag}",))
app.release_raw_connection(conn)
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
