# 1.0.104: new — Builder R, V05 (VALUENCE-AP-GAPS.md §2 V05; SPEC-1104 Phase 2)
"""An independent review of captured invoices before the SAP upload.

Selection (at Tess time, per portfolio; the reasons kept as `review_why`):
    every document of `review_threshold_r` (R50,000) or more;
    every document a person typed, corrected or coded by hand;
    every yellow;
    every document whose tax code is not the portfolio's default (I9 / I0), and every invoice that is not a valid tax
    invoice (V06);
    every first invoice to a vendor (none posted in SAP's history, none uploaded before);
    plus a random `review_sample_pct` (10 %) of the rest — the batch's seed is kept (`ApBatch.review_seed`), so the same
    documents come out of the sample when it is worked out again.
A red document is not picked (it never goes out); a rejected one is red until it changes.

A reviewer — the reviewer role in the document's portfolio, never someone who touched it or dropped its folder (`roles.touched_by`) — sees the
page beside supplier, vendor, invoice number, date, amount, company, G/L, cost centre, tax code and the duplicate search,
and picks Agree · Correct (the corrected fields go back to Pip and Tess; the fields found wrong are kept for the coverage
report) · Reject (red, with what is wrong). Audited ap_capture_reviewed, a history line on the file. A document whose SAP
rows change after its review is picked again ("changed after the review").

The SAP upload needs the poster role in the documents' portfolios, and is refused while a picked document is not
reviewed: "3 documents wait for review" (portfolio setting `review_before_upload`). The review workbook gets the columns
Selected why · Reviewer · Reviewed · Outcome; the monthly coverage report counts selected, reviewed and the errors found
by field."""
from __future__ import annotations

import hashlib
import io
import json
import re
import secrets
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from ..audit import log
from ..db import utcnow
from ..models import AuditLog, User
from ..models.ap import ApBatch, ApCapture, ApItem, ApPosted, ApVendorCode
from . import controls_needs as CN
from . import portfolio as PF
from . import roles as R
from . import sap as P
from . import vatcheck as VC

STATES = ("", "selected", "agreed", "corrected", "rejected")
OUTCOME = {"selected": "waiting", "agreed": "agreed", "corrected": "corrected", "rejected": "rejected"}
OWN_ACTIONS = VC.OWN_ACTIONS
# the fields a reviewer can find wrong (the coverage report counts them)
ERROR_FIELDS = {"supplier": "Supplier", "vendor": "Vendor", "invoice_no": "Invoice no", "date": "Date", "amount": "Amount", "company": "Company",
                "gl": "G/L", "cost_centre": "Cost centre", "tax_code": "Tax code", "vat": "VAT", "duplicate": "Duplicate", "other": "Other"}
# what Correct may change, per kind of document → the error field it counts as
EDITABLE = {"manual": {"supplier": "supplier", "supplier_vat": "supplier", "bill_to": "company", "bill_to_vat": "company", "invoice_no": "invoice_no",
                       "invoice_date": "date", "due_date": "date", "excl": "amount", "vat": "amount", "incl": "amount", "gl": "gl", "cost_centre": "cost_centre"},
            "utility": {"account": "vendor", "invoice_no": "invoice_no", "billing_date": "date", "due_date": "date"}}
HAND = {"ap_capture_save": "typed or corrected by a person", "ap_fix": "corrected by a person", "ap_capture_vendor": "vendor coded by hand"}
DUP_WORDS = re.compile(r"duplicate|same invoice|already went out|posted in SAP|posted already|was posted", re.I)


class Refused(PF.Refused):
    """A plain-words refusal (400)."""


def _name(u: Optional[User]) -> str:
    return (u.name or u.email) if u else ""


def _amount(c: ApCapture) -> Optional[Decimal]:
    rows = c.sap_rows or []
    return P.dec(rows[0].get("amount")) if rows else None


def fingerprint(c: ApCapture) -> str:
    """The SAP rows as they post, without the posting date (the batch's day moves) and Tess's own marks."""
    keep = ("type", "comp", "curr", "refdocnr", "docdate", "pkey", "account", "amount", "tc", "dt", "costcentre", "ProfitCentre", "text", "DueDate")
    rows = [[str(r.get(k) or "") for k in keep] for r in c.sap_rows or []]
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()


