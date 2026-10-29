"""Read Swiftnet PDFs — quotations, Notices to Proceed and signed Site Schedule Agreements — into one
agreement's fields and charge tables.

A port of the reading engine in Edwin's Swiftnet Site Schedule Generator (v5, pdf.js) to pdfplumber, rule
for rule: the page is turned into positioned text runs, runs into lines, and every value is found next to
its label ("Site Name :", "Area in m²", "Antenna Comments :" …). One change, asked for on 9 Oct: every
numbered comment on a quote is its own row in the charge table — nothing is joined with "; " any more.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
LABEL_RE = re.compile(r":\s*$|^(Site Owner|Longitude|Latitude|Structure\b|Number of|Area in|T-Area|Total \d|Contact Person|Comm Verified|PM Verified|Leasing Verified"
                      r"|Ops Contact|Return to|Standby Charge|Electricity Charge|Consumption Charge|Usage Frequency|Charge per Usage|Road - Charge|Once-?Off)", re.I)
NA_RE = re.compile(r"^#N/A$", re.I)
LEASES = ["New Application", "Renewal", "Re-Assessment", "Re-assessment (Add)", "Re-assessment (Swop)", "New Re-Assessment", "MOU"]


# ---- the page model ----------------------------------------------------------------------------------
@dataclass
class Item:
    x: float
    y: float  # baseline, measured up from the bottom of the page (as pdf.js gives it)
    w: float
    h: float
    s: str
    p: int
    start: int = 0  # where the run sits in its line's text
    end: int = 0


@dataclass
class Line:
    i: int
    p: int
    y: float
    items: list = field(default_factory=list)
    s: str = ""


@dataclass
class Model:
    lines: list
    pages: int
    name: str = ""

    def text(self, n: int = 10**9) -> str:
        return "\n".join(l.s for l in self.lines[:n])


def model_from_pdf(data: bytes, name: str = "") -> Model:
    import pdfplumber

    items: list[Item] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        npages = len(pdf.pages)
        for pn, page in enumerate(pdf.pages, 1):
            words = page.extract_words(keep_blank_chars=True, x_tolerance=1.5, y_tolerance=2, use_text_flow=True, extra_attrs=["size"])
            for w in words:
                s = re.sub(r"\s+", " ", w["text"]).strip()
                if not s:
                    continue
                items.append(Item(x=float(w["x0"]), y=float(page.height - w["bottom"]), w=float(w["x1"] - w["x0"]), h=float(w.get("size") or 8), s=s, p=pn))
    return Model(lines=build_lines(items), pages=npages, name=name)


def build_lines(items: list[Item]) -> list[Line]:
    lines: list[Line] = []
    for pn in sorted({it.p for it in items}):
        page = sorted((it for it in items if it.p == pn), key=lambda it: (-it.y, it.x))
        groups: list[list[Item]] = []
        last_y = None
        for it in page:
            if groups and last_y is not None and abs(last_y - it.y) <= 2.2:
                groups[-1].append(it)
            else:
                groups.append([it])
            last_y = it.y
        for g in groups:
            g.sort(key=lambda it: it.x)
            text, pos = [], 0
            for it in g:
                if text:
                    pos += 1
                it.start, it.end = pos, pos + len(it.s)
                text.append(it.s)
                pos += len(it.s)
            lines.append(Line(i=len(lines), p=pn, y=g[0].y, items=g, s=" ".join(text)))
    return lines


def join_items(items: list[Item]) -> str:
    """Runs top to bottom when they sit on different lines, else left to right; one space between."""
    rows: list[list[Item]] = []
    for it in sorted(items, key=lambda it: -it.y):
        if rows and abs(rows[-1][0].y - it.y) <= 2.2:
            rows[-1].append(it)
        else:
            rows.append([it])
    return re.sub(r"\s+", " ", " ".join(" ".join(i.s for i in sorted(r, key=lambda it: it.x)) for r in rows)).strip()


def is_label(s: str) -> bool:
    return bool(LABEL_RE.search(s))


@dataclass
class KV:
    v: str = ""
    line: int = -1
    y: float = 0
    x: float = 0
    x_end: float = 0
    items: list = field(default_factory=list)


def find_line(m: Model, rx: re.Pattern | str, start: int = 0, stop: Optional[int] = None) -> int:
    rx = re.compile(rx) if isinstance(rx, str) else rx
    stop = len(m.lines) - 1 if stop is None else min(stop, len(m.lines) - 1)
    for i in range(max(0, start), stop + 1):
        if rx.search(m.lines[i].s):
            return i
    return -1


def kv(m: Model, rx: re.Pattern | str, *, dy_up: float = 4, dy_down: float = 3.5, max_dx: float = 240, max_gap: float = 60,
       start: int = 0, stop: Optional[int] = None, no_inline: bool = False) -> KV:
    """The value to the right of a label: the rest of the label's own run, else the runs that follow it on
    the same baseline (stopping at the next label or a wide gap)."""
    rx = re.compile(rx) if isinstance(rx, str) else rx
    li = find_line(m, rx, start, stop)
    if li < 0:
        return KV()
    L = m.lines[li]
    mt = rx.search(L.s)
    last = mt.end() - 1
    lab = next((it for it in L.items if it.start <= last < it.end), L.items[-1])
    if not no_inline:
        off = mt.end() - lab.start
        rest = re.sub(r"^[\s:*]+", "", lab.s[off:] if 0 <= off <= len(lab.s) else "").strip()
        if rest and not NA_RE.match(rest):
            return KV(v=rest, line=li, y=L.y, x=lab.x, x_end=lab.x + lab.w, items=[lab])
    x_end = lab.x + lab.w
    cands = []
    for ln in m.lines:
        if ln.p != L.p:
            continue
        for it in ln.items:
            if it is lab or NA_RE.match(it.s):
                continue
            if x_end - 2 <= it.x <= x_end + max_dx and it.y - L.y <= dy_up and L.y - it.y <= dy_down:
                cands.append(it)
    cands.sort(key=lambda it: it.x)
    out: list[Item] = []
    last_end = x_end
    for c in cands:
        if is_label(c.s) or c.x - last_end > max_gap:
            break
        out.append(c)
        last_end = max(last_end, c.x + c.w)
    return KV(v=join_items(out), line=li, y=L.y, x=lab.x, x_end=x_end, items=out)


def kvs(m: Model, rx, **o) -> str:
    return kv(m, rx, **o).v


# 1.0.107: quotes with odd spacing — 'Latitude :' and '-26.13194' in separate cells, the value right of a coloured
# box, 'Site PID* :' with the value on the next line, 'LATITUDE:' — the label is matched loosely and, for the
# fields that have a clear shape (coordinates, a PID, a region), the value is looked for further right and below.
COORD = r"[-−–]?\s*\d{1,3}(?:[.,]\d+)?(?:(?:\s*[°º]\s*|\s+)\d{1,2}(?:(?:\s*['’]\s*|\s+)\d{1,2}(?:[.,]\d+)?\s*[\"”]?)?)?(?:\s*[NSEW]\b)?(?![\d.,])"
PID = r"[A-Z]{0,4}\d{2,8}\b"
REGION = r"(?:North|South|East|West|Central|Gauteng|Coastal|Inland|KZN|Kwa|Limpopo|Mpumalanga|Free State)[A-Za-z]*(?:[ -][A-Z][a-z]+)?"


def label_rx(*words: str) -> re.Pattern:
    """'Site', 'PID' → matches 'Site PID* :', 'SITE PID :', 'Site PID:' and 'Site  PID *  :'."""
    return re.compile(r"(?<![A-Za-z])" + r"\s*".join(re.escape(w) for w in words) + r"\s*\**\s*:", re.I)


def clean_coord(s: str) -> str:
    s = re.sub(r"[−–]", "-", s or "").strip()
    return re.sub(r"^-\s+", "-", s)


def field(m: Model, rx: re.Pattern | str, want: Optional[str] = None, *, below: bool = True, max_gap: float = 40,
          start: int = 0, stop: Optional[int] = None) -> str:
    """kvs(), then — when the field has a shape (`want`) and that didn't give it — the first run of that shape
    further right on the label's line (up to the next label), else just below the label (on a line with no label
    left of it). Only the part of the value that has the shape is kept; nothing of that shape → kvs() as before."""
    k = kv(m, rx, max_gap=max_gap, start=start, stop=stop)
    if not want:
        return k.v
    wrx = re.compile(want, re.I)
    mt = wrx.match(k.v or "")
    if mt and mt.group(0).strip():
        return mt.group(0).strip()
    if k.line < 0:
        return ""
    L = m.lines[k.line]
    lab_items = [it for it in L.items if it.x <= k.x_end and it.x + it.w >= k.x]
    lab_x = min([it.x for it in lab_items] or [k.x])

    def clusters(items):
        out: list[list[Item]] = []
        for it in sorted(items, key=lambda it: it.x):
            if out and it.x - (out[-1][-1].x + out[-1][-1].w) <= 8:
                out[-1].append(it)
            else:
                out.append([it])
        return out

    def take(items) -> str:
        for cl in clusters(items):
            s = re.sub(r"^[\s:*]+", "", " ".join(i.s for i in cl)).strip()
            if is_label(cl[0].s) and not wrx.match(s):
                return "\x00"  # the next label: stop
            hit = wrx.match(s)
            if hit and hit.group(0).strip():
                return hit.group(0).strip()
        return ""

    same = [it for ln in m.lines if ln.p == L.p and abs(ln.y - L.y) <= 5 for it in ln.items
            if it.x >= k.x_end - 2 and it.x <= k.x_end + 320 and not NA_RE.match(it.s) and it not in lab_items]
    v = take(same)
    if v and v != "\x00":
        return v
    if not below:
        return k.v
    for ln in m.lines[k.line + 1:k.line + 4]:
        if ln.p != L.p or L.y - ln.y > 18:
            break
        cand = [it for it in ln.items if lab_x - 10 <= it.x <= k.x_end + 320 and not NA_RE.match(it.s)]
        labels = [it for it in ln.items if is_label(it.s)]
        cand = [it for it in cand if not is_label(it.s) and not any(lb.x < it.x for lb in labels)]
        v = take(cand)
        if v and v != "\x00":
            return v
    return k.v  # nothing of that shape: what was there before (a region not in the list stays as read)


# ---- numbers and money --------------------------------------------------------------------------------
def num(s: Any) -> float:
    if isinstance(s, (int, float)):
        return float(s)
    s = re.sub(r"^R", "", str(s or "").strip())
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"^[^\d-]+", "", s)
    if re.search(r",\d{1,3}$", s) and not re.search(r"\.\d", s):
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    mt = re.match(r"-?[\d.]+", re.sub(r"[^\d.-]", "", s))
    try:
        return float(mt.group(0)) if mt else 0.0
    except ValueError:
        return 0.0


def fmt_zar(v: Any) -> str:
    """'R 20 211.77' — the agreement's money format."""
    if not isinstance(v, (int, float)):
        s = re.sub(r"^R", "", str(v or "").strip())
        s = s.replace(" ", "")
        s = s.replace(",", ".") if re.search(r"\d,\d{2}$", s) else s.replace(",", "")
        s = re.sub(r"[^\d.]", "", s)
        try:
            v = float(s)
        except ValueError:
            return "R 0.00"
    whole, dec = f"{abs(float(v)):,.2f}".split(".")
    return "R " + whole.replace(",", " ") + "." + dec


