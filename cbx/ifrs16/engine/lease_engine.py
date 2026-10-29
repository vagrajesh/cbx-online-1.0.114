"""
Lease workflow engine for IFRS 16 Workflow.

Automates the monthly Lease Accelerator (LA) update cycle:

    RR06 movement file  +  Sequentra validation export  +  LA transaction report
        -> reconciliation
        -> PIW rows          (Additions / anything not yet on LA)
        -> MDIW rows         (Renewals of leases already on LA)
        -> Rent Review rows  (payment escalations effective in the month)
        -> Termination rows  (Removals of leases on LA)
        -> Recon + Exceptions report

Routing rules (from the finance team's key notes):
    * PIW  is used for all leases not on Lease Accelerator
    * MDIW is used to update leases already loaded
    * Rent Review updates lease payments based on the movement file
    * Terminations (bulk asset event) removes leases with a "Removal" movement

Column mappings follow PIW_mapping.xlsx / CIW_mapping.xlsx and were verified
against the team's own prepared June 2026 files (PIW_Additions_2026.06.29,
MDIW_SA_2026.06.29v3, Rent Review_28.06.2026, Terminations 2026.06.29).
"""

from __future__ import annotations

import calendar
import numbers
import os
import re
import threading
from collections import OrderedDict
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# Fixed values used by the team on every import sheet (from PIW_mapping.xlsx
# "LA fixed data field" rows and their prepared June 2026 workbooks).
# ---------------------------------------------------------------------------
FIXED = {
    "ledger": "IFRS 16 Ledger",
    "lease_type": "FMV",
    "frequency": "Monthly",
    "repayment_mode": "Advance",
    "accounting_classification": "Capital",
    "asset_owner": "",
    "treasury_approver": "",
    "partial_building_flag": "Yes",
    "gl_code": "Building",
    "lessee": "",
    "default_currency": "ZAR",
    "termination_event": "Return",
    "rent_review_timing": "Recurring",
    "rent_review_reason": "Contingent Rent Resolved",
    "rent_review_comment": "Rent Review",
}

def apply_code_library(path):
    """Overlay FIXED values from an editable workbook (Data/codes.xlsx,
    sheet 'Codes', columns Setting | Value). Lets the team change the
    ledger name, approvers, GL code and so on without touching code."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        df = pd.read_excel(path)
    except Exception:
        return {}
    cols = {str(c).strip().lower(): c for c in df.columns}
    if "setting" not in cols or "value" not in cols:
        return {}
    applied = {}
    for _, r in df.iterrows():
        key = str(r[cols["setting"]]).strip()
        val = r[cols["value"]]
        if key in FIXED and _text(val):
            FIXED[key] = _text(val)
            applied[key] = FIXED[key]
    return applied


# Exact template headers (must match the LA import workbooks column for
# column so the generated rows can be pasted straight in).
PIW_SCHEDULE_COLS = [
    "Schedule Number", "Lease Start Date", "Ledger", "Ledger Date", "Currency",
    "Comments", "PO Number", "Lease Type", "Frequency", "Duration",
    "Reasonably Certain Holding Period", "LRF", "Payment", "FIIR",
    "Rent Escalation Cap", "Estimated GRV", "Entity", "BU", "IBR",
    "Repayment Mode", "Accounting Classification", "Override Explanation",
    "Lessee", "Funder", "Lessor Reference Number", "ShipTo Company",
    "ShipTo Address1", "ShipTo Address2", "ShipTo City", "ShipTo State",
    "ShipTo Zip", "ShipTo Country", "Facility Code", "Geo", "Reporting Area",
    "Asset Owner", "Asset User", "Vendor", "Order Administrator",
    "Treasury Approver", "Analyst", "Deal Tag", "Is Sub-Leased",
    "Partial Building Flag",
]
PIW_DETAILS_COLS = [
    "Schedule Number", "Product Category", "External Id", "Allocation Percent",
    "Product Number", "Manufacturer", "Description", "Cost Center", "Project",
    "GL Code", "Available for Use Date", "Depreciation Start Date",
    "Default Disposition at EOT", "Reference Number", "Quantity", "Unit Price",
    "Observable Price", "Unit Rent", "Serial Number", "Asset Tag", "Comments",
    "Asset Owner", "Asset User", "Property Tax Authority", "ShipTo Company",
    "ShipTo Address1", "ShipTo Address2", "ShipTo City", "ShipTo State",
    "ShipTo Zip", "ShipTo Country", "Facility Code", "MAC Address",
    "IP Address", "Common Name", "Fully Qualified Name", "Units",
    "Total Space", "Usable Space", "Rentable Space", "Price Per Utilized Unit",
    "Utility Pricing Basis", "Service State", "Vendor",
]
EOT_COLS = [
    "Schedule Number", "Timing", "Option Type", "Reasonably Certain EOT Option",
    "Effective Month", "Min Days Notice", "Max Days Notice",
    "Exercisable by Lessee", "Description", "Purchase Amount",
    "Buyout Ceiling", "Buyout Floor", "Renewal Term", "Renewal Payment",
    "Renewal Payment LRF", "Price Per Utilized Unit", "Utility Units",
    "Return Fee", "Early Penalty", "Limit Fee",
]
STEP_COLS = [
    "Schedule Number", "Starting Payment Number", "Number Payments",
    "Payment Amount", "Payment Frequency",
]
MDIW_SCHEDULE_COLS = [
    "Schedule", "Lease Type", "Frequency", "Repayment Mode",
    "Accounting Classification", "Overrride Explanation", "Duration",
    "Payment Basis", "Down Payment", "LRF", "Payment", "Rent Escalation Cap",
    "Partial Building Flag",
]
MDIW_MODSUM_COLS = [
    "Schedule Number", "IBR", "Reasonably Certain Holding Period",
    "Estimated Guaranteed Residual Value (GRV)", "Comments",
    "Modification Date", "Asset Available Date", "Commensurate Adjustment",
    "Remeasure ROU Basis",
]
MDIW_DETAILS_COLS = [
    "Schedule Number", "Product Category", "External Id", "Product Id",
    "Manufacturer", "Product Description", "Unit Rent", "Cost Center",
    "GL Code", "Quantity", "Unit Price", "Standalone Observable Price",
    "Units", "Total Space", "Usable Space", "Rentable Space",
    "Price Per Utilized Unit", "Utility Pricing Basis",
]
RENT_REVIEW_COLS = [
    "Schedule Number", "Remeasurement Effective Date", "Effective Date",
    "Index Basis", "Rate", "Amount", "Timing", "Reason", "Comments",
]
TERMINATION_COLS = [
    "Schedule Number", "Asset ID", "Serial Number", "Description", "EOT Event",
    "Ledger Date Month", "Ledger Date Day", "Ledger Date Year",
    "Effective Date Month", "Effective Date Day", "Effective Date Year",
    "Renewal Term in Months", "Renewal Term Payment Frequency",
    "Renewal Payment", "Buyout Cost", "RMA Number",
]


# ---------------------------------------------------------------------------
# Robust file loading
# ---------------------------------------------------------------------------
def _check_readable(path):
    """Fail early with a plain-English message when a workbook is
    incomplete or corrupt. An .xlsx is a zip archive; if the end-of-
    archive record is missing the file was copied or downloaded while
    still being written, and no reader can open it."""
    p = Path(path)
    if not p.exists():
        raise ValueError("File not found: %s" % p.name)
    size = p.stat().st_size
    if size == 0:
        raise ValueError("'%s' is empty (0 bytes) - re-download it."
                         % p.name)
    head = b""
    tail = b""
    try:
        with open(p, "rb") as fh:
            head = fh.read(8)
            fh.seek(max(0, size - 65536))
            tail = fh.read()
    except Exception:
        return
    if p.suffix.lower() in (".xlsx", ".xlsm"):
        if head[:2] != b"PK":
            if head[:4] == b"\xd0\xcf\x11\xe0":
                return      # 1.0.106 / 2.10.9: an old .xls named .xlsx - read as one
            if head.lstrip()[:1] == b"<":
                return      # a web page / XML sheet named .xlsx - read as one
            raise ValueError(
                "'%s' is not a valid Excel file. Re-export or re-download "
                "it." % p.name)
        if b"PK\x05\x06" not in tail:
            raise ValueError(
                "'%s' is incomplete or corrupted - the Excel archive is "
                "cut short (%.0f KB). This happens when a file is copied "
                "or downloaded while still being saved. Please re-export "
                "or re-download it, then check the file size looks right."
                % (p.name, size / 1024.0))


def _markup_book(path):
    """{sheet: frame} from an '.xls' that is really a web page (HTML table)
    or an Excel 2003 XML spreadsheet - what several report exports save
    under an .xls name. Standard library only. {} when it is neither."""
    import html as _html
    from html.parser import HTMLParser
    import xml.etree.ElementTree as ET

    raw = Path(path).read_bytes()[:50_000_000]
    head = raw[:2048].lstrip().lower()
    text = raw.decode("utf-8", "replace")
    if b"urn:schemas-microsoft-com:office:spreadsheet" in raw[:4096]:
        ns = {"ss": "urn:schemas-microsoft-com:office:spreadsheet"}
        book = {}
        root = ET.fromstring(raw)
        for i, ws in enumerate(root.findall("ss:Worksheet", ns)):
            name = ws.get("{%s}Name" % ns["ss"]) or "Sheet%d" % (i + 1)
            rows = []
            for row in ws.findall(".//ss:Row", ns):
                vals, col = [], 0
                for cell in row.findall("ss:Cell", ns):
                    idx = cell.get("{%s}Index" % ns["ss"])
                    if idx:
                        while col < int(idx) - 1:
                            vals.append(None)
                            col += 1
                    d = cell.find("ss:Data", ns)
                    vals.append(d.text if d is not None else None)
                    col += 1
                rows.append(vals)
            width = max((len(r) for r in rows), default=0)
            book[name] = pd.DataFrame([r + [None] * (width - len(r))
                                       for r in rows])
        return book
    if not (head.startswith((b"<!doctype html", b"<html", b"<table",
                             b"<?xml")) or b"<table" in raw[:20000].lower()):
        return {}

    class _T(HTMLParser):
        def __init__(self):
            super().__init__()
            self.tables, self.row, self.cell, self.inc = [], None, [], False

        def handle_starttag(self, tag, attrs):
            if tag == "table":
                self.tables.append([])
            elif tag == "tr" and self.tables:
                self.row = []
            elif tag in ("td", "th") and self.row is not None:
                self.inc, self.cell = True, []

        def handle_endtag(self, tag):
            if tag in ("td", "th") and self.row is not None and self.inc:
                self.row.append(_html.unescape("".join(self.cell)).strip()
                                or None)
                self.inc = False
            elif tag == "tr" and self.row is not None and self.tables:
                self.tables[-1].append(self.row)
                self.row = None

        def handle_data(self, data):
            if self.inc:
                self.cell.append(data)

    t = _T()
    t.feed(text)
    book = {}
    for i, rows in enumerate(x for x in t.tables if x):
        width = max(len(r) for r in rows)
        book["Sheet%d" % (i + 1)] = pd.DataFrame(
            [r + [None] * (width - len(r)) for r in rows])
    return book


# 1.0.106 / 2.10.9: what a file put in the wrong slot really is, so the
# message names the right slot instead of only saying what is missing.
_FILE_KINDS = (
    ("la", "the LA transaction report (Portfolio Transactions)",
     "The LA register", ("transaction #",)),
    ("sequentra", "the Sequentra validation export (Record Info)",
     "The Sequentra export", ("record id",)),
    ("steps", "the step payments report",
     "The step payments report", ("expense type name", "number of payments")),
    ("movements", "the RR06 / ARO06 export",
     "The RR06 / ARO06 movements", ("lease reference",)),
    ("ledger", "the LA ledger export",
     "the ledger check", ("ledger date", "jeshortdesc")),
)


def what_file(book, slot):
    """' This looks like <kind> - it belongs in <slot>.' for a book whose
    headings say it is another of the IFRS 16 files; '' when it is not
    recognisable (or it is the slot's own kind)."""
    try:
        for name, raw in list(book.items())[:20]:
            if raw is None or not len(raw):
                continue
            for i in range(min(30, len(raw))):
                heads = {norm_head(v) for v in raw.iloc[i].tolist()}
                for kind, words, where, need in _FILE_KINDS:
                    if all(norm_head(n) in heads for n in need):
                        if kind == slot:
                            return ""
                        return (" This looks like %s - it belongs in '%s'."
                                % (words, where))
    except Exception:
        pass
    return ""


def _pick_from_book(book, sheet_name):
    if sheet_name is None:
        return book
    if isinstance(sheet_name, int):
        return list(book.values())[sheet_name]
    return book[sheet_name]


# 1.0.106 / 2.10.9: a month run read the RR06 three times (the movements,
# the Legal Entity per lease, the Step Payment tab) and the scan read every
# file again - 30 s to 2 min on the real exports. A kept upload never
# changes, so a whole book read once is kept for the next reader (keyed by
# path, size and time; copies handed out, so no reader changes another's).
_BOOKS = OrderedDict()
_BOOKS_MAX = 4            # the four source files of one upload (~110 MB for the real ones)
_BOOKS_TTL = 15 * 60      # seconds: an idle server lets them go
_BOOKS_LOCK = threading.Lock()


def _book_key(path, header):
    try:
        st = os.stat(str(path))
    except OSError:
        return None
    return (os.path.abspath(str(path)), st.st_size, st.st_mtime_ns, header)


def _read_workbook(path, sheet_name=None, header=None):
    """Read Excel with engine fallback (see _read_book). Whole books
    (sheet_name=None) are kept for the next reader of the same file."""
    _check_readable(path)
    if sheet_name is not None:
        return _read_book(path, sheet_name, header)
    import time

    key = _book_key(path, header)
    now = time.monotonic()
    with _BOOKS_LOCK:
        for k in [k for k, (at, _b) in _BOOKS.items() if now - at > _BOOKS_TTL]:
            del _BOOKS[k]
        hit = _BOOKS.get(key) if key else None
        if hit is not None:
            _BOOKS[key] = (now, hit[1])
            _BOOKS.move_to_end(key)
            hit = hit[1]
    if hit is None:
        hit = _read_book(path, None, header)
        if key:
            with _BOOKS_LOCK:
                _BOOKS[key] = (now, hit)
                while len(_BOOKS) > _BOOKS_MAX:
                    _BOOKS.popitem(last=False)
    return {k: v.copy() for k, v in hit.items()}


def _sniff(path):
    """What the file really is, from its first bytes (an export's name does
    not always say): 'xlsx' (a zip), 'xls' (an OLE/BIFF workbook), 'text'
    (a web page, an Excel 2003 XML sheet or a CSV) or '' (unreadable)."""
    try:
        with open(str(path), "rb") as fh:
            head = fh.read(8)
    except OSError:
        return ""
    if head[:2] == b"PK":
        return "xlsx"
    if head[:4] == b"\xd0\xcf\x11\xe0":
        return "xls"
    return "text"


def _read_csv_book(path, header=None):
    """{'Sheet1': frame} from a CSV export (comma, semicolon or tab; a title
    line above the headings is fine), cells as text the way a spreadsheet
    shows them; None when it is not one."""
    import csv
    import io

    raw = Path(str(path)).read_bytes()[:200_000_000]
    for enc in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        return None
    if "\x00" in text[:4096]:
        return None                       # binary, not text
    lines = [ln for ln in text.splitlines()[:50] if ln.strip()]
    if not lines:
        return None
    sep = max((";", ",", "\t", "|"), key=lambda d: max(ln.count(d) for ln in lines))
    if not any(ln.count(sep) for ln in lines):
        return None
    rows = list(csv.reader(io.StringIO(text), delimiter=sep))
    width = max((len(r) for r in rows), default=0)
    df = pd.DataFrame([[(c.strip() or None) for c in r] + [None] * (width - len(r))
                       for r in rows])
    return {"Sheet1": df}


def _read_book(path, sheet_name=None, header=None):
    """Read a workbook by what it really is (1.0.106 / 2.10.9: by content,
    not by name - an .xls that is really an .xlsx, an .xlsx that is really
    an old .xls, a web page or XML sheet saved as .xls, a CSV):
    .xlsx: calamine -> openpyxl;  .xls: xlrd -> calamine;
    text: a web page / Excel 2003 XML sheet, else a CSV."""
    path = str(path)
    kind = _sniff(path)
    if kind == "xls":
        try:
            return pd.read_excel(path, sheet_name=sheet_name, header=header,
                                 engine="xlrd")
        except Exception as first:
            try:
                return pd.read_excel(path, sheet_name=sheet_name,
                                     header=header, engine="calamine")
            except Exception:
                raise first from None
    if kind == "text":
        try:
            book = _markup_book(path)
        except Exception:
            book = {}
        if not book:
            book = _read_csv_book(path) or {}
        if book and header is None:
            return _pick_from_book(book, sheet_name)
        raise ValueError(
            "'%s' is not an Excel workbook, a web page saved as Excel or a "
            "CSV file. Open it in Excel, Save As .xlsx and upload that."
            % Path(path).name)
    # calamine first - the same cells as openpyxl (checked on the real
    # Sequentra, step payments and RR06 exports) in a fifth of the time;
    # openpyxl reads what calamine can't (or when it is missing).
    try:
        return pd.read_excel(path, sheet_name=sheet_name, header=header,
                             engine="calamine")
    except Exception:
        return pd.read_excel(path, sheet_name=sheet_name, header=header,
                             engine="openpyxl")


def _find_header_row(df, must_contain, search_rows=30):
    """Locate the header row: first row containing the given column label.
    1.0.106 / 2.10.9: 30 rows down (exports put a title block of report
    name, 'Created by ...' and parameters above the headings), and
    headings compared with their spaces collapsed ('Schedule  Number ')."""
    target = re.sub(r"\s+", " ", must_contain).strip().lower()
    for i in range(min(search_rows, len(df))):
        cells = [re.sub(r"\s+", " ", str(v)).strip().lower()
                 for v in df.iloc[i].tolist()]
        if target in cells:
            return i
    return None


def _reheader(raw, header_row):
    """Re-head a sheet at the given row.

    A column with no heading of its own takes the group banner from the
    row above - the RR06 keeps the movement date in column A under the
    'Movement' banner with nothing in the header cell. Columns that are
    still unnamed are kept when they hold data and dropped only when
    they are entirely empty. (Unnamed columns used to be deleted
    outright, which threw the movement date away before anything could
    read it.)"""
    df = raw.iloc[header_row + 1:].reset_index(drop=True)
    banner = raw.iloc[header_row - 1].tolist() if header_row > 0 else []
    names, seen = [], {}
    for i, c in enumerate(raw.iloc[header_row].tolist()):
        # 1.0.106 / 2.10.9: spaces collapsed ('Total lease \nvalue')
        name = re.sub(r"\s+", " ", str(c)).strip()
        if not name or name.lower() == "nan":
            b = re.sub(r"\s+", " ", str(banner[i])).strip() \
                if i < len(banner) else ""
            name = b if b and b.lower() != "nan" else "_blank"
        if name in seen:              # duplicate headers break pandas
            seen[name] += 1
            name = "%s.%d" % (name, seen[name])
        else:
            seen[name] = 0
        names.append(name)
    df.columns = names
    keep = [c for c in names
            if not c.startswith("_blank") or df[c].notna().any()]
    return df[keep]


def _pick_sheet(sheet_dict, *preferred, contains=None):
    """Pick a sheet by preferred names, then by substring, else None."""
    lower = {str(k).strip().lower(): k for k in sheet_dict}
    for name in preferred:
        if name.lower() in lower:
            return sheet_dict[lower[name.lower()]]
    if contains:
        for k in sheet_dict:
            if contains.lower() in str(k).lower():
                return sheet_dict[k]
    return None


def load_la_register(path):
    """LA 'Portfolio Transactions' report. Key column: 'Transaction #'."""
    book = _read_workbook(path, sheet_name=None, header=None)
    raw = _pick_sheet(book, "Portfolio Transactions", contains="transaction")
    if raw is None:
        raw = next(iter(book.values()))
    hr = _find_header_row(raw, "Transaction #", search_rows=30)
    if hr is None:
        raise ValueError("Could not find a 'Transaction #' header in the "
                         "LA transaction report '%s'.%s"
                         % (Path(str(path)).name, what_file(book, "la")))
    df = _reheader(raw, hr)
    df = df[df["Transaction #"].notna()]
    df["Transaction #"] = df["Transaction #"].astype(str).str.strip()
    return df


# Columns a movement export might carry the month in, in preference
# order. Anything date-like is accepted; see month_tokens below.
# Where the movement month comes from, in preference order. The team's
# process keys the month off the DCF date on the Contract Movement tab -
# the month the DCF is loaded - so that column wins when the file has
# one; an explicit Month column is next, then any other date column.
MONTH_COLS = ("DCF date", "Movement", "Month", "Movement Month",
              "Movement Date", "Month Loaded", "Load Month",
              "Reporting Month", "Movement Period", "Period",
              "Month/Year", "Mth")

# Dates that belong to the LEASE, never to the movement. Treating one of
# these as the movement month is what put every month into every run, so
# they are excluded from the search entirely.
NOT_MONTH_COLS = ("current start date", "initial lease start date",
                  "lease expiry date", "termination date",
                  "current lease end date", "move in date",
                  "signature date", "bo date")


def norm_head(c):
    """A column heading reduced for comparison: lower-cased FIRST, then
    punctuation folded to single spaces. Lower-casing first matters -
    doing it the other way round eats every capital letter, which is how
    'DCF Date' stopped matching 'DCF date'."""
    return re.sub(r"[^a-z0-9]+", " ", str(c).strip().lower()).strip()

_MONTH_WORDS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5,
                "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9,
                "oct": 10, "nov": 11, "dec": 12}


