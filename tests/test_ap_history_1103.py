"""1.0.103 (CBX-Connect 2.10.8 in the old CBX; Edwin, 19 Oct): the new SAP data and the misread invoice — the Supplier Master and the EFM cost centres shipped
in the code, SAP's posting history (vendor line items FBL1N + G/L line items FBL3N: the duplicate check and coding learned
from SAP), Mia's vendor pick when one VAT number has many SAP vendors, and the AP engine reading an invoice by its layout
(Edwin's 467332: supplier "Date:", the total ex. VAT taken as the VAT). Vendors, references and amounts of the made-up
cases are made up; the seed checks read the files shipped in cbx/knowledge_data. The AI is off in tests (the capture call
returns what a vision model would read)."""
from __future__ import annotations

import io
import uuid

import pymupdf
import pytest
from sqlalchemy import func
from openpyxl import Workbook

from cbx import knowledge as K
from cbx.ap import capture as C
from cbx.ap import engine as E
from cbx.ap import history as H
from cbx.ap import masters as M
from cbx.db import SessionLocal
from cbx.models import AppState
from cbx.models.ap import ApCapture, ApCostCentre, ApEntity, ApItem, ApPosted, ApSapVendor, ApVendorCode, ApVendorCompany, ApVendorHistory

from .fixtures import make_ap
from .test_ap_1102 import FULL, _answer, _cap, _code, _hist, _run, _vat
from .test_ap_capture_1098 import _admin, _app, _drop, _sheet, _user  # noqa: F401 — _app: the app started, the AP lists seeded

MINE: dict[str, set] = {"posted": set(), "sapv": set(), "vc": set(), "codes": set()}


@pytest.fixture(autouse=True)
def _setup(_app):
    """X143's full name taught (a sure bill-to match); the made-up G/Ls and cost centres in SAP's lists. Afterwards the
    made-up SAP vendors, postings and vendor ↔ company rows go, so other tests see SAP's lists as they were."""
    make_ap.chart("SB205", "SB206", "SB207")
    with SessionLocal() as s:
        e = s.query(ApEntity).filter(ApEntity.code == "X143").one()
        if FULL not in (e.aliases or ""):
            e.aliases = (e.aliases + "\n" if e.aliases else "") + FULL
        for cc in ("AC0310", "AC0311"):
            if not s.query(ApCostCentre.id).filter(ApCostCentre.cost_centre == cc, ApCostCentre.company_code == "X143").first():
                s.add(ApCostCentre(cost_centre=cc, company_code="X143", currency="ZAR", profit_centre=cc, description=f"Test {cc}"))
        s.commit()
        first = (s.query(func.max(ApCapture.id)).scalar() or 0) + 1
    K.forget()
    yield
    with SessionLocal() as s:
        # nothing this test made is left in the readers' queues (a later test's capture job would read it too)
        s.query(ApCapture).filter(ApCapture.id >= first, ApCapture.stage.in_(C.READING)).update(
            {"bucket": "skipped", "stage": "skipped", "needs": "", "claimed_at": None}, synchronize_session=False)
        pairs = set()
        for co, doc in MINE["posted"]:
            r = s.query(ApPosted).filter(ApPosted.company_code == co, ApPosted.doc_number == doc).first()
            if r is not None:
                pairs.add((co, r.vendor))
                s.delete(r)
        s.flush()
        H.rebuild(s, pairs)
        s.query(ApSapVendor).filter(ApSapVendor.vendor.in_(sorted(MINE["sapv"]) or ["-"])).delete(synchronize_session=False)
        for v, co in MINE["vc"]:
            s.query(ApVendorCompany).filter(ApVendorCompany.vendor == v, ApVendorCompany.company_code == co).delete(synchronize_session=False)
        s.query(ApVendorCode).filter(ApVendorCode.vendor_no.in_(sorted(MINE["codes"]) or ["-"])).delete(synchronize_session=False)
        s.commit()
    for k in MINE:
        MINE[k].clear()
    K.forget()


def _no(prefix: str = "77") -> str:
    return prefix + str(uuid.uuid4().int)[:6]


def _sapv(vendor: str, name: str, vat: str, *, blocked: bool = False, companies=("X143",)) -> str:
    with SessionLocal() as s:
        s.query(ApSapVendor).filter(ApSapVendor.vendor == vendor).delete()
        s.add(ApSapVendor(vendor=vendor, name=name, name_key=M.name_key(name), vat=vat, tax1="", city="Sandton", country="ZA", blocked=blocked, payment_terms="",
                          source="excel", account_group="ZMGN"))
        for co in companies:
            if not s.query(ApVendorCompany.id).filter(ApVendorCompany.vendor == vendor, ApVendorCompany.company_code == co).first():
                s.add(ApVendorCompany(vendor=vendor, company_code=co))
                MINE["vc"].add((vendor, co))
        s.commit()
    MINE["sapv"].add(vendor)
    K.forget()
    return vendor