def amount_from(s: str) -> str:
    mt = re.search(r"R\s*\d[\d\s,.]*", s or "")
    return mt.group(0) if mt else ""


def zar(s: Any) -> str:
    return fmt_zar(num(s))


def long_date(d: int, m: int, y: int) -> str:
    return f"{d:02d} {MONTHS[m - 1]} {y}"


def parse_long_date(s: str) -> Optional[date]:
    mt = re.match(r"^\s*(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\s*$", s or "")
    if not mt:
        return None
    name = mt.group(2).lower()
    mi = next((i for i, n in enumerate(MONTHS) if n.lower() == name or n.lower()[:3] == name[:3]), -1)
    if mi < 0:
        return None
    try:
        return date(int(mt.group(3)), mi + 1, int(mt.group(1)))
    except ValueError:
        return None


def normalise_lease(s: str) -> str:
    t = (s or "").strip()
    for rx, out in ((r"re.?assess.*add", "Re-assessment (Add)"), (r"re.?assess.*sw[oa]p", "Re-assessment (Swop)"),
                    (r"new.*re.?assess|re.?assess.*new", "New Re-Assessment"), (r"re.?assess", "Re-Assessment"),
                    (r"new.*applic", "New Application"), (r"renewal|renew", "Renewal"), (r"\bmou\b", "MOU")):
        if re.search(rx, t, re.I):
            return out
    return t


# ---- what kind of PDF, and the reference in its name ---------------------------------------------------
def classify(m: Model) -> str:
    head = m.text(60)
    title = m.text(8)
    # 1.0.107: a Scope of Work, by its title (or its file name) — before the NTP, which may mention a scope of work further down
    if (re.search(r"\b(SCOPE|STATEMENT)\s+OF\s+WORKS?\b|\bSOW\b", title, re.I) or re.search(r"\bSOW\b|scope of work", m.name or "", re.I)) \
            and not re.search(r"NOTICE TO PROCEED|SITE SCHEDULE AGREEMENT|Quotation for Co-?location", title, re.I):
        return "sow"
    if re.search(r"NOTICE TO PROCEED", head, re.I):
        return "ntp"
    if re.search(r"SITE SCHEDULE AGREEMENT", head, re.I):
        return "agreement"
    if re.search(r"Quotation for Co-?location", head, re.I):
        return "quote"
    return "unknown"


