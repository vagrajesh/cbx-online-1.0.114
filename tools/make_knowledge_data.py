# 1.0.98: from CBX-Connect 2.10.3 tools/make_knowledge_data.py (1.0.101: + the utilities list, from 2.10.6)
"""Rebuild CBX's SAP seed files (2.10.3) from SAP's own exports — Edwin's next export replaces them in one step:

    python tools/make_knowledge_data.py "Document Type List.xlsx" "Chart of Accounts.xlsx" "Vendor Master - Comp Code.xlsx" "Cost Center List.xlsx" \
        vendor_mappings.xlsx Utilities_Master_List.xlsx

Any of them may be left out (give "-" in its place, e.g. `… - - "Vendor Master.xlsx"`). Writes, utf-8:

    cbx/knowledge_data/sap_doc_types.csv          every column of SAP's Document Type List (sheet Data), as SAP gives it
    cbx/knowledge_data/chart_of_accounts.csv      G/L Account, G/L Acct Long Text
    cbx/knowledge_data/vendor_company_codes.csv   Vendor, Company Code (vendor numbers as text, leading zeros kept)
    cbx/knowledge_data/cost_centres.csv           Cost Center, Company Code, Currency, Profit Center, Description (sheet Data;
                                                  one row per cost centre and company code; codes as text)
    cbx/knowledge_data/utilities_accounts.csv     1.0.101: the Harvester's utilities list (Data/SAP/vendor_mappings.xlsx), every
                                                  row as it is: VENDOR/BP NUMBER, PID/CC, NAME1, NAME3, ACCOUNT REFERENCE,
                                                  MUNICIPALITY/PAYEE (text, leading zeros kept), then what the Utilities
                                                  Master List (Data/Recon) adds for the same account and vendor (District,
                                                  Responsible Person, Region …: kept beside the row, no new columns)

1.0.103 — Edwin's SAP data of 19 Oct, by name (any of them):

    --supplier-master "Supplier Master - Gen Sec - EFM.xlsx"   cbx/knowledge_data/sap_vendors.csv (gzipped when over 3 MB): Vendor,
                                                  Name (Name + Name 2), VAT Registration No. (digits; or a VAT number in Tax
                                                  Number 1), Tax Number 1, City, Country, Blocked (deletion flag or central
                                                  posting block), Account group — read by the AP upload's own reader
    --cost-centres-add "EFM - Cost Center List - 20261019.xlsx"  added to cost_centres.csv (the newer list wins on a clash)
    --vendor-items "Vendor Line Items.xlsx" --gl-items "GL Line Items.xlsx"
                                                  cbx/knowledge_data/sap_posted.csv (one row per vendor document, payments and
                                                  clearing left out) and sap_posted_lines.csv (their expense lines: posting key
                                                  40/50, not VAT, not a creditors' control account)

The files ship in the release zip; a new install (and an upgrade, for what is not there yet) loads them at start. A
running CBX takes a new export through AP → Settings instead (upload, preview, save). The same readers as those uploads
are used here, so a file the tool takes is a file the page takes.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cbx import knowledge as K  # noqa: E402


OPTIONS = ("--supplier-master", "--cost-centres-add", "--vendor-items", "--gl-items")  # 1.0.103


def main(argv: list[str]) -> int:
    opts: dict[str, Path] = {}
    rest: list[str] = []
    it = iter(argv)
    for a in it:  # 1.0.103: the named files first, the six positional ones as before
        if a in OPTIONS:
            nxt = next(it, None)
            if not nxt:
                print(__doc__)
                return 2
            opts[a] = Path(nxt)
        else:
            rest.append(a)
    argv = rest
    if (not argv and not opts) or len(argv) > 6:
        print(__doc__)
        return 2
    paths = [Path(a) if a and a != "-" else None for a in argv] + [None] * (6 - len(argv))
    K.DATA.mkdir(parents=True, exist_ok=True)
    if paths[0]:
        rows = K.read_doc_type_list(paths[0].read_bytes(), paths[0].name)
        heads = list(rows[0]["sap"])
        n = K.write_csv(K.DOC_TYPES_CSV, heads, ([r["sap"].get(h, "") for h in heads] for r in rows))
        print(f"{K.DOC_TYPES_CSV}: {n} document types, {len(heads)} columns")
    if paths[1]:
        rows = K.read_chart(paths[1].read_bytes(), paths[1].name)
        n = K.write_csv(K.CHART_CSV, ["G/L Account", "G/L Acct Long Text"], ([r["account"], r["name"]] for r in rows))
        print(f"{K.CHART_CSV}: {n} G/L accounts")
    if paths[2]:
        pairs = K.read_vendor_companies(paths[2].read_bytes(), paths[2].name)
        n = K.write_csv(K.VENDOR_CC_CSV, ["Vendor", "Company Code"], sorted(pairs))
        print(f"{K.VENDOR_CC_CSV}: {n} vendor / company code pairs, {len({v for v, _ in pairs})} vendors, {len({c for _, c in pairs})} company codes")
    if paths[3]:
        rows = K.read_cost_centres(paths[3].read_bytes(), paths[3].name)
        n = K.write_csv(K.COST_CENTRES_CSV, ["Cost Center", "Company Code", "Currency", "Profit Center", "Description"],
                        ([r["cost_centre"], r["company_code"], r["currency"], r["profit_centre"], r["description"]] for r in rows))
        print(f"{K.COST_CENTRES_CSV}: {n} cost centres in {len({r['company_code'] for r in rows})} company codes")
    if paths[4]:  # 1.0.101: the utilities list (the AP masters' own reader, as an upload reads it)
        from cbx.ap import masters as M

        heads, rows = M.utility_seed_rows(paths[4].read_bytes(), paths[4].name, paths[5].read_bytes() if paths[5] else None, paths[5].name if paths[5] else "")
        n = K.write_csv(M.UTILITIES_CSV, heads, rows)
        print(f"{M.UTILITIES_CSV}: {n} rows, {len(M.seed_utilities())} with an account number")
    if "--supplier-master" in opts:  # 1.0.103: SAP's vendor general data, read the way AP → Settings reads an upload
        supplier_master(opts["--supplier-master"])
    if "--cost-centres-add" in opts:
        cost_centres_add(opts["--cost-centres-add"])
    if "--vendor-items" in opts or "--gl-items" in opts:
        posting_history(opts.get("--vendor-items"), opts.get("--gl-items"))
    return 0


def supplier_master(path: Path) -> None:
    """1.0.103: Edwin's Supplier Master (LFA1, 200+ columns, English labels) → sap_vendors.csv, only the columns CBX uses."""
    from cbx.ap import masters as M

    rows = M.read_sap_vendors(path.read_bytes(), path.name)
    print(f"{path.name}: columns used {rows.mapping}")
    out: dict[str, dict] = {}
    for rec in rows:
        r = M._sap_vendor_row(rec)
        if r is not None:
            out[r["vendor"]] = r
    body = [[r["vendor"], r["name"], r["vat"], r["tax1"], r["city"], r["country"], "X" if r["blocked"] else "", r["account_group"] or ""] for _v, r in sorted(out.items())]
    for p in (K.SAP_VENDORS_CSV, K.SAP_VENDORS_CSV.with_name(K.SAP_VENDORS_CSV.name + ".gz")):
        if p.is_file():
            p.unlink()
    n = K.write_csv(K.SAP_VENDORS_CSV, K.SAP_VENDOR_HEADS, body)
    if K.SAP_VENDORS_CSV.stat().st_size > 3 * 1024 * 1024:  # big: gzipped (read back with gzip in text mode, utf-8)
        K.SAP_VENDORS_CSV.unlink()
        K.write_csv(K.SAP_VENDORS_CSV.with_name(K.SAP_VENDORS_CSV.name + ".gz"), K.SAP_VENDOR_HEADS, body)
    p = K.sap_vendors_seed_path()
    print(f"{p}: {n} vendors, {sum(1 for r in out.values() if len(r['vat']) == 10)} with a VAT number, {sum(1 for r in out.values() if r['blocked'])} blocked, "
          f"{p.stat().st_size // 1024} KB")


