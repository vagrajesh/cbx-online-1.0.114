# 1.0.104: new (Builder C) — V11 reconciliation population, roll-forward exceptions, register (VALUENCE-AP-GAPS.md §2 V11)
"""Supplier and municipal account reconciliations across a defined, risk-based population — by exception, not by hand.

    Population (`build`): each month, per portfolio, from the rules in the portfolio's settings (`recon_rules`):
        top_balances     the top N closing balances (recon_top_n)
        credit_balances  accounts in credit (the municipality owes us)
        unsupported_bf   a brought-forward balance ≥ recon_bf_min_r with no earlier bill in the history to support it
        disputes         an open dispute (Builder U's ap_disputes)
        suspended        a suspended / disconnected account
        estimates        on estimate this month (an estimate in ap_estimates, or a bill read as estimated)
        manual_payments  a manual or non-Portal payment (recon_manual_payment_types, default KZ) in the month
        roll_forward     the municipal roll-forward does not agree (below)
    Municipal roll-forward: the account's bills (Builder U's `bill_history.periods`) and SAP's payments (`ap_payments`):
    last closing − payments since that bill ≠ this bill's opening (beyond recon_tolerance_r) → an exception. Only the
    exceptions need a person: management's "exception reporting from retained billing history".

    Register: `ap_recons` (account or vendor, month, statement and ledger balance, difference, preparer, reviewer, status
    open → prepared → reviewed, or returned to the preparer) and `ap_recon_items` (credit to recover · possible duplicate ·
    missing invoice · unallocated payment · roll-forward difference · other, with an owner and a target date until cleared).
    The rules raise items themselves (once each); people add, re-own and clear them.

    Separation: the reviewer holds the reconciliation-reviewer role in the portfolio and is not the preparer (admins get no
    exemption). While a portfolio has no AP role rows at all (bootstrap), a reviewer counts as a reconciliation reviewer.
    The preparer must also not release the account's payments — CBX holds no release data yet (V15), so that check waits
    for it.

    Audited ap_recon_prepared, ap_recon_reviewed (reviewed or returned), ap_recon_item (added, changed, cleared); a
    history line on the reconciliation for each. Needs you shows only overdue items (`needs_lines`)."""
from __future__ import annotations

import copy
import io
import re
from datetime import date, datetime, timedelta
from typing import Any, Optional

from sqlalchemy.orm import Session

from .. import knowledge as K
from ..audit import log
from ..db import utcnow
from ..models import User
from ..models.ap import ApBillHistory, ApDispute, ApEstimate, ApPayment, ApRecon, ApReconItem, ApSapVendor, ApUtilityAccount
from . import completeness as CP
from . import portfolio as PF

RULES = {"top_balances": "Top balance", "credit_balances": "Credit balance", "unsupported_bf": "Brought-forward balance without support",
         "disputes": "Open dispute", "suspended": "Suspended or disconnected", "estimates": "On estimate", "manual_payments": "Manual payment",
         "roll_forward": "Roll-forward does not agree", "duplicate_bills": "More than one bill for the month", "person": "Added by a person"}
ITEM_TYPES = {"credit": "Credit to recover", "duplicate": "Possible duplicate", "missing_invoice": "Missing invoice", "unallocated_payment": "Unallocated payment",
              "roll_forward": "Roll-forward difference", "other": "Other"}
STATUSES = {"open": "Open", "prepared": "Prepared", "reviewed": "Reviewed", "returned": "Returned"}
AGES = ((0, 30, "0–30"), (31, 60, "31–60"), (61, 90, "61–90"), (91, 10 ** 6, "90+"))

# 1.0.104: this module's portfolio settings (Settings → Portfolios lists them with these words)
SETTINGS = {
    "recon_rules": (list(k for k in RULES if k != "person"), "Reconciliation population rules",
                    "One per line: top_balances, credit_balances, unsupported_bf, disputes, suspended, estimates, manual_payments, roll_forward, duplicate_bills."),
    "recon_top_n": (20, "Top balances to reconcile", "The N accounts with the largest balances join the population each month."),
    "recon_bf_min_r": (1000, "Brought-forward balance from (R)", "A balance brought forward at least this large with no earlier bill to support it."),
    "recon_tolerance_r": (1.0, "Roll-forward tolerance (R)", "Last closing − payments may differ from the opening by this much."),
    "recon_manual_payment_types": (["KZ"], "Manual payment document types", "SAP document types of manual (non-Portal) payments, one per line."),
    "recon_item_days": (30, "Days to clear an item", "A reconciling item's target date: this many days after it is raised."),
}
for _k, (_d, _l, _h) in SETTINGS.items():
    PF._SEEN.setdefault(_k, copy.deepcopy(_d))
    PF.DESCRIBE.setdefault(_k, (_l, _h, ()))


class Refused(Exception):
    """A plain-words refusal (400)."""


def _who(u: Optional[User]) -> str:
    return (u.name or u.email) if u else ""


def _r(v: Optional[float]) -> str:
    return f"R {v:,.2f}".replace(",", " ") if v is not None else "R ?"


def _hist(r: ApRecon, who: str, text: str) -> None:
    r.history = list(r.history or []) + [{"at": utcnow().isoformat(timespec="seconds"), "who": who, "text": text[:400]}]