def parse_ref(s: str) -> dict:
    """'9130 - 5183 - 70204797 - SITE - (01.08.2026 - 31.07.2031) - (7708) - R - VDC' → its parts."""
    out: dict = {}
    s = re.sub(r"\.pdf$", "", s or "", flags=re.I).replace("_", " ")
    mt = re.match(r"^\s*(\d{3,5})\s*-\s*(\d{2,5})\s*-\s*(7\d{7})\b", s)
    if mt:
        out.update(t100=mt.group(1), t000=mt.group(2), contract=mt.group(3))
    else:
        mt = re.match(r"^\s*(\d{3,5})\s*-\s*(7\d{7})\b", s)
        if mt:
            out.update(t100=mt.group(1), contract=mt.group(2))
    mt = re.search(r"(\d{2})\.(\d{2})\.(\d{4})\s*-\s*(\d{2})\.(\d{2})\.(\d{4})", s)
    if mt:
        out["start"] = [int(mt.group(1)), int(mt.group(2)), int(mt.group(3))]
        out["end"] = [int(mt.group(4)), int(mt.group(5)), int(mt.group(6))]
    mt = re.search(r"\d{4}\s*\)?\s*-?\s*\(?\s*([A-Z]{0,3}\d{3,6}(?:[ _-]?PL\d+)?)\s*\)?\s*-\s*(N|R|RA|RV|NA|MOU)\s*(?:-|$)", s, re.I)
    if mt:
        out["col_id"] = re.sub(r"\s+", "_", mt.group(1))
        out["lease_code"] = mt.group(2).upper()
        tail = s[mt.end():]
        code = re.match(r"\s*([A-Z]{2,5})\b", tail)
        if code:
            out["co_code"] = code.group(1)
    # # 1.0.108: a reference written with its prefix ("T100-9130", "T000 5183") is that one, whatever its place in the name
    for key, code in (("t100", "T100"), ("t000", "T000")):
        mt = re.search(code + r"\s*[-/ ]\s*(\d{2,6})\b", s, re.I)
        if mt:
            out[key] = mt.group(1)
    # a quotation's own name / footer: 'Upington Exch - Central - S06173 SAP 4129 - VDC - 70200909 - renewal'
    mt = re.search(r"-\s*([A-Z]{2,5})\s*-\s*(7\d{7})\s*-\s*(new(?:\s*application)?|renewal|re-?assessment|mou)\s*$", s, re.I)
    if mt:
        out.setdefault("co_code", mt.group(1).upper())
        out.setdefault("contract", mt.group(2))
        kind = mt.group(3).lower().replace("-", "").replace(" ", "")
        out.setdefault("lease_code", {"new": "N", "newapplication": "N", "renewal": "R", "reassessment": "RA", "mou": "MOU"}[kind])
    return out


# # 1.0.108: the agreement's two reference lines always read "T100/<number>" and "T000/<number>": the prefixes are fixed
# text, never taken from what was read or typed. Anything a person or an older version saved ("T100 9130", "t100-9130",
# "9130", "T000/9130" in the T100 field) comes down to its number and gets its own field's prefix back.
REF_PREFIX = {"t100_ref": "T100", "t000_ref": "T000"}
_REF_NUM = re.compile(r"^\s*(?:T\s*[01]\s*0\s*0\s*[-–/\\:.,_ ]*\s*)?(?:No\.?\s*)?(\d{1,12})(?!\d)", re.I)


def ref_number(v: Any) -> str:
    mt = _REF_NUM.match(str(v or ""))
    return mt.group(1) if mt else ""


def ref_text(key: str, v: Any) -> str:
    """'T100 9130' / '9130' / 'T000/9130' in t100_ref → 'T100/9130'; no number → '' (the line is left out)."""
    n = ref_number(v)
    return f"{REF_PREFIX[key]}/{n}" if n else ""


def fix_refs(fields: Optional[dict]) -> dict:
    out = dict(fields or {})
    for k in REF_PREFIX:
        if k in out:
            out[k] = ref_text(k, out[k])
    return out


# # 1.0.108: the file name's lease code follows the agreement's lease type, never a code read from the PDFs
LEASE_CODES = {"New Application": "N", "Renewal": "R", "Re-Assessment": "RA", "Re-assessment (Add)": "RA", "Re-assessment (Swop)": "RA",
               "New Re-Assessment": "NA", "MOU": "MOU"}


def lease_code_for(lease_type: Any, fallback: Any = "") -> str:
    """'Renewal' → 'R', 'New Re-Assessment' → 'NA'; a lease type not in the list keeps the code it had (fallback)."""
    low = str(lease_type or "").strip().lower()
    return next((c for t, c in LEASE_CODES.items() if t.lower() == low), str(fallback or "").strip())


def footer_ref(m: Model) -> str:
    for l in m.lines:
        if l.y < 40 and (re.search(r"\d{2}\.\d{2}\.\d{4}\s*-\s*\d{2}\.\d{2}\.\d{4}", l.s) or re.match(r"^\d{3,5}\s*-\s*\d{2,5}\s*-\s*7\d{7}", l.s)
                         or re.search(r"-\s*7\d{7}\s*-\s*(new|renewal|re-?assessment|mou)\b", l.s, re.I)):
            return l.s
    return ""


# ---- numbered comment blocks ---------------------------------------------------------------------------
MARK = re.compile(r"(?:([\w/()-]{4,}\*)\s*)?(?<![\w(])(\d{1,2})\s*\.?\s*\)\s*", re.ASCII)


def comment_entries(m: Model, label_rx: str, start_rx: str, end_rx: str) -> list[dict]:
    lab = kv(m, label_rx, no_inline=True)
    if lab.line < 0:
        return []
    x_min = (lab.x_end or lab.x) - 3
    a = find_line(m, re.compile(start_rx, re.I), max(0, lab.line - 4))
    if a < 0 or a > lab.line:
        a = lab.line - 1
    b = find_line(m, re.compile(end_rx, re.I), lab.line + 1)
    if b < 0:
        b = min(len(m.lines), lab.line + 12)
    page = m.lines[lab.line].p
    orphans: list[str] = []
    rows: list[list] = []  # [y, [items]]
    for i in range(a + 1, b):
        L = m.lines[i]
        if L.p != page:
            break
        keep = []
        for it in L.items:
            if it.x < x_min or re.match(r"^R\s*\d", it.s) or is_label(it.s) or NA_RE.match(it.s):
                continue
            if it.x > 440 and re.match(r"^([\w/()-]{4,}\*)?\s*\d{1,2}\s*\.?\s*\)$", it.s, re.ASCII):
                orphans.append(it.s)
                continue
            keep.append(it)
        if not keep:
            continue
        if rows and abs(rows[-1][0] - L.y) <= 3.5:  # 1.0.107: a run set a little higher/lower ("&" in another font) is the same line
            rows[-1][1].extend(keep)
        else:
            rows.append([L.y, keep])
    texts: list[str] = []
    for _, its in rows:
        t = " ".join(it.s for it in sorted(its, key=lambda it: it.x)).strip()
        if t and not re.match(r"^Not applicable$", t, re.I):
            texts.append(t)
    texts = glue_fragments(texts)
    entries: list[dict] = []
    for t in texts:
        if NA_WORD.match(t):  # 1.0.107: "N/A" holds its item's place but is never a description of its own
            entries.append({"n": None, "t": "N/A", "na": True})
            continue
        marks = list(MARK.finditer(t))
        if marks:
            before = t[:marks[0].start()].strip()
            if before:
                if entries and not entries[-1].get("na"):
                    entries[-1]["t"] += " " + before
                else:
                    entries.append({"n": None, "t": before})
            for k, mk in enumerate(marks):
                body = t[mk.end(): marks[k + 1].start() if k + 1 < len(marks) else len(t)]
                e = {"n": int(mk.group(2)), "t": ((mk.group(1) or "") + " " + body).strip()}
                if NA_WORD.match(body.strip()):
                    e.update(t="N/A", na=True)
                entries.append(e)
        elif orphans and re.match(r"^[A-Z0-9]", t):
            o = orphans.pop(0)
            om = re.match(r"^([\w/()-]{4,}\*)?\s*(\d{1,2})", o, re.ASCII)
            entries.append({"n": int(om.group(2)) if om else None, "t": (((om.group(1) or "") if om else "") + " " + t).strip()})
        elif (not entries or re.match(r"^(\d{6,}\b|T000|[\w/()-]{4,}\*|Co-?locate ID|\d+\s*x\s)", t, re.I | re.ASCII) or re.match(r"^[A-Z][a-z]+\s*\d+\s*:", t)
              or (entries[-1]["t"].rstrip().endswith(")") and re.match(r"^[A-Z0-9]", t))):
            entries.append({"n": None, "t": t})
        elif entries[-1].get("na"):
            entries.append({"n": None, "t": t})
        else:
            entries[-1]["t"] += " " + t
    out, seen = [], set()
    for e in entries:
        if e.get("na"):
            out.append({"n": e["n"], "t": "N/A", "na": True})
            continue
        s = re.sub(r"\*\s*", "* ", e["t"])
        s = re.sub(r"\(\s+", "(", s)
        s = re.sub(r"\s+", " ", s)
        s = re.sub(r"\s+([,.])", r"\1", s)
        s = re.sub(r"[\s,;]+$", "", s).strip()
        s = re.sub(r"^(?:&|\+|/|and\b)\s*", "", s, flags=re.I).strip()
        if s and s not in seen:
            seen.add(s)
            out.append({"n": e["n"], "t": s})
    return out


