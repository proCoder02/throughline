"""Every AI prompt Corporate uses, in one place. Prompts are short and every
input is capped, to keep token use low. call_llm(messages) -> str is handed
in by the host app."""
from __future__ import annotations

import json
import os
import re
from datetime import date

TRANSCRIPT_CAP = 16000
EVIDENCE_CAP = 5000
DISCUSSION_CAP = 10000
ASK_CAP = 12000


def parse_json(text):
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


class AIUnavailable(Exception):
    """The AI service itself failed (down, out of credit, rate limited) --
    different from a reply we couldn't read. Callers tell the user plainly
    instead of saving empty results."""

    def __init__(self, cause):
        text = str(cause)
        if "402" in text or "Payment Required" in text:
            msg = "The AI service has reached its usage limit. Please try again later."
        elif "429" in text:
            msg = "The AI service is busy. Please try again in a minute."
        else:
            msg = "The AI service isn't reachable right now. Please try again later."
        super().__init__(msg)
        self.cause = text[:300]


def _call(call_llm, messages) -> str:
    try:
        return call_llm(messages) or ""
    except AIUnavailable:
        raise
    except Exception as e:  # noqa: BLE001
        raise AIUnavailable(e) from e


def _collect(task: str, messages: list, raw: str) -> None:
    """Training examples for the self-hosted model (ml/README.md): when
    CORPORATE_COLLECT_DIR is set, every answer that parsed is saved as one JSONL
    line per task. Off by default; only ever enable it on test data."""
    folder = os.getenv("CORPORATE_COLLECT_DIR")
    if not folder:
        return
    try:
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, f"{task}.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"messages": messages + [{"role": "assistant", "content": raw.strip()}]}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _ask(call_llm, system: str, user: str, task: str = "other"):
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    raw = _call(call_llm, messages)
    out = parse_json(raw)
    if out is not None:
        _collect(task, messages, raw)
    return out