def _post(vendor: str, ref: str, *, company: str = "X143", amount: float = 1150.0, day: str = "2026-08-17", lines=None, doc: str = "", dtype: str = "KR") -> str:
    """A document SAP posted (as an upload of SAP's line items leaves it), and the vendor's history learned again."""
    doc = doc or ("98" + str(uuid.uuid4().int)[:7])
    with SessionLocal() as s:
        s.add(ApPosted(company_code=company, vendor=vendor, reference=ref, ref_key=H.ref_key(ref), doc_type=dtype, doc_number=doc, posting_date=day, doc_date=day,
                       amount=amount, currency="ZAR", tax_code="I9", terms="ZI00", text=f"Test {ref}", vendor_lines=1,
                       lines=lines if lines is not None else [{"gl": "SB205", "pc": "AC0310", "amount": round(amount / 1.15, 2), "text": "Lifts"}], source="excel"))
        s.flush()
        H.rebuild(s, {(company, vendor)})
        s.commit()
    MINE["posted"].add((company, doc))
    return doc


def _xlsx(rows: list) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---- 1, 2: the seeds ----------------------------------------------------------------------------------------------------------
def test_the_supplier_master_and_the_efm_cost_centres_are_in_the_code_and_load_into_empty_tables():
    rows = {r["vendor"]: r for r in K.seed_sap_vendors()}
    assert len(rows) > 30000
    assert rows["120001"]["name"] == "ADAMS & ADAMS ATTORNEYS" and rows["120001"]["vat"] == "4320111463" and rows["120001"]["blocked"] is False
    assert rows["126606"]["blocked"] is True and rows["126606"]["account_group"] == "ZMGN"  # central posting block
    assert rows["123807"]["name"] == "EXCELLERATE FACILITY MANAGEMENT PTY LTD"  # Name + Name 2
    ccs = {(r["cost_centre"], r["company_code"]): r for r in K.seed_cost_centres()}
    assert ccs[("XA0001", "XA00")]["profit_centre"] == "XA0001" and ccs[("AC0310", "X143")]["description"] == "SA - Exec"
    with SessionLocal() as s:
        # an empty vendor list takes the shipped one, bulk; Mia finds a vendor by its VAT number, with its name; blocked is kept
        s.query(ApSapVendor).delete()
        K.forget()
        assert H.seed_sap_vendors(s) == len(rows)
        K.forget()
        hits = {x["vendor"]: x for x in K.sap_vendors_by_vat(s, "4320111463")}
        assert hits["120001"]["name"] == "ADAMS & ADAMS ATTORNEYS" and hits["120001"]["account_group"] == "ZMGS"
        assert K.vendor_blocked(s, "126606") is True and K.vendor_blocked(s, "120001") is False
        assert len(K.sap_vendors_by_vat(s, "4380190845", limit=5000)) > 20  # one VAT number, many vendors: normal in SAP
        # an existing cost-centre table gets the rows it lacks; a row a person uploaded stays as it is
        s.query(ApCostCentre).filter(ApCostCentre.cost_centre == "XA0001", ApCostCentre.company_code == "XA00").delete()
        s.query(ApCostCentre).filter(ApCostCentre.cost_centre == "XA0002", ApCostCentre.company_code == "XA00").update({"description": "A person's name for it"})
        s.flush()
        assert H.cost_centres_added(s) >= 1
        K.forget()
        assert K.cost_centre(s, "XA0001", "XA00")["profit_centre"] == "XA0001"
        assert s.query(ApCostCentre.description).filter(ApCostCentre.cost_centre == "XA0002", ApCostCentre.company_code == "XA00").scalar() == "A person's name for it"
        # SAP's posting history: the shipped documents into an empty table, the vendors' coding learned from them
        s.query(ApPosted).delete()
        s.query(ApVendorHistory).delete()
        s.query(AppState).filter(AppState.key == "ap.seeded.posting_history").delete()
        s.flush()
        H.seed(s)
        n = s.query(ApPosted).count()
        assert n > 4000 and s.query(ApVendorHistory).count() > 100
        d = s.query(ApPosted).filter(ApPosted.company_code == "XA05", ApPosted.doc_number == "190000054").one()
        assert (d.vendor, d.reference, d.doc_type, d.amount) == ("139249", "IN036246", "KR", 20355.0) and d.lines
        assert H.counts(s)["documents"] == n and H.counts(s)["from"] == "2026-08-01"
        s.rollback()
    K.forget()


