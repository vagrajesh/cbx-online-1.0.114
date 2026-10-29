# 1.0.98: from CBX-Connect 2.10.3 cbx/support/sapblock.py
"""2.10.3: SAP in the summary (Edwin, 16 Oct: "if it sees it's SAP, it needs to put it into the summary").

When a ticket is about SAP (the readers say so, SAP's words are in it, or Iris saw an SAP screen), the lead gets an SAP
block after the summary lines, built here in code — never written by a model:

    Transaction: MIRO · Company code: X120 CBRE Excellerate … · Document: 5100012345 (KR Vendor Invoice) · Year 2026 ·
    Posted 12.08.2026 · Vendor 100234 · G/L 6123 Cleaning services · Amount ZAR 42 929.88 · Message F5 702 "…"

The fields come from the readers' answer, then what Iris read on the pictures, then plain rules over the words; each is
checked against CBX's SAP lists (cbx/knowledge.py): a document type, a G/L account or a company code the lists don't
have is shown with "(not in CBX's SAP lists)" — never dropped, never guessed. Only the fields found are shown.
The requester sees only the plain words of what they sent (no names looked up): the block is for the lead.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from sqlalchemy.orm import Session

logger = logging.getLogger("cbx.support.sap")
FIELDS = ("tcode", "company_code", "document_no", "doc_type", "fiscal_year", "posting_date", "vendor", "customer", "gl_accounts", "cost_centre",
          "amount", "currency", "message_id", "message_text", "user", "screen")
NOT_LISTED = "(not in CBX's SAP lists)"
SAP_Q = "Which SAP transaction were you in (for example MIRO or FB60), and is there a document number? A screenshot of the message helps."
# without cbx/knowledge.py (or before it loads): the words that make a ticket SAP's
_LOCAL_WORDS = re.compile(r"\b(?:me2[1-3]n|me5[1-3]n|migo|miro|fb0[1-9]|fb[1-9]0|fb65|fbl[135]n|f110|fk0[1-3]|xk0[1-3]|va0[1-3]|vf0[1-3]|fbra|fs10n|ksb1|se16n"
                          r"|s_alr_\w+|s4 ?hana|sap easy access|message no\.? ?[a-z0-9]{1,3} ?\d{3})\b", re.I)
TCODE = re.compile(r"(?<![\w-])(ME2[1-3]N|ME5[1-3]N|MIGO|MIRO|MR8M|MRBR|FB0[1-9]|FB[1-9]0|FB65|FBL[135]N|F110|FK0[1-3]|XK0[1-3]|VA0[1-3]|VF0[1-3]|FBRA"
                   r"|FS10N|KSB1|SE16N|S_ALR_\d+)(?![\w-])", re.I)
MSG = re.compile(r"(?i)\bmessage\s*(?:no\.?|number|id)?\s*:?\s*([A-Z][A-Z0-9]{0,2})\s?(\d{3})\b")
RULES = {
    "document_no": re.compile(r"(?i)\b(?:document|doc\.?|belnr|invoice document)\s*(?:no\.?|number|#)?\s*:?\s*(\d{8,10})\b"),
    "company_code": re.compile(r"(?i)\b(?:company code|co\.? ?code|cocd)\s*:?\s*([A-Z0-9]{4})\b"),
    "fiscal_year": re.compile(r"(?i)\b(?:fiscal year|year)\s*:?\s*(20\d\d)\b"),
    "posting_date": re.compile(r"(?i)\bposting date\s*:?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})"),
    "vendor": re.compile(r"(?i)\b(?:vendor|supplier|creditor)\s*(?:no\.?|number|account)?\s*:?\s*(\d{5,10})\b"),
    "customer": re.compile(r"(?i)\bcustomer\s*(?:no\.?|number|account)?\s*:?\s*(\d{5,10})\b"),
    "cost_centre": re.compile(r"(?i)\bcost cent(?:re|er)\s*:?\s*([A-Z0-9]{4,12})\b"),
}
GL = re.compile(r"(?i)\bG/?L(?:\s*acc(?:oun)?t)?\s*(?:no\.?|number)?\s*:?\s*([A-Z]{0,4}\d{3,10}[A-Z]{0,6})\b")  # CBX's chart: 6123, JA186
AMOUNT = re.compile(r"\b(ZAR|USD|EUR|GBP|AED|EGP|KES|NGN|MAD|SAR)\s?(\d[\d ,]*(?:\.\d{1,2})?)\b")
ERROR = re.compile(r"(?i)\berror|message no|\bfail(?:s|ed|ing)?\b|can'?t|cannot|won'?t|unable|not (?:working|posting|saving|allowed|possible)|blocked|rejected"
                   r"|stuck|\bproblem\b")
VENDOR_MSG = re.compile(r"(?i)\b(?:vendor|supplier|creditor|account)\b")
NOT_OPEN_MSG = re.compile(r"(?i)\bcompany code\b|not (?:defined|created|opened|maintained)|does(?:n't| not) exist|\bblocked\b")


def _k():
    """cbx/knowledge.py (Builder K) — None until it is there."""
    try:
        from .. import knowledge as K

        return K
    except ImportError:
        return None


def words() -> re.Pattern:
    K = _k()
    if K is not None:
        try:
            return K.sap_words()
        except Exception:  # noqa: BLE001
            logger.debug("sap_words failed", exc_info=True)
    return _LOCAL_WORDS


def mentions_sap(text: str) -> bool:
    return bool(words().search(text or ""))


def about_sap(system: str, text: str, seen: Optional[list]) -> bool:
    """The readers say SAP, SAP's own words are in the ticket, or Iris saw an SAP screen."""
    return system == "SAP" or mentions_sap(text) or any((s or {}).get("system") == "SAP" for s in (seen or []))


