# 1.0.98: from CBX-Connect 2.10.3 cbx/ap/masters.py (app_state through cbx/compat2.py; the pull's own GET, _get)
"""The AP master lists (2.10.0, Edwin 15 Oct: "it will match the entities to the master sheet we have; admins can either
pull it from SAP using an API or upload a new Excel spreadsheet").

    entities    the company codes (Stat Company Codes: Company Code | Company Name | Entity Name), seeded once
    utilities   the utilities list in either of the Harvester's shapes: vendor_mappings.xlsx (VENDOR/BP NUMBER, PID/CC,
                NAME1, NAME3, ACCOUNT REFERENCE, MUNICIPALITY/PAYEE) or Utilities_Master_List.xlsx (Supplier, PID,
                Reference, Site Name, …, Vendor Name, …). 1.0.101 (Edwin: "use all I gave you"): the Harvester's list ships
                in cbx/knowledge_data/utilities_accounts.csv and is loaded once into an empty table; an upload replaces it.
    vendors     the vendors of manual invoices: vendor BP, GL, cost centre, tax code; plus what people coded (learned)
    sap         the Harvester's sap_config.xlsx: Fixed (Key, Value) and GL Codes (Charge, GL, Description)
    doc_types   SAP document types (Edwin's table; 2.10.3: the whole of SAP's Document Type List, Edwin's meanings kept)
    gl_accounts      2.10.3: the chart of accounts (G/L account, long text): coding refuses a G/L that is not in it
    vendor_companies 2.10.3: which company codes each SAP vendor is opened in (Vendor | Company Code, ~118 000 pairs)
    cost_centres     2.10.3: SAP's cost centres per company code (Cost Center | Company Code | Currency | Profit Center | Description)
    sites            1.0.102: a bill-to's site code (RIO2X21, DELL) → its cost centre, per company code or any (Site code |
                     Company code | Cost centre | Note)
    sap_vendors      1.0.102: SAP's vendor general data (LFA1: LIFNR, NAME1, STCEG / STCD1 / STCD2, ORT01, LAND1, SPERR, LOEVM,
                     ZTERM — or their English labels): Mia finds a supplier's vendor number by its VAT number

Replacing a list is two steps: `replace(..., confirm=False)` says what would change (added / changed / removed); the
same call with confirm=True saves it. Every change is audited. Rows from a sheet or SAP are data, never instructions.
"""
from __future__ import annotations

import io
import json
import re
from typing import Any, Iterable, Optional

from sqlalchemy.orm import Session

from ..audit import log
from ..db import utcnow
from .. import compat2 as C2
from ..models import AppState, User
from .. import knowledge as K
from ..models.ap import ApCostCentre, ApDocType, ApEntity, ApGlAccount, ApSapValue, ApUtilityAccount, ApVendorCode, ApVendorCompany
from ..models.ap import ApSapVendor, ApSite
from . import sap as P

MAX_MB = 25
MAX_ROWS = 60000
KINDS = ("entities", "utilities", "vendors", "sites")  # 1.0.102: + sites


class Refused(Exception):
    pass


def _who(u: Optional[User]) -> str:
    return (u.name or u.email) if u else ""


# ---- seeds -----------------------------------------------------------------------------------------------------
# Edwin's Stat Company Codes (inputs/Stat Company Codes.xlsx, 30 rows): the starting entity list
COMPANY_CODES = [
    ("X100", "CBRE Excel Mauritius", "Mauritius"), ("X101", "CBRE Excellerate Ltd", "Mauritius"), ("X102", "CBRE Services CO. W.L.L.", "Bahrain"),
    ("X103", "CBRE Excellerate LLC", "Oman"), ("X104", "CBRE Egypt LLC", "Egypt"), ("X105", "CBRE Excellerate FS LLC", "UAE"),
    ("X106", "CBRE EPS Ghana Limited", "Ghana"), ("X108", "EPS Zambia Ltd", "Zambia"), ("X109", "EPS Senegal SARL", "Senegal"),
    ("X110", "CBRE Excellerate Property", "Ivory Coast"), ("X117", "EPS Tanzania", "Tanzania"), ("X119", "Cbre EPS (Botswana)", "Botswana"),
    ("X120", "EPS Rwanda Limited", "Rwanda"), ("X121", "EPS Eswatini (Pty) Ltd", "Swaziland"), ("X122", "EPS Mozambique, LDA", "Mozambique"),
    ("X123", "EPS Cameroon Ltd", "Cameroon"), ("X124", "EPS Uganda Ltd", "Uganda"), ("X125", "CBRE GWS SARL", "Morocco"),
    ("X126", "CBRE Corporate Kenya", "COS Kenya"), ("X129", "Morocco  Adv & Transc", "A&T Morocco"), ("X130", "CBRE Excellerate Algeria", "Algeria"),
    ("X133", "CBRE Excellerate Nigeria", "Nigeria"), ("X135", "Excellerate Property Serv", "Congo"), ("X136", "Excellerate Facilities Ma", "Angola"),
    ("X137", "CBRE Excellerate FM & Con", "Qatar"), ("X138", "CBX Technical Services", "CTS"), ("X140", "CBRE Excellerate Algeria", "Algeria"),
    ("X141", "EPS Mozambique, LDA", "Mozambique"), ("X143", "CBRE Excellerate Faciliti", "EFM SA"), ("X144", "CBRE Property Service Ltd", "Nigeria"),
]
# Edwin's SAP document types (15 Oct): the last four are "liability, as a journal"
DOC_TYPES = [
    ("KR", "Invoice", "invoice", "Liability per SAP"), ("YR", "Invoice", "invoice", "Liability per SAP"),
    ("KG", "Credit memo", "credit", "Liability, reduces it"), ("YG", "Credit memo", "credit", "Liability, reduces it"),
    ("KZ", "Payment", "payment", "Payments per SAP"), ("ZV", "Payment", "payment", "Payments per SAP"),
    ("AB", "Clearing", "clearing", "Liability, as a journal"), ("KA", "Journal", "journal", "Adjustment or correction; liability, as a journal"),
    ("SA", "Journal", "journal", "Adjustment or correction; liability, as a journal"), ("UE", "Go-live migration", "journal", "September 2019 go-live migration; liability, as a journal"),
    ("DR", "Clearing items", "clearing", "Liability, as a journal"),
]
MEANINGS = ("invoice", "credit", "payment", "clearing", "journal", "migration", "other")  # 2.10.3: + migration, other (SAP's types that mean nothing to AP yet)


def _seeded(db: Session, what: str) -> bool:
    return bool(db.get(AppState, f"ap.seeded.{what}"))


def _mark_seeded(db: Session, what: str) -> None:
    C2.state_set(db, f"ap.seeded.{what}", {"at": utcnow().isoformat()})


def seed(db: Session) -> None:
    """At start: the company codes, the document types and the Harvester's SAP values — each once, and only into an
    empty table (an admin's later changes are never undone)."""
    if not _seeded(db, "entities"):
        if not db.query(ApEntity).count():
            for code, name, entity in COMPANY_CODES:
                db.add(ApEntity(code=code, name=name, entity=entity, source="seed", updated_by="Stat Company Codes"))
        _mark_seeded(db, "entities")
    if not _seeded(db, "doc_types"):
        if not db.query(ApDocType).count():
            for code, label, meaning, note in DOC_TYPES:
                db.add(ApDocType(code=code, label=label, meaning=meaning, note=note))
        _mark_seeded(db, "doc_types")
    if not _seeded(db, "sap"):
        if not db.query(ApSapValue).count():
            for k, v in P.SAP_DEFAULT_FIXED:
                db.add(ApSapValue(kind="fixed", key=k, value=v, description=""))
            for k, g, d in P.DEFAULT_GL:
                db.add(ApSapValue(kind="gl", key=k, value=g, description=d))
        _mark_seeded(db, "sap")
    if not _seeded(db, "sap_tax_exempt"):  # 1.0.102: the Fixed value TaxCodeExempt (empty: an exempt line keeps its usual code)
        db.flush()
        if not db.query(ApSapValue).filter(ApSapValue.kind == "fixed", ApSapValue.key == "TaxCodeExempt").first():
            db.add(ApSapValue(kind="fixed", key="TaxCodeExempt", value="", description=""))
        _mark_seeded(db, "sap_tax_exempt")
    db.flush()
    seed_knowledge(db)  # 2.10.3
    from . import portfolio as PF  # 1.0.104: the portfolios (EFM-SA, GENSEC, SHELL, TELKOM, SWIFTNET, EGYPT), once, into an empty table

    PF.seed(db)
    from . import autofill as AF  # 1.0.105 (Builder G): EPS / Nervada (Z999) and Excellerate Holdings (Z102) in the company list, once

    AF.seed_group_entities(db)


_SEEDED = {"done": False}


def ensure_seeded(db: Optional[Session] = None) -> None:
    """1.0.98: seed() once per start — from the first AP request (cbx/api/ap_api.py) or the routine below — never from
    cbx/main.py. Each part goes only into an empty table and is marked in app_state, so an upgrade adds what is new and an
    admin's changes stay. The big lists load in bulk (118 000 vendor pairs in a second or two)."""
    import threading

    if _SEEDED["done"]:
        return
    lock = _SEEDED.setdefault("lock", threading.Lock())
    with lock:
        if _SEEDED["done"]:
            return
        if db is None:
            from ..db import session_scope

            with session_scope() as s:
                seed(s)
        else:
            seed(db)
            db.commit()
        _SEEDED["done"] = True


# ---- small helpers -------------------------------------------------------------------------------------------------
def acct_key(v: Any) -> str:
    """The Harvester's _sap_norm_acct, for a value from Excel too (an int, or a float like 2100090266.0)."""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return P._sap_norm_acct(v)


def name_key(v: Any) -> str:
    n = re.sub(r"[^a-z0-9]", "", str(v or "").lower())
    for _ in range(2):
        n = re.sub(r"(ptyltd|pty|ltd|limited|cc|inc|llc|sarl|lda|wll|plc)$", "", n)
    return n


def vat_key(v: Any) -> str:
    return re.sub(r"\D", "", str(v or ""))[:20]


def _txt(v: Any, n: int) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return re.sub(r"[\r\n\t]+", " ", str(v)).strip()[:n]  # inner spaces stay as given: NAME1 goes into the SAP header as the Harvester wrote it


