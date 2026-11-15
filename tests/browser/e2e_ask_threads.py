import os
"""Browser: Ask keeps conversations as threads -- New chat keeps the old one,
it can be reopened (with its follow-up), deleted; works at phone width."""
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
api(B + "/commitments", {"text": "Send the rollback plan to the DBA", "unit_id": tech, "due_date": "2026-11-20"}, ta)
api(B + "/decisions", {"unit_id": tech, "text": "Target PostgreSQL 17 for the migration"}, ta)

results = []
def check(n, c):
    results.append(bool(c)); print(("PASS " if c else "FAIL ") + n)

def answers(pg):
    return pg.locator(".ac-assistant .ac-actions")

with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    for name, vp in (("desktop", {"width": 1400, "height": 900}), ("phone", {"width": 390, "height": 844})):
        ctx = b.new_context(viewport=vp)
        ctx.add_init_script(f"localStorage.setItem('authToken','{ta}');localStorage.setItem('tl_mode','corporate');localStorage.setItem('tl_persona_skipped_{ua}','1');")
        pg = ctx.new_page(); errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(f"{BASE}/app/corp/ask")
        try: pg.get_by_role("dialog", name="Our Terms and Privacy Policy").get_by_role("button", name="Agree").click(timeout=3000)
        except Exception: pass
        expect(pg.get_by_role("heading", name="What do you want to know about your work?")).to_be_visible(timeout=15000)
        box = pg.get_by_label("Ask about your teams")
        box.fill(f"What did we decide about the database? ({name})"); box.press("Enter")
        expect(answers(pg)).to_have_count(1, timeout=120000)
        box.fill("and when is it due?"); box.press("Enter")
        expect(answers(pg)).to_have_count(2, timeout=120000)
        pg.get_by_role("button", name="New chat").first.click()
        expect(pg.get_by_role("heading", name="What do you want to know about your work?")).to_be_visible()
        box.fill(f"Who owes what this week? ({name})"); box.press("Enter")
        expect(answers(pg)).to_have_count(1, timeout=120000)
        if name == "phone":
            pg.get_by_role("button", name="Your chats").click()
        hist = pg.get_by_role("navigation", name="Your Ask conversations").last
        expect(hist.locator(".corp-ask-history-item", has_text=f"What did we decide about the database? ({name})")).to_be_visible(timeout=10000)
        check(f"{name}: the first chat is still listed after New chat", True)
        pg.screenshot(path=f"{OUT}/ask_threads_{name}.png")
        hist.locator(".corp-ask-history-item", has_text=f"What did we decide about the database? ({name})").click()
        expect(pg.locator(".ac-user-text", has_text="and when is it due?")).to_be_visible(timeout=15000)
        check(f"{name}: reopened chat shows its follow-up", pg.locator(".ac-user-bubble").count() == 2)
        pg.reload()
        expect(pg.locator(".ac-user-text", has_text="and when is it due?")).to_be_visible(timeout=15000)
        check(f"{name}: coming back reopens the same chat", True)
        if name == "phone":
            pg.get_by_role("button", name="Your chats").click()
            hist = pg.get_by_role("navigation", name="Your Ask conversations").last
        hist.get_by_role("button", name=f"Delete “Who owes what this week? ({name})”").click()
        pg.get_by_role("button", name="Delete", exact=True).click()
        expect(hist.locator(".corp-ask-history-item", has_text=f"Who owes what this week? ({name})")).to_have_count(0, timeout=10000)
        check(f"{name}: delete removes a chat", True)
        check(f"{name}: no sideways scroll", pg.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"))
        check(f"{name}: no page errors", not errs)
        if errs: print(errs)
    b.close()
print(f"\n{sum(results)}/{len(results)} passed")
