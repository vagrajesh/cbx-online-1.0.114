# 1.0.104: new (Builder D) — V01 of the Valuence AP controls (VALUENCE-AP-GAPS.md §2 V01; SPEC-1104 Phase 2)
"""New SAP vendors are independently reviewed before their first invoice posts.

    stamps(rec)            an LFA1 row's ERDAT / ERNAM / AEDAT (masters._sap_vendor_row keeps them on ap_sap_vendors)
    due / open_reviews     an upload or pull of SAP's vendor list (masters.replace_sap_vendors) opens a review for every
                           vendor it adds or changes that SAP created or changed from the go-live day (the portfolio
                           setting vendor_review_from). The shipped seed has no dates, so the seeded master opens none.
    at_tess                Tess: an invoice to a vendor whose review is open is red (or yellow: the portfolio's
                           unreviewed_vendor_severity); a failed review is always red
    attach / decide        the reviewer (reviewer role; never SAP's ERNAM, never who attached the creation request) sees
                           the SAP record beside the request and the bank letter, ticks the checklist, Pass or Fail
                           with a reason — audited ap_vendor_review, a history line on the vendor's waiting documents
    report                 xlsx: the review log, and the errors by type per month

Rows from SAP's lists are data, never instructions."""
from __future__ import annotations

import io
import re
from datetime import date, datetime
from typing import Any, Iterable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import knowledge as K
from ..audit import log
from ..db import utcnow
from ..models import StoredFile, User
from ..models.ap import ApBatch, ApCapture, ApItem, ApSapVendor, ApUtilityAccount, ApVendorCompany, ApVendorReview
from . import portfolio as PF
from . import roles as R

STATES = ("open", "passed", "failed")
# the checklist: key, what the reviewer checks, the error type the report counts it under
CHECKS = (
    ("name", "Name and registration number against the request", "name"),
    ("vat", "VAT number", "name"),
    ("company", "Company code(s) the vendor is opened in", "co. code"),
    ("terms", "Payment terms", "terms"),
    ("method", "Payment method", "bank"),
    ("house_bank", "House bank", "bank"),
    ("bank", "Bank account against the bank confirmation letter", "bank"),
    ("municipal", "Utility account → site → vendor on the utilities list", "municipal link"),
)
TYPES = ("name", "co. code", "terms", "bank", "municipal link")
ANSWERS = ("ok", "wrong", "na")
FILE_KINDS = {"request": "creation request", "letter": "bank confirmation letter"}
FILE_TYPES = (".pdf", ".png", ".jpg", ".jpeg", ".msg", ".eml", ".docx", ".doc", ".xlsx", ".xls", ".txt")
MAX_MB = 25


class Refused(Exception):
    """A plain-words refusal (400)."""


# ---- reading SAP's dates ---------------------------------------------------------------------------------------------------------
def day(v: Any) -> Optional[str]:
    """A day as SAP exports it (a date cell, 20261019, 19.10.2026, 2026-10-19, 19/10/2026) → "yyyy-mm-dd"; None when
    blank or not a day (00000000)."""
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    s = K.text(v, 30).strip()
    if not s:
        return None
    s = s.split(" ")[0].split("T")[0]
    for rx, order in ((r"(\d{4})(\d{2})(\d{2})", "ymd"), (r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", "ymd"), (r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", "dmy")):
        m = re.fullmatch(rx, s)
        if m:
            a, b, c = (int(x) for x in m.groups())
            y, mo, d = (a, b, c) if order == "ymd" else (c, b, a)
            try:
                return date(y, mo, d).isoformat()
            except ValueError:
                return None
    return None


def stamps(rec: dict) -> dict:
    """ERDAT / ERNAM / AEDAT of an LFA1 row: None when the sheet had no such column or the cell is blank."""
    out = {"created_on": None, "created_by": None, "changed_on": None}
    if "created_on" in rec:
        out["created_on"] = day(rec.get("created_on"))
    if "created_by" in rec:
        out["created_by"] = re.sub(r"\s+", "", K.text(rec.get("created_by"), 40)).upper() or None
    if "changed_on" in rec:
        out["changed_on"] = day(rec.get("changed_on"))
    return out


