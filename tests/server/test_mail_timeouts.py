"""A slow or unreachable Outlook/Gmail is an ordinary failure, not a crash:
the mail helpers raise their own API error, the tools return {"error": ...},
and the Corporate email scan carries on for everyone else (it only fails, so
ops alerts, when every person's scan failed). Needs no database."""
import os, sys
sys.path.insert(0, os.getcwd())
import requests

results = []
def check(n, c, x=""):
    results.append(bool(c)); print(("PASS " if c else "FAIL ") + n + (f"  -- {x}" if x != "" and not c else ""))

from outlook import graph
from gmail import google
from corporate import mail

real_get = requests.get
def boom(exc):
    def f(*a, **k):
        raise exc
    return f

# --- the helpers
for name, mod, err in (("Outlook", graph, graph.GraphError), ("Gmail", google, google.GoogleError)):
    for exc, status in ((requests.ReadTimeout("Read timed out. (read timeout=20)"), 504),
                        (requests.ConnectionError("connection refused"), 503)):
        requests.get = boom(exc)
        try:
            mod.get("/x", "token")
            check(f"{name}: {type(exc).__name__} raises", False, "nothing raised")
        except err as e:
            check(f"{name}: {type(exc).__name__} becomes {err.__name__} {status}", e.status == status, e.status)
        except Exception as e:
            check(f"{name}: {type(exc).__name__} becomes {err.__name__}", False, repr(e))
        finally:
            requests.get = real_get

# --- the shared tool wrapper turns it into an error result (the scan then moves on)
from mail_common import Mailboxes
mb = Mailboxes(provider="outlook", label="Outlook", get_accounts=lambda cur, uid: [{"id": 1, "email": "a@x.test"}],
               ensure_token=lambda cur, db, a: "tok", mark_needs_reconnect=lambda *a: None,
               api_error=graph.GraphError, not_connected=RuntimeError)
@mb.tool
def listing(cur, db, accounts, args):
    done, broken = mb.across(cur, db, accounts, lambda token, a: graph.get("/me/messages", token))
    return {"ok": True}
requests.get = boom(requests.ReadTimeout("Read timed out."))
try:
    r = listing(None, None, 1, {})
    check("tool returns an error instead of raising", isinstance(r, dict) and "didn't answer in time" in r.get("error", ""), r)
except Exception as e:
    check("tool returns an error instead of raising", False, repr(e))
finally:
    requests.get = real_get

# --- the scan: one person failing doesn't stop the others
class Cur:
    def __init__(self, rows): self.rows = rows
    def execute(self, *a): pass
    def fetchall(self): return self.rows
class Conn:
    rollbacks = 0
    def rollback(self): Conn.rollbacks += 1

real_person = mail._scan_person
def person(cur, conn, llm, handler, org_id, user_id):
    if user_id == 2:
        raise requests.ReadTimeout("Read timed out.")
    return 1
mail._scan_person = person
try:
    n = mail.scan(Cur([{"org_id": 1, "user_id": 1}, {"org_id": 1, "user_id": 2}, {"org_id": 1, "user_id": 3}]), Conn(), None, lambda name: None)
    check("others still scanned when one fails", n == 2, n)
    check("failed person's transaction rolled back", Conn.rollbacks == 1, Conn.rollbacks)
except Exception as e:
    check("others still scanned when one fails", False, repr(e))
mail._scan_person = lambda *a: (_ for _ in ()).throw(requests.ReadTimeout("Read timed out."))
try:
    mail.scan(Cur([{"org_id": 1, "user_id": 1}]), Conn(), None, lambda name: None)
    check("fails (so ops alerts) when everyone failed", False, "no error")
except RuntimeError as e:
    check("fails (so ops alerts) when everyone failed", "failed for everyone" in str(e), str(e))
check("no links: nothing to do", mail.scan(Cur([]), Conn(), None, lambda name: None) == 0)
mail._scan_person = real_person

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