def _s(db: Session, p, key: str) -> Any:
    return PF.setting(db, p, key, copy.deepcopy(SETTINGS[key][0]))


# ---- roles -------------------------------------------------------------------------------------------------------------------
def may_prepare(db: Session, user: Optional[User], portfolio: str) -> bool:
    from . import roles as R

    return bool(user is not None and (R.may(db, user, "capturer", portfolio) or R.may(db, user, "reviewer", portfolio)
                                      or R.may(db, user, "recon_reviewer", portfolio)))


def may_review(db: Session, user: Optional[User], portfolio: str) -> bool:
    """The reconciliation-reviewer role in the portfolio; in bootstrap (no AP role rows for it yet) a reviewer counts."""
    from . import roles as R

    if R.may(db, user, "recon_reviewer", portfolio):
        return True
    return not R.has_rows(db, portfolio) and R.may(db, user, "reviewer", portfolio)


def may_build(db: Session, user: Optional[User], portfolio: str) -> bool:
    return bool(user is not None and (user.is_admin or may_review(db, user, portfolio) or CP.may_manage(db, user, portfolio)))


def _forbid(text: str):
    from . import roles as R

    return R.Forbidden(text)


# ---- the municipal roll-forward ------------------------------------------------------------------------------------------------
def _day_of(b: dict) -> str:
    return b.get("bill_date") or b.get("read_end") or (f"{b['period']}-28" if b.get("period") else "")


def roll_forward(db: Session, account_key: str, period: str, tolerance: float = 1.0, bills: Optional[list] = None) -> dict:
    """The roll-forward of the account's bill for the month: {state, prev, cur, paid, expected, opening, difference, text}.
    state: ok · exception · no_bill · no_prev · no_balances (the profile reads no opening / closing) · no_payments (no
    SAP payments loaded for those months: nothing can be judged)."""
    from . import bill_history as BH
    from . import payments as PAY

    bills = bills if bills is not None else BH.periods(db, account_key)
    cur = [b for b in bills if b.get("period") == period]
    if not cur:
        return {"state": "no_bill"}
    c = cur[-1]
    prev = [b for b in bills if (b.get("period") or "") < period]
    if not prev:
        return {"state": "no_prev", "cur": c.get("invoice_no") or period}
    p = prev[-1]
    if c.get("opening") is None or p.get("closing") is None:
        return {"state": "no_balances", "cur": c.get("invoice_no") or period, "prev": p.get("invoice_no") or p.get("period")}
    after, upto = _day_of(p), _day_of(c)
    if not PAY.loaded(db, (after or p["period"])[:7], (upto or period)[:7]):
        return {"state": "no_payments", "prev_closing": p["closing"], "opening": c["opening"]}
    paid = PAY.paid(db, account_key, after, upto)
    expected = round(float(p["closing"]) - paid, 2)
    diff = round(float(c["opening"]) - expected, 2)
    out = {"state": "exception" if abs(diff) > tolerance else "ok", "prev": p.get("invoice_no") or p.get("period"), "prev_period": p.get("period"),
           "prev_closing": p["closing"], "paid": paid, "after": after, "upto": upto, "expected": expected, "opening": c["opening"], "difference": diff,
           "cur": c.get("invoice_no") or period}
    out["text"] = (f"last closing {_r(p['closing'])} − paid {_r(paid)} = {_r(expected)}, but this bill opens at {_r(c['opening'])} "
                   f"(difference {_r(diff)})")
    return out


# ---- building the month's population ---------------------------------------------------------------------------------------------
def _latest(bills: list[dict], period: str) -> Optional[dict]:
    upto = [b for b in bills if (b.get("period") or "") <= period]
    return upto[-1] if upto else None


