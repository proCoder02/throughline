import re
"""End-to-end check of the web UX changes against the local server, desktop
and phone, plus every existing section still rendering. Fails on any page
error. Usage: e2e_ux.py <reset_token_helper_cmd...> (token minted by caller)."""
import json, subprocess, sys, time, uuid
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

BASE = "http://127.0.0.1:5000"
OUT = Path(__file__).parent / "_shots"
PROJECT = Path(r"C:\Users\P3INW57\Desktop\speech2text")
results = []
def check(name, cond, extra=""):
    results.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + (f"  -- {extra}" if extra and not cond else ""), flush=True)

def mint_reset_token(username):
    code = (
        "import os,sys,hashlib; sys.path.insert(0,'.');"
        "from dotenv import load_dotenv; load_dotenv('.env');"
        "import psycopg2; from itsdangerous import URLSafeTimedSerializer as S;"
        "c=psycopg2.connect(os.environ['DATABASE_URL']).cursor();"
        f"c.execute('select id,password_hash from users where username=%s',({username!r},)); i,h=c.fetchone();"
        "fp=hashlib.sha256(h.encode()).hexdigest()[:16];"
        "print(S(os.getenv('FLASK_SECRET_KEY','dev-secret-change-me'),salt='throughline-password-reset').dumps({'uid':i,'fp':fp}))"
    )
    return subprocess.check_output([str(PROJECT / ".venv/Scripts/python.exe"), "-c", code], cwd=PROJECT, text=True).strip()

