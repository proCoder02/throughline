"""The personality card -- a short summary the user has seen and approved,
deliberately separate from everything else the app knows about them. A
first-time user gets a draft built from their onboarding persona and the
likes the app has learned (preferences); nothing reaches another person
until they save it. Emotional-intelligence data, notes about other people
and conversation content are never read here (the optional "suggest my
personality" draft is produced elsewhere and only ever lands in the editor,
for the user to accept or change).

Front (the first 3 seconds): face, name, role, a one-line hook, top
traits, what the two of you have in common, and "ask me about".
Behind "More": how I communicate, about, education, all interests/likes.
  who I am     -- headline (role), organization, city, education
  personality  -- up to 4 traits, a "how I communicate" line, about, hook
  what I'm into and how to stay in touch -- interests, likes, links, ask_me
  look         -- color, one of COLORS (empty = picked from the name)"""
from __future__ import annotations

import re

MAX_LIST = 8
MAX_TRAITS = 4
MAX_LINKS = 3
MAX_TEXT = 280
MAX_SHORT = 80

# Offered as one-tap chips in the editor (custom ones are allowed too).
TRAIT_OPTIONS = [
    "Curious", "Calm", "Direct", "Warm", "Analytical", "Creative", "Organised", "Spontaneous",
    "Good listener", "Big-picture thinker", "Detail-oriented", "Adventurous", "Funny", "Thoughtful",
    "Driven", "Easygoing", "Introvert", "Extrovert", "Optimistic", "Practical",
]

# Card colours (the app and web map each name to a shade that works in
# light and dark mode).
COLORS = ["amber", "coral", "teal", "indigo", "plum", "forest"]

TEXT_FIELDS = {"headline": MAX_SHORT, "organization": MAX_SHORT, "city": MAX_SHORT, "education": MAX_SHORT,
               "about": MAX_TEXT, "style": 160, "hook": 90, "ask_me": MAX_SHORT}
LIST_FIELDS = ("interests", "likes", "traits", "links")
ALL_FIELDS = tuple(TEXT_FIELDS) + LIST_FIELDS


def _clean_list(values, limit=MAX_LIST, item_len=40) -> list[str]:
    out = []
    for v in values or []:
        if not isinstance(v, str):
            continue
        v = " ".join(v.split())[:item_len]
        if v and v.lower() not in (x.lower() for x in out):
            out.append(v)
        if len(out) >= limit:
            break
    return out


def _clean_text(value, limit) -> str:
    return " ".join(str(value or "").split())[:limit]


_HOST_RE = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}(/.*)?$", re.IGNORECASE)


def clean_links(values) -> list[str]:
    """http(s) links only (a bare "linkedin.com/in/x" gets https:// added);
    anything else -- javascript:, mailto:, phone numbers -- is dropped."""
    out = []
    for v in values or []:
        if not isinstance(v, str):
            continue
        v = v.strip()
        if not v or " " in v or len(v) > 200:
            continue
        if not re.match(r"^https?://", v, re.IGNORECASE):
            if not _HOST_RE.match(v):
                continue
            v = "https://" + v
        if v.lower() not in (x.lower() for x in out):
            out.append(v)
        if len(out) >= MAX_LINKS:
            break
    return out


def _from_row(row: dict) -> dict:
    card = {k: row.get(k) or "" for k in TEXT_FIELDS}
    card.update({k: list(row.get(k) or []) for k in LIST_FIELDS})
    card["sharing"] = row.get("sharing", True)
    card["color"] = row.get("color") or ""
    return card


