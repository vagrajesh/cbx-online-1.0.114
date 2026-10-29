"""C-EXA's vocabulary: which printed labels have worked for which kind of column, on any layout.

This is the harness that makes municipality #10 easier than #1. Every layout C-EXA reads teaches her
label text — `Account Number`, `TOTAL DUE ON OR BEFORE`, `OLD READING` — and the next layout starts
from that vocabulary instead of from nothing.

    candidates(db, column)                              → labels to try, best first
    confirm(db, column_key=, label=, placement=, layout=)  → this label worked here
    reject(db, column_key=, label=, placement=, layout=)   → this label did not

Matching is on the column's *meaning*, not its key: a label learned for `account_number` is offered
for `acc_no`, `account_no` and `customer_account` too (see `group_for`). Ranking is confirmations
minus failures, then anything learned before anything shipped, then recency.

Nothing in this module may raise. A broken database, a missing table, a session in a bad state —
all of it falls back to the shipped seed vocabulary, because a batch of a person's PDFs must never
fail over C-EXA's memory.

The seed vocabulary below is the printed label text of real South African municipal statements
(two billing-system layouts, 44 bills read with pymupdf) plus the label conventions South African
vendor tax invoices use. It is a *starting* vocabulary, not a per-municipality cheat sheet: every
entry is the general phrase, and bank-account labels from a statement's banking-details block are
deliberately absent — reading the municipality's own ABSA account number as the customer's account
number is exactly the failure this table exists to prevent.
"""
from __future__ import annotations

import re
from typing import Optional

PLACEMENTS = ("right", "below", "left")
RULE_TYPE = {"right": "label_right", "below": "label_below", "left": "label_left"}
FROM_RULE_TYPE = {"label_right": "right", "label_below": "below", "label_left": "left"}
MAX_LABEL = 60  # a printed label is short; anything longer is a sentence, not a heading
SEED_SOURCE = "seed"


# ---- the column's meaning ------------------------------------------------------------------------
_ABBREV = {"acc": "account", "acct": "account", "no": "no", "nr": "no", "num": "no", "cust": "customer", "qty": "units"}


def _tokens(*parts: str) -> set[str]:
    out: set[str] = set()
    for p in parts:
        for t in re.sub(r"[^a-z0-9]+", " ", str(p or "").lower()).split():
            out.add(_ABBREV.get(t, t))
    return out


def group_for(column: dict) -> str:
    """The stable name of what this column *means*. Two columns with the same meaning share a
    lexicon entry however they are keyed, so `acc_no` inherits everything `account_number` learned.
    A column we have no name for falls back to its own normalised key, so it still learns."""
    try:
        s = _tokens(column.get("key", ""), column.get("label", ""), column.get("hint", "")[:120] if column.get("hint") else "")
    except Exception:  # noqa: BLE001
        s = set()
    has = s.__contains__

    if {"work", "order"} <= s or {"purchase", "order"} <= s or has("po") or has("wo"):
        return "work_order"
    if has("vat") and ({"no", "number", "reg", "registration"} & s):
        return "vat_number"
    if has("nmd") or has("notified"):
        return "nmd"
    if has("demand"):
        return "demand"
    if has("opening") or has("bfwd") or ({"brought", "forward"} & s and has("balance")):
        return "opening_balance"
    if (has("reading") or has("read")) and ({"old", "previous", "prev", "start", "opening", "from"} & s):
        return "reading_prev"
    if has("reading") or has("read"):
        return "reading_curr"
    if {"kwh", "kl", "units", "consumption", "consumed", "usage", "energy"} & s:
        return "consumption"
    if has("meter"):
        return "meter"
    if has("tariff"):
        return "tariff"
    if has("deposit") and not has("date"):
        return "deposit"
    if has("due") and has("date"):
        return "due_date"
    if {"payable", "pay"} & s and ({"date", "by", "before"} & s):
        return "due_date"
    if has("period"):
        return "period"
    if has("date"):
        return "date"
    if has("invoice"):
        return "invoice"
    if has("account") and ({"name", "holder"} & s):
        return "customer"
    if {"account", "customer"} & s and not ({"name", "holder"} & s):
        return "account"
    if {"consumer", "client", "holder", "name"} & s and not ({"municipality", "vendor", "supplier"} & s):
        return "customer"
    if {"excl", "excluding", "subtotal", "nett"} & s or ({"sub", "net"} & s and has("total")) or (has("net") and has("amount")) or (has("before") and ({"vat", "tax"} & s)):
        return "excl"  # "before" alone is not excl-VAT: "Total due on or before" is a due date
    if has("vat"):
        return "vat"
    if {"total", "due", "payable", "incl", "including", "amount", "balance"} & s:
        return "total"
    if {"municipality", "municipal", "vendor", "supplier", "issuer", "issued"} & s:
        return "issuer"
    if {"site", "premises", "property", "address", "street", "erf", "stand"} & s:
        return "site"
    if {"reference", "ref"} & s:
        return "reference"
    key = re.sub(r"[^a-z0-9]+", "_", str(column.get("key") or column.get("label") or "").lower()).strip("_")
    return key[:48] or "unknown"


