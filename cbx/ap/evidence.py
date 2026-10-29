# 1.0.104: new — Builder R, V12 (VALUENCE-AP-GAPS.md §2 V12; SPEC-1104 Phase 2)
"""The evidence pack per batch, payment run and account.

    At each SAP upload download, the upload file and the review workbook as they were then are stored (Tools/AP evidence)
    with their SHA-256, who and when (`on_download`, from capture.download).
    People attach evidence on a batch, a payment run (an AP file Bo checked) or a utility account: a signed payment
    schedule, an F110 proposal, a funding request or N7 file, a DocuSign memo, a release audit report, a POP, a Council
    Liaison mail, a dispute reference, the landlord's municipal invoice … (.msg and .eml too). Audited ap_evidence_added.
    Each portfolio's `evidence_checklist` says what a batch must have on file; a batch shows what is missing, and Needs
    you raises a batch still missing items `evidence_missing_days` after its first upload.
    The export is a zip per portfolio and month in the naming convention
    <portfolio>/<yyyy-mm>/<stage>/<account|batch>_<type>_<date>.<ext> with an index sheet (each file's hash checked
    against the one kept), ready for the Shared Drive or SharePoint.
    Each piece is kept `retention_years` for its type (the portfolio's table, "default" for the rest): deletion is refused
    before then. The warehouse never moves or deletes a tool's files, so the evidence can't be binned around this."""
from __future__ import annotations

import email
import email.policy
import hashlib
import io
import re
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from ..audit import log
from ..db import utcnow
from ..models import StoredFile, User
from ..models.ap import ApBatch, ApCapture, ApEvidence, ApItem, ApUtilityAccount
from . import controls_needs as CN
from . import portfolio as PF
from . import roles as R

FOLDER = "AP evidence"
MAX_MB = 25
# the kinds of evidence and the stage each belongs to (a checklist item not listed here is stage "other")
TYPES: dict[str, str] = {
    "SAP upload file": "upload", "Review workbook": "review",
    "Signed payment schedule": "approval", "SFM approval": "approval", "KAM approval": "approval", "DocuSign memo": "approval",
    "F110 proposal": "payment", "Funding request / N7 file": "payment",
    "Release audit report": "release", "Proof of payment": "release",
    "Council Liaison mail": "account", "Dispute reference": "account", "Landlord's municipal invoice": "account",
    "Other": "other",
}
STAGES = ("review", "upload", "approval", "payment", "release", "account", "other")
EXTS = {".pdf", ".msg", ".eml", ".xlsx", ".xlsm", ".xls", ".csv", ".docx", ".doc", ".png", ".jpg", ".jpeg", ".txt", ".zip"}
SEE_ROLES = ("poster", "reviewer", "approver", "recon_reviewer")


class Refused(PF.Refused):
    """A plain-words refusal (400)."""


def _name(u: Optional[User]) -> str:
    return (u.name or u.email) if u else ""


def stage_of(kind: str) -> str:
    return TYPES.get(kind) or next((s for t, s in TYPES.items() if t.lower() == str(kind or "").lower()), "other")


def types_for(db: Session, portfolio) -> list[str]:
    seen = list(TYPES)
    for x in PF.setting(db, portfolio, "evidence_checklist", []) or []:
        if isinstance(x, str) and x.strip() and x.strip().lower() not in {t.lower() for t in seen}:
            seen.append(x.strip())
    return seen


def keep_until(db: Session, portfolio, kind: str, at: Optional[datetime] = None) -> str:
    """The day before which the piece can't be deleted: its type's years in the portfolio's retention table, else the
    table's "default", else 5."""
    table = PF.setting(db, portfolio, "retention_years", {"default": 5}) or {}
    years = next((v for k, v in table.items() if str(k).strip().lower() == kind.lower()), table.get("default", 5)) if isinstance(table, dict) else 5
    try:
        years = int(float(years))
    except (TypeError, ValueError):
        years = 5
    d = (at or utcnow()).date()
    try:
        return d.replace(year=d.year + years).isoformat()
    except ValueError:  # 29 February
        return (d.replace(day=28).replace(year=d.year + years)).isoformat()


def _folder(db: Session):
    from ..warehouse import folders as F

    return F.tool_folder(db, FOLDER)


def _safe(s: str, n: int = 60) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(s or "")).strip("_")[:n] or "x"


