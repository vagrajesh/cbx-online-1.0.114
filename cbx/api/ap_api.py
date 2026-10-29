# 1.0.98: from CBX-Connect 2.10.3 cbx/api/ap.py, on the old access checks (viewers by role, require_human)
"""AP Tool API (1.0.95; 1.0.98: CBX-Connect 2.10.3's routes on the old access checks): drop a folder → batches → files (filter, sort, search) → fix, teach, sign off; Quinn's drafts;
Paula's board; the thresholds (admins). A team tool: everyone with the AP Tool sees the team's folders.

2.10.0 invoice capture: each batch's buckets and SAP sheet, the two downloads, Needs you (Other, Odd, unread invoices,
vendors to code, red documents), the team's live view, and Settings — the master lists, the SAP values, the document
types and the capture models (everyone with the tool reads them; admins change them).

2.10.3 SAP knowledge: the whole of SAP's Document Type List (upload with a preview; "Check" rows an admin confirms), the
chart of accounts (G/L accounts: search, upload, Pull from SAP; the coding forms' G/L search), the vendors per company
code and SAP's cost centres (count, search, upload, Pull from SAP).

1.0.101: a new utilities list (upload or pull) sends the bills waiting for it in Other back to Mia; Other's "Add to the
utilities list" (admins) is a decide action (add_account).

1.0.102: a batch's posting date (the SAP sheet's date box: anyone who can download sets it; "Today" resets it); Settings
→ Sites (site code → cost centre) and Needs you → Sites to map (admins); a vendor coding's line rules and payment days;
SAP's vendor general data (AP Settings → Vendors (SAP general data): upload with the column mapping shown, or Pull from
SAP)."""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..ap import capture as C
from ..ap import engine as E
from ..ap import history as H  # 1.0.103
from ..ap import masters as M
from ..ap import portfolio as PF  # 1.0.104: portfolios (Valuence F0a)
from ..ap import roles as R  # 1.0.104: AP roles (Valuence F0b)
from ..ap import service as S
from .. import knowledge as K
from ..db import get_db
from ..models import StoredFile, User
from ..models.ap import ApBatch, ApCapture, ApItem, ApRule
from ..security.deps import require_human, require_tool
from ..warehouse import storage

router = APIRouter(prefix="/api/ap", tags=["ap"])
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def SEE(me: User = Depends(require_tool(S.SLUG)), db: Session = Depends(get_db)) -> User:  # noqa: N802
    """Everyone with the AP Tool. 1.0.98: the first AP request after a start seeds the company codes, the document types,
    the SAP values and SAP's lists (once; only into empty tables) — not cbx/main.py."""
    M.ensure_seeded(db)
    return me


def WRITE(me: User = Depends(SEE)) -> User:  # noqa: N802 — viewers look; they don't change
    if me.role == "viewer":
        raise HTTPException(403, "Viewers look; they don't change the AP Tool.")
    return me


def HUMAN(me: User = Depends(WRITE), _h: User = Depends(require_human)) -> User:  # noqa: N802
    """2.10.0: what changes capture data is a person's (C-EXA's account and sessions never pass), with the AP Tool to write."""
    return me


def ADMIN(me: User = Depends(HUMAN)) -> User:
    if not me.is_admin:
        raise HTTPException(403, "Only an admin changes this.")
    return me