def dmy(iso: str) -> str:
    try:
        return date.fromisoformat(iso).strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return iso or "?"


# ---- which vendors are due a review ------------------------------------------------------------------------------------------------
def _companies(db: Session, vendors: Iterable[str]) -> dict[str, list[str]]:
    """{vendor: [company codes]} from SAP's vendor ↔ company list (leading zeros as SAP may give them)."""
    back: dict[str, str] = {}
    for v in vendors:
        for x in K.vendor_variants(v):
            back.setdefault(x, v)
    keys = sorted(back)
    out: dict[str, list[str]] = {}
    for i in range(0, len(keys), 900):
        for v, co in db.query(ApVendorCompany.vendor, ApVendorCompany.company_code).filter(ApVendorCompany.vendor.in_(keys[i:i + 900])).all():
            lst = out.setdefault(back[v], [])
            if co not in lst:
                lst.append(co)
    return {k: sorted(v) for k, v in out.items()}


class _Portfolios:
    """The vendor's portfolio (from its company codes; the default when none claims them) and its go-live day."""

    def __init__(self, db: Session):
        self.db, self.by_code = db, {}
        d = PF.default(db)
        self.default = d.code if d is not None else ""

    def of(self, codes: list[str]) -> str:
        found = []
        for co in codes:
            if co not in self.by_code:
                p = PF.for_company(self.db, co, fallback=False)
                self.by_code[co] = p.code if p is not None else ""
            if self.by_code[co] and self.by_code[co] not in found:
                found.append(self.by_code[co])
        return (sorted(found, key=lambda c: (c == self.default, c)) or [self.default])[0]

    def go_live(self, code: str) -> str:
        return str(PF.setting(self.db, code or None, "vendor_review_from", "2026-10-19") or "")


def due(db: Session, new: dict, added: Iterable[str], changed: Iterable[str], diffs: dict) -> list[dict]:
    """The reviews an upload or pull of SAP's vendor list opens: each vendor it adds or changes that SAP created (ERDAT)
    or changed (AEDAT) on or after its portfolio's vendor_review_from, unless that review exists already. Plain dicts
    (the list is saved after this is worked out)."""
    cands = [v for v in list(added) + list(changed) if new.get(v) and (new[v].get("created_on") or new[v].get("changed_on"))]
    if not cands:
        return []
    pfs = _Portfolios(db)
    earliest = min((pfs.go_live(p.code) for p in PF.portfolios(db)), default=pfs.go_live(""))
    cands = [v for v in cands if max(new[v].get("created_on") or "", new[v].get("changed_on") or "") >= earliest]
    if not cands:
        return []
    cos = _companies(db, cands)
    have: set[tuple[str, str, str]] = set()
    new_seen: set[str] = set()
    for i in range(0, len(cands), 900):
        for v, k, s in db.query(ApVendorReview.vendor, ApVendorReview.kind, ApVendorReview.stamp).filter(ApVendorReview.vendor.in_(cands[i:i + 900])).all():
            have.add((v, k, s))
            if k == "new":
                new_seen.add(v)
    out = []
    for v in cands:
        r = new[v]
        codes = cos.get(v, [])
        pf = pfs.of(codes)
        start = pfs.go_live(pf)
        created, changed_on = r.get("created_on") or "", r.get("changed_on") or ""
        if created and created >= start:
            kind, stamp = "new", created
            if v in new_seen:
                continue
        elif changed_on and changed_on >= start and changed_on > created:
            kind, stamp = "changed", changed_on
        else:
            continue
        if (v, kind, stamp) in have:
            continue
        what = [{"field": f, "from": a, "to": b} for f, a, b in diffs.get(v, []) if f not in ("name_key", "created_on", "created_by", "changed_on")]
        out.append({"vendor": v, "name": r.get("name") or "", "kind": kind, "stamp": stamp, "created_on": created, "created_by": r.get("created_by") or "",
                    "changed_on": changed_on, "portfolio": pf, "company_codes": ", ".join(codes),
                    "snapshot": {k: r.get(k) for k in ("name", "vat", "tax1", "city", "country", "blocked", "payment_terms", "account_group", "created_on", "created_by", "changed_on")},
                    "changes": what})
    return out