# ---- the shipped vocabulary ------------------------------------------------------------------------
# (group, label as it is printed, placement of the value, starting confirmations)
# `measured` entries were read off the 44 real municipal statements; the rest are the phrases South
# African bills and vendor invoices conventionally use. Counts are 1 or 2 — modest on purpose, so
# that a label a real document confirms overtakes a shipped guess (see `confirm`).
SEED: tuple[tuple[str, str, str, int], ...] = (
    # --- who the account is ---------------------------------------------------------------------
    ("account", "Account Number", "right", 2),          # measured
    ("account", "Account No", "right", 2),              # measured
    ("account", "Account No", "below", 1),              # measured (footer block)
    ("account", "Acc No", "right", 1),
    ("account", "Account Nr", "right", 1),
    ("account", "Customer Account", "right", 1),
    ("account", "Customer Account Number", "right", 1),
    ("account", "Municipal Account Number", "right", 1),
    ("customer", "Consumer Name", "right", 2),          # measured
    ("customer", "Customer Name", "right", 1),
    ("customer", "Account Holder", "right", 1),
    ("customer", "Client Name", "right", 1),
    ("issuer", "Municipality", "right", 1),
    ("issuer", "Supplier", "right", 1),
    ("issuer", "Vendor", "right", 1),
    ("issuer", "Issued By", "right", 1),
    # --- invoice identity ------------------------------------------------------------------------
    ("invoice", "Tax Invoice No", "right", 2),          # measured
    ("invoice", "Tax Invoice No", "below", 2),          # measured (as a column heading)
    ("invoice", "Invoice No", "right", 2),
    ("invoice", "Invoice Number", "right", 1),
    ("invoice", "Tax Invoice Number", "right", 1),
    ("invoice", "Invoice", "right", 1),
    ("vat_number", "VAT No", "right", 2),               # measured
    ("vat_number", "VAT Registration No", "right", 2),  # measured
    ("vat_number", "VAT Reg No", "right", 1),
    ("vat_number", "VAT Number", "right", 1),
    ("vat_number", "Consumer VAT Number", "below", 1),  # measured (as a column heading)
    # --- dates -------------------------------------------------------------------------------------
    ("date", "Account Date", "right", 2),               # measured
    ("date", "Statement Date", "right", 2),
    ("date", "Invoice Date", "right", 2),
    ("date", "Tax Invoice Date", "right", 1),
    ("date", "Date", "right", 1),
    ("date", "Date", "below", 1),                       # measured (table heading)
    ("date", "Bill Date", "right", 1),
    ("due_date", "Total Due On Or Before", "right", 2),  # measured
    ("due_date", "Due Date", "right", 2),
    ("due_date", "Due Date", "below", 1),               # measured (stacked heading)
    ("due_date", "Payment Due", "right", 1),
    ("due_date", "Pay By", "right", 1),
    ("due_date", "Payable By", "right", 1),
    ("total", "Due", "below", 1),                       # measured (Dihlabeng ageing row: the amount)
    ("period", "Reading Dates", "below", 1),            # measured
    ("period", "Billing Period", "right", 1),
    ("period", "Statement Period", "right", 1),
    ("period", "Consumption Period", "right", 1),
    ("period", "Service Period", "right", 1),
    ("period", "For The Period", "right", 1),
    # --- meters and readings -----------------------------------------------------------------------
    ("meter", "Meter No", "below", 2),                  # measured
    ("meter", "Meter Number", "below", 2),              # measured
    ("meter", "Meter No", "right", 1),
    ("meter", "Meter Number", "right", 1),
    ("meter", "Meter", "below", 1),
    ("reading_prev", "Old Reading", "below", 2),        # measured
    ("reading_prev", "Previous Reading", "below", 2),   # measured
    ("reading_prev", "Previous", "below", 1),
    ("reading_prev", "Prev Reading", "below", 1),
    ("reading_prev", "Opening Reading", "below", 1),
    ("reading_curr", "New Reading", "below", 2),        # measured
    ("reading_curr", "Current Reading", "below", 1),
    ("reading_curr", "Current", "below", 1),
    ("reading_curr", "Present Reading", "below", 1),
    ("reading_curr", "Closing Reading", "below", 1),
    ("consumption", "Consumption", "below", 2),         # measured
    ("consumption", "Units", "below", 2),               # measured
    ("consumption", "KL / Units", "below", 2),          # measured
    ("consumption", "Consumed", "below", 2),            # measured
    ("consumption", "Consumption", "right", 1),
    ("consumption", "Units Consumed", "below", 1),
    ("consumption", "kWh Consumed", "right", 1),
    ("consumption", "Energy Consumed", "right", 1),
    ("consumption", "Total kWh", "right", 1),
    ("demand", "Maximum Demand", "right", 1),
    ("demand", "Maximum Demand", "below", 1),
    ("demand", "Max Demand kVA", "right", 1),
    ("demand", "Demand kVA", "right", 1),
    ("nmd", "Notified Maximum Demand", "right", 1),
    ("nmd", "Notified Demand", "right", 1),
    ("nmd", "NMD", "right", 1),
    ("nmd", "NMD kVA", "right", 1),
    ("tariff", "Tariff", "below", 2),                   # measured
    ("tariff", "Tariff Code", "below", 2),              # measured
    ("tariff", "Tariff Rate", "below", 2),              # measured
    ("tariff", "Tariff", "right", 1),
    ("tariff", "Tariff Description", "right", 1),
    # --- money -------------------------------------------------------------------------------------
    ("total", "Total Due", "right", 2),                 # measured (remittance advice)
    ("total", "Total Due", "below", 2),                 # measured (ageing row)
    ("total", "Closing Balance", "below", 2),           # measured
    ("total", "Amount Due", "below", 2),                # measured
    ("total", "Amount Due", "right", 2),
    ("total", "Balance Due", "right", 1),
    ("total", "Total", "right", 1),
    ("total", "Total Payable", "right", 1),
    ("total", "Total Amount Due", "right", 1),
    ("total", "Grand Total", "right", 1),
    ("total", "Total Incl VAT", "right", 1),
    ("total", "Amount Payable", "right", 1),
    ("vat", "VAT", "right", 2),                         # measured
    ("vat", "VAT Amount", "right", 1),
    ("vat", "VAT 15%", "right", 1),
    ("vat", "VAT @ 15%", "right", 1),
    ("vat", "Value Added Tax", "right", 1),
    ("vat", "Total VAT", "right", 1),
    ("excl", "Sub Total", "right", 1),
    ("excl", "Subtotal", "right", 1),
    ("excl", "Total Excl VAT", "right", 1),
    ("excl", "Amount Excl VAT", "right", 1),
    ("excl", "Excl VAT", "right", 1),
    ("excl", "Nett Amount", "right", 1),
    ("excl", "Total Before VAT", "right", 1),
    ("opening_balance", "Opening Balance", "right", 2),  # measured
    ("opening_balance", "Balance B/Fwd", "right", 2),    # measured
    ("opening_balance", "Balance Brought Forward", "right", 1),
    ("deposit", "Deposit", "right", 2),                 # measured
    ("deposit", "Deposit Amount", "below", 2),          # measured
    ("deposit", "Deposit Held", "right", 1),
    ("deposit", "Security Deposit", "right", 1),
    ("deposit", "Security Held", "right", 1),
    ("deposit", "Consumer Deposit", "right", 1),
    # --- where and what --------------------------------------------------------------------------
    ("site", "Street", "right", 2),                     # measured
    ("site", "ERF Description", "right", 2),            # measured
    ("site", "Service Address", "right", 1),
    ("site", "Physical Address", "right", 1),
    ("site", "Premises", "right", 1),
    ("site", "Site Name", "right", 1),
    ("reference", "Reference", "below", 2),             # measured
    ("reference", "Reference", "right", 1),
    ("reference", "Ref No", "right", 1),
    ("reference", "Your Reference", "right", 1),
    ("work_order", "Work Order", "right", 1),
    ("work_order", "Work Order No", "right", 1),
    ("work_order", "WO No", "right", 1),
    ("work_order", "Order Number", "right", 1),
    ("work_order", "Purchase Order", "right", 1),
    ("work_order", "PO Number", "right", 1),
    ("work_order", "Job Number", "right", 1),
)