def _s(v, n: int = 80) -> str:
    if v is None or isinstance(v, (dict, list, bool)):
        return ""
    return re.sub(r"\s+", " ", str(v)).strip()[:n]


def clean(raw) -> dict:
    """A model's sap object in the shape the desk keeps: known keys, short strings, a list of G/L accounts."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k in FIELDS:
        v = raw.get(k)
        if k == "gl_accounts":
            xs = v if isinstance(v, list) else ([v] if isinstance(v, (str, int)) else [])
            gl = [_s(x, 20) for x in xs if _s(x, 20)][:10]
            if gl:
                out[k] = gl
        elif _s(v, 300 if k == "message_text" else 60):
            out[k] = _s(v, 300 if k == "message_text" else 60)
    if out.get("tcode"):
        out["tcode"] = out["tcode"].upper()
    if out.get("message_id"):
        m = re.fullmatch(r"([A-Za-z][A-Za-z0-9]{0,2})\s?(\d{3})", out["message_id"].replace("Message no.", "").strip())
        out["message_id"] = f"{m.group(1).upper()} {m.group(2)}" if m else out["message_id"]
    return out


def rules(text: str) -> dict:
    """What plain rules find in the words (the requester's and Iris's)."""
    t = text or ""
    out: dict = {}
    K = _k()
    try:  # 2.10.3: CBX's own list of transaction codes (a short code like F-03 only where the words say it is one)
        codes = K.tcodes_in(t) if K is not None and hasattr(K, "tcodes_in") else []
    except Exception:  # noqa: BLE001
        codes = []
    m = TCODE.search(t) if K is None else None
    if codes or m:
        out["tcode"] = codes[0] if codes else m.group(1).upper()
    m = MSG.search(t)
    if m:
        out["message_id"] = f"{m.group(1).upper()} {m.group(2)}"
    for k, rx in RULES.items():
        m = rx.search(t)
        if m:
            out[k] = m.group(1).upper() if k in ("company_code", "cost_centre") else m.group(1)
    gl = list(dict.fromkeys(x.upper() for x in GL.findall(t)))[:10]
    if gl:
        out["gl_accounts"] = gl
    m = AMOUNT.search(t)
    if m:
        out["currency"], out["amount"] = m.group(1).upper(), m.group(2).strip(" ,")
    return out


def merge(*sources) -> dict:
    """The first source that has a field gives it (the readers, then each picture, then the rules); G/L accounts add up."""
    out: dict = {}
    for src in sources:
        src = clean(src)
        for k, v in src.items():
            if k == "gl_accounts":
                out[k] = list(dict.fromkeys(list(out.get(k) or []) + list(v)))[:10]
            elif not out.get(k):
                out[k] = v
    return out


# ---- checked against CBX's SAP lists ---------------------------------------------------------------------------
def _look(fn, *args):
    """(the answer, known) — known None when the lists can't be asked (no cbx/knowledge.py, or it failed)."""
    try:
        got = fn(*args)
    except Exception:  # noqa: BLE001
        logger.debug("knowledge lookup failed", exc_info=True)
        return None, None
    return got, bool(got)