def clip(text: str, cap: int) -> str:
    text = text or ""
    return text if len(text) <= cap else text[: cap // 3] + "\n...\n" + text[-(cap * 2 // 3):]


EXTRACT_SYS = (
    "Extract from a work discussion. JSON only: "
    '{"summary":"<=4 sentences","decisions":[{"title":"<=8 words","text":"","made_by":null,"quote":"","updates_id":null}],'
    '"commitments":[{"text":"","owner":null,"owed_to":null,"due":null,"quote":""}],'
    '"blockers":[{"text":"","quote":""}]}. '
    "Only explicit items. due = YYYY-MM-DD from today's date, or null. owner/owed_to = names as said, or null. "
    "updates_id = id of an existing decision this one changes, else null. quote = short exact words. Empty lists if none."
)


def extract(call_llm, text: str, people: list, decisions: list, summary: bool = True):
    known = ", ".join(people) or "unknown"
    existing = "\n".join(f"[{d['id']}] {d['title']}: {d['text'][:160]}" for d in decisions[:30]) or "none"
    sys = EXTRACT_SYS if summary else EXTRACT_SYS.replace('"summary":"<=4 sentences",', "")
    out = _ask(call_llm, sys, f"Today: {date.today().isoformat()}\nPeople: {known}\nExisting decisions:\n{existing}\n\n"
                              f"Discussion:\n{clip(text, TRANSCRIPT_CAP)}", task="extract")
    out = out if isinstance(out, dict) else {}
    return {k: out.get(k) if isinstance(out.get(k), list) else [] for k in ("decisions", "commitments", "blockers")} | {
        "summary": str(out.get("summary") or "")[:1500]}


def evidence(call_llm, commitment: str, owner: str, activity: str):
    out = _ask(call_llm,
               'Did the new activity show this commitment was fulfilled? JSON only: {"done":true|false,"quote":"exact words or empty"}. '
               "Say done only on clear evidence.",
               f"Commitment ({owner or 'unknown owner'}): {commitment}\n\nNew activity:\n{clip(activity, EVIDENCE_CAP)}", task="evidence")
    return bool(out and out.get("done") is True), str((out or {}).get("quote") or "")[:400]


def radar(call_llm, blocker: str, candidates: list):
    lines = "\n".join(f"[{c['id']}] {c['text'][:200]} (due {c['due_date']})" for c in candidates[:25])
    out = _ask(call_llm,
               'Which commitments could this blocker delay? JSON only: {"ids":[ids],"why":"one short sentence"}. '
               "Only clear links on the same work item.",
               f"Blocker: {blocker}\n\nCommitments:\n{lines}", task="radar")
    ids = [i for i in (out or {}).get("ids") or [] if isinstance(i, int)]
    return ids, str((out or {}).get("why") or "")[:300]


def common_ground(call_llm, discussion: str, notes: list):
    extra = ("\nWork notes the participants chose to share:\n" + "\n".join(notes)) if notes else ""
    out = _ask(call_llm,
               "Help a work group reach common ground. Use only what was said (and shared notes). Neutral, no judging people. "
               'JSON only: {"positions":[{"name":"","position":"<=15 words"}],"agreed":["..."],"open":["..."],"options":["..."]}',
               f"Discussion:\n{clip(discussion, DISCUSSION_CAP)}{extra}", task="common_ground")
    if not isinstance(out, dict):
        return None
    return {k: [x for x in (out.get(k) or []) if x][:8] for k in ("positions", "agreed", "open", "options")}


def catch_up(call_llm, who: str, focus: str, sources: str) -> str:
    text = _call(call_llm, [
        {"role": "system", "content": "Write a short work catch-up from the sources only. Plain text, no markdown bold. "
                                      "Sections, each 1-4 lines starting with '- ': What changed, Decided, At risk, Needs you. "
                                      "Skip a section with nothing in it. Cite sources like [M3], [D2], [C5], [K4]. Be specific: names, dates."},
        {"role": "user", "content": f"For: {who}. Focus: {focus}.\n\nSources:\n{clip(sources, ASK_CAP)}"},
    ]).strip()
    return re.sub(r"[\u3010\uff3b]([MDCK]\d+)[\u3011\uff3d]", r"[\1]", text).replace("**", "")


def answer(call_llm, question: str, sources: str, history=None):
    earlier = "\n".join(f"{'Q' if h['role'] == 'user' else 'A'}: {h['content'][:600]}" for h in (history or []))
    context = f"Earlier in this conversation (to understand the question; facts still come from the sources):\n{earlier}\n\n" if earlier else ""
    text = _call(call_llm, [
        {"role": "system", "content": "Answer a work question only from the sources. Cite like [M3], [D2], [C5], [T7], [K4]. "
                                      "If the sources don't say, reply that you don't have that yet. Be brief, plain text."},
        {"role": "user", "content": f"Sources:\n{clip(sources, ASK_CAP)}\n\n{context}Question: {question}"},
    ]).strip()
    # Models sometimes cite with full-width brackets or add markdown bold.
    return re.sub(r"[【［]([MDCTK]\d+)[】］]", r"[\1]", text).replace("**", "")


# ---------------------------------------------------------------- tickets
TICKET_CAP = 9000


def link_summary(call_llm, kind: str, text: str) -> str:
    """One neutral sentence about a tagged message, email or call -- what
    others on the ticket see when the content itself is private."""
    out = _call(call_llm, [
        {"role": "system", "content": "Summarise this work item in one neutral sentence (max 30 words) for teammates on a ticket: "
                                      "what it says about the issue -- facts, decisions, next steps. No greetings, no personal "
                                      "details unrelated to the work. Plain text."},
        {"role": "user", "content": f"{kind}:\n{clip(text, 4000)}"},
    ])
    return out.strip().replace("**", "")[:300]


def ticket_match(call_llm, discussion: str, candidates: list) -> list:
    """Which open tickets a call or meeting was about (ids), for people to confirm."""
    lines = "\n".join(f"[{c['id']}] {c['key']}: {c['title'][:160]}" for c in candidates[:30])
    out = _ask(call_llm,
               'Which of these tickets did the discussion work on? JSON only: {"ids":[ids]}. '
               "Only clear matches: the same specific issue, not just the same project.",
               f"Tickets:\n{lines}\n\nDiscussion:\n{clip(discussion, EVIDENCE_CAP)}", task="ticket_match")
    return [i for i in (out or {}).get("ids") or [] if isinstance(i, int)]


def ticket_brief(call_llm, ticket: str, material: str) -> str:
    """What we know about a ticket so far, from what all its viewers may see."""
    text = _call(call_llm, [
        {"role": "system", "content": "Write a short brief on a work ticket from the material only. Plain text, no markdown bold. "
                                      "Sections, each 1-4 lines starting with '- ': What we know, Decided, Still open, Next step. "
                                      "Skip a section with nothing in it. Be specific: names, dates."},
        {"role": "user", "content": f"Ticket: {ticket}\n\nMaterial:\n{clip(material, TICKET_CAP)}"},
    ]).strip()
    return text.replace("**", "")


def client_update(call_llm, project: str, client: str, recipient: str, sender: str, lines: str) -> dict:
    """A status update a salesperson can send to the client, in client-safe words,
    plus internal notes they should know first. Never sent automatically."""
    out = _ask(call_llm,
               "Draft a short status update from a vendor to its client, using ONLY the facts given. Client-safe: no internal "
               "team or employee names, no ticket keys, no blame, no internal jargon. For each deliverable say: done; or in "
               "progress, with its date. Never invent a date, a cause, a step being taken, or anyone's involvement that the "
               "facts don't state. If something is blocked or late and the facts give no new date, say plainly that it is "
               "delayed and that a revised date will follow shortly. Warm and professional, under 160 words, plain text, "
               "greeting and sign-off. In 'internal', list what the sender must confirm before sending (e.g. a revised date). "
               'JSON only: {"subject":"...","body":"...","internal":["things the sender should know or check before sending"]}',
               f"Project: {project}\nClient: {client or '-'}\nTo: {recipient or 'the client'}\nFrom: {sender}\n\nFacts:\n{clip(lines, 6000)}",
               task="client_update")
    if not isinstance(out, dict) or not out.get("body"):
        return {"subject": "", "body": "", "internal": []}
    return {"subject": str(out.get("subject") or "")[:200], "body": str(out["body"]).replace("**", "")[:4000],
            "internal": [str(x)[:300] for x in (out.get("internal") or []) if x][:8]}
