"""Per-person daily AI limit (llm/quota.py): AI requests are counted per person
per day, the limit returns a clear 429, other people and non-AI requests are
unaffected, and the day key resets."""
import os, sys, uuid
sys.path.insert(0, os.getcwd())
os.environ.update(CORPORATE_ENABLED="true", CORPORATE_JOBS="false", PYTHONIOENCODING="utf-8", LLM_DAILY_LIMIT="3")
import app  # noqa: E402
app._smtp_send = lambda *a, **k: None
from datetime import datetime, timezone
from llm import quota
from corporate import ai

results = []
def check(n, c, x=""):
    results.append(bool(c)); print(("PASS " if c else "FAIL ") + n + (f"  -- {x}" if x != "" and not c else ""))
tag = uuid.uuid4().hex[:5]
def person(n):
    c = app.app.test_client(); u = f"{n}{tag}"
    return c, c.post("/register", json={"username": u, "email": f"{u}@example.test", "password": "secret123"}).get_json()["id"]
ai.answer = lambda call_llm, q, sources, history=None: "Fine."  # no real model calls
a, ua = person("quota"); b, ub = person("other")
d = a.post("/corp/orgs", json={"name": f"Q {tag}", "departments": ["Ops"]}).get_json()
org = d["org"]["id"]; B = f"/corp/orgs/{org}"
a.post(B + "/decisions", json={"unit_id": d["units"][0]["id"], "text": "Use the blue logo"})
a.put(f"{B}/units/{d['units'][0]['id']}/members/{ua}", json={"role": "manager"})

check("limit read from env", quota.limit() == 3)
codes = [a.post(B + "/ask", json={"question": f"what about the logo {i}?"}).status_code for i in range(4)]
check("first 3 AI requests allowed, 4th refused", codes == [200, 200, 200, 429], codes)
r = a.post(B + "/ask", json={"question": "again?"})
check("clear message with the limit", r.status_code == 429 and "today's 3 AI requests" in r.get_json()["error"], r.get_json())
check("non-AI requests still work", a.get(B + "/commitments").status_code == 200)
check("someone else isn't affected", b.post("/corp/orgs", json={"name": f"Q2 {tag}", "departments": ["X"]}).status_code in (201, 403))
check("only POSTs to AI routes count", quota.limited_path("POST", f"/corp/orgs/{org}/ask") and not quota.limited_path("GET", f"/corp/orgs/{org}/ask")
      and not quota.limited_path("POST", f"/corp/orgs/{org}/commitments") and quota.limited_path("POST", "/chat/global"))
check("recording processing is never counted", not quota.limited_path("POST", "/analyze"))
check("used today = 5 attempts counted", quota.used_today(app.redis_client, ua) == 5, quota.used_today(app.redis_client, ua))
check("the day resets at midnight IST", quota._day(datetime(2026, 11, 11, 18, 29, tzinfo=timezone.utc)) == "2026-11-11"
      and quota._day(datetime(2026, 11, 11, 18, 31, tzinfo=timezone.utc)) == "2026-11-12")
os.environ["LLM_DAILY_LIMIT"] = "0"
check("0 turns the limit off", a.post(B + "/ask", json={"question": "now?"}).status_code == 200)
class Down:
    def incr(self, k): raise ConnectionError("redis down")
check("Redis down: requests still allowed", quota.take(Down(), ua)[0] is True or quota.limit() == 0)
os.environ["LLM_DAILY_LIMIT"] = "3"
check("Redis down with a limit set: still allowed", quota.take(Down(), ua) == (True, -1))
app.redis_client.delete(quota._key(ua))
print(f"\n{sum(results)}/{len(results)} passed")
