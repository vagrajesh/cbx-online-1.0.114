# 1.0.104: new (Builder U) — V07/V08: the utility bill history (VALUENCE-AP-GAPS.md §2 V07 item 1)
"""Every utility bill CBX reads, one row per bill (`ap_bill_history`): account, period, read dates, kWh, kl, the charge
per service excl. VAT, reading type (A/E), tariff name, and the balances when the profile reads them.

    record(db, c)        Tess's hook: the capture's row, written or brought up to date (estimates are not bills)
    periods(db, key)     an account's bills, oldest → newest (the variance checks, the estimates, the municipal
                         roll-forward of the reconciliations read this)
    backfill(db)         once (the routine below): every utility capture read before 1.0.104
    extras(got)          capture.utility_fields's hook: kl, tariff and balances from the profile's rows

Text from a bill is data: nothing here acts on it."""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any, Optional

from sqlalchemy.orm import Session

from .. import compat2 as jobs
from ..db import session_scope, utcnow
from ..models.ap import ApBillHistory, ApCapture, ApItem

logger = logging.getLogger("cbx.ap.bill_history")

BACKFILL_KEY = "ap.v1104.bill_history_backfill"  # the once-marker (app_state)
CHANGED_LINE = "The bill's figures changed; the variance needs a new decision."
BACKFILL_CHUNK = 500  # captures per transaction: the SQLite write lock is never held for long
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


# ---- small helpers ---------------------------------------------------------------------------------------------------------
def num(v: Any) -> Optional[float]:
    """A number from a bill's text ("1 234,50" "R1,234.50" "-12"), else None."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if v == v and abs(v) != float("inf") else None
    s = re.sub(r"[R\s]", "", str(v))
    if re.fullmatch(r"-?\d+,\d{1,2}", s):
        s = s.replace(",", ".")
    s = s.replace(",", "")
    if s.endswith("-"):  # "123.45-" (a credit, SAP style)
        s = "-" + s[:-1]
    try:
        f = float(s)
    except ValueError:
        return None
    return f if f == f and abs(f) != float("inf") else None


def iso(v: Any) -> str:
    """yyyy-mm-dd from yyyy-mm-dd / yyyy/mm/dd / dd/mm/yyyy / dd.mm.yyyy, else ""."""
    s = str(v or "").strip()
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})", s)
        if not m:
            return ""
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return ""


def period_of(read_end: str = "", bill_date: str = "", month_words: str = "") -> str:
    """The bill's period (yyyy-mm): the month its reading ends in, else its bill date's, else its printed month."""
    for v in (read_end, bill_date):
        d = iso(v)
        if d:
            return d[:7]
    m = re.search(r"([a-z]{3})[a-z]*\W*(\d{2,4})", str(month_words or "").lower())
    if m and m.group(1) in MONTHS:
        y = int(m.group(2))
        return f"{y + 2000 if y < 100 else y:04d}-{MONTHS[m.group(1)]:02d}"
    return ""


def shift(period: str, months: int) -> str:
    y, m = int(period[:4]), int(period[5:7])
    n = y * 12 + (m - 1) + months
    return f"{n // 12:04d}-{n % 12 + 1:02d}"


def days_between(a: str, b: str) -> Optional[int]:
    try:
        return (date.fromisoformat(b) - date.fromisoformat(a)).days
    except (TypeError, ValueError):
        return None


def acct_key(account: Any) -> str:
    from . import masters as M

    return M.acct_key(account)


# ---- capture.utility_fields's hook: what else the profile read ----------------------------------------------------------------
_KL = re.compile(r"(^|_)kl($|_)")
_KL_NOT = re.compile(r"(^|_)(start|end|prev|previous|curr|current|read|reading|r|rand|rands|tariff|rate|cents|c|txt|desc|doc|diff)($|_)")
_OPEN = re.compile(r"(^|_)(opening|bbf|bf|brought|prev_balance|previous_balance|balance_bf|bal_opening|opening_balance)($|_)")
_ARREARS = re.compile(r"(^|_)(arrears|arrear|overdue|outstanding)($|_)")
_CLOSE = re.compile(r"(^|_)(closing|bal_closing|total_due|amount_due|total_now_due|balance_due|closing_balance)($|_)")
_SKIP = re.compile(r"(^|_)(txt|desc|delta|calc|gap|vs|sum|check|date|days)($|_)")


def _types(prof: dict) -> dict:
    out: dict[str, str] = {}
    for r in prof.get("fields") or []:
        fs = r["fields"] if "fields" in r else [r.get("field")]
        ts = r.get("types") or [r.get("type", "text")] * len(fs)
        for f, t in zip(fs, ts):
            if f:
                out.setdefault(f, t)
    return out


def extras(got: dict) -> dict:
    """{kl, tariff, opening, arrears, closing} from the municipal engine's read (MU.read): the water row's kl, the
    electricity tariff name, and the balances on the bill's first row when the profile reads them (else None / "")."""
    try:
        rows = got.get("rows") or []
        prof = got.get("profile") or {}
        types = _types(prof)
        canon = got.get("canon") or {}
        out: dict[str, Any] = {"kl": None, "tariff": str(canon.get("tariff_name") or "")[:120], "opening": None, "arrears": None, "closing": None}
        kl = 0.0
        seen_kl = False
        for r in rows:
            if r.get("service") not in ("water", None, ""):
                continue
            for k, v in r.items():
                if k.startswith("_") or not _KL.search(k) or _KL_NOT.search(k) or types.get(k) == "money":
                    continue
                n = num(v)
                if n is not None:
                    kl += n
                    seen_kl = True
                    break  # one kl figure per row (the total, not its parts)
        out["kl"] = round(kl, 3) if seen_kl else None
        meta = rows[0] if rows else {}
        for key, rx in (("opening", _OPEN), ("arrears", _ARREARS), ("closing", _CLOSE)):
            for k, v in meta.items():
                if k.startswith("_") or not rx.search(k) or _SKIP.search(k) or types.get(k) not in ("money", None):
                    continue
                n = num(v)
                if n is not None:
                    out[key] = n
                    break
        return out
    except Exception:  # noqa: BLE001 — extras never stop a bill being read
        logger.exception("bill extras")
        return {}


