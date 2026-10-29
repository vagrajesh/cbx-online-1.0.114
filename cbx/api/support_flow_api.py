"""1.0.109 (round 2, 23 Oct) — the support desk's flow: the responsible person, the follow-up clock, assignee suggestions.

    POST /api/support/tickets/{id}/owner {kind: user, id} | {kind: other, name, email} | {kind: none}   staff who see it
    GET  /api/support/owner-people                                   the picker's list (active CBX people)
    GET|POST /api/support/followup-settings                          owner/admin: N and M working days, the defaults
    POST /api/support/tickets/{id}/why-not {reason, text}            owner/admin: why not AXL's first suggestion
    POST /api/support/tickets/{id}/undo-auto                         owner/admin: undo AXL's assignment
    GET|POST /api/support/assign-settings                            owner/admin: auto-assign per system, margin, history, "How AXL picks"
(cbx/support/followup.py, cbx/support/suggest.py)

1.0.111 (round 3):
    GET|POST /api/support/system-people                              owner/admin: who works each system (support leads + the desk)
    GET  /api/support/system-people/{sid}/guess                      owner/admin: "Use AXL's guess" (nothing saved)
    POST /api/support/new-problem/{mid}/belongs                      owner/admin: "It belongs here" — the mail is a reply after all
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import SupportTicket, User
from ..security.deps import current_user, require_human
from ..support import core as C
from ..support import followup as FU
from ..support import suggest as SG

router = APIRouter(prefix="/api/support", tags=["support"])


def _support(db: Session = Depends(get_db), me: User = Depends(current_user)) -> User:
    if not C.is_staff(db, me):
        raise HTTPException(403, "This is for the support team.")
    return me


def _assigner(me: User = Depends(current_user)) -> User:
    if not C.can_assign(me):
        raise HTTPException(403, "Only the owner or an admin does this.")
    return me


def _seen(db: Session, tid: int, me: User) -> SupportTicket:
    t = db.get(SupportTicket, tid)
    if t is None or not C.staff_may_see(db, me, t):
        raise HTTPException(404, "No such ticket.")
    return t


def _do(fn):
    try:
        return fn()
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


class OwnerIn(BaseModel):
    kind: str = "none"
    id: Optional[int] = None
    name: str = ""
    email: str = ""


@router.post("/tickets/{tid}/owner")
def set_owner(tid: int, body: OwnerIn, db: Session = Depends(get_db), me: User = Depends(_support), _h: User = Depends(require_human)):
    t = _seen(db, tid, me)
    if me.role == "viewer":
        raise HTTPException(403, "Viewers only look.")
    _do(lambda: FU.set_owner(db, t, body.model_dump(), me))
    return C.ticket_out(db, t, staff=True, full=True, viewer=me)


@router.get("/owner-people")
def owner_people(db: Session = Depends(get_db), me: User = Depends(_support)):
    rows = db.query(User).filter(User.is_active.is_(True), User.is_axl.is_(False)).order_by(User.name).all()
    return {"people": [{"id": u.id, "name": u.name or u.email, "email": u.email} for u in rows]}


class FollowSettingsIn(BaseModel):
    follow_days: Optional[int] = None
    escalate_days: Optional[int] = None
    desk_owner: Optional[dict] = None
    systems: Optional[list] = None


@router.get("/followup-settings")
def followup_settings(db: Session = Depends(get_db), me: User = Depends(_assigner)):
    return FU.settings_out(db)


@router.post("/followup-settings")
def save_followup_settings(body: FollowSettingsIn, db: Session = Depends(get_db), me: User = Depends(_assigner), _h: User = Depends(require_human)):
    data = body.model_dump(exclude_unset=True)
    return _do(lambda: FU.save_settings(db, data, me))


class WhyNotIn(BaseModel):
    reason: str
    text: str = ""


@router.post("/tickets/{tid}/why-not")
def why_not(tid: int, body: WhyNotIn, db: Session = Depends(get_db), me: User = Depends(_assigner), _h: User = Depends(require_human)):
    t = _seen(db, tid, me)
    _do(lambda: SG.why_not(db, t, body.reason, body.text, me))
    return {"ok": True}


@router.post("/tickets/{tid}/undo-auto")
def undo_auto(tid: int, db: Session = Depends(get_db), me: User = Depends(_assigner), _h: User = Depends(require_human)):
    t = _seen(db, tid, me)
    _do(lambda: SG.undo_auto(db, t, me))
    return C.ticket_out(db, t, staff=True, full=True, viewer=me)


class AssignSettingsIn(BaseModel):
    systems: Optional[list] = None
    margin: Optional[float] = None
    min_history: Optional[int] = None


@router.get("/assign-settings")
def assign_settings(db: Session = Depends(get_db), me: User = Depends(_assigner)):
    return SG.settings_out(db)


@router.post("/assign-settings")
def save_assign_settings(body: AssignSettingsIn, db: Session = Depends(get_db), me: User = Depends(_assigner), _h: User = Depends(require_human)):
    data = body.model_dump(exclude_unset=True)
    return _do(lambda: SG.save_settings(db, data, me))


# ---- 1.0.111 (round 3) -----------------------------------------------------------------------------------------------
class SystemPeopleIn(BaseModel):
    systems: list = []


@router.get("/system-people")
def system_people(db: Session = Depends(get_db), me: User = Depends(_assigner)):
    return SG.people_out(db)


@router.post("/system-people")
def save_system_people(body: SystemPeopleIn, db: Session = Depends(get_db), me: User = Depends(_assigner), _h: User = Depends(require_human)):
    return _do(lambda: SG.save_people(db, body.model_dump(), me))


@router.get("/system-people/{sid}/guess")
def system_people_guess(sid: int, db: Session = Depends(get_db), me: User = Depends(_assigner)):
    from ..models import SupportSystem

    if db.get(SupportSystem, sid) is None:
        raise HTTPException(404, "No such system.")
    return {"people": SG.guess(db, sid)}


@router.post("/new-problem/{mid}/belongs")
def new_problem_belongs(mid: int, db: Session = Depends(get_db), me: User = Depends(_assigner), _h: User = Depends(require_human)):
    from ..support import inbox as I

    t = _do(lambda: I.belongs_here(db, mid, me))
    if t.status == "new":
        C.start_reading(db, t)
        db.refresh(t)
    return C.ticket_out(db, t, staff=True, full=True, viewer=me)
