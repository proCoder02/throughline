"""Departments and teams (one tree per organisation) and who is in each.
Permission checks happen in routes.py via roles.py; these functions only
read and write."""
from __future__ import annotations

import psycopg2

from .errors import CorpError


def list_units(cur, org_id: int) -> list:
    """Flat list (the client builds the tree from parent_id), each with its
    member count and its managers."""
    cur.execute(
        "SELECT u.id, u.parent_id, u.kind, u.name, "
        "(SELECT count(*) FROM corp.unit_members um WHERE um.unit_id = u.id) AS member_count "
        "FROM corp.units u WHERE u.org_id = %s ORDER BY lower(u.name)",
        (org_id,),
    )
    units = [dict(r) for r in cur.fetchall()]
    cur.execute(
        "SELECT um.unit_id, um.user_id, us.username FROM corp.unit_members um "
        "JOIN corp.units u ON u.id = um.unit_id JOIN users us ON us.id = um.user_id "
        "WHERE u.org_id = %s AND um.role = 'manager' ORDER BY lower(us.username)",
        (org_id,),
    )
    managers: dict = {}
    for r in cur.fetchall():
        managers.setdefault(r["unit_id"], []).append({"user_id": r["user_id"], "username": r["username"]})
    for u in units:
        u["managers"] = managers.get(u["id"], [])
    return units


def create_unit(cur, org_id: int, parent_id, kind: str, name: str) -> int:
    try:
        cur.execute("SAVEPOINT corp_unit")
        cur.execute(
            "INSERT INTO corp.units (org_id, parent_id, kind, name) VALUES (%s, %s, %s, %s) RETURNING id",
            (org_id, parent_id, kind, name),
        )
        unit_id = cur.fetchone()["id"]  # before RELEASE, which clears the result
        cur.execute("RELEASE SAVEPOINT corp_unit")
    except psycopg2.errors.UniqueViolation:
        cur.execute("ROLLBACK TO SAVEPOINT corp_unit")
        raise CorpError(409, f"There is already a unit called \"{name}\" here.")
    return unit_id


def rename_unit(cur, unit_id: int, name: str) -> None:
    try:
        cur.execute("SAVEPOINT corp_unit")
        cur.execute("UPDATE corp.units SET name = %s WHERE id = %s", (name, unit_id))
        cur.execute("RELEASE SAVEPOINT corp_unit")
    except psycopg2.errors.UniqueViolation:
        cur.execute("ROLLBACK TO SAVEPOINT corp_unit")
        raise CorpError(409, f"There is already a unit called \"{name}\" here.")


def delete_unit(cur, unit_id: int) -> None:
    """Deletes the unit and everything under it (memberships go with them;
    people stay in the organisation)."""
    cur.execute("DELETE FROM corp.units WHERE id = %s", (unit_id,))


def unit_members(cur, unit_id: int) -> list:
    cur.execute(
        "SELECT um.user_id, us.username, us.profile_picture_url, um.role, m.title "
        "FROM corp.unit_members um JOIN users us ON us.id = um.user_id "
        "JOIN corp.units u ON u.id = um.unit_id "
        "LEFT JOIN corp.members m ON m.org_id = u.org_id AND m.user_id = um.user_id "
        "WHERE um.unit_id = %s ORDER BY (um.role = 'manager') DESC, lower(us.username)",
        (unit_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def unit_role(cur, unit_id: int, user_id: int) -> str | None:
    cur.execute("SELECT role FROM corp.unit_members WHERE unit_id = %s AND user_id = %s", (unit_id, user_id))
    row = cur.fetchone()
    return row["role"] if row else None


def put_unit_member(cur, unit_id: int, user_id: int, role: str) -> None:
    cur.execute(
        "INSERT INTO corp.unit_members (unit_id, user_id, role) VALUES (%s, %s, %s) "
        "ON CONFLICT (unit_id, user_id) DO UPDATE SET role = EXCLUDED.role",
        (unit_id, user_id, role),
    )


def remove_unit_member(cur, unit_id: int, user_id: int) -> None:
    cur.execute("DELETE FROM corp.unit_members WHERE unit_id = %s AND user_id = %s", (unit_id, user_id))