def test_the_lfa1_reader_takes_the_supplier_master_as_sap_exports_it():
    """200+ columns, "Name" three times (the first is NAME1), "Name 2" twice, Country/Region Key, the payment block at column 71."""
    heads = ["Supplier", "Country/Region Key", "Name", "Name 2", "Name 3", "Name 4", "City", "District", "PO Box", "P.O. Box Postal Code", "Postal Code", "Region",
             "Search term", "Street", "Address", "Name", "Name 2", "City"] + [f"Col {i}" for i in range(18, 32)] + ["Account group", "Customer", "Alternative Payee",
             "Central Deletion Flag", "Central posting block", "Central purchasing block", "Language Key", "Tax Number 1", "Tax Number 2"]
    heads += [f"More {i}" for i in range(len(heads), 53)] + ["VAT Registration No."] + [f"Rest {i}" for i in range(54, 70)] + ["Payment block"] + [f"Tail {i}" for i in range(71, 210)]
    assert heads.index("VAT Registration No.") == 53 and heads.index("Payment block") == 70 and len(heads) == 210

    def row(vendor, name, name2, vat, *, deleted="", block="", pay="", tax1="", group="ZMGN"):
        r = [""] * len(heads)
        r[0], r[1], r[2], r[3], r[6], r[15], r[16], r[17] = vendor, "ZA", name, name2, "SANDTON", "WRONG NAME", "WRONG 2", "WRONG CITY"
        r[32], r[35], r[36], r[39], r[53], r[70] = group, deleted, block, tax1, vat, pay
        return r
    data = _xlsx([heads, row("880001", "FIRST NAME", "(PTY) LTD", "4380000001"), row("880002", "GONE CC", "", "", deleted="X", tax1="4380000002"),
                  row("880003", "PAY BLOCKED", "", "4380000003", pay="X"), row("880004", "POST BLOCKED", "", "4380000004", block="X", group="ZVIN")])
    rows = M.read_sheet("sap_vendors", data, "Supplier Master.xlsx")
    assert rows.mapping == {"vendor": "Supplier", "name": "Name", "name2": "Name 2", "vat": "VAT Registration No.", "tax1": "Tax Number 1", "tax2": "Tax Number 2",
                            "city": "City", "country": "Country/Region Key", "blocked": "Central posting block", "deleted": "Central Deletion Flag",
                            "account_group": "Account group", "payment_block": "Payment block"}
    got = {r["vendor"]: r for r in (M._sap_vendor_row(x) for x in rows)}
    assert got["880001"]["name"] == "FIRST NAME (PTY) LTD" and got["880001"]["city"] == "SANDTON" and got["880001"]["country"] == "ZA"
    assert got["880002"]["blocked"] is True and got["880002"]["vat"] == "4380000002"  # the deletion flag; the VAT number in Tax Number 1
    assert got["880003"]["blocked"] is False  # a payment block is not a block on posting
    assert got["880004"]["blocked"] is True and got["880004"]["account_group"] == "ZVIN"
    # the page's upload takes it too (preview: the columns read)
    adm = _admin()
    r = adm.post("/api/ap/masters/sap_vendors/import", files={"file": ("Supplier Master.xlsx", data)}, data={"confirm": "false"}).json()
    assert r["rows"] == 4 and r["blocked"] == 2 and r["mapping"]["country"] == "Country/Region Key" and r["saved"] is False


def test_a_big_seed_file_is_gzipped_and_read_back_as_utf8_text(tmp_path):
    p = tmp_path / "x.csv.gz"
    assert K.write_csv(p, ["Vendor", "Name"], [["1", "Café Ünïcode (Pty) Ltd"]]) == 1
    assert K.read_csv_any(p) == [["Vendor", "Name"], ["1", "Café Ünïcode (Pty) Ltd"]]


