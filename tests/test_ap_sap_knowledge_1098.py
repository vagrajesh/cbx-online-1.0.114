"""1.0.98: from CBX-Connect 2.10.3 tests/test_sap_knowledge_2103.py, on the old conftest.

2.10.3: what CBX knows about SAP (cbx/knowledge.py) — SAP's Document Type List merged into Edwin's table, the chart
of accounts, the vendors per company code — and AP → Settings, where admins replace them (preview, then save)."""
from __future__ import annotations

import csv
import io

import pytest

from cbx import knowledge as K
from cbx.ap import masters as M
from cbx.ap import sap as P
from cbx.db import SessionLocal
from cbx.models.ap import ApCostCentre, ApDocType, ApGlAccount, ApVendorCompany

from .fixtures.make_ap import TEST_GLS as _TEST_GLS
from .test_ap_capture_1098 import _admin, _app, _tables, signed_in  # noqa: F401


@pytest.fixture(autouse=True)
def _no_connections_left():
    """1.0.98: the suite shares one database: the SAP connections these tests make are removed after each test."""
    yield
    from cbx.models.connections import ApiConnection as _Conn

    with SessionLocal() as s:
        s.query(_Conn).filter(_Conn.slug.like("know-sap-%")).delete(synchronize_session=False)
        s.commit()


def _csv(rows: list[list]) -> bytes:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    return buf.getvalue().encode("utf-8")


def _seed_csv(path) -> list[list[str]]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.reader(fh))


def test_the_seed_loads_sap_s_lists_and_keeps_edwin_s_rows():
    with SessionLocal() as s:
        assert s.query(ApDocType).count() == 75  # Edwin's 11 rows + SAP's other 64 types
        # Chart of Accounts - CBX.xlsx has 5 130 rows; SD025 "SG&A" is in it twice, so 5 129 accounts
        assert s.query(ApGlAccount).filter(ApGlAccount.source == "seed").count() == 5129 and len(K.seed_chart()) == 5129
        assert s.query(ApVendorCompany).count() >= 117855 - 5 and len(K.seed_vendor_companies()) == 117855
        # Cost Center List - 20261016.xlsx: 24 483 rows, 24 410 (cost centre, company code) pairs in 1 803 company codes
        assert len(K.seed_cost_centres()) == 24410 and s.query(ApCostCentre).filter(ApCostCentre.description != "Test cost centre CC1").count() >= 24410
        rows = {d.code: d for d in s.query(ApDocType).all()}
        kr, dr, yr, yg, ue = rows["KR"], rows["DR"], rows["YR"], rows["YG"], rows["UE"]
        # Edwin's label, meaning and note stay; SAP's description, account types, number range and reverse type are beside them
        assert (kr.label, kr.meaning, kr.description, kr.account_types, kr.number_range, kr.reverse, kr.needs_check) == ("Invoice", "invoice", "Vendor Invoice", "AKMS", "19", "KG", False)
        assert (ue.meaning, ue.description, ue.needs_check) == ("journal", "Data Transfer", False)
        assert len(kr.sap) == 39 and kr.sap["Supplier"] == "X"
        # where they disagree: Check, with both sides
        assert dr.needs_check and dr.label == "Clearing items" and "SAP: Customer Invoice" in dr.check_note and "CBX: Clearing items" in dr.check_note
        assert yr.needs_check and yr.meaning == "invoice" and yr.check_note.startswith("SAP: RE Customer Invoice (account types D, S)")
        assert yg.needs_check and "RE Cust Cred Memo" in yg.check_note
        assert {d.code for d in rows.values() if d.needs_check} == {"DR", "YR", "YG"}
        new = rows["RE"]
        assert (new.source, new.meaning, new.label, new.account_types) == ("sap", "other", "Invoice - Gross", "AKMS")