user = f"ux{uuid.uuid4().hex[:6]}"
pw, pw2 = "secret123", "newsecret456"
errors = []

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)

    def watch(page, tag):
        page.on("pageerror", lambda e: errors.append(f"{tag} pageerror: {e}"))
        page.on("console", lambda m: m.type == "error" and "Failed to load resource" not in m.text and errors.append(f"{tag} console: {m.text[:200]}"))

    # ------------------------------------------------------------ desktop
    ctx = browser.new_context(viewport={"width": 1400, "height": 900})
    page = ctx.new_page(); watch(page, "desktop")
    page.goto(BASE + "/app")
    expect(page.get_by_role("heading", name="Log in")).to_be_visible()
    check("login: visible labels", page.get_by_label("Username").is_visible() and page.get_by_label("Password", exact=True).is_visible())
    check("login: forgot-password link", page.get_by_role("button", name="Forgot password?").is_visible())
    page.get_by_label("Password", exact=True).fill("abc")
    page.get_by_role("button", name="Show password").click()
    check("login: show password reveals it", page.get_by_label("Password", exact=True).get_attribute("type") == "text")
    page.get_by_role("button", name="Hide password").click()
    page.get_by_label("Username").fill("nobody_" + user)
    page.get_by_label("Username").press("Enter")  # real form: Enter from any field submits
    expect(page.get_by_role("alert")).to_contain_text("Invalid username or password")
    check("login: Enter in username submits; error announced", True)
    check("login: autocomplete set", page.get_by_label("Username").get_attribute("autocomplete") == "username")

    page.get_by_role("button", name="Forgot password?").click()
    page.get_by_label("Username or email").fill("nobody_" + user)
    page.get_by_role("button", name="Email me a reset link").click()
    expect(page.get_by_role("status").filter(has_text="If that account exists")).to_be_visible()
    check("forgot: same generic answer for unknown account", True)
    page.get_by_role("button", name="Back to log in").click()

    page.get_by_role("button", name="Create an account").click()
    page.get_by_label("Username").fill(user)
    page.get_by_label("Email").fill(f"{user}@example.test")
    page.get_by_label("Password", exact=True).fill(pw)
    page.get_by_role("checkbox", name="I agree").check()  # new: required agreement
    page.get_by_role("button", name="Create account").click()
    expect(page.get_by_role("dialog", name="Personalize Throughline")).to_be_visible(timeout=15000)
    check("onboarding opens with Skip for now", page.get_by_role("button", name="Skip for now").is_visible())
    page.get_by_role("button", name="Skip for now").click()
    # New: a device's first visit asks Personal or Corporate once.
    page.get_by_role("button", name=re.compile("^Personal")).click()
    expect(page.get_by_role("heading", name="Needs you")).to_be_visible(timeout=15000)
    check("skip lands in the app (desktop home)", page.url.rstrip("/").endswith("/app/os"), page.url)
    expect(page.get_by_role("button", name="Record your first conversation")).to_be_visible(timeout=15000)
    check("new user: first-step call to action", True)
    check("home: Personalize card while unanswered", page.get_by_role("button", name="Answer a few questions").is_visible())
    page.screenshot(path=str(OUT / "ux_desktop_home.png"), full_page=True)

    # sidebar: every screen gets its own URL; Back works
    for label, slug in [("Conversations", "conversations"), ("Tasks", "tasks"), ("People", "people"), ("Calls", "calls"),
                        ("Ask Throughline", "ask"), ("Insights", "insights"), ("Sharing with friends", "sharing"),
                        ("Food ordering", "commerce"), ("Notifications", "notifications"), ("Settings", "settings")]:
        page.get_by_role("navigation", name="Main").get_by_role("button", name=label, exact=True).click()
        page.wait_for_url(f"**/app/os/{slug}")
        expect(page.get_by_role("navigation", name="Main").get_by_role("button", name=label, exact=True)).to_have_attribute("aria-current", "page", timeout=5000)
        check(f"nav: {label} -> /app/os/{slug}", True)
    page.go_back(); page.wait_for_url("**/app/os/notifications")
    check("browser Back returns to the previous screen", True)
    page.goto(BASE + "/app/os/tasks")
    expect(page.get_by_role("heading", name="Tasks", exact=True)).to_be_visible()
    check("deep link /app/os/tasks opens Tasks", True)

    # search
    page.get_by_label("Search conversations, people and tasks").fill("zzqq")
    expect(page.get_by_text("No conversations, people or tasks match.")).to_be_visible()
    page.get_by_label("Search conversations, people and tasks").press("Enter")
    page.wait_for_url("**/app/os/ask")
    page.wait_for_timeout(800)
    box = page.locator(".tl-os textarea, .tl-os input[type=text]").filter(has_text="").first
    vals = page.eval_on_selector_all(".tl-os textarea, .tl-os input", "els => els.map(e => e.value)")
    check("search Enter with no match opens Ask pre-filled (not sent)", "zzqq" in vals, vals)
    page.goto(BASE + "/app/os/conversations")
    check("conversation filter box is enabled", page.get_by_label("Filter conversations").is_enabled())
    check("notifications button has a name", page.get_by_role("button", name="Notifications", exact=False).first.is_visible())
    check("mode select is labelled", page.get_by_label("Mode").is_visible())

    # personalize card opens the questions over the app, skip closes it
    page.goto(BASE + "/app/os")
    page.get_by_role("button", name="Answer a few questions").click()
    expect(page.get_by_role("dialog", name="Personalize Throughline")).to_be_visible()
    page.get_by_role("button", name="Skip for now").click()
    expect(page.get_by_role("heading", name="Needs you")).to_be_visible()
    check("personalize from Home opens and closes over the app", True)

    # every existing section still renders; the rail has Home
    for path, marker in [("/app/chats", "Conversations"), ("/app/tasks", "Tasks"), ("/app/friends", "Friends"),
                         ("/app/profiles", "People"), ("/app/settings", "Settings"), ("/app/meet", None)]:
        page.goto(BASE + path)
        page.wait_for_load_state("networkidle")
        if marker:
            rail = page.get_by_role("navigation", name="Main")
            check(f"{path}: rail shows '{marker}' as current", rail.get_by_role("button", name=marker, exact=True).get_attribute("aria-current") == "page")
        check(f"{path}: page rendered (no blank screen)", page.locator("#root").inner_text().strip() != "")
    page.goto(BASE + "/app/tasks")
    page.get_by_role("navigation", name="Main").get_by_role("button", name="Home", exact=True).click()
    page.wait_for_url("**/app/os")
    check("rail Home returns to the main screen", True)
    page.screenshot(path=str(OUT / "ux_desktop_os.png"))

    # instant start: reload shows the app, not the login screen or a blank page
    page.reload(wait_until="domcontentloaded")
    page.wait_for_timeout(300)
    check("reload: app drawn straight away (cached user)", page.locator(".tl-os, .app-shell").count() > 0 and page.get_by_role("heading", name="Log in").count() == 0)
    ctx.close()

    # ------------------------------------------------------------ password reset end to end
    token = mint_reset_token(user)
    ctx = browser.new_context(viewport={"width": 1400, "height": 900})
    page = ctx.new_page(); watch(page, "reset")
    page.goto(BASE + f"/app?reset_token={token}")
    expect(page.get_by_role("heading", name="Choose a new password")).to_be_visible()
    page.get_by_label("New password").fill(pw2)
    page.get_by_role("button", name="Save new password").click()
    expect(page.get_by_role("status").filter(has_text="Password changed")).to_be_visible()
    check("reset: link sets a new password", "reset_token" not in page.url, page.url)
    page.goto(BASE + f"/app?reset_token={token}")
    page.get_by_label("New password").fill("another789")
    page.get_by_role("button", name="Save new password").click()
    expect(page.get_by_role("alert")).to_contain_text("already been used")
    check("reset: the same link can't be used twice", True)
    page.get_by_role("button", name="Back to log in").click()
    page.get_by_label("Username").fill(user)
    page.get_by_label("Password", exact=True).fill(pw2)
    page.get_by_role("button", name="Log in").click()
    expect(page.get_by_role("dialog", name="Personalize Throughline")).to_be_visible(timeout=15000)
    check("reset: log in with the new password", True)
    ctx.close()

    # ------------------------------------------------------------ phone
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = ctx.new_page(); watch(page, "phone")
    page.goto(BASE + "/app")
    page.get_by_label("Username").fill(user)
    page.get_by_label("Password", exact=True).fill(pw2)
    page.get_by_role("button", name="Log in").click()
    expect(page.get_by_role("dialog", name="Personalize Throughline")).to_be_visible(timeout=15000)
    w = page.evaluate("document.querySelector('.persona-card').getBoundingClientRect().width")
    check("phone: onboarding card fits the screen", w <= 390, w)
    page.get_by_role("button", name="Skip for now").click()
    page.get_by_role("button", name=re.compile("^Personal")).click()  # new device: chooser once
    page.wait_for_url("**/app/home")
    try:
        expect(page.get_by_role("button", name="Start", exact=True)).to_be_visible(timeout=10000)
        seen = True
    except AssertionError:
        seen = False
    check("phone: Personalize card on Home", seen)
    page.screenshot(path=str(OUT / "ux_phone_home.png"), full_page=True)
    page.get_by_role("button", name="Open menu").click()
    drawer = page.locator("nav.app-drawer")
    labels = [t.strip() for t in drawer.locator(".app-drawer-item span:not(.app-drawer-badge)").all_inner_texts()]
    check("phone: drawer uses the shared names", labels == ["Home", "Conversations", "Tasks", "Friends", "People", "Meet", "Settings", "Switch to Corporate"], labels)
    page.screenshot(path=str(OUT / "ux_phone_drawer.png"))
    for key, title in [("chats", "Conversations"), ("tasks", "Tasks"), ("friends", "Friends"), ("profiles", "People"), ("settings", "Settings")]:
        page.goto(BASE + f"/app/{key}")
        page.wait_for_load_state("networkidle")
        check(f"phone /app/{key}: top bar says {title}", page.locator(".phone-topbar-title").inner_text() == title)
    hscroll = page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
    check("phone: no sideways scrolling", not hscroll)
    ctx.close()
    browser.close()

check("no page errors anywhere", not errors, "\n  " + "\n  ".join(errors[:15]))
print(f"\n{sum(results)}/{len(results)} passed")
print("USER", user)