def due_out(due_rows: list[dict], new: dict) -> dict:
    """What the upload's preview says about reviews."""
    dated = any(r.get("created_on") for r in new.values())
    out = {"due": len(due_rows), "new": sum(1 for r in due_rows if r["kind"] == "new"), "changed": sum(1 for r in due_rows if r["kind"] == "changed"),
           "dated": dated, "sample": [f"{r['vendor']} {r['name']}".strip() for r in due_rows[:5]], "opened": 0}
    if not dated:
        out["note"] = "The list has no created-on date (ERDAT): no vendor reviews open from it. Export LFA1 with ERDAT, ERNAM and AEDAT."
    return out


def open_reviews(db: Session, user: Optional[User], rows: list[dict], *, source: str = "excel") -> int:
    """Open the reviews `due` worked out (after the list is saved). Audited ap_vendor_review (one line for the upload)."""
    if not rows:
        return 0
    muni = set()
    vs = sorted({x for r in rows for x in K.vendor_variants(r["vendor"])})
    for i in range(0, len(vs), 900):
        muni |= {v for (v,) in db.query(ApUtilityAccount.vendor).filter(ApUtilityAccount.vendor.in_(vs[i:i + 900])).distinct().all()}
    for r in rows:
        db.add(ApVendorReview(vendor=r["vendor"], name=r["name"][:160], kind=r["kind"], stamp=r["stamp"], created_on=r["created_on"], created_by=r["created_by"][:40],
                              changed_on=r["changed_on"], portfolio=r["portfolio"], company_codes=r["company_codes"],
                              municipal=bool(K.vendor_variants(r["vendor"]) & muni), snapshot=r["snapshot"], changes=r["changes"], state="open",
                              source=(source or "excel")[:8], opened_by_id=user.id if user is not None else None))
    db.flush()
    log(db, "ap_vendor_review", actor=user, target="ap:sap_vendors",
        detail=f"opened {len(rows)} vendor review(s) from {source}: " + ", ".join(f"{r['vendor']} ({r['kind']})" for r in rows[:20]) + (" …" if len(rows) > 20 else ""))
    return len(rows)


# ---- Tess ----------------------------------------------------------------------------------------------------------------------
def standing(db: Session, vendor: Any) -> Optional[ApVendorReview]:
    """The vendor's review that stops its invoices: a failed one first, else the oldest open one. None: nothing stops it."""
    vs = sorted(K.vendor_variants(vendor))
    if not vs:
        return None
    rows = db.query(ApVendorReview).filter(ApVendorReview.vendor.in_(vs), ApVendorReview.state.in_(("open", "failed"))).all()
    if not rows:
        return None
    return sorted(rows, key=lambda r: (r.state != "failed", r.opened_at or utcnow(), r.id))[0]


def tess_text(r: ApVendorReview) -> str:
    if r.state == "failed":
        return f"vendor {r.vendor} failed its independent review on {dmy(r.reviewed_at.date().isoformat()) if r.reviewed_at else '?'}: {(r.reason or '')[:160]}"
    if r.kind == "changed":
        return f"vendor {r.vendor} not yet independently reviewed (changed {dmy(r.changed_on)})"
    return f"vendor {r.vendor} not yet independently reviewed (created {dmy(r.created_on)})"


def at_tess(db: Session, c: ApCapture, warn: list, stop: list) -> None:
    """Tess's hook (capture.test_batch): an invoice to a vendor whose review is open → red (or yellow when the
    portfolio's unreviewed_vendor_severity says so); failed → red."""
    if not c.vendor_no:
        return
    r = standing(db, c.vendor_no)
    if r is None:
        return
    text = tess_text(r)
    if r.state == "failed":
        stop.append(text)
        return
    sev = str(PF.setting(db, c.portfolio or R.portfolio_of(db, c) or None, "unreviewed_vendor_severity", "red") or "red").lower()
    (warn if sev == "yellow" else stop).append(text)