# ---- 3: posting history --------------------------------------------------------------------------------------------------------
def test_the_history_joins_vendor_and_gl_items_by_company_and_document_number():
    run = uuid.uuid4().hex[:5].upper()
    v1, v2 = _no(), _no()
    co1, co2 = "X143", "X120"
    doc = "97" + str(uuid.uuid4().int)[:7]
    pay = "15" + str(uuid.uuid4().int)[:7]
    for co in (co1, co2):
        MINE["posted"].add((co, doc))
    MINE["posted"].add((co1, pay))
    # FBL1N in SAP's technical names; FBL3N with the English labels
    fbl1n = [["BUKRS", "LIFNR", "XBLNR", "BLART", "BELNR", "BUDAT", "BLDAT", "WAERS", "MWSKZ", "DMBTR", "SGTXT", "ZTERM"],
             [co1, v1, f"INV-{run}", "KR", doc, "2026-08-17", "2026-08-15", "ZAR", "I9", -1150.0, "Lifts August", "ZI00"],
             [co1, v1, f"INV-{run}", "KZ", pay, "2026-08-31", "2026-08-31", "ZAR", "", 1150.0, "Paid", ""],  # a payment: not a posted invoice
             [co2, v2, f"OTHER-{run}", "KR", doc, "2026-09-02", "2026-09-01", "ZAR", "I9", -230.0, "Same number, other company", "ZI00"]]
    fbl3n = [["Company Code", "Account", "Profit Center", "Assignment", "Document Type", "Document Number", "Posting Period", "Text", "Reference",
              "Amount in Local Currency", "Posting Key", "Document Date", "Clearing Document", "Posting Date", "Document Currency"],
             [co1, "SB205", "AC0310", "", "KR", doc, "2", "Lift service", f"INV-{run}", 600.0, "40", "2026-08-15", "", "2026-08-17", "ZAR"],
             [co1, "SB206", "NOT-A-CC", "", "KR", doc, "2", "Doors", f"INV-{run}", 400.0, "40", "2026-08-15", "", "2026-08-17", "ZAR"],
             [co1, "JA600", "XX0000", "", "KR", doc, "2", "VAT", f"INV-{run}", 150.0, "40", "2026-08-15", "", "2026-08-17", "ZAR"],  # VAT: not an expense
             [co1, "JA000", "XX0000", "", "KR", doc, "2", "", f"INV-{run}", -1150.0, "31", "2026-08-15", "", "2026-08-17", "ZAR"],  # the vendor's own line
             [co2, "SB207", "AC0311", "", "KR", doc, "3", "Other", f"OTHER-{run}", 200.0, "40", "2026-09-01", "", "2026-09-02", "ZAR"]]
    adm, u = _admin(), _user()
    files = {"vendor_items": ("Vendor Line Items.xlsx", _xlsx(fbl1n)), "gl_items": ("GL Line Items.csv", "\n".join(",".join(str(x) for x in r) for r in fbl3n).encode("utf-8"))}
    assert u.post("/api/ap/history/import", files=files, data={"confirm": "true"}).status_code == 403  # admins only
    pre = adm.post("/api/ap/history/import", files=files, data={"confirm": "false"}).json()
    assert (pre["vendor_lines"], pre["gl_lines"], pre["documents"], pre["with_lines"], pre["saved"]) == (3, 5, 2, 2, False)
    r = adm.post("/api/ap/history/import", files=files, data={"confirm": "true"}).json()
    assert r["saved"] and r["added"] == 2
    with SessionLocal() as s:
        a = s.query(ApPosted).filter(ApPosted.company_code == co1, ApPosted.doc_number == doc).one()
        b = s.query(ApPosted).filter(ApPosted.company_code == co2, ApPosted.doc_number == doc).one()
        assert (a.vendor, a.reference, a.ref_key, a.amount, a.tax_code, a.terms, a.posting_date) == (v1, f"INV-{run}", f"INV{run}", 1150.0, "I9", "ZI00", "2026-08-17")
        assert sorted((x["gl"], x["pc"], x["amount"]) for x in a.lines) == [("SB205", "AC0310", 600.0), ("SB206", "NOT-A-CC", 400.0)]
        assert (b.vendor, [x["gl"] for x in b.lines]) == (v2, ["SB207"])  # the same number in another company code: its own document
        assert not s.query(ApPosted).filter(ApPosted.doc_number == pay).first()  # the payment is not kept
        h = H.history(s, co1, v1)
        assert (h.docs, h.split, h.gls, h.cost_centres, h.tax_codes, h.terms, h.last_posting) == (1, 1, {"SB205": 1, "SB206": 1}, {"AC0310": 1}, {"I9": 1}, {"ZI00": 1}, "2026-08-17")
        assert h.profit_centres == {"AC0310": 1, "NOT-A-CC": 1}  # a profit centre that is no cost centre stays a profit centre only
    m = adm.get("/api/ap/masters").json()["history"]
    assert m["documents"] >= 2 and m["vendor_lines"] >= 2 and m["companies"] >= 2
    assert u.get(f"/api/ap/history?q=INV-{run}").json()["rows"][0]["doc_number"] == doc


def test_an_invoice_sap_already_posted_is_red_and_another_vendor_s_same_number_is_not(monkeypatch):
    run = uuid.uuid4().hex[:6].upper()
    vat, other_vat = _vat(), _vat()
    vendor = _code(f"Posted Twice {run} (Pty) Ltd", vat)
    other = _code(f"Other Supplier {run} (Pty) Ltd", other_vat)
    MINE["codes"] |= {vendor, other}
    _post(vendor, f"DUP-{run}", amount=1150.0, doc="190001638", day="2026-08-17")
    _post(vendor, f"AMT {run}", amount=1000.0, day="2026-08-20")
    u = _user()
    b = _run(monkeypatch, u, {f"dup_{run}.pdf": _answer(f"dup{run}", f"Posted Twice {run} (Pty) Ltd", vat),  # printed differently: A–Z 0–9 compare
                              f"amt_{run}.pdf": _answer(f"AMT-{run}", f"Posted Twice {run} (Pty) Ltd", vat),
                              f"oth_{run}.pdf": _answer(f"DUP-{run}", f"Other Supplier {run} (Pty) Ltd", other_vat)}, f"Dup {run}")
    docs = _sheet(u, b["id"])
    red = {x["text"] for x in docs[f"dup_{run}.pdf"]["issues"] if x["level"] == "red"}
    assert docs[f"dup_{run}.pdf"]["status"] == "red" and "already posted in SAP: doc 190001638 on 17.08.2026 (R 1 150.00)" in red
    amt = {x["text"] for x in docs[f"amt_{run}.pdf"]["issues"]}
    assert docs[f"amt_{run}.pdf"]["status"] == "red" and any(t.startswith("already posted in SAP: doc ") and t.endswith("on 20.08.2026 (R 1 000.00); this invoice is R 1 150.00") for t in amt)
    assert not any("already posted in SAP" in x["text"] for x in docs[f"oth_{run}.pdf"]["issues"])  # the same number from another vendor: nothing
    # before a vendor is coded: the SAP vendors with the supplier's VAT number are checked (Mia says it)
    # 1.0.105: no longer Needs you — CBX takes SAP's vendor by the VAT number (cbx/ap/autofill.py), so Tess's red says it too
    new_vat = _vat()
    sv = _sapv(_no("66"), f"Uncoded {run} (Pty) Ltd", new_vat)
    _post(sv, f"NEW-{run}", amount=1150.0, doc="190009999", day="2026-09-01", lines=[{"gl": "SB205", "pc": "AC0310", "amount": 600.0, "text": ""},
                                                                                     {"gl": "SB206", "pc": "AC0311", "amount": 400.0, "text": ""}])
    b2 = _run(monkeypatch, u, {f"new_{run}.pdf": _answer(f"NEW-{run}", f"Uncoded {run} (Pty) Ltd", new_vat)}, f"Dup2 {run}")
    new = _sheet(u, b2["id"])[f"new_{run}.pdf"]
    assert new["status"] == "red" and "already posted in SAP: doc 190009999 on 01.09.2026 (R 1 150.00)" in {x["text"] for x in new["issues"] if x["level"] == "red"}
    assert any(h.startswith("Already posted in SAP: doc 190009999") for h in _hist(new["item_id"]))