NA_WORD = re.compile(r"^\s*(?:#?N\s*/\s*A|None|Nil)\s*\.?\s*$", re.I)
_JOIN_END = re.compile(r"(?:[&+/,–-]|\b(?:and|or|with|for|of|the|to|in|on|at|a|an|incl\.?|including))\s*$", re.I)
_JOIN_START = re.compile(r"^\s*(?:[&+/,–-]|and\b|or\b)", re.I)


def glue_fragments(texts: list[str]) -> list[str]:
    """1.0.107: wrapped text stays together — a lone "&", a line that starts with "&"/"and", and the line after one
    that ends with "&", "and", "of", "," … all belong to the same comment (the screenshot: "N/A", "&" and
    "Rf cable trenching." landed on three charge lines). A line that starts with a numbered mark "2)" never joins."""
    out: list[str] = []
    for t in texts:
        starts_mark = bool(re.match(r"^\s*(?:[\w/()-]{4,}\*\s*)?\d{1,2}\s*\.?\s*\)", t, re.ASCII))
        if out and not starts_mark and not NA_WORD.match(t) and not NA_WORD.match(out[-1]) and (_JOIN_END.search(out[-1]) or _JOIN_START.match(t)):
            out[-1] = out[-1].rstrip() + " " + t.strip()
        else:
            out.append(t)
    return out


def _names(c: str, g: dict) -> bool:
    """Does a plain (un-numbered) antenna comment name this antenna group? '4 x 5G panel antennas' → the 5G
    groups; '0.371m dish at 42m' → the dish of that size / height; 'Sector antennas' → the other sector groups."""
    low = c.lower()
    sec = (g.get("section") or "").lower()
    if re.search(r"\b5g\b", low):
        return (g.get("label") or "").upper() == "5G"
    if re.search(r"\b(dish|mw|microwave|parabolic)\b", low):
        if "parabolic" not in sec:
            return False
        d = re.search(r"(\d+[.,]\d+)\s*m\s*dish", low)
        h = re.search(r"\bat\s*(\d+(?:[.,]\d+)?)\s*m\b", low)
        if d and g.get("dia") and abs(num(d.group(1)) - g["dia"]) > 0.005:
            return False
        if h and g.get("hagl") and abs(num(h.group(1)) - g["hagl"]) > 0.05:
            return False
        return True
    if "sector" in low:
        return "sector" in sec and (g.get("label") or "").upper() != "5G"
    return False


def comments_to_groups(comments: list[str], groups: list[dict], numbered: bool) -> tuple[list[list[str]], list[str]]:
    """Each priced antenna group's comments, and the comments left over (each becomes its own row)."""
    per: list[list[str]] = [[] for _ in groups]
    if numbered:
        for k in range(min(len(groups), len(comments))):
            per[k].append(comments[k])
        return per, comments[len(groups):]
    loose = []
    for c in comments:
        hits = [k for k, g in enumerate(groups) if _names(c, g)]
        for k in hits:
            per[k].append(c)
        if not hits:
            loose.append(c)
    left = []
    for c in loose:  # what names nothing goes, in order, to a group that has no comment yet
        k = next((k for k in range(len(groups)) if not per[k]), None)
        if k is None:
            left.append(c)
        else:
            per[k].append(c)
    return per, left


def _row(item: str, req: str = "", desc: str = "", amt: str = "") -> dict:
    return {"item": item, "req": req, "desc": desc, "amt": amt}


