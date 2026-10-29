# 1.0.104: new (Builder U) — V08: the estimate register and its true-up (VALUENCE-AP-GAPS.md §2 V08)
"""An account whose bill did not come is estimated, approved by a second person, posted, and trued up when the real bill
for that account and period arrives.

    missing(db)                 active accounts past their usual bill day (+ grace) with no bill and no estimate for a
                                period: the Estimates view's list, each with the proposed value
    propose(db, key, period)    the value from ap_bill_history by the basis (portfolio `estimate_basis`, or per municipality
                                or account in `estimate_basis_by`): last · avg3 · avg6 · daily (usage per day × the days in
                                the month) · prepaid (the fixed amount the preparer types; the last bill's to start from)
    prepare(db, user, data)     the preparer (capturer role) → ap_estimates "prepared" (audited ap_estimate_created)
    approve(db, user, id)       a second person (approver role, never the preparer) → "approved": the EST-<account>-<yyyymm>
                                document goes through Mia, Pip and Tess in the batch "Estimates <yyyy-mm>" (audited
                                ap_estimate_approved)
    reject(db, user, id, why)
    true_up(db, c, h)           Tess's hook (cbx/ap/variance.tess): the real bill for an estimated account and period →
                                the estimate is "trued_up" with the actual capture, value and difference. An estimate not
                                uploaded yet is taken out (never posted); one uploaded is reversed by a credit memo
                                EST-…-R in the estimates batch of the month (portfolio `estimate_true_up` "reverse"), or
                                EST-…-ADJ in next month's batch ("adjust": in the next cycle). The history of both files
                                links them. Audited ap_estimate_trued_up.
    escalations(db)             accounts with `estimate_escalate_n` estimates running (no real bill since): Needs you →
                                "escalate to Council Liaison"; escalate(db, user, key) records it (ap_estimate_escalated)
    report_xlsx(db, month)      count and value of open estimates per portfolio, for the client"""
from __future__ import annotations

import calendar
import io
import statistics
from datetime import date, timedelta
from typing import Any, Optional

from sqlalchemy.orm import Session

from .. import jobs as J
from ..audit import log
from ..db import after_commit, utcnow
from ..models import User
from ..models.ap import ApBatch, ApBillHistory, ApCapture, ApEstimate, ApItem, ApUtilityAccount
from . import bill_history as BH
from . import portfolio as PF
from . import roles as R

BASES = ("last", "avg3", "avg6", "daily", "prepaid")
BASIS_WORDS = {"last": "last bill", "avg3": "3-bill average", "avg6": "6-bill average", "daily": "daily usage × days", "prepaid": "prepaid fixed"}
OPEN = ("prepared", "approved")
NEW_KEYS = {"estimate_basis_by": {},  # V08: {"<municipality or account>": "avg3"}: the basis for one municipality or account
            "estimate_true_up": "reverse"}  # V08: reverse (credit memo now) · adjust (credit memo in the next cycle's batch)
for _k, _v in NEW_KEYS.items():
    PF.setting(None, None, _k, _v)
PF.DESCRIBE.setdefault("estimate_basis_by", ("Estimate basis per municipality or account", 'JSON, e.g. {"Bela-Bela": "avg3", "2100090266": "daily"} (else the estimate basis).', ()))
PF.DESCRIBE.setdefault("estimate_true_up", ("Estimate true-up", "reverse: a credit memo reverses the estimate when the real bill comes · adjust: in the next cycle's batch.", ("reverse", "adjust")))
VAT = 0.15
NO_VAT = {"rates"}  # property rates carry no VAT (as Pip)


class Refused(Exception):
    pass


def _who(u: Optional[User]) -> str:
    return (u.name or u.email) if u else ""


def _line(e: ApEstimate, who: str, text: str) -> None:
    e.history = [*(e.history or []), {"at": utcnow().isoformat(timespec="seconds"), "who": who, "text": text}][-60:]


def _item_line(it: Optional[ApItem], who: str, text: str) -> None:
    if it is not None:
        from . import service as S

        S._add(it, who, text)


