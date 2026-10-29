"""Who may see a company, and who answers for it.

  access(db, ent)                        the people given this one company (EntityAccess)
  grant(db, ent, user, by) / revoke(...) one company for one person — with the Entity Compliance tool
                                         itself, so the grant is enough on its own
  set_responsible(db, ent, user, by)     link the responsible person to an account, and give them the
                                         company (they can hardly answer for what they cannot open)
  new_responsible(db, ent, name, email, by, team_id)
                                         Edwin's rule: adding a person adds their ACCOUNT, not a name.
                                         Creates the user (temporary password, must change on first
                                         sign-in), grants the tool and the company, links them.
"""
from __future__ import annotations

import re
from typing import Optional

from sqlalchemy.orm import Session

from ..audit import log
from ..models import Team, Tool, ToolAccess, User
from ..models.docs import Entity, EntityAccess
from ..security import auth as A
from ..warehouse.folders import ensure_person_folders
from . import service as S

TOOL_SLUG = "doc-watch"
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _tool(db: Session) -> Optional[Tool]:
    return db.query(Tool).filter(Tool.slug == TOOL_SLUG).one_or_none()


def ensure_tool(db: Session, user: User, by: User, *, only: Optional[bool] = None) -> bool:
    """The Entity Compliance tool in their sidebar, if it isn't already. True when it was added.
    `only=True` makes it a narrow grant: they see only the companies they were given."""
    tool = _tool(db)
    if tool is None or user.is_admin:
        return False
    ta = db.query(ToolAccess).filter(ToolAccess.tool_id == tool.id, ToolAccess.user_id == user.id).one_or_none()
    if ta is not None:
        if only is not None and (ta.scope == "granted") != only:
            S.set_only(db, user, only, by)
        return False
    db.add(ToolAccess(tool_id=tool.id, user_id=user.id, granted_by_id=by.id, scope="granted" if only else "all"))
    db.flush()
    S.forget_scope(user)
    log(db, "access_granted", actor=by, target=user.email, detail=f"tool {TOOL_SLUG}" + (" — only the companies given to them" if only else " (with a company on the compliance grid)"))
    return True


def access(db: Session, ent: Entity) -> list[dict]:
    rows = db.query(EntityAccess).filter(EntityAccess.entity_id == ent.id).all()
    users = {u.id: u for u in db.query(User).filter(User.id.in_([r.user_id for r in rows] + [r.granted_by_id for r in rows if r.granted_by_id] or [0])).all()}
    out = []
    for r in rows:
        u = users.get(r.user_id)
        if u is None:
            continue
        g = users.get(r.granted_by_id) if r.granted_by_id else None
        out.append({"id": u.id, "name": u.name, "email": u.email, "role": u.role, "is_active": u.is_active, "responsible": ent.responsible_user_id == u.id,
                    "only": S.scope(db, u)["only"], "granted_at": r.created_at.isoformat() if r.created_at else None, "granted_by": g.name if g else "", "note": r.note or ""})
    out.sort(key=lambda x: (not x["responsible"], x["name"].lower()))
    return out


def grant(db: Session, ent: Entity, user: User, by: User, note: str = "", *, only: Optional[bool] = None) -> bool:
    """One company for one person. Admins are not granted (they see everything); C-EXA never.
    `only` (when given) also sets whether that is ALL they see — a brand-new account for one company's
    controller gets True; an existing colleague who already sees the grid keeps what they had."""
    if user.is_axl:
        raise ValueError("C-EXA doesn't get companies — she sees what the person asking her sees.")
    if user.is_admin:
        return False
    ensure_tool(db, user, by, only=only)
    if db.query(EntityAccess).filter(EntityAccess.entity_id == ent.id, EntityAccess.user_id == user.id).count():
        return False
    db.add(EntityAccess(entity_id=ent.id, user_id=user.id, granted_by_id=by.id, note=(note or "")[:200]))
    db.flush()
    S.forget_scope(user)
    log(db, "docwatch_access_granted", actor=by, target=ent.name, detail=f"{user.name} <{user.email}> may see this company" + (f" — {note}" if note else ""))
    return True


def revoke(db: Session, ent: Entity, user: User, by: User) -> bool:
    row = db.query(EntityAccess).filter(EntityAccess.entity_id == ent.id, EntityAccess.user_id == user.id).one_or_none()
    if row is None:
        return False
    db.delete(row)
    db.flush()
    S.forget_scope(user)
    log(db, "docwatch_access_revoked", actor=by, target=ent.name, detail=f"{user.name} <{user.email}> no longer sees this company")
    return True


def set_responsible(db: Session, ent: Entity, user: Optional[User], by: User) -> str:
    """Link (or unlink) the responsible account. Linking also gives them the company and the tool."""
    if user is None:
        ent.responsible_user_id = None
        db.add(ent)
        db.flush()
        return "account link removed"
    if user.is_axl:
        raise ValueError("C-EXA can't be responsible for a company.")
    ent.responsible_user_id = user.id
    if not ent.responsible or ent.responsible.strip().lower() != (user.name or "").strip().lower():
        ent.responsible = (user.name or "")[:80]
    if user.email and not ent.responsible_email:
        ent.responsible_email = user.email
    db.add(ent)
    db.flush()
    grant(db, ent, user, by, note="responsible person")
    return f"linked to {user.name}"


def new_responsible(db: Session, ent: Entity, *, name: str, email: str, by: User, team_id: Optional[int] = None) -> tuple[User, str]:
    """A responsible person who has no account yet: make one. Returns the user and their temporary
    password — shown once, to the person who created the account, never stored in clear."""
    name = " ".join((name or "").split())[:120]
    email = (email or "").strip().lower()
    if len(name) < 2:
        raise ValueError("Give the person their full name.")
    if not EMAIL.match(email):
        raise ValueError("That doesn't look like an email address.")
    if db.query(User).filter(User.email == email).count():
        raise ValueError("There is already an account with that email — pick it from the list instead.")
    if not by.can("people.add"):
        raise PermissionError("Only an admin or a manager can add a person.")
    team = None
    if team_id:
        team = db.get(Team, team_id)
    if team is None and ent.team_id:
        team = db.get(Team, ent.team_id)
    if team is None and by.team_id:
        team = db.get(Team, by.team_id)
    if by.is_team_limited and not by.is_admin and (team is None or team.id != by.team_id):
        raise PermissionError("You can only add people to your own team.")
    pw = A.temp_password()
    # R8: the same rule as Users & roles — someone a team-limited person makes is limited to the team too
    u = User(email=email, name=name, title="", role="user", team_id=team.id if team else None, password_hash=A.hash_password(pw), must_change_password=True, created_by_id=by.id,
             team_limited=bool(by.is_team_limited and not by.is_admin))
    db.add(u)
    db.flush()
    ensure_person_folders(db, u)
    log(db, "account_created", actor=by, target=email, detail=f"role=user tools=1 — added as the responsible person for {ent.name}")
    # a new account made for one company sees that company and nothing else until someone widens it
    grant(db, ent, u, by, note="responsible person", only=True)
    set_responsible(db, ent, u, by)
    return u, pw