def seed_of(b: Optional[ApBatch]) -> str:
    if b is None:
        return ""
    if not b.review_seed:
        b.review_seed = secrets.token_hex(8)
    return b.review_seed


def sampled(seed: str, cid: int, pct) -> bool:
    """The random sample: the same seed and document always give the same answer."""
    try:
        p = float(pct or 0)
    except (TypeError, ValueError):
        return False
    if p <= 0 or not seed:
        return False
    h = int(hashlib.sha256(f"{seed}:{cid}".encode("utf-8")).hexdigest()[:8], 16) % 10000
    return h < p * 100


def _vendor_variants(v: str) -> set[str]:
    v = str(v or "").strip()
    return {x for x in (v, v.lstrip("0"), v.zfill(10)) if x}


def first_invoice(db: Session, c: ApCapture) -> str:
    """The vendor (31/21 line) when this is its first invoice: nothing posted in SAP's history, nothing uploaded before,
    and no earlier document of the same vendor waiting — "" otherwise."""
    rows = c.sap_rows or []
    v = str(rows[0].get("account") or "").strip() if rows else ""
    if not v:
        return ""
    vs = _vendor_variants(v)
    if db.query(ApPosted.id).filter(ApPosted.vendor.in_(vs)).first():
        return ""
    if db.query(ApCapture.id).filter(ApCapture.vendor_no.in_(vs), ApCapture.id != c.id,
                                     (ApCapture.uploaded_at.isnot(None)) | and_(ApCapture.id < c.id, ApCapture.stage.in_(("test", "ready")))).first():
        return ""
    return v


def reasons(db: Session, c: ApCapture, it: Optional[ApItem], status: str, seed: str) -> list[str]:
    """Why the document is reviewed (empty: it isn't)."""
    pf = c.portfolio or None
    out: list[str] = []
    amt = _amount(c)
    thr = PF.setting(db, pf, "review_threshold_r", 50000)
    try:
        if amt is not None and thr is not None and abs(amt) >= Decimal(str(thr)):
            out.append(f"R {abs(amt):,.2f} is R {float(thr):,.0f} or more".replace(",", " "))
    except (ArithmeticError, ValueError):
        pass
    if c.read_by == "person":
        out.append("typed by a person")
    if it is not None:
        acts = {a for (a,) in db.query(AuditLog.action).filter(AuditLog.target == f"ap_item:{it.id}", AuditLog.action.in_(tuple(HAND)), AuditLog.actor_id.isnot(None)).distinct().all()}
        for a in HAND:
            if a in acts and HAND[a] not in out and not (a == "ap_capture_save" and c.read_by == "person"):
                out.append(HAND[a])
    f = c.fields or {}
    if c.bucket == "manual" and (str(f.get("gl") or "").strip() or str(f.get("cost_centre") or "").strip()):
        out.append("G/L or cost centre set by hand for this invoice")
    v = (c.match or {}).get("vendor") or {}
    if c.bucket == "manual" and v.get("found") and v.get("id"):
        row = db.get(ApVendorCode, v["id"])
        if row is not None and row.learned and "vendor coded by hand" not in out:
            out.append("vendor coded by hand")
    if status == "yellow" and PF.setting(db, pf, "review_yellow", True):  # 1.0.105: a portfolio may switch this off
        out.append("yellow")
    fixed = PF.fixed_for(db, pf)
    usual = {str(fixed.get("TaxCode") or "I9").upper(), str(fixed.get("TaxCodeCredit") or "I0").upper()}
    odd = sorted({str(r.get("tc") or "").upper() for r in c.sap_rows or [] if r.get("pkey") in ("40", "50") and r.get("tc")} - usual)
    if odd:
        out.append("tax code " + ", ".join(odd))
    if c.vat_valid is False:
        out.append("not a valid tax invoice")
    fv = first_invoice(db, c)
    if fv:
        out.append(f"first invoice to vendor {fv}")
    if not out and sampled(seed, c.id, PF.setting(db, pf, "review_sample_pct", 10)):
        out.append(f"random sample ({PF.setting(db, pf, 'review_sample_pct', 10)} %)")
    return out