def test_lookups():
    with SessionLocal() as s:
        K.forget()
        kr = K.doc_type(s, "kr")
        assert kr["code"] == "KR" and kr["kind"] == "invoice" and kr["allows"] == {"asset", "vendor", "material", "gl"} and kr["reverse"] == "KG" and kr["number_range"] == "19"
        assert K.doc_type(s, "YR")["allows"] == {"customer", "gl"} and K.doc_type(s, "ZR")["allows"] == set() and K.doc_type(s, "Q9") is None
        assert K.doc_type(s, "RE")["kind"] == "other"
        assert K.gl_account(s, " ja600 ") == {"account": "JA600", "name": "Input VAT - Expense"} == K.gl_account(s, "00JA600")
        assert K.gl_account(s, "hb000zbeb5")["name"] == "Intercompany Loan - System journals" and K.gl_account(s, "6123") is None
        assert any(x["account"] == "SB006" for x in K.gl_search(s, "cleaning service")) and K.gl_search(s, "JA60")[0]["account"].startswith("JA60")
        assert K.company(s, "x143")["name"] == "CBRE Excellerate Faciliti" and K.company(s, "CBRE Excellerate Faciliti - DELL")["code"] == "X143"
        assert K.company(s, "EPS Rwanda Limited")["code"] == "X120" and K.company(s, "Nobody (Pty) Ltd") is None
        assert K.vendor_in_company(s, "120083", "x120") is True and K.vendor_in_company(s, "0000120083", "X120") is True
        assert K.vendor_in_company(s, "120000", "X120") is False and K.vendor_in_company(s, "120000", "X143") is None  # X143 is not in SAP's export
        assert "X120" in K.companies_of_vendor(s, "120083") and K.companies_of_vendor(s, "nobody") == []
        cc = K.cost_centre(s, "2822")  # no company code: the first match
        assert cc == {"cost_centre": "2822", "company_code": "AAK0", "currency": "ZAR", "profit_centre": "2822", "description": "Castle Ridge Orange Farm"}
        assert K.cost_centre(s, "ac0171", "X120")["description"] == "Rwanda Exec" and K.cost_centre(s, "2822", "X120") is None
        assert K.cost_centre_in_company(s, "2822", "X120") == "no" and K.cost_centre_in_company(s, "AC0171", "X120") == "yes"
        assert K.cost_centre_in_company(s, "SAP-DUMMY", "X120") == "dummy" and K.cost_centre_in_company(s, "2822", "T000") is None  # T000: not in SAP's list
        assert K.company_currency(s, "X120") == "RWF" and K.company_currency(s, "X143") == "ZAR" and K.company_currency(s, "NOPE") is None
    rx = K.sap_words()
    assert rx.search("I was in MIRO when it said: Message no. F5 702") and rx.search("Balance in transaction currency") and rx.search("/nfb03")
    assert rx.search("Document 5100012345 was posted in company code X120") and rx.search("SAP Easy Access") and rx.search("ran F-53 and FBL1N")
    assert not rx.search("Please send my vendor number (BP number 120000) for the supplier portal") and not rx.search("check my inbox")
    assert K.tcodes_in("MIRO then fb60, /nFK03 and transaction BP") == ["MIRO", "FB60", "FK03", "BP"]
    assert K.split_bill_to("CBRE Excellerate Facilities Management (Pty) Ltd - RIO2X21") == ("CBRE Excellerate Facilities Management (Pty) Ltd", "RIO2X21")


