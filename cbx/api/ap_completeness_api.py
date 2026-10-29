# 1.0.104: new (Builder C) — the routes of V10 completeness and V11 reconciliations (SPEC-1104 Phase 2), included with the AP
# Tool's (cbx/api/ap_api.py extra_routers)
"""Controls → Completeness: the month per portfolio and cluster, an account's status and fields, masterlist imports and
their exceptions, the Council Liaison list, the weekly workbook.
Controls → Reconciliations: the month's population (built from the rules), one reconciliation with its items, prepare,
review, the report.

Registers and reports are for reviewers and admins (`_viewer`); what a person changes is checked again in cbx/ap/*."""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..ap import completeness as CP
from ..ap import payments as PAY
from ..ap import portfolio as PF
from ..ap import recons as RC
from ..ap import roles as R
from ..db import get_db
from ..models import User
from .ap_api import HUMAN, SEE

router = APIRouter(prefix="/api/ap", tags=["ap"])
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_MB = 25


def _run(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except HTTPException:
        raise
    except PermissionError as e:
        raise HTTPException(403, str(e)) from None
    except (CP.Refused, RC.Refused, PF.Refused) as e:
        raise HTTPException(400, str(e)) from None
    except LookupError as e:
        raise HTTPException(404, str(e) or "Not found.") from None


def _xlsx(data: bytes, name: str) -> Response:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:120] or "file.xlsx"
    return Response(data, media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="{safe}"', "Cache-Control": "no-store"})


def _portfolio(db: Session, code: str) -> str:
    p = PF.get(db, code) if code else PF.default(db)
    if p is None:
        raise HTTPException(404, "No such portfolio.")
    return p.code


def _viewer(db: Session, me: User, code: str) -> None:
    """The registers are for admins and the portfolio's reviewers, approvers and reconciliation reviewers."""
    if me.is_admin or any(R.may(db, me, r, code) for r in ("reviewer", "approver", "recon_reviewer", "poster")):
        return
    raise HTTPException(403, f"This register is for the reviewers of {code} and admins.")


def _period(p: str) -> str:
    return _run(CP.period_ok, p or CP.this_month())


# ---- V10: completeness ---------------------------------------------------------------------------------------------------------
@router.get("/completeness")
def completeness(portfolio: str = "", period: str = "", cluster: str = "", state: str = "", q: str = "", limit: int = 300,
                 me: User = Depends(SEE), db: Session = Depends(get_db)):
    code = _portfolio(db, portfolio)
    _viewer(db, me, code)
    out = _run(CP.month_view, db, code, _period(period), cluster=cluster[:20], state=state[:12], q=q, limit=limit)
    out.update(portfolios=CP.portfolios_out(db, me), manage=CP.may_manage(db, me, code), payments=PAY.counts(db), kinds=CP.KINDS,
               statuses=list(CP.STATUSES), exceptions_open=CP.exceptions(db, code, "open", limit=1)["total"], imports=CP.imports(db, code, 5))
    return out


@router.get("/completeness/export")
def completeness_export(portfolio: str = "", period: str = "", cluster: str = "", me: User = Depends(SEE), db: Session = Depends(get_db)):
    code = _portfolio(db, portfolio)
    _viewer(db, me, code)
    p = _period(period)
    return _xlsx(_run(CP.export, db, code, p, cluster[:20]), f"Completeness {code} {p}{' ' + cluster if cluster else ''}.xlsx")


@router.get("/completeness/liaison")
def completeness_liaison(portfolio: str = "", period: str = "", cluster: str = "", me: User = Depends(SEE), db: Session = Depends(get_db)):
    code = _portfolio(db, portfolio)
    _viewer(db, me, code)
    return {"rows": _run(CP.liaison, db, code, _period(period), cluster[:20])}


class AccountIn(BaseModel):
    status: Optional[str] = None
    status_from: Optional[str] = None
    co_do: Optional[str] = None
    cluster: Optional[str] = None
    check_meter: Optional[bool] = None
    bill_day: Optional[int] = None
    portfolio: Optional[str] = None
    clear_bill_day: bool = False


@router.put("/completeness/accounts/{aid}")
def account_save(aid: int, body: AccountIn, me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    data = body.model_dump(exclude_none=True, exclude={"clear_bill_day"})
    if body.clear_bill_day:
        data["bill_day"] = None
    a = _run(CP.save_account, db, me, aid, data)
    db.commit()
    return CP.account_out(a, CP.Portfolios(db).of(a))


@router.post("/completeness/import")
async def masterlist_import(file: UploadFile = File(...), portfolio: str = Form(...), kind: str = Form("masterlist"), source: str = Form(""),
                            confirm: bool = Form(False), me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    """A masterlist, Termination or Anomaly report for a portfolio: confirm=False shows the diff and the exceptions; True saves."""
    code = _portfolio(db, portfolio)
    data = await file.read(MAX_MB * 1024 * 1024 + 1)
    if len(data) > MAX_MB * 1024 * 1024:
        raise HTTPException(400, f"The file is over {MAX_MB} MB.")
    rows = _run(CP.read_list, data, file.filename or "")
    out = _run(CP.import_list, db, me, code, kind, rows, source=source, file_name=file.filename or "", confirm=confirm)
    db.commit()
    return out


@router.get("/completeness/imports")
def masterlist_imports(portfolio: str = "", me: User = Depends(SEE), db: Session = Depends(get_db)):
    code = _portfolio(db, portfolio)
    _viewer(db, me, code)
    return {"imports": CP.imports(db, code, 50)}


@router.get("/completeness/exceptions")
def masterlist_exceptions(portfolio: str = "", status: str = "open", kind: str = "", me: User = Depends(SEE), db: Session = Depends(get_db)):
    code = _portfolio(db, portfolio)
    _viewer(db, me, code)
    return {**CP.exceptions(db, code, status, kind[:20]), "labels": CP.EXCEPTIONS}


class NoteIn(BaseModel):
    note: str = ""


@router.post("/completeness/exceptions/{eid}/resolve")
def masterlist_resolve(eid: int, body: NoteIn, me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    e = _run(CP.resolve, db, me, eid, body.note)
    db.commit()
    return CP.exception_out(e)


# ---- V11: reconciliations --------------------------------------------------------------------------------------------------------
@router.get("/recons")
def recons(portfolio: str = "", period: str = "", status: str = "", overdue: bool = False, q: str = "", me: User = Depends(SEE), db: Session = Depends(get_db)):
    code = _portfolio(db, portfolio)
    _viewer(db, me, code)
    out = _run(RC.listing, db, code, _period(period), status[:10], overdue, q)
    rep = RC.report_data(db, code, out["period"])
    out.update(portfolios=CP.portfolios_out(db, me), ages=rep["ages"], credits_recoverable=rep["credits_recoverable"], payments=PAY.counts(db),
               can_build=RC.may_build(db, me, code), can_prepare=RC.may_prepare(db, me, code), can_review=RC.may_review(db, me, code), item_types=RC.ITEM_TYPES)
    return out


@router.get("/recons/report")
def recons_report(portfolio: str = "", period: str = "", me: User = Depends(SEE), db: Session = Depends(get_db)):
    code = _portfolio(db, portfolio)
    _viewer(db, me, code)
    p = _period(period)
    return _xlsx(_run(RC.report, db, code, p), f"Reconciliations {code} {p}.xlsx")


@router.get("/recons/people")
def recons_people(me: User = Depends(SEE), db: Session = Depends(get_db)):
    """Who may own a reconciling item: the AP Tool's people."""
    return {"people": [{"id": u.id, "name": u.name or u.email} for u in R.people(db)]}


class BuildIn(BaseModel):
    portfolio: str = ""
    period: str = ""


@router.post("/recons/build")
def recons_build(body: BuildIn, me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    out = _run(RC.build, db, me, _portfolio(db, body.portfolio), _period(body.period))
    db.commit()
    return out


class AddIn(BaseModel):
    portfolio: str = ""
    period: str = ""
    account: str = ""
    vendor: str = ""
    why: str = ""


@router.post("/recons/add")
def recons_add(body: AddIn, me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    r = _run(RC.add, db, me, _portfolio(db, body.portfolio), _period(body.period), body.account[:60], body.vendor[:40], body.why[:300])
    db.commit()
    return RC.detail(db, r.id)


def _recon(db: Session, me: User, rid: int) -> dict:
    d = _run(RC.detail, db, rid)
    _viewer(db, me, d["portfolio"])
    return d


@router.get("/recons/{rid}")
def recon(rid: int, me: User = Depends(SEE), db: Session = Depends(get_db)):
    d = _recon(db, me, rid)
    d.update(can_prepare=RC.may_prepare(db, me, d["portfolio"]), can_send_back=RC.may_review(db, me, d["portfolio"]),
             can_review=RC.may_review(db, me, d["portfolio"]) and me.id not in d["editors"])  # 1.0.104: no editor reviews it
    return d


class PrepareIn(BaseModel):
    statement_balance: Optional[float] = None
    ledger_balance: Optional[float] = None
    note: Optional[str] = None
    sign: bool = False


@router.put("/recons/{rid}")
def recon_prepare(rid: int, body: PrepareIn, me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    data = body.model_dump(exclude_unset=True)
    _run(RC.prepare, db, me, rid, data)
    db.commit()
    return RC.detail(db, rid)


class ReviewIn(BaseModel):
    outcome: str = "reviewed"
    note: str = ""


@router.post("/recons/{rid}/review")
def recon_review(rid: int, body: ReviewIn, me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    _run(RC.review, db, me, rid, body.outcome, body.note)
    db.commit()
    return RC.detail(db, rid)


@router.post("/recons/{rid}/send-back")
def recon_send_back(rid: int, body: NoteIn, me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    """1.0.104 (review finding): the reviewer sends a prepared reconciliation back to the preparer (open again)."""
    _run(RC.send_back, db, me, rid, body.note)
    db.commit()
    return RC.detail(db, rid)


class ItemIn(BaseModel):
    type: Optional[str] = None
    amount: Optional[float] = None
    text: Optional[str] = None
    owner_id: Optional[int] = None
    target_date: Optional[str] = None


@router.post("/recons/{rid}/items")
def recon_item_add(rid: int, body: ItemIn, me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    _run(RC.item_save, db, me, rid, body.model_dump(exclude_unset=True))
    db.commit()
    return RC.detail(db, rid)


@router.put("/recons/{rid}/items/{iid}")
def recon_item_edit(rid: int, iid: int, body: ItemIn, me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    _run(RC.item_save, db, me, rid, body.model_dump(exclude_unset=True), iid)
    db.commit()
    return RC.detail(db, rid)


@router.post("/recons/{rid}/items/{iid}/clear")
def recon_item_clear(rid: int, iid: int, body: NoteIn, me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    _run(RC.item_clear, db, me, rid, iid, body.note)
    db.commit()
    return RC.detail(db, rid)