def _period_ok(p: str) -> bool:
    try:
        return len(p) == 7 and 1 <= int(p[5:7]) <= 12 and 2000 <= int(p[:4]) <= 2100 and p[4] == "-"
    except ValueError:
        return False


def _today() -> date:
    from . import sap as P

    return P.sap_today()


def _account(db: Session, key: str) -> Optional[ApUtilityAccount]:
    return db.query(ApUtilityAccount).filter(ApUtilityAccount.account_key == key).order_by(ApUtilityAccount.id.desc()).first()


def _active(a: Optional[ApUtilityAccount]) -> bool:
    st = str(getattr(a, "status", "") or "").lower() if a is not None else ""  # Builder C's account status (V10), when it exists
    return st in ("", "active")


def _port(db: Session, key: str, rows: list[ApBillHistory]) -> str:
    if rows and rows[-1].portfolio:
        return rows[-1].portfolio
    a = _account(db, key)
    p = PF.for_account(db, a) if a is not None else PF.default(db)
    return p.code if p is not None else ""


def basis_for(db: Session, port: str, key: str, municipality: str = "") -> str:
    by = PF.setting(db, port or None, "estimate_basis_by", {}) or {}
    if isinstance(by, dict):
        for k, v in by.items():
            kk = BH.acct_key(k) or ""
            if (kk and kk == key) or (municipality and T_key(k) and T_key(k) in T_key(municipality)):
                if str(v).strip() in BASES:
                    return str(v).strip()
    b = str(PF.setting(db, port or None, "estimate_basis", "avg6") or "avg6")
    return b if b in BASES else "avg6"


def T_key(v: Any) -> str:  # noqa: N802
    from .tariffs import key

    return key(v)


# ---- the proposal ---------------------------------------------------------------------------------------------------------
def propose(db: Session, key: str, period: str, basis: Optional[str] = None) -> dict:
    """{basis, amounts {service: R excl. VAT}, value, vat, kwh, workings} from the account's bills before `period`."""
    key = BH.acct_key(key) or str(key or "")
    rows = [h for h in BH.rows_of(db, key) if (h.period or "") < period]
    if not rows:
        raise Refused("This account has no earlier bill to estimate from.")
    port = _port(db, key, rows)
    basis = basis if basis in BASES else basis_for(db, port, key, rows[-1].municipality)
    how, used = BASIS_WORDS[basis], rows
    amounts: dict[str, float] = {}
    kwh: Optional[float] = None
    if basis in ("last", "prepaid"):
        used = rows[-1:]
        amounts = {k: float(v) for k, v in (used[0].amounts or {}).items()}
        kwh = used[0].kwh
    elif basis in ("avg3", "avg6"):
        used = rows[-(3 if basis == "avg3" else 6):]
        for h in used:
            for k, v in (h.amounts or {}).items():
                amounts[k] = amounts.get(k, 0.0) + float(v or 0)
        amounts = {k: v / len(used) for k, v in amounts.items()}
        ks = [h.kwh for h in used if h.kwh is not None]
        kwh = sum(ks) / len(ks) if ks else None
    else:  # daily: usage per day of the bills that show their reading dates × the days in the estimated month
        used = [h for h in rows[-6:] if h.days and h.days > 0]
        if not used:
            return {**propose(db, key, period, "avg6"), "note": "No bill shows its reading dates: the 6-bill average is used instead of daily usage."}
        days = sum(h.days for h in used)
        target = calendar.monthrange(int(period[:4]), int(period[5:7]))[1]
        for h in used:
            for k, v in (h.amounts or {}).items():
                amounts[k] = amounts.get(k, 0.0) + float(v or 0)
        amounts = {k: v / days * target for k, v in amounts.items()}
        ks = [h.kwh for h in used if h.kwh is not None]
        kwh = sum(ks) / days * target if ks else None
        how += f" ({days} days read, {target} days in {period})"
    amounts = {k: round(v, 2) for k, v in amounts.items() if round(v, 2)}
    value = round(sum(amounts.values()), 2)
    vat = round(sum(v * VAT for k, v in amounts.items() if k not in NO_VAT), 2)
    return {"basis": basis, "basis_words": BASIS_WORDS[basis], "amounts": amounts, "value": value, "vat": vat, "kwh": round(kwh, 1) if kwh is not None else None,
            "portfolio": port, "municipality": rows[-1].municipality,
            "workings": {"how": how, "periods": [h.period for h in used], "values": [h.total_excl for h in used], "kwh": [h.kwh for h in used]}}


