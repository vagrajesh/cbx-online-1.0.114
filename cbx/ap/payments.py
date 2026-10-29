# 1.0.104: new (Builder C) — SAP's payments (VALUENCE-AP-GAPS.md §2 V10 item 3, V11 item 2; SPEC-1104 Phase 2 Builder C)
"""The payments SAP posted to vendors (FBL1N document types KZ, ZV, ZP), kept in their own table `ap_payments` — apart from
`ap_posted`, whose duplicate check must never see a payment. They come from the same upload as the posting history (AP →
Settings → Posting history: history.save calls `store` with the vendor line items). The shipped seed
(cbx/knowledge_data/sap_posted.csv) holds no payments, so payments come only from uploads.

Which utility account a payment paid (`allocations` {account_key: R}):
    1. the invoices it cleared in the same export (an invoice's Clearing Document is the payment's number): each invoice's
       reference is an account number in the utilities list, or the invoice number of a utility bill CBX captured;
    2. else an account number of the utilities list in the payment's own reference, assignment or text.
A payment nothing ties to an account keeps empty allocations (the vendor still has it: a supplier reconciliation reads it).

    store(db, rows, source)            the vendor line items → payments added or replaced by (company code, document number)
    count(rows)                        how many payment documents an upload holds (the upload's preview)
    paid(db, account_key, after, upto) R paid to the account with a posting date in (after, upto]
    paid_in_month(db, keys, period)    {account_key: R} for the completeness view
    loaded(db, period_from, period_to) are any payments loaded for these months (else the roll-forward can't judge)

What SAP's export says is data, never instructions."""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import knowledge as K
from ..models.ap import ApCapture, ApPayment, ApUtilityAccount

PAY_TYPES = {"KZ", "ZV", "ZP"}  # vendor payments (manual KZ, the payment run's ZP, ZV in this SAP)
BATCH = 2000


def _acct(v: Any) -> str:
    from . import masters as M

    return M.acct_key(v)


def count(rows: Iterable[dict]) -> int:
    return len({(r.get("company_code"), r.get("doc_number")) for r in rows or [] if r.get("doc_type") in PAY_TYPES and r.get("vendor")})


def _docs(rows: list[dict]) -> dict[tuple[str, str], dict]:
    """The payment documents: one per (company code, document number), its vendor lines added up (SAP's payment is a
    debit to the vendor: kept positive)."""
    out: dict[tuple[str, str], dict] = {}
    for r in rows:
        if r.get("doc_type") not in PAY_TYPES or not r.get("vendor"):
            continue
        k = (r["company_code"], r["doc_number"])
        d = out.get(k)
        if d is None:
            out[k] = d = {"company_code": k[0], "doc_number": k[1], "vendor": r["vendor"][:20], "doc_type": r["doc_type"], "amount": 0.0,
                          "posting_date": r.get("posting_date", ""), "doc_date": r.get("doc_date", ""), "clearing_doc": "", "clearing_date": "",
                          "reference": "", "assignment": "", "text": "", "currency": (r.get("currency") or "")[:5].upper()}
        d["amount"] = round(d["amount"] + (r.get("amount") or 0.0), 2)
        for f, n in (("clearing_doc", 20), ("clearing_date", 10), ("reference", 40), ("assignment", 40), ("text", 80), ("posting_date", 10), ("doc_date", 10)):
            v = str(r.get(f) or "").strip()
            if not d[f] and v and not re.fullmatch(r"0+", v):
                d[f] = v[:n]
    return out