def test_tess_uses_the_chart_the_doc_types_and_the_vendor_list():
    rows, _, _ = P.manual_doc({"supplier": "Acme", "invoice_no": "K-1", "invoice_date": "2026-10-01", "due_date": "2026-10-31", "excl": "100.00", "vat": "15.00", "incl": "115.00"},
                              {"vendor_no": "120000", "gl": "SB006", "cost_centre": "CCZ9"}, "X120", _tables(), file="k.pdf")
    with SessionLocal() as s:
        K.forget()
        known = {"vendors": {"120000"}, "gls": {"SB006", "JA600", "6123"}, "companies": {"X120"}, "doc_types": {"KR", "KG", "YR"}, "tax_codes": {"I9", "I0"},
                 "chart": K.chart_keys(s), "doc_type_accounts": {c: d["account_types"] for c, d in K.doc_types_all(s).items()},
                 "vendor_in_company": lambda v, cc: K.vendor_in_company(s, v, cc)}
        st, why = P.test_doc(rows, known)
        assert st == "red" and [x["text"] for x in why] == ["Vendor 120000 is not opened in company code X120 in SAP"]
        ok = [{**r, "account": "120083"} if r["pkey"] == "31" else r for r in rows]
        assert P.test_doc(ok, {**known, "vendors": {"120083"}}) == ("green", [])
        st, why = P.test_doc([{**r, "account": "6123"} if r["account"] == "SB006" else r for r in ok], {**known, "vendors": {"120083"}})
        assert st == "yellow" and why == [{"level": "yellow", "text": "G/L 6123 is not in the chart of accounts — check with finance"}]
        kc = {**known, "vendors": {"120083"}, "cost_centre_in_company": lambda cc, co: K.cost_centre_in_company(s, cc, co)}
        assert P.test_doc(ok, kc) == ("red", [{"level": "red", "text": "Cost centre CCZ9 doesn't exist in company code X120 in SAP"}])
        on = [{**r, "costcentre": "AC0171"} if r["pkey"] == "40" else r for r in ok]
        assert P.test_doc(on, kc) == ("green", [])
        on = [{**r, "costcentre": "SAP-DUMMY"} if r["pkey"] == "40" else r for r in ok]
        assert P.test_doc(on, kc) == ("red", [{"level": "red", "text": "Cost centre SAP-DUMMY is a dummy cost centre"}])
        on = [{**r, "comp": "T000"} for r in ok]  # a company code SAP's Cost Center List doesn't have at all: not checked
        assert "Cost centre" not in " ".join(x["text"] for x in P.test_doc(on, {**kc, "companies": {"T000"}})[1])
        st, why = P.test_doc([{**r, "type": "YR"} for r in ok], {**known, "vendors": {"120083"}})
        assert st == "red" and {x["text"] for x in why} == {"YR doesn't allow vendor lines in SAP (account types D, S)"}
        # without SAP's lists (a 2.10.0 caller), nothing of this is tested
        plain = {k: v for k, v in known.items() if k not in ("chart", "doc_type_accounts", "vendor_in_company")}
        assert P.test_doc([{**r, "type": "YR"} for r in rows], plain)[0] == "green"


def test_coding_allows_a_gl_the_chart_does_not_have_with_a_warning_and_names_it():
    adm = _admin()
    r = adm.post("/api/ap/masters/vendors", json={"vendor_no": "9920001", "name": "Chart Check Co", "gl": "6123", "cost_centre": "CC1", "tax_code": "I9"})
    assert r.status_code == 200 and r.json()["gl"] == "6123" and r.json()["warning"] == "G/L 6123 is not in the chart of accounts CBX has."
    assert "6123" in adm.get("/api/ap/masters").json()["gl_missing"]  # Settings → G/L accounts lists it
    adm.delete(f"/api/ap/masters/vendors/{r.json()['id']}")
    r = adm.post("/api/ap/masters/vendors", json={"vendor_no": "9920001", "name": "Chart Check Co", "gl": "sb006", "cost_centre": "CC1", "tax_code": "I9", "item_text": "{site} {description}"})
    assert r.status_code == 200 and r.json()["gl"] == "SB006" and r.json()["item_text"] == "{site} {description}" and "warning" not in r.json()  # as the chart spells it
    adm.delete(f"/api/ap/masters/vendors/{r.json()['id']}")
    rows = adm.get("/api/ap/gl-search", params={"q": "input vat"}).json()["rows"]
    assert {"account": "JA600", "name": "Input VAT - Expense"} in rows
    m = adm.get("/api/ap/masters").json()
    assert m["counts"]["gl_accounts"] >= 5129 and m["counts"]["vendor_companies_codes"] == 1133 and m["counts"]["doc_types_check"] >= 1
    yr = next(d for d in m["doc_types"] if d["code"] == "YR")
    assert yr["vendor_lines"] is False and next(d for d in m["doc_types"] if d["code"] == "KR")["vendor_lines"] is True
    assert m["doc_types"][0]["code"] == "KR"  # Edwin's rows first
    assert m["counts"]["cost_centres_codes"] >= 1803 and {"SA110", "SA210", "SA010", "SA311", "SA312", "SA412"} <= set(m["gl_missing"]) | set(_TEST_GLS)