_BY_GROUP: dict[str, list[tuple[str, str, int, int]]] = {}
for _i, (_g, _lab, _pl, _n) in enumerate(SEED):
    _BY_GROUP.setdefault(_g, []).append((_lab, _pl, _n, _i))


# Meanings whose printed labels name themselves plainly. A label from one of these never belongs to
# a column of another meaning: "Consumer Name" is not where the consumption is, and "Account Name"
# in a banking-details block is not the customer's account. Everything else is left alone, because
# plenty of good labels ("Security held", "Previous") do not carry their meaning in their words.
SELF_NAMING_GROUPS = {"customer", "issuer", "site", "vat_number", "tariff", "reference", "meter"}


def label_conflicts_with(label: str, group: str) -> bool:
    """Does this label plainly name a different meaning from the column it is being taught for?"""
    lab_group = group_for({"key": _clean_label(label), "label": _clean_label(label)})
    return lab_group != group and lab_group in SELF_NAMING_GROUPS


def seed_count() -> int:
    return len(SEED)


def seed_confirmations(group: str, label: str, placement: str) -> int:
    """The starting confirmation count a shipped entry carries, 0 when it is not a seed."""
    lab = _clean_label(label).lower()
    for s_lab, s_pl, n, _ in _BY_GROUP.get(group, []):
        if s_lab.lower() == lab and s_pl == placement:
            return n
    return 0