def build(db: Session, user: Optional[User], portfolio: str, period: str) -> dict:
    """The month's population from the portfolio's rules: reconciliations added (or their reasons brought up to date) and
    the rules' items raised once each. Safe to run again."""
    from . import bill_history as BH

    p = PF.get(db, portfolio)
    if p is None:
        raise Refused(f"{portfolio or '(none)'} is not a portfolio.")
    period = CP.period_ok(period)
    if user is not None and not may_build(db, user, p.code):
        raise _forbid(f"Building the reconciliation population needs the reviewer or reconciliation-reviewer role in {p.code} (or an admin).")
    rules = set(_s(db, p, "recon_rules") or [])
    top_n = int(_s(db, p, "recon_top_n") or 0)
    bf_min = float(_s(db, p, "recon_bf_min_r") or 0)
    tol = float(_s(db, p, "recon_tolerance_r") or 0)
    manual = {str(t).strip().upper() for t in (_s(db, p, "recon_manual_payment_types") or []) if str(t).strip()}
    month_start = f"{period}-01"
    accts = {a.account_key: a for a in CP.accounts(db, p.code) if a.account_key and not CP._closed_before(a, month_start)}
    keys = sorted(accts)
    # the accounts with any bill up to the month (their balances), and their bills
    with_bills: set[str] = set()
    for ch in CP._chunks(keys):
        with_bills |= {k for (k,) in db.query(ApBillHistory.account_key).filter(ApBillHistory.account_key.in_(ch), ApBillHistory.period <= period).distinct().all()}
    bills = {k: BH.periods(db, k) for k in sorted(with_bills)}
    picked: dict[tuple[str, str], dict] = {}

    def pick(key: str, vendor: str, rule: str, text: str, **extra) -> None:
        u = picked.setdefault((key, vendor), {"why": [], "items": [], "roll": {}})
        if not any(w["rule"] == rule for w in u["why"]):
            u["why"].append({"rule": rule, "text": text[:300]})
        u.update({k: v for k, v in extra.items() if k != "items"})
        u["items"] += extra.get("items", [])

    latest = {k: _latest(bs, period) for k, bs in bills.items()}
    if "top_balances" in rules and top_n > 0:
        ranked = sorted(((abs(b["closing"]), k) for k, b in latest.items() if b and b.get("closing") is not None), reverse=True)[:top_n]
        for _v, k in ranked:
            pick(k, accts[k].vendor, "top_balances", f"balance {_r(latest[k]['closing'])} ({latest[k].get('period')})")
    for k, b in latest.items():
        if not b:
            continue
        if "credit_balances" in rules and b.get("closing") is not None and b["closing"] < -0.005:
            pick(k, accts[k].vendor, "credit_balances", f"in credit {_r(b['closing'])} ({b.get('period')})",
                 items=[{"key": f"credit:{period}", "type": "credit", "amount": round(-b["closing"], 2), "text": f"credit balance of {_r(-b['closing'])} on the {b.get('period')} bill"}])
        cur = [x for x in bills[k] if x.get("period") == period]
        if "unsupported_bf" in rules and cur:
            bf = cur[-1].get("opening") if cur[-1].get("opening") is not None else cur[-1].get("arrears")
            if bf is not None and abs(bf) >= bf_min and not any((x.get("period") or "") < period for x in bills[k]):
                pick(k, accts[k].vendor, "unsupported_bf", f"brought forward {_r(bf)} with no earlier bill in the history")
        if "estimates" in rules and any(x.get("reading_type") == "E" for x in cur):
            pick(k, accts[k].vendor, "estimates", "the bill was read as estimated")
        if "duplicate_bills" in rules and len({x.get("capture_id") for x in cur}) > 1:
            nos = ", ".join(sorted({str(x.get("invoice_no") or x.get("capture_id")) for x in cur}))
            pick(k, accts[k].vendor, "duplicate_bills", f"{len(cur)} bills for {period}",
                 items=[{"key": f"dup:{period}", "type": "duplicate", "amount": cur[-1].get("total_excl"), "text": f"{len(cur)} bills for {period}: {nos}"}])
        if "roll_forward" in rules and cur:
            rf = roll_forward(db, k, period, tol, bills[k])
            if rf["state"] == "exception":
                pick(k, accts[k].vendor, "roll_forward", rf["text"], roll=rf,
                     items=[{"key": f"roll:{period}", "type": "roll_forward", "amount": rf["difference"], "text": "roll-forward: " + rf["text"]}])
    if "suspended" in rules:
        for k, a in accts.items():
            if CP.status_of(a) == "suspended":
                pick(k, a.vendor, "suspended", f"suspended{' since ' + a.status_from if a.status_from else ''}")
    if "disputes" in rules and keys:
        for ch in CP._chunks(keys):
            for d in db.query(ApDispute).filter(ApDispute.account_key.in_(ch), ApDispute.status == "open").all():
                pick(d.account_key, accts[d.account_key].vendor, "disputes", f"dispute open since {d.created_at:%d.%m.%Y}" if d.created_at else "dispute open")
    if "estimates" in rules and keys:
        for ch in CP._chunks(keys):
            for e in db.query(ApEstimate).filter(ApEstimate.period == period, ApEstimate.account_key.in_(ch), ApEstimate.status.in_(("prepared", "approved"))).all():
                pick(e.account_key, accts[e.account_key].vendor, "estimates", f"estimated {_r(e.value)} ({e.status})")
    if "manual_payments" in rules and manual:
        pcos = {c for c in _company_codes(db, p)}
        for pay in db.query(ApPayment).filter(ApPayment.posting_date.like(f"{period}-%"), ApPayment.doc_type.in_(sorted(manual))).all():
            al = {k: v for k, v in (pay.allocations or {}).items() if k in accts}
            if al:
                for k, v in al.items():
                    pick(k, accts[k].vendor, "manual_payments", f"{pay.doc_type} payment {pay.doc_number} of {_r(v)} on {pay.posting_date}")
            elif not pay.allocations and pay.company_code in pcos:
                pick("", pay.vendor, "manual_payments", f"{pay.doc_type} payment {pay.doc_number} of {_r(pay.amount)} on {pay.posting_date}", company_code=pay.company_code,
                     items=[{"key": f"pay:{pay.company_code}:{pay.doc_number}", "type": "unallocated_payment", "amount": pay.amount,
                             "text": f"payment {pay.doc_number} of {_r(pay.amount)} on {pay.posting_date} is tied to no account or invoice"}])
    # a missing bill this month for an account in the population: a missing-invoice item
    for (k, _v) in list(picked):
        if k and not any(x.get("period") == period for x in bills.get(k, [])) and any((x.get("period") or "") < period for x in bills.get(k, [])):
            picked[(k, _v)]["items"].append({"key": f"missing:{period}", "type": "missing_invoice", "amount": None, "text": f"no bill read for {period}"})
    return _save(db, user, p, period, accts, latest, picked)


