"""Pay memory: "You owe" from conversation tasks, payee memory by UPI ID,
recording a confirmed payment (closes the promise), and that the existing
/transactions and /tasks endpoints are unchanged. The AI classifier is
stubbed so the test is deterministic."""
import os, sys, uuid
sys.path.insert(0, os.getcwd())
os.environ.update(CORPORATE_JOBS="false", COSTLENS_SDK="false", PYTHONIOENCODING="utf-8")
import app  # noqa: E402
import psycopg2.extras
from pay import owed

results = []
def check(n, c, x=""):
    results.append(bool(c)); print(("PASS " if c else "FAIL ") + n + (f"  -- {x}" if x != "" and not c else ""))

asked = []
def fake_classify(call_llm, username, tasks):
    asked.extend(t["description"] for t in tasks)
    out = {}
    for t in tasks:
        d = t["description"].lower()
        if "send ravi" in d:
            out[t["id"]] = {"pay": True, "amount": 500.0, "to": "Ravi", "for": "tickets"}
        elif "rent" in d:
            out[t["id"]] = {"pay": True, "amount": 12000.0, "to": "Landlord", "for": "rent"}
        else:
            out[t["id"]] = {"pay": False, "amount": None, "to": None, "for": None}
    return out
owed._classify = fake_classify