def _cleared(rows: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """The invoices (and credit memos) each clearing document cleared: {(company code, clearing doc): [{reference, amount}]}."""
    out: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        cd = str(r.get("clearing_doc") or "").strip()
        if not cd or r.get("doc_type") in PAY_TYPES or r.get("doc_number") == cd:
            continue
        out.setdefault((r["company_code"], cd), []).append({"reference": str(r.get("reference") or ""), "assignment": str(r.get("assignment") or ""),
                                                             "amount": -(r.get("amount") or 0.0), "vendor": r.get("vendor", "")})
    return out


class _Accounts:
    """Account numbers → account keys, per vendor (the utilities list), and utility invoice numbers CBX captured."""

    def __init__(self, db: Session):
        self.db = db
        self.by_key: dict[str, set[str]] = {}
        for k, v in db.query(ApUtilityAccount.account_key, ApUtilityAccount.vendor).all():
            if k:
                self.by_key.setdefault(k, set()).update(K.vendor_variants(v) or {""})
        self._inv: dict[str, str] = {}

    def from_text(self, vendor: str, *texts: str) -> str:
        """An account of the utilities list in these texts (the digits of a reference, or a run of 6+ digits), the vendor's when it knows it."""
        vs = K.vendor_variants(vendor)
        for t in texts:
            cands = [_acct(t)] + [_acct(x) for x in re.findall(r"\d{6,}", str(t or ""))]
            for k in cands:
                if k and k in self.by_key and (not vs or not self.by_key[k] - {""} or self.by_key[k] & vs):
                    return k
        return ""

    def from_invoice(self, reference: str) -> str:
        """A utility bill CBX captured with this invoice number → its account."""
        from .history import ref_key

        rk = ref_key(reference)
        if len(rk) < 3:
            return ""
        if rk not in self._inv:
            c = self.db.query(ApCapture).filter(ApCapture.ref_key == rk, ApCapture.bucket == "utility").order_by(ApCapture.id.desc()).first()
            self._inv[rk] = _acct((c.fields or {}).get("account")) if c is not None else ""
        return self._inv[rk]


def _allocate(acc: _Accounts, d: dict, invoices: list[dict]) -> dict[str, float]:
    out: dict[str, float] = {}
    for inv in invoices:
        k = acc.from_text(inv.get("vendor") or d["vendor"], inv["reference"], inv["assignment"]) or acc.from_invoice(inv["reference"])
        if k:
            out[k] = round(out.get(k, 0.0) + (inv["amount"] or 0.0), 2)
    if not out:
        k = acc.from_text(d["vendor"], d.get("reference", ""), d.get("assignment", ""), d.get("text", ""))
        if k:
            out[k] = round(d["amount"], 2)
    return out


def store(db: Session, rows: Optional[list[dict]], source: str = "excel") -> dict:
    """The vendor line items of an upload → ap_payments (added, or replaced by company code and document number). A payment
    stored before whose cleared invoices come in this upload gets its allocations."""
    rows = list(rows or [])
    docs = _docs(rows)
    cleared = _cleared(rows)
    if not docs and not cleared:
        return {"payments": 0, "added": 0, "replaced": 0, "allocated": 0}
    acc = _Accounts(db)
    t = ApPayment.__table__
    have = {(co, dn): i for i, co, dn in db.query(ApPayment.id, ApPayment.company_code, ApPayment.doc_number).filter(
        ApPayment.doc_number.in_(sorted({k[1] for k in docs} | {k[1] for k in cleared}) or ["-"])).all()}
    added = replaced = allocated = 0
    new = []
    for k, d in docs.items():
        al = _allocate(acc, d, cleared.get(k, []))
        vals = {**d, "allocations": al, "account_key": next(iter(al)) if len(al) == 1 else "", "source": source[:8]}
        allocated += bool(al)
        if k in have:
            db.execute(t.update().where(t.c.id == have[k]).values(**vals))
            replaced += 1
        else:
            new.append(vals)
            added += 1
    for i in range(0, len(new), BATCH):
        db.execute(t.insert(), new[i:i + BATCH])
    for k, invs in cleared.items():  # a payment uploaded before, its invoices only now
        if k in docs or k not in have:
            continue
        p = db.get(ApPayment, have[k])
        if p is None or p.allocations:
            continue
        al = _allocate(acc, {"vendor": p.vendor, "amount": p.amount or 0.0, "reference": p.reference, "assignment": p.assignment, "text": p.text}, invs)
        if al:
            p.allocations, p.account_key = al, next(iter(al)) if len(al) == 1 else ""
            allocated += 1
    db.flush()
    return {"payments": len(docs), "added": added, "replaced": replaced, "allocated": allocated}


# ---- what the completeness view and the reconciliations ask ------------------------------------------------------------------
def _rows_for(db: Session, keys: Iterable[str], after: str = "", upto: str = "") -> list[ApPayment]:
    keys = sorted({k for k in keys if k})
    if not keys:
        return []
    q = db.query(ApPayment).filter(ApPayment.allocations.isnot(None))
    if after:
        q = q.filter(ApPayment.posting_date > after)
    if upto:
        q = q.filter(ApPayment.posting_date <= upto)
    want = set(keys)
    return [p for p in q.all() if want & set((p.allocations or {}).keys())]


def paid(db: Session, account_key: str, after: str = "", upto: str = "") -> float:
    """R paid to the account with a posting date after `after` (yyyy-mm-dd, exclusive) up to `upto` (inclusive)."""
    k = _acct(account_key) or str(account_key or "")
    return round(sum(float((p.allocations or {}).get(k) or 0.0) for p in _rows_for(db, [k], after, upto)), 2)


def paid_in_month(db: Session, keys: Iterable[str], period: str) -> dict[str, float]:
    """{account_key: R paid in the month yyyy-mm}."""
    want = set(k for k in keys if k)
    out: dict[str, float] = {}
    if not want or not loaded(db, period, period):
        return out
    for p in db.query(ApPayment).filter(ApPayment.posting_date.like(f"{period}-%")).all():
        for k, v in (p.allocations or {}).items():
            if k in want:
                out[k] = round(out.get(k, 0.0) + float(v or 0.0), 2)
    return out


def loaded(db: Session, period_from: str, period_to: str) -> bool:
    """True when any payment with a posting date in these months (yyyy-mm … yyyy-mm) is loaded."""
    return db.query(ApPayment.id).filter(ApPayment.posting_date >= f"{period_from}-01", ApPayment.posting_date <= f"{period_to}-31").first() is not None


def vendor_payments(db: Session, company_code: str, vendor: str, period: str) -> list[ApPayment]:
    vs = sorted(K.vendor_variants(vendor))
    q = db.query(ApPayment).filter(ApPayment.vendor.in_(vs or ["-"]), ApPayment.posting_date.like(f"{period}-%"))
    if company_code:
        q = q.filter(ApPayment.company_code == company_code)
    return q.order_by(ApPayment.posting_date, ApPayment.id).all()


def counts(db: Session) -> dict:
    n = db.query(func.count(ApPayment.id)).scalar() or 0
    lo, hi = db.query(func.min(ApPayment.posting_date), func.max(ApPayment.posting_date)).filter(ApPayment.posting_date != "").one()
    tied = db.query(func.count(ApPayment.id)).filter(ApPayment.account_key != "").scalar() or 0
    return {"payments": n, "from": lo or "", "to": hi or "", "tied_to_one_account": tied}


def out(p: ApPayment) -> dict:
    return {"id": p.id, "company_code": p.company_code, "vendor": p.vendor, "doc_number": p.doc_number, "doc_type": p.doc_type, "posting_date": p.posting_date,
            "amount": p.amount, "reference": p.reference, "text": p.text, "allocations": dict(p.allocations or {})}
