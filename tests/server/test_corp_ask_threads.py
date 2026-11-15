"""Ask threads: saved per person, listed, reopened, deleted; follow-ups get
the earlier turns and find the same sources; nobody else can see them."""
import os, sys, uuid
sys.path.insert(0, os.getcwd())
os.environ.update(CORPORATE_ENABLED="true", CORPORATE_JOBS="false", PYTHONIOENCODING="utf-8")
import app  # noqa: E402
app._smtp_send = lambda *a, **k: None
import psycopg2.extras
from corporate import ai

results = []
def check(n, c, x=""):
    results.append(bool(c)); print(("PASS " if c else "FAIL ") + n + (f"  -- {x}" if x != "" and not c else ""))
tag = uuid.uuid4().hex[:5]
conn = app.get_raw_connection(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
def q(sql, a=()):
    cur.execute(sql, a); out = cur.fetchall() if cur.description else None; conn.commit(); return out
def person(n):
    c = app.app.test_client(); u = f"{n}{tag}"
    return c, c.post("/register", json={"username": u, "email": f"{u}@example.test", "password": "secret123"}).get_json()["id"]
own, uo = person("own"); dev, ud = person("dev")
d = own.post("/corp/orgs", json={"name": f"Nodex {tag}", "departments": ["Engineering"]}).get_json()
org = d["org"]["id"]; eng = d["units"][0]["id"]
q("INSERT INTO corp.members (org_id, user_id) VALUES (%s,%s)", (org, ud))
own.put(f"/corp/orgs/{org}/units/{eng}/members/{uo}", json={"role": "manager"})
own.put(f"/corp/orgs/{org}/units/{eng}/members/{ud}", json={"role": "member"})
B = f"/corp/orgs/{org}"
pid = own.post(B + "/projects", json={"name": "Payments"}).get_json()["id"]
own.put(f"{B}/projects/{pid}/ticket-key", json={"key": "PAY"})
own.put(f"{B}/projects/{pid}/members/{ud}", json={"role": "member"})
t1 = own.post(B + "/tickets", json={"project_id": pid, "title": "Refund webhook times out", "assignees": [ud]}).get_json()["ticket"]["id"]

seen = []
def fake_answer(call_llm, question, sources, history=None):
    seen.append({"question": question, "sources": sources, "history": history or []})
    return f"Answer to: {question} [K{t1}]"
ai.answer = fake_answer

r = own.post(B + "/ask", json={"question": "What's the status of PAY-1?"}).get_json()
check("first question starts a thread", r.get("thread_id"), r)
th = r["thread_id"]
check("answer and its sources come back", r["answer"].startswith("Answer to") and any(s["type"] == "ticket" for s in r["sources"]))
r2 = own.post(B + "/ask", json={"question": "and who owns it?", "thread_id": th}).get_json()
check("follow-up stays in the same thread", r2["thread_id"] == th)
check("follow-up gets the earlier turns", [h["role"] for h in seen[-1]["history"]] == ["user", "assistant"]
      and "PAY-1" in seen[-1]["history"][0]["content"], seen[-1]["history"])
check("follow-up finds the ticket from the earlier question", f"[K{t1}]" in seen[-1]["sources"], seen[-1]["sources"][:300])
lst = own.get(B + "/ask-threads").get_json()["threads"]
check("thread listed with its title and 2 questions", lst and lst[0]["id"] == th and lst[0]["title"].startswith("What's the status") and lst[0]["questions"] == 2, lst)
full = own.get(f"{B}/ask-threads/{th}").get_json()
check("reopened thread has all 4 turns with sources", [m["role"] for m in full["messages"]] == ["user", "assistant", "user", "assistant"]
      and full["messages"][1]["sources"], full)
r3 = own.post(B + "/ask", json={"question": "Anything new in Engineering?", "unit_id": eng}).get_json()
check("'New chat' (no thread) starts a second thread", r3["thread_id"] != th)
check("...and the first one is still there", len(own.get(B + "/ask-threads").get_json()["threads"]) == 2)
full3 = own.get(f"{B}/ask-threads/{r3['thread_id']}").get_json()
check("team scope remembered on the question", full3["messages"][0]["unit_name"] == "Engineering")
check("someone else can't see my threads", dev.get(B + "/ask-threads").get_json()["threads"] == [])
check("...or open one", dev.get(f"{B}/ask-threads/{th}").status_code == 404)
check("...or add to it", dev.post(B + "/ask", json={"question": "x", "thread_id": th}).status_code == 404)
check("...or delete it", dev.delete(f"{B}/ask-threads/{th}").status_code == 404)
check("I can delete mine", own.delete(f"{B}/ask-threads/{th}").status_code == 200
      and [t["id"] for t in own.get(B + "/ask-threads").get_json()["threads"]] == [r3["thread_id"]])
print(f"\n{sum(results)}/{len(results)} passed")
