# 1.0.104: new — Builder R (SPEC-1104 Phase 2): V05 independent review, V06 VAT decisions, V12 evidence. Included with the
# AP Tool's routes (cbx/api/ap_api.py extra_routers).
"""Controls → Review (reviewers): the documents picked for review, one document beside its page, Agree / Correct / Reject;
the invoices that are not valid tax invoices waiting for Claim VAT / Don't claim; the monthly coverage report and the
"posted without the VAT claim" supplier report.
Controls → Evidence: the register per portfolio and month, attach (batch / payment run / account; .msg and .eml too),
a file, delete (admins, only after the retention), the zip export with its index, and a batch's checklist."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..ap import evidence as EV
from ..ap import review as RV
from ..ap import roles as R
from ..ap import vatcheck as VC
from ..db import get_db
from ..models import User
from ..models.ap import ApBatch, ApCapture, ApEvidence
from .ap_api import HUMAN, SEE, _refuse, _xlsx

router = APIRouter(prefix="/api/ap", tags=["ap"])


def _found(fn, *a, **kw):
    try:
        return _refuse(fn, *a, **kw)
    except LookupError as e:
        raise HTTPException(404, str(e) or "Not found.") from None


def _capture(db: Session, cid: int) -> ApCapture:
    c = db.get(ApCapture, cid)
    if c is None:
        raise HTTPException(404, "No such document.")
    return c


def _reviewer_somewhere(db: Session, me: User) -> None:
    """The registers and reports are for reviewers and admins (everyday users keep their simple screens)."""
    if not (me.is_admin or any(R.may(db, me, r, None) for r in ("reviewer", "poster", "approver", "recon_reviewer"))):
        raise HTTPException(403, "This is for reviewers. An admin gives the role in AP Settings → AP people.")


# ---- V05 / V06: Controls → Review -----------------------------------------------------------------------------------------------
@router.get("/review")
def review_queue(show: str = "review", portfolio: str = "", batch: Optional[int] = None, me: User = Depends(SEE), db: Session = Depends(get_db)):
    _reviewer_somewhere(db, me)
    return RV.queue(db, me, show if show in ("review", "vat", "done") else "review", portfolio[:20], batch)


@router.get("/review/{cid}")
def review_doc(cid: int, me: User = Depends(SEE), db: Session = Depends(get_db)):
    _reviewer_somewhere(db, me)
    return RV.doc_out(db, _capture(db, cid), me, full=True)


class ReviewIn(BaseModel):
    action: str = ""  # agree · correct · reject
    note: str = ""
    fields: dict = {}  # Correct: the corrected fields
    errors: list[str] = []  # the fields found wrong (Correct, Reject)


@router.post("/review/{cid}")
def review_act(cid: int, body: ReviewIn, me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    c = _found(RV.act, db, me, _capture(db, cid), body.action, body.note, body.fields, body.errors)
    db.commit()
    return RV.doc_out(db, db.get(ApCapture, c.id), me, full=True)


class VatIn(BaseModel):
    decision: str = ""  # claim · no_claim
    reason: str = ""


@router.post("/captures/{cid}/vat")
def vat_decide(cid: int, body: VatIn, me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    c = _found(VC.decide, db, me, _capture(db, cid), body.decision, body.reason)
    db.commit()
    return RV.doc_out(db, db.get(ApCapture, c.id), me, full=True)


@router.get("/reports/review")
def review_report(month: str = "", portfolio: str = "", me: User = Depends(SEE), db: Session = Depends(get_db)):
    _reviewer_somewhere(db, me)
    data = _refuse(RV.report, db, month[:7], portfolio[:20])
    return _xlsx(data, f"AP_review_coverage_{(month or 'this_month')[:7]}{'_' + portfolio[:20] if portfolio else ''}.xlsx")


@router.get("/reports/vat")
def vat_report(month: str = "", portfolio: str = "", me: User = Depends(SEE), db: Session = Depends(get_db)):
    _reviewer_somewhere(db, me)
    data = _refuse(VC.report, db, month[:7], portfolio[:20])
    return _xlsx(data, f"AP_VAT_not_claimed{'_' + month[:7] if month else ''}{'_' + portfolio[:20] if portfolio else ''}.xlsx")


# ---- V12: Controls → Evidence -------------------------------------------------------------------------------------------------------
@router.get("/evidence")
def evidence_list(portfolio: str = "", month: str = "", batch: Optional[int] = None, account: str = "", me: User = Depends(SEE), db: Session = Depends(get_db)):
    _reviewer_somewhere(db, me)
    return _found(EV.listing, db, me, portfolio=portfolio[:20], month=month[:7], batch_id=batch, account=account[:60])


@router.post("/evidence")
async def evidence_attach(file: UploadFile = File(...), type: str = Form(""), batch_id: Optional[int] = Form(None), run_item_id: Optional[int] = Form(None),
                          account: str = Form(""), note: str = Form(""), period: str = Form(""), me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    data = await file.read(EV.MAX_MB * 1024 * 1024 + 1)
    e = _found(EV.attach, db, me, kind=type, filename=file.filename or "", data=data, batch_id=batch_id or None, run_item_id=run_item_id or None,
               account=account, note=note, period=period[:7])
    db.commit()
    return EV.out(e)


@router.get("/evidence/export")
def evidence_export(portfolio: str = "", month: str = "", me: User = Depends(SEE), db: Session = Depends(get_db)):
    _reviewer_somewhere(db, me)
    data, name = _found(EV.export, db, me, portfolio[:20], month[:7])
    db.commit()  # the export is audited
    return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


def _evidence(db: Session, eid: int) -> ApEvidence:
    e = db.get(ApEvidence, eid)
    if e is None:
        raise HTTPException(404, "No such evidence.")
    return e


@router.get("/evidence/{eid}/file")
def evidence_file(eid: int, me: User = Depends(SEE), db: Session = Depends(get_db)):
    e = _evidence(db, eid)
    if not EV.may_see(db, me, e.portfolio or None):
        raise HTTPException(403, "This is for reviewers, posters and approvers of the portfolio.")
    path, name = _found(EV.file_of, db, e)
    return FileResponse(path, filename=name)


@router.delete("/evidence/{eid}")
def evidence_delete(eid: int, me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    _found(EV.delete, db, me, _evidence(db, eid))
    db.commit()
    return {"ok": True}


@router.get("/batches/{bid}/evidence")
def batch_evidence(bid: int, me: User = Depends(SEE), db: Session = Depends(get_db)):
    """The SAP sheet's line: the batch's checklist and what is missing (reviewers, posters, approvers, admins)."""
    b = db.get(ApBatch, bid)
    if b is None:
        raise HTTPException(404, "No such folder.")
    st = EV.batch_status(db, b)
    if not EV.may_see(db, me, st["portfolio"] or None):
        raise HTTPException(403, "This is for reviewers, posters and approvers of the portfolio.")
    return st
