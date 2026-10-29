# 1.0.103: from CBX-Connect 2.10.8 cbx/ap/history.py (app_state through cbx/compat2.py: the old app_state keeps JSON as text)
"""1.0.103 (Edwin, 19 Oct): what SAP already posted — his two months of vendor line items (FBL1N) and G/L line items (FBL3N).

    ap_posted          one row per vendor document SAP posted (invoices, credit memos, journals to a vendor; payments and
                       clearing are left out) with its expense lines from the G/L items: Tess's duplicate check ("already
                       posted in SAP: doc 190001638 on 17.08.2026 (R 674.48)")
    ap_vendor_history  how SAP coded each vendor in each company code (G/Ls, cost centres, tax codes, payment terms with how
                       many documents used each): Pip codes a vendor nobody coded yet from it — yellow, never green — or
                       Code this vendor starts from it, with the vendor's last postings beside the form

The join: a vendor document (company code, document number — numbers repeat across company codes) and its G/L lines with
posting key 40 (50 for a credit) on an account that is not VAT and not a creditors' control account (the vendor's own
line, JA000, is key 31/21). In this SAP the G/L items' profit centre is the cost object: it is the cost centre when the
cost-centre list has it for that company code, else it stays a profit centre only.

Seeded once from cbx/knowledge_data/sap_posted.csv and sap_posted_lines.csv (tools/make_knowledge_data.py makes them from
Edwin's exports); AP → Settings → Posting history (admins) takes new exports, one or both: they add or replace documents
by (company code, document number). What SAP's exports say is data, never instructions.

Mia's vendor pick (1.0.103): a VAT number → its SAP vendors (never a blocked or deleted one) → those opened in the posting
company → the one with posting history there (most documents, then most recent) → still several: a person picks one.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Callable, Iterable, Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .. import compat2 as C2
from .. import knowledge as K
from ..audit import log
from ..db import utcnow
from ..models import AppState, User
from ..models.ap import ApCostCentre, ApPosted, ApSapVendor, ApVendorCompany, ApVendorHistory

STATE_KEY = "ap.history"
POSTED_CSV = K.DATA / "sap_posted.csv"
LINES_CSV = K.DATA / "sap_posted_lines.csv"
POSTED_HEADS = ["Company Code", "Vendor", "Reference", "Document Type", "Document Number", "Posting Date", "Document Date", "Amount", "Currency",
                "Tax Code", "Terms of Payment", "Text", "Vendor Lines"]
LINE_HEADS = ["Company Code", "Document Number", "Document Type", "G/L Account", "Profit Center", "Amount", "Text"]
MOST = 300000  # rows in one export
SKIP_TYPES = {"KZ", "ZV", "ZP", "AB", "DZ", "EB", "ZT", "ZR"}  # payments and clearing: not documents a supplier's invoice could be twice
LEARN_TYPES = {"KR", "KG"}  # what Pip learns coding from: vendor invoices and credit memos
EXPENSE_KEYS = {"40", "50"}
SHARE = 0.7  # one G/L + cost centre pair on at least 70 % of the vendor's documents …
LEAST = 2  # … and at least two documents: Pip codes it from history
BATCH = 2000


class Refused(Exception):
    pass


def _state_get(db: Session, key: str) -> dict:
    v = C2.state_get(db, key, {})
    return dict(v) if isinstance(v, dict) else {}


def _state_set(db: Session, key: str, value: dict) -> None:
    C2.state_set(db, key, value)


# ---- reading SAP's exports ------------------------------------------------------------------------------------------------
VENDOR_COLS = {"company_code": ("company code", "bukrs", "cocd"), "vendor": ("account", "vendor", "supplier", "lifnr", "vendor account"),
               "reference": ("reference", "xblnr", "ref. doc."), "doc_type": ("document type", "blart", "doc. type", "type"),
               "posting_date": ("posting date", "budat", "pstng date"), "doc_date": ("document date", "bldat", "doc. date"),
               "doc_number": ("document number", "belnr", "documentno", "doc. no."), "currency": ("local currency", "currency", "waers", "lcurr"),
               "tax_code": ("tax code", "mwskz", "tx"), "amount": ("amount in local currency", "dmbtr", "amount in lc", "amount"),
               "text": ("text", "sgtxt"), "terms": ("terms of payment", "zterm", "payt terms"),
               # 1.0.104 (Builder C): read when the export has them — the payments (cbx/ap/payments.py) and what they cleared
               "clearing_doc": ("clearing document", "augbl", "clrng doc."), "clearing_date": ("clearing date", "augdt", "clrng date"),
               "assignment": ("assignment", "zuonr")}
GL_COLS = {"company_code": ("company code", "bukrs", "cocd"), "account": ("account", "g/l account", "g/l acct", "hkont", "saknr", "g/l"),
           "profit_centre": ("profit center", "profit centre", "prctr"), "doc_type": ("document type", "blart", "doc. type", "type"),
           "doc_number": ("document number", "belnr", "documentno", "doc. no."), "text": ("text", "sgtxt"),
           "amount": ("amount in local currency", "dmbtr", "amount in lc", "amount"), "posting_key": ("posting key", "bschl", "pk")}
NEEDED = {"vendor": ("company_code", "vendor", "doc_number", "doc_type", "amount"), "gl": ("company_code", "account", "doc_number", "amount", "posting_key")}
NAMES = {"vendor": "the vendor line items (FBL1N: Company Code, Account, Reference, Document Type, Document Number, Amount …)",
         "gl": "the G/L line items (FBL3N: Company Code, Account, Profit Center, Document Number, Posting Key, Amount …)"}


def _head(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip().lower()


def _map(heads: list, cols: dict) -> dict[str, int]:
    norm = [_head(h) for h in heads]
    out: dict[str, int] = {}
    for f, names in cols.items():
        for nm in names:
            if nm in norm and norm.index(nm) not in out.values():
                out[f] = norm.index(nm)
                break
    return out


def iso(v: Any) -> str:
    """An SAP date as yyyy-mm-dd: a datetime, "2026-08-26", "26.08.2026", "20260826"; SAP's empty "0001-01-01" is ""."""
    if v is None or v == "":
        return ""
    if isinstance(v, datetime):
        v = v.date()
    if isinstance(v, date):
        return "" if v.year < 1900 else v.isoformat()
    s = str(v).strip()
    for rx, order in ((r"^(\d{4})-(\d{1,2})-(\d{1,2})", "ymd"), (r"^(\d{1,2})[./](\d{1,2})[./](\d{4})", "dmy"), (r"^(\d{4})(\d{2})(\d{2})$", "ymd")):
        m = re.match(rx, s)
        if m:
            a, b, c = (int(x) for x in m.groups())
            y, mo, d = (a, b, c) if order == "ymd" else (c, b, a)
            try:
                return "" if y < 1900 else date(y, mo, d).isoformat()
            except ValueError:
                return ""
    return ""