def test_pip_codes_an_uncoded_vendor_from_sap_history_yellow_and_below_70_percent_only_prefills(monkeypatch):
    run = uuid.uuid4().hex[:6].upper()
    u = _user()
    # 4 of 5 invoices on SB205 / AC0310: Pip codes it, Tess says yellow (never green from history alone)
    vat = _vat()
    v = _sapv(_no("55"), f"Lift Masters {run} (Pty) Ltd", vat)
    for i in range(4):
        _post(v, f"LM{i}-{run}", day=f"2026-08-1{i}")
    _post(v, f"LMX-{run}", day="2026-08-25", lines=[{"gl": "SB206", "pc": "AC0310", "amount": 1000.0, "text": "Doors"}])
    with SessionLocal() as s:
        got = H.coding(s, "X143", v)
    assert (got["auto"], got["gl"], got["cost_centre"], got["tax_code"], got["pair_docs"], got["docs"]) == (True, "SB205", "AC0310", "I9", 4, 5)
    b = _run(monkeypatch, u, {f"lm_{run}.pdf": _answer(f"LM-NEW-{run}", f"Lift Masters {run} (Pty) Ltd", vat)}, f"Hist {run}")
    d = _sheet(u, b["id"])[f"lm_{run}.pdf"]
    assert d["status"] == "yellow", d["issues"]
    assert "coded from SAP history (G/L SB205, cost centre AC0310: 4 of 5 invoices) - check it" in {x["text"] for x in d["issues"]}
    rows = _cap(u, d["id"])["sap_rows"]
    assert rows[0]["account"] == v and {(r["account"], r["costcentre"], r["tc"]) for r in rows if r["pkey"] == "40" and r["account"] != "JA600"} == {("SB205", "AC0310", "I9")}
    # 1 of 3 on one pair: no coding from history — Code this vendor starts from the most used, with the last postings beside it
    vat2 = _vat()
    w = _sapv(_no("44"), f"Mixed Works {run} (Pty) Ltd", vat2)
    for i, (gl, cc) in enumerate((("SB205", "AC0310"), ("SB206", "AC0311"), ("SB205", "AC0311"))):
        _post(w, f"MW{i}-{run}", day=f"2026-09-0{i + 1}", lines=[{"gl": gl, "pc": cc, "amount": 1000.0, "text": "Works"}])
    b2 = _run(monkeypatch, u, {f"mw_{run}.pdf": _answer(f"MW-NEW-{run}", f"Mixed Works {run} (Pty) Ltd", vat2)}, f"Mixed {run}")
    with SessionLocal() as s:
        hist = H.coding(s, "X143", w)
    assert (hist["auto"], hist["gl"], hist["cost_centre"], hist["tax_code"], hist["docs"]) == (False, "SB205", "AC0311", "I9", 3)
    assert [x["reference"] for x in hist["recent"]] == [f"MW2-{run}", f"MW1-{run}", f"MW0-{run}"] and hist["recent"][0]["gl"] == "SB205"
    # 1.0.105: no longer Needs you — the AI has no answer here (stub), so CBX takes the vendor's most used coding as best
    # guesses: yellow, never green, each guess said (cbx/ap/autofill.py)
    d2 = _sheet(u, b2["id"])[f"mw_{run}.pdf"]
    texts = {x["text"] for x in d2["issues"]}
    assert d2["stage"] == "ready" and d2["status"] == "yellow" and not any("coded from SAP history" in t for t in texts)
    assert "G/L SB205 guessed: the vendor's most used G/L in X143 - check it" in texts and "cost centre AC0311 guessed: the vendor's most used cost centre in X143 - check it" in texts
    rows = _cap(u, d2["id"])["sap_rows"]
    assert rows[0]["account"] == w and {(r["account"], r["costcentre"], r["tc"]) for r in rows if r["pkey"] == "40" and r["account"] != "JA600"} == {("SB205", "AC0311", "I9")}