def first_invoice(db: Session, vendor: Any) -> bool:
    """For V05's selection ("every new-vendor first invoice"): the vendor has a new-vendor review (any state) and nothing
    of it was posted in SAP's history or went out in a CBX upload yet."""
    from ..models.ap import ApPosted

    vs = sorted(K.vendor_variants(vendor))
    if not vs or not db.query(ApVendorReview.id).filter(ApVendorReview.vendor.in_(vs), ApVendorReview.kind == "new").first():
        return False
    if db.query(ApPosted.id).filter(ApPosted.vendor.in_(vs)).first():
        return False
    return db.query(ApCapture.id).filter(ApCapture.vendor_no.in_(vs), ApCapture.uploaded_at.isnot(None)).first() is None


# ---- who may review -------------------------------------------------------------------------------------------------------------
def _sap_user_is(user: User, ernam: str) -> bool:
    """Is this person SAP's ERNAM? CBX knows no SAP user names: the e-mail's name part or the person's name, squeezed."""
    e = re.sub(r"[^A-Z0-9]", "", (ernam or "").upper())
    if not e:
        return False
    local = re.sub(r"[^A-Z0-9]", "", (user.email or "").split("@")[0].upper())
    name = re.sub(r"[^A-Z0-9]", "", (user.name or "").upper())
    parts = [re.sub(r"[^A-Z0-9]", "", p.upper()) for p in re.split(r"[\s.]+", user.name or "") if p]
    initial_surname = (parts[0][:1] + parts[-1]) if len(parts) >= 2 else ""
    return e in {x for x in (local, name, initial_surname) if x}


def blockers(db: Session, user: Optional[User], r: ApVendorReview) -> list[str]:
    """Why this person can't review it (empty: they can)."""
    out = []
    if user is None:
        return ["Sign in."]
    if not R.may(db, user, "reviewer", r.portfolio or None):
        out.append(f"Reviewing a new vendor needs the reviewer role{' in ' + r.portfolio if r.portfolio else ''}.")
    if _sap_user_is(user, r.created_by):
        out.append(f"SAP says you created this vendor ({r.created_by}): a different person reviews it.")
    if r.request_by_id and r.request_by_id == user.id:
        out.append("You attached the creation request: a different person reviews it.")
    return out


def can_see(db: Session, user: Optional[User]) -> bool:
    return bool(user is not None and (user.is_admin or R.may(db, user, "reviewer", None)))


# ---- the reviewer's screen ----------------------------------------------------------------------------------------------------------
def checks_for(r: ApVendorReview) -> list[dict]:
    return [{"key": k, "label": label, "type": typ} for k, label, typ in CHECKS if k != "municipal" or r.municipal]


def _name(db: Session, uid: Optional[int]) -> str:
    u = db.get(User, uid) if uid else None
    return (u.name or u.email) if u is not None else ""


def _file(db: Session, fid: Optional[int]) -> Optional[dict]:
    f = db.get(StoredFile, fid) if fid else None
    return {"id": f.id, "name": f.name, "size": f.size} if f is not None else None