def _company_codes(db: Session, p) -> list[str]:
    """The company codes the portfolio claims (a range opened up), for vendor-level payments."""
    out = []
    for t in PF.codes_of(p):
        if "-" in t:
            lo, hi = t.split("-", 1)
            m1, m2 = re.fullmatch(r"([A-Z]*)(\d+)", lo), re.fullmatch(r"([A-Z]*)(\d+)", hi)
            if m1 and m2:
                w = len(m1.group(2))
                out += [f"{m1.group(1)}{i:0{w}d}" for i in range(int(m1.group(2)), int(m2.group(2)) + 1)]
        else:
            out.append(t)
    if p.is_default:
        out.append("")
    return out


def _save(db: Session, user: Optional[User], p, period: str, accts: dict, latest: dict, picked: dict) -> dict:
    who = _who(user) or "CBX"
    days = int(_s(db, p, "recon_item_days") or 30)
    target = (date.today() + timedelta(days=days)).isoformat()
    have = {(r.account_key, r.vendor): r for r in db.query(ApRecon).filter(ApRecon.portfolio == p.code, ApRecon.period == period).all()}
    added = updated = items = 0
    for (k, v), u in picked.items():
        r = have.get((k, v))
        a = accts.get(k)
        if r is None:
            name = f"{a.site} · {a.municipality}" if a is not None else (_vendor_name(db, v) or v)
            r = ApRecon(portfolio=p.code, period=period, account_key=k, account=a.account if a is not None else "", vendor=v or "",
                        company_code=(a.company_code if a is not None else u.get("company_code", "")) or "", name=name[:200], why=[], roll={}, status="open", history=[])
            b = latest.get(k)
            if b and b.get("closing") is not None:
                r.statement_balance = b["closing"]
            db.add(r)
            db.flush()
            _hist(r, who, "In the population: " + "; ".join(w["text"] for w in u["why"]))
            added += 1
        else:
            old = {w["rule"] for w in (r.why or [])}
            new = [w for w in u["why"] if w["rule"] not in old]
            if new:
                _hist(r, who, "Also: " + "; ".join(w["text"] for w in new))
                updated += 1
        r.why = list(r.why or []) + [w for w in u["why"] if w["rule"] not in {x["rule"] for x in (r.why or [])}]
        if u.get("roll"):
            r.roll = u["roll"]
        keys = {x for (x,) in db.query(ApReconItem.key).filter(ApReconItem.recon_id == r.id, ApReconItem.key != "").all()}
        for it in u["items"]:
            if it["key"] in keys:
                continue
            keys.add(it["key"])
            db.add(ApReconItem(recon_id=r.id, type=it["type"], amount=it.get("amount"), text=it["text"][:400], key=it["key"][:80], target_date=target, created_by="CBX (the rules)"))
            items += 1
            if r.status in ("reviewed", "prepared"):  # a new exception reopens a reviewed (or prepared) reconciliation
                r.status = "open"
                _hist(r, "CBX", f"Opened again: {ITEM_TYPES[it['type']].lower()} raised")
    db.flush()
    log(db, "ap_recon_population", actor=user, actor_label="" if user else "system", target=f"ap_portfolio:{p.code}",
        detail=f"{period}: {len(picked)} in the population ({added} new, {updated} with new reasons), {items} items raised")
    return {"portfolio": p.code, "period": period, "population": len(picked), "added": added, "updated": updated, "items": items}


def _vendor_name(db: Session, vendor: str) -> str:
    vs = sorted(K.vendor_variants(vendor))
    r = db.query(ApSapVendor.name).filter(ApSapVendor.vendor.in_(vs or ["-"])).first()
    return r[0] if r else ""


def add(db: Session, user: User, portfolio: str, period: str, account: str = "", vendor: str = "", why: str = "") -> ApRecon:
    """A person adds an account (or a vendor) to the month's population."""
    p = PF.get(db, portfolio)
    if p is None:
        raise Refused(f"{portfolio or '(none)'} is not a portfolio.")
    period = CP.period_ok(period)
    if not may_prepare(db, user, p.code):
        raise _forbid(f"Adding to the reconciliations needs an AP role in {p.code}.")
    k = CP._acct(account) if account else ""
    a = db.query(ApUtilityAccount).filter(ApUtilityAccount.account_key == k).first() if k else None
    if k and a is None:
        raise Refused(f"Account {account} is not in the utilities list.")
    v = re.sub(r"\s+", "", str(vendor or (a.vendor if a is not None else "")))[:40]
    if not k and not v:
        raise Refused("Give an account or a vendor.")
    _save(db, user, p, period, {k: a} if a is not None else {}, {}, {(k, v if not k else (a.vendor or "")): {
        "why": [{"rule": "person", "text": (why or f"added by {_who(user)}")[:300]}], "items": [], "roll": {}, "company_code": ""}})
    r = db.query(ApRecon).filter(ApRecon.portfolio == p.code, ApRecon.period == period, ApRecon.account_key == k,
                                 ApRecon.vendor == (v if not k else (a.vendor or ""))).one()
    if k:
        rf = roll_forward(db, k, period, float(_s(db, p, "recon_tolerance_r") or 0))
        if rf.get("state") not in (None, "no_bill"):
            r.roll = rf
    return r


