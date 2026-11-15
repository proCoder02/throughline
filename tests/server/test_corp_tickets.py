"""Tickets: project keys, tickets and permissions, tagging by key / by hand / by
AI suggestion, the summary-only privacy rule, the brief, and Ask."""
import os, sys, uuid
sys.path.insert(0, os.getcwd())
os.environ.update(CORPORATE_ENABLED="true", CORPORATE_JOBS="false", PYTHONIOENCODING="utf-8")
import app  # noqa: E402
app._smtp_send = lambda *a, **k: None
import psycopg2.extras
from corporate import insights, tickets, orgs

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
own, uo = person("own"); lead, ul = person("lead"); dev, ud = person("dev"); qa, uq = person("qa"); out, ux = person("out")
d = own.post("/corp/orgs", json={"name": f"Nodex {tag}", "departments": ["Engineering", "Sales"]}).get_json()
org = d["org"]["id"]; U = {u["name"]: u["id"] for u in d["units"]}
for uid in (ul, ud, uq, ux):
    q("INSERT INTO corp.members (org_id, user_id) VALUES (%s,%s)", (org, uid))
own.put(f"/corp/orgs/{org}/units/{U['Engineering']}/members/{ul}", json={"role": "manager"})
own.put(f"/corp/orgs/{org}/units/{U['Engineering']}/members/{ud}", json={"role": "member"})
own.put(f"/corp/orgs/{org}/units/{U['Engineering']}/members/{uq}", json={"role": "member"})
B = f"/corp/orgs/{org}"

# --- project key (website: lead or admin)
pid = lead.post(B + "/projects", json={"name": "Payments"}).get_json()["id"]
lead.put(f"{B}/projects/{pid}/members/{ud}", json={"role": "member"})
lead.put(f"{B}/projects/{pid}/members/{uq}", json={"role": "member"})
check("no key yet: can't create tickets", lead.post(B + "/tickets", json={"project_id": pid, "title": "x"}).status_code == 400)
check("a project member who isn't lead can't set the key", dev.put(f"{B}/projects/{pid}/ticket-key", json={"key": "PAY"}).status_code == 403)
check("bad key refused", lead.put(f"{B}/projects/{pid}/ticket-key", json={"key": "pay 1"}).status_code == 400)
check("lead sets key PAY", lead.put(f"{B}/projects/{pid}/ticket-key", json={"key": "pay"}).get_json()["ticket_key"] == "PAY")
pid2 = lead.post(B + "/projects", json={"name": "Other"}).get_json()["id"]
check("key is unique in the organisation", lead.put(f"{B}/projects/{pid2}/ticket-key", json={"key": "PAY"}).status_code == 400)

# --- tickets
r = lead.post(B + "/tickets", json={"project_id": pid, "title": "Refund webhook times out", "priority": "high",
                                     "description": "The payment gateway refund webhook times out after 30 seconds.", "assignees": [ud]})
check("ticket created as PAY-1", r.status_code == 201 and r.get_json()["ticket"]["key"] == "PAY-1", r.get_json())
t1 = r.get_json()["ticket"]["id"]
check("assignee listed", [p["user_id"] for p in r.get_json()["people"]["assignees"]] == [ud])
t2 = dev.post(B + "/tickets", json={"project_id": pid, "title": "Currency rounding on invoices"}).get_json()["ticket"]["id"]
check("numbers go up per project (PAY-2)", dev.get(f"{B}/tickets/{t2}").get_json()["ticket"]["key"] == "PAY-2")
check("outsider can't see a ticket", out.get(f"{B}/tickets/{t1}").status_code == 404)
check("outsider can't create in the project", out.post(B + "/tickets", json={"project_id": pid, "title": "x"}).status_code == 404)
check("outsider's list is empty", out.get(B + "/tickets").get_json()["tickets"] == [])
mine = dev.get(B + "/tickets?scope=mine").get_json()["tickets"]
check("'mine' = assigned to me", [t["key"] for t in mine] == ["PAY-1"], mine)
check("search by key", [t["key"] for t in lead.get(B + "/tickets?q=pay-2").get_json()["tickets"]] == ["PAY-2"])
check("search by words", [t["key"] for t in lead.get(B + "/tickets?q=webhook").get_json()["tickets"]] == ["PAY-1"])

