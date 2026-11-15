"""Browser check of work memory on the live dev server (real AI: 3 calls).
Two people in one team: chat both ways, a meeting from pasted notes ->
decisions/commitments, confirm + history, decisions, common ground, Ask, brief."""
import json, re, sys, uuid, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

BASE = "http://127.0.0.1:5000"; OUT = Path(__file__).parent / "_shots"; tag = uuid.uuid4().hex[:6]
A, K = f"amit{tag}", f"khushi{tag}"
results, errors = [], []
def check(n, c, x=""):
    results.append(bool(c)); print(("PASS " if c else "FAIL ") + n + (f"  -- {x}" if x and not c else ""), flush=True)
def api(path, body=None, token=None, method=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, method=method or ("POST" if body is not None else "GET"),
                                 headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + token} if token else {})})
    return json.loads(urllib.request.urlopen(req).read() or b"{}")
def reg(u):
    r = api("/register", {"username": u, "email": f"{u}@example.test", "password": "secret123", "accepted_terms": True})
    return r["token"], r["id"]
ta, ua = reg(A); tk, uk = reg(K)
d = api("/corp/orgs", {"name": f"Nodex {tag}", "departments": ["Technology"]}, ta)
org = d["org"]["id"]; tech = d["units"][0]["id"]
db = api(f"/corp/orgs/{org}/units", {"name": "Database Team", "kind": "team", "parent_id": tech}, ta)["id"]
api(f"/corp/orgs/{org}/units/{db}/members/{ua}", {"role": "manager"}, ta, "PUT")
import os; sys.path.insert(0, r"C:\Users\P3INW57\Desktop\speech2text")
# Khushi joins through a real invitation link (made via API, accepted via API).
link = api(f"/corp/orgs/{org}/invites", {"emails": f"{K}@example.test", "unit_id": db, "unit_role": "member"}, ta)["created"][0]["link"]
api(f"/corp/invites/{link.rsplit('/', 1)[1]}/accept", {}, tk)