def out(db: Session, r: ApVendorReview, user: Optional[User] = None, full: bool = False) -> dict:
    age = (utcnow().date() - (r.opened_at.date() if r.opened_at else utcnow().date())).days
    o = {"id": r.id, "vendor": r.vendor, "name": r.name, "kind": r.kind, "stamp": r.stamp, "created_on": r.created_on, "created_by": r.created_by,
         "changed_on": r.changed_on, "portfolio": r.portfolio, "company_codes": r.company_codes, "municipal": bool(r.municipal), "state": r.state,
         "opened_at": r.opened_at.isoformat() if r.opened_at else "", "age_days": age, "reviewed_by": r.reviewed_by,
         "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else "", "reason": r.reason, "errors": list(r.errors or []),
         "has_request": bool(r.request_file_id), "has_letter": bool(r.letter_file_id)}
    if full:
        sv = db.query(ApSapVendor).filter(ApSapVendor.vendor.in_(sorted(K.vendor_variants(r.vendor)) or ["-"])).first()
        accts = db.query(ApUtilityAccount).filter(ApUtilityAccount.vendor.in_(sorted(K.vendor_variants(r.vendor)) or ["-"])).order_by(ApUtilityAccount.account).limit(20).all() if r.municipal else []
        o.update(snapshot=r.snapshot or {}, changes=list(r.changes or []), checklist=dict(r.checklist or {}), checks=checks_for(r),
                 sap_now=({"name": sv.name, "vat": sv.vat, "tax1": sv.tax1, "city": sv.city, "country": sv.country, "blocked": bool(sv.blocked),
                           "payment_terms": sv.payment_terms, "account_group": sv.account_group} if sv is not None else None),
                 accounts=[{"account": a.account, "site": a.site, "cost_centre": a.cost_centre, "municipality": a.municipality, "company_code": a.company_code} for a in accts],
                 request=_file(db, r.request_file_id), letter=_file(db, r.letter_file_id),
                 request_by=_name(db, r.request_by_id), letter_by=_name(db, r.letter_by_id),
                 blockers=blockers(db, user, r) if user is not None and r.state != "passed" else [])
    return o


def seen_portfolios(db: Session, user: Optional[User]) -> Optional[set[str]]:
    """1.0.104 review: the portfolios whose reviews this person sees — None = every one (admins); else those where they
    are a reviewer or approver ("" = a review with no portfolio: any such role)."""
    if user is None:
        return set()
    if user.is_admin:
        return None
    codes = {r.portfolio or "" for r in db.query(ApVendorReview.portfolio).distinct().all()}
    return {c for c in codes if any(R.may(db, user, role, c or None) for role in ("reviewer", "approver"))}


def may_see(db: Session, user: Optional[User], r: ApVendorReview) -> bool:
    seen = seen_portfolios(db, user)
    return seen is None or (r.portfolio or "") in seen


def listing(db: Session, user: User, state: str = "open", q: str = "", portfolio: str = "", limit: int = 200) -> dict:
    qy = db.query(ApVendorReview)
    seen = seen_portfolios(db, user)  # 1.0.104 review: only the portfolios where the person reviews or approves
    if seen is not None:
        qy = qy.filter(ApVendorReview.portfolio.in_(sorted(seen) or ["-"]))
    if state in STATES:
        qy = qy.filter(ApVendorReview.state == state)
    if portfolio:
        qy = qy.filter(ApVendorReview.portfolio == portfolio.strip().upper())
    q = (q or "").strip()[:60]
    if q:
        qy = qy.filter((ApVendorReview.vendor.like(f"%{q}%")) | (ApVendorReview.name.ilike(f"%{q}%")))
    rows = qy.order_by(ApVendorReview.opened_at, ApVendorReview.id).limit(max(1, min(int(limit or 200), 1000))).all()
    cq = db.query(ApVendorReview.state, func.count(ApVendorReview.id))
    if seen is not None:
        cq = cq.filter(ApVendorReview.portfolio.in_(sorted(seen) or ["-"]))
    counts = dict(cq.group_by(ApVendorReview.state).all())
    return {"rows": [out(db, r) for r in rows], "counts": {s: counts.get(s, 0) for s in STATES}, "checks": [{"key": k, "label": label, "type": t} for k, label, t in CHECKS]}


def get(db: Session, rid: int) -> ApVendorReview:
    r = db.get(ApVendorReview, int(rid or 0))
    if r is None:
        raise LookupError("No such vendor review.")
    return r


