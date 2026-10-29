"""Run a ruleset over one document's spans → one cell per column, each with provenance.

A cell: {value, raw, page, bbox, quote, method, quality, score, status, note}
  quote   – the exact text on the page the value was read from (provenance)
  method  – id of the rule that produced it
  quality – exact | fuzzy | positional | derived (how the anchor was found)

Rules are tried in order; the first that yields a value that parses as the column type wins.
`derived` rules run in a second pass because they read other cells.
"""
from __future__ import annotations

import difflib
import re
from typing import Optional

from . import money as M
from .spans import contains_verbatim, page_lines, page_text

RULE_TYPES = ("const", "regex", "label_right", "label_below", "label_left", "line_after", "between", "derived", "table", "identity")
QUALITIES = ("exact", "fuzzy", "positional", "derived")
TEXT_GAP_PT = 14.0  # a horizontal gap wider than this ends a text value
BELOW_MAX_PT = 44.0  # how far below a label we look for its value
LABEL_RULE_TYPES = ("label_right", "label_below", "label_left", "line_after", "table")


# ---- normalisation ---------------------------------------------------------------------
def norm_tokens(s: str) -> list[str]:
    return re.sub(r"[^a-z0-9%]+", " ", str(s or "").lower()).split()


def _flat_tokens(line: dict) -> list[tuple[str, int]]:
    out = []
    for i, w in enumerate(line["words"]):
        for t in norm_tokens(w["t"]):
            out.append((t, i))
    return out


def _tok_sim(a: str, b: str) -> float:
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


# ---- anchors -----------------------------------------------------------------------------
_ANCHOR_CACHE: dict = {}
_ANCHOR_CACHE_MAX = 60000


def clear_anchor_cache() -> None:
    _ANCHOR_CACHE.clear()


