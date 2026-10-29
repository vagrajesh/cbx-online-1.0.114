"""Inbox → master sheet pipeline. Every 30 s C-EXA looks at new files in people's Inbox folders,
works out what each one is (municipal bill, vendor invoice, SAP bill list, Eskom demand …), says
which master sheet it belongs to and hands it to PDF Studio when a profile exists for that type.
Scanned PDFs are OCR'd first. Nothing here is a model call — plain heuristics, so it is cheap and
deterministic; PDF Studio does the careful reading afterwards."""
from __future__ import annotations

import logging
import re
from typing import Optional

from sqlalchemy.orm import Session

from ..audit import log
from ..db import session_scope
from ..jobs import routine
from ..models import Folder, MasterSheet, StoredFile, User
from . import storage
from .extract import ocr_pdf, pdf_text

logger = logging.getLogger("cbx.pipeline")

MUNICIPALITIES = [
    "Ekurhuleni", "City of Johannesburg", "Johannesburg", "City of Tshwane", "Tshwane", "City of Cape Town", "Cape Town",
    "eThekwini", "Centlec", "Mangaung", "Buffalo City", "Nelson Mandela Bay", "Emfuleni", "Msunduzi", "Polokwane",
    "Mbombela", "Rustenburg", "Sol Plaatje", "Stellenbosch", "Drakenstein", "George Municipality", "Matjhabeng",
    "Mogale City", "Midvaal", "Lesedi", "Govan Mbeki", "Steve Tshwete", "uMhlathuze", "Newcastle", "KwaDukuza",
    "Ray Nkonyeni", "Overstrand", "Swartland", "Saldanha Bay", "Umgeni Water", "Rand Water", "City Power",
]
_MUNI_RE = re.compile("|".join(re.escape(m) for m in sorted(MUNICIPALITIES, key=len, reverse=True)), re.I)
_CANON = {m.lower(): m for m in MUNICIPALITIES}

# detected type → master sheet it belongs to
GOES_TO = {
    "municipal": "Municipal accounts",
    "eskom": "Municipal accounts",
    "vendor": "Vendor payments",
    "sap": "Vendor payments",
}


def detect(text: str, filename: str = "") -> dict:
    """Return {"type": municipal|eskom|vendor|sap|unknown|scanned, "label": "Ekurhuleni bill", "vendor": ...}."""
    t = (text or "").strip()
    if not t:
        return {"type": "scanned", "label": "Unknown · scanned"}
    low = t.lower()
    head = t[:6000]
    # SAP bill list (csv export)
    if filename.lower().endswith(".csv") or "bill list" in low[:2000]:
        if re.search(r"\b(sap|billing document|bill\s*list|vendor code|purchase order)\b", low[:4000]):
            return {"type": "sap", "label": "SAP bill list"}
    # Eskom demand / kVA
    if "eskom" in low and re.search(r"\bkva\b|maximum demand|notified maximum demand|nmd", low):
        return {"type": "eskom", "label": "Eskom demand"}
    if "eskom" in low[:3000]:
        return {"type": "eskom", "label": "Eskom bill"}
    # Municipal bill
    m = _MUNI_RE.search(head)
    if m and re.search(r"\b(account|acc(ount)?\s*no|rates|refuse|sewer|electricity|water|kwh|kl|amount due|total due|municipal)\b", low):
        name = _CANON.get(m.group(0).lower(), m.group(0))
        return {"type": "municipal", "label": f"{name} bill", "municipality": name}
    # Vendor invoice
    if re.search(r"\btax\s+invoice\b", low):
        vendor = _vendor_name(t)
        return {"type": "vendor", "label": "Vendor invoice" + (f" · {vendor}" if vendor else ""), "vendor": vendor}
    if m:
        name = _CANON.get(m.group(0).lower(), m.group(0))
        return {"type": "municipal", "label": f"{name} document", "municipality": name}
    return {"type": "unknown", "label": "Unknown"}


def _vendor_name(text: str) -> str:
    for line in text.splitlines()[:12]:
        s = line.strip()
        if not s or re.search(r"tax\s+invoice|invoice\s*(no|number|#)|date|page", s, re.I):
            continue
        if re.search(r"\b(pty|ltd|cc|inc|\(pty\)|limited)\b", s, re.I) or (len(s) < 48 and s[0].isupper() and not any(ch.isdigit() for ch in s)):
            return s[:80]
    return ""