def _num(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(" ", "")
    neg = s.endswith("-") or s.startswith("-") or (s.startswith("(") and s.endswith(")"))
    s = re.sub(r"[^\d.,]", "", s)
    if s.count(",") and not s.count("."):
        s = s.replace(",", ".") if re.search(r",\d{1,2}$", s) else s.replace(",", "")
    s = s.replace(",", "")
    try:
        f = float(s)
    except ValueError:
        return None
    return -f if neg else f


def read_items(kind: str, data: bytes, filename: str = "") -> list[dict]:
    """SAP's vendor line items (kind "vendor", FBL1N) or G/L line items (kind "gl", FBL3N) — Excel or CSV, English labels
    or the technical names (BUKRS, LIFNR, XBLNR, BLART, BELNR, BUDAT, BLDAT, MWSKZ, DMBTR, PRCTR, SGTXT, ZTERM / HKONT,
    BSCHL) → rows of {field: value} (text trimmed, dates yyyy-mm-dd, amounts numbers)."""
    cols = VENDOR_COLS if kind == "vendor" else GL_COLS
    try:
        it = iter(K._rows(data, filename, MOST, cols=40))
        got: dict[str, int] = {}
        for _ in range(15):
            r = next(it, None)
            if r is None:
                break
            got = _map(list(r), cols)
            if all(f in got for f in NEEDED[kind]):
                break
        if not all(f in got for f in NEEDED[kind]):
            raise Refused(f"This is not {NAMES[kind]}: no header row with {', '.join(f.replace('_', ' ') for f in NEEDED[kind] if f not in got)}.")
        out = []
        for r in it:
            cell = lambda f: r[got[f]] if f in got and got[f] < len(r) else None  # noqa: E731
            rec = {f: K.text(cell(f), 80) for f in got if f not in ("amount", "posting_date", "doc_date", "clearing_date")}  # 1.0.104: + clearing date
            rec["amount"] = _num(cell("amount"))
            for f in ("posting_date", "doc_date", "clearing_date"):
                if f in got:
                    rec[f] = iso(cell(f))
            rec["company_code"] = rec.get("company_code", "").replace(" ", "").upper()[:12]
            rec["doc_number"] = rec.get("doc_number", "").replace(" ", "")[:20]
            rec["doc_type"] = rec.get("doc_type", "").upper()[:4]
            if kind == "vendor":
                rec["vendor"] = rec.get("vendor", "").replace(" ", "")[:20]
            else:
                rec["account"] = rec.get("account", "").replace(" ", "")[:20]
                rec["posting_key"] = rec.get("posting_key", "").strip().zfill(2)[:2]
            if rec["company_code"] and rec["doc_number"]:
                out.append(rec)
        return out
    except K.Refused as e:
        raise Refused(str(e)) from None


def ref_key(v: Any) -> str:
    """A reference as CBX compares it: A–Z and 0–9 only, upper case, leading zeros gone ("INV-0042" → "INV0042", "000123" → "123")."""
    return re.sub(r"[^A-Z0-9]", "", K.text(v, 80).upper()).lstrip("0")[:40]


def posted_docs(rows: Iterable[dict]) -> dict[tuple[str, str], dict]:
    """The vendor line items → one document per (company code, document number), payments and clearing left out. A
    document's vendor lines add up (SAP's invoice is a credit to the vendor: it is kept positive, a credit memo negative)."""
    out: dict[tuple[str, str], dict] = {}
    for r in rows:
        if r.get("doc_type") in SKIP_TYPES or not r.get("vendor"):
            continue
        k = (r["company_code"], r["doc_number"])
        d = out.get(k)
        if d is None:
            out[k] = d = {"company_code": k[0], "doc_number": k[1], "vendor": r["vendor"], "reference": r.get("reference", "")[:40], "doc_type": r.get("doc_type", ""),
                          "posting_date": r.get("posting_date", ""), "doc_date": r.get("doc_date", ""), "amount": 0.0, "currency": r.get("currency", "")[:5].upper(),
                          "tax_code": "", "terms": "", "text": "", "vendor_lines": 0}
        d["amount"] = round(d["amount"] - (r.get("amount") or 0.0), 2)
        d["vendor_lines"] += 1
        for f, n in (("reference", 40), ("tax_code", 8), ("terms", 8), ("text", 80), ("posting_date", 10), ("doc_date", 10)):
            if not d[f] and r.get(f):
                d[f] = str(r[f])[:n]
        if d["tax_code"] == "**":  # SAP's "several tax codes": the first real one
            d["tax_code"] = r.get("tax_code", "") if r.get("tax_code") not in ("", "**") else "**"
    for d in out.values():
        d["ref_key"] = ref_key(d["reference"])
    return out


VAT_NAME = re.compile(r"(?i)^(?:input|output|withholding)\s+vat\b|^vat\s*-\s*net|^net\s+receivable\s+vat|\bvat\s+take-on|creditors\s+control|^creditors\s+provision")


def skip_account(account: str, name: str, vat_gl: str = "") -> bool:
    """Not an expense line: a VAT account (Input VAT - Expense …, the GL table's VAT G/L) or a creditors' control account."""
    return bool(VAT_NAME.search(name or "")) or (bool(vat_gl) and K.gl_key(account) == K.gl_key(vat_gl))


def expense_lines(rows: Iterable[dict], name_of: Callable[[str], str], vat_gl: str = "") -> dict[tuple[str, str], list[dict]]:
    """The G/L line items → each document's expense lines [{gl, pc, amount, text, type}]: posting key 40 (50 for a
    credit) on an account that is not VAT and not a creditors' control account."""
    out: dict[tuple[str, str], list[dict]] = {}
    names: dict[str, bool] = {}
    for r in rows:
        if r.get("posting_key") not in EXPENSE_KEYS or not r.get("account"):
            continue
        a = r["account"]
        if a not in names:
            names[a] = skip_account(a, name_of(a), vat_gl)
        if names[a]:
            continue
        amt = r.get("amount") or 0.0
        out.setdefault((r["company_code"], r["doc_number"]), []).append({"gl": a, "pc": (r.get("profit_centre") or "").replace(" ", "")[:20],
                                                                          "amount": round(amt, 2), "text": (r.get("text") or "")[:60], "type": r.get("doc_type", "")})
    return out


# ---- the seed files -------------------------------------------------------------------------------------------------------
def seed_rows() -> tuple[list[dict], dict[tuple[str, str], list[dict]]]:
    """The shipped documents and their expense lines (cbx/knowledge_data/sap_posted.csv, sap_posted_lines.csv)."""
    docs = []
    for r in K._read_csv(POSTED_CSV)[1:]:
        if len(r) < len(POSTED_HEADS) or not r[0] or not r[4]:
            continue
        d = dict(zip(("company_code", "vendor", "reference", "doc_type", "doc_number", "posting_date", "doc_date", "amount", "currency", "tax_code", "terms", "text"), r))
        d["amount"], d["vendor_lines"] = _num(d["amount"]), int(r[12] or 1)
        d["ref_key"] = ref_key(d["reference"])
        docs.append(d)
    lines: dict[tuple[str, str], list[dict]] = {}
    for r in K._read_csv(LINES_CSV)[1:]:
        if len(r) >= 7 and r[0] and r[1]:
            lines.setdefault((r[0], r[1]), []).append({"gl": r[3], "pc": r[4], "amount": _num(r[5]) or 0.0, "text": r[6], "type": r[2]})
    return docs, lines


def write_seed(vendor_rows: list[dict], gl_rows: list[dict], name_of: Callable[[str], str], vat_gl: str = "JA600") -> tuple[int, int]:
    """tools/make_knowledge_data.py: Edwin's two exports → the two seed files (only the documents kept and their expense lines)."""
    docs = posted_docs(vendor_rows)
    lines = expense_lines(gl_rows, name_of, vat_gl)
    n = K.write_csv(POSTED_CSV, POSTED_HEADS, ([d["company_code"], d["vendor"], d["reference"], d["doc_type"], d["doc_number"], d["posting_date"], d["doc_date"],
                                               f"{d['amount']:.2f}", d["currency"], d["tax_code"], d["terms"], d["text"], d["vendor_lines"]] for _k, d in sorted(docs.items())))
    m = K.write_csv(LINES_CSV, LINE_HEADS, ([co, doc, ln["type"], ln["gl"], ln["pc"], f"{ln['amount']:.2f}", ln["text"]]
                                            for (co, doc), lns in sorted(lines.items()) if (co, doc) in docs for ln in lns))
    return n, m


def seed(db: Session) -> None:
    """At start: the shipped posting history, once, into an empty table (an admin's later uploads are never undone)."""
    if db.get(AppState, "ap.seeded.posting_history"):
        return
    if not db.query(ApPosted.id).first():
        docs, lines = seed_rows()
        if docs:
            _insert(db, docs, lines, "seed")
            db.flush()
            rebuild(db)
            _meta(db, "seed", "SAP line items (shipped)", docs)
    _state_set(db, "ap.seeded.posting_history", {"at": utcnow().isoformat()})
    db.flush()


def _insert(db: Session, docs: list[dict], lines: dict, source: str) -> None:
    t = ApPosted.__table__
    rows = [{**{f: d.get(f) if d.get(f) is not None else "" for f in ("company_code", "vendor", "reference", "ref_key", "doc_type", "doc_number", "posting_date",
                                                                   "doc_date", "currency", "tax_code", "terms", "text")},
             "amount": d.get("amount"), "vendor_lines": int(d.get("vendor_lines") or 1), "source": source[:8],
             "lines": _own_lines(d, lines.get((d["company_code"], d["doc_number"])) or [])} for d in docs]
    for i in range(0, len(rows), BATCH):
        db.execute(t.insert(), rows[i:i + BATCH])


def _own_lines(d: dict, lns: list[dict]) -> list[dict]:
    """A document's expense lines, only those of the same document type (a G/L export's document number is the same
    document only when its type agrees)."""
    return [{k: ln[k] for k in ("gl", "pc", "amount", "text")} for ln in lns if not ln.get("type") or not d.get("doc_type") or ln["type"] == d["doc_type"]]


def _meta(db: Session, source: str, by: str, docs: list, vendor_lines: Optional[int] = None, gl_lines: Optional[int] = None) -> None:
    """What the Settings page says about the history: documents, vendor lines, the months, the company codes."""
    n = db.query(func.count(ApPosted.id)).scalar() or 0
    lo, hi = db.query(func.min(ApPosted.posting_date), func.max(ApPosted.posting_date)).filter(ApPosted.posting_date != "").one()
    cos = db.query(func.count(func.distinct(ApPosted.company_code))).scalar() or 0
    vl = db.query(func.sum(ApPosted.vendor_lines)).scalar() or 0
    with_lines = sum(1 for (x,) in db.query(ApPosted.lines).all() if x)
    _state_set(db, STATE_KEY, {**_state_get(db, STATE_KEY), "documents": n, "vendor_lines": int(vl), "with_lines": with_lines, "from": lo or "", "to": hi or "",
                               "companies": cos, "vendors": db.query(func.count(ApVendorHistory.id)).scalar() or 0, "source": source, "by": by,
                               "at": utcnow().isoformat(timespec="minutes"),
                               **({"last_vendor_lines": vendor_lines} if vendor_lines is not None else {}),
                               **({"last_gl_lines": gl_lines} if gl_lines is not None else {})})


def counts(db: Session) -> dict:
    v = _state_get(db, STATE_KEY)
    return {"documents": db.query(func.count(ApPosted.id)).scalar() or 0, "vendors": db.query(func.count(ApVendorHistory.id)).scalar() or 0,
            "vendor_lines": v.get("vendor_lines", 0), "with_lines": v.get("with_lines", 0), "from": v.get("from", ""), "to": v.get("to", ""),
            "companies": v.get("companies", 0), "source": v.get("source", ""), "by": v.get("by", ""), "at": v.get("at", "")}


# ---- an admin's upload ------------------------------------------------------------------------------------------------------
def save(db: Session, user: User, vendor_rows: Optional[list] = None, gl_rows: Optional[list] = None, *, source: str = "excel", confirm: bool = False) -> dict:
    """AP → Settings → Posting history: the vendor line items and/or the G/L line items. confirm=False says what would
    change; confirm=True saves: documents added or replaced by (company code, document number) — a document keeps its
    expense lines when only the vendor items came — the G/L items' lines go on the documents they belong to, and the
    history of every vendor touched is learned again."""
    if not user.is_admin:
        raise PermissionError("Only an admin changes the posting history.")
    if not vendor_rows and not gl_rows:
        raise Refused("Give the vendor line items, the G/L line items, or both.")
    from . import masters as M

    docs = posted_docs(vendor_rows or [])
    vat_gl = M.tables(db)["gl"].get("vat", "")
    lines = expense_lines(gl_rows or [], lambda a: (K.gl_account(db, a) or {}).get("name", ""), vat_gl) if gl_rows else {}
    have = {(co, dn): (i, v) for i, co, dn, v in db.query(ApPosted.id, ApPosted.company_code, ApPosted.doc_number, ApPosted.vendor).all()}
    new = [k for k in docs if k not in have]
    again = [k for k in docs if k in have]
    attach = [k for k in lines if k in docs or k in have]
    dates = sorted(d["posting_date"] for d in docs.values() if d["posting_date"])
    out = {"vendor_lines": len(vendor_rows or []), "gl_lines": len(gl_rows or []), "documents": len(docs), "added": len(new), "replaced": len(again),
           "with_lines": len(attach), "lines_without_document": len(lines) - len(attach), "companies": len({k[0] for k in docs} | {k[0] for k in lines}),
           "from": dates[0] if dates else "", "to": dates[-1] if dates else "", "saved": False}
    from . import payments as PAY  # 1.0.104 (Builder C): the payments in the same export go to ap_payments (posted_docs leaves them out)

    out["payments"] = PAY.count(vendor_rows or [])
    if not confirm:
        return out
    touched: set[tuple[str, str]] = set()
    t = ApPosted.__table__
    for k in again:
        i, old_vendor = have[k]
        d = docs[k]
        touched |= {(k[0], old_vendor), (k[0], d["vendor"])}
        vals = {f: d[f] for f in ("vendor", "reference", "ref_key", "doc_type", "posting_date", "doc_date", "amount", "currency", "tax_code", "terms", "text", "vendor_lines")}
        if k in lines:
            vals["lines"] = _own_lines(d, lines[k])
        db.execute(t.update().where(t.c.id == i).values(**vals, source=source[:8]))
    _insert(db, [docs[k] for k in new], lines, source)
    touched |= {(k[0], docs[k]["vendor"]) for k in new}
    for k in attach:  # G/L lines for documents uploaded before (the vendor items came in an earlier upload)
        if k in docs:
            continue
        i, v = have[k]
        dt = db.query(ApPosted.doc_type).filter(ApPosted.id == i).scalar() or ""
        db.execute(t.update().where(t.c.id == i).values(lines=_own_lines({"doc_type": dt}, lines[k])))
        touched.add((k[0], v))
    db.flush()
    rebuild(db, touched)
    _meta(db, source, (user.name or user.email), list(docs.values()), len(vendor_rows or []) or None, len(gl_rows or []) or None)
    log(db, "ap_posting_history", actor=user, target="ap:posting_history",
        detail=f"from {source}: {len(vendor_rows or [])} vendor lines, {len(gl_rows or [])} G/L lines; {len(new)} documents added, {len(again)} replaced, "
               f"{len(attach)} with expense lines; {len(touched)} vendor histories learned again")
    if vendor_rows:  # 1.0.104 (Builder C): the payments
        out["payments_saved"] = PAY.store(db, vendor_rows, source)
        if out["payments_saved"]["payments"]:
            log(db, "ap_payments_loaded", actor=user, target="ap:payments", detail=f"from {source}: {out['payments_saved']['payments']} payments "
                f"({out['payments_saved']['added']} added, {out['payments_saved']['replaced']} replaced, {out['payments_saved']['allocated']} tied to utility accounts)")
    out["saved"] = True
    out["vendors"] = len(touched)
    return out


# ---- learning each vendor's coding ----------------------------------------------------------------------------------------
def _cost_centres(db: Session, companies: set) -> set[tuple[str, str]]:
    """(cost centre, company code) pairs SAP's cost-centre list has for these company codes."""
    out: set = set()
    cos = sorted(c for c in companies if c)
    for i in range(0, len(cos), 500):
        out |= {(cc, co) for cc, co in db.query(ApCostCentre.cost_centre, ApCostCentre.company_code).filter(ApCostCentre.company_code.in_(cos[i:i + 500])).all()}
    return out


def _bump(d: dict, k: str) -> None:
    if k:
        d[k] = d.get(k, 0) + 1


def rebuild(db: Session, pairs: Optional[set] = None) -> int:
    """ap_vendor_history from ap_posted, for these (company code, vendor) pairs (all when None)."""
    q = db.query(ApPosted.company_code, ApPosted.vendor, ApPosted.doc_type, ApPosted.posting_date, ApPosted.tax_code, ApPosted.terms, ApPosted.text, ApPosted.lines)
    keys = sorted(p for p in (pairs or set()) if p[0] and p[1])
    if pairs is not None:
        if not keys:
            return 0
        rows = []
        by_co: dict[str, list[str]] = {}
        for co, v in keys:
            by_co.setdefault(co, []).append(v)
        for co, vs in by_co.items():
            for i in range(0, len(vs), 500):
                rows += q.filter(ApPosted.company_code == co, ApPosted.vendor.in_(vs[i:i + 500])).all()
    else:
        rows = q.all()
    known = _cost_centres(db, {r[0] for r in rows})
    agg: dict[tuple[str, str], dict] = {}
    for co, v, dt, pd, tc, terms, text, lines in sorted(rows, key=lambda r: r[3] or "", reverse=True):
        if dt not in LEARN_TYPES or not lines:
            continue
        h = agg.setdefault((co, v), {"docs": 0, "split": 0, "last_posting": "", "gls": {}, "cost_centres": {}, "profit_centres": {}, "pairs": {}, "tax_codes": {},
                                     "terms": {}, "texts": []})
        h["docs"] += 1
        h["last_posting"] = max(h["last_posting"], pd or "")
        doc_pairs = set()
        for ln in lines:
            pc = ln.get("pc") or ""
            cc = pc if (pc, co) in known else ""
            doc_pairs.add((ln.get("gl") or "", cc))
        for g in {p[0] for p in doc_pairs}:
            _bump(h["gls"], g)
        for c in {p[1] for p in doc_pairs}:
            _bump(h["cost_centres"], c)
        for p in {ln.get("pc") or "" for ln in lines}:
            _bump(h["profit_centres"], p)
        if len(doc_pairs) == 1:
            g, c = next(iter(doc_pairs))
            _bump(h["pairs"], f"{g}|{c}")
        else:
            h["split"] += 1
        if tc and tc != "**":
            _bump(h["tax_codes"], tc)
        _bump(h["terms"], terms or "")
        sample = (text or next((ln.get("text") for ln in lines if ln.get("text")), "") or "").strip()
        if sample and sample not in h["texts"] and len(h["texts"]) < 2:
            h["texts"].append(sample[:80])
    t = ApVendorHistory.__table__
    if pairs is None:
        db.execute(t.delete())
    else:
        for co, vs in {co: [v for c2, v in keys if c2 == co] for co, _ in keys}.items():
            for i in range(0, len(vs), 500):
                db.execute(t.delete().where(t.c.company_code == co, t.c.vendor.in_(vs[i:i + 500])))
    out = [{"company_code": co, "vendor": v, **h} for (co, v), h in agg.items()]
    for i in range(0, len(out), BATCH):
        db.execute(t.insert(), out[i:i + BATCH])
    db.flush()
    return len(out)


# ---- what Mia, Pip, Tess and the page ask -----------------------------------------------------------------------------------
def _variants(vendors: Iterable[Any]) -> list[str]:
    out: set[str] = set()
    for v in vendors:
        out |= K.vendor_variants(v)
    return sorted(out)


def doc_kinds(db: Session) -> tuple[set, set]:
    """1.0.103: (invoice types, credit types): KR, RE and the types the document-type table means as invoices; KG and the
    types it means as credit memos."""
    kinds = K.doc_types_all(db)
    return ({"KR", "RE"} | {c for c, d in kinds.items() if d.get("kind") == "invoice"},
            {"KG"} | {c for c, d in kinds.items() if d.get("kind") == "credit"})


def _doc(d: ApPosted) -> dict:
    return {"doc_number": d.doc_number, "company_code": d.company_code, "vendor": d.vendor, "reference": d.reference, "posting_date": d.posting_date,
            "amount": d.amount, "doc_type": d.doc_type}


def posted_twin(db: Session, vendors: Iterable[Any], reference: Any, credit: bool = False) -> Optional[dict]:
    """The document SAP already posted for this vendor (one of these vendor numbers) and this reference (compared A–Z 0–9
    only). None: SAP has none. The same reference for another vendor is not the same invoice. 1.0.103: an invoice matches
    only SAP's invoices (KR, RE …), a credit note only its credit memos (KG …); an invoice SAP posted and a credit memo
    with the same reference took back to nothing is `reversed` (a yellow note, not a duplicate)."""
    k = ref_key(reference)
    vs = _variants(vendors)
    if len(k) < 2 or not vs:
        return None
    docs: list[ApPosted] = []
    for i in range(0, len(vs), 900):
        docs += db.query(ApPosted).filter(ApPosted.ref_key == k, ApPosted.vendor.in_(vs[i:i + 900])).all()
    inv_t, cred_t = doc_kinds(db)
    invs = [d for d in docs if d.doc_type in inv_t]
    creds = [d for d in docs if d.doc_type in cred_t]
    want = creds if credit else invs
    if not want:
        return None
    last = lambda ds: max(ds, key=lambda d: (d.posting_date or "", d.id))  # noqa: E731
    out = _doc(last(want))
    if not credit and creds and abs(sum(d.amount or 0 for d in invs) + sum(d.amount or 0 for d in creds)) < 0.01:
        out["reversed"] = _doc(last(creds))
    return out


def _dmy(iso_day: str) -> str:
    try:
        return date.fromisoformat(iso_day).strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return iso_day or "?"


def _r(v: Optional[float]) -> str:
    """1.0.103: with its sign (a credit memo is negative): "R 1 150.00", "R -1 150.00"."""
    return f"R {v:,.2f}".replace(",", " ") if v is not None else "R ?"


def twin_text(hit: dict, amount: Any = None, company: str = "") -> str:
    """Tess's red: "already posted in SAP: doc 190001638 on 17.08.2026 (R 674.48)" — and, when the amounts differ, both.
    1.0.103: an invoice SAP posted and reversed: "posted and reversed in SAP: doc … (R 1 150.00), reversed by doc … (R -1 150.00)"."""
    co = f" in {hit['company_code']}" if company and hit.get("company_code") and hit["company_code"] != company else ""
    rev = hit.get("reversed")
    if rev:
        return (f"posted and reversed in SAP: doc {hit['doc_number']}{co} on {_dmy(hit.get('posting_date') or '')} ({_r(hit.get('amount'))}), "
                f"reversed by doc {rev['doc_number']} on {_dmy(rev.get('posting_date') or '')} ({_r(rev.get('amount'))})")
    out = f"already posted in SAP: doc {hit['doc_number']}{co} on {_dmy(hit.get('posting_date') or '')} ({_r(hit.get('amount'))})"
    a = _num(amount)
    if a is not None and hit.get("amount") is not None and abs(abs(a) - abs(hit["amount"])) > 0.01:
        a = -abs(a) if hit["amount"] < 0 else abs(a)  # the invoice in the same sign as SAP's document
        out += f"; this {'credit note' if a < 0 else 'invoice'} is {_r(a)}"
    return out


def history(db: Session, company: Any, vendor: Any) -> Optional[ApVendorHistory]:
    co = re.sub(r"\s+", "", K.text(company, 12)).upper()
    vs = _variants([vendor])
    if not co or not vs:
        return None
    return db.query(ApVendorHistory).filter(ApVendorHistory.company_code == co, ApVendorHistory.vendor.in_(vs)).order_by(ApVendorHistory.docs.desc()).first()


def _top(d: Optional[dict]) -> tuple[str, int]:
    items = sorted(((k, n) for k, n in (d or {}).items() if k), key=lambda x: (-x[1], x[0]))
    return items[0] if items else ("", 0)


def recent(db: Session, company: Any, vendor: Any, n: int = 5) -> list[dict]:
    """The vendor's last postings in this company code: date, reference, amount, G/L, cost centre (or profit centre), text."""
    co = re.sub(r"\s+", "", K.text(company, 12)).upper()
    vs = _variants([vendor])
    if not co or not vs:
        return []
    rows = db.query(ApPosted).filter(ApPosted.company_code == co, ApPosted.vendor.in_(vs), ApPosted.doc_type.notin_(SKIP_TYPES)) \
        .order_by(ApPosted.posting_date.desc(), ApPosted.id.desc()).limit(n).all()
    pcs = sorted({ln.get("pc") or "" for r in rows for ln in (r.lines or [])} - {""})  # only these (a company code has thousands)
    known = {(cc, co) for (cc,) in db.query(ApCostCentre.cost_centre).filter(ApCostCentre.company_code == co, ApCostCentre.cost_centre.in_(pcs or ["-"])).all()}
    out = []
    for r in rows:
        main = max(r.lines or [{}], key=lambda ln: abs(ln.get("amount") or 0))
        pc = main.get("pc") or ""
        out.append({"date": r.posting_date, "reference": r.reference, "amount": r.amount, "doc_number": r.doc_number, "doc_type": r.doc_type,
                    "gl": main.get("gl") or "", "cost_centre": pc if (pc, co) in known else "", "profit_centre": pc, "text": r.text or main.get("text") or "",
                    "tax_code": r.tax_code})
    return out


def coding(db: Session, company: Any, vendor: Any) -> Optional[dict]:
    """What SAP's history says about coding this vendor in this company code (None: no history):
    {auto, gl, cost_centre, tax_code, pair_docs, docs, payment_terms, payment_days, split, recent} — `auto` when one G/L +
    cost centre pair covers at least 70 % of at least two documents (Pip may code it, yellow); otherwise gl / cost_centre /
    tax_code are the most used ones (Code this vendor starts from them)."""
    h = history(db, company, vendor)
    if h is None or not h.docs:
        return None
    pair, n = _top(h.pairs)
    gl, cc = (pair.split("|", 1) + [""])[:2] if pair else ("", "")
    auto = bool(gl and cc and h.docs >= LEAST and n / h.docs >= SHARE)
    tc, _ = _top(h.tax_codes)
    terms, _ = _top(h.terms)
    if not auto:
        gl, cc = _top(h.gls)[0], _top(h.cost_centres)[0]
    return {"auto": auto, "gl": gl, "cost_centre": cc, "tax_code": tc, "pair_docs": n if auto else 0, "docs": h.docs, "split": h.split,
            "payment_terms": terms, "payment_days": K.terms_days(terms), "last_posting": h.last_posting, "texts": list(h.texts or []),
            "recent": recent(db, company, vendor)}


def history_note(c: dict) -> str:
    """Tess's yellow: "coded from SAP history (G/L SB205, cost centre XA0001: 5 of 5 invoices) - check it"."""
    return f"coded from SAP history (G/L {c['gl']}, cost centre {c['cost_centre']}: {c['pair_docs']} of {c['docs']} invoices) - check it"


# ---- Mia's pick when one VAT number has many SAP vendors ---------------------------------------------------------------------
def _opened(db: Session, vendors: list[str], company: str) -> Optional[set]:
    """The vendors (as given) SAP has opened in the company code; None when the vendor ↔ company table can't tell."""
    co = re.sub(r"\s+", "", K.text(company, 12)).upper()
    if not co or not vendors or K.vendor_in_company(db, vendors[0], co) is None:
        return None
    back: dict[str, str] = {}
    for v in vendors:
        for x in K.vendor_variants(v):
            back.setdefault(x, v)
    keys = sorted(back)
    hits: set[str] = set()
    for i in range(0, len(keys), 900):
        hits |= {back[v] for (v,) in db.query(ApVendorCompany.vendor).filter(ApVendorCompany.company_code == co, ApVendorCompany.vendor.in_(keys[i:i + 900])).all()}
    return hits


def _posted_counts(db: Session, vendors: list[str], company: str) -> dict[str, tuple[int, str]]:
    """{vendor: (documents posted in the company code, the last posting date)} — invoices and credit memos."""
    back: dict[str, str] = {}
    for v in vendors:
        for x in K.vendor_variants(v):
            back.setdefault(x, v)
    keys = sorted(back)
    out: dict[str, tuple[int, str]] = {}
    for i in range(0, len(keys), 900):
        for v, n, last in db.query(ApPosted.vendor, func.count(ApPosted.id), func.max(ApPosted.posting_date)).filter(
                ApPosted.company_code == company, ApPosted.vendor.in_(keys[i:i + 900]), ApPosted.doc_type.in_(sorted(LEARN_TYPES))).group_by(ApPosted.vendor).all():
            k = back[v]
            n0, l0 = out.get(k, (0, ""))
            out[k] = (n0 + n, max(l0, last or ""))
    return out


def pick(db: Session, rows: list[dict], company: str) -> dict:
    """1.0.103: from the SAP vendors that share a VAT number (or a name): never a blocked or deleted one; those opened in
    the posting company; then the one with posting history there (most documents, then the most recent). → {pick: the
    vendor dict or None, several: [vendor dicts] when a person must pick, blocked: [vendor numbers left out], why}."""
    live = [r for r in rows if not r.get("blocked")]
    blocked = [r["vendor"] for r in rows if r.get("blocked")]
    if not live:
        return {"pick": None, "several": [], "blocked": blocked, "why": "blocked"}
    co = re.sub(r"\s+", "", K.text(company, 12)).upper()
    opened = _opened(db, [r["vendor"] for r in live], co) if co and len(live) > 1 else None
    if opened:
        live = [r for r in live if r["vendor"] in opened]
    if len(live) == 1:
        return {"pick": live[0], "several": [], "blocked": blocked, "why": "opened" if opened else "only"}
    counts_ = _posted_counts(db, [r["vendor"] for r in live], co) if co else {}
    ranked = sorted(live, key=lambda r: (counts_.get(r["vendor"], (0, ""))[0], counts_.get(r["vendor"], (0, ""))[1]), reverse=True)
    best = counts_.get(ranked[0]["vendor"], (0, ""))
    if best[0] and (len(ranked) == 1 or counts_.get(ranked[1]["vendor"], (0, "")) != best):
        return {"pick": ranked[0], "several": [], "blocked": blocked, "why": "history", "docs": best[0]}
    return {"pick": None, "several": sorted(live, key=lambda r: r["vendor"]), "blocked": blocked, "why": "several"}


def several_text(vat: str, several: list) -> str:
    """The yellow when a person must pick: "several SAP vendors share VAT 4380190845: 120120, 125911 … - pick one"."""
    vs = [r["vendor"] for r in several]
    return f"several SAP vendors share VAT {vat}: {', '.join(vs[:6])}{' …' if len(vs) > 6 else ''} - pick one"


def sap_name(db: Session, vendor: Any) -> str:
    hit = K.sap_vendor(db, vendor)
    return hit["name"] if hit else ""


def candidates_out(rows: list[dict], db: Session, company: str) -> list[dict]:
    """The picker's choices: vendor, name, city, account group, documents posted in the company code."""
    counts_ = _posted_counts(db, [r["vendor"] for r in rows], re.sub(r"\s+", "", K.text(company, 12)).upper()) if company else {}
    return [{"vendor": r["vendor"], "name": r.get("name", ""), "city": r.get("city", ""), "account_group": r.get("account_group", ""),
             "docs": counts_.get(r["vendor"], (0, ""))[0], "last": counts_.get(r["vendor"], (0, ""))[1]} for r in rows[:60]]


def seed_sap_vendors(db: Session) -> int:
    """1.0.103: the shipped vendor list (Edwin's Supplier Master) into an empty ap_sap_vendors, bulk, source "seed"."""
    from . import masters as M

    if db.query(ApSapVendor.id).first():
        return 0
    rows, seen = [], set()
    for r in K.seed_sap_vendors():
        v = re.sub(r"\s+", "", r["vendor"])[:20]
        if not v or v in seen:
            continue
        seen.add(v)
        name = r["name"][:160]
        rows.append({"vendor": v, "name": name, "name_key": M.name_key(name)[:160], "vat": K.sap_vat(r["vat"]), "tax1": r["tax1"][:40], "city": r["city"][:60],
                     "country": r["country"][:4].upper(), "blocked": bool(r["blocked"]), "payment_terms": "", "account_group": r["account_group"][:8], "source": "seed"})
    t = ApSapVendor.__table__
    for i in range(0, len(rows), 5000):
        db.execute(t.insert(), rows[i:i + 5000])
    return len(rows)


def cost_centres_added(db: Session) -> int:
    """1.0.103, once: the shipped cost-centre list's rows the table doesn't have yet are added (rows a person uploaded are
    never touched: an existing (cost centre, company code) stays as it is)."""
    from . import masters as M

    have = {(cc, co) for cc, co in db.query(ApCostCentre.cost_centre, ApCostCentre.company_code).all()}
    add = [r for r in K.seed_cost_centres() if (r["cost_centre"], r["company_code"]) not in have]
    if add:
        M._insert_cost_centres(db, add)
    return len(add)


def search_posted(db: Session, q: str = "", limit: int = 50) -> dict:
    """Settings → Posting history: by vendor, reference or document number."""
    total = db.query(func.count(ApPosted.id)).scalar() or 0
    q = (q or "").strip()[:60]
    qy = db.query(ApPosted)
    if q:
        k = ref_key(q)
        qy = qy.filter(or_(ApPosted.vendor.in_(_variants([q])) if re.fullmatch(r"\d{1,10}", q) else ApPosted.id < 0, ApPosted.ref_key == (k or "-"),
                           ApPosted.doc_number == q, ApPosted.text.ilike(f"%{q}%")))
    rows = qy.order_by(ApPosted.posting_date.desc(), ApPosted.id.desc()).limit(max(1, min(int(limit or 50), 500))).all()
    return {"total": total, "rows": [{"id": r.id, "company_code": r.company_code, "vendor": r.vendor, "name": "", "reference": r.reference, "doc_type": r.doc_type,
                                      "doc_number": r.doc_number, "posting_date": r.posting_date, "amount": r.amount, "tax_code": r.tax_code, "text": r.text,
                                      "lines": len(r.lines or [])} for r in rows]}
