# 1.0.104: new (Builder D) — the Valuence AP controls V01 (new-vendor reviews) and V09 (the duplicates register): the
# routes of Controls → New vendors and Controls → Duplicates (cbx/ap/vendor_review.py, cbx/ap/dupcases.py). Included
# with the AP Tool's routes through cbx/api/ap_api.py extra_routers.
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..ap import dupcases as DC
from ..ap import portfolio as PF
from ..ap import roles as R
from ..ap import vendor_review as VR
from ..db import get_db
from ..models import User
from ..warehouse import storage
from .ap_api import HUMAN, SEE, _xlsx

router = APIRouter(prefix="/api/ap", tags=["ap"])


def _do(fn, *a, **kw):
    """Plain words: not found 404, a refusal 400, not allowed 403."""
    try:
        return fn(*a, **kw)
    except HTTPException:
        raise
    except LookupError as e:
        raise HTTPException(404, str(e) or "Not found.") from None
    except (VR.Refused, DC.Refused, PF.Refused) as e:
        raise HTTPException(400, str(e)) from None
    except PermissionError as e:
        raise HTTPException(403, str(e)) from None


def _controls(db: Session, me: User) -> None:
    """The registers are for reviewers, approvers and admins."""
    if not (me.is_admin or R.may(db, me, "reviewer", None) or R.may(db, me, "approver", None)):
        raise HTTPException(403, "The controls are for AP reviewers and admins.")


# ---- V01: new vendors to review -------------------------------------------------------------------------------------------------
@router.get("/vendor-reviews")
def vendor_reviews(state: str = "open", q: str = "", portfolio: str = "", me: User = Depends(SEE), db: Session = Depends(get_db)):
    _controls(db, me)
    return VR.listing(db, me, state, q, portfolio)


@router.get("/vendor-reviews/report")
def vendor_reviews_report(me: User = Depends(SEE), db: Session = Depends(get_db)):
    _controls(db, me)
    return _xlsx(VR.report(db, me), "Vendor_reviews.xlsx")


@router.get("/vendor-reviews/{rid}")
def vendor_review(rid: int, me: User = Depends(SEE), db: Session = Depends(get_db)):
    _controls(db, me)
    r = _do(VR.get, db, rid)
    if not VR.may_see(db, me, r):  # 1.0.104 review: a role in the review's own portfolio, else not found
        raise HTTPException(404, "No such vendor review.")
    return VR.out(db, r, me, full=True)


@router.post("/vendor-reviews/{rid}/files")
async def vendor_review_file(rid: int, kind: str = Form(...), file: UploadFile = File(...), me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    r = _do(VR.get, db, rid)
    data = await file.read(VR.MAX_MB * 1024 * 1024 + 1)
    out = _do(VR.attach, db, me, r, kind, file.filename or "", data)
    db.commit()
    return out


@router.get("/vendor-reviews/{rid}/files/{fid}")
def vendor_review_file_get(rid: int, fid: int, me: User = Depends(SEE), db: Session = Depends(get_db)):
    _controls(db, me)
    r = _do(VR.get, db, rid)
    # 1.0.104 review: a creation request or bank letter is seen only with a role in the review's own portfolio (else: not found)
    if not VR.may_see(db, me, r):
        raise HTTPException(404, "That file isn't here.")
    f = _do(VR.file_of, db, r, fid)
    p = storage.abs_path(f)
    if not p.is_file():
        raise HTTPException(404, "That file isn't here.")
    return FileResponse(p, filename=f.name)


class ReviewIn(BaseModel):
    outcome: str = ""  # pass | fail
    checklist: dict = {}
    reason: str = ""


@router.post("/vendor-reviews/{rid}/decide")
def vendor_review_decide(rid: int, body: ReviewIn, me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    r = _do(VR.get, db, rid)
    _do(VR.decide, db, me, r, body.outcome, body.checklist, body.reason)
    db.commit()
    return VR.out(db, r, me, full=True)


# ---- V09: the duplicates register --------------------------------------------------------------------------------------------------
@router.get("/dups")
def dups(state: str = "live", portfolio: str = "", owner: Optional[int] = None, q: str = "", import_id: Optional[int] = None,
         me: User = Depends(SEE), db: Session = Depends(get_db)):
    _controls(db, me)
    return DC.listing(db, state, portfolio, owner, q, import_id)


@router.get("/dups/export")
def dups_export(import_id: Optional[int] = None, me: User = Depends(SEE), db: Session = Depends(get_db)):
    _controls(db, me)
    return _xlsx(DC.export(db, import_id), f"Duplicates_{'listing_' + str(import_id) if import_id else 'register'}.xlsx")


class DupIn(BaseModel):
    outcome: str = ""  # genuine | valid
    reason: str = ""


@router.post("/dups/{cid}/decide")
def dup_decide(cid: int, body: DupIn, me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    case = _do(DC.get, db, cid)
    _do(DC.decide, db, me, case, body.outcome, body.reason)
    db.commit()
    return DC.case_out(db, case)


class SecondIn(BaseModel):
    agree: bool = True
    reason: str = ""


@router.post("/dups/{cid}/second")
def dup_second(cid: int, body: SecondIn, me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    case = _do(DC.get, db, cid)
    _do(DC.second, db, me, case, body.agree, body.reason)
    db.commit()
    return DC.case_out(db, case)


class TakeIn(BaseModel):
    owner_id: Optional[int] = None


@router.post("/dups/{cid}/take")
def dup_take(cid: int, body: TakeIn, me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    case = _do(DC.get, db, cid)
    _do(DC.take, db, me, case, body.owner_id)
    db.commit()
    return DC.case_out(db, case)


class BulkIn(BaseModel):
    ids: list[int] = []
    outcome: str = ""  # "" = each as proposed
    reason: str = ""


@router.post("/dups/confirm")
def dups_confirm(body: BulkIn, me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    """Bulk: the cases as proposed (an imported listing) or as one outcome."""
    _controls(db, me)
    out = DC.confirm_bulk(db, me, body.ids, body.outcome, body.reason)
    db.commit()
    return out


@router.post("/dups/import")
async def dups_import(file: UploadFile = File(...), portfolio: str = Form(""), confirm: bool = Form(False), me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    """The backlog: a MOL / Workflow Basket listing (Excel or CSV) — confirm=False shows the proposals, true saves the cases."""
    _controls(db, me)
    from ..ap import masters as M

    data = await file.read(M.MAX_MB * 1024 * 1024 + 1)
    lines, mapping = _do(DC.read_listing, data, file.filename or "")
    out = _do(DC.import_listing, db, me, lines, mapping, filename=file.filename or "", portfolio=portfolio, confirm=confirm)
    db.commit()
    return out
