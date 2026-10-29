"""Synthetic South African municipal electricity bills in three layouts (Emfuleni, Centlec,
Ekurhuleni) for PDF Studio tests and demos. Deterministic. Returns gold values per file.

    from tests.fixtures.make_bills import make_bills
    bills = make_bills(Path("/tmp/bills"))   # [{file, family, gold:{...}, scanned, vat_mismatch, nmd_page}]

Special cases: one Centlec bill puts NMD on page 2; one Emfuleni bill is a scanned image (no text
layer); `make_vat_mismatch()` makes an Ekurhuleni bill whose VAT does not equal 15% of the amount.
"""
from __future__ import annotations

import io
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

W, H = A4


def _money(v: float) -> str:
    s = f"{v:,.2f}".replace(",", " ")
    return f"R {s}"


def _num(v: float) -> str:
    return f"{int(v):,}".replace(",", " ")


def _emfuleni(c: canvas.Canvas, g: dict) -> None:
    c.setFont("Helvetica-Bold", 15)
    c.drawString(40, H - 60, "EMFULENI LOCAL MUNICIPALITY")
    c.setFont("Helvetica", 10)
    c.drawString(40, H - 76, "Vanderbijlpark Civic Centre · PO Box 3 · Vanderbijlpark 1900 · VAT reg 4560123456")
    c.setFont("Helvetica-Bold", 12)
    c.drawString(40, H - 100, "TAX INVOICE")
    c.setFont("Helvetica", 10.5)
    y = H - 130
    c.drawString(40, y, "Account No.:")
    c.drawString(200, y, g["account_no"])
    c.drawString(330, y, "Invoice date:")
    c.drawString(430, y, g["invoice_date"])
    y -= 18
    c.drawString(40, y, "Billing period:")
    c.drawString(200, y, g["period_text"])
    y -= 18
    c.drawString(40, y, "Site:")
    c.drawString(200, y, g["site"])
    y -= 36
    c.setFont("Helvetica-Bold", 10.5)
    c.drawString(40, y, "ELECTRICITY")
    c.setFont("Helvetica", 10.5)
    y -= 18
    c.drawString(40, y, "Consumption (kWh)")
    c.drawRightString(330, y, _num(g["kwh"]))
    y -= 18
    c.drawString(40, y, "Rate per kWh")
    c.drawRightString(330, y, "R 1.83")
    y -= 18
    c.drawString(40, y, "Maximum demand (kVA)")
    c.drawRightString(330, y, _num(g["demand_kva"]))
    y -= 18
    c.drawString(40, y, "Notified maximum demand (kVA)")
    c.drawRightString(330, y, _num(g["nmd_kva"]))
    y -= 18
    c.drawString(40, y, "Security held")
    c.drawRightString(330, y, _money(g["deposit"]))
    y -= 36
    c.drawString(40, y, "Sub-total (excl. VAT)")
    c.drawRightString(330, y, _money(g["excl_vat"]))
    y -= 18
    c.drawString(40, y, "VAT @ 15%")
    c.drawRightString(330, y, _money(g["vat"]))
    y -= 18
    c.setFont("Helvetica-Bold", 11)
    c.drawString(40, y, "TOTAL DUE")
    c.drawRightString(330, y, _money(g["total"]))
    c.setFont("Helvetica", 9)
    c.drawString(40, 60, "Please pay by the due date. Queries: 016 950 5000. This invoice is also available on the e-Services portal.")


def _centlec(c: canvas.Canvas, g: dict, nmd_page2: bool) -> None:
    c.setFont("Helvetica-Bold", 15)
    c.drawString(40, H - 60, "CENTLEC (SOC) Ltd")  # letterhead; issuer name is printed in caps
    c.setFont("Helvetica", 10)
    c.drawString(40, H - 76, "Electricity distributor for Mangaung · 6 Rhodes Avenue · Bloemfontein 9301")
    c.setFont("Helvetica-Bold", 12)
    c.drawString(40, H - 100, "Statement of account")
    c.setFont("Helvetica", 10.5)
    y = H - 130
    c.drawString(40, y, "Account number")
    c.drawString(180, y, g["account_no"])
    y -= 18
    c.drawString(40, y, "Period")
    c.drawString(180, y, g["period_text"].replace("–", "-"))
    y -= 18
    c.drawString(40, y, "Premises")
    c.drawString(180, y, g["site"])
    y -= 36
    c.drawString(40, y, "Energy consumed")
    c.drawString(220, y, f"{_num(g['kwh'])} kWh")
    y -= 18
    c.drawString(40, y, "Demand")
    c.drawString(220, y, f"{_num(g['demand_kva'])} kVA")
    if not nmd_page2:
        y -= 18
        c.drawString(40, y, "NMD")
        c.drawString(220, y, f"{_num(g['nmd_kva'])} kVA")
    y -= 36
    c.drawString(40, y, "Amount excl. VAT")
    c.drawRightString(360, y, _money(g["excl_vat"]))
    y -= 18
    c.drawString(40, y, "VAT 15%")
    c.drawRightString(360, y, _money(g["vat"]))
    y -= 18
    c.setFont("Helvetica-Bold", 11)
    c.drawString(40, y, "Total payable")
    c.drawRightString(360, y, _money(g["total"]))
    if nmd_page2:
        c.setFont("Helvetica", 9)
        c.drawString(40, 60, "Meter detail continues on page 2.")
        c.showPage()
        c.setFont("Helvetica-Bold", 12)
        c.drawString(40, H - 60, "Meter detail (page 2)")
        c.setFont("Helvetica", 10.5)
        c.drawString(40, H - 90, "Meter 1")
        c.drawString(220, H - 90, "Supply point A")
        c.drawString(40, H - 108, "Meter 2")
        c.drawString(220, H - 108, "Supply point B")
        c.drawString(40, H - 140, "NMD")
        c.drawString(220, H - 140, f"{_num(g['nmd_kva'])} kVA")