def _store(db: Session, user: Optional[User], name: str, data: bytes):
    from ..warehouse import storage

    return storage.store_in(db, _folder(db), user, name, data, source="ap", mirror=False, meta={"purpose": "ap_evidence"})


def _add(db: Session, user: Optional[User], *, portfolio: str, period: str, kind: str, data: bytes, name: str, batch_id=None, run_item_id=None,
         account: str = "", note: str = "", source: str = "person", at: Optional[datetime] = None) -> ApEvidence:
    sha = hashlib.sha256(data).hexdigest()
    target = account or (f"batch{batch_id}" if batch_id else f"run{run_item_id}" if run_item_id else "portfolio")
    when = at or utcnow()
    f = _store(db, user, f"{_safe(portfolio, 20)}_{period}_{_safe(target, 40)}_{_safe(kind, 40)}_{when:%Y%m%d-%H%M%S}{Path(name).suffix.lower()[:8]}", data)
    e = ApEvidence(portfolio=portfolio, period=period, stage=stage_of(kind), type=kind, batch_id=batch_id, run_item_id=run_item_id, account=account or "", file_id=f.id,
                   name=Path(name).name[:255], sha256=sha, size=len(data), note=note[:2000], source=source, by_id=user.id if user else None, by_name=_name(user),
                   keep_until=keep_until(db, portfolio, kind, when), created_at=when)
    db.add(e)
    db.flush()
    return e


# ---- at each download (capture.download) ---------------------------------------------------------------------------------------
def on_download(db: Session, user: User, b: ApBatch, caps: list, upload: bytes) -> list[ApEvidence]:
    """V12.1: the SAP upload file and the review workbook as they were at this download, with their hashes."""
    from . import capture as C

    db.flush()  # the upload marks are in the review workbook
    p = PF.for_batch(db, b)
    code = p.code if p is not None else ""
    now = utcnow()
    period = f"{now:%Y-%m}"
    review = C.review(db, b)
    out = []
    for kind, data, name in (("SAP upload file", upload, f"SAP_upload_{_safe(b.name, 60)}.xlsx"), ("Review workbook", review, f"SAP_review_{_safe(b.name, 60)}.xlsx")):
        e = _add(db, user, portfolio=code, period=period, kind=kind, data=data, name=name, batch_id=b.id, source="download", at=now,
                 note=f"{len(caps)} documents in the upload")
        out.append(e)
    log(db, "ap_evidence_added", actor=user, target=f"ap_batch:{b.id}",
        detail=f"{b.name}: SAP upload file {out[0].sha256[:12]}…, review workbook {out[1].sha256[:12]}… ({len(caps)} documents)")
    return out


# ---- attaching -------------------------------------------------------------------------------------------------------------------
def may_see(db: Session, user: Optional[User], portfolio) -> bool:
    return bool(user is not None and (user.is_admin and R.can_write(db, user) or any(R.may(db, user, r, portfolio) for r in SEE_ROLES)))


def _mail_subject(name: str, data: bytes) -> str:
    """An .eml's subject (the approval mail's own words), for the note."""
    if not name.lower().endswith(".eml"):
        return ""
    try:
        m = email.message_from_bytes(data[:2_000_000], policy=email.policy.default)
        return re.sub(r"\s+", " ", str(m.get("subject") or "")).strip()[:200]
    except Exception:  # noqa: BLE001 — not a mail we can read: the file is kept all the same
        return ""