# ---- a person's work on one reconciliation ----------------------------------------------------------------------------------------
def _num(v: Any, what: str) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        f = float(str(v).replace(" ", "").replace(",", ""))
    except (TypeError, ValueError):
        raise Refused(f"{what}: give an amount.") from None
    if f != f or abs(f) == float("inf"):
        raise Refused(f"{what}: give an amount.")
    return round(f, 2)


def _get(db: Session, rid: int) -> ApRecon:
    r = db.get(ApRecon, int(rid or 0))
    if r is None:
        raise LookupError("No such reconciliation.")
    return r


def _editing(r: ApRecon, user: User) -> None:
    """1.0.104 (review finding): a person changes a reconciliation's balances or items. Refused while it is prepared (it
    waits for review: the reviewer sends it back first); whoever edits becomes its preparer and joins its editors — the
    reviewer is never one of them."""
    if r.status == "prepared":
        raise Refused("It waits for review: send it back to the preparer first.")
    r.preparer, r.preparer_id = _who(user), user.id
    eds = [int(x) for x in (r.editors or [])]
    if user.id not in eds:
        r.editors = eds + [user.id]


def editors_of(r: ApRecon) -> set[int]:
    return {int(x) for x in (r.editors or [])} | ({r.preparer_id} if r.preparer_id else set())


def prepare(db: Session, user: User, rid: int, data: dict) -> ApRecon:
    """The preparer's balances and note; "prepared" signs it for review. Audited ap_recon_prepared."""
    r = _get(db, rid)
    if not may_prepare(db, user, r.portfolio):
        raise _forbid(f"Preparing a reconciliation needs an AP role in {r.portfolio}.")
    if r.status == "reviewed":
        raise Refused("It is reviewed: it opens again only when a new item is raised.")
    _editing(r, user)  # 1.0.104 (review finding)
    data = data or {}
    if "statement_balance" in data:
        r.statement_balance = _num(data.get("statement_balance"), "Statement balance")
    if "ledger_balance" in data:
        r.ledger_balance = _num(data.get("ledger_balance"), "Ledger balance")
    if "note" in data:
        r.note = str(data.get("note") or "").strip()[:4000]
    r.difference = round(r.statement_balance - r.ledger_balance, 2) if r.statement_balance is not None and r.ledger_balance is not None else None
    if data.get("sign"):
        if r.statement_balance is None or r.ledger_balance is None:
            raise Refused("Give the statement balance and the ledger balance first.")
        opened = db.query(ApReconItem).filter(ApReconItem.recon_id == r.id, ApReconItem.cleared.is_(False)).all()
        explained = round(sum(i.amount or 0 for i in opened), 2)
        if abs((r.difference or 0) - explained) > 0.01 and not str(data.get("note") or r.note or "").strip():
            raise Refused(f"The difference {_r(r.difference)} is not explained by the open items ({_r(explained)}): add items or say why in the note.")
        r.status, r.preparer, r.preparer_id, r.prepared_at = "prepared", _who(user), user.id, utcnow()
        r.reviewer, r.reviewer_id, r.reviewed_at = "", None, None
        _hist(r, _who(user), f"Prepared: statement {_r(r.statement_balance)}, ledger {_r(r.ledger_balance)}, difference {_r(r.difference)}; {len(opened)} open items")
        log(db, "ap_recon_prepared", actor=user, target=f"ap_recon:{r.id}",
            detail=f"{r.portfolio} {r.period} {r.account or r.vendor} {r.name}: statement {_r(r.statement_balance)}, ledger {_r(r.ledger_balance)}, "
                   f"difference {_r(r.difference)}, {len(opened)} open items"[:900])
    else:
        _hist(r, _who(user), f"Balances: statement {_r(r.statement_balance)}, ledger {_r(r.ledger_balance)}")
    db.flush()
    return r


def review(db: Session, user: User, rid: int, outcome: str, note: str = "") -> ApRecon:
    """The reconciliation reviewer (not the preparer, nor anyone who changed its balances or items) reviews it; "returned"
    is `send_back`. Audited ap_recon_reviewed."""
    if outcome in ("returned", "send_back"):
        return send_back(db, user, rid, note)
    r = _get(db, rid)
    if not may_review(db, user, r.portfolio):
        raise _forbid(f"Reviewing a reconciliation needs the reconciliation-reviewer role in {r.portfolio}. An admin gives it in AP Settings → AP people.")
    if r.status != "prepared":
        raise Refused("Only a prepared reconciliation is reviewed.")
    if user.id in editors_of(r):  # 1.0.104 (review finding): every editor, not only the last preparer
        raise _forbid("You prepared or changed this reconciliation, so a different person reviews it.")
    note = str(note or "").strip()[:2000]
    if outcome != "reviewed":
        raise Refused("Reviewed, or sent back to the preparer.")
    r.status, r.reviewer, r.reviewer_id, r.reviewed_at = outcome, _who(user), user.id, utcnow()
    _hist(r, _who(user), "Reviewed" + (f": {note}" if note else ""))
    db.flush()
    log(db, "ap_recon_reviewed", actor=user, target=f"ap_recon:{r.id}", outcome="ok",
        detail=f"{r.portfolio} {r.period} {r.account or r.vendor}: reviewed (prepared by {r.preparer}){': ' + note if note else ''}"[:900])
    return r