def test_rights_viewers_read_admins_change():
    viewer = signed_in("know.viewer@test.local", "viewer", name="Vic Viewer", tools=["ap"])
    user = signed_in("know.user@test.local", "user", name="Una User", tools=["ap"])
    assert viewer.get("/api/ap/masters/gl_accounts?q=JA600").json()["rows"][0]["account"] == "JA600"
    assert viewer.get("/api/ap/masters/vendor_companies?q=120083").json()["rows"][0]["companies"]
    assert viewer.get("/api/ap/gl-search?q=JA6").status_code == 200
    assert viewer.get("/api/ap/masters/cost_centres?q=AC0171").json()["rows"][0]["description"] == "Rwanda Exec"
    with SessionLocal() as s:
        dr = s.query(ApDocType).filter(ApDocType.code == "DR").one().id
    for c in (viewer, user):
        assert c.post("/api/ap/masters/gl_accounts/import", files={"file": ("c.csv", _csv([["G/L Account"], ["X1"]]))}, data={"confirm": "true"}).status_code == 403
        assert c.post("/api/ap/masters/vendor_companies/import", files={"file": ("v.csv", _csv([["Vendor", "Company Code"], ["1", "X1"]]))}, data={"confirm": "true"}).status_code == 403
        assert c.post("/api/ap/doc-types/import", files={"file": ("d.csv", b"x")}, data={"confirm": "false"}).status_code == 403
        assert c.post(f"/api/ap/doc-types/{dr}/confirm").status_code == 403
        assert c.post("/api/ap/masters/vendor_companies/pull", json={"connection_id": 1, "path": "/x", "columns": {}}).status_code == 403
        assert c.post("/api/ap/masters/cost_centres/import", files={"file": ("c.csv", _csv([["Cost Center", "Company Code"], ["1", "X1"]]))}, data={"confirm": "true"}).status_code == 403
    nobody = signed_in("know.none@test.local", "user", name="No Tool")
    assert nobody.get("/api/ap/gl-search?q=JA6").status_code == 403


def test_document_type_list_upload_preview_then_save_and_check_confirmed():
    adm = _admin()
    seed = _seed_csv(K.DOC_TYPES_CSV)
    head, body = seed[0], seed[1:]
    ic, idesc = head.index("Document Type"), head.index("Description")
    # an admin confirms DR's Check: CBX's meaning stands
    with SessionLocal() as s:
        dr = s.query(ApDocType).filter(ApDocType.code == "DR").one().id
    r = adm.post(f"/api/ap/doc-types/{dr}/confirm")
    assert r.status_code == 200 and r.json()["needs_check"] is False
    # SAP's next export: one description changed, one new type, one SAP-only type gone
    new = [list(r_) for r_ in body if r_[ic] != "YZ"]
    for r_ in new:
        if r_[ic] == "ZT":
            r_[idesc] = "Payroll Journal"
    new.append(["ZW"] + [""] * (len(head) - 2) + ["Test Write-off"])
    new[-1][head.index("Account Types")] = "KS"
    up = lambda confirm: adm.post("/api/ap/doc-types/import", files={"file": ("Document Type List.csv", _csv([head] + new))}, data={"confirm": "true" if confirm else "false"})  # noqa: E731
    pre = up(False).json()
    assert (pre["added"], pre["changed"], pre["removed"], pre["saved"]) == (1, 1, 1, False)
    assert pre["changes"][0]["fields"] == [{"field": "description", "from": "Payroll", "to": "Payroll Journal"}] and pre["sample"]["removed"] == ["YZ RE Bnk Chrgs Recover"]
    with SessionLocal() as s:
        assert s.query(ApDocType).filter(ApDocType.code == "ZW").count() == 0  # nothing saved by a preview
    try:
        assert up(True).json()["saved"]
        with SessionLocal() as s:
            rows = {d.code: d for d in s.query(ApDocType).all()}
            assert "YZ" not in rows and rows["ZW"].account_types == "KS" and rows["ZW"].meaning == "other" and rows["ZT"].description == "Payroll Journal"
            assert rows["DR"].needs_check is False and rows["DR"].checked_by == "Ayanda Admin"  # SAP's side of DR did not change: still confirmed
            assert rows["YR"].needs_check and rows["KR"].label == "Invoice"
        K.forget()
        with SessionLocal() as s:
            assert K.doc_type(s, "ZW")["allows"] == {"vendor", "gl"}
        # a file without the list's columns is refused plainly
        r = adm.post("/api/ap/doc-types/import", files={"file": ("x.csv", _csv([["Code", "Text"], ["KR", "x"]]))}, data={"confirm": "false"})
        assert r.status_code == 400 and "Document Type, Account Types and Description" in r.json()["detail"]
    finally:
        assert adm.post("/api/ap/doc-types/import", files={"file": ("Document Type List.csv", _csv(seed))}, data={"confirm": "true"}).json()["saved"]
        with SessionLocal() as s:
            assert s.query(ApDocType).count() == 75