# ---- helpers ---------------------------------------------------------------------------------------
def _clean_label(label: str) -> str:
    return re.sub(r"\s+", " ", str(label or "")).strip(" :.-–—,;")


def placement_of(rule_type: str) -> Optional[str]:
    """'label_right' → 'right'. Returns None for a rule that has no label placement."""
    return FROM_RULE_TYPE.get(str(rule_type or ""))


def _norm_placement(placement: str) -> Optional[str]:
    p = str(placement or "").strip().lower()
    if p in PLACEMENTS:
        return p
    return FROM_RULE_TYPE.get(p)


def _usable(label: str) -> bool:
    """A label is a printed phrase. Two-letter codes in a table cell ('EL' for an electricity meter)
    are data sitting next to the value, not the heading that names it."""
    lab = _clean_label(label)
    if not (3 <= len(lab) <= MAX_LABEL):
        return False
    if not re.search(r"[A-Za-z]{2}", lab):
        return False  # a label is words, not a number someone happened to be standing next to
    words = lab.split()
    if len(words) == 1 and len(words[0]) < 3:
        return False
    return len(words) <= 8


def _ts(dt) -> float:
    try:
        return dt.timestamp()
    except Exception:  # noqa: BLE001
        return 0.0


def _row_out(group: str, label: str, placement: str, confirmed: int, failed: int, **extra) -> dict:
    out = {"label": label, "placement": placement, "confirmed": int(confirmed), "failed": int(failed), "group": group, "rule_type": RULE_TYPE.get(placement, "label_right")}
    out.update(extra)
    return out


