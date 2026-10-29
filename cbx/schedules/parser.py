"""Read a Swiftnet quotation. Every field comes back as {value, quote, confidence}; anything unclear
becomes an issue the admin confirms in plain English. Money is South African: "R 1 250,00" is fine,
"R1,250,00" (comma as thousands AND decimal) is read as 1250.00 but flagged."""
from __future__ import annotations

import re
from typing import Optional

FIELDS = [
    ("site", "Site name"),
    ("quote_no", "Quote number"),
    ("province", "Province"),
    ("accommodation", "Accommodation (per month)"),
    ("antennas", "Antennas"),
    ("electricity", "Electricity (per month)"),
    ("access_road", "Access road"),
    ("once_off", "Once-off fee"),
    ("total_monthly", "Total per month"),
    ("total_once_off", "Total once-off"),
]
MONEY_FIELDS = {"accommodation", "electricity", "access_road", "once_off", "total_monthly", "total_once_off"}
LABELS = {
    "site": r"site\s*(?:name)?",
    "quote_no": r"(?:quotation|quote)\s*(?:no\.?|number|ref(?:erence)?|#)?",
    "province": r"province",
    "accommodation": r"(?:equipment\s+)?accommodation(?:\s+fee)?",
    "antennas": r"antennas?(?:\s+\(?\w*\)?)?",
    "electricity": r"electricity(?:\s+(?:supply|fee|charge))?",
    "access_road": r"access\s+road(?:\s+(?:fee|contribution|maintenance))?",
    "once_off": r"(?:once[\s\-]?off|installation|establishment)(?:\s+(?:fee|charge|cost))?",
    "total_monthly": r"total\s+(?:monthly|per\s+month)|monthly\s+total",
    "total_once_off": r"total\s+once[\s\-]?off|once[\s\-]?off\s+total",
}
_MONEY = re.compile(r"R\s?[\d\s .,]*\d")


def parse_money(raw: str) -> dict:
    """{'value': float|None, 'ambiguous': bool, 'display': 'R 1 250.00'}"""
    s = raw.replace(" ", " ").strip()
    s = re.sub(r"^R\s*", "", s, flags=re.I).strip()
    s = s.replace(" ", "")
    if not s:
        return {"value": None, "ambiguous": False, "display": ""}
    ambiguous = False
    if re.fullmatch(r"\d{1,3}(,\d{3})*,\d{2}", s) and s.count(",") >= 2:
        # "1,250,00" — commas as thousands and decimal
        head, dec = s.rsplit(",", 1)
        num = head.replace(",", "") + "." + dec
        ambiguous = True
    elif re.fullmatch(r"\d+,\d{2}", s):
        num = s.replace(",", ".")  # SA decimal comma
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+,\d{2}", s):
        num = s.replace(".", "").replace(",", ".")  # European style
    elif re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d{1,2})?", s):
        num = s.replace(",", "")
    elif re.fullmatch(r"\d+(\.\d{1,2})?", s):
        num = s
    else:
        digits = re.sub(r"[^\d]", "", s)
        if not digits:
            return {"value": None, "ambiguous": False, "display": ""}
        num = digits[:-2] + "." + digits[-2:] if len(digits) > 2 else digits
        ambiguous = True
    try:
        v = round(float(num), 2)
    except ValueError:
        return {"value": None, "ambiguous": True, "display": ""}
    return {"value": v, "ambiguous": ambiguous, "display": fmt_money(v)}


def fmt_money(v: Optional[float]) -> str:
    if v is None:
        return ""
    whole, dec = f"{v:,.2f}".split(".")
    return "R " + whole.replace(",", " ") + "." + dec


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _find_line(lines: list[str], label_rx: str) -> Optional[tuple[str, str]]:
    rx = re.compile(r"^\s*(?:\d+\.\s*)?(" + label_rx + r")\b\s*[:\-–]?\s*(.*)$", re.I)
    for ln in lines:
        m = rx.match(ln)
        if m:
            return ln, m.group(2).strip()
    # label anywhere in the line
    rx2 = re.compile(r"\b(" + label_rx + r")\b", re.I)
    for ln in lines:
        if rx2.search(ln):
            return ln, rx2.split(ln, 1)[-1].strip(" :-–")
    return None