def attach(db: Session, user: User, *, kind: str, filename: str, data: bytes, batch_id: Optional[int] = None, run_item_id: Optional[int] = None,
           account: str = "", note: str = "", period: str = "", portfolio: str = "") -> ApEvidence:
    """V12.3: a person attaches evidence on a batch, a payment run or an account (one of them)."""
    from . import masters as M
    from .vatcheck import month_ok

    targets = [x for x in (batch_id, run_item_id, str(account or "").strip()) if x]
    if len(targets) != 1:
        raise Refused("Attach it to one folder, one payment run or one account.")
    if not data:
        raise Refused("The file is empty.")
    if len(data) > MAX_MB * 1024 * 1024:
        raise Refused(f"The file is over {MAX_MB} MB.")
    ext = Path(filename or "").suffix.lower()
    if ext not in EXTS:
        raise Refused(f"A {ext or 'file without an extension'} can't be kept as evidence: PDF, Outlook mail (.msg, .eml), Excel, CSV, Word, a picture, text or zip.")
    b = it = a = None
    acct_key = ""
    if batch_id:
        b = db.get(ApBatch, int(batch_id))
        if b is None:
            raise LookupError("No such folder.")
        p = PF.for_batch(db, b)
    elif run_item_id:
        it = db.get(ApItem, int(run_item_id))
        if it is None:
            raise LookupError("No such payment run.")
        if it.kind != "payment_run":
            raise Refused(f"{it.name} is not a payment run.")
        p = PF.for_batch(db, db.get(ApBatch, it.batch_id))
    else:
        acct_key = M.acct_key(account)
        a = db.query(ApUtilityAccount).filter(ApUtilityAccount.account_key == acct_key).first() if acct_key else None
        if a is None:
            raise LookupError(f"Account {account} is not in the utilities list.")
        p = PF.for_account(db, a)
    if portfolio and not (b or it or a):
        p = PF.get(db, portfolio) or p
    code = p.code if p is not None else ""
    if not may_see(db, user, code):
        raise R.Forbidden(f"Attaching evidence needs the poster, reviewer or approver role{' in ' + code if code else ''}. An admin gives it in AP Settings → AP people.")
    t = next((x for x in types_for(db, code) if x.lower() == re.sub(r"\s+", " ", str(kind or "")).strip().lower()), "")
    if not t:
        raise Refused("Pick what kind of evidence it is.")
    period = month_ok(period) or (f"{utcnow():%Y-%m}")
    subj = _mail_subject(filename, data)
    note = re.sub(r"\s+", " ", str(note or "")).strip()
    if subj:
        note = (note + " · " if note else "") + f"Mail: {subj}"
    e = _add(db, user, portfolio=code, period=period, kind=t, data=data, name=filename, batch_id=b.id if b else None, run_item_id=it.id if it else None,
             account=acct_key, note=note)
    where = f"folder {b.name}" if b else f"payment run {it.name}" if it else f"account {a.account}"
    log(db, "ap_evidence_added", actor=user, target=f"ap_batch:{b.id}" if b else f"ap_item:{it.id}" if it else f"ap_account:{acct_key}",
        detail=f"{t} on {where}: {e.name} ({e.sha256[:12]}…)" + (f" — {note}" if note else ""))
    if it is not None:
        from . import capture as C

        C._hist(it, _name(user), f"Evidence on file: {t} ({e.name}).")
    return e


def delete(db: Session, user: User, e: ApEvidence) -> None:
    """V12.6: an admin deletes a piece once its retention is over; before that it is refused."""
    if not user.is_admin:
        raise R.Forbidden("Only an admin deletes evidence, and only once its retention is over.")
    if e.keep_until and date.today().isoformat() < e.keep_until:
        raise Refused(f"Kept until {date.fromisoformat(e.keep_until):%d %b %Y}: the retention for {e.type} in {e.portfolio or 'this portfolio'} is not over.")
    f = db.get(StoredFile, e.file_id) if e.file_id else None
    if f is not None:
        from ..warehouse import storage

        path = storage.abs_path(f)
        db.delete(f)
        path.unlink(missing_ok=True)
    log(db, "ap_evidence_deleted", actor=user, target=f"ap_evidence:{e.id}", detail=f"{e.type} {e.name} ({e.sha256[:12]}…), kept until {e.keep_until}")
    db.delete(e)
    db.flush()


def file_of(db: Session, e: ApEvidence) -> tuple[Path, str]:
    from ..warehouse import storage

    f = db.get(StoredFile, e.file_id) if e.file_id else None
    p = storage.abs_path(f) if f is not None else None
    if p is None or not p.is_file():
        raise LookupError("The file is not on the server any more.")
    return p, e.name or f.name


# ---- what a batch has on file and what is missing -------------------------------------------------------------------------------
def _first_upload(db: Session, bid: int) -> Optional[datetime]:
    from sqlalchemy import func

    return db.query(func.min(ApCapture.uploaded_at)).filter(ApCapture.batch_id == bid).scalar()


def _aware(t: Optional[datetime]) -> Optional[datetime]:
    return t if t is None or t.tzinfo else t.replace(tzinfo=timezone.utc)