# ---- Tess's hook ---------------------------------------------------------------------------------------------------------------
def after_tess(db: Session, b: ApBatch, c: ApCapture, it: Optional[ApItem], status: str, issues: list[dict]) -> tuple[str, list[dict]]:
    """capture.test_batch, once Tess has her verdict on a document not uploaded yet: a rejected document is red; a reviewed
    one whose rows changed is picked again; the rest are picked (or not) by the rules above."""
    from . import capture as C

    if c.uploaded_at or c.bucket not in ("manual", "utility"):
        return status, issues
    st = c.review_state or ""
    fp = fingerprint(c)
    changed = False
    if st in ("agreed", "corrected", "rejected"):
        if not c.review_hash:
            c.review_hash = fp  # corrected: the rows as Pip built them from the reviewer's correction
        elif c.review_hash != fp:
            changed = True
            C._hist(it, "Tess", f"Changed after the review ({OUTCOME.get(st, st)}): picked for review again.")
            c.review_state, c.review_hash, st = "selected", "", "selected"
            c.review_selected_at = utcnow()
    if st == "rejected":
        u = db.get(User, c.reviewed_by_id) if c.reviewed_by_id else None
        text = f"rejected in the review by {_name(u) or 'a reviewer'}" + (f": {c.review_note}" if c.review_note else "")
        return "red", [x for x in issues if x.get("text") != text] + [{"level": "red", "text": text[:300]}]
    # picked again after a review (a reviewer acted on it before, then its rows changed): sticky until a reviewer acts again —
    # a later pass never drops the reason nor un-picks it, whatever else changed (download() runs Tess again itself)
    again = changed or (st == "selected" and c.reviewed_at is not None)
    if st in ("", "selected") and (status != "red" or again):
        why = reasons(db, c, it, status, seed_of(b))
        if again:
            why.insert(0, "changed after the review")
        if why:
            if st == "":
                c.review_state, c.review_selected_at = "selected", utcnow()
            c.review_why = "; ".join(why)[:400]
        elif st == "selected":
            c.review_state, c.review_why = "", ""
    return status, issues


# ---- the SAP upload's gate (capture.download) -------------------------------------------------------------------------------------
def gate(db: Session, user: User, b: ApBatch, want: tuple) -> set[int]:
    """Before the SAP upload (Tess has just tested the batch): the poster role in every portfolio the documents going out
    fall in. 1.0.105: the documents still waiting for their review (when the portfolio asks for it) no longer refuse the
    whole download — their ids come back and capture.download leaves them out (never sent); it refuses, in the same words
    as before, only when nothing else is left to go out."""
    caps = db.query(ApCapture).filter(ApCapture.batch_id == b.id, ApCapture.stage == "ready", ApCapture.sap_status.in_(want), ApCapture.uploaded_at.is_(None)).all()
    for p in sorted({c.portfolio or R.portfolio_of(db, c) for c in caps}):  # nothing ready: download says so itself
        R.require(db, user, "poster", p or None, what="The SAP upload" + (" with yellow documents" if "yellow" in want else ""))
    return {c.id for c in caps if waits(db, c)}


def waits(db: Session, c: ApCapture) -> bool:
    """1.0.105: picked for review and not reviewed yet, in a portfolio whose upload waits for the review."""
    return (c.review_state or "") == "selected" and bool(PF.setting(db, c.portfolio or None, "review_before_upload", True))


def waiting_words(n: int) -> str:
    """1.0.105: the 1.0.104 refusal's words, now said only when every document that could go out waits for its review."""
    return (f"{n} document{'s' if n != 1 else ''} wait{'' if n != 1 else 's'} for review (Controls → Review): a reviewer agrees, corrects or rejects "
            f"{'them' if n != 1 else 'it'} first.")


# ---- what the pages and the workbook show -------------------------------------------------------------------------------------------
def sheet_fields(c: ApCapture) -> dict:
    """The SAP sheet's row: its review state and why (the Review filter)."""
    return {"review": c.review_state or "", "review_why": c.review_why or "", "vat_valid": c.vat_valid, "vat_decision": c.vat_decision or ""}


def capture_fields(db: Session, c: ApCapture) -> dict:
    u = db.get(User, c.reviewed_by_id) if c.reviewed_by_id else None
    return {"review": {"state": c.review_state or "", "why": c.review_why or "", "by": _name(u), "at": c.reviewed_at.isoformat(timespec="minutes") if c.reviewed_at else "",
                       "note": c.review_note or "", "errors": list(c.review_errors or [])}, "vat": VC.out(db, c)}


