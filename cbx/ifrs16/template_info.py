"""A clean, readable overview of what the IFRS 16 workflow produces.

Everything here is read from the real engine: the template map it writes into, and the actual
header rows of the shipped template workbooks. Nothing is hand-typed, so the preview cannot drift
from what the tool really does. (Ported as-is from the old portal's ifrs16/template_info.py.)
"""
from __future__ import annotations

from pathlib import Path

from . import ENGINE_DIR  # noqa: F401 — importing the package puts the engine on sys.path

# Plain-language purpose per output workbook, keyed by its template file.
PURPOSE = {
    "CIW_template.xlsx": "New companies, addresses and cost centres that must exist in the lease system before anything else can be imported.",
    "PIW_template.xlsx": "Brand-new leases: the schedule, its details, end-of-term options and the payment steps.",
    "MDIW_template.xlsx": "Changes to leases that already exist — modifications, with the same detail sheets as a new lease plus a summary of what changed.",
    "RentReview_template.xlsx": "Rent review adjustments to existing payment schedules (only rows marked as approved are written).",
    "Terminations_template.xlsx": "Leases ending early, on the advanced termination form.",
}
def workbook_overview(max_columns: int = 14) -> list[dict]:
    """[{file, output_name, purpose, sheets:[{name, source, columns, more, total_columns}], available}]"""
    import lease_engine  # engine module, unmodified
    from openpyxl import load_workbook

    out = []
    for tpl_name, out_pattern, mapping in lease_engine._TEMPLATE_MAP:
        tpl = lease_engine.TEMPLATES_DIR / tpl_name
        entry = {"file": tpl_name, "output_name": out_pattern % "<month>", "purpose": PURPOSE.get(tpl_name, ""), "sheets": [], "available": tpl.exists()}
        if tpl.exists():
            wb = load_workbook(tpl, read_only=True)
            for sheet, source_key in mapping:
                if sheet not in wb.sheetnames:
                    continue
                ws = wb[sheet]
                headers: list[str] = []
                for row in ws.iter_rows(min_row=1, max_row=1, values_only=True):
                    headers = [str(h).replace("\n", " ").strip() for h in row if h not in (None, "")]
                    break
                entry["sheets"].append({"name": sheet, "source": source_key, "columns": headers[:max_columns], "more": max(0, len(headers) - max_columns), "total_columns": len(headers)})
            wb.close()
        out.append(entry)
    return out


def run_summary(results, written) -> dict:
    """Turn an engine result into a short 'what it did' list: [{workbook, rows}] plus the recon lines
    the engine itself reported."""
    files = [{"workbook": Path(p).name, "rows": int(rows)} for p, rows in written]
    recon = []
    try:
        df = results.get("Recon Summary")
        if df is not None and len(df):
            for _, r in df.iterrows():
                recon.append({"item": str(r["Item"]), "value": str(r["Value"])})
    except Exception:  # noqa: BLE001
        pass
    return {"files": files, "recon": recon[:40]}
