"""Server checks for corporate/: permission rules (pure), the org / unit /
member / invite API end to end with several users, and that Personal
(settings, conversations) is unaffected. No real email is sent: the SMTP
sender is replaced with a recorder. Cleans up its own users and orgs."""
import os, sys, uuid, time
sys.path.insert(0, os.getcwd())
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["CORPORATE_ENABLED"] = "true"
import app  # noqa: E402
import psycopg2.extras
from datetime import datetime, timedelta, timezone
from corporate import roles

sent = []
app._smtp_send = lambda to, subject, body, **kw: sent.append((to, subject, body))

results = []
def check(n, c, extra=""):
    results.append(bool(c)); print(("PASS " if c else "FAIL ") + n + (f"  -- {extra}" if extra != "" and not c else ""))

# ---------------------------------------------------------------- roles (pure)
units = {1: {"parent_id": None, "kind": "department"}, 2: {"parent_id": 1, "kind": "team"},
         3: {"parent_id": 2, "kind": "team"}, 4: {"parent_id": None, "kind": "department"}}
head = roles.OrgContext(user_id=9, org_id=1, role="member", units=units, my_units={1: "manager"})
check("roles: head manages people in own dept", roles.can_manage_people(head, 1))
check("roles: head can't rename own dept", not roles.can_edit_unit(head, 1))
check("roles: head edits teams below, any depth", roles.can_edit_unit(head, 2) and roles.can_edit_unit(head, 3))
check("roles: head has no say in another dept", not roles.can_manage_people(head, 4) and not roles.can_edit_unit(head, 4))
check("roles: only admins add top-level departments", not roles.can_create_under(head, None))
check("roles: department can't go inside a team", not roles.valid_parent("department", units[2]) and roles.valid_parent("team", units[2]))
admin = roles.OrgContext(user_id=1, org_id=1, role="admin", units=units)
check("roles: admin can't invite admins (owner only)", not roles.can_invite(admin, "admin", None, None))
check("roles: owner can't be removed", not roles.can_remove_member(roles.OrgContext(1, 1, "owner", units), 2, "owner"))

# ---------------------------------------------------------------- API
tag = uuid.uuid4().hex[:6]
conn = app.get_raw_connection()
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
def q(sql, args=()):
    cur.execute(sql, args)
    out = cur.fetchall() if cur.description else None
    conn.commit()
    return out

def user(name):
    c = app.app.test_client()
    r = c.post("/register", json={"username": f"cp{tag}{name}", "email": f"cp{tag}{name}@example.test", "password": "secret123"})
    assert r.status_code == 200, r.get_json()
    return c, r.get_json()["id"], f"cp{tag}{name}@example.test"

anon = app.app.test_client()
check("corp needs sign-in", anon.get("/corp/me").status_code == 401)

owner, uo, _ = user("own")
head_c, uh, eh = user("head")
dev_c, ud, ed = user("dev")
out_c, ux, ex = user("out")

r = owner.get("/corp/me").get_json()
check("new user has no organisations", r["enabled"] is True and r["orgs"] == [], r)
r = owner.post("/corp/orgs", json={"name": f"  ABC {tag}  Tech ", "departments": ["Engineering", "Finance", "engineering"]})
d = r.get_json()
org = d["org"]["id"]
check("create org with departments (duplicate name skipped, spaces cleaned)",
      r.status_code == 201 and d["org"]["name"] == f"ABC {tag} Tech" and sorted(u["name"] for u in d["units"]) == ["Engineering", "Finance"], d)
check("creator is owner", d["me"]["role"] == "owner" and d["me"]["org_owner"])
eng = next(u["id"] for u in d["units"] if u["name"] == "Engineering")
fin = next(u["id"] for u in d["units"] if u["name"] == "Finance")
check("empty org name refused", owner.post("/corp/orgs", json={"name": "   "}).status_code == 400)
check("outsider gets 404 for the org (no probing)", out_c.get(f"/corp/orgs/{org}").status_code == 404)

r = owner.post(f"/corp/orgs/{org}/units", json={"name": "Backend", "kind": "team", "parent_id": eng})
backend = r.get_json()["id"]
check("team added under department", r.status_code == 201)
check("same name twice under one parent refused", owner.post(f"/corp/orgs/{org}/units", json={"name": "backend", "kind": "team", "parent_id": eng}).status_code == 409)
check("same name under another parent is fine", owner.post(f"/corp/orgs/{org}/units", json={"name": "Backend", "kind": "team", "parent_id": fin}).status_code == 201)
check("department inside a team refused", owner.post(f"/corp/orgs/{org}/units", json={"name": "X", "kind": "department", "parent_id": backend}).status_code == 400)
check("unit of another org refused", owner.post(f"/corp/orgs/{org}/units", json={"name": "X", "kind": "team", "parent_id": 999999999}).status_code == 404)

