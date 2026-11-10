"""Who may do what inside an organisation. Pure functions over an OrgContext
(no DB), so the rules can be read and tested in one place.

The rules:
  - Owner / admin: everything in the organisation (only an owner can make
    someone an admin, and only an owner can delete the organisation).
  - A unit's manager (department head / team lead) manages everything BELOW
    their unit: creates and renames child units, appoints their managers,
    adds and removes people. In their own unit they can add and remove
    members, but can't appoint another manager or rename/delete it -- that
    is for the level above.
  - Everyone in the organisation can see the structure and the directory.
  - Seniority alone grants nothing: access comes from these explicit roles."""
from __future__ import annotations

from dataclasses import dataclass, field

ORG_ROLES = ("owner", "admin", "member")
INVITE_ROLES = ("admin", "member")
UNIT_ROLES = ("manager", "member")
UNIT_KINDS = ("department", "team")


@dataclass
class OrgContext:
    user_id: int
    org_id: int
    role: str                                       # my organisation role
    units: dict = field(default_factory=dict)       # unit id -> {"id", "parent_id", "kind", "name"}
    my_units: dict = field(default_factory=dict)    # unit id -> my role in that unit

    @property
    def is_admin(self) -> bool:
        return self.role in ("owner", "admin")

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"


def ancestors(units: dict, unit_id: int) -> list:
    """Ids from the unit's parent up to the top (not the unit itself)."""
    out, seen = [], {unit_id}
    parent = units.get(unit_id, {}).get("parent_id")
    while parent is not None and parent not in seen:
        out.append(parent)
        seen.add(parent)
        parent = units.get(parent, {}).get("parent_id")
    return out


def _manages(ctx: OrgContext, unit_id: int) -> bool:
    return ctx.my_units.get(unit_id) == "manager"


def manages_above(ctx: OrgContext, unit_id: int) -> bool:
    """I manage a unit strictly above this one."""
    return any(_manages(ctx, a) for a in ancestors(ctx.units, unit_id))


def can_edit_unit(ctx: OrgContext, unit_id: int) -> bool:
    """Rename / delete the unit, appoint or remove its managers."""
    return unit_id in ctx.units and (ctx.is_admin or manages_above(ctx, unit_id))


def can_manage_people(ctx: OrgContext, unit_id: int) -> bool:
    """Add / remove ordinary members of the unit, invite people into it."""
    return unit_id in ctx.units and (ctx.is_admin or _manages(ctx, unit_id) or manages_above(ctx, unit_id))


def can_create_under(ctx: OrgContext, parent_id) -> bool:
    if parent_id is None:
        return ctx.is_admin
    return can_manage_people(ctx, parent_id)


def valid_parent(kind: str, parent: dict | None) -> bool:
    """A department can't sit inside a team; everything else nests freely."""
    if parent is None:
        return True
    return not (kind == "department" and parent["kind"] == "team")


def can_invite(ctx: OrgContext, org_role: str, unit_id, unit_role) -> bool:
    if org_role == "admin" and not ctx.is_owner:
        return False
    if unit_id is None:
        return ctx.is_admin
    if unit_role == "manager":
        return can_edit_unit(ctx, unit_id)
    return can_manage_people(ctx, unit_id)


def can_set_org_role(ctx: OrgContext, target_role: str, new_role: str) -> bool:
    """Only an owner changes organisation roles, between admin and member.
    Making another owner (ownership transfer) is its own step, later."""
    if new_role not in ("admin", "member"):
        return False
    if ctx.is_owner:
        return target_role != "owner"
    return False


def can_remove_member(ctx: OrgContext, target_user_id: int, target_role: str) -> bool:
    if target_role == "owner":
        return False
    if target_user_id == ctx.user_id:
        return True                       # leaving
    if target_role == "admin":
        return ctx.is_owner
    return ctx.is_admin


def capabilities(ctx: OrgContext) -> dict:
    """What the UI should offer, per unit and for the organisation."""
    return {
        "org_admin": ctx.is_admin,
        "org_owner": ctx.is_owner,
        "create_department": ctx.is_admin,
        "units": {
            uid: {
                "edit": can_edit_unit(ctx, uid),
                "manage_people": can_manage_people(ctx, uid),
                "create_child": can_create_under(ctx, uid),
            }
            for uid in ctx.units
        },
    }