def workbook_columns(db: Session, c: ApCapture, users: Optional[dict] = None) -> dict:
    """V05.5: the review workbook's columns for one document."""
    u = (users or {}).get(c.reviewed_by_id) if users is not None else (db.get(User, c.reviewed_by_id) if c.reviewed_by_id else None)
    st = c.review_state or ""
    return {"Selected why": c.review_why or "", "Reviewer": _name(u) if st in ("agreed", "corrected", "rejected") else "",
            "Reviewed": f"{c.reviewed_at:%Y-%m-%d %H:%M}" if c.reviewed_at and st in ("agreed", "corrected", "rejected") else "",
            "Outcome": OUTCOME.get(st, "") + (" (" + ", ".join(ERROR_FIELDS.get(x, x) for x in c.review_errors or []) + ")" if st in ("corrected", "rejected") and c.review_errors else "")}


def _dup_text(c: ApCapture) -> str:
    hits = [x.get("text") or "" for x in c.sap_issues or [] if DUP_WORDS.search(str(x.get("text") or ""))]
    return "; ".join(hits) if hits else "No twin found: not in this batch, not in an earlier upload, not in SAP's posting history."


def _can(db: Session, user: Optional[User], c: ApCapture) -> tuple[bool, str]:
    return _can_many(db, user, [c]).get(c.id, (False, ""))


def _can_many(db: Session, user: Optional[User], caps: list) -> dict[int, tuple[bool, str]]:
    """May this person review each document? The same answer as roles.require + touched_by(include_dropper=True,
    exclude_actions=OWN_ACTIONS), worked out for many documents in a few reads (Needs you counts up to 1 000 of them):
    the role once per portfolio; the person's own audit rows on those files, the folders they dropped and the vendors they
    coded, in one query each."""
    if user is None or not caps:
        return {c.id: (False, "") for c in caps}
    pfs = {c.id: (c.portfolio or R.portfolio_of(db, c)) for c in caps}
    role = {pf: R.may(db, user, "reviewer", pf) for pf in set(pfs.values())}
    skip = set(R.NOT_A_TOUCH) | set(OWN_ACTIONS)
    targets = [f"ap_item:{c.item_id}" for c in caps]
    mine_items: set[str] = set()
    for i in range(0, len(targets), 500):
        mine_items |= {t for t, a in db.query(AuditLog.target, AuditLog.action).filter(AuditLog.actor_id == user.id, AuditLog.target.in_(targets[i:i + 500]),
                                                                                      AuditLog.action.like("ap\\_%", escape="\\")).all() if a not in skip}
    dropped = {bid for (bid,) in db.query(ApBatch.id).filter(ApBatch.id.in_({c.batch_id for c in caps}), ApBatch.user_id == user.id).all()}
    vends = sorted({c.vendor_no for c in caps if c.vendor_no})
    coded = {t.split(":", 1)[1] for (t,) in db.query(AuditLog.target).filter(AuditLog.actor_id == user.id, AuditLog.action == "ap_vendor_learned",
                                                                            AuditLog.target.in_([f"ap_vendor:{v}" for v in vends] or ["-"])).all()}
    out = {}
    for c in caps:
        pf = pfs[c.id]
        if not role[pf]:
            out[c.id] = (False, f"needs the reviewer role in {pf}" if pf else "needs the reviewer role")
        elif f"ap_item:{c.item_id}" in mine_items or c.batch_id in dropped or (c.vendor_no and c.vendor_no in coded):
            out[c.id] = (False, "you dropped or worked on it: a different person reviews it")
        else:
            out[c.id] = (True, "")
    return out


