# 1.0.104: new — the Valuence AP controls' foundation F0a (VALUENCE-AP-GAPS.md §2 F0a and §2b; SPEC-1104 Phase 1)
"""Portfolios: EFM-SA, GENSEC, SHELL, TELKOM, SWIFTNET, EGYPT — each with its company codes, its overrides on top of the
global Fixed and G/L tables Pip uses (Shell posts electricity to SA101, the global default is SA110), and its control
settings (review threshold, variance %, estimate basis …) as plain key/value pairs an admin edits in Settings → Portfolios.

Where a document's portfolio comes from (`for_capture`):
    1. its batch's, when a person picked one in the drop form;
    2. else its utility account's (ApUtilityAccount.portfolio);
    3. else the portfolio that lists its company code (Mia's match);
    4. else the default portfolio (EFM-SA).

Every function takes the session first; a `portfolio` argument may be an ApPortfolio, its code, or None (None reads the
default settings). Later builders add their own settings through `setting(db, portfolio, key, default)`: a key not in
DEFAULTS shows up in Settings → Portfolios with that default, no new code needed."""
from __future__ import annotations

import copy
import json
import re
from datetime import date
from typing import Any, Optional, Union

from sqlalchemy.orm import Session

from ..audit import log
from ..db import utcnow
from ..models import User
from ..models.ap import ApBatch, ApCapture, ApItem, ApPortfolio, ApUtilityAccount

DEFAULT_CODE = "EFM-SA"
SEEDED_KEY = "ap.seeded.portfolios"


class Refused(Exception):
    """A plain-words refusal (the API answers 400 with it)."""


# ---- the settings every portfolio has (the value here unless the portfolio sets its own) --------------------------------------
DEFAULTS: dict[str, Any] = {
    "review_threshold_r": 50000,  # V05: every document at or above this amount is reviewed
    "review_sample_pct": 10,  # V05: plus this % of the rest, at random
    "review_yellow": True,  # 1.0.105: every yellow document (incl. the AI's guesses) is reviewed too
    "variance_pct": 20,  # V07: amount and kWh against the average of the last periods
    "rate_pct": 15,  # V07: effective R/kWh against the account's own history
    "compare_periods": 6,  # V07: how many earlier bills the average uses
    "estimate_basis": "avg6",  # V08: last · avg3 · avg6 · daily · prepaid
    "estimate_escalate_n": 3,  # V08: this many estimates running → escalate to Council Liaison
    "missing_grace_days": 10,  # V10: days past the usual bill day before a bill counts as missing
    "vat_recipient_threshold_r": 5000,  # V06: above this the tax invoice must show the recipient's VAT number
    "unreviewed_vendor_severity": "red",  # V01: an invoice to a vendor not yet independently reviewed
    "vendor_review_from": "2026-10-19",  # V01: vendors created or changed from this day are reviewed (go-live)
    "evidence_checklist": ["SAP upload file", "Review workbook"],  # V12: what each batch must have on file
    "retention_years": {"default": 5},  # V12: years per evidence type ("default": any other type)
    "no_claim_tax_code": "",  # V06: the tax code when VAT is not claimed ("" = the Fixed TaxCodeCredit)
    "approval_matrix": [],  # V14: [{"up_to_r": 4000000, "approver": "SFM"}, {"up_to_r": null, "approver": "KAM"}]
    # 1.0.104 (Builder R: cbx/ap/review.py, vatcheck.py, evidence.py)
    "review_before_upload": True,  # V05: the SAP upload waits while a selected document is not reviewed
    "vat_invalid": "red",  # V06: an invoice that is not a valid tax invoice: red until a person decides · no_claim · off
    "vat_requirements": ["Tax Invoice words", "Supplier name", "Supplier VAT number", "Recipient name", "Recipient VAT number",
                         "Invoice number", "Invoice date", "Description", "VAT amount"],  # V06: what s20 of the VAT Act asks for
    "vat_number_profiles": [],  # V06: utility profiles whose bills print the municipality's VAT number (missing = not valid)
    "evidence_missing_days": 7,  # V12: Needs you raises a folder's missing evidence after this many days
}
# label · help · choices (Settings → Portfolios shows these words beside each key)
DESCRIBE: dict[str, tuple[str, str, tuple]] = {
    "review_threshold_r": ("Review everything from (R)", "Every document at or above this amount is reviewed by a second person.", ()),
    "review_sample_pct": ("Random review sample %", "Plus this share of the other documents, picked at random.", ()),
    "review_yellow": ("Review every yellow", "Yellow documents (the AI's guesses included) also wait for a second person.", ()),
    "variance_pct": ("Bill variance %", "A bill's amount or kWh this far from the average of the earlier bills is checked.", ()),
    "rate_pct": ("Rate variance %", "The rate per kWh this far from the account's own history is checked.", ()),
    "compare_periods": ("Bills to compare", "How many earlier bills the average uses.", ()),
    "estimate_basis": ("Estimate basis", "last month · avg3 · avg6 · daily usage × days · prepaid fixed.", ("last", "avg3", "avg6", "daily", "prepaid")),
    "estimate_escalate_n": ("Estimates before escalating", "This many estimates in a row go to Council Liaison.", ()),
    "missing_grace_days": ("Missing bill: days of grace", "Days past the usual bill day before a bill counts as missing.", ()),
    "vat_recipient_threshold_r": ("Recipient VAT number above (R)", "A tax invoice above this amount must show our VAT number.", ()),
    "unreviewed_vendor_severity": ("Unreviewed new vendor", "red stops the invoice; yellow lets it through with a warning.", ("red", "yellow")),
    "vendor_review_from": ("Review new vendors from", "Vendors SAP created or changed from this day (yyyy-mm-dd) are reviewed.", ()),
    "evidence_checklist": ("Evidence checklist", "One item per line: what each batch must have on file.", ()),
    "retention_years": ("Keep evidence (years)", "One line per type, e.g. default: 5.", ()),
    "no_claim_tax_code": ("Tax code when VAT is not claimed", "Blank = the Fixed TaxCodeCredit.", ()),
    "approval_matrix": ("Payment approval levels", "Up to an amount → who approves (JSON list).", ()),
    # 1.0.104 (Builder R)
    "review_before_upload": ("Downloads wait for the review", "The SAP upload is refused while a document picked for review is not reviewed.", ()),
    "vat_invalid": ("Not a valid tax invoice", "red: a reviewer decides claim or don't claim · no_claim: posted without the VAT claim · off: not checked.",
                    ("red", "no_claim", "off")),
    "vat_requirements": ("What a tax invoice must show", "One per line (s20 of the VAT Act). Remove a line to stop checking it.", ()),
    "vat_number_profiles": ("Utility bills that print the VAT number", "One PDF Studio profile per line: a bill of these without the municipality's VAT number is not a valid tax invoice.", ()),
    "evidence_missing_days": ("Evidence missing: days before Needs you", "A folder uploaded this many days ago with checklist items missing shows in Needs you.", ()),
}
KIND = {"approval_matrix": "json", "vat_number_profiles": "lines"}  # 1.0.104 (Builder R): vat_number_profiles
# keys a later builder asked for through setting() with a default, in this process (Settings → Portfolios shows them too)
_SEEN: dict[str, Any] = {}

