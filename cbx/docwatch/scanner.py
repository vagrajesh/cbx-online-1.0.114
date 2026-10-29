"""Find an expiry date in document text. Heuristics first (regexes for the phrases South African
certificates actually use, plus the date formats they use); the model only confirms what we found.
Every answer carries the quote it came from, so it can be checked against the text."""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Optional

MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], 1)}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
MONTHS["sept"] = 9

_MON = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
DATE_PATTERNS = [
    # 31/03/2027, 31-03-2027, 31.03.2027
    (re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})\b"), "dmy"),
    # 2027-03-31 / 2027/03/31 / 2027.03.31
    (re.compile(r"\b(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})\b"), "ymd"),
    # 31 March 2027 / 31st March, 2027 / 31 Mar 2027
    (re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + _MON + r")[,.]?\s+(\d{4})\b", re.I), "dMy"),
    # March 31, 2027
    (re.compile(r"\b(" + _MON + r")\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", re.I), "Mdy"),
]
LABELS = re.compile(
    r"(valid\s+until|valid\s+till|valid\s+to|valid\s+up\s+to|expiry\s+date|expiry|expires\s+on|expires|expiration\s+date|expiration|"
    r"renewal\s+date|date\s+of\s+expiry|next\s+(?:inspection|service|renewal)\s+(?:due|date)|due\s+for\s+renewal|expire[sd]?\s+on|"
    r"geldig\s+tot|vervaldatum)",
    re.I,
)
ISSUED = re.compile(r"(date\s+of\s+issue|issued\s+on|issue\s+date|issued|date\s+of\s+inspection|inspection\s+date)[:\s]*", re.I)
VALID_FOR = re.compile(r"valid\s+for\s+(?:a\s+period\s+of\s+)?(\d{1,3}|one|two|three|five|twelve|six)\s+(month|year)s?", re.I)
WORDS = {"one": 1, "two": 2, "three": 3, "five": 5, "six": 6, "twelve": 12}
DOC_TYPES = [
    ("certificate of compliance", "Certificate of compliance"), ("coc", "Certificate of compliance"), ("fire", "Fire equipment certificate"),
    ("lift", "Lift inspection"), ("elevator", "Lift inspection"), ("generator", "Generator permit"), ("licence", "Licence"), ("license", "Licence"),
    ("permit", "Permit"), ("service level agreement", "Contract"), ("sla", "Contract"), ("contract", "Contract"), ("lease", "Lease"),
    ("insurance", "Insurance"), ("tax clearance", "Tax clearance"), ("bee", "B-BBEE certificate"), ("b-bbee", "B-BBEE certificate"), ("certificate", "Certificate"),
]


def _mk(y: int, m: int, d: int) -> Optional[date]:
    try:
        if not (1990 <= y <= 2100):
            return None
        return date(y, m, d)
    except ValueError:
        return None


def parse_date(s: str) -> Optional[date]:
    """First date in `s`, or None."""
    for rx, kind in DATE_PATTERNS:
        m = rx.search(s)
        if not m:
            continue
        g = m.groups()
        if kind == "dmy":
            d, mo, y = int(g[0]), int(g[1]), int(g[2])
            if mo > 12 and d <= 12:  # mm/dd slipped in
                d, mo = mo, d
            return _mk(y, mo, d)
        if kind == "ymd":
            return _mk(int(g[0]), int(g[1]), int(g[2]))
        if kind == "dMy":
            return _mk(int(g[2]), MONTHS.get(g[1].lower()[:3], 0) or 0, int(g[0]))
        if kind == "Mdy":
            return _mk(int(g[2]), MONTHS.get(g[0].lower()[:3], 0) or 0, int(g[1]))
    return None


def _snippet(text: str, start: int, end: int) -> str:
    a = text.rfind("\n", 0, start) + 1
    b = text.find("\n", end)
    b = len(text) if b < 0 else b
    return text[a:b].strip()


