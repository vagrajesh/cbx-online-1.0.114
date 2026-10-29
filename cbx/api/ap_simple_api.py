# 1.0.105: new — Builder S (SPEC-1105): the AP Tool's "Make SAP upload" screen. Included with the AP Tool's routes
# (cbx/api/ap_api.py extra_routers).
"""Make SAP upload: drop invoices → one run → the result and one Download. The VAT decision and the second person's check
on a left-out invoice use the 1.0.104 routes (POST /api/ap/captures/{id}/vat, GET/POST /api/ap/review/{id})."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..ap import capture as C
from ..ap import roles as R
from ..ap import service as S
from ..ap import simple as SIMPLE
from ..db import get_db
from ..models import User
from ..models.ap import ApBatch
from .ap_api import HUMAN, SEE, WRITE, _refuse, _xlsx

router = APIRouter(prefix="/api/ap", tags=["ap"])


def _run(db: Session, bid: int) -> ApBatch:
    b = db.get(ApBatch, bid)
    if b is None:
        raise HTTPException(404, "No such run.")
    return b


@router.get("/simple")
def simple_home(me: User = Depends(SEE), db: Session = Depends(get_db)):
    """The screen's frame: may this person drop and download; does the small "More" link show (admins, and people with a
    reviewer / approver / poster role of their own — everyday users never see the other tabs); the recent runs."""
    can = me.role != "viewer" and not me.is_axl
    return {"can_write": can, "more": bool(R.me_out(db, me)["controls"]), "recent": SIMPLE.recent(db, me), "ai": S.model_on()}


@router.post("/simple/runs")
async def simple_start(files: list[UploadFile] = File(...), me: User = Depends(WRITE), db: Session = Depends(get_db)):
    got = []
    for f in files:
        if f is not None and f.filename:
            got.append((f.filename, await f.read(S.MAX_MB * 1024 * 1024 + 1)))
    b = _refuse(SIMPLE.start, db, me, got)
    db.commit()
    db.refresh(b)
    return SIMPLE.status(db, me, b)


@router.get("/simple/runs/{bid}")
def simple_status(bid: int, me: User = Depends(SEE), db: Session = Depends(get_db)):
    return SIMPLE.status(db, me, _run(db, bid))


@router.post("/simple/runs/{bid}/download")
def simple_download(bid: int, me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    """Every document of the run that may go out (green and yellow); the left-out ones are not in the file."""
    b = _run(db, bid)
    try:
        data, _n = SIMPLE.download(db, me, b)
    except (C.Refused, S.Refused) as e:
        db.commit()  # Tess's fresh test stays, though nothing went out (as /sap/upload)
        raise HTTPException(400, str(e)) from None
    db.commit()
    return _xlsx(data, SIMPLE.file_name(b))


@router.get("/simple/runs/{bid}/sent")
def simple_sent(bid: int, me: User = Depends(SEE), db: Session = Depends(get_db)):
    """Recent runs → "What was sent": the run's record (first sheet "ALREADY SENT - DO NOT UPLOAD", then the review
    workbook). 1.0.105 review: no route gives a second uploadable copy of what went out — that would post it twice."""
    b = _run(db, bid)
    return _xlsx(_refuse(SIMPLE.sent_record, db, me, b), SIMPLE.file_name(b, record=True))


@router.get("/simple/companies")
def simple_companies(me: User = Depends(SEE), db: Session = Depends(get_db)):
    """Left out → company unknown: the picker's list (the group's own companies first). Saving it is the existing
    POST /api/ap/captures/{id}/decide {action: "manual", data: {company_code}} (Mia matches it again)."""
    return SIMPLE.companies(db)


@router.get("/simple/runs/{bid}/review")
def simple_review(bid: int, me: User = Depends(SEE), db: Session = Depends(get_db)):
    b = _run(db, bid)
    return _xlsx(C.review(db, b), f"SAP_review_{b.name}_{date.today():%Y%m%d}.xlsx")


@router.post("/simple/learn")
def simple_learn(file: UploadFile = File(...), me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    """"Drop the corrected Excel here to teach CBX": Builder G's learn_from_upload reads the person's corrections and
    answers {learnt, documents, updated, not_yours, skipped, text: "Learnt 7 codings from your file."} (SPEC-1105-interface.md).
    1.0.105 review (Builder G): a plain def — reading a workbook is CPU work, so it runs in the thread pool, never on the
    event loop; the file is cut at AF.MAX_MB."""
    from ..ap import autofill as AF

    data = file.file.read(AF.MAX_MB * 1024 * 1024 + 1)
    if len(data) > AF.MAX_MB * 1024 * 1024:
        raise HTTPException(400, f"The file is larger than {AF.MAX_MB} MB: drop the SAP upload you downloaded, with your changes.")
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "Drop the Excel file (.xlsx) you downloaded and corrected.")
    try:
        out = _refuse(AF.learn_from_upload, db, me, data)
    except AF.Refused as e:  # Builder G's own plain-words refusal
        raise HTTPException(400, str(e)) from None
    db.commit()
    return out