def gap_threshold(spans: Optional[dict]) -> float:
    """How wide a space has to be before it separates two FIELDS rather than two words. Measured
    from the document itself: the ordinary space between words, times three."""
    if not isinstance(spans, dict):
        return 8.0
    if "_gap" in spans:
        return spans["_gap"]
    gaps = []
    for line in spans.get("lines") or []:
        ws = line["words"]
        for a, b in zip(ws, ws[1:]):
            g = float(b["x0"]) - float(a["x1"])
            if g >= 0:
                gaps.append(g)
    gaps.sort()
    out = max(6.0, 3.0 * (gaps[len(gaps) // 2] if gaps else 2.0))
    spans["_gap"] = out
    return out


def field_groups(ws: list[dict], gap: float) -> list[list[dict]]:
    """Split a line at the wide gaps: one group per field printed on that line."""
    groups: list[list[dict]] = [[]]
    for n, w in enumerate(ws):
        if n and float(w["x0"]) - float(ws[n - 1]["x1"]) > gap:
            groups.append([])
        groups[-1].append(w)
    return [g for g in groups if g]


def is_label_anchor(line: dict, i: int, j: int, gap: float = 8.0) -> bool:
    """Is words[i:j] a printed LABEL on this line, rather than a word inside a row of data?

    Three shapes count, and between them they cover every layout we have measured:
      * the label is punctuated as one ("Deposit:", "Account Number:") — the last word ends in ':';
      * nothing numeric stands before it on the line, so the line reads label-then-value
        ("VAT @ 15%   R 4 185.84", "METER NO.  METER TYPE  OLD READING ...");
      * it STARTS a field — a gap wide enough to be a column break stands before it, which is how
        two fields that merely share a line look ("1A LANDROS MAARTENS AVENUE     Account  22962").
    A word in the middle of a data row ("19/08/2026 050301 ELECTRICITY CONSUMPTION 350.000 @ ...")
    is none of the three, and that is the difference between reading a value and reading a line item."""
    words = line["words"]
    if j - 1 < len(words) and str(words[j - 1]["t"]).rstrip().endswith(":"):
        return True
    if j < len(words) and str(words[j]["t"]).strip() in (":", "-", "–"):
        return True
    if i > 0 and float(words[i]["x0"]) - float(words[i - 1]["x1"]) > gap:
        return True
    return not any(re.search(r"\d", w["t"]) for w in words[:i])


def has_values(line: dict) -> bool:
    """Does this line carry any figure at all? A line with none is a heading row."""
    return any(re.search(r"\d", w["t"]) for w in line["words"])


def payee_bands(spans: dict) -> list[dict]:
    """The parts of the page that are about paying the issuer (worked out in `profile.py`)."""
    if isinstance(spans, dict) and "_payee" in spans:
        return spans["_payee"]
    try:
        from .profile import payee_regions  # local: profile imports this module

        return payee_regions(spans)
    except Exception:  # noqa: BLE001
        return []


def _in_band(bands: list[dict], line: dict) -> bool:
    cy = (float(line["top"]) + float(line["bottom"])) / 2.0
    cx = (float(line["x0"]) + float(line["x1"])) / 2.0
    return any(b["page"] == line["page"] and b["x0"] <= cx <= b["x1"] and b["top"] <= cy <= b["bottom"] for b in bands)


def find_anchors(spans: dict, label: str, *, page: Optional[int] = None, fuzzy: bool = True, limit: int = 6, anchor: str = "any", below: bool = False, avoid_payee: bool = False) -> list[dict]:
    """Lines where `label` occurs. Each anchor: {line, i, j, quality} with words[i:j] = the label.
    Exact = the label's tokens appear contiguously; fuzzy = same count of tokens, each ≥0.8 similar
    (OCR slips like 'kVa'/'kVA', 'Acount'), or all label tokens present in order with ≤2 gaps.

    anchor="label" keeps only occurrences that read as a printed label (see `is_label_anchor`);
    `below` prefers a heading row (a line with no figures on it) because that is what a value sits
    under. Order: exact first, then label-like, then heading row (when below), then the label at the
    start of its line, then the tightest line, then reading order."""
    ltoks = norm_tokens(label)
    if not ltoks:
        return []
    key = (id(spans), " ".join(ltoks), page, bool(fuzzy), anchor, bool(below), bool(avoid_payee), limit)
    got = _ANCHOR_CACHE.get(key)
    if got is not None and got[0] is spans:
        return got[1]
    gap = gap_threshold(spans)
    gap = gap_threshold(spans)
    bands = payee_bands(spans) if avoid_payee else []
    out: list[dict] = []
    for line in page_lines(spans, page):
        toks = _flat_tokens(line)
        if len(toks) < len(ltoks):
            continue
        hit = _match_exact(toks, ltoks)
        q = "exact"
        if hit is None and fuzzy:
            hit = _match_fuzzy(toks, ltoks)
            q = "fuzzy"
        if hit is not None:
            i, j = hit
            lab = is_label_anchor(line, i, j, gap)
            if anchor == "label" and not lab:
                continue
            if anchor == "label" and below and has_values(line):
                # "below" is how a table header names the column under it. A label on a line that
                # already carries its own figure ("Material excl VAT  R 9 000.00") is answered to
                # its right; what happens to be printed under it belongs to the next row.
                continue
            extra = sum(1 for t, wi in toks if (wi < i or wi >= j) and len(t) > 1 and not t.isdigit() and not re.search(r"\d", t) and t not in ("r", "zar", "kwh", "kva", "kw", "kl"))
            out.append({"line": line, "i": i, "j": j, "quality": q, "extra": extra, "label_like": lab, "heading": not has_values(line), "payee": _in_band(bands, line) if bands else False})
            if len(out) >= limit * 4:
                break
    # an occurrence inside the block that says how to pay the ISSUER goes last: the label there is
    # genuine ("ACCOUNT NUMBER:") but the value beside it is the municipality's own bank account
    out.sort(key=lambda a: (1 if a["payee"] else 0, 0 if a["quality"] == "exact" else 1, 0 if a["label_like"] else 1, (0 if a["heading"] else 1) if below else 0, 0 if a["i"] == 0 else 1, a["extra"], a["line"]["page"], a["line"]["top"]))
    out = out[:limit]
    if len(_ANCHOR_CACHE) < _ANCHOR_CACHE_MAX:
        _ANCHOR_CACHE[key] = (spans, out)
    return out


def _match_exact(toks, ltoks):
    n = len(ltoks)
    for s in range(0, len(toks) - n + 1):
        if [t for t, _ in toks[s : s + n]] == ltoks:
            return toks[s][1], toks[s + n - 1][1] + 1
    return None


def _match_fuzzy(toks, ltoks):
    n = len(ltoks)
    for s in range(0, len(toks) - n + 1):
        window = [t for t, _ in toks[s : s + n]]
        if all(_tok_sim(a, b) >= 0.8 for a, b in zip(window, ltoks)):
            return toks[s][1], toks[s + n - 1][1] + 1
    # in-order with gaps
    if n >= 2:
        pos = 0
        first = last = None
        gaps = 0
        for idx, (t, wi) in enumerate(toks):
            if pos < n and _tok_sim(t, ltoks[pos]) >= 0.85:
                if first is None:
                    first = wi
                last = wi
                pos += 1
            elif first is not None:
                gaps += 1
                if gaps > 2:
                    first = last = None
                    pos = 0
                    gaps = 0
            if pos == n:
                return first, last + 1
    return None


# ---- the document's own numeric convention ------------------------------------------------
_NUM_TOKEN = re.compile(r"\d[\d.,'    ]*\d|\d")


def decimal_sep(spans: Optional[dict]) -> Optional[str]:
    """Which mark this document uses for decimals: '.', ',' or None when it never says.

    South African documents mix "R 1 250,00" and "1,250.00", and a meter reading is printed
    "91767.000" — three decimals, not ninety-one million. Reading the whole document first settles
    it: a figure that carries BOTH marks names the decimal one, and so does a tail that cannot be a
    thousands group (one, two, or four-plus digits). Everything else stays ambiguous and is left to
    the general parser."""
    if not isinstance(spans, dict):
        return None
    if "_decimal_sep" in spans:
        return spans["_decimal_sep"]
    score = {".": 0, ",": 0}
    for line in spans.get("lines") or []:
        for m in _NUM_TOKEN.finditer(line.get("text") or ""):
            t = re.sub(r"[\s'   ]", "", m.group(0))
            for sep in (".", ","):
                other = "," if sep == "." else "."
                i = t.rfind(sep)
                if i < 0:
                    continue
                head, tail = t[:i], t[i + 1 :]
                if not tail.isdigit() or not head:
                    continue
                mixed = other in head or sep in head
                if len(tail) in (1, 2) or len(tail) >= 4:
                    score[sep] += 3 if mixed else 2
                elif mixed and other in head:
                    score[sep] += 1
    best = "." if score["."] > score[","] else ("," if score[","] > score["."] else None)
    out = best if best and score[best] >= 2 else None
    spans["_decimal_sep"] = out
    return out


def retype(raw: str, value: float, sep: Optional[str]) -> float:
    """Re-read `raw` with the document's decimal mark. Keeps the sign the parser worked out."""
    if not sep or raw is None:
        return value
    body = re.sub(r"[\s'   ]", "", str(raw))
    body = re.sub(r"(?i)[r()−\-+]|zar|cr|dr", "", body)
    if not body or not re.fullmatch(r"[\d.,]+", body):
        return value
    i = body.rfind(sep)
    if i >= 0:
        head, tail = re.sub(r"[.,]", "", body[:i]) or "0", body[i + 1 :]
        if not tail.isdigit():
            return value
        try:
            v = float(f"{head}.{tail}")
        except ValueError:
            return value
    else:
        digits = re.sub(r"[.,]", "", body)
        if not digits.isdigit():
            return value
        v = float(digits)
    return -v if value < 0 else v


# ---- value picking -----------------------------------------------------------------------
def pick_value(ctype: str, words: list[dict], *, pick: str = "first", sep: Optional[str] = None) -> Optional[dict]:
    """From candidate words pick a typed value. Returns {value, raw, quote, bbox} or None."""
    if not words:
        return None
    if ctype in ("money", "number"):
        text = " ".join(w["t"] for w in words)
        finder = M.find_money if ctype == "money" else M.find_numbers
        found = finder(text)
        if not found:
            return None
        raw, val = found[-1] if pick == "last" else found[0]
        val = retype(raw, val, sep)
        if ctype == "number" and float(val).is_integer():
            val = int(val)
        if ctype == "money":
            val = round(val, 2)
        return {"value": val, "raw": raw, "quote": raw, "bbox": _bbox_for(words, raw)}
    if ctype == "date":
        text = " ".join(w["t"] for w in words)
        found = M.find_dates(text)
        if not found:
            return None
        raw, val = found[-1] if pick == "last" else found[0]
        return {"value": val, "raw": raw, "quote": raw, "bbox": _bbox_for(words, raw)}
    # text / list: words until a wide gap or the next label-like token
    vals: list[dict] = []
    prev = None
    for w in words:
        t = w["t"]
        if not vals and re.fullmatch(r"[:\-–·|.]+", t):
            continue
        if vals and prev is not None and w["x0"] - prev["x1"] > TEXT_GAP_PT:
            break
        if vals and t.endswith(":"):
            break
        vals.append(w)
        prev = w
    raw = " ".join(w["t"] for w in vals).strip(" :;-–·|")
    if not raw:
        return None
    return {"value": raw, "raw": raw, "quote": raw, "bbox": _bbox(vals)}


def _bbox(ws: list[dict]) -> list[float]:
    return [round(min(w["x0"] for w in ws), 1), round(min(w["top"] for w in ws), 1), round(max(w["x1"] for w in ws), 1), round(max(w["bottom"] for w in ws), 1)]


def _bbox_for(words: list[dict], raw: str) -> list[float]:
    """Box of the words that make up `raw` (best effort: the words whose text appears in raw)."""
    parts = raw.split()
    hit = [w for w in words if any(p == w["t"] or w["t"] in parts for p in parts)]
    return _bbox(hit or words)


# ---- individual rules --------------------------------------------------------------------
def apply_rule(rule: dict, ctype: str, spans: dict, cells: Optional[dict] = None) -> Optional[dict]:
    """Apply one rule. Returns a partial cell (value, raw, quote, page, bbox, quality) or None."""
    t = rule.get("type")
    page = rule.get("page")
    pick = rule.get("pick", "first")
    sep = decimal_sep(spans)
    if t in LABEL_RULE_TYPES:
        label = rule.get("label") or ""
        anchor_mode = rule.get("anchor") if rule.get("anchor") in ("label", "any") else "any"
        gap = gap_threshold(spans)
        for a in find_anchors(spans, label, page=page, fuzzy=rule.get("fuzzy", True), anchor=anchor_mode, below=t in ("label_below", "table"), avoid_payee=rule.get("avoid") == "payee"):
            line = a["line"]
            if t == "label_right":
                cand = pick_value(ctype, line["words"][a["j"] :], pick=pick, sep=sep)
            elif t == "label_left":
                # a label punctuated with a colon points to its right, and a label with a column
                # break before it has the PREVIOUS field to its left, not its own value
                # ("Internet PIN: 440624127  Deposit: 0.00", "1A LANDROS AVENUE    Account 22962")
                if str(line["words"][a["j"] - 1]["t"]).rstrip().endswith(":"):
                    continue
                if a["i"] > 0 and float(line["words"][a["i"]]["x0"]) - float(line["words"][a["i"] - 1]["x1"]) > gap:
                    continue
                cand = pick_value(ctype, line["words"][: a["i"]], pick=rule.get("pick", "last"), sep=sep)
            elif t == "line_after":
                nxt = _lines_below(spans, line, 1)
                cand = pick_value(ctype, nxt[0]["words"], pick=pick, sep=sep) if nxt else None
            else:  # label_below / table
                skip = int(rule.get("skip", 0))
                below = _lines_below(spans, line, 2 + skip)
                cand = None
                lx0 = line["words"][a["i"]]["x0"] - 10
                lx1 = line["words"][a["j"] - 1]["x1"] + 30
                # a heading row that holds several headings ("DATE CODE DESCRIPTION UNITS TARIFF
                # VALUE") only speaks for the column under THIS one; reading the whole line below it
                # picks up a neighbour's figure. Only a label that is alone on its line may do that.
                alone = a["i"] == 0 and a["j"] == len(line["words"])
                for nl in below[skip:]:
                    aligned = [w for w in nl["words"] if w["x1"] >= lx0 and w["x0"] <= lx1]
                    cand = pick_value(ctype, aligned, pick=pick, sep=sep) or (pick_value(ctype, nl["words"], pick=pick, sep=sep) if not aligned and alone and ctype != "text" else None)
                    if cand:
                        break
            if cand:
                cand["page"] = line["page"]
                cand["quality"] = a["quality"] if rule.get("quality", "exact") != "positional" else "positional"
                cand["ocr"] = bool(line.get("ocr"))
                cand["anchor"] = " ".join(w["t"] for w in line["words"][a["i"] : a["j"]])
                return cand
        return None
    if t == "regex":
        try:
            rx = re.compile(rule.get("pattern", ""), re.I)
        except re.error:
            return None
        pages = [page] if page else sorted({l["page"] for l in spans["lines"]})
        bands = payee_bands(spans) if rule.get("avoid") == "payee" else []
        every = [l for p in pages for l in page_lines(spans, p)]
        ordered = sorted(every, key=lambda l: 1 if bands and _in_band(bands, l) else 0)  # payment block last
        for line in ordered:
            m = rx.search(line["text"])
            if not m:
                continue
            raw = m.group(1) if m.groups() else m.group(0)
            v, ok = M.parse_typed(ctype, raw)
            if not ok:
                continue
            if ctype in ("money", "number") and isinstance(v, (int, float)):
                v = retype(raw, float(v), sep)
                v = round(v, 2) if ctype == "money" else (int(v) if float(v).is_integer() else v)
            return {"value": v, "raw": raw, "quote": raw, "page": line["page"], "bbox": _bbox_for(line["words"], raw), "quality": rule.get("quality", "positional"), "ocr": bool(line.get("ocr"))}
        return None
    if t == "identity":
        from .profile import identity as _identity  # local: profile imports this module

        slot = rule.get("slot") or "issuer"
        try:
            got = (_identity(spans) or {}).get(slot)
        except Exception:  # noqa: BLE001
            got = None
        if not got:
            return None
        v, ok = M.parse_typed(ctype, str(got.get("text") if isinstance(got, dict) else got))
        if not ok:
            return None
        line = got.get("line") if isinstance(got, dict) else _line_containing(spans, str(got))
        raw = str(got.get("text") if isinstance(got, dict) else got)
        return {"value": v, "raw": raw, "quote": raw, "page": (line or {}).get("page", 1), "bbox": _bbox(line["words"]) if line else None, "quality": rule.get("quality", "exact"), "ocr": bool((line or {}).get("ocr")), "anchor": slot}
    if t == "between":
        a, b = rule.get("after") or "", rule.get("before") or ""
        pages = [page] if page else sorted({l["page"] for l in spans["lines"]})
        for p in pages:
            txt = page_text(spans, p)
            ia = txt.lower().find(a.lower()) if a else 0
            if ia < 0:
                continue
            start = ia + len(a)
            ib = txt.lower().find(b.lower(), start) if b else len(txt)
            if ib < 0:
                continue
            seg = txt[start:ib].strip()
            v, ok = M.parse_typed(ctype, seg)
            if ok:
                return {"value": v, "raw": seg, "quote": seg, "page": p, "bbox": None, "quality": rule.get("quality", "positional"), "ocr": spans.get("scanned", False)}
        return None
    if t == "const":
        val = rule.get("value")
        if val is None:
            return None
        v, ok = M.parse_typed(ctype, str(val))
        if not ok:
            return None
        line = _line_containing(spans, str(val))
        return {"value": v, "raw": str(val), "quote": str(val), "page": line["page"] if line else 1, "bbox": _bbox(line["words"]) if line else None, "quality": rule.get("quality", "exact"), "ocr": bool(line.get("ocr")) if line else False}
    if t == "derived":
        return _derive(rule, ctype, spans, cells or {})
    return None


def _lines_below(spans: dict, line: dict, n: int) -> list[dict]:
    same = [l for l in page_lines(spans, line["page"]) if l["top"] > line["bottom"] - 1 and l["top"] - line["bottom"] <= BELOW_MAX_PT and l is not line]
    same.sort(key=lambda l: l["top"])
    return same[:n]


def _line_containing(spans: dict, text: str) -> Optional[dict]:
    n = M.normalise_text(text)
    for l in spans["lines"]:
        if n in M.normalise_text(l["text"]):
            return l
    return None


def _derive(rule: dict, ctype: str, spans: dict, cells: dict) -> Optional[dict]:
    op = rule.get("op")
    args = rule.get("args") or []
    vals = []
    for a in args:
        if isinstance(a, (int, float)):
            vals.append(float(a))
        else:
            c = cells.get(a)
            if not c or c.get("value") is None or not isinstance(c.get("value"), (int, float)):
                return None
            vals.append(float(c["value"]))
    if len(vals) < 2:
        return None
    if op == "add":
        v = sum(vals)
    elif op == "sub":
        v = vals[0] - sum(vals[1:])
    elif op == "mul":
        v = vals[0]
        for x in vals[1:]:
            v *= x
    elif op == "div":
        if any(x == 0 for x in vals[1:]):
            return None
        v = vals[0]
        for x in vals[1:]:
            v /= x
    else:
        return None
    v = round(v, 2)
    # provenance: the derived figure must itself be printed somewhere on the document
    hit = find_number_in_doc(spans, v)
    if not hit:
        return {"value": v, "raw": str(v), "quote": "", "page": None, "bbox": None, "quality": "derived", "ocr": False}
    return {"value": v if ctype == "money" else (int(v) if v.is_integer() else v), "raw": hit["raw"], "quote": hit["raw"], "page": hit["page"], "bbox": hit["bbox"], "quality": "derived", "ocr": hit["ocr"]}


def find_number_in_doc(spans: dict, value: float, tol: float = 0.005) -> Optional[dict]:
    """Find a printed number equal to `value` anywhere in the document (for verification/derived)."""
    sep = decimal_sep(spans)
    for l in spans["lines"]:
        for raw, v0 in M.find_money(l["text"]) + M.find_numbers(l["text"]):
            v = retype(raw, v0, sep)
            if abs(v - value) <= tol or abs(abs(v) - abs(value)) <= tol:
                return {"raw": raw, "page": l["page"], "bbox": _bbox_for(l["words"], raw), "ocr": bool(l.get("ocr")), "line": l}
    return None


def find_text_in_doc(spans: dict, text: str) -> Optional[dict]:
    n = M.normalise_text(text)
    if not n:
        return None
    for l in spans["lines"]:
        if n in M.normalise_text(l["text"]):
            return {"raw": text, "page": l["page"], "bbox": _bbox(l["words"]), "ocr": bool(l.get("ocr")), "line": l}
    return None


# ---- whole document ----------------------------------------------------------------------
def extract(ruleset: dict, columns: list[dict], spans: dict, filename: Optional[str] = None) -> dict:
    """columns: [{key,label,ctype,required,critical,expr}] → {key: cell}. Scores are added by scoring.py.
    `filename` fills `filename` columns; `formula` columns are worked out last from the other cells."""
    fields = ruleset.get("fields") or {}
    cells: dict[str, dict] = {}
    deferred: list[tuple[dict, dict]] = []
    for col in columns:
        if col.get("ctype") == "filename":
            cells[col["key"]] = filename_cell(filename)
            continue
        if col.get("ctype") == "formula":
            cells[col["key"]] = _empty_cell()  # filled by apply_formulas below
            continue
        rules = (fields.get(col["key"]) or {}).get("rules") or []
        cell = None
        raw_fallback = None
        for r in rules:
            if r.get("type") == "derived":
                continue
            got = apply_rule(r, col["ctype"], spans)
            if got is None:
                continue
            cell = _mk_cell(got, r)
            break
        if cell is None and any(r.get("type") == "derived" for r in rules):
            deferred.append((col, {"rules": [r for r in rules if r.get("type") == "derived"]}))
        cells[col["key"]] = cell or _empty_cell(raw_fallback)
    for col, spec in deferred:
        for r in spec["rules"]:
            got = apply_rule(r, col["ctype"], spans, cells)
            if got is not None:
                cells[col["key"]] = _mk_cell(got, r)
                break
    # provenance: no quote on the page → no value we can stand behind
    for key, c in cells.items():
        if c["value"] is None or c.get("method") in ("filename", "formula"):
            continue
        c["provenance"] = bool(c.get("quote")) and contains_verbatim(spans, c["quote"])
    apply_formulas(cells, columns)
    return cells


def filename_cell(filename: Optional[str]) -> dict:
    """The PDF's own name: always right, never a miss."""
    name = (filename or "").strip()
    if not name:
        return _empty_cell()
    return {"value": name, "raw": name, "page": None, "bbox": None, "quote": "", "method": "filename", "quality": "exact", "ocr": False, "anchor": "", "parsed": True, "provenance": True, "score": 1.0, "status": "ok", "note": ""}


def apply_formulas(cells: dict, columns: list[dict]) -> None:
    """Fill every `formula` column from the other cells (mutates `cells`). A formula may use another
    formula column; we pass over them until nothing changes. Blank when an input is blank — the
    input's own status says whether that is a miss (required) or fine (optional). User-owned cells
    (filled / trusted / flagged) are never overwritten."""
    from . import formula as F

    fcols = [c for c in columns if c.get("ctype") == "formula" and (c.get("expr") or (c.get("config") or {}).get("expr"))]
    if not fcols:
        return
    by_key = {c["key"]: c for c in columns}
    for _ in range(len(fcols)):
        changed = False
        values = {k: c.get("value") for k, c in cells.items() if c.get("status") in ("ok", "filled", "trusted") or (c.get("method") == "formula" and c.get("value") is not None)}
        for col in fcols:
            cur = cells.get(col["key"]) or {}
            if cur.get("status") in ("filled", "trusted", "flagged"):
                continue
            expr = col.get("expr") or (col.get("config") or {}).get("expr")
            v = F.evaluate(expr, values)
            if v is None:
                missing = F.missing_inputs(expr, values)
                unsure = [by_key.get(k, {}).get("label") or k for k in missing if (cells.get(k) or {}).get("value") is not None]
                blank = [by_key.get(k, {}).get("label") or k for k in missing if (cells.get(k) or {}).get("value") is None]
                parts = []
                if blank:
                    parts.append(", ".join(blank) + (" is" if len(blank) == 1 else " are") + " blank")
                if unsure:
                    parts.append(", ".join(unsure) + (" needs" if len(unsure) == 1 else " need") + " checking first")
                note = "blank: " + "; ".join(parts) if parts else "blank: the formula could not be worked out (division by zero?)"
                cell = {"value": None, "raw": expr, "page": None, "bbox": None, "quote": "", "method": "formula", "quality": "formula", "ocr": False, "anchor": "", "parsed": True, "provenance": True, "score": 1.0, "status": "absent", "note": note}
            else:
                cell = {"value": v, "raw": expr, "page": None, "bbox": None, "quote": "", "method": "formula", "quality": "formula", "ocr": False, "anchor": "", "parsed": True, "provenance": True, "score": 1.0, "status": "ok", "note": ""}
            if cell.get("value") != cur.get("value") or cell.get("status") != cur.get("status"):
                changed = True
            cells[col["key"]] = cell
        if not changed:
            break


def _mk_cell(got: dict, rule: dict) -> dict:
    return {
        "value": got.get("value"),
        "raw": got.get("raw"),
        "page": got.get("page"),
        "bbox": got.get("bbox"),
        "quote": got.get("quote") or "",
        "method": rule.get("id") or rule.get("type"),
        "quality": got.get("quality") or rule.get("quality") or "exact",
        "ocr": bool(got.get("ocr")),
        "anchor": got.get("anchor", ""),
        "parsed": True,
        "score": None,
        "status": "ok",
        "note": "",
    }


def _empty_cell(raw=None) -> dict:
    return {"value": None, "raw": raw, "page": None, "bbox": None, "quote": "", "method": "", "quality": "", "ocr": False, "anchor": "", "parsed": False, "score": 0.0, "status": "miss", "note": "not found"}