SEED: list[dict] = [
    {"code": "EFM-SA", "name": "EFM South Africa", "company_codes": "X143, XA00-XA60", "is_default": True,
     "note": "CBRE Excellerate Facilities Management and the SA entities. The default for anything no other portfolio claims."},
    {"code": "GENSEC", "name": "GENSEC (EPS, Nervada, Holdings)", "company_codes": "Z999, Z102"},
    {"code": "SHELL", "name": "Shell", "company_codes": "T100, T101, T102", "fixed": {"Company": "T100"}, "gl": {"electricity": "SA101"},
     "settings": {"variance_pct": 15, "evidence_checklist": ["SAP upload file", "Review workbook", "Signed payment schedule", "SFM approval",
                                                             "Release audit report", "Proof of payment"]},
     "notes": {"variance_pct": "Flow chart p3 says 15 %, narrative p9 says 20 %: confirm with the Senior FM."},
     "note": "T100 Ops, T101 Capital, T102 other (a bill with no company code posts to T100). SAP client 100 (utilities, the FM creates "
             "vendors) vs client 150 (Vendor Portal). Landlord invoices need the municipal first page."},
    {"code": "TELKOM", "name": "Telkom", "company_codes": "T000",
     "settings": {"variance_pct": 20, "approval_matrix": [{"up_to_r": 4000000, "approver": "SFM"}, {"up_to_r": None, "approver": "KAM"}]},
     "notes": {"approval_matrix": "SFM up to R4m, KAM above (not yet in the DoA)."},
     "note": "DME company code T000. Three clusters, each with its AP Controller. Check-meter sites (BCX). Estimate basis per municipality."},
    {"code": "SWIFTNET", "name": "Swiftnet", "company_codes": "",
     "note": "Managed client: posts in the client's own trial balance and G/L. The Utility Master List is the completeness source."},
    {"code": "EGYPT", "name": "Egypt", "company_codes": "X104", "fixed": {"Currency": "EGP"},
     "note": "EGP; ETA Portal; WHT; HSBCnet. Finance Lead marks Pay / Do Not Pay, then the FD approves."},
]

