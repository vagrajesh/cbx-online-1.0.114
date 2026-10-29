"""The CBX Harvester 6 profile engine, inside PDF Studio (1.0.75).

Edwin's municipal bill profiles (CBX Harvester 6, Aug 2026) read a bill with plain regex rules over the
PDF's text: a fingerprint picks the municipality, sections cut the bill per service, field rules pull
the values, `computes` derive the rest, and a `canonical` map lands every electricity bill in the same
28 columns. The functions below are the Harvester's own (pdf_harvester.py), copied as they are so the
profiles read exactly as they did there; reading from bytes (`read_pdf` accepts bytes) is the only change.
Nothing here branches on a municipality — all of that lives in the JSON profiles.

Left out on purpose: the Harvester's GUI, its AI double-check and its OCR of scanned pages (PDF Studio has
its own OCR and learning path for bills no profile knows).
"""
# flake8: noqa  — copied as written in CBX Harvester 6
from __future__ import annotations

import os
import re
from decimal import Decimal, InvalidOperation

try:
    import pymupdf as fitz  # PyMuPDF (the Harvester imported it as `fitz`)
    HAS_FITZ = True
except ImportError:  # pragma: no cover
    try:
        import fitz
        HAS_FITZ = True
    except ImportError:
        HAS_FITZ = False

SERVICES = ["electricity", "water", "sewerage", "refuse", "rates", "sundry", "smart_meter"]
SERVICE_LABELS = {
    "electricity": "Electricity", "water": "Water", "sewerage": "Sewerage",
    "refuse": "Refuse", "rates": "Rates", "sundry": "Sundry",
    "smart_meter": "Smart Meter",
}
# Pseudo-service for files that could not be harvested. Kept strictly
# separate from SERVICES so it never mixes into real tables or exports.
UNRECOGNIZED = "unrecognized"

# =============================================================================
#  CANONICAL ELECTRICITY SCHEMA  (the ONE template every profile maps into)
# =============================================================================
# Every municipality's electricity data lands in these exact columns, in this
# order — the 24-column "General" template from Harvester_Template.xlsx. A
# profile fills its own native fields (whatever the bill calls them), then a
# per-profile "canonical" map aliases native -> canonical so the General view
# is identical for CoJ, CoCT, Eskom, CoT, Ekurhuleni, eThekwini and every
# future munic. Adding a munic never changes these columns.
#
#   canonical_field            template column
CANONICAL_ELEC = [
    ("site_name",        "Site Name"),
    ("billing_date",     "Billing Date"),
    ("municipality",     "Munic/Eskom"),
    ("tariff_name",      "Tariff Name"),
    ("account_number",   "Account Number"),
    ("meter",            "Meter number"),
    ("reading_type",     "Actual/Estimate"),
    ("read_start",       "Start Date"),
    ("read_end",         "End Date"),
    ("start_reading_kwh","Start Reading kWh"),
    ("end_reading_kwh",  "End Reading kWh"),
    ("total_kwh",        "Total kWh"),
    ("peak_kwh",         "Peak kWh"),
    ("standard_kwh",     "Standard kWh"),
    ("offpeak_kwh",      "Off Peak kWh"),
    ("demand_kva",       "Demand kVA"),
    ("nmd_kva",          "NMD kVA"),
    ("peak_r",           "Peak Rands"),
    ("standard_r",       "Standard Rands"),
    ("offpeak_r",        "Off Peak Rands"),
    ("demand_r",         "Demand Rands"),
    ("nmd_r",            "NMD Rands"),
    ("other_r",          "Other Charges Rands"),
    ("total_ex_vat_r",   "Total Charge Ex VAT Rands"),
    # July 2026 (Edwin): consumer securities, filled only where the bill
    # prints them — CoT/CoJ/eThekwini/Eskom print a deposit (Eskom calls it
    # SECURITY HELD), Ekurhuleni splits Cash / Guarantee, eThekwini prints
    # both a Deposit and a Guarantee. Blank everywhere else, by design.
    ("deposit_r",        "Deposit"),
    ("cash_r",           "Cash"),
    ("guarantee_r",      "Guarantee"),
    ("security_held_r",  "Security Held"),   # Eskom's deposit, own column
]