def test_chart_upload_preview_then_save():
    adm = _admin()
    seed = _seed_csv(K.CHART_CSV)
    new = [seed[0]] + [r for r in seed[1:] if r[0] != "SC605"] + [["ZZ9001", "Test New Account"]]
    new = [[a, "Input VAT - Expenses (renamed)"] if a == "JA600" else [a, n] for a, n in new]
    up = lambda rows, confirm: adm.post("/api/ap/masters/gl_accounts/import", files={"file": ("Chart.csv", _csv(rows))}, data={"confirm": "true" if confirm else "false"}).json()  # noqa: E731
    with SessionLocal() as s:
        before = s.query(ApGlAccount).count()
    pre = up(new, False)
    assert pre["saved"] is False and pre["added"] == 1 and pre["changed"] == 1 and pre["removed"] == before - 5129 + 1  # the tests' own G/Ls and SC605
    assert pre["changes"] == [{"row": "JA600", "fields": [{"field": "name", "from": "Input VAT - Expense", "to": "Input VAT - Expenses (renamed)"}]}]
    assert "JA600" not in pre["removed_in_use"]
    try:
        assert up(new, True)["saved"]
        with SessionLocal() as s:
            assert K.gl_account(s, "zz9001")["name"] == "Test New Account" and K.gl_account(s, "SC605") is None
            assert K.gl_account(s, "JA600")["name"] == "Input VAT - Expenses (renamed)"
    finally:
        assert up(seed, True)["saved"]
        with SessionLocal() as s:
            assert K.gl_account(s, "SC605") is not None and K.gl_account(s, "ZZ9001") is None


def test_vendors_per_company_code_upload_preview_then_save():
    adm = _admin()
    with K.VENDOR_CC_CSV.open("rb") as fh:
        seed = fh.read()
    lines = seed.decode("utf-8").splitlines()
    assert lines[0] == "Vendor,Company Code"
    new = "\n".join([lines[0], *[ln for ln in lines[1:] if ln != "120083,X120"], "0099999,X120", "0099999,X143"]).encode("utf-8")
    pre = adm.post("/api/ap/masters/vendor_companies/import", files={"file": ("Vendor Master.csv", new)}, data={"confirm": "false"}).json()
    assert (pre["added"], pre["removed"], pre["saved"]) == (2, 1, False) and pre["sample"]["removed"] == ["120083 · X120"]
    with SessionLocal() as s:
        K.forget()
        assert K.vendor_in_company(s, "120083", "X120") is True  # nothing saved by a preview
    try:
        r = adm.post("/api/ap/masters/vendor_companies/import", files={"file": ("Vendor Master.csv", new)}, data={"confirm": "true"}).json()
        assert r["saved"] and r["companies"] == 1134
        with SessionLocal() as s:
            assert K.vendor_in_company(s, "120083", "X120") is False and K.vendor_in_company(s, "99999", "X143") is True  # X143 is known now
            assert K.companies_of_vendor(s, "0099999") == ["X120", "X143"]
        rows = adm.get("/api/ap/masters/vendor_companies?q=0099999").json()["rows"]
        assert rows == [{"id": 1, "vendor": "0099999", "companies": ["X120", "X143"]}]
    finally:
        assert adm.post("/api/ap/masters/vendor_companies/import", files={"file": ("Vendor Master.csv", seed)}, data={"confirm": "true"}).json()["saved"]
        with SessionLocal() as s:
            assert s.query(ApVendorCompany).count() == 117855 and K.vendor_in_company(s, "120083", "X120") is True