# ---- a quotation ---------------------------------------------------------------------------------------
def extract_quote(m: Model) -> dict:
    notes: list[str] = []
    f: dict = {}
    # 1.0.107: every label matched loosely (spaces around the colon, asterisks, case); lat/lon/PID/region also
    # found in a box further right or on the next line (field())
    f["col_id"] = kvs(m, label_rx("Co-locate ID")) or kvs(m, label_rx("Colocate ID")) or kvs(m, label_rx("NRSE Ref"))
    f["lease_type"] = kvs(m, label_rx("Type of Lease"))
    f["contract_no"] = kvs(m, label_rx("Contract No"))
    f["company"] = kvs(m, label_rx("Company Name"))
    f["site_name"] = kvs(m, label_rx("Site Name"), max_gap=40)
    f["region"] = field(m, label_rx("Swiftnet Region"), REGION)
    f["lat"] = clean_coord(field(m, label_rx("Latitude"), COORD, max_gap=30))
    f["lon"] = clean_coord(field(m, label_rx("Longitude"), COORD, max_gap=30))
    f["struct_name"] = kvs(m, label_rx("Structure Name"), max_gap=40)
    f["struct_hagl"] = kvs(m, label_rx("Structure Total HAGL"))
    f["pid"] = field(m, label_rx("Site PID"), PID)
    f["struct_id"] = field(m, label_rx("Structure ID"), PID)
    f["struct_cat"] = kvs(m, label_rx("Structure Category"))
    f["site_owner"] = kvs(m, label_rx("Site Owner")) or kvs(m, label_rx("Structure Owner"))
    sd = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", kvs(m, label_rx("Contract Start Date")))
    f["start_date"] = f"{int(sd.group(1)):02d} {sd.group(2)} {sd.group(3)}" if sd else ""
    st = re.match(r"^([A-Z]{2,5})\s*:", f["struct_name"] or "")
    f["stac"] = st.group(1) if st else ""
    if not f["struct_cat"] and not st and re.search(r"Mast|Tower|Monopole|Lattice|Pole|Concrete|Guyed", f["struct_name"] or "", re.I):
        f["struct_cat"] = f["struct_name"]
    c = f["contract_no"]
    mt = re.search(r"\b(7\d{7})\b", c or "") or re.search(r"T000-\d+", c or "")
    if c and not mt:
        notes.append(f'Contract No on quote is "{c}" — not a contract number')
    f["contract_no"] = (mt.group(1) if mt and mt.lastindex else (mt.group(0) if mt else ""))
    tm = re.search(r"\bT\s*000\s*[-/ ]\s*(\d+)", c or "", re.I)  # # 1.0.108: a "T000-5183" contract is the T000 reference too
    f["t000"] = tm.group(1) if tm else ""

    # antenna groups → Mast Space
    sec_re = re.compile(r"^(\d\s+)?(Sector Antenna Requirements|Other Antenna Requirements|RRU/Diplexer/Triplexer|Parabolic/MW Requirements|Equipment Accommodation)", re.I)
    eq_line = find_line(m, re.compile(r"^Equipment Accommodation", re.I))
    first_ant = find_line(m, re.compile(r"(Sector Antenna|Other Antenna|Parabolic/MW) Requirements|RRU/Diplexer", re.I))
    groups = []
    if first_ant >= 0:
        stop = eq_line if eq_line >= 0 else len(m.lines)
        section = ""
        i = first_ant
        while i < stop:
            s = m.lines[i].s
            sm = sec_re.search(s)
            if sm:
                section = sm.group(2)
            if not re.search(r"HAGL\s*\*+\s*:", s) or re.search(r"Structure Total", s, re.I):
                i += 1
                continue
            nxt = find_line(m, re.compile(r"HAGL\s*\*+\s*:|G-Total Area|Antenna\s*-\s*Charge|Antenna Requirements|RRU/Diplexer|Parabolic/MW"), i + 1, stop - 1)
            end = nxt if nxt >= 0 else stop
            hagl = num(kvs(m, r"HAGL\s*\*+\s*:", start=i, stop=i, max_gap=20))
            lm = re.match(r"^\s*(\S+)\s+HAGL", s)
            label = lm.group(1) if lm else ""
            dm = re.search(r"([\d]+[.,]\d+)\s*Di\b", m.lines[i + 1].s) if i + 1 < end else None
            dia = num(dm.group(1)) if dm else 0.0
            ck = kv(m, r"Number of (Antennas|Units)\s*:", start=i, stop=end - 1, max_gap=40)
            first = (ck.items[0].s if ck.items else ck.v) or ""
            cnt = num(first.split(" ")[0] if first else "")
            bundled = bool(re.search(r"Part of Bundle", ck.v or "", re.I))
            t_area = num(kvs(m, r"T-Area\s*:", start=i, stop=end - 1))
            total = num(amount_from(kvs(m, r"Total\s*\d+\s*:", start=i, stop=end - 1)))
            if cnt > 0 or t_area > 0 or total > 0:
                groups.append({"section": section, "label": label, "dia": dia, "hagl": hagl, "cnt": cnt, "t_area": t_area, "total": total, "bundled": bundled})
            i = end if end > i else i + 1
    g_area = num(kvs(m, r"G-Total Area in m\s*²?\s*:"))
    ant_charge = num(amount_from(kvs(m, r"Antenna\s*-\s*Charge\s*:")))
    ant_entries = comment_entries(m, r"Antenna Comments\s*:", r"Antenna\s*-\s*Charge", r"^Equipment Accommodation")
    ant_comments = [c["t"] for c in ant_entries]
    real = lambda c: c and c != "N/A"  # noqa: E731 — 1.0.107: an "N/A" comment holds its place, never a description or a line
    rental: list[dict] = []
    priced = [g for g in groups if not re.search(r"RRU", g["section"], re.I) and g["cnt"] > 0 and (g["total"] > 0 or (g["bundled"] and g["t_area"] > 0))]
    if priced and abs(sum(g["total"] for g in priced) - ant_charge) < 0.05:
        numbered = any(c["n"] is not None for c in ant_entries)
        per, left = comments_to_groups(ant_comments, priced, numbered)
        for k, g in enumerate(priced):
            desc = " – ".join(x for x in ("Part of Bundle" if g["bundled"] else "", *per[k]) if real(x))
            rental.append(_row("Mast Space", f"{g['t_area']:.2f}m²", desc, zar(g["total"])))
        for extra in filter(real, left):  # every comment is its own row
            rental.append(_row("Mast Space", "", extra, ""))
    elif ant_charge > 0:
        area = g_area or sum(g["t_area"] for g in groups)
        rental.append(_row("Mast Space", f"{area:.2f}m²", ant_comments[0] if ant_comments and real(ant_comments[0]) else "", zar(ant_charge)))
        for extra in filter(real, ant_comments[1:]):
            rental.append(_row("Mast Space", "", extra, ""))
    hs = sorted({g["hagl"] for g in groups if g["hagl"] > 0 and not re.search(r"RRU", g["section"], re.I)}) or sorted({g["hagl"] for g in groups if g["hagl"] > 0})
    f["inst_hagl"] = ", ".join(f"{h:.2f}" for h in hs)
    f["scope"] = " ".join(filter(real, ant_comments))

    # equipment accommodation → Ground Space
    eq_comments = comment_entries(m, r"^Comments\s*:", r"G-Total Area in m\s*²?\s*&\s*Charge", r"Accommodation Total|Electricity & Standby")
    f["equip_comments"] = " ".join(c["t"] for c in eq_comments if not c.get("na"))
    ground: list[dict] = []
    if eq_line >= 0:
        end_eq = find_line(m, re.compile(r"G-Total Area in m\s*²?\s*&\s*Charge", re.I), eq_line + 1)
        if end_eq < 0:
            end_eq = find_line(m, re.compile(r"Comments\s*:|Accommodation Total", re.I), eq_line + 1)
        if end_eq < 0:
            end_eq = len(m.lines)
        for i in range(eq_line + 1, end_eq):
            L = m.lines[i]
            lab = next((it for it in L.items if re.match(r"^Area in m", it.s)), None)
            if lab is None:
                continue
            band = [it for ln in m.lines if ln.p == L.p for it in ln.items if abs(it.y - L.y) <= 3.2 and it.x > lab.x]
            band.sort(key=lambda it: it.x)
            area_it = next((it for it in band if re.match(r"^[\d.,]+$", it.s)), None)
            xa = (area_it.x + 20) if area_it else 470
            am_items = [it for it in band if it is not area_it and (re.match(r"^R\b", it.s) or (it.x > xa and re.match(r"^[\d\s.,]+$", it.s)))]
            joined = " ".join(it.s for it in am_items)
            amt = num(amount_from(joined) or joined)
            area = num(area_it.s) if area_it else 0.0
            name = ""
            for j in range(i - 1, max(eq_line, i - 4), -1):
                Lj = m.lines[j]
                dig = next((it for it in Lj.items if re.match(r"^\d$", it.s) and it.x < 75), None)
                if dig:
                    name = " ".join(it.s for it in Lj.items if it.x > dig.x and it.x < 215 and not re.match(r"^\d$", it.s)).strip()
                    break
            if amt == 0 and area == 0:
                continue
            ground.append({"name": name, "area": area, "amt": amt})
    numbered = any(c["n"] is not None for c in eq_comments)
    used: set[int] = set()
    for k, g in enumerate(ground):
        if numbered:
            hit = next((ix for ix, c in enumerate(eq_comments) if c["n"] == k + 1), None)
        elif len(eq_comments) == len(ground) or k < len(eq_comments):
            hit = k if k < len(eq_comments) else None
        else:
            hit = None
        if hit is not None:
            used.add(hit)
        desc = eq_comments[hit]["t"] if hit is not None and not eq_comments[hit].get("na") else g["name"]
        rental.append(_row("Ground Space", f"{g['area']:.2f}m²", desc, zar(g["amt"])))
    for ix, c in enumerate(eq_comments):  # comments left over: each its own row
        if ix not in used and ground and not c.get("na"):
            rental.append(_row("Ground Space", "", c["t"], ""))

    # electricity
    ac_req = kvs(m, r"AC Electricity Required\s*:", max_gap=40)
    phase = kvs(m, r"AC Electricity Phase Required\s*:", max_gap=40)
    amps = kvs(m, r"AC Circuit Breaker Required in Amps\s*:", max_gap=40)
    standby = num(amount_from(kvs(m, r"Standby Charge:\s*(?=R\b|$)")))
    elec = num(amount_from(kvs(m, r"Electricity Charge:\s*(?=R\b|$)") or kvs(m, r"Consumption Charge:\s*(?=R\b|$)")))
    no_power = bool(re.match(r"^No", ac_req or "", re.I) or re.search(r"No Power", phase or "", re.I) or not num(amps))
    if no_power and not (standby or elec):
        amps_s, phase = "0 Amp", ""
    else:
        amps_s = re.sub(r"\s*Amp.*", "", amps or "", flags=re.I) + " Amp"
    power = {"ac_req": ac_req, "phase": phase, "amps": amps_s, "standby": standby, "elec": elec, "no_power": no_power}

    # road and once-off fees
    km = num(kvs(m, r"Road Length, Km\s*:", max_gap=20))
    road = {"km": f"{km:.2f}", "charge": num(amount_from(kvs(m, r"Road - Charge\s*:")))}
    once: list[dict] = []
    for label, rx in (("Application & Site Survey Fee", r"^Application & Site Surv(?:[ea]ry|ey) Fee"), ("Structural Analysis Fee", r"^Structural Analysis Fee"),
                      ("Other Costs", r"^Other Costs"), ("Supervision Fee", r"^Supervision fees")):
        li = find_line(m, re.compile(rx, re.I))
        if li < 0:
            continue
        amt = 0.0
        for j in (li - 1, li, li + 1):
            if 0 <= j < len(m.lines) and m.lines[j].p == m.lines[li].p and abs(m.lines[j].y - m.lines[li].y) <= 4:
                v = kvs(m, re.compile(r"Once-?Off\s+(Total\s+)?Charge\s*\(Excl\.?\s*VAT\)\s*:", re.I), start=j, stop=j, dy_up=4.5, dy_down=3)
                if num(amount_from(v) or v) > 0:
                    amt = num(amount_from(v) or v)
                    break
        if amt > 0:
            once.append(_row(label, "Yes", "Once-off", zar(amt)))
    totals = {"accommodation": num(amount_from(kvs(m, r"Accommodation Total\s*:"))), "power": num(amount_from(kvs(m, r"Total Power - Charge\s*:"))), "antenna": ant_charge}
    return {"fields": f, "rental": rental, "power": power, "road": road, "once": once, "totals": totals, "notes": notes}