# Value translator: Afrikaans (and other) source terms -> English, applied to
# text fields as data flows into canonical columns (e.g. CoT bills are
# Afrikaans). Extend freely; matching is case-insensitive, whole-value first
# then word-by-word for multi-word cells.
TRANSLATE = {
    # reading type
    "werklike": "Actual", "geskatte": "Estimated", "geraamde": "Estimated", "beraamde": "Estimated", "estimate": "Estimated", "estimated": "Estimated", "actual": "Actual",
    # single-letter reading codes (KwaDukuza/Ugu style). SAFE ONLY via the
    # exact-match path — the word-replacement fallback skips 1-char terms.
    "a": "Actual", "e": "Estimated",
    "meerdere verbruiksmeters": "Multiple cons. meters",
    "meerdere meters": "Multiple cons. meters",
    # TOU bands
    "spits": "Peak", "niespits": "Off-Peak", "nie-spits": "Off-Peak",
    "standaard": "Standard",
    # services / charge words (for tariff/label text)
    "elektrisiteit": "Electricity", "vooruitbetaal": "Prepaid",
    "water": "Water", "sanitasie": "Sanitation", "riool": "Sewerage",
    "vaste heffing": "Fixed Charge", "aanvraagheffing": "Demand Charge",
    "aanvraagheff": "Demand Charge", "energieheffing": "Energy Charge",
    "energ heff": "Energy Charge", "heffing": "Charge",
    "laevolt": "Low Voltage", "aanvraagskaal": "Demand Scale", "l/volt": "Low Voltage", "gebruiktyd": "TOU",
    "verbruik": "Consumption", "huidige": "Current", "vorige": "Previous",
    "lesingdatums": "Reading dates", "dae": "days", "maand": "month",
}


def translate_value(value):
    """Translate a text cell (Afrikaans -> English) for canonical output.
    Tries the whole value first, then translates recognised words in place.
    Numbers/dates pass through untouched."""
    if not value or any(ch.isdigit() for ch in value) and "/" in value:
        return value  # dates
    low = value.strip().lower()
    if low in TRANSLATE:
        return TRANSLATE[low]
    # multi-word phrase replacement (longest keys first)
    out = value
    for term in sorted(TRANSLATE, key=len, reverse=True):
        if len(term) < 2:
            continue  # 1-char codes translate via exact match only
        out = re.sub(r"(?i)\b" + re.escape(term) + r"\b", TRANSLATE[term], out)
    return out


def to_canonical(row, profile):
    """Build the canonical electricity dict for one extracted row using the
    profile's 'canonical' alias map (canonical_field -> native_field or
    list of native fields to try in order). Missing -> ''. Text values are
    translated. This is what the General/template view and exports read."""
    amap = profile.get("canonical", {})
    out = {}
    for cfield, _label in CANONICAL_ELEC:
        src = amap.get(cfield, cfield)  # default: same name
        val = ""
        for name in ([src] if isinstance(src, str) else src):
            v = row.get(name)
            if v not in (None, ""):
                val = v
                break
        out[cfield] = val
    # municipality + translation
    out["municipality"] = profile.get("short_name", out.get("municipality", ""))
    for tf in ("site_name", "tariff_name", "reading_type"):
        if out.get(tf):
            out[tf] = translate_value(out[tf])
    return out


# =============================================================================
# 1. PDF -> RAW TEXT
# =============================================================================

def normalize_text(text):
    """Tame PDF text quirks without destroying layout information.
    NBSP -> space, Windows newlines -> \n. Nothing else."""
    return text.replace("\u00a0", " ").replace("\r\n", "\n").replace("\r", "\n")


def _open(src):
    """CBX: a path (as in the Harvester) or the PDF's bytes."""
    if isinstance(src, (bytes, bytearray)):
        return fitz.open(stream=bytes(src), filetype="pdf")
    return fitz.open(src)


