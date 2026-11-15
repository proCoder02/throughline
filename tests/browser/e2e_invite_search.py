import re, uuid, sys
from playwright.sync_api import sync_playwright, expect
BASE="http://127.0.0.1:5000"; tag=uuid.uuid4().hex[:6]; u=f"cs{tag}"; errs=[]
with sync_playwright() as p:
    b=p.chromium.launch(channel="chrome", headless=True); pg=b.new_page(viewport={"width":1400,"height":900})
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(BASE+"/app"); pg.get_by_role("button", name="Create an account").click()
    pg.get_by_label("Username").fill(u); pg.get_by_label("Email").fill(f"{u}@example.test"); pg.get_by_label("Password", exact=True).fill("secret123")
    pg.get_by_role("checkbox", name="I agree").check(); pg.get_by_role("button", name="Create account").click()
    pg.get_by_role("button", name="Skip for now").click(); pg.get_by_role("button", name=re.compile("^Corporate")).click()
    pg.get_by_role("button", name="Set up a new organisation").click(); pg.get_by_label("Organisation name").fill(f"Org {tag}"); pg.get_by_role("button", name="Create organisation").click()
    pg.wait_for_url("**/structure"); pg.get_by_role("link", name="People").click()
    pg.get_by_label("Search people").fill("someone@company.test")
    btn=pg.get_by_role("button", name="Invite someone@company.test"); expect(btn).to_be_visible()
    btn.click()
    expect(pg.get_by_label("Email addresses")).to_have_value("someone@company.test")
    ok1 = pg.evaluate("document.activeElement === document.querySelector('textarea')")
    pg.get_by_label("Search people").fill("zzz")
    ok2 = "type their email address" in pg.locator(".corp-pad").inner_text()
    print("invite button fills + focuses the form:", ok1, "| plain-name miss explains how to add:", ok2, "| page errors:", errs)
    checks = {"invite button fills + focuses the form": ok1, "plain-name miss explains how to add": ok2, "no page errors": not errs}
    for n, c in checks.items():
        print(("PASS " if c else "FAIL ") + n)
    print(f"{sum(checks.values())}/{len(checks)} passed")
    b.close()