# ---- a Notice to Proceed -------------------------------------------------------------------------------
def extract_ntp(m: Model) -> dict:
    f: dict = {}

    def pair(rx):
        k = kv(m, rx, max_gap=40)
        right = ""
        if k.line >= 0:
            L = m.lines[k.line]
            right = join_items([it for ln in m.lines if ln.p == L.p for it in ln.items if it.x > 330 and abs(it.y - L.y) <= 3 and not is_label(it.s)])
        return k.v, right

    f["site_name"], f["co_site_name"] = pair(r"Swiftnet Site Name\s*:")
    f["pid"], bs = pair(r"Swiftnet Site ID\s*:")
    f["bs_site_no"] = re.sub(r"^BS\s*", "", bs or "")
    f["region"], f["co_region"] = pair(r"Swiftnet Region\s*:")
    f["lat"] = clean_coord(field(m, re.compile(r"Site\s*Latitude\s*\**\s*:?", re.I), COORD, max_gap=20))  # 1.0.107: as loose as the quote's
    f["lon"] = clean_coord(field(m, re.compile(r"Site\s*Longitude\s*\**\s*:?", re.I), COORD, max_gap=20))
    f["col_id"] = kvs(m, r"Application Ref No\s*:", dy_up=6)
    for l in m.lines:
        mt = re.match(r"^Dear\s+(.+?)(\s+Date:|$)", l.s)
        if mt:
            f["company"] = mt.group(1).strip()
            break
    # 1.0.107: the Scope of Approved Work comes from the NTP (or the SOW), not the quote: a "Scope of (Approved) Work"
    # paragraph when the NTP has one, else the "Other … Description" line it always had
    f["scope"] = block_after(m, SCOPE_RX) or re.sub(r"^Description\s*", "", kvs(m, r"^Other\b", max_dx=400))
    return {"fields": f, "notes": []}


# ---- a Scope of Work (SOW) ----------------------------------------------------------------------------------
# 1.0.107: the scope of approved work, the same for every user: SOW → NTP → (last) the quote's antenna comments
SCOPE_RX = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?\s+)?(?:Scope\s+of\s+(?:the\s+)?(?:Approved\s+)?Works?|Description\s+of\s+(?:the\s+)?Works?|Work\s+Description)\b\s*:?", re.I)
STOP_RX = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?\s+)?(?:Sign(?:ed|ature)|Approved\s+by|Authori[sz]ed\s+by|Name\s*:|Date\s*:|Yours|Kind\s+regards|Regards|"
                     r"Terms|Conditions|Special\s+Conditions|Acceptance|Please\s+note|Note\s*:)", re.I)


def block_after(m: Model, rx: re.Pattern) -> str:
    """The paragraph under a heading such as "Scope of Work:" — its own line's rest and the lines below it, up to the
    next heading/label, a signing line, a wide gap, the footer or the next page. The title line of a SOW ("SCOPE OF
    WORK" on its own at the top) is skipped when a "Scope of Work:" heading follows."""
    hits = [i for i, L in enumerate(m.lines) if rx.search(L.s)]
    if not hits:
        return ""
    with_colon = [i for i in hits if ":" in rx.search(m.lines[i].s).group(0)]
    li = with_colon[0] if with_colon else hits[-1] if len(hits) > 1 else hits[0]
    L = m.lines[li]
    parts = []
    rest = L.s[rx.search(L.s).end():].strip(" :\t")
    if rest:
        parts.append(rest)
    last_y = L.y
    for ln in m.lines[li + 1:li + 40]:
        if ln.p != L.p or ln.y < 40 or last_y - ln.y > 30:
            break
        s = ln.s.strip()
        if STOP_RX.match(s) or (re.search(r":\s*$", s) and len(s) < 60) or re.match(r"^[A-Z][A-Za-z /&()-]{1,30}\s:\s", s) or rx.search(s):
            break
        parts.append(re.sub(r"^[•●◦▪\-–*]\s*", "", s))
        last_y = ln.y
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def extract_sow(m: Model) -> dict:
    return {"fields": {"scope": block_after(m, SCOPE_RX)}, "notes": []}


# ---- an existing (signed) agreement --------------------------------------------------------------------
def _fix_digits(s: str) -> str:
    s = re.sub(r"(\d)\s+(?=\d(\D|$))", r"\1", s or "")
    return re.sub(r"(\d)\s*\.\s*(\d)", r"\1.\2", s)


