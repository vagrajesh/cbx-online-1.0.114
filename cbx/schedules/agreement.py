"""Site Schedules, the new engine (1.0.88): CBX-Connect 2's reading and Word agreement inside the old CBX's
Builder tool.

    read(files)              the Swiftnet PDFs (quote, NTP, an earlier agreement) → the row's fields, the
                             charge lines as text, what to check, and everything read (kept on the row)
    build(details, fields)   the row (with what a person corrected) → the Site Schedule Agreement in Word

reader.py and template.py are CBX-Connect 2's files unchanged, so both apps read a quote the same way and
make the same Word document. Every numbered comment on a quote is its own line in the charge table.
"""
from __future__ import annotations

import json
import re
from typing import Any

from . import reader as R
from . import template as T

# the row's fields, top to bottom — the same as CBX-Connect 2's form
FIELDS: list[tuple[str, str, str]] = [
    ("site_name", "Site name", "text"), ("company", "Co-user company", "text"), ("lease_type", "Lease type", "text"),
    ("start_date", "Commencement date", "text"), ("term_years", "Term: years", "int"), ("term_months", "Term: months", "int"),
    ("esc_year", "First escalation: 01 April of", "int"),
    ("col_id", "Co-locate ID", "text"), ("contract_no", "Contract No", "text"), ("soc_no", "Swiftnet SOC No", "text"),
    ("t100_ref", "T100 reference", "text"), ("t000_ref", "T000 reference", "text"), ("co_code", "Co-user code (file name)", "text"),
    ("lease_code", "Lease code (file name)", "text"),
    ("swiftnet_rep", "Swiftnet representative", "text"), ("couser_rep", "Co-user representative", "text"),
    ("stac", "STAC code", "text"), ("pid", "Site PID", "text"), ("region", "Region", "text"), ("lat", "Latitude", "text"), ("lon", "Longitude", "text"),
    ("struct_name", "Structure / mast name", "text"), ("struct_cat", "Structure category", "text"), ("site_owner", "Site owner", "text"),
    ("co_site_name", "Co-user site name", "text"), ("bs_site_no", "BS / site no", "text"), ("co_region", "Co-user region", "text"),
    ("mast_cat", "Mast category", "text"), ("mast_hagl", "Mast height HAGL (m)", "text"), ("inst_hagl", "Installation height HAGL (m)", "text"),
    ("scope", "Antenna comments / scope", "text"), ("equip_comments", "Equipment / accommodation comments", "text"),
    ("current_install", "Client current installation on site", "text"),  # 1.0.108: its own clause 7 heading, shown only with text
]
KEYS = [k for k, _, _ in FIELDS]
LABEL = {k: lab for k, lab, _ in FIELDS}
INTS = {k for k, _, kind in FIELDS if kind == "int"}
REQUIRED = ["lease_type", "company", "col_id", "site_name", "pid", "region", "lat", "lon", "co_site_name", "bs_site_no", "co_region", "start_date",
            "mast_cat", "mast_hagl", "inst_hagl", "t100_ref", "t000_ref"]
LEASE_CODES = R.LEASE_CODES  # 1.0.108: one list with the page (reader.py)
CO_CODES = {"vodacom": "VDC", "mtn": "MTN", "telkom": "TKM", "cell c": "CELLC", "rain": "RAIN"}
TABLE_TITLES = {"t1": "6.1 Rental", "t2": "6.2 Electricity", "t3": "6.3 Escort and road", "t4": "6.4 Once-off"}
SOC_NO = "4320151972"


def _clean(fields: dict) -> dict:
    out = {}
    for k in KEYS:
        v = fields.get(k)
        if v in (None, ""):
            continue
        if k in INTS:
            try:
                out[k] = int(float(str(v).strip()))
            except ValueError:
                continue
        else:
            out[k] = str(v).strip()[:4000]
    for k in R.REF_PREFIX:  # 1.0.108: "T100/<number>", "T000/<number>" whatever the row holds ("T100 9130", "9130"); no number, no line
        if k in out:
            out[k] = R.ref_text(k, out[k])
            if not out[k]:
                del out[k]
    return out


def missing(fields: dict) -> list[str]:
    new = re.search(r"New Application", str(fields.get("lease_type") or ""), re.I)
    return [k for k in REQUIRED if not str(fields.get(k) or "").strip() and not (new and k in ("t100_ref", "t000_ref"))]


def charges_text(tables: dict) -> str:
    """The charge tables as plain lines, for the row: '6.1 Rental · Mast Space · 3.00m · three panels · R 1 234.00'."""
    lines = []
    for k, title in TABLE_TITLES.items():
        for r in tables.get(k) or []:
            bits = [title] + [str(r.get(c) or "").strip() for c in ("item", "req", "desc", "amt")]
            lines.append(" · ".join(b for b in bits if b))
    return "\n".join(lines)


