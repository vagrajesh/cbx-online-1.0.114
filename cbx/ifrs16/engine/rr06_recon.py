"""LA transaction report vs the RR06 contract list (finance, Oct 2026).

    run_recon(la_path, rr06_path, period)   -> results (summary, lists, notes)
    write_recon_workbook(results, out_path)  -> the reconciliation on its own, in Excel

Steps, as finance set them out:
  1. LA: Lease Reference = the first 8 characters of Transaction #.
  2. LA: leave out Deal Status "Disposed (Modification)".
  3. LA: keep Lessee "ABSA Bank Limited" with Asset Location South Africa.
  4. Compare the LA lease references to the RR06.
  5. List leases on the RR06 and not on the LA, and leases Booked on the LA and not on the RR06.
  6. Sort the RR06-not-LA leases, first match wins, in this order:
        Receivable          Lease Marker says Receivable
        Future acquisition  Initial lease date after the period end
        Monthly             Lease Expiry Date reads "Monthly"
        A year or less      Current Lease Term is 1 or less
        Zero rental         Current Charges add up to nil
        Exception           none of the above: to investigate
Column headings are matched loosely (case, spaces and punctuation ignored, a few other names accepted).
"""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd

import lease_engine as LE

LESSEE = "ABSA Bank Limited"
COUNTRY = "South Africa"
REF_LEN = 8

CATEGORIES = ("Receivable", "Future acquisition", "Monthly", "A year or less", "Zero rental", "Exception")
CATEGORY_RULE = {
    "Receivable": "Lease Marker is Receivable",
    "Future acquisition": "Initial lease date is after the period end",
    "Monthly": "Lease Expiry Date reads Monthly",
    "A year or less": "Current Lease Term is 1 or less",
    "Zero rental": "Current Charges total is nil",
    "Exception": "None of the above: investigate",
}

# what we look for -> headings accepted (compared with LE.norm_head)
LA_COLS = {
    "Transaction #": ("transaction #", "transaction", "transaction no", "transaction number"),
    "Deal Status": ("deal status", "status"),
    "Lessee": ("lessee", "lessee name"),
    "Asset Location": ("asset location", "asset location s", "asset locations", "location", "country"),
}
RR_COLS = {
    "Lease Reference": ("lease reference", "lease ref", "schedule number", "contract reference", "record id"),
    "Lease Marker": ("lease marker", "marker"),
    "Initial Lease Date": ("initial lease date", "initial lease start date", "initial start date", "initial lease commencement date"),
    "Lease Expiry Date": ("lease expiry date", "expiry date", "lease expiry"),
    "Current Lease Term": ("current lease term", "lease term", "current term",
                           "current lease term years", "lease term years", "current lease term in years"),  # 1.0.106 / 2.10.9: the real RR06 says '(Years)'
    "Current Charges": ("current charges", "current charge", "total current charges"),
}
RR_KEEP = ("Lease Name", "Building Name", "Property Name", "Lessor", "Landlord", "Lease Status", "Region", "Country")


def _norm(s) -> str:
    return LE.norm_head(s)


def _find(columns, accepted) -> str | None:
    by = {_norm(c): c for c in columns}
    for a in accepted:
        if _norm(a) in by:
            return by[_norm(a)]
    return None


def _ref(v) -> str:
    return re.sub(r"\s+", "", LE._text(v)).upper()


def _plain(v) -> str:
    return re.sub(r"[^a-z0-9]+", "", LE._text(v).lower())


def _period_end(period) -> date:
    if isinstance(period, datetime):
        period = period.date()
    if isinstance(period, date):
        return LE.month_end(period)
    s = str(period or "").strip()
    m = re.match(r"^(\d{4})-(\d{1,2})$", s)
    if m:
        return LE.month_end(date(int(m.group(1)), int(m.group(2)), 1))
    d = LE._to_date(s)
    if d is None:
        raise ValueError("The period must look like 2026-09 (year-month).")
    return LE.month_end(d)