def test_one_vat_number_many_sap_vendors_mia_picks_or_asks(monkeypatch):
    run = uuid.uuid4().hex[:6].upper()
    vat = _vat()
    blocked = _sapv(_no("31"), f"Shared {run} Blocked", vat, blocked=True)
    a = _sapv(_no("32"), f"Shared {run} A", vat)
    b = _sapv(_no("33"), f"Shared {run} B", vat)
    _post(b, f"SH1-{run}", lines=[])  # b has two documents in X143, a has one: b
    _post(b, f"SH2-{run}", lines=[], day="2026-08-01")
    _post(a, f"SH3-{run}", lines=[], day="2026-09-30")
    with SessionLocal() as s:
        K.forget()
        assert C.sap_vendor_for(s, "Shared", vat, "X143")["vendor"] == b
        # no history in the posting company and SAP can't tell which is opened there: a person picks (never the blocked one)
        sv = C.sap_vendor_for(s, "Shared", vat, "X120")
        assert sv["vendor"] == "" and [x["vendor"] for x in sv["several"]] == sorted([a, b])
        assert sv["note"] == f"several SAP vendors share VAT {vat}: {', '.join(sorted([a, b]))} - pick one"
        # only blocked ones: none is picked
        only = _vat()
    _sapv(_no("34"), f"Only Blocked {run}", only, blocked=True)
    with SessionLocal() as s:
        K.forget()
        ob = C.sap_vendor_for(s, "Only", only, "X143")
        assert ob["vendor"] == "" and ob["blocked_only"] and "only on blocked or deleted vendors" in ob["note"]
        # the opened one wins over history
        assert H.pick(s, [{"vendor": a, "blocked": False}, {"vendor": "999999991", "blocked": False}], "X143")["pick"]["vendor"] == a
    # Needs you: the picker's choices (never the blocked one) and the yellow line; the history of the vendor a person picks
    vat2 = _vat()
    one, two = _sapv(_no("35"), f"Twin {run} One", vat2), _sapv(_no("36"), f"Twin {run} Two", vat2)
    gone = _sapv(_no("37"), f"Twin {run} Blocked", vat2, blocked=True)
    _post(two, f"TW-{run}", company="X120")  # history in another company code doesn't decide it for X143
    u = _user()
    bt = _run(monkeypatch, u, {f"tw_{run}.pdf": _answer(f"TW-NEW-{run}", f"Twin {run}", vat2)}, f"Twin {run}")
    with SessionLocal() as s:
        K.forget()
        sv2 = C.sap_vendor_for(s, f"Twin {run}", vat2, "X143")
    assert sv2["vendor"] == "" and sorted(x["vendor"] for x in sv2["several"]) == sorted([one, two]) and gone not in {x["vendor"] for x in sv2["several"]}
    assert sv2["note"] == f"several SAP vendors share VAT {vat2}: {', '.join(sorted([one, two]))} - pick one"
    # 1.0.105: no longer Needs you — CBX picks one (no creation dates in this list: the lowest number), never the blocked one, yellow
    tw = _sheet(u, bt["id"])[f"tw_{run}.pdf"]
    low = min([one, two], key=int)
    assert tw["vendor"] == low and tw["status"] == "yellow"
    assert f"vendor {low} guessed: one of 2 SAP vendors with this VAT number: the lowest number - check it" in {x["text"] for x in tw["issues"]}
    r = u.get(f"/api/ap/vendors/history?vendor={two}&company=X120").json()
    assert r["sap"]["name"] == f"Twin {run} Two" and (r["history"]["gl"], r["history"]["docs"]) == ("SB205", 1)
    assert u.get(f"/api/ap/vendors/history?vendor={one}&company=X143").json()["history"] is None