PortfolioArg = Union[ApPortfolio, str, None]


# ---- seed (from masters.seed: once, into an empty table) ---------------------------------------------------------------------
def seed(db: Session) -> None:
    from .. import compat2 as C2
    from ..models import AppState

    if db.get(AppState, SEEDED_KEY):
        return
    if not db.query(ApPortfolio.id).first():
        for s in SEED:
            db.add(ApPortfolio(code=s["code"], name=s["name"], company_codes=s.get("company_codes", ""), fixed=dict(s.get("fixed") or {}),
                               gl=dict(s.get("gl") or {}), settings=copy.deepcopy(s.get("settings") or {}), notes=dict(s.get("notes") or {}),
                               note=s.get("note", ""), is_default=bool(s.get("is_default")), updated_by="Valuence findings (seed)"))
    C2.state_set(db, SEEDED_KEY, {"at": utcnow().isoformat()})
    db.flush()


# ---- finding a portfolio ---------------------------------------------------------------------------------------------------
def portfolios(db: Session, active_only: bool = True) -> list[ApPortfolio]:
    q = db.query(ApPortfolio)
    if active_only:
        q = q.filter(ApPortfolio.is_active.is_(True))
    return q.order_by(ApPortfolio.id).all()


def get(db: Session, code: PortfolioArg) -> Optional[ApPortfolio]:
    """The portfolio with this code (any case), or None."""
    if isinstance(code, ApPortfolio):
        return code
    c = str(code or "").strip().upper()
    return db.query(ApPortfolio).filter(ApPortfolio.code == c).first() if c else None


def default(db: Session) -> Optional[ApPortfolio]:
    """The default portfolio (EFM-SA): anything nothing else claims."""
    return (db.query(ApPortfolio).filter(ApPortfolio.is_default.is_(True), ApPortfolio.is_active.is_(True)).order_by(ApPortfolio.id).first()
            or get(db, DEFAULT_CODE))


def _code_split(c: str) -> Optional[tuple[str, str]]:
    m = re.fullmatch(r"([A-Z]*)(\d+)", c)
    return (m.group(1), m.group(2)) if m else None


def codes_of(p: ApPortfolio) -> list[str]:
    """The portfolio's company codes as written ("T100", "XA00-XA60")."""
    return [x for x in (t.strip().upper().replace("–", "-").replace(" ", "") for t in re.split(r"[,;\n]+", p.company_codes or "")) if x]


def claims(p: ApPortfolio, company_code: str) -> int:
    """2 = the portfolio lists the code itself, 1 = it is inside one of its ranges (XA00-XA60), 0 = not its code."""
    code = str(company_code or "").strip().upper()
    if not code:
        return 0
    best = 0
    for t in codes_of(p):
        if t == code:
            return 2
        if "-" in t:
            lo, hi = t.split("-", 1)
            a, b, x = _code_split(lo), _code_split(hi), _code_split(code)
            if a and b and x and a[0] == b[0] == x[0] and len(a[1]) == len(b[1]) == len(x[1]) and int(a[1]) <= int(x[1]) <= int(b[1]):
                best = 1
    return best


def for_company(db: Session, company_code: str, fallback: bool = True) -> Optional[ApPortfolio]:
    """The portfolio that lists this company code (an exact code beats a range); else the default (fallback) or None."""
    hits = sorted(((claims(p, company_code), -p.id, p) for p in portfolios(db)), key=lambda x: (x[0], x[1]), reverse=True)
    if hits and hits[0][0]:
        return hits[0][2]
    return default(db) if fallback else None


def initial(b: Optional[ApBatch]) -> str:
    """A new capture's portfolio: its batch's when a person picked one, else "" (Pip sets it once Mia has matched it)."""
    return (b.portfolio or "") if b is not None and b.portfolio_how == "picked" else ""


def _account(db: Session, c: ApCapture) -> Optional[ApUtilityAccount]:
    aid = ((c.match or {}).get("account") or {}).get("id")
    return db.get(ApUtilityAccount, int(aid)) if aid else None


def own_claims(db: Session, c: ApCapture) -> list[tuple[str, ApPortfolio]]:
    """1.0.104 review: the portfolios the document itself names — its utility account's portfolio, the account's company
    code, a company code a person chose, a manual invoice's company code (Mia's match) — as (why, portfolio). A utility
    bill whose account has no company code names none (Mia's Fixed Company is a fallback, not the bill's own)."""
    out: list[tuple[str, ApPortfolio]] = []
    a = _account(db, c) if c.bucket == "utility" else None
    if a is not None and a.portfolio and get(db, a.portfolio) is not None:
        out.append((f"utility account {a.account} is", get(db, a.portfolio)))
    if a is not None and (a.company_code or "").strip():
        p = for_company(db, a.company_code, fallback=False)
        if p is not None:
            out.append((f"company code {a.company_code} (utility account {a.account}) is", p))
    ent = (c.match or {}).get("entity") or {}
    code = ent.get("code") if ent.get("how") == "a person" else (c.company_code if c.bucket != "utility" else "")
    if code:
        p = for_company(db, code, fallback=False)
        if p is not None:
            out.append((f"company code {code} is", p))
    return out


