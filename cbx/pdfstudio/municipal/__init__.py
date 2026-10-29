"""Municipal bills the CBX Harvester already knows (1.0.75).

The municipal profiles from CBX Harvester 6 (18 at first; 63 with profile pack v4, 1.0.76) (Eskom, Tshwane, Johannesburg, Cape Town, Ekurhuleni,
eThekwini …) run inside PDF Studio. When a batch — or a Harvest folder — is read for a template whose
columns are the Harvester's (Site Name, Billing Date, Account Number, Total kWh, Peak Rands …), each PDF is
first offered to the profiles:

  * a profile recognises the bill (its fingerprint) → its rules read it, exactly as the Harvester did
    (engine.py is the Harvester's own code), and the 28 canonical electricity columns land in the
    template's columns; nothing is learned or guessed, and the bill counts as read by "<Municipality>
    (Harvester profile)" in PDF Studio's profiles;
  * the Harvester's own contradiction checks run on every bill (end reading below start, TOU bands not
    adding up to the total, charge lines not adding up to the ex-VAT total …): a figure that fails goes to
    a person instead of being trusted;
  * a bill no profile knows goes on to PDF Studio's usual learning path, as before.

Profiles: the built-in ones in profiles/, plus any `*.profile.json` an admin puts in
DATA_DIR/pdfstudio/municipal/ — the same file name replaces a built-in one (as on the Harvester's disk).
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from ...config import settings
from . import engine as E

BUILT_IN = Path(__file__).with_name("profiles")
CANONICAL = E.CANONICAL_ELEC  # [(key, label)] — the Harvester's General template, in its order
CTYPE = {**{k: "text" for k in ("site_name", "municipality", "tariff_name", "account_number", "meter", "reading_type")},
         **{k: "date" for k in ("billing_date", "read_start", "read_end")},
         **{k: "number" for k in ("start_reading_kwh", "end_reading_kwh", "total_kwh", "peak_kwh", "standard_kwh", "offpeak_kwh", "demand_kva", "nmd_kva")},
         **{k: "money" for k in ("peak_r", "standard_r", "offpeak_r", "demand_r", "nmd_r", "other_r", "total_ex_vat_r", "deposit_r", "cash_r", "guarantee_r", "security_held_r")}}
REQUIRED = {"account_number", "billing_date", "municipality", "total_ex_vat_r"}
# other names a template may give a canonical column (lower-case, words only)
SYNONYMS = {
    "site_name": ["site", "site name", "premises", "property"],
    "billing_date": ["billing date", "bill date", "invoice date", "date", "statement date"],
    "municipality": ["munic eskom", "munic", "municipality", "supplier", "utility"],
    "tariff_name": ["tariff", "tariff name"],
    "account_number": ["account number", "account no", "account", "acc no"],
    "meter": ["meter number", "meter no", "meter"],
    "reading_type": ["actual estimate", "actual estimated", "reading type"],
    "read_start": ["start date", "reading start", "period start", "from date"],
    "read_end": ["end date", "reading end", "period end", "to date"],
    "start_reading_kwh": ["start reading kwh", "start reading", "previous reading", "opening reading"],
    "end_reading_kwh": ["end reading kwh", "end reading", "current reading", "closing reading"],
    "total_kwh": ["total kwh", "kwh", "consumption", "consumption kwh", "units"],
    "peak_kwh": ["peak kwh"], "standard_kwh": ["standard kwh"], "offpeak_kwh": ["off peak kwh", "offpeak kwh"],
    "demand_kva": ["demand kva", "demand"], "nmd_kva": ["nmd kva", "nmd"],
    "peak_r": ["peak rands", "peak r"], "standard_r": ["standard rands", "standard r"], "offpeak_r": ["off peak rands", "offpeak rands", "off peak r"],
    "demand_r": ["demand rands", "demand r"], "nmd_r": ["nmd rands", "nmd r"], "other_r": ["other charges rands", "other charges", "other rands"],
    "total_ex_vat_r": ["total charge ex vat rands", "total ex vat", "total excl vat", "excl vat", "total excluding vat", "total charge ex vat"],
    "deposit_r": ["deposit"], "cash_r": ["cash"], "guarantee_r": ["guarantee"], "security_held_r": ["security held"],
}
_cache: dict = {"sig": None, "profiles": []}


def _words(s: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (s or "").lower()))


def extra_dir() -> Path:
    return settings.path("pdfstudio", "municipal")


def profiles() -> list[dict]:
    """Every profile, in the Harvester's order (file name), a file in DATA_DIR replacing a built-in one."""
    files: dict[str, Path] = {p.name: p for p in sorted(BUILT_IN.glob("*.profile.json"))}
    try:
        files.update({p.name: p for p in sorted(extra_dir().glob("*.profile.json"))})
    except OSError:
        pass
    sig = tuple((n, p.stat().st_mtime, p.stat().st_size) for n, p in sorted(files.items()))
    if _cache["sig"] == sig:
        return _cache["profiles"]
    out = []
    for name, p in sorted(files.items()):
        try:
            raw = p.read_bytes()
            prof = json.loads(raw.decode("utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(prof, dict) and prof.get("profile_id") and prof.get("fingerprint"):
            prof["_file"], prof["_hash"], prof["_custom"] = name, hashlib.sha256(raw).hexdigest()[:16], p.parent != BUILT_IN
            out.append(prof)
    _cache.update(sig=sig, profiles=out)
    return out


def column_map(columns: list[dict]) -> dict[str, str]:
    """{template column key: canonical key} for the columns that are the Harvester's."""
    names: dict[str, str] = {}
    for k, label in CANONICAL:
        names.setdefault(_words(k.replace("_", " ")), k)
        names.setdefault(_words(label), k)
        for s in SYNONYMS.get(k, []):
            names.setdefault(_words(s), k)
    out, used = {}, set()
    for c in columns:
        if c.get("ctype") in ("filename", "formula"):
            continue
        k = c["key"] if c["key"] in CTYPE else names.get(_words(c.get("label", ""))) or names.get(_words(c["key"].replace("_", " ")))
        if k and k not in used:
            out[c["key"]], used = k, used | {k}
    return out


def applies(columns: list[dict]) -> bool:
    """A template is the Harvester's when it carries enough of its columns — never just an Account column."""
    m = set(column_map(columns).values())
    return len(m) >= 3 and bool(m & {"total_ex_vat_r", "total_kwh", "start_reading_kwh", "end_reading_kwh"})


def _tesseract_on_path() -> None:
    """The Harvester's OCR looks for tesseract on PATH (and in Program Files); CBX_TESSERACT_CMD points at it too."""
    import os

    cmd = (settings.tesseract_cmd or "").strip()
    if cmd and os.path.isfile(cmd):
        d = os.path.dirname(cmd)
        if d and d not in os.environ.get("PATH", "").split(os.pathsep):
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
        td = os.path.join(d, "tessdata")
        if os.path.isdir(td):
            os.environ.setdefault("TESSDATA_PREFIX", td)


def read(data: bytes, filename: str = "") -> dict:
    """{profile, rows, canon, checks, misses, pages, warnings} — profile None when no profile knows the bill."""
    _tesseract_on_path()
    rd = E.read_pdf(data, ocr="auto")  # 1.0.77: a page with no text is OCR'd (as in the Harvester); its values go to a person
    prof = E.identify_profile(rd["full"], profiles()) if rd["full"].strip() else None
    if prof is None:
        return {"profile": None, "rows": [], "canon": None, "checks": [], "misses": [], "pages": rd["pages"], "warnings": rd["warnings"], "ocr": bool(rd.get("ocr"))}
    rows, diags = E.extract(rd["full"], prof, filename)
    elec = next((r for r in rows if r.get("service") == "electricity"), None)
    canon = elec.get("_canon") if elec else None
    return {"profile": prof, "rows": rows, "canon": canon, "checks": checks(canon) if canon else [], "pages": rd["pages"], "warnings": rd["warnings"], "ocr": bool(rd.get("ocr")),
            "misses": [d for d in diags if d["status"] == "MISS" and d["required"]]}


def _num(v):
    from decimal import Decimal, InvalidOperation

    try:
        s = str(v).replace(",", "").replace(" ", "").strip()
        return Decimal(s) if s else None
    except (InvalidOperation, ValueError):
        return None


def checks(c: dict) -> list[dict]:
    """The Harvester's own contradiction checks on one bill (compute_exceptions, per row).
    [{sev, code, detail, keys}] — `keys` are the canonical columns a failing check casts doubt on."""
    from decimal import Decimal

    out = []
    start, end, kwh = _num(c.get("start_reading_kwh")), _num(c.get("end_reading_kwh")), _num(c.get("total_kwh"))
    peak, std, off = _num(c.get("peak_kwh")), _num(c.get("standard_kwh")), _num(c.get("offpeak_kwh"))
    tou = any(v is not None for v in (peak, std, off))
    multi = "/" in str(c.get("meter") or "")
    if start is not None and end is not None and end < start:
        out.append({"sev": "ERROR", "code": "READ↓", "detail": f"End reading {end} is lower than start reading {start} (rollover or replaced meter?)", "keys": ["end_reading_kwh", "start_reading_kwh"]})
    if start is not None and end is not None and kwh is not None and not tou and not multi and end > start and abs((end - start) - kwh) > 1:
        out.append({"sev": "WARNING", "code": "CONS≠DIFF", "detail": f"Consumption {kwh} is not end − start ({end} − {start} = {end - start})", "keys": []})
    if kwh is not None and kwh < 0:
        out.append({"sev": "ERROR", "code": "NEG-KWH", "detail": f"Negative consumption {kwh}", "keys": ["total_kwh"]})
    if all(v is not None for v in (peak, std, off)) and kwh is not None and abs((peak + std + off) - kwh) > 1:
        out.append({"sev": "ERROR", "code": "TOU≠TOT", "detail": f"Peak + Standard + Off-peak = {peak + std + off} but total kWh = {kwh}", "keys": ["total_kwh"]})
    tot = _num(c.get("total_ex_vat_r"))
    if tot is not None:
        parts = sum((_num(c.get(k)) or Decimal(0)) for k in ("peak_r", "standard_r", "offpeak_r", "demand_r", "nmd_r", "other_r"))
        if abs(parts - tot) >= Decimal("0.10"):
            out.append({"sev": "ERROR", "code": "CHARGE≠SUM", "detail": f"Charge lines add to R {parts} but the ex-VAT total is R {tot}", "keys": ["total_ex_vat_r"]})
    if kwh is None or kwh == 0:
        energy = sum((_num(c.get(k)) or Decimal(0)) for k in ("peak_r", "standard_r", "offpeak_r"))
        if energy > 0:
            out.append({"sev": "WARNING", "code": "0KWH+ENERGY", "detail": f"Energy charges of R {energy} but no kWh", "keys": []})
    return out


AF_MONTHS = {"januarie": "January", "februarie": "February", "maart": "March", "mei": "May", "junie": "June", "julie": "July",
             "augustus": "August", "oktober": "October", "desember": "December"}  # Afrikaans bills (Overstrand, Tshwane …)


def _date(raw: str) -> Optional[str]:
    from .. import money as M

    raw = re.sub(r"[A-Za-z]+", lambda m: AF_MONTHS.get(m.group(0).lower(), m.group(0)), raw or "")
    v = M.parse_date(raw)
    if v:
        return v
    for f in ("%d-%b-%y", "%d-%b-%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(raw.strip(), f).date().isoformat()
        except ValueError:
            pass
    return None


def cell(canon_key: str, raw: str, ctype: str, pages: list[str], pid: str, ocr: bool = False) -> dict:
    """One PDF Studio cell from a profile's value. A value read from a scan (OCR) always goes to a person:
    OCR can misread a digit, and the profile's strict patterns only make a damaged figure blank, not right."""
    method = f"profile:{pid}"
    raw = "" if raw is None else str(raw).strip()
    base = {"raw": raw or None, "bbox": None, "method": method, "quality": "fuzzy" if ocr else "exact", "ocr": ocr, "anchor": "read from a scan" if ocr else "",
            "parsed": True, "provenance": True, "score": None, "status": "ok", "note": ""}
    if not raw:
        return {**base, "value": None, "page": None, "quote": "", "parsed": False, "status": "miss", "note": "not on this bill (Harvester profile)"}
    page = next((i + 1 for i, p in enumerate(pages) if raw in p), None)
    quote = raw if page else ""
    if ctype in ("money", "number"):
        n = _num(raw)
        if n is None:
            return {**base, "value": None, "page": page, "quote": quote, "parsed": False, "note": f"printed as '{raw}'"}
        v = round(float(n), 2) if ctype == "money" else (int(n) if n == n.to_integral_value() else float(n))
        return {**base, "value": v, "page": page, "quote": quote}
    if ctype == "date":
        v = _date(raw)
        if v is None:
            return {**base, "value": None, "page": page, "quote": quote, "parsed": False, "status": "miss", "note": f"printed as '{raw}' — not a full date"}
        return {**base, "value": v, "page": page, "quote": quote}
    return {**base, "value": raw, "page": page, "quote": quote}


# ---- inside a PDF Studio batch ------------------------------------------------------------------------

def db_version(db, prof: dict):
    """The PDF Studio profile row for a Harvester profile (one per municipality, a new version when its file
    changes), so every bill shows which profile read it."""
    from ...db import utcnow
    from ...models import Profile, ProfileVersion

    key = f"municipal:{prof['profile_id']}"
    p = db.query(Profile).filter(Profile.key == key).first()
    if p is None:
        p = Profile(key=key, label=f"{prof.get('profile_name') or prof['profile_id']} (Harvester profile)", status="live", created_by="harvester", shared=True)
        db.add(p)
        db.flush()
    v = next((x for x in p.versions if (x.ruleset or {}).get("hash") == prof["_hash"]), None)
    if v is None:
        v = ProfileVersion(profile_id=p.id, number=len(p.versions) + 1, author="harvester", approved_at=utcnow(),
                           ruleset={"engine": "municipal", "profile": prof["profile_id"], "file": prof["_file"], "hash": prof["_hash"], "custom": prof["_custom"]},
                           notes=str(prof.get("_doc") or "")[:4000], reason="CBX Harvester profile" + (" (from the server's own folder)" if prof["_custom"] else ""))
        db.add(v)
        db.flush()
        p.live_version_id = v.id
    p.status = "live"
    return p, v


def claim(db, template, columns: list[dict], docs: list[dict], read_bytes: Callable[[dict], bytes], say=None) -> set:
    """Read every bill a Harvester profile knows; returns the ids of the documents it read. Their cells,
    scores and validators are set on each doc dict (as repair.run_docs does) and d['municipal'] = version."""
    from .. import extract as X
    from .. import scoring as SC

    if not applies(columns):
        return set()
    cmap = column_map(columns)
    done: set = set()
    seen: dict[str, int] = {}
    for d in docs:
        try:
            got = read(read_bytes(d), d.get("filename") or d.get("name") or "")
        except Exception:  # noqa: BLE001 — an unreadable file goes the usual way
            continue
        prof = got["profile"]
        if prof is None or got["canon"] is None:
            continue
        p, v = db_version(db, prof)
        pid = prof["profile_id"]
        cells: dict = {}
        for col in columns:
            if col.get("ctype") == "filename":
                cells[col["key"]] = X.filename_cell(d.get("filename") or d.get("name"))
            elif col.get("ctype") == "formula":
                cells[col["key"]] = X._empty_cell()
            elif col["key"] in cmap:
                ck = cmap[col["key"]]
                cells[col["key"]] = cell(ck, got["canon"].get(ck), col.get("ctype") or CTYPE[ck], got["pages"], pid, got.get("ocr", False))
            else:
                cells[col["key"]] = {**X._empty_cell(), "note": "not one of the Harvester's columns"}
        for key, old in (d.get("cells") or {}).items():
            if old.get("status") in SC.USER_OWNED and key in cells:
                cells[key] = old  # what people filled stays
        back = {v_: k for k, v_ in cmap.items()}
        failing = sorted({back[k] for ch in got["checks"] if ch["sev"] == "ERROR" for k in ch["keys"] if k in back})
        val = {"failing": failing, "note": "; ".join(ch["detail"] for ch in got["checks"] if ch["sev"] == "ERROR"),
               "harvester_checks": [{k: ch[k] for k in ("sev", "code", "detail")} for ch in got["checks"]], "profile": pid}
        SC.score_cells(cells, columns, val, False)
        X.apply_formulas(cells, columns)
        d["cells"], d["validators"] = cells, val
        d["row"], d["k"], d["n"] = SC.row_score(cells, columns)
        d["municipal"] = v
        d["db"].profile_id = p.id
        done.add(d["id"])
        seen[prof.get("short_name") or pid] = seen.get(prof.get("short_name") or pid, 0) + 1
    if done and say:
        say(f"{len(done)} bill{'s' if len(done) != 1 else ''} read with the Harvester profiles ({', '.join(f'{k} ×{n}' for k, n in seen.items())}) — "
            "their rules, their checks; nothing learned or guessed." + (f" The other {len(docs) - len(done)} go the usual way." if len(done) < len(docs) else ""))
    return done


def save(db, docs: list[dict], columns: list[dict], now_iso: str) -> None:
    """Fill in the bills the profiles read (as the pipeline's fill-in does for learned profiles)."""
    from ...models import Extraction
    from .. import scoring as SC

    for d in docs:
        dd, cells, v = d["db"], d["cells"], d["municipal"]
        ext = dd.extraction
        if ext is None:
            ext = Extraction(document_id=dd.id)
            db.add(ext)
            v.profile.docs_seen = (v.profile.docs_seen or 0) + 1
        elif ext.cells:
            hist = list(ext.history or [])
            hist.append({"at": now_iso, "profile_version_id": ext.profile_version_id, "row_score": ext.row_score, "cells": ext.cells})
            ext.history = hist[-5:]
        ext.cells, ext.row_score, ext.profile_version_id = cells, d["row"], v.id
        ext.validators = d.get("validators") or {}
        dd.score = d["row"]
        dd.status = "ok" if all((cells.get(c["key"]) or {}).get("status") not in SC.FOR_USER for c in columns) else "review"


def template_columns() -> list[dict]:
    """The Harvester's General template as PDF Studio columns."""
    return [{"key": k, "label": label, "ctype": CTYPE[k], "required": k in REQUIRED, "critical": k == "total_ex_vat_r"} for k, label in CANONICAL]


TEMPLATE_NAME = "Municipal electricity bills"
