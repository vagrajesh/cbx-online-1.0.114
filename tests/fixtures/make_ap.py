"""A small AP folder for the AP Tool's tests: invoices (one good, one with wrong VAT, one duplicate), a credit note,
a payment run, a bank letter, a vendor ledger, a supplier statement, a supplier query mail and a file nobody knows.
Made on the fly (no real supplier data). The same file in the old CBX and CBX-Connect 2."""
from __future__ import annotations

import io
from datetime import date, timedelta


def _pdf(text: str) -> bytes:
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    y = 50
    for line in text.strip().splitlines():
        page.insert_text((50, y), line, fontsize=10)
        y += 14
    return doc.tobytes()


def _xlsx(sheets: dict) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def invoice(no="INV-10045", supplier="Acme Cleaning (Pty) Ltd", vat_no="4123456789", excl=10000.00, vat=1500.00, po="PO-55821", when=None, words="TAX INVOICE") -> bytes:
    when = when or (date.today() - timedelta(days=10))
    total = excl + vat
    return _pdf(f"""
{supplier}
12 Main Road, Sandton
{words}
Invoice No: {no}
Invoice Date: {when:%d/%m/%Y}
VAT Reg No: {vat_no}
Customer: CBRE Excellerate
Customer VAT No: 4999888777
Order No: {po}
Cleaning services, September
Sub Total: R {excl:,.2f}
VAT 15%: R {vat:,.2f}
Total Due: R {total:,.2f}
""")


def bank_letter() -> bytes:
    return _pdf(f"""
Standard Bank
Confirmation of banking details
Date: {date.today():%d/%m/%Y}
Account holder: Acme Cleaning (Pty) Ltd
Account number: 0712 345 678
Branch code: 051001
Account type: Current
""")


def statement() -> bytes:
    return _pdf("""
Acme Cleaning (Pty) Ltd
STATEMENT
VAT 4123456789
INV-10045   11,500.00
INV-10046   5,750.00
INV-10050   2,300.00
Balance due: R 19,550.00
""")


def payment_run() -> bytes:
    pay = date.today()
    return _xlsx({"Proposal": [
        ["Payment proposal", None, None, None, None, None, None],
        ["Vendor", "Vendor name", "Reference", "Amount", "Due date", "Payment date", "Bank account"],
        ["100200", "Acme Cleaning (Pty) Ltd", "INV-10045", 11500.00, pay - timedelta(days=5), pay, "0712345678"],
        ["100200", "Acme Cleaning (Pty) Ltd", "INV-10045", 11500.00, pay - timedelta(days=5), pay, "0712345678"],
        ["100310", "Blue Gardens CC", "BG-778", 4200.50, pay + timedelta(days=10), pay, "62012345678"],
        ["100400", "Zero Supplies", "Z-1", 0, pay, pay, "1234567"],
        ["Total", None, None, 27200.50, None, None, None],
    ]})


def ledger() -> bytes:
    t = date.today()
    return _xlsx({"Open items": [
        ["Vendor", "Name", "Document No", "Document date", "Net due date", "Amount in LC"],
        ["100200", "Acme Cleaning (Pty) Ltd", "INV-10045", t - timedelta(days=20), t - timedelta(days=5), 11500.00],
        ["100200", "Acme Cleaning (Pty) Ltd", "INV-10046", t - timedelta(days=50), t - timedelta(days=35), 5750.00],
        ["100200", "Acme Cleaning (Pty) Ltd", "INV-09001", t - timedelta(days=200), t - timedelta(days=170), 900.00],
    ]})


def query_mail() -> bytes:
    return (b"From: Jane Smith <accounts@acmecleaning.example>\r\nTo: ap@example.com\r\nSubject: Outstanding payment INV-10045\r\n"
            b"Date: Mon, 12 Oct 2026 09:10:00 +0200\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
            b"Good day, please advise when invoice INV-10045 will be paid. It is now overdue.\r\nRegards, Jane\r\n")


def folder() -> dict[str, bytes]:
    """{file name: bytes} — the folder a person would drop."""
    return {
        "acme_inv_10045.pdf": invoice(),
        "acme_inv_10045 copy.pdf": invoice(),
        "acme_inv_10046.pdf": invoice(no="INV-10046", excl=5000.00, vat=900.00),  # VAT is wrong (18%)
        "acme_credit.pdf": invoice(no="CN-200", excl=1000.00, vat=150.00, words="CREDIT NOTE"),
        "payment_run_proposal.xlsx": payment_run(),
        "acme_bank_letter.pdf": bank_letter(),
        "acme_open_items.xlsx": ledger(),
        "acme_statement.pdf": statement(),
        "query_inv_10045.eml": query_mail(),
        "canteen_menu.txt": b"Monday: soup. Tuesday: curry. Wednesday: braai.",
    }


# 1.0.98: from CBX-Connect 2.10.3 tests/fixtures/make_ap.py


# 2.10.3: Tess and the coding forms check every G/L against the chart of accounts (Edwin's chart ships with CBX). The
# made-up G/Ls the 2.10.0 tests code with, and the Harvester's default GL Codes (SA110 … SA412, not in the CBX chart), are
# added to it here, the way an admin would upload them.
TEST_GLS = ("6100100", "6100200", "6200100", "999999", "SA110", "SA010", "SA210", "SA311", "SA312", "SA412")


TEST_CCS = ("CC1", "CC9", "CC12", "CC77", "CCX", "C")  # the made-up cost centres the 2.10.0 tests code with


def chart(*accounts: str) -> None:
    """The made-up G/Ls in the chart of accounts, and (2.10.3) the made-up cost centres in SAP's Cost Center List for every
    starting company code — Tess checks both."""
    from cbx import knowledge as K
    from cbx.ap import masters as M
    from cbx.db import SessionLocal
    from cbx.models.ap import ApCostCentre, ApGlAccount

    M.ensure_seeded()  # 1.0.98: the seeds load on the first AP request; a table filled here first would never be seeded
    with SessionLocal() as s:
        have = {(a, b) for a, b in s.query(ApCostCentre.cost_centre, ApCostCentre.company_code).filter(ApCostCentre.cost_centre.in_(TEST_CCS)).all()}
        for cc in TEST_CCS:
            for co, _name, _entity in M.COMPANY_CODES:
                if (cc, co) not in have:
                    s.add(ApCostCentre(cost_centre=cc, company_code=co, currency="", profit_centre="", description=f"Test cost centre {cc}"))
        s.commit()
    with SessionLocal() as s:
        have = {a for (a,) in s.query(ApGlAccount.account).filter(ApGlAccount.account.in_(accounts or TEST_GLS)).all()}
        for a in accounts or TEST_GLS:
            if a not in have:
                s.add(ApGlAccount(account=a, account_key=K.gl_key(a), name=f"Test account {a}", source="upload", updated_by="tests"))
        s.commit()
    K.forget()