# assigning someone outside the project lets them see and work on it
lead.put(f"{B}/tickets/{t1}/people/{ux}", json={"role": "assignee"})
check("assignee outside the project can see it", out.get(f"{B}/tickets/{t1}").status_code == 200)
check("...and change its status", out.patch(f"{B}/tickets/{t1}", json={"status": "in_progress"}).status_code == 200)
lead.delete(f"{B}/tickets/{t1}/people/{ux}?role=assignee")
check("removed again: can't see it", out.get(f"{B}/tickets/{t1}").status_code == 404)
check("bad status refused", dev.patch(f"{B}/tickets/{t1}", json={"status": "nope"}).status_code == 400)
own_view = own.get(f"{B}/tickets/{t1}")
check("org admin sees every ticket", own_view.status_code == 200)

# a watcher outside the project can follow but not change it
lead.put(f"{B}/tickets/{t2}/people/{ux}", json={"role": "watcher"})
check("watcher sees it", out.get(f"{B}/tickets/{t2}").status_code == 200)
check("watcher can't change it", out.patch(f"{B}/tickets/{t2}", json={"status": "done"}).status_code == 403)
check("watcher can unwatch themselves", out.delete(f"{B}/tickets/{t2}/people/{ux}?role=watcher").status_code == 200)

dev.post(f"{B}/tickets/{t1}/comments", json={"body": "Reproduced with a 2MB payload; the gateway retries 3 times."})
det = lead.get(f"{B}/tickets/{t1}").get_json()
kinds = [x["type"] + ":" + x.get("kind", "") for x in det["timeline"]]
check("timeline has created, assigned, status and the comment", {"event:created", "event:assigned", "event:status", "comment:"} <= set(kinds), kinds)
check("timeline link entries keep type=link", all(x["type"] in ("event", "comment", "link") for x in det["timeline"]))

# --- tagging by typed key: team chat
th = {t["unit_id"]: t["id"] for t in dev.get(B + "/threads").get_json()["threads"] if t["kind"] == "team"}
team_tid = th[U["Engineering"]]
dev.post(f"{B}/threads/{team_tid}/messages", json={"body": "PAY-1: raising the webhook timeout to 60s fixes it locally"})
det = lead.get(f"{B}/tickets/{t1}").get_json()
check("key in team chat links the message", any(l["type"] == "message" and l["how"] == "key" for l in det["links"]), det["links"])
check("label is generic", det["links"][0]["label"].startswith("Team chat"))
mid = det["links"][0]["open"]
check("team member sees the content", det["links"][0]["content"] and "60s" in det["links"][0]["content"])
check("lower-case 'pay-1' doesn't link (keys are typed in capitals)",
      dev.post(f"{B}/threads/{team_tid}/messages", json={"body": "pay-1 is weird"}).status_code == 201
      and len(lead.get(f"{B}/tickets/{t1}").get_json()["links"]) == 1)

