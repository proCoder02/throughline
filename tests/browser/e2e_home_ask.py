import re
import os
"""Browser: asking from Home opens Ask in a new chat with the answer, the chat
is kept, Home's quick actions still work, and nothing errors (desktop + phone)."""
import json, sys, uuid, urllib.request
from playwright.sync_api import sync_playwright, expect

BASE = "http://127.0.0.1:5000"; t = uuid.uuid4().hex[:5]; OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "_shots")
def api(path, body=None, token=None, method=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 method=method or ("POST" if body is not None else "GET"),
                                 headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + token} if token else {})})
    return json.loads(urllib.request.urlopen(req).read() or b"{}")
r = api("/register", {"username": f"Amit{t}", "email": f"amit{t}@example.test", "password": "secret123", "accepted_terms": True})
ta, ua = r["token"], r["id"]
d = api("/corp/orgs", {"name": f"NodexData {t}", "departments": ["Technology"]}, ta); org = d["org"]["id"]; tech = d["units"][0]["id"]
B = f"/corp/orgs/{org}"
api(f"{B}/units/{tech}/members/{ua}", {"role": "manager"}, ta, "PUT")
api(B + "/decisions", {"unit_id": tech, "text": "Target PostgreSQL 17 for the migration"}, ta)

results = []
def check(n, c):
    results.append(bool(c)); print(("PASS " if c else "FAIL ") + n)

with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    for name, vp in (("desktop", {"width": 1400, "height": 950}), ("phone", {"width": 390, "height": 844})):
        ctx = b.new_context(viewport=vp)
        ctx.add_init_script(f"localStorage.setItem('authToken','{ta}');localStorage.setItem('tl_mode','corporate');localStorage.setItem('tl_persona_skipped_{ua}','1');")
        pg = ctx.new_page(); errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(f"{BASE}/app/corp")
        try: pg.get_by_role("dialog", name="Our Terms and Privacy Policy").get_by_role("button", name="Agree").click(timeout=3000)
        except Exception: pass
        box = pg.get_by_label("Ask about your work")
        expect(box).to_be_visible(timeout=15000)
        check(f"{name}: Home has the ask box", True)
        check(f"{name}: 'Ask' quick action replaced by Tickets", pg.get_by_role("button", name="Tickets").count() == 1)
        if name == "desktop":
            pg.screenshot(path=f"{OUT}/home_ask_desktop.png")
        q = f"What did we decide about the database? ({name})"
        box.fill(q); box.press("Enter")
        expect(pg.get_by_label("Ask about your teams")).to_be_visible(timeout=15000)
        expect(pg).to_have_url(re.compile(r".*/app/corp/ask$"), timeout=10000)
        check(f"{name}: lands on Ask", True)
        expect(pg.locator(".ac-user-text", has_text=q)).to_be_visible(timeout=15000)
        expect(pg.locator(".ac-assistant .ac-actions")).to_have_count(1, timeout=120000)
        check(f"{name}: question asked and answered in a new chat", pg.locator(".ac-user-bubble").count() == 1)
        if name == "phone":
            pg.get_by_role("button", name="Your chats").click()
        hist = pg.get_by_role("navigation", name="Your Ask conversations").last
        expect(hist.locator(".corp-ask-history-item", has_text=q)).to_be_visible(timeout=10000)
        check(f"{name}: the chat is saved in Chats", True)
        pg.reload()
        expect(pg.locator(".ac-user-text", has_text=q)).to_be_visible(timeout=15000)
        check(f"{name}: reload doesn't ask again (one question, same chat)", pg.locator(".ac-user-bubble").count() == 1)
        pg.goto(f"{BASE}/app/corp")
        pg.get_by_role("button", name="Tickets").click()
        expect(pg.get_by_role("heading", name="Tickets")).to_be_visible(timeout=10000)
        check(f"{name}: Tickets quick action opens Tickets", True)
        check(f"{name}: no sideways scroll", pg.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"))
        check(f"{name}: no page errors", not errs)
        if errs: print(errs)
    b.close()
print(f"\n{sum(results)}/{len(results)} passed")
