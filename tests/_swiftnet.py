"""Made-up Swiftnet PDFs laid out like the real ones: a label, then its value further right on the same
baseline; numbered comment blocks to the right of their label; the reference line in the footer."""
import io

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

REF = "9130 - 5183 - 70204797 - TIERVLEI EXCHANGE - (01.08.2026 - 31.07.2031) - (7708) - R - VDC"


def _pdf(draw) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica", 8)
    draw(c)
    c.save()
    return buf.getvalue()


def quote(*, ground_comments=None, antenna_comments=None, extra_ground=False, start="1 August 2026", standby="R 5 615.70", odd=None) -> bytes:
    """odd (2.10.10): the header drawn the way some quotes are — "cells": label, colon and value in separate cells,
    the value right of a coloured box, the PID on the next line; "tight": capitals, no spaces round the colon;
    "split": the minus sign its own run, "Site PID*:" with the value below, the region in a box."""
    ground_comments = ground_comments if ground_comments is not None else [
        "1) 70205157* Ground space for 35m Vodacom mast inside the Swiftnet yard",
        "2) 70205157* Vodacom container plinth inside the Swiftnet yard",
        "3) 70204797* Vodacom optic fibre route inside the Swiftnet yard"]
    antenna_comments = antenna_comments if antenna_comments is not None else ["1) 70205157* Three panel antennas at 36m on the north leg",
                                                                              "2) 70205157* One 0.6m dish at 30m"]

    def d(c):
        y = 800
        c.setFont("Helvetica-Bold", 11)
        c.drawString(40, y, "Quotation for Co-location")
        c.setFont("Helvetica", 8)
        y -= 24

        def kv(label, value, x=40, vx=None):
            c.drawString(x, y, label)
            if value:  # the value just after its label, as on the real quotes
                c.drawString(x + c.stringWidth(label, "Helvetica", 8) + 8, y, value)
        def box(x, w=90):  # a coloured box, as the quotes put round an input
            c.setFillColorRGB(0.85, 0.92, 1)
            c.rect(x, y - 3, w, 11, fill=1, stroke=0)
            c.setFillColorRGB(0, 0, 0)

        for label, value in (("Co-locate ID :", "7708"), ("Type of Lease :", "Renewal"), ("Contract No :", "70204797"), ("Company Name :", "Vodacom (Pty) Ltd"),
                             ("Site Name :", "TIERVLEI EXCHANGE")):
            kv(label, value)
            y -= 13
        if odd == "cells":
            c.drawString(40, y, "Swiftnet Region")
            c.drawString(150, y, ":")
            box(160)
            c.drawString(262, y, "Northern")
            y -= 13
            c.drawString(40, y, "Latitude")
            c.drawString(150, y, ":")
            box(160)
            c.drawString(270, y, "-26.13194")
            c.drawString(330, y, "Longitude")
            c.drawString(400, y, ":")
            box(410, 60)
            c.drawString(480, y, "28.203140")
            y -= 13
        elif odd == "tight":
            kv("SWIFTNET REGION:", "Northern")
            y -= 13
            c.drawString(40, y, "LATITUDE:-26.13194")
            c.drawString(300, y, "LONGITUDE  :   28.203140")
            y -= 13
        elif odd == "split":
            c.drawString(40, y, "Swiftnet Region :")
            box(200)
            c.drawString(300, y, "Northern")
            y -= 13
            c.drawString(40, y, "Latitude :")
            c.drawString(150, y, "-")
            c.drawString(154, y, "26.13194")
            c.drawString(300, y, "Longitude :")
            box(360, 50)
            c.drawString(450, y, "28.203140")
            y -= 13
        else:
            kv("Swiftnet Region :", "Western")
            y -= 13
            kv("Latitude :", "33 55 37")
            kv("Longitude :", "18 35 19", x=300, vx=380)
            y -= 13
        kv("Structure Name :", "TVI: Vodacom Mast")
        y -= 13
        kv("Structure Total HAGL ** :", "35")
        y -= 13
        if odd == "cells":
            c.drawString(40, y, "Site PID*")
            c.drawString(150, y, ":")
            c.drawString(300, y, "Structure ID :")
            c.drawString(420, y, "7295")
            y -= 11
            box(160, 40)
            c.drawString(165, y, "7295")
            y -= 13
        elif odd == "tight":
            kv("SITE PID*:", "7295")
            y -= 13
        elif odd == "split":
            c.drawString(40, y, "Site PID*:")
            y -= 10
            box(48, 40)
            c.drawString(52, y, "7295")
            y -= 13
            kv("Structure ID :", "7295")
            y -= 13
        else:
            kv("Site PID * :", "5431")
            y -= 13
        for label, value in (("Structure Category :", "Sectional Mast"), ("Site Owner :", "Swiftnet"), ("Contract Start Date :", start)):
            kv(label, value)
            y -= 13
        y -= 8
        c.drawString(40, y, "1 Sector Antenna Requirements")
        y -= 13
        kv("HAGL ** :", "36.00", vx=100)
        kv("Number of Antennas :", "3", x=200, vx=300)
        y -= 13
        kv("T-Area :", "1.20", vx=100)
        kv("Total 1 :", "R 1 500.00", x=200, vx=300)
        y -= 13
        c.drawString(40, y, "4 Parabolic/MW Requirements")
        y -= 13
        kv("HAGL ** :", "30.00", vx=100)
        kv("Number of Units :", "1", x=200, vx=300)
        y -= 13
        kv("T-Area :", "0.50", vx=100)
        kv("Total 2 :", "R 650.00", x=200, vx=300)
        y -= 13
        kv("Antenna - Charge :", "R 2 150.00")
        y -= 13
        c.drawString(40, y, "Antenna Comments :")
        for t in antenna_comments:
            c.drawString(150, y, t)
            y -= 13
        y -= 8
        c.drawString(40, y, "Equipment Accommodation")
        y -= 13
        items = [("Ground Space", "25.00", "R 2 485.25"), ("Container plinth", "12.00", "R 1 192.92"), ("Fibre route", "4.80", "R 477.17")]
        if extra_ground:
            items.append(("Cable tray", "1.00", "R 100.00"))
        for n, (name, area, amt) in enumerate(items, 1):
            c.drawString(50, y, str(n))
            c.drawString(80, y, name)
            y -= 13
            c.drawString(80, y, "Area in m²")
            c.drawString(300, y, area)
            c.drawString(450, y, amt)
            y -= 13
        kv("G-Total Area in m² & Charge :", "41.80")
        c.drawString(450, y, "R 4 155.34")
        y -= 13
        c.drawString(40, y, "Comments :")
        for t in ground_comments:
            c.drawString(150, y, t)
            y -= 13
        kv("Accommodation Total :", "R 4 155.34", vx=200)
        y -= 18
        c.drawString(40, y, "Electricity & Standby")
        y -= 13
        for label, value in (("AC Electricity Required :", "Yes"), ("AC Electricity Phase Required :", "3 Phase"), ("AC Circuit Breaker Required in Amps :", "40 Amps"),
                             ("Standby Charge:", standby), ("Electricity Charge:", "R 0.00"), ("Total Power - Charge :", standby),
                             ("Road Length, Km :", "1.5"), ("Road - Charge :", "R 120.00")):
            kv(label, value, vx=220)
            y -= 13
        c.drawString(40, y, "Application & Site Survey Fee")
        c.drawString(260, y, "Once-Off Charge (Excl VAT) :")
        c.drawString(260 + c.stringWidth("Once-Off Charge (Excl VAT) :", "Helvetica", 8) + 8, y, "R 1 000.00")
        y -= 13
        c.drawString(40, 25, REF)
    return _pdf(d)