def read_pdf(path, ocr="auto"):
    """Return {'pages': [...], 'full': str, 'warnings': [...], 'ocr': bool,
    'scanned': bool}.
    PyMuPDF plain text with sort=True (reading order). Pages with NO text
    layer are scanned images; 'scanned' flags the document.
    ocr="auto"  - OCR scanned pages inline when Tesseract is available.
    ocr="never" - do NOT OCR (first extraction pass: scans are deferred
                  and the app asks the person afterwards whether to run
                  Tesseract over them)."""
    if not HAS_FITZ:
        raise RuntimeError("PyMuPDF missing. Run:  pip install pymupdf")
    warnings = []
    pages = []
    used_ocr = False
    scanned = False
    with _open(path) as doc:
        if doc.needs_pass:
            warnings.append("PDF is password protected — no text extracted.")
        for i, page in enumerate(doc):
            t = normalize_text(page.get_text("text", sort=True))
            if not t.strip():
                # BEFORE calling it a scan, try the other places PDFs
                # hide text (Edwin's Mbombela case: copyable in the
                # viewer, empty here):
                #   1) plain get_text without sort (some generators break
                #      the sorted path),
                #   2) the word list re-joined in reading order,
                #   3) AcroForm field values + FreeText annotations
                #      (form-filled bills carry the data in widgets that
                #      normal text extraction never sees).
                t = _page_text_fallbacks(page, i, warnings)
            if not t.strip():
                scanned = True
                # Scanned page: OCR fallback when allowed and Tesseract is
                # installed (optional; without it scans land under "Did
                # not pull"). OCR'd rows are ALWAYS red-flagged: OCR can
                # misread digits, so a human must verify against the PDF.
                ot = ""
                if ocr != "never" and _ocr_available():
                    try:
                        tp = page.get_textpage_ocr(dpi=300, full=True)
                        # Tesseract's textpage often streams ONE WORD PER
                        # LINE (top-down), which no profile can match.
                        # Rebuild proper reading order from the word
                        # coordinates instead of trusting the raw text.
                        ot = normalize_text(_ocr_words_to_text(page, tp))
                        if not ot.strip():
                            ot = normalize_text(page.get_text(textpage=tp))
                    except Exception:
                        ot = ""
                if ot.strip():
                    t = ot
                    used_ocr = True
                    warnings.append("Page %d read via OCR — verify "
                                    "values." % (i + 1))
                else:
                    warnings.append("Page %d has no text layer (scanned "
                                    "image?)." % (i + 1))
            pages.append(t)
    full = ("\n\f\n").join(pages)
    if _looks_like_xfa_shell(full) or len(full.strip()) < 40:
        try:
            with _open(path) as d2:
                xfa = _xfa_recover_text(d2)
        except Exception:
            xfa = ""
        if xfa:
            full = xfa
            pages = [xfa]
            scanned = False
            warnings.append("Bill data recovered from the embedded XFA "
                            "form (no Adobe re-save needed) — verify "
                            "against the PDF once.")
    return {"pages": pages, "full": full, "warnings": warnings,
            "ocr": used_ocr, "scanned": scanned}