def _ekurhuleni(c: canvas.Canvas, g: dict) -> None:
    c.setFont("Helvetica-Bold", 15)
    c.drawString(40, H - 60, "City of Ekurhuleni")
    c.setFont("Helvetica", 10)
    c.drawString(40, H - 76, "Metropolitan Municipality · Energy Department · Germiston 1400")
    c.setFont("Helvetica-Bold", 12)
    c.drawString(40, H - 100, "Municipal account")
    # header row with values below
    cols = [(40, "Acc no", g["account_no"]), (140, "Period", g["period_short"]), (230, "kWh", _num(g["kwh"])), (310, "Max demand kVA", _num(g["demand_kva"])), (430, "NMD (kVA)", _num(g["nmd_kva"]))]
    c.setFont("Helvetica-Bold", 10)
    for x, lab, _ in cols:
        c.drawString(x, H - 140, lab)
    c.setFont("Helvetica", 10.5)
    for x, _, val in cols:
        c.drawString(x, H - 158, val)
    y = H - 210
    c.drawString(40, y, "Service address")
    c.drawString(200, y, g["site"])
    y -= 36
    c.drawString(40, y, "Total excl VAT")
    c.drawRightString(400, y, _money(g["excl_vat"]))
    y -= 18
    c.drawString(40, y, "VAT")
    c.drawRightString(400, y, _money(g["vat"]))
    y -= 18
    c.setFont("Helvetica-Bold", 11)
    c.drawString(40, y, "Amount due")
    c.drawRightString(400, y, _money(g["total"]))


LAYOUTS = {"emfuleni": _emfuleni, "centlec": _centlec, "ekurhuleni": _ekurhuleni}
PREFIX = {"emfuleni": "EMF-771", "centlec": "CEN-209", "ekurhuleni": "5501 44"}
NAME = {"emfuleni": "Emfuleni", "centlec": "Centlec", "ekurhuleni": "Ekurhuleni"}
SITES = ["Vaal Mall", "Riverside Office Park", "Sasolburg Depot", "Boksburg Warehouse", "Bloem Plaza", "Germiston Hub"]


def gold_for(family: str, i: int, *, vat_mismatch: bool = False) -> dict:
    kwh = 9870 + i * 1731
    demand = 184 + i * 17
    nmd = demand + 16 + (i % 3) * 10
    excl = round(kwh * 2.5 + demand * 47.3, 2)
    vat = round(excl * 0.15, 2)
    if vat_mismatch:
        vat = round(vat - 100.0, 2)
    total = round(excl + vat, 2)
    month = 6 + (i % 2)
    return {
        "account_no": f"{PREFIX[family]}{20 + i}",
        "municipality": NAME[family],
        "period": f"2026-{month:02d}-01 – 2026-{month:02d}-30",
        "period_text": f"01/{month:02d}/2026 – 30/{month:02d}/2026",
        "period_short": f"{['Jun','Jul'][month-6]} 2026",
        "invoice_date": f"05/{month+1:02d}/2026",
        "site": SITES[i % len(SITES)],
        "kwh": kwh,
        "demand_kva": demand,
        "nmd_kva": nmd,
        "excl_vat": excl,
        "vat": vat,
        "total": total,
        "deposit": 5000.0 + i * 250,
    }


def render(family: str, g: dict, *, nmd_page2: bool = False) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"{NAME[family]} {g['account_no']}")
    if family == "centlec":
        _centlec(c, g, nmd_page2)
    else:
        LAYOUTS[family](c, g)
    c.showPage()
    c.save()
    return buf.getvalue()


def to_scanned(pdf_bytes: bytes, dpi: int = 200) -> bytes:
    """Re-render every page as an image and build a PDF that contains only images (no text layer)."""
    try:
        import pymupdf as fitz
    except ImportError:  # pragma: no cover
        import fitz

    src = fitz.open(stream=pdf_bytes, filetype="pdf")
    out = fitz.open()
    for page in src:
        pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), colorspace=fitz.csGRAY)
        np = out.new_page(width=page.rect.width, height=page.rect.height)
        np.insert_image(np.rect, stream=pix.tobytes("png"))
    return out.tobytes()


def make_bills(out_dir: Path) -> list[dict]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    bills = []
    i = 0
    for family in ("emfuleni", "centlec", "ekurhuleni"):
        for k in range(4):
            g = gold_for(family, i)
            nmd_page2 = family == "centlec" and k == 2
            scanned = family == "emfuleni" and k == 3
            data = render(family, g, nmd_page2=nmd_page2)
            if scanned:
                data = to_scanned(data)
            name = f"{family}-{g['account_no'].replace(' ', '')}{'-scan' if scanned else ''}.pdf"
            (out_dir / name).write_bytes(data)
            bills.append({"file": str(out_dir / name), "name": name, "family": family, "gold": g, "scanned": scanned, "nmd_page": 2 if nmd_page2 else 1})
            i += 1
    return bills


def make_vat_mismatch(out_dir: Path) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    g = gold_for("ekurhuleni", 40, vat_mismatch=True)
    data = render("ekurhuleni", g)
    name = "ekurhuleni-vat-mismatch.pdf"
    (out_dir / name).write_bytes(data)
    return {"file": str(out_dir / name), "name": name, "family": "ekurhuleni", "gold": g, "scanned": False, "vat_mismatch": True}


if __name__ == "__main__":
    import sys

    d = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/bills")
    for b in make_bills(d):
        print(b["name"])
    print(make_vat_mismatch(d)["name"])