def _refuse(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from None
    except (S.Refused, C.Refused, M.Refused, H.Refused, PF.Refused) as e:  # 1.0.103: + the posting history; 1.0.104: + portfolios
        raise HTTPException(400, str(e)) from None


def _xlsx(data: bytes, name: str) -> Response:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:120] or "file.xlsx"
    return Response(data, media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="{safe}"', "Cache-Control": "no-store"})


def _batch(db: Session, bid: int) -> ApBatch:
    b = db.get(ApBatch, bid)
    if b is None:
        raise HTTPException(404, "No such folder.")
    return b


def _capture(db: Session, cid: int) -> ApCapture:
    c = db.get(ApCapture, cid)
    if c is None:
        raise HTTPException(404, "No such file.")
    return c


def _item(db: Session, iid: int) -> ApItem:
    it = db.get(ApItem, iid)
    if it is None:
        raise HTTPException(404, "No such file.")
    return it


@router.get("")
def overview(me: User = Depends(SEE), db: Session = Depends(get_db)):
    bs = db.query(ApBatch).order_by(ApBatch.id.desc()).limit(60).all()
    users = {u.id: u for u in db.query(User).filter(User.id.in_({b.user_id for b in bs} or {0})).all()}
    pfs = PF.batch_outs(db, bs)  # 1.0.104
    return {"batches": [{**S.batch_out(db, b, users), "portfolio": pfs.get(b.id)} for b in bs], "board": S.board(db), "team": S.team(), "settings": S.settings_of(db),
            "rules": S.rules_of(db), "kinds": [{"key": k, "label": v[0], "bot": v[1]} for k, v in E.KINDS.items()],
            "can_write": me.role != "viewer" and not me.is_axl, "is_admin": me.is_admin, "me": me.id, "ai": S.model_on(),
            "capture": _capture_board(db),
            "portfolios": PF.listing(db), "controls": R.me_out(db, me)}  # 1.0.104: the drop form's portfolios; my AP roles and the Controls tab


def _capture_board(db: Session) -> dict:
    """2.10.0: the capture numbers, and everything that needs a person (an Odd file the team couldn't place counts once)."""
    out = C.board(db)
    level3 = db.query(ApItem.id).filter(ApItem.level == 3, ApItem.status.in_(S.TODO))
    twice = db.query(ApCapture.id).filter(ApCapture.stage == "needs", ApCapture.item_id.in_(level3)).count()
    out["need_all"] = out["need_you"] + level3.count() - twice
    return out


@router.post("/batches")
async def drop(files: list[UploadFile] = File(...), name: str = Form(""), portfolio: str = Form(""), me: User = Depends(WRITE), db: Session = Depends(get_db)):
    pf = _refuse(PF.pick, db, portfolio)  # 1.0.104: the portfolio picked in the drop form ("" = from the company codes)
    if pf:
        R.require(db, me, "capturer", pf, what="Dropping a folder for " + pf)
    got = []
    for f in files:
        if f is not None and f.filename:
            got.append((f.filename, await f.read(S.MAX_MB * 1024 * 1024 + 1)))
    b = _refuse(S.create_batch, db, me, got, name)
    PF.set_batch(db, me, b, pf, on_drop=True)  # 1.0.104
    db.commit()
    db.refresh(b)
    return S.batch_out(db, b)


@router.get("/batches/{bid}")
def batch(bid: int, me: User = Depends(SEE), db: Session = Depends(get_db)):
    b = db.get(ApBatch, bid)
    if b is None:
        raise HTTPException(404, "No such folder.")
    items = db.query(ApItem).filter(ApItem.batch_id == bid).order_by(ApItem.id).all()
    users = S.users_for(db, items)
    return {**S.batch_out(db, b), "items": [S.item_out(i, users) for i in items], "capture": C.buckets(db, b)}


@router.post("/batches/{bid}/read")
def read_again(bid: int, me: User = Depends(WRITE), db: Session = Depends(get_db)):
    b = db.get(ApBatch, bid)
    if b is None:
        raise HTTPException(404, "No such folder.")
    _refuse(S.read_again, db, me, b)
    db.commit()
    return S.batch_out(db, db.get(ApBatch, bid))


@router.get("/items")
def items(status: str = "", kind: str = "", bot: str = "", level: Optional[int] = None, q: str = "", batch: Optional[int] = None, open: bool = False,
          me: User = Depends(SEE), db: Session = Depends(get_db)):
    qy = db.query(ApItem)
    if batch:
        qy = qy.filter(ApItem.batch_id == batch)
    if status:
        qy = qy.filter(ApItem.status.in_(status.split(",")))
    if open:
        qy = qy.filter(ApItem.status.in_(S.TODO))
    if kind:
        qy = qy.filter(ApItem.kind.in_(kind.split(",")))
    if bot:
        qy = qy.filter(ApItem.bot == bot)
    if level is not None:
        qy = qy.filter(ApItem.level == level)
    if q.strip():
        like = f"%{q.strip()}%"
        qy = qy.filter(or_(ApItem.name.ilike(like), ApItem.supplier.ilike(like), ApItem.ref.ilike(like)))
    rows = qy.order_by(ApItem.id.desc()).limit(1000).all()
    users = S.users_for(db, rows)
    return {"items": [S.item_out(i, users) for i in rows]}


@router.get("/items/{iid}")
def item(iid: int, me: User = Depends(SEE), db: Session = Depends(get_db)):
    it = _item(db, iid)
    return S.item_out(it, S.users_for(db, [it]), full=True)


class FixIn(BaseModel):
    kind: str = ""
    fields: dict = {}
    teach: str = ""
    note: str = ""


@router.post("/items/{iid}/fix")
def fix(iid: int, body: FixIn, me: User = Depends(WRITE), db: Session = Depends(get_db)):
    it = _refuse(S.fix, db, me, _item(db, iid), body.kind, body.fields, body.teach, body.note)
    db.commit()
    return S.item_out(it, S.users_for(db, [it]), full=True)


class SignIn(BaseModel):
    action: str
    note: str = ""


@router.post("/items/{iid}/sign")
def sign(iid: int, body: SignIn, me: User = Depends(WRITE), db: Session = Depends(get_db)):
    it = _refuse(S.sign, db, me, _item(db, iid), body.action, body.note)
    db.commit()
    return S.item_out(it, S.users_for(db, [it]), full=True)


@router.post("/items/{iid}/draft")
def draft(iid: int, me: User = Depends(WRITE), db: Session = Depends(get_db)):
    it = _refuse(S.draft, db, me, _item(db, iid))
    db.commit()
    return S.item_out(it, S.users_for(db, [it]), full=True)


class ReplyIn(BaseModel):
    text: str


@router.post("/items/{iid}/reply")
def reply(iid: int, body: ReplyIn, me: User = Depends(WRITE), db: Session = Depends(get_db)):
    it = _refuse(S.save_reply, db, me, _item(db, iid), body.text)
    db.commit()
    return S.item_out(it, S.users_for(db, [it]), full=True)


@router.get("/files/{fid}")
def file(fid: int, me: User = Depends(SEE), db: Session = Depends(get_db)):
    ok = db.query(ApItem.id).filter(ApItem.file_id == fid).first() or db.query(ApBatch.id).filter(ApBatch.report_file_id == fid).first()
    f = db.get(StoredFile, fid) if ok else None
    if f is None or not storage.abs_path(f).is_file():
        raise HTTPException(404, "That file isn't here.")
    return FileResponse(storage.abs_path(f), filename=f.name)


@router.put("/settings")
def settings(body: dict, me: User = Depends(WRITE), db: Session = Depends(get_db)):
    out = _refuse(S.save_settings, db, me, body)
    db.commit()
    return out


@router.post("/rules/{rid}/off")
def rule_off(rid: int, me: User = Depends(WRITE), db: Session = Depends(get_db)):
    r = db.get(ApRule, rid)
    if r is None:
        raise HTTPException(404, "No such rule.")
    if not me.is_admin and r.by_id != me.id:
        raise HTTPException(403, "Only the person who taught it, or an admin, turns a rule off.")
    r.is_active = False
    db.commit()
    return {"ok": True}


# ---- 2.10.0: invoice capture ----------------------------------------------------------------------------------------
@router.get("/batches/{bid}/sap")
def sap_sheet(bid: int, me: User = Depends(SEE), db: Session = Depends(get_db)):
    b = _batch(db, bid)
    return {"batch": S.batch_out(db, b), "capture": C.buckets(db, b), "docs": C.sheet(db, b), "posting": C.posting_out(b),  # 1.0.102
            "portfolio": PF.batch_out(db, b)}  # 1.0.104


class PostingIn(BaseModel):
    date: str = ""  # yyyy-mm-dd; "" or "today" = today (the day of the download)


@router.post("/batches/{bid}/sap/posting-date")
def sap_posting_date(bid: int, body: PostingIn, me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    """1.0.102: the batch's posting date — anyone who can download sets it (audited); Pip builds the rows again."""
    b = _batch(db, bid)
    out = _refuse(C.set_posting_date, db, me, b, body.date)
    db.commit()
    return {"posting": out, "capture": C.buckets(db, b), "docs": C.sheet(db, b)}


@router.get("/batches/{bid}/sap/review")
def sap_review(bid: int, me: User = Depends(SEE), db: Session = Depends(get_db)):
    b = _batch(db, bid)
    return _xlsx(C.review(db, b), f"SAP_review_{b.name}_{date.today():%Y%m%d}.xlsx")


class UploadIn(BaseModel):
    include_yellow: bool = False


@router.post("/batches/{bid}/sap/upload")
def sap_upload(bid: int, body: UploadIn, me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    b = _batch(db, bid)
    try:
        data, n = C.download(db, me, b, body.include_yellow)
    except C.Refused as e:
        db.commit()  # 2.10.0: Tess's fresh test stays (a document she just turned red shows red), though nothing went out
        raise HTTPException(400, str(e)) from None
    db.commit()
    return _xlsx(data, f"SAP_upload_{b.name}_{date.today():%Y%m%d}.xlsx")


@router.post("/batches/{bid}/sap/release")
def sap_release(bid: int, me: User = Depends(ADMIN), db: Session = Depends(get_db)):
    b = _batch(db, bid)
    n = _refuse(C.release, db, me, db.query(ApCapture).filter(ApCapture.batch_id == b.id, ApCapture.uploaded_at.isnot(None)).all())
    db.commit()
    return {"released": n}


@router.get("/captures/{cid}")
def capture(cid: int, me: User = Depends(SEE), db: Session = Depends(get_db)):
    return C.capture_out(db, _capture(db, cid), admin=me.is_admin)


@router.get("/captures/{cid}/page/{n}")
def capture_page(cid: int, n: int, me: User = Depends(SEE), db: Session = Depends(get_db)):
    png = _refuse(C.page_png, db, _capture(db, cid), n)
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


class DecideIn(BaseModel):
    action: str
    data: dict = {}


@router.post("/captures/{cid}/decide")
def capture_decide(cid: int, body: DecideIn, me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    c = _refuse(C.decide, db, me, _capture(db, cid), body.action, body.data)
    db.commit()
    return C.capture_out(db, db.get(ApCapture, c.id), admin=me.is_admin)


@router.post("/captures/{cid}/release")
def capture_release(cid: int, me: User = Depends(ADMIN), db: Session = Depends(get_db)):
    n = _refuse(C.release, db, me, [_capture(db, cid)])
    db.commit()
    return {"released": n}


@router.get("/needs")
def needs(me: User = Depends(SEE), db: Session = Depends(get_db)):
    out = C.needs(db, admin=me.is_admin)  # 2.10.0: admins also see what people added, to confirm
    # 1.0.104: one small line per person-actionable control, linking into Controls ({key, sub, params, text}); each builder adds its own
    from ..ap import recons as _RC  # Builder C: overdue reconciliation items

    out.setdefault("controls", []).extend(_RC.needs_lines(db, me))
    from .ap_utilities_api import needs_lines as _U_lines  # Builder U: variances to decide, estimates to approve / escalate

    out["controls"].extend(_U_lines(db, me))
    from ..ap import dupcases as _DC, vendor_review as _VR  # Builder D: new vendors to review (V01), possible duplicates to settle (V09)

    out["controls"].extend(x for x in (_VR.needs_line(db, me), _DC.needs_line(db, me)) if x)
    from ..ap import controls_needs as _CN  # Builder R: documents waiting for review, VAT decisions (V05/V06), missing evidence (V12)

    out["controls"].extend(_CN.lines(db, me))
    return out


class VendorIn(BaseModel):
    ids: list[int]
    vendor_no: str
    gl: str
    cost_centre: str = ""
    tax_code: str = ""
    name: str = ""
    vat_no: str = ""
    company_code: str = ""
    only_this_company: bool = False
    payment_days: Optional[int] = None  # 1.0.102
    line_rules: list[dict] = []


@router.post("/vendors/code")
def code_vendor(body: VendorIn, me: User = Depends(HUMAN), db: Session = Depends(get_db)):
    out = _refuse(C.code_vendor, db, me, body.model_dump(exclude={"ids"}), body.ids[:500])
    db.commit()
    w = _gl_warning(db, dict(out["vendor"]))
    return {**out, **({"warning": w["warning"], "missing": w["missing"]} if w.get("warning") else {})}  # 1.0.102: the line rules' G/Ls too


class SiteIn(BaseModel):
    site_code: str
    cost_centre: str
    company_code: str = ""
    note: str = ""


@router.post("/sites/map")
def site_map(body: SiteIn, me: User = Depends(ADMIN), db: Session = Depends(get_db)):
    """1.0.102: Needs you → Sites to map: the site's cost centre (admins); its invoices are built again."""
    out = _refuse(C.map_site, db, me, body.model_dump())
    db.commit()
    return out


@router.get("/live")
def live(batch: Optional[int] = None, me: User = Depends(SEE), db: Session = Depends(get_db)):
    return C.live(db, batch, me.is_admin)


# ---- Settings: everyone with the tool reads; admins change ---------------------------------------------------------------
@router.get("/masters")
def masters(me: User = Depends(SEE), db: Session = Depends(get_db)):
    from ..models.ap import ApEntity, ApUtilityAccount, ApVendorCode  # 1.0.102: the sites and SAP's vendor list are counted in knowledge_counts

    out = {"counts": {"entities": db.query(ApEntity).count(), "utilities": db.query(ApUtilityAccount).count(), "vendors": db.query(ApVendorCode).count(),
                      "learned": db.query(ApVendorCode).filter(ApVendorCode.learned.is_(True)).count(), **M.knowledge_counts(db)},  # 2.10.3
           "sap": M.sap_values(db), "doc_types": M.doc_types(db), "meanings": list(M.MEANINGS), "capture": C.settings_of(db), "models": C.default_models(),
           "columns": {k: [f for f, _ in v] for k, v in M.COLUMNS.items()}, "is_admin": me.is_admin,
           "gl_missing": M.gl_missing(db)}  # 2.10.3: G/Ls in use that the chart of accounts doesn't have
    out["history"] = H.counts(db)  # 1.0.103: SAP's posting history
    if me.is_admin:
        out["connections"] = M.connections(db)
        out["pull"] = M.pull_settings(db)
    else:  # 1.0.98: users never see model names (the Models pane is for admins)
        out["models"] = {}
        out["capture"] = {k: v for k, v in out["capture"].items() if not k.endswith("_model")}
    return out


# ---- 1.0.103: SAP's posting history (vendor line items FBL1N, G/L line items FBL3N) ------------------------------------------------
@router.get("/history")
def history_rows(q: str = "", limit: int = 50, me: User = Depends(SEE), db: Session = Depends(get_db)):
    return {**H.search_posted(db, q, limit), "counts": H.counts(db)}


@router.post("/history/import")
async def history_import(vendor_items: Optional[UploadFile] = File(None), gl_items: Optional[UploadFile] = File(None), confirm: bool = Form(False),
                         me: User = Depends(ADMIN), db: Session = Depends(get_db)):
    """Settings → Posting history (admins): one export or both; confirm=False shows what would change, true saves it. Invoices
    waiting for a vendor coding are matched again and the batches not uploaded yet are tested again (a duplicate turns red)."""
    rows = {}
    for kind, up in (("vendor", vendor_items), ("gl", gl_items)):
        if up is not None and (up.filename or ""):
            data = await up.read(M.MAX_MB * 1024 * 1024 + 1)
            rows[kind] = _refuse(H.read_items, kind, data, up.filename or "")
    out = _refuse(H.save, db, me, rows.get("vendor"), rows.get("gl"), source="excel", confirm=confirm)
    if out.get("saved"):
        db.flush()
        out["rematched"] = C.sap_vendors_changed(db, me)
    db.commit()
    return out


@router.get("/vendors/history")
def vendor_history(vendor: str = "", company: str = "", me: User = Depends(SEE), db: Session = Depends(get_db)):
    """1.0.103: Code this vendor → a vendor picked from several: SAP's general data and its history in the posting company."""
    v = re.sub(r"\s+", "", vendor)[:20]
    if not v:
        raise HTTPException(400, "Give the vendor number.")
    return {"sap": K.sap_vendor(db, v), "history": H.coding(db, company[:12], v)}


@router.get("/masters/{kind}")
def masters_rows(kind: str, q: str = "", limit: int = 50, me: User = Depends(SEE), db: Session = Depends(get_db)):
    return _refuse(M.search, db, kind, q, limit)


@router.get("/gl-search")
def gl_search(q: str = "", me: User = Depends(SEE), db: Session = Depends(get_db)):
    """2.10.3: the coding forms' G/L search — by number or by words in the name, from the chart of accounts."""
    return {"rows": K.gl_search(db, q[:80], 20)}


@router.get("/masters/{kind}/file")
def masters_file(kind: str, me: User = Depends(SEE), db: Session = Depends(get_db)):
    data = _refuse(M.list_file, db, kind)
    return _xlsx(data, {"entities": "Company_codes", "utilities": "vendor_mappings", "vendors": "AP_vendors", "gl_accounts": "Chart_of_Accounts",
                        "vendor_companies": "Vendor_Master_Comp_Code", "cost_centres": "Cost_Center_List", "sites": "AP_sites", "sap_vendors": "LFA1_vendors"}.get(kind, kind) + ".xlsx")


@router.post("/masters/{kind}/import")
async def masters_import(kind: str, file: UploadFile = File(...), confirm: bool = Form(False), me: User = Depends(ADMIN), db: Session = Depends(get_db)):
    data = await file.read(M.MAX_MB * 1024 * 1024 + 1)
    rows = _refuse(M.read_sheet, kind, data, file.filename or "")
    out = _refuse(M.replace, db, me, kind, rows, source="excel", confirm=confirm)
    if getattr(rows, "mapping", None):
        out["mapping"] = rows.mapping  # 1.0.102: which of the sheet's columns went where
    _rematch(db, me, kind, out)
    db.commit()
    return out


def _rematch(db: Session, me: User, kind: str, out: dict) -> None:
    """1.0.101: a new utilities list saved: the bills in Other waiting for it go back to Mia (and on down the line)."""
    if kind == "utilities" and out.get("saved"):
        db.flush()
        out["rematched"] = C.rematch(db, me)
    elif kind == "sites" and out.get("saved"):  # 1.0.102: the sites' invoices are built again with their cost centres
        db.flush()
        out["rebuilt"] = C.site_saved(db, me)
    elif kind == "sap_vendors" and out.get("saved"):  # 1.0.102: invoices waiting for a vendor coding are matched again
        db.flush()
        out["rematched"] = C.sap_vendors_changed(db, me)


class PullIn(BaseModel):
    connection_id: int = 0
    path: str = ""
    columns: dict = {}
    confirm: bool = False
    preview_id: str = ""


@router.post("/masters/{kind}/pull")
def masters_pull(kind: str, body: PullIn, me: User = Depends(ADMIN), db: Session = Depends(get_db)):
    if body.confirm:  # 2.10.0: Save stores exactly the rows the admin was shown, not a second pull
        rows = _refuse(M.previewed, db, me, kind, body.preview_id)
    else:
        rows = _refuse(M.pull, db, me, kind, body.connection_id, body.path, body.columns)
    out = _refuse(M.replace, db, me, kind, rows, source="sap", confirm=body.confirm)
    _rematch(db, me, kind, out)  # 1.0.101
    if not body.confirm:
        out["preview_id"] = M.keep_preview(db, me, kind, rows)
    db.commit()
    return out


@router.post("/masters/{kind}/{rid}/confirm")
def masters_confirm(kind: str, rid: int, me: User = Depends(ADMIN), db: Session = Depends(get_db)):
    """2.10.0: an admin confirms a vendor coding or an account a person added in Needs you."""
    out = _refuse(C.confirm, db, me, kind, rid)
    db.commit()
    return out


@router.post("/masters/entities")
def entity_add(body: dict, me: User = Depends(ADMIN), db: Session = Depends(get_db)):
    e = _refuse(M.save_entity, db, me, body)
    db.commit()
    return M.entity_out(e)


@router.put("/masters/entities/{eid}")
def entity_edit(eid: int, body: dict, me: User = Depends(ADMIN), db: Session = Depends(get_db)):
    e = _refuse(M.save_entity, db, me, body, eid)
    db.commit()
    return M.entity_out(e)


def _gl_warning(db: Session, out: dict) -> dict:
    """2.10.3: a G/L the chart of accounts doesn't have is saved, with a warning the page shows. 1.0.102: the line rules'
    G/Ls too (`missing`: every one of them)."""
    gls = [out.get("gl") or ""] + [r.get("gl") or "" for r in out.get("line_rules") or []]
    missing = list(dict.fromkeys(g for g in gls if g and K.gl_account(db, g) is None))
    if missing:
        out["warning"] = f"G/L {missing[0]} is not in the chart of accounts CBX has."
        out["missing"] = missing
    return out


@router.post("/masters/vendors")
def vendor_add(body: dict, me: User = Depends(ADMIN), db: Session = Depends(get_db)):
    v = _refuse(M.save_vendor, db, me, body)
    db.commit()
    return _gl_warning(db, M.vendor_out(v))


@router.put("/masters/vendors/{vid}")
def vendor_edit(vid: int, body: dict, me: User = Depends(ADMIN), db: Session = Depends(get_db)):
    v = _refuse(M.save_vendor, db, me, body, vid)
    db.commit()
    return _gl_warning(db, M.vendor_out(v))


@router.post("/masters/sites")
def site_add(body: dict, me: User = Depends(ADMIN), db: Session = Depends(get_db)):
    """1.0.102: Settings → Sites (admins); the site's invoices are built again."""
    x = _refuse(M.save_site, db, me, body)
    C.site_saved(db, me, x.site_code)
    db.commit()
    return M.site_out(x)


@router.put("/masters/sites/{sid}")
def site_edit(sid: int, body: dict, me: User = Depends(ADMIN), db: Session = Depends(get_db)):
    x = _refuse(M.save_site, db, me, body, sid)
    C.site_saved(db, me, x.site_code)
    db.commit()
    return M.site_out(x)


@router.delete("/masters/{kind}/{rid}")
def masters_delete(kind: str, rid: int, me: User = Depends(ADMIN), db: Session = Depends(get_db)):
    _refuse(M.delete_row, db, me, kind, rid)
    if kind == "sites":  # 1.0.102: its invoices fall back to the vendor coding's cost centre
        C.site_saved(db, me)
    db.commit()
    return {"ok": True}


class SapValuesIn(BaseModel):
    fixed: list[dict]
    gl: list[dict]


@router.put("/sap-values")
def sap_values(body: SapValuesIn, me: User = Depends(ADMIN), db: Session = Depends(get_db)):
    out = _refuse(M.save_sap_values, db, me, body.fixed, body.gl)
    db.commit()
    return {**out, **M.sap_values(db)}


@router.get("/sap-values/file")
def sap_values_file(me: User = Depends(SEE), db: Session = Depends(get_db)):
    return _xlsx(M.sap_config_file(db), "sap_config.xlsx")


@router.post("/sap-values/import")
async def sap_values_import(file: UploadFile = File(...), confirm: bool = Form(False), me: User = Depends(ADMIN), db: Session = Depends(get_db)):
    data = await file.read(M.MAX_MB * 1024 * 1024 + 1)
    fixed, gl = _refuse(M.read_sap_config, data)
    out = _refuse(M.save_sap_values, db, me, fixed, gl, confirm=confirm, source="sap_config.xlsx")
    db.commit()
    return out


@router.post("/doc-types")
def doc_type_add(body: dict, me: User = Depends(ADMIN), db: Session = Depends(get_db)):
    d = _refuse(M.save_doc_type, db, me, body)
    db.commit()
    return {"id": d.id, "code": d.code}


@router.put("/doc-types/{did}")
def doc_type_edit(did: int, body: dict, me: User = Depends(ADMIN), db: Session = Depends(get_db)):
    d = _refuse(M.save_doc_type, db, me, body, did)
    db.commit()
    return {"id": d.id, "code": d.code}


@router.post("/doc-types/import")
async def doc_types_import(file: UploadFile = File(...), confirm: bool = Form(False), me: User = Depends(ADMIN), db: Session = Depends(get_db)):
    """2.10.3: a new Document Type List from SAP (the same columns): what is added, changed and removed, then saved."""
    data = await file.read(M.MAX_MB * 1024 * 1024 + 1)
    rows = _refuse(M.read_doc_type_list, data, file.filename or "")
    out = _refuse(M.replace_doc_types, db, me, rows, source="excel", confirm=confirm)
    db.commit()
    return out


@router.post("/doc-types/{did}/confirm")
def doc_type_confirm(did: int, me: User = Depends(ADMIN), db: Session = Depends(get_db)):
    """2.10.3: an admin confirms a "Check" row (CBX's meaning stands beside SAP's description)."""
    d = _refuse(M.confirm_doc_type, db, me, did)
    db.commit()
    return {"id": d.id, "code": d.code, "needs_check": d.needs_check}


@router.put("/capture-settings")
def capture_settings(body: dict, me: User = Depends(ADMIN), db: Session = Depends(get_db)):
    out = _refuse(C.save_settings, db, me, body)
    db.commit()
    return out


# 1.0.103: which bot is busy with which document (GET /api/ap/busy, /api/support/busy) — its own router, included with this one
from .busy_api import router as _busy_router  # noqa: E402
from .ap_controls_api import router as _controls_router  # noqa: E402  # 1.0.104: portfolios, AP roles (and the Valuence controls)
from .ap_completeness_api import router as _completeness_router  # noqa: E402  # 1.0.104 (Builder C): V10 completeness, V11 reconciliations

extra_routers = (_busy_router, _controls_router, _completeness_router)
from .ap_utilities_api import router as _utilities_router  # noqa: E402  # 1.0.104 (Builder U): V07 variances/disputes/tariffs, V08 estimates

extra_routers = extra_routers + (_utilities_router,)
from .ap_vendor_dups_api import router as _vendor_dups_router  # noqa: E402  # 1.0.104 (Builder D): V01 new-vendor reviews, V09 duplicates register

extra_routers = extra_routers + (_vendor_dups_router,)
from .ap_review_api import router as _review_router  # noqa: E402  # 1.0.104 (Builder R): V05 review, V06 VAT decisions, V12 evidence

extra_routers = extra_routers + (_review_router,)
from .ap_simple_api import router as _simple_router  # noqa: E402  # 1.0.105 (Builder S): the "Make SAP upload" screen

extra_routers = extra_routers + (_simple_router,)