def doc_out(db: Session, c: ApCapture, user: Optional[User] = None, *, items=None, batches=None, users=None, full: bool = False, can=None) -> dict:
    """One document as the Review view shows it."""
    it = (items or {}).get(c.item_id) if items is not None else db.get(ApItem, c.item_id)
    b = (batches or {}).get(c.batch_id) if batches is not None else db.get(ApBatch, c.batch_id)
    rows, f = c.sap_rows or [], c.fields or {}
    r0 = rows[0] if rows else {}
    lines = [r for r in rows[1:] if r.get("pkey") in ("40", "50")]
    m = c.match or {}
    can, why_not = can if can is not None else _can(db, user, c)
    rev = (users or {}).get(c.reviewed_by_id) if users is not None else (db.get(User, c.reviewed_by_id) if c.reviewed_by_id else None)
    out = {"id": c.id, "item_id": c.item_id, "batch_id": c.batch_id, "batch": b.name if b else "", "file": it.name if it else "", "portfolio": c.portfolio or "",
           "bucket": c.bucket, "status": c.sap_status or "", "issues": c.sap_issues or [], "why": c.review_why or "", "state": c.review_state or "",
           "supplier": f.get("supplier") or f.get("municipality") or "", "vendor": c.vendor_no or r0.get("account") or "",
           "vendor_name": (m.get("vendor") or {}).get("name") or (m.get("account") or {}).get("name1") or "",
           "invoice_no": r0.get("refdocnr") or f.get("invoice_no") or "", "date": f.get("invoice_date") or f.get("billing_date") or "", "amount": r0.get("amount") or "",
           "credit": r0.get("pkey") == "21", "company": r0.get("comp") or c.company_code or "",
           "lines": [{"gl": r.get("account") or "", "gl_name": r.get("_gl_name") or "", "cost_centre": r.get("costcentre") or "", "tax_code": r.get("tc") or "",
                      "amount": r.get("amount") or "", "debit": r.get("pkey") == "40", "vat_line": str(r.get("dt") or "") == "X", "text": r.get("text") or ""} for r in lines],
           "duplicate": _dup_text(c), "vat": VC.out(db, c), "uploaded": bool(c.uploaded_at),
           "reviewed_by": _name(rev), "reviewed_at": c.reviewed_at.isoformat(timespec="minutes") if c.reviewed_at else "", "note": c.review_note or "",
           "errors": list(c.review_errors or []), "can": can, "cannot": why_not}
    if full:
        from . import capture as C

        out["pages"] = C._pages(db, it) if it else 0
        out["is_pdf"] = bool(it and it.name.lower().endswith(".pdf"))
        kind = "utility" if c.bucket == "utility" else "manual"
        out["editable"] = {k: ("" if f.get(k) is None else str(f.get(k))) for k in EDITABLE[kind]}
        out["error_fields"] = ERROR_FIELDS
        out["history"] = (it.history or [])[-30:] if it else []
    return out


def queue(db: Session, user: User, show: str = "review", portfolio: str = "", batch: Optional[int] = None, limit: int = 300) -> dict:
    """The Review view: `review` the documents waiting for a review; `vat` the invoices waiting for a VAT decision;
    `done` the latest reviews."""
    q = db.query(ApCapture).filter(ApCapture.bucket.in_(("manual", "utility")))
    if show == "vat":
        q = q.filter(ApCapture.vat_valid.is_(False), func.coalesce(ApCapture.vat_decision, "") == "", ApCapture.uploaded_at.is_(None), ApCapture.stage == "ready")
        order = ApCapture.id
    elif show == "done":
        q = q.filter(ApCapture.review_state.in_(("agreed", "corrected", "rejected")))
        order = ApCapture.reviewed_at.desc()
    else:
        q = q.filter(ApCapture.review_state == "selected", ApCapture.uploaded_at.is_(None), ApCapture.stage == "ready", ApCapture.sap_status != "red")
        order = ApCapture.id
    if portfolio:
        q = q.filter(ApCapture.portfolio == str(portfolio).upper())
    if batch:
        q = q.filter(ApCapture.batch_id == int(batch))
    caps = q.order_by(order).limit(max(1, min(1000, limit))).all()
    items = {i.id: i for i in db.query(ApItem).filter(ApItem.id.in_([c.item_id for c in caps] or [0])).all()}
    batches = {x.id: x for x in db.query(ApBatch).filter(ApBatch.id.in_({c.batch_id for c in caps} or {0})).all()}
    users = {u.id: u for u in db.query(User).filter(User.id.in_({c.reviewed_by_id for c in caps if c.reviewed_by_id} or {0})).all()}
    cans = _can_many(db, user, caps)
    docs = [doc_out(db, c, user, items=items, batches=batches, users=users, can=cans.get(c.id)) for c in caps]
    return {"show": show, "docs": docs, "counts": counts(db, user)}


def counts(db: Session, user: Optional[User]) -> dict:
    """How many wait for a review / a VAT decision — all, and those this person may do."""
    out = {}
    for key, qf in (("review", (ApCapture.review_state == "selected", ApCapture.sap_status != "red")),
                    ("vat", (ApCapture.vat_valid.is_(False), func.coalesce(ApCapture.vat_decision, "") == ""))):
        caps = db.query(ApCapture).filter(ApCapture.bucket.in_(("manual", "utility")), ApCapture.uploaded_at.is_(None), ApCapture.stage == "ready", *qf).limit(500).all()
        out[key] = len(caps)
        out[key + "_mine"] = sum(1 for ok, _w in _can_many(db, user, caps).values() if ok) if user is not None else 0
    return out


