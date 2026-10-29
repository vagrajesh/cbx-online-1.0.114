"""Practice data for the lessons: two entities nothing real depends on, and PDFs made on the spot.

The entities are real rows with `is_demo=True`, visible to everyone with the tool and to nobody
outside training mode (`visible_entities(demo=...)`). Files filed against them go into the warehouse
like any other; `reset()` throws the practice slate away and lays a clean one.

The PDFs are generated, not shipped: a "certificate" with a readable expiry, one with no date at all
(so the lesson can show what C-EXA does when she cannot read one), and three months of a utility bill
for the PDF Studio lesson. Being generated they hold no real account, no real amount, no real name.
"""
from __future__ import annotations

import io
from datetime import date, timedelta

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from sqlalchemy.orm import Session

from ..audit import log
from ..docwatch import service as S
from ..models import User, WatchedDoc
from ..models.docs import Entity

DEMO_ENTITIES = [
    ("Training Co (practice)", "A practice entity for lessons. Nothing here is real."),
    ("Training Site (practice)", "A second practice entity, so the grid has more than one row."),
]


def ensure_entities(db: Session, me: User) -> list[Entity]:
    """The practice entities, created on first use. Cells for every active document type come with
    them (sync_matrix), exactly as for a real entity."""
    out = []
    for name, notes in DEMO_ENTITIES:
        e = db.query(Entity).filter(Entity.name == name).one_or_none()
        if e is None:
            e = Entity(name=name, notes=notes, team_id=None, is_demo=True, created_by_id=me.id)
            db.add(e)
            db.flush()
        elif not e.is_demo:
            e.is_demo = True  # someone renamed a real entity to this by hand — it is practice now
        if e.archived:
            e.archived, e.archived_at, e.archived_reason = False, None, ""
        out.append(e)
    S.sync_matrix(db, me, entities=out)
    db.flush()
    return out


def reset(db: Session, me: User) -> dict:
    """Wipe what people filed against the practice entities and lay them out fresh. Files stay in the
    warehouse (they are somebody's uploads, and the warehouse is append-only by design); the cells
    forget them."""
    ents = db.query(Entity).filter(Entity.is_demo.is_(True)).all()
    cleared = 0
    for e in ents:
        for d in db.query(WatchedDoc).filter(WatchedDoc.entity_id == e.id).all():
            if d.file_id or d.expiry or d.history:
                cleared += 1
            d.file_id, d.expiry, d.history, d.status_note = None, None, [], ""
            for attr in ("read_at", "read_by", "no_expiry", "required"):
                if hasattr(d, attr) and attr in ("read_at", "read_by"):
                    setattr(d, attr, None)
            if hasattr(d, "no_expiry"):
                d.no_expiry = False
            if hasattr(d, "required"):
                d.required = True
        e.archived, e.archived_at, e.archived_reason = False, None, ""
    db.flush()
    ensure_entities(db, me)
    log(db, "training_reset", actor=me, detail=f"{len(ents)} practice entities laid out fresh · {cleared} cell(s) cleared")
    return {"entities": len(ents), "cleared": cleared}


# ---- generated PDFs --------------------------------------------------------------------------------

def _pdf(lines: list[str], *, title: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(title)
    y = 800
    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, y, lines[0])
    y -= 28
    c.setFont("Helvetica", 11)
    for ln in lines[1:]:
        c.drawString(50, y, ln)
        y -= 18
    c.showPage()
    c.save()
    return buf.getvalue()


def _dmy(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def fire_certificate() -> bytes:
    """Expires in about seven months — comfortably in date, so the dot goes green."""
    until = date.today() + timedelta(days=210)
    return _pdf([
        "CERTIFICATE OF COMPLIANCE — FIRE EQUIPMENT",
        "TRAINING DOCUMENT · not a real certificate",
        "Site: Training Co (practice), 1 Example Road, Johannesburg",
        "Client: CBX-Connect training",
        "Issued by: A. Practitioner, Registered Person 00000",
        f"Date of issue: {_dmy(date.today() - timedelta(days=155))}",
        f"Valid until: {_dmy(until)}",
        "All extinguishers, hose reels and signage inspected and tagged.",
    ], title="Training — fire certificate")


def tax_clearance_expiring() -> bytes:
    """Expires in twelve days — inside the warning window, so the dot goes amber."""
    until = date.today() + timedelta(days=12)
    return _pdf([
        "TAX COMPLIANCE STATUS — PIN",
        "TRAINING DOCUMENT · not a real certificate",
        "Taxpayer: Training Site (practice)",
        "Reference: 0000000000",
        f"Issued: {_dmy(date.today() - timedelta(days=353))}",
        f"Valid until: {_dmy(until)}",
        "This letter confirms compliance status at the date of issue.",
    ], title="Training — tax clearance")


def certificate_without_date() -> bytes:
    """No date anywhere — the lesson uses it to show the 'C-EXA couldn't read the expiry' path."""
    return _pdf([
        "PUBLIC LIABILITY — CONFIRMATION OF COVER",
        "TRAINING DOCUMENT · not a real policy",
        "Insured: Training Co (practice)",
        "Policy: TRAIN-0000",
        "Cover: R 10 000 000 any one event.",
        "Renewal terms to be confirmed by the broker.",
    ], title="Training — confirmation of cover")


def utility_bill(month: int) -> bytes:
    """One month of a made-up municipal bill; three of them make a batch."""
    today = date.today().replace(day=1)
    period = (today - timedelta(days=30 * (3 - month))).strftime("%B %Y")
    kwh = 17800 + month * 640
    kl = 190 + month * 9
    elec = kwh * 2.30
    water = kl * 29.20
    total = elec + water + 1212.00 + 2066.10
    return _pdf([
        "CITY OF EXAMPLE METROPOLITAN MUNICIPALITY",
        "TRAINING DOCUMENT · TAX INVOICE / STATEMENT OF ACCOUNT",
        "Account number: 0000 0000 00",
        f"Period: {period}",
        "Property: 1 Example Road, Johannesburg",
        f"Electricity  kWh consumed  {kwh:,}   R {elec:,.2f}".replace(",", " "),
        f"Water  kl consumed  {kl}   R {water:,.2f}".replace(",", " "),
        "Refuse removal   R 1 212.00",
        "Sewer   R 2 066.10",
        f"TOTAL DUE   R {total:,.2f}".replace(",", " "),
        f"Due date: {_dmy(today + timedelta(days=20))}",
    ], title=f"Training — utility bill {period}")


FILES = {
    "fire-certificate.pdf": ("Fire certificate (in date)", fire_certificate),
    "tax-clearance.pdf": ("Tax clearance (expiring soon)", tax_clearance_expiring),
    "cover-no-date.pdf": ("Confirmation of cover (no date on it)", certificate_without_date),
    "bill-1.pdf": ("Utility bill · month 1", lambda: utility_bill(1)),
    "bill-2.pdf": ("Utility bill · month 2", lambda: utility_bill(2)),
    "bill-3.pdf": ("Utility bill · month 3", lambda: utility_bill(3)),
}