def send_back(db: Session, user: User, rid: int, note: str) -> ApRecon:
    """1.0.104 (review finding): the reviewer sends a prepared reconciliation back to the preparer with a note — it is open
    again and can be changed. Audited ap_recon_sent_back."""
    r = _get(db, rid)
    if not may_review(db, user, r.portfolio):
        raise _forbid(f"Sending a reconciliation back needs the reconciliation-reviewer role in {r.portfolio}.")
    if r.status != "prepared":
        raise Refused("Only a prepared reconciliation is sent back.")
    note = str(note or "").strip()[:2000]
    if len(note) < 3:
        raise Refused("Say what the preparer should look at.")
    r.status, r.reviewer, r.reviewer_id, r.reviewed_at = "open", "", None, None
    _hist(r, _who(user), f"Sent back to the preparer: {note}")
    db.flush()
    log(db, "ap_recon_sent_back", actor=user, target=f"ap_recon:{r.id}", outcome="returned",
        detail=f"{r.portfolio} {r.period} {r.account or r.vendor}: sent back to {r.preparer or 'the preparer'}: {note}"[:900])
    return r


def _owner(db: Session, owner_id: Any) -> tuple[Optional[int], str]:
    if owner_id in (None, "", 0):
        return None, ""
    u = db.get(User, int(owner_id))
    if u is None or u.is_axl or not u.is_active:
        raise Refused("No such person.")
    return u.id, _who(u)


def item_save(db: Session, user: User, rid: int, data: dict, iid: Optional[int] = None) -> ApReconItem:
    """A person adds an item, or changes its type, amount, owner or target date. Audited ap_recon_item."""
    r = _get(db, rid)
    if not may_prepare(db, user, r.portfolio):
        raise _forbid(f"Reconciling items need an AP role in {r.portfolio}.")
    data = data or {}
    it = None
    if iid:
        it = db.get(ApReconItem, int(iid))
        if it is None or it.recon_id != r.id:
            raise LookupError("No such item.")
        if it.cleared:
            raise Refused("The item is cleared.")
    _editing(r, user)  # 1.0.104 (review finding): refused while prepared; the editor becomes the preparer
    typ = str(data.get("type") or (it.type if it else "other"))
    if typ not in ITEM_TYPES:
        raise Refused(f"An item is one of: {', '.join(ITEM_TYPES.values())}.")
    tgt = str(data.get("target_date") or (it.target_date if it else "") or "").strip()[:10]
    if tgt:
        try:
            tgt = date.fromisoformat(tgt).isoformat()
        except ValueError:
            raise Refused("The target date as yyyy-mm-dd.") from None
    else:
        tgt = (date.today() + timedelta(days=int(_s(db, r.portfolio, "recon_item_days") or 30))).isoformat()
    said = []
    if it is None:
        text = re.sub(r"\s+", " ", str(data.get("text") or "")).strip()[:400]
        if len(text) < 3:
            raise Refused("Say what the item is.")
        oid, oname = _owner(db, data.get("owner_id")) if "owner_id" in data else (user.id, _who(user))
        it = ApReconItem(recon_id=r.id, type=typ, amount=_num(data.get("amount"), "Amount"), text=text, owner=oname, owner_id=oid, target_date=tgt,
                         created_by=_who(user))
        db.add(it)
        said.append(f"added {ITEM_TYPES[typ].lower()} {_r(it.amount) if it.amount is not None else ''}: {text}")
        if r.status in ("reviewed",):
            r.status = "open"
    else:
        if typ != it.type:
            said.append(f"type {ITEM_TYPES.get(it.type, it.type)} → {ITEM_TYPES[typ]}")
            it.type = typ
        if "amount" in data:
            a = _num(data.get("amount"), "Amount")
            if a != it.amount:
                said.append(f"amount {_r(it.amount)} → {_r(a)}")
                it.amount = a
        if "owner_id" in data:
            oid, oname = _owner(db, data.get("owner_id"))
            if oid != it.owner_id:
                said.append(f"owner {it.owner or '-'} → {oname or '-'}")
                it.owner, it.owner_id = oname, oid
        if tgt != it.target_date:
            said.append(f"target {it.target_date or '-'} → {tgt}")
            it.target_date = tgt
        if "text" in data and str(data.get("text") or "").strip() and str(data["text"]).strip()[:400] != it.text:
            it.text = re.sub(r"\s+", " ", str(data["text"])).strip()[:400]
            said.append("text")
    db.flush()
    if said:
        _hist(r, _who(user), "Item: " + "; ".join(said))
        log(db, "ap_recon_item", actor=user, target=f"ap_recon:{r.id}", detail=f"item {it.id}: " + "; ".join(said)[:800])
    return it