def attach(db: Session, user: User, r: ApVendorReview, kind: str, filename: str, data: bytes) -> dict:
    """The creation request or the bank confirmation letter, kept in Tools/AP vendor reviews (not mirrored)."""
    from ..warehouse import folders as F
    from ..warehouse import storage

    if kind not in FILE_KINDS:
        raise Refused("Attach the creation request or the bank confirmation letter.")
    if r.state == "passed":
        raise Refused("This review has passed: its files stay as they were.")
    if not R.may(db, user, "capturer", r.portfolio or None) and not R.may(db, user, "reviewer", r.portfolio or None) and not user.is_admin:
        raise R.Forbidden("Attaching to a vendor review needs an AP role in its portfolio.")
    name = (filename or "").strip() or f"{kind}.pdf"
    if not name.lower().endswith(FILE_TYPES):
        raise Refused("Attach a PDF, a picture, a mail (.msg / .eml) or an Office file.")
    if not data:
        raise Refused("The file is empty.")
    if len(data) > MAX_MB * 1024 * 1024:
        raise Refused(f"The file is over {MAX_MB} MB.")
    f = storage.store_in(db, F.tool_folder(db, "AP vendor reviews"), user, f"{r.vendor}_{kind}_{name}", data, source="ap-vendor-review", mirror=False)
    db.flush()
    if kind == "request":
        r.request_file_id, r.request_by_id = f.id, user.id
    else:
        r.letter_file_id, r.letter_by_id = f.id, user.id
    log(db, "ap_vendor_review", actor=user, target=f"ap_vendor:{r.vendor}", detail=f"review {r.id}: attached the {FILE_KINDS[kind]} ({f.name})"[:400])
    return {"id": f.id, "name": f.name}


def file_of(db: Session, r: ApVendorReview, fid: int) -> StoredFile:
    if fid not in (r.request_file_id, r.letter_file_id):
        raise LookupError("That file isn't on this review.")
    f = db.get(StoredFile, fid)
    if f is None:
        raise LookupError("That file isn't here.")
    return f


def decide(db: Session, user: User, r: ApVendorReview, outcome: str, checklist: Optional[dict], reason: str = "") -> ApVendorReview:
    """Pass (every item ok or not applicable, the request attached, the letter too unless the bank item is not
    applicable) or Fail with a reason (the items found wrong are its error types). Reviewer role, never ERNAM or who
    attached the request. Audited ap_vendor_review; a history line on the vendor's waiting documents, which Tess tests again."""
    if outcome not in ("pass", "fail"):
        raise Refused("Pass or Fail.")
    if r.state == "passed":
        raise Refused("This review has passed already.")
    why = blockers(db, user, r)
    if why:
        raise R.Forbidden(why[0])
    reason = (reason or "").strip()[:2000]
    keys = [c["key"] for c in checks_for(r)]
    got = {k: str((checklist or {}).get(k) or "").strip().lower() for k in keys}
    missing = [k for k in keys if got[k] not in ANSWERS]
    if outcome == "pass":
        if missing:
            raise Refused("Tick every item of the checklist (ok, wrong or not applicable) before it passes.")
        if got["name"] == "na":
            raise Refused("The name is always checked.")
        if any(v == "wrong" for v in got.values()):
            raise Refused("An item is wrong: the review fails (say why).")
        if not r.request_file_id:
            raise Refused("Attach the creation request first: the review compares SAP's record with it.")
        if got.get("bank") != "na" and not r.letter_file_id:
            raise Refused("Attach the bank confirmation letter first (or mark the bank account not applicable).")
    else:
        if not reason:
            raise Refused("Say why it fails.")
    wrong = [k for k in keys if got.get(k) == "wrong"]
    r.checklist = {k: v for k, v in got.items() if v in ANSWERS}
    r.errors = wrong
    r.reason = reason
    r.state = "passed" if outcome == "pass" else "failed"
    r.reviewed_by_id, r.reviewed_by, r.reviewed_at = user.id, (user.name or user.email or "")[:160], utcnow()
    db.flush()
    words = f"Vendor {r.vendor} {'passed' if outcome == 'pass' else 'failed'} its independent review ({_who(user)})" + (f": {reason}" if reason else "")
    log(db, "ap_vendor_review", actor=user, target=f"ap_vendor:{r.vendor}",
        detail=(f"review {r.id} ({r.kind}): {r.state}" + (f"; wrong: {', '.join(wrong)}" if wrong else "") + (f"; {reason}" if reason else ""))[:400])
    _tell_documents(db, r.vendor, words)
    return r


def _who(u: Optional[User]) -> str:
    return (u.name or u.email) if u is not None else "?"


