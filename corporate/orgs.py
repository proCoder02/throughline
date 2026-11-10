"""Organisations and their members. Every function takes a dict cursor and
leaves committing to the caller (routes.py), so a request is one transaction."""
from __future__ import annotations

from .errors import CorpError
from .roles import OrgContext


def clean_name(value, max_len: int, what: str) -> str:
    name = " ".join(str(value or "").split())
    if not name:
        raise CorpError(400, f"{what} can't be empty.")
    if len(name) > max_len:
        raise CorpError(400, f"{what} is too long (at most {max_len} characters).")
    return name


def my_orgs(cur, user_id: int) -> list:
    cur.execute(
        "SELECT o.id, o.name, m.role, "
        "(SELECT count(*) FROM corp.members x WHERE x.org_id = o.id) AS member_count "
        "FROM corp.members m JOIN corp.orgs o ON o.id = m.org_id "
        "WHERE m.user_id = %s ORDER BY o.name",
        (user_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def create_org(cur, user_id: int, name: str) -> int:
    cur.execute("INSERT INTO corp.orgs (name, created_by) VALUES (%s, %s) RETURNING id", (name, user_id))
    org_id = cur.fetchone()["id"]
    cur.execute("INSERT INTO corp.members (org_id, user_id, role) VALUES (%s, %s, 'owner')", (org_id, user_id))
    return org_id


def get_org(cur, org_id: int) -> dict | None:
    cur.execute("SELECT id, name, created_at FROM corp.orgs WHERE id = %s", (org_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def load_context(cur, org_id: int, user_id: int) -> OrgContext:
    """The caller's view of an organisation. 404 (not 403) when they aren't
    in it, so organisation ids can't be probed."""
    cur.execute("SELECT role FROM corp.members WHERE org_id = %s AND user_id = %s", (org_id, user_id))
    row = cur.fetchone()
    if not row:
        raise CorpError(404, "Organisation not found.")
    cur.execute("SELECT id, parent_id, kind, name FROM corp.units WHERE org_id = %s", (org_id,))
    units = {r["id"]: dict(r) for r in cur.fetchall()}
    cur.execute(
        "SELECT um.unit_id, um.role FROM corp.unit_members um JOIN corp.units u ON u.id = um.unit_id "
        "WHERE u.org_id = %s AND um.user_id = %s",
        (org_id, user_id),
    )
    my_units = {r["unit_id"]: r["role"] for r in cur.fetchall()}
    return OrgContext(user_id=user_id, org_id=org_id, role=row["role"], units=units, my_units=my_units)


def member_role(cur, org_id: int, user_id: int) -> str | None:
    cur.execute("SELECT role FROM corp.members WHERE org_id = %s AND user_id = %s", (org_id, user_id))
    row = cur.fetchone()
    return row["role"] if row else None


def list_members(cur, org_id: int, show_email: bool) -> list:
    cur.execute(
        "SELECT m.user_id, u.username, u.email, u.profile_picture_url, m.role, m.title, m.joined_at "
        "FROM corp.members m JOIN users u ON u.id = m.user_id WHERE m.org_id = %s "
        "ORDER BY (m.role = 'owner') DESC, (m.role = 'admin') DESC, lower(u.username)",
        (org_id,),
    )
    members = [dict(r) for r in cur.fetchall()]
    cur.execute(
        "SELECT um.user_id, um.unit_id, um.role, un.name, un.kind FROM corp.unit_members um "
        "JOIN corp.units un ON un.id = um.unit_id WHERE un.org_id = %s ORDER BY lower(un.name)",
        (org_id,),
    )
    by_user: dict = {}
    for r in cur.fetchall():
        by_user.setdefault(r["user_id"], []).append(
            {"id": r["unit_id"], "name": r["name"], "kind": r["kind"], "role": r["role"]})
    for m in members:
        m["units"] = by_user.get(m["user_id"], [])
        m["joined_at"] = m["joined_at"].isoformat()
        if not show_email:
            m.pop("email", None)
    return members


def set_member(cur, org_id: int, user_id: int, *, role=None, title=None) -> None:
    if role is not None:
        cur.execute("UPDATE corp.members SET role = %s WHERE org_id = %s AND user_id = %s", (role, org_id, user_id))
    if title is not None:
        cur.execute("UPDATE corp.members SET title = %s WHERE org_id = %s AND user_id = %s", (title, org_id, user_id))


def remove_member(cur, org_id: int, user_id: int) -> None:
    """Out of the organisation, and out of every unit in it."""
    cur.execute(
        "DELETE FROM corp.unit_members WHERE user_id = %s AND unit_id IN (SELECT id FROM corp.units WHERE org_id = %s)",
        (user_id, org_id),
    )
    cur.execute("DELETE FROM corp.members WHERE org_id = %s AND user_id = %s", (org_id, user_id))


def add_member(cur, org_id: int, user_id: int, role: str) -> None:
    cur.execute(
        "INSERT INTO corp.members (org_id, user_id, role) VALUES (%s, %s, %s) ON CONFLICT (org_id, user_id) DO NOTHING",
        (org_id, user_id, role),
    )


def hand_over(cur, user_id: int) -> None:
    """Before an account is deleted: every organisation it solely owns passes
    to its longest-serving admin, else its longest-serving member; one left
    with nobody is deleted."""
    cur.execute("SELECT to_regclass('corp.members') AS t")
    if cur.fetchone()["t"] is None:  # Corporate never used on this database
        return
    cur.execute("SELECT org_id FROM corp.members m WHERE user_id = %s AND role = 'owner' AND NOT EXISTS ("
                "SELECT 1 FROM corp.members o WHERE o.org_id = m.org_id AND o.role = 'owner' AND o.user_id <> %s)",
                (user_id, user_id))
    for r in cur.fetchall():
        cur.execute("SELECT user_id FROM corp.members WHERE org_id = %s AND user_id <> %s "
                    "ORDER BY (role = 'admin') DESC, joined_at LIMIT 1", (r["org_id"], user_id))
        heir = cur.fetchone()
        if heir:
            cur.execute("UPDATE corp.members SET role = 'owner' WHERE org_id = %s AND user_id = %s", (r["org_id"], heir["user_id"]))
        else:
            cur.execute("DELETE FROM corp.orgs WHERE id = %s", (r["org_id"],))


def rename_org(cur, org_id: int, name: str) -> None:
    cur.execute("UPDATE corp.orgs SET name = %s WHERE id = %s", (name, org_id))


def delete_org(cur, org_id: int) -> None:
    cur.execute("DELETE FROM corp.orgs WHERE id = %s", (org_id,))