# ---- the interface ----------------------------------------------------------------------------------
def candidates(db, column: dict, *, limit: int = 40) -> list[dict]:
    """Labels that have worked for this column on other layouts, best first.

    Returns [{"label": "Account Number", "placement": "below"|"right"|"left",
              "confirmed": 7, "failed": 1}].
    `db` may be None → the shipped seed vocabulary only. Never raises."""
    try:
        group = group_for(column or {})
    except Exception:  # noqa: BLE001
        return []
    learned: list[dict] = []
    if db is not None:
        learned = _learned(db, group)
    seen = {(str(r["label"]).lower(), r["placement"]) for r in learned}
    shipped = []
    for lab, pl, n, order in _BY_GROUP.get(group, []):
        if (lab.lower(), pl) in seen:
            continue
        shipped.append(_row_out(group, lab, pl, n, 0, source=SEED_SOURCE, layouts=[], example="", corrections=0, order=order, updated=0.0))
    rows = learned + shipped
    rows.sort(key=lambda r: (-(r["confirmed"] - r["failed"]), 0 if r.get("source") != SEED_SOURCE else 1, -r.get("updated", 0.0), r.get("order", 0)))
    for r in rows:
        r.pop("order", None)
        r.pop("updated", None)
    return rows[: max(1, int(limit or 40))]


def _learned(db, group: str) -> list[dict]:
    """Rows from the database, or [] when anything at all goes wrong."""
    try:
        from ..models.pdfstudio import PdfLexicon

        rows = db.query(PdfLexicon).filter(PdfLexicon.column_group == group).all()
        return [
            _row_out(
                group,
                r.label,
                r.placement,
                r.confirmed or 0,
                r.failed or 0,
                source="learned",
                layouts=list(r.layouts or []),
                example=r.example or "",
                corrections=r.corrections or 0,
                ctype=r.ctype or "",
                column_key=r.column_key or "",
                order=0,
                updated=_ts(r.updated_at),
            )
            for r in rows
        ]
    except Exception:  # noqa: BLE001 — C-EXA's memory must never break a batch
        _safe_rollback(db)
        return []


def confirm(db, *, column_key: str, label: str, placement: str, layout: str, example: str = "", ctype: str = "", source: str = "gate") -> None:
    """This label, with the value in this place, gave the right answer for this column on `layout`.
    `source="correction"` marks the strongest signal there is: a person fixed the cell and the value
    was printed next to this label. Never raises; a `db` of None is a no-op."""
    _write(db, column_key=column_key, label=label, placement=placement, layout=layout, example=example, ctype=ctype, source=source, ok=True)


def reject(db, *, column_key: str, label: str, placement: str, layout: str, ctype: str = "", source: str = "gate") -> None:
    """This label did not give the right answer for this column on `layout`. Never raises."""
    _write(db, column_key=column_key, label=label, placement=placement, layout=layout, example="", ctype=ctype, source=source, ok=False)


