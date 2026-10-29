"""PDF → positional text. Every word keeps its page and box so a value can always be pointed at.

    spans = read_document(pdf_bytes, sha)      # cached under DATA_DIR/spans/<sha>.json
    spans["pages"]     -> number of pages
    spans["scanned"]   -> True if any page had to be OCR'd
    spans["lines"]     -> [{page, top, bottom, x0, x1, text, ocr, words:[{t,x0,x1,top,bottom}]}]

Text layer via pdfplumber. A page with no text layer is rendered with PyMuPDF at ~200 dpi and
read with tesseract; OCR word boxes are converted back to PDF points so both kinds of page share
one coordinate system.
"""
from __future__ import annotations

import io
import json
import logging
import re
from pathlib import Path
from typing import Optional

from ..config import settings
from .money import normalise_text

log = logging.getLogger("cbx.pdfstudio")
OCR_DPI = 200
LINE_TOL = 3.0  # points: words whose tops are within this belong to one line


def cache_path(sha: str) -> Path:
    return settings.path("spans") / f"{sha}.json"


def read_document(pdf_bytes: bytes, sha: str, *, force: bool = False) -> dict:
    p = cache_path(sha)
    if p.exists() and not force:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    doc = _read(pdf_bytes)
    p.write_text(json.dumps(doc), encoding="utf-8")
    return doc


def cached(sha: str) -> Optional[dict]:
    p = cache_path(sha)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None
    return None


def quick_text(pdf_bytes: bytes, max_pages: int = 3) -> tuple[str, int]:
    """Fast text (first pages only) + page count, for the upload step and dedupe fingerprints.
    PyMuPDF is ~50× faster than pdfplumber here; the full positional read happens later in the
    pipeline, page by page, with progress. No OCR at upload time — uploads must feel instant."""
    try:
        import pymupdf  # type: ignore

        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
            n = doc.page_count
            texts = [doc[i].get_text("text") or "" for i in range(min(n, max_pages))]
            return "\n".join(texts), n
    except Exception:  # noqa: BLE001
        try:
            import pdfplumber

            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                return "\n".join((p.extract_text() or "") for p in pdf.pages[:max_pages]), len(pdf.pages)
        except Exception:  # noqa: BLE001
            return "", 0


def _read(pdf_bytes: bytes) -> dict:
    import pdfplumber

    lines: list[dict] = []
    scanned = False
    ocr_missing = False
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        n = len(pdf.pages)
        for i, page in enumerate(pdf.pages):
            try:
                words = page.extract_words(x_tolerance=1.5, y_tolerance=2, keep_blank_chars=False)
            except Exception:  # noqa: BLE001
                words = []
            ws = [{"t": w["text"], "x0": float(w["x0"]), "x1": float(w["x1"]), "top": float(w["top"]), "bottom": float(w["bottom"])} for w in words if w["text"].strip()]
            ws = _add_missing_words(pdf_bytes, i, page, ws)
            ocr = False
            if len(ws) < 5:
                # No text layer: this is a scan whether or not OCR is available. Without tesseract the
                # page stays empty and its cells go to the user, but the document is still marked scanned
                # so the UI can say why.
                scanned = True
                ocr_words = _ocr_page(pdf_bytes, i, float(page.width), float(page.height))
                if ocr_words:
                    ws, ocr = ocr_words, True
                else:
                    ocr_missing = True
            lines.extend(group_lines(ws, i + 1, ocr))
    return {"pages": n, "scanned": scanned, "lines": lines, "ocr_missing": ocr_missing}


def _add_missing_words(pdf_bytes: bytes, index: int, page, ws: list[dict]) -> list[dict]:
    """Two readers, because one of them goes quiet on some documents.

    pdfplumber is the primary reader. On a municipal statement we measured it returned the VALUES on
    the page and none of the LABELS — 237 words where PyMuPDF found 341, with the whole left-hand
    label block missing (`ACCOUNT NUMBER`, `ACCOUNT DATE`, `TAX INVOICE NO`). A profile cannot be
    written against text that is not there, so every word PyMuPDF finds that pdfplumber did not is
    added back. Words already read are never replaced, so nothing that works today changes; a page
    that is rotated, or a PyMuPDF that is not installed, is left exactly as pdfplumber read it."""
    try:
        if int(getattr(page, "rotation", 0) or 0) % 360 != 0:
            return ws
        try:
            import pymupdf  # type: ignore
        except ImportError:  # pragma: no cover
            import fitz as pymupdf  # type: ignore

        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
            got = doc[index].get_text("words")
    except Exception as e:  # noqa: BLE001 — a second opinion must never fail the read
        log.debug("second text reader unavailable on page %s: %s", index + 1, e)
        return ws
    extra = []
    for x0, top, x1, bottom, text, *_rest in got:
        t = str(text).strip()
        if not t:
            continue
        cx, cy = (float(x0) + float(x1)) / 2.0, (float(top) + float(bottom)) / 2.0
        if any(w["x0"] - 1 <= cx <= w["x1"] + 1 and w["top"] - 1 <= cy <= w["bottom"] + 1 for w in ws):
            continue  # pdfplumber already read this one
        extra.append({"t": t, "x0": float(x0), "x1": float(x1), "top": float(top), "bottom": float(bottom)})
    if extra:
        log.info("page %s: %s word(s) pdfplumber did not return were read with PyMuPDF", index + 1, len(extra))
    return ws + extra