# ---- 5: the misread invoice (the layout of Edwin's 467332, made up) --------------------------------------------------------------
def cut_pdf(*, totals: bool = True, cut: bool = False) -> bytes:
    """An invoice laid out like Edwin's 467332: the supplier top right, "TAX INVOICE 467332", the labels (Date:, Consultant:,
    Total Ex. VAT:, VAT:, Total Inc. VAT) stored apart from their values — in the plain text the total ex. VAT comes right
    after "Total Inc. VAT". `cut`: the amounts sit past the page's right edge (cut from the text layer)."""
    doc = pymupdf.open()
    p = doc.new_page(width=595, height=842)
    put = lambda x, y, t, size=8: p.insert_text((x, y), t, fontsize=size)  # noqa: E731
    put(236, 125, "TAX INVOICE  467332", 11)
    for y, v in ((146, "07 September 2026"), (159, "Precious Person"), (173, "Window Cleaning (A,B,C,D)"), (200, "4910000017"), (186, "80000118")):
        put(455, y, v)
    for y, k in ((146, "Date:"), (159, "Consultant:"), (173, "Client Order No:"), (200, "Client VAT No:"), (186, "Client Account No:")):
        put(369, y, k)
    put(22, 146, "To:")
    for i, t in enumerate(("NERINA TRADING 12 (PTY) LTD", "SUMMIT RD DUNKELD WEST", "BENMORE", "2010")):
        put(46, 146 + 13 * i, t)
    for i, t in enumerate(("BRIGHTWATER SERVICES (PTY) LTD", "Brightwater on Summit, Block D", "3A Summit Road", "Dunkeld West", "2196", "Tel: +27 11 000 0000")):
        put(420, 31 + 10 * i, t)
    put(22, 260, "Description"); put(500, 260, "Total Ex. VAT")
    put(40, 280, "Window Cleaning (Block A)"); put(380, 280, "1,389.00"); put(450, 280, "5.00"); put(520 if not cut else 620, 280, "6 945.00")
    vx = 520 if not cut else 620
    if totals:
        put(vx, 470, "21 970.75"); put(430, 470, "Total Inc. VAT")
        for y, v in ((420, "19 105.00"), (433, "2 865.75"), (450, "21 970.75")):
            put(vx, y, v)
    for y, k in ((420, "Total Ex. VAT:"), (433, "VAT:"), (450, "Unpaid Amount:")):
        put(370, y, k)
    put(22, 520, "Banking details:"); put(22, 532, "Somebank"); put(22, 544, "Name: Brightwater Services (Pty) Ltd"); put(22, 556, "Account number: 0000000000")
    put(300, 556, "VAT No."); put(300, 568, "4030000011")
    put(470, 800, "Page 1 of 1")
    data = doc.tobytes()
    doc.close()
    return data


def test_the_467332_layout_reads_the_supplier_the_number_and_the_vat_not_the_labels():
    d = E.read("467332_190000054.pdf", cut_pdf())
    assert "Total Inc. VAT\n19 105.00" in d.text  # the plain text, as the old reading saw it: the total ex. VAT after "Total Inc. VAT"
    kind, _conf, _why = E.classify(d.name, d)
    f = E.extract(kind, d)
    assert kind == "invoice"
    assert f["supplier"] == "BRIGHTWATER SERVICES (PTY) LTD" and f["supplier"] != "Date:"
    assert (f["invoice_no"], f["total"], f["vat"], f["excl"]) == ("467332", 21970.75, 2865.75, 19105.0)
    assert f["supplier_vat"] == "4030000011"  # not the "Client VAT No:"
    assert E.text_unusable(kind, f, d) == ""
    found = E.check(kind, f, today=__import__("datetime").date(2026, 9, 30))
    assert not any("VAT is R" in x["text"] for x in found)
    # a label is never a supplier name; the VAT check never takes the total as the VAT
    assert all(E.is_label(x) for x in ("Date:", "To:", "Invoice", "Tax Invoice", "Page 1 of 1", "TAX INVOICE 467332", "VAT No :", "Banking details:", "NUMBER:"))
    assert not E.is_label("Brightwater Services (Pty) Ltd")
    bad = E.check("invoice", {"supplier": "X", "invoice_no": "1", "total": 21970.75, "vat": 19105.0, "excl": 21970.75, "tax_invoice_words": True, "date": "2026-09-07", "po": "1"})
    assert not any(x["code"] == "vat_rate" for x in bad) and any(x["code"] == "vat_misread" and x["severity"] == "check" for x in bad)


def test_an_invoice_whose_text_layer_is_cut_goes_to_the_vision_reader_not_stopped(monkeypatch):
    run = uuid.uuid4().hex[:6].upper()
    d = E.read("cut.pdf", cut_pdf(cut=True))
    f = E.extract("invoice", d)
    assert f["total"] is None and E.text_unusable("invoice", f, d) == "no total in the text"
    u = _user()
    b = _drop(u, {f"cut_{run}.pdf": cut_pdf(cut=True)}, f"Cut {run}")
    with SessionLocal() as s:
        it = s.query(ApItem).filter(ApItem.batch_id == b["id"]).one()
        c = s.query(ApCapture).filter(ApCapture.item_id == it.id).one()
        assert it.status != "exception" and it.kind == "invoice" and it.stops == 0
        assert it.findings[0]["code"] == "text_unusable" and "capture bots read it with vision" in it.findings[0]["text"]
        assert c.bucket == "manual" and c.stage in ("queued", "capturing", "senior", "needs")
        assert any("can't be trusted (no total in the text)" in h["text"] for h in it.history)