def month_token(v):
    """A month from the many shapes a month column arrives in:
    202606, 2026-06, 06/2026, Jun-26, June 2026, a real date, or an
    Excel serial number. None when it is not a month at all."""
    if v is None:
        return None
    if isinstance(v, (date, datetime)):   # 1.0.106 / 2.10.9: a real date needs no parsing
        try:                              # (18 000 RR06 rows went through pandas one by one)
            return None if pd.isna(v) else date(v.year, v.month, 1)
        except (TypeError, ValueError):
            return None
    t = re.sub(r"\s+", " ", str(v)).strip()
    if not t or t.lower() in ("nan", "nat", "none"):
        return None
    if re.fullmatch(r"(19|20)\d{4}(\.0)?", t):          # 202606
        t = t.split(".")[0]
        y, m = int(t[:4]), int(t[4:6])
        return date(y, m, 1) if 1 <= m <= 12 else None
    m0 = re.fullmatch(r"((19|20)\d{2})[-/. ](\d{1,2})", t)   # 2026-06
    if m0:
        y, m = int(m0.group(1)), int(m0.group(3))
        return date(y, m, 1) if 1 <= m <= 12 else None
    m1 = re.fullmatch(r"(\d{1,2})[-/. ]((19|20)\d{2})", t)   # 06/2026
    if m1:
        m, y = int(m1.group(1)), int(m1.group(2))
        return date(y, m, 1) if 1 <= m <= 12 else None
    m2 = re.fullmatch(r"([A-Za-z]{3,9})[-/. ]+(\d{2,4})", t)  # Jun-26
    if m2:
        m = _MONTH_WORDS.get(m2.group(1)[:4].lower().rstrip(".")) or \
            _MONTH_WORDS.get(m2.group(1)[:3].lower())
        y = int(m2.group(2))
        if m:
            y = y + 2000 if y < 100 else y
            return date(y, m, 1)
    m3 = re.fullmatch(r"(\d{2,4})[-/. ]+([A-Za-z]{3,9})", t)  # 2026-Jun
    if m3:
        m = _MONTH_WORDS.get(m3.group(2)[:3].lower())
        y = int(m3.group(1))
        if m:
            y = y + 2000 if y < 100 else y
            return date(y, m, 1)
    if re.fullmatch(r"\d{5}(\.\d+)?", t):               # Excel serial
        n = int(float(t))
        if not (15000 <= n <= 60000):        # ~1941 to ~2064
            return None
        try:
            from datetime import timedelta
            d = date(1899, 12, 30) + timedelta(days=n)
            return date(d.year, d.month, 1)
        except Exception:
            return None
    if re.fullmatch(r"\d+(\.\d+)?", t):
        return None          # any other bare number is a reference, not
                             # a date - pandas would read 9716 as a year
    d = _to_date(t)
    return date(d.year, d.month, 1) if d else None


def month_series(mv):
    """(one month per row, the column it came from).

    This is the single definition of 'which month is this movement in',
    used both to fill the month dropdown and to filter the run - so the
    two can never disagree."""
    if mv is None or not len(mv):
        return None, None
    norm = {}
    for c in mv.columns:                 # first column of a name wins
        norm.setdefault(norm_head(c), c)
    for want in MONTH_COLS:
        col = norm.get(norm_head(want))
        if col is None:
            continue
        vals = [month_token(v) for v in mv[col]]
        if any(vals):
            return vals, col
    best, best_col, best_n = None, None, 0
    for col in mv.columns:               # last resort: whatever parses
        if norm_head(col) in NOT_MONTH_COLS:
            continue                     # a lease date is not a month
        vals = [month_token(v) for v in mv[col]]
        n = sum(1 for v in vals if v)
        if n > best_n:
            best, best_col, best_n = vals, col, n
    return (best, best_col) if best_n else (None, None)


def months_in_movement_file(path):
    """(months newest first as MM/YYYY, a note saying where they came
    from). Never raises - the note explains an empty list, so the app
    can tell the user what to fix instead of showing a blank box."""
    try:
        mv, _steps = load_movements(path)
    except Exception as exc:
        return [], "could not read the movement file: %s" % str(exc)[:160]
    if mv is None or not len(mv):
        return [], "the movement file has no rows"
    vals, col = month_series(mv)
    got = [v for v in (vals or []) if v]
    if got:
        return _month_labels(got), "months read from the '%s' column" % col
    return [], ("no month or date column found in this file - columns "
                "are: %s" % ", ".join(str(c) for c in list(mv.columns)[:12]))


def _month_labels(dates):
    return sorted({d.strftime("%m/%Y") for d in dates},
                  key=lambda t: (t[3:], t[:2]), reverse=True)


def _movement_sheet(book):
    """The tab holding the contract movements.

    Chosen by CONTENT, not by name order: the tab whose header row
    actually carries 'Move Type' together with a lease reference. Where
    more than one qualifies, a tab called 'Contract Movement' wins - the
    RR06/ARO06 export's own name - ahead of the older prepared working
    papers. Returns (frame, sheet name) or (None, None)."""
    fits = []
    for name, raw in book.items():
        if raw is None or not len(raw):
            continue
        hr = _find_header_row(raw, "Move Type", search_rows=25)
        if hr is None:
            raw = _name_move_type(raw)
            hr = _find_header_row(raw, "Move Type", search_rows=25)
        if hr is None:
            continue
        heads = {norm_head(v) for v in raw.iloc[hr].tolist()}
        if heads & {"lease reference", "lease ref", "schedule number",
                    "contract reference"}:
            fits.append((str(name), raw))
    if not fits:
        return None, None
    for want in ("contract movement", "sa movement",
                 "consolidated movements"):
        for name, raw in fits:
            if norm_head(name) == want:
                return raw, name
    for name, raw in fits:               # any tab mentioning movement
        if "movement" in norm_head(name):
            return raw, name
    return fits[0][1], fits[0][0]


MOVE_WORDS = ("addition", "renewal", "removal", "portfolio movement",
              "annual escalation", "termination", "extension",
              "rent review", "new", "modification", "acquisition")
_REF_HEADS = {"lease reference", "lease ref", "schedule number",
              "contract reference"}


def _name_move_type(raw, search_rows=25):
    """1.0.106 / 2.10.9: the real RR06 'Contract Movement' tab has no
    'Move Type' heading - a merged 'Movement' banner sits over two columns
    whose own header cells are blank: the movement date (A) and the move
    type (B: Addition / Renewal / Removal ...). The header row is found by
    its lease reference heading, and a blank-headed column whose cells are
    move types gets the heading 'Move Type'. The frame is returned
    unchanged (the same object) when there is no such column."""
    for i in range(min(search_rows, len(raw))):
        heads = [norm_head(v) for v in raw.iloc[i].tolist()]
        if not set(heads) & _REF_HEADS:
            continue
        body = raw.iloc[i + 1:i + 401]
        for j, h in enumerate(heads):
            if h and h != "nan":
                continue
            vals = [norm_head(v) for v in body.iloc[:, j].tolist()]
            vals = [v for v in vals if v and v != "nan"]
            if len(vals) < 1:
                continue
            if sum(v in MOVE_WORDS for v in vals) >= 0.8 * len(vals):
                raw = raw.copy()
                raw.iat[i, j] = "Move Type"
                return raw
        return raw
    return raw


def load_movements(path):
    """Movement source. Accepts either the prepared movements workbook
    ('SA Movement' / 'Consolidated movements' sheet, with its optional
    'Step Payment' sheet) or a raw RR06 / ARO06 export ('Contract
    Movement' tab). Returns (movements, step_payments|None)."""
    book = _read_workbook(path, sheet_name=None, header=None)
    raw, _sheet_name = _movement_sheet(book)
    if raw is None:
        # fall back to recognising a movement sheet by its HEADERS, so
        # ad-hoc extracts (Sheet1 etc.) work too
        for cand in book.values():
            hr = _find_header_row(cand, "Move Type")
            if hr is None:
                continue
            probe = _reheader(cand, hr)
            if "Lease Reference" in probe.columns:
                raw = cand
                break
    if raw is None:
        raise ValueError("No movement sheet found in '%s' - need a sheet "
                         "named 'SA Movement', 'Consolidated movements' or "
                         "'Contract Movement', or any sheet with 'Move "
                         "Type' and 'Lease Reference' columns.%s"
                         % (Path(str(path)).name, what_file(book, "movements")))
    hr = _find_header_row(raw, "Move Type", search_rows=25)
    if hr is None:
        raise ValueError("Could not find a 'Move Type' header row in the "
                         "movement file.")
    mv = _reheader(raw, hr)
    mv.columns = [re.sub(r"\s+", " ", c).strip() for c in mv.columns]
    # the DCF date column arrives with varying capitalisation / wording;
    # normalise it so the engine's "DCF date" lookup always finds it
    if "DCF date" not in mv.columns:
        for c in list(mv.columns):
            if norm_head(c) in ("dcf date", "dcf loaded date",
                                "date dcf loaded", "dcf load date",
                                "dcf", "dcf dte"):
                mv = mv.rename(columns={c: "DCF date"})
                break
    if "Lease Reference" not in mv.columns:
        for c in list(mv.columns):
            if norm_head(c) in ("lease reference", "lease ref",
                                "schedule number", "contract reference"):
                mv = mv.rename(columns={c: "Lease Reference"})
                break
    if "Move Type" not in mv.columns:
        for c in list(mv.columns):
            if norm_head(c) in ("move type", "movement type",
                                "type of move"):
                mv = mv.rename(columns={c: "Move Type"})
                break
    mv = mv[mv["Lease Reference"].notna()]
    mv["Lease Reference"] = mv["Lease Reference"].astype(str).str.strip()

    # ONE movement month for the whole run. Whatever column it comes
    # from (the DCF date on the Contract Movement tab by default), it is
    # written back as 'Month' so the month dropdown, the processing
    # window and the drafted rows all agree.
    _vals, _src = month_series(mv)
    if _vals is not None:
        mv["Month"] = _vals
        mv.attrs["month_source"] = _src
    else:
        mv.attrs["month_source"] = ""
    mv.attrs["sheet_name"] = _sheet_name or ""

    steps = None
    raw_sp = _pick_sheet(book, "Step Payment", contains="step")
    if raw_sp is not None:
        hr = None
        for key in ("Schedule Number", "Schedule No", "Schedule #",
                    "Schedule"):
            hr = _find_header_row(raw_sp, key)
            if hr is not None:
                break
        if hr is not None:
            steps = _normalise_step_columns(_reheader(raw_sp, hr))
            if "Schedule Number" in steps.columns:
                steps = steps[steps["Schedule Number"].notna()]
                steps["Schedule Number"] = (steps["Schedule Number"]
                                            .astype(str).str.strip())
            else:
                steps = None
    return mv, steps


# common variants of the step-payment column names -> the engine's names,
# so an ARO06/RR06 Step Payment tab or a re-saved report that says
# "Start Date" instead of "StartDate" still runs
_STEP_ALIASES = {
    "schedule number": "Schedule Number", "schedule": "Schedule Number",
    "schedule #": "Schedule Number", "schedule no": "Schedule Number",
    "schedule no.": "Schedule Number", "scheduleno": "Schedule Number",
    "schedule_no": "Schedule Number", "lease reference": "Schedule Number",
    "expense type name": "Expense Type Name",
    "expense type": "Expense Type Name",
    "expensetype": "Expense Type Name", "expense_type": "Expense Type Name",
    "startdate": "StartDate", "start date": "StartDate",
    "start_date": "StartDate", "payment start date": "StartDate",
    "payment start": "StartDate", "from date": "StartDate",
    "enddate": "EndDate", "end date": "EndDate", "end_date": "EndDate",
    "payment end date": "EndDate", "payment end": "EndDate",
    "to date": "EndDate",
    "number of payments": "Number of Payments",
    "numberofpayments": "Number of Payments",
    "number_of_payments": "Number of Payments",
    "no of payments": "Number of Payments",
    "no. of payments": "Number of Payments",
    "payments": "Number of Payments",
    "payment amount": "Payment Amount", "paymentamount": "Payment Amount",
    "payment_amount": "Payment Amount", "amount": "Payment Amount",
    "starting payment number": "Starting Payment Number",
    "start payment number": "Starting Payment Number",
    "payment number": "Starting Payment Number",
    "payment frequency": "Payment Frequency",
    "paymentfrequency": "Payment Frequency",
    "payment_frequency": "Payment Frequency",
    "frequency": "Payment Frequency",
}


def _normalise_step_columns(df):
    """Rename recognised variants of the step-payment columns to the
    canonical names (StartDate, EndDate, ...). Existing canonical
    columns are never overwritten."""
    if df is None or not len(df.columns):
        return df
    ren = {}
    for c in df.columns:
        key = re.sub(r"\s+", " ", str(c)).strip().lower()
        canon = _STEP_ALIASES.get(key)
        if canon and canon != c and canon not in df.columns \
                and canon not in ren.values():
            ren[c] = canon
    return df.rename(columns=ren) if ren else df


def _check_step_columns(steps, source):
    """A clear, actionable error instead of a bare KeyError('StartDate')
    when the step-payments data lacks a column the engine needs."""
    # Two accepted shapes: dated blocks (StartDate/EndDate), or the LA
    # step-payment shape that numbers the payments instead.
    if "Starting Payment Number" in steps.columns:
        need = ["Schedule Number", "Number of Payments", "Payment Amount"]
    else:
        need = ["Schedule Number", "StartDate", "EndDate",
                "Number of Payments", "Payment Amount",
                "Payment Frequency"]
    missing = [c for c in need if c not in steps.columns]
    if missing:
        raise ValueError(
            "The step payments data (%s) is missing column(s): %s. "
            "Columns found: %s. Update the Sequentra step payments "
            "report (Lease workflow > Update), or check the movement "
            "file's Step Payment tab - the report needs: %s."
            % (source, ", ".join(missing),
               ", ".join(str(c) for c in list(steps.columns)[:15]),
               ", ".join(need)))