def find_expiry(text: str) -> dict:
    """{"found", "expiry" (date), "quote", "how": label|issued+valid_for, "doc_type", "entity"}"""
    out = {"found": False, "expiry": None, "quote": "", "how": "", "check": "", "doc_type": guess_doc_type(text), "entity": guess_entity(text)}
    if not text:
        return out
    best = None
    for m in LABELS.finditer(text):
        window = text[m.end() : m.end() + 60]
        d = parse_date(window)
        if d is None:
            continue
        dm = next((rx.search(window) for rx, _k in DATE_PATTERNS if rx.search(window)), None)
        end = m.end() + (dm.end() if dm else 12)
        a = text.rfind("\n", 0, m.start()) + 1
        quote = text[a:end].strip()
        # prefer the latest of several labelled dates (a renewal beats a first issue)
        if best is None or d > best[0]:
            best = (d, quote[:200], "label")
    if best is None:
        vf = VALID_FOR.search(text)
        iss = ISSUED.search(text)
        if vf and iss:
            issued = parse_date(text[iss.end() : iss.end() + 40])
            n = WORDS.get(vf.group(1).lower(), None) or int(vf.group(1))
            if issued:
                months = n * (12 if vf.group(2).lower().startswith("year") else 1)
                y, mo = issued.year + (issued.month - 1 + months) // 12, (issued.month - 1 + months) % 12 + 1
                try:
                    exp = date(y, mo, issued.day)
                except ValueError:
                    exp = date(y, mo, 28)
                exp = exp - timedelta(days=1)
                best = (exp, (_snippet(text, iss.start(), iss.end() + 20) + " … " + vf.group(0))[:200], "issued+valid_for")
    if best:
        out.update({"found": True, "expiry": best[0], "quote": best[1], "how": best[2]})
        if best[2] == "label":
            out["check"] = cross_check(text, best[0])
    return out


def _add_months(d: date, months: int) -> date:
    y, mo = d.year + (d.month - 1 + months) // 12, (d.month - 1 + months) % 12 + 1
    try:
        return date(y, mo, d.day)
    except ValueError:
        return date(y, mo, 28)


def cross_check(text: str, expiry: date) -> str:
    """Independent arithmetic check (the old DocWatch's 'rule zero'): if the document states an issue
    date and a validity period, issue + period must land on the expiry we read. '' when the document
    gives us nothing to check against; 'verified' or 'disagrees' otherwise."""
    vf = VALID_FOR.search(text)
    iss = ISSUED.search(text)
    if not (vf and iss):
        return ""
    issued = parse_date(text[iss.end() : iss.end() + 40])
    if issued is None:
        return ""
    n = WORDS.get(vf.group(1).lower(), None) or int(vf.group(1))
    months = n * (12 if vf.group(2).lower().startswith("year") else 1)
    predicted = _add_months(issued, months)
    # certificates write either the anniversary or the day before it
    return "verified" if abs((predicted - expiry).days) <= 1 else "disagrees"


def date_candidates(text: str, limit: int = 6) -> list[dict]:
    """Every distinct date in the text with the line it sits on, soonest-in-the-future first — shown to
    the person when nothing is labelled as an expiry, so they can pick instead of retyping."""
    seen: dict[str, dict] = {}
    for rx, kind in DATE_PATTERNS:
        for m in rx.finditer(text or ""):
            d = parse_date(m.group(0))
            if d is None:
                continue
            iso = d.isoformat()
            if iso not in seen:
                seen[iso] = {"date": iso, "quote": _snippet(text, m.start(), m.end())[:160]}
    rows = list(seen.values())
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows[:limit]


def guess_doc_type(text: str) -> str:
    low = (text or "").lower()[:4000]
    for key, label in DOC_TYPES:
        if re.search(r"\b" + re.escape(key) + r"\b", low):
            return label
    return ""


def guess_entity(text: str) -> str:
    m = re.search(r"(?:site|premises|property|client|address|building|location)\s*[:\-]\s*([^\n]{3,80})", text or "", re.I)
    return m.group(1).strip() if m else ""