@CN.register
def needs_lines(db: Session, user: User) -> list[dict]:
    """Needs you (reviewers): one line each into Controls → Review."""
    if user is None or not R.may(db, user, "reviewer", None):
        return []
    n = counts(db, user)
    out = []
    if n["review_mine"]:
        out.append({"key": "review", "sub": "review", "params": {}, "text": f"{n['review_mine']} document{'s' if n['review_mine'] != 1 else ''} wait{'' if n['review_mine'] != 1 else 's'} for review"})
    if n["vat_mine"]:
        out.append({"key": "vat", "sub": "review", "params": {"show": "vat"},
                    "text": f"{n['vat_mine']} invoice{'s' if n['vat_mine'] != 1 else ''} not a valid tax invoice: claim VAT or not"})
    return out


# ---- a reviewer's outcome -----------------------------------------------------------------------------------------------------------
def act(db: Session, user: User, c: ApCapture, action: str, note: str = "", fields: Optional[dict] = None, errors: Optional[list] = None) -> ApCapture:
    """Agree · Correct · Reject — a reviewer in the document's portfolio who did not touch it."""
    from . import capture as C

    action = str(action or "").strip().lower()
    if action not in ("agree", "correct", "reject"):
        raise Refused("Agree, Correct or Reject.")
    if c.uploaded_at:
        raise Refused("This document already went out in an SAP upload.")
    if c.bucket not in ("manual", "utility") or c.stage != "ready":
        raise Refused("This document is not ready for a review yet (it is still being read or built).")
    if (c.review_state or "") not in ("selected", "rejected"):
        raise Refused("This document is not waiting for a review.")
    pf = c.portfolio or R.portfolio_of(db, c)
    R.require(db, user, "reviewer", pf, what="Reviewing a document")
    if user.id in R.touched_by(db, c, include_dropper=True, exclude_actions=OWN_ACTIONS):
        raise R.Forbidden("You dropped or worked on this document, so a different person reviews it.")
    note = re.sub(r"\s+", " ", str(note or "")).strip()[:600]
    errs = [e for e in dict.fromkeys(str(x or "").strip().lower() for x in errors or []) if e in ERROR_FIELDS]
    it = db.get(ApItem, c.item_id)
    b = db.get(ApBatch, c.batch_id)
    who = _name(user)
    if action == "agree":
        c.review_state, c.review_errors, c.review_hash = "agreed", [], fingerprint(c)
        said = "Agreed" + (f": {note}" if note else ".")
    elif action == "reject":
        if len(note) < 3:
            raise Refused("Say what is wrong (the capturer fixes it from that).")
        c.review_state, c.review_errors, c.review_hash = "rejected", errs or ["other"], fingerprint(c)
        said = f"Rejected: {note}" + (f" (wrong: {', '.join(ERROR_FIELDS[e] for e in errs)})" if errs else "")
    else:
        kind = "utility" if c.bucket == "utility" else "manual"
        f = c.fields or {}
        want = {k: v for k, v in (fields or {}).items() if k in EDITABLE[kind]} if isinstance(fields, dict) else {}
        changed = {k: v for k, v in want.items() if str(v if v is not None else "").strip() != str(f.get(k) if f.get(k) is not None else "").strip()}
        if not changed:
            raise Refused("Correct changes a field. Nothing changed: Agree, or Reject with what is wrong.")
        errs = list(dict.fromkeys([EDITABLE[kind][k] for k in changed] + errs))
        c.review_state, c.review_errors, c.review_hash = "corrected", errs, ""  # the hash is taken once Pip has built the correction
        said = "Corrected " + ", ".join(f"{k} {f.get(k) or '-'} → {v or '-'}" for k, v in changed.items()) + (f": {note}" if note else ".")
    c.reviewed_by_id, c.reviewed_at, c.review_note = user.id, utcnow(), note
    C._hist(it, who, "Review: " + said)
    log(db, "ap_capture_reviewed", actor=user, target=f"ap_item:{c.item_id}", detail=f"{it.name if it else c.id}: {action} — {said}"[:400])
    db.flush()
    if action == "correct":
        C.decide(db, user, c, "save", {"fields": changed})  # Pip builds it again, Tess tests it (and keeps the hash)
    elif action == "reject" and b is not None and it is not None:
        C.retest(db, it)  # red now, not at the next test
    return c


