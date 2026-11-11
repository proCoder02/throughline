"""Onboarding checks: invitations found in-app, CSV bulk invites, creation
policy, pausing access, and leavers' commitments handed over. No real email."""
import os, sys, uuid
sys.path.insert(0, os.getcwd())
os.environ.update(CORPORATE_ENABLED="true", CORPORATE_JOBS="false", PYTHONIOENCODING="utf-8")
import app  # noqa: E402
app._smtp_send = lambda *a, **k: None
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
    r = c.post("/register", json={"username": u, "email": f"{u}@example.test", "password": "secret123"})
    return c, r.get_json()["id"], f"{u}@example.test"
amit, ua, ea = person("amit"); kunal, uk, ek = person("kunal"); lead, ul, el = person("lead"); stranger, us_, es = person("str")

d = amit.post("/corp/orgs", json={"name": f"Nodex {tag}", "departments": ["Technology", "Sales"]}).get_json()
org = d["org"]["id"]; U = {u["name"]: u["id"] for u in d["units"]}
dbt = amit.post(f"/corp/orgs/{org}/units", json={"name": "Database Team", "kind": "team", "parent_id": U["Technology"]}).get_json()["id"]
amit.post(f"/corp/orgs/{org}/units", json={"name": "Database Team", "kind": "team", "parent_id": U["Sales"]})  # same name elsewhere
B = f"/corp/orgs/{org}"

# --- 1. invitations show up in-app (the Kunal case)
check("no invitation yet: nothing offered", kunal.get("/corp/invites/mine").get_json()["invites"] == [])
amit.post(B + "/invites", json={"emails": ek, "unit_id": dbt})
mine = kunal.get("/corp/invites/mine").get_json()
check("Kunal sees Amit's invitation without the email link", len(mine["invites"]) == 1 and mine["invites"][0]["org_name"] == f"Nodex {tag}"
      and mine["invites"][0]["invited_by"] == f"amit{tag}" and mine["email"] == ek, mine)
check("someone else doesn't see it", stranger.get("/corp/invites/mine").get_json()["invites"] == [])
iid = mine["invites"][0]["id"]
check("someone else can't accept it by id", stranger.post(f"/corp/invites/mine/{iid}/accept").status_code == 403)
check("Kunal joins with one tap", kunal.post(f"/corp/invites/mine/{iid}/accept").get_json().get("org_id") == org)
check("joined: invitation gone from his list, he's in the team",
      kunal.get("/corp/invites/mine").get_json()["invites"] == [] and kunal.get(f"{B}/units/{dbt}/members").status_code == 200)

# --- 2. CSV bulk invites
rows = [{"email": el, "team": "Technology", "team_role": "head"},
        {"email": "new1@example.test", "team": "sales"},
        {"email": "new2@example.test", "team": "Database Team"},       # ambiguous
        {"email": "new3@example.test", "team": "Nope"},
        {"email": "not-an-email"},
        {"email": "new4@example.test", "role": "admin"},
        {"email": ek}]                                                  # already a member
r = amit.post(B + "/invites", json={"rows": rows}).get_json()
made = {c["email"]: c["team"] for c in r["created"]}
check("valid rows invited into their teams", made.get(el) == "Technology" and made.get("new1@example.test") == "Sales" and "new4@example.test" in made, made)
check("'head' maps to department head", q("SELECT unit_role FROM corp.invites WHERE org_id = %s AND email = %s", (org, el))[0]["unit_role"] == "manager")
errs = {e["email"]: e["error"] for e in r["errors"]}
check("ambiguous and unknown team names reported per row", "more than one" in errs.get("new2@example.test", "") and "no team" in errs.get("new3@example.test", ""), errs)
check("bad address and existing member reported", r["invalid"] == ["not-an-email"] and r["already_members"] == [ek])
lead_tok = q("SELECT id FROM corp.invites WHERE org_id = %s AND email = %s AND accepted_at IS NULL AND revoked_at IS NULL", (org, el))[0]["id"]
lead.post(f"/corp/invites/mine/{lead_tok}/accept")
r = lead.post(B + "/invites", json={"rows": [{"email": "x1@example.test", "team": "Database Team"}, {"email": "x2@example.test", "team": "Sales"},
                                              {"email": "x3@example.test", "role": "admin", "team": "Technology"}]}).get_json()