def batch_status(db: Session, b: ApBatch, rows: Optional[list] = None) -> dict:
    """The batch's checklist (its portfolio's), what is on file for each item, and what is missing."""
    p = PF.for_batch(db, b)
    code = p.code if p is not None else ""
    have = rows if rows is not None else db.query(ApEvidence).filter(ApEvidence.batch_id == b.id).all()
    items = []
    for kind in [x for x in PF.setting(db, code or None, "evidence_checklist", []) or [] if isinstance(x, str) and x.strip()]:
        got = [e for e in have if e.type.lower() == kind.strip().lower()]
        items.append({"type": kind.strip(), "stage": stage_of(kind.strip()), "n": len(got), "missing": not got})
    first = _first_upload(db, b.id)
    days = PF.setting(db, code or None, "evidence_missing_days", 7) or 0
    missing = [x["type"] for x in items if x["missing"]]
    late = bool(missing and first is not None and _aware(first) + timedelta(days=float(days)) <= utcnow())
    return {"batch_id": b.id, "batch": b.name, "portfolio": code, "items": items, "missing": missing, "uploaded": bool(first),
            "first_upload": first.isoformat(timespec="minutes") if first else "", "late": late, "on_file": len(have)}


def missing_batches(db: Session, user: Optional[User] = None, limit: int = 100) -> list[dict]:
    """Batches already uploaded whose checklist still misses items past `evidence_missing_days` (Needs you)."""
    from sqlalchemy import func

    out = []
    bids = [bid for bid, _t in db.query(ApCapture.batch_id, func.min(ApCapture.uploaded_at)).filter(ApCapture.uploaded_at.isnot(None))
            .group_by(ApCapture.batch_id).order_by(ApCapture.batch_id.desc()).limit(limit).all()]
    for b in db.query(ApBatch).filter(ApBatch.id.in_(bids or [0])).all():
        st = batch_status(db, b)
        if st["late"] and (user is None or may_see(db, user, st["portfolio"])):
            out.append(st)
    return sorted(out, key=lambda x: x["first_upload"])


@CN.register
def needs_lines(db: Session, user: User) -> list[dict]:
    """Needs you (posters, reviewers, approvers): folders past their days with evidence missing → Controls → Evidence."""
    if user is None or not any(R.may(db, user, r, None) for r in SEE_ROLES):
        return []
    late = missing_batches(db, user)
    if not late:
        return []
    n = len(late)
    return [{"key": "evidence", "sub": "evidence", "params": {"show": "missing"},
             "text": f"Evidence missing on {n} uploaded folder{'s' if n != 1 else ''} ({', '.join(late[0]['missing'][:2])}{'…' if len(late[0]['missing']) > 2 or n > 1 else ''})"}]


# ---- the register -----------------------------------------------------------------------------------------------------------------
def out(e: ApEvidence, batches: Optional[dict] = None) -> dict:
    b = (batches or {}).get(e.batch_id) if e.batch_id else None
    return {"id": e.id, "portfolio": e.portfolio, "period": e.period, "stage": e.stage, "type": e.type, "batch_id": e.batch_id, "batch": b.name if b else "",
            "run_item_id": e.run_item_id, "account": e.account, "name": e.name, "sha256": e.sha256, "size": e.size, "note": e.note, "source": e.source,
            "by": e.by_name, "at": e.created_at.isoformat(timespec="minutes") if e.created_at else "", "keep_until": e.keep_until,
            "deletable": bool(not e.keep_until or date.today().isoformat() >= e.keep_until)}