# ---- the monthly coverage report ----------------------------------------------------------------------------------------------------
def _month_of(t: Optional[datetime]) -> str:
    return f"{t:%Y-%m}" if t else ""


def report(db: Session, month: str = "", portfolio: str = "") -> bytes:
    """V05.6: per portfolio — documents uploaded, selected, reviewed (agreed / corrected / rejected), coverage, and the
    errors found by field; then every selected document."""
    import openpyxl

    from .. import xlsafe

    month = VC.month_ok(month) or f"{utcnow():%Y-%m}"
    q = db.query(ApCapture).filter(ApCapture.bucket.in_(("manual", "utility")))
    if portfolio:
        q = q.filter(ApCapture.portfolio == str(portfolio).upper())
    caps = [c for c in q.all() if month in (_month_of(c.uploaded_at), _month_of(c.review_selected_at), _month_of(c.reviewed_at))]
    users = {u.id: u for u in db.query(User).filter(User.id.in_({c.reviewed_by_id for c in caps if c.reviewed_by_id} or {0})).all()}
    items = {i.id: i for i in db.query(ApItem).filter(ApItem.id.in_([c.item_id for c in caps] or [0])).all()}
    per: dict[str, dict] = {}
    for c in caps:
        g = per.setdefault(c.portfolio or "-", {"uploaded": 0, "selected": 0, "reviewed": 0, "agreed": 0, "corrected": 0, "rejected": 0, "waiting": 0, "errors": {}})
        if _month_of(c.uploaded_at) == month:
            g["uploaded"] += 1
        if _month_of(c.review_selected_at) == month:
            g["selected"] += 1
            if (c.review_state or "") == "selected":
                g["waiting"] += 1
        if _month_of(c.reviewed_at) == month and (c.review_state or "") in ("agreed", "corrected", "rejected"):
            g["reviewed"] += 1
            g[c.review_state] += 1
            for e in c.review_errors or []:
                g["errors"][e] = g["errors"].get(e, 0) + 1
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"Coverage {month}"
    fields = list(ERROR_FIELDS)
    ws.append(["Portfolio", "Uploaded", "Selected", "Reviewed", "Coverage of selected %", "Agreed", "Corrected", "Rejected", "Still waiting"] + [f"Errors: {ERROR_FIELDS[k]}" for k in fields])
    for code in sorted(per):
        g = per[code]
        ws.append([code, g["uploaded"], g["selected"], g["reviewed"], round(100 * g["reviewed"] / g["selected"], 1) if g["selected"] else "", g["agreed"], g["corrected"],
                   g["rejected"], g["waiting"]] + [g["errors"].get(k, 0) for k in fields])
    ws2 = wb.create_sheet("Reviews")
    ws2.append(["Portfolio", "Batch", "File", "Supplier", "Invoice no", "Amount", "Selected why", "Selected", "Outcome", "Reviewer", "Reviewed", "Fields wrong", "Note"])
    for c in sorted(caps, key=lambda x: (x.portfolio or "", x.id)):
        if not c.review_state and not c.review_selected_at:
            continue
        f, it = c.fields or {}, items.get(c.item_id)
        st = c.review_state or ""
        ws2.append([c.portfolio or "", c.batch_id, it.name if it else "", f.get("supplier") or f.get("municipality") or "", f.get("invoice_no") or "",
                    float(_amount(c) or 0), c.review_why or "", f"{c.review_selected_at:%Y-%m-%d}" if c.review_selected_at else "", OUTCOME.get(st, "not picked now"),
                    _name(users.get(c.reviewed_by_id)) if st in ("agreed", "corrected", "rejected") else "", f"{c.reviewed_at:%Y-%m-%d %H:%M}" if c.reviewed_at and st != "selected" else "",
                    ", ".join(ERROR_FIELDS.get(e, e) for e in c.review_errors or []), c.review_note or ""])
    for sh in (ws, ws2):
        P._head_style(sh, sh.max_column)
        sh.freeze_panes = "B2"
        P._fit(sh, 50)
    xlsafe.book(wb)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