def extract_agreement(m: Model) -> dict:
    f: dict = {}
    g = lambda rx, **o: kvs(m, re.compile(rx, re.I), **{"max_gap": 40, **o})  # noqa: E731
    f["site_name"] = g(r"Site\s*name\s*:")
    f["stac"] = g(r"S\s*T\s*A\s*C\s*Code\s*:")
    f["pid"] = g(r"Site\s*(ID|PID)\s*:")
    f["region"] = g(r"^Site.*Region\s*:") or g(r"Region\s*:")
    f["lat"] = re.sub(r"-\s+", "-", g(r"Latitude\s*:"))
    f["lon"] = re.sub(r"-\s+", "-", g(r"Longitude\s*:"))
    f["col_id"] = g(r"Co\s*-\s*locate\s*ID\s*:")
    ci = find_line(m, re.compile(r"Contract\s*no\s*\.?\s*:", re.I))
    if ci >= 0:
        block = " ".join([g(r"Contract\s*no\s*\.?\s*:")] + [m.lines[j].s for j in range(ci + 1, min(ci + 3, len(m.lines)))])
        mt = re.search(r"\b7\d{7}\b", block)
        f["contract_no"] = mt.group(0) if mt else ""
        mt = re.search(r"T\s*100\s*[-/ ]\s*(\d+)", block, re.I)  # 1.0.108: "T100/9130", "T100 / 9130", "T100-9130"
        f["t100"] = mt.group(1) if mt else ""
        mt = re.search(r"T\s*000\s*[-/ ]\s*(\d+)", block, re.I)
        f["t000"] = mt.group(1) if mt else ""
    cu = find_line(m, re.compile(r"CO\s*-\s*USER\s*SITE\s*DETAILS", re.I))
    if cu >= 0:
        f["co_site_name"] = g(r"Site\s*Name\s*:", start=cu)
        f["bs_site_no"] = re.sub(r"^[:\s]*(No\s*)?", "", g(r"BS\s*/\s*Site\s*\.?\s*:?\s*No\s*:?", start=cu, max_gap=30))
        f["co_region"] = g(r"Region\s*:", start=cu)
    sd = re.search(r"(\d{1,2})\s*([A-Za-z]+)\s*(\d{4})", _fix_digits(g(r"Commencement\s*Date\s*:")))
    f["start_date"] = f"{int(sd.group(1)):02d} {sd.group(2)} {sd.group(3)}" if sd else ""
    f["mast_cat"] = g(r"Mast\s*Category\s*:")
    f["struct_hagl"] = re.sub(r"m$", "", _fix_digits(g(r"Mast\s*Height\s*Above.*:")).strip())
    f["inst_hagl"] = re.sub(r"m$", "", _fix_digits(g(r"Installation\s*Height\s*on\s*Mast.*:")).strip())
    si = find_line(m, re.compile(r"SITE SCHEDULE AGREEMENT", re.I))
    if si >= 0 and si + 1 < len(m.lines):
        f["lease_type"] = re.sub(r"\s*-\s*", "-", m.lines[si + 1].s).replace("( ", "(").replace(" )", ")")
    for l in m.lines:
        if re.search(r"herein referred to as the .Co\s*-\s*User", l.s, re.I):
            f["company"] = re.sub(r",?\s*herein referred.*$", "", l.s, flags=re.I).strip()
            break
    tables: dict[str, list] = {}
    for li, L in enumerate(m.lines):
        head = next((it for it in L.items if re.match(r"^Leased Item", it.s, re.I)), None)
        if head is None:
            continue
        near = [it for ln in m.lines if ln.p == L.p for it in ln.items if abs(it.y - L.y) <= 10]
        req_x = next((it.x for it in near if re.match(r"^Requirement", it.s, re.I)), head.x + 85)
        desc_x = next((it.x for it in near if re.match(r"^Comment", it.s, re.I)), req_x + 85)
        amt_x = next((it.x for it in near if re.match(r"^Amount", it.s, re.I)), desc_x + 190)
        ti = find_line(m, re.compile(r"^Total .*Charges Payable", re.I), li + 1)
        if ti < 0:
            continue
        tl = m.lines[ti]
        kind = "t1" if re.search(r"Rental", tl.s) else "t2" if re.search(r"Electricity", tl.s) else "t3" if re.search(r"per\s*Month", tl.s) else "t4"
        top = max(it.y for it in near)
        body = [it for ln in m.lines[li + 1:ti] if ln.p == L.p for it in ln.items if it.y < top - 4 and it.y > tl.y + 2 and it.x >= head.x - 10]
        anchors = sorted((it for it in body if it.x >= amt_x - 12), key=lambda it: -it.y)
        rows: list[dict] = []
        for a in anchors:
            if rows and abs(rows[-1]["y"] - a.y) <= 3:
                continue
            rows.append({"y": a.y, "amt": a.s, "item": [], "req": [], "desc": []})
        cuts = row_cuts([r["y"] for r in rows], [it.y for it in body])
        for it in body:
            if it.x >= amt_x - 12 or not rows:
                continue
            r = rows[sum(1 for c in cuts if it.y < c)]  # 1.0.107: by the row it sits in, not the nearest amount
            (r["item"] if it.x < req_x - 5 else r["req"] if it.x < desc_x - 5 else r["desc"]).append(it.s)
        tables[kind] = [_row(re.sub(r"\s*&\s*", " & ", " ".join(r["item"])), re.sub(r"^N\s+o$", "No", re.sub(r"\s*m²", "m²", _fix_digits(" ".join(r["req"])))),
                             re.sub(r"\s*-\s*", "-", " ".join(r["desc"])), zar(r["amt"].replace(" ", ""))) for r in rows]
    return {"fields": f, "tables": tables, "notes": []}


def row_cuts(anchors: list[float], ys: list[float]) -> list[float]:
    """1.0.107: where one table row ends and the next starts, between each two amounts (top to bottom): at the
    widest gap between the text lines there (a row's own wrapped lines sit closer together than two rows). With the
    nearest amount instead, a description wrapped over three lines spilt into the rows above and below."""
    levels: list[float] = []
    for y in sorted(set(ys) | set(anchors), reverse=True):
        if not levels or levels[-1] - y > 2.2:
            levels.append(y)
    cuts = []
    for hi, lo in zip(anchors, anchors[1:]):
        inside = [y for y in levels if lo - 2.2 <= y <= hi + 2.2]
        gaps = [(inside[k] - inside[k + 1], k) for k in range(len(inside) - 1)]
        if not gaps:
            cuts.append((hi + lo) / 2)
            continue
        widest = max(g for g, _ in gaps)
        k = max(k for g, k in gaps if g >= widest - 0.6)  # a tie: just above the next row's amount
        cuts.append((inside[k] + inside[k + 1]) / 2)
    return cuts


# ---- put several PDFs together -------------------------------------------------------------------------
def read_pdfs(files: list[tuple[str, bytes]]) -> dict:
    """[(filename, bytes)] → {"fields", "tables": {t1..t4}, "notes", "files": [{name, kind, pages}]}."""
    quotes, ntps, ags, sows, info, notes = [], [], [], [], [], []
    refs: dict = {}
    for name, data in files:
        try:
            m = model_from_pdf(data, name)
        except Exception as e:  # noqa: BLE001 — one bad file never stops the others
            info.append({"name": name, "kind": "unreadable", "pages": 0})
            notes.append(f"{name}: could not be read ({str(e)[:80]})")
            continue
        kind = classify(m)
        info.append({"name": name, "kind": kind, "pages": m.pages})
        if kind == "unknown":
            notes.append(f"{name}: not recognised as a Swiftnet quote, NTP, SOW or agreement — skipped")
            continue
        r = {"quote": extract_quote, "ntp": extract_ntp, "agreement": extract_agreement, "sow": extract_sow}[kind](m)
        r["name"] = name
        ref = {**parse_ref(footer_ref(m)), **parse_ref(name)} if kind not in ("ntp", "sow") else parse_ref(name)
        for k, v in ref.items():
            refs.setdefault(k, v)
        {"quote": quotes, "ntp": ntps, "agreement": ags, "sow": sows}[kind].append(r)
        notes.extend(f"{name}: {n}" for n in r.get("notes", []))
    return merge(quotes, ntps, ags, refs, notes, info, sows)