def _write(db, *, column_key: str, label: str, placement: str, layout: str, example: str, ctype: str, source: str, ok: bool) -> None:
    if db is None:
        return
    try:
        pl = _norm_placement(placement)
        lab = _clean_label(label)
        if pl is None or not _usable(lab):
            return
        group = group_for({"key": column_key, "label": column_key})
        from ..db import utcnow
        from ..models.pdfstudio import PdfLexicon

        with db.begin_nested():  # a savepoint: a failure here cannot roll back the caller's work
            row = db.query(PdfLexicon).filter(PdfLexicon.column_group == group, PdfLexicon.label == lab, PdfLexicon.placement == pl).one_or_none()
            if row is None:
                row = PdfLexicon(
                    column_group=group,
                    column_key=str(column_key or "")[:64],
                    label=lab[:120],
                    placement=pl,
                    ctype=str(ctype or "")[:16],
                    # a shipped entry is a prior, not a rival: a learned row starts from the seed's
                    # count so the first real confirmation puts it in front of every other seed
                    confirmed=seed_confirmations(group, lab, pl),
                    failed=0,
                    corrections=0,
                    layouts=[],
                    example="",
                )
                db.add(row)
            if ok:
                row.confirmed = (row.confirmed or 0) + 1
                if source == "correction":
                    row.corrections = (row.corrections or 0) + 1
                if example and not (row.example or ""):
                    row.example = str(example)[:160]
                lays = list(row.layouts or [])
                if layout and str(layout) not in lays:
                    row.layouts = (lays + [str(layout)])[-20:]
            else:
                row.failed = (row.failed or 0) + 1
            if ctype and not (row.ctype or ""):
                row.ctype = str(ctype)[:16]
            row.updated_at = utcnow()
    except Exception:  # noqa: BLE001 — never block a batch over C-EXA's memory
        _safe_rollback(db)


def _safe_rollback(db) -> None:
    try:
        if db is not None and db.in_nested_transaction():
            db.rollback()
    except Exception:  # noqa: BLE001
        pass


# ---- what C-EXA knows (the admin card, and her own tool) --------------------------------------------
def stats(db, *, top: int = 12) -> dict:
    """{learned, from_corrections, groups, layouts, seeds, top: [...]} — never raises."""
    out = {"learned": 0, "from_corrections": 0, "groups": 0, "layouts": 0, "seeds": seed_count(), "seed_groups": len(_BY_GROUP), "top": []}
    if db is None:
        return out
    try:
        from ..models.pdfstudio import PdfLexicon

        rows = db.query(PdfLexicon).all()
        out["learned"] = len(rows)
        out["from_corrections"] = sum(1 for r in rows if (r.corrections or 0) > 0)
        out["groups"] = len({r.column_group for r in rows})
        out["layouts"] = len({l for r in rows for l in (r.layouts or [])})
        rows.sort(key=lambda r: (-((r.confirmed or 0) - (r.failed or 0)), -_ts(r.updated_at)))
        out["top"] = [
            {
                "group": r.column_group,
                "label": r.label,
                "placement": r.placement,
                "confirmed": r.confirmed or 0,
                "failed": r.failed or 0,
                "corrections": r.corrections or 0,
                "layouts": len(r.layouts or []),
                "example": r.example or "",
            }
            for r in rows[: max(1, int(top))]
        ]
    except Exception:  # noqa: BLE001
        _safe_rollback(db)
    return out


def describe(db, column: dict, *, limit: int = 12) -> str:
    """Plain-English answer to "what do you know about this column?" (C-EXA's own tool)."""
    group = group_for(column or {})
    rows = candidates(db, column, limit=limit)
    if not rows:
        return f"I have nothing for '{column.get('label') or column.get('key')}' yet — I'd have to read it off the page from scratch."
    lines = [f"'{column.get('label') or column.get('key')}' reads as {group}. {len(rows)} label(s) I would try, best first:"]
    for r in rows:
        where = {"right": "value to the right", "below": "value below", "left": "value to the left"}[r["placement"]]
        tail = f"confirmed {r['confirmed']}×"
        if r["failed"]:
            tail += f", failed {r['failed']}×"
        if r.get("corrections"):
            tail += f", {r['corrections']} from a person's fix"
        if r.get("source") == SEED_SOURCE:
            tail += ", shipped vocabulary"
        elif r.get("layouts"):
            tail += f", on {len(r['layouts'])} layout{'s' if len(r['layouts']) != 1 else ''}"
        lines.append(f"  - '{r['label']}' — {where} ({tail})")
    return "\n".join(lines)