def _ocr_words_to_text(page, textpage):
    """Reconstruct left-to-right, top-to-bottom text from an OCR
    textpage's word boxes. Words are clustered into lines by their
    vertical midpoint (tolerance scaled from the median word height) and
    sorted by x within a line; horizontal gaps wider than ~2 characters
    become a double space so column-aware patterns (\\s{2,}) keep
    working."""
    words = page.get_text("words", textpage=textpage)
    if not words:
        return ""
    hs = sorted((w[3] - w[1]) for w in words)
    med_h = hs[len(hs) // 2] or 8.0
    tol = max(3.0, med_h * 0.6)
    words = sorted(words, key=lambda w: ((w[1] + w[3]) / 2.0, w[0]))
    lines = []
    cur = []
    cur_mid = None
    for w in words:
        mid = (w[1] + w[3]) / 2.0
        if cur_mid is None or abs(mid - cur_mid) <= tol:
            cur.append(w)
            cur_mid = mid if cur_mid is None else \
                (cur_mid * (len(cur) - 1) + mid) / len(cur)
        else:
            lines.append(cur)
            cur = [w]
            cur_mid = mid
    if cur:
        lines.append(cur)
    out = []
    char_w = max(2.0, med_h * 0.5)
    for ln in lines:
        ln.sort(key=lambda w: w[0])
        parts = []
        prev_end = None
        for w in ln:
            if prev_end is not None and (w[0] - prev_end) > 2 * char_w:
                parts.append(" ")
            parts.append(w[4])
            prev_end = w[2]
        out.append(" ".join(parts).replace("  ", "  "))
    return "\n".join(out)


def _looks_like_xfa_shell(text):
    """Whitespace-proof check for the Adobe XFA placeholder page (the
    phrase can wrap across lines)."""
    flat = " ".join((text or "").lower().split())
    return ("requires adobe reader" in flat or
            "please wait... if this message is not eventually" in flat)


def _xfa_recover_text(doc):
    """Tshwane e-billing PDFs are XFA form SHELLS: the visible page only
    says 'requires Adobe Reader', but the actual bill data travels INSIDE
    the file as an XML 'datasets' package that Adobe renders. Pull that
    XML straight out of the PDF objects and flatten it to 'Field: value'
    lines - no Adobe, no manual re-saving. Returns '' when the file
    carries no usable datasets."""
    import re as _re
    try:
        import xml.etree.ElementTree as ET
    except Exception:
        return ""
    xml_bytes = None
    try:
        for x in range(1, doc.xref_length()):
            try:
                if not doc.xref_is_stream(x):
                    continue
                raw = doc.xref_stream(x)
            except Exception:
                continue
            if not raw:
                continue
            if b"xfa:datasets" in raw or b"<xfa:data" in raw or \
                    (b"<xdp:xdp" in raw and b"datasets" in raw):
                xml_bytes = raw
                break
    except Exception:
        return ""
    if not xml_bytes:
        return ""
    try:
        txt = xml_bytes.decode("utf-8", "replace")
        # keep only the datasets package when a whole XDP came back
        m = _re.search(r"<xfa:datasets[\s\S]*?</xfa:datasets>", txt)
        if m:
            txt = m.group(0)
        # namespaces only get in the way of flattening
        txt = _re.sub(r"<(/?)\w+:", r"<\1", txt)
        txt = _re.sub(r'xmlns(:\w+)?="[^"]*"', "", txt)
        # CBX (1.0.77): prefixed attributes (dd:dataNode="dataGroup") stay "unbound" once the namespaces
        # are gone, so the parse failed and Tshwane's e-billing PDFs looked empty — drop them too
        txt = _re.sub(r'\s[\w.-]+:[\w.-]+="[^"]*"', "", txt)
        root = ET.fromstring(txt)
    except Exception:
        return ""
    lines = []

    def walk(el, depth):
        kids = list(el)
        val = (el.text or "").strip()
        tag = _re.sub(r"[_\-]+", " ", el.tag).strip()
        if not kids:
            if val:
                lines.append("%s: %s" % (tag, val))
            return
        if depth:
            lines.append("")  # group separator
        for k in kids:
            walk(k, depth + 1)
    walk(root, 0)
    out = "\n".join(lines).strip()
    return out if len(out) >= 40 else ""


def _page_text_fallbacks(page, i, warnings):
    """Recover text from pages where the sorted extractor sees nothing
    but a human can select/copy in a viewer. Returns '' only when every
    route is empty (then it really is an image)."""
    try:
        t = normalize_text(page.get_text("text"))
        if t.strip():
            warnings.append("Page %d read via unsorted text fallback."
                            % (i + 1))
            return t
    except Exception:
        pass
    try:
        words = page.get_text("words")
        if words:
            words.sort(key=lambda w: (round(w[3], 1), w[0]))
            lines, cury, buf = [], None, []
            for w in words:
                y = round(w[3], 1)
                if cury is None or abs(y - cury) <= 2:
                    buf.append(w[4])
                else:
                    lines.append(" ".join(buf))
                    buf = [w[4]]
                cury = y
            if buf:
                lines.append(" ".join(buf))
            t = normalize_text("\n".join(lines))
            if t.strip():
                warnings.append("Page %d rebuilt from the word list."
                                % (i + 1))
                return t
    except Exception:
        pass
    try:
        bits = []
        for wdg in (page.widgets() or []):
            for v in (wdg.field_name, wdg.field_value):
                v = str(v or "").strip()
                if v:
                    bits.append(v)
        for an in (page.annots() or []):
            info = an.info or {}
            v = str(info.get("content") or "").strip()
            if v:
                bits.append(v)
        if bits:
            t = normalize_text("\n".join(bits))
            if t.strip():
                warnings.append("Page %d read from form fields / "
                                "annotations." % (i + 1))
                return t
    except Exception:
        pass
    return ""


def _ocr_available(_c=[]):
    """Is Tesseract usable? Checks PATH first, then the standard Windows
    install folders (the winget/UB-Mannheim installer does NOT add itself
    to PATH). When found off-PATH the folder is prepended to PATH and
    TESSDATA_PREFIX is set so PyMuPDF's get_textpage_ocr can find it.
    Result is cached for the session."""
    if _c:
        return _c[0]
    import shutil
    ok = bool(shutil.which("tesseract"))
    if not ok:
        cands = []
        for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                cands.append(os.path.join(base, "Tesseract-OCR",
                                          "tesseract.exe"))
                cands.append(os.path.join(base, "Programs",
                                          "Tesseract-OCR",
                                          "tesseract.exe"))
        for exe in cands:
            if os.path.exists(exe):
                d = os.path.dirname(exe)
                os.environ["PATH"] = d + os.pathsep + \
                    os.environ.get("PATH", "")
                td = os.path.join(d, "tessdata")
                if os.path.isdir(td):
                    os.environ.setdefault("TESSDATA_PREFIX", td)
                ok = True
                break
    _c.append(ok)
    return ok


# =============================================================================
# 2. FINGERPRINT -> PROFILE
# =============================================================================

def identify_profile(text, profiles):
    """Fingerprint = {'all': [...], 'any': [...]} of case-insensitive
    substrings. Every 'all' marker must appear; at least one 'any' marker
    must appear (if 'any' is non-empty). First matching profile wins."""
    up = text.upper()
    for prof in profiles:
        fp = prof.get("fingerprint", {})
        if all(m.upper() in up for m in fp.get("all", [])):
            anys = fp.get("any", [])
            if anys and not any(m.upper() in up for m in anys):
                continue
            # A profile can disqualify itself if a "poison" marker is present
            # (e.g. an unrendered XFA shell that says "requires Adobe Reader").
            rejects = fp.get("reject_if_text", [])
            if any(m.upper() in up for m in rejects):
                continue
            return prof
    return None


# =============================================================================
# 3. SECTIONS + METER BLOCKS  (the anti-contamination layer)
# =============================================================================

def classify_unrecognized(text, profiles=None):
    """Human-readable reason a PDF could not be harvested, for the
    Unrecognized table. Explains WHY: XFA shell, no text layer, or which
    municipality it looks like but couldn't be confirmed (and what marker
    is missing)."""
    if _looks_like_xfa_shell(text):
        return ("XFA form shell — this copy carries no embedded data "
                "package, so nothing could be recovered automatically. "
                "Open it in Adobe Reader and Print/Save to a flat PDF, "
                "then re-run (and send the diagnostic bundle so this "
                "variant can be handled).")
    if len(re.findall(r"(?m)^\w[\w ]{1,32}: \S", text)) >= 5:
        return ("Recovered from an XFA form — the data is readable but "
                "this field layout has no profile yet. Export the "
                "diagnostic bundle so the columns can be mapped.")
    if len(text.strip()) < 40:
        if _ocr_available():
            return ("Scanned image — OCR ran but could not read enough to "
                    "recognise the bill. A cleaner scan or the original "
                    "PDF is needed.")
        return ("No text layer (likely a scanned image). Install "
                "Tesseract OCR (e.g. 'winget install tesseract') and "
                "re-run — the app will then read scans automatically and "
                "red-flag the rows for verification.")

    # Which municipalities does it partly resemble? Report the closest.
    up = text.upper()
    hints = {
        "City of Johannesburg": ["JOHANNESBURG", "CITY POWER", "PIKITUP"],
        "City of Cape Town": ["CAPE TOWN", "CAPETOWN.GOV"],
        "Eskom": ["ESKOM"],
        "City of Tshwane": ["TSHWANE"],
        "City of Ekurhuleni": ["EKURHULENI", "SIYAKHOKHA"],
        "eThekwini (Durban)": ["METRO BILL", "DURBAN.GOV", "ETHEKWINI"],
    }
    partial = []
    for name, markers in hints.items():
        hit = [m for m in markers if m in up]
        if hit:
            partial.append((name, hit))
    if partial:
        name, hit = partial[0]
        return ("Looks like %s (found: %s) but it didn't match that "
                "profile's full fingerprint — likely a new bill layout or "
                "sub-format. Send this PDF to calibrate a profile for it."
                % (name, ", ".join(h.title() for h in hit)))

    # Nothing recognizable at all — show a short snippet to help identify it.
    snippet = " ".join(text.split())[:80]
    return ("No municipality profile matched, and no known markers were "
            "found. First readable text: \u201c%s\u2026\u201d" % snippet)


def split_sections(text, profile):
    """Find each service's start marker (a regex, earliest match wins),
    sort by position; each section runs until the next section starts.
    Returns (sections: {service: text}, header: text-above-first-section).
    A service whose marker is not found is simply absent -> 'n/a' later."""
    starts = []
    overlays = []
    for svc, cfg in profile.get("sections", {}).items():
        pat = cfg.get("start")
        if not pat:
            continue
        m = re.search(pat, text, re.I | re.M)
        if not m:
            continue
        if cfg.get("overlay"):
            # An OVERLAY section captures its own span but is NOT a
            # boundary for the others - for rows that live INSIDE another
            # service's table (e.g. Mbombela's 'RATES : BUSINESS' line
            # inside the shared details table, which must not cut the
            # electricity span short).
            overlays.append((m.start(), svc, cfg))
        else:
            starts.append((m.start(), svc))
    starts.sort()
    sections = {}
    for i, (pos, svc) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
        sections[svc] = text[pos:end]
    for pos, svc, cfg in overlays:
        endpat = cfg.get("end")
        end = len(text)
        if endpat:
            m2 = re.search(endpat, text[pos:], re.I | re.M)
            if m2:
                end = pos + m2.end()
        else:
            nxt = [p for p, _s in starts if p > pos]
            if nxt:
                end = nxt[0]
        sections[svc] = text[pos:end]
    # A section spec may declare "require": a regex that must ALSO match
    # inside the section body, else the section is treated as ABSENT
    # (heading-only shells: e.g. CoJ prints the City Power heading with
    # "Unbilled Electricity: Eskom supply" and no data on Eskom-supplied
    # stands — absent means N/A diagnostics, never required-misses).
    for svc in list(sections):
        req = profile.get("sections", {}).get(svc, {}).get("require")
        if req and not re.search(req, sections[svc], re.I | re.M):
            del sections[svc]
    header = text[: starts[0][0]] if starts else text
    return sections, header


def split_blocks(section_text, block_anchor):
    """Split a section into per-meter blocks at each anchor match.
    No anchor / no match -> the whole section is one block."""
    if not block_anchor:
        return [section_text]
    hits = [m.start() for m in re.finditer(block_anchor, section_text, re.I | re.M)]
    if not hits:
        return [section_text]
    blocks = []
    for i, pos in enumerate(hits):
        end = hits[i + 1] if i + 1 < len(hits) else len(section_text)
        blocks.append(section_text[pos:end])
    return blocks


# =============================================================================
# 4. VALUE CLEANING + NUMBER FORMATTING (Decimal-safe, no float drift)
# =============================================================================

def _to_decimal(raw):
    s = raw.strip()
    neg = s.endswith("-") or s.upper().endswith("CR")
    if s.startswith("(") and s.endswith(")"):  # accountant negatives (Ugu)
        neg, s = True, s[1:-1].strip()
    s = re.sub(r"(?i)CR$", "", s).rstrip("-").strip()
    # SA comma-decimal / space-thousands ("55 473,14"): the comma IS the decimal
    # point. Stripping it as a thousands separator silently multiplies by 100.
    # Only a string whose sole comma is followed by exactly two trailing digits
    # qualifies, so period-decimal and comma-thousands numbers are untouched.
    # (Harvester profile pack v4, optional_comma_decimal_patch — applied with its ditsobotla profile.)
    _sa = s.replace("R", "").strip()
    if re.fullmatch(r"-?\d[\d ]*,\d{2}", _sa):
        s = _sa.replace(" ", "").replace(",", ".")
    s = s.replace("R", "").replace(" ", "").replace(",", "")
    if not s:
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return -d if neg else d


def fmt_money(d):
    return "%.2f" % d


def fmt_number(d):
    """298707.000 -> '298707'; 90052.373 -> '90052.373'."""
    s = format(d.normalize(), "f")
    return s


def clean_value(raw, vtype):
    if raw is None:
        return None
    s = raw.strip()
    if vtype == "money":
        d = _to_decimal(s)
        return None if d is None else fmt_money(d)
    if vtype == "number":
        d = _to_decimal(s)
        return None if d is None else fmt_number(d)
    return s  # text / date: as printed on the bill


# =============================================================================
# 5. RULES ENGINE  ->  RECORD rows + DIAGNOSTICS
# =============================================================================

def _rule_fields(rule):
    return rule["fields"] if "fields" in rule else [rule["field"]]


def _rule_patterns(rule):
    return rule["patterns"] if "patterns" in rule else [rule["pattern"]]


def _diag(diags, filename, rule, field, scope, status, value):
    diags.append({"file": filename, "field": field,
                  "label": rule.get("label", field), "scope": scope,
                  "status": status, "required": bool(rule.get("required")),
                  "value": value})


def _apply_rule(rule, scope_text, out, diags, filename, scope_note):
    """Run one rule against its scope text.
    Default: first match wins; capture groups map to field(s) in order.
    collect='sum': finditer, each group summed across matches (Decimal).
    collect='join_unique': unique captures joined with '/' (sorted)."""
    fields = _rule_fields(rule)
    # Per-field types: "types": ["number","money"] pairs with "fields";
    # otherwise the single "type" applies to every capture group.
    types = rule.get("types") or [rule.get("type", "text")] * len(fields)
    collect = rule.get("collect")
    values = {f: None for f in fields}

    for pattern in _rule_patterns(rule):
        if collect == "sum":
            sums = [None] * len(fields)
            for m in re.finditer(pattern, scope_text, re.I | re.M):
                for gi in range(len(fields)):
                    if gi < len(m.groups()) and m.group(gi + 1) is not None:
                        d = _to_decimal(m.group(gi + 1))
                        if d is not None:
                            sums[gi] = d if sums[gi] is None else sums[gi] + d
            for gi, f in enumerate(fields):
                if sums[gi] is not None:
                    values[f] = fmt_money(sums[gi]) if types[gi] == "money" \
                        else fmt_number(sums[gi])
        elif collect == "join_unique":
            found = []
            for m in re.finditer(pattern, scope_text, re.I | re.M):
                v = (m.group(1) or "").strip()
                if v and v not in found:
                    found.append(v)
            if found:
                values[fields[0]] = "/".join(sorted(found))
        else:
            m = re.search(pattern, scope_text, re.I | re.M)
            if m:
                for gi, f in enumerate(fields):
                    if gi < len(m.groups()):
                        values[f] = clean_value(m.group(gi + 1), types[gi])
        if any(v not in (None, "") for v in values.values()):
            break  # this pattern delivered; skip fallbacks

    for f in fields:
        v = values[f]
        if v not in (None, ""):
            out[f] = v
            _diag(diags, filename, rule, f, scope_note, "HIT", v)
        else:
            _diag(diags, filename, rule, f, scope_note, "MISS", "")


def apply_computes(row, computes):
    """Derived fields per row.
      op 'sum'    -> add all inputs (missing = 0)
      op 'sub'    -> first minus the rest (needs first operand)
      op 'diff'   -> abs(first - second); for kWh from End-Start readings
      op 'prefer' -> first input that is present and non-empty (fallback chain)
      op 'mul'    -> product of all inputs (every operand must be present)
      op 'concat' -> join present TEXT inputs with a space, skipping a part
                     that is identical to one already joined (guards against
                     the same header line being captured by two rules).
    Output format: money by default; set "as": "number" for kWh/units.
    Skipped silently when there's nothing valid to compute."""
    for c in computes or []:
        op = c.get("op")
        as_number = c.get("as") == "number"

        if op == "concat":
            parts = []
            for f in c.get("of", []):
                v = str(row.get(f, "") or "").strip()
                if v and v not in parts:
                    parts.append(v)
            if parts:
                row[c["field"]] = " ".join(parts)
            continue

        if op == "prefer":
            # first present, non-empty input wins (no arithmetic)
            for f in c.get("of", []):
                v = row.get(f, "")
                if v not in (None, ""):
                    row[c["field"]] = v
                    break
            continue

        if op == "label_present":
            # Classification: the first field in 'of' that holds a value
            # selects the matching string from 'labels'. Used to DERIVE a
            # tariff type when the bill never prints one (e.g. CoJ: an
            # off-peak register present => "Time of Use", else a flat
            # energy charge => "Conventional").
            for f, lbl in zip(c.get("of", []), c.get("labels", [])):
                v = row.get(f, "")
                if v not in (None, ""):
                    row[c["field"]] = lbl
                    break
            continue

        vals, present = [], 0
        for f in c.get("of", []):
            d = _to_decimal(str(row.get(f, "")))
            if d is None:
                d = Decimal(0)
            else:
                present += 1
            vals.append(d)
        if not vals or present == 0:
            continue
        if op in ("sub", "diff") and _to_decimal(str(row.get(c["of"][0], ""))) is None:
            continue
        # 'mul' multiplies its inputs and requires EVERY operand present:
        # a missing factor must not silently become ×0 or ×1. Added for the
        # CoT CT-metered registers ("Cons 107 (X60) KWH" — billed kWh is the
        # printed product 107×60; the printed Rand charge proves it).
        if op == "mul":
            if present != len(vals):
                continue
            total = Decimal(1)
            for v in vals:
                total *= v
            row[c["field"]] = fmt_number(total) if as_number else fmt_money(total)
            continue

        if op == "sum":
            total = sum(vals, Decimal(0))
        elif op == "diff":
            total = vals[0] - (vals[1] if len(vals) > 1 else Decimal(0))
            if total < 0:
                total = -total
        else:  # sub
            total = vals[0]
            for v in vals[1:]:
                total -= v
        row[c["field"]] = fmt_number(total) if as_number else fmt_money(total)


def extract(pdf_text, profile, filename):
    """The heart. Returns (rows, diagnostics).
    Row = {'file','service','municipality', meta..., field values...}
    One row per meter block per present service (CoJ: one row/service).
    Absent sections produce 'N/A' diagnostics (absent != missed)."""
    diags = []
    if profile is None:
        return [], diags
    sections, header = split_sections(pdf_text, profile)
    scope_texts = {"document": pdf_text, "header": header}

    # --- meta rules (document/header scope) -> merged into every row
    meta = {}
    for rule in profile.get("fields", []):
        scope = rule.get("scope", "document")
        if scope in ("document", "header"):
            _apply_rule(rule, scope_texts[scope], meta, diags, filename, scope)

    # --- per-service rules
    rows = []
    for svc in SERVICES:
        svc_rules = [r for r in profile.get("fields", [])
                     if r.get("scope", "").endswith(":" + svc)]
        if not svc_rules:
            continue
        sec_text = sections.get(svc)
        if sec_text is None:
            # Section absent from this PDF: not an error, not a miss.
            for rule in svc_rules:
                for field in _rule_fields(rule):
                    _diag(diags, filename, rule, field, rule["scope"], "N/A", "")
            continue

        section_vals = {}
        for rule in svc_rules:
            if rule["scope"].startswith("section:"):
                _apply_rule(rule, sec_text, section_vals, diags, filename,
                            rule["scope"])

        block_rules = [r for r in svc_rules if r["scope"].startswith("block:")]
        anchor = profile.get("sections", {}).get(svc, {}).get("block_anchor")
        blocks = split_blocks(sec_text, anchor) if block_rules else [sec_text]

        for block in blocks:
            row = {"file": filename, "service": svc,
                   "_profile_id": profile.get("profile_id", ""),
                   "municipality": profile.get("short_name",
                                               profile.get("profile_name", "?"))}
            row.update(meta)
            row.update(section_vals)
            for rule in block_rules:
                _apply_rule(rule, block, row, diags, filename, rule["scope"])
            apply_computes(row, profile.get("computes"))
            if svc == "electricity":
                row["_canon"] = to_canonical(row, profile)
            rows.append(row)

    return rows, diags