# ---- missing bills ----------------------------------------------------------------------------------------------------------
def usual_day(a: Optional[ApUtilityAccount], rows: list[ApBillHistory]) -> int:
    d = (getattr(a, "bill_day", None) or getattr(a, "usual_bill_day", None)) if a is not None else None  # Builder C's field (V10), when it exists
    try:
        if d and 1 <= int(d) <= 31:
            return int(d)
    except (TypeError, ValueError):
        pass
    days = [int(h.bill_date[8:10]) for h in rows[-6:] if h.bill_date and len(h.bill_date) == 10]
    return int(statistics.median(days)) if days else 28


def _due(period: str, day: int, grace: int) -> date:
    y, m = int(period[:4]), int(period[5:7])
    return date(y, m, min(day, calendar.monthrange(y, m)[1])) + timedelta(days=grace)


def missing(db: Session, portfolio: str = "", today: Optional[date] = None, limit: int = 500) -> list[dict]:
    """Every active account with bills in the history whose next bill is past its usual day + grace (`missing_grace_days`)
    and has neither a bill nor an open estimate: the earliest such period, how many are missing, and the proposal."""
    today = today or _today()
    now = today.strftime("%Y-%m")
    by: dict[str, list[ApBillHistory]] = {}
    for h in db.query(ApBillHistory).filter(ApBillHistory.account_key != "").all():
        by.setdefault(h.account_key, []).append(h)
    ests = {(e.account_key, e.period) for e in db.query(ApEstimate).filter(ApEstimate.status.in_(OPEN + ("trued_up",))).all()}
    out = []
    for key, rows in by.items():
        rows.sort(key=lambda h: (h.period or "", h.id))
        rows = [h for h in rows if h.period]
        if not rows:
            continue
        port = rows[-1].portfolio or ""
        if portfolio and port != portfolio:
            continue
        a = _account(db, key)
        if not _active(a):
            continue
        have = {h.period for h in rows}
        day, grace = usual_day(a, rows), int(float(PF.setting(db, port or None, "missing_grace_days", 10) or 0))
        p, gone = BH.shift(rows[-1].period, 1), []
        while p <= now and len(gone) < 24:
            if p not in have and (key, p) not in ests and _due(p, day, grace) <= today:
                gone.append(p)
            p = BH.shift(p, 1)
        if not gone:
            continue
        try:
            prop = propose(db, key, gone[0])
        except Refused:
            continue
        out.append({"account_key": key, "account": a.account if a else key, "site": a.site if a else "", "municipality": (a.municipality if a else "") or rows[-1].municipality,
                    "portfolio": port, "period": gone[0], "missing": len(gone), "periods": gone, "usual_day": day,
                    "due": _due(gone[0], day, grace).isoformat(), "last_period": rows[-1].period, "proposal": prop})
        if len(out) >= limit:
            break
    out.sort(key=lambda r: (r["period"], r["account_key"]))
    return out


# ---- prepare / approve / reject ---------------------------------------------------------------------------------------------
def out(e: ApEstimate) -> dict:
    return {"id": e.id, "account_key": e.account_key, "account": e.account, "site": e.site, "municipality": e.municipality, "portfolio": e.portfolio,
            "period": e.period, "basis": e.basis, "basis_words": BASIS_WORDS.get(e.basis, e.basis), "value": e.value, "vat": e.vat, "kwh": e.kwh,
            "workings": e.workings or {}, "status": e.status, "prepared_by": e.prepared_by, "prepared_by_id": e.prepared_by_id,
            "prepared_at": e.prepared_at.isoformat() if e.prepared_at else "", "approved_by": e.approved_by, "approved_at": e.approved_at.isoformat() if e.approved_at else "",
            "note": e.note, "capture_id": e.capture_id, "actual_capture_id": e.actual_capture_id, "actual_value": e.actual_value, "difference": e.difference,
            "true_up": e.true_up, "trued_up_at": e.trued_up_at.isoformat() if e.trued_up_at else "", "reversal_capture_id": e.reversal_capture_id,
            "escalated_by": e.escalated_by, "escalated_at": e.escalated_at.isoformat() if e.escalated_at else "", "reference": reference(e), "history": e.history or []}