def load_step_payments(path):
    """Sequentra 'PIW Step Payments' report (or any sheet with the same
    columns: Schedule Number / Expense Type Name / StartDate / EndDate /
    Number of Payments / Payment Amount / Payment Frequency). Common
    variants of the column names (e.g. 'Start Date') are accepted."""
    book = _read_workbook(path, sheet_name=None, header=None)
    for raw in book.values():
        hr = None
        for key in ("Schedule Number", "Schedule No", "Schedule #",
                    "Schedule"):
            hr = _find_header_row(raw, key)
            if hr is not None:
                break
        if hr is not None:
            df = _normalise_step_columns(_reheader(raw, hr))
            if "Payment Amount" in df.columns \
                    and "Schedule Number" in df.columns:
                df = df[df["Schedule Number"].notna()]
                df["Schedule Number"] = (df["Schedule Number"].astype(str)
                                         .str.strip())
                return df
    raise ValueError("No step payment sheet found in '%s' (needs "
                     "'Schedule Number' and 'Payment Amount' columns).%s"
                     % (Path(path).name, what_file(book, "steps")))


_norm_head = norm_head          # one definition, one behaviour


def load_asset_report(path):
    """LA 'BU asset' report -> {schedule: [{'Asset ID','Serial Number',
    'Description'}, ...]}.

    Header names vary between BU asset exports, so both the asset column
    and the schedule column are matched loosely: any heading that reads
    as an asset id, and any heading that reads as a schedule /
    transaction / lease reference. References are upper-cased and
    trimmed so they join to the movement file."""
    book = _read_workbook(path, sheet_name=None, header=None)
    asset_names = ("asset id", "assetid", "asset", "asset number",
                   "asset no", "asset #")
    sched_names = ("schedule number", "schedule", "schedule no",
                   "schedule #", "transaction", "transaction #",
                   "transaction number", "lease reference", "lease ref",
                   "contract number", "record id")
    for raw in book.values():
        if raw is None or not len(raw):
            continue
        hr = None
        for i in range(min(20, len(raw))):
            cells = [_norm_head(v) for v in raw.iloc[i].tolist()]
            if any(c in asset_names for c in cells):
                hr = i
                break
        if hr is None:
            continue
        df = _reheader(raw, hr)
        heads = {c: _norm_head(c) for c in df.columns}
        asset_col = next((c for c, n in heads.items()
                          if n in asset_names), None)
        sched_col = next((c for c, n in heads.items()
                          if n in sched_names), None)
        if sched_col is None:      # last resort: any heading mentioning it
            sched_col = next((c for c, n in heads.items()
                              if "schedule" in n or "transaction" in n
                              or "lease ref" in n), None)
        if sched_col is None or asset_col is None:
            continue
        out = {}
        for _, r in df.iterrows():
            ref = _text(r.get(sched_col)).upper()
            aid = r.get(asset_col)
            if not ref or ref == "NAN" or aid is None or pd.isna(aid):
                continue
            serial = _text(r.get("Serial Number")) or \
                _text(r.get("Serial #"))
            out.setdefault(ref, []).append({
                "Asset ID": int(aid) if isinstance(aid, float)
                and aid == int(aid) else aid,
                "Serial Number": serial,
                "Description": _text(r.get("Description")),
            })
        if out:
            return out
    raise ValueError("No sheet with 'Asset ID' plus a schedule column "
                     "found in the asset report.")


# only genuine 'Record ID' variants - deliberately NOT 'Schedule Number'
# or 'Lease Reference', so picking the step-payments or movement file for
# this slot fails loudly instead of loading the wrong data
SEQ_KEY_ALIASES = ("record id", "recordid", "record_id", "record no",
                   "record number", "record #")


def load_sequentra(path):
    """Sequentra 'Validation - Record Info' export. Key: 'Record ID'.

    Scans every sheet (not just the first) and accepts the common
    variants of the key column, so trimmed or re-saved exports still
    work. If nothing matches, the error names the sheets and columns
    actually found - which usually reveals that the wrong file was
    picked for this slot."""
    book = _read_workbook(path, sheet_name=None, header=None)
    found = None
    for sheet_name, raw in book.items():
        if raw is None or not len(raw):
            continue
        # look deeper than the default: some exports carry long preambles
        for i in range(min(30, len(raw))):
            cells = [str(v).strip().lower() for v in raw.iloc[i].tolist()]
            for alias in SEQ_KEY_ALIASES:
                if alias in cells:
                    found = (sheet_name, i, alias)
                    break
            if found:
                break
        if found:
            break

    if found is None:
        # build a helpful description of what the file actually contains
        desc = []
        seen_text = []
        for sheet_name, raw in list(book.items())[:6]:
            cols = []
            if raw is not None and len(raw):
                for i in range(min(8, len(raw))):
                    row = [str(v).strip() for v in raw.iloc[i].tolist()
                           if str(v).strip() and str(v).strip() != "nan"]
                    seen_text.extend(row)
                    if len(row) >= 2 and not cols:
                        cols = row[:6]
            desc.append("'%s' (%s)" % (sheet_name,
                                       ", ".join(cols) if cols
                                       else "no header row found"))
        hint = ""
        joined = " ".join(seen_text + desc).lower()
        if "party role type" in joined or "shiptokey" in joined:
            hint = (" This looks like the ParticipantValidation master, "
                    "not the Sequentra validation export - it belongs in "
                    "the CIW master slot.")
        elif what_file(book, "sequentra"):     # 1.0.106 / 2.10.9: names the slot
            hint = what_file(book, "sequentra")
        elif "move type" in joined or "contract movement" in joined:
            hint = (" This looks like an RR06/ARO06 movement file, not "
                    "the Sequentra validation export.")
        elif "expense type" in joined or "number of payments" in joined:
            hint = (" This looks like the step payments report, not the "
                    "Sequentra validation export.")
        raise ValueError(
            "Could not find a 'Record ID' column in '%s'.%s Sheets found: "
            "%s." % (Path(path).name, hint, "; ".join(desc)))

    sheet_name, hr, alias = found
    df = _reheader(book[sheet_name], hr)
    df.columns = [re.sub(r"\s+", " ", str(c)).strip() for c in df.columns]
    # normalise whichever alias matched to the canonical 'Record ID'
    keycol = next((c for c in df.columns
                   if str(c).strip().lower() == alias), None)
    if keycol and keycol != "Record ID":
        df = df.rename(columns={keycol: "Record ID"})
    df = df[df["Record ID"].notna()]
    df["Record ID"] = df["Record ID"].astype(str).str.strip()
    df = df[~df["Record ID"].str.lower().isin(("", "nan", "none"))]
    # 1.0.106 / 2.10.9: the 'Record Info Tab with Custom Fields' export has
    # no Address 1 / Address 2 - its address is Building Number + Street
    # Name, Suite, Town. Without them the CIW address line was the lease
    # name. Built only when the export has no address columns of its own.
    if "Address 1" not in df.columns and "Street Name" in df.columns:
        df = df.copy()
        num = df["Building Number"].map(_text) if "Building Number" in \
            df.columns else pd.Series("", index=df.index)
        df["Address 1"] = (num + " " + df["Street Name"].map(_text)) \
            .str.strip()
        if "Address 2" not in df.columns:
            def _addr2(r):
                city = _text(r.get("City")).lower()
                got = []
                for c in ("Suite", "Town"):
                    t = _text(r.get(c))
                    if t and t.lower() != city and t not in got:
                        got.append(t)
                return ", ".join(got)
            df["Address 2"] = df.apply(_addr2, axis=1)
    return df.drop_duplicates(subset="Record ID", keep="first")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
_SERIAL = re.compile(r"^\d{5}(\.0+)?$")
_ISO_DAY = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})"
                      r"(?:[ T]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?$")
_DMY_DAY = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})"
                      r"(?: \d{1,2}:\d{2}(?::\d{2})?)?$")


