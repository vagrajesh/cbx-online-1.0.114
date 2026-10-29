"""How a value earns its score.

field score = base(quality) × 0.9 if OCR × 0.4 if an arithmetic check fails × 0.2 if it won't parse
              = 0 if the quote is not verbatim on the page (no provenance, no score)
  base: exact 1.0 · fuzzy 0.75 · positional 0.55 · derived 0.9
optional column with no evidence on the page → status `absent`, score 1.0 (blank is the right answer)
filename / formula columns → score 1.0 (never read off the page; a formula is `absent` while an input is blank)
row score      = mean over required columns that are read off the page
column accuracy= share of documents where that column scored ≥ 0.9 (absent counts as correct)
batch accuracy = mean of row scores
Gates: batch ≥ 99% · per profile ≥ 97% · critical (money) columns ≥ 99.5%
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

BASE = {"exact": 1.0, "fuzzy": 0.75, "positional": 0.55, "derived": 0.9}
OCR_FACTOR = 0.9
ARITH_FACTOR = 0.4
PARSE_FACTOR = 0.2
CELL_OK = 0.9
GATE_BATCH = 0.99
GATE_PROFILE = 0.97
GATE_CRITICAL = 0.995
NO_RULE_TYPES = ("filename", "formula")  # never extracted, never for the user
USER_OWNED = ("filled", "trusted", "flagged")
FOR_USER = ("miss", "flagged")  # cell statuses that land on the person's desk


# a money column named after a line item is not the invoice's excl / VAT / total figure
LINE_ITEM_WORDS = {"material", "materials", "labour", "labor", "travel", "travelling", "km", "kms", "parts", "item", "items", "line", "call", "callout", "goods", "service", "services", "fuel", "electricity", "water", "sundry", "sundries", "consumables", "mu", "markup", "mark", "rate", "hours", "deposit", "guarantee", "security", "retention"}


def is_read(col: dict) -> bool:
    """Is this column read off the page (as opposed to filename / formula)?"""
    return col.get("ctype") not in NO_RULE_TYPES


def money_roles(columns: list[dict]) -> dict[str, str]:
    """Which columns are the excl-VAT amount, the VAT amount and the total (by key/label)."""
    cands: dict[str, list[tuple[int, int, str]]] = {"excl": [], "vat": [], "total": []}
    for i, c in enumerate(columns):
        if c.get("ctype") != "money":
            continue
        t = " ".join(re.sub(r"[^a-z0-9]+", " ", str(c.get(k, "")).lower()) for k in ("key", "label")).split()
        s = set(t)
        # 'material excl VAT' / 'travel excl VAT' are line items, not the invoice's excl-VAT amount:
        # a qualified name ranks after a plain one, then the fewest distinct words wins the role
        rank = (10 if s & LINE_ITEM_WORDS else 0) + len(s), i
        if "excl" in s or "excluding" in s or "subtotal" in s or "sub" in s or "nett" in s or "net" in s or "before" in s:
            cands["excl"].append((*rank, c["key"]))
        elif "vat" in s and not ({"incl", "including", "total", "due"} & s):
            cands["vat"].append((*rank, c["key"]))
        elif {"total", "due", "payable", "incl", "including", "amount"} & s:
            cands["total"].append((*rank, c["key"]))
    return {role: min(v)[2] for role, v in cands.items() if v}


def run_validators(ruleset: dict, columns: list[dict], cells: dict) -> dict:
    """Arithmetic checks over the money cells. Returns {vat_ok, sum_ok, failing: [keys], note}."""
    v = ruleset.get("validators") or {}
    roles = money_roles(columns)
    out: dict = {"vat_ok": None, "sum_ok": None, "failing": [], "note": ""}
    excl = _num(cells, roles.get("excl"))
    vat = _num(cells, roles.get("vat"))
    total = _num(cells, roles.get("total"))
    rate = v.get("vat_rate", 0.15)
    notes = []
    if rate and excl is not None and vat is not None:
        expect = round(excl * rate, 2)
        tol = 1.0  # cents rounding only; anything bigger is a real discrepancy
        out["vat_ok"] = abs(vat - expect) <= tol
        if not out["vat_ok"]:
            out["failing"] += [roles["excl"], roles["vat"]]
            notes.append(f"VAT {vat:,.2f} is not {int(rate*100)}% of {excl:,.2f} (expected {expect:,.2f})")
    if v.get("sum_check", True) and excl is not None and vat is not None and total is not None:
        out["sum_ok"] = abs(total - (excl + vat)) <= 0.05
        if not out["sum_ok"]:
            out["failing"] += [roles["excl"], roles["vat"], roles["total"]]
            notes.append(f"total {total:,.2f} ≠ {excl:,.2f} + {vat:,.2f}")
    out["failing"] = sorted(set(out["failing"]))
    out["note"] = "; ".join(notes)
    return out


def _num(cells: dict, key: Optional[str]) -> Optional[float]:
    if not key:
        return None
    c = cells.get(key) or {}
    v = c.get("value")
    return float(v) if isinstance(v, (int, float)) else None


def score_cells(cells: dict, columns: list[dict], validators: dict, scanned: bool = False) -> None:
    """Mutates cells: sets score, status, note."""
    failing = set(validators.get("failing") or [])
    for col in columns:
        c = cells.get(col["key"])
        if c is None:
            continue
        if c.get("status") in USER_OWNED:
            continue  # user-owned cells keep their status
        if col.get("ctype") == "filename":
            c["score"] = 1.0
            c["status"] = "ok" if c.get("value") is not None else "absent"
            c["note"] = "" if c.get("value") is not None else "no file name"
            continue
        if col.get("ctype") == "formula":
            # worked out by extract.apply_formulas (after this pass); it carries its own score/status
            if c.get("method") != "formula":
                c.update({"score": 1.0, "status": "absent", "method": "formula", "quality": "formula", "note": c.get("note") or "blank: not worked out yet"})
            continue
        if c.get("value") is None:
            if not col.get("required", True):
                # optional and nothing on the page: blank is the right answer, not a miss
                c["score"] = 1.0
                c["status"] = "absent"
                c["quality"] = "absent"
                c["note"] = "not on this document"
                continue
            c["score"] = 0.0
            c["status"] = "miss"
            c["note"] = c.get("note") or "not found"
            continue
        s = BASE.get(c.get("quality") or "exact", 0.55)
        reasons = []
        if c.get("ocr") or (scanned and c.get("page") is None):
            s *= OCR_FACTOR
            reasons.append("scanned page")
        if col["key"] in failing:
            s *= ARITH_FACTOR
            reasons.append("figures don't add up: " + validators.get("note", ""))
        if not c.get("parsed", True):
            s *= PARSE_FACTOR
            reasons.append(f"couldn't read a {col['ctype']} next to the label")
        if not c.get("provenance", True):
            s = 0.0
            reasons.append("not verbatim on the page")
        if c.get("quality") == "fuzzy":
            reasons.append(f"label only roughly matched ('{c.get('anchor','')}')")
        c["score"] = round(s, 3)
        c["status"] = "ok" if s >= CELL_OK else "miss"
        c["note"] = "; ".join(reasons) if s < CELL_OK else ""


def row_score(cells: dict, columns: list[dict]) -> tuple[float, int, int]:
    """(mean score over required columns read off the page, k correct, n required). Filename and
    formula columns are always right by construction, so they don't inflate the mark."""
    read = [c for c in columns if is_read(c)]
    req = [c for c in read if c.get("required", True)] or read or columns
    if not req:
        return 1.0, 0, 0
    scores = [_cell_score(cells.get(c["key"])) for c in req]
    k = sum(1 for s in scores if s >= CELL_OK)
    return round(sum(scores) / len(scores), 4), k, len(req)