def check(db: Session, f: dict) -> dict:
    """The fields plus what CBX's SAP lists say about them."""
    K = _k()
    out = dict(f)
    if K is None or not f:
        return out
    if f.get("doc_type"):
        got, known = _look(K.doc_type, db, f["doc_type"])
        out["doc_type_known"] = known
        if got:
            out["doc_type_name"] = _s(got.get("description"), 80)
    if f.get("company_code"):
        got, known = _look(K.company, db, f["company_code"])
        out["company_known"] = known
        if got:
            out["company_name"] = _s(got.get("name") or got.get("entity"), 120)
            out["company_code"] = _s(got.get("code"), 12) or f["company_code"]
    if f.get("gl_accounts"):
        names, unknown = {}, []
        chart, _ = _look(getattr(K, "chart_keys", lambda _db: True), db)  # no chart loaded at all: nothing is flagged
        for a in f["gl_accounts"]:
            got, known = _look(K.gl_account, db, a)
            if got:
                names[a] = _s(got.get("name"), 80)
            elif known is False and chart is not None:
                unknown.append(a)
        out["gl_names"], out["gl_unknown"] = names, unknown
    if f.get("cost_centre") and hasattr(K, "cost_centre"):  # 2.10.3 (Builder K): the cost centre with SAP's description
        co = out.get("company_code") or f.get("company_code") or ""
        co = co if co and _look(K.cost_centres_cover, db, co)[0] else None  # a company code SAP's list doesn't cover: any company's match
        got, known = _look(K.cost_centre, db, f["cost_centre"], co)
        if got:
            out["cost_centre_name"] = _s(got.get("description"), 80)
        elif known is False and _look(K.cost_centres_cover, db)[0]:  # no Cost Center List at all: nothing is flagged
            out["cost_centre_known"] = False
    if f.get("vendor") and f.get("company_code") and hasattr(K, "vendor_in_company"):
        msg = f"{f.get('message_text', '')} {f.get('message_id', '')}"
        try:
            opened = K.vendor_in_company(db, f["vendor"], out.get("company_code") or f["company_code"])
        except Exception:  # noqa: BLE001
            opened = None
        # only when SAP's message is about that (or there is no message): "Vendor 120000 · not opened in X120"
        if opened is False and (not msg.strip() or (VENDOR_MSG.search(msg) and NOT_OPEN_MSG.search(msg))):
            out["vendor_note"] = f"not opened in {out.get('company_code') or f['company_code']}"
    return out


def build(db: Session, readers: Optional[dict], seen: Optional[list], text: str) -> dict:
    pics = [(s or {}).get("sap") for s in (seen or [])]
    pic_words = " ".join(f"{(s or {}).get('error', '')} {(s or {}).get('text_seen', '')}" for s in (seen or []))
    return check(db, merge(readers or {}, *pics, rules(f"{text}\n{pic_words}")))


# ---- how it reads -------------------------------------------------------------------------------------------------
def _amount(f: dict) -> str:
    a = f.get("amount", "")
    try:
        n = float(re.sub(r"[ ,]", "", a))
        a = f"{n:,.2f}".replace(",", " ")
    except ValueError:
        pass
    return f"{f.get('currency', '')} {a}".strip()