# ---- reading ------------------------------------------------------------------------------------------------
def load_la(path) -> pd.DataFrame:
    return LE.load_la_register(path)


def load_rr06(path) -> pd.DataFrame:
    """The RR06 contract list: the tab whose header row has a lease reference and the most of the columns
    above; a tab called 'Contract List' wins a tie."""
    book = LE._read_workbook(path, sheet_name=None, header=None)
    best = None
    for name, raw in book.items():
        if raw is None or not len(raw):
            continue
        for i in range(min(25, len(raw))):
            heads = [_norm(v) for v in raw.iloc[i].tolist()]
            if not any(_norm(a) in heads for a in RR_COLS["Lease Reference"]):
                continue
            score = sum(any(_norm(a) in heads for a in acc) for acc in RR_COLS.values())
            score += 0.5 if "contract list" in _norm(name) else 0
            if best is None or score > best[0]:
                best = (score, raw, i)
            break
    if best is None:
        raise ValueError("No 'Lease Reference' heading found in the RR06 file '%s'.%s"
                         % (Path(str(path)).name, LE.what_file(book, "movements")))
    df = LE._reheader(best[1], best[2])
    # 1.0.106 / 2.10.9: the real Contracts tab has no 'Current Charges' column: a merged 'Current Charges' banner
    # sits over Rent / Parking / Other (the same three headings repeat under Area, Escalation, Charges Old ...).
    # Those columns, added up per row, are the current charges.
    if not _find(df.columns, RR_COLS["Current Charges"]):
        cols = [j for j, b in enumerate(_banners(best[1], best[2])) if _norm(b) in RR_COLS["Current Charges"]]
        if cols:
            body = best[1].iloc[best[2] + 1:, cols].reset_index(drop=True)
            def _total(row):
                vals = [v for v in (LE._num(x) for x in row) if v is not None]
                return round(sum(vals), 2) if vals else None
            df["Current Charges"] = body.apply(_total, axis=1).values
    return df


def _banners(raw, header_row) -> list:
    """The merged group banner over each column of the header row (a merged cell's text is in its first column
    only, so it is carried right until the next banner); '' where there is none."""
    if header_row <= 0:
        return [""] * raw.shape[1]
    out, cur = [], ""
    for v in raw.iloc[header_row - 1].tolist():
        t = LE._text(v)
        cur = t or cur
        out.append(cur)
    return out


# ---- the reconciliation -------------------------------------------------------------------------------------
def clean_la(la: pd.DataFrame, lessee: str = LESSEE, country: str = COUNTRY):
    """Steps 1 to 3. Returns (cleaned rows, notes, column names found)."""
    cols = {k: _find(la.columns, v) for k, v in LA_COLS.items()}
    if not cols["Transaction #"]:
        raise ValueError("The LA report has no 'Transaction #' column.")
    notes = []
    df = la.copy()
    df["Lease Reference"] = df[cols["Transaction #"]].map(lambda v: _ref(v)[:REF_LEN])
    df = df[df["Lease Reference"] != ""]
    n0 = len(df)
    if cols["Deal Status"]:
        disposed = df[cols["Deal Status"]].map(_plain) == "disposedmodification"
        df = df[~disposed]
        notes.append(f"Left out {int(disposed.sum())} LA rows with Deal Status Disposed (Modification)")
    else:
        notes.append("The LA report has no Deal Status column: nothing left out for Disposed (Modification)")
    if cols["Lessee"]:
        keep = df[cols["Lessee"]].map(_plain) == _plain(lessee)
        notes.append(f"Kept {int(keep.sum())} LA rows with Lessee {lessee}")
        df = df[keep]
    else:
        notes.append("The LA report has no Lessee column: every lessee kept")
    if cols["Asset Location"]:
        keep = df[cols["Asset Location"]].map(lambda v: _plain(country) in _plain(v))
        notes.append(f"Kept {int(keep.sum())} of those with Asset Location {country}")
        df = df[keep]
    else:
        notes.append("The LA report has no Asset Location column: every location kept")
    notes.insert(0, f"LA rows read: {n0}")
    return df.reset_index(drop=True), notes, cols


