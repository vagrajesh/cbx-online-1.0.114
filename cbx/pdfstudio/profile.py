"""Profiles: what a layout looks like and where each column lives on it.

RULESET_SCHEMA = {
  "match":      {"all": [tokens that must appear on page 1], "none": [tokens that must not]},
  "validators": {"vat_rate": 0.15, "sum_check": true},
  "fields": {
     "<col_key>": {"rules": [
        {"id": "r1", "type": "label_right", "label": "Account No.", "pick": "first", "anchor": "label"},
        {"id": "r2", "type": "label_below", "label": "OLD READING"},
        {"id": "r3", "type": "regex", "pattern": "VAT\\s*No\\.?\\s*:?\\s*(4\\d{9})"},
        {"id": "r4", "type": "identity", "slot": "issuer"},
        {"id": "r5", "type": "derived", "op": "add", "args": ["excl_vat", "vat"]}
     ]}
  }
}
Rules are tried in order; the first that yields a value parsing as the column type wins.

HOW A LAYOUT IS LEARNED (docs/cexa-learning.md has the rationale; this is the shape of it):

  identity(spans)      VAT number, the organisation on the letterhead, the document-type line.
                       Generic patterns only — no list of issuer names, because a list of names
                       matches the city in an address and calls a Capitec profile "cape-town".
  skeleton(samples)    the lines printed identically on ≥80% of the samples. Those are the layout;
                       everything else is data. The family is a hash of that skeleton, NOT the
                       issuer — three municipalities on one billing system share one profile.
  label_phrases()      the printed labels inside the skeleton (and every "Xxx:" run anywhere).
  author_heuristic()   for every column: generate candidates widely (its own vocabulary, every
                       skeleton label × placement, the lexicon, the regex library, identity slots),
                       then put each one through the EVIDENCE GATE — fires on ≥80% of samples, the
                       value parses as the column type, the value is not itself a printed label,
                       and its variance across the samples matches what the column should do.
                       Survivors compete for spans: no span serves two columns. Failures are
                       DISCARDED, never kept as fallbacks, and a column with no survivor is
                       reported not_found and left blank for a person.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Optional

from . import extract as X
from .spans import page_lines, top_tokens

RULESET_SCHEMA = {"match": {"all": [], "none": []}, "validators": {"vat_rate": 0.15, "sum_check": True}, "fields": {}}

SKELETON_SHARE = 0.8  # a line printed on this share of the samples is layout, not data
GATE_SHARE = 0.8  # a rule must fire on this share of the samples to be kept at all
MIN_FIT = 0.45  # …and the printed label has to name the column, not merely mention a word it shares
MAX_PHRASE = 5  # words in a printed label we will consider
MAX_RULES = 3  # rules saved per column (the winner plus alternatives that also passed the gate)
FAMILY_SIM = 0.45  # share of one document's printed labels that another must share to be one layout


# ---- generic vocabulary ------------------------------------------------------------------------
# None of this names a municipality, a vendor or a customer. It is the English of billing documents,
# and it is the only vocabulary C-EXA starts with; the lexicon is how she learns the rest.
SYNONYM_GROUPS: list[set[str]] = [
    {"account", "acc", "acct", "customer", "consumer", "debtor"},
    {"number", "no", "nr", "num", "code", "id"},
    {"reference", "ref"},
    {"start", "starting", "begin", "beginning", "opening", "previous", "prev", "prior", "old", "former", "brought"},
    {"end", "ending", "close", "closing", "current", "present", "new", "latest", "final"},
    {"reading", "read", "readings"},
    {"date", "dated", "day"},
    {"billing", "bill", "billed", "invoice", "invoiced", "statement", "tax"},
    {"due", "payable", "owing", "outstanding", "owed"},
    {"total", "sum", "overall", "aggregate", "grand"},
    {"consumption", "consumed", "consume", "usage", "used", "units", "energy", "kwh", "kw"},
    {"demand", "maximum", "max", "kva", "kilovolt"},
    {"notified", "nmd", "capacity"},
    {"municipality", "municipal", "munic", "council", "authority", "metro", "metropolitan", "local", "city", "town"},
    {"issuer", "issued", "supplier", "vendor", "provider", "utility", "biller", "company", "organisation", "organization"},
    {"site", "premises", "property", "stand", "erf", "location", "building", "address", "street"},
    {"rate", "tariff", "price"},
    {"period", "cycle", "month", "monthly"},
    {"excl", "excluding", "exclusive", "before", "nett", "net", "sub", "subtotal"},
    {"incl", "including", "inclusive", "gross"},
    {"meter", "metering"},
    {"estimate", "estimated", "est"},
    {"actual", "real"},
    {"description", "desc", "detail", "details"},
    {"balance", "bal"},
    {"water", "kl", "kilolitre", "kilolitres"},
]
_GROUP_OF: dict[str, int] = {}
for _i, _g in enumerate(SYNONYM_GROUPS):
    for _w in _g:
        _GROUP_OF.setdefault(_w, _i)

# words in a column name that carry no meaning of their own: they never have to be found on the page
NEUTRAL_WORDS = {"total", "amount", "amt", "value", "values", "name", "names", "no", "nr", "num", "number", "the", "of", "for", "and", "per", "this", "my", "your", "our", "a", "an", "in", "on", "at", "to"}
# words that may go unmatched at a cost: units, and words that describe the whole document
SOFT_WORDS = {"kwh", "kva", "kw", "kl", "mwh", "va", "m3", "r", "zar", "rand", "rands", "unit", "units", "hours", "hrs", "hour", "km", "kms", "pct", "percent", "billing", "bill", "invoice", "statement", "tax", "monthly", "rate", "rands", "excl", "excluding", "exclusive", "incl", "including", "inclusive", "vat"}
# generic organisation markers on a letterhead — a shape, not a list of names
ORG_WORDS = {"municipality", "municipal", "council", "metropolitan", "metro", "district", "local", "regional", "provincial", "city", "town", "authority", "ltd", "limited", "pty", "proprietary", "soc", "cc", "inc", "incorporated", "holdings", "group", "corporation", "corp", "company", "co", "utilities", "utility", "power", "water", "energy", "electricity", "university", "bank", "trust", "association", "foundation"}
ADDRESS_HINTS = ("p o box", "po box", "private bag", "postnet", "tel:", "fax:", "cell:", "email", "www.", "http", "@", "vat no", "vat reg", "reg no", "co. reg")
DOC_TYPE_RE = re.compile(r"\b(tax\s+invoice(?:\s*/\s*statement(?:\s+of\s+account)?)?|invoice|statement\s+of\s+account|statement|credit\s+note|debit\s+note|quotation|proforma|remittance\s+advice)\b", re.I)
CONTACT_RE = re.compile(r"(?:\btel\b|\bfax\b|\bcell\b|\bemail\b|@|www\.|https?:|\bvat\b|\breg(?:istration)?\b|p\.?\s?o\.?\s+box|private\s+bag|postnet)", re.I)
VAT_RE = re.compile(r"(?:vat|btw)[^A-Za-z0-9]{0,4}(?:reg(?:istration)?)?[^A-Za-z0-9]{0,4}(?:no|nr|number)?[^A-Za-z0-9]{0,4}(4\d{9})\b", re.I)

# the regex library: shapes that mean the same thing on any South African document
REGEX_LIBRARY: list[tuple[set[str], tuple[str, ...], str, str]] = [
    ({"vat"}, ("text", "list"), r"(?:VAT|BTW)[^A-Za-z0-9]{0,4}(?:Reg(?:istration)?)?[^A-Za-z0-9]{0,4}(?:No|Nr|Number)?[^A-Za-z0-9]{0,4}(4\d{9})", "VAT registration number"),
    ({"account"}, ("text", "list", "number"), r"Acc(?:ount)?[^A-Za-z0-9]{0,4}(?:No|Nr|Number)?[^A-Za-z0-9]{0,4}([0-9][0-9\- ]{4,20})", "account number"),
]

SYNONYMS: dict[str, list[str]] = {
    "account": ["account number", "account no", "acc no", "account nr", "acc number", "customer account", "account"],
    "invoice": ["invoice number", "invoice no", "tax invoice no", "invoice nr", "invoice"],
    "municipality": ["municipality", "issued by", "supplier"],
    "vendor": ["vendor", "supplier", "from"],
    "period": ["billing period", "statement period", "consumption period", "service period", "for the period", "period"],
    "date": ["invoice date", "statement date", "account date", "date of invoice", "date"],
    "due_date": ["due date", "payment due", "pay by", "payable by", "due"],
    "kwh": ["consumption kwh", "energy consumed kwh", "kwh consumed", "energy consumed", "units consumed", "consumption", "energy kwh", "total kwh", "kwh"],
    "demand": ["maximum demand kva", "maximum demand", "max demand kva", "max demand", "actual demand", "demand kva", "demand"],
    "nmd": ["notified maximum demand kva", "notified maximum demand", "nmd kva", "nmd", "notified demand", "network capacity"],
    "excl": ["amount excl vat", "amount excluding vat", "total excl vat", "sub total excl vat", "subtotal excl vat", "sub total", "subtotal", "excl vat", "total before vat", "nett amount", "net amount"],
    "vat": ["vat amount", "vat 15%", "vat @ 15%", "value added tax", "vat"],
    "total": ["total due", "amount due", "total payable", "total incl vat", "total including vat", "grand total", "amount payable", "total amount due", "balance due", "total"],
    "site": ["site name", "service address", "premises", "property", "site"],
    "meter": ["meter number", "meter no", "meter"],
    "reference": ["reference", "ref no", "ref"],
}


def synonym_group(col: dict) -> Optional[str]:
    s = set(X.norm_tokens(col.get("key", "")) + X.norm_tokens(col.get("label", "")))
    if "nmd" in s or "notified" in s:
        return "nmd"
    if "demand" in s:
        return "demand"
    if "kwh" in s or "consum" in " ".join(s) or "energy" in s or "units" in s:
        return "kwh"
    if "excl" in s or "excluding" in s or "subtotal" in s or "nett" in s or "net" in s:
        return "excl"
    if "vat" in s and not ({"incl", "including", "total", "due"} & s):
        return "vat"
    if {"total", "due", "payable", "incl", "including"} & s and not ({"date", "day"} & s):
        return "total"
    if "due" in s and "date" in s:
        return "due_date"
    if "period" in s:
        return "period"
    if "account" in s or "acc" in s or "acct" in s:
        return "account"
    if "invoice" in s:
        return "invoice"
    if "municipal" in " ".join(s) or "municipality" in s or "munic" in s:
        return "municipality"
    if "vendor" in s or "supplier" in s:
        return "vendor"
    if "site" in s or "premises" in s or "address" in s:
        return "site"
    if "meter" in s:
        return "meter"
    if "date" in s:
        return "date"
    if "ref" in s or "reference" in s:
        return "reference"
    return None


def candidate_labels(col: dict) -> list[str]:
    """The column's own vocabulary: its label, phrases in its hint, and the synonyms of its group."""
    out: list[str] = []

    def add(s: str):
        s = s.strip(" :.-")
        if s and X.norm_tokens(s) and s.lower() not in [o.lower() for o in out]:
            out.append(s)

    add(col.get("label", ""))
    lab = col.get("label", "")
    add(re.sub(r"\(.*?\)", "", lab))  # label without the unit in brackets
    for phrase in re.findall(r"[\"“'‘]([^\"”'’]{2,60})[\"”'’]", col.get("hint") or ""):
        add(phrase)
    for part in re.split(r"[;,|/]| or ", col.get("hint") or ""):
        if 2 <= len(part.strip()) <= 40 and len(X.norm_tokens(part)) <= 5:
            add(part)
    g = synonym_group(col)
    if g:
        for s in SYNONYMS[g]:
            add(s)
    return out


