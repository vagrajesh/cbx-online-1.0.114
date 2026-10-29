"""Typed parsing for South African documents.

Money on SA bills comes in every shape: "R 1 250,00", "1,250.00", "1 250.00", "R1.250,00",
"1 250.00-" (trailing minus), "1 250.00 CR" / "DR", "(1 250.00)". The rule: the LAST separator
followed by exactly two digits is the decimal mark; everything else is a thousands separator.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

_SPACES = "    "
_MONEY_CORE = r"\d{1,3}(?:[    '.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?"
# A money-looking token: optional R, optional sign/bracket, digits with separators, optional CR/DR or trailing minus.
MONEY_RE = re.compile(
    rf"(?<![\d/\-:.])(?:R\s?)?(?:[-−]\s?)?\(?(?:{_MONEY_CORE})\)?(?:\s?(?:CR|DR|Cr|Dr))?(?:\s?[-−])?(?![\d/%]|\s?%)",
)
NUMBER_RE = re.compile(rf"(?<![\d/\-:.])[-−]?(?:{_MONEY_CORE})(?![\d/]|\s?%)")
PCT_RE = re.compile(r"\d+(?:[.,]\d+)?\s?%")
DATE_RE = re.compile(
    r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4}|\d{2})\b"  # 01/06/2026
    r"|\b(\d{4})[/.\-](\d{1,2})[/.\-](\d{1,2})\b"  # 2026-06-01
    r"|\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\b"  # 01 June 2026
    r"|\b([A-Za-z]{3,9})\s+(\d{4})\b"  # June 2026 / Jun 2026
    r"|\b(\d{4})[/\-](\d{2})\b",  # 2026/06
)
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
RANGE_SEP = re.compile(r"\s*(?:-|–|—|to|until|till)?\s*", re.I)  # two adjacent dates also read as a range


def _clean_spaces(s: str) -> str:
    for ch in _SPACES:
        s = s.replace(ch, " ")
    return s.strip()


def parse_money(raw: str) -> Optional[float]:
    """'R 1 250,00' → 1250.0. None if it does not look like money."""
    if raw is None:
        return None
    s = _clean_spaces(str(raw))
    if not s or "%" in s:
        return None
    neg = False
    if re.search(r"\bCR\b", s, re.I):
        neg = True
    s = re.sub(r"\b(CR|DR)\b", "", s, flags=re.I)
    s = s.replace("R", "").replace("ZAR", "").replace("r", "")
    s = s.strip()
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1]
    if s.endswith("-") or s.endswith("−"):
        neg, s = True, s[:-1]
    if s.startswith("-") or s.startswith("−"):
        neg, s = True, s[1:]
    s = s.replace(" ", "").replace("'", "")
    if not re.fullmatch(r"\d[\d.,]*", s):
        return None
    # decide the decimal mark: the last separator that is followed by exactly 1-2 digits at the end
    m = re.search(r"([.,])(\d{1,2})$", s)
    if m:
        intpart = s[: m.start()]
        dec = m.group(2)
        intpart = re.sub(r"[.,]", "", intpart)
        if not intpart:
            intpart = "0"
        try:
            v = float(f"{intpart}.{dec}")
        except ValueError:
            return None
    else:
        digits = re.sub(r"[.,]", "", s)
        if not digits:
            return None
        v = float(digits)
    return -v if neg else v


def parse_number(raw: str) -> Optional[float]:
    """Plain numbers ('9 870', '1,204', '312.5'). Falls back to the money parser (same shapes)."""
    if raw is None:
        return None
    s = _clean_spaces(str(raw))
    s = re.sub(r"\s*(kWh|kVA|kva|kwh|kW|kw|kl|KL|m3|m³|units?)\s*$", "", s, flags=re.I)
    if "%" in s:
        return None
    v = parse_money(s)
    return v


def parse_date(raw: str) -> Optional[str]:
    """Return an ISO date ('2026-06-01'), a month ('2026-06') or a range ('2026-06-01 – 2026-06-30')."""
    if raw is None:
        return None
    s = _clean_spaces(str(raw))
    found = [(m.start(), m.end(), _date_from_match(m)) for m in DATE_RE.finditer(s)]
    found = [f for f in found if f[2]]
    if not found:
        return None
    if len(found) >= 2 and RANGE_SEP.fullmatch(s[found[0][1] : found[1][0]] or "x"):
        return f"{found[0][2]} – {found[1][2]}"
    return found[0][2]


def _date_from_match(m: re.Match) -> Optional[str]:
    g = m.groups()
    try:
        if g[0]:
            d, mo, y = int(g[0]), int(g[1]), int(g[2])
            if y < 100:
                y += 2000
            if mo > 12 and d <= 12:  # US-style slip
                d, mo = mo, d
            return date(y, mo, d).isoformat()
        if g[3]:
            return date(int(g[3]), int(g[4]), int(g[5])).isoformat()
        if g[6]:
            mo = MONTHS.get(g[7][:3].lower())
            if not mo:
                return None
            return date(int(g[8]), mo, int(g[6])).isoformat()
        if g[9]:
            mo = MONTHS.get(g[9][:3].lower())
            if not mo:
                return None
            return f"{int(g[10]):04d}-{mo:02d}"
        if g[11]:
            mo = int(g[12])
            if 1 <= mo <= 12:
                return f"{int(g[11]):04d}-{mo:02d}"
    except ValueError:
        return None
    return None


def strip_dates(text: str) -> str:
    """Blank out dates so money/number regexes do not bite into them."""
    return DATE_RE.sub(lambda m: " " * (m.end() - m.start()), text)


def find_money(text: str) -> list[tuple[str, float]]:
    """All money-looking tokens in `text` with their parsed value, in order."""
    out = []
    t = strip_dates(text)
    t = PCT_RE.sub(lambda m: " " * (m.end() - m.start()), t)
    for m in MONEY_RE.finditer(t):
        raw = m.group(0).strip()
        if not re.search(r"\d", raw):
            continue
        v = parse_money(raw)
        if v is not None:
            out.append((raw, v))
    return out


def find_numbers(text: str) -> list[tuple[str, float]]:
    out = []
    t = strip_dates(text)
    t = PCT_RE.sub(lambda m: " " * (m.end() - m.start()), t)
    for m in NUMBER_RE.finditer(t):
        raw = m.group(0).strip()
        v = parse_number(raw)
        if v is not None:
            out.append((raw, v))
    return out


def find_dates(text: str) -> list[tuple[str, str]]:
    out = []
    s = _clean_spaces(text)
    matches = [(m.start(), m.end(), m.group(0), _date_from_match(m)) for m in DATE_RE.finditer(s)]
    matches = [m for m in matches if m[3]]
    i = 0
    while i < len(matches):
        a = matches[i]
        if i + 1 < len(matches) and RANGE_SEP.fullmatch(s[a[1] : matches[i + 1][0]] or "x"):
            b = matches[i + 1]
            out.append((s[a[0] : b[1]], f"{a[3]} – {b[3]}"))
            i += 2
        else:
            out.append((a[2], a[3]))
            i += 1
    return out


def parse_typed(ctype: str, raw: str):
    """Parse `raw` as the column type. Returns (value, ok)."""
    if raw is None:
        return None, False
    if ctype == "money":
        v = parse_money(raw)
        return (round(v, 2), True) if v is not None else (raw, False)
    if ctype == "number":
        v = parse_number(raw)
        if v is None:
            return raw, False
        return (int(v) if float(v).is_integer() else v), True
    if ctype == "date":
        v = parse_date(raw)
        return (v, True) if v else (raw, False)
    s = _clean_spaces(str(raw)).strip(" :;-·|")
    return (s, bool(s))


def normalise_text(s: str) -> str:
    """Case- and whitespace-insensitive form used for provenance and verification."""
    return re.sub(r"\s+", "", str(s or "").lower())
