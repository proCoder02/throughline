"""The permission filter. Every read of work content goes through these SQL
conditions, so nothing outside a person's teams reaches a page or the AI.

  team content (unit_id set)   -> members of that unit only (seniority alone grants nothing)
  private content (no unit)    -> its participants only
  a commitment                 -> also always its owner and the person it is owed to

Each function returns an SQL condition over alias x, using the named
parameter %(me)s for the viewer's user id."""

MY_UNITS = "(SELECT unit_id FROM corp.unit_members WHERE user_id = %(me)s)"


def meetings() -> str:
    return (f"(x.unit_id IN {MY_UNITS} OR (x.unit_id IS NULL AND EXISTS ("
            "SELECT 1 FROM corp.meeting_participants p WHERE p.meeting_id = x.id AND p.user_id = %(me)s)))")


def commitments() -> str:
    return (f"(x.unit_id IN {MY_UNITS} OR x.owner_id = %(me)s OR x.owed_to_id = %(me)s "
            "OR (x.unit_id IS NULL AND x.created_by = %(me)s))")


def decisions() -> str:
    return (f"(x.unit_id IN {MY_UNITS} OR (x.unit_id IS NULL AND EXISTS ("
            "SELECT 1 FROM corp.meeting_participants p WHERE p.meeting_id = x.meeting_id AND p.user_id = %(me)s)))")


def threads() -> str:
    return (f"((x.kind = 'team' AND x.unit_id IN {MY_UNITS}) OR (x.kind = 'direct' AND EXISTS ("
            "SELECT 1 FROM corp.thread_members t WHERE t.thread_id = x.id AND t.user_id = %(me)s)))")


def blockers() -> str:
    return f"(x.unit_id IN {MY_UNITS})"


def one(cur, table: str, cond: str, row_id: int, me: int, org_id: int):
    """The row if the viewer may see it, else None (callers answer 404)."""
    cur.execute(f"SELECT x.* FROM corp.{table} x WHERE x.id = %(id)s AND x.org_id = %(org)s AND {cond}",
                {"id": row_id, "org": org_id, "me": me})
    return cur.fetchone()


def unit_member_ids(cur, unit_id: int) -> list:
    cur.execute("SELECT user_id FROM corp.unit_members WHERE unit_id = %s", (unit_id,))
    return [r["user_id"] for r in cur.fetchall()]