def first_escalation_year(start: date) -> int:
    """1.0.107 (the site schedules owner, 20 Oct): a lease that starts from 01 January to 31 March is first escalated
    on 01 April of the same year; one that starts later, on 01 April of the next year."""
    return start.year if start.month <= 3 else start.year + 1


def merge(quotes: list, ntps: list, ags: list, refs: dict, notes: list, info: list, sows: Optional[list] = None) -> dict:
    sows = sows or []
    def first(key, *groups):
        for grp in groups:
            for r in grp:
                v = str((r.get("fields") or {}).get(key) or "").strip()
                if v:
                    return v
        return ""

    f: dict = {}
    f["lease_type"] = normalise_lease(first("lease_type", ags, quotes))
    if not f["lease_type"] and refs.get("lease_code"):
        f["lease_type"] = {"N": "New Application", "R": "Renewal", "RA": "Re-Assessment", "NA": "New Re-Assessment", "MOU": "MOU"}.get(refs["lease_code"], "")
    f["company"] = first("company", ags, quotes, ntps)
    f["col_id"] = first("col_id", ags, quotes, ntps) or refs.get("col_id", "")
    c = first("contract_no", ags, quotes)
    f["contract_no"] = c if re.match(r"^7\d{7}$", c) or not refs.get("contract") else refs["contract"]
    t100 = first("t100", ags) or refs.get("t100", "")
    t000 = first("t000", ags) or refs.get("t000", "") or first("t000", quotes)
    f["t100_ref"], f["t000_ref"] = ref_text("t100_ref", t100), ref_text("t000_ref", t000)  # # 1.0.108: always "T100/<n>", "T000/<n>"
    for k in ("site_name", "stac", "pid", "region", "lat", "lon", "struct_name", "struct_cat", "struct_hagl", "site_owner"):
        f[k] = first(k, ags, quotes, ntps)
    for k in ("lat", "lon"):
        f[k] = clean_coord(f[k])
    if not f["pid"] and first("struct_id", quotes):  # 1.0.107: some quotes give the number only as "Structure ID"
        f["pid"] = first("struct_id", quotes)
        notes.append("Site PID taken from the quote's Structure ID — check it")
    f["mast_cat"] = first("mast_cat", ags) or f["struct_cat"]
    f["mast_hagl"] = f["struct_hagl"]
    inst = first("inst_hagl", ags)
    if not inst:
        hs = sorted({h.strip() for q in quotes for h in (q["fields"].get("inst_hagl") or "").split(",") if h.strip()}, key=lambda h: num(h))
        inst = "m, ".join(hs)
        if not inst and quotes and not any(r["item"] == "Mast Space" for q in quotes for r in q["rental"]):
            inst = "0.00"
    f["inst_hagl"] = inst
    for k in ("co_site_name", "bs_site_no", "co_region"):
        f[k] = first(k, ags, ntps)
    f["start_date"] = first("start_date", ags, quotes)
    if not f["start_date"] and refs.get("start"):
        d, mth, y = refs["start"]
        f["start_date"] = long_date(d, mth, y)
    f["term_years"], f["term_months"] = 5, 0
    if refs.get("start") and refs.get("end"):
        (d1, m1, y1), (d2, m2, y2) = refs["start"], refs["end"]
        from datetime import timedelta

        try:
            e = date(y2, m2, d2) + timedelta(days=1)
            months = (e.year - y1) * 12 + (e.month - m1)
            if 0 < months < 1200:
                f["term_years"], f["term_months"] = months // 12, months % 12
        except ValueError:
            pass
    # 1.0.107: the scope of approved work from the SOW, else the NTP; the quote's antenna comments only as a last resort, marked
    f["scope"], f["scope_src"] = first("scope", sows), "sow"
    if not f["scope"]:
        f["scope"], f["scope_src"] = first("scope", ntps), "ntp"
    if not f["scope"]:
        f["scope"], f["scope_src"] = " ".join(q["fields"].get("scope", "") for q in quotes if q["fields"].get("scope")).strip(), "quote"
        if f["scope"]:
            notes.append("Scope of approved work: no SOW or NTP gave it — taken from the quotation's antenna comments; check it")
    if not f["scope"]:
        f["scope_src"] = ""
    f["equip_comments"] = " ".join(q["fields"].get("equip_comments", "") for q in quotes if q["fields"].get("equip_comments")).strip()
    if refs.get("co_code"):
        f["co_code"] = refs["co_code"]
    if refs.get("lease_code"):
        f["lease_code"] = refs["lease_code"]

    t1 = [r for q in quotes for r in q["rental"]]
    t1 = [r for r in t1 if r["item"] == "Mast Space"] + [r for r in t1 if r["item"] != "Mast Space"]
    t2: list = []
    powered = [q for q in quotes if not q["power"]["no_power"]]
    pq = (powered or [q for q in quotes if q["power"]["elec"] or q["power"]["standby"]] or quotes[:1])
    if pq:
        p = pq[0]["power"]
        na = p["no_power"] and (p["standby"] + p["elec"]) == 0
        t2 = [_row("Electricity", "0 Amp" if na else p["amps"], "N/A" if na else " – ".join(x for x in (p["phase"], p["ac_req"]) if x), zar(p["elec"])),
              _row("Standby Electricity", "0 Amp" if na else p["amps"], "N/A" if na else (p["phase"] or "N/A"), zar(p["standby"]))]
        if len(powered) > 1:
            notes.append("More than one quote has electricity — only the first was used")
    t3: list = []
    if quotes:
        km = next((q["road"]["km"] for q in quotes if num(q["road"]["km"]) > 0), quotes[0]["road"]["km"])
        road = sum(q["road"]["charge"] for q in quotes)
        t3 = [_row("Escort Fees", "N/A", "N/A", "R 0.00"), _row("Access Road Usage", f"{km} Km", "Access road" if road > 0 else "N/A", zar(road))]
    t4 = [r for q in quotes for r in q["once"]]
    tables = {"t1": t1, "t2": t2, "t3": t3, "t4": t4}
    if ags:
        for k, rows in (ags[0].get("tables") or {}).items():
            if not tables.get(k):
                tables[k] = rows
    for r in tables.get("t2") or []:  # 1.0.107: no standby rental when it is R0.00 — the electricity is metered
        if re.match(r"^\s*Standby\s+Electricity", r.get("item") or "", re.I) and num(r.get("amt")) == 0:
            r["req"], r["desc"] = "0 Amp", "N/A"
    for q in quotes:
        s1 = sum(num(r["amt"]) for r in q["rental"])
        acc = q["totals"]["accommodation"] + q["totals"]["antenna"]
        if q["totals"]["accommodation"] and abs(s1 - acc) > 0.05 and abs(s1 - q["totals"]["accommodation"]) > 0.05:
            notes.insert(0, f"{q['name']}: rental rows add up to {fmt_zar(s1)} but the quote's Accommodation Total is {fmt_zar(q['totals']['accommodation'])}")
        if q["totals"]["power"] and abs(q["power"]["elec"] + q["power"]["standby"] - q["totals"]["power"]) > 0.05:
            notes.insert(0, f"{q['name']}: electricity rows do not match Total Power Charge {fmt_zar(q['totals']['power'])}")
    if f["contract_no"]:
        notes = [n for n in notes if "not a contract number" not in n]
    if not ntps and not ags and quotes:
        notes.append("Co-User site details are not on the quote — add the NTP PDF or type them in")
    seen, uniq = set(), []
    for n in notes:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    return {"fields": f, "tables": tables, "notes": uniq, "files": info}