# ---- the history ----------------------------------------------------------------------------------------------------------
def is_estimate(c: ApCapture) -> bool:
    """An EST- document CBX made itself (cbx/ap/estimates.py): not a bill."""
    return bool((c.fields or {}).get("estimate"))


def _billable(c: ApCapture) -> bool:
    f = c.fields or {}
    return c.bucket == "utility" and f.get("kind") == "utility" and not is_estimate(c) and bool(acct_key(f.get("account")))


def values_of(db: Session, c: ApCapture) -> dict:
    """The history row's values for a utility capture (what the bill says now: a person may have corrected it)."""
    f = c.fields or {}
    x = f.get("bill_extras") or {}
    canon = (((c.read or {}).get("elec_row") or {}).get("_canon") or {})
    amounts: dict[str, float] = {}
    vat = 0.0
    seen_vat = False
    for s in f.get("services") or []:
        n = num(s.get("ex"))
        if n is None:
            continue
        amounts[str(s.get("service") or "other")] = round(amounts.get(str(s.get("service") or "other"), 0.0) + n, 2)
        v = num(s.get("vat"))
        if v is not None:
            vat += v
            seen_vat = True
    rs, re_ = iso(f.get("read_start")), iso(f.get("read_end"))
    bd = iso(f.get("billing_date"))
    rt = str(f.get("reading_type") or canon.get("reading_type") or "").strip().upper()[:1]
    a = ((c.match or {}).get("account") or {})
    port = c.portfolio or ""
    if not port:
        try:
            from . import portfolio as PF

            p = PF.for_capture(db, c)
            port = p.code if p is not None else ""
        except Exception:  # noqa: BLE001
            port = ""
    return {
        "account_key": acct_key(f.get("account")), "portfolio": port, "municipality": str(a.get("muni") or f.get("municipality") or "")[:160],
        "profile": str(c.profile or f.get("profile") or "")[:64], "period": period_of(re_, bd, f.get("statement_month")), "bill_date": bd,
        "invoice_no": str(f.get("invoice_no") or "")[:60], "read_start": rs, "read_end": re_, "days": days_between(rs, re_) if rs and re_ else None,
        "kwh": num(f.get("kwh")), "kl": num(x.get("kl")), "amounts": amounts, "total_excl": round(sum(amounts.values()), 2) if amounts else None,
        "vat": round(vat, 2) if seen_vat else None, "reading_type": rt if rt in ("A", "E") else "",
        "tariff": str(x.get("tariff") or canon.get("tariff_name") or "")[:120],
        "opening": num(x.get("opening")), "arrears": num(x.get("arrears")), "closing": num(x.get("closing")),
    }


def row_for(db: Session, capture_id: int) -> Optional[ApBillHistory]:
    return db.query(ApBillHistory).filter(ApBillHistory.capture_id == capture_id).one_or_none()


def record(db: Session, c: ApCapture, source: str = "capture") -> Optional[ApBillHistory]:
    """Tess's hook: the capture's history row (new, or brought up to date with what the bill says now). None for what is
    not a utility bill with an account. A decision a person took on the row stays."""
    if c.id is None or not _billable(c):
        return None
    vals = values_of(db, c)
    h = row_for(db, c.id)
    if h is None:
        h = ApBillHistory(capture_id=c.id, source=source, findings=[], **vals)
        db.add(h)
        db.flush()
        return h
    moved = any(getattr(h, k) != vals[k] for k in ("total_excl", "kwh"))
    for k, v in vals.items():
        if getattr(h, k) != v:
            setattr(h, k, v)
    if moved and h.decision == "accepted":  # 1.0.104 (review): an acceptance is for the figures it saw — new figures, a new decision
        h.decision, h.decision_reason, h.decided_by, h.decided_by_id, h.decided_at = "", "", "", None, None
        it = db.get(ApItem, c.item_id) if c.item_id else None
        if it is not None:
            from . import service as S

            S._add(it, "Tess", CHANGED_LINE)
    return h


