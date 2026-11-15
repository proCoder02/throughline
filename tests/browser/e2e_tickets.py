import os
"""Browser test: project key, new ticket, key typed in chat links it, tag a
message by hand, the ticket page trace, privacy for others; screenshots."""
import json, sys, time, uuid, urllib.request
from playwright.sync_api import sync_playwright, expect

BASE = "http://127.0.0.1:5000"; t = uuid.uuid4().hex[:5]; OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "_shots")
def api(path, body=None, token=None, method=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 method=method or ("POST" if body is not None else "GET"),
                                 headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + token} if token else {})})
    return json.loads(urllib.request.urlopen(req).read() or b"{}")
def reg(u):
    r = api("/register", {"username": u, "email": f"{u}@example.test", "password": "secret123", "accepted_terms": True}); return r["token"], r["id"]
ta, ua = reg(f"Amit{t}"); tk, uk = reg(f"Khushi{t}"); tr, ur = reg(f"Ravi{t}")
d = api("/corp/orgs", {"name": f"NodexData {t}", "departments": ["Technology"]}, ta); org = d["org"]["id"]; tech = d["units"][0]["id"]
B = f"/corp/orgs/{org}"
for tok in (tk, tr):
    link = api(f"{B}/invites", {"emails": "", "unit_id": tech}, ta) if False else None
for name, tok, uid in (("k", tk, uk), ("r", tr, ur)):
    inv = api(f"{B}/invites", {"emails": f"{'Khushi' if name == 'k' else 'Ravi'}{t}@example.test".lower(), "unit_id": tech}, ta)["created"][0]["link"]
    api(f"/corp/invites/{inv.rsplit('/', 1)[1]}/accept", {}, tok)
pid = api(B + "/projects", {"name": "Payments revamp"}, ta)["id"]
api(f"{B}/projects/{pid}/members/{uk}", {"role": "member"}, ta, "PUT")
api(f"{B}/projects/{pid}/members/{ur}", {"role": "member"}, ta, "PUT")

def ctx_for(b, token, uid, vp):
    c = b.new_context(viewport=vp)
    c.add_init_script(f"localStorage.setItem('authToken','{token}');localStorage.setItem('tl_mode','corporate');localStorage.setItem('tl_persona_skipped_{uid}','1');")
    return c
def agree(pg):
    try: pg.get_by_role("dialog", name="Our Terms and Privacy Policy").get_by_role("button", name="Agree").click(timeout=3000)
    except Exception: pass

results = []
def check(n, c):
    results.append(bool(c)); print(("PASS " if c else "FAIL ") + n)