def get_saved(cur, user_id: int) -> dict | None:
    cur.execute("SELECT * FROM meet_cards WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    return _from_row(row) if row else None


def draft(cur, user_id: int) -> dict:
    """Suggested card for someone who hasn't saved one: occupation as the
    headline, persona interests + hobbies, and their strongest learned
    likes. Each source is optional -- a brand-new user just gets blanks."""
    headline, interests, likes = "", [], []
    cur.execute("SAVEPOINT meet_draft")
    try:
        cur.execute("SELECT occupation, interests, hobbies FROM user_personas WHERE user_id = %s", (user_id,))
        p = cur.fetchone()
        if p:
            headline = p.get("occupation") or ""
            interests = list(p.get("interests") or []) + list(p.get("hobbies") or [])
        cur.execute(
            "SELECT item FROM preferences WHERE user_id = %s AND COALESCE(weight, 0) > 0 "
            "ORDER BY COALESCE(weight, 0) * COALESCE(confidence, 0.5) DESC LIMIT %s",
            (user_id, MAX_LIST),
        )
        likes = [r["item"] for r in cur.fetchall()]
        cur.execute("RELEASE SAVEPOINT meet_draft")
    except Exception as e:  # a missing optional table must not break the card screen
        cur.execute("ROLLBACK TO SAVEPOINT meet_draft")
        print(f"[meet] card draft partial: {e!r}")
    card = {k: "" for k in TEXT_FIELDS}
    card.update({"headline": _clean_text(headline, MAX_SHORT), "interests": _clean_list(interests),
                 "likes": _clean_list(likes), "traits": [], "links": [], "color": "", "sharing": True})
    return card


def clean(data: dict) -> dict:
    card = {k: _clean_text(data.get(k), limit) for k, limit in TEXT_FIELDS.items()}
    card["interests"] = _clean_list(data.get("interests"))
    card["likes"] = _clean_list(data.get("likes"))
    card["traits"] = _clean_list(data.get("traits"), limit=MAX_TRAITS, item_len=24)
    card["links"] = clean_links(data.get("links"))
    card["sharing"] = bool(data.get("sharing", True))
    card["color"] = data.get("color") if data.get("color") in COLORS else ""
    return card


def save(cur, user_id: int, data: dict) -> dict:
    card = clean(data)
    cols = list(ALL_FIELDS) + ["color", "sharing"]
    cur.execute(
        f"INSERT INTO meet_cards (user_id, {', '.join(cols)}, updated_at) VALUES (%s, {', '.join(['%s'] * len(cols))}, now()) "
        f"ON CONFLICT (user_id) DO UPDATE SET {', '.join(f'{c} = EXCLUDED.{c}' for c in cols)}, updated_at = now()",
        [user_id] + [card[c] for c in cols],
    )
    return card


def identity(cur, user_id: int) -> dict:
    """Name + photo only -- what the confirm screen shows before anyone
    has agreed to share."""
    cur.execute("SELECT id, username, profile_picture_url FROM users WHERE id = %s", (user_id,))
    u = cur.fetchone() or {}
    return {"id": u.get("id"), "name": u.get("username"), "photo": u.get("profile_picture_url")}


def public_card(cur, user_id: int) -> dict | None:
    """What the other person sees. None when there's no saved card or
    sharing is switched off."""
    saved = get_saved(cur, user_id)
    if not saved or not saved["sharing"]:
        return None
    card = {k: saved[k] for k in ALL_FIELDS}
    card["color"] = saved["color"]
    return {**identity(cur, user_id), **card}


def in_common(mine: dict | None, theirs: dict | None, limit: int = 4) -> list[str]:
    """What two people share -- the best conversation starter on the card.
    Interests/likes/traits matched case-insensitively, plus the same city,
    organisation or education. In the order they appear on THEIR card."""
    if not mine or not theirs:
        return []
    out: list[str] = []

    def add(label):
        if label and label.lower() not in (x.lower() for x in out) and len(out) < limit:
            out.append(label)

    for field in ("city", "organization", "education"):
        a, b = (mine.get(field) or "").strip(), (theirs.get(field) or "").strip()
        if a and b and a.lower() == b.lower():
            add(b)
    mine_items = {x.lower() for f in ("interests", "likes", "traits") for x in (mine.get(f) or [])}
    for f in ("interests", "likes", "traits"):
        for x in theirs.get(f) or []:
            if x.lower() in mine_items:
                add(x)
    return out


def clean_suggestion(raw) -> dict:
    """Whatever the personality suggester returned, trimmed to card shape."""
    raw = raw if isinstance(raw, dict) else {}
    return {
        "traits": _clean_list(raw.get("traits"), limit=MAX_TRAITS, item_len=24),
        "style": _clean_text(raw.get("style"), TEXT_FIELDS["style"]),
    }