def ntp(scope: str = "") -> bytes:
    def d(c):
        y = 800
        c.drawString(40, y, "NOTICE TO PROCEED")
        y -= 20
        c.drawString(40, y, "Dear Vodacom (Pty) Ltd")
        y -= 20
        for label, left, right in (("Swiftnet Site Name :", "TIERVLEI EXCHANGE", "Beaconvale"), ("Swiftnet Site ID :", "5431", "BS 2981"), ("Swiftnet Region :", "Western", "Western")):
            c.drawString(40, y, label)
            c.drawString(40 + c.stringWidth(label, "Helvetica", 8) + 8, y, left)
            c.drawString(360, y, right)
            y -= 14
        c.drawString(40, y, "Application Ref No :")
        c.drawString(125, y, "7708")
        y -= 20
        if scope:  # the NTP's table of work: "Other | Description | …"
            c.drawString(40, y, "Other")
            c.drawString(110, y, "Description")
            c.drawString(170, y, scope)
    return _pdf(d)


def sow(lines=("Install 3 x panel antennas at 36m on the north leg", "and one 0.6m dish at 30m.", "Run fibre & RF cables in the existing trench.")) -> bytes:
    """2.10.10: a Scope of Work as Swiftnet sends one: its title, the site, the scope paragraph, the signing line."""
    def d(c):
        y = 800
        c.setFont("Helvetica-Bold", 12)
        c.drawString(40, y, "SCOPE OF WORK")
        c.setFont("Helvetica", 8)
        y -= 24
        c.drawString(40, y, "Site Name : TIERVLEI EXCHANGE")
        y -= 13
        c.drawString(40, y, "Co-locate ID : 7708")
        y -= 20
        c.setFont("Helvetica-Bold", 9)
        c.drawString(40, y, "Scope of Work:")
        c.setFont("Helvetica", 8)
        y -= 13
        for t in lines:
            c.drawString(50, y, t)
            y -= 11
        y -= 20
        c.drawString(40, y, "Signed: ____________________")
    return _pdf(d)


def agreement(rows, *, align="top") -> bytes:
    """2.10.10: an earlier, signed Site Schedule Agreement with its 6.1 table — rows of (item, requirement,
    [description lines], amount); a description wrapped over several lines, the other cells at the top of the row
    (Word's default) or in its middle."""
    def d(c):
        y = 800
        c.setFont("Helvetica-Bold", 12)
        c.drawString(200, y, "SITE SCHEDULE AGREEMENT")
        c.setFont("Helvetica", 8)
        y -= 14
        c.drawString(250, y, "Renewal")
        y -= 20
        c.drawString(40, y, "Site name: TIERVLEI EXCHANGE")
        y -= 30
        xs = (45, 130, 215, 470)
        for x, h in zip(xs, ("Leased Item", "Requirement", "Comment/Equipment Description", "Amount (excl VAT)")):
            c.drawString(x, y, h)
        y -= 6
        for item, req, desc, amt in rows:
            n = max(1, len(desc))
            top = y - 3 - 8  # the cell's padding, then the first line's baseline
            mid = top - (n - 1) * 10 / 2
            at = top if align == "top" else mid
            c.line(40, y, 555, y)
            c.drawString(xs[0], at, item)
            c.drawString(xs[1], at, req)
            for k, t in enumerate(desc):
                c.drawString(xs[2], top - k * 10, t)
            c.drawString(xs[3], at, amt)
            y = top - (n - 1) * 10 - 6
        c.line(40, y, 555, y)
        y -= 11
        c.drawString(45, y, "Total Rental Charges Payable (Excl. VAT) per Month")
        c.drawString(470, y, "R 0.00")
    return _pdf(d)