def reference(e: ApEstimate) -> str:
    return f"EST-{e.account_key}-{e.period.replace('-', '')}"


def _get(db: Session, eid: int) -> ApEstimate:
    e = db.get(ApEstimate, eid)
    if e is None:
        raise LookupError("No such estimate.")
    return e


def prepare(db: Session, user: User, data: dict) -> ApEstimate:
    key = BH.acct_key((data or {}).get("account_key") or (data or {}).get("account"))
    period = str((data or {}).get("period") or "").strip()[:7]
    if not key:
        raise Refused("Which account?")
    if not _period_ok(period):
        raise Refused("The period is yyyy-mm.")
    if db.query(ApBillHistory.id).filter(ApBillHistory.account_key == key, ApBillHistory.period == period).first():
        raise Refused(f"The bill for {period} is already in: nothing to estimate.")
    if db.query(ApEstimate.id).filter(ApEstimate.account_key == key, ApEstimate.period == period, ApEstimate.status.in_(OPEN + ("trued_up",))).first():
        raise Refused(f"An estimate for {period} exists already.")
    basis = str((data or {}).get("basis") or "").strip() or None
    if basis and basis not in BASES:
        raise Refused("The basis is one of " + ", ".join(BASES) + ".")
    prop = propose(db, key, period, basis)
    R.require(db, user, "capturer", prop["portfolio"] or None, what="Preparing an estimate")
    typed = BH.num((data or {}).get("value"))
    if prop["basis"] == "prepaid" and typed is None:
        raise Refused("A prepaid estimate needs the fixed amount (R excl. VAT).")
    amounts = dict(prop["amounts"])
    note = str((data or {}).get("note") or "").strip()[:2000]
    if typed is not None:
        if typed <= 0:
            raise Refused("An estimate is more than R 0.")
        if prop["value"] and abs(typed - prop["value"]) > 0.005:
            f = typed / prop["value"]
            amounts = {k: round(v * f, 2) for k, v in amounts.items()}
            amounts[next(iter(amounts))] = round(amounts[next(iter(amounts))] + typed - sum(amounts.values()), 2)
            if len(note) < 3:
                raise Refused(f"Say why the value differs from the proposal (R {prop['value']:,.2f}).")
        elif not prop["value"]:
            amounts = {"electricity": round(typed, 2)}
    a = _account(db, key)
    value = round(sum(amounts.values()), 2)
    e = ApEstimate(account_key=key, account=(a.account if a else key)[:60], site=(a.site if a else "")[:160], municipality=prop["municipality"][:160], portfolio=prop["portfolio"],
                   period=period, basis=prop["basis"], value=value, vat=round(sum(v * VAT for k, v in amounts.items() if k not in NO_VAT), 2), kwh=prop["kwh"],
                   workings={**prop["workings"], "amounts": amounts, "proposed": prop["value"]}, status="prepared", prepared_by=_who(user), prepared_by_id=user.id,
                   prepared_at=utcnow(), note=note, history=[])
    db.add(e)
    db.flush()
    _line(e, _who(user), f"Prepared: R {value:,.2f} excl. VAT ({BASIS_WORDS[e.basis]})" + (f" — {note[:200]}" if note else ""))
    log(db, "ap_estimate_created", actor=user, target=f"ap_estimate:{e.id}", detail=f"{reference(e)}: R {value:,.2f} ({e.basis})" + (f" — {note[:160]}" if note else ""))
    return e


def reject(db: Session, user: User, eid: int, reason: str) -> ApEstimate:
    e = _get(db, eid)
    if e.status != "prepared":
        raise Refused("Only a prepared estimate is rejected.")
    reason = str(reason or "").strip()
    if len(reason) < 3:
        raise Refused("Say why.")
    if user.id != e.prepared_by_id:  # the preparer may withdraw their own; anyone else needs the approver role
        R.require(db, user, "approver", e.portfolio or None, what="Rejecting an estimate")
    e.status = "rejected"
    _line(e, _who(user), f"Rejected: {reason[:300]}")
    log(db, "ap_estimate_rejected", actor=user, target=f"ap_estimate:{e.id}", detail=f"{reference(e)} — {reason[:200]}")
    return e