# ---- how well a printed label fits a column ------------------------------------------------------
def _match_token(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if len(a) >= 4 and len(b) >= 4 and (a.startswith(b) or b.startswith(a)):
        return 0.9
    ga, gb = _GROUP_OF.get(a), _GROUP_OF.get(b)
    if ga is not None and ga == gb:
        return 0.6
    if len(a) >= 5 and len(b) >= 5:
        n = 0
        for x, y in zip(a, b):
            if x != y:
                break
            n += 1
        if n >= 5:
            return 0.7
    return 0.0


def _alternatives(col: dict) -> list[list[str]]:
    """'Munic/Eskom' and 'Actual/Estimate' name alternatives — matching either one is enough."""
    lab = str(col.get("label") or "")
    parts = [p for p in re.split(r"\s*/\s*|\s+or\s+", lab) if X.norm_tokens(p)]
    if len(parts) > 1:
        return [X.norm_tokens(p) for p in parts]
    toks = X.norm_tokens(lab)
    for t in X.norm_tokens(col.get("key", "")):
        if t not in toks:
            toks.append(t)
    return [toks] if toks else []


def _key_only(col: dict) -> set[str]:
    """Tokens that come from the column's KEY and not from its label. A key is an internal slug,
    often abbreviated ('invoice_dt'); it may help a match but must never be required to make one."""
    lab = set(X.norm_tokens(col.get("label") or ""))
    return {t for t in X.norm_tokens(col.get("key") or "") if t not in lab}


def _core(toks: list[str]) -> list[str]:
    core = [t for t in toks if t not in NEUTRAL_WORDS]
    return core or list(toks)


def label_fit(col: dict, phrase: str) -> Optional[float]:
    """0…1 — how well a printed label names this column. None when it cannot be this column's label.

    Every word of the column name that carries meaning must be found in the label (directly, as a
    stem, or through the generic vocabulary above). Units and document-scope words ('kWh', 'billing')
    may be missing at a cost. Words in the label the column does not account for cost as well: that
    is what separates 'METER NO.' from 'METER TYPE' for a Meter number column."""
    ptoks = [t for t in X.norm_tokens(phrase)]
    if not ptoks:
        return None
    best: Optional[float] = None
    weak = SOFT_WORDS | _key_only(col)
    for toks in _alternatives(col):
        core = _core(toks)
        if not core:
            continue
        hard = [t for t in core if t not in weak]
        soft = [t for t in core if t in weak]
        score, matched, ok, n, missed = 0.0, False, True, 0, 0
        for t in hard:
            m = max((_match_token(t, p) for p in ptoks), default=0.0)
            if m <= 0:
                ok = False
                break
            score += m
            n += 1
            matched = True
        if not ok:
            continue
        for t in soft:
            m = max((_match_token(t, p) for p in ptoks), default=0.0)
            if m > 0:
                score += m
                n += 1
                matched = True
            else:
                missed += 1
        if not matched:
            continue
        fit = score / max(1, n) - 0.2 * missed
        extra = 0
        for p in ptoks:
            if re.search(r"\d", p) or p in NEUTRAL_WORDS or p in SOFT_WORDS:
                continue
            if max((_match_token(p, t) for t in toks), default=0.0) <= 0:
                extra += 1
        fit -= 0.12 * extra
        best = fit if best is None else max(best, fit)
    return None if best is None else max(0.02, min(1.0, best))


def _slot_fit(col: dict, words: set[str]) -> Optional[float]:
    """Fit between a column and one of the identity slots (issuer, VAT number)."""
    best: Optional[float] = None
    weak = SOFT_WORDS | _key_only(col)
    for toks in _alternatives(col):
        core = _core(toks)
        if not core:
            continue
        score, ok, matched = 0.0, True, False
        for t in core:
            m = max((_match_token(t, w) for w in words), default=0.0)
            if m > 0:
                score += m
                matched = True
            elif t in weak:
                score -= 0.2
            else:
                ok = False
                break
        if not ok or not matched:
            continue
        f = score / len(core)
        best = f if best is None else max(best, f)
    return None if best is None else max(0.02, min(1.0, best))


ISSUER_WORDS = {"issuer", "issued", "supplier", "vendor", "provider", "utility", "biller", "municipality", "municipal", "munic", "council", "authority", "company", "organisation", "organization", "metro"}
VAT_SLOT_WORDS = {"vat", "registration", "reg"}


# ---- identity ------------------------------------------------------------------------------------
def identity(spans: dict) -> dict:
    """What this document says about itself: {vat, issuer, doc_type}.

    `issuer` is the organisation on the letterhead — the first line near the top of page 1 that reads
    like an organisation and not like an address, a phone number or the name of the document. There
    is deliberately no list of known issuers: a list of names matches the city inside a postal
    address, which is how twenty-one 'Capitec' profiles were born. A layout that does not print its
    issuer at the top of page 1 gets an empty `issuer` — the block half-way down the page is the
    ADDRESSEE, and naming the customer as the supplier is the kind of confident wrong answer that
    costs more than a blank cell."""
    if isinstance(spans, dict) and "_identity" in spans:
        return spans["_identity"]
    out = {"vat": "", "issuer": "", "issuer_name": "", "doc_type": ""}
    lines = page_lines(spans, 1)
    for l in lines:
        m = VAT_RE.search(l["text"])
        if m:
            out["vat"] = m.group(1)
            break
    head = lines[: max(10, len(lines) // 6)]
    best = None
    for rank, l in enumerate(head):
        t = re.sub(r"\s+", " ", l["text"]).strip()
        low = t.lower()
        toks = X.norm_tokens(t)
        if len(toks) < 2 or len(t) > 90:
            continue
        if any(h in low for h in ADDRESS_HINTS):
            continue
        if re.search(r"\d", t) and not re.fullmatch(r"[A-Za-z0-9 .,'&()\-/]+", t):
            continue
        if sum(1 for w in l["words"] if re.search(r"\d", w["t"])) > len(l["words"]) / 3:
            continue
        if DOC_TYPE_RE.fullmatch(t.strip()) or (DOC_TYPE_RE.search(t) and len(toks) <= 6):
            if not out["doc_type"]:
                out["doc_type"] = t
            continue
        org = 1 if any(w in ORG_WORDS for w in toks) else 0
        caps = 1 if t == t.upper() else 0
        # an organisation printed above its OWN contact details is the issuer; a name above a bare
        # postal address is the addressee, and reading the customer as the supplier is a wrong answer
        contact = 1 if any(CONTACT_RE.search(n["text"]) for n in head[rank + 1 : rank + 4]) else 0
        score = (org * 3) + caps + (contact * 2) - rank * 2.0  # a letterhead is at the top of page 1
        if score <= 0:
            continue
        if best is None or score > best[0]:
            best = (score, t)
    if best is not None:
        out["issuer"] = best[1]
        out["issuer_name"] = printed_org(best[1])
    if not out["doc_type"]:
        for l in lines[:30]:
            t = re.sub(r"\s+", " ", l["text"]).strip()
            m = DOC_TYPE_RE.search(t)
            if m:
                out["doc_type"] = t if len(X.norm_tokens(t)) <= 8 else m.group(0)
                break
    if isinstance(spans, dict):
        spans["_identity"] = out
    return out


def issuer_of(spans: dict) -> Optional[str]:
    """The organisation on the letterhead, as printed (None when the page does not say)."""
    return identity(spans).get("issuer") or None


def printed_org(issuer: str, words: int = 2) -> str:
    """The organisation's own name as PRINTED, with the generic descriptors around it taken off:
    'EMFULENI LOCAL MUNICIPALITY' → 'Emfuleni', 'City of Ekurhuleni' → 'Ekurhuleni'. A `list` column
    wants this (it is a value from a controlled list); a `text` column gets the letterhead entire."""
    ws = [w for w in re.split(r"\s+", str(issuer or "").strip()) if w]
    low = [re.sub(r"[^a-z0-9]", "", w.lower()) for w in ws]
    i, j = 0, len(ws)
    while i < j and (low[i] in ORG_WORDS or low[i] in ("the", "of")):
        i += 1
    while j > i and low[j - 1] in ORG_WORDS:
        j -= 1
    out = " ".join(ws[i : min(j, i + words)]) if j > i else " ".join(ws[:words])
    return out.title() if out.isupper() else out


def org_name(issuer: str, words: int = 2) -> str:
    """A readable short name for an organisation on a letterhead: 'EMFULENI LOCAL MUNICIPALITY' →
    'emfuleni', 'City of Ekurhuleni' → 'ekurhuleni'. Generic descriptors are stripped from either
    end — there is no list of known organisations. This NAMES a family; it never groups one."""
    toks = [t for t in re.sub(r"[^a-z0-9]+", " ", str(issuer or "").lower().replace("(", " ").replace(")", " ")).split() if t]
    while toks and (toks[0] in ORG_WORDS or toks[0] in ("the", "of", "city", "town")):
        toks.pop(0)
    while toks and toks[-1] in ORG_WORDS:
        toks.pop()
    toks = [t for t in toks if not re.search(r"\d", t)]
    return "-".join(toks[:words])


def _slug(s: str, words: int = 3) -> str:
    toks = [t for t in re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).split() if len(t) >= 2 and not re.search(r"\d", t)]
    return "-".join(toks[:words])


# ---- the layout skeleton -------------------------------------------------------------------------
def _norm_line(text: str) -> str:
    return " ".join(X.norm_tokens(text))


def _put(out: dict, words: list[dict], line: dict) -> None:
    text = " ".join(w["t"] for w in words).strip(" :.-–·|")
    norm = _norm_line(text)
    if not norm or len(norm) < 2 or all(re.search(r"\d", w["t"]) for w in words):
        return
    prev = out.get(norm)
    if prev is None or (line["page"], line["top"]) < (prev["page"], prev["top"]):
        out[norm] = {"text": text, "page": line["page"], "top": line["top"], "norm": norm}


gap_threshold = X.gap_threshold  # one definition of "a gap wide enough to separate two fields"


# ---- the payment block: the part of a page that is about paying the ISSUER ----------------------
PAYEE_RE = re.compile(r"(?i)\b(bank|banking|branch\s*code|swift|bic|iban|beneficiary|payee|remittance|eft|electronic\s+funds|account\s+name|pay\s*now|payable\s+to|deposit\s+slip)\b")
PAYEE_MAX_TOKENS = 12  # a banking field is short; a paragraph that mentions a bank is prose
PAYEE_PAD_X, PAYEE_PAD_Y, PAYEE_JOIN = 25.0, 20.0, 60.0


def payee_regions(spans: dict) -> list[dict]:
    """Where this document tells you how to pay the issuer: [{page,x0,x1,top,bottom}].

    Every municipal statement carries the municipality's OWN bank account under a label that reads
    exactly like the customer's — `ACCOUNT NUMBER:`. Neither variance nor proximity can tell them
    apart: both are constant every month and the wrong one is often the closer. What does tell them
    apart is the neighbourhood: branch code, bank name, beneficiary, remittance, "pay now", or an
    account name that is the issuer's own. That neighbourhood is not a source for a customer-side
    column. The markers are generic words; no bank and no municipality is named."""
    if isinstance(spans, dict) and "_payee" in spans:
        return spans["_payee"]
    ident = identity(spans)
    own = {t for t in X.norm_tokens(ident.get("issuer_name") or ident.get("issuer") or "") if len(t) >= 4 and t not in ORG_WORDS}
    letterhead = _norm_line(ident.get("issuer") or "")
    marks: list[dict] = []
    for line in spans.get("lines") or []:
        text = line["text"]
        toks = X.norm_tokens(text)
        if len(toks) > PAYEE_MAX_TOKENS:
            continue  # prose that happens to mention a bank is not a banking block
        hit = bool(PAYEE_RE.search(text))
        if not hit and own and ":" in text and len(toks) <= 8 and _norm_line(text) != letterhead:
            hit = any(t in own for t in toks)  # "Name: <the issuer>" — an account in the issuer's name
        if hit:
            marks.append(line)
    regions: list[dict] = []
    for line in sorted(marks, key=lambda l: (l["page"], l["top"])):
        cur = regions[-1] if regions else None
        if cur and cur["page"] == line["page"] and line["top"] - cur["bottom"] <= PAYEE_JOIN:
            cur["x0"], cur["x1"] = min(cur["x0"], line["x0"]), max(cur["x1"], line["x1"])
            cur["bottom"] = max(cur["bottom"], line["bottom"])
        else:
            regions.append({"page": line["page"], "x0": line["x0"], "x1": line["x1"], "top": line["top"], "bottom": line["bottom"]})
    for r in regions:
        r["x0"] -= PAYEE_PAD_X
        r["x1"] += PAYEE_PAD_X
        r["top"] -= PAYEE_PAD_Y
        r["bottom"] += PAYEE_PAD_Y
    if isinstance(spans, dict):
        spans["_payee"] = regions
    return regions


def _in_payee(spans: dict, page, bbox) -> bool:
    if not bbox or page is None:
        return False
    cx, cy = (float(bbox[0]) + float(bbox[2])) / 2.0, (float(bbox[1]) + float(bbox[3])) / 2.0
    return any(r["page"] == page and r["x0"] <= cx <= r["x1"] and r["top"] <= cy <= r["bottom"] for r in payee_regions(spans))


def _is_payment_column(col: dict) -> bool:
    """A column that ASKS for the payment details is allowed to read them."""
    toks = {t for a in _alternatives(col) for t in a}
    return bool(toks & {"bank", "banking", "branch", "swift", "bic", "iban", "beneficiary", "payee", "remittance"})


# ---- corroboration: a value the page repeats somewhere else ------------------------------------
REGION_PT = 100.0  # two occurrences this far apart on a page are in different parts of it
MIN_CORROBORATION_DIGITS = 5


def _needle(raw) -> str:
    d = re.sub(r"\D", "", str(raw or ""))
    return d if len(d) >= MIN_CORROBORATION_DIGITS else ""


def value_places(spans: dict, raw) -> list[dict]:
    """Every line of the document that prints this value, wherever it is embedded.

    A customer's account number is printed two or three times in unrelated parts of a statement —
    the header, the remittance advice, inside the invoice number, inside the barcode string. The
    municipality's own bank account is printed once, in the banking block. Counting the parts of
    the page a value appears in separates them, and nothing else on these documents does."""
    n = _needle(raw)
    if not n:
        return []
    cache = spans.setdefault("_places", {}) if isinstance(spans, dict) else {}
    if n in cache:
        return cache[n]
    out = []
    for line in spans.get("lines") or []:
        if n in re.sub(r"\D", "", line["text"]):
            out.append({"page": line["page"], "top": line["top"], "x0": line["x0"], "x1": line["x1"], "bottom": line["bottom"]})
    cache[n] = out
    return out


def corroboration(spans: dict, raw, *, outside_payee: bool = False) -> int:
    """How many distinct parts of the page print this value (optionally ignoring the payment block)."""
    places = value_places(spans, raw)
    if outside_payee:
        places = [p for p in places if not _in_payee(spans, p["page"], [p["x0"], p["top"], p["x1"], p["bottom"]])]
    return len({(p["page"], int(p["top"] // REGION_PT)) for p in places})



def doc_labels(spans: dict) -> dict[str, dict]:
    """Every printed label visible in ONE document: each heading of a line that carries no figures
    at all (a heading row, split at the wide gaps between its headings), and every 'Xxx:' run
    anywhere. Data words never qualify."""
    out: dict[str, dict] = {}
    gap = gap_threshold(spans)
    for line in spans.get("lines") or []:
        ws = line["words"]
        if not ws:
            continue
        numeric = [bool(re.search(r"\d", w["t"])) for w in ws]
        if not any(numeric):
            for group in X.field_groups(ws, gap):
                for i in range(len(group)):
                    for j in range(i + 1, min(i + MAX_PHRASE, len(group)) + 1):
                        _put(out, group[i:j], line)
        start = 0
        for k, w in enumerate(ws):
            if numeric[k]:
                start = k + 1
                continue
            if str(w["t"]).rstrip().endswith(":"):
                if k >= start:
                    _put(out, ws[start : k + 1], line)
                start = k + 1
    return out


def doc_signature(spans: dict) -> set[str]:
    """A compact print of the layout: whole heading lines and 'Xxx:' labels, no n-grams."""
    out: set[str] = set()
    for line in spans.get("lines") or []:
        ws = line["words"]
        if not ws:
            continue
        numeric = [bool(re.search(r"\d", w["t"])) for w in ws]
        if not any(numeric):
            n = _norm_line(line["text"])
            if len(n) >= 4:
                out.add(n)
            continue
        start = 0
        for k, w in enumerate(ws):
            if numeric[k]:
                start = k + 1
                continue
            if str(w["t"]).rstrip().endswith(":"):
                if k >= start:
                    n = _norm_line(" ".join(x["t"] for x in ws[start : k + 1]))
                    if len(n) >= 3:
                        out.add(n)
                start = k + 1
    return out


def skeleton(samples: list[dict]) -> set[str]:
    """The lines printed identically on ≥80% of the samples: the layout itself."""
    if not samples:
        return set()
    seen: Counter = Counter()
    for sp in samples:
        seen.update({_norm_line(l["text"]) for l in (sp.get("lines") or []) if _norm_line(l["text"])})
    need = max(1, math.ceil(SKELETON_SHARE * len(samples)))
    return {t for t, n in seen.items() if n >= need}


def label_phrases(samples: list[dict]) -> list[dict]:
    """Printed labels that survive across the samples, earliest on the page first."""
    if not samples:
        return []
    seen: Counter = Counter()
    where: dict[str, dict] = {}
    for sp in samples:
        for norm, info in doc_labels(sp).items():
            seen[norm] += 1
            if norm not in where:
                where[norm] = info
    need = max(1, math.ceil(SKELETON_SHARE * len(samples)))
    letterhead = {_norm_line(identity(sp).get("issuer") or "") for sp in samples} - {""}
    out = [dict(where[n], n=c) for n, c in seen.items() if c >= need and not any(n in lh for lh in letterhead)]
    out.sort(key=lambda p: (p["page"], p["top"], p["norm"]))
    return out


def heading_texts(samples: list[dict], skel: Optional[set[str]] = None) -> set[str]:
    """Everything on this layout that is printed, not filled in: the skeleton lines themselves and,
    because a table header row holds several headings side by side, every run of words inside a
    skeleton line that carries no figures. A value equal to one of these is a heading being read as
    if it were data — `meter_number = "METER TYPE"` is exactly that."""
    skel = skeleton(samples) if skel is None else skel
    out: set[str] = set(skel)
    for sp in samples:
        for line in sp.get("lines") or []:
            ws = line["words"]
            if not ws or any(re.search(r"\d", w["t"]) for w in ws):
                continue
            if _norm_line(line["text"]) not in skel:
                continue  # a value-free line that changes from document to document is data
            for i in range(len(ws)):
                for j in range(i + 1, min(i + MAX_PHRASE, len(ws)) + 1):
                    n = _norm_line(" ".join(w["t"] for w in ws[i:j]))
                    if n:
                        out.add(n)
    return out


def label_texts(samples: list[dict]) -> set[str]:
    """Every run of words that is printed on this layout and never carries a figure — taken field by
    field, not line by line, because two fields share a line whenever the page has columns.

    `Deposit/ Deposito/ Peeletso   R 0` prints its label in three languages and its value once; a
    text column that reads "Deposito/ Peeletso" has read the label, not the deposit."""
    if not samples:
        return set()
    seen: Counter = Counter()
    for sp in samples:
        gap = X.gap_threshold(sp)
        got: set[str] = set()
        for line in sp.get("lines") or []:
            for grp in X.field_groups(line["words"], gap):
                if any(re.search(r"\d", w["t"]) for w in grp):
                    continue
                for i in range(len(grp)):
                    for j in range(i + 1, min(i + MAX_PHRASE, len(grp)) + 1):
                        n = _norm_line(" ".join(w["t"] for w in grp[i:j]))
                        if n:
                            got.add(n)
        seen.update(got)
    need = max(1, math.ceil(SKELETON_SHARE * len(samples)))
    return {t for t, c in seen.items() if c >= need}


# ---- C: a value is a field, not a sentence -------------------------------------------------------
STOPWORDS = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "for", "with", "by", "from", "is", "are", "be", "will", "shall", "must", "may", "can", "this", "that", "which", "your", "our", "their", "if", "not", "as", "it", "within", "any", "all", "has", "have", "been", "was", "were", "please", "note"}
PROSE_TOKENS = 8
PROSE_STOPWORDS = 3


def is_prose(line: Optional[dict]) -> bool:
    """A printed field value never lives inside a sentence."""
    if not line:
        return False
    toks = X.norm_tokens(line.get("text") or "")
    return len(toks) >= PROSE_TOKENS and sum(1 for t in toks if t in STOPWORDS) >= PROSE_STOPWORDS


def line_at(spans: dict, page, bbox) -> Optional[dict]:
    if not bbox or page is None:
        return None
    cy = (float(bbox[1]) + float(bbox[3])) / 2.0
    for l in spans.get("lines") or []:
        if l["page"] == page and l["top"] - 1 <= cy <= l["bottom"] + 1:
            return l
    return None


def layout_signature(sig: set[str]) -> str:
    h = hashlib.sha1("\n".join(sorted(sig)).encode("utf-8")).hexdigest()
    return h[:8]


# ---- families ------------------------------------------------------------------------------------
def fingerprint(spans: dict) -> Counter:
    return Counter(top_tokens(spans))


def _overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def cluster(docs: list[tuple[int, dict]], threshold: float = FAMILY_SIM) -> list[dict]:
    """docs: [(doc_id, spans)] → [{family, doc_ids, issuers, signature, ...}].

    Documents are grouped by their printed labels, NOT by who issued them: Abaqulusi, Bela-Bela and
    Alfred Duma are three municipalities on one billing system and must share one profile, while two
    layouts from one issuer must split."""
    fams: list[dict] = []
    for did, spans in docs:
        sig = doc_signature(spans)
        fp = fingerprint(spans)
        home, best = None, 0.0
        for f in fams:
            s = max(_overlap(sig, o) for o in f["sigs"])
            if s >= threshold and s > best:
                home, best = f, s
        if home is None:
            fams.append({"doc_ids": [did], "sigs": [sig], "fps": [fp], "spans": [spans], "centroid": Counter(fp)})
        else:
            home["doc_ids"].append(did)
            home["sigs"].append(sig)
            home["fps"].append(fp)
            home["spans"].append(spans)
            home["centroid"].update(fp)
    for f in fams:
        core = set.intersection(*f["sigs"]) if f["sigs"] else set()
        f["skeleton"] = core
        f["signature"] = layout_signature(core)
        issuers: list[str] = []
        for sp in f["spans"]:
            iss = issuer_of(sp)
            if iss and iss not in issuers:
                issuers.append(iss)
        f["issuers"] = issuers
        f["issuer"] = None  # a family is a layout, not an issuer — kept for older callers
        f["base"] = (org_name(issuers[0]) if len(issuers) == 1 else "") or _slug(identity(f["spans"][0]).get("doc_type") or "") or "layout"
        f["keywords"] = _family_keywords(f, fams)
    # a readable name; the layout signature is only appended when two layouts would otherwise
    # share it, so a profile keeps its name from one batch to the next
    bases = Counter(f["base"] for f in fams)
    for f in fams:
        f["family"] = f["base"] if bases[f["base"]] == 1 else f"{f['base']}-{f['signature'][:4]}"
    return fams


def _family_keywords(fam: dict, fams: list[dict]) -> list[str]:
    """Printed labels this family has and no other family here has."""
    core = fam["skeleton"]
    others: set[str] = set()
    for g in fams:
        if g is not fam:
            for s in g["sigs"]:
                others |= s
    def rank(t: str) -> tuple:
        toks = t.split()
        return (1 if (CONTACT_RE.search(t) or "@" in t) else 0, 0 if 3 <= len(toks) <= 10 else 1, -len(t), t)

    distinct = sorted(core - others, key=rank)
    return [re.sub(r"[^a-z0-9]", "", t) for t in distinct[:6] if len(re.sub(r"[^a-z0-9]", "", t)) >= 6]


def match_rules_for(fam: dict, fams: list[dict]) -> dict:
    """A match rule that is TESTED before it is saved: it must match every sample of its own family
    and no sample of any other family we know about. A rule that cannot be made discriminative is
    not saved — better no profile than one that hijacks another layout's documents."""
    own = fam.get("spans") or []
    others = [sp for g in fams if g is not fam for sp in (g.get("spans") or [])]
    # a match key should be something the layout PRINTS, not something this customer happens to
    # carry: an e-mail address or a reference on the page is stable across one account's bills and
    # useless for the next account. Headings of three to ten words first, then anything else.
    def rank(t: str) -> tuple:
        toks = t.split()
        contact = 1 if CONTACT_RE.search(t) or "@" in t else 0
        return (contact, 0 if 3 <= len(toks) <= 10 else 1, -len(t), t)

    pool = list(fam.get("keywords") or [])
    for t in sorted(fam.get("skeleton") or set(), key=rank):
        tok = re.sub(r"[^a-z0-9]", "", t)
        if len(tok) >= 6 and tok not in pool:
            pool.append(tok)
    chosen: list[str] = []
    for tok in pool[:24]:
        trial = {"match": {"all": chosen + [tok], "none": []}}
        if own and not all(matches(trial, sp) for sp in own):
            continue
        chosen.append(tok)
        if not any(matches({"match": {"all": chosen, "none": []}}, sp) for sp in others):
            break
        if len(chosen) >= 3:
            break
    if not chosen:
        return {"all": [], "none": [], "verified": False, "why": "no printed label of this layout tells it apart from the others"}
    rule = {"all": chosen, "none": []}
    ok_own = all(matches({"match": rule}, sp) for sp in own) if own else False
    hit_other = [sp for sp in others if matches({"match": rule}, sp)]
    rule["verified"] = bool(ok_own and not hit_other)
    if not rule["verified"]:
        rule["why"] = "this rule also fits another layout's documents" if hit_other else "this rule does not fit all of its own samples"
    return rule


def matches(ruleset: dict, spans: dict) -> bool:
    m = ruleset.get("match") or {}
    toks = set(top_tokens(spans))
    text = re.sub(r"[^a-z]", "", " ".join(l["text"].lower() for l in page_lines(spans, 1)))
    alnum = re.sub(r"[^a-z0-9]", "", " ".join(l["text"].lower() for l in page_lines(spans, 1)))
    ok_all = all((t in toks) or (t in text) or (t in alnum) for t in (m.get("all") or []))
    ok_none = not any((t in toks) or (t in text) or (t in alnum) for t in (m.get("none") or []))
    return bool(m.get("all")) and ok_all and ok_none


# ---- heuristic authoring -------------------------------------------------------------------------
NO_RULE_TYPES = ("filename", "formula")  # never read off the page → no rules are authored


def readable(columns: list[dict]) -> list[dict]:
    """Columns that are read off the page (not filename / formula)."""
    return [c for c in columns if c.get("ctype") not in NO_RULE_TYPES]


def _placements(ctype: str) -> list[tuple[str, str]]:
    if ctype in ("money", "number"):
        return [("label_right", "first"), ("label_right", "last"), ("label_below", "first"), ("label_below", "last"), ("label_left", "last")]
    return [("label_right", "first"), ("label_below", "first"), ("label_left", "last")]


_PLACE_BONUS = {"label_right": 0.06, "label_below": 0.04, "label_left": 0.0}
_SRC_BONUS = {"identity": 0.16, "column": 0.10, "lexicon": 0.08, "skeleton": 0.06, "regex": 0.02, "option": 0.0}


def _lexicon_candidates(db, col: dict, limit: int = 40) -> list[dict]:
    """Labels that have worked for this column on other layouts. Degrades to nothing if the module
    is not there yet."""
    try:
        from . import lexicon  # noqa: PLC0415 — optional, may be mid-edit
    except Exception:  # noqa: BLE001
        return []
    try:
        got = lexicon.candidates(db, col, limit=limit)
    except Exception:  # noqa: BLE001
        return []
    return [c for c in (got or []) if isinstance(c, dict) and c.get("label")]


def _lexicon_confirm(db, *, column_key: str, label: str, placement: str, layout: str, example: str = "") -> None:
    try:
        from . import lexicon  # noqa: PLC0415

        lexicon.confirm(db, column_key=column_key, label=label, placement=placement, layout=layout, example=example)
    except Exception:  # noqa: BLE001
        return


_PLACEMENT_OF = {"label_right": "right", "label_below": "below", "label_left": "left"}
_MODE_OF = {"right": "label_right", "below": "label_below", "left": "label_left"}


def _rule_sig(rule: dict) -> tuple:
    return (rule.get("type"), str(rule.get("label", "")).lower(), rule.get("pick"), rule.get("slot"), rule.get("pattern"))


def _candidate_rules(col: dict, phrases: list[dict], db=None, widen: int = 0) -> list[tuple[dict, float, str]]:
    """Generate widely and cheaply: the column's own vocabulary, every printed label of the layout,
    the lexicon, the regex library and the identity slots — each at every placement."""
    out: list[tuple[dict, float, str]] = []
    seen: set[tuple] = set()
    ctype = col["ctype"]
    avoid = {} if _is_payment_column(col) else {"avoid": "payee"}

    def add(rule: dict, fit: float, src: str) -> None:
        sig = _rule_sig(rule)
        if sig in seen:  # the same label from two sources keeps the better reading of it
            for n, (r0, f0, s0) in enumerate(out):
                if _rule_sig(r0) == sig and (fit + _SRC_BONUS.get(src, 0.0)) > (f0 + _SRC_BONUS.get(s0, 0.0)):
                    out[n] = (r0, fit, src)
            return
        seen.add(sig)
        out.append((rule, fit, src))

    for li, lab in enumerate(candidate_labels(col)):
        f = label_fit(col, lab)
        if f is None:
            continue  # a synonym of the column's GROUP is not a name for the column itself:
        f -= li * 0.005  # 'consumption' names Total kWh, and does not name Peak kWh
        for mode, pick in _placements(ctype):
            add({"type": mode, "label": lab, "pick": pick, "page": None, "anchor": "label", "quality": "exact", **avoid}, f, "column")

    graded = []
    for ph in phrases:
        f = label_fit(col, ph["norm"])
        if f is not None:
            graded.append((f, ph))
    graded.sort(key=lambda t: (-t[0], t[1]["page"], t[1]["top"]))
    for f, ph in graded[: 12 + 8 * widen]:
        for mode, pick in _placements(ctype):
            add({"type": mode, "label": ph["text"], "pick": pick, "page": None, "anchor": "label", "quality": "exact", **avoid}, f, "skeleton")

    for lex in _lexicon_candidates(db, col):
        mode = _MODE_OF.get(str(lex.get("placement") or "right"), "label_right")
        f = label_fit(col, str(lex["label"]))
        if f is None:
            continue  # experience elsewhere does not make a label mean something it cannot mean
        confirmed = int(lex.get("confirmed") or 0)
        failed = int(lex.get("failed") or 0)
        weight = confirmed / max(1, confirmed + failed)
        add({"type": mode, "label": str(lex["label"]), "pick": "first", "page": None, "anchor": "label", "quality": "exact", **avoid}, max(f, 0.3 + 0.5 * weight), "lexicon")

    if ctype in ("text", "list"):
        f = _slot_fit(col, ISSUER_WORDS)
        if f is not None and f >= 0.5:
            add({"type": "identity", "slot": "issuer_name" if ctype == "list" else "issuer", "quality": "exact"}, f, "identity")
        fv = _slot_fit(col, VAT_SLOT_WORDS)
        if fv is not None and fv >= 0.5 and any("vat" in t for toks in _alternatives(col) for t in toks):
            add({"type": "identity", "slot": "vat", "quality": "exact"}, fv, "identity")

    coltoks = {t for toks in _alternatives(col) for t in toks}
    for words, types, pattern, name in REGEX_LIBRARY:
        if coltoks & words and ctype in types:
            f = label_fit(col, name)
            add({"type": "regex", "pattern": pattern, "page": None, "quality": "positional", **avoid}, max(f if f is not None else 0.0, 0.3), "regex")
    return out


def _trial(rule: dict, col: dict, samples: list[dict], exact_only: bool = False) -> dict:
    """Run one candidate against every sample and write down exactly what it did."""
    values: list = []
    spans_at: dict[int, tuple] = {}
    anchors: Counter = Counter()
    quality = 0
    pages: Counter = Counter()
    payee = free = prose = 0
    regions: list[int] = []
    probe = dict(rule, fuzzy=False) if exact_only else rule
    for i, sp in enumerate(samples):
        got = X.apply_rule(probe, col["ctype"], sp)
        if got is None or got.get("value") is None:
            continue
        values.append(got["value"])
        bbox = got.get("bbox")
        spans_at[i] = (got.get("page"), tuple(round(float(b)) for b in bbox) if bbox else str(got.get("value")))
        anchors[got.get("anchor") or rule.get("label") or rule.get("slot") or ""] += 1
        quality += 1 if got.get("quality") == "exact" else 0
        pages[got.get("page")] += 1
        raw = got.get("raw")
        prose += 1 if is_prose(line_at(sp, got.get("page"), bbox)) else 0
        regions.append(corroboration(sp, raw))
        if _in_payee(sp, got.get("page"), bbox):
            payee += 1
            free += 1 if corroboration(sp, raw, outside_payee=True) else 0
    hits = len(values)
    distinct = len({str(v) for v in values})
    return {
        "rule": rule,
        "hits": hits,
        "of": len(samples),
        "values": values,
        "spans": spans_at,
        "anchors": anchors,
        "exact": quality,
        "distinct": distinct,
        "varying": hits >= 2 and distinct >= 2,
        "page": pages.most_common(1)[0][0] if pages else None,
        "ctype": col["ctype"],
        "payee": payee,
        "payee_free": free,
        "prose": prose,
        "regions": min(regions) if regions else 0,
    }


def _is_heading(value, heads: set[str]) -> bool:
    n = _norm_line(str(value))
    return bool(n) and n in heads


def _gate(cand: dict, col: dict, samples: list[dict], heads: set[str]) -> Optional[str]:
    """The evidence gate. Returns a reason to reject, or None when the candidate earns its place.

    The "fires often enough" test comes LAST on purpose: a rule that fires on two documents in four
    is a candidate for an optional figure (see `propose_optional`), but only if everything else
    about it is sound. Asking that question first would let a rule that reads a heading in through
    the optional door."""
    n = len(samples)
    if not cand["hits"]:
        return f"fired on 0 of {n}"
    if col["ctype"] in ("text", "list") and cand["rule"].get("type") in X.LABEL_RULE_TYPES:
        bad = sum(1 for v in cand["values"] if _is_heading(v, heads))
        if bad:
            return "reads a printed heading, not a value"
    if col["ctype"] in ("text", "list") and all(str(v).strip() == "" for v in cand["values"]):
        return "empty value"
    if cand["exact"] < cand["hits"] and cand["rule"].get("type") in X.LABEL_RULE_TYPES and cand.get("anchors"):
        # the anchor was only a rough match. A rough match is worth having for an OCR slip
        # ('kVa' for 'kVA'), but not when the text it landed on cannot name this column at all —
        # a reversed or garbled text layer offers plenty of those ('demand' matching ':emaN')
        printed = cand["anchors"].most_common(1)[0][0]
        if printed and cand.get("src") != "option" and label_fit(col, printed) is None:
            return f"the printed label '{printed}' cannot name this column"
    if col["ctype"] in ("text", "list") and cand["prose"] > cand["hits"] / 2:
        return "read out of a sentence, not a field"
    if not _is_payment_column(col) and cand["payee"] > cand["hits"] / 2 and cand["payee_free"] * 2 <= cand["payee"]:
        # it is read out of the block that says how to pay the issuer, and the document prints it
        # nowhere else: that is the municipality's own bank account, not this customer's
        return "reads the issuer's own payment details"
    if "fit" in cand and (cand.get("fit") or 0) < MIN_FIT and cand.get("src") != "option":
        return f"no printed label really names this column (best fit {cand.get('fit') or 0:.2f})"
    if cand["hits"] < max(1, math.ceil(GATE_SHARE * n)):
        return f"fired on {cand['hits']} of {n}"
    return None


def _variance_filter(cands: list[dict], col: dict) -> list[dict]:
    """Variance is the signal that needs no model, and no hard-coded list of column names.

    Money, numbers and dates on a periodic document move from one document to the next. When a
    column has BOTH a candidate that moves and one that never does, the one that never does is
    reading something else — a tariff block size, a bank account, a printed constant. It is dropped.
    When every candidate is steady the column is an identity-like one (an account number is the same
    every month) and nothing is dropped. Text columns merely prefer the steady one."""
    if len(cands) < 2:
        return cands
    varying = [c for c in cands if c["varying"]]
    if col["ctype"] in ("money", "number", "date") and varying:
        top_var = max(c["fit"] for c in varying)
        out = [c for c in cands if c["varying"] or c["fit"] >= top_var]
        for c in cands:
            if c not in out:
                c["dropped"] = "same value on every sample, where this column should move"
        return out
    return cands


def _score(cand: dict) -> float:
    r = cand["rule"]
    # a value the document prints in two or three unrelated places corroborates itself: a customer's
    # account number is repeated in the header, the remittance advice and inside the barcode, while
    # a figure that appears once may be anything. It breaks a tie; it never overrides a better name,
    # and it says nothing about a DATE, which repeats all over a statement for ordinary reasons.
    corrob = 0.0 if cand.get("ctype") == "date" else 0.15 * min(max(int(cand.get("regions") or 0) - 1, 0), 2)
    return round(2.0 * (cand["hits"] / max(1, cand["of"])) + 1.5 * cand["fit"] + corrob + _SRC_BONUS.get(cand["src"], 0.0) + _PLACE_BONUS.get(r.get("type"), 0.0) + 0.05 * (cand["exact"] / max(1, cand["hits"])), 4)


def _conflict(a: dict, b: dict) -> bool:
    """Do two candidates read the SAME span? (the same figure, in the same box, on most samples)"""
    common = set(a["spans"]) & set(b["spans"])
    if not common:
        return False
    same = sum(1 for i in common if a["spans"][i] == b["spans"][i])
    return same >= max(1, math.ceil(0.6 * len(common)))


def _assign(pool: dict[str, list[dict]]) -> dict[str, dict]:
    """Exclusive assignment: columns compete for spans, best score first, and no span serves two
    columns — unless both columns have nothing else and both name it confidently, which is the only
    honest reading of a template that wants the same figure twice."""
    pairs = []
    for key, cands in pool.items():
        for c in cands:
            pairs.append((c["score"], key, c))
    pairs.sort(key=lambda t: (-t[0], t[1]))
    taken: list[tuple[str, dict]] = []
    out: dict[str, dict] = {}
    for _s, key, c in pairs:
        if key in out:
            continue
        if any(_conflict(c, other) for k, other in taken if k != key):
            continue
        out[key] = c
        taken.append((key, c))
    for key, cands in pool.items():
        if key in out or not cands:
            continue
        best = cands[0]
        holders = [(k, o) for k, o in taken if _conflict(best, o)]
        if len(cands) == 1 and len(holders) == 1 and best["fit"] >= 0.8 and best.get("named") and all(len(pool[k]) == 1 and o["fit"] >= 0.8 and o.get("named") for k, o in holders):
            out[key] = best  # the template really does want this figure twice
    return out


def _option_words(col: dict) -> list[str]:
    alts = _alternatives(col)
    if len(alts) < 2:
        return []
    words = [" ".join(a) for a in alts if len(a) == 1]
    return words if len(words) == len(alts) else []


def _enumerated(col: dict, samples: list[dict], phrases: list[dict], heads: set[str]) -> list[dict]:
    """Last resort for a column whose NAME lists its values ('Actual/Estimate'): look for a cell
    that only ever holds one of them. Initials count only when the words themselves are nowhere on
    the page — otherwise 'Munic/Eskom' would happily read the 'E' out of a reading-type column."""
    opts = _option_words(col)
    if not opts or col["ctype"] not in ("text", "list"):
        return []
    doc_text = " ".join(_norm_line(l["text"]) for l in (samples[0].get("lines") or []))
    spelled = any(re.search(rf"\b{re.escape(o)}", doc_text) or _match_token(o, w) >= 0.9 for o in opts for w in doc_text.split())
    accept = {o for o in opts}
    if not spelled:
        accept |= {o[0] for o in opts if o}
    width = max(len(o) for o in opts)
    out = []
    for ph in phrases[:120]:
        for mode in ("label_below", "label_right"):
            rule = {"type": mode, "label": ph["text"], "pick": "first", "page": None, "anchor": "label", "quality": "exact", "avoid": "payee"}
            t = _trial(rule, col, samples, exact_only=True)
            if t["hits"] < max(1, math.ceil(GATE_SHARE * len(samples))):
                continue
            vals = [_norm_line(str(v)) for v in t["values"]]
            # a coded column: short values, a small alphabet, and the codes are the names the column
            # itself gives (a reading type may also be printed N or I — those are codes we have not
            # been told about yet, not a reason to throw the column away)
            if any(len(v) > width or not v for v in vals) or t["distinct"] > max(3, len(opts) + 1):
                continue
            if sum(1 for v in vals if v in accept) < math.ceil(0.5 * len(vals)):
                continue
            if any(_is_heading(v, heads) for v in t["values"]):
                continue
            t["fit"] = 0.55
            t["src"] = "option"
            t["score"] = _score(t)
            out.append(t)
    out.sort(key=lambda c: -c["score"])
    return out[:1]


def author_heuristic(columns: list[dict], samples: list[dict], family: str = "", db=None) -> tuple[dict, list[str]]:
    """Build a ruleset for `columns` from sample spans. Returns (ruleset, notes lines)."""
    rs = {"match": {"all": [], "none": []}, "validators": {"vat_rate": 0.15, "sum_check": True}, "fields": {}}
    notes: list[str] = []
    cols = readable(columns)
    if not samples:
        for c in cols:
            rs["fields"][c["key"]] = {"rules": []}
            notes.append(f"{c['label']}: I had no sample pages to learn from.")
        return rs, notes
    X.clear_anchor_cache()
    try:
        phrases = label_phrases(samples)
        strong = heading_texts(samples)
        heads = strong | label_texts(samples) | {p["norm"] for p in phrases}
        pool: dict[str, list[dict]] = {}
        rejected: dict[str, list[str]] = {}
        assigned: dict[str, dict] = {}
        widen = 0
        stale = 0
        while widen <= 2 and stale < 2:
            for col in cols:
                if col["key"] in assigned:
                    continue
                pool[col["key"]] = _gather(col, samples, phrases, heads, db=db, widen=widen, reject_log=rejected)
            if widen >= 1:
                for col in cols:
                    if col["key"] not in assigned and not pool.get(col["key"]):
                        pool[col["key"]] = _enumerated(col, samples, phrases, strong)
            before = len(assigned)
            assigned = _assign({k: v for k, v in pool.items() if v})
            stale = stale + 1 if len(assigned) <= before else 0
            widen += 1
        # ---- write the ruleset ----------------------------------------------------------------
        not_found: list[str] = []
        for col in cols:
            key = col["key"]
            win = assigned.get(key)
            if win is None:
                rs["fields"][key] = {"rules": []}
                not_found.append(key)
                why = "; ".join(rejected.get(key, [])[:2])
                notes.append(f"{col['label']}: no rule passed the evidence gate on these samples" + (f" ({why})" if why else "") + " — it will be left blank for you unless a fix teaches me.")
                continue
            keep = [win]
            others = [o for k, o in assigned.items() if k != key]
            if win["hits"] < win["of"]:
                # the winner does not read every sample: a second rule may cover the gap. When it
                # reads all of them, an "alternative" could only ever fire where the winner failed —
                # that is a guess on a document nobody checked, and a guess is what we are killing.
                for c in pool.get(key, []):
                    if len(keep) >= MAX_RULES:
                        break
                    if c is win or any(_conflict(c, o) for o in others) or any(_conflict(c, k) for k in keep):
                        continue
                    if c["fit"] < 0.8 * win["fit"]:
                        continue
                    keep.append(c)
            rules = []
            for n, c in enumerate(keep, start=1):
                r = dict(c["rule"], id=f"h{n}", hits=c["hits"], of=c["of"])
                if c["rule"].get("type") in X.LABEL_RULE_TYPES and c["anchors"]:
                    r["label"] = c["anchors"].most_common(1)[0][0] or r.get("label")
                rules.append(r)
            rs["fields"][key] = {"rules": rules}
            notes.append(_note(col, win, len(rules) - 1, len(samples)))
            if win["rule"].get("type") in X.LABEL_RULE_TYPES:
                _lexicon_confirm(db, column_key=key, label=str(rules[0].get("label") or ""), placement=_PLACEMENT_OF.get(win["rule"]["type"], "right"), layout=family or "", example=str(win["values"][0]) if win["values"] else "")
        if not_found:
            rs["not_found"] = not_found
        _add_derived(rs, columns, samples)
        return rs, notes
    finally:
        X.clear_anchor_cache()


def _gather(col: dict, samples: list[dict], phrases: list[dict], heads: set[str], *, db=None, widen: int = 0, reject_log: Optional[dict] = None) -> list[dict]:
    """Every candidate for one column, gated on evidence and ranked. Failures are thrown away."""
    kept: list[dict] = []
    weak: list[dict] = []
    why: list[str] = []
    for rule, fit, src in _candidate_rules(col, phrases, db=db, widen=widen):
        cand = _trial(rule, col, samples, exact_only=widen == 0)
        cand["src"], cand["fit"] = src, fit
        bad = _gate(cand, col, samples, heads)
        if bad:
            if cand["hits"]:
                why.append(f"'{rule.get('label') or rule.get('slot') or 'regex'}' {bad}")
                # the column's OWN name, printed on some of the documents and not on the others:
                # that is a figure this layout only sometimes carries (see propose_optional), not a
                # rule that failed. Nothing weaker than an exact naming of the column qualifies.
                if bad.startswith("fired on") and (fit if fit is not None else 0) >= OPTIONAL_FIT:
                    cand["fit"], cand["src"], cand["named"] = fit, src, True
                    cand["score"] = _score(cand)
                    weak.append(cand)
            continue
        cand["named"] = label_fit(col, str(rule.get("label") or rule.get("slot") or "")) is not None
        cand["score"] = _score(cand)
        kept.append(cand)
    if not kept and weak:
        weak.sort(key=lambda c: (-c["score"], str(c["rule"].get("label", ""))))
        kept = weak[:1]
    kept = _variance_filter(kept, col)
    kept.sort(key=lambda c: (-c["score"], str(c["rule"].get("label", ""))))
    # one rule per span: alternatives that read the same figure add nothing
    out: list[dict] = []
    for c in kept:
        if any(_conflict(c, o) for o in out):
            continue
        out.append(c)
    if reject_log is not None and not out and why:
        reject_log[col["key"]] = why[:3]
    return out[:6]


def _note(col: dict, win: dict, extras: int, n: int) -> str:
    r = win["rule"]
    where = {"label_right": "to the right of", "label_below": "below", "label_left": "to the left of", "regex": "by the pattern", "identity": "from the document's own"}.get(r.get("type"), r.get("type"))
    what = r.get("label") or r.get("slot") or r.get("pattern") or ""
    return f"{col['label']}: {where} '{what}' — works on {win['hits']} of {n} samples" + (f"; {extras} alternative rule(s) that also passed" if extras else "")


# ---- optional by evidence ---------------------------------------------------------------------
OPTIONAL_SHARE = 0.6  # a figure seen on fewer than this share of the samples is proposed as optional
OPTIONAL_FIT = 0.75  # …but only where the printed label really is this column's own name


def propose_optional(rs: dict, columns: list[dict], n_samples: int, only_keys: Optional[set] = None) -> list[dict]:
    """Money / number / date columns whose best rule works on SOME but fewer than 60% of the sample
    documents are proposed as optional: the section is not on every document, so a blank cell is the
    right answer there, not a miss. Columns found on no sample stay required (a label we can't find
    at all must go to a person, not be hidden). The excl / VAT / total trio is never proposed — those
    are the arithmetic backbone. Returns [{key, label, hits, of}] for the caller to apply and log."""
    from .scoring import money_roles

    if n_samples < 2:
        return []
    backbone = set(money_roles(columns).values())
    out = []
    for c in readable(columns):
        if only_keys is not None and c["key"] not in only_keys:
            continue
        if not c.get("required", True) or c.get("ctype") not in ("money", "number", "date") or c["key"] in backbone:
            continue
        rules = [r for r in (rs.get("fields", {}).get(c["key"]) or {}).get("rules", []) if r.get("type") not in ("derived", "const") and "hits" in r]
        best = max((int(r.get("hits") or 0) for r in rules), default=0)
        if 0 < best < OPTIONAL_SHARE * n_samples:
            out.append({"key": c["key"], "label": c["label"], "hits": best, "of": n_samples})
    return out


# ---- rule reuse from the template's other profiles (small vendors) ----------------------------
def sibling_rules(siblings: list[tuple[str, dict]], columns: list[dict], samples: list[dict]) -> tuple[dict[str, dict], dict[str, str]]:
    """A new layout under a template that already has profiles (Megaglo under 'Capitec vendor
    invoices' next to capitec.invoice with 1 269 docs): try every field rule of the other profiles on
    the new samples — best profile first — and keep the ones that pass the same evidence gate as a rule
    we write ourselves: a typed value on ≥80% of the samples, and not a label word of one of our own
    columns. `siblings` is [(label, ruleset)] best accuracy first. Returns ({col_key: field},
    {col_key: label of the profile it came from}); only columns with a working rule appear. For text
    and list columns a "value" that is really a label word ('number' right of 'Account' on an
    'Account number' line) does not count."""
    out: dict[str, dict] = {}
    used: dict[str, str] = {}
    if not siblings or not samples:
        return out, used
    cols = readable(columns)
    label_words: set[str] = set()
    for c in cols:
        for l in candidate_labels(c):
            toks = X.norm_tokens(l)
            label_words.add(" ".join(toks))
            label_words.update(toks)
    need = max(1, math.ceil(GATE_SHARE * len(samples)))
    for c in cols:
        cands: list[tuple[int, int, int, dict, str]] = []  # (-hits, sibling order, rule order, rule, label)
        seen_sig = set()
        for si, (label, rs) in enumerate(siblings):
            field = (rs.get("fields") or {}).get(c["key"]) or {}
            for ri, r in enumerate(field.get("rules") or []):
                if r.get("type") in ("derived", "const"):
                    continue  # derived = other keys' arithmetic; const = another vendor's letterhead
                sig = (r.get("type"), str(r.get("label", r.get("pattern", ""))).lower(), r.get("pick"), r.get("page"))
                if sig in seen_sig:
                    continue
                seen_sig.add(sig)
                hits = 0
                for sp in samples:
                    got = X.apply_rule(r, c["ctype"], sp)
                    if got is None or got.get("value") is None:
                        continue
                    if c["ctype"] in ("text", "list") and " ".join(X.norm_tokens(str(got.get("value")))) in label_words:
                        continue  # read a label word, not a value
                    hits += 1
                if hits >= need:  # a borrowed rule earns its place on THIS layout like any other
                    cands.append((-hits, si, ri, r, label))
        if not cands:
            continue
        cands.sort(key=lambda t: (t[0], t[1], t[2]))
        rules = []
        for n, (neg, _si, _ri, r, _label) in enumerate(cands[:3], start=1):
            rr = dict(r, id=f"s{n}", hits=-neg, of=len(samples))
            rr.pop("seeded_from", None)
            rules.append(rr)
        out[c["key"]] = {"rules": rules, "seeded_from": cands[0][4]}
        used[c["key"]] = cands[0][4]
    return out, used


def merge_sibling_fields(rs: dict, sib_fields: dict[str, dict]) -> dict[str, str]:
    """Fold borrowed rules into a freshly authored ruleset. Where the borrowed rule works on more
    samples than our own best rule it leads (and the field records `seeded_from`); otherwise it is
    appended as a fallback. Returns {col_key: profile label} for the columns a sibling now leads."""
    led: dict[str, str] = {}
    for key, sf in sib_fields.items():
        own = rs["fields"].setdefault(key, {"rules": []})
        own_rules = [r for r in own.get("rules", []) if r.get("type") != "derived"]
        own_best = max((int(r.get("hits") or 0) for r in own_rules if r.get("type") != "const"), default=0)
        sib_rules = list(sf["rules"])
        sib_best = int(sib_rules[0].get("hits") or 0) if sib_rules else 0

        def sig(r):
            return (r.get("type"), str(r.get("label", r.get("pattern", ""))).lower(), r.get("pick"))

        if sib_best > own_best:
            merged = sib_rules + [r for r in own_rules if sig(r) not in {sig(x) for x in sib_rules}]
            own["seeded_from"] = sf["seeded_from"]
            led[key] = sf["seeded_from"]
        else:
            merged = own_rules + [r for r in sib_rules if sig(r) not in {sig(x) for x in own_rules}]
        for n, r in enumerate(merged, start=1):
            if str(r.get("id", "")).startswith("s"):
                r["id"] = f"s{n}"
        own["rules"] = merged[:5]
    return led


def author_column(col: dict, samples: list[dict], family: str = "", exclude_labels: Optional[set] = None, other_labels: Optional[set] = None, db=None) -> tuple[list[dict], str]:
    """One column on its own (a template that gained a column, or a repair). Same evidence gate as
    `author_heuristic`; there is simply no one to compete with for the span."""
    if not samples:
        return [], f"{col['label']}: I had no sample pages to learn from."
    X.clear_anchor_cache()
    try:
        phrases = label_phrases(samples)
        heads = heading_texts(samples) | label_texts(samples) | {p["norm"] for p in phrases}
        if other_labels:
            heads = heads | {" ".join(X.norm_tokens(l)) for l in other_labels}
        cands = _gather(col, samples, phrases, heads, db=db, widen=0) or _gather(col, samples, phrases, heads, db=db, widen=1)
        if not cands:
            cands = _enumerated(col, samples, phrases, heading_texts(samples))
        if exclude_labels:
            cands = [c for c in cands if str(c["rule"].get("label", "")).lower() not in exclude_labels]
        if not cands:
            return [], f"{col['label']}: no rule passed the evidence gate on these samples. It will be left for you unless a fix teaches me."
        # as in author_heuristic: a second rule is only worth saving when the first leaves a gap
        keep = cands[:MAX_RULES] if cands[0]["hits"] < cands[0]["of"] else cands[:1]
        rules = []
        for n, c in enumerate(keep, start=1):
            r = dict(c["rule"], id=f"h{n}", hits=c["hits"], of=c["of"])
            if c["rule"].get("type") in X.LABEL_RULE_TYPES and c["anchors"]:
                r["label"] = c["anchors"].most_common(1)[0][0] or r.get("label")
            rules.append(r)
        return rules, _note(col, cands[0], len(rules) - 1, len(samples))
    finally:
        X.clear_anchor_cache()


# ---- derivations: acyclic, and true on the samples ----------------------------------------------
def _add_derived(rs: dict, columns: list[dict], samples: Optional[list[dict]] = None) -> None:
    """`total = excl + VAT` is written only for a column nothing else fills, only when its inputs are
    read directly (so the graph cannot cycle), and — when there are samples — only if the sum
    actually holds on ≥90% of them. The live system had excl = total − VAT, total = excl + VAT and
    VAT = excl × 0.15 all at once: three rules defining each other."""
    from .scoring import money_roles

    roles = money_roles(columns)
    e, v, t = roles.get("excl"), roles.get("vat"), roles.get("total")
    rate = (rs.get("validators") or {}).get("vat_rate", 0.15)
    fields = rs.get("fields") or {}

    def direct(key: Optional[str]) -> bool:
        return bool(key) and bool([r for r in (fields.get(key) or {}).get("rules", []) if r.get("type") != "derived"])

    def empty(key: Optional[str]) -> bool:
        return bool(key) and key in fields and not (fields[key] or {}).get("rules")

    def holds(op: str, target: str, args: list) -> bool:
        if not samples:
            return True
        cols = readable(columns)
        ok = n = 0
        for sp in samples:
            cells = X.extract(rs, cols, sp)
            vals = []
            for a in args:
                if isinstance(a, (int, float)):
                    vals.append(float(a))
                else:
                    cv = (cells.get(a) or {}).get("value")
                    if not isinstance(cv, (int, float)):
                        vals = []
                        break
                    vals.append(float(cv))
            want = (cells.get(target) or {}).get("value")
            if not vals or not isinstance(want, (int, float)):
                continue
            got = vals[0] + vals[1] if op == "add" else (vals[0] - vals[1] if op == "sub" else vals[0] * vals[1])
            n += 1
            ok += 1 if abs(got - float(want)) <= max(0.05, abs(float(want)) * 0.002) else 0
        return n == 0 or ok >= 0.9 * n

    def add(key, rule):
        rules = fields[key]["rules"]
        rule["id"] = f"d{len(rules)+1}"
        rule["quality"] = "derived"
        rules.append(rule)

    # only a column with no rule of its own may be derived, and only from columns that have one:
    # that keeps the dependency graph a tree, one level deep.
    if empty(t) and direct(e) and direct(v) and holds("add", t, [e, v]):
        add(t, {"type": "derived", "op": "add", "args": [e, v]})
    elif empty(v) and direct(e) and direct(t) and holds("sub", v, [t, e]):
        add(v, {"type": "derived", "op": "sub", "args": [t, e]})
    elif empty(v) and direct(e) and rate and holds("mul", v, [e, rate]):
        add(v, {"type": "derived", "op": "mul", "args": [e, rate]})
    elif empty(e) and direct(t) and direct(v) and holds("sub", e, [t, v]):
        add(e, {"type": "derived", "op": "sub", "args": [t, v]})


def derivation_cycles(rs: dict) -> list[list[str]]:
    """Any cycle in the derived-rule graph (there must be none)."""
    graph: dict[str, set[str]] = {}
    for key, field in (rs.get("fields") or {}).items():
        for r in (field or {}).get("rules", []):
            if r.get("type") == "derived":
                graph.setdefault(key, set()).update(a for a in (r.get("args") or []) if isinstance(a, str))
    out: list[list[str]] = []
    state: dict[str, int] = {}

    def walk(node: str, path: list[str]):
        state[node] = 1
        for nxt in graph.get(node, ()):  # noqa: B007
            if state.get(nxt) == 1:
                out.append(path + [nxt])
            elif state.get(nxt) is None:
                walk(nxt, path + [nxt])
        state[node] = 2

    for k in list(graph):
        if state.get(k) is None:
            walk(k, [k])
    return out


# ---- sharing rules between profiles of the same layout -------------------------------------
def seed_fields(seed_rs: dict, seed_columns: list[dict], columns: list[dict]) -> tuple[dict, dict[str, str]]:
    """Copy the field rules of `seed_rs` (a profile for the same layout, built for another template)
    onto `columns`. A column takes the seed field with the same key, else the seed column with the
    same synonym group (e.g. 'total_due' ← 'total'). Returns ({col_key: field}, {col_key: seed_key}).
    Derived rules are dropped — they name the seed's column keys and are re-added for ours."""
    fields = seed_rs.get("fields") or {}
    seed_group = {}
    for sc in seed_columns:
        g = synonym_group(sc)
        if g and sc["key"] in fields and g not in seed_group:
            seed_group[g] = sc["key"]
    out: dict[str, dict] = {}
    used: dict[str, str] = {}
    for c in readable(columns):
        src = c["key"] if c["key"] in fields else seed_group.get(synonym_group(c) or "__none__")
        if not src:
            continue
        rules = [dict(r) for r in (fields[src].get("rules") or []) if r.get("type") != "derived"]
        if not rules:
            continue
        out[c["key"]] = {"rules": rules, "seeded_from": src}
        used[c["key"]] = src
    return out, used


# ---- validation of model-written rules ---------------------------------------------------
def clean_rules(raw_rules, prefix: str = "axl") -> list[dict]:
    out: list[dict] = []
    if not isinstance(raw_rules, list):
        return out
    for i, r in enumerate(raw_rules):
        if not isinstance(r, dict):
            continue
        t = r.get("type")
        if t not in X.RULE_TYPES:
            continue
        rule = {"id": r.get("id") or f"{prefix}{i+1}", "type": t, "quality": r.get("quality") if r.get("quality") in X.QUALITIES else ("derived" if t == "derived" else "positional" if t in ("regex", "between") else "exact")}
        if t in ("label_right", "label_below", "label_left", "line_after", "table"):
            if not isinstance(r.get("label"), str) or not X.norm_tokens(r["label"]):
                continue
            rule["label"] = r["label"]
            rule["pick"] = r.get("pick") if r.get("pick") in ("first", "last") else "first"
            rule["page"] = r.get("page") if isinstance(r.get("page"), int) else None
            rule["anchor"] = "label"
            rule["avoid"] = "payee"
            if t == "table":
                rule["skip"] = int(r.get("skip", 0) or 0)
        elif t == "regex":
            try:
                re.compile(str(r.get("pattern", "")))
            except re.error:
                continue
            rule["pattern"] = str(r.get("pattern", ""))
            rule["page"] = r.get("page") if isinstance(r.get("page"), int) else None
        elif t == "const":
            if r.get("value") is None:
                continue
            rule["value"] = r["value"]
        elif t == "identity":
            if r.get("slot") not in ("issuer", "issuer_name", "vat", "doc_type"):
                continue
            rule["slot"] = r["slot"]
        elif t == "derived":
            if r.get("op") not in ("add", "sub", "mul", "div") or not isinstance(r.get("args"), list) or len(r["args"]) < 2:
                continue
            rule["op"], rule["args"] = r["op"], r["args"]
        elif t == "between":
            rule["after"], rule["before"] = str(r.get("after", "")), str(r.get("before", ""))
            rule["page"] = r.get("page") if isinstance(r.get("page"), int) else None
        out.append(rule)
    return out


def verify_rules(rules: list[dict], col: dict, samples: list[dict], heads: Optional[set[str]] = None) -> list[dict]:
    """The gate applied to rules that came from anywhere else (the model, a person, a sibling).
    A rule that does not fire on ≥80% of the samples, or that reads a printed heading, is dropped —
    an unverified rule is not a fallback, it is a wrong answer waiting to happen."""
    if not samples:
        return []
    heads = heads if heads is not None else (heading_texts(samples) | label_texts(samples) | {p["norm"] for p in label_phrases(samples)})
    out = []
    for r in rules:
        if r.get("type") == "derived":
            continue
        cand = _trial(r, col, samples)
        if _gate(cand, col, samples, heads) is None:
            out.append(dict(r, hits=cand["hits"], of=cand["of"]))
    return out


def merge_model_rules(rs: dict, model_rules: dict, columns: list[dict], samples: Optional[list[dict]] = None) -> int:
    """Validated model rules go through the same evidence gate as everything else. The model is a
    candidate generator, never an authority — this is why a weaker local model is acceptable."""
    added = 0
    by_key = {c["key"]: c for c in readable(columns)}
    if not isinstance(model_rules, dict):
        return 0
    heads = (heading_texts(samples) | label_texts(samples) | {p["norm"] for p in label_phrases(samples)}) if samples else set()
    for key, rules in model_rules.items():
        col = by_key.get(key)
        if col is None:
            continue
        if isinstance(rules, dict):
            rules = rules.get("rules")
        cleaned = clean_rules(rules, prefix=f"axl-{key}-")
        if samples:
            cleaned = verify_rules(cleaned, col, samples, heads)
        if not cleaned:
            continue
        field = rs["fields"].setdefault(key, {"rules": []})
        existing = {(r.get("type"), str(r.get("label", r.get("pattern", r.get("value", "")))).lower()) for r in field["rules"]}
        for r in cleaned:
            sig = (r.get("type"), str(r.get("label", r.get("pattern", r.get("value", "")))).lower())
            if sig in existing:
                continue
            field["rules"].append(r)
            added += 1
    return added


# ---- prompts ---------------------------------------------------------------------------------
AUTHOR_SYSTEM = (
    "You are C-EXA, the document controller at CBRE Excellerate. You are shown the text of sample PDF pages "
    "(with page numbers and line order kept) and the columns a user wants filled, one row per PDF. "
    "Write extraction rules in the ruleset schema. Rule types: label_right (value to the right of a printed label), "
    "label_below, label_left, line_after, regex (pattern with one capture group), const (a fixed value that is printed on the page), "
    "derived (op add|sub|mul with args = column keys or numbers), between (text between two anchors). Prefer labels exactly as printed. "
    "Never invent values — every rule must point at text that is on the page, and every rule you write is tested on the samples "
    "before it is kept, so a guess is simply thrown away. Also write 'notes': 3-6 plain sentences for a "
    "non-technical colleague describing this layout and anything tricky about it."
)
AUTHOR_SCHEMA = {"notes": "string", "rules": {"<col_key>": [{"type": "label_right", "label": "string", "pick": "first|last", "page": "int|null"}]}}

REPAIR_SYSTEM = (
    "You are C-EXA. A ruleset extracts columns from PDFs. Some columns scored below 90% on sample documents. You are shown the "
    "current rules for those columns, what each rule returned (or failed to return) on each sample, and the text around the "
    "expected label. Propose better rules (same schema as the ruleset) for ONLY the failing columns, and write 'notes': one or two "
    "plain sentences on what went wrong and what you changed, the way you'd tell a colleague."
)
REPAIR_SCHEMA = {"notes": "string", "changes": ["string"], "rules": {"<col_key>": [{"type": "label_right", "label": "string"}]}}


def sample_text_for_prompt(samples: list[dict], max_chars: int = 7000) -> str:
    parts = []
    budget = max_chars
    for n, sp in enumerate(samples, 1):
        parts.append(f"=== Sample {n} ===")
        for l in sp["lines"]:
            s = f"[p{l['page']}] {l['text']}"
            if budget - len(s) < 0:
                parts.append("…")
                break
            parts.append(s)
            budget -= len(s) + 1
    return "\n".join(parts)
