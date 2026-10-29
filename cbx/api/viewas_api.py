"""View as — see cbx/security/viewas.py for how it holds. Owners and admins only, a person not the AI,
only people they may manage, an hour at most, and both ends written to the audit trail."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..audit import log
from ..db import get_db
from ..models import LoginSession, User
from ..security import auth as A
from ..security import viewas
from ..security.deps import client_ip, require_admin, require_human

router = APIRouter(prefix="/api/view-as", tags=["view-as"])


def _session(request: Request, db: Session) -> LoginSession:
    s = db.get(LoginSession, request.cookies.get(A.SESSION_COOKIE) or "")
    if s is None or s.revoked:
        raise HTTPException(401, "Please sign in.")
    return s


@router.post("/stop")
def stop(request: Request, db: Session = Depends(get_db)):
    s = _session(request, db)
    real = db.get(User, s.user_id)
    was = viewas.stop(db, s)
    db.info.pop("read_only", None)
    db.info.pop("viewer", None)
    if was:
        target = db.get(User, was)
        log(db, "view_as_stopped", actor=real, target=target.email if target else str(was), ip=client_ip(request))
    return {"ok": True}


@router.post("/{uid}")
def start(uid: int, request: Request, db: Session = Depends(get_db), me: User = Depends(require_admin), _h: User = Depends(require_human)):
    target = db.get(User, uid)
    if target is None or target.is_axl:
        raise HTTPException(404, "No such person.")
    if target.id == me.id:
        raise HTTPException(400, "That is you already.")
    if not target.is_active:
        raise HTTPException(400, "That account is disabled, so there is nothing to see as them.")
    if not me.can_manage_user(target):
        log(db, "view_as_blocked", actor=me, target=target.email, outcome="blocked", ip=client_ip(request))
        raise HTTPException(403, "You can only view as people you manage.")
    s = _session(request, db)
    viewas.start(db, s, me, target)
    log(db, "view_as_started", actor=me, target=target.email, detail=f"role {target.role} · read-only · up to {viewas.MAX_MINUTES} min", ip=client_ip(request))
    return {"ok": True, "user": {"id": target.id, "name": target.name, "role": target.role}, "minutes": viewas.MAX_MINUTES}