def _tell_documents(db: Session, vendor: str, words: str) -> int:
    """A history line on each of the vendor's documents not uploaded yet, and their batches tested again."""
    from . import capture as C

    vs = sorted(K.vendor_variants(vendor))
    caps = db.query(ApCapture).filter(ApCapture.vendor_no.in_(vs or ["-"]), ApCapture.uploaded_at.is_(None)).limit(500).all()
    batches = set()
    for c in caps:
        it = db.get(ApItem, c.item_id)
        if it is not None:
            C._hist(it, "AP controls", words[:400])
        if c.stage == "ready":
            batches.add(c.batch_id)
    for bid in sorted(batches):
        b = db.get(ApBatch, bid)
        if b is not None:
            C.test_batch(db, b)
    return len(caps)


# ---- Needs you and the report --------------------------------------------------------------------------------------------------------
def needs_line(db: Session, user: Optional[User]) -> Optional[dict]:
    """Needs you → "New vendors to review" (reviewers): one line, linking into Controls → New vendors."""
    if user is None or not R.may(db, user, "reviewer", None):
        return None
    rows = db.query(ApVendorReview.portfolio, ApVendorReview.created_by, ApVendorReview.request_by_id).filter(ApVendorReview.state == "open").all()
    mine = {}
    n = 0
    for pf, ernam, req in rows:
        if pf not in mine:
            mine[pf] = R.may(db, user, "reviewer", pf or None)
        if mine[pf] and req != user.id and not _sap_user_is(user, ernam):
            n += 1
    if not n:
        return None
    return {"key": "new_vendors", "sub": "new_vendors", "title": "New vendors to review", "count": n,
            "text": "1 new SAP vendor waits for an independent review" if n == 1 else f"{n} new SAP vendors wait for an independent review"}


def report(db: Session, user: Optional[User] = None) -> bytes:
    """xlsx: the review log, and the errors by type per month (when reviewed) — of the portfolios the person sees."""
    from openpyxl import Workbook

    from .. import xlsafe

    rows = db.query(ApVendorReview).order_by(ApVendorReview.opened_at, ApVendorReview.id).all()
    seen = seen_portfolios(db, user) if user is not None else None  # 1.0.104 review
    if seen is not None:
        rows = [r for r in rows if (r.portfolio or "") in seen]
    wb = Workbook()
    ws = wb.active
    ws.title = "Review log"
    ws.append(["Vendor", "Name", "New or changed", "Created on", "Created by (SAP)", "Changed on", "Portfolio", "Company codes", "Municipal",
               "Opened", "State", "Reviewed by", "Reviewed on", "Errors", "Reason", "Creation request", "Bank letter"])
    label = {k: lab for k, lab, _ in CHECKS}
    for r in rows:
        ws.append([r.vendor, r.name, r.kind, r.created_on, r.created_by, r.changed_on, r.portfolio, r.company_codes, "yes" if r.municipal else "",
                   r.opened_at.strftime("%Y-%m-%d") if r.opened_at else "", r.state, r.reviewed_by, r.reviewed_at.strftime("%Y-%m-%d") if r.reviewed_at else "",
                   "; ".join(label.get(e, e) for e in r.errors or []), r.reason, "yes" if r.request_file_id else "", "yes" if r.letter_file_id else ""])
    ws2 = wb.create_sheet("Errors by month")
    ws2.append(["Month", "Reviewed", "Passed", "Failed", *[f"Errors: {t}" for t in TYPES]])
    months: dict[str, dict] = {}
    typ = {k: t for k, _, t in CHECKS}
    for r in rows:
        if not r.reviewed_at or r.state not in ("passed", "failed"):
            continue
        m = months.setdefault(r.reviewed_at.strftime("%Y-%m"), {"n": 0, "passed": 0, "failed": 0, **{t: 0 for t in TYPES}})
        m["n"] += 1
        m[r.state] += 1
        for t in {typ.get(e, "") for e in r.errors or []}:
            if t in m:
                m[t] += 1
    for k in sorted(months):
        m = months[k]
        ws2.append([k, m["n"], m["passed"], m["failed"], *[m[t] for t in TYPES]])
    xlsafe.book(wb)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