def group_lines(words: list[dict], page_no: int, ocr: bool) -> list[dict]:
    words = sorted(words, key=lambda w: (round(w["top"]), w["x0"]))
    lines: list[dict] = []
    cur: list[dict] = []
    for w in words:
        if cur and abs(w["top"] - cur[0]["top"]) > LINE_TOL and abs(w["bottom"] - cur[0]["bottom"]) > LINE_TOL:
            lines.append(_mk_line(cur, page_no, ocr))
            cur = []
        cur.append(w)
    if cur:
        lines.append(_mk_line(cur, page_no, ocr))
    lines.sort(key=lambda l: (l["page"], l["top"], l["x0"]))
    return lines


def _mk_line(ws: list[dict], page_no: int, ocr: bool) -> dict:
    ws = sorted(ws, key=lambda w: w["x0"])
    return {
        "page": page_no,
        "top": min(w["top"] for w in ws),
        "bottom": max(w["bottom"] for w in ws),
        "x0": ws[0]["x0"],
        "x1": ws[-1]["x1"],
        "text": " ".join(w["t"] for w in ws),
        "ocr": ocr,
        "words": ws,
    }


def _ocr_page(pdf_bytes: bytes, index: int, width_pt: float, height_pt: float) -> list[dict]:
    try:
        try:
            import pymupdf as fitz
        except ImportError:  # pragma: no cover
            import fitz
        import pytesseract

        from ..config import settings as _st

        if _st.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = _st.tesseract_cmd
        from PIL import Image
    except Exception as e:  # noqa: BLE001
        log.warning("OCR libraries not available: %s", e)
        return []
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc[index]
        zoom = OCR_DPI / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT, config="--psm 6")
    except Exception as e:  # noqa: BLE001
        log.warning("OCR failed on page %s: %s", index + 1, e)
        return []
    out = []
    scale = 72.0 / OCR_DPI
    for i, t in enumerate(data["text"]):
        t = (t or "").strip()
        if not t:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = 0
        if conf < 0:
            continue
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        out.append({"t": t, "x0": x * scale, "x1": (x + w) * scale, "top": y * scale, "bottom": (y + h) * scale, "conf": conf})
    return out


def render_page_png(pdf_bytes: bytes, page_no: int, zoom: float = 1.6) -> bytes:
    try:
        import pymupdf as fitz
    except ImportError:  # pragma: no cover
        import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    if page_no < 1 or page_no > len(doc):
        raise ValueError("no such page")
    pix = doc[page_no - 1].get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    return pix.tobytes("png")


# ---- helpers used by extraction / verification ------------------------------------------
def page_lines(spans: dict, page: Optional[int] = None) -> list[dict]:
    if page is None:
        return spans["lines"]
    return [l for l in spans["lines"] if l["page"] == page]


def page_text(spans: dict, page: Optional[int] = None) -> str:
    return "\n".join(l["text"] for l in page_lines(spans, page))


def doc_norm_text(spans: dict) -> str:
    return normalise_text(page_text(spans))


def contains_verbatim(spans: dict, quote: str) -> bool:
    """Provenance: is `quote` in the document, ignoring case and whitespace?"""
    q = normalise_text(quote)
    return bool(q) and q in doc_norm_text(spans)


def top_tokens(spans: dict, frac: float = 0.35) -> list[str]:
    """Label-like tokens from the top part of page 1 (used for the layout fingerprint)."""
    p1 = page_lines(spans, 1)
    if not p1:
        return []
    ymax = max(l["bottom"] for l in p1)
    cut = ymax * frac
    toks = []
    for l in p1:
        if l["top"] <= cut:
            for w in l["words"]:
                t = re.sub(r"[^a-z]", "", w["t"].lower())
                if len(t) >= 3:
                    toks.append(t)
    return toks