# --- direct message with a key: summary-only for others on the ticket
dm = dev.post(B + "/threads/direct", json={"user_id": ul}).get_json()["thread_id"]
dev.post(f"{B}/threads/{dm}/messages", json={"body": "PAY-1 between us: the vendor admitted their retry logic is broken"})
link_dm = [l for l in dev.get(f"{B}/tickets/{t1}").get_json()["links"] if l["label"] == "Direct message"]
check("key in a DM links it too", len(link_dm) == 1)
check("DM participant sees the content", "vendor admitted" in (link_dm[0]["content"] or ""))
qa_dm = [l for l in qa.get(f"{B}/tickets/{t1}").get_json()["links"] if l["label"] == "Direct message"][0]
check("someone else on the ticket sees no DM content", qa_dm["content"] is None and qa_dm["open"] is None, qa_dm)
# summaries: written by the job (fake model here)
n = tickets.write_link_summaries(cur, lambda m: "Vendor retry logic is the cause."); conn.commit()
qa_dm = [l for l in qa.get(f"{B}/tickets/{t1}").get_json()["links"] if l["label"] == "Direct message"][0]
check("...only its one-line summary", n >= 2 and qa_dm["summary"] == "Vendor retry logic is the cause.", (n, qa_dm))

# --- manual tagging + sharing
dm_msgs = dev.get(f"{B}/threads/{dm}/messages").get_json()["messages"]
dev.post(f"{B}/threads/{dm}/messages", json={"body": "Also: the gateway sandbox ignores timeouts entirely"})
last = dev.get(f"{B}/threads/{dm}/messages").get_json()["messages"][-1]["id"]
check("can't tag a message you can't see", qa.post(f"{B}/tickets/{t1}/links", json={"source_type": "message", "source_id": last}).status_code == 404)
r = dev.post(f"{B}/tickets/{t1}/links", json={"source_type": "message", "source_id": last, "shared": True})
check("manual tag with 'share'", r.status_code == 201)
shared = [l for l in qa.get(f"{B}/tickets/{t1}").get_json()["links"] if l["how"] == "manual"][0]
check("shared content visible to others on the ticket", "sandbox ignores" in (shared["content"] or ""), shared)
on_item = dev.get(f"{B}/ticket-links?source_type=message&source_id={last}").get_json()["tickets"]
check("the message knows its ticket", [t["key"] for t in on_item] == ["PAY-1"])
check("unlink by someone else not working on it", out.delete(f"{B}/tickets/{t1}/links/{shared['id']}").status_code == 404)
check("tagger can unlink", dev.delete(f"{B}/tickets/{t1}/links/{shared['id']}").status_code == 200)

# --- AI suggestion after a private call
m = q("INSERT INTO corp.meetings (org_id, unit_id, title, source, status, transcript, summary, created_by) "
      "VALUES (%s, NULL, 'Call with gateway vendor', 'record', 'ready', %s, %s, %s) RETURNING id",
      (org, "We talked about the refund webhook timing out. They will patch the retry logic by Friday.",
       "Vendor will patch refund webhook retries by Friday.", ud))[0]["id"]
q("INSERT INTO corp.meeting_participants VALUES (%s,%s)", (m, ud))
pushed = []
n = tickets.suggest_for_meetings(cur, lambda msgs: '{"ids": [%d]}' % t1, push=lambda *a: pushed.append(a)); conn.commit()
check("call suggested for PAY-1", n == 1)
check("participant gets 'Was this about PAY-1?'", pushed and pushed[0][0] == ud and "PAY-1" in pushed[0][1], pushed)
dsug = dev.get(f"{B}/tickets/{t1}").get_json()["suggestions"]
check("participant sees the suggestion", len(dsug) == 1 and dsug[0]["type"] == "meeting")
check("others on the ticket don't", lead.get(f"{B}/tickets/{t1}").get_json()["suggestions"] == [])
check("non-participant can't confirm", lead.post(f"{B}/tickets/{t1}/links/{dsug[0]['id']}/confirm").status_code == 403)
r = dev.post(f"{B}/tickets/{t1}/links/{dsug[0]['id']}/confirm").get_json()
call_link = [l for l in r["links"] if l["type"] == "meeting"]
check("confirmed: linked", len(call_link) == 1 and r["suggestions"] == [])
lv = [l for l in lead.get(f"{B}/tickets/{t1}").get_json()["links"] if l["type"] == "meeting"][0]
check("private call: others see 'Call' + its summary, not the transcript",
      lv["label"].startswith("Private meeting") and lv["content"] is None and "Friday" in (lv["summary"] or ""), lv)