def for_capture(db: Session, c: ApCapture) -> Optional[ApPortfolio]:
    """A document's portfolio (no write): what the document itself names (own_claims: its utility account's portfolio,
    its company code) — a batch's pick never overrides that (1.0.104 review) — else its batch's when picked, else its
    company code's (Mia's Fixed Company for a bill with none), else the default."""
    own = own_claims(db, c)
    if own:
        return own[0][1]
    b = db.get(ApBatch, c.batch_id) if c.batch_id else None
    if b is not None and b.portfolio_how == "picked" and b.portfolio:
        return get(db, b.portfolio) or default(db)
    if c.company_code:
        p = for_company(db, c.company_code, fallback=False)
        if p is not None:
            return p
    return default(db)


def stamp(db: Session, c: ApCapture) -> Optional[ApPortfolio]:
    """for_capture, written on the capture (ApCapture.portfolio)."""
    p = for_capture(db, c)
    c.portfolio = p.code if p is not None else ""
    return p


def for_batch(db: Session, b: ApBatch) -> Optional[ApPortfolio]:
    """The batch's portfolio: the one a person picked; else the one most of its documents fall in; else the default."""
    if b.portfolio_how == "picked" and b.portfolio:
        return get(db, b.portfolio) or default(db)
    from sqlalchemy import func

    rows = (db.query(ApCapture.portfolio, func.count(ApCapture.id)).filter(ApCapture.batch_id == b.id, ApCapture.portfolio.isnot(None), ApCapture.portfolio != "")
            .group_by(ApCapture.portfolio).order_by(func.count(ApCapture.id).desc(), ApCapture.portfolio).all())
    for code, _n in rows:
        p = get(db, code)
        if p is not None:
            return p
    return default(db)


def for_account(db: Session, a: ApUtilityAccount) -> Optional[ApPortfolio]:
    """A utility account's portfolio: its own, else its company code's, else the default."""
    return (get(db, a.portfolio) if a.portfolio else None) or for_company(db, a.company_code or "")


# ---- settings -----------------------------------------------------------------------------------------------------------------
def setting(db: Session, portfolio: PortfolioArg, key: str, default: Any = None) -> Any:
    """The portfolio's value for `key`: its own, else DEFAULTS[key], else `default` (and the key is then offered in
    Settings → Portfolios with that default). A copy: changing it changes nothing stored."""
    p = get(db, portfolio) if portfolio is not None else None
    own = (p.settings or {}) if p is not None else {}
    if key in own:
        return copy.deepcopy(own[key])
    if key in DEFAULTS:
        return copy.deepcopy(DEFAULTS[key])
    _SEEN.setdefault(key, copy.deepcopy(default))
    return copy.deepcopy(default)


def known_keys() -> dict[str, Any]:
    """Every settings key with its default: DEFAULTS, then what later builders asked for."""
    return {**DEFAULTS, **{k: v for k, v in _SEEN.items() if k not in DEFAULTS}}


def settings_of(db: Session, portfolio: PortfolioArg) -> dict[str, Any]:
    """Every setting of the portfolio (its own over the defaults)."""
    p = get(db, portfolio) if portfolio is not None else None
    return {**copy.deepcopy(known_keys()), **copy.deepcopy((p.settings or {}) if p is not None else {})}


# ---- Pip's tables -------------------------------------------------------------------------------------------------------------
def _clean_map(d: Any) -> dict[str, str]:
    return {str(k): str(v).strip() for k, v in (d or {}).items() if str(k).strip()} if isinstance(d, dict) else {}


def fixed_for(db: Session, portfolio: PortfolioArg, fixed: Optional[dict] = None) -> dict:
    """The Fixed table (masters.tables, or the one given) with the portfolio's overrides on top."""
    from . import masters as M

    base = dict(fixed if fixed is not None else M.tables(db)["fixed"])
    p = get(db, portfolio) if portfolio is not None else None
    return {**base, **_clean_map(p.fixed if p is not None else {})}