def _out(h: ApBillHistory) -> dict:
    return {"id": h.id, "period": h.period, "bill_date": h.bill_date, "invoice_no": h.invoice_no, "read_start": h.read_start, "read_end": h.read_end,
            "days": h.days, "kwh": h.kwh, "kl": h.kl, "amounts": dict(h.amounts or {}), "total_excl": h.total_excl, "vat": h.vat,
            "reading_type": h.reading_type, "tariff": h.tariff, "opening": h.opening, "arrears": h.arrears, "closing": h.closing,
            "capture_id": h.capture_id, "source": h.source, "portfolio": h.portfolio, "municipality": h.municipality,
            "decision": h.decision, "dispute_id": h.dispute_id}


def rows_of(db: Session, account_key: str, *, before: Optional[ApBillHistory] = None) -> list[ApBillHistory]:
    """An account's history rows, oldest → newest (by period, then reading end, then id); `before` = only the bills
    before that one (by period, then id)."""
    k = acct_key(account_key) or str(account_key or "")
    if not k:
        return []
    rows = db.query(ApBillHistory).filter(ApBillHistory.account_key == k).all()
    rows.sort(key=lambda h: (h.period or "", h.read_end or "", h.id or 0))
    if before is not None:
        rows = [h for h in rows if h.id != before.id and ((h.period or "") < (before.period or "") or ((h.period or "") == (before.period or "") and (h.id or 0) < (before.id or 0)))]
    return rows


def periods(db: Session, account_key: str) -> list[dict]:
    """An account's bills, oldest → newest: [{period (yyyy-mm), read_start, read_end (yyyy-mm-dd), kwh, kl, amounts
    ({service: R excl. VAT}), total_excl, vat, reading_type (A/E/""), tariff, opening, arrears, closing (None when the
    profile does not read them), capture_id, …}]. One entry per bill read (a period may have two when a bill was
    read twice or corrected by a second bill); `account_key` may be the account as printed (it is normalised)."""
    return [_out(h) for h in rows_of(db, account_key)]


# ---- the one-time backfill -------------------------------------------------------------------------------------------------
def backfill(db: Session, after_id: int = 0, limit: Optional[int] = None) -> tuple[int, int]:
    """Utility captures already read (built, tested or uploaded) with id > `after_id` (at most `limit` of them) get their
    history row (source "backfill"); a capture that has one already is left alone, so running it twice adds nothing.
    → (rows added, the last capture id looked at; 0 when none was left)."""
    q = (db.query(ApCapture).filter(ApCapture.bucket == "utility", ApCapture.stage.in_(("sap", "test", "ready")), ApCapture.id > after_id)
         .order_by(ApCapture.id))
    caps = q.limit(limit).all() if limit else q.all()
    have = {i for (i,) in db.query(ApBillHistory.capture_id).filter(ApBillHistory.capture_id.in_([c.id for c in caps] or [0])).all()}
    n = 0
    for c in caps:
        if c.id in have or not _billable(c):
            continue
        if record(db, c, source="backfill") is not None:
            n += 1
    return n, (caps[-1].id if caps else 0)


def run_backfill(force: bool = False) -> Optional[int]:
    """The backfill with its once-marker and audit row; its count, or None when it had already run. 1.0.104 (review): one
    commit per BACKFILL_CHUNK captures, so the write lock is never held for minutes; an interrupted run leaves no marker
    and the next round carries on (rows already there are skipped)."""
    with session_scope() as db:
        if not force and jobs.state_get(db, BACKFILL_KEY) is not None:
            return None
    total, last = 0, 0
    while True:
        with session_scope() as db:
            n, last = backfill(db, last, BACKFILL_CHUNK)
        total += n
        if not last:
            break
    with session_scope() as db:
        jobs.audit(db, "ap_bill_history_backfill", actor_label="system", target="ap", detail=f"{total} utility bills into the bill history", count=total)
        jobs.state_set(db, BACKFILL_KEY, {"at": utcnow().isoformat(timespec="seconds"), "count": total})
    return total


@jobs.routine("ap-bill-history-backfill", 3600)
def backfill_routine() -> None:
    """Once, at the first start of 1.0.104 (the scheduler's first round): the bills read before it go into the history.
    A failure leaves no marker: the next round tries again."""
    run_backfill()