def listing(db: Session, user: User, *, portfolio: str = "", month: str = "", batch_id: Optional[int] = None, account: str = "", limit: int = 500) -> dict:
    from . import masters as M
    from .vatcheck import month_ok

    month = month_ok(month)
    q = db.query(ApEvidence)
    if portfolio:
        q = q.filter(ApEvidence.portfolio == str(portfolio).upper())
    if month:
        q = q.filter(ApEvidence.period == month)
    if batch_id:
        q = q.filter(ApEvidence.batch_id == int(batch_id))
    if account:
        q = q.filter(ApEvidence.account == M.acct_key(account))
    rows = [e for e in q.order_by(ApEvidence.id.desc()).limit(max(1, min(2000, limit))).all() if may_see(db, user, e.portfolio or None)]
    batches = {b.id: b for b in db.query(ApBatch).filter(ApBatch.id.in_({e.batch_id for e in rows if e.batch_id} or {0})).all()}
    code = str(portfolio or "").upper() or None
    out_ = {"evidence": [out(e, batches) for e in rows], "types": types_for(db, code), "stages": list(STAGES),
            "checklist": [x for x in PF.setting(db, code, "evidence_checklist", []) or [] if isinstance(x, str)]}
    if batch_id:
        b = db.get(ApBatch, int(batch_id))
        if b is not None:
            out_["batch"] = batch_status(db, b)
    out_["missing"] = [x for x in missing_batches(db, user) if not portfolio or x["portfolio"] == str(portfolio).upper()]
    out_["recent_batches"] = [{"id": b.id, "name": b.name, "at": b.created_at.isoformat(timespec="minutes") if b.created_at else ""}
                              for b in db.query(ApBatch).order_by(ApBatch.id.desc()).limit(40).all()]
    return out_


# ---- the export (V12.5) --------------------------------------------------------------------------------------------------------------
def export(db: Session, user: User, portfolio: str, month: str) -> tuple[bytes, str]:
    """A zip per portfolio and month: <portfolio>/<yyyy-mm>/<stage>/<account|batch>_<type>_<date>.<ext>, plus index.xlsx
    (each file's path, what it is, who, when, its SHA-256 and whether the file still matches it). Audited."""
    import openpyxl

    from .. import xlsafe
    from . import sap as P
    from .vatcheck import month_ok
    from ..warehouse import storage

    month = month_ok(month) or f"{utcnow():%Y-%m}"
    p = PF.get(db, portfolio) if portfolio else PF.default(db)
    if p is None:
        raise LookupError("No such portfolio.")
    if not may_see(db, user, p.code):
        raise R.Forbidden(f"The evidence export needs the poster, reviewer or approver role in {p.code}.")
    rows = db.query(ApEvidence).filter(ApEvidence.portfolio == p.code, ApEvidence.period == month).order_by(ApEvidence.id).all()
    batches = {b.id: b for b in db.query(ApBatch).filter(ApBatch.id.in_({e.batch_id for e in rows if e.batch_id} or {0})).all()}
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Index"
    ws.append(["Path", "Type", "Stage", "Folder", "Payment run", "Account", "File as attached", "SHA-256", "Bytes", "By", "When", "Keep until", "Check", "Note"])
    buf = io.BytesIO()
    used: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for e in rows:
            b = batches.get(e.batch_id)
            target = e.account or (f"run{e.run_item_id}" if e.run_item_id else f"batch{e.batch_id}_{_safe(b.name, 40) if b else ''}".rstrip("_"))
            ext = Path(e.name).suffix.lower()[:8]
            path = f"{_safe(p.code, 20)}/{month}/{e.stage or 'other'}/{_safe(target, 50)}_{_safe(e.type, 40)}_{e.created_at:%Y-%m-%d}{ext}"
            n, base = 2, path
            while path in used:
                path = base[: -len(ext)] + f"_{n}" + ext if ext else f"{base}_{n}"
                n += 1
            used.add(path)
            f = db.get(StoredFile, e.file_id) if e.file_id else None
            data = None
            if f is not None and storage.abs_path(f).is_file():
                data = storage.abs_path(f).read_bytes()
            check = "missing" if data is None else ("ok" if hashlib.sha256(data).hexdigest() == e.sha256 else "CHANGED since it was filed")
            if data is not None:
                z.writestr(path, data)
            ws.append([path if data is not None else "", e.type, e.stage, b.name if b else "", e.run_item_id or "", e.account, e.name, e.sha256, e.size, e.by_name,
                       f"{e.created_at:%Y-%m-%d %H:%M}" if e.created_at else "", e.keep_until, check, e.note])
        P._head_style(ws, ws.max_column)
        ws.freeze_panes = "A2"
        P._fit(ws, 60)
        xlsafe.book(wb)
        ib = io.BytesIO()
        wb.save(ib)
        z.writestr(f"{_safe(p.code, 20)}/{month}/index.xlsx", ib.getvalue())
    log(db, "ap_evidence_export", actor=user, target=f"ap_portfolio:{p.code}", detail=f"{p.code} {month}: {len(rows)} pieces")
    return buf.getvalue(), f"AP_evidence_{_safe(p.code, 20)}_{month}.zip"