def gl_for(db: Session, portfolio: PortfolioArg, gl: Optional[dict] = None) -> dict:
    """The GL Codes (charge → G/L) with the portfolio's overrides on top (SHELL: electricity → SA101)."""
    from . import masters as M

    base = dict(gl if gl is not None else M.tables(db)["gl"])
    p = get(db, portfolio) if portfolio is not None else None
    return {**base, **_clean_map(p.gl if p is not None else {})}


def tables_for(db: Session, portfolio: PortfolioArg, tables: Optional[dict] = None) -> dict:
    """masters.tables (or the given tables, with any extra keys such as postdate) under the portfolio's overrides."""
    from . import masters as M

    t = tables if tables is not None else M.tables(db)
    return {**t, "fixed": fixed_for(db, portfolio, t["fixed"]), "gl": gl_for(db, portfolio, t["gl"])}


def pip_tables(db: Session, c: ApCapture, tables: dict) -> dict:
    """Pip's hook (capture.build_one): stamps the document's portfolio and returns the tables it builds with. A utility
    account with no company code of its own posts to the portfolio's Fixed Company when the portfolio sets one."""
    p = stamp(db, c)
    if p is None or not (_clean_map(p.fixed) or _clean_map(p.gl)):
        return tables
    out = tables_for(db, p, tables)
    co = _clean_map(p.fixed).get("Company", "")
    if co and c.bucket == "utility" and ((c.match or {}).get("entity") or {}).get("how") != "a person":
        a = _account(db, c)
        if a is not None and not (a.company_code or "").strip():
            c.company_code = co
    return out


def known_for(db: Session, c: ApCapture, known: dict) -> dict:
    """Tess's hook (capture.test_batch, per document): what the document's own portfolio sets is admin-kept too — its
    G/Ls, its Fixed Company and tax codes, its no-claim tax code. Another portfolio's (SHELL's SA101) is not."""
    p = get(db, c.portfolio) if c.portfolio else for_capture(db, c)
    return with_overrides(db, known, p) if p is not None else known


def with_overrides(db: Session, known: dict, portfolio: PortfolioArg) -> dict:
    """`known` (Tess's known values) with ONE portfolio's overrides added (1.0.104 review: never every portfolio's)."""
    p = get(db, portfolio) if portfolio is not None else None
    if p is None:
        return known
    gls, cos, tax = set(known.get("gls") or set()), set(known.get("companies") or set()), set(known.get("tax_codes") or set())
    gls |= {v for v in _clean_map(p.gl).values() if v}
    fx = _clean_map(p.fixed)
    if fx.get("Company"):
        cos.add(fx["Company"].upper())
    tax |= {fx[k].upper() for k in ("TaxCode", "TaxCodeCredit", "TaxCodeExempt") if fx.get(k)}
    nc = str((p.settings or {}).get("no_claim_tax_code") or "").strip().upper()
    if nc:
        tax.add(nc)
    return {**known, "gls": gls, "companies": cos, "tax_codes": tax}


# ---- what admins change (Settings → Portfolios) -----------------------------------------------------------------------------------
def _who(u: Optional[User]) -> str:
    return (u.name or u.email) if u else ""


def _number(key: str, v: Any) -> Union[int, float]:
    try:
        x = float(str(v).replace(" ", "").replace(",", ""))
    except (TypeError, ValueError):
        raise Refused(f"{label(key)}: give a number.") from None
    if x != x or x in (float("inf"), float("-inf")) or x < 0:
        raise Refused(f"{label(key)}: give a number of zero or more.")
    return int(x) if x.is_integer() else x


def coerce(key: str, value: Any) -> Any:
    """A value an admin typed, in the shape of the key's default (number, words, a list, a table)."""
    d = known_keys().get(key)
    if isinstance(d, bool):
        return str(value).strip().lower() in ("1", "true", "yes", "on") if not isinstance(value, bool) else value
    if isinstance(d, (int, float)):
        return _number(key, value)
    if isinstance(d, str):
        s = re.sub(r"\s+", " ", str(value if value is not None else "")).strip()[:200]
        choices = DESCRIBE.get(key, ("", "", ()))[2]
        if choices and s not in choices:
            raise Refused(f"{label(key)}: one of {', '.join(choices)}.")
        if key.endswith("_from") and s:
            try:
                s = date.fromisoformat(s[:10]).isoformat()
            except ValueError:
                raise Refused(f"{label(key)}: a date as yyyy-mm-dd.") from None
        return s
    if isinstance(d, list):
        if isinstance(value, str):
            t = value.strip()
            if t.startswith("["):
                try:
                    value = json.loads(t)
                except ValueError:
                    raise Refused(f"{label(key)}: not a list.") from None
            else:
                value = [x.strip() for x in t.splitlines() if x.strip()]
        if not isinstance(value, list):
            raise Refused(f"{label(key)}: give a list.")
        return json.loads(json.dumps(value[:200], default=str))
    if isinstance(d, dict):
        if isinstance(value, str):
            t = value.strip()
            if t.startswith("{"):
                try:
                    value = json.loads(t)
                except ValueError:
                    raise Refused(f"{label(key)}: not a table.") from None
            else:
                out = {}
                for line in t.splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        if k.strip():
                            out[k.strip()[:60]] = _number(key, v) if isinstance(next(iter(d.values()), ""), (int, float)) else v.strip()[:200]
                value = out
        if not isinstance(value, dict):
            raise Refused(f"{label(key)}: give a table.")
        return json.loads(json.dumps(dict(list(value.items())[:200]), default=str))
    # a key no builder described: kept as given (JSON; text that reads as JSON is taken as JSON)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = value.strip()[:2000]
    try:
        return json.loads(json.dumps(value, default=str))
    except (TypeError, ValueError):
        raise Refused(f"{key}: can't be stored.") from None