def _to_date(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (date, datetime)) and pd.isna(v):
        return None                   # 1.0.106 / 2.10.9: NaT is a datetime, and truthy
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, numbers.Real) and not isinstance(v, bool):
        # 1.0.106 / 2.10.9: a date cell that arrives as a number is an Excel
        # serial (45809 = 2025-06-01); any other number is not a date (9716
        # used to come back as the year 9716)
        if pd.isna(v) or not 15000 <= v <= 80000:
            return None
        return date(1899, 12, 30) + timedelta(days=int(v))
    s = str(v).strip()
    if _SERIAL.match(s) and 15000 <= float(s) <= 80000:
        return date(1899, 12, 30) + timedelta(days=int(float(s)))
    # 1.0.106 / 2.10.9: the common shapes without pandas (a call each was
    # most of a month run): 2026-07-01[ 00:00:00] (also 2026/07/01, which
    # pandas read day-first as 7 January), 01/07/2026
    m = _ISO_DAY.match(s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass                      # left to pandas, as before
    m = _DMY_DAY.match(s)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass                      # 12/25/2026: pandas reads it month-first
    # ISO-style strings (2026-07-01) must not be re-ordered; only
    # slash-formats (01/07/2026) are day-first
    dayfirst = "/" in s and not re.match(r"^\d{4}-", s)
    try:
        ts = pd.to_datetime(s, errors="coerce", dayfirst=dayfirst)
        if pd.isna(ts):
            return None
        # real files carry stray values that parse to impossible years;
        # .date() raises on those rather than returning anything useful
        return ts.date()
    except (ValueError, OverflowError, NotImplementedError, TypeError):
        return None


def _months_between(a, b):
    """Whole months from a to b (calendar difference)."""
    if a is None or b is None:
        return None
    return (b.year - a.year) * 12 + (b.month - a.month)


def _num(v):
    try:
        f = float(v)
        return None if pd.isna(f) else f
    except (TypeError, ValueError):
        return _text_num(v)


_NUM_JUNK = re.compile(r"^(?:R|ZAR)\s*|\s*(?:%|ZAR)$", re.I)


def _text_num(v):
    """1.0.106 / 2.10.9: a number stored as text the way exports write them
    - '1 234 567.89', '1,234,567.89', '1.234.567,89', '1 234,56',
    'R 12 500', '(1 500.00)' for minus, '7.5%' -> 7.5. None when it is not
    a number. A lone comma with three digits after it is a thousands mark
    ('12,500' -> 12500); otherwise it is the decimal mark ('12,5')."""
    if not isinstance(v, str):
        return None
    t = v.strip().replace("\u00a0", " ").replace("\u202f", " ")
    neg = t.startswith("(") and t.endswith(")")
    if neg:
        t = t[1:-1].strip()
    t = _NUM_JUNK.sub("", t).strip()
    if t.startswith("-"):
        neg, t = not neg, t[1:].strip()
    t = t.replace(" ", "").replace("'", "")
    if not t or not re.fullmatch(r"[0-9.,]+", t) or not re.search(r"\d", t):
        return None
    if "," in t and "." in t:
        dec = "," if t.rfind(",") > t.rfind(".") else "."
        t = t.replace("." if dec == "," else ",", "")
        t = t.replace(",", ".")
    elif "," in t:
        parts = t.split(",")
        if len(parts) > 2 or len(parts[-1]) == 3:
            t = t.replace(",", "")
        else:
            t = t.replace(",", ".")
    elif t.count(".") > 1:
        if not all(len(g) == 3 for g in t.split(".")[1:]):
            return None
        t = t.replace(".", "")
    try:
        f = float(t)
    except ValueError:
        return None
    return -f if neg else f


def _text(v):
    if v is None or (isinstance(v, float) and pd.isna(v)) or pd.isna(v):
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("nan", "none", "nat") else s


def month_start(d):
    return date(d.year, d.month, 1)


def _clean(v):
    """CIW-safe text: drop parentheses and other special characters that
    break the LA import, collapse the whitespace left behind. Keeps
    letters, digits, spaces, hyphen, ampersand, slash, comma and dot."""
    s = _text(v)
    if not s:
        return ""
    s = re.sub(r"[^0-9A-Za-z &/,.\-]", " ", s)
    return re.sub(r"\s{2,}", " ", s).strip()


def _clean_funder(v):
    """A funder (landlord) name for the PIW and the CIW: no special characters
    at all (finance, Oct 2026) - letters, digits and single spaces only;
    "&" reads "and" so "A & B Properties" stays readable."""
    s = _text(v)
    if not s:
        return ""
    s = re.sub(r"\s*&\s*", " and ", s)
    s = re.sub(r"[^0-9A-Za-z ]", " ", s)
    return re.sub(r"\s{2,}", " ", s).strip()


_TRUE_UP = re.compile(r"true\s*[-_]?\s*up", re.I)


def is_true_up(expense_type):
    """A true-up adjustment charge (Sequentra expense type)."""
    return bool(_TRUE_UP.search(_text(expense_type)))


def without_true_ups(steps):
    """The step payments with the true-up adjustment charges taken out
    (finance, Oct 2026: PIW additions carry no true-ups; MDIW renewals
    keep them)."""
    if steps is None or "Expense Type Name" not in steps.columns:
        return steps
    return steps[~steps["Expense Type Name"].map(is_true_up)]


def month_end(d):
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


# Dates written to the LA import sheets. ISO (2026-08-01) per the
# team's format standard - unambiguous and sorted correctly by Excel.
DATE_FORMAT = "%Y-%m-%d"


def _fmt(d):
    return d.strftime(DATE_FORMAT) if d else ""


# ---------------------------------------------------------------------------
# Legal Entity per lease from the movement file's Contracts tab
# ---------------------------------------------------------------------------
def load_contract_entities(path):
    """{lease reference: legal entity} from the RR06/ARO06 export's
    Contracts tab (column 'Legal Entity'). This is the team's entity
    mapping for the LA import sheets: Entity, BU and Lessee are all
    this value. Tolerant of column-name variants and of the tab being
    named differently, but never reads the Contract Movement tab."""
    out = {}
    if not path or not Path(str(path)).exists():
        return out
    try:
        book = _read_workbook(path, sheet_name=None, header=None)
    except Exception:
        return out
    ref_names = ("lease reference", "schedule number", "schedule #",
                 "schedule", "contract reference", "record id",
                 "contract number")
    ent_names = ("legal entity", "legal entities", "legal entity name",
                 "legalentity")
    # prefer a sheet literally named Contracts, then fall back to any
    # sheet carrying a Legal Entity column (skipping movement tabs)
    names = sorted(book, key=lambda n: (0 if "contract" in str(n).lower()
                                        and "movement" not in
                                        str(n).lower() else 1))
    for sheet in names:
        raw = book[sheet]
        if raw is None or not len(raw) or \
                "movement" in str(sheet).lower():
            continue
        for i in range(min(15, len(raw))):
            cells = [re.sub(r"\s+", " ", str(v)).strip().lower()
                     for v in raw.iloc[i].tolist()]
            rcol = next((j for j, c in enumerate(cells)
                         if c in ref_names), None)
            ecol = next((j for j, c in enumerate(cells)
                         if c in ent_names), None)
            if rcol is None or ecol is None:
                continue
            body = raw.iloc[i + 1:]
            for ref, ent in zip(body.iloc[:, rcol], body.iloc[:, ecol]):
                ref = str(ref).strip().upper()
                ent = "" if ent is None else str(ent).strip()
                if ref and ref != "NAN" and ent and ent.lower() != "nan":
                    out.setdefault(ref, ent)
            if out:
                return out
    return out


# ---------------------------------------------------------------------------
# Entity lookup built from the LA register itself
# ---------------------------------------------------------------------------
def build_entity_lookup(la):
    """Country -> most common (Entity, BU, Lessee) from the LA register."""
    lookup = {}
    cols = ("Asset Location(s)", "Entity", "BU", "Lessee")
    if not all(c in la.columns for c in cols):
        return lookup
    sub = la[list(cols)].dropna(subset=["Asset Location(s)"])
    for country, grp in sub.groupby(sub["Asset Location(s)"].astype(str)
                                    .str.strip()):
        lookup[country.lower()] = (
            _text(grp["Entity"].mode().iat[0]) if not grp["Entity"].mode().empty else "",
            _text(grp["BU"].mode().iat[0]) if not grp["BU"].mode().empty else "",
            _text(grp["Lessee"].mode().iat[0]) if not grp["Lessee"].mode().empty else "",
        )
    return lookup


# ---------------------------------------------------------------------------
# Step payment transformation
#
# Verified against the team's own June 2026 files:
#   * duplicate rows in the movement Step Payment sheet are dropped
#     (A7219P01 appears twice; the team's MDIW uses the amount once)
#   * amounts are summed across expense types per period
#     (4168P002: Base Rent 71,300 + Operating Costs 14,006.40 = 85,306.40)
#   * PIW: a zero-payment lead-in is inserted when payments start after
#     the lease start date (4168P002: 2 months at 0, duration 62)
#   * MDIW: blocks are clipped to the modification month and numbering
#     restarts at 1 (A7219P01: 12-month block becomes 11 remaining)
# ---------------------------------------------------------------------------
# months between payments for each stated frequency
FREQ_INTERVAL = {
    "monthly": 1, "month": 1,
    "quarterly": 3, "quarter": 3, "english quarters": 3,
    "semi-annually": 6, "semi annually": 6, "semiannually": 6,
    "semi-annual": 6, "semi annual": 6, "semiannual": 6,
    "bi-annually": 6, "bi-annual": 6, "biannual": 6,
    "annually": 12, "annual": 12, "yearly": 12, "year": 12,
}


def freq_interval(freq):
    """Months between payments; None for one-time / unknown."""
    f = str(freq).strip().lower()
    if f in ("one-time", "one time", "onetime", "once"):
        return None
    return FREQ_INTERVAL.get(f, 1)      # default monthly


def _grouped_blocks(steps, schedule):
    """Deduped, expense-type-summed payment blocks for one schedule,
    keeping each block's own payment frequency (a schedule can mix
    Annually and One-time)."""
    sub = steps[steps["Schedule Number"] == schedule].copy()
    if sub.empty:
        return None, "Monthly"
    # the dated shape has StartDate/EndDate; the LA shape numbers the
    # payments instead, so those columns may simply not be there
    sub["_start"] = (sub["StartDate"].map(_to_date)
                     if "StartDate" in sub.columns else None)
    sub["_end"] = (sub["EndDate"].map(_to_date)
                   if "EndDate" in sub.columns else None)
    sub["_n"] = sub["Number of Payments"].map(_num)
    sub["_amt"] = sub["Payment Amount"].map(_num)
    if "Payment Frequency" in sub.columns:
        sub["_freq"] = sub["Payment Frequency"].map(
            lambda v: _text(v) or "Monthly")
        freq = _text(sub["Payment Frequency"].iloc[0]) or "Monthly"
    else:
        sub["_freq"] = "Monthly"
        freq = "Monthly"
    exp_col = "Expense Type Name" if "Expense Type Name" in sub.columns \
        else None
    dedupe_cols = ["_start", "_end", "_n", "_amt"] + \
        ([exp_col] if exp_col else [])
    sub = sub.drop_duplicates(subset=dedupe_cols)
    grouped = (sub.groupby(["_start", "_end"], dropna=False)
               .agg(n=("_n", "max"), amt=("_amt", "sum"),
                    freq=("_freq", "first"))
               .reset_index().sort_values("_start"))
    grouped = grouped[grouped["n"].notna() & (grouped["n"] > 0)]
    return (grouped if not grouped.empty else None), freq


def schedule_frequencies(steps, schedule):
    """Distinct payment frequencies for a schedule (lower-cased)."""
    if steps is None:
        return set()
    sub = steps[steps["Schedule Number"] == schedule]
    if "Payment Frequency" not in sub.columns:
        return set()
    return {str(v).strip().lower() for v in
            sub["Payment Frequency"].dropna()}


def _add_months(d, k):
    """The first of the month k months after d."""
    total = d.year * 12 + (d.month - 1) + int(k)
    return date(total // 12, total % 12 + 1, 1)


def _clean_blocks(steps, schedule):
    """The de-duplicated payment blocks for one schedule.

    Two shapes are accepted: dated blocks (StartDate/EndDate), and the
    LA step-payment shape that numbers payments instead of dating them.
    Sequentra step payments are all fixed payments, so rows are dropped
    only when they are literally the same row twice - two rows for the
    same payment are different charge types and must both survive to be
    summed."""
    sub = steps[steps["Schedule Number"] == schedule].copy()
    if sub.empty:
        return None, "Monthly"
    has_no = "Starting Payment Number" in sub.columns
    sub["_start"] = (sub["StartDate"].map(_to_date)
                     if "StartDate" in sub.columns else None)
    sub["_end"] = (sub["EndDate"].map(_to_date)
                   if "EndDate" in sub.columns else None)
    sub["_n"] = sub["Number of Payments"].map(_num)
    sub["_amt"] = sub["Payment Amount"].map(_num)
    if "Payment Frequency" in sub.columns:
        sub["_freq"] = sub["Payment Frequency"].map(
            lambda v: _text(v) or "Monthly")
        freq = _text(sub["Payment Frequency"].iloc[0]) or "Monthly"
    else:
        sub["_freq"] = "Monthly"
        freq = "Monthly"
    exp_col = "Expense Type Name" if "Expense Type Name" in sub.columns \
        else None
    dedupe = ["_start", "_end", "_n", "_amt", "_freq"] + \
        ([exp_col] if exp_col else []) + \
        (["Starting Payment Number"] if has_no else [])
    sub = sub.drop_duplicates(subset=dedupe)
    sub = sub[sub["_n"].notna() & (sub["_n"] > 0)]
    return (sub if len(sub) else None), freq


def payment_number_amounts(steps, schedule, base_month):
    """{month: total charged} for the LA step-payment shape, which
    numbers payments instead of dating them.

    Payment 1 falls in base_month (the lease start), payment k in
    base_month + (k-1) intervals. Every row covering a payment is
    SUMMED - the draft splits rent, operating costs and other charges
    into separate rows for the same payment, and the schedule LA needs
    is their total.

    Returns (amounts, first, last, notes) or (None, ...) when the rows
    cannot be placed."""
    sub, freq = _clean_blocks(steps, schedule)
    if sub is None or base_month is None:
        return None, None, None, []
    if "Starting Payment Number" not in sub.columns:
        return None, None, None, []
    notes = []
    freqs = {_text(f).strip().lower() for f in sub["_freq"] if _text(f)}
    if len(freqs) > 1:
        notes.append("payment rows mix %s frequencies - the schedule was "
                     "built on the most common one; check it against the "
                     "agreement" % "/".join(sorted(freqs)))
    main = sub["_freq"].astype(str).str.strip().str.lower().mode()
    main = main.iat[0] if len(main) else "monthly"
    interval = freq_interval(main) or 1
    base = month_start(base_month)
    amounts, last_no, highest = {}, 0, 0
    for _i, r in sub.iterrows():
        try:
            n = int(r["_n"])
        except (TypeError, ValueError):
            continue
        if n <= 0:
            continue
        start_no = r.get("Starting Payment Number")
        try:
            start_no = int(float(start_no))
        except (TypeError, ValueError):
            start_no = last_no + 1          # blank: carry on in sequence
        if start_no <= 0:
            start_no = 1
        amt = r["_amt"] or 0.0
        for k in range(n):
            m = _add_months(base, (start_no - 1 + k) * interval)
            amounts[m] = amounts.get(m, 0.0) + amt
        last_no = start_no + n - 1
        highest = max(highest, last_no)
    if not amounts:
        return None, None, None, notes
    return amounts, min(amounts), _add_months(
        base, (highest - 1) * interval), notes


def monthly_amounts(steps, schedule):
    """Lay every payment block on a calendar and total per month.

    Returns (amounts, first_month, last_month, notes) where amounts is
    {month start: total charged that month}. Base rent, operating costs
    and any other expense type are SUMMED into one figure per month,
    even when their blocks run over different date ranges - which is
    what makes the LA rows one payment per month rather than one per
    expense type. Non-monthly blocks charge only in their due months;
    the months in between are nil.

    Returns (None, ...) when a block has no start date, so the caller
    can fall back to the sequential builder."""
    sub, freq = _clean_blocks(steps, schedule)
    if sub is None:
        return None, None, None, ["no step payments found for this lease"]
    amounts, spans, notes = {}, [], []
    # iterrows, not itertuples: pandas renames columns that start with
    # an underscore when it builds namedtuples
    for _i, r in sub.iterrows():
        s_date, e_date = r["_start"], r["_end"]
        n, amt = int(r["_n"]), (r["_amt"] or 0.0)
        if s_date is None:
            return None, None, None, ["step payment rows had no start date"]
        interval = freq_interval(_text(r["_freq"]) or freq)
        if e_date:
            span = (_months_between(s_date, e_date) or 0) + 1
            if span > 0 and span != n:
                if interval == 1:
                    notes.append("payment count %d adjusted to %d-month "
                                 "period %s - %s"
                                 % (n, span, s_date, e_date))
                n = span
        if n <= 0:
            continue
        first = month_start(s_date)
        spans.append((first, _add_months(first, n - 1)))
        if interval is None:                 # one-time: a single charge
            amounts[first] = amounts.get(first, 0.0) + amt
            continue
        k = 0
        while k < n:
            m = _add_months(first, k)
            amounts[m] = amounts.get(m, 0.0) + amt
            k += interval
    if not spans:
        return None, None, None, ["no step payments found for this lease"]
    return (amounts, min(a for a, _b in spans),
            max(b for _a, b in spans), notes)


def _compress(amounts, first_month, last_month, schedule):
    """Months -> LA step rows, merging consecutive months that carry the
    same amount. Starting Payment Number always continues from the
    previous row's number plus its count."""
    rows, start_no = [], 1
    m, run_amt, run_len = first_month, None, 0
    while m <= last_month:
        amt = round(amounts.get(m, 0.0), 2)
        if run_amt is None or amt != run_amt:
            if run_amt is not None:
                rows.append([schedule, start_no, run_len, run_amt,
                             "Monthly"])
                start_no += run_len
            run_amt, run_len = amt, 1
        else:
            run_len += 1
        m = _add_months(m, 1)
    if run_amt is not None:
        rows.append([schedule, start_no, run_len, run_amt, "Monthly"])
    return rows


def build_step_blocks(steps, schedule, anchor, zero_lead,
                      base_month=None):
    """Convert payment blocks to LA step payment rows.

    anchor      -> PIW: the acquisition start (earlier of the BO date and
                   the lease start date); MDIW: the DCF date. Months
                   before it are dropped.
    zero_lead   -> PIW mode: when the anchor falls before the first
                   payment, the months in between are carried at zero
                   rather than cut off.

    Returns (rows, duration, first_amount, notes).
    """
    if steps is None:
        return [], None, None, ["no Step Payment sheet in movement file"]
    amounts, first, last, notes = monthly_amounts(steps, schedule)
    if amounts is None:
        # the LA shape: payments numbered from the lease start
        amounts, first, last, notes = payment_number_amounts(
            steps, schedule, base_month or anchor)
    if amounts is None:
        return _legacy_step_blocks(steps, schedule, anchor, zero_lead,
                                   notes)
    clip = month_start(anchor) if anchor else None
    start = first
    if clip is not None:
        if clip > last:
            return [], None, None, ["no remaining payments after the "
                                    "anchor date"]
        start = clip if (zero_lead and clip < first) else max(first, clip)
    freqs = schedule_frequencies(steps, schedule)
    if freqs - {"monthly"}:
        notes.append("non-monthly payments charged in their due months "
                     "only; the months between are nil")
    rows = _compress(amounts, start, last, schedule)
    if not rows:
        return [], None, None, ["no remaining payments after the "
                                "anchor date"]
    duration = sum(r[2] for r in rows)
    first_amt = next((r[3] for r in rows if r[3]), rows[0][3])
    return rows, duration, first_amt, notes


def _legacy_step_blocks(steps, schedule, anchor, zero_lead, notes=None):
    """Sequential builder, used only when a block carries no start date
    and so cannot be placed on a calendar."""
    notes = list(notes or [])
    notes.append("payment blocks had no dates - rows built in sequence")
    grouped, freq = _grouped_blocks(steps, schedule)
    if grouped is None:
        return [], None, None, notes + ["no step payments found"]
    rows, start_no = [], 1
    for _i, r in grouped.iterrows():
        n = int(r["n"])
        if n <= 0:
            continue
        rows.append([schedule, start_no, n, round(r["amt"] or 0.0, 2),
                     "Monthly"])
        start_no += n
    if not rows:
        return [], None, None, notes
    duration = sum(r[2] for r in rows)
    first_amt = next((r[3] for r in rows if r[3]), rows[0][3])
    return rows, duration, first_amt, notes



def build_rent_review_rows(steps, schedule, wm_start, begin=None):
    """Monthly Recurring payment-adjustment rows for one schedule.

    Reproduces the team's Rent Review pattern (verified against
    Rent Review_28.06.2026): one row per month from the most recent
    payment-change date up to the end of the payment schedule.
    Amount = summed step amount covering that month; the remeasurement
    date is capped at the workflow month.
    """
    if steps is None:
        return [], ["no Step Payment sheet in movement file"]
    grouped, _ = _grouped_blocks(steps, schedule)
    if grouped is None:
        return [], ["no step payments found in movement file"]

    wm_end = month_end(wm_start)
    starts = [s for s in grouped["_start"] if s is not None]
    ends = [e for e in grouped["_end"] if e is not None]
    if not starts or not ends:
        return [], ["step payment rows had no dates"]
    if begin is not None:
        begin = month_start(begin)      # charge start from the agreement
    else:
        changed = [s for s in starts if s <= wm_end]
        begin = month_start(max(changed)) if changed else wm_start
    finish = month_start(max(ends))

    def covering_amount(day):
        total, found = 0.0, False
        for _, r in grouped.iterrows():
            if r["_start"] and r["_end"] and r["_start"] <= day <= r["_end"]:
                total += r["amt"] or 0.0
                found = True
        return round(total, 2) if found else None

    rows, notes, m = [], [], begin
    while m <= finish:
        amt = covering_amount(m)
        if amt is not None:
            rows.append({
                "Schedule Number": schedule,
                "Remeasurement Effective Date": _fmt(min(m, wm_start)),
                "Effective Date": _fmt(m),
                "Index Basis": "", "Rate": "",
                "Amount": amt,
                "Timing": FIXED["rent_review_timing"],
                "Reason": FIXED["rent_review_reason"],
                "Comments": FIXED["rent_review_comment"],
            })
        m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    if not rows:
        notes.append("no payment blocks cover the adjustment period")
    return rows, notes


# ---------------------------------------------------------------------------
# The workflow itself
# ---------------------------------------------------------------------------
def run_workflow(la, movements, sequentra, steps, workflow_month=None,
                 lookback=1, verified_ibr=None, rent_review_starts=None,
                 assets=None, rates=None, movement_label="RR06",
                 ciw_master=None, contract_entities=None):
    """Reconcile and generate all LA import rows.

    lookback: how many months of movements to process, ending at the
    workflow month (raw RR06/ARO06 exports carry the full history since
    2013; the prepared working papers carried about six months).

    verified_ibr: Schedule Number -> rate, from the tracker's IBR
    Register; a verified rate beats every other source.

    Returns dict of DataFrames keyed by output sheet name.
    """
    verified_ibr = verified_ibr or {}
    # where the movements and their month actually came from - recorded
    # before any filtering, and reported on the Recon Summary
    _mv_tab = ""
    _mv_month_col = ""
    try:
        _mv_tab = movements.attrs.get("sheet_name", "")
        _mv_month_col = movements.attrs.get("month_source", "")
    except Exception:
        pass
    rent_review_starts = rent_review_starts or {}
    assets = assets or {}
    rates = rates or {}
    ciw_master = ciw_master or {}
    # compared the way the PIW writes them: no special characters
    steps_piw = without_true_ups(steps)
    m_funders = {_clean_funder(f).lower() for f in ciw_master.get("funders", set())}
    m_facilities = ciw_master.get("facilities", set())
    m_costs = ciw_master.get("costs", set())
    m_fac_addr = ciw_master.get("facility_addr", {})
    m_cost_desc = ciw_master.get("cost_desc", {})

    def rate_month_for(ref):
        """The month whose approved rates apply: the lease Signature Date if
        present, else Current Term Start Date, else Move-In Date, else
        the workflow month."""
        for col in ("Signature Date", "Current Term Start Date",
                    "Move-In Date"):
            d = _to_date(seq_get(ref, col))
            if d is not None:
                return month_start(d)
        return wm_start

    def table_ibr(ref, term, cur_ibr, cur_src):
        """SA leases: the approved rate table outranks everything except a
        verified rate. The rate month comes from the lease Signature
        Date. Returns (ibr, source, band, code, rate_month)."""
        band = rate_band(term)
        if cur_src.startswith("Verified"):
            return cur_ibr, cur_src, band or "", "", ""
        if not rates or _text(seq_get(ref, "Country")) != "South Africa":
            return cur_ibr, cur_src, band or "", "", ""
        rate, band, code, rmonth = lookup_rate(rates, rate_month_for(ref),
                                               term)
        if rate is not None:
            return rate, "Approved rate table (%s, %s)" % (code, rmonth), \
                band, code, rmonth
        add_exc(ref, "IBR", "South African lease but no approved rate found "
                "for this term/month - update Data/%s" % RATES_NAME)
        return cur_ibr, cur_src, band or "", "", ""
    la_ids = set(la["Transaction #"]) if "Transaction #" in la.columns else set()
    seq = sequentra.set_index("Record ID")

    # Current LA payment per transaction, for the payment-mismatch check
    la_pay = {}
    pay_col = next((c for c in la.columns
                    if "Current Payment (Transactional" in str(c)), None)
    if pay_col:
        for _, r in la.iterrows():
            la_pay[r["Transaction #"]] = _num(r[pay_col])

    entity_lookup = build_entity_lookup(la)

    # Workflow month: given, else the latest Month in the movement file
    if workflow_month is None:
        months = [_to_date(v) for v in movements.get("Month", pd.Series())]
        months = [m for m in months if m]
        workflow_month = month_start(max(months)) if months \
            else month_start(date.today())
    wm_start, wm_end = month_start(workflow_month), month_end(workflow_month)
    month_label = wm_start.strftime("%B %Y")

    # Limit to the processing window. A row whose month cannot be read
    # is NOT carried into every month "for safety" - that quietly put
    # every undated movement into every run. Such rows are dropped and
    # counted, and the count is reported on the Recon Summary.
    rows_in_file = len(movements)
    rows_no_month = 0
    if lookback and "Month" in movements.columns:
        lo = date(wm_start.year, wm_start.month, 1)
        for _ in range(int(lookback) - 1):
            lo = date(lo.year - (lo.month == 1),
                      (lo.month - 2) % 12 + 1, 1)

        def _in_window(v):
            d = month_token(v)
            return d is not None and lo <= d <= wm_start

        months_col = movements["Month"].map(month_token)
        rows_no_month = int(months_col.isna().sum()) if hasattr(
            months_col, "isna") else 0
        movements = movements[movements["Month"].map(_in_window)]
        if not len(movements):
            have = sorted({d.strftime("%m/%Y") for d in months_col
                           if d is not None}, reverse=True)
            raise ValueError(
                "No contract movements dated %s on the movement tab. "
                "The months actually on that tab are: %s. Pick one of "
                "those, or update the stored RR06/ARO06."
                % (month_label, ", ".join(have[:14]) or "none - no "
                   "readable date column"))
    month_movements = movements.copy()

    def seq_get(ref, col):
        if ref in seq.index and col in seq.columns:
            return seq.at[ref, col]
        return None

    def seq_first(ref, names):
        """The first of several possible Sequentra column names that
        exists and holds a value - export headings vary."""
        for col in names:
            v = seq_get(ref, col)
            if v is not None and _text(v) and _text(v).lower() != "nan":
                return v
        return None

    piw_sched, piw_det, piw_eot, piw_steps = [], [], [], []
    mdiw_sched, mdiw_mod, mdiw_eot, mdiw_steps = [], [], [], []
    mdiw_det = []
    terminations, rent_review, exceptions, routing = [], [], [], []
    not_applicable = []
    ibr_check = []
    ciw_companies, ciw_addresses, ciw_costs = [], [], []
    rr_approver = []
    handled = set()
    latest_month = {}
    for _, m in movements.iterrows():
        _r = _text(m.get("Lease Reference"))
        _t = _text(m.get("Move Type")).lower()
        _d = _to_date(m.get("Month"))
        if _r and _t and _d:
            k = (_r, _t)
            d0 = month_start(_d)
            if k not in latest_month or d0 > latest_month[k]:
                latest_month[k] = d0
    _month_pat = re.compile(
        r"loaded\s+(january|february|march|april|may|june|july|august|"
        r"september|october|november|december)\s+(\d{4})", re.I)
    _month_no = {m: i + 1 for i, m in enumerate(
        ["january", "february", "march", "april", "may", "june", "july",
         "august", "september", "october", "november", "december"])}

    def add_exc(ref, mtype, issue, severity="Review"):
        exceptions.append({"Lease Reference": ref, "Move Type": mtype,
                           "Issue": issue, "Severity": severity})

    def eot_rows(ref, duration):
        base = {c: "" for c in EOT_COLS}
        ret = dict(base, **{"Schedule Number": ref, "Timing": "EOT",
                            "Option Type": "Return",
                            "Reasonably Certain EOT Option": "Yes",
                            "Effective Month": duration,
                            "Min Days Notice": 0, "Max Days Notice": 0,
                            "Exercisable by Lessee": "Yes"})
        ren = dict(base, **{"Schedule Number": ref, "Timing": "EOT",
                            "Option Type": "Renewal",
                            "Reasonably Certain EOT Option": "No",
                            "Effective Month": (duration + 1) if duration else "",
                            "Min Days Notice": 0, "Max Days Notice": 0,
                            "Exercisable by Lessee": "Yes"})
        return ret, ren

    for _, m in movements.iterrows():
        ref = _text(m.get("Lease Reference"))
        if not ref:
            continue
        mtype = _text(m.get("Move Type")).lower()
        on_la = ref in la_ids
        in_seq = ref in seq.index
        if (ref, mtype) in handled:
            continue                       # duplicate movement row
        _lm = latest_month.get((ref, mtype))
        _mm = _to_date(m.get("Month"))
        if _lm and _mm and month_start(_mm) < _lm:
            continue                     # an older duplicate - skip it
        handled.add((ref, mtype))
        move_month = _to_date(m.get("Month"))
        move_comment = _text(m.get("Comments"))

        def route(dest, _ref=ref, _mt=mtype, _mm=move_month, _c=move_comment):
            routing.append({"Lease Reference": _ref,
                            "Move Type": (_mt or "?").title(),
                            "Month": _fmt(month_start(_mm)) if _mm else "",
                            "Routed To": dest,
                            "Movement Comment": _c})

        def na(reason, _ref=ref, _mt=mtype, _mm=move_month,
               _c=move_comment):
            not_applicable.append({
                "Lease Reference": _ref,
                "Move Type": (_mt or "?").title(),
                "Month": _fmt(month_start(_mm)) if _mm else "",
                "Reason": reason,
                "Movement Comment": _c})
            route("Not IFRS 16 applicable - " + reason)

        # movements whose comment says they were loaded in an earlier
        # batch: noted only when the lease is NOT on LA (a lease already
        # on the register is expected to have been loaded before)
        cm = _month_pat.search(move_comment)
        prior_batch = None
        if cm:
            loaded = date(int(cm.group(2)), _month_no[cm.group(1).lower()], 1)
            if loaded < wm_start:
                prior_batch = "%s %s" % (cm.group(1).title(), cm.group(2))

        if mtype and not in_seq and mtype not in ("removal",):
            add_exc(ref, mtype or "?", "Not found in Sequentra validation "
                                       "export - fields left blank")

        # --- IFRS 16 applicability (team rules) ---------------------------
        expiry_txt = _text(m.get("Lease Expiry Date")).strip().lower()
        # Receivable leases (lease ref contains an "R") are not applicable.
        # Removals still process: a lease being removed is removed anyway.
        if mtype != "removal" and mtype and is_receivable(ref):
            na("receivable lease (reference contains 'R')")
            continue

        # month-to-month: the RR06 expiry column literally reads
        # "Monthly" instead of a date - not IFRS 16 applicable
        # (Additions and Renewals only; removals still terminate)
        if mtype in ("addition", "renewal") and expiry_txt == "monthly":
            na("month-to-month lease (expiry shown as Monthly)")
            continue

        # zero rental stated in the movement comment - not applicable
        # (Additions and Renewals only; removals still terminate).
        # matches "zero rental", "zero rentals", "zero-rental" etc.
        if mtype in ("addition", "renewal") and \
                re.search(r"zero[\s-]*rental", move_comment, re.I):
            na("zero rental (stated in the movement comment)")
            continue

        # Non-monthly leases are now expanded to monthly rows (1 charge
        # then zero months per interval); flag for review so the
        # placement can be eyeballed against the agreement.
        freqs = schedule_frequencies(steps, ref)
        if mtype != "removal" and mtype and (freqs - {"monthly"}):
            others = ", ".join(sorted(f.title() for f in
                                      freqs - {"monthly"}))
            add_exc(ref, mtype.title(),
                    "Non-monthly payment schedule (%s) - expanded to "
                    "monthly rows; check the charge months against the "
                    "agreement" % others, "Review")

        # ---- shared field harvesting ------------------------------------
        prop_ref = _text(m.get("Property Ref"))
        one_view = _text(m.get("One View Ref"))
        funder = _clean_funder(m.get("Landlord / Tenant Name"))
        country = _text(seq_get(ref, "Country"))
        currency = _text(seq_get(ref, "Currency")) or FIXED["default_currency"]

        # IBR: the team's own calculated rate in the movement file wins,
        # then Sequentra's New Discount Rate, then Discount Rate
        # (verified: all 10 June PIW IBRs match the movement file column)
        ibr = verified_ibr.get(ref)
        ibr_source = "Verified (tracker IBR Register)"
        if ibr is None:
            ibr = _num(m.get("Calculated check"))
            ibr_source = "Movement file calculated rate"
        if ibr is None or not (0 < ibr <= 100):
            ibr = _num(seq_get(ref, "New Discount Rate"))
            if ibr and 0 < ibr < 1:
                ibr = round(ibr * 100, 4)
            ibr_source = "Sequentra New Discount Rate"
        if not ibr:
            ibr = _num(seq_get(ref, "Discount Rate"))
            # 1.0.106 / 2.10.9: the same rule as the New Discount Rate - the
            # real export holds both 0.0749 and 7.49 for 7.49 %; 0.0749 went
            # to LA as an IBR of 0.0749 %
            if ibr and 0 < ibr < 1:
                ibr = round(ibr * 100, 4)
            ibr_source = "Sequentra Discount Rate"
        if not ibr:
            ibr_source = ""

        ext_ref = _text(seq_get(ref, "External Reference")) or one_view

        # Lease start: earliest of Move-In Date / Current Term Start
        # (Sequentra) / DCF date (movement), snapped to month start.
        # A Move-In Date only counts when it plausibly belongs to the
        # current term (no more than 24 months before the term start) -
        # historic move-ins from prior terms are ignored
        # (reproduces 9 of the 10 June PIW lease start dates; the team
        # keyed the 10th manually)
        cts = _to_date(seq_get(ref, "Current Term Start Date"))
        move_in = _to_date(seq_get(ref, "Move-In Date"))
        if move_in and cts and not (
                cts >= move_in and _months_between(move_in, cts) <= 24):
            move_in = None
        dcf = _to_date(m.get("DCF date")) or wm_start
        # The acquisition starts at the EARLIER of the beneficial
        # occupation date and the lease start date - never at the
        # workflow / DCF month, which would cap a future acquisition and
        # produce leading zero payments. Months between the BO date and
        # the first payment are carried at zero (zero_lead below).
        bo = _to_date(seq_first(ref, BO_DATE_COLS))
        start_candidates = [d for d in (bo, move_in, cts) if d]
        lease_start = month_start(min(start_candidates)) \
            if start_candidates else (_to_date(m.get("Current Start Date"))
                                      or month_start(dcf))
        lease_end = _to_date(seq_get(ref, "Current Lease End Date")) \
            or _to_date(m.get("Lease Expiry Date"))
        # Entity per the team's mapping: the Legal Entity on the movement
        # file's Contracts tab, falling back to the LA-register country
        # lookup. BU and Lessee are always the same value as Entity.
        ent = (contract_entities or {}).get(ref, "")
        if not ent:
            ent = entity_lookup.get(country.lower(), ("", "", ""))[0]
        bu = lessee = ent

        # ================= ROUTING (per the team's key notes) =============
        # PIW  -> any lease NOT yet on LA (additions, and renewals that
        #         were never loaded)
        # MDIW -> any addition/renewal for a lease ALREADY on LA
        # Rent Review  -> portfolio movements for leases on LA
        # Termination  -> removals for leases on LA
        if mtype in ("addition", "renewal") and not on_la:
            # ------------------------------------------------ PIW --------
            if mtype == "renewal":
                add_exc(ref, "Renewal",
                        "Renewal but not on LA - drafted as PIW addition")
            if steps is not None and "Expense Type Name" in steps.columns:
                tu = steps[(steps["Schedule Number"] == ref)
                           & steps["Expense Type Name"].map(is_true_up)]
                if len(tu):
                    add_exc(ref, mtype.title(),
                            "True-up adjustment charges left out of the "
                            "PIW (%d line%s)" % (len(tu), "" if len(tu) == 1
                                                 else "s"), "Info")
            srows, duration, first_amt, notes = build_step_blocks(
                steps_piw, ref, lease_start, zero_lead=True,
                base_month=lease_start)
            if duration is None and lease_start and lease_end:
                duration = (_months_between(lease_start, lease_end) or 0) + 1
            # IFRS 16: a term of 1 year or less is not applicable
            if duration is not None and duration <= 12:
                na("term of 1 year or less (%d months)" % duration)
                continue
            # IFRS 16: zero rental is not applicable
            if srows and all((r[3] or 0) == 0 for r in srows):
                na("zero rental")
                continue
            if prior_batch:
                add_exc(ref, mtype.title(),
                        "Not on LA but movement comment says loaded %s - "
                        "check if already processed in a prior batch"
                        % prior_batch, "Info")
            for n in notes:
                add_exc(ref, mtype.title(), n,
                        "Info" if "adjusted" in n else "Review")
            if not country:
                add_exc(ref, mtype.title(),
                        "No country on Sequentra - Geo left blank")
            if not ent:
                add_exc(ref, mtype.title(),
                        "No Legal Entity found for this lease on the "
                        "movement file's Contracts tab (and no fallback "
                        "on the LA register) - Entity/BU/Lessee left "
                        "blank", "Review")
            if not ibr:
                add_exc(ref, mtype.title(), "No IBR / discount rate found")

            row = {c: "" for c in PIW_SCHEDULE_COLS}
            row.update({
                "Schedule Number": ref,
                "Lease Start Date": _fmt(lease_start),
                "Ledger": FIXED["ledger"],
                "Ledger Date": _fmt(lease_start),
                "Currency": currency,
                "Lease Type": FIXED["lease_type"],
                "Frequency": FIXED["frequency"],
                "Duration": duration or "",
                "Reasonably Certain Holding Period": duration or "",
                "Payment": first_amt if first_amt is not None else "",
                "Entity": ent, "BU": bu, "Lessee": lessee,
                "IBR": ibr or "",
                "Repayment Mode": FIXED["repayment_mode"],
                "Accounting Classification":
                    FIXED["accounting_classification"],
                "Funder": funder,
                "Lessor Reference Number": ext_ref,
                "Facility Code": f"{prop_ref}-{one_view}"
                                 if prop_ref and one_view else "",
                "Geo": country, "Reporting Area": country,
                "Asset Owner": FIXED["asset_owner"],
                "Treasury Approver": FIXED["treasury_approver"],
                "Partial Building Flag": FIXED["partial_building_flag"],
            })
            ibr_term = duration
            if lease_start and lease_end:
                ibr_term = (_months_between(lease_start, lease_end) or 0) + 1
                if duration and duration < ibr_term - 2:
                    add_exc(ref, mtype.title(),
                            "Payment schedule covers %d of %d months to "
                            "the lease end - Sequentra steps look "
                            "incomplete" % (duration, ibr_term))
            ibr, ibr_source, band, rcode, rmonth = table_ibr(
                ref, ibr_term, ibr, ibr_source)
            row["IBR"] = ibr or ""
            piw_sched.append(row)

            # CIW: any of funder / facility code / cost centre not already
            # on LA (per the master sheet) -> add a CIW row for it. All
            # text is stripped of special characters (parentheses etc.)
            # that break the LA import.
            funder = _clean_funder(row.get("Funder"))
            facility = _clean(row.get("Facility Code"))
            # ledger code / cost centre = the facility-code prefix (the
            # schedule stub before the dash), e.g. 9695-37454X -> 9695
            ledger = _clean(facility.split("-")[0]) if facility else ""
            # location / building name: Record Name, else the address or
            # city, so the Cost Centre Description is a place, not blank
            building = (_clean(seq_get(ref, "Record Name"))
                        or _clean(seq_get(ref, "Title Description"))
                        or _clean(seq_get(ref, "Address 1"))
                        or _clean(seq_get(ref, "City")))
            city = _clean(seq_get(ref, "City"))
            country = _clean(seq_get(ref, "Country")) or "South Africa"
            addr1 = _clean(seq_get(ref, "Address 1")) or building
            addr2 = _clean(seq_get(ref, "Address 2"))
            postal = _clean(seq_get(ref, "Postal Code"))
            lessee = _clean(row.get("Lessee")) or FIXED.get("lessee", "")
            missing = []
            if funder and funder.lower() not in m_funders:
                missing.append("funder")
                ciw_companies.append({
                    "Company Role Type": "Funder",
                    "Company Name": funder})
            if facility and facility.lower() not in m_facilities:
                missing.append("facility code")
                # master first (if it happens to hold a partial record),
                # else the Sequentra-derived fields
                mrec = m_fac_addr.get(facility.lower(), {})
                ciw_addresses.append({
                    "Address Role Type": "ShipTo",
                    "Company Name": mrec.get("Company Name") or lessee,
                    "Address Line 1":
                        mrec.get("Address Line 1") or addr1,
                    "Address Line 2":
                        mrec.get("Address Line 2") or addr2,
                    "City": mrec.get("City") or city,
                    "State Province": mrec.get("State Province") or "",
                    "Country": mrec.get("Country") or country,
                    "Postal Code": mrec.get("Postal Code") or postal,
                    "Facility Code": facility, "Ledger Code": ledger})
            if ledger and ledger.lower() not in m_costs:
                missing.append("cost centre")
                desc = m_cost_desc.get(ledger.lower()) or building
                ciw_costs.append({"Cost Center": ledger,
                                  "Description": desc})
                if not desc:
                    add_exc(ref, "CIW", "Cost centre %s has no location "
                            "name in the master or Sequentra; fill the "
                            "Description manually" % ledger, "Review")
            if missing:
                add_exc(ref, "PIW", "New reference data for CIW: "
                        + ", ".join(missing), "Info")
            ibr_check.append({"Schedule Number": ref, "Workbook": "PIW",
                              "IBR Used": ibr or "", "Source": ibr_source,
                              "Band": band, "Rate Code": rcode,
                              "Rate Month": rmonth,
                              "Calculated IBR (fill in)": ""})

            det = {c: "" for c in PIW_DETAILS_COLS}
            nonzero = next((r[3] for r in srows if r[3]), None)
            det.update({
                "Schedule Number": ref,
                "Product Category": _text(seq_first(ref,
                                                    LEASE_TYPE_COLS)),
                "Cost Center": prop_ref,
                "Project": _text(seq_get(ref, "Contract Cost Centre"))
                    or _text(m.get("Contract Cost Centre")),
                "GL Code": FIXED["gl_code"],
                "Reference Number": ext_ref,
                "Quantity": 1,
                "Unit Price": nonzero if nonzero is not None else "",
            })
            piw_det.append(det)
            r1, r2 = eot_rows(ref, duration)
            piw_eot.extend([r1, r2])
            piw_steps.extend(srows)
            route("PIW")

        elif mtype in ("addition", "renewal"):
            # --------------------------------------- MDIW (on LA) --------
            # the payment schedule runs from the month the DCF is loaded
            # (the movement file's DCF date) to the end of the lease -
            # not from the workflow month. dcf already falls back to the
            # workflow month when the movement file carries no DCF date.
            mod_anchor = month_start(dcf) if dcf else wm_start
            if mod_anchor != wm_start:
                add_exc(ref, mtype.title(),
                        "MDIW schedule anchored at the DCF date %s "
                        "(not the workflow month)" % _fmt(mod_anchor),
                        "Info")
            srows, duration, first_amt, notes = build_step_blocks(
                steps, ref, mod_anchor, zero_lead=False,
                base_month=lease_start)
            if duration is None and lease_end:
                duration = (_months_between(mod_anchor, lease_end)
                            or 0) + 1
            # IFRS 16: less than a year from the modification date to the
            # lease end date is not applicable
            if duration is not None and duration <= 12:
                na("1 year or less remaining at the modification date "
                   "(%d months)" % duration)
                continue
            if srows and all((r[3] or 0) == 0 for r in srows):
                na("zero rental")
                continue
            for n in notes:
                add_exc(ref, mtype.title(), n,
                        "Info" if "adjusted" in n else "Review")
            if not ibr:
                add_exc(ref, mtype.title(), "No IBR / discount rate found")

            row = {c: "" for c in MDIW_SCHEDULE_COLS}
            row.update({
                "Schedule": ref,
                "Lease Type": FIXED["lease_type"],
                "Frequency": FIXED["frequency"],
                "Repayment Mode": FIXED["repayment_mode"],
                "Duration": duration or "",
            })
            mdiw_sched.append(row)
            mod = {c: "" for c in MDIW_MODSUM_COLS}
            mod.update({
                "Schedule Number": ref,
                "IBR": ibr or "",
                "Comments": f"{month_label} Renewal",
                # the first of the month, never a day within it (finance,
                # Oct 2026)
                "Modification Date": _fmt(month_start(dcf or wm_start)),
                "Commensurate Adjustment": "No",
            })
            ibr_term = duration
            if lease_end:
                ibr_term = (_months_between(mod_anchor, lease_end)
                            or 0) + 1
                if duration and duration < ibr_term - 2:
                    add_exc(ref, mtype.title(),
                            "Payment schedule covers %d of %d months to "
                            "the lease end - Sequentra steps look "
                            "incomplete" % (duration, ibr_term))
            ibr, ibr_source, band, rcode, rmonth = table_ibr(
                ref, ibr_term, ibr, ibr_source)
            mod["IBR"] = ibr or ""
            mdiw_mod.append(mod)
            ibr_check.append({"Schedule Number": ref, "Workbook": "MDIW",
                              "IBR Used": ibr or "", "Source": ibr_source,
                              "Band": band, "Rate Code": rcode,
                              "Rate Month": rmonth,
                              "Calculated IBR (fill in)": ""})
            # Details tab stays EMPTY on an MDIW by team instruction -
            # the modification carries no asset detail rows. The sheet is
            # still produced with its headers so the upload file has the
            # tab the template expects.
            r1, r2 = eot_rows(ref, duration)
            mdiw_eot.extend([r1, r2])
            mdiw_steps.extend(srows)
            route("MDIW")

        elif mtype == "removal":
            # ----------------------------------------- Termination -------
            if not on_la:
                add_exc(ref, "Removal", "Not on LA - nothing to terminate")
                route("EXCEPTION (not on LA)")
                continue
            # The team books both dates at the workflow month end
            # (verified: all 10 rows in their June file are 6/30/2026,
            # including leases the movement file terminated months
            # earlier). Flag back-dated terminations for awareness.
            move_term = _to_date(m.get("Termination Date"))
            if move_term and month_end(move_term) != wm_end:
                add_exc(ref, "Removal",
                        "Movement file termination date %s - effective "
                        "date set to month end %s per team practice"
                        % (_fmt(move_term), _fmt(wm_end)), "Info")
            lease_assets = assets.get(ref) or [{"Asset ID": "",
                                                "Serial Number": "",
                                                "Description": ""}]
            if not assets.get(ref) and assets:
                add_exc(ref, "Removal", "Not found in the stored asset "
                        "report - Asset ID left blank")
            for a in lease_assets:
                row = {c: "" for c in TERMINATION_COLS}
                row.update({
                    "Schedule Number": ref,
                    "Asset ID": a.get("Asset ID", ""),
                    "Serial Number": a.get("Serial Number", ""),
                    "Description": a.get("Description", ""),
                    "EOT Event": FIXED["termination_event"],
                    "Ledger Date Month": wm_end.month,
                    "Ledger Date Day": wm_end.day,
                    "Ledger Date Year": wm_end.year,
                    "Effective Date Month": wm_end.month,
                    "Effective Date Day": wm_end.day,
                    "Effective Date Year": wm_end.year,
                })
                terminations.append(row)
            route("Termination")

        elif "portfolio" in mtype:
            # ---------------------------------------- Rent Review --------
            # EVERY portfolio movement appears in the rent-review section.
            # Rows are drafted but default to UNAPPROVED - nothing feeds
            # the import workbook until it is approved on the Rent Review
            # Approver sheet (Happy? -> Yes).
            route("Rent Review")
            cm = move_comment.lower()
            looks_transfer = any(w in cm for w in
                                 ("transfer", "owner", "landlord",
                                  "cession", "assign"))
            if not on_la:
                rr_approver.append({
                    "Schedule Number": ref, "On LA": "No",
                    "Movement Comment": move_comment,
                    "Suggested": "No (not on LA)", "Happy? (Yes/No)": "No",
                    "Rows": 0})
                add_exc(ref, "Portfolio Movement",
                        "Portfolio movement not on LA - review manually",
                        "Review")
                continue
            rows, notes = build_rent_review_rows(
                steps, ref, wm_start,
                begin=rent_review_starts.get(ref))
            for n in notes:
                add_exc(ref, "Portfolio Movement", n)
            suggested = "No (looks like a transfer)" if looks_transfer \
                else ("No (zero rental)"
                      if rows and all((r["Amount"] or 0) == 0 for r in rows)
                      else "Yes")
            for r in rows:
                r["Approved"] = "NO"          # default unapproved
                r["Movement Comment"] = move_comment
            rent_review.extend(rows)
            rr_approver.append({
                "Schedule Number": ref, "On LA": "Yes",
                "Movement Comment": move_comment,
                "Suggested": suggested, "Happy? (Yes/No)": "No",
                "Rows": len(rows)})
            add_exc(ref, "Portfolio Movement",
                    "Rent review pending approval (default No) - comment: %s"
                    % (move_comment or "(none)"), "Review")

        elif mtype:
            add_exc(ref, mtype.title(), "Manual review - no automatic route "
                                        "for this movement type")
            route("Manual review")

    # ---- Recon summary ---------------------------------------------------
    mtypes = (movements["Move Type"].astype(str).str.strip().str.title()
              .replace("Nan", "(blank)").value_counts())
    summary = [{"Item": "Workflow month", "Value": month_label},
               {"Item": "Movement tab used", "Value": _mv_tab or "?"},
               {"Item": "Month read from column",
                "Value": _mv_month_col or "?"},
               {"Item": "Movement rows on that tab", "Value": rows_in_file},
               {"Item": "Movement rows", "Value": len(movements)},
               {"Item": "Rows skipped (no readable month)",
                "Value": rows_no_month},
               {"Item": "On LA already", "Value":
                   int(movements["Lease Reference"].isin(la_ids).sum())},
               {"Item": "Found in Sequentra", "Value":
                   int(movements["Lease Reference"].isin(seq.index).sum())}]
    summary += [{"Item": f"Move type: {k}", "Value": int(v)}
                for k, v in mtypes.items()]
    summary += [
        {"Item": "-> PIW additions drafted", "Value": len(piw_sched)},
        {"Item": "-> MDIW renewals drafted", "Value": len(mdiw_sched)},
        {"Item": "-> Rent Review adjustments drafted",
         "Value": len(rent_review)},
        {"Item": "-> Terminations drafted", "Value": len(terminations)},
        {"Item": "-> Not IFRS 16 applicable", "Value": len(not_applicable)},
        {"Item": "-> IBRs to verify (see IBR Check sheet)", "Value":
         sum(1 for r in ibr_check
             if r["Source"] != "Movement file calculated rate"
             and not r["Source"].startswith("Verified")
             and not r["Source"].startswith("approved rate table"))},
        {"Item": "-> Exceptions to review", "Value":
         sum(1 for e in exceptions if e["Severity"] == "Review")},
        {"Item": "-> Informational notes", "Value":
         sum(1 for e in exceptions if e["Severity"] == "Info")},
    ]

    def frame(rows, cols):
        return pd.DataFrame(rows, columns=cols) if rows \
            else pd.DataFrame(columns=cols)

    dash = [
        {"Section": "Month", "Metric": "Workflow month",
         "Value": month_label},
        {"Section": "Input", "Metric": "Contract movements",
         "Value": len(movements)},
        {"Section": "Input", "Metric": "Already on LA",
         "Value": int(movements["Lease Reference"].isin(la_ids).sum())},
        {"Section": "Drafted", "Metric": "PIW (new leases)",
         "Value": len(piw_sched)},
        {"Section": "Drafted", "Metric": "MDIW (modifications)",
         "Value": len(mdiw_sched)},
        {"Section": "Drafted", "Metric": "Terminations",
         "Value": len(terminations)},
        {"Section": "Drafted", "Metric": "Rent review rows",
         "Value": len(rent_review)},
        {"Section": "Drafted", "Metric": "CIW reference rows",
         "Value": len(ciw_companies) + len(ciw_addresses) + len(ciw_costs)},
        {"Section": "Excluded", "Metric": "Not IFRS 16 applicable",
         "Value": len(not_applicable)},
        {"Section": "Review", "Metric": "Exceptions to review",
         "Value": sum(1 for e in exceptions
                      if e["Severity"] == "Review")},
        {"Section": "Review", "Metric": "Informational notes",
         "Value": sum(1 for e in exceptions if e["Severity"] == "Info")},
        {"Section": "Review", "Metric": "IBRs to verify",
         "Value": sum(1 for r in ibr_check
                      if r["Source"] != "Movement file calculated rate"
                      and not r["Source"].startswith("Verified")
                      and not r["Source"].startswith("approved rate table"))},
    ]
    for k, v in mtypes.items():
        dash.append({"Section": "Move types", "Metric": str(k),
                     "Value": int(v)})
    # where the applicable movements were routed
    for dest, cnt in _count_by(routing, "Routed To").items():
        dash.append({"Section": "Routed to", "Metric": dest,
                     "Value": cnt})
    # new reference data needed on LA
    for lbl, cnt in (("Funders (Companies)", len(ciw_companies)),
                     ("Addresses", len(ciw_addresses)),
                     ("Cost centres", len(ciw_costs))):
        if cnt:
            dash.append({"Section": "New reference data (CIW)",
                         "Metric": lbl, "Value": cnt})
    # rent review approval state
    _appr = sum(1 for r in rent_review
                if str(r.get("Approved", "NO")).upper() != "NO")
    if rent_review:
        dash.append({"Section": "Rent review", "Metric": "Approved rows",
                     "Value": _appr})
        dash.append({"Section": "Rent review",
                     "Metric": "Awaiting approval",
                     "Value": len(rent_review) - _appr})
    for reason, cnt in _count_by(not_applicable, "Reason").items():
        dash.append({"Section": "Why excluded", "Metric": reason,
                     "Value": cnt})

    return {
        "Dashboard": pd.DataFrame(dash, columns=["Section", "Metric",
                                                "Value"]),
        "Recon Summary": pd.DataFrame(summary),
        "Routing": frame(routing, ["Lease Reference", "Move Type", "Month",
                                   "Routed To", "Movement Comment"]),
        movement_label + " Movements": month_movements.reset_index(
            drop=True),
        "Rent Review Dates": _rent_review_dates_frame(rent_review),
        "Rent Review Approver": frame(rr_approver, [
            "Schedule Number", "On LA", "Movement Comment", "Suggested",
            "Happy? (Yes/No)", "Rows"]),
        movement_label + " Routing": _ifrs_routing_frame(
            routing, not_applicable),
        "IBR Check": frame(ibr_check,
                           ["Schedule Number", "Workbook", "IBR Used",
                            "Source", "Band", "Rate Code", "Rate Month",
                            "Calculated IBR (fill in)"]),
        "CIW Companies": frame(ciw_companies, CIW_COMPANIES_COLS),
        "CIW Addresses": frame(ciw_addresses, CIW_ADDRESSES_COLS),
        "CIW Cost Centers": frame(ciw_costs, CIW_COSTCENTRE_COLS),
        "Not Applicable": frame(not_applicable,
                                ["Lease Reference", "Move Type", "Month",
                                 "Reason", "Movement Comment"]),
        "Exceptions": frame(exceptions, ["Lease Reference", "Move Type",
                                         "Issue", "Severity"]),
        "PIW Schedule": frame(piw_sched, PIW_SCHEDULE_COLS),
        "PIW Details": frame(piw_det, PIW_DETAILS_COLS),
        "PIW EOT Options": frame(piw_eot, EOT_COLS),
        "PIW Step Payments": frame(
            [dict(zip(STEP_COLS, r)) for r in piw_steps], STEP_COLS),
        "MDIW Schedule": frame(mdiw_sched, MDIW_SCHEDULE_COLS),
        "MDIW Modification Summary": frame(mdiw_mod, MDIW_MODSUM_COLS),
        "MDIW Details": frame(mdiw_det, MDIW_DETAILS_COLS),
        "MDIW EOT Options": frame(mdiw_eot, EOT_COLS),
        "MDIW Step Payments": frame(
            [dict(zip(STEP_COLS, r)) for r in mdiw_steps], STEP_COLS),
        "Rent Review Adjustments": _rent_review_frame(rent_review),
        "_Rent Review All": pd.DataFrame(
            rent_review, columns=RENT_REVIEW_COLS + ["Approved",
                                                     "Movement Comment"])
        if rent_review else pd.DataFrame(
            columns=RENT_REVIEW_COLS + ["Approved", "Movement Comment"]),
        "Terminations Advanced Form": frame(terminations,
                                           TERMINATION_COLS),
    }


def _count_by(rows, key):
    out = {}
    for r in rows:
        k = str(r.get(key, "")).strip() or "(unstated)"
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _rent_review_frame(rent_review_rows):
    """The Rent Review Adjustments sheet: only APPROVED rows, and without
    the internal Approved / Movement Comment control columns (those are
    not Lease Accelerator fields)."""
    approved = [r for r in rent_review_rows
                if str(r.get("Approved", "NO")).upper() != "NO"]
    df = pd.DataFrame(approved, columns=RENT_REVIEW_COLS + ["Approved",
                                                            "Movement Comment"])
    return df[RENT_REVIEW_COLS] if len(df) else \
        pd.DataFrame(columns=RENT_REVIEW_COLS)


def _rent_review_dates_frame(rent_review_rows):
    """One row per rent-review schedule: the start date inferred from
    the payment data and a New Start column to fill in from the lease
    agreement (double-click the row in the app)."""
    per = {}
    for r in rent_review_rows:
        s = r["Schedule Number"]
        eff = r["Effective Date"]
        d = per.setdefault(s, {"first": eff, "last": eff, "rows": 0})
        d["rows"] += 1
        if _sort_dmy(eff) < _sort_dmy(d["first"]):
            d["first"] = eff
        if _sort_dmy(eff) > _sort_dmy(d["last"]):
            d["last"] = eff
    rows = [{"Schedule Number": s,
             "Start Used (inferred)": v["first"],
             "Schedule End": v["last"],
             "Monthly Rows": v["rows"],
             "New Start (fill in - MM/YYYY)": ""}
            for s, v in sorted(per.items())]
    return pd.DataFrame(rows, columns=[
        "Schedule Number", "Start Used (inferred)", "Schedule End",
        "Monthly Rows", "New Start (fill in - MM/YYYY)"])


def _sort_dmy(v):
    try:
        d, m, y = str(v).split("/")
        return y + m + d
    except Exception:
        return str(v)


def _ifrs_routing_frame(routing, not_applicable):
    """The call's 'Routing' sheet: every movement of the month with an
    'IFRS 16 Applicable' YES/NO (NO for receivables and, on Additions
    and Renewals only, terms of a year or less / zero rental), the
    reason, and the MDIW-or-PIW destination from the LA lookup."""
    na = {(r["Lease Reference"], r["Move Type"]): r["Reason"]
          for r in not_applicable}
    rows = []
    for r in routing:
        key = (r["Lease Reference"], r["Move Type"])
        no_reason = na.get(key)
        rows.append({
            "Lease Reference": r["Lease Reference"],
            "Move Type": r["Move Type"],
            "Month": r["Month"],
            "IFRS 16 Applicable": "NO" if no_reason else "YES",
            "Reason": no_reason or "",
            "Workbook": "" if no_reason else r["Routed To"],
            "Movement Comment": r["Movement Comment"],
        })
    return pd.DataFrame(rows, columns=[
        "Lease Reference", "Move Type", "Month", "IFRS 16 Applicable",
        "Reason", "Workbook", "Movement Comment"])


def run_workflow_from_files(la_path, movements_path, sequentra_path,
                            workflow_month=None, steps_path=None,
                            lookback=1, tracker_path=None, codes_path=None,
                            assets_path=None, rent_review_starts=None,
                            rates_path=None, movement_label=None,
                            ciw_master_path=None, steps_df=None,
                            sequentra_df=None):
    """Convenience wrapper: load the source files and run.

    movements_path may be a prepared movements workbook or a raw
    RR06 / ARO06 export. steps_path (the Sequentra 'PIW Step Payments'
    report) is required when the movements file has no Step Payment
    sheet, and preferred even when it does.
    """
    if codes_path:
        apply_code_library(codes_path)
    la = load_la_register(la_path)
    mv, steps = load_movements(movements_path)
    # steps / Sequentra facts may be injected from the API instead of files
    if steps_df is not None:
        steps = steps_df
        steps_src = "the Sequentra API feed"
    elif steps_path and Path(str(steps_path)).exists():
        steps = load_step_payments(steps_path)
        steps_src = "'%s'" % Path(str(steps_path)).name
    elif steps is not None and len(steps):
        steps_src = ("the Step Payment tab of '%s'"
                     % Path(str(movements_path)).name)
    else:
        # No step payments: routing and applicability still produce, but
        # payment schedules cannot be drafted (flagged per lease).
        steps = pd.DataFrame(columns=[
            "Schedule Number", "Expense Type Name", "StartDate", "EndDate",
            "Number of Payments", "Payment Amount", "Payment Frequency"])
        steps_src = "none supplied"
    # accept common column-name variants, then fail with a clear message
    # (not a bare KeyError) if a needed column really is absent
    steps = _normalise_step_columns(steps)
    if steps is not None and len(steps):
        _check_step_columns(steps, steps_src)
    if sequentra_df is not None:
        seq = sequentra_df
    elif sequentra_path and Path(str(sequentra_path)).exists():
        seq = load_sequentra(sequentra_path)
    else:
        # No Sequentra validation export supplied: the IFRS 16 tests and
        # the MDIW/PIW split still work (they use the movement file and
        # the LA register). Everything that needs lease facts is flagged.
        seq = pd.DataFrame(columns=[
            "Record ID", "Country", "Currency",
            "Current Term Start Date", "Current Lease End Date"])
    assets = load_asset_report(assets_path) if assets_path else None
    rates = load_rate_table(rates_path) if rates_path else {}
    ciw_master = load_ciw_master(ciw_master_path)
    verified = load_verified_ibrs(tracker_path) if tracker_path else {}
    if movement_label is None:
        movement_label = "ARO06" if "aro" in str(movements_path).lower() \
            else "RR06"
    results = run_workflow(la, mv, seq, steps, workflow_month, lookback,
                           verified_ibr=verified,
                           rent_review_starts=rent_review_starts,
                           assets=assets, rates=rates,
                           movement_label=movement_label,
                           ciw_master=ciw_master,
                           contract_entities=load_contract_entities(
                               movements_path))
    if tracker_path:
        update_tracker(results, tracker_path)
    return results



# ---------------------------------------------------------------------------
# Monthly report
# ---------------------------------------------------------------------------
def report_summary_frame(results):
    """Flat Section / Item / Value view of the month for the app grid."""
    rows = []

    def add(section, item, value=""):
        rows.append({"Section": section, "Item": item, "Value": value})

    for _, r in results.get("Recon Summary", pd.DataFrame()).iterrows():
        add("Summary", str(r["Item"]).replace("-> ", ""), r["Value"])
    rt = results.get("Routing", pd.DataFrame())
    if len(rt):
        for dest, n in rt["Routed To"].value_counts().items():
            add("Routing", dest, int(n))
    piw = results.get("PIW Schedule", pd.DataFrame())
    if len(piw):
        add("PIW additions", ", ".join(piw["Schedule Number"].astype(str)))
    term = results.get("Terminations Advanced Form",
                       results.get("Terminations",
                                   pd.DataFrame()))
    if len(term):
        add("Terminations", ", ".join(term["Schedule Number"].astype(str)))
    rr = results.get("Rent Review Adjustments", pd.DataFrame())
    if len(rr):
        add("Rent reviews", ", ".join(sorted(
            rr["Schedule Number"].astype(str).unique())),
            "%d rows" % len(rr))
    return pd.DataFrame(rows, columns=["Section", "Item", "Value"])


def write_monthly_report(results, path, tracker_path=None):
    """Write the monthly report by filling the report template
    (App/templates/Report_template.xlsx - edit that file to restyle the
    report). Falls back to a built-in layout if the template is missing.
    When a tracker is given, a month-over-month section is added from
    its Runs sheet."""
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    GREEN, MINT = "003F2D", "17E88F"
    CELADON, GREY = "DBE7E1", "435254"
    thin = Side(style="thin", color="C0D4CB")
    box = Border(top=thin, bottom=thin, left=thin, right=thin)

    recon = results.get("Recon Summary", pd.DataFrame())
    month = ""
    for _, r in recon.iterrows():
        if str(r["Item"]).strip().lower() == "workflow month":
            month = str(r["Value"])
            break

    tpl = TEMPLATES_DIR / "Report_template.xlsx"
    row = None
    if tpl.exists():
        wb = load_workbook(tpl)
        ws = wb.active
        gen = datetime.now().strftime("%d/%m/%Y %H:%M")
        for r in ws.iter_rows():
            for cell in r:
                if isinstance(cell.value, str):
                    if "{{CONTENT}}" in cell.value:
                        row = cell.row
                        cell.value = None
                    elif "{{" in cell.value:
                        cell.value = (cell.value
                                      .replace("{{MONTH}}", month)
                                      .replace("{{GENERATED}}", gen))
        if row is None:
            row = ws.max_row + 2
    else:
        wb = Workbook()
        ws = wb.active
        ws.title = "Monthly Report"
        ws.sheet_view.showGridLines = False
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        for i, w in enumerate([30, 20, 14, 60, 12], 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        row = 1
        ws.cell(row, 1, "IFRS 16 WORKFLOW").font = Font(
            bold=True, size=16, color=GREEN, name="Segoe UI")
        row += 1
        ws.cell(row, 1, "Monthly Lease Workflow Report - %s" % month).font \
            = Font(size=12, color="1A1A1A", name="Segoe UI")
        row += 1
        ws.cell(row, 1, "Generated %s" %
                datetime.now().strftime("%d/%m/%Y %H:%M")).font = Font(
            size=9, italic=True, color=GREY, name="Segoe UI")
        for c in range(1, 6):
            ws.cell(row + 1, c).fill = PatternFill("solid", fgColor=MINT)
        ws.row_dimensions[row + 1].height = 3
        row += 3

    def section(title):
        nonlocal row
        ws.cell(row, 1, title).font = Font(
            bold=True, size=12, color=GREEN, name="Segoe UI")
        row += 1

    def table(df, cols, max_rows=None):
        nonlocal row
        for j, c in enumerate(cols, 1):
            cell = ws.cell(row, j, c)
            cell.font = Font(bold=True, size=9, color=GREEN, name="Segoe UI")
            cell.fill = PatternFill("solid", fgColor=CELADON)
            cell.border = box
        row += 1
        data = df if max_rows is None else df.head(max_rows)
        for _, r in data.iterrows():
            for j, c in enumerate(cols, 1):
                cell = ws.cell(row, j, r.get(c, ""))
                cell.font = Font(size=9, name="Segoe UI")
                cell.border = box
                cell.alignment = Alignment(vertical="top", wrap_text=True)
            row += 1
        if max_rows is not None and len(df) > max_rows:
            ws.cell(row, 1, "... and %d more (see the workflow output)"
                    % (len(df) - max_rows)).font = Font(
                size=9, italic=True, color=GREY, name="Segoe UI")
            row += 1
        row += 1

    # 1. summary
    section("1. Summary")
    table(recon.rename(columns={"Item": "Measure"}),
          ["Measure", "Value"])

    # 2. what was drafted
    section("2. Drafted this month")
    piw = results.get("PIW Schedule", pd.DataFrame())
    mdiw = results.get("MDIW Schedule", pd.DataFrame())
    rr = results.get("Rent Review Adjustments", pd.DataFrame())
    term = results.get("Terminations Advanced Form",
                       results.get("Terminations",
                                   pd.DataFrame()))
    drafted = pd.DataFrame([
        {"Workbook": "PIW (new leases)", "Schedules": len(piw),
         "Rows": len(piw),
         "Lease references": ", ".join(piw.get(
             "Schedule Number", pd.Series(dtype=str)).astype(str))},
        {"Workbook": "MDIW (modifications)", "Schedules": len(mdiw),
         "Rows": len(mdiw),
         "Lease references": ", ".join(mdiw.get(
             "Schedule", pd.Series(dtype=str)).astype(str).head(40)) +
            ("..." if len(mdiw) > 40 else "")},
        {"Workbook": "Rent Review", "Schedules":
            rr["Schedule Number"].nunique() if len(rr) else 0,
         "Rows": len(rr),
         "Lease references": ", ".join(sorted(rr.get(
             "Schedule Number", pd.Series(dtype=str)).astype(str).unique()))},
        {"Workbook": "Terminations", "Schedules": len(term),
         "Rows": len(term),
         "Lease references": ", ".join(term.get(
             "Schedule Number", pd.Series(dtype=str)).astype(str))},
    ])
    table(drafted, ["Workbook", "Schedules", "Rows", "Lease references"])

    # 3. not applicable
    na = results.get("Not Applicable", pd.DataFrame())
    section("3. Not IFRS 16 applicable (%d)" % len(na))
    if len(na):
        table(na, ["Lease Reference", "Move Type", "Month", "Reason"],
              max_rows=40)
    else:
        ws.cell(row, 1, "None this month.").font = Font(
            size=9, italic=True, color=GREY, name="Segoe UI")
        row += 2

    # 4. exceptions to review
    exc = results.get("Exceptions", pd.DataFrame())
    rev = exc[exc.get("Severity", "") == "Review"] if len(exc) else exc
    section("4. Exceptions to review (%d, plus %d informational)"
            % (len(rev), len(exc) - len(rev)))
    if len(rev):
        table(rev, ["Lease Reference", "Move Type", "Severity", "Issue"],
              max_rows=40)
    else:
        ws.cell(row, 1, "None - clean run.").font = Font(
            size=9, italic=True, color=GREY, name="Segoe UI")
        row += 2

    # 5. IBRs to verify
    ibr = results.get("IBR Check", pd.DataFrame())
    todo = ibr[ibr["Source"] != "Movement file calculated rate"] \
        if len(ibr) else ibr
    section("5. IBRs to verify (%d)" % len(todo))
    if len(todo):
        table(todo, ["Schedule Number", "Workbook", "IBR Used", "Source"],
              max_rows=40)
    ws.cell(row, 1, "Verify rates on the IBR Check sheet of the workflow "
                    "output, then key them into the drafts before "
                    "loading.").font = Font(size=9, italic=True, color=GREY,
                                            name="Segoe UI")
    row += 2

    # 6. month over month, from the master tracker
    if tracker_path:
        runs = load_tracker(tracker_path).get("Runs")
        if runs is not None and len(runs):
            section("6. Previous runs (master tracker)")
            keep = [c for c in ["Run", "Workflow Month", "Generated",
                                "PIW additions drafted",
                                "MDIW renewals drafted",
                                "Rent Review adjustments drafted",
                                "Terminations drafted",
                                "Exceptions to review"]
                    if c in runs.columns]
            table(runs[keep].tail(12), keep)
    ws.cell(row, 1, "\u00a9 2026 CBRE Excellerate \u00b7 All rights "
                    "reserved").font = Font(size=9, color=GREY,
                                            name="Segoe UI")
    return wb.save(path)


# ---------------------------------------------------------------------------
# Ready-to-import LA workbooks
#
# Fills complete copies of the actual LA import templates - every sheet
# and column present (Version Control, Overview, Manifest, Data, ...),
# exactly as the LA importer expects - with the drafted rows written
# into the right tabs. Blank repaired templates ship in App/templates.
# ---------------------------------------------------------------------------
def load_ciw_master(path):
    """Read the LA master (Participant Validation export, or a simple
    Funder / Facility Code / Cost Center sheet).

    Returns a dict:
      funders        - set of funder names already on LA (lower-cased)
      facilities     - set of facility codes already on LA
      costs          - set of cost-centre / ledger codes already on LA
      facility_addr  - {facility_code(lower): {address fields}} for
                       reference addresses already captured on LA
      cost_desc      - {cost_code(lower): description/location}

    The address/description maps let the CIW fill a row from the master
    first (falling back to Sequentra) when the reference exists there.
    Empty structures when no file is given, so every reference then
    counts as new and the CIW drafts in full."""
    funders, facilities, costs = set(), set(), set()
    facility_addr, cost_desc = {}, {}
    if not path or not Path(path).exists():
        return {"funders": funders, "facilities": facilities,
                "costs": costs, "facility_addr": facility_addr,
                "cost_desc": cost_desc}
    book = _read_workbook(path, sheet_name=None, header=None)

    def add(target, v):
        t = _text(v)
        if t:
            target.add(t.strip().lower())

    for name, raw in book.items():
        hr = None
        known = {"companies", "company", "party role type", "attribute",
                 "value", "facility code", "funder", "cost center",
                 "cost centre", "address1", "address 1"}
        for i in range(min(8, len(raw))):
            cells = [str(v).strip().lower() for v in raw.iloc[i].tolist()]
            if sum(1 for c in cells if c in known) >= 2:
                hr = i
                break
        if hr is None:
            continue
        df = _reheader(raw, hr)
        cols = {str(c).strip().lower(): c for c in df.columns}

        # -- Companies tab: Funder role --------------------------------
        if "party role type" in cols and \
                ("companies" in cols or "company" in cols):
            namecol = cols.get("companies") or cols.get("company")
            for _, r in df.iterrows():
                if str(r[cols["party role type"]]).strip() == "Funder":
                    add(funders, r[namecol])

        # -- Addresses tab: Attribute/Value + the full address ---------
        if "attribute" in cols and "value" in cols:
            a1 = cols.get("address1") or cols.get("address 1")
            a2 = cols.get("address2") or cols.get("address 2")
            for _, r in df.iterrows():
                attr = str(r[cols["attribute"]]).strip()
                val = _text(r[cols["value"]])
                if attr == "ShipToKey":
                    add(facilities, val)
                    rec = {
                        "Company Name": _text(r.get(cols.get("company"))),
                        "Address Line 1": _text(r.get(a1)) if a1 else "",
                        "Address Line 2": _text(r.get(a2)) if a2 else "",
                        "City": _text(r.get(cols.get("city"))),
                        "State Province":
                            _text(r.get(cols.get("state province"))),
                        "Postal Code":
                            _text(r.get(cols.get("postal code"))),
                        "Country": _text(r.get(cols.get("country"))),
                    }
                    if val:
                        facility_addr[val.strip().lower()] = rec
                elif attr == "ShipToLedgerCode":
                    add(costs, val)
                    if val:
                        cost_desc[val.strip().lower()] = _text(
                            r.get(cols.get("company")))

        # -- simple fallback sheets ------------------------------------
        for key, target in (("funder", funders),
                            ("facility", facilities),
                            ("cost", costs)):
            col = next((c for c in df.columns
                        if key in str(c).strip().lower()
                        and "role" not in str(c).strip().lower()), None)
            if col is not None:
                for v in df[col].dropna():
                    add(target, v)

    return {"funders": funders, "facilities": facilities, "costs": costs,
            "facility_addr": facility_addr, "cost_desc": cost_desc}


CIW_COMPANIES_COLS = ["Company Role Type", "Company Name", "Parent Name",
                      "URL", "Address Line 1", "Address Line 2", "City",
                      "State Province", "Country", "Postal Code",
                      "Contact", "Phone", "Email", "Ledger Code",
                      "Functional Currency"]
CIW_ADDRESSES_COLS = ["Address Role Type", "Company Name",
                      "Address Line 1", "Address Line 2", "City",
                      "State Province", "Country", "Postal Code",
                      "Facility Code", "Ledger Code"]
CIW_COSTCENTRE_COLS = ["Cost Center", "Description"]
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# CIW reference lookups (master sheet). Until the master is supplied the
# sets are empty, so every PIW reference counts as "missing" and the CIW
# is drafted in full - exactly what you want before the master exists.
CIW_MASTER_NAME = "ciw_master.xlsx"



_TEMPLATE_MAP = [
    ("CIW_template.xlsx", "CIW_%s.xlsx",
     [("Companies", "CIW Companies"), ("Addresses", "CIW Addresses"),
      ("Cost Centers", "CIW Cost Centers")]),
    ("PIW_template.xlsx", "PIW_Additions_%s.xlsx",
     [("Schedule", "PIW Schedule"), ("Details", "PIW Details"),
      ("EOT Options", "PIW EOT Options"),
      ("Step Payments", "PIW Step Payments")]),
    ("MDIW_template.xlsx", "MDIW_%s.xlsx",
     [("Schedule", "MDIW Schedule"),
      ("Modification Summary", "MDIW Modification Summary"),
      ("Details", "MDIW Details"),
      ("EOT Options", "MDIW EOT Options"),
      ("Step Payments", "MDIW Step Payments")]),
    ("RentReview_template.xlsx", "Rent_Review_%s.xlsx",
     [("Payment Adjustments", "Rent Review Adjustments")]),
    ("Terminations_template.xlsx", "Terminations_%s.xlsx",
     [("Advanced Form", "Terminations Advanced Form")]),
]

_DMY = re.compile(r"^\d{2}/\d{2}/\d{4}$")
# The PIW template's date format (its date columns are m/d/yyyy), used for
# every date on the PIW and the MDIW (finance, Oct 2026): real Excel dates,
# never text, so LA reads them the same way in both workbooks.
IMPORT_DATE_FORMAT = "m/d/yyyy;@"
DATE_TEMPLATES = ("PIW_template.xlsx", "MDIW_template.xlsx")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _is_date_header(h):
    return "date" in str(h or "").lower()


def _as_datetime(v):
    if isinstance(v, datetime):
        return datetime(v.year, v.month, v.day)
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    if isinstance(v, str):
        t = v.strip()
        if _ISO.match(t):
            try:
                y, m, d = (int(x) for x in t[:10].split("-"))
                return datetime(y, m, d)
            except ValueError:
                return None
        if _DMY.match(t):
            d, m, y = t.split("/")
            try:
                return datetime(int(y), int(m), int(d))
            except ValueError:
                return None
    if hasattr(v, "to_pydatetime"):
        try:
            x = v.to_pydatetime()
            return datetime(x.year, x.month, x.day)
        except Exception:
            return None
    return None


def _cell_value(v):
    """Dates were formatted DD/MM/YYYY for the grid - write real dates
    into the import workbooks so LA parses them natively."""
    if isinstance(v, str) and _DMY.match(v):
        d, m, y = v.split("/")
        try:
            return datetime(int(y), int(m), int(d))
        except ValueError:
            return v
    return v


def _scrub_external_links(wb):
    """Remove any external workbook links so Excel never shows the
    'repaired records: external formula reference' warning. openpyxl
    exposes these as wb._external_links."""
    try:
        if getattr(wb, "_external_links", None):
            wb._external_links = []
    except Exception:
        pass
    return wb



def _to_shared_strings(path):
    """Rewrite a saved .xlsx so text cells use a shared-string table
    instead of inline strings.

    openpyxl always writes <c t="inlineStr"><is><t>text</t></is></c> and
    never creates xl/sharedStrings.xml. Both forms are valid OOXML, but
    some importers (Lease Accelerator among them) only read the shared
    string table - to those parsers the cells look empty, which shows up
    as "unable to find required column". Excel rewrites the file with a
    shared-string table when a human opens and saves it, which is why a
    manual copy/paste of the headers made the same file work.

    This does the same conversion automatically, in place.
    """
    import shutil
    import zipfile
    path = Path(path)
    with zipfile.ZipFile(path) as zin:
        names = zin.namelist()
        if any(n.endswith("xl/sharedStrings.xml") for n in names):
            return False                     # already shared
        parts = {n: zin.read(n) for n in names}

    inline = re.compile(
        rb'<c([^>]*?)\st="inlineStr"([^>]*?)>\s*<is>(.*?)</is>\s*</c>',
        re.S)
    table, index = [], {}
    changed = False

    def repl(m):
        nonlocal changed
        pre, post, inner = m.group(1), m.group(2), m.group(3)
        key = inner.strip()
        if key not in index:
            index[key] = len(table)
            table.append(key)
        changed = True
        return (b'<c' + pre + b' t="s"' + post + b'><v>'
                + str(index[key]).encode() + b'</v></c>')

    for name in list(parts):
        if re.match(r"xl/worksheets/sheet\d+\.xml$", name):
            new = inline.sub(repl, parts[name])
            if new != parts[name]:
                parts[name] = new
    if not changed:
        return False

    # build the shared string table
    sst = [b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
           b'<sst xmlns="http://schemas.openxmlformats.org/'
           b'spreadsheetml/2006/main" count="',
           str(len(table)).encode(), b'" uniqueCount="',
           str(len(table)).encode(), b'">']
    for item in table:
        sst.append(b"<si>" + item + b"</si>")
    sst.append(b"</sst>")
    parts["xl/sharedStrings.xml"] = b"".join(sst)

    # register the part
    ct = parts.get("[Content_Types].xml", b"")
    if b"sharedStrings.xml" not in ct:
        ct = ct.replace(
            b"</Types>",
            b'<Override PartName="/xl/sharedStrings.xml" ContentType='
            b'"application/vnd.openxmlformats-officedocument.'
            b'spreadsheetml.sharedStrings+xml"/></Types>')
        parts["[Content_Types].xml"] = ct
    rels_name = "xl/_rels/workbook.xml.rels"
    rels = parts.get(rels_name, b"")
    if b"sharedStrings.xml" not in rels:
        used = [int(x) for x in re.findall(rb'Id="rId(\d+)"', rels)] or [0]
        rid = b"rId" + str(max(used) + 1).encode()
        rels = rels.replace(
            b"</Relationships>",
            b'<Relationship Id="' + rid + b'" Type="http://schemas.'
            b'openxmlformats.org/officeDocument/2006/relationships/'
            b'sharedStrings" Target="sharedStrings.xml"/>'
            b"</Relationships>")
        parts[rels_name] = rels

    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in parts.items():
            zout.writestr(name, data)
    shutil.move(str(tmp), str(path))
    return True


def write_import_workbooks(results, out_dir, month_label=None):
    """Write one complete, ready-to-import workbook per LA template.

    Returns a list of (path, rows_written). Templates with no drafted
    rows are skipped. Requires the blank templates in App/templates.
    """
    from openpyxl import load_workbook

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not month_label:
        recon = results.get("Recon Summary", pd.DataFrame())
        month_label = "run"
        for _, r in recon.iterrows():
            if str(r["Item"]).strip().lower() == "workflow month":
                month_label = str(r["Value"]).replace(" ", "_")
                break
    written = []
    for tpl_name, out_pattern, mapping in _TEMPLATE_MAP:
        tpl = TEMPLATES_DIR / tpl_name
        if not tpl.exists():
            raise FileNotFoundError(
                "Template missing: %s (the App/templates folder ships "
                "with the app)" % tpl)
        total = sum(len(results.get(key, ())) for _, key in mapping)
        if total == 0:
            continue
        wb = load_workbook(tpl)
        for sheet, key in mapping:
            df = results.get(key)
            if df is None or not len(df):
                continue
            if key == "Rent Review Adjustments" and "Approved" in df.columns:
                df = df[df["Approved"].astype(str).str.upper() != "NO"]
                if not len(df):
                    continue
            ws = wb[sheet]
            headers = [c.value for c in ws[1]]
            # match template headers to columns tolerantly: some template
            # headers carry line breaks ("Standalone \nObservable Price")
            def _norm_h(v):
                return re.sub(r"\s+", " ", str(v)).strip().lower()
            colmap = {_norm_h(c): c for c in df.columns}
            for i, (_, r) in enumerate(df.iterrows(), start=2):
                for j, h in enumerate(headers, start=1):
                    if h is None or str(h).strip() == "":
                        continue
                    if h in df.columns:
                        v = r[h]
                    elif _norm_h(h) in colmap:
                        v = r[colmap[_norm_h(h)]]
                    elif sheet == "Schedule" and h == "Schedule" \
                            and "Schedule Number" in df.columns:
                        v = r["Schedule Number"]
                    else:
                        continue
                    v = _cell_value(v)
                    if v is None or (isinstance(v, float) and pd.isna(v)) \
                            or v == "":
                        continue
                    if tpl_name in DATE_TEMPLATES and _is_date_header(h):
                        dv = _as_datetime(v)
                        if dv is not None:
                            c = ws.cell(row=i, column=j, value=dv)
                            c.number_format = IMPORT_DATE_FORMAT
                            continue
                    ws.cell(row=i, column=j, value=v)
        out = out_dir / (out_pattern % month_label)
        _scrub_external_links(wb)
        wb.save(out)
        _to_shared_strings(out)      # importers need a shared string table
        written.append((str(out), total))
    return written



# ---------------------------------------------------------------------------
# Master tracker - the Excel backend
#
# Data/master_tracker.xlsx keeps everything across runs:
#   Runs              one row per workflow run (counts, month, when)
#   Lease Master      one row per lease, updated with the latest data
#   IBR Register      IBR used per lease + a "Verified IBR" column the
#                     team fills in; verified rates are pulled back in
#                     and used automatically on the next run
#   Movement History  every routing decision, per run
#   Draft History     what was drafted into which workbook, per run
#   Exception History / Not Applicable History
# ---------------------------------------------------------------------------
TRACKER_NAME = "master_tracker.xlsx"

_LEASE_MASTER_COLS = [
    "Schedule Number", "Last Move Type", "Last Month", "Last Routed To",
    "Lease Start Date", "Duration", "Payment", "IBR Used", "IBR Source",
    "Funder", "Geo", "Facility Code", "First Seen Run", "Last Run"]
_IBR_COLS = ["Schedule Number", "Workbook", "IBR Used", "Source",
             "Verified IBR", "Last Run"]


def load_tracker(path):
    """Read the tracker workbook -> dict of DataFrames ({} if absent)."""
    path = Path(path)
    if not path.exists():
        return {}
    book = pd.read_excel(path, sheet_name=None)
    return {str(k): v for k, v in book.items()}


def load_verified_ibrs(path):
    """Schedule Number -> Verified IBR from the tracker's IBR Register."""
    book = load_tracker(path)
    reg = book.get("IBR Register")
    out = {}
    if reg is None or "Verified IBR" not in reg.columns:
        return out
    for _, r in reg.iterrows():
        v = _num(r.get("Verified IBR"))
        if v and 0 < v <= 100:
            out[str(r["Schedule Number"]).strip()] = v
    return out


def update_tracker(results, path, run_id=None):
    """Fold a run's results into the tracker workbook (upsert)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    run_id = run_id or datetime.now().strftime("%d%m%Y%H%M%S")
    book = load_tracker(path)

    recon = results.get("Recon Summary", pd.DataFrame())
    month = ""
    counts = {}
    for _, r in recon.iterrows():
        item = str(r["Item"]).strip()
        if item.lower() == "workflow month":
            month = str(r["Value"])
        if item.startswith("-> "):
            counts[item[3:]] = r["Value"]

    def append(sheet, df, run_cols=True):
        if df is None or not len(df):
            return
        df = df.copy()
        if run_cols:
            df.insert(0, "Run", run_id)
            df.insert(1, "Workflow Month", month)
        old = book.get(sheet)
        book[sheet] = pd.concat([old, df], ignore_index=True) \
            if old is not None and len(old) else df

    # 1. Runs
    run_row = {"Run": run_id, "Workflow Month": month,
               "Generated": datetime.now().strftime("%d/%m/%Y %H:%M")}
    run_row.update(counts)
    append("Runs", pd.DataFrame([run_row]), run_cols=False)

    # 2. histories
    append("Movement History", results.get("Routing"))
    append("Exception History", results.get("Exceptions"))
    append("Not Applicable History", results.get("Not Applicable"))
    drafts = []
    for wbk, key, col in [("PIW", "PIW Schedule", "Schedule Number"),
                          ("MDIW", "MDIW Schedule", "Schedule"),
                          ("Rent Review", "Rent Review Adjustments",
                           "Schedule Number"),
                          ("Terminations", "Terminations Advanced Form",
                           "Schedule Number")]:
        df = results.get(key)
        if df is not None and len(df):
            for s in df[col].astype(str).unique():
                drafts.append({"Workbook": wbk, "Schedule Number": s})
    append("Draft History", pd.DataFrame(drafts))

    # 3. Lease Master upsert (latest data wins)
    master = book.get("Lease Master")
    master = master.set_index("Schedule Number") if master is not None \
        and len(master) else pd.DataFrame(
            columns=_LEASE_MASTER_COLS).set_index("Schedule Number")
    master.index = master.index.astype(str)
    master = master.astype(object)   # reloaded sheets come back typed
    rt = results.get("Routing", pd.DataFrame())
    latest = {}
    for _, r in rt.iterrows():
        latest[str(r["Lease Reference"])] = r
    piw = results.get("PIW Schedule", pd.DataFrame())
    piw = piw.set_index(piw["Schedule Number"].astype(str)) \
        if len(piw) else piw
    ibr_check = results.get("IBR Check", pd.DataFrame())
    ibrmap = {str(r["Schedule Number"]): r for _, r in ibr_check.iterrows()}
    for ref, r in latest.items():
        row = dict.fromkeys(_LEASE_MASTER_COLS[1:], "")
        if ref in master.index:
            row.update({k: master.at[ref, k] for k in master.columns
                        if k in row})
        row.update({"Last Move Type": r["Move Type"],
                    "Last Month": r["Month"],
                    "Last Routed To": r["Routed To"],
                    "Last Run": run_id})
        if not row.get("First Seen Run"):
            row["First Seen Run"] = run_id
        if len(piw) and ref in piw.index:
            p = piw.loc[ref]
            row.update({"Lease Start Date": p["Lease Start Date"],
                        "Duration": p["Duration"], "Payment": p["Payment"],
                        "Funder": p["Funder"], "Geo": p["Geo"],
                        "Facility Code": p["Facility Code"]})
        if ref in ibrmap:
            row.update({"IBR Used": ibrmap[ref]["IBR Used"],
                        "IBR Source": ibrmap[ref]["Source"]})
        master.loc[ref] = row
    book["Lease Master"] = master.reset_index().rename(
        columns={"index": "Schedule Number"})

    # 4. IBR Register upsert - PRESERVES the team's Verified IBR column
    reg = book.get("IBR Register")
    reg = reg.set_index("Schedule Number") if reg is not None and len(reg) \
        else pd.DataFrame(columns=_IBR_COLS).set_index("Schedule Number")
    reg.index = reg.index.astype(str)
    reg = reg.astype(object)
    for ref, r in ibrmap.items():
        verified = reg.at[ref, "Verified IBR"] if ref in reg.index else ""
        reg.loc[ref] = {"Workbook": r["Workbook"],
                        "IBR Used": r["IBR Used"], "Source": r["Source"],
                        "Verified IBR": verified, "Last Run": run_id}
    book["IBR Register"] = reg.reset_index().rename(
        columns={"index": "Schedule Number"})

    order = ["Runs", "Lease Master", "IBR Register", "Movement History",
             "Draft History", "Exception History", "Not Applicable History"]
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        for name in order:
            if name in book:
                book[name].to_excel(xl, sheet_name=name, index=False)
        for name, df in book.items():
            if name not in order:
                df.to_excel(xl, sheet_name=name[:31], index=False)
    return str(path), run_id



# ---------------------------------------------------------------------------
# the client discount rate table (SA / ZAR)
#
# Data/discount_rates.xlsx holds the monthly treasury rates. IBR = the
# rate for the lease's term band in the latest table month available at
# the workflow month. Bands: A 0-23, B 24-35, C 36-47, D 48-59, E 60-71,
# F 72+. Code = band letter + Excel serial of the month (A45658 =
# band A, Jan 2025) - generated automatically.
# ---------------------------------------------------------------------------
RATES_NAME = "discount_rates.xlsx"
RATE_BANDS = [(0, 23, "A"), (24, 35, "B"), (36, 47, "C"),
              (48, 59, "D"), (60, 71, "E"), (72, 10 ** 9, "F")]

# Sequentra headings that carry the beneficial occupation date and the
# lease type. Listed in preference order; the first one present wins, so
# a renamed export keeps working.
BO_DATE_COLS = ("BO Date", "B/O Date", "BO date",
                "Beneficial Occupation Date", "Beneficial Occupation",
                "Beneficial Occupancy Date", "BO", "Beneficial Occ Date")
LEASE_TYPE_COLS = ("ABSA Lease Type", "Lease Type", "the client Lease Type")


def is_receivable(ref):
    """Receivable leases carry an R in the schedule slot - the letter
    just before the trailing digits (4041R007, R0605R04). A property
    code starting with R (R0006P01) does NOT make a lease receivable."""
    m = re.search(r"([A-Z])\d+$", str(ref).strip().upper())
    return bool(m) and m.group(1) == "R"


def rate_band(term_months):
    """Band letter for a lease term in months (None if unknown)."""
    try:
        t = float(term_months)
    except (TypeError, ValueError):
        return None
    for lo, hi, band in RATE_BANDS:
        if lo <= t <= hi:
            return band
    return None


def _excel_serial(d):
    return (date(d.year, d.month, 1) - date(1899, 12, 30)).days


def rate_code(band, month):
    return "%s%d" % (band, _excel_serial(month))


def load_rate_table(path):
    """Read the rates workbook -> {(YYYY-MM, band): rate}. Tolerates the
    the client layout (title rows above the header) and the canonical layout
    the in-app editor writes."""
    path = Path(path)
    if not path.exists():
        return {}
    book = _read_workbook(path, sheet_name=None, header=None)
    raw = _pick_sheet(book, "Rates", contains="rate")
    if raw is None:
        raw = next(iter(book.values()))
    hr = None
    for i in range(min(12, len(raw))):
        cells = [str(v) for v in raw.iloc[i].tolist()]
        if any("From" in c for c in cells) and \
                any("rate" in c.lower() for c in cells):
            hr = i
            break
    if hr is None:
        return {}
    df = raw.iloc[hr + 1:]
    out = {}
    for _, r in df.iterrows():
        month = _to_date(r.iloc[0])
        lo = _num(r.iloc[1])
        rate = _num(r.iloc[3])
        if month is None or lo is None or rate is None:
            continue
        band = _text(r.iloc[4]) or rate_band(lo)
        if band:
            out[(month.strftime("%Y-%m"), band.strip().upper()[:1])] = rate
    return out


def lookup_rate(rates, workflow_month, term_months):
    """(rate, band, code, rate_month) for the latest table month at or
    before the workflow month; (None, band, None, None) if unavailable."""
    band = rate_band(term_months)
    if not rates or band is None:
        return None, band, None, None
    wm = month_start(workflow_month).strftime("%Y-%m")
    months = sorted({m for m, _b in rates})
    # latest table month at or before the target
    for m in reversed(months):
        if m <= wm and (m, band) in rates:
            d = date(int(m[:4]), int(m[5:7]), 1)
            return rates[(m, band)], band, rate_code(band, d), \
                d.strftime("%B %Y")
    # target predates the table (e.g. an old signature date) -> use the
    # earliest month the table covers for that band
    for m in months:
        if (m, band) in rates:
            d = date(int(m[:4]), int(m[5:7]), 1)
            return rates[(m, band)], band, rate_code(band, d), \
                d.strftime("%B %Y")
    return None, band, None, None


def save_rate_month(path, month, band_rates):
    """Add or update one month's six band rates (the in-app editor).
    Rewrites the Rates sheet in a clean canonical layout - bands, the
    from/to month ranges and the codes are generated automatically."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    rates = load_rate_table(path)
    m = month_start(month).strftime("%Y-%m")
    for band, rate in band_rates.items():
        if rate is not None:
            rates[(m, band.upper())] = float(rate)

    wb = Workbook()
    ws = wb.active
    ws.title = "Rates"
    ws.cell(1, 2, "IFRS 16 transition rates")
    ws.cell(2, 2, "SA ZAR")
    hdr = ["Month", "From (months#)", "To (months#)", "Nominal rate(%)",
           "Band", "Code"]
    for j, h in enumerate(hdr, 1):
        c = ws.cell(4, j, h)
        c.font = Font(bold=True, color="003F2D", name="Segoe UI")
        c.fill = PatternFill("solid", fgColor="DBE7E1")
    widths = [12, 15, 15, 15, 8, 12]
    from openpyxl.utils import get_column_letter
    for j, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(j)].width = w
    bands = {b: (lo, hi) for lo, hi, b in RATE_BANDS}
    row = 5
    for mk in sorted({mm for mm, _b in rates}):
        d = date(int(mk[:4]), int(mk[5:7]), 1)
        for _lo, _hi, band in RATE_BANDS:
            if (mk, band) not in rates:
                continue
            lo, hi = bands[band]
            ws.cell(row, 1, datetime(d.year, d.month, 1))
            ws.cell(row, 1).number_format = "YYYY-MM-DD"
            ws.cell(row, 2, lo)
            ws.cell(row, 3, "And greater" if hi > 10 ** 6 else hi)
            ws.cell(row, 4, rates[(mk, band)])
            ws.cell(row, 5, band)
            ws.cell(row, 6, rate_code(band, d))
            row += 1
    wb.save(path)
    return rates


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description="Run the lease workflow from the command line.")
    ap.add_argument("la", help="LA transaction report (.xls/.xlsx)")
    ap.add_argument("movements", help="Movement reconciliation file")
    ap.add_argument("sequentra", help="Sequentra validation export")
    ap.add_argument("-o", "--out", default="lease_workflow_output.xlsx")
    ap.add_argument("-m", "--month", help="Workflow month as MM/YYYY")
    ap.add_argument("-s", "--steps",
                    help="Sequentra step payments report (needed when the "
                         "movement file is a raw RR06/ARO06)")
    ap.add_argument("-L", "--lookback", type=int, default=1,
                    help="months of movements to process (default 1 - "
                         "just the selected month)")
    ap.add_argument("-r", "--rates",
                    help="the client discount rate table (Data/%s)" % RATES_NAME)
    ap.add_argument("-t", "--tracker",
                    help="master tracker workbook to update (verified "
                         "IBRs in it are used automatically)")
    args = ap.parse_args()

    wm = None
    if args.month:
        mm, yy = args.month.split("/")
        wm = date(int(yy), int(mm), 1)

    results = run_workflow_from_files(args.la, args.movements,
                                      args.sequentra, wm,
                                      steps_path=args.steps,
                                      lookback=args.lookback,
                                      tracker_path=args.tracker,
                                      rates_path=args.rates)
    with pd.ExcelWriter(args.out, engine="openpyxl") as xl:
        for name, df in results.items():
            df.to_excel(xl, sheet_name=name[:31], index=False)
    print("Wrote", args.out)
    try:
        for p, n in write_import_workbooks(
                results, Path(args.out).resolve().parent):
            print("Wrote", p, "(%d rows)" % n)
    except FileNotFoundError as exc:
        print("Import workbooks skipped:", exc)
    print(results["Recon Summary"].to_string(index=False))
