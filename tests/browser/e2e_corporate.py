"""Browser check of Personal/Corporate: the one-time chooser, organisation
setup, structure, invitations (wrong account + right account), switching
both ways, and the phone layout. Fails on any page error."""
import os, re, sys, uuid
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

BASE = "http://127.0.0.1:5000"
OUT = Path(__file__).parent / "_shots"
results, errors = [], []
STEP = {"n": "start"}
def check(name, cond, extra=""):
    STEP["n"] = name
    results.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + (f"  -- {extra}" if extra and not cond else ""), flush=True)

tag = uuid.uuid4().hex[:6]
A, B, C = f"ca{tag}", f"cb{tag}", f"cc{tag}"
ORG = f"Acme {tag}"
NOISE = ("Invalid frame header", "WebSocket")  # dev-server websocket noise, seen before this change too

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    def new_page(name, **kw):
        ctx = browser.new_context(viewport=kw.pop("viewport", {"width": 1400, "height": 900}), **kw)
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(f"{name}/{STEP['n']} pageerror: {e}"))
        page.on("console", lambda m: m.type == "error" and "Failed to load resource" not in m.text
                and not any(n in m.text for n in NOISE) and errors.append(f"{name}/{STEP['n']} console: {m.text[:160]}"))
        return ctx, page

    def sign_up(page, user, start="/app"):
        page.goto(BASE + start)
        page.get_by_role("button", name="Create an account").click()
        page.get_by_label("Username").fill(user)
        page.get_by_label("Email").fill(f"{user}@example.test")
        page.get_by_label("Password", exact=True).fill("secret123")
        page.get_by_role("checkbox", name="I agree").check()
        page.get_by_role("button", name="Create account").click()
        expect(page.get_by_role("dialog", name="Personalize Throughline")).to_be_visible(timeout=15000)
        page.get_by_role("button", name="Skip for now").click()

    # ------------------------------------------------------------ chooser
    ctxA, a = new_page("A")
    sign_up(a, A)
    expect(a.get_by_role("heading", name=re.compile("How do you want to use Throughline"))).to_be_visible(timeout=15000)
    check("first visit on a device shows the chooser", a.url.endswith("/app/choose"), a.url)
    check("chooser offers Personal and Corporate",
          a.get_by_role("button", name=re.compile("^Personal")).is_visible() and a.get_by_role("button", name=re.compile("^Corporate")).is_visible())
    check("no Personal rail on the chooser", a.locator(".icon-rail").count() == 0)
    a.screenshot(path=str(OUT / "corp_chooser.png"))

    a.get_by_role("button", name=re.compile("^Corporate")).click()
    expect(a.get_by_role("heading", name="Joining your company?")).to_be_visible(timeout=15000)
    check("no invitation: told to ask the admin, shown own email", a.locator(".corp-email").inner_text().endswith("@example.test"))
    a.screenshot(path=str(OUT / "corp_welcome.png"))
    a.get_by_role("button", name="Set up a new organisation").click()
    expect(a.get_by_role("heading", name="Set up your organisation")).to_be_visible(timeout=15000)
    check("Corporate with no organisation opens setup", a.url.rstrip("/").endswith("/app/corp"), a.url)
    check("choosing Corporate saved personalization = office",
          a.evaluate("fetch('/settings',{headers:{Authorization:'Bearer '+localStorage.authToken}}).then(r=>r.json()).then(d=>d.personalization)") == "office")

    # ------------------------------------------------------------ setup
    a.get_by_label("Organisation name").fill(ORG)
    a.get_by_role("button", name="Engineering").click()
    a.get_by_role("button", name="Finance").click()
    a.get_by_placeholder("Another department").fill("Legal & Compliance")
    a.get_by_role("button", name="Add department", exact=True).click()
    a.screenshot(path=str(OUT / "corp_setup.png"))
    a.get_by_role("button", name="Create organisation").click()
    a.wait_for_url("**/app/corp/structure", timeout=15000)
    tree = a.get_by_role("tree", name="Departments and teams")
    expect(tree.get_by_role("button", name=re.compile("Engineering"))).to_be_visible()
    check("structure shows the three departments",
          all(tree.get_by_role("button", name=re.compile(n)).count() == 1 for n in ["Engineering", "Finance", "Legal & Compliance"]))

    tree.get_by_role("button", name=re.compile("Engineering")).click()
    expect(a.get_by_role("heading", name="Engineering", level=2)).to_be_visible()
    check("selected unit is in the URL", "unit=" in a.url)
    a.get_by_role("button", name=re.compile("Add a team or sub-department")).click()
    a.get_by_label("New team name").fill("Backend")
    a.get_by_role("button", name="Add team", exact=True).click()
    expect(a.get_by_role("heading", name="Backend", level=2)).to_be_visible(timeout=10000)
    check("team added under Engineering and opened", a.locator(".corp-eyebrow").inner_text().startswith("Team in"))
    a.screenshot(path=str(OUT / "corp_structure.png"))

    # ------------------------------------------------------------ invite
    tree.get_by_role("button", name=re.compile("Engineering")).click()
    a.get_by_role("button", name="Not in the organisation yet? Invite them").click()
    a.wait_for_url("**/app/corp/people?invite=*")
    check("invite form preselects the unit", a.get_by_label("Department or team").evaluate("e => e.options[e.selectedIndex].text") == "Engineering")
    a.get_by_label("Email addresses").fill(f"{B}@example.test, {C}x@nowhere.test, not-an-email")
    a.get_by_label("Their role there").select_option("manager")
    a.get_by_role("button", name="Send invitations").click()
    expect(a.locator(".corp-invite-result")).to_contain_text("2 invitations", timeout=10000)
    check("bad address reported", "not-an-email" in a.locator(".corp-invite-result").inner_text())
    expect(a.get_by_role("heading", name="Waiting to join (2)")).to_be_visible(timeout=10000)
    check("pending list shows both", True)
    a.screenshot(path=str(OUT / "corp_people.png"))
    # The invited person gets the link by email; here we mint a fresh one the same way through the API.
    r = a.evaluate(f"""(async () => {{
        const h = {{Authorization:'Bearer '+localStorage.authToken, 'Content-Type':'application/json'}};
        const me = await (await fetch('/corp/me', {{headers:h}})).json();
        const org = me.orgs[0].id;
        const o = await (await fetch('/corp/orgs/'+org, {{headers:h}})).json();
        const eng = o.units.find(u => u.name==='Engineering').id;
        const r = await (await fetch('/corp/orgs/'+org+'/invites', {{method:'POST', headers:h,
            body: JSON.stringify({{emails:'{B}@example.test', unit_id: eng, unit_role:'manager'}})}})).json();
        return r.created[0].link;
    }})()""")
    link = r.replace("http://localhost:5000", BASE).replace("http://127.0.0.1:5000", BASE)
    path = link[link.index("/app/corp/join/"):]

    # ------------------------------------------------------------ overview
    a.get_by_role("link", name="Home").click()
    expect(a.get_by_role("heading", name="Needs you")).to_be_visible()
    check("home greets the user and names the organisation", "Good" in a.locator("h1").inner_text() and ORG in a.locator(".home-head").inner_text())
    check("unfinished setup shown to the owner as one next step", "Setup" in a.locator(".home-setup").inner_text())
    a.screenshot(path=str(OUT / "corp_overview.png"))

    # ------------------------------------------------------------ switch to Personal and back
    a.get_by_role("group", name="Personal or Corporate").get_by_role("button", name="Personal").click()
    a.wait_for_url("**/app/os", timeout=15000)
    expect(a.locator(".tl-os")).to_be_visible(timeout=15000)
    check("switch to Personal opens the Personal home", a.locator(".tl-os").count() == 1)
    check("Personal sidebar shows the switch with Personal pressed",
          a.get_by_role("group", name="Personal or Corporate").get_by_role("button", name="Personal").get_attribute("aria-pressed") == "true")
    a.goto(BASE + "/app")
    a.wait_for_url("**/app/os", timeout=15000)
    check("reload lands on the side last used (Personal), no chooser", "/app/os" in a.url)
    a.get_by_role("group", name="Personal or Corporate").get_by_role("button", name="Corporate").click()
    a.wait_for_url("**/app/corp", timeout=15000)
    expect(a.get_by_role("heading", name="Needs you")).to_be_visible(timeout=15000)
    check("switch back to Corporate opens the organisation", True)
    a.goto(BASE + "/app/chats")
    check("old Personal pages still have the rail with a Corporate button",
          a.locator(".icon-rail").get_by_role("button", name="Switch to Corporate").is_visible())

    # ------------------------------------------------------------ wrong account
    ctxC, c = new_page("C")
    sign_up(c, C, start=path)
    expect(c.get_by_role("heading", name=f"Join {ORG}")).to_be_visible(timeout=15000)
    check("invite link survives sign-up and opens the join page", "/app/corp/join/" in c.url)
    check("wrong account is told, can't join",
          c.locator(".corp-warn").is_visible() and c.get_by_role("button", name=f"Join {ORG}").count() == 0)
    c.screenshot(path=str(OUT / "corp_join_wrong.png"))

    # ------------------------------------------------------------ right account
    ctxB, b = new_page("B")
    sign_up(b, B, start=path)
    expect(b.get_by_role("button", name=f"Join {ORG}")).to_be_visible(timeout=15000)
    check("invited person sees the role they're invited to", "department head" in b.locator(".corp-empty").inner_text())
    b.screenshot(path=str(OUT / "corp_join.png"))
    b.get_by_role("button", name=f"Join {ORG}").click()
    expect(b.get_by_role("heading", name="Needs you")).to_be_visible(timeout=15000)
    check("joined: overview of the org", b.url.rstrip("/").endswith("/app/corp"))
    check("joining switched the account to Corporate",
          b.evaluate("fetch('/settings',{headers:{Authorization:'Bearer '+localStorage.authToken}}).then(r=>r.json()).then(d=>d.personalization)") == "office")
    check("new member doesn't see the owner's setup", b.locator(".home-setup").count() == 0)
    b.get_by_role("link", name="Structure").click()
    b.get_by_role("tree", name="Departments and teams").get_by_role("button", name=re.compile("Engineering")).click()
    expect(b.get_by_role("heading", name="Engineering", level=2)).to_be_visible(timeout=10000)
    check("head can't rename own department (no edit buttons)", b.get_by_role("button", name="Rename").count() == 0)
    expect(b.get_by_label("Person to add")).to_be_visible(timeout=10000)
    check("head can add people and teams in own department",
          b.get_by_label("Person to add").is_visible() and b.get_by_role("button", name=re.compile("Add a team")).is_visible())

    # ------------------------------------------------------------ phone
    ctxP, ph = new_page("phone", viewport={"width": 390, "height": 844}, storage_state=ctxA.storage_state())
    ph.goto(BASE + "/app/corp/structure")
    expect(ph.get_by_role("tree", name="Departments and teams")).to_be_visible(timeout=15000)
    check("phone: no horizontal scroll on Corporate", ph.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"))
    check("phone: switch visible", ph.get_by_role("group", name="Personal or Corporate").is_visible())
    ph.screenshot(path=str(OUT / "corp_phone.png"), full_page=True)
    ph.get_by_role("group", name="Personal or Corporate").get_by_role("button", name="Personal").click()
    ph.wait_for_url("**/app/home", timeout=15000)
    ph.get_by_role("button", name="Open menu").click()
    check("phone: Personal drawer offers Switch to Corporate", ph.get_by_role("button", name="Switch to Corporate").is_visible())

    browser.close()

for e in errors:
    print("ERROR", e)
print(f"\n{sum(results)}/{len(results)} passed, {len(errors)} page errors")
sys.exit(0 if all(results) and not errors else 1)