def approve(db: Session, user: User, eid: int) -> ApEstimate:
    e = _get(db, eid)
    if e.status != "prepared":
        raise Refused("This estimate is not waiting for approval.")
    R.require(db, user, "approver", e.portfolio or None, what="Approving an estimate")
    if user.id == e.prepared_by_id:
        raise R.Forbidden("You prepared this estimate, so a different person approves it.")
    if db.query(ApBillHistory.id).filter(ApBillHistory.account_key == e.account_key, ApBillHistory.period == e.period).first():
        raise Refused(f"The real bill for {e.period} came in meanwhile: reject this estimate.")
    e.status, e.approved_by, e.approved_by_id, e.approved_at = "approved", _who(user), user.id, utcnow()
    c = _document(db, user, e, reverse=False)
    e.capture_id = c.id
    _line(e, _who(user), f"Approved: {reference(e)} goes to Pip in the folder \"{db.get(ApBatch, c.batch_id).name}\".")
    log(db, "ap_estimate_approved", actor=user, target=f"ap_estimate:{e.id}", detail=f"{reference(e)}: R {e.value:,.2f} prepared by {e.prepared_by}")
    from . import capture as C

    db.flush()
    C.advance(db, db.get(ApBatch, c.batch_id), live=False)
    return e


# ---- the EST- documents (Mia, Pip and Tess as any bill) ------------------------------------------------------------------------
NOT_READ_AGAIN = "Estimates are not read again: change or reject the estimate instead."


def is_document(c: ApCapture) -> bool:
    """An EST- document (or its reversal) CBX made: no file behind it, nothing to read."""
    return BH.is_estimate(c) or c.read_by == "estimate" or bool((c.read or {}).get("estimate"))


def item_ids(db: Session, batch_id: int) -> set[int]:
    """The files of a folder that are EST- documents ("Read again" leaves them as they are)."""
    return {c.item_id for c in db.query(ApCapture).filter(ApCapture.batch_id == batch_id).all() if is_document(c)}


def _batch(db: Session, user: Optional[User], period: str, port: str) -> ApBatch:
    name = f"Estimates {period}" + (f" · {port}" if port else "")
    b = db.query(ApBatch).filter(ApBatch.name == name).order_by(ApBatch.id.desc()).first()
    if b is None:
        owner = user.id if user is not None else None
        if owner is None:
            from ..models import User as U

            first = db.query(U.id).filter(U.role.in_(("owner", "admin"))).order_by(U.id).first() or db.query(U.id).order_by(U.id).first()
            owner = first[0] if first else None
        b = ApBatch(user_id=owner, name=name, status="ready", counts={}, note="Estimates (V08): EST- documents CBX made for bills that did not come.",
                    portfolio=port or "", portfolio_how="picked" if port else "")
        db.add(b)
        db.flush()
    return b


def _fields(db: Session, e: ApEstimate, sign: int, suffix: str, today: date) -> dict:
    last = BH.rows_of(db, e.account_key)
    prev = next((h for h in reversed(last) if h.capture_id), None)
    pc = db.get(ApCapture, prev.capture_id) if prev is not None else None
    pf = (pc.fields or {}) if pc is not None else {}
    y, m = int(e.period[:4]), int(e.period[5:7])
    end = date(y, m, calendar.monthrange(y, m)[1])
    bill = min(end, today) if sign > 0 else today
    days = 30
    try:
        if pf.get("due_date") and pf.get("billing_date"):
            days = max(1, min(90, (date.fromisoformat(pf["due_date"]) - date.fromisoformat(pf["billing_date"])).days))
    except ValueError:
        pass
    amounts = (e.workings or {}).get("amounts") or {"electricity": e.value or 0}
    services = [{"service": k, "ex": "%.2f" % (sign * v), "vat": "%.2f" % (sign * (0 if k in NO_VAT else round(v * VAT, 2))),
                 "how": f"estimate ({BASIS_WORDS.get(e.basis, e.basis)})", "sure": True} for k, v in amounts.items()]
    return {"kind": "utility", "municipality": e.municipality or pf.get("municipality") or "", "profile": pf.get("profile") or "",
            "account": pf.get("account") or e.account or e.account_key, "site": e.site or pf.get("site") or "", "billing_date": bill.isoformat(),
            "due_date": (bill + timedelta(days=days)).isoformat(), "statement_month": "Estimate" if sign > 0 else "Estimate reversal",
            "invoice_no": reference(e) + suffix, "read_start": "", "read_end": "", "reading_type": "E", "kwh": "" if e.kwh is None else "%g" % (sign * e.kwh),
            "services": services, "adjustments": {}, "vat_printed": None, "current_ex": "", "current_incl": "", "checks": [], "ocr": False,
            "estimate": {"id": e.id, "basis": e.basis, "reverse": sign < 0}}


