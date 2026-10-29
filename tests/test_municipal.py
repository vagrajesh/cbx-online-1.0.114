"""1.0.75: the CBX Harvester's municipal profiles inside PDF Studio — a bill a profile knows is read by its rules
(the Harvester's own engine), lands in the 28 canonical columns, and its contradiction checks send a bad figure
to a person; a bill no profile knows goes PDF Studio's usual way. Bills here are made up (no real accounts)."""
from __future__ import annotations

import pymupdf
import pytest

from cbx.models import Document, Profile, ProfileVersion
from cbx.pdfstudio import municipal as MU
from cbx.pdfstudio.municipal import engine as E

from ._pdfs import text_pdf

# a made-up City of Johannesburg flat-rate bill in the layout the CoJ profile reads (the city's VAT numbers are
# printed on every bill and are part of its fingerprint; everything else is invented)
COJ = """VAT NO: CITY OF JOHANNESBURG: 4760117194
TAX INVOICE
TEST SITE HOLDINGS (PTY)                  Date                2026/03/04
LTD                                       Statement for        March 2026
Account Number : 900000001
Total Due             59,340.00
Due Date              2026/03/31
Current Charges (Excl. VAT)     {total}
 City Power
  Electricity                     VAT 4710191182 Sub - Total      Total
 (Reading period = 2026/02/01 to 2026/02/28 = 28 days)
 Meter: 11112222; Register: 1; Multiply factor: 1; Start reading: 100,000.000;
 End reading: 112,000.000; Difference: 12,000.000; Consumption: 12,000.000;
 Units: kWh; Type: Actual Readings.
 Charges for 12,000.000 kWh are based on a non-sliding scale for a 28 day period
 Energy charge 12,000.000 kWh @ R 2.5000  ( Billing Period 2026/03 )       30,000.00
 Network Surcharge kWh                                                        600.00
 Demand charge Energy charge 50.000 kVA @ R 400.00 ( Billing Period 2026/03 )   20,000.00
 Service charge ( Billing Period 2026/03 )                                  1,000.00
 VAT: 15.00%                                          7,740.00      59,340.00
"""


def coj_pdf(end: str = "112,000.000") -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(20, 20, 592, 820), COJ.replace("{total}", "51,600.00").replace("End reading: 112,000.000", f"End reading: {end}"), fontsize=7.0, fontname="cour")
    data = doc.tobytes()
    doc.close()
    return data


def test_the_profiles_are_all_there_and_a_bill_reads_like_in_the_harvester():
    ids = [p["profile_id"] for p in MU.profiles()]
    assert len(ids) == 177 and {"eskom", "cot", "coj", "coct", "ekurhuleni", "ethekwini", "mbombela", "ugu"} <= set(ids)
    assert ids == sorted(ids, key=lambda i: next(p["_file"] for p in MU.profiles() if p["profile_id"] == i))  # the Harvester's order
    assert {"newcastle", "stellenbosch", "rustenburg", "nelson_mandela_bay", "george_amptera", "midvaal"} <= set(ids)  # pack v4 (8 Sep 2026)
    got = MU.read(coj_pdf(), "coj.pdf")
    assert got["profile"]["profile_id"] == "coj" and not got["misses"]
    c = got["canon"]
    assert c["account_number"] == "900000001" and c["municipality"] == "CoJ" and c["meter"] == "11112222"
    assert c["total_kwh"] == "12000" and c["demand_kva"] == "50" and c["demand_r"] == "20000.00" and c["total_ex_vat_r"] == "51600.00"
    assert got["checks"] == []  # the charge lines add up to the ex-VAT total
    assert [k for k, _ in E.CANONICAL_ELEC] == [k for k, _ in MU.CANONICAL] and len(MU.CANONICAL) == 28


def test_the_telkom_october_profiles_are_tidy_and_do_not_take_each_other_s_bills():
    """1.0.78: 112 more billers, made on the three Telkom batches of October 2026."""
    import re

    ps = MU.profiles()
    ids = [p["profile_id"] for p in ps]
    assert len(set(ids)) == len(ids) and all(p["_file"] == f"{p['profile_id']}.profile.json" for p in ps)
    assert {"emadlangeni", "sundays_river_valley", "hantam", "polokwane", "jb_marks", "csir", "south_african_national_parks"} <= set(ids)
    for p in ps:
        assert p.get("short_name") and (p["fingerprint"].get("all") or p["fingerprint"].get("any")), p["profile_id"]
    # Sundays River Valley's bills share the "120 DAYS PLUS" layout with King Sabata Dalindyebo — never KSD's any more
    srv = "SUNDAYS RIVER VALLEY MUNICIPALITY\nElectricity Sales: 100.00\n120 DAYS PLUS\nREFERENCE: x\nBALANCE BROUGHT FORWARD"
    assert E.identify_profile(srv, ps)["profile_id"] == "sundays_river_valley"
    assert E.identify_profile(srv.replace("SUNDAYS RIVER VALLEY MUNICIPALITY\nElectricity Sales: 100.00\n", ""), ps)["profile_id"] == "king_sabata_dalindyebo"