def label(key: str) -> str:
    return DESCRIBE.get(key, (key.replace("_", " ").capitalize(), "", ()))[0]


def _map_in(what: str, d: Any) -> dict:
    if d is None:
        return {}
    if not isinstance(d, dict):
        raise Refused(f"The {what} overrides are a table (key → value).")
    out = {}
    for k, v in d.items():
        k = str(k).strip()
        if not k:
            continue
        if not re.fullmatch(r"[A-Za-z0-9_ .\-]{1,40}", k):
            raise Refused(f"'{k}' is not a key (letters, digits and _).")
        v = str(v if v is not None else "").strip()[:40]
        if v:
            out[k] = v
    return out


def _codes_in(text: Any) -> str:
    toks = [t.strip().upper().replace("–", "-").replace(" ", "") for t in re.split(r"[,;\n]+", str(text or "")) if t.strip()]
    for t in toks:
        if not re.fullmatch(r"[A-Z0-9]{2,12}(-[A-Z0-9]{2,12})?", t):
            raise Refused(f"'{t}' is not a company code (e.g. X143, or a range XA00-XA60).")
        if "-" in t:
            lo, hi = t.split("-", 1)
            a, b = _code_split(lo), _code_split(hi)
            if not (a and b and a[0] == b[0] and len(a[1]) == len(b[1]) and int(a[1]) <= int(b[1])):
                raise Refused(f"'{t}' is not a range: the same letters and as many digits at both ends (XA00-XA60).")
    return ", ".join(dict.fromkeys(toks))