tag = uuid.uuid4().hex[:6]
conn = app.get_raw_connection(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
def q(sql, a=()):
    cur.execute(sql, a); out = cur.fetchall() if cur.description else None; conn.commit(); return out
def person(n):
    c = app.app.test_client(); u = f"{n}{tag}"
    r = c.post("/register", json={"username": u, "email": f"{u}@example.test", "password": "secret123", "accepted_terms": True}).get_json()
    c.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {r['token']}"
    return c, r["id"]
me, uid = person("payer"); other, oid = person("other")
conv = q("INSERT INTO conversations (user_id, title, raw_transcript) VALUES (%s, 'Call with Ravi', 'x') RETURNING id", (uid,))[0]["id"]
def task(user, text, owner=None, status="open"):
    return q("INSERT INTO tasks (user_id, conversation_id, description, owner, status) VALUES (%s,%s,%s,%s,%s) RETURNING id",
             (user, conv if user == uid else None, text, owner, status))[0]["id"]
t_ravi = task(uid, "Send Ravi 500 for the tickets")
t_owed_me = task(uid, "Ravi will pay me 300 back")
t_milk = task(uid, "Buy milk on the way home")
t_rent = task(uid, "Pay rent by the 5th")
t_other = task(oid, "Send Ravi 500 for the tickets")
VPA = f"ravi{tag}@okaxis"

# --- existing endpoints still behave the same
r = me.post("/transactions", json={"amount": 99, "payee": "Tea stall", "payment_method": "gpay"})
check("POST /transactions unchanged", r.status_code == 200 and r.get_json()["payee"] == "Tea stall", r.get_json())
check("GET /tasks unchanged", r.status_code == 200 and any(t["id"] == t_ravi for t in me.get("/tasks").get_json()))

# --- payee memory before any payment
m = me.get(f"/pay/payee?vpa={VPA}").get_json()
check("new payee flagged", m["new_payee"] is True and m["times_paid"] == 0 and m["warnings"], m)
check("bad UPI ID refused", me.get("/pay/payee?vpa=not-a-vpa").status_code == 400)
check("needs login", app.app.test_client().get(f"/pay/payee?vpa={VPA}").status_code == 401)

# --- You owe
o = me.get("/pay/owed").get_json()
texts = [i["text"] for i in o["items"]]
check("money promises listed", "Send Ravi 500 for the tickets" in texts and "Pay rent by the 5th" in texts, texts)
check("money owed TO me is not listed", "Ravi will pay me 300 back" not in texts, texts)
check("non-money tasks never sent to the AI", "Buy milk on the way home" not in asked, asked)
item = next(i for i in o["items"] if i["task_id"] == t_ravi)
check("amount, person and source", item["amount"] == 500 and item["person"] == "Ravi" and item["source_title"] == "Call with Ravi", item)
check("no UPI suggestion before Ravi is known", item["suggestion"] is None, item)
n_asked = len(asked)
me.get("/pay/owed")
check("each task asked about once only", len(asked) == n_asked, asked)
check("other people's tasks not shown", all(i["task_id"] != t_other for i in o["items"]))

# --- record a payment
r = me.post("/pay/record", json={"vpa": VPA, "payee_name": "RAVI KUMAR", "amount": 500, "note": "tickets", "app": "Google Pay", "task_id": t_ravi})
d = r.get_json()
check("payment recorded", r.status_code == 200 and d["duplicate"] is False and d["transaction_id"], d)
check("promise closed", d["closed_task"] and d["closed_task"]["task_id"] == t_ravi, d)
check("task is done", q("SELECT status FROM tasks WHERE id = %s", (t_ravi,))[0]["status"] == "done")
txn = q("SELECT amount, payee, item FROM transactions WHERE id = %s", (d["transaction_id"],))[0]
check("same spending row as before (amount, payee, note)", float(txn["amount"]) == 500 and txn["payee"] == "RAVI KUMAR" and txn["item"] == "tickets", txn)
check("closed promise leaves You owe", all(i["task_id"] != t_ravi for i in me.get("/pay/owed").get_json()["items"]))
r2 = me.post("/pay/record", json={"vpa": VPA, "payee_name": "RAVI KUMAR", "amount": 500, "app": "Google Pay"})
check("double confirm is a duplicate, not a second payment", r2.get_json()["duplicate"] is True and r2.get_json()["transaction_id"] == d["transaction_id"], r2.get_json())

# --- someone else's task can't be closed
r = other.post("/pay/record", json={"vpa": f"x{tag}@ybl", "amount": 50, "task_id": t_ravi})
check("can't close another user's task", r.status_code == 200 and r.get_json()["closed_task"] is None, r.get_json())
r = other.post("/pay/record", json={"vpa": f"x{tag}@ybl", "amount": 60, "task_id": t_other})
check("own task closes", r.get_json()["closed_task"]["task_id"] == t_other)
check("bad amount refused", me.post("/pay/record", json={"vpa": VPA, "amount": -5}).status_code == 400)
check("bad UPI refused on record", me.post("/pay/record", json={"vpa": "nope", "amount": 5}).status_code == 400)

# --- memory after payments
q("UPDATE pay.payments SET created_at = now() - interval '1 hour' WHERE user_id = %s", (uid,))
me.post("/pay/record", json={"vpa": VPA, "payee_name": "RAVI KUMAR", "amount": 500, "note": "dinner"})
m = me.get(f"/pay/payee?vpa={VPA.upper()}&name=RAVI KUMAR").get_json()
check("known payee: times, usual, last", m["times_paid"] == 2 and m["usual_amount"] == 500 and m["last"]["note"] == "dinner", m)
check("recent notes kept", m["recent_notes"][:2] == ["dinner", "tickets"], m)
check("no warning when name matches", m["warnings"] == [], m)
m = me.get(f"/pay/payee?vpa={VPA}&name=Someone Else").get_json()
check("warns when the QR's name differs", m["warnings"] and "Someone Else" in m["warnings"][0], m)
check("other users see nothing of mine", other.get(f"/pay/payee?vpa={VPA}").get_json()["times_paid"] == 0)

# --- label + suggestion for the next promise to Ravi
check("label saved", me.put("/pay/payee", json={"vpa": VPA, "label": "Ravi (cricket)"}).status_code == 200)
check("label shown", me.get(f"/pay/payee?vpa={VPA}").get_json()["label"] == "Ravi (cricket)")
t_ravi2 = task(uid, "Send Ravi 500 for the match fee")
it = next(i for i in me.get("/pay/owed").get_json()["items"] if i["task_id"] == t_ravi2)
check("known person suggests their UPI ID", it["suggestion"] and it["suggestion"]["vpa"] == VPA.lower(), it)
rec = me.get("/pay/recent").get_json()["payees"]
check("recent payees, labelled", rec and rec[0]["vpa"] == VPA.lower() and rec[0]["name"] == "Ravi (cricket)", rec)

# --- dismiss
check("dismiss hides it", me.post(f"/pay/owed/{t_rent}/dismiss").status_code == 200
      and all(i["task_id"] != t_rent for i in me.get("/pay/owed").get_json()["items"]))
check("dismissed task itself untouched", q("SELECT status FROM tasks WHERE id = %s", (t_rent,))[0]["status"] == "open")
check("can't dismiss another user's", other.post(f"/pay/owed/{t_ravi2}/dismiss").status_code == 404)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