def _rr06_leases(rr: pd.DataFrame):
    """One row a lease (charges added up), with the columns we need named as finance names them."""
    cols = {k: _find(rr.columns, v) for k, v in RR_COLS.items()}
    if not cols["Lease Reference"]:
        raise ValueError("The RR06 has no 'Lease Reference' column.")
    df = rr.copy()
    df["_ref"] = df[cols["Lease Reference"]].map(_ref)
    df = df[(df["_ref"] != "") & (df["_ref"] != "LEASEREFERENCE")]
    extra = [c for c in RR_KEEP if c in df.columns]
    rows = []
    for ref, g in df.groupby("_ref", sort=True):
        first = g.iloc[0]
        r = {"Lease Reference": ref}
        for k in ("Lease Marker", "Initial Lease Date", "Lease Expiry Date", "Current Lease Term"):
            r[k] = first[cols[k]] if cols[k] else None
        if cols["Current Charges"]:
            vals = [LE._num(v) for v in g[cols["Current Charges"]]]
            vals = [v for v in vals if v is not None]
            r["Current Charges"] = round(sum(vals), 2) if vals else None
        else:
            r["Current Charges"] = None
        for c in extra:
            r[c] = first[c]
        r["RR06 lines"] = len(g)
        rows.append(r)
    return pd.DataFrame(rows), cols


def categorise(r, period_end: date) -> str:
    if "receivable" in LE._text(r.get("Lease Marker")).lower():
        return "Receivable"
    d = LE._to_date(r.get("Initial Lease Date"))
    if d is not None and d > period_end:
        return "Future acquisition"
    if "monthly" in LE._text(r.get("Lease Expiry Date")).lower():
        return "Monthly"
    t = LE._num(r.get("Current Lease Term"))
    if t is not None and t <= 1:
        return "A year or less"
    c = r.get("Current Charges")
    if c is not None and not pd.isna(c) and abs(float(c)) < 0.005:
        return "Zero rental"
    return "Exception"


def run_recon(la_path, rr06_path, period, lessee: str = LESSEE, country: str = COUNTRY) -> dict:
    end = _period_end(period)
    la, notes, la_cols = clean_la(load_la(la_path), lessee, country)
    rr, rr_cols = _rr06_leases(load_rr06(rr06_path))
    la_refs = set(la["Lease Reference"])
    rr_refs = set(rr["Lease Reference"]) if len(rr) else set()

    rr_not_la = rr[~rr["Lease Reference"].isin(la_refs)].copy() if len(rr) else rr.copy()
    if len(rr_not_la):
        rr_not_la.insert(1, "Category", rr_not_la.apply(lambda r: categorise(r, end), axis=1))
        rr_not_la["Initial Lease Date"] = rr_not_la["Initial Lease Date"].map(lambda v: LE._to_date(v) or LE._text(v))
    else:
        rr_not_la["Category"] = []

    status = la_cols["Deal Status"]
    booked = la[la[status].map(_plain) == "booked"] if status else la.iloc[0:0]
    la_not_rr = booked[~booked["Lease Reference"].isin(rr_refs)].copy()
    show = ["Lease Reference"] + [c for c in la_cols.values() if c] + [c for c in ("Company Name", "Lease Name", "Entity", "Current Payment (Transactional)", "Currency") if c in la_not_rr.columns]
    show = list(dict.fromkeys(show))
    la_not_rr = la_not_rr[show].drop_duplicates("Lease Reference").reset_index(drop=True)
    if not status:
        notes.append("No Deal Status on the LA report, so leases Booked on LA and not on the RR06 can't be listed")

    summary = []
    for cat in CATEGORIES:
        sub = rr_not_la[rr_not_la["Category"] == cat] if len(rr_not_la) else rr_not_la
        charges = pd.to_numeric(sub["Current Charges"], errors="coerce").sum() if len(sub) else 0.0
        summary.append({"Category": cat, "Rule": CATEGORY_RULE[cat], "Leases": int(len(sub)), "Current Charges": round(float(charges or 0), 2)})
    summary = pd.DataFrame(summary)

    missing = [k for k, v in rr_cols.items() if not v and k != "Lease Reference"]
    if missing:
        notes.append("Not on the RR06 (so that category can't be used): " + ", ".join(missing))
    overview = [
        ("Period end", end.strftime("%d/%m/%Y")),
        ("Lessee", lessee), ("Asset location", country),
        ("LA leases after the filters", len(la_refs)),
        ("RR06 leases", len(rr_refs)),
        ("On both", len(la_refs & rr_refs)),
        ("On the RR06 and not on LA", int(len(rr_not_la))),
        ("Booked on LA and not on the RR06", int(len(la_not_rr))),
        ("Exceptions to investigate", int((rr_not_la["Category"] == "Exception").sum()) if len(rr_not_la) else 0),
    ]
    return {"Overview": pd.DataFrame(overview, columns=["Item", "Value"]), "Summary": summary,
            "RR06 not on LA": rr_not_la.reset_index(drop=True), "LA not on RR06": la_not_rr,
            "Notes": notes, "_period_end": end}