def _document(db: Session, user: Optional[User], e: ApEstimate, *, reverse: bool, suffix: str = "", period: Optional[str] = None) -> ApCapture:
    """An EST- capture in the estimates batch of `period` (default: the estimate's), at Mia's stage."""
    today = _today()
    b = _batch(db, user, period or e.period, e.portfolio)
    f = _fields(db, e, -1 if reverse else 1, suffix, today)
    total = sum(float(s["ex"]) + float(s["vat"]) for s in f["services"])
    it = ApItem(batch_id=b.id, name=f"{f['invoice_no']}.estimate", kind="utility", bot="uma", confidence=100, status="ok", level=0,
                why="An estimate (V08)" if not reverse else "The reversal of an estimate (V08)", fields={"account": f["account"], "invoice_no": f["invoice_no"], "total": total},
                findings=[], history=[], supplier=(f["municipality"] or "")[:160], ref=f["invoice_no"][:120], amount=total)
    db.add(it)
    db.flush()
    _item_line(it, _who(user) or "CBX", (f"Estimate {reference(e)} for {e.period}: R {e.value:,.2f} excl. VAT ({BASIS_WORDS.get(e.basis, e.basis)}), prepared by "
                                         f"{e.prepared_by}, approved by {e.approved_by}.") if not reverse else
               f"Reverses estimate {reference(e)} (R {e.value:,.2f} excl. VAT): the real bill came.")
    c = ApCapture(item_id=it.id, batch_id=b.id, bucket="utility", stage="match", read={"estimate": e.id}, fields=f, match={}, sap_rows=[], sap_issues=[],
                  confidence=100, read_by="estimate", read_at=utcnow(), sorted_at=utcnow(), worker="uma", profile=f["profile"][:64], portfolio=e.portfolio or "")
    db.add(c)
    db.flush()
    # 1.0.104 (review): the preparer and the approver touched this document (roles.touched_by reads ap_item audit rows), so
    # neither of them reviews it — nor its reversal
    for uid, action, what in ((e.prepared_by_id, "ap_estimate_created", "prepared"), (e.approved_by_id, "ap_estimate_approved", "approved")):
        u = db.get(User, uid) if uid else None
        if u is not None:
            log(db, action, actor=u, target=f"ap_item:{it.id}", detail=f"{f['invoice_no']}: estimate {what} by {_who(u)}")
    return c


