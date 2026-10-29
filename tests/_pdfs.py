"""Synthetic PDFs for the warehouse, Doc Watch and Site Schedules tests (reportlab)."""
from __future__ import annotations

import io

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


def text_pdf(lines: list[str], *, title: str = "") -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    if title:
        c.setTitle(title)
    y = 800
    c.setFont("Helvetica", 11)
    for ln in lines:
        c.drawString(50, y, ln)
        y -= 18
        if y < 60:
            c.showPage()
            c.setFont("Helvetica", 11)
            y = 800
    c.showPage()
    c.save()
    return buf.getvalue()


def municipal_bill(account="4471 0022 19", period="July 2026") -> bytes:
    return text_pdf(
        [
            "CITY OF EKURHULENI METROPOLITAN MUNICIPALITY",
            "TAX INVOICE / STATEMENT OF ACCOUNT",
            f"Account number: {account}",
            f"Period: {period}",
            "Property: 12 Rietfontein Road, Boksburg",
            "Electricity  kWh consumed  18 420   R 42 366.00",
            "Water  kl consumed  212   R 6 190.40",
            "Refuse removal   R 1 212.00",
            "Sewer   R 2 066.10",
            "VAT 15%   R 7 775.18",
            "TOTAL DUE   R 59 609.68",
            "Please pay by 15 August 2026.",
        ],
        title="EKU statement",
    )


def vendor_invoice() -> bytes:
    return text_pdf(["Bidvest Facilities Management (Pty) Ltd", "TAX INVOICE", "Invoice No: INV-20441", "Date: 2026-08-02", "Description: Monthly cleaning services", "Sub total R 18 000.00", "VAT R 2 700.00", "Total R 20 700.00"])


def _r(v: float) -> str:
    return "R " + f"{v:,.2f}".replace(",", " ")


def bidvest_invoice(n: int, *, excl: float = 18000.0, travel_hours: float | None = None) -> bytes:
    """Vendor layout A ('capitec.invoice' in real life): labels in the Bidvest style. `travel_hours`
    adds a travel section — most invoices don't have one, which is what optional columns are for."""
    vat = round(excl * 0.15, 2)
    lines = [
        "Bidvest Facilities Management (Pty) Ltd",
        "Reg 1998/012345/07 · VAT 4123456789 · 12 Sandton Drive, Sandton",
        "TAX INVOICE",
        f"Invoice No: INV-{20440 + n}",
        f"Date: 2026-08-{2 + n:02d}",
        "Description: Monthly cleaning services",
        f"Material excl VAT {_r(excl / 2)}",
    ]
    if travel_hours is not None:
        lines += [f"Travel hours {travel_hours:g}", f"Travel time rate {_r(350)}"]
    lines += [f"Sub total {_r(excl)}", f"VAT {_r(vat)}", f"Total {_r(round(excl + vat, 2))}"]
    return text_pdf(lines, title=f"Bidvest INV-{20440 + n}")


def megaglo_invoice(n: int, *, excl: float = 4200.0) -> bytes:
    """Vendor layout B: a small vendor (1–3 invoices) with a different letterhead but the same
    money labels as Bidvest, so a sibling profile's rules can be borrowed and tested."""
    vat = round(excl * 0.15, 2)
    return text_pdf(
        [
            "MEGAGLO ELECTRICAL CONTRACTORS",
            "Unit 4, Industrial Park, Boksburg · Tel 011 555 0100 · info@megaglo.co.za",
            "INVOICE",
            f"Invoice number: MG-{310 + n}",
            f"Invoice date: 2026-08-{10 + n:02d}",
            "Job: replace distribution board, Capitec Boksburg",
            f"Material excl VAT {_r(excl / 3)}",
            f"Sub total {_r(excl)}",
            f"VAT {_r(vat)}",
            f"Total {_r(round(excl + vat, 2))}",
        ],
        title=f"Megaglo MG-{310 + n}",
    )