def test_the_seed_tool_rebuilds_the_files(tmp_path, monkeypatch):
    """tools/make_knowledge_data.py: SAP's three exports → the three seed files, with the readers the uploads use."""
    import importlib.util
    from pathlib import Path

    from openpyxl import Workbook

    def xlsx(rows, title="Sheet1"):
        wb = Workbook()
        ws = wb.active
        ws.title = title
        for r in rows:
            ws.append(r)
        p = tmp_path / f"{title}_{len(list(tmp_path.iterdir()))}.xlsx"
        wb.save(p)
        wb.close()
        return p

    seed = _seed_csv(K.DOC_TYPES_CSV)
    docs = xlsx([seed[0]] + seed[1:4], "Data")
    chart = xlsx([["G/L Account", "G/L Acct Long Text"], ["AA000", "Fixed Capital"], ["AA000", "Fixed Capital"], ["JA600", "Input VAT - Expense"]])
    vend = xlsx([["Vendor", "Company Code"], ["0012", "X100"], [120000, "X120"], ["0012", "X100"]])
    ccs = xlsx([["Cost Center", "Company Code", "Currency", "Profit Center", "Description"], ["0042", "X100", "MUR", "0042", ""], ["0042", "X100", "MUR", "0042", "Port Louis"],
                [2822, "AAK0", "ZAR", 2822, "Castle Ridge"]], "Data")
    for name in ("DOC_TYPES_CSV", "CHART_CSV", "VENDOR_CC_CSV", "COST_CENTRES_CSV"):
        monkeypatch.setattr(K, name, tmp_path / Path(getattr(K, name)).name)
    monkeypatch.setattr(K, "DATA", tmp_path)
    spec = importlib.util.spec_from_file_location("make_knowledge_data", Path(__file__).resolve().parents[1] / "tools" / "make_knowledge_data.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    assert tool.main([str(docs), str(chart), str(vend)]) == 0
    assert len(_seed_csv(K.DOC_TYPES_CSV)) == 4 and len(_seed_csv(K.DOC_TYPES_CSV)[0]) == 39
    assert _seed_csv(K.CHART_CSV) == [["G/L Account", "G/L Acct Long Text"], ["AA000", "Fixed Capital"], ["JA600", "Input VAT - Expense"]]
    assert _seed_csv(K.VENDOR_CC_CSV) == [["Vendor", "Company Code"], ["0012", "X100"], ["120000", "X120"]]  # text, leading zeros kept
    assert tool.main(["-", "-", str(vend)]) == 0  # one file at a time
    assert tool.main(["-", "-", "-", str(ccs)]) == 0
    assert _seed_csv(K.COST_CENTRES_CSV) == [["Cost Center", "Company Code", "Currency", "Profit Center", "Description"], ["0042", "X100", "MUR", "0042", "Port Louis"],
                                             ["2822", "AAK0", "ZAR", "2822", "Castle Ridge"]]  # one row per pair: a later row fills the blanks


def test_pull_from_sap_previews_then_saves_the_chart(monkeypatch):
    """Pull from SAP: the same connection pattern as the other lists (preview, then exactly those rows are saved)."""
    import json
    import threading
    import uuid
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from cbx.config import settings
    from cbx.models.connections import ApiConnection

    seed = _seed_csv(K.CHART_CSV)[1:]

    class Sap(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            rows = [{"GLAccount": a, "GLAccountLongName": n} for a, n in seed] + [{"GLAccount": "ZZ9002", "GLAccountLongName": "Pulled Account"}]
            if self.path.startswith("/pairs"):
                rows = [{"Supplier": "120083", "CompanyCode": "X120"}]
            b = json.dumps({"d": {"results": rows}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

    monkeypatch.setattr(settings, "api_allow_private", True)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Sap)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    adm = _admin()
    try:
        with SessionLocal() as s:
            c = ApiConnection(slug="know-sap-" + uuid.uuid4().hex[:6], name="SAP chart", base_url=f"http://127.0.0.1:{srv.server_address[1]}", auth_kind="none", tools=["ap"], is_active=True)
            s.add(c)
            s.commit()
            cid = c.id
        cols = {"account": "GLAccount", "name": "GLAccountLongName"}
        pre = adm.post("/api/ap/masters/gl_accounts/pull", json={"connection_id": cid, "path": "/chart", "columns": cols}).json()
        assert pre["added"] == 1 and pre["saved"] is False and pre["preview_id"]
        done = adm.post("/api/ap/masters/gl_accounts/pull", json={"confirm": True, "preview_id": pre["preview_id"]}).json()
        assert done["saved"]
        with SessionLocal() as s:
            assert K.gl_account(s, "ZZ9002")["name"] == "Pulled Account" and s.query(ApGlAccount).filter(ApGlAccount.account == "ZZ9002").one().source == "sap"
        # the vendor list's columns are needed both; a one-pair answer would remove nearly all: the preview says so
        r = adm.post("/api/ap/masters/vendor_companies/pull", json={"connection_id": cid, "path": "/pairs", "columns": {"vendor": "Supplier"}})
        assert r.status_code == 400
        pre = adm.post("/api/ap/masters/vendor_companies/pull", json={"connection_id": cid, "path": "/pairs", "columns": {"vendor": "Supplier", "company_code": "CompanyCode"}}).json()
        assert pre["rows"] == 1 and pre["removed"] == 117854 and not pre["saved"]
    finally:
        srv.shutdown()
        rows = [["G/L Account", "G/L Acct Long Text"]] + seed
        adm.post("/api/ap/masters/gl_accounts/import", files={"file": ("Chart.csv", _csv(rows))}, data={"confirm": "true"})


def test_cost_centre_list_upload_preview_then_save():
    adm = _admin()
    seed = _seed_csv(K.COST_CENTRES_CSV)
    head, body = seed[0], seed[1:]
    new = [r for r in body if not (r[0] == "AC0172" and r[1] == "X120")]
    new = [[*r[:4], "Rwanda Executive"] if (r[0], r[1]) == ("AC0171", "X120") else r for r in new] + [["AC9999", "X120", "RWF", "AC9999", "Rwanda New"]]
    up = lambda rows, confirm: adm.post("/api/ap/masters/cost_centres/import", files={"file": ("Cost Center List.csv", _csv([head] + rows))},  # noqa: E731
                                        data={"confirm": "true" if confirm else "false"}).json()
    with SessionLocal() as s:
        extra = s.query(ApCostCentre).count() - 24410  # the made-up ones other tests add
    pre = up(new, False)
    assert (pre["added"], pre["changed"], pre["removed"], pre["saved"]) == (1, 1, 1 + extra, False)
    assert pre["changes"] == [{"row": "AC0171 · X120", "fields": [{"field": "description", "from": "Rwanda Exec", "to": "Rwanda Executive"}]}]
    try:
        assert up(new, True)["saved"]
        with SessionLocal() as s:
            assert K.cost_centre(s, "AC0171", "X120")["description"] == "Rwanda Executive" and K.cost_centre(s, "AC0172", "X120") is None
            assert K.cost_centre(s, "ac9999")["company_code"] == "X120"
    finally:
        assert up(body, True)["saved"]
        with SessionLocal() as s:
            assert s.query(ApCostCentre).count() == 24410 and K.cost_centre(s, "AC0172", "X120") is not None


def test_the_support_sap_block_names_the_cost_centre():
    from cbx.support import sapblock as SB

    with SessionLocal() as s:
        K.forget()
        f = SB.check(s, {"tcode": "FB60", "company_code": "X120", "cost_centre": "AC0171"})
        assert ("Cost centre", "AC0171 Rwanda Exec") in SB.rows(f)
        f = SB.check(s, {"company_code": "X120", "cost_centre": "2822"})  # SAP has no 2822 in X120
        assert ("Cost centre", "2822 (not in CBX's SAP lists)") in SB.rows(f)
        f = SB.check(s, {"cost_centre": "2822"})  # no company code: the first match
        assert ("Cost centre", "2822 Castle Ridge Orange Farm") in SB.rows(f) and "Cost centre 2822 Castle Ridge Orange Farm" in SB.line(f)
        f = SB.check(s, {"company_code": "T000", "cost_centre": "2822"})  # a company code SAP's list doesn't cover: any company's match
        assert ("Cost centre", "2822 Castle Ridge Orange Farm") in SB.rows(f)
