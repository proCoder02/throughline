"""Browser check of the consent notice, Terms agreement, legal pages and
account deletion against the local server. Fails on any page error."""
import re, uuid
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
user, pw = f"cl{tag}", "secret123"

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True,
                                args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"])
    def new_page(tag_, **kw):
        ctx = browser.new_context(viewport={"width": 1400, "height": 900}, permissions=["microphone"], **kw)
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(f"{tag_}/{STEP['n']} pageerror: {e}"))
        page.on("console", lambda m: m.type == "error" and "Failed to load resource" not in m.text and "WebSocket connection" not in m.text and errors.append(f"{tag_}/{STEP['n']} console: {m.text[:60]} … {m.text[-120:]}"))
        return ctx, page

    # ---------------------------------------------------------------- sign-up needs the agreement
    ctx, page = new_page("signup")
    page.goto(BASE + "/app")
    expect(page.get_by_role("link", name="Privacy", exact=True)).to_be_visible(timeout=15000)
    check("log-in screen links Privacy and Terms", page.get_by_role("link", name="Terms", exact=True).is_visible())
    page.get_by_role("button", name="Create an account").click()
    page.get_by_label("Username").fill(user)
    page.get_by_label("Email").fill(f"{user}@example.test")
    page.get_by_label("Password", exact=True).fill(pw)
    page.get_by_role("button", name="Create account").click()
    expect(page.get_by_role("alert")).to_contain_text("agree to the Terms")
    check("sign-up refused until the agreement is ticked", "/app" in page.url and page.get_by_role("button", name="Create account").is_visible())
    page.get_by_role("checkbox", name="I agree").check()
    page.get_by_role("button", name="Create account").click()
    expect(page.get_by_role("dialog", name="Personalize Throughline")).to_be_visible(timeout=15000)
    page.get_by_role("button", name="Skip for now").click()
    page.wait_for_timeout(800)
    check("new account isn't asked to agree again", page.get_by_role("dialog", name="Our Terms and Privacy Policy").count() == 0)

    # ---------------------------------------------------------------- recording notice before Listen
    page.goto(BASE + "/app/chats")
    page.get_by_role("button", name="Listen", exact=True).click()
    notice = page.get_by_role("dialog", name="Before you record")
    expect(notice).to_be_visible()
    check("Listen shows the recording notice first", True)
    check("Continue disabled until the box is ticked", page.get_by_role("button", name="Continue").is_disabled())
    page.get_by_role("button", name="Cancel").click()
    page.wait_for_timeout(600)
    check("Cancel: no recording starts", page.get_by_role("button", name="Stop Listening").count() == 0)
    page.get_by_role("button", name="Listen", exact=True).click()
    page.get_by_role("checkbox", name="I'll tell people").check()
    page.get_by_role("button", name="Continue").click()
    expect(page.get_by_role("button", name="Stop Listening")).to_be_visible(timeout=15000)
    check("after agreeing, recording starts", True)
    page.wait_for_timeout(1500)
    check("live badge reminds to tell people", page.get_by_text("make sure everyone here knows").count() > 0)
    page.screenshot(path=str(OUT / "legal_listening.png"))
    page.get_by_role("button", name="Stop Listening").click()
    page.wait_for_timeout(1500)
    page.reload()
    page.get_by_role("button", name="Listen", exact=True).click()
    expect(page.get_by_role("button", name="Stop Listening")).to_be_visible(timeout=15000)
    check("notice not shown again once accepted", page.get_by_role("dialog", name="Before you record").count() == 0)
    page.get_by_role("button", name="Stop Listening").click()
    page.wait_for_timeout(1500)

    # ---------------------------------------------------------------- settings: links + delete account
    page.goto(BASE + "/app/os/settings")
    expect(page.get_by_role("navigation", name="Legal").get_by_role("link", name="Privacy Policy")).to_be_visible(timeout=15000)
    check("settings links Privacy Policy and Terms", True)
    page.get_by_role("button", name="Delete account…").click()
    page.get_by_label("Your password").fill("wrongpass")
    page.get_by_label("Type DELETE to confirm").fill("DELETE")
    page.get_by_role("button", name="Delete my account").click()
    page.get_by_role("button", name="Delete permanently").click()
    expect(page.get_by_role("alert").filter(has_text="don't match")).to_be_visible()
    check("wrong password: refused with a clear message", True)
    page.get_by_label("Your password").fill(pw)
    page.get_by_role("button", name="Delete my account").click()
    page.screenshot(path=str(OUT / "legal_delete_confirm.png"))
    page.get_by_role("button", name="Delete permanently").click()
    expect(page.get_by_role("heading", name="Log in")).to_be_visible(timeout=15000)
    check("after deletion: signed out to the log-in screen", True)
    page.get_by_label("Username").fill(user)
    page.get_by_label("Password", exact=True).fill(pw)
    page.get_by_role("button", name="Log in").click()
    expect(page.get_by_role("alert")).to_contain_text("Invalid username or password")
    check("deleted account can't log in", True)
    ctx.close()

    # ---------------------------------------------------------------- existing account without the agreement
    old_user = f"cl{tag}old"
    ctx, page = new_page("existing")
    r = page.request.post(BASE + "/register", data={"username": old_user, "email": f"{old_user}@example.test", "password": pw})
    check("old-style sign-up (no agreement field) still works", r.ok)
    page.goto(BASE + "/app")
    if page.get_by_role("heading", name="Log in").count():
        page.get_by_label("Username").fill(old_user)
        page.get_by_label("Password", exact=True).fill(pw)
        page.get_by_role("button", name="Log in").click()
    page.get_by_role("button", name="Skip for now").click(timeout=15000)
    terms = page.get_by_role("dialog", name="Our Terms and Privacy Policy")
    expect(terms).to_be_visible(timeout=10000)
    check("existing account is asked to agree once", page.get_by_role("button", name="Agree").is_disabled())
    page.screenshot(path=str(OUT / "legal_terms_prompt.png"))
    page.get_by_role("checkbox", name="I agree").check()
    page.get_by_role("button", name="Agree").click()
    expect(terms).to_be_hidden()
    page.reload(); page.wait_for_timeout(1500)
    check("not asked again after agreeing", page.get_by_role("dialog", name="Our Terms and Privacy Policy").count() == 0)
    ctx.close()

    # ---------------------------------------------------------------- public pages + delete by page form
    ctx, page = new_page("public")
    for path, heading in [("/privacy", "Privacy Policy"), ("/terms", "Terms of Service"), ("/account/delete", "Delete your Throughline account")]:
        page.goto(BASE + path)
        check(f"{path} renders with its heading", page.get_by_role("heading", name=heading, level=1).is_visible())
    page.goto(BASE + "/privacy"); page.screenshot(path=str(OUT / "legal_privacy.png"), full_page=True)
    page.goto(BASE + "/account/delete")
    page.get_by_label("Username").fill(old_user)
    page.get_by_label("Password").fill(pw)
    page.get_by_label("Type DELETE to confirm").fill("delete")
    page.get_by_role("button", name="Delete my account permanently").click()
    expect(page.get_by_role("status")).to_contain_text("Type DELETE")
    page.get_by_label("Type DELETE to confirm").fill("DELETE")
    page.get_by_role("button", name="Delete my account permanently").click()
    expect(page.get_by_role("status")).to_contain_text("have been deleted")
    check("deletion page deletes by username + password", True)
    ctx.close()

    # ---------------------------------------------------------------- phone widths
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    for path in ["/privacy", "/terms", "/account/delete"]:
        page.goto(BASE + path)
        check(f"phone {path}: no sideways scrolling", not page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"))
    page.goto(BASE + "/privacy"); page.screenshot(path=str(OUT / "legal_privacy_phone.png"))
    ctx.close()
    browser.close()

check("no page errors anywhere", not errors, "\n  " + "\n  ".join(errors[:15]))
print(f"\n{sum(results)}/{len(results)} passed")