def cost_centres_add(path: Path) -> None:
    """1.0.103: a newer Cost Center List added to the shipped one: the union, the newer list's row wins on a clash."""
    new = K.read_cost_centres(path.read_bytes(), path.name)
    rows = {(r["cost_centre"], r["company_code"]): r for r in K.seed_cost_centres()}
    before = len(rows)
    clash = sum(1 for r in new if (r["cost_centre"], r["company_code"]) in rows)
    for r in new:
        rows[(r["cost_centre"], r["company_code"])] = r
    n = K.write_csv(K.COST_CENTRES_CSV, ["Cost Center", "Company Code", "Currency", "Profit Center", "Description"],
                    ([r["cost_centre"], r["company_code"], r["currency"], r["profit_centre"], r["description"]] for r in rows.values()))  # the shipped order kept, new rows last
    print(f"{K.COST_CENTRES_CSV}: {n} cost centres ({before} before; {len(new)} in {path.name}: {len(new) - clash} new, {clash} replaced)")


def posting_history(vendor_items, gl_items) -> None:
    """1.0.103: SAP's vendor line items (FBL1N) and G/L line items (FBL3N) → the posting history seed files."""
    from cbx.ap import history as H

    chart = {K.gl_key(r["account"]): r["name"] for r in K.seed_chart()}
    v = H.read_items("vendor", vendor_items.read_bytes(), vendor_items.name) if vendor_items else []
    g = H.read_items("gl", gl_items.read_bytes(), gl_items.name) if gl_items else []
    n, m = H.write_seed(v, g, lambda a: chart.get(K.gl_key(a), ""))
    print(f"{H.POSTED_CSV}: {n} vendor documents from {len(v)} vendor lines; {H.LINES_CSV}: {m} expense lines from {len(g)} G/L lines")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
