"""IFRS 16 API (1.0.92) — CBX-Connect 2's, line for line: uploads → months, the ledger check, Sequentra (check, download, the connection), the Rates table."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..audit import log
from ..connections import service as CS
from ..db import get_db
from ..ifrs16 import page as S
from ..models import StoredFile, User
from ..models.connections import ApiConnection
from ..models.ifrs16_page import IfrsApiRun, IfrsLedgerCheck, IfrsMaster, IfrsMonth, IfrsRate, IfrsRecon, IfrsUpload
from ..security.deps import require_tool
from ..warehouse import storage

router = APIRouter(prefix="/api/ifrs16", tags=["ifrs16"])
SEE = require_tool(S.SLUG)


def WRITE(me: User = Depends(SEE)) -> User:  # noqa: N802 — viewers look; they don't change
    if me.role == "viewer":
        raise HTTPException(403, "Viewers look; they don't change IFRS 16.")
    return me


LIMIT = 60


def _refuse(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from None
    except (S.Refused, CS.ConnectionError_) as e:
        raise HTTPException(400, str(e)) from None


def _mine(q, model, me: User):
    return q if me.is_admin else q.filter(model.user_id == me.id)


def _sequentra(db: Session) -> Optional[ApiConnection]:
    rows = db.query(ApiConnection).all()
    return next((x for x in rows if x.slug == "sequentra"), None) or next((x for x in rows if "/sequentra" in (x.base_url or "").lower()), None)


@router.get("")
def overview(me: User = Depends(SEE), db: Session = Depends(get_db)):
    ups = _mine(db.query(IfrsUpload), IfrsUpload, me).order_by(IfrsUpload.id.desc()).limit(LIMIT).all()
    months = _mine(db.query(IfrsMonth), IfrsMonth, me).order_by(IfrsMonth.id.desc()).limit(LIMIT).all()
    ledger = _mine(db.query(IfrsLedgerCheck), IfrsLedgerCheck, me).order_by(IfrsLedgerCheck.id.desc()).limit(LIMIT).all()
    runs = _mine(db.query(IfrsApiRun), IfrsApiRun, me).order_by(IfrsApiRun.id.desc()).limit(LIMIT).all()
    recons = _mine(db.query(IfrsRecon), IfrsRecon, me).order_by(IfrsRecon.id.desc()).limit(LIMIT).all()
    masters = _mine(db.query(IfrsMaster), IfrsMaster, me).order_by(IfrsMaster.id.desc()).limit(LIMIT).all()
    users = {u.id: u for u in db.query(User).filter(User.id.in_({x.user_id for x in ups + months} or {0})).all()}
    c = _sequentra(db)
    return {"uploads": [S.upload_out(db, u, users.get(u.user_id)) for u in ups], "months": [S.month_out(db, m, users.get(m.user_id)) for m in months],
            "ledger": [S.ledger_out(db, r) for r in ledger], "api": [S.api_out(db, r) for r in runs],
            "recons": [S.recon_out(db, r) for r in recons],
            "masters": [S.master_out(db, r) for r in masters],
            "rates": [S.rate_out(r) for r in db.query(IfrsRate).order_by(IfrsRate.month.desc()).all()],
            "connection": ({"set": True, "active": c.is_active, "ticked": "ifrs16" in (c.tools or []), "has_key": bool(c.secret_sealed), **({"detail": CS.describe(c)} if me.is_admin else {})}
                           if c else {"set": False}),
            "can_write": me.role != "viewer", "is_admin": me.is_admin}


@router.post("/uploads")
async def upload(la: UploadFile = File(...), movements: UploadFile = File(...), sequentra: Optional[UploadFile] = File(None), steps: Optional[UploadFile] = File(None),
                 assets: Optional[UploadFile] = File(None), ciw: Optional[UploadFile] = File(None), me: User = Depends(WRITE), db: Session = Depends(get_db)):
    files = {}
    for k, f in (("la", la), ("movements", movements), ("sequentra", sequentra), ("steps", steps), ("assets", assets), ("ciw", ciw)):
        if f is not None and f.filename:
            files[k] = (f.filename, await f.read(S.MAX_MB * 1024 * 1024 + 1))
    u = _refuse(S.create_upload, db, me, files)
    return S.upload_out(db, u, me)


@router.post("/uploads/{uid}/read")
def read_again(uid: int, me: User = Depends(WRITE), db: Session = Depends(get_db)):
    u = db.get(IfrsUpload, uid)
    if u is None or not S.can_see(me, u):
        raise HTTPException(404, "That upload isn't yours.")
    return S.upload_out(db, _refuse(S.read_again, db, me, u), me)


class MonthIn(BaseModel):
    month: str
    source: str = "files"
    charges: bool = False


@router.post("/uploads/{uid}/month")
def month(uid: int, body: MonthIn, me: User = Depends(WRITE), db: Session = Depends(get_db)):
    u = db.get(IfrsUpload, uid)
    if u is None or not S.can_see(me, u):
        raise HTTPException(404, "That upload isn't yours.")
    m = _refuse(S.start_month, db, me, u, body.month.strip()[:7], source="api" if "api" in body.source.lower() or "sequentra" in body.source.lower() else "files", use_charges=body.charges)
    return S.month_out(db, m, me)


@router.post("/ledger")
async def ledger(file: UploadFile = File(...), as_at: str = Form(""), months_back: int = Form(12), sequentra: Optional[UploadFile] = File(None),
                 upload_id: Optional[int] = Form(None), entity: str = Form(""), span: str = Form("life"),
                 me: User = Depends(WRITE), db: Session = Depends(get_db)):
    data = await file.read(S.MAX_MB * 1024 * 1024 + 1)
    seq = (sequentra.filename, await sequentra.read(S.MAX_MB * 1024 * 1024 + 1)) if sequentra is not None and sequentra.filename else None
    r = _refuse(S.start_ledger, db, me, file.filename or "ledger.xlsx", data, as_at, months_back, sequentra=seq, upload_id=upload_id or None,
                entity=entity, span=(span or "life").strip().lower())
    return S.ledger_out(db, r)


@router.post("/uploads/{uid}/master")
async def master(uid: int, month: str = Form(...), as_at: str = Form(""), entity: str = Form(""), ledger: Optional[UploadFile] = File(None),
                 me: User = Depends(WRITE), db: Session = Depends(get_db)):
    u = db.get(IfrsUpload, uid)
    if u is None or not S.can_see(me, u):
        raise HTTPException(404, "That upload isn't yours.")
    led = (ledger.filename, await ledger.read(S.MAX_MB * 1024 * 1024 + 1)) if ledger is not None and ledger.filename else None
    r = _refuse(S.start_master, db, me, u, month.strip()[:7], as_at, entity, led)
    return S.master_out(db, r)


@router.post("/recon")
async def recon(period: str = Form(""), la: Optional[UploadFile] = File(None), rr06: Optional[UploadFile] = File(None), upload_id: Optional[int] = Form(None),
                me: User = Depends(WRITE), db: Session = Depends(get_db)):
    u = None
    if upload_id:
        u = db.get(IfrsUpload, upload_id)
        if u is None or not S.can_see(me, u):
            raise HTTPException(404, "That upload isn't yours.")
    files = {}
    for k, f in (("la", la), ("rr06", rr06)):
        if f is not None and f.filename:
            files[k] = (f.filename, await f.read(S.MAX_MB * 1024 * 1024 + 1))
    r = _refuse(S.start_recon, db, me, period, la=files.get("la"), rr06=files.get("rr06"), upload=u)
    return S.recon_out(db, r)


@router.get("/recon/{rid}")
def recon_detail(rid: int, me: User = Depends(SEE), db: Session = Depends(get_db)):
    r = db.get(IfrsRecon, rid)
    if r is None or not S.can_see(me, r):
        raise HTTPException(404, "That reconciliation isn't yours.")
    return S.recon_out(db, r, full=True)


class ApiIn(BaseModel):
    kind: str = "check"
    month: str = ""
    to_month: str = ""


@router.post("/api")
def api_run(body: ApiIn, me: User = Depends(WRITE), db: Session = Depends(get_db)):
    r = _refuse(S.start_api, db, me, body.kind.lower(), body.month.strip(), body.to_month.strip())
    return S.api_out(db, r)


class RateIn(BaseModel):
    month: str
    a: Optional[float] = None
    b: Optional[float] = None
    c: Optional[float] = None
    d: Optional[float] = None
    e: Optional[float] = None
    f: Optional[float] = None
    note: str = ""


@router.post("/rates")
def set_rate(body: RateIn, me: User = Depends(WRITE), db: Session = Depends(get_db)):
    m = body.month.strip()[:7]
    try:
        S.month_date(m)
    except (ValueError, IndexError):
        raise HTTPException(400, "Months look like 2026-08.") from None
    for b in "abcdef":
        v = getattr(body, b)
        if v is not None and not 0 <= v <= 50:
            raise HTTPException(400, f"Band {b.upper()} looks wrong: rates are percentages, e.g. 10.75.")
    r = db.query(IfrsRate).filter(IfrsRate.month == m).one_or_none() or IfrsRate(month=m, created_by_id=me.id)
    for b in "abcdef":
        setattr(r, b, getattr(body, b))
    r.note = body.note.strip()[:300]
    db.add(r)
    db.flush()
    log(db, "ifrs16_rates", actor=me, target=m, detail=" ".join(f"{b.upper()}={getattr(r, b)}" for b in "abcdef" if getattr(r, b) is not None))
    return S.rate_out(r)


@router.delete("/rates/{rid}")
def delete_rate(rid: int, me: User = Depends(WRITE), db: Session = Depends(get_db)):
    r = db.get(IfrsRate, rid)
    if r is None:
        raise HTTPException(404, "Already gone.")
    log(db, "ifrs16_rates_removed", actor=me, target=r.month)
    db.delete(r)
    return {"ok": True}


@router.get("/workbooks")
def workbooks(me: User = Depends(SEE)):
    from ..ifrs16 import template_info

    return {"workbooks": template_info.workbook_overview()}


@router.get("/files/{fid}")
def download(fid: int, me: User = Depends(SEE), db: Session = Depends(get_db)):
    f = db.get(StoredFile, fid)
    if f is None or (f.meta or {}).get("purpose") != S.SLUG or not (me.is_admin or f.uploaded_by_id == me.id):
        raise HTTPException(404, "There's no file here.")
    p = storage.abs_path(f)
    if not p.is_file():
        raise HTTPException(410, "The file is no longer on the server.")
    return FileResponse(p, filename=f.name, headers={"X-Content-Type-Options": "nosniff"})


# ---- the Sequentra connection (admins) ---------------------------------------------------------------------
class ConnIn(BaseModel):
    base_url: Optional[str] = None
    header_name: Optional[str] = None
    secret: Optional[str] = None
    is_active: Optional[bool] = None


@router.post("/connection")
def set_connection(body: ConnIn, me: User = Depends(SEE), db: Session = Depends(get_db)):
    """Make or change the Sequentra connection. The key is write-only: sealed, never shown, never logged."""
    if not me.is_admin:
        raise HTTPException(403, "An admin sets the Sequentra connection.")
    from ..ifrs16 import api as SQ

    c = _sequentra(db)
    if c is None:
        c = _refuse(CS.create, db, me, name="Sequentra", base_url=body.base_url or SQ.BASE, auth_kind="header", header_name=body.header_name or SQ.HEADER,
                    secret=body.secret or "", tools=["ifrs16"], description="Lease facts and reports for IFRS 16 (the key the finance team uses in Power BI).")
    else:
        fields = {k: v for k, v in body.model_dump().items() if v is not None}
        fields["tools"] = sorted(set(c.tools or []) | {"ifrs16"})
        _refuse(CS.update, db, c, me, **fields)
    return CS.describe(c, db)


@router.post("/connection/test")
def test_connection(me: User = Depends(SEE), db: Session = Depends(get_db)):
    if not me.is_admin:
        raise HTTPException(403, "An admin tests the Sequentra connection.")
    c = _sequentra(db)
    if c is None:
        raise HTTPException(404, "There's no Sequentra connection yet.")
    return CS.test(db, c, me)