# ---- the workbook ------------------------------------------------------------------------------------------
def _sheet_name(s: str) -> str:
    return re.sub(r"[\[\]:*?/\\]", " ", s)[:31]


def write_recon_workbook(results: dict, out_path) -> str:
    """Overview, Summary, one tab per category, RR06 not on LA (all), LA not on RR06, Notes."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    head_fill, head_font = PatternFill("solid", fgColor="12372A"), Font(bold=True, color="FFFFFF")
    wb = Workbook()
    wb.remove(wb.active)

    def put(title, df):
        ws = wb.create_sheet(_sheet_name(title))
        cols = list(df.columns)
        ws.append(cols)
        for c in ws[1]:
            c.fill, c.font, c.alignment = head_fill, head_font, Alignment(vertical="center")
        for row in df.itertuples(index=False):
            vals = []
            for v in row:
                if isinstance(v, float) and pd.isna(v):
                    v = None
                elif isinstance(v, pd.Timestamp):
                    v = v.to_pydatetime()
                vals.append(v)
            ws.append(vals)
        for i, col in enumerate(cols, 1):
            width = max([len(str(col))] + [len(str(v)) for v in df[col].head(500).tolist()]) if len(df) else len(str(col))
            ws.column_dimensions[get_column_letter(i)].width = min(max(10, width + 2), 50)
            if "date" in str(col).lower():
                for cell in ws.iter_rows(min_row=2, min_col=i, max_col=i):
                    if isinstance(cell[0].value, (date, datetime)):
                        cell[0].number_format = "dd/mm/yyyy"
            if str(col) == "Current Charges" or "Payment" in str(col):
                for cell in ws.iter_rows(min_row=2, min_col=i, max_col=i):
                    cell[0].number_format = "#,##0.00"
        ws.freeze_panes = "A2"
        if len(df):
            ws.auto_filter.ref = ws.dimensions

    put("Overview", results["Overview"])
    put("Summary", results["Summary"])
    rr = results["RR06 not on LA"]
    for cat in CATEGORIES:
        sub = rr[rr["Category"] == cat] if len(rr) else rr
        put(cat, sub)
    put("RR06 not on LA", rr)
    put("LA not on RR06", results["LA not on RR06"])
    put("Notes", pd.DataFrame({"Note": results.get("Notes") or []}))
    wb.save(str(out_path))
    return str(out_path)