def create(db: Session, user: User, data: dict) -> ApPortfolio:
    if not user.is_admin:
        raise PermissionError("Only an admin adds a portfolio.")
    code = str((data or {}).get("code") or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{1,19}", code):
        raise Refused("A portfolio code is 2 to 20 letters, digits or - (e.g. SHELL).")
    if get(db, code) is not None:
        raise Refused(f"There is a portfolio {code} already.")
    p = ApPortfolio(code=code, name=str(data.get("name") or code).strip()[:160], company_codes="", fixed={}, gl={}, settings={}, notes={}, note="",
                    updated_by=_who(user))
    db.add(p)
    db.flush()
    log(db, "ap_portfolio", actor=user, target=f"ap_portfolio:{code}", detail=f"added {code} {p.name}")
    return save(db, user, code, {k: v for k, v in (data or {}).items() if k != "code"})


def save(db: Session, user: User, code: str, data: dict) -> ApPortfolio:
    """An admin's change to a portfolio: name, company codes, Fixed / G/L overrides, settings (a value of None or "" puts
    a key back to its default), notes. Audited ap_portfolio (what changed); documents not yet uploaded are built again
    when the overrides change."""
    if not user.is_admin:
        raise PermissionError("Only an admin changes a portfolio.")
    p = get(db, code)
    if p is None:
        raise LookupError("No such portfolio.")
    data = data or {}
    said: list[str] = []
    if "name" in data:
        n = str(data.get("name") or "").strip()[:160]
        if not n:
            raise Refused("Give the portfolio a name.")
        if n != p.name:
            said.append(f"name {p.name} → {n}")
            p.name = n
    if "company_codes" in data:
        cc = _codes_in(data.get("company_codes"))
        if cc != (p.company_codes or ""):
            said.append(f"company codes {p.company_codes or '-'} → {cc or '-'}")
            p.company_codes = cc
    rebuild = False
    for what in ("fixed", "gl"):
        if what in data:
            new = _map_in("Fixed" if what == "fixed" else "G/L", data.get(what))
            old = _clean_map(getattr(p, what))
            if new != old:
                said.append(f"{'Fixed' if what == 'fixed' else 'G/L'} {json.dumps(old, ensure_ascii=False)} → {json.dumps(new, ensure_ascii=False)}")
                setattr(p, what, new)
                rebuild = True
    if "settings" in data:
        if not isinstance(data.get("settings"), dict):
            raise Refused("The settings are a table (key → value).")
        own = dict(p.settings or {})
        for k, v in data["settings"].items():
            k = str(k).strip()
            if not re.fullmatch(r"[a-z][a-z0-9_]{1,40}", k):
                raise Refused(f"'{k}' is not a settings key (small letters, digits and _).")
            if v is None or (isinstance(v, str) and not v.strip() and not isinstance(known_keys().get(k), str)):
                if k in own:
                    said.append(f"{k} back to the default")
                    own.pop(k)
                continue
            val = coerce(k, v)
            if val is None:  # "null" for a key with no default: back to nothing of its own
                if k in own:
                    said.append(f"{k} back to the default")
                    own.pop(k)
                continue
            cur = own.get(k, known_keys().get(k))
            if val != cur:
                said.append(f"{k} {json.dumps(cur, ensure_ascii=False, default=str)} → {json.dumps(val, ensure_ascii=False, default=str)}")
            if k in known_keys() and val == known_keys()[k]:
                own.pop(k, None)  # the same as the default: nothing of its own
            else:
                own[k] = val
        p.settings = own
    if "notes" in data:
        if not isinstance(data.get("notes"), dict):
            raise Refused("The notes are a table (key → note).")
        notes = {str(k)[:40]: str(v).strip()[:300] for k, v in data["notes"].items() if str(v or "").strip()}
        if notes != (p.notes or {}):
            said.append("notes")
            p.notes = notes
    if "note" in data:
        n = str(data.get("note") or "").strip()[:2000]
        if n != (p.note or ""):
            said.append("note")
            p.note = n
    if "is_active" in data:
        on = bool(data.get("is_active"))
        if on != p.is_active:
            if not on and p.is_default:
                raise Refused("The default portfolio stays active.")
            said.append("active" if on else "not active")
            p.is_active = on
    if data.get("is_default") and not p.is_default:
        for o in db.query(ApPortfolio).filter(ApPortfolio.is_default.is_(True)).all():
            o.is_default = False
        p.is_default, p.is_active = True, True
        said.append("the default portfolio")
    if said:
        p.updated_by = _who(user)
        db.flush()
        log(db, "ap_portfolio", actor=user, target=f"ap_portfolio:{p.code}", detail="; ".join(said)[:1000])
    if rebuild:
        _rebuild(db, lambda c: (c.portfolio or "") == p.code)
    return p


def _rebuild(db: Session, which) -> int:
    """The documents not uploaded yet that `which` picks go back to Mia (the company code she found comes back too: a
    portfolio's Fixed Company may have replaced it), then Pip builds them again and Tess tests them."""
    from . import capture as C

    n, bids = 0, set()
    for c in db.query(ApCapture).filter(ApCapture.uploaded_at.is_(None), ApCapture.stage.in_(("sap", "test", "ready"))).all():
        if which(c):
            c.stage = "match"
            bids.add(c.batch_id)
            n += 1
    db.flush()
    for bid in sorted(bids):
        b = db.get(ApBatch, bid)
        if b is not None:
            C.advance(db, b, live=False)
    return n


# ---- a batch's portfolio (the drop form; changed later on the SAP sheet) --------------------------------------------------
def pick(db: Session, code: Any) -> str:
    """The drop form's choice: "" (from the company codes) or an active portfolio's code."""
    c = str(code or "").strip().upper()
    if not c or c == "AUTO":
        return ""
    p = get(db, c)
    if p is None or not p.is_active:
        raise Refused(f"{c} is not a portfolio.")
    return p.code


def check_move(db: Session, user: User, b: ApBatch, code: Any) -> str:
    """1.0.104 review: before a batch is put in a portfolio after the drop — refused (plain words) when a document names
    another portfolio itself (own_claims), and the person needs the capturer role in the new portfolio, the old one and
    every portfolio its documents name. Returns the picked code ("" = from the company codes)."""
    from . import roles as R

    new = pick(db, code)
    old = for_batch(db, b)
    need = {x for x in (new, old.code if old else "") if x}
    for c in db.query(ApCapture).filter(ApCapture.batch_id == b.id, ApCapture.uploaded_at.is_(None)).order_by(ApCapture.id).all():
        for why, p in own_claims(db, c):
            if new and p.code != new:
                it = db.get(ApItem, c.item_id)
                name = (it.name.split("/")[-1] if it else f"document {c.id}")
                raise Refused(f"{name}: {why} in {p.code}, not {new}. Its documents stay in their own portfolio; split the folder instead.")
            need.add(p.code)
    for x in sorted(need):
        R.require(db, user, "capturer", x, what=f"Moving a folder in or out of {x}")
    return new


def set_batch(db: Session, user: User, b: ApBatch, code: Any, *, on_drop: bool = False) -> dict:
    """A person picks the batch's portfolio ("" = from each document's account or company code). On a change the
    documents not yet uploaded take it and are built again (audited ap_batch_portfolio)."""
    new = pick(db, code)
    old = (b.portfolio or "") if b.portfolio_how == "picked" else ""
    b.portfolio, b.portfolio_how = (new, "picked") if new else ("", "auto")
    if on_drop:
        if new:
            log(db, "ap_batch_portfolio", actor=user, target=f"ap_batch:{b.id}", detail=f"{b.name}: portfolio {new} (picked in the drop form)")
        return batch_out(db, b)
    if new != old:
        log(db, "ap_batch_portfolio", actor=user, target=f"ap_batch:{b.id}", detail=f"{b.name}: portfolio {old or 'from the company codes'} → {new or 'from the company codes'}")
        for c in db.query(ApCapture).filter(ApCapture.batch_id == b.id, ApCapture.uploaded_at.is_(None)).all():
            stamp(db, c)
        _rebuild(db, lambda c: c.batch_id == b.id)
    return batch_out(db, b)


def batch_out(db: Session, b: ApBatch) -> dict:
    p = for_batch(db, b)
    return {"code": p.code if p else "", "name": p.name if p else "", "picked": b.portfolio_how == "picked"}


def batch_outs(db: Session, batches: list[ApBatch]) -> dict[int, dict]:
    """batch_out for many batches in two reads (the folder list)."""
    from sqlalchemy import func

    ps = {p.code: p for p in portfolios(db, active_only=False)}
    d = default(db)
    auto = [b.id for b in batches if not (b.portfolio_how == "picked" and b.portfolio)]
    most: dict[int, str] = {}
    if auto:
        for bid, code, _n in (db.query(ApCapture.batch_id, ApCapture.portfolio, func.count(ApCapture.id))
                              .filter(ApCapture.batch_id.in_(auto), ApCapture.portfolio.isnot(None), ApCapture.portfolio != "")
                              .group_by(ApCapture.batch_id, ApCapture.portfolio).order_by(func.count(ApCapture.id).desc(), ApCapture.portfolio).all()):
            if code in ps:
                most.setdefault(bid, code)
    out = {}
    for b in batches:
        picked = b.portfolio_how == "picked" and bool(b.portfolio)
        p = ps.get(b.portfolio if picked else most.get(b.id, "")) or d
        out[b.id] = {"code": p.code if p else "", "name": p.name if p else "", "picked": picked}
    return out


# ---- what the pages show -------------------------------------------------------------------------------------------------------
def kind_of(key: str, d: Any) -> str:
    """How Settings → Portfolios edits a key: bool · number · choice · text · lines (one item per line) · table (key: number
    per line) · json."""
    if key in KIND:
        return KIND[key]
    if isinstance(d, bool):
        return "bool"
    if isinstance(d, (int, float)):
        return "number"
    if isinstance(d, str):
        return "choice" if DESCRIBE.get(key, ("", "", ()))[2] else "text"
    if isinstance(d, list) and d and all(isinstance(x, str) for x in d):
        return "lines"
    if isinstance(d, dict) and d and all(isinstance(x, (int, float)) for x in d.values()):
        return "table"
    return "json"


def keys_out() -> list[dict]:
    out = []
    for k, d in known_keys().items():
        lab, hlp, ch = DESCRIBE.get(k, (label(k), "", ()))
        out.append({"key": k, "label": lab, "help": hlp, "choices": list(ch), "default": copy.deepcopy(d), "kind": kind_of(k, d)})
    return out


def out(p: ApPortfolio, full: bool = False) -> dict:
    o = {"code": p.code, "name": p.name, "is_default": bool(p.is_default), "is_active": bool(p.is_active)}
    if full:
        own = dict(p.settings or {})
        o.update(company_codes=p.company_codes or "", fixed=_clean_map(p.fixed), gl=_clean_map(p.gl), note=p.note or "", notes=dict(p.notes or {}),
                 settings={**copy.deepcopy(known_keys()), **copy.deepcopy(own)}, own=sorted(own), updated_by=p.updated_by or "",
                 updated_at=p.updated_at.isoformat(timespec="minutes") if p.updated_at else "")
    return o


def listing(db: Session, full: bool = False) -> list[dict]:
    return [out(p, full) for p in portfolios(db, active_only=not full)]
