# 1.0.104: new — the Valuence AP controls' routes (SPEC-1104), included with the AP Tool's (cbx/api/ap_api.py extra_routers)
"""Phase 1 (F0a/F0b): Settings → Portfolios and Settings → AP people (admins), and a batch's portfolio (the SAP sheet).
Later builders add their Controls routes here or in a module of their own listed in ap_api.extra_routers."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..ap import portfolio as PF
from ..ap import roles as R
from ..db import get_db
from ..models import User
from ..models.ap import ApBatch
from .ap_api import ADMIN, HUMAN, _refuse

router = APIRouter(prefix="/api/ap", tags=["ap"])


def _found(fn, *a, **kw):
    """_refuse, and "not found" (LookupError) as a 404."""
    try:
        return _refuse(fn, *a, **kw)
    except LookupError as e:
        raise HTTPException(404, str(e) or "Not found.") from None


# ---- Settings → Portfolios (admins) ------------------------------------------------------------------------------------------
@router.get("/portfolios")
def portfolios(me: User = Depends(ADMIN), db: Session = Depends(get_db)):
    return {"portfolios": PF.listing(db, full=True), "keys": PF.keys_out()}


class PortfolioIn(BaseModel):
    code: str = ""
    name: Optional[str] = None
    company_codes: Optional[str] = None
    fixed: Optional[dict] = None
    gl: Optional[dict] = None
    settings: Optional[dict] = None
    notes: Optional[dict] = None
    note: Optional[str] = None
    is_active: Optional[bool] = None
    is_default: Optional[bool] = None


@router.post("/portfolios")
def portfolio_add(body: PortfolioIn, me: User = Depends(ADMIN), db: Session = Depends(get_db)):
    p = _found(PF.create, db, me, body.model_dump(exclude_none=True))
    db.commit()
    return PF.out(p, full=True)


@router.put("/portfolios/{code}")
def portfolio_edit(code: str, body: PortfolioIn, me: User = Depends(ADMIN), db: Session = Depends(get_db)):
    p = _found(PF.save, db, me, code, body.model_dump(exclude_none=True, exclude={"code"}))
    db.commit()
    return PF.out(p, full=True)


# ---- a batch's portfolio -----------------------------------------------------------------------------------------------------
class BatchPortfolioIn(BaseModel):
    portfolio: str = ""  # "" = from each document's account or company code


@router.post("/batches/{bid}/portfolio")
def batch_portfolio(bid: int, body: BatchPortfolioIn, me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    """The batch's portfolio (picked, or "" = from the company codes): the documents not yet uploaded are built again."""
    b = db.get(ApBatch, bid)
    if b is None:
        raise HTTPException(404, "No such folder.")
    code = _found(PF.check_move, db, me, b, body.portfolio)  # 1.0.104 review: the roles in the old, new and documents' own portfolios; no contradiction
    out = _found(PF.set_batch, db, me, b, code)
    db.commit()
    return {"portfolio": out}


# ---- Settings → AP people (admins) -------------------------------------------------------------------------------------------
@router.get("/roles")
def roles_grid(me: User = Depends(ADMIN), db: Session = Depends(get_db)):
    return R.grid(db)


class RolesIn(BaseModel):
    portfolio: str = ""  # "" = every portfolio
    roles: list[str] = []


@router.put("/roles/{user_id}")
def roles_set(user_id: int, body: RolesIn, me: User = Depends(ADMIN), db: Session = Depends(get_db)):
    out = _found(R.set_roles, db, me, user_id, body.portfolio, body.roles)
    db.commit()
    return out