# invitations
r = owner.post(f"/corp/orgs/{org}/invites", json={"emails": f"{eh.upper()}, not-an-email", "unit_id": eng, "unit_role": "manager"})
inv = r.get_json()
check("invite created, bad address reported, email case ignored",
      r.status_code == 201 and len(inv["created"]) == 1 and inv["created"][0]["email"] == eh and inv["invalid"] == ["not-an-email"], inv)
head_link = inv["created"][0]["link"]
head_token = head_link.rsplit("/", 1)[1]
check("link points at the web join page", "/app/corp/join/" in head_link)
time.sleep(0.5)
check("invitation emailed (stubbed) with the link", any(t == eh and head_link in b for t, s, b in sent), sent)
check("token not stored in plain text", not q("SELECT 1 FROM corp.invites WHERE token_hash = %s", (head_token,)))

p = out_c.get(f"/corp/invites/{head_token}").get_json()
check("preview for the wrong account says so, email masked", p["email_matches"] is False and p["email_masked"].startswith(eh[0]) and "***@" in p["email_masked"], p)
check("wrong account can't accept a forwarded link", out_c.post(f"/corp/invites/{head_token}/accept").status_code == 403)
check("right account accepts", head_c.post(f"/corp/invites/{head_token}/accept").get_json().get("org_id") == org)
check("link can't be used twice", head_c.post(f"/corp/invites/{head_token}/accept").status_code == 410)
hv = head_c.get(f"/corp/orgs/{org}").get_json()
check("head is a member and Engineering's manager", hv["me"]["role"] == "member" and next(u for u in hv["units"] if u["id"] == eng)["my_role"] == "manager")

# head's powers
check("head can't rename own department", head_c.patch(f"/corp/orgs/{org}/units/{eng}", json={"name": "Eng"}).status_code == 403)
check("head can't add top-level department", head_c.post(f"/corp/orgs/{org}/units", json={"name": "Ops", "kind": "department"}).status_code == 403)
r = head_c.post(f"/corp/orgs/{org}/units", json={"name": "Frontend", "kind": "team", "parent_id": eng})
frontend = r.get_json().get("id")
check("head adds a team in own department", r.status_code == 201)
check("head can't touch Finance", head_c.post(f"/corp/orgs/{org}/units", json={"name": "T", "kind": "team", "parent_id": fin}).status_code == 403)
check("head renames a team below", head_c.patch(f"/corp/orgs/{org}/units/{frontend}", json={"name": "Web"}).status_code == 200)
check("head can't invite to org only", head_c.post(f"/corp/orgs/{org}/invites", json={"emails": ed}).status_code == 403)
check("head can't invite admins", head_c.post(f"/corp/orgs/{org}/invites", json={"emails": ed, "role": "admin", "unit_id": backend}).status_code == 403)
r = head_c.post(f"/corp/orgs/{org}/invites", json={"emails": ed, "unit_id": backend, "unit_role": "member"})
dev_token = r.get_json()["created"][0]["link"].rsplit("/", 1)[1]
check("head invites a developer into a team below", r.status_code == 201)
check("developer joins", dev_c.post(f"/corp/invites/{dev_token}/accept").status_code == 200)
check("head appoints the developer as Backend lead", head_c.put(f"/corp/orgs/{org}/units/{backend}/members/{ud}", json={"role": "manager"}).status_code == 200)
check("lead can't appoint another lead in own team", dev_c.put(f"/corp/orgs/{org}/units/{backend}/members/{uh}", json={"role": "manager"}).status_code == 403)
check("lead can add a member to own team", dev_c.put(f"/corp/orgs/{org}/units/{backend}/members/{uh}", json={"role": "member"}).status_code == 200)
check("lead can't demote themselves (manager change is for the level above)",
      dev_c.put(f"/corp/orgs/{org}/units/{backend}/members/{ud}", json={"role": "member"}).status_code == 403)
check("non-members can't be put in a unit", owner.put(f"/corp/orgs/{org}/units/{eng}/members/{ux}", json={"role": "member"}).status_code == 400)
check("member sees structure", dev_c.get(f"/corp/orgs/{org}").status_code == 200)
check("member can't rename org", dev_c.patch(f"/corp/orgs/{org}", json={"name": "Nope"}).status_code == 403)

m = owner.get(f"/corp/orgs/{org}/members").get_json()["members"]
dev_row = next(x for x in m if x["user_id"] == ud)
check("directory lists units and roles per person", {u["name"] for u in dev_row["units"]} == {"Backend"} and dev_row["units"][0]["role"] == "manager", dev_row)
check("member edits own title", dev_c.patch(f"/corp/orgs/{org}/members/{ud}", json={"title": "Senior Engineer"}).status_code == 200)
check("member can't edit someone else's title", dev_c.patch(f"/corp/orgs/{org}/members/{uh}", json={"title": "x"}).status_code == 403)
check("owner makes head an admin", owner.patch(f"/corp/orgs/{org}/members/{uh}", json={"role": "admin"}).status_code == 200)
check("admin can't promote others", head_c.patch(f"/corp/orgs/{org}/members/{ud}", json={"role": "admin"}).status_code == 403)
check("nobody becomes a second owner here", owner.patch(f"/corp/orgs/{org}/members/{ud}", json={"role": "owner"}).status_code == 403)