def read(files: list[tuple[str, bytes]]) -> dict:
    """What the Builder job gives back to the row."""
    r = R.read_pdfs(files)
    kinds = [x.get("kind") for x in r["files"]]
    if not any(k in ("quote", "ntp", "agreement") for k in kinds):
        raise ValueError("None of these PDFs is a Swiftnet quote, NTP or Site Schedule Agreement I can read: " + "; ".join(r["notes"])[:300])
    fields = {"soc_no": SOC_NO, **{k: v for k, v in (r["fields"] or {}).items() if v not in (None, "")}}
    if not fields.get("co_code") and fields.get("company"):
        low = fields["company"].lower()
        fields["co_code"] = next((code for name, code in CO_CODES.items() if name in low), "")
    if fields.get("start_date") and not fields.get("esc_year"):
        d = R.parse_long_date(fields["start_date"])
        if d:
            fields["esc_year"] = R.first_escalation_year(d)  # 1.0.108: the reader's rule: 01 Jan–31 Mar → 01 April that year
    fields = _clean(fields)
    tables = {k: r["tables"].get(k) or [] for k in ("t1", "t2", "t3", "t4")}
    tables["extra"] = []
    checks = list(r["notes"])
    gone = missing(fields)
    if gone:
        checks.append("Not on the PDFs: " + ", ".join(LABEL[k] for k in gone) + ". Type them on the row (Edit), or add the NTP and read it again.")
    total = sum(R.num(x.get("amt")) for x in tables["t1"])
    out: dict[str, Any] = {k: fields.get(k) for k in KEYS}
    out.update(monthly=round(total, 2), charges=charges_text(tables), checks=checks, read_from=", ".join(f"{x['name']} ({x['kind']})" for x in r["files"]),
               details={"fields": fields, "tables": tables, "notes": r["notes"], "files": r["files"]})
    return out


def file_name(fields: dict, tables: dict) -> str:
    """'9130 - 5183 - 70204797 - TIERVLEI EXCHANGE - 01.08.2026 - 31.07.2031 - 7708 - R - VDC.docx' (empty parts left out)."""
    v = T.values({"fields": fields, "tables": tables})
    d1, d2 = R.parse_long_date(fields.get("start_date") or ""), R.parse_long_date(v.get("end_date") or "")
    t100, t000 = R.ref_number(fields.get("t100_ref")), R.ref_number(fields.get("t000_ref"))
    # 1.0.108: both numbers as Swiftnet names the file; one alone keeps its prefix, so reading the file back never swaps it
    refs = [t100, t000] if t100 and t000 else [f"T100-{t100}" if t100 else "", f"T000-{t000}" if t000 else ""]
    parts = [*refs, fields.get("contract_no"), str(fields.get("site_name") or "").upper(),
             d1.strftime("%d.%m.%Y") if d1 else "", d2.strftime("%d.%m.%Y") if d2 else "", fields.get("col_id"),
             R.lease_code_for(fields.get("lease_type"), fields.get("lease_code")), fields.get("co_code")]  # 1.0.108: the code follows the lease type
    name = " - ".join(re.sub(r"[\\/:*?\"<>|]+", " ", str(p)).strip() for p in parts if str(p or "").strip())
    return (name or "Site Schedule")[:200] + ".docx"


def build(details: Any, row_fields: dict, signers: dict | None = None) -> tuple[str, bytes]:
    """The agreement in Word: what was read, with every field as the row now has it (a person's corrections win)."""
    if isinstance(details, str):
        try:
            details = json.loads(details or "{}")
        except ValueError:
            details = {}
    details = details if isinstance(details, dict) else {}
    fields = dict(details.get("fields") or {})
    for k in KEYS:
        if k in row_fields:
            v = row_fields[k]
            if v in (None, ""):
                fields.pop(k, None)
            else:
                fields[k] = v
    fields = _clean(fields)
    if fields.get("start_date") and not R.parse_long_date(fields["start_date"]):
        raise ValueError("Commencement date: write it like 01 August 2026.")
    tables = details.get("tables") or {"t1": [], "t2": [], "t3": [], "t4": []}
    signers = signers or {}
    signing = {"swift": {"city": "Centurion", "date": "", "witnesses": ["", ""], "people": signers.get("swift") or []},
               "couser": {"city": "", "date": "", "witnesses": ["", ""], "people": signers.get("couser") or []}}
    data = T.to_docx(T.default(), {"fields": fields, "tables": tables, "signing": signing})
    return file_name(fields, tables), data
