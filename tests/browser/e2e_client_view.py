import os
"""Browser: Sales/BU view -- ticket progress on the client deliverable, the
ticket's Supports field, stakeholder view, client update draft, Home digest."""
import json, sys, uuid, urllib.request
from datetime import date, timedelta
from playwright.sync_api import sync_playwright, expect

BASE = "http://127.0.0.1:5000"; t = uuid.uuid4().hex[:5]; OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "_shots")
def api(path, body=None, token=None, method=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 method=method or ("POST" if body is not None else "GET"),
                                 headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + token} if token else {})})
    return json.loads(urllib.request.urlopen(req).read() or b"{}")
def reg(u):
    r = api("/register", {"username": u, "email": f"{u}@example.test".lower(), "password": "secret123", "accepted_terms": True}); return r["token"], r["id"]
ts, us = reg(f"Priya{t}"); te, ue = reg(f"Dev{t}"); tb, ub = reg(f"Bu{t}")
d = api("/corp/orgs", {"name": f"NodexData {t}", "departments": ["Sales", "Engineering"]}, ts); org = d["org"]["id"]
U = {u["name"]: u["id"] for u in d["units"]}
B = f"/corp/orgs/{org}"
for tok, name, unit in ((te, f"Dev{t}", "Engineering"), (tb, f"Bu{t}", "Sales")):
    inv = api(f"{B}/invites", {"emails": f"{name}@example.test".lower(), "unit_id": U[unit]}, ts)["created"][0]["link"]
    api(f"/corp/invites/{inv.rsplit('/', 1)[1]}/accept", {}, tok)
api(f"{B}/units/{U['Sales']}/members/{us}", {"role": "manager"}, ts, "PUT")
pid = api(B + "/projects", {"name": "e-Gov portal", "client": "Ministry of IT"}, ts)["id"]
api(f"{B}/projects/{pid}/ticket-key", {"key": "GOV"}, ts, "PUT")
api(f"{B}/projects/{pid}/members/{ue}", {"role": "member"}, ts, "PUT")
api(f"{B}/projects/{pid}/members/{ub}", {"role": "stakeholder"}, ts, "PUT")
contact = api(f"{B}/projects/{pid}/contacts", {"name": "A. Verma", "position": "Joint Secretary", "email": "verma@gov.example"}, ts)["contacts"][0]["id"]
due = date.today() + timedelta(days=10)
promise = api(B + "/commitments", {"text": "Deliver citizen login module", "unit_id": U["Sales"], "due_date": due.isoformat(),
                                   "project_id": pid, "contact_id": contact}, ts)["id"]
t1 = api(B + "/tickets", {"project_id": pid, "title": "OTP login API", "supports_id": promise, "assignees": [ue]}, te)["ticket"]["id"]
t2 = api(B + "/tickets", {"project_id": pid, "title": "Aadhaar consent screen", "supports_id": promise}, te)["ticket"]["id"]
api(f"{B}/tickets/{t1}", {"status": "done"}, te, "PATCH")
api(f"{B}/tickets/{t2}", {"status": "blocked"}, te, "PATCH")

results = []
def check(n, c):
    results.append(bool(c)); print(("PASS " if c else "FAIL ") + n)
def page(b, token, uid, vp):
    c = b.new_context(viewport=vp)
    c.add_init_script(f"localStorage.setItem('authToken','{token}');localStorage.setItem('tl_mode','corporate');localStorage.setItem('tl_persona_skipped_{uid}','1');")
    pg = c.new_page(); pg.errs = []; pg.on("pageerror", lambda e: pg.errs.append(str(e))); return pg
def agree(pg):
    try: pg.get_by_role("dialog", name="Our Terms and Privacy Policy").get_by_role("button", name="Agree").click(timeout=3000)
    except Exception: pass

with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    s = page(b, ts, us, {"width": 1400, "height": 1000})
    s.goto(f"{BASE}/app/corp/projects/{pid}"); agree(s)
    expect(s.get_by_text("1 of 2 tickets done · 1 blocked").first).to_be_visible(timeout=15000)
    check("deliverable shows its ticket progress", True)
    check("deliverable shows it's at risk because GOV-2 is blocked", s.get_by_text("GOV-2 is blocked").count() >= 1)
    check("stakeholder role shown on the team", s.get_by_label(f"Role of Bu{t}").input_value() == "stakeholder")
    s.get_by_label("For whom").select_option(label="What was promised to A. Verma")
    s.get_by_role("button", name="Draft update").click()
    expect(s.get_by_label("Message")).not_to_be_empty(timeout=120000)
    body = s.get_by_label("Message").input_value()
    check("client update drafted", len(body) > 40)
    check("draft is client-safe (no ticket keys)", "GOV-" not in body)
    check("'Open in email' prepared to the contact", "verma%40gov.example" in (s.get_by_role("link", name="Open in email to verma@gov.example").get_attribute("href") or ""))
    s.screenshot(path=f"{OUT}/project_client_view.png", full_page=True)
    # ticket page: Supports field
    e = page(b, te, ue, {"width": 1400, "height": 950})
    e.goto(f"{BASE}/app/corp/tickets/GOV-2"); agree(e)
    expect(e.get_by_label("Supports")).to_be_visible(timeout=15000)
    check("ticket shows which client promise it supports", e.get_by_label("Supports").input_value() == str(promise))
    check("Blocked is a status", e.get_by_label("Status").input_value() == "blocked")
    # BU stakeholder: Home digest, read-only ticket
    u = page(b, tb, ub, {"width": 1400, "height": 1000})
    u.goto(f"{BASE}/app/corp"); agree(u)
    expect(u.get_by_role("heading", name="Your client projects this week")).to_be_visible(timeout=15000)
    check("BU head's Home shows client projects", u.get_by_role("link", name="e-Gov portal").count() >= 1)
    u.screenshot(path=f"{OUT}/bu_home_digest.png", full_page=True)
    u.goto(f"{BASE}/app/corp/tickets/GOV-2")
    expect(u.get_by_label("Status")).to_be_visible(timeout=15000)
    check("stakeholder can't change the ticket", u.get_by_label("Status").is_disabled())
    m = page(b, tb, ub, {"width": 390, "height": 844})
    m.goto(f"{BASE}/app/corp"); agree(m)
    expect(m.get_by_role("heading", name="Your client projects this week")).to_be_visible(timeout=15000)
    check("phone width: no sideways scroll", m.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"))
    errs = s.errs + e.errs + u.errs + m.errs
    check("no page errors", not errs)
    if errs: print(errs)
    b.close()
print(f"\n{sum(results)}/{len(results)} passed")