# ---- review fixes (19 Oct) ------------------------------------------------------------------------------------------------------
def test_an_item_sent_to_vision_never_stays_reading(monkeypatch):
    """Skipped in Needs you, or read again with its capture kept: the AP team's text checks give it a status. While the AI is
    off it says "Waiting for the AI", not "Reading"."""
    from cbx.ap import service as S

    run = uuid.uuid4().hex[:6].upper()
    u = _user()
    b = _drop(u, {f"cut2_{run}.pdf": cut_pdf(cut=True)}, f"Cut2 {run}")
    with SessionLocal() as s:
        it = s.query(ApItem).filter(ApItem.batch_id == b["id"]).one()
        iid, cid = it.id, s.query(ApCapture.id).filter(ApCapture.item_id == it.id).scalar()
    item = u.get(f"/api/ap/items/{iid}").json()
    assert item["status"] == "new" and item["ai_wait"] is True  # the AI is off in tests
    monkeypatch.setattr(S, "model_on", lambda: True)
    assert u.get(f"/api/ap/items/{iid}").json()["ai_wait"] is False
    monkeypatch.undo()
    assert u.post(f"/api/ap/captures/{cid}/decide", json={"action": "skip"}).status_code == 200
    item = u.get(f"/api/ap/items/{iid}").json()
    assert item["status"] != "new" and not any(f["code"] == "text_unusable" for f in item["findings"])
    # read again: the capture is kept (a person decided it), so it is not sent to the capture bots again — checked as before
    with SessionLocal() as s:  # as it was before the person skipped it
        it = s.get(ApItem, iid)
        it.status, it.findings = "new", [{"code": "text_unusable", "severity": "info", "text": "x"}]
        s.commit()
    assert u.post(f"/api/ap/batches/{b['id']}/read").status_code == 200
    item = u.get(f"/api/ap/items/{iid}").json()
    assert item["status"] != "new" and not any(f["code"] == "text_unusable" for f in item["findings"])


def credit_pdf() -> bytes:
    return make_ap._pdf("""Credit Supplies (Pty) Ltd
12 Main Road, Sandton
CREDIT NOTE
Credit Note No: CN-10045
Date: 09/10/2026
VAT Reg No: 4123456789
Customer: CBRE Excellerate
Sub Total: -1,000.00
VAT: -150.00
Total: -1,150.00
""")


def test_a_credit_note_with_negative_amounts_keeps_its_vat_and_is_not_sent_to_vision():
    d = E.read("cn.pdf", credit_pdf())
    kind = E.classify("cn.pdf", d)[0]
    f = E.extract(kind, d)
    assert (kind, f["excl"], f["vat"], f["total"]) == ("credit_note", -1000.0, -150.0, -1150.0)
    assert E.text_unusable(kind, f, d) == "" and not any(x["code"] in ("vat_misread", "vat_rate") for x in E.check(kind, f))
    run = uuid.uuid4().hex[:6].upper()
    u = _user()
    b = _drop(u, {f"cn_{run}.pdf": credit_pdf()}, f"CN {run}")
    with SessionLocal() as s:
        it = s.query(ApItem).filter(ApItem.batch_id == b["id"]).one()
        assert it.kind == "credit_note" and it.status != "new" and it.fields["vat"] == -150.0


def test_a_reversed_posting_is_a_yellow_note_and_an_invoice_never_matches_a_credit_memo(monkeypatch):
    run = uuid.uuid4().hex[:6].upper()
    vat = _vat()
    vendor = _code(f"Reversed {run} (Pty) Ltd", vat)
    MINE["codes"].add(vendor)
    _post(vendor, f"REV-{run}", amount=1150.0, doc="190007001", day="2026-08-10")
    _post(vendor, f"REV-{run}", amount=-1150.0, doc="170007002", day="2026-08-20", dtype="KG")
    _post(vendor, f"CRD-{run}", amount=-1150.0, doc="170007003", day="2026-08-21", dtype="KG")  # only a credit memo with this reference
    _post(vendor, f"JRN-{run}", amount=1150.0, doc="100007004", day="2026-08-22", dtype="SA")  # a journal: not an invoice
    with SessionLocal() as s:
        cm = H.posted_twin(s, [vendor], f"CRD-{run}", credit=True)
        assert cm["doc_number"] == "170007003" and H.twin_text(cm, "1150.00") == "already posted in SAP: doc 170007003 on 21.08.2026 (R -1 150.00)"
        assert H.posted_twin(s, [vendor], f"CRD-{run}") is None and H.posted_twin(s, [vendor], f"JRN-{run}") is None
    u = _user()
    b = _run(monkeypatch, u, {f"rev_{run}.pdf": _answer(f"REV-{run}", f"Reversed {run} (Pty) Ltd", vat),
                              f"crd_{run}.pdf": _answer(f"CRD-{run}", f"Reversed {run} (Pty) Ltd", vat)}, f"Rev {run}")
    docs = _sheet(u, b["id"])
    rev = {x["text"]: x["level"] for x in docs[f"rev_{run}.pdf"]["issues"]}
    want = "posted and reversed in SAP: doc 190007001 on 10.08.2026 (R 1 150.00), reversed by doc 170007002 on 20.08.2026 (R -1 150.00)"
    assert docs[f"rev_{run}.pdf"]["status"] == "yellow" and rev.get(want) == "yellow", rev
    assert not any("already posted in SAP" in x["text"] for x in docs[f"crd_{run}.pdf"]["issues"])