def scanned_pdf(lines: list[str]) -> bytes:
    """Image-only PDF: the text is rasterised, so pdfplumber sees nothing and OCR is needed."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1654, 2339), "white")  # A4 @ 200 dpi
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 44)
    except OSError:
        font = ImageFont.load_default()
    y = 200
    for ln in lines:
        d.text((150, y), ln, fill="black", font=font)
        y += 80
    out = io.BytesIO()
    img.save(out, format="PDF", resolution=200.0)
    return out.getvalue()


def certificate(valid_until="Valid until: 31/03/2027", site="Sandton campus") -> bytes:
    return text_pdf(
        [
            "CERTIFICATE OF COMPLIANCE",
            "Electrical installation — in terms of the OHS Act",
            f"Site: {site}",
            "Client: Absa",
            "Issued by: J. Naidoo, Registered Person 12345",
            "Date of issue: 01/04/2025",
            valid_until,
            "This certificate covers the complete electrical installation of the premises.",
        ]
    )


def certificate_no_date() -> bytes:
    return text_pdf(["FIRE EQUIPMENT SERVICE RECORD", "Site: Rosebank", "Client: Absa", "Serviced by: FirePro", "All extinguishers checked and tagged.", "Next service to be scheduled with the client."])


def swiftnet_quote(*, electricity="R1,250,00 per month", site="Upington", quote_no="Q-88240") -> bytes:
    return text_pdf(
        [
            "SWIFTNET (PTY) LTD",
            "CO-LOCATION QUOTATION",
            f"Quotation No: {quote_no}",
            f"Site name: {site}",
            "Province: Northern Cape",
            "Date: 12 August 2026",
            "",
            "Equipment accommodation: 1 x 600x600 outdoor cabinet position  R 4 850,00 per month",
            "Antennas: 3 x panel antennas at 25 m  R 0,00",
            f"Electricity: metered supply  {electricity}",
            "Access road: maintenance contribution  R 350,00 per month",
            "Once-off installation fee  R 18 500,00",
            "",
            "Total monthly (excl. VAT)  R 6 450,00",
            "Total once-off (excl. VAT)  R 18 500,00",
            "Valid for 30 days from date of issue.",
        ],
        title="Swiftnet quotation",
    )


# ---- one billing system, several municipalities (the Abaqulusi / Bela-Bela / Alfred Duma shape) ----
def _bill_canvas(lines: list[tuple[float, float, str]], *, title: str = "") -> bytes:
    """Draw strings at exact positions so heading rows and the data under them line up."""
    from reportlab.lib.pagesizes import A4

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    if title:
        c.setTitle(title)
    c.setFont("Helvetica", 9)
    for x, y, text in lines:
        c.drawString(x, y, text)
    c.showPage()
    c.save()
    return buf.getvalue()


def shared_system_bill(issuer: str, *, account: str, date: str, meter: str, old: float, new: float, bank: str = "9 555 001 224", deposit: str = "0.00") -> bytes:
    """A statement from a shared municipal billing system: the same printed skeleton whoever issues
    it. Note the traps this layout really has — a second 'ACCOUNT NUMBER:' in the banking block that
    holds the MUNICIPALITY's bank account, a 'METER TYPE' heading right of 'METER NO.', and meter
    readings printed with three decimals ('91767.000')."""
    H = 842.0
    used = round(new - old, 3)
    rows = [
        (300, H - 60, issuer),
        (300, H - 74, "P O BOX 12 · TEL: 012 345 6789"),
        (300, H - 88, "VAT No.:4991820371"),
        (180, H - 120, "TAX INVOICE/STATEMENT OF ACCOUNT"),
        (40, H - 150, f"Account Number: {account}"),
        (300, H - 150, f"Account Date: {date}"),
        (40, H - 164, "Consumer Name: TFMC"),
        (300, H - 164, f"Tax Invoice No.: {account}2026"),
        (40, H - 178, "Internet PIN: 440624127"),
        (300, H - 178, f"Deposit: {deposit}"),
        (250, H - 205, "METER READINGS"),
        (40, H - 225, "METER NO."),
        (130, H - 225, "METER TYPE"),
        (215, H - 225, "OLD READING"),
        (300, H - 225, "NEW READING"),
        (385, H - 225, "READING TYPE"),
        (470, H - 225, "CONSUMPTION"),
        (40, H - 239, meter),
        (130, H - 239, "ELECTRICITY"),
        (215, H - 239, f"{old:.3f}"),
        (300, H - 239, f"{new:.3f}"),
        (385, H - 239, "E"),
        (470, H - 239, f"{used:.3f}"),
        (250, H - 265, "ACCOUNT DETAILS"),
        (40, H - 285, "DATE CODE DESCRIPTION UNITS TARIFF VALUE"),
        (40, H - 299, f"{date} 050301 ELECTRICITY CONSUMPTION 350.000 @ 4.99700000 * 1,748.95"),
        (40, H - 330, "REMITTANCE ADVICE BANKING DETAILS"),
        (40, H - 344, f"ACCOUNT NUMBER: {account}"),
        (300, H - 344, f"BANK NAME: ABSA  ACCOUNT NUMBER: {bank}"),
        (40, H - 358, f"TOTAL DUE: 5,228.68"),
    ]
    return _bill_canvas(rows, title=f"{issuer} {account}")


def other_system_bill(*, account: str = "100003212") -> bytes:
    """A genuinely different layout (values before labels, its own headings) — it must NOT land in
    the same family as `shared_system_bill`."""
    H = 842.0
    rows = [
        (40, H - 60, "DIANA@EXAMPLE.CO.ZA"),
        (120, H - 100, "TELKOM TELKOM"),
        (120, H - 114, "TFMC (PTY) LTD"),
        (47, H - 150, f"ACCOUNT NO: {account}"),
        (60, H - 170, "DEPOSIT DATE DEPOSIT AMOUNT PROPERTY VALUE TAX INVOICE NO."),
        (63, H - 184, "1999/01/01 47800.00 4310000 100003212/2026/07/21"),
        (150, H - 205, "PREVIOUS READING NEW READING KL / UNITS TARIFF CODE"),
        (50, H - 219, "TYPE METER NUMBER 2025/11/11 2025/12/12 WATER ELECTRICITY"),
        (48, H - 233, "EL 013403 9135703.00 9205673.00 69970.000 EL0103 0+ @ 3.123600"),
        (154, H - 270, "DETAILS DATE REFERENCE AMOUNT"),
        (48, H - 284, "BALANCE B/FWD 2025/11/18 232639.67"),
        (65, H - 320, "120 + DAYS 90 DAYS 60 DAYS 30 DAYS CURRENT DUE"),
        (75, H - 334, "0.00 0.00 0.00 0.00 0.01 262867.52"),
        (45, H - 360, "MESSAGE BOX DUE"),
        (44, H - 380, "SEQUENCE CURRENT AMOUNT DUE"),
    ]
    return _bill_canvas(rows, title=f"other layout {account}")