check("a department head's CSV only works inside their own department",
      [c["email"] for c in r["created"]] == [] and len(r["errors"]) == 3, r)  # 'Database Team' is ambiguous, Sales isn't theirs, admin is owner-only

# --- 3. creation policy
os.environ["CORPORATE_ORG_CREATION"] = "closed"; os.environ["CORPORATE_CREATORS"] = ea
check("closed: strangers can't create organisations", stranger.post("/corp/orgs", json={"name": "Spam Co"}).status_code == 403)
check("closed: told to ask their admin", "ask its admin" in stranger.post("/corp/orgs", json={"name": "Spam Co"}).get_json()["error"])
check("closed: listed creator still can", amit.post("/corp/orgs", json={"name": f"Second {tag}"}).status_code == 201)
check("me tells the page whether to offer 'create'", stranger.get("/corp/me").get_json()["can_create"] is False and amit.get("/corp/me").get_json()["can_create"] is True)
os.environ["CORPORATE_ORG_CREATION"] = "open"

# --- 4. pausing access
check("can't pause the owner", lead.patch(f"{B}/members/{ua}", json={"status": "suspended"}).status_code == 403)
check("can't pause yourself", amit.patch(f"{B}/members/{ua}", json={"status": "suspended"}).status_code == 403)
dm = kunal.post(B + "/threads/direct", json={"user_id": ua}).get_json()["thread_id"]
amit.post(f"{B}/threads/{dm}/messages", json={"body": "hi Kunal"})
check("admin pauses Kunal", amit.patch(f"{B}/members/{uk}", json={"status": "suspended"}).status_code == 200)
check("paused: Kunal can't open the organisation", kunal.get(B).status_code == 403)
check("paused: his chats and unread counts are hidden", kunal.get(f"{B}/threads/{dm}/messages").status_code == 403
      and kunal.get("/corp/unread").get_json()["total"] == 0)
check("paused: listed as paused for the page", any(o["status"] == "suspended" for o in kunal.get("/corp/me").get_json()["orgs"]))
amit.patch(f"{B}/members/{uk}", json={"status": "active"})
check("restored: back in, history intact", kunal.get(f"{B}/threads/{dm}/messages").get_json()["messages"][-1]["body"] == "hi Kunal")

# --- 5. leavers' commitments
c1 = kunal.post(B + "/commitments", json={"text": "Migrate DB", "unit_id": dbt}).get_json()["id"]
c2 = kunal.post(B + "/commitments", json={"text": "Write runbook", "unit_id": dbt}).get_json()["id"]
kunal.post(f"{B}/commitments/{c2}/done", json={})
check("can't hand over to the person leaving", amit.delete(f"{B}/members/{uk}", json={"reassign_to": uk}).status_code == 400)
r = amit.delete(f"{B}/members/{uk}", json={"reassign_to": ul}).get_json()
check("removing Kunal hands his open commitment to the lead", r["handed_over"] == 1
      and q("SELECT owner_id FROM corp.commitments WHERE id = %s", (c1,))[0]["owner_id"] == ul)
check("finished commitments stay credited to him", q("SELECT owner_id FROM corp.commitments WHERE id = %s", (c2,))[0]["owner_id"] == uk)
check("hand-over is in the history", q("SELECT kind FROM corp.commitment_events WHERE commitment_id = %s ORDER BY id DESC LIMIT 1", (c1,))[0]["kind"] == "reassigned")
c3 = lead.post(B + "/commitments", json={"text": "Plan Q3", "unit_id": U["Technology"]}).get_json()["id"]
lead.post(f"{B}/leave")
row = q("SELECT owner_id, owner_name FROM corp.commitments WHERE id = %s", (c3,))[0]
check("someone who leaves: their work is flagged as needing an owner", row["owner_id"] is None and row["owner_name"] == f"lead{tag} (left)", row)

q("DELETE FROM corp.orgs WHERE name LIKE %s", (f"%{tag}",)); q("DELETE FROM users WHERE username LIKE %s", (f"%{tag}",))
app.release_raw_connection(conn)
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
