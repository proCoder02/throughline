"""Work identity: a person confirms a work email with a code and accepts
invitations sent to it; leaving keeps history but ends access (teams,
projects, tickets, email scanning) and hands open promises to the team's
manager; rejoining reactivates; a company reusing an email for a new hire
moves it; deleted accounts read as "Former member"."""
import os, sys, uuid, re
sys.path.insert(0, os.getcwd())
os.environ.update(CORPORATE_ENABLED="true", CORPORATE_JOBS="false", PYTHONIOENCODING="utf-8")
import app  # noqa: E402
sent = []
app._smtp_send = lambda to, subject, body, **kw: sent.append((to, subject, body))
import psycopg2.extras

results = []
def check(n, c, x=""):
    results.append(bool(c)); print(("PASS " if c else "FAIL ") + n + (f"  -- {x}" if x != "" and not c else ""))
tag = uuid.uuid4().hex[:5]
conn = app.get_raw_connection(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
def q(sql, a=()):
    cur.execute(sql, a); out = cur.fetchall() if cur.description else None; conn.commit(); return out
def person(n):
    c = app.app.test_client(); u = f"{n}{tag}"
    return c, c.post("/register", json={"username": u, "email": f"{u}@gmail.test", "password": "secret123"}).get_json()["id"]
def last_code(to):
    for t, subject, _ in reversed(sent):
        if t == to:
            return re.search(r"(\d{6})", subject).group(1)
    return None

own, uo = person("own"); amit, ua = person("amit"); mgr, um = person("mgr"); newhire, un = person("newhire")
d = own.post("/corp/orgs", json={"name": f"Acme {tag}", "departments": ["Sales"]}).get_json()
org = d["org"]["id"]; sales = d["units"][0]["id"]; B = f"/corp/orgs/{org}"
work = f"amit.{tag}@acme.test"

# --- invitation to a work email the account doesn't use
r = own.post(B + "/invites", json={"emails": work, "unit_id": sales})
link = r.get_json()["created"][0]["link"]; token = link.rsplit("/", 1)[1]
check("invitation emailed to the work address", any(t == work for t, _, _ in sent))
check("not shown before the work email is confirmed", amit.get("/corp/invites/mine").get_json()["invites"] == [])
r = amit.post(f"/corp/invites/{token}/accept")
check("accepting needs the work email confirmed", r.status_code == 403 and "Confirm that work email" in r.get_json()["error"], r.get_json())

# --- confirm the work email with a code
check("bad address refused", amit.post("/corp/work-email", json={"email": "not-an-email"}).status_code == 400)
r = amit.post("/corp/work-email", json={"email": work})
code = last_code(work)
check("code sent to the work address", r.status_code == 200 and code is not None, r.get_json())
check("no resend within a minute", amit.post("/corp/work-email", json={"email": work}).status_code == 429)
bad = "000000" if code != "000000" else "111111"
check("wrong code refused", amit.post("/corp/work-email/verify", json={"email": work, "code": bad}).status_code == 400)
check("someone else can't use my code", newhire.post("/corp/work-email/verify", json={"email": work, "code": code}).status_code == 400)
r = amit.post("/corp/work-email/verify", json={"email": work, "code": code})
check("right code confirms it", r.status_code == 200 and r.get_json()["verified"] is True)
check("listed as my work email", [e["email"] for e in amit.get("/corp/work-email").get_json()["work_emails"]] == [work])
inv = amit.get("/corp/invites/mine").get_json()
check("now the invitation shows up", len(inv["invites"]) == 1 and work in inv["emails"], inv)
check("preview says the email matches", amit.get(f"/corp/invites/{token}").get_json()["email_matches"] is True)
check("accept works", amit.post(f"/corp/invites/{token}/accept").status_code == 200)
me = [o for o in amit.get("/corp/me").get_json()["orgs"] if o["id"] == org]
check("membership records the work email", me and me[0]["work_email"] == work, me)
check("admin sees the work email", any(m.get("work_email") == work for m in own.get(B + "/members").get_json()["members"]))
check("own account email still works for invitations", own.post("/corp/work-email", json={"email": f"own{tag}@gmail.test"}).get_json().get("verified") is True)

# --- set up work that the leaver touches
q("INSERT INTO corp.members (org_id, user_id) VALUES (%s,%s)", (org, um))
own.put(f"{B}/units/{sales}/members/{um}", json={"role": "manager"})
c1 = amit.post(B + "/commitments", json={"text": "Send the pricing sheet to Vikings", "unit_id": sales}).get_json()["id"]
pid = own.post(B + "/projects", json={"name": "Vikings rollout"}).get_json()["id"]
own.put(f"{B}/projects/{pid}/ticket-key", json={"key": f"V{tag[:3].upper()}"})
own.put(f"{B}/projects/{pid}/members/{ua}", json={"role": "member"})
t1 = own.post(B + "/tickets", json={"project_id": pid, "title": "Pricing sheet", "assignees": [ua]}).get_json()["ticket"]["id"]
team = [t for t in amit.get(B + "/threads").get_json()["threads"] if t["kind"] == "team"][0]["id"]
amit.post(f"{B}/threads/{team}/messages", json={"body": "I'll send the pricing sheet tomorrow."})
q("INSERT INTO corp.mail_links (org_id, user_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (org, ua))

# --- leaving
check("amit leaves", amit.post(f"{B}/leave").status_code == 200)
row = q("SELECT status, left_at FROM corp.members WHERE org_id = %s AND user_id = %s", (org, ua))[0]
check("membership kept as 'left'", row["status"] == "left" and row["left_at"] is not None, row)
check("organisation gone from their list", all(o["id"] != org for o in amit.get("/corp/me").get_json()["orgs"]))
r = amit.get(B)
check("no access any more", r.status_code == 404 and "no longer" in r.get_json()["error"], r.get_json())
check("off the team list", all(m["user_id"] != ua for m in own.get(B + "/members").get_json()["members"]))
owner_now = q("SELECT owner_id FROM corp.commitments WHERE id = %s", (c1,))[0]["owner_id"]
check("open promise handed to the team's manager", owner_now == um, owner_now)
ev = q("SELECT kind, detail FROM corp.commitment_events WHERE commitment_id = %s ORDER BY id DESC LIMIT 1", (c1,))[0]
check("hand-over recorded", ev["kind"] == "reassigned")
check("off the project", not q("SELECT 1 FROM corp.project_members WHERE project_id = %s AND user_id = %s", (pid, ua)))
check("unassigned from tickets", not q("SELECT 1 FROM corp.ticket_people WHERE ticket_id = %s AND user_id = %s", (t1, ua)))
check("work email no longer read", q("SELECT enabled FROM corp.mail_links WHERE org_id = %s AND user_id = %s", (org, ua))[0]["enabled"] is False)
msgs = mgr.get(f"{B}/threads/{team}/messages").get_json()["messages"]  # a team member reads the team chat
check("their messages still show their name", any(m["sender"] == f"amit{tag}" for m in msgs))
check("can be assigned work no more", own.put(f"{B}/tickets/{t1}/people/{ua}", json={"role": "assignee"}).status_code == 400)

# --- rejoin
r = own.post(B + "/invites", json={"emails": work, "unit_id": sales})
check("a former member can be invited again", r.status_code == 201 and r.get_json()["created"], r.get_json())
tok2 = r.get_json()["created"][0]["link"].rsplit("/", 1)[1]
check("rejoining works", amit.post(f"/corp/invites/{tok2}/accept").status_code == 200)
check("active again", q("SELECT status FROM corp.members WHERE org_id = %s AND user_id = %s", (org, ua))[0]["status"] == "active")
check("history is visible again", any("pricing sheet" in m["body"] for m in amit.get(f"{B}/threads/{team}/messages").get_json()["messages"]))

# --- the company reuses the address for a new hire
newhire.post("/corp/work-email", json={"email": work})
check("new hire confirms the reused address", newhire.post("/corp/work-email/verify", json={"email": work, "code": last_code(work)}).status_code == 200)
check("it moves to the new hire", [e["email"] for e in newhire.get("/corp/work-email").get_json()["work_emails"]] == [work]
      and amit.get("/corp/work-email").get_json()["work_emails"] == [])

# --- account deleted: records read "Former member"
c2 = mgr.post(B + "/commitments", json={"text": "Review the Vikings contract", "unit_id": sales}).get_json()["id"]
mgr.post(f"{B}/threads/{team}/messages", json={"body": "Contract reviewed, two comments."}) if False else None
from corporate import orgs
orgs.hand_over(cur, um); conn.commit()
q("DELETE FROM users WHERE id = %s", (um,))
cm = q("SELECT owner_id, owner_name FROM corp.commitments WHERE id = %s", (c2,))[0]
check("deleted account: promise kept, owner reads 'Former member'", cm["owner_id"] is None and cm["owner_name"] == "Former member", cm)
print(f"\n{sum(results)}/{len(results)} passed")