check("not scanned twice", tickets.suggest_for_meetings(cur, lambda msgs: '{"ids": [%d]}' % t1) == 0)
# rejected suggestions stay rejected
m2 = q("INSERT INTO corp.meetings (org_id, unit_id, title, source, status, transcript, summary, created_by) "
       "VALUES (%s, %s, 'Standup', 'record', 'ready', %s, 'Standup', %s) RETURNING id",
       (org, U["Engineering"], "Invoices currency rounding is still wrong for JPY invoices", ul))[0]["id"]
tickets.suggest_for_meetings(cur, lambda msgs: '{"ids": [%d]}' % t2); conn.commit()
s2 = lead.get(f"{B}/tickets/{t2}").get_json()["suggestions"]
check("team meeting suggested to a team member", len(s2) == 1)
lead.post(f"{B}/tickets/{t2}/links/{s2[0]['id']}/reject")
check("rejected: gone", lead.get(f"{B}/tickets/{t2}").get_json()["suggestions"] == [])
q("UPDATE corp.meetings SET tickets_scanned_at = NULL WHERE id = %s", (m2,))
tickets.suggest_for_meetings(cur, lambda msgs: '{"ids": [%d]}' % t2); conn.commit()
check("...and never suggested again", lead.get(f"{B}/tickets/{t2}").get_json()["suggestions"] == [])

# --- brief and Ask
row = q("SELECT * FROM corp.tickets WHERE id = %s", (t1,))[0]
seen = {}
def fake_brief(msgs):
    seen["material"] = msgs[1]["content"]; return "- What we know: vendor retry logic."
tickets.refresh_brief(cur, fake_brief, row); conn.commit()
check("brief saved", dev.get(f"{B}/tickets/{t1}").get_json()["ticket"]["summary"].startswith("- What we know"))
check("brief uses the DM's summary, never its text", "Vendor retry logic is the cause." in seen["material"]
      and "vendor admitted" not in seen["material"], seen["material"][:600])
ans = insights.ask(cur, lambda msgs: f"It's in progress [K{t1}].", org, ud, "What's the latest on PAY-1?")
check("Ask finds the ticket by key", any(s["type"] == "ticket" and s["id"] == t1 for s in ans["sources"]), ans)
ans = insights.ask(cur, lambda msgs: f"See [K{t2}].", org, ud, "anything about rounding on invoices?")
check("Ask finds a ticket by its words", any(s["type"] == "ticket" and s["id"] == t2 for s in ans["sources"]), ans)
ans = insights.ask(cur, lambda msgs: "nothing", org, ux, "What's the latest on PAY-1?")
check("Ask never shows a ticket to someone who can't see it", not any(s["type"] == "ticket" for s in ans["sources"]))
cu = insights.catch_up(cur, lambda msgs: f"- PAY-1 moved [K{t1}]", org, ud, 7)
check("catch-up includes recent tickets", any(s["type"] == "ticket" for s in cu["sources"]), cu)
dash = lead.get(f"{B}/projects/{pid}").get_json()
check("project dashboard counts open tickets", dash["counts"]["tickets_open"] == 2 and dash["project"]["ticket_key"] == "PAY")

# --- key can't change once tickets exist; status done closes
check("key locked once there are tickets", lead.put(f"{B}/projects/{pid}/ticket-key", json={"key": "PMT"}).status_code == 400)
dev.patch(f"{B}/tickets/{t2}", json={"status": "done"})
check("done tickets leave the open list", [t["key"] for t in dev.get(B + "/tickets").get_json()["tickets"]] == ["PAY-1"])
check("...and show with status=all", len(dev.get(B + "/tickets?status=all").get_json()["tickets"]) == 2)

print(f"\n{sum(results)}/{len(results)} passed")