def item_clear(db: Session, user: User, rid: int, iid: int, note: str) -> ApReconItem:
    r = _get(db, rid)
    if not may_prepare(db, user, r.portfolio):
        raise _forbid(f"Clearing an item needs an AP role in {r.portfolio}.")
    it = db.get(ApReconItem, int(iid or 0))
    if it is None or it.recon_id != r.id:
        raise LookupError("No such item.")
    if it.cleared:
        raise Refused("It is cleared already.")
    _editing(r, user)  # 1.0.104 (review finding)
    note = re.sub(r"\s+", " ", str(note or "")).strip()[:1000]
    if len(note) < 3:
        raise Refused("Say how it was cleared (credit received, invoice captured, payment allocated …).")
    it.cleared, it.cleared_note, it.cleared_by, it.cleared_at = True, note, _who(user), utcnow()
    _hist(r, _who(user), f"Cleared {ITEM_TYPES.get(it.type, it.type).lower()} {_r(it.amount) if it.amount is not None else ''}: {note}")
    db.flush()
    log(db, "ap_recon_item", actor=user, target=f"ap_recon:{r.id}", detail=f"item {it.id} cleared ({ITEM_TYPES.get(it.type, it.type)}, {_r(it.amount)}): {note}"[:800])
    return it


# ---- what the screens show ----------------------------------------------------------------------------------------------------------
def _age(it: ApReconItem, today: date) -> int:
    return max(0, (today - (it.created_at.date() if it.created_at else today)).days)


def item_out(it: ApReconItem, today: Optional[date] = None) -> dict:
    today = today or date.today()
    return {"id": it.id, "recon_id": it.recon_id, "type": it.type, "label": ITEM_TYPES.get(it.type, it.type), "amount": it.amount, "text": it.text,
            "owner": it.owner, "owner_id": it.owner_id, "target_date": it.target_date, "cleared": bool(it.cleared), "cleared_note": it.cleared_note,
            "cleared_by": it.cleared_by, "cleared_at": it.cleared_at.isoformat(timespec="minutes") if it.cleared_at else "", "created_by": it.created_by,
            "at": it.created_at.isoformat(timespec="minutes") if it.created_at else "", "age_days": _age(it, today),
            "overdue": bool(not it.cleared and it.target_date and it.target_date < today.isoformat())}


def recon_out(r: ApRecon, items: Optional[list] = None) -> dict:
    items = items if items is not None else []
    open_ = [i for i in items if not i.cleared]
    today = date.today().isoformat()
    return {"id": r.id, "portfolio": r.portfolio, "period": r.period, "account": r.account, "account_key": r.account_key, "vendor": r.vendor,
            "company_code": r.company_code, "name": r.name, "why": list(r.why or []), "roll": dict(r.roll or {}), "statement_balance": r.statement_balance,
            "ledger_balance": r.ledger_balance, "difference": r.difference, "status": r.status, "status_label": STATUSES.get(r.status, r.status),
            "preparer": r.preparer, "preparer_id": r.preparer_id, "prepared_at": r.prepared_at.isoformat(timespec="minutes") if r.prepared_at else "",
            "editors": sorted(editors_of(r)), "reviewer": r.reviewer, "reviewed_at": r.reviewed_at.isoformat(timespec="minutes") if r.reviewed_at else "", "note": r.note,
            "items_open": len(open_), "items_overdue": sum(1 for i in open_ if i.target_date and i.target_date < today),
            "open_amount": round(sum(i.amount or 0 for i in open_), 2)}


def listing(db: Session, portfolio: str, period: str, status: str = "", overdue: bool = False, q: str = "") -> dict:
    period = CP.period_ok(period)
    rs = db.query(ApRecon).filter(ApRecon.portfolio == portfolio, ApRecon.period == period).order_by(ApRecon.id).all()
    items: dict[int, list] = {}
    if rs:
        for it in db.query(ApReconItem).filter(ApReconItem.recon_id.in_([r.id for r in rs])).all():
            items.setdefault(it.recon_id, []).append(it)
    rows = [recon_out(r, items.get(r.id, [])) for r in rs]
    summary = {"population": len(rows), **{s: sum(1 for x in rows if x["status"] == s) for s in STATUSES}}
    summary["complete_pct"] = round(100.0 * summary["reviewed"] / len(rows), 1) if rows else 0.0
    summary["items_open"] = sum(x["items_open"] for x in rows)
    summary["items_overdue"] = sum(x["items_overdue"] for x in rows)
    ql = str(q or "").strip().lower()[:60]
    shown = [x for x in rows if (not status or x["status"] == status) and (not overdue or x["items_overdue"])
             and (not ql or ql in f"{x['account']} {x['vendor']} {x['name']}".lower())]
    return {"portfolio": portfolio, "period": period, "summary": summary, "rows": shown, "statuses": STATUSES, "rules": RULES}