def test_a_bill_that_does_not_add_up_is_caught():
    got = MU.read(coj_pdf(end="99,000.000"), "coj.pdf")  # the end reading below the start
    assert any(ch["code"] == "READ↓" and "end_reading_kwh" in ch["keys"] for ch in got["checks"])


def test_which_templates_are_the_harvester_s():
    assert MU.applies(MU.template_columns())
    own = [{"key": "site", "label": "Site Name", "ctype": "text"}, {"key": "acc", "label": "Account No", "ctype": "text"},
           {"key": "kwh", "label": "Total kWh", "ctype": "number"}, {"key": "ex", "label": "Total excl VAT", "ctype": "money"}]
    assert MU.column_map(own) == {"site": "site_name", "acc": "account_number", "kwh": "total_kwh", "ex": "total_ex_vat_r"} and MU.applies(own)
    assert not MU.applies([{"key": "acc", "label": "Account number", "ctype": "text"}, {"key": "inv", "label": "Invoice", "ctype": "text"}])


def test_in_a_batch_known_bills_are_read_by_the_profile_the_rest_go_the_usual_way(owner, db):
    t = owner.post("/api/pdfstudio/templates/municipal", json={}).json()
    assert t["name"] == MU.TEMPLATE_NAME and len(t["columns"]) == 28
    assert owner.post("/api/pdfstudio/templates/municipal", json={}).json()["id"] == t["id"]  # made once
    assert len(owner.get("/api/pdfstudio/municipal").json()["profiles"]) == 177
    b = owner.post("/api/pdfstudio/batches", json={"template_id": t["id"], "name": "March bills"}).json()
    other = text_pdf(["Some Supplier (Pty) Ltd", "Invoice 77", "Account Number: 123", "Total excl VAT: R 100.00"])
    files = [("files", ("coj.pdf", coj_pdf(), "application/pdf")), ("files", ("bad.pdf", coj_pdf("99,000.000"), "application/pdf")),
             ("files", ("other.pdf", other, "application/pdf"))]
    assert owner.post(f"/api/pdfstudio/batches/{b['id']}/files", files=files).status_code == 200
    assert owner.post(f"/api/pdfstudio/batches/{b['id']}/start").status_code == 200
    got = owner.get(f"/api/pdfstudio/batches/{b['id']}").json()
    rows = {r["filename"]: r for r in got["sheet"]["rows"]}
    good = rows["coj.pdf"]["cells"]
    assert good["account_number"]["value"] == "900000001" and good["account_number"]["method"] == "profile:coj" and good["account_number"]["status"] == "ok"
    assert good["total_ex_vat_r"]["value"] == 51600.0 and good["total_kwh"]["value"] == 12000 and good["billing_date"]["value"] == "2026-03-04"
    assert good["read_start"]["value"] == "2026-02-01" and good["municipality"]["value"] == "CoJ"
    assert good["nmd_kva"]["status"] == "absent"  # not on a CoJ bill: blank is right, not a miss
    bad = rows["bad.pdf"]["cells"]
    assert bad["end_reading_kwh"]["status"] == "miss" and "lower than start" in bad["end_reading_kwh"]["note"]  # the check sends it to a person
    assert bad["total_ex_vat_r"]["status"] == "ok"
    assert rows["other.pdf"]["cells"]["account_number"]["method"] != "profile:coj"  # no profile knows it: learned as before
    p = db.query(Profile).filter(Profile.key == "municipal:coj").one()
    assert p.status == "live" and "Harvester profile" in p.label and p.docs_seen == 2
    v = db.get(ProfileVersion, p.live_version_id)
    assert v.ruleset["engine"] == "municipal" and v.ruleset["profile"] == "coj"
    assert {d.profile_id for d in db.query(Document).filter(Document.batch_id == b["id"], Document.filename.in_(("coj.pdf", "bad.pdf"))).all()} == {p.id}
    # a re-run reads them the same way, keeping the earlier read as history
    r = owner.post(f"/api/pdfstudio/batches/{b['id']}/rerun", json={})
    assert r.status_code == 200, r.text
    again = {r["filename"]: r for r in owner.get(f"/api/pdfstudio/batches/{b['id']}").json()["sheet"]["rows"]}
    assert again["coj.pdf"]["cells"]["total_ex_vat_r"]["value"] == 51600.0
    db.expire_all()
    assert db.query(Profile).filter(Profile.key == "municipal:coj").one().docs_seen == 2