def true_up(db: Session, c: ApCapture, h: ApBillHistory, it: Optional[ApItem] = None) -> Optional[ApEstimate]:
    """The real bill for an estimated account and period (Tess's hook, once per bill)."""
    if BH.is_estimate(c) or not h.period:
        return None
    e = (db.query(ApEstimate).filter(ApEstimate.account_key == h.account_key, ApEstimate.period == h.period, ApEstimate.status == "approved",
                                     ApEstimate.actual_capture_id.is_(None)).order_by(ApEstimate.id).first())
    if e is None:
        return None
    it = it or db.get(ApItem, c.item_id)
    ec = db.get(ApCapture, e.capture_id) if e.capture_id else None
    eit = db.get(ApItem, ec.item_id) if ec is not None else None
    diff = round((h.total_excl or 0) - (e.value or 0), 2)
    said = f"the real bill ({it.name if it else c.id}) is R {(h.total_excl or 0):,.2f} excl. VAT; the estimate was R {(e.value or 0):,.2f}: difference R {diff:,.2f}"
    rc, mode = None, "cancelled"
    if ec is not None and ec.uploaded_at:  # it went out: a credit memo reverses it (made first: a failure here changes nothing)
        mode = str(PF.setting(db, e.portfolio or None, "estimate_true_up", "reverse") or "reverse")
        mode = mode if mode in ("reverse", "adjust") else "reverse"
        nxt = BH.shift(_today().strftime("%Y-%m"), 1) if mode == "adjust" else _today().strftime("%Y-%m")
        rc = _document(db, None, e, reverse=True, suffix="-R" if mode == "reverse" else "-ADJ", period=nxt)
    e.status, e.actual_capture_id, e.actual_value, e.trued_up_at, e.difference, e.true_up = "trued_up", c.id, h.total_excl, utcnow(), diff, mode
    if rc is None:
        if ec is not None:
            ec.stage, ec.bucket, ec.sap_status = "skipped", "skipped", ""
        _item_line(eit, "Tess", f"Not posted: {said}.")
        _line(e, "Tess", f"Trued up before it went out: {said}. The estimate is not posted.")
    else:
        e.reversal_capture_id = rc.id
        _item_line(eit, "Tess", f"Trued up: {said}. Reversed by {reference(e)}{'-R' if mode == 'reverse' else '-ADJ'} ({'now' if mode == 'reverse' else 'in the next cycle'}).")
        _line(e, "Tess", f"Trued up: {said}. Credit memo {reference(e)}{'-R' if mode == 'reverse' else '-ADJ'} in the folder \"{db.get(ApBatch, rc.batch_id).name}\".")
        from . import capture as C

        after_commit(db, J.submit, C.advance_batch, rc.batch_id)
    _item_line(it, "Tess", f"Trues up estimate {reference(e)}: {said}.")
    log(db, "ap_estimate_trued_up", actor_label="AP Tool", target=f"ap_estimate:{e.id}", detail=f"{reference(e)} ({e.true_up}): {said}"[:400])
    return e


# ---- running estimates → Council Liaison -------------------------------------------------------------------------------------
def escalations(db: Session, portfolio: str = "") -> list[dict]:
    """Accounts with `estimate_escalate_n` or more estimates in consecutive periods and no real bill since the first."""
    by: dict[str, list[ApEstimate]] = {}
    for e in db.query(ApEstimate).filter(ApEstimate.status.in_(OPEN + ("trued_up",))).all():
        by.setdefault(e.account_key, []).append(e)
    out = []
    for key, es in by.items():
        es.sort(key=lambda e: e.period)
        run = [es[-1]]
        for e in reversed(es[:-1]):
            if BH.shift(e.period, 1) == run[-1].period:
                run.append(e)
            else:
                break
        run.reverse()
        port = run[-1].portfolio or ""
        if portfolio and port != portfolio:
            continue
        n = int(float(PF.setting(db, port or None, "estimate_escalate_n", 3) or 3))
        bills_since = db.query(ApBillHistory.id).filter(ApBillHistory.account_key == key, ApBillHistory.period >= run[0].period).count()
        if len(run) < n or bills_since:
            continue
        done = next((e for e in reversed(run) if e.escalated_at), None)
        out.append({"account_key": key, "account": run[-1].account, "site": run[-1].site, "municipality": run[-1].municipality, "portfolio": port,
                    "periods": [e.period for e in run], "count": len(run), "value": round(sum(e.value or 0 for e in run), 2), "threshold": n,
                    "escalated_by": done.escalated_by if done else "", "escalated_at": done.escalated_at.isoformat() if done and done.escalated_at else ""})
    out.sort(key=lambda r: (-r["count"], r["account_key"]))
    return out


