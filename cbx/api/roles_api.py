"""Roles: read them, invent them, change them. Built-ins are read-only by design.

Every write here re-checks two things rather than trusting the screen: you cannot give away a
capability you do not hold, and you cannot touch a role that ranks at or above your own.
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..audit import log
from ..db import get_db
from ..models import Role, User
from ..security import caps as C
from ..security import roles as R
from ..security.deps import client_ip, require_cap, require_human

router = APIRouter(prefix="/api/roles", tags=["roles"])
MANAGE = require_cap("people.roles")


def _out(db: Session, r: Role, me: User) -> dict:
    editable, why = R.may_edit(db, me, r)
    return {
        "id": r.id, "slug": r.slug, "name": r.name, "description": r.description or "",
        "caps": C.valid(r.caps or []), "rank": r.rank, "locked": bool(r.locked),
        "people": db.query(User).filter(User.role == r.slug, User.is_axl.is_(False)).count(),
        "editable": editable, "why_not": why,
    }


@router.get("")
def list_roles(db: Session = Depends(get_db), me: User = Depends(MANAGE)):
    rows = db.query(Role).order_by(Role.rank.desc()).all()
    return {
        "roles": [_out(db, r, me) for r in rows],
        "capabilities": C.describe([]),      # every capability, with what it means
        "groups": C.GROUPS,
        "my_caps": sorted(me.caps()),        # the screen greys out what you cannot give away
        "my_rank": (R.by_slug(db, me.role).rank if R.by_slug(db, me.role) else 0),
        "team_limited": bool(me.is_team_limited and not me.is_owner),  # R8: their roles always keep "Their own team only"
    }


class RoleIn(BaseModel):
    name: str
    description: str = ""
    caps: list[str] = []
    rank: int = 10
    copy_of: Optional[str] = None


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:32]


def _check_rank(db: Session, me: User, rank: int) -> None:
    if me.is_owner:
        return
    mine = R.by_slug(db, me.role)
    if mine is not None and rank >= mine.rank:
        raise HTTPException(400, f"That rank is at or above your own ({mine.rank}). You can only create roles below yourself.")


@router.post("")
def create_role(body: RoleIn, request: Request, db: Session = Depends(get_db), me: User = Depends(MANAGE), _h: User = Depends(require_human)):
    name = " ".join(body.name.split())[:80]
    if not name:
        raise HTTPException(400, "Give the role a name.")
    slug = _slugify(name)
    if not slug:
        raise HTTPException(400, "That name has no letters or numbers in it — give it a plain name.")
    if R.by_slug(db, slug) is not None:
        raise HTTPException(400, f"There is already a role called '{name}'.")
    _check_rank(db, me, body.rank)
    wanted = body.caps
    if body.copy_of:
        src = R.by_slug(db, body.copy_of)
        if src is None:
            raise HTTPException(400, "The role you're copying doesn't exist.")
        wanted = list(src.caps or [])
    caps, note = R.check_caps(me, wanted)
    role = Role(slug=slug, name=name, description=body.description.strip()[:500], caps=caps, rank=body.rank, created_by_id=me.id)
    db.add(role)
    db.flush()
    log(db, "role_created", actor=me, target=slug, detail=f"{name} · {len(caps)} capabilities · rank {body.rank}" + (f" · {note}" if note else ""), ip=client_ip(request))
    out = _out(db, role, me)
    out["message"] = f"'{name}' created." + (f" {note}" if note else "")
    return out


class RolePatch(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    caps: Optional[list[str]] = None
    rank: Optional[int] = None


@router.patch("/{rid}")
def update_role(rid: int, body: RolePatch, request: Request, db: Session = Depends(get_db), me: User = Depends(MANAGE), _h: User = Depends(require_human)):
    role = db.get(Role, rid)
    if role is None:
        raise HTTPException(404, "That role isn't here any more.")
    ok, why = R.may_edit(db, me, role)
    if not ok:
        log(db, "role_edit_blocked", actor=me, target=role.slug, detail=why, outcome="blocked", ip=client_ip(request))
        raise HTTPException(403, why)
    changes, note = [], ""
    if body.name is not None and body.name.strip():
        role.name = " ".join(body.name.split())[:80]
        changes.append("name")
    if body.description is not None:
        role.description = body.description.strip()[:500]
        changes.append("description")
    if body.rank is not None and body.rank != role.rank:
        _check_rank(db, me, body.rank)
        role.rank, _ = body.rank, changes.append("rank")
    if body.caps is not None:
        caps, note = R.check_caps(me, body.caps)
        # You may not strip the last thing keeping the system administrable.
        if not ({"everything", "people.roles"} & set(caps)) and R.last_administrator(db, role):
            raise HTTPException(400, f"'{role.name}' is the only role left that can administer this system. Give another role permission to change roles first, or nobody will be able to.")
        # ...nor cut the branch you are sitting on.
        if role.slug == me.role and "people.roles" not in caps and not me.is_owner:
            raise HTTPException(400, "That would remove your own permission to change roles, and you would not be able to put it back.")
        was, role.caps = set(role.caps or []), caps
        added, gone = set(caps) - was, was - set(caps)
        changes.append(f"capabilities (+{len(added)}, -{len(gone)})")
    db.flush()
    log(db, "role_changed", actor=me, target=role.slug, detail=", ".join(changes) or "no change", ip=client_ip(request))
    out = _out(db, role, me)
    out["message"] = f"'{role.name}' saved." + (f" {note}" if note else "")
    return out


@router.delete("/{rid}")
def delete_role(rid: int, request: Request, db: Session = Depends(get_db), me: User = Depends(MANAGE), _h: User = Depends(require_human)):
    role = db.get(Role, rid)
    if role is None:
        raise HTTPException(404, "That role isn't here any more.")
    ok, why = R.may_edit(db, me, role)
    if not ok:
        raise HTTPException(403, why)
    n = db.query(User).filter(User.role == role.slug).count()
    if n:
        raise HTTPException(400, f"{n} {'person is' if n == 1 else 'people are'} on '{role.name}'. Move them to another role first — deleting it would leave them with no permissions at all.")
    if R.last_administrator(db, role):
        raise HTTPException(400, f"'{role.name}' is the only role left that can administer this system.")
    name = role.name
    db.delete(role)
    db.flush()
    log(db, "role_deleted", actor=me, target=role.slug, detail=name, ip=client_ip(request))
    return {"ok": True, "deleted": rid, "message": f"'{name}' deleted."}
