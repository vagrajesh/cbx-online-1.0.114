# 1.0.104: new (Builder U) — the routes of V07 (variances, disputes, tariffs, check meters) and V08 (estimates); included
# with the AP Tool's routes through ap_api.extra_routers
"""Controls → Variances & disputes and Estimates (reviewers, approvers and admins), Settings → Tariffs (admins), and the
bill history of an account. Decisions go through cbx/ap/variance.py and cbx/ap/estimates.py (roles and audit there)."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..ap import bill_history as BH
from ..ap import estimates as ES
from ..ap import roles as R
from ..ap import tariffs as T
from ..ap import variance as VAR
from ..db import get_db
from ..models import User
from .ap_api import ADMIN, HUMAN, WRITE, _xlsx

router = APIRouter(prefix="/api/ap", tags=["ap"])
MAX_UPLOAD = 20 * 1024 * 1024


def CTL(me: User = Depends(WRITE), db: Session = Depends(get_db)) -> User:  # noqa: N802
    """The Controls registers: admins, and people holding an AP role (everyone with the tool while no roles are set)."""
    if not (me.is_admin or R.roles_of(db, me)):
        raise HTTPException(403, "The controls are for the AP reviewers, approvers and admins.")
    return me


def _do(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except HTTPException:
        raise
    except PermissionError as e:
        raise HTTPException(403, str(e)) from None
    except LookupError as e:
        raise HTTPException(404, str(e) or "Not found.") from None
    except (VAR.Refused, ES.Refused, T.Refused) as e:
        raise HTTPException(400, str(e)) from None


async def _read(f: UploadFile) -> bytes:
    data = await f.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "The file is larger than 20 MB.")
    return data


# ---- the bill history ------------------------------------------------------------------------------------------------------
@router.get("/bill-history/{account}")
def bill_history(account: str, me: User = Depends(CTL), db: Session = Depends(get_db)):
    return {"account_key": BH.acct_key(account), "periods": BH.periods(db, account)}


# ---- V07: variances and disputes ----------------------------------------------------------------------------------------------
@router.get("/variances")
def variances(state: str = "open", portfolio: str = "", account: str = "", me: User = Depends(CTL), db: Session = Depends(get_db)):
    return {"bills": VAR.listing(db, state, portfolio, account), "open": VAR.open_count(db), "disputes_open": len(VAR.disputes(db, "open"))}


@router.get("/variances/{hid}")
def variance_one(hid: int, me: User = Depends(CTL), db: Session = Depends(get_db)):
    return _do(VAR.bill_detail, db, hid)


class ReasonIn(BaseModel):
    reason: str = ""


@router.post("/variances/{hid}/accept")
def variance_accept(hid: int, body: ReasonIn, me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    _do(VAR.accept, db, me, hid, body.reason)
    db.commit()
    return VAR.bill_detail(db, hid)


class DisputeIn(BaseModel):
    reason: str = ""
    amount: Optional[Any] = None
    municipal_ref: str = ""
    owner: str = ""
    pay_under_dispute: bool = False


@router.post("/variances/{hid}/dispute")
def variance_dispute(hid: int, body: DisputeIn, me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    d = _do(VAR.dispute, db, me, hid, body.model_dump())
    db.commit()
    return VAR.dispute_out(d)


@router.get("/disputes")
def disputes(status: str = "open", portfolio: str = "", me: User = Depends(CTL), db: Session = Depends(get_db)):
    return {"disputes": VAR.disputes(db, status, portfolio)}


@router.get("/disputes.xlsx")
def disputes_xlsx(status: str = "", portfolio: str = "", me: User = Depends(CTL), db: Session = Depends(get_db)):
    return _xlsx(VAR.disputes_xlsx(db, status, portfolio), "AP disputes.xlsx")


class DisputeEdit(BaseModel):
    municipal_ref: Optional[str] = None
    owner: Optional[str] = None
    amount: Optional[Any] = None
    pay_under_dispute: Optional[bool] = None
    status: Optional[str] = None
    resolution: Optional[str] = None


@router.put("/disputes/{did}")
def dispute_edit(did: int, body: DisputeEdit, me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    d = _do(VAR.dispute_update, db, me, did, body.model_dump(exclude_none=True))
    db.commit()
    return VAR.dispute_out(d)


@router.post("/check-meters")
async def check_meters(file: UploadFile = File(...), me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    data = await _read(file)
    out = _do(VAR.upload_check_meters, db, me, data, file.filename or "")
    db.commit()
    return out


@router.get("/check-meters/ranking")
def no_meter(portfolio: str = "", me: User = Depends(CTL), db: Session = Depends(get_db)):
    rows = VAR.no_meter_ranking(db, portfolio)
    return {"sites": rows[:100], "total": len(rows)}


@router.get("/check-meters/ranking.xlsx")
def no_meter_xlsx(portfolio: str = "", me: User = Depends(CTL), db: Session = Depends(get_db)):
    return _xlsx(VAR.ranking_xlsx(db, portfolio), f"No check meter{(' ' + portfolio) if portfolio else ''}.xlsx")


# ---- Settings → Tariffs (admins change; everyone in Controls reads) ------------------------------------------------------------
@router.get("/tariffs")
def tariffs(me: User = Depends(CTL), db: Session = Depends(get_db)):
    return {"tariffs": [T.out(t) for t in T.rows(db)], "columns": [{"key": k, "label": lbl} for k, lbl in T.COLUMNS], "can_edit": me.is_admin}


@router.get("/tariffs.xlsx")
def tariffs_xlsx(me: User = Depends(CTL), db: Session = Depends(get_db)):
    return _xlsx(T.download(db), "AP tariffs.xlsx")


@router.post("/tariffs")
def tariff_add(body: dict, me: User = Depends(ADMIN), db: Session = Depends(get_db)):
    t = _do(T.save, db, me, body)
    db.commit()
    return T.out(t)


@router.put("/tariffs/{tid}")
def tariff_edit(tid: int, body: dict, me: User = Depends(ADMIN), db: Session = Depends(get_db)):
    t = _do(T.save, db, me, body, tid)
    db.commit()
    return T.out(t)


@router.delete("/tariffs/{tid}")
def tariff_delete(tid: int, me: User = Depends(ADMIN), db: Session = Depends(get_db)):
    _do(T.delete, db, me, tid)
    db.commit()
    return {"ok": True}


@router.post("/tariffs/upload")
async def tariff_upload(file: UploadFile = File(...), me: User = Depends(ADMIN), db: Session = Depends(get_db)):
    data = await _read(file)
    out = _do(T.upload, db, me, data, file.filename or "")
    db.commit()
    return out


# ---- V08: estimates -------------------------------------------------------------------------------------------------------------
@router.get("/estimates")
def estimates(status: str = "open", portfolio: str = "", me: User = Depends(CTL), db: Session = Depends(get_db)):
    return {"missing": ES.missing(db, portfolio), "estimates": ES.register(db, status, portfolio), "escalations": ES.escalations(db, portfolio),
            "bases": [{"key": k, "label": ES.BASIS_WORDS[k]} for k in ES.BASES], "me": me.id}


@router.get("/estimates/propose")
def estimate_propose(account: str, period: str, basis: str = "", me: User = Depends(CTL), db: Session = Depends(get_db)):
    return _do(ES.propose, db, account, period[:7], basis or None)


class EstimateIn(BaseModel):
    account_key: str = ""
    period: str = ""
    basis: str = ""
    value: Optional[Any] = None
    note: str = ""


@router.post("/estimates")
def estimate_prepare(body: EstimateIn, me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    e = _do(ES.prepare, db, me, body.model_dump())
    db.commit()
    return ES.out(e)


@router.post("/estimates/{eid}/approve")
def estimate_approve(eid: int, me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    e = _do(ES.approve, db, me, eid)
    db.commit()
    return ES.out(e)


@router.post("/estimates/{eid}/reject")
def estimate_reject(eid: int, body: ReasonIn, me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    e = _do(ES.reject, db, me, eid, body.reason)
    db.commit()
    return ES.out(e)


class EscalateIn(BaseModel):
    account_key: str = ""
    note: str = ""


@router.post("/estimates/escalate")
def estimate_escalate(body: EscalateIn, me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    out = _do(ES.escalate, db, me, body.account_key, body.note)
    db.commit()
    return out


@router.get("/estimates/report.xlsx")
def estimates_report(month: str = "", me: User = Depends(CTL), db: Session = Depends(get_db)):
    return _xlsx(ES.report_xlsx(db, month), f"AP open estimates {month or 'this month'}.xlsx")


# ---- Needs you: one small line each (GET /api/ap/needs → "controls") -------------------------------------------------------------
def needs_lines(db: Session, user: Optional[User]) -> list[dict]:
    if user is None:
        return []
    held = {r for rs in R.roles_of(db, user).values() for r in rs}
    out = []
    if user.is_admin or "reviewer" in held:
        n = VAR.open_count(db)
        if n:
            out.append({"key": "variances", "sub": "variances", "text": f"{n} utility bill{'s' if n != 1 else ''} with a variance to accept or dispute", "count": n})
    if user.is_admin or "approver" in held:
        n = db.query(ES.ApEstimate.id).filter(ES.ApEstimate.status == "prepared", ES.ApEstimate.prepared_by_id != user.id).count()
        if n:
            out.append({"key": "estimates_approve", "sub": "estimates", "text": f"{n} estimate{'s' if n != 1 else ''} wait{'' if n != 1 else 's'} for your approval", "count": n, "params": {"status": "prepared"}})
    esc = [r for r in ES.escalations(db) if not r["escalated_at"]]
    if esc and (user.is_admin or held & {"capturer", "reviewer", "approver"}):
        out.append({"key": "estimates_escalate", "sub": "estimates", "text": f"{len(esc)} account{'s' if len(esc) != 1 else ''} on estimates {esc[0]['threshold']}+ months running: escalate to Council Liaison",
                    "count": len(esc), "params": {"show": "escalate"}})
    return out