def detail(db: Session, rid: int) -> dict:
    from . import bill_history as BH
    from . import payments as PAY

    r = _get(db, rid)
    items = db.query(ApReconItem).filter(ApReconItem.recon_id == r.id).order_by(ApReconItem.cleared, ApReconItem.id).all()
    out = recon_out(r, items)
    out["items"] = [item_out(i) for i in items]
    out["history"] = list(r.history or [])
    out["bills"] = BH.periods(db, r.account_key)[-6:] if r.account_key else []
    if r.account_key:
        out["payments"] = [PAY.out(p) for p in db.query(ApPayment).filter(ApPayment.posting_date >= f"{CP.period_ok(r.period)[:4]}-01-01").order_by(ApPayment.posting_date).all()
                           if r.account_key in (p.allocations or {})][-12:]
    else:
        out["payments"] = [PAY.out(p) for p in PAY.vendor_payments(db, r.company_code, r.vendor, r.period)]
    out["item_types"] = ITEM_TYPES
    return out


# ---- the report -------------------------------------------------------------------------------------------------------------------
def report_data(db: Session, portfolio: str, period: str) -> dict:
    """Completion %, open items by age, credits recoverable — for the month's population."""
    v = listing(db, portfolio, period)
    ids = [r["id"] for r in v["rows"]]
    items = db.query(ApReconItem).filter(ApReconItem.recon_id.in_(ids or [0])).all()
    today = date.today()
    ages = {lab: {"items": 0, "amount": 0.0} for _lo, _hi, lab in AGES}
    for it in items:
        if it.cleared:
            continue
        a = _age(it, today)
        lab = next(lab for lo, hi, lab in AGES if lo <= a <= hi)
        ages[lab]["items"] += 1
        ages[lab]["amount"] = round(ages[lab]["amount"] + (it.amount or 0), 2)
    credits = round(sum(it.amount or 0 for it in items if it.type == "credit" and not it.cleared), 2)
    by_type = {t: sum(1 for it in items if it.type == t and not it.cleared) for t in ITEM_TYPES}
    return {**v["summary"], "ages": ages, "credits_recoverable": credits, "by_type": by_type, "rows": v["rows"], "items": [item_out(i, today) for i in items]}


def report(db: Session, portfolio: str, period: str) -> bytes:
    import openpyxl

    from .. import xlsafe

    d = report_data(db, portfolio, period)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Summary"
    for row in (["Portfolio", portfolio], ["Month", period], ["Made", datetime.now().strftime("%Y-%m-%d %H:%M")], [],
                ["In the population", d["population"]], ["Prepared", d["prepared"]], ["Reviewed", d["reviewed"]], ["Returned", d["returned"]], ["Open", d["open"]],
                ["Completion % (reviewed)", d["complete_pct"]], ["Credits recoverable (R)", d["credits_recoverable"]], [], ["Open items by age (days)", "Items", "Amount (R)"]):
        ws.append(row)
    for lab, x in d["ages"].items():
        ws.append([lab, x["items"], x["amount"]])
    ws.append([])
    ws.append(["Open items by type", "Items"])
    for t, n in d["by_type"].items():
        ws.append([ITEM_TYPES[t], n])
    reg = wb.create_sheet("Register")
    reg.append(["Account", "Vendor", "Company code", "Name", "Why", "Statement balance", "Ledger balance", "Difference", "Status", "Preparer", "Prepared",
                "Reviewer", "Reviewed", "Open items", "Overdue items", "Open amount"])
    for r in d["rows"]:
        reg.append([r["account"], r["vendor"], r["company_code"], r["name"], "; ".join(w["text"] for w in r["why"]), r["statement_balance"], r["ledger_balance"],
                    r["difference"], r["status_label"], r["preparer"], r["prepared_at"], r["reviewer"], r["reviewed_at"], r["items_open"], r["items_overdue"], r["open_amount"]])
    its = wb.create_sheet("Items")
    its.append(["Reconciliation", "Type", "Amount", "What", "Owner", "Target", "Age (days)", "Overdue", "Cleared", "Cleared by", "How"])
    names = {r["id"]: (r["account"] or r["vendor"]) for r in d["rows"]}
    for it in d["items"]:
        its.append([names.get(it["recon_id"], ""), it["label"], it["amount"], it["text"], it["owner"], it["target_date"], it["age_days"], "yes" if it["overdue"] else "",
                    it["cleared_at"], it["cleared_by"], it["cleared_note"]])
    xlsafe.book(wb)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---- Needs you: only overdue items -------------------------------------------------------------------------------------------------
def needs_lines(db: Session, user: Optional[User]) -> list[dict]:
    """One line when reconciling items this person owns (or prepared, unowned) are past their target date; admins see all."""
    if user is None or user.is_axl:
        return []
    today = date.today().isoformat()
    q = db.query(ApReconItem).filter(ApReconItem.cleared.is_(False), ApReconItem.target_date != "", ApReconItem.target_date < today)
    its = q.all()
    if not its:
        return []
    if not user.is_admin:
        prep = {rid for (rid,) in db.query(ApRecon.id).filter(ApRecon.preparer_id == user.id).all()}
        its = [i for i in its if i.owner_id == user.id or (i.owner_id is None and i.recon_id in prep)]
    if not its:
        return []
    n = len(its)
    return [{"key": "recons_overdue", "sub": "reconciliations", "params": {"overdue": "1"},
             "text": f"{n} reconciliation item{'s are' if n != 1 else ' is'} past the target date"}]