with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    a = ctx_for(b, ta, ua, {"width": 1400, "height": 950}).new_page(); errs = []; a.on("pageerror", lambda e: errs.append(str(e)))
    # project key on the website
    a.goto(f"{BASE}/app/corp/projects/{pid}"); agree(a)
    expect(a.get_by_role("heading", name="Tickets")).to_be_visible(timeout=15000)
    a.get_by_label("Ticket key").fill("pay"); a.get_by_role("button", name="Save").click()
    expect(a.get_by_text("PAY-1", exact=False).first).to_be_visible(timeout=10000)
    check("lead sets the ticket key PAY", True)
    # new ticket
    a.get_by_role("link", name="Tickets", exact=True).click()
    a.get_by_role("button", name="New ticket").click()
    a.get_by_label("Title").fill("Refund webhook times out")
    a.get_by_label("Description").fill("Gateway refund webhook times out after 30s for large payloads.")
    a.get_by_label("Priority").select_option("high")
    a.get_by_label("Add to assignees").select_option(label=f"Khushi{t}")
    a.get_by_role("button", name="Create ticket").click()
    expect(a.get_by_role("heading", name="Refund webhook times out")).to_be_visible(timeout=10000)
    check("ticket page opens at /tickets/PAY-1", a.url.endswith("/app/corp/tickets/PAY-1"))
    # key typed in team chat links it (as Khushi)
    k = ctx_for(b, tk, uk, {"width": 1400, "height": 950}).new_page(); k.on("pageerror", lambda e: errs.append(str(e)))
    k.goto(f"{BASE}/app/corp/messages"); agree(k)
    k.get_by_role("button", name="# Technology").click()
    k.get_by_label("Message").fill("PAY-1: bumping the webhook timeout to 60s fixes it on staging")
    k.get_by_role("button", name="Send").click()
    expect(k.locator(".corp-msg-body .tk-key-link", has_text="PAY-1")).to_be_visible(timeout=10000)
    check("key in chat shows as a link", True)
    # hand-tag a direct message (Khushi -> Amit) and leave it private
    dm = api(f"{B}/threads/direct", {"user_id": ua}, tk)["thread_id"]
    api(f"{B}/threads/{dm}/messages", {"body": "Between us: the vendor admitted their retry logic is broken."}, tk)
    k.goto(f"{BASE}/app/corp/messages?t={dm}")
    msg = k.locator(".corp-msg", has_text="vendor admitted")
    msg.hover(); msg.get_by_role("button", name="Tag this message to a ticket").click()
    dlg = k.get_by_role("dialog", name="Tag this message to a ticket")
    dlg.get_by_label("Find a ticket").fill("refund")
    dlg.get_by_role("option").first.get_by_role("button").click()
    dlg.get_by_role("button", name="Tag to PAY-1").click()
    expect(k.get_by_text("Tagged to PAY-1")).to_be_visible(timeout=10000)
    check("tag a direct message from the message", True)
    # ticket page: timeline shows both, with a comment
    a.reload()
    expect(a.locator(".tk-link", has_text="Team chat")).to_be_visible(timeout=10000)
    expect(a.locator(".tk-link", has_text="Direct message")).to_be_visible()
    check("ticket timeline shows the chat message and the DM", True)
    a.get_by_label("Comment").fill("Let's ship the 60s timeout and chase the vendor. See PAY-1 notes.")
    a.get_by_role("button", name="Comment").click()
    expect(a.locator(".tk-comment", has_text="chase the vendor")).to_be_visible(timeout=10000)
    a.get_by_label("Status").select_option("in_progress")
    expect(a.get_by_text("moved it from To do to In progress")).to_be_visible(timeout=10000)
    check("comment and status change on the timeline", True)
    time.sleep(1)
    a.screenshot(path=f"{OUT}/ticket_page.png", full_page=True)
    # Ravi: on the ticket, not in the DM -> no DM content
    r = ctx_for(b, tr, ur, {"width": 1400, "height": 950}).new_page(); r.on("pageerror", lambda e: errs.append(str(e)))
    r.goto(f"{BASE}/app/corp/tickets/PAY-1"); agree(r)
    dm_link = r.locator(".tk-link", has_text="Direct message")
    expect(dm_link).to_be_visible(timeout=15000)
    check("someone not in the DM sees no 'Show the message'", dm_link.get_by_role("button", name="Show the message").count() == 0)
    check("...and is told it's private", dm_link.get_by_text("Private to the people who were in it").count() == 1)
    r.screenshot(path=f"{OUT}/ticket_other_view.png", full_page=True)
    # list page
    a.goto(f"{BASE}/app/corp/tickets?scope=all")
    expect(a.locator(".tk-row", has_text="PAY-1")).to_be_visible(timeout=10000)
    a.screenshot(path=f"{OUT}/tickets_list.png")
    check("list shows PAY-1", True)
    # phone width
    m = ctx_for(b, ta, ua, {"width": 390, "height": 844}).new_page(); m.on("pageerror", lambda e: errs.append(str(e)))
    m.goto(f"{BASE}/app/corp/tickets/PAY-1"); agree(m)
    expect(m.get_by_role("heading", name="Refund webhook times out")).to_be_visible(timeout=15000)
    check("phone width: no sideways scroll", m.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"))
    m.screenshot(path=f"{OUT}/ticket_phone.png", full_page=True)
    check("no page errors", not errs)
    if errs: print(errs)
    b.close()
print(f"\n{sum(results)}/{len(results)} passed")