def escalate(db: Session, user: User, key: str, note: str = "") -> dict:
    key = BH.acct_key(key) or key
    row = next((r for r in escalations(db) if r["account_key"] == key), None)
    if row is None:
        raise Refused("This account has no run of estimates to escalate.")
    R.require(db, user, "capturer", row["portfolio"] or None, what="Escalating to Council Liaison")
    e = (db.query(ApEstimate).filter(ApEstimate.account_key == key, ApEstimate.period == row["periods"][-1], ApEstimate.status.in_(OPEN + ("trued_up",)))
         .order_by(ApEstimate.id.desc()).first())
    e.escalated_by, e.escalated_at = _who(user), utcnow()
    _line(e, _who(user), f"Escalated to Council Liaison: {row['count']} estimates running ({', '.join(row['periods'])})" + (f" — {note[:200]}" if note else ""))
    log(db, "ap_estimate_escalated", actor=user, target=f"ap_estimate:{e.id}", detail=f"account {key}: {row['count']} estimates running" + (f" — {note[:160]}" if note else ""))
    return {**row, "escalated_by": e.escalated_by, "escalated_at": e.escalated_at.isoformat()}


# ---- the register and the monthly report -------------------------------------------------------------------------------------
def register(db: Session, status: str = "", portfolio: str = "", limit: int = 500) -> list[dict]:
    q = db.query(ApEstimate)
    if status == "open":
        q = q.filter(ApEstimate.status.in_(OPEN))
    elif status:
        q = q.filter(ApEstimate.status == status)
    if portfolio:
        q = q.filter(ApEstimate.portfolio == portfolio)
    return [out(e) for e in q.order_by(ApEstimate.id.desc()).limit(limit).all()]


def waiting_count(db: Session) -> int:
    return db.query(ApEstimate.id).filter(ApEstimate.status == "prepared").count()


def report_rows(db: Session, month: str = "") -> tuple[list[dict], list[dict]]:
    """Open estimates (approved, not trued up) as at the end of `month` (yyyy-mm; "" = this month): per portfolio, and the list."""
    month = month if _period_ok(month) else _today().strftime("%Y-%m")
    es = [e for e in db.query(ApEstimate).filter(ApEstimate.status.in_(("approved", "trued_up"))).all()
          if e.period <= month and (e.status == "approved" or (e.trued_up_at is not None and e.trued_up_at.strftime("%Y-%m") > month))]
    per: dict[str, dict] = {}
    for e in es:
        p = per.setdefault(e.portfolio or "", {"portfolio": e.portfolio or "", "count": 0, "value": 0.0, "oldest": e.period})
        p["count"] += 1
        p["value"] = round(p["value"] + (e.value or 0), 2)
        p["oldest"] = min(p["oldest"], e.period)
    return sorted(per.values(), key=lambda r: r["portfolio"]), [out(e) for e in sorted(es, key=lambda e: (e.portfolio, e.period, e.account_key))]


def report_xlsx(db: Session, month: str = "") -> bytes:
    import openpyxl

    from .. import xlsafe

    month = month if _period_ok(month) else _today().strftime("%Y-%m")
    per, rows = report_rows(db, month)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Per portfolio"
    ws.append([f"Open estimates at the end of {month}"])
    ws.append(["Portfolio", "Open estimates", "Value (R excl. VAT)", "Oldest period"])
    for p in per:
        ws.append([p["portfolio"], p["count"], p["value"], p["oldest"]])
    ws2 = wb.create_sheet("Estimates")
    head = [("reference", "Reference"), ("portfolio", "Portfolio"), ("municipality", "Municipality"), ("account", "Account"), ("site", "Site"), ("period", "Period"),
            ("basis_words", "Basis"), ("value", "Value (R excl. VAT)"), ("vat", "VAT"), ("kwh", "kWh"), ("status", "Status"), ("prepared_by", "Prepared by"),
            ("approved_by", "Approved by"), ("approved_at", "Approved"), ("actual_value", "Actual (R excl. VAT)"), ("difference", "Difference")]
    ws2.append([lbl for _k, lbl in head])
    for r in rows:
        ws2.append([r.get(k) for k, _l in head])
    for w in (ws, ws2):
        for col in w.columns:
            w.column_dimensions[col[0].column_letter].width = min(44, max(8, *(len(str(c.value or "")) for c in col[:300])) + 2)
    xlsafe.book(wb)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
