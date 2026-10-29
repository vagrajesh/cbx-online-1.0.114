"""Seeding and the rules about who may change a role.

Two invariants, both learned the hard way:

* **Nobody can grant what they do not hold.** Otherwise the first thing a new "user admin" does is
  give themselves `everything`.
* **The last role that can administer the system cannot be broken.** The 1.0.26 lockout taught us
  that a door with nobody on the other side is not security, it is a locked building — and here
  there would be no `unlock-owner.bat` to run.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from ..models import Role, User
from . import caps as C


def seed(db: Session) -> int:
    """Make sure every built-in and preset exists. Built-ins are re-synced to the shipped definition
    on every start — they are what the app's own checks assume, so they are not editable and not
    allowed to drift. Presets are created once and then belong to the owner."""
    n = 0
    for slug, spec in C.BUILTIN.items():
        row = db.query(Role).filter(Role.slug == slug).one_or_none()
        if row is None:
            db.add(Role(slug=slug, name=spec["name"], description=spec["description"],
                        caps=C.valid(spec["caps"]), rank=spec["rank"], locked=True))
            n += 1
        else:
            row.name, row.description = spec["name"], spec["description"]
            row.caps, row.rank, row.locked = C.valid(spec["caps"]), spec["rank"], True
    for slug, spec in C.PRESETS.items():
        if db.query(Role).filter(Role.slug == slug).one_or_none() is None:
            db.add(Role(slug=slug, name=spec["name"], description=spec["description"],
                        caps=C.valid(spec["caps"]), rank=spec["rank"], locked=False))
            n += 1
    db.flush()
    return n


def attach(db: Session, user: User) -> User:
    """Hang this person's Role row on them so `User.caps()` reads the database rather than the
    shipped defaults. Safe on a database where the table is not seeded yet."""
    try:
        rows = {r.slug: r for r in db.query(Role).all()}
        user._roles_by_slug = rows
        user._role_row = rows.get(user.role)
    except Exception:  # noqa: BLE001 — a missing table must never stop someone signing in
        user._role_row = None
        user._roles_by_slug = {}
    return user


def by_slug(db: Session, slug: str) -> Optional[Role]:
    return db.query(Role).filter(Role.slug == slug).one_or_none()


def grantable(db: Session, me: User) -> list[Role]:
    """The roles this person may hand out: strictly below their own rank, never their own or above.

    Rank, not a hard-coded table, is what stops a ladder — an admin cannot make an owner, and a role
    you invent slots in wherever you rank it."""
    if me.is_axl:
        return []
    mine = by_slug(db, me.role)
    ceiling = mine.rank if mine else 0
    if me.is_owner:
        ceiling = 10 ** 6  # the owner may grant anything, including another owner
        return db.query(Role).order_by(Role.rank.desc()).all()
    return db.query(Role).filter(Role.rank < ceiling).order_by(Role.rank.desc()).all()


def may_edit(db: Session, me: User, role: Role) -> tuple[bool, str]:
    """May this person change this role? Returns (allowed, why not)."""
    if me.is_axl:
        return False, "C-EXA can't change what people are allowed to do — a person has to."
    if not me.can("people.roles"):
        return False, "You don't have permission to change roles."
    if role.locked:
        return False, f"'{role.name}' is a built-in role. The app's own checks rely on it, so it can be read but not changed. Copy it into a new role instead."
    # R8: a team-limited person changes only the roles they built. The Viewer preset and an admin's roles are
    # handed out company-wide; one manager editing them would change what people on every team can do.
    if me.is_team_limited and not me.is_owner and role.created_by_id != me.id:
        return False, f"'{role.name}' wasn't built by you, so it isn't yours to change."
    mine = by_slug(db, me.role)
    if mine is not None and not me.is_owner and role.rank >= mine.rank:
        return False, f"'{role.name}' ranks at or above your own role, so it isn't yours to change."
    return True, ""


def check_caps(me: User, wanted: list[str]) -> tuple[list[str], str]:
    """Trim a requested capability list to what this person actually holds.

    The owner holds everything, so this only bites for anyone else — which is the point: a user
    admin cannot quietly write `versions.promote` into a role and then assign it to themselves."""
    wanted = C.valid(wanted)
    if me.is_owner:
        return wanted, ""
    # A narrowing flag is a limit, not a grant: adding "their own team only" to a role takes access
    # away, so it is never something you have to hold yourself to hand out.
    mine = me.caps() | C.NARROWING
    over = [c for c in wanted if c not in mine]
    note = ""
    if over:
        names = ", ".join(C.CAPS[c][1] for c in over)
        wanted, note = [c for c in wanted if c in mine], f"Left out {names} — you can't give away something you don't have yourself."
    # R8: a role a team-limited person builds is team-limited too, or the first thing it is used for is reaching
    # past their team. A role with no powers at all (a plain user's) needs no limit.
    if me.is_team_limited and (set(wanted) - C.NARROWING) and "team.scope" not in wanted:
        wanted = [*wanted, "team.scope"]
        note = (note + " " if note else "") + f"Added {C.CAPS['team.scope'][1]} — roles you build stay limited to a team, like yours."
    return wanted, note


def may_grant(me: User, role: Role) -> tuple[bool, str]:
    """R8: may this person give `role` to someone? Rank alone let a manager hand out the Viewer preset — ranked
    below them, but with a power they don't hold. So both: below their own rank, and no power they don't hold
    themselves. The role need not carry the team limit: whoever a team-limited person makes carries it on their
    account (`User.team_limited`, set in cbx/api/users.py create_user), and they can't change anyone's role."""
    if me.is_axl or not me.can("people.roles"):
        return False, "You can't hand out roles."
    if me.is_owner:
        return True, ""
    mine = getattr(me, "_role_row", None)
    if mine is None or role.rank >= mine.rank:
        return False, f"'{role.name}' ranks at or above your own role."
    powers = C.expand(role.caps or []) - C.NARROWING
    over = powers - me.caps()
    if over:
        names = ", ".join(sorted(C.CAPS[c][1] for c in over if c in C.CAPS))
        return False, f"'{role.name}' can do things you can't ({names})."
    return True, ""


def admin_slugs(db: Session) -> list[str]:
    """The roles whose holders are admins or owners — never part of a team's people, whatever team they sit on.
    R8: the team lists used to name `user` and `manager` outright, so a viewer on the team was invisible to its manager."""
    return [r.slug for r in db.query(Role).all() if {"admin.all", "everything"} & set(r.caps or [])]


def last_administrator(db: Session, role: Role) -> bool:
    """Would removing or emptying this role leave nobody who can administer the system?"""
    holders = {u.id for u in db.query(User).filter(User.role == role.slug, User.is_active.is_(True), User.is_axl.is_(False)).all()}
    others = db.query(User).filter(User.is_active.is_(True), User.is_axl.is_(False), ~User.id.in_(holders or {0})).all()
    for u in others:
        attach(db, u)
        if u.can("everything") or u.can("people.roles"):
            return False
    return True