def _sheet_named(db: Session, name: str) -> Optional[MasterSheet]:
    if not name:
        return None
    return db.query(MasterSheet).filter(MasterSheet.name == name, MasterSheet.is_active.is_(True)).one_or_none()


def _inbox_candidates(db: Session, limit: int = 20) -> list[StoredFile]:
    return (
        db.query(StoredFile)
        .join(Folder, Folder.id == StoredFile.folder_id)
        .filter(Folder.kind == "sub", Folder.name == "Inbox", StoredFile.library_copy_of.is_(None), StoredFile.source != "result")
        .filter(StoredFile.kind.in_(("pdf", "csv")), StoredFile.pipeline_status.in_(("", "queued")))
        .order_by(StoredFile.created_at)
        .limit(limit)
        .all()
    )


def process_file(db: Session, f: StoredFile) -> StoredFile:
    """Detect one file and decide where it goes. Safe to call from a request (POST /pipeline/run) or the routine."""
    f.pipeline_status = "reading"
    # commit, not flush: SQLite has one writer, and reading a PDF (seconds) or OCR (up to half a minute)
    # must not keep every other save on the server waiting behind this row
    db.commit()
    try:
        data = storage.read_bytes(f)
    except FileNotFoundError:
        f.pipeline_status = "unknown"
        f.detected_as = "File missing on disk"
        return f
    meta = dict(f.meta or {})
    if f.kind == "pdf":
        text, pages = pdf_text(data)
        if pages and not f.pages:
            f.pages = pages
    else:
        text = data.decode("utf-8", errors="ignore")[:200_000]
    d = detect(text, f.name)
    if d["type"] == "scanned" and f.kind == "pdf":
        f.pipeline_status = "ocr"
        db.commit()
        ocr_text = ocr_pdf(data)
        meta["ocr"] = True
        if ocr_text:
            meta["ocr_chars"] = len(ocr_text)
            if not f.text_sha:
                f.text_sha = storage.text_fingerprint(ocr_text)
            d = detect(ocr_text, f.name)
            if d["type"] == "scanned":
                d = {"type": "unknown", "label": "Unknown · scanned"}
        else:
            d = {"type": "unknown", "label": "Unknown · scanned"}
    f.detected_as = d["label"]
    meta["detected_type"] = d["type"]
    sheet = _sheet_named(db, GOES_TO.get(d["type"], ""))
    f.goes_to = sheet.name if sheet else ""
    if d["type"] == "unknown":
        f.pipeline_status = "unknown"
    elif sheet is None:
        f.pipeline_status = "detected"
    else:
        f.pipeline_status = "detected"
        owner = db.get(User, f.uploaded_by_id) if f.uploaded_by_id else None
        try:
            from ..pdfstudio.service import enqueue_from_warehouse  # type: ignore

            batch = enqueue_from_warehouse(db, f, owner)
            if batch is not None:
                meta["batch_id"] = getattr(batch, "id", None)
                f.pipeline_status = "added"
        except ImportError:
            pass
        except Exception as e:  # noqa: BLE001 — hand-off problems are C-EXA's to sort out, not the uploader's
            logger.warning("pipeline hand-off failed for %s: %s", f.name, e)
            meta["handoff_error"] = str(e)[:200]
    f.meta = meta
    db.add(f)
    log(db, "pipeline_detected", actor_label="axl", target=f.name, detail=f"{f.detected_as} → {f.goes_to or 'no sheet'} ({f.pipeline_status})")
    db.flush()
    return f


def run_once(db: Session, limit: int = 20) -> list[StoredFile]:
    out = []
    for f in _inbox_candidates(db, limit):
        try:
            out.append(process_file(db, f))
        except Exception as e:  # noqa: BLE001
            logger.exception("pipeline failed on %s", f.name)
            f.pipeline_status = "unknown"
            f.detected_as = "Could not read"
            f.meta = {**(f.meta or {}), "error": str(e)[:200]}
            db.add(f)
    db.flush()
    return out


@routine("warehouse-inbox", 30)
def inbox_routine() -> None:
    with session_scope() as db:
        run_once(db)