def test_an_admin_s_own_profile_file_replaces_the_built_in_one(tmp_path, monkeypatch):
    import json

    d = tmp_path / "muni"
    d.mkdir()
    monkeypatch.setattr(MU, "extra_dir", lambda: d)
    coj = next(p for p in MU.profiles() if p["profile_id"] == "coj")
    mine = {k: v for k, v in coj.items() if not k.startswith("_")}
    mine["short_name"] = "Joburg"
    (d / coj["_file"]).write_text(json.dumps(mine), encoding="utf-8")
    p = next(p for p in MU.profiles() if p["profile_id"] == "coj")
    assert p["_custom"] and MU.read(coj_pdf(), "x.pdf")["canon"]["municipality"] == "Joburg"
    with pytest.raises(StopIteration):
        next(p for p in MU.profiles() if p["profile_id"] == "coj" and not p["_custom"])


def test_comma_decimal_money_reads_right():
    """Pack v4's engine fix: '55 473,14' is R55 473.14, not 100× that; other money is untouched."""
    assert E.clean_value("55 473,14", "money") == "55473.14" and E.clean_value("R 1 250,00", "money") == "1250.00"
    assert E.clean_value("1,234.56", "money") == "1234.56" and E.clean_value("2,112,942.58", "money") == "2112942.58"
    assert E.clean_value("1,234", "number") == "1234" and E.clean_value("(R 4 070.06)", "money") == "-4070.06"


def test_no_account_numbers_in_the_profile_notes():
    import re

    for p in MU.profiles():
        assert not re.search(r"(?<!\d)(?!4\d{9}(?!\d))\d{7,}", str(p.get("_doc") or "")), p["profile_id"]
        assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", str(p.get("_doc") or "")), p["profile_id"]  # no mailbox addresses


# ---- 1.0.77: profiles made and checked on the Telkom folder (made-up figures here) --------------------------

XFA = """<xfa:datasets xmlns:xfa="http://www.xfa.org/schema/xfa-data/1.0/"><xfa:data><Invoice xmlns:dd="http://ns.adobe.com/data-description/" dd:dataNode="dataGroup">
<InvoiceNumber>111122223333</InvoiceNumber><AccountNumber>5000000001</AccountNumber><PremiseAddress1>1 TEST STREET</PremiseAddress1>
<GISKEY>1/1</GISKEY><InvoiceDate>2026-05-14</InvoiceDate><DueDate>2026/06/05</DueDate><Deposit>0.0</Deposit><GuaranteeDeposit>0.0</GuaranteeDeposit>
<Category dd:dataNode="dataGroup"><CategoryName>Elektrisiteit Vooruitbetaal</CategoryName>
<Line><Description>Niespits energ heff L/Volt gebruiktyd</Description><Amount>1000.00</Amount><VAT>150.00</VAT><Total>1150.00</Total></Line>
<Line><Description>Spits energ heff L/Volt gebruiktyd</Description><Amount>2000.00</Amount><VAT>300.00</VAT><Total>2300.00</Total></Line>
<Line><Description>Standard Energieheff L/Volt gebruiktyd</Description><Amount>1500.00</Amount><VAT>225.00</VAT><Total>1725.00</Total></Line>
<Line><Description>Vaste Heffing L/Volt gebruiktyd</Description><Amount>500.00</Amount><VAT>75.00</VAT><Total>575.00</Total></Line>
<Line><Description>Aanvraagheff Elel laevolt gebruiktyd</Description><Amount>3000.00</Amount><VAT>450.00</VAT><Total>3450.00</Total></Line>
<Line><Description>Lesingdatums Huidige 30/04/26 Vorige 31/03/26 ( 30 dae)</Description><Amount>null</Amount></Line>
<Line><Description>Meter: 1234567890123(Werklike) Huidige 5000 Verbruik 400 KWH ( E_PK_PP )</Description><Amount>null</Amount></Line>
<Line><Description>Meter: 1234567890123(Werklike) Huidige 9000 Verbruik 900 KWH ( E_OP_PP )</Description><Amount>null</Amount></Line>
<Line><Description>Meter: 1234567890123(Werklike) Huidige 7000 Verbruik 600 KWH ( E_STD_PP )</Description><Amount>null</Amount></Line>
<Line><Description>Meter: 1234567890123(Werklike) Huidige 0 Vorige 0 Verbruik 80 KVA</Description><Amount>null</Amount></Line>
</Category><Category dd:dataNode="dataGroup"><CategoryName>VAT</CategoryName></Category>
<LevyAmount>8000.00</LevyAmount><VatRegNo>4000142267</VatRegNo></Invoice></xfa:data></xfa:datasets>"""