def _cell_score(c: Optional[dict]) -> float:
    if not c:
        return 0.0
    if c.get("status") in ("filled", "trusted", "absent"):
        return 1.0
    return float(c.get("score") or 0.0)


def cell_counts(rows: Iterable[dict], columns: list[dict]) -> dict:
    """{total, filled, absent, for_user} over rows of cells. `filled` includes absent cells (blank
    is the answer for an optional column); `for_user` is what lands on the person's desk."""
    total = filled = absent = for_user = 0
    for cells in rows:
        for c in columns:
            total += 1
            cell = (cells or {}).get(c["key"]) or {}
            st = cell.get("status")
            if st in FOR_USER:
                for_user += 1
            elif st == "absent":
                absent += 1
                filled += 1
            elif cell.get("value") is not None:
                filled += 1
    return {"total": total, "filled": filled, "absent": absent, "for_user": for_user}


def column_accuracy(rows: Iterable[dict], columns: list[dict]) -> dict[str, float]:
    rows = list(rows)
    out = {}
    for c in columns:
        if not rows:
            out[c["key"]] = 0.0
            continue
        ok = sum(1 for r in rows if _cell_score(r.get(c["key"])) >= CELL_OK)
        out[c["key"]] = round(ok / len(rows), 4)
    return out


def batch_accuracy(row_scores: Iterable[float]) -> float:
    rs = list(row_scores)
    return round(sum(rs) / len(rs), 4) if rs else 0.0


def gates(accuracy: float, by_column: dict[str, float], columns: list[dict], profile_acc: dict[str, float]) -> dict:
    crit = {c["key"]: by_column.get(c["key"], 0.0) >= GATE_CRITICAL for c in columns if c.get("critical")}
    return {
        "batch": accuracy >= GATE_BATCH,
        "profiles": {k: v >= GATE_PROFILE for k, v in profile_acc.items()},
        "critical": crit,
        "all": accuracy >= GATE_BATCH and all(crit.values()) and all(v >= GATE_PROFILE for v in profile_acc.values()),
    }