def rows(sap: Optional[dict]) -> list[tuple[str, str]]:
    """[(label, value)] for the lead: the table in the mail and on the ticket page. Only the fields found."""
    f = sap or {}
    out: list[tuple[str, str]] = []

    def known(v: str, ok) -> str:
        return f"{v} {NOT_LISTED}" if ok is False else v

    if f.get("tcode"):
        out.append(("Transaction", f["tcode"]))
    if f.get("company_code"):
        out.append(("Company code", known(f"{f['company_code']} {f.get('company_name', '')}".strip(), f.get("company_known"))))
    dt = ""
    if f.get("doc_type"):
        dt = f"{f['doc_type']} {f.get('doc_type_name', '')}".strip()
        dt = f"{f['doc_type']}, {NOT_LISTED[1:-1]}" if f.get("doc_type_known") is False else dt
    if f.get("document_no"):
        out.append(("Document", f"{f['document_no']}" + (f" ({dt})" if dt else "")))
    elif dt:
        out.append(("Document type", known(f["doc_type"], False) if f.get("doc_type_known") is False else dt))
    if f.get("fiscal_year"):
        out.append(("Year", f["fiscal_year"]))
    if f.get("posting_date"):
        out.append(("Posted", f["posting_date"]))
    if f.get("vendor"):
        out.append(("Vendor", f["vendor"] + (f" · {f['vendor_note']}" if f.get("vendor_note") else "")))
    if f.get("customer"):
        out.append(("Customer", f["customer"]))
    if f.get("gl_accounts"):
        names, unknown = f.get("gl_names") or {}, set(f.get("gl_unknown") or [])
        out.append(("G/L", ", ".join(f"{a} {NOT_LISTED}" if a in unknown else f"{a} {names.get(a, '')}".strip() for a in f["gl_accounts"])))
    if f.get("cost_centre"):
        out.append(("Cost centre", known(f"{f['cost_centre']} {f.get('cost_centre_name', '')}".strip(), f.get("cost_centre_known"))))
    if f.get("amount"):
        out.append(("Amount", _amount(f)))
    if f.get("message_id") or f.get("message_text"):
        out.append(("Message", " ".join(x for x in (f.get("message_id", ""), f"\"{f['message_text']}\"" if f.get("message_text") else "") if x)))
    if f.get("user"):
        out.append(("User", f["user"]))
    if f.get("screen"):
        out.append(("Screen", f["screen"]))
    return out


NO_COLON = {"Year", "Posted", "Vendor", "Customer", "G/L", "Cost centre", "Amount", "Message", "User"}


def line(sap: Optional[dict]) -> str:
    """'Transaction: MIRO · Company code: X120 … · Document: … · Year 2026 · Posted … · Vendor 100234 · G/L 6123 …'"""
    return " · ".join(f"{k} {v}" if k in NO_COLON else f"{k}: {v}" for k, v in rows(sap))


def said(sap: Optional[dict]) -> str:
    """What the requester sees: the plain words of what they sent — no names looked up, no checks."""
    f = sap or {}
    bits = []
    for k, label in (("tcode", "transaction"), ("company_code", "company code"), ("document_no", "document"), ("doc_type", "document type"),
                     ("fiscal_year", "year"), ("vendor", "vendor"), ("customer", "customer"), ("message_id", "message")):
        if f.get(k):
            bits.append(f"{label} {f[k]}")
    if f.get("gl_accounts"):
        bits.append("G/L " + ", ".join(f["gl_accounts"]))
    return ("SAP: " + " · ".join(bits)) if bits else ""


def needs_question(system: str, sap: Optional[dict], text: str, seen: Optional[list]) -> bool:
    """SAP, an error, but no transaction and no document number, and no screenshot: the desk asks (once)."""
    f = sap or {}
    if system != "SAP" or f.get("tcode") or f.get("document_no") or (seen or []):
        return False
    return bool(ERROR.search(text or "") or any((s or {}).get("error") for s in (seen or [])))