def xfa_shell_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((40, 60), "The document you are trying to load requires Adobe Reader 8 or higher.", fontsize=9)
    x = doc.get_new_xref()
    doc.update_object(x, "<<>>")
    doc.update_stream(x, XFA.encode())
    data = doc.tobytes()
    doc.close()
    return data


def test_tshwane_e_billing_shells_are_read_from_their_form_data():
    got = MU.read(xfa_shell_pdf(), "cot.pdf")
    assert got["profile"]["profile_id"] == "cot_xfa", got["profile"]
    c = got["canon"]
    assert c["account_number"] == "5000000001" and c["municipality"] == "CoT" and c["billing_date"] == "2026-05-14"
    assert (c["peak_kwh"], c["offpeak_kwh"], c["standard_kwh"], c["total_kwh"], c["demand_kva"]) == ("400", "900", "600", "1900", "80")
    assert (c["peak_r"], c["offpeak_r"], c["standard_r"], c["demand_r"], c["other_r"], c["total_ex_vat_r"]) == ("2000.00", "1000.00", "1500.00", "3000.00", "500.00", "8000.00")
    assert c["reading_type"] == "Actual" and c["read_start"] == "31/03/26" and got["checks"] == []
    row = next(r for r in got["rows"] if r["service"] == "electricity")
    assert row["levy_total_ex_r"] == c["total_ex_vat_r"]  # the bill's own total agrees


GEORGE = """                               VAT No. 4630193664
                                  P.O.BOX 19, GEORGE, 6530              STATEMENT DATE                        15/08/2025
                                                                          TAX INVOICE NO.                     12345678
                                                                     ACCOUNT NO.                           GRG 10000000001
                                                                             SITE ADDRESS:                     1 TEST STREET
  SERVICE     BALANCEOPENING       RECEIPTS       CHARGE        INTEREST    ADJUSTMENTS       VAT         BALANCECLOSING
                  12,000.00      -12,000.00       11,100.00             0.00             0.00         1,665.00       12,765.00
  Peak Energy        TOUPM               1000.000 3000.003450.00 450.00
  Peak Energy        TOUPM
  Electricity         Demand: kVa           100.000 2000.002300.00  300.00
  Elec:1508          Days/Dae                   30
  Elec:1508          Cons New                  200   400.00  920.00   120.00
  Standard           TOUSM               2000.000 4000.004600.00 600.00
  Off Peak           TOUOS               3000.000 1300.001495.00  195.00
                                                                                  FNB  Tp.    Meter No.    Previous    New Reading Factor
  E     P12345678  100000.000 101000.000         1000.000  01/07-01/08      32.26
  K     D12345678               100.000              100.000     01/07-01/08
"""


def george_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=900, height=842)
    page.insert_textbox(pymupdf.Rect(10, 20, 890, 830), GEORGE, fontsize=6.5, fontname="cour")
    data = doc.tobytes()
    doc.close()
    return data


def test_the_2025_george_statement_with_glued_amounts():
    got = MU.read(george_pdf(), "geo.pdf")
    assert got["profile"]["profile_id"] == "george_2025", got["profile"]
    c = got["canon"]
    assert c["account_number"] == "GRG 10000000001" and c["billing_date"] == "15/08/2025" and c["site_name"] == "1 TEST STREET"
    assert (c["peak_r"], c["standard_r"], c["offpeak_r"], c["demand_r"], c["other_r"]) == ("3000.00", "4000.00", "1300.00", "2000.00", "800.00")
    assert c["total_kwh"] == "6000" and c["demand_kva"] == "100" and c["meter"] == "12345678"
    row = next(r for r in got["rows"] if r["service"] == "electricity")
    assert c["total_ex_vat_r"] == row["ge_elec_charge_r"] == "11100.00" and got["checks"] == []
