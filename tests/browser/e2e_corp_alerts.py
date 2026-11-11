import os
"""Live work-message alerts: Khushi messages Amit while Amit is on the
Personal side -> instant toast, badge on the switch, tab title; on the
Corporate side the Messages badge; opening the thread clears it."""
import json, re, sys, uuid, urllib.request
from playwright.sync_api import sync_playwright, expect
BASE = "http://127.0.0.1:5000"; tag = uuid.uuid4().hex[:6]; A, K = f"amit{tag}", f"khushi{tag}"
results, errors = [], []
def check(n, c, x=""):
    results.append(bool(c)); print(("PASS " if c else "FAIL ") + n + (f"  -- {x}" if x and not c else ""), flush=True)
def api(path, body=None, token=None, method=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, method=method or ("POST" if body is not None else "GET"),
                                 headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + token} if token else {})})
    return json.loads(urllib.request.urlopen(req).read() or b"{}")
def reg(u):
    r = api("/register", {"username": u, "email": f"{u}@example.test", "password": "secret123", "accepted_terms": True}); return r["token"], r["id"]
ta, ua = reg(A); tk, uk = reg(K)
org = api("/corp/orgs", {"name": f"Nodex {tag}"}, ta)["org"]["id"]
link = api(f"/corp/orgs/{org}/invites", {"emails": f"{K}@example.test"}, ta)["created"][0]["link"]
api(f"/corp/invites/{link.rsplit('/', 1)[1]}/accept", {}, tk)
tid = api(f"/corp/orgs/{org}/threads/direct", {"user_id": ua}, tk)["thread_id"]

with sync_playwright() as p:
    br = p.chromium.launch(channel="chrome", headless=True)
    ctx = br.new_context(viewport={"width": 1400, "height": 900})
    ctx.add_init_script(f"localStorage.setItem('authToken','{ta}');localStorage.setItem('tl_mode','personal');localStorage.setItem('tl_persona_skipped_{ua}','1');")
    a = ctx.new_page()
    a.on("pageerror", lambda e: errors.append(str(e)))
    a.goto(BASE + "/app/os")
    expect(a.locator(".tl-os")).to_be_visible(timeout=20000)
    try: a.get_by_role("dialog", name="Our Terms and Privacy Policy").get_by_role("button", name=re.compile("Agree")).click(timeout=4000)
    except Exception: pass
    a.wait_for_timeout(2500)  # socket connected
    api(f"/corp/orgs/{org}/threads/{tid}/messages", {"body": "Hi Amit, scripts are ready for review"}, tk)
    toast = a.locator(".corp-alert")
    expect(toast).to_be_visible(timeout=25000)
    check("toast appears instantly on the Personal side", K in toast.inner_text() and "scripts are ready" in toast.inner_text())
    sw = a.get_by_role("group", name="Personal or Corporate")
    check("badge on the Corporate switch", sw.locator(".mode-badge").inner_text() == "1")
    check("tab title shows the count", a.title().startswith("(1) "), a.title())
    a.screenshot(path=os.path.join(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_shots"), "alerts_personal.png"))
    toast.click()
    a.wait_for_url("**/app/corp/messages?t=*", timeout=10000)
    expect(a.locator(".corp-msg").first).to_contain_text("scripts are ready")
    check("clicking the toast opens the conversation", True)
    expect(a.locator(".corp-nav-item .corp-unread")).to_have_count(0, timeout=8000)
    check("opening it clears the badge and title", not a.title().startswith("("), a.title())
    a.get_by_role("link", name="Home").click()
    api(f"/corp/orgs/{org}/threads/{tid}/messages", {"body": "Also, standup moved to 11"}, tk)
    expect(a.locator(".corp-nav-item .corp-unread")).to_have_text("1", timeout=25000)
    check("Messages badge in the Corporate sidebar", True)
    a.get_by_role("link", name=re.compile("Messages")).click()
    a.get_by_role("button", name=re.compile(K)).click()
    api(f"/corp/orgs/{org}/threads/{tid}/messages", {"body": "third message while open"}, tk)
    expect(a.locator(".corp-msg", has_text="third message while open")).to_be_visible(timeout=25000)
    check("open thread updates live, no toast", a.locator(".corp-alert").count() == 0)
    br.close()
api_del = None
for e in errors: print("ERROR", e)
print(f"\n{sum(results)}/{len(results)} passed, {len(errors)} page errors")
