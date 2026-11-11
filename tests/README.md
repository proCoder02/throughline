# Tests

Plain Python scripts: each prints `PASS`/`FAIL` lines and ends with `N/M passed`.
Run everything from the repo root:

```
python tests/run_all.py              # server suites
python tests/run_all.py --browser    # + browser suites
python tests/run_all.py --ai         # + suites that call the real AI model
```

## Server suites (`tests/server/`)

Import `app.py` directly and use Flask's test client against the **local** database from `.env`
(Postgres + Redis must be running). They create their own users and organisations with random
names, so they can run repeatedly. The AI is replaced by fakes, except in `roleplay_corporate.py`.

| Suite | Covers |
|---|---|
| `test_corporate.py` | Organisations, departments and teams, roles, invitations, people |
| `test_corp_work.py` | Work chat, meetings, commitments, decisions, Ask, common ground, work notes |
| `test_corp_onboarding.py` | Setting up an organisation, CSV invitations, joining |
| `test_corp_projects.py` | Projects, client contacts, cross-team requests, dependencies, risk |
| `test_corp_tickets.py` | Tickets, keys, tagging by key / by hand / AI suggestion, privacy, brief |
| `test_corp_ask_threads.py` | Saved Ask chats, follow-ups, privacy |
| `test_corp_client_view.py` | Ticket roll-up into deliverables, stakeholders, client updates, weekly digest |
| `test_corp_identity.py` | Work email verification, invitations by work email, leaving and rejoining |
| `test_ai_quota.py` | Per-person daily AI limit |
| `test_legal.py` | Privacy and terms pages, consent, account deletion |

## Browser suites (`tests/browser/`)

Drive the built website in Chrome with Playwright against a running local server:

```
pip install playwright            # once; uses your installed Chrome (channel="chrome")
cd frontend && npx vite build     # the server serves static/app
python app.py                     # in another terminal, on :5000
python tests/run_all.py --browser
```

Screenshots go to `tests/browser/_shots/` (ignored by git). `e2e_corp_work.py` uses the real AI.

## Before a deploy

Run `python tests/run_all.py` and, for changes to the website, `--browser` as well.
