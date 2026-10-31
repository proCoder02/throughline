"""The personality card -- a short summary the user has seen and approved,
deliberately separate from everything else the app knows about them. A
first-time user gets a draft built from their onboarding persona and the
likes the app has learned (preferences); nothing reaches another person
until they save it. Emotional-intelligence data, notes about other people
and conversation content are never read here."""
from __future__ import annotations

MAX_LIST = 8
MAX_TEXT = 280
MAX_HEADLINE = 80


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


def get_saved(cur, user_id: int) -> dict | None:
    cur.execute("SELECT headline, about, interests, likes, sharing, updated_at FROM meet_cards WHERE user_id = %s", (user_id,))
    return cur.fetchone()


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
    return {
        "headline": _clean_text(headline, MAX_HEADLINE),
        "about": "",
        "interests": _clean_list(interests),
        "likes": _clean_list(likes),
        "sharing": True,
    }


def save(cur, user_id: int, data: dict) -> dict:
    card = {
        "headline": _clean_text(data.get("headline"), MAX_HEADLINE),
        "about": _clean_text(data.get("about"), MAX_TEXT),
        "interests": _clean_list(data.get("interests")),
        "likes": _clean_list(data.get("likes")),
        "sharing": bool(data.get("sharing", True)),
    }
    cur.execute(
        "INSERT INTO meet_cards (user_id, headline, about, interests, likes, sharing, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, now()) "
        "ON CONFLICT (user_id) DO UPDATE SET headline = EXCLUDED.headline, about = EXCLUDED.about, "
        "interests = EXCLUDED.interests, likes = EXCLUDED.likes, sharing = EXCLUDED.sharing, updated_at = now()",
        (user_id, card["headline"], card["about"], card["interests"], card["likes"], card["sharing"]),
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
    return {
        **identity(cur, user_id),
        "headline": saved["headline"],
        "about": saved["about"],
        "interests": list(saved["interests"] or []),
        "likes": list(saved["likes"] or []),
    }