# invite states
r = owner.post(f"/corp/orgs/{org}/invites", json={"emails": [ex]}).get_json()
old_token = r["created"][0]["link"].rsplit("/", 1)[1]
r = owner.post(f"/corp/orgs/{org}/invites", json={"emails": [ex]}).get_json()
new_token = r["created"][0]["link"].rsplit("/", 1)[1]
check("re-inviting replaces the old link", out_c.get(f"/corp/invites/{old_token}").get_json()["status"] == "revoked")
q("UPDATE corp.invites SET expires_at = %s WHERE id = %s", (datetime.now(timezone.utc) - timedelta(days=1), r["created"][0]["id"]))
check("expired link refused", out_c.post(f"/corp/invites/{new_token}/accept").status_code == 410)
r = owner.post(f"/corp/orgs/{org}/invites", json={"emails": [ex, eh]}).get_json()
check("existing members aren't re-invited", r["already_members"] == [eh] and len(r["created"]) == 1, r)
iid = r["created"][0]["id"]
check("dev can't revoke someone else's invite", dev_c.delete(f"/corp/orgs/{org}/invites/{iid}").status_code == 403)
check("owner revokes", owner.delete(f"/corp/orgs/{org}/invites/{iid}").status_code == 200)
check("garbage token is 404", out_c.get("/corp/invites/nope").status_code == 404)

# removal, leave, delete
check("admin can't remove the owner", head_c.delete(f"/corp/orgs/{org}/members/{uo}").status_code == 403)
check("owner can't leave", owner.post(f"/corp/orgs/{org}/leave").status_code == 400)
check("owner removes the developer", owner.delete(f"/corp/orgs/{org}/members/{ud}").status_code == 200)
check("removed person is out of every unit too",
      not q("SELECT 1 FROM corp.unit_members um JOIN corp.units u ON u.id = um.unit_id WHERE u.org_id = %s AND um.user_id = %s", (org, ud)))
check("removed person now gets 404", dev_c.get(f"/corp/orgs/{org}").status_code == 404)
check("head leaves", head_c.post(f"/corp/orgs/{org}/leave").status_code == 200)
r = owner.delete(f"/corp/orgs/{org}/units/{eng}").get_json()
check("deleting a department deletes its teams", not any(u["id"] in (eng, backend, frontend) for u in r["units"]))
check("delete org without the name refused", owner.delete(f"/corp/orgs/{org}", json={"confirm_name": "abc"}).status_code == 400)
check("delete org with its name", owner.delete(f"/corp/orgs/{org}", json={"confirm_name": f"abc {tag} tech"}).status_code == 200)
check("org and everything in it gone",
      not q("SELECT 1 FROM corp.orgs WHERE id = %s", (org,)) and not q("SELECT 1 FROM corp.units WHERE org_id = %s", (org,))
      and not q("SELECT 1 FROM corp.invites WHERE org_id = %s", (org,)))

# ---------------------------------------------------------------- Personal unaffected + mode via category
check("settings personalization -> office (Corporate)", owner.post("/settings", json={"personalization": "office"}).status_code == 200
      and owner.get("/settings").get_json()["personalization"] == "office")
check("and back to study (Personal)", owner.post("/settings", json={"personalization": "study"}).status_code == 200)
check("conversations still load", owner.get("/conversations").status_code == 200)
check("tasks still load", owner.get("/tasks").status_code == 200)
check("SPA serves /app/corp and /app/choose", anon.get("/app/corp").status_code == 200 and anon.get("/app/choose").status_code == 200)
tables = q("SELECT table_schema, table_name FROM information_schema.tables WHERE table_name IN ('orgs','members','units','unit_members','invites')")
check("corporate tables live only in the corp schema", all(t["table_schema"] == "corp" for t in tables), tables)

# account deletion cascades out of organisations
c2, u2, _ = user("del")
oid = c2.post("/corp/orgs", json={"name": f"Del {tag}"}).get_json()["org"]["id"]
q("DELETE FROM users WHERE id = %s", (u2,))
check("deleting a user removes their memberships", not q("SELECT 1 FROM corp.members WHERE user_id = %s", (u2,)))
q("DELETE FROM corp.orgs WHERE id = %s", (oid,))

# ---------------------------------------------------------------- cleanup
q("DELETE FROM corp.orgs WHERE name LIKE %s", (f"%{tag}%",))
q("DELETE FROM users WHERE username LIKE %s", (f"cp{tag}%",))
app.release_raw_connection(conn)
print(f"\n{sum(results)}/{len(results)} passed; emails captured (not sent): {len(sent)}")
sys.exit(0 if all(results) else 1)