def parse(text: str) -> dict:
    lines = _lines(text)
    fields: dict[str, dict] = {}
    issues: list[dict] = []
    for key, label in FIELDS:
        hit = _find_line(lines, LABELS[key])
        if hit is None:
            fields[key] = {"value": None, "quote": "", "confidence": 0.0, "label": label}
            if key in ("site", "quote_no", "electricity", "accommodation", "total_monthly"):
                issues.append({"field": key, "text": f"I couldn't find the {label.lower()} anywhere in the quote. Please type it.", "suggestion": None})
            continue
        line, rest = hit
        if key in MONEY_FIELDS:
            m = _MONEY.search(line)
            if not m:
                if re.search(r"\b(included|incl\.?|n/?a|not applicable|nil|free)\b", rest, re.I):
                    fields[key] = {"value": 0.0, "quote": line, "confidence": 0.85, "label": label, "note": rest}
                else:
                    fields[key] = {"value": None, "quote": line, "confidence": 0.3, "label": label}
                    issues.append({"field": key, "text": f"{label} shows “{line[:70]}” but I can't see an amount. What is it?", "suggestion": None})
                continue
            pm = parse_money(m.group(0))
            per = "per month" if re.search(r"p/?m|per\s+month|monthly|/\s*month", line, re.I) else ""
            fields[key] = {"value": pm["value"], "quote": line, "confidence": 0.6 if pm["ambiguous"] else 0.92, "label": label, "raw": m.group(0).strip(), "period": per}
            if pm["ambiguous"]:
                issues.append({"field": key, "text": f"{label.split(' (')[0]} shows “{m.group(0).strip()}{' ' + per if per else ''}” — a comma where the decimal should be. I read it as {pm['display']}. Confirm?", "suggestion": pm["value"]})
        elif key == "antennas":
            n = re.search(r"(\d+)\s*(?:x|×)?\s*(?:antenna|panel|dish|microwave|rru|unit)", line, re.I) or re.search(r"(?:antenna|antennas)\D{0,20}?(\d+)", line, re.I)
            desc = _MONEY.sub("", rest or line).strip(" -–:")
            fields[key] = {"value": desc, "quote": line, "confidence": 0.85 if n else 0.6, "label": label, "count": int(n.group(1)) if n else None}
        elif key == "quote_no":
            q = re.search(r"\b([A-Z]{1,4}[-/ ]?\d{3,}[-/\dA-Z]*)\b", rest) or re.search(r"\b(\d{4,})\b", rest)
            fields[key] = {"value": q.group(1) if q else rest, "quote": line, "confidence": 0.9 if q else 0.5, "label": label}
            if not q and not rest:
                issues.append({"field": key, "text": "I can see a quotation heading but no number next to it. What is the quote number?", "suggestion": None})
        else:
            fields[key] = {"value": rest, "quote": line, "confidence": 0.9 if rest else 0.3, "label": label}
            if not rest:
                issues.append({"field": key, "text": f"The {label.lower()} line is empty. Please type it.", "suggestion": None})
    # cross-check monthly total
    parts = [fields[k]["value"] for k in ("accommodation", "electricity", "access_road") if fields.get(k, {}).get("value") is not None]
    tm = fields.get("total_monthly", {}).get("value")
    if parts and tm is not None and abs(sum(parts) - tm) > 0.51 and not any(i["field"] == "total_monthly" for i in issues):
        issues.append({"field": "total_monthly", "text": f"The monthly lines add up to {fmt_money(sum(parts))} but the quote says the total is {fmt_money(tm)}. Which is right?", "suggestion": round(sum(parts), 2)})
    return {"fields": fields, "issues": issues, "site": (fields.get("site", {}).get("value") or "")[:160], "quote_no": (fields.get("quote_no", {}).get("value") or "")[:64]}