with sync_playwright() as p:
    br = p.chromium.launch(channel="chrome", headless=True)
    def page_for(token, name):
        ctx = br.new_context(viewport={"width": 1400, "height": 900})
        ctx.add_init_script(f"localStorage.setItem('authToken','{token}');localStorage.setItem('tl_mode','corporate');"
                            f"localStorage.setItem('tl_persona_skipped_{ua if name=='A' else uk}','1');")
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errors.append(f"{name}: {e}"))
        pg.on("console", lambda m: m.type == "error" and "Failed to load resource" not in m.text and "WebSocket" not in m.text and "frame header" not in m.text and errors.append(f"{name} console: {m.text[:150]}"))
        return pg
    a, k = page_for(ta, "A"), page_for(tk, "K")
    for pg in (a, k):
        pg.goto(BASE + "/app/corp")
        for dlg in ("Our Terms and Privacy Policy",):
            try:
                pg.get_by_role("dialog", name=dlg).get_by_role("button", name=re.compile("Agree")).click(timeout=4000)
            except Exception:
                pass

    # chat: Amit messages Khushi from People
    a.get_by_role("link", name="People").click()
    a.locator(".corp-person", has_text=K).get_by_role("button", name="Message").click()
    a.wait_for_url("**/messages?t=*")
    a.get_by_label("Message", exact=True).fill("Hi Khushi, can you prepare the migration scripts by Friday?")
    a.get_by_role("button", name="Send", exact=True).click()
    expect(a.locator(".corp-msg.mine")).to_have_count(1)
    check("Amit sends a direct message from People", True)
    k.goto(BASE + "/app/corp/messages")
    row = k.get_by_role("button", name=re.compile(A))
    expect(row.locator(".corp-unread")).to_have_text("1", timeout=20000)
    check("Khushi sees it unread", True)
    row.click()
    expect(k.locator(".corp-msg").first).to_contain_text("migration scripts")
    k.get_by_label("Message", exact=True).fill("Yes, I'll send them by Friday.")
    k.get_by_role("button", name="Send", exact=True).click()
    expect(a.locator(".corp-msg:not(.mine)")).to_contain_text("by Friday", timeout=10000)
    check("reply arrives on Amit's screen without reload", True)
    a.screenshot(path=str(OUT / "work_chat.png"))

    # meeting from pasted notes (real AI)
    a.get_by_role("link", name="Meetings").click()
    a.get_by_role("button", name="Paste notes").click()
    a.get_by_label("Team").select_option(str(db))
    a.get_by_label("Title").fill("Database migration")
    a.get_by_label("Notes or transcript").fill(
        f"{A}: We decided to migrate to PostgreSQL 17. Staging validation is required before production.\n"
        f"{K}: I will prepare the migration scripts by 14 November 2030 for {A}.\n"
        f"{A}: The API rate limit is blocking our load test, that's a blocker.")
    a.get_by_role("button", name="Add meeting").click()
    a.wait_for_url("**/meetings/*")
    expect(a.get_by_role("heading", name="Summary")).to_be_visible(timeout=120000)
    check("meeting processed by the real AI", True)
    dec = a.locator("section", has=a.get_by_role("heading", name=re.compile("^Decisions"))).locator(".corp-person")
    com = a.locator(".corp-commit")
    check("decision extracted", dec.count() >= 1, a.locator("main").inner_text()[:400])
    check("commitment extracted with Khushi as owner", com.count() >= 1 and K in com.first.inner_text(), com.first.inner_text() if com.count() else "")
    a.screenshot(path=str(OUT / "work_meeting.png"), full_page=True)

    # Khushi confirms it in Commitments; history shows
    k.goto(BASE + "/app/corp/commitments")
    first = k.locator(".corp-commit").first
    expect(first).to_be_visible(timeout=10000)
    first.get_by_role("button", name="Confirm").click()
    expect(first.locator(".corp-status.open")).to_be_visible()
    first.locator(".act-toggle").click()
    check("owner confirms; history recorded", "confirm" in first.locator(".act-thread").inner_text(timeout=10000).lower())
    k.screenshot(path=str(OUT / "work_commitments.png"), full_page=True)

    # decisions: new version keeps the old one
    a.get_by_role("link", name="Decisions").click()
    a.locator(".corp-tree-row").first.click()
    a.get_by_label("New decision").fill("Migrate to PostgreSQL 17 after staging sign-off by the DBA")
    a.get_by_role("button", name="Save new version").click()
    expect(a.locator(".corp-versions li")).to_have_count(2)
    check("decision changed, earlier version kept", True)

    # common ground in the team thread (real AI)
    a.get_by_role("link", name="Messages").click()
    a.get_by_role("button", name=re.compile("# Database Team")).click()
    expect(a.get_by_label("Message", exact=True)).to_be_visible(timeout=10000)
    a.screenshot(path=str(OUT / "work_team_thread.png"))
    for t in ("I want to migrate this sprint.", "I need a tested rollback before production.", "Release can't slip past 20 November."):
        a.get_by_label("Message", exact=True).fill(t); a.get_by_role("button", name="Send", exact=True).click()
        expect(a.locator(".corp-msg.mine", has_text=t)).to_be_visible(timeout=10000)
    a.get_by_role("button", name="Find common ground").click()
    expect(a.locator(".corp-cg")).to_be_visible(timeout=120000)
    check("common ground posted in the thread", "Common ground" in a.locator(".corp-cg").inner_text())
    a.screenshot(path=str(OUT / "work_common_ground.png"))

    # ask (real AI)
    a.get_by_role("link", name="Ask").click()
    a.get_by_label("Ask about your teams").fill("What did we decide about the database?")
    a.get_by_role("button", name="Send question").click()
    expect(a.locator(".ac-assistant .ac-actions").first).to_be_visible(timeout=120000)
    check("ask answers with sources", a.locator(".corp-ask-source").count() >= 1, a.locator(".ac-assistant-body").first.inner_text()[:300])
    a.screenshot(path=str(OUT / "work_ask.png"))

    # brief
    a.get_by_role("link", name="Home").click()
    expect(a.get_by_role("heading", name="Waiting on others")).to_be_visible()
    expect(a.locator("section", has=a.get_by_role("heading", name="Waiting on others")).locator(".home-item")).to_have_count(1, timeout=10000)
    check("home shows what's owed to Amit", "migration" in a.locator("section", has=a.get_by_role("heading", name="Waiting on others")).inner_text().lower())
    a.screenshot(path=str(OUT / "work_overview.png"), full_page=True)
    br.close()

for e in errors: print("ERROR", e)
print(f"\n{sum(results)}/{len(results)} passed, {len(errors)} page errors")
print("CLEANUP", org, A, K)