def _head(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip().lower()


# ---- 1.0.102: a vendor coding's payment days and line rules ---------------------------------------------------------------
RULES_MAX = 30


def days_of(v: Any) -> Optional[int]:
    """Payment days as typed or in a sheet ("30", 30.0, "30 days"); blank or nonsense → None."""
    if v is None or isinstance(v, bool):
        return None
    m = re.fullmatch(r"\s*(\d{1,3})(?:\.0+)?\s*(?:days?)?\s*", str(int(v) if isinstance(v, float) and v.is_integer() else v), re.I)
    n = int(m.group(1)) if m else None
    return n if n is not None and n <= 365 else None


def clean_rules(v: Any) -> list[dict]:
    """Line rules from the forms ([{words, gl, cost_centre, tax_code}]) or a sheet's text ("biokinetic => SC600 / AC0326;
    equipment => SB006"): words and at least one of G/L, cost centre, tax code; at most RULES_MAX."""
    items: list = []
    if isinstance(v, list):
        items = [x for x in v if isinstance(x, dict)]
    elif isinstance(v, str) and v.strip():
        for part in re.split(r"[;\n]+", v):
            if "=>" in part or "→" in part:
                words, rest = re.split(r"=>|→", part, maxsplit=1)
                bits = [b.strip() for b in rest.split("/")] + ["", "", ""]
                items.append({"words": words, "gl": bits[0], "cost_centre": bits[1], "tax_code": bits[2]})
    out = []
    for x in items[:RULES_MAX]:
        r = {"words": re.sub(r"\s+", " ", _txt(x.get("words"), 80)).strip(" ,"), "gl": re.sub(r"\s+", "", _txt(x.get("gl"), 20)),
             "cost_centre": re.sub(r"\s+", "", _txt(x.get("cost_centre"), 40)), "tax_code": re.sub(r"\s+", "", _txt(x.get("tax_code"), 8)).upper()}
        if r["tax_code"] and not re.fullmatch(r"[A-Z0-9]{1,8}", r["tax_code"]):
            r["tax_code"] = ""
        if r["words"] and (r["gl"] or r["cost_centre"] or r["tax_code"]):
            out.append(r)
    return out


def rules_text(rules: Any) -> str:
    """Line rules as one cell of the vendors workbook (the upload reads it back)."""
    out = []
    for r in clean_rules(rules if isinstance(rules, list) else []):
        tail = " / ".join([r["gl"], r["cost_centre"], r["tax_code"]]).rstrip(" /")
        out.append(f"{r['words']} => {tail}")
    return "; ".join(out)


def _said(v: Any) -> Any:
    return rules_text(v) if isinstance(v, list) else v


# ---- what each list holds, and the column names a sheet may give it ---------------------------------------------------
COLUMNS: dict[str, list[tuple[str, tuple[str, ...]]]] = {
    "entities": [("code", ("company code", "code", "company")), ("name", ("company name", "name")), ("entity", ("entity name", "entity")),
                 ("vat_no", ("vat no", "vat number", "vat", "vat registration")), ("aliases", ("aliases", "other names", "bill-to names")),
                 ("doc_type", ("doc type", "document type")), ("doc_type_credit", ("doc type credit", "credit doc type", "document type credit"))],
    "utilities": [("vendor", ("vendor/bp number", "vendor / bp number", "bp number", "vendor number", "vendor", "supplier")),
                  ("cost_centre", ("pid/cc", "pid / cc", "pid", "cost centre", "cost center", "cc")),
                  ("site", ("name1", "site name", "site")), ("name3", ("name3",)),
                  ("account", ("account reference", "reference", "account number", "account no", "account")),
                  ("municipality", ("municipality/payee", "municipality / payee", "vendor name", "municipality", "payee")),
                  ("company_code", ("company code",))],
    "vendors": [("vendor_no", ("vendor/bp number", "vendor no", "vendor number", "bp number", "vendor", "bp", "supplier no", "supplier number")),
                ("name", ("vendor name", "supplier name", "name", "supplier")), ("vat_no", ("vat no", "vat number", "vat")),
                ("gl", ("gl", "gl account", "g/l", "g/l account", "gl code")), ("cost_centre", ("cost centre", "cost center", "cc")),
                ("tax_code", ("tax code", "tax")), ("company_code", ("company code", "company")), ("aliases", ("aliases", "other names")),
                ("item_text", ("line text", "item text", "item_text", "sgtxt", "text")),  # 2.10.3
                ("payment_days", ("payment days", "payment_days", "payment terms (days)")), ("line_rules", ("line rules", "line_rules"))],  # 1.0.102
    # 1.0.102: a bill-to's site code → its cost centre
    "sites": [("site_code", ("site code", "site", "site_code", "client code")), ("company_code", ("company code", "company")),
              ("cost_centre", ("cost centre", "cost center", "cc")), ("note", ("note", "notes", "description"))],
    # 1.0.102: SAP's vendor general data — LFA1's technical names and their English labels
    "sap_vendors": [("vendor", ("lifnr", "vendor", "supplier", "account number of vendor or creditor", "vendor number")),
                    ("name", ("name1", "name 1", "name", "suppliername", "supplier name", "vendor name")), ("name2", ("name2", "name 2")),
                    ("vat", ("stceg", "vat registration no.", "vat registration no", "vat registration number", "vatregistration", "vat number", "vat no")),
                    ("tax1", ("stcd1", "tax number 1", "taxnumber1")), ("tax2", ("stcd2", "tax number 2", "taxnumber2")),
                    ("city", ("ort01", "city", "cityname")), ("country", ("land1", "country", "country key", "country/region key")),  # 1.0.103: + SAP's newer label
                    ("blocked", ("sperr", "central posting block", "posting block", "postingisblocked")),
                    ("deleted", ("loevm", "central deletion flag", "deletion flag", "deletionindicator")),
                    ("payment_terms", ("zterm", "terms of payment", "payment terms", "paymentterms")),
                    # 1.0.103: the account group (KTOKK); the payment block (SPERZ) is read but not a block on posting
                    ("account_group", ("ktokk", "account group")), ("payment_block", ("sperz", "payment block")),
                    # 1.0.104 (Builder D, V01): created on / by, changed on — new and changed vendors are reviewed
                    ("created_on", ("erdat", "created on", "date on which the record was created", "creation date", "date created")),
                    ("created_by", ("ernam", "created by", "name of person who created the object", "name of person who created object")),
                    ("changed_on", ("aedat", "changed on", "last changed on", "date of last change", "changed on date"))],
    # 2.10.3: the two SAP lists that only come from SAP (an upload of SAP's export, or Pull from SAP)
    "gl_accounts": [("account", ("g/l account", "gl account", "account", "saknr")), ("name", ("g/l acct long text", "long text", "name", "description", "txt50"))],
    "vendor_companies": [("vendor", ("vendor", "supplier", "lifnr")), ("company_code", ("company code", "bukrs", "cocd"))],
    "cost_centres": [("cost_centre", ("cost center", "cost centre", "kostl")), ("company_code", ("company code", "bukrs", "cocd")), ("currency", ("currency", "waers")),
                     ("profit_centre", ("profit center", "profit centre", "prctr")), ("description", ("description", "name", "ktext"))],
}
NEEDED = {"entities": ("code",), "utilities": ("account", "vendor"), "vendors": ("vendor_no",), "gl_accounts": ("account",), "vendor_companies": ("vendor", "company_code"),
          "cost_centres": ("cost_centre", "company_code"), "sites": ("site_code", "cost_centre"), "sap_vendors": ("vendor",)}  # 1.0.102
SAP_KINDS = ("gl_accounts", "vendor_companies", "cost_centres")  # 2.10.3
LEN = {"code": 12, "name": 160, "entity": 160, "vat_no": 20, "aliases": 2000, "doc_type": 4, "doc_type_credit": 4, "vendor": 40, "cost_centre": 40,
       "site": 160, "name3": 80, "account": 60, "municipality": 160, "company_code": 12, "vendor_no": 40, "gl": 20, "tax_code": 8, "item_text": 60,
       "site_code": 60, "note": 200, "payment_days": 4, "line_rules": 4000}  # 1.0.102


def map_columns(kind: str, heads: list) -> dict[str, int]:
    """{field: column index} from a sheet's header row; the first column with a name wins (the master list has two PIDs)."""
    norm = [_head(h) for h in heads]
    out: dict[str, int] = {}
    for field, names in COLUMNS[kind]:
        for name in names:  # exact names first, in the order given
            if name in norm and norm.index(name) not in out.values():
                out[field] = norm.index(name)
                break
    return out


class Rows(list):
    """1.0.102: the rows read from a sheet, with `mapping` — {field: the sheet's column name} — for the upload's preview."""
    mapping: dict = {}


def read_sheet(kind: str, data: bytes, filename: str = "") -> list[dict]:
    """The rows of an uploaded Excel (or CSV) list as {field: value}; the header row is found in the first 15 rows.
    1.0.102: the list carries `mapping` (which column went where)."""
    if kind == "sap_vendors":
        return read_sap_vendors(data, filename)
    if kind in SAP_KINDS:  # 2.10.3: SAP's own exports, read the way tools/make_knowledge_data.py reads them
        try:
            if kind == "gl_accounts":
                return K.read_chart(data, filename)
            if kind == "cost_centres":
                return K.read_cost_centres(data, filename)
            return [{"vendor": v, "company_code": c} for v, c in K.read_vendor_companies(data, filename)]
        except K.Refused as e:
            raise Refused(str(e)) from None
    if kind not in KINDS:
        raise Refused("Unknown list.")
    if len(data) > MAX_MB * 1024 * 1024:
        raise Refused(f"The file is over {MAX_MB} MB.")
    rows = _raw_rows(data, filename)
    for i, r in enumerate(rows[:15]):
        cols = map_columns(kind, r)
        if all(f in cols for f in NEEDED[kind]):
            mapped = {_head(r[c]) for c in cols.values()}
            names = {i_: _txt(h, 60) for i_, h in enumerate(r) if _head(h) and _head(h) not in mapped}  # the master list's second PID is the first one again
            out = Rows()
            out.mapping = {f: _txt(r[c], 60) for f, c in cols.items()}
            for raw in rows[i + 1:]:
                rec = {f: raw[c] if c < len(raw) else None for f, c in cols.items()}
                if kind == "utilities":  # the list's other columns are kept beside the row (district, region …)
                    rec["extra"] = {names[j]: _txt(v, 120) for j, v in enumerate(raw) if j in names and v not in (None, "")}
                out.append(rec)
                if len(out) > MAX_ROWS:
                    raise Refused(f"More than {MAX_ROWS} rows: split the list.")
            return out
    want = {"entities": "Company Code", "utilities": "VENDOR/BP NUMBER and ACCOUNT REFERENCE (or Supplier and Reference)", "vendors": "Vendor",
            "sites": "Site code and Cost centre"}[kind]
    raise Refused(f"No header row with {want} in the first 15 rows.")


MAX_COLS = 100


def _trim(r) -> list:
    r = list(r[:MAX_COLS])
    while r and r[-1] in (None, ""):
        r.pop()
    return r


def _keep(it, cut: int, most: int = MAX_ROWS) -> list[list]:
    """2.10.0: rows one at a time, MAX_COLS wide at most; past `cut` rows nothing is kept, and a row with a value there
    means the list is too long. A small file that claims a million rows by 16,384 columns is never built in memory."""
    rows: list[list] = []
    for r in it:
        if len(rows) < cut:
            rows.append(_trim(r))
        elif any(v not in (None, "") for v in r[:MAX_COLS]):
            raise Refused(f"More than {most} rows: split the list.")
    while rows and not rows[-1]:
        rows.pop()
    return rows


def _raw_rows(data: bytes, filename: str) -> list[list]:
    if filename.lower().endswith(".csv"):
        import csv

        text = data.decode("utf-8-sig", "replace")
        try:
            return _keep(csv.reader(io.StringIO(text)), MAX_ROWS + 20)
        except csv.Error as e:
            raise Refused(f"The file could not be read as CSV ({type(e).__name__}).") from None
    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:  # noqa: BLE001
        raise Refused(f"The file could not be read as Excel ({type(e).__name__}).") from None
    try:
        return _keep(wb.active.iter_rows(values_only=True, max_col=MAX_COLS), MAX_ROWS + 20)
    except Refused:
        raise
    except Exception as e:  # noqa: BLE001 — 2.10.0: a broken sheet is "the file could not be read", not a 500
        raise Refused(f"The file could not be read as Excel ({type(e).__name__}).") from None
    finally:
        wb.close()


# ---- clean rows per list -----------------------------------------------------------------------------------------
def _clean(kind: str, rec: dict) -> Optional[dict]:
    r = {f: _txt(rec.get(f), LEN.get(f, 160)) for f, _ in COLUMNS[kind]}
    if kind == "entities":
        r["code"] = r["code"].upper()
        if not re.fullmatch(r"[A-Z0-9]{2,12}", r["code"]):
            return None
        r["vat_no"] = vat_key(r["vat_no"])
        r["doc_type"], r["doc_type_credit"] = r["doc_type"].upper()[:4], r["doc_type_credit"].upper()[:4]
        r["aliases"] = "\n".join(a.strip() for a in re.split(r"[\n;|]", str(rec.get("aliases") or "")) if a.strip())[:2000]
        return r
    if kind == "utilities":
        r["account_key"] = acct_key(rec.get("account"))
        if not r["account_key"]:
            return None  # the Harvester skipped a row with no account too
        r["account_key"] = r["account_key"][:40]
        r["company_code"] = r["company_code"].upper()
        r["extra"] = rec.get("extra") or {}
        return r
    if kind == "sites":  # 1.0.102
        r["site_code"] = re.sub(r"\s+", " ", r["site_code"]).upper()
        r["company_code"], r["cost_centre"] = r["company_code"].upper().replace(" ", ""), r["cost_centre"].replace(" ", "")
        return r if r["site_code"] and r["cost_centre"] else None
    r["vat_no"] = vat_key(r["vat_no"])
    r["payment_days"] = days_of(rec.get("payment_days"))  # 1.0.102
    r["line_rules"] = clean_rules(rec.get("line_rules"))
    r["name_key"] = name_key(r["name"])[:160]
    r["company_code"] = r["company_code"].upper()
    r["tax_code"] = r["tax_code"].upper()
    if not (r["vendor_no"] or r["vat_no"] or r["name_key"]):
        return None
    return r


def _key(kind: str, r: dict) -> tuple:
    if kind == "entities":
        return (r["code"],)
    if kind == "sites":  # 1.0.102
        return (r["site_code"], r["company_code"] or "")
    if kind == "utilities":
        return (r["account_key"], r["vendor"])
    return (r["vendor_no"] or "", r["vat_no"] or "", "" if (r["vendor_no"] or r["vat_no"]) else r["name_key"], r.get("company_code") or "")


SAME = {"entities": ("name", "entity", "vat_no", "aliases", "doc_type", "doc_type_credit"),
        "utilities": ("account", "cost_centre", "site", "name3", "municipality", "company_code"),
        "vendors": ("name", "aliases", "gl", "cost_centre", "tax_code", "payment_days", "line_rules"), "sites": ("cost_centre", "note")}  # 1.0.102
MODEL = {"entities": ApEntity, "utilities": ApUtilityAccount, "vendors": ApVendorCode, "sites": ApSite}


def _row_of(kind: str, o) -> dict:
    d = {f: getattr(o, f) or "" for f, _ in COLUMNS[kind]}
    if kind == "utilities":
        d["account_key"] = o.account_key
    if kind == "vendors":
        d["name_key"] = o.name_key
    return d


def replace(db: Session, user: User, kind: str, records: Iterable[dict], *, source: str, confirm: bool) -> dict:
    """Replace a list with these rows. confirm=False: only what would change. Learned vendor rows (people's answers from
    Needs you) are kept unless the new list carries the same vendor."""
    if not user.is_admin:
        raise PermissionError("Only an admin changes the AP lists.")
    if kind == "gl_accounts":  # 2.10.3
        return replace_gl(db, user, records, source=source, confirm=confirm)
    if kind == "vendor_companies":
        return replace_vendor_companies(db, user, records, source=source, confirm=confirm)
    if kind == "cost_centres":
        return replace_cost_centres(db, user, records, source=source, confirm=confirm)
    if kind == "sap_vendors":  # 1.0.102
        return replace_sap_vendors(db, user, records, source=source, confirm=confirm)
    if kind not in KINDS:
        raise Refused("Unknown list.")
    records = list(records)
    # 2.10.0: only the columns the sheet (or the pull) has are written: a 3-column company list keeps the VAT numbers,
    # the taught names and the YR/YG document types; a vendor sheet without Aliases keeps the aliases
    present = {f for rec in records if isinstance(rec, dict) for f in rec}
    new: dict[tuple, dict] = {}
    skipped = repeated = 0
    for rec in records:
        r = _clean(kind, rec)
        if r is None:
            skipped += 1
            continue
        k = _key(kind, r)
        repeated += k in new
        new[k] = r  # a repeated row: the last one wins, as in the Harvester
    if not new:
        raise Refused("No usable rows in it: nothing would be left in the list.")
    model = MODEL[kind]
    old, twins = {}, []
    for o in db.query(model).all():
        k = _key(kind, _row_of(kind, o))
        if k in old:
            twins.append(o)  # the same row twice in the table: the replace tidies it
        else:
            old[k] = o
    keep_learned = {k for k, o in old.items() if kind == "vendors" and o.learned and k not in new}
    for k, r in new.items():
        if k in old and "aliases" in r:  # 2.10.0: names people taught are merged in, never dropped by a sheet
            r["aliases"] = _merge_names(old[k].aliases or "", r["aliases"] if "aliases" in present else "")
    diffs = {k: [(f, _row_of(kind, old[k]).get(f) or "", new[k].get(f) or "") for f in SAME[kind]
                 if (f in present or f == "aliases") and (_row_of(kind, old[k]).get(f) or "") != (new[k].get(f) or "")] for k in new if k in old}
    added = [k for k in new if k not in old]
    changed = [k for k in new if k in old and diffs[k]]
    removed = [k for k in old if k not in new and k not in keep_learned]
    out = {"kind": kind, "rows": len(new), "added": len(added), "changed": len(changed), "removed": len(removed), "kept_learned": len(keep_learned),
           "skipped": skipped, "repeated": repeated, "saved": False,
           "sample": {"added": [_show(kind, new[k]) for k in added[:5]], "changed": [_show(kind, new[k]) for k in changed[:5]],
                      "removed": [_show(kind, _row_of(kind, old[k])) for k in removed[:5]]},
           "changes": [{"row": _show(kind, _row_of(kind, old[k])), "fields": [{"field": f, "from": _said(a), "to": _said(b)} for f, a, b in diffs[k]]} for k in changed[:20]]}  # 2.10.0: what changes, field by field
    if kind == "sites":  # 1.0.102: cost centres SAP's Cost Center List doesn't have in that company code (Tess will say so)
        out["cc_unknown"] = [_show(kind, r) for r in new.values() if r["company_code"] and K.cost_centre_in_company(db, r["cost_centre"], r["company_code"]) in ("no", "dummy")][:20]
    if not confirm:
        return out
    who = _who(user)
    for k in removed:
        db.delete(old[k])
    for o in twins:
        db.delete(o)
    for k, r in new.items():
        o = old.get(k)
        if o is None:
            o = model()
            db.add(o)
        elif k not in changed:
            continue
        for f, v in r.items():
            if hasattr(o, f) and (o.id is None or f in present or f in ("account_key", "aliases") or (f == "name_key" and "name" in present)):  # 2.10.0: a column the sheet doesn't have is left as it is
                setattr(o, f, v)
        o.source = source
        if hasattr(o, "updated_by"):
            o.updated_by = who
        if kind == "vendors":
            o.learned, o.learned_by, o.learned_at = False, "", None
    db.flush()
    log(db, "ap_list_replace", actor=user, target=f"ap:{kind}", detail=f"from {source}: {len(added)} added, {len(changed)} changed, {len(removed)} removed")
    K.forget()
    out["saved"] = True
    return out


def _merge_names(old: str, new: str) -> str:
    """2.10.0: the names already kept, then the sheet's new ones (one per line, the same name once)."""
    out, seen = [], set()
    for a in (old or "").splitlines() + re.split(r"[\n;|]", new or ""):
        a = a.strip()
        if a and name_key(a) not in seen:
            seen.add(name_key(a))
            out.append(a)
    return "\n".join(out)[:2000]


def _show(kind: str, r: dict) -> str:
    if kind == "entities":
        return f"{r.get('code')} {r.get('name')}".strip()
    if kind == "utilities":
        return f"{r.get('account')} → {r.get('vendor')} {r.get('site')}".strip()
    if kind == "sites":  # 1.0.102
        return f"{r.get('site_code')} · {r.get('company_code') or 'any company'} → {r.get('cost_centre')}"
    return f"{r.get('vendor_no')} {r.get('name')} {r.get('gl')}".strip()


# ---- one row at a time (Settings tables, Needs you answers) ----------------------------------------------------------
def save_entity(db: Session, user: User, data: dict, eid: Optional[int] = None) -> ApEntity:
    if not user.is_admin:
        raise PermissionError("Only an admin changes the company codes.")
    r = _clean("entities", data)
    if r is None:
        raise Refused("A company code is letters and digits (e.g. X143).")
    dup = db.query(ApEntity).filter(ApEntity.code == r["code"]).first()
    e = db.get(ApEntity, eid) if eid else dup
    if eid and e is None:
        raise Refused("No such company code.")
    if dup is not None and e is not None and dup.id != e.id:
        raise Refused(f"{r['code']} is already in the list.")
    if e is None:
        e = ApEntity(code=r["code"])
        db.add(e)
    for f in ("code", "name", "entity", "vat_no", "aliases", "doc_type", "doc_type_credit"):
        setattr(e, f, r[f])
    for f in ("doc_type", "doc_type_credit"):
        if getattr(e, f) and not db.query(ApDocType).filter(ApDocType.code == getattr(e, f)).first():
            raise Refused(f"{getattr(e, f)} is not one of the document types.")
    e.is_active = bool(data.get("is_active", True))
    e.source, e.updated_by = "person", _who(user)
    db.flush()
    log(db, "ap_entity", actor=user, target=f"ap_entity:{e.code}", detail=f"{e.name}; {e.entity}; VAT {e.vat_no or '-'}; {e.doc_type or 'KR'}/{e.doc_type_credit or 'KG'}")
    K.forget()
    return e


def delete_row(db: Session, user: User, kind: str, rid: int) -> None:
    if not user.is_admin:
        raise PermissionError("Only an admin changes the AP lists.")
    model = {"entities": ApEntity, "utilities": ApUtilityAccount, "vendors": ApVendorCode, "doc_types": ApDocType, "gl_accounts": ApGlAccount, "sites": ApSite}.get(kind)  # 1.0.102: + sites
    o = db.get(model, rid) if model else None
    if o is None:
        raise Refused("No such row.")
    log(db, "ap_list_row_deleted", actor=user, target=f"ap:{kind}", detail=json.dumps(_brief(o))[:400])
    db.delete(o)
    db.flush()
    K.forget()


def _brief(o) -> dict:
    return {c.name: str(getattr(o, c.name) or "")[:60] for c in o.__table__.columns if c.name in ("code", "name", "account", "vendor", "vendor_no", "gl", "label", "description",
                                                                                              "site_code", "company_code", "cost_centre")}


def save_vendor(db: Session, user: User, data: dict, vid: Optional[int] = None) -> ApVendorCode:
    """An admin adds or edits one vendor row in Settings (a list row, not a learned one)."""
    if not user.is_admin:
        raise PermissionError("Only an admin changes the vendor list.")
    r = _clean("vendors", data)
    if r is None or not r["vendor_no"]:
        raise Refused("Give the vendor (BP) number.")
    r["gl"] = in_chart(db, r["gl"])  # 2.10.3
    r["line_rules"] = [{**x, "gl": in_chart(db, x["gl"])} for x in r["line_rules"]]  # 1.0.102
    v = db.get(ApVendorCode, vid) if vid else None
    if vid and v is None:
        raise Refused("No such vendor.")
    if v is None:
        v = ApVendorCode()
        db.add(v)
    for f in ("vendor_no", "name", "name_key", "vat_no", "gl", "cost_centre", "tax_code", "company_code", "aliases", "item_text", "payment_days", "line_rules"):
        setattr(v, f, r[f])
    v.learned, v.learned_by, v.learned_at, v.source = False, "", None, "person"
    db.flush()
    log(db, "ap_vendor", actor=user, target=f"ap_vendor:{v.vendor_no}", detail=f"{v.name}: GL {v.gl or '-'}, cost centre {v.cost_centre or '-'}, tax {v.tax_code or '-'}"
        + (f", {v.payment_days} days" if v.payment_days is not None else "") + (f"; line rules: {rules_text(v.line_rules)}" if v.line_rules else ""))
    return v


def add_alias(db: Session, user: User, code: str, alias: str) -> Optional[ApEntity]:
    """A person said a bill-to name belongs to a company code (Needs you → Other): remembered as one of its names."""
    e = db.query(ApEntity).filter(ApEntity.code == code).first()
    alias = _txt(alias, 160)
    if e is None or not alias or name_key(alias) in {name_key(x) for x in [e.name, e.entity, *e.aliases.splitlines()]}:
        return e
    e.aliases = ((e.aliases + "\n") if e.aliases else "") + alias
    e.updated_by = _who(user)
    log(db, "ap_entity_alias", actor=user, target=f"ap_entity:{e.code}", detail=f"learned the bill-to name '{alias}'")
    return e


def add_account(db: Session, user: User, data: dict) -> ApUtilityAccount:
    """A person added a utility account from Needs you (Other → continue)."""
    r = _clean("utilities", {**data, "extra": {}})
    if r is None or not r["vendor"]:
        raise Refused("Give the account number and its vendor (BP) number.")
    # 2.10.0: added by a person = learned (Tess: yellow) until an admin confirms it. 1.0.104 (Valuence F0b): an admin's own
    # addition too — a second admin confirms it (roles.check_confirm refuses the person who added it)
    a = ApUtilityAccount(**{k: v for k, v in r.items()}, source="person", updated_by=_who(user))
    db.add(a)
    db.flush()
    log(db, "ap_utility_account", actor=user, target=f"ap_account:{a.account_key}", detail=f"vendor {a.vendor}, PID/CC {a.cost_centre}, {a.site}")
    return a


def learn_vendor(db: Session, user: User, data: dict) -> ApVendorCode:
    """Needs you → Code this vendor: the answer goes into the vendor table as learned (who, when) and is used from then on."""
    r = _clean("vendors", data)
    if r is None or not r["vendor_no"]:
        raise Refused("Give the vendor (BP) number.")
    if not r["gl"]:
        raise Refused("Give the GL account.")
    r["gl"] = in_chart(db, r["gl"])  # 2.10.3: never a G/L that is not in the chart of accounts
    r["line_rules"] = [{**x, "gl": in_chart(db, x["gl"])} for x in r["line_rules"]]  # 1.0.102
    v = None
    mine = ApVendorCode.learned.is_(True)  # 2.10.0: a person's coding only ever adds or updates a learned row, never a row an admin keeps
    if r["vat_no"]:
        v = db.query(ApVendorCode).filter(ApVendorCode.vat_no == r["vat_no"], ApVendorCode.company_code == r["company_code"], mine).first()
    if v is None and r["name_key"]:
        v = db.query(ApVendorCode).filter(ApVendorCode.name_key == r["name_key"], ApVendorCode.company_code == r["company_code"], mine).first()
    if v is None:
        v = ApVendorCode()
        db.add(v)
    for f in ("vendor_no", "name", "name_key", "vat_no", "gl", "cost_centre", "tax_code", "company_code"):
        if r.get(f) or f in ("gl", "cost_centre", "tax_code", "vendor_no"):
            setattr(v, f, r[f])
    v.payment_days, v.line_rules = r["payment_days"], r["line_rules"]  # 1.0.102
    if r["aliases"]:
        v.aliases = r["aliases"]
    v.learned, v.learned_by, v.learned_at, v.source = True, _who(user), utcnow(), "person"
    db.flush()
    log(db, "ap_vendor_learned", actor=user, target=f"ap_vendor:{v.vendor_no}", detail=f"{v.name}: GL {v.gl}, cost centre {v.cost_centre or '-'}, tax {v.tax_code or '-'}"
        + (f", {v.payment_days} days" if v.payment_days is not None else "") + (f"; line rules: {rules_text(v.line_rules)}" if v.line_rules else ""))
    return v


def confirm_row(db: Session, user: User, kind: str, rid: int):
    """2.10.0: an admin confirms what a person added in Needs you: a learned vendor row, or an account added from Other.
    It then counts as part of the lists (Tess no longer marks the documents that use it yellow)."""
    if not user.is_admin:
        raise PermissionError("Only an admin confirms what people added.")
    model = {"vendors": ApVendorCode, "utilities": ApUtilityAccount}.get(kind)
    if model is None:
        raise Refused("Unknown list.")
    o = db.get(model, int(rid or 0))
    if o is None:
        raise Refused("No such row.")
    if kind == "vendors" and o.learned:
        o.learned = False
        log(db, "ap_vendor_confirmed", actor=user, target=f"ap_vendor:{o.vendor_no}", detail=f"{o.name}: GL {o.gl or '-'}, tax {o.tax_code or '-'} (learned from {o.learned_by or '?'})")
    elif kind == "utilities" and o.source == "person":
        o.source = "admin"
        log(db, "ap_utility_account_confirmed", actor=user, target=f"ap_account:{o.account_key}", detail=f"vendor {o.vendor}, PID/CC {o.cost_centre} (added by {o.updated_by or '?'})")
        o.updated_by = _who(user)
    db.flush()
    return o


# ---- the SAP values (Fixed + GL Codes) ----------------------------------------------------------------------------------
def tables(db: Session) -> dict:
    """{fixed: {key: value}, gl: {charge: GL}} — the Harvester's load_sap_tables, from the database (defaults under it)."""
    fixed = {**dict(P.SAP_DEFAULT_FIXED), "TaxCodeExempt": ""}  # 1.0.102: TaxCodeExempt, empty = an exempt line keeps its usual code
    gl: dict[str, str] = {}
    rows = db.query(ApSapValue).all()
    if not any(r.kind == "gl" for r in rows) and not _seeded(db, "sap"):  # 2.10.0: only before the first seed; a GL table an admin emptied stays empty (red, never a guess)
        gl = {c: g for c, g, _d in P.DEFAULT_GL}
    for r in rows:
        if r.kind == "fixed":
            fixed[r.key] = r.value or ""
        elif r.kind == "gl":
            gl[r.key] = (r.value or "").strip()
    return {"fixed": fixed, "gl": gl}


def sap_values(db: Session) -> dict:
    rows = db.query(ApSapValue).order_by(ApSapValue.id).all()
    return {"fixed": [{"key": r.key, "value": r.value} for r in rows if r.kind == "fixed"],
            "gl": [{"key": r.key, "value": r.value, "description": r.description} for r in rows if r.kind == "gl"]}


def save_sap_values(db: Session, user: User, fixed: list, gl: list, *, confirm: bool = True, source: str = "person") -> dict:
    """Replace both tables (from the Settings page or an uploaded sap_config.xlsx). Keys are kept as typed; a GL may be blank."""
    if not user.is_admin:
        raise PermissionError("Only an admin changes the SAP values.")
    new: dict[tuple, tuple] = {}
    for kind, rows in (("fixed", fixed or []), ("gl", gl or [])):
        for r in rows:
            k = _txt((r or {}).get("key"), 40)
            if not k:
                continue
            if not re.fullmatch(r"[A-Za-z0-9_ .\-]{1,40}", k):
                raise Refused(f"'{k}' is not a key (letters, digits and _).")
            new[(kind, k)] = (_txt(r.get("value"), 40), _txt(r.get("description"), 160))
    if not any(k[0] == "fixed" for k in new):
        raise Refused("The Fixed table can't be empty.")
    old = {(r.kind, r.key): r for r in db.query(ApSapValue).all()}
    added = [k for k in new if k not in old]
    changed = [k for k in new if k in old and (old[k].value or "", old[k].description or "") != new[k]]
    removed = [k for k in old if k not in new]
    out = {"added": len(added), "changed": len(changed), "removed": len(removed), "saved": False}
    if not confirm:
        return out
    for k in removed:
        db.delete(old[k])
    for k in added + changed:
        o = old.get(k) or ApSapValue(kind=k[0], key=k[1])
        o.value, o.description = new[k]
        db.add(o)
    db.flush()
    log(db, "ap_sap_values", actor=user, target="ap:sap", detail=f"from {source}: " + ", ".join(f"{k[1]}={new[k][0] or '(blank)'}" for k in added + changed)[:380])
    out["saved"] = True
    return out


def read_sap_config(data: bytes) -> tuple[list, list]:
    """An uploaded sap_config.xlsx: the Fixed sheet (Key, Value) and the GL Codes sheet (Charge, GL, Description)."""
    import openpyxl

    if len(data) > MAX_MB * 1024 * 1024:
        raise Refused(f"The file is over {MAX_MB} MB.")
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:  # noqa: BLE001
        raise Refused(f"The file could not be read as Excel ({type(e).__name__}).") from None
    try:
        if "Fixed" not in wb.sheetnames or "GL Codes" not in wb.sheetnames:
            raise Refused("It needs the two sheets of the Harvester's sap_config.xlsx: Fixed and GL Codes.")
        # 2.10.0: one, two or three columns, read lazily and capped: a one-column sheet or a sheet claiming a million rows is not a 500
        cell = lambda r, i: r[i] if len(r) > i and r[i] is not None else ""  # noqa: E731
        fixed = [{"key": r[0], "value": cell(r, 1)} for r in _keep(wb["Fixed"].iter_rows(min_row=2, values_only=True, max_col=3), 1000, 1000) if r and r[0]]
        gl = [{"key": r[0], "value": cell(r, 1), "description": cell(r, 2)} for r in _keep(wb["GL Codes"].iter_rows(min_row=2, values_only=True, max_col=3), 1000, 1000) if r and r[0]]
    except Refused:
        raise
    except Exception as e:  # noqa: BLE001
        raise Refused(f"The file could not be read as Excel ({type(e).__name__}).") from None
    finally:
        wb.close()
    return fixed, gl


def sap_config_file(db: Session) -> bytes:
    """The Harvester's sap_config.xlsx, from what is saved here."""
    import openpyxl

    from .. import xlsafe

    v = sap_values(db)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Fixed"
    ws.append(["Key", "Value"])
    for r in v["fixed"]:
        ws.append([r["key"], r["value"]])
    g = wb.create_sheet("GL Codes")
    g.append(["Charge", "GL", "Description"])
    for r in v["gl"]:
        g.append([r["key"], r["value"], r["description"]])
    xlsafe.book(wb)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---- document types -----------------------------------------------------------------------------------------------
def doc_types(db: Session) -> list[dict]:
    # 2.10.3: Edwin's rows first (in his order), then SAP's others by code; each with what SAP says and whether it allows vendor lines
    rows = sorted(db.query(ApDocType).all(), key=lambda d: (d.source == "sap", d.code if d.source == "sap" else "", d.id))
    return [{"id": d.id, "code": d.code, "label": d.label, "meaning": d.meaning, "note": d.note, "description": d.description, "account_types": d.account_types,
             "number_range": d.number_range, "reverse": d.reverse, "source": d.source, "vendor_lines": "K" in (d.account_types or ""),
             "needs_check": bool(d.needs_check), "check_note": d.check_note, "checked_by": d.checked_by,
             "checked_at": d.checked_at.isoformat(timespec="minutes") if d.checked_at else ""} for d in rows]


def doc_type_map(db: Session) -> dict[str, str]:
    """{code: meaning} — Rae tells invoices, credits, payments and journals apart by it."""
    return {d.code.upper(): d.meaning for d in db.query(ApDocType).all()}


def save_doc_type(db: Session, user: User, data: dict, did: Optional[int] = None) -> ApDocType:
    if not user.is_admin:
        raise PermissionError("Only an admin changes the document types.")
    code = _txt(data.get("code"), 4).upper()
    if not re.fullmatch(r"[A-Z0-9]{2,4}", code):
        raise Refused("A document type is two to four letters (e.g. KR).")
    meaning = str(data.get("meaning") or "").strip().lower()
    if meaning not in MEANINGS:
        raise Refused("Say what it is: invoice, credit, payment, clearing or journal.")
    d = db.get(ApDocType, did) if did else None
    if did and d is None:
        raise Refused("No such document type.")
    other = db.query(ApDocType).filter(ApDocType.code == code).first()
    if other is not None and (d is None or other.id != d.id):
        raise Refused(f"{code} is already in the list.")
    if d is None:
        d = ApDocType(code=code)
        db.add(d)
    d.code, d.label, d.meaning, d.note = code, _txt(data.get("label"), 80), meaning, _txt(data.get("note"), 200)
    if d.source == "sap" and meaning != "other":
        d.source = "person"  # 2.10.3: an admin gave SAP's type a meaning for AP: it is one of CBX's rows now (kept if SAP's list drops it)
    db.flush()
    log(db, "ap_doc_type", actor=user, target=f"ap_doc_type:{code}", detail=f"{d.label} ({meaning})")
    K.forget()
    return d


# ---- downloads ---------------------------------------------------------------------------------------------------------
def list_file(db: Session, kind: str) -> bytes:
    """The list as Excel, in the shape it is uploaded in (vendors: learned rows marked)."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill

    from .. import xlsafe

    wb = openpyxl.Workbook()
    ws = wb.active
    if kind == "entities":
        ws.title = "Company codes"
        ws.append(["Company Code", "Company Name", "Entity Name", "VAT No", "Aliases", "Doc type", "Doc type credit"])
        for e in db.query(ApEntity).order_by(ApEntity.code).all():
            ws.append([e.code, e.name, e.entity, e.vat_no, "; ".join(e.aliases.splitlines()), e.doc_type, e.doc_type_credit])
    elif kind == "utilities":
        ws.title = "Data"
        ws.append(["VENDOR/BP NUMBER", "PID/CC", "NAME1", "NAME3", "ACCOUNT REFERENCE", "MUNICIPALITY/PAYEE", "Company Code"])
        for a in db.query(ApUtilityAccount).order_by(ApUtilityAccount.id).yield_per(2000):
            ws.append([a.vendor, a.cost_centre, a.site, a.name3, a.account, a.municipality, a.company_code])
    elif kind == "vendors":
        ws.title = "Vendors"
        ws.append(["Vendor", "Name", "VAT No", "GL", "Cost centre", "Tax code", "Company code", "Aliases", "Learned from", "Learned on", "Line text",
                   "Payment days", "Line rules"])  # 1.0.102
        for v in db.query(ApVendorCode).order_by(ApVendorCode.name).all():
            ws.append([v.vendor_no, v.name, v.vat_no, v.gl, v.cost_centre, v.tax_code, v.company_code, "; ".join(v.aliases.splitlines()),
                       v.learned_by if v.learned else "", f"{v.learned_at:%Y-%m-%d}" if v.learned and v.learned_at else "", v.item_text,
                       v.payment_days if v.payment_days is not None else "", rules_text(v.line_rules)])
    elif kind == "sites":  # 1.0.102
        ws.title = "Sites"
        ws.append(["Site code", "Company code", "Cost centre", "Note"])
        for x in db.query(ApSite).order_by(ApSite.site_code, ApSite.company_code).all():
            ws.append([x.site_code, x.company_code, x.cost_centre, x.note])
    elif kind == "sap_vendors":  # 1.0.102: in LFA1's own column names (the upload reads them back)
        ws.title = "LFA1"
        ws.append(["LIFNR", "NAME1", "STCEG", "STCD1", "ORT01", "LAND1", "SPERR", "ZTERM", "KTOKK"])  # 1.0.103: + the account group
        for x in db.query(ApSapVendor).order_by(ApSapVendor.vendor).yield_per(5000):
            ws.append([x.vendor, x.name, x.vat, x.tax1, x.city, x.country, "X" if x.blocked else "", x.payment_terms, x.account_group or ""])
    elif kind == "gl_accounts":  # 2.10.3: in the shape SAP exports it (and the upload takes)
        ws.title = "Sheet1"
        ws.append(["G/L Account", "G/L Acct Long Text"])
        for a, n in db.query(ApGlAccount.account, ApGlAccount.name).order_by(ApGlAccount.account).all():
            ws.append([a, n])
    elif kind == "cost_centres":
        ws.title = "Data"
        ws.append(["Cost Center", "Company Code", "Currency", "Profit Center", "Description"])
        for r in db.query(ApCostCentre).order_by(ApCostCentre.company_code, ApCostCentre.cost_centre).yield_per(5000):
            ws.append([r.cost_centre, r.company_code, r.currency, r.profit_centre, r.description])
    elif kind == "vendor_companies":
        ws.title = "Sheet1"
        ws.append(["Vendor", "Company Code"])
        for v, c in db.query(ApVendorCompany.vendor, ApVendorCompany.company_code).order_by(ApVendorCompany.vendor, ApVendorCompany.company_code).yield_per(5000):
            ws.append([v, c])
    else:
        raise Refused("Unknown list.")
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3A5F")
    ws.freeze_panes = "A2"
    xlsafe.book(wb)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---- Pull from SAP (an Admin → Connections connection) --------------------------------------------------------------------
def connections(db: Session) -> list[dict]:
    """The connections an admin ticked for the AP Tool (switched on)."""
    from ..models.connections import ApiConnection

    return [{"id": c.id, "name": c.name, "base_url": c.base_url} for c in db.query(ApiConnection).filter(ApiConnection.is_active.is_(True)).order_by(ApiConnection.name).all()
            if "ap" in (c.tools or [])]


PULL_KEY = "ap.pull"


def pull_settings(db: Session) -> dict:
    v = C2.state_get(db, PULL_KEY, {})  # 1.0.98: the old app_state keeps JSON as text
    return dict(v) if isinstance(v, dict) else {}


def pull(db: Session, user: User, kind: str, connection_id: int, path: str, columns: dict) -> list[dict]:
    """GET `path` on the connection and map its columns: {field: the column's name in SAP's answer}. OData answers
    ({"d": {"results": [...]}} or {"value": [...]}) and a plain list are read."""
    from ..connections import service as CS
    from ..models.connections import ApiConnection

    if not user.is_admin:
        raise PermissionError("Only an admin pulls the AP lists from SAP.")
    if kind not in KINDS + SAP_KINDS + ("sap_vendors",):  # 1.0.102: + SAP's vendor general data
        raise Refused("Unknown list.")
    most = PULL_MOST.get(kind, MAX_ROWS)  # 2.10.3: the vendor / company code list is ~118 000 rows
    c = db.get(ApiConnection, int(connection_id or 0))
    if c is None or not c.is_active or "ap" not in (c.tools or []):
        raise Refused("Pick a connection that is switched on and ticked for the AP Tool (Admin → Connections).")
    cols = {f: _txt(v, 80) for f, v in (columns or {}).items() if f in dict(COLUMNS[kind]) and _txt(v, 80)}
    if not all(f in cols for f in NEEDED[kind]):
        raise Refused("Say which SAP column holds " + " and ".join(NEEDED[kind]).replace("_", " ") + ".")
    # 2.10.0: every page SAP sends (d.__next / @odata.nextLink, on the connection's own address, PULL_PAGES at most), and an
    # answer cut at the size cap is said plainly: a part of the list is never taken for all of it (saving it removes the rest)
    rows, here, pages = [], _txt(path, 400), 0
    while True:
        pages += 1
        try:
            out = _get(db, c, here, user, PULL_MB * 1024 * 1024)  # 1.0.98: the old CS.call has no max_body
        except CS.ConnectionError_ as e:
            raise Refused(str(e)) from None
        if not out["ok"]:
            raise Refused(f"{c.name} answered {out['status']}.")
        if out.get("truncated"):
            raise Refused("SAP's answer is too big to take in one go and was cut, so nothing was saved. Ask SAP for pages (server paging or $top) or fewer columns ($select).")
        data = out.get("json")
        d = data.get("d") if isinstance(data, dict) else None
        page = d.get("results") if isinstance(d, dict) else d if isinstance(d, list) else data.get("value") if isinstance(data, dict) else data if isinstance(data, list) else None
        if not isinstance(page, list):
            raise Refused("The answer has no list of rows in it (OData d.results or value).")
        rows.extend(page)
        if len(rows) > most:
            raise Refused(f"More than {most} rows: split the list.")
        nxt = (d.get("__next") if isinstance(d, dict) else None) or (data.get("@odata.nextLink") or data.get("odata.nextLink") if isinstance(data, dict) else None)
        if not nxt:
            break
        if pages >= PULL_PAGES:
            raise Refused("SAP keeps sending more pages, so nothing was saved. Ask for bigger pages ($top) or a shorter list.")
        here = _next_path(c.base_url, here, str(nxt))
    st = pull_settings(db)
    st[kind] = {"connection_id": c.id, "path": _txt(path, 400), "columns": cols}
    C2.state_set(db, PULL_KEY, st)
    return [{f: (r.get(src) if isinstance(r, dict) else None) for f, src in cols.items()} for r in rows]


PULL_MB, PULL_PAGES = 16, 200


def _get(db: Session, c, path: str, user: User, max_body: int) -> dict:
    """1.0.98: the old cbx/connections/service.call caps every answer at the relay's 512 KB and takes no max_body, so the
    pull's GET is made here with the same checks (switched on, the address re-checked, the key added there, audited) and
    its own cap: the body is read in chunks and cut at max_body ("truncated", as 2.x's call says it)."""
    import httpx
    from urllib.parse import urlsplit

    from ..connections import service as CS

    if not c.is_active:
        raise CS.ConnectionError_(f"The connection '{c.name}' is switched off.")
    url = CS._join(c.base_url, path)
    why = CS.host_refused((urlsplit(c.base_url).hostname or "").lower())
    if why:
        log(db, "api_call", actor=user, target=c.slug, detail=f"GET {path or '/'} refused: {why}", outcome="blocked")
        raise CS.ConnectionError_(why)
    h = {k: v for k, v in (c.extra_headers or {}).items()}
    q: dict = {}
    CS._apply_auth(c, h, q)
    t0 = utcnow()
    raw, cut = bytearray(), False
    try:
        with httpx.Client(timeout=CS.TIMEOUT, follow_redirects=False, trust_env=True) as cl:
            with cl.stream("GET", url, params=q or None, headers=h) as r:
                for chunk in r.iter_bytes():
                    raw += chunk
                    if len(raw) > max_body:
                        cut = True
                        break
                status, ctype, enc = r.status_code, (r.headers.get("content-type") or ""), (r.encoding or "utf-8")
    except httpx.HTTPError as e:
        log(db, "api_call", actor=user, target=c.slug, detail=f"GET {path or '/'} → no answer ({type(e).__name__})", outcome="failed")
        raise CS.ConnectionError_(f"{c.name} did not answer ({type(e).__name__}).") from e
    ms = int((utcnow() - t0).total_seconds() * 1000)
    parsed = None
    if not cut and "json" in ctype:
        try:
            parsed = json.loads(bytes(raw).decode(enc, errors="replace"))
        except ValueError:
            parsed = None
    log(db, "api_call", actor=user, target=c.slug, detail=f"GET {path or '/'} → {status} in {ms} ms", outcome="ok" if status < 400 else "failed")
    return {"ok": status < 400, "status": status, "ms": ms, "json": parsed, "truncated": cut}
PULL_MOST = {"vendor_companies": K.VENDOR_PAIRS_MAX, "cost_centres": K.COST_CENTRES_MAX, "sap_vendors": K.SAP_VENDORS_MAX}
PREVIEW = "ap.pullpreview."
PREVIEW_LIFE_MIN = 15


def _next_path(base: str, here: str, link: str) -> str:
    """SAP's next-page link as a path under the connection's address; one that points anywhere else is not followed."""
    from urllib.parse import urljoin, urlsplit

    b = (base or "").rstrip("/")
    full = urljoin(b + "/" + here.lstrip("/"), link.strip())
    bs, fs = urlsplit(b), urlsplit(full)
    if (fs.scheme, fs.netloc.lower()) != (bs.scheme, bs.netloc.lower()) or not full.startswith(b) or full[len(b):][:1] not in ("", "/", "?"):
        raise Refused("SAP's next page is not on the connection's address, so it was not followed and nothing was saved.")
    return full[len(b):] or "/"


def keep_preview(db: Session, user: User, kind: str, rows: list) -> str:
    """2.10.0: the rows an admin was shown, kept for a few minutes: Save stores exactly these (it does not pull again)."""
    import secrets
    from datetime import timedelta

    old = utcnow() - timedelta(minutes=PREVIEW_LIFE_MIN)
    db.query(AppState).filter(AppState.key.like(PREVIEW + "%"), AppState.updated_at < old).delete(synchronize_session=False)
    token = secrets.token_urlsafe(16)
    C2.state_set(db, PREVIEW + token, {"kind": kind, "by": user.id, "at": utcnow().isoformat(), "rows": rows})
    return token


def previewed(db: Session, user: User, kind: str, token: str) -> list:
    from datetime import datetime, timedelta, timezone

    if not user.is_admin:
        raise PermissionError("Only an admin pulls the AP lists from SAP.")
    row = db.get(AppState, PREVIEW + re.sub(r"[^A-Za-z0-9_\-]", "", str(token or ""))[:40]) if token else None
    v = C2.state_get(db, row.key, {}) if row is not None else {}  # 1.0.98: JSON as text
    v = v if isinstance(v, dict) else {}
    try:
        at = datetime.fromisoformat(v.get("at") or "")
        fresh = utcnow() - (at if at.tzinfo else at.replace(tzinfo=timezone.utc)) < timedelta(minutes=PREVIEW_LIFE_MIN)
    except (TypeError, ValueError):
        fresh = False
    if not v or v.get("kind") != kind or v.get("by") != user.id or not fresh or not isinstance(v.get("rows"), list):
        raise Refused("That preview is gone (they last 15 minutes). Show what would change again, then save.")
    db.delete(row)
    return v["rows"]


# ---- counts and pages for Settings --------------------------------------------------------------------------------------
def entity_out(e: ApEntity) -> dict:
    return {"id": e.id, "code": e.code, "name": e.name, "entity": e.entity, "vat_no": e.vat_no, "aliases": e.aliases, "doc_type": e.doc_type,
            "doc_type_credit": e.doc_type_credit, "source": e.source, "is_active": e.is_active, "updated_by": e.updated_by}


def account_out(a: ApUtilityAccount) -> dict:
    return {"id": a.id, "account": a.account, "vendor": a.vendor, "cost_centre": a.cost_centre, "site": a.site, "name3": a.name3,
            "municipality": a.municipality, "company_code": a.company_code, "source": a.source}


def vendor_out(v: ApVendorCode) -> dict:
    return {"id": v.id, "vendor_no": v.vendor_no, "name": v.name, "vat_no": v.vat_no, "gl": v.gl, "cost_centre": v.cost_centre, "tax_code": v.tax_code,
            "company_code": v.company_code, "item_text": v.item_text, "aliases": v.aliases, "learned": v.learned, "learned_by": v.learned_by,
            "learned_at": v.learned_at.isoformat(timespec="minutes") if v.learned_at else "", "uses": v.uses,
            "payment_days": v.payment_days, "line_rules": clean_rules(v.line_rules or [])}  # 1.0.102


def site_out(x: ApSite) -> dict:
    return {"id": x.id, "site_code": x.site_code, "company_code": x.company_code, "cost_centre": x.cost_centre, "note": x.note, "source": x.source,
            "updated_by": x.updated_by}


def sap_vendor_out(x: ApSapVendor) -> dict:
    return {"id": x.id, "vendor": x.vendor, "name": x.name, "vat": x.vat, "tax1": x.tax1, "city": x.city, "country": x.country, "blocked": bool(x.blocked),
            "payment_terms": x.payment_terms, "source": x.source, "account_group": x.account_group or ""}  # 1.0.103


def search(db: Session, kind: str, q: str = "", limit: int = 50) -> dict:
    if kind == "gl_accounts":
        return search_gl(db, q, limit)
    if kind == "vendor_companies":
        return search_vendor_companies(db, q, limit)
    if kind == "cost_centres":
        return search_cost_centres(db, q, limit)
    if kind == "sap_vendors":  # 1.0.102
        return search_sap_vendors(db, q, limit)
    model = MODEL.get(kind)
    if model is None:
        raise Refused("Unknown list.")
    qy = db.query(model)
    total = qy.count()
    q = (q or "").strip()
    if q:
        like = f"%{q}%"
        if kind == "utilities":
            from sqlalchemy import or_

            k = acct_key(q)
            qy = qy.filter(or_(ApUtilityAccount.account.ilike(like), ApUtilityAccount.account_key == (k or "-"), ApUtilityAccount.site.ilike(like),
                               ApUtilityAccount.vendor.ilike(like), ApUtilityAccount.municipality.ilike(like), ApUtilityAccount.cost_centre.ilike(like)))
        elif kind == "vendors":
            from sqlalchemy import or_

            qy = qy.filter(or_(ApVendorCode.name.ilike(like), ApVendorCode.vendor_no.ilike(like), ApVendorCode.vat_no.ilike(like), ApVendorCode.gl.ilike(like)))
        elif kind == "sites":  # 1.0.102
            from sqlalchemy import or_

            qy = qy.filter(or_(ApSite.site_code.ilike(like), ApSite.cost_centre.ilike(like), ApSite.company_code.ilike(like), ApSite.note.ilike(like)))
        else:
            from sqlalchemy import or_

            qy = qy.filter(or_(ApEntity.code.ilike(like), ApEntity.name.ilike(like), ApEntity.entity.ilike(like)))
    order = {"entities": ApEntity.code, "utilities": ApUtilityAccount.id, "vendors": ApVendorCode.name, "sites": ApSite.site_code}[kind]
    rows = qy.order_by(order).limit(max(1, min(int(limit or 50), 500))).all()
    fn = {"entities": entity_out, "utilities": account_out, "vendors": vendor_out, "sites": site_out}[kind]
    return {"total": total, "rows": [fn(r) for r in rows]}


# =============================================================================================================
# 2.10.3 — SAP knowledge: SAP's Document Type List, the chart of accounts, vendors per company code
# =============================================================================================================
def in_chart(db: Session, gl: str) -> str:
    """A G/L a person codes, as the chart of accounts spells it; blank stays blank. A G/L the chart doesn't have is
    allowed (the chart export may be incomplete: the Harvester's SA110 … are real postings) — the form warns, Tess is yellow."""
    g = _txt(gl, 20)
    if not g:
        return ""
    hit = K.gl_account(db, g)
    return hit["account"] if hit else g


def rule_gls(db: Session) -> set[str]:
    """1.0.102: every G/L the vendor codings' line rules use."""
    return {r["gl"] for (rules,) in db.query(ApVendorCode.line_rules).all() for r in clean_rules(rules or []) if r["gl"]}


def gl_missing(db: Session) -> list[str]:
    """The G/Ls the GL Codes table and the vendor codings use that the chart of accounts doesn't have (Settings shows them)."""
    used = {g.strip() for g in tables(db)["gl"].values() if g and g.strip()} | {g.strip() for (g,) in db.query(ApVendorCode.gl).filter(ApVendorCode.gl != "").distinct().all() if g.strip()}
    used |= rule_gls(db)  # 1.0.102: the line rules' G/Ls too
    chart = K.chart_keys(db)
    return sorted(g for g in used if chart is not None and K.gl_key(g) not in chart)


VC_KEY = "ap.vendor_companies"
CC_KEY = "ap.cost_centres"
BATCH = 5000


def seed_knowledge(db: Session) -> None:
    """At start, from cbx/knowledge_data/: SAP's document types merged into Edwin's table once (an upgrade adds every type
    not there yet), and the chart of accounts and the vendors per company code — each only into an empty table."""
    if not _seeded(db, "sap_doc_types"):
        rows = K.seed_doc_types()
        if rows:
            replace_doc_types(db, None, rows, source="seed", confirm=True)
        _mark_seeded(db, "sap_doc_types")
    if not _seeded(db, "gl_accounts"):
        if not db.query(ApGlAccount.id).first():
            rows = K.seed_chart()
            for i in range(0, len(rows), BATCH):
                db.execute(ApGlAccount.__table__.insert(), [{"account": r["account"], "account_key": K.gl_key(r["account"]), "name": r["name"][:200], "source": "seed",
                                                             "is_active": True, "updated_by": "Chart of Accounts - CBX", "updated_at": utcnow()} for r in rows[i:i + BATCH]])
        _mark_seeded(db, "gl_accounts")
    if not _seeded(db, "cost_centres"):
        if not db.query(ApCostCentre.id).first():
            _insert_cost_centres(db, K.seed_cost_centres())
            C2.state_set(db, CC_KEY, {"source": "seed", "by": "Cost Center List", "at": utcnow().isoformat(timespec="minutes")})
        _mark_seeded(db, "cost_centres")
    if not _seeded(db, "vendor_companies"):
        if not db.query(ApVendorCompany.id).first():
            pairs = K.seed_vendor_companies()
            _insert_pairs(db, pairs)
            C2.state_set(db, VC_KEY, {"source": "seed", "by": "Vendor Master - Comp Code", "at": utcnow().isoformat(timespec="minutes"), "pairs": len(pairs)})
        _mark_seeded(db, "vendor_companies")
    if not _seeded(db, "utilities"):  # 1.0.101: the Harvester's utilities list (the AP Settings table was empty: every bill went to Other)
        if not db.query(ApUtilityAccount.id).first():
            _insert_utilities(db, seed_utilities())
        _mark_seeded(db, "utilities")
    # 1.0.103: Edwin's Supplier Master (SAP's vendor general data) into an empty vendor list; the EFM cost centres the table
    # doesn't have yet (once; a person's rows untouched); SAP's posting history (FBL1N + FBL3N) into an empty history
    from . import history as H

    if not _seeded(db, "sap_vendors"):
        n = H.seed_sap_vendors(db)
        if n:
            C2.state_set(db, "ap.sap_vendors", {"source": "seed", "by": "Supplier Master - Gen Sec - EFM", "at": utcnow().isoformat(timespec="minutes"), "vendors": n})
        _mark_seeded(db, "sap_vendors")
    if not _seeded(db, "cost_centres_2108"):
        H.cost_centres_added(db)
        _mark_seeded(db, "cost_centres_2108")
    db.flush()
    K.forget()
    H.seed(db)


# ---- 1.0.101: the utilities list in the code ------------------------------------------------------------------------------
UTILITIES_CSV = K.DATA / "utilities_accounts.csv"
UTILITY_HEADS = ["VENDOR/BP NUMBER", "PID/CC", "NAME1", "NAME3", "ACCOUNT REFERENCE", "MUNICIPALITY/PAYEE", "Company Code"]
UTILITY_FIELDS = ("vendor", "cost_centre", "site", "name3", "account", "municipality", "company_code")


def utility_seed_rows(mappings: bytes, mappings_name: str, master: Optional[bytes] = None, master_name: str = "") -> tuple[list[str], list[list[str]]]:
    """tools/make_knowledge_data.py: the Harvester's vendor_mappings.xlsx, every row as it is (read by the same reader as
    an upload, so the columns land the same: the vendor BP, the PID/CC and the account reference as text, leading zeros
    kept). The Utilities Master List only adds what the table keeps beside a row (its `extra`: district, responsible
    person, region …) for the same account and vendor; its own rows are not added (the vendor mappings are the list)."""
    recs = read_sheet("utilities", mappings, mappings_name)
    extra: dict[tuple, dict] = {}
    if master:
        for rec in read_sheet("utilities", master, master_name):
            r = _clean("utilities", rec)
            if r is not None and r["extra"]:
                extra[_key("utilities", r)] = r["extra"]
    names: list[str] = []
    for e in extra.values():
        names += [n for n in e if n not in names and _head(n) not in {_head(h) for h in UTILITY_HEADS}]
    rows = []
    for rec in recs:
        r = _clean("utilities", rec)
        more = extra.get(_key("utilities", r), {}) if r is not None else {}
        rows.append([_txt(rec.get(f), LEN.get(f, 160)) for f in UTILITY_FIELDS] + [more.get(n, "") for n in names])
    return UTILITY_HEADS + names, rows


def seed_utilities() -> list[dict]:
    """The seed file's rows, cleaned the way an upload's are (a row with no account number is left out, as the Harvester
    did; the same account and vendor twice: the last one wins)."""
    if not UTILITIES_CSV.is_file():
        return []
    data = UTILITIES_CSV.read_bytes()
    new: dict[tuple, dict] = {}
    for rec in read_sheet("utilities", data, UTILITIES_CSV.name):
        r = _clean("utilities", rec)
        if r is not None:
            new[_key("utilities", r)] = r
    return list(new.values())


def _insert_utilities(db: Session, rows: list[dict]) -> None:
    """Bulk insert, 5 000 rows a statement (the 10 000 accounts load in a second)."""
    t, now = ApUtilityAccount.__table__, utcnow()
    for i in range(0, len(rows), BATCH):
        db.execute(t.insert(), [{**{f: r.get(f) or "" for f in UTILITY_FIELDS}, "account_key": r["account_key"], "extra": r.get("extra") or {}, "source": "seed",
                                 "updated_by": "vendor_mappings.xlsx", "updated_at": now} for r in rows[i:i + BATCH]])


def _insert_pairs(db: Session, pairs: list[tuple[str, str]]) -> None:
    """Bulk insert, 5 000 rows a statement (118 000 pairs load in a second or two)."""
    t = ApVendorCompany.__table__
    for i in range(0, len(pairs), BATCH):
        db.execute(t.insert(), [{"vendor": v, "company_code": c} for v, c in pairs[i:i + BATCH]])


def _insert_pairs(db: Session, pairs: list[tuple[str, str]]) -> None:
    """Bulk insert, 5 000 rows a statement (118 000 pairs load in a second or two)."""
    t = ApVendorCompany.__table__
    for i in range(0, len(pairs), BATCH):
        db.execute(t.insert(), [{"vendor": v, "company_code": c} for v, c in pairs[i:i + BATCH]])


# ---- document types ------------------------------------------------------------------------------------------------
SAP_FIELDS = ("description", "account_types", "number_range", "reverse")
AP_MEANINGS = ("invoice", "credit", "payment", "clearing", "journal", "migration")


def _disagree(d: ApDocType) -> str:
    """Edwin's meaning is about the vendor ledger (every AP meaning is) but SAP's type allows no vendor lines (no K):
    the two are kept side by side and the row says Check (DR: SAP's customer invoice; YR/YG: SAP's RE customer
    invoice / credit memo, account types D S)."""
    if d.source == "sap" or d.meaning not in AP_MEANINGS:
        return ""
    if d.sap and not d.description and not d.account_types:
        return ""
    if not d.sap:
        return "not in SAP's Document Type List"
    if "K" not in (d.account_types or ""):
        return f"SAP: {d.description or '?'} (account types {K.letters_said(d.account_types)}); CBX: {d.label or d.meaning} ({d.meaning})"
    return ""


def replace_doc_types(db: Session, user: Optional[User], records: Iterable[dict], *, source: str, confirm: bool) -> dict:
    """SAP's Document Type List into the table: types not there yet are added (meaning "other" until an admin says what
    they are for AP); Edwin's rows keep his label, meaning and note and get SAP's description, account types, number
    range and reverse type beside them; where the two disagree the row says Check. A type SAP's list no longer has is
    removed when it came from SAP, kept (and marked Check) when it is one of CBX's own rows."""
    if user is not None and not user.is_admin:
        raise PermissionError("Only an admin changes the document types.")
    new = {}
    for r in records:
        code = _txt(r.get("code"), 4).upper()
        if re.fullmatch(r"[A-Z0-9]{1,4}", code):
            new[code] = {"description": _txt(r.get("description"), 80), "account_types": K.letters_of(_txt(r.get("account_types"), 8)),
                         "number_range": _txt(r.get("number_range"), 8), "reverse": _txt(r.get("reverse"), 4).upper(),
                         "sap": {str(k)[:60]: _txt(v, 80) for k, v in (r.get("sap") or {}).items()} if isinstance(r.get("sap"), dict) else {}}
    if not new:
        raise Refused("No document types in it.")
    old = {d.code.upper(): d for d in db.query(ApDocType).all()}
    added = [c for c in new if c not in old]
    diffs = {c: [(f, getattr(old[c], f) or "", new[c][f]) for f in SAP_FIELDS if (getattr(old[c], f) or "") != new[c][f]] for c in new if c in old}
    changed = [c for c in new if c in old and (diffs[c] or old[c].sap != new[c]["sap"])]
    gone = [c for c in old if c not in new]
    removed = [c for c in gone if old[c].source == "sap"]
    kept = [c for c in gone if old[c].source != "sap"]
    show = lambda c, r: f"{c} {r.get('description') or ''}".strip()  # noqa: E731
    out = {"kind": "doc_types", "rows": len(new), "added": len(added), "changed": len(changed), "removed": len(removed), "kept": len(kept), "saved": False,
           "sample": {"added": [show(c, new[c]) for c in added[:5]], "changed": [show(c, new[c]) for c in changed[:5]], "removed": [f"{c} {old[c].description}" for c in removed[:5]]},
           "changes": [{"row": show(c, new[c]), "fields": [{"field": f, "from": a, "to": b} for f, a, b in diffs[c]]} for c in changed[:20] if diffs[c]]}
    if not confirm:
        return out
    for c in removed:
        db.delete(old[c])
    for c, r in new.items():
        d = old.get(c)
        if d is None:
            d = ApDocType(code=c, label=r["description"][:80], meaning="other", note="", source="sap")
            db.add(d)
        moved = d.id is None or bool(diffs.get(c))
        for f in SAP_FIELDS:
            setattr(d, f, r[f])
        d.sap = r["sap"]
        why = _disagree(d)
        if why and (moved or not d.checked_by):  # a row an admin confirmed stays confirmed until SAP's side changes
            d.needs_check, d.check_note, d.checked_by, d.checked_at = True, why[:300], "", None
        elif not why:
            d.needs_check, d.check_note = False, ""
    for c in kept:
        d = old[c]
        if d.sap:  # it was in SAP's list before and is not now
            d.sap, d.description, d.account_types, d.number_range, d.reverse = {}, "", "", "", ""
        why = "not in SAP's Document Type List"
        if not d.checked_by or d.check_note != why:
            d.needs_check, d.check_note, d.checked_by, d.checked_at = True, why, "", None
    db.flush()
    if user is not None:
        log(db, "ap_doc_types_replace", actor=user, target="ap:doc_types", detail=f"from {source}: {len(added)} added, {len(changed)} changed, {len(removed)} removed, {len(kept)} kept")
    out["saved"] = True
    out["checks"] = sorted(c for (c,) in db.query(ApDocType.code).filter(ApDocType.needs_check.is_(True)).all())
    K.forget()
    return out


def confirm_doc_type(db: Session, user: User, did: int) -> ApDocType:
    """An admin read both sides of a "Check" row and confirms CBX's meaning stands: the row no longer says Check."""
    if not user.is_admin:
        raise PermissionError("Only an admin changes the document types.")
    d = db.get(ApDocType, int(did or 0))
    if d is None:
        raise Refused("No such document type.")
    if d.needs_check:
        d.needs_check, d.checked_by, d.checked_at = False, _who(user), utcnow()
        log(db, "ap_doc_type_confirmed", actor=user, target=f"ap_doc_type:{d.code}", detail=f"{d.check_note}"[:400])
        db.flush()
        K.forget()
    return d


def read_doc_type_list(data: bytes, filename: str = "") -> list[dict]:
    try:
        return K.read_doc_type_list(data, filename)
    except K.Refused as e:
        raise Refused(str(e)) from None


# ---- the chart of accounts ------------------------------------------------------------------------------------------
def replace_gl(db: Session, user: User, records: Iterable[dict], *, source: str, confirm: bool) -> dict:
    """A new chart of accounts (an upload of SAP's export, or Pull from SAP): what is added, renamed and removed, then saved."""
    if not user.is_admin:
        raise PermissionError("Only an admin changes the chart of accounts.")
    new: dict[str, tuple[str, str]] = {}
    skipped = 0
    for r in records:
        a = re.sub(r"\s+", "", _txt((r or {}).get("account"), 40))
        if not a or len(a) > 20:
            skipped += 1
            continue
        new[K.gl_key(a)] = (a, _txt(r.get("name"), 200))
    if not new:
        raise Refused("No usable rows in it: nothing would be left in the list.")
    old = {k: (i, a, n or "") for i, a, k, n in db.query(ApGlAccount.id, ApGlAccount.account, ApGlAccount.account_key, ApGlAccount.name).all()}
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = sorted(k for k in set(new) & set(old) if new[k] != old[k][1:])
    used = {K.gl_key(g) for (g,) in db.query(ApVendorCode.gl).filter(ApVendorCode.gl != "").distinct().all()} | {K.gl_key(g) for g in tables(db)["gl"].values() if g}
    used |= {K.gl_key(g) for g in rule_gls(db)}  # 1.0.102
    out = {"kind": "gl_accounts", "rows": len(new), "added": len(added), "changed": len(changed), "removed": len(removed), "skipped": skipped, "saved": False,
           "removed_in_use": sorted(old[k][1] for k in removed if k in used)[:20],  # what Tess will then turn red
           "sample": {"added": [" ".join(new[k]) for k in added[:5]], "changed": [" ".join(new[k]) for k in changed[:5]], "removed": [" ".join(old[k][1:]) for k in removed[:5]]},
           "changes": [{"row": new[k][0], "fields": [{"field": "name", "from": old[k][2], "to": new[k][1]}]} for k in changed[:20]]}
    if not confirm:
        return out
    who = _who(user)
    ids = [old[k][0] for k in removed]
    for i in range(0, len(ids), 1000):
        db.query(ApGlAccount).filter(ApGlAccount.id.in_(ids[i:i + 1000])).delete(synchronize_session=False)
    for k in changed:
        db.query(ApGlAccount).filter(ApGlAccount.id == old[k][0]).update({"account": new[k][0], "name": new[k][1], "source": source[:8], "updated_by": who, "updated_at": utcnow()},
                                                                          synchronize_session=False)
    rows = [{"account": new[k][0], "account_key": k, "name": new[k][1], "source": source[:8], "is_active": True, "updated_by": who, "updated_at": utcnow()} for k in added]
    for i in range(0, len(rows), BATCH):
        db.execute(ApGlAccount.__table__.insert(), rows[i:i + BATCH])
    db.flush()
    log(db, "ap_gl_accounts_replace", actor=user, target="ap:gl_accounts", detail=f"from {source}: {len(added)} added, {len(changed)} changed, {len(removed)} removed")
    K.forget()
    out["saved"] = True
    return out


def search_gl(db: Session, q: str = "", limit: int = 50) -> dict:
    from sqlalchemy import or_

    qy = db.query(ApGlAccount)
    total = qy.count()
    q = (q or "").strip()
    if q:
        words = [w for w in re.split(r"\s+", q) if w][:5]
        k = K.gl_key(q)
        qy = qy.filter(or_(ApGlAccount.account_key.like(f"{k}%") if k else ApGlAccount.id < 0, *[ApGlAccount.name.ilike(f"%{w}%") for w in words[:1]]))
        rows = [r for r in qy.order_by(ApGlAccount.account).limit(2000).all() if (k and r.account_key.startswith(k)) or all(w.lower() in (r.name or "").lower() for w in words)]
    else:
        rows = qy.order_by(ApGlAccount.account).limit(max(1, min(int(limit or 50), 500))).all()
    rows = rows[:max(1, min(int(limit or 50), 500))]
    return {"total": total, "rows": [{"id": r.id, "account": r.account, "name": r.name, "source": r.source} for r in rows]}


# ---- vendors per company code -----------------------------------------------------------------------------------------
def replace_vendor_companies(db: Session, user: User, records: Iterable, *, source: str, confirm: bool) -> dict:
    """SAP's vendor ↔ company code export replaces the table. It is big (~118 000 pairs): what changes is worked out
    with set differences, and saved 5 000 rows a statement."""
    if not user.is_admin:
        raise PermissionError("Only an admin changes the vendors per company code.")
    new: set[tuple[str, str]] = set()
    skipped = 0
    for r in records:
        v, c = (r if isinstance(r, (tuple, list)) else ((r or {}).get("vendor"), (r or {}).get("company_code")))[:2]
        v, c = re.sub(r"\s+", "", K.text(v, 20)), re.sub(r"\s+", "", K.text(c, 12)).upper()
        if v and c:
            new.add((v, c))
        else:
            skipped += 1
    if not new:
        raise Refused("No usable rows in it: nothing would be left in the list.")
    old = set(db.query(ApVendorCompany.vendor, ApVendorCompany.company_code).all())
    old = {(v, c) for v, c in old}
    added, removed = new - old, old - new
    pairs = lambda s: [f"{v} · {c}" for v, c in sorted(s)[:5]]  # noqa: E731
    out = {"kind": "vendor_companies", "rows": len(new), "added": len(added), "changed": 0, "removed": len(removed), "skipped": skipped, "saved": False,
           "vendors": len({v for v, _ in new}), "companies": len({c for _, c in new}),
           "sample": {"added": pairs(added), "changed": [], "removed": pairs(removed)}, "changes": []}
    if not confirm:
        return out
    t = ApVendorCompany.__table__
    if len(removed) > len(old) // 2:  # most of it goes: empty the table and load the new list
        db.execute(t.delete())
        _insert_pairs(db, sorted(new))
    else:
        by_cc: dict[str, list[str]] = {}
        for v, c in removed:
            by_cc.setdefault(c, []).append(v)
        for c, vs in by_cc.items():
            for i in range(0, len(vs), 1000):  # SQL Server takes at most ~2 100 values in one statement
                db.execute(t.delete().where(t.c.company_code == c, t.c.vendor.in_(vs[i:i + 1000])))
        _insert_pairs(db, sorted(added))
    C2.state_set(db, VC_KEY, {"source": source, "by": _who(user), "at": utcnow().isoformat(timespec="minutes"), "pairs": len(new)})
    db.flush()
    log(db, "ap_vendor_companies_replace", actor=user, target="ap:vendor_companies", detail=f"from {source}: {len(added)} added, {len(removed)} removed ({len(new)} pairs)")
    K.forget()
    out["saved"] = True
    return out


def search_vendor_companies(db: Session, q: str = "", limit: int = 50) -> dict:
    """Search by vendor number (its start): each vendor with the company codes it is opened in."""
    from sqlalchemy import func

    total = db.query(func.count(ApVendorCompany.id)).scalar() or 0
    q = re.sub(r"\s+", "", (q or ""))[:20]
    n = max(1, min(int(limit or 50), 200))
    vq = db.query(ApVendorCompany.vendor).distinct()
    if q:
        vq = vq.filter(ApVendorCompany.vendor.like(f"{q.lstrip('0') or q}%") | ApVendorCompany.vendor.like(f"{q}%"))
    vendors = [v for (v,) in vq.order_by(ApVendorCompany.vendor).limit(n).all()]
    got: dict[str, list[str]] = {v: [] for v in vendors}
    if vendors:
        for v, c in db.query(ApVendorCompany.vendor, ApVendorCompany.company_code).filter(ApVendorCompany.vendor.in_(vendors)).order_by(ApVendorCompany.company_code).all():
            got[v].append(c)
    return {"total": total, "rows": [{"id": i, "vendor": v, "companies": cs} for i, (v, cs) in enumerate(got.items(), 1)]}


def knowledge_counts(db: Session) -> dict:
    from sqlalchemy import func

    vc = C2.state_get(db, VC_KEY, {})  # 1.0.98: JSON as text
    return {"cost_centres": db.query(func.count(ApCostCentre.id)).scalar() or 0,
            "cost_centres_codes": db.query(func.count(func.distinct(ApCostCentre.company_code))).scalar() or 0,"gl_accounts": db.query(func.count(ApGlAccount.id)).scalar() or 0,
            "vendor_companies": db.query(func.count(ApVendorCompany.id)).scalar() or 0,
            "vendor_companies_vendors": db.query(func.count(func.distinct(ApVendorCompany.vendor))).scalar() or 0,
            "vendor_companies_codes": db.query(func.count(func.distinct(ApVendorCompany.company_code))).scalar() or 0,
            "vendor_companies_from": dict(vc) if isinstance(vc, dict) else {},
            "doc_types_check": db.query(func.count(ApDocType.id)).filter(ApDocType.needs_check.is_(True)).scalar() or 0,
            # 1.0.102
            "sites": db.query(func.count(ApSite.id)).scalar() or 0, "sap_vendors": db.query(func.count(ApSapVendor.id)).scalar() or 0,
            "sap_vendors_blocked": db.query(func.count(ApSapVendor.id)).filter(ApSapVendor.blocked.is_(True)).scalar() or 0}


# ---- cost centres -------------------------------------------------------------------------------------------------------
def _insert_cost_centres(db: Session, rows: list[dict]) -> None:
    t = ApCostCentre.__table__
    for i in range(0, len(rows), BATCH):
        db.execute(t.insert(), [{"cost_centre": r["cost_centre"], "company_code": r["company_code"], "currency": r.get("currency") or "",
                                 "profit_centre": r.get("profit_centre") or "", "description": (r.get("description") or "")[:120]} for r in rows[i:i + BATCH]])


def replace_cost_centres(db: Session, user: User, records: Iterable[dict], *, source: str, confirm: bool) -> dict:
    """SAP's Cost Center List replaces the table: added, changed (currency, profit centre or description) and removed,
    worked out by key (cost centre, company code); saved 5 000 rows a statement."""
    if not user.is_admin:
        raise PermissionError("Only an admin changes the cost centres.")
    new: dict[tuple[str, str], tuple[str, str, str]] = {}
    skipped = 0
    for r in records:
        r = r or {}
        cc, co = re.sub(r"\s+", "", K.text(r.get("cost_centre"), 20)), re.sub(r"\s+", "", K.text(r.get("company_code"), 12)).upper()
        if not cc or not co:
            skipped += 1
            continue
        row = (K.text(r.get("currency"), 5).upper(), re.sub(r"\s+", "", K.text(r.get("profit_centre"), 20)), K.text(r.get("description"), 120))
        old_row = new.get((cc, co))
        new[(cc, co)] = row if old_row is None else tuple(a or b for a, b in zip(old_row, row))  # the first row wins; a later one fills its blanks
    if not new:
        raise Refused("No usable rows in it: nothing would be left in the list.")
    old = {(cc, co): (i, (cur or "", pc or "", d or "")) for i, cc, co, cur, pc, d in
           db.query(ApCostCentre.id, ApCostCentre.cost_centre, ApCostCentre.company_code, ApCostCentre.currency, ApCostCentre.profit_centre, ApCostCentre.description).all()}
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = sorted(k for k in set(new) & set(old) if new[k] != old[k][1])
    names = ("currency", "profit_centre", "description")
    out = {"kind": "cost_centres", "rows": len(new), "added": len(added), "changed": len(changed), "removed": len(removed), "skipped": skipped, "saved": False,
           "companies": len({co for _, co in new}),
           "sample": {"added": [f"{cc} · {co} {new[(cc, co)][2]}".strip() for cc, co in added[:5]], "changed": [f"{cc} · {co}" for cc, co in changed[:5]],
                      "removed": [f"{cc} · {co} {old[(cc, co)][1][2]}".strip() for cc, co in removed[:5]]},
           "changes": [{"row": f"{cc} · {co}", "fields": [{"field": f, "from": a, "to": b} for f, a, b in zip(names, old[(cc, co)][1], new[(cc, co)]) if a != b]}
                       for cc, co in changed[:20]]}
    if not confirm:
        return out
    t = ApCostCentre.__table__
    if len(removed) > len(old) // 2:  # most of it goes: empty the table and load the new list
        db.execute(t.delete())
        _insert_cost_centres(db, [{"cost_centre": cc, "company_code": co, "currency": v[0], "profit_centre": v[1], "description": v[2]} for (cc, co), v in sorted(new.items())])
    else:
        ids = [old[k][0] for k in removed]
        for i in range(0, len(ids), 1000):
            db.execute(t.delete().where(t.c.id.in_(ids[i:i + 1000])))
        for k in changed:
            db.execute(t.update().where(t.c.id == old[k][0]).values(currency=new[k][0], profit_centre=new[k][1], description=new[k][2]))
        _insert_cost_centres(db, [{"cost_centre": cc, "company_code": co, "currency": new[(cc, co)][0], "profit_centre": new[(cc, co)][1], "description": new[(cc, co)][2]}
                                  for cc, co in added])
    C2.state_set(db, CC_KEY, {"source": source, "by": _who(user), "at": utcnow().isoformat(timespec="minutes")})
    db.flush()
    log(db, "ap_cost_centres_replace", actor=user, target="ap:cost_centres", detail=f"from {source}: {len(added)} added, {len(changed)} changed, {len(removed)} removed ({len(new)} rows)")
    K.forget()
    out["saved"] = True
    return out


def search_cost_centres(db: Session, q: str = "", limit: int = 50) -> dict:
    """By cost centre number (its start), company code, or words in the description."""
    from sqlalchemy import func, or_

    total = db.query(func.count(ApCostCentre.id)).scalar() or 0
    q = (q or "").strip()[:60]
    qy = db.query(ApCostCentre)
    if q:
        k = q.replace(" ", "")
        qy = qy.filter(or_(ApCostCentre.cost_centre.like(f"{k}%"), ApCostCentre.cost_centre.like(f"{k.lstrip('0') or k}%"), ApCostCentre.company_code == k.upper(),
                           ApCostCentre.description.ilike(f"%{q}%")))
    rows = qy.order_by(ApCostCentre.cost_centre, ApCostCentre.company_code).limit(max(1, min(int(limit or 50), 500))).all()
    return {"total": total, "rows": [{"id": r.id, **K._cc_out(r)} for r in rows]}


# =============================================================================================================
# 1.0.102 — sites (a bill-to's site code → its cost centre) and SAP's vendor general data (LFA1)
# =============================================================================================================
def save_site(db: Session, user: User, data: dict, sid: Optional[int] = None) -> ApSite:
    """An admin adds or edits one site row (Settings → Sites, or Needs you → Sites to map). The cost centre is checked
    against SAP's Cost Center List for the company code when the list has that company code (as Tess does)."""
    if not user.is_admin:
        raise PermissionError("Only an admin changes the sites.")
    r = _clean("sites", data or {})
    if r is None:
        raise Refused("Give the site code and its cost centre.")
    if r["company_code"] and not db.query(ApEntity.id).filter(ApEntity.code == r["company_code"]).first():
        raise Refused(f"{r['company_code']} is not in the company list.")
    got = K.cost_centre_in_company(db, r["cost_centre"], r["company_code"]) if r["company_code"] else ("dummy" if r["cost_centre"].upper() == K.DUMMY_CC else None)
    if got == "dummy":
        raise Refused(f"Cost centre {r['cost_centre']} is a dummy cost centre.")
    if got == "no":
        raise Refused(f"Cost centre {r['cost_centre']} doesn't exist in company code {r['company_code']} in SAP.")
    hit = K.cost_centre(db, r["cost_centre"], r["company_code"] or None)
    if hit:
        r["cost_centre"] = hit["cost_centre"]  # as SAP spells it
    x = db.get(ApSite, sid) if sid else None
    if sid and x is None:
        raise Refused("No such site.")
    twin = db.query(ApSite).filter(ApSite.site_code == r["site_code"], ApSite.company_code == r["company_code"]).first()
    if twin is not None and (x is None or twin.id != x.id):
        if x is not None:
            raise Refused(f"{r['site_code']} already has a row for {r['company_code'] or 'any company'}.")
        x = twin  # the same site and company again: that row is updated
    if x is None:
        x = ApSite()
        db.add(x)
    old = f"{x.cost_centre}" if x.id else ""
    for f in ("site_code", "company_code", "cost_centre", "note"):
        setattr(x, f, r[f])
    x.source, x.updated_by = "person", _who(user)
    db.flush()
    log(db, "ap_site", actor=user, target=f"ap_site:{x.site_code}", detail=f"{x.site_code} · {x.company_code or 'any company'} → cost centre {x.cost_centre}"
        + (f" (was {old})" if old and old != x.cost_centre else "") + (f"; {x.note}" if x.note else ""))
    return x


def sites_index(db: Session) -> dict[tuple[str, str], str]:
    """{(site code, company code or ""): cost centre} — small (one row per site and company)."""
    return {(s_, c or ""): cc for s_, c, cc in db.query(ApSite.site_code, ApSite.company_code, ApSite.cost_centre).all()}


def site_cost_centre(db: Session, site: str, company: str, index: Optional[dict] = None) -> tuple[str, str]:
    """Pip's cost centre for a bill-to's site: the site's row for this company, then its row for any company →
    (cost centre, "" | "company" | "any"); ("", "") when the site has no row."""
    k = re.sub(r"\s+", " ", str(site or "")).strip().upper()
    if not k:
        return "", ""
    idx = index if index is not None else sites_index(db)
    co = str(company or "").strip().upper()
    if co and idx.get((k, co)):
        return idx[(k, co)], "company"
    if idx.get((k, "")):
        return idx[(k, "")], "any"
    return "", ""


def read_sap_vendors(data: bytes, filename: str = "") -> Rows:
    """SAP's vendor general data (an LFA1 export from SE16N, or a report with its English labels) → rows of
    {vendor, name, name2, vat, tax1, tax2, city, country, blocked, deleted, payment_terms}, with the mapping."""
    try:
        it = iter(K._rows(data, filename, K.SAP_VENDORS_MAX, cols=MAX_COLS))  # 1.0.103: SAP's own export has 200+ columns (the payment block is the 71st)
        out = Rows()
        cols: dict[str, int] = {}
        for _ in range(15):
            r = next(it, None)
            if r is None:
                break
            cols = map_columns("sap_vendors", list(r))
            if "vendor" in cols:
                out.mapping = {f: _txt(r[c], 60) for f, c in cols.items()}
                break
        if "vendor" not in cols:
            raise Refused("No header row with the vendor number (LIFNR or Vendor) in the first 15 rows.")
        for raw in it:
            rec = {f: raw[c] if c < len(raw) else None for f, c in cols.items()}
            if rec.get("vendor") not in (None, ""):
                out.append(rec)
    except K.Refused as e:
        raise Refused(str(e)) from None
    return out


def _flag(v: Any) -> bool:
    return v is True or str(v if v is not None else "").strip().upper() in ("X", "TRUE", "1", "YES", "Y")


def _sap_vendor_row(rec: dict) -> Optional[dict]:
    vendor = re.sub(r"\s+", "", K.text(rec.get("vendor"), 20))
    if not vendor:
        return None
    name = " ".join(x for x in (K.text(rec.get("name"), 160), K.text(rec.get("name2"), 160)) if x)[:160]
    vat = K.sap_vat(rec.get("vat"))
    if not (len(vat) == 10 and vat.startswith("4")):  # SA vendors often carry the VAT number in STCD1 / STCD2
        for alt in (rec.get("tax1"), rec.get("tax2")):
            a = K.sap_vat(alt)
            if len(a) == 10 and a.startswith("4"):
                vat = a
                break
    return {"vendor": vendor, "name": name, "name_key": name_key(name)[:160], "vat": vat, "tax1": K.text(rec.get("tax1"), 40),
            "city": K.text(rec.get("city"), 60), "country": K.text(rec.get("country"), 4).upper(), "blocked": _flag(rec.get("blocked")) or _flag(rec.get("deleted")),
            "payment_terms": re.sub(r"\s+", "", K.text(rec.get("payment_terms"), 8)).upper(),
            # 1.0.103: a sheet without the account group keeps what the list has (None = not in the sheet)
            "account_group": re.sub(r"\s+", "", K.text(rec.get("account_group"), 8)).upper() if "account_group" in rec else None,
            # 1.0.104 (Builder D, V01): ERDAT / ERNAM / AEDAT (None = not in the sheet: the list keeps what it has)
            **_vr_stamps(rec)}


def _vr_stamps(rec: dict) -> dict:
    """1.0.104 (Builder D, V01): the created on / by and changed on of an LFA1 row (cbx/ap/vendor_review.py reads them)."""
    from . import vendor_review as VR

    return VR.stamps(rec)


SV_FIELDS = ("name", "name_key", "vat", "tax1", "city", "country", "blocked", "payment_terms", "account_group",  # 1.0.103: + account group
             "created_on", "created_by", "changed_on")  # 1.0.104 (Builder D, V01)


def replace_sap_vendors(db: Session, user: User, records: Iterable[dict], *, source: str, confirm: bool) -> dict:
    """SAP's vendor general data replaces the table: added, changed and removed by vendor number, saved 5 000 rows a
    statement (it can be tens of thousands of vendors)."""
    if not user.is_admin:
        raise PermissionError("Only an admin changes SAP's vendor list.")
    new: dict[str, dict] = {}
    skipped = 0
    for rec in records:
        r = _sap_vendor_row(rec or {}) if isinstance(rec, dict) else None
        if r is None:
            skipped += 1
            continue
        new[r["vendor"]] = r  # the same vendor twice: the last row wins
    if not new:
        raise Refused("No usable rows in it: nothing would be left in the list.")
    old = {x.vendor: x for x in db.query(ApSapVendor).all()}
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    diffs = {v: [(f, getattr(old[v], f), new[v][f]) for f in SV_FIELDS if f != "name_key" and new[v][f] is not None
                 and (getattr(old[v], f) if f != "blocked" else bool(old[v].blocked)) != new[v][f]]
             for v in set(new) & set(old)}
    changed = sorted(v for v, d in diffs.items() if d)
    show = lambda r: f"{r['vendor']} {r['name']}".strip()  # noqa: E731
    out = {"kind": "sap_vendors", "rows": len(new), "added": len(added), "changed": len(changed), "removed": len(removed), "skipped": skipped, "saved": False,
           "with_vat": sum(1 for r in new.values() if len(r["vat"]) == 10), "blocked": sum(1 for r in new.values() if r["blocked"]),
           "sample": {"added": [show(new[v]) for v in added[:5]], "changed": [show(new[v]) for v in changed[:5]], "removed": [f"{v} {old[v].name}".strip() for v in removed[:5]]},
           "changes": [{"row": show(new[v]), "fields": [{"field": f, "from": a, "to": b} for f, a, b in diffs[v]]} for v in changed[:20]]}
    from . import vendor_review as VR  # 1.0.104 (Builder D, V01): the vendors created or changed from the go-live day are reviewed

    due = VR.due(db, new, added, changed, diffs)
    out["reviews"] = VR.due_out(due, new)
    if not confirm:
        return out
    t = ApSapVendor.__table__
    src = (source or "excel")[:8]
    if len(removed) > len(old) // 2:  # most of it goes: empty the table and load the new list
        db.execute(t.delete())
        rows = [{**r, "account_group": r["account_group"] or "", "source": src} for _, r in sorted(new.items())]
    else:
        ids = [old[v].id for v in removed]
        for i in range(0, len(ids), 1000):
            db.execute(t.delete().where(t.c.id.in_(ids[i:i + 1000])))
        for v in changed:
            db.execute(t.update().where(t.c.id == old[v].id).values(**{f: new[v][f] for f in SV_FIELDS if new[v][f] is not None}, source=src))
        rows = [{**new[v], "account_group": new[v]["account_group"] or "", "source": src} for v in added]
    for i in range(0, len(rows), BATCH):
        db.execute(t.insert(), rows[i:i + BATCH])
    db.flush()
    log(db, "ap_sap_vendors_replace", actor=user, target="ap:sap_vendors", detail=f"from {source}: {len(added)} added, {len(changed)} changed, {len(removed)} removed ({len(new)} vendors)")
    out["reviews"]["opened"] = VR.open_reviews(db, user, due, source=src)  # 1.0.104 (Builder D, V01)
    K.forget()
    out["saved"] = True
    return out


def search_sap_vendors(db: Session, q: str = "", limit: int = 50) -> dict:
    """By vendor number (its start), VAT number, or words in the name."""
    from sqlalchemy import func, or_

    total = db.query(func.count(ApSapVendor.id)).scalar() or 0
    q = (q or "").strip()[:60]
    qy = db.query(ApSapVendor)
    if q:
        k, v = q.replace(" ", ""), K.sap_vat(q)
        qy = qy.filter(or_(ApSapVendor.vendor.like(f"{k}%"), ApSapVendor.vendor.like(f"{k.lstrip('0') or k}%"), ApSapVendor.vat == (v or "-"), ApSapVendor.name.ilike(f"%{q}%")))
    rows = qy.order_by(ApSapVendor.vendor).limit(max(1, min(int(limit or 50), 500))).all()
    return {"total": total, "rows": [sap_vendor_out(r) for r in rows]}
