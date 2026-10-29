"""Work areas (1.0.80): Dev → Test → Live. See cbx/versions/areas.py.

    GET  /api/areas                    what this person has: their Dev area, Test, Live (builders and testers)
    POST /api/areas/dev/start          start my Dev area: the live app + my Builder drafts, my own sign-in
    POST /api/areas/dev/stop           stop it (its data stays for next time)
    POST /api/areas/dev/reset          empty its data
    POST /api/areas/test/{version_id}  put a released, tested version in Test for everyone who tests
    POST /api/areas/test/stop          take it out of Test
    POST /api/areas/{slot}/stop        the owner stops anyone's area
A person always; C-EXA never has an area.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AppVersion, User
from ..security.deps import current_user, require_human, require_owner
from ..versions import areas as A
from ..versions import sandbox_runner as R

router = APIRouter(prefix="/api/areas", tags=["areas"])


def _anyone(me: User = Depends(current_user)) -> User:
    if not (A.can_build(me) or A.can_test(me)):
        raise HTTPException(403, "You don't have access to this.")
    return me


def _builder(me: User = Depends(current_user)) -> User:
    if not A.can_build(me):
        raise HTTPException(403, "Dev areas are for the people who build (admins and developers).")
    return me


@router.get("")
def overview(db: Session = Depends(get_db), me: User = Depends(_anyone)):
    return A.overview(db, me)


@router.post("/dev/start")
def dev_start(db: Session = Depends(get_db), me: User = Depends(_builder), _h: User = Depends(require_human)):
    out = A.start_dev(db, me)
    return {**out, "area": A.overview(db, me)}


@router.post("/dev/stop")
def dev_stop(db: Session = Depends(get_db), me: User = Depends(_builder), _h: User = Depends(require_human)):
    A.stop(db, me, A.dev_slot(me))
    return A.overview(db, me)


@router.post("/dev/reset")
def dev_reset(db: Session = Depends(get_db), me: User = Depends(_builder), _h: User = Depends(require_human)):
    A.reset_data(db, me)
    return A.overview(db, me)


@router.post("/test/stop")
def test_stop(db: Session = Depends(get_db), me: User = Depends(_builder), _h: User = Depends(require_human)):
    if not me.is_admin:
        raise HTTPException(403, "Admins take a version out of Test.")
    A.load(db)
    A.stop(db, me, A.TEST)
    return A.overview(db, me)


@router.post("/test/{version_id}")
def test_start(version_id: int, db: Session = Depends(get_db), me: User = Depends(_builder), _h: User = Depends(require_human)):
    v = db.get(AppVersion, version_id)
    if v is None:
        raise HTTPException(404, "That version doesn't exist.")
    if not (me.is_admin or v.uploaded_by_id == me.id):
        raise HTTPException(403, "Admins, or whoever released it, put a version in Test.")
    try:
        out = A.start_test(db, me, v)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {**out, "area": A.overview(db, me)}


@router.post("/{slot}/stop")
def any_stop(slot: str, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    A.load(db)
    if not R.SLOT_RE.match(slot) or not R.known(slot):
        raise HTTPException(404, "No such area.")
    A.stop(db, me, slot)
    return A.overview(db, me)
