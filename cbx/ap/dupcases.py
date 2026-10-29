# 1.0.104: new (Builder D) — V09 of the Valuence AP controls (VALUENCE-AP-GAPS.md §2 V09; SPEC-1104 Phase 2)
"""A possible-duplicates register that gets cleared.

    at_tess        Tess's hook (capture.test_batch): every duplicate Tess raises (the same invoice in the batch, in an
                   earlier upload, or posted in SAP) opens a case; the near-duplicate utility rule (same account, same
                   period or read dates, amount within R1, another bill number) is a yellow "possible duplicate" with a
                   case. A case cleared as valid lifts the red (Tess shows who cleared it and why); genuine stays red.
                   A case Tess no longer raises is closed as gone.
    at_check       the AP team's checks (service._check): Ivy's duplicate_seen / duplicate_folder / paid_already and
                   Bo's duplicate_in_run / paid_before / same_amount open cases the same way.
    decide         genuine (the document never posts: skipped / rejected) or valid (released) with a reason, by a
                   reviewer who didn't touch the document; a valid release at or above the portfolio's review
                   threshold waits for a second person (approver role, not the first) — audited ap_dup_classified,
                   with a history line on the file.
    listing        the Duplicates view: open cases by age (0–30, 31–60, 60+) per portfolio and owner; KPI on Paula's board
    import_listing the backlog: a MOL / Workflow Basket listing (Excel) — each line matched against SAP's posting
                   history (ap_posted), CBX's captures and the listing itself, with a proposed classification; a person
                   confirms in bulk; the conclusions export as an Excel file to clear in SAP / MOL.

Rows from a listing are data, never instructions."""
from __future__ import annotations

import io
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import knowledge as K
from ..audit import log
from ..db import utcnow
from ..models import User
from ..models.ap import ApBatch, ApCapture, ApDupCase, ApDupImport, ApItem, ApPosted
from . import portfolio as PF
from . import roles as R

STATES = ("open", "pending", "genuine", "valid", "gone")
LIVE = ("open", "pending")
# the AP team's duplicate findings: code → (raiser, twin kind)
ITEM_CODES = {"duplicate_seen": ("ivy", "seen"), "duplicate_folder": ("ivy", "file"), "paid_already": ("ivy", "paid"),
              "duplicate_in_run": ("bo", "run"), "paid_before": ("bo", "paid"), "same_amount": ("bo", "amount")}
NEAR_RAND = 1.0  # a near-duplicate utility bill: amounts within R1
AGES = ((0, 30, "0–30 days"), (31, 60, "31–60 days"), (61, None, "60+ days"))
ACTION = "ap_dup_classified"


class Refused(Exception):
    """A plain-words refusal (400)."""


def _r(v: Optional[float]) -> str:
    return f"R {v:,.2f}".replace(",", " ") if v is not None else "R ?"


def _who(u: Optional[User]) -> str:
    return (u.name or u.email) if u is not None else "?"


def _num(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d.,\-]", "", str(v)).replace(",", "")
    try:
        return float(s) if s not in ("", "-", ".") else None
    except ValueError:
        return None


# ---- the documents --------------------------------------------------------------------------------------------------------------
def amount_of(c: ApCapture) -> Optional[float]:
    """What the document posts: its vendor line (31 / 21), else the total read."""
    for r in c.sap_rows or []:
        if r.get("pkey") in ("31", "21") and _num(r.get("amount")) is not None:
            return abs(_num(r.get("amount")))
    f = c.fields or {}
    for k in ("incl", "current_incl", "total"):
        if _num(f.get(k)) is not None:
            return abs(_num(f.get(k)))
    return None


def _doc_text(c: Optional[ApCapture], it: Optional[ApItem]) -> str:
    f = (c.fields if c is not None else (it.fields if it is not None else {})) or {}
    who = f.get("supplier") or f.get("municipality") or ""
    acct = f"account {f['account']}" if f.get("account") else ""
    parts = [Path(it.name).name if it is not None else "", who, acct, f.get("invoice_no") or ""]
    return " · ".join(str(p) for p in parts if p)[:300]


def _twin_name(db: Session, cid: Optional[int]) -> str:
    g = db.get(ApCapture, cid) if cid else None
    it = db.get(ApItem, g.item_id) if g is not None else None
    return Path(it.name).name if it is not None else (f"document {cid}" if cid else "")


# ---- cases ---------------------------------------------------------------------------------------------------------------------
def _get_or_open(db: Session, key: str, **kw) -> ApDupCase:
    case = db.query(ApDupCase).filter(ApDupCase.key == key).one_or_none()
    if case is None:
        case = ApDupCase(key=key[:200], state="open", opened_at=utcnow(), **kw)
        db.add(case)
        db.flush()
    elif case.state == "gone":  # it came back: open again (the earlier person decision, if any, stays in the audit)
        case.state, case.closed_at = "open", None
        case.decided_by_id, case.decided_by, case.decided_at, case.reason = None, "", None, ""
        case.second_by_id, case.second_by, case.second_at = None, "", None
        for k, v in kw.items():
            setattr(case, k, v)
    return case


def _close_gone(db: Session, keep: set, **where) -> int:
    q = db.query(ApDupCase).filter(ApDupCase.state.in_(LIVE))
    for k, v in where.items():
        q = q.filter(getattr(ApDupCase, k).in_(v) if isinstance(v, (list, tuple, set)) else getattr(ApDupCase, k) == v)
    n = 0
    for case in q.all():
        if case.key not in keep:
            case.state, case.closed_at = "gone", utcnow()
            n += 1
    return n


def _cleared_words(case: ApDupCase) -> str:
    by = case.decided_by + (f" and {case.second_by}" if case.second_by else "")
    when = (case.second_at or case.decided_at)
    return f"cleared as not a duplicate by {by}{' on ' + when.strftime('%d.%m.%Y') if when else ''}: {case.reason}"[:300]


def _confirmed_words(case: ApDupCase) -> str:
    return f"confirmed a duplicate by {case.decided_by}: {case.reason}"[:200]


def _pending_words(case: ApDupCase) -> str:
    return f"cleared as not a duplicate by {case.decided_by}; a second person confirms it"


# ---- Tess --------------------------------------------------------------------------------------------------------------------
def _upload_twin(db: Session, c: ApCapture) -> Optional[ApCapture]:
    from . import capture as C

    keys = C._dup_keys(c)
    refs = {k[0] for k in keys}
    if not refs:
        return None
    for g in db.query(ApCapture).filter(ApCapture.uploaded_at.isnot(None), ApCapture.ref_key.in_(refs), ApCapture.id != c.id).order_by(ApCapture.uploaded_at, ApCapture.id).all():
        if C._dup_keys(g) & keys:
            return g
    return None


def _batch_twin(db: Session, c: ApCapture) -> Optional[ApCapture]:
    from . import capture as C

    keys = C._dup_keys(c)
    if not keys:
        return None
    for g in db.query(ApCapture).filter(ApCapture.batch_id == c.batch_id, ApCapture.id != c.id, ApCapture.uploaded_at.is_(None), ApCapture.item_id < c.item_id,
                                        ApCapture.stage.in_(("test", "ready"))).order_by(ApCapture.item_id).all():
        if C._dup_keys(g) & keys:
            return g
    return None


def _tess_twin(db: Session, c: ApCapture, dup: str) -> Optional[dict]:
    """Which twin Tess's red names: {kind, ref, id, text} (the words are the ones capture.test_batch writes)."""
    from . import capture as C

    if "already went out in an upload" in dup:
        g = _upload_twin(db, c)
        return {"kind": "upload", "ref": str(g.id) if g else "?", "id": g.id if g else None, "text": _twin_name(db, g.id) if g else ""}
    if "in this batch" in dup:
        g = _batch_twin(db, c)
        return {"kind": "batch", "ref": str(g.id) if g else "?", "id": g.id if g else None, "text": _twin_name(db, g.id) if g else ""}
    if "posted in SAP" in dup:
        t = C._sap_twin(db, c) or {}
        doc, co = t.get("doc_number") or "", t.get("company_code") or ""
        pid = db.query(ApPosted.id).filter(ApPosted.company_code == co, ApPosted.doc_number == doc).scalar() if doc else None
        return {"kind": "sap", "ref": f"{co}:{doc}" if doc else "?", "id": pid, "text": f"SAP doc {doc}{' in ' + co if co else ''}" if doc else "SAP"}
    return {"kind": "other", "ref": re.sub(r"\W+", "", dup)[:40], "id": None, "text": ""}


def _period_of(f: dict) -> tuple[str, tuple[str, str]]:
    return str(f.get("statement_month") or "").strip().lower(), (str(f.get("read_start") or "").strip(), str(f.get("read_end") or "").strip())


def near_twin(db: Session, c: ApCapture) -> Optional[tuple[ApCapture, str]]:
    """The near-duplicate utility rule: an earlier utility bill (not skipped) for the same account and the same period
    (statement month) or the same read dates, with another bill number, whose amount is within R1."""
    from . import masters as M

    f = c.fields or {}
    if c.bucket != "utility" or c.uploaded_at:
        return None
    a = M.acct_key(f.get("account"))
    amt = amount_of(c)
    month, reads = _period_of(f)
    if not a or amt is None or not (month or all(reads)):
        return None
    # the account's earlier bills through the indexed bill history (Builder U's ap_bill_history: every utility capture Tess
    # tests has a row, older ones are backfilled), not a scan of every capture
    from ..models.ap import ApBillHistory

    ids = sorted({cid for (cid,) in db.query(ApBillHistory.capture_id).filter(ApBillHistory.account_key == a, ApBillHistory.capture_id.isnot(None),
                                                                              ApBillHistory.capture_id < c.id).all()}, reverse=True)[:200]
    if not ids:
        return None
    q = db.query(ApCapture).filter(ApCapture.id.in_(ids), ApCapture.bucket == "utility", ApCapture.stage != "skipped")
    for g in q.order_by(ApCapture.id.desc()).all():
        gf = g.fields or {}
        if M.acct_key(gf.get("account")) != a or (g.ref_key and g.ref_key == c.ref_key):
            continue
        gm, gr = _period_of(gf)
        same = (month and gm == month) or (all(reads) and gr == reads)
        ga = amount_of(g)
        if same and ga is not None and abs(ga - amt) <= NEAR_RAND:
            when = f"for {f.get('statement_month')}" if month and gm == month else f"read {reads[0]} to {reads[1]}"
            text = (f"possible duplicate: account {f.get('account')} {when}, {_r(amt)}, like {_twin_name(db, g.id)} "
                    f"(bill {gf.get('invoice_no') or '?'}, {_r(ga)})")
            return g, text
    return None


def at_tess(db: Session, c: ApCapture, it: Optional[ApItem], dup: str, warn: list, stop: list) -> tuple[str, list[str]]:
    """Tess's hook, per document, before test_doc → (the red to use, notes to show). Opens or finds the case for the
    red Tess raises; applies its outcome; the near-duplicate utility rule; closes the cases she no longer raises."""
    keep: set[str] = set()
    notes: list[str] = []
    pf = None
    if dup:
        tw = _tess_twin(db, c, dup)
        key = f"tess:{c.id}:{tw['kind']}:{tw['ref']}"
        pf = pf or R.portfolio_of(db, c)
        case = _get_or_open(db, key, source="tess", code=tw["kind"], item_id=c.item_id, capture_id=c.id, batch_id=c.batch_id, portfolio=pf,
                            document=_doc_text(c, it), vendor=c.vendor_no or "", reference=str((c.fields or {}).get("invoice_no") or "")[:80],
                            amount=amount_of(c), twin_kind=tw["kind"], twin_ref=(tw["text"] or tw["ref"])[:200], twin_id=tw["id"], text=dup[:400])
        keep.add(key)
        if case.state == "valid":
            notes.append(_cleared_words(case))
            dup = ""
        elif case.state == "genuine":
            dup = f"{dup} ({_confirmed_words(case)})"
        elif case.state == "pending":
            dup = f"{dup} ({_pending_words(case)})"
    else:
        hit = near_twin(db, c)
        if hit is not None:
            g, text = hit
            key = f"near:{c.id}:{g.id}"
            pf = pf or R.portfolio_of(db, c)
            case = _get_or_open(db, key, source="near", code="near", item_id=c.item_id, capture_id=c.id, batch_id=c.batch_id, portfolio=pf,
                                document=_doc_text(c, it), vendor=c.vendor_no or "", reference=str((c.fields or {}).get("invoice_no") or "")[:80],
                                amount=amount_of(c), twin_kind="upload" if g.uploaded_at else "batch", twin_ref=_twin_name(db, g.id)[:200], twin_id=g.id, text=text[:400])
            keep.add(key)
            if case.state == "valid":
                notes.append(_cleared_words(case))
            elif case.state == "genuine":
                stop.append(f"{text} ({_confirmed_words(case)})")
            elif case.state == "pending":
                warn.append(f"{text} ({_pending_words(case)})")
            else:
                warn.append(text)
    _close_gone(db, keep, capture_id=c.id, source=("tess", "near"))
    return dup, notes


# ---- the AP team's checks (Ivy, Bo) -----------------------------------------------------------------------------------------------
def _base(x: dict) -> str:
    return str(x.get("dup_text") or x.get("text") or "")


def _apply_item(case: ApDupCase, x: dict) -> dict:
    base = _base(x)
    y = {**x, "dup_text": base, "case": case.id}
    if case.state == "valid":
        y["severity"], y["text"] = "info", f"{base} {_cleared_words(case)[:1].upper()}{_cleared_words(case)[1:]}."
        y.setdefault("was", x.get("severity"))
    elif case.state == "genuine":
        y["text"] = f"{base} {_confirmed_words(case)[:1].upper()}{_confirmed_words(case)[1:]}."
    elif case.state == "pending":
        y["text"] = f"{base} {_pending_words(case)[:1].upper()}{_pending_words(case)[1:]}."
    else:
        y["text"] = base
        if x.get("was"):
            y["severity"] = x["was"]
    return y


def _item_amount(it: ApItem, row: Optional[int]) -> Optional[float]:
    f = it.fields or {}
    if row is not None:
        for r in f.get("rows") or []:
            if r.get("row") == row:
                return _num(r.get("amount"))
    for k in ("total", "amount", "excl"):
        if _num(f.get(k)) is not None:
            return abs(_num(f.get(k)))
    return None


def at_check(db: Session, it: ApItem, found: list) -> list:
    """The AP team's checks' hook (service._check): their duplicate findings open cases; a decided case changes the
    finding (valid: no longer a stop, with who cleared it; genuine: says so). Other findings pass through."""
    if it is None or it.id is None:
        return found
    keep: set[str] = set()
    out = []
    pf = None
    c = None
    for x in found:
        code = x.get("code") if isinstance(x, dict) else None
        if code not in ITEM_CODES:
            out.append(x)
            continue
        src, tk = ITEM_CODES[code]
        row = x.get("row")
        key = f"{src}:{it.id}:{code}:{row if row is not None else ''}"
        if pf is None:
            pf = R.portfolio_of(db, it)
            c = db.query(ApCapture).filter(ApCapture.item_id == it.id).one_or_none()
        f = it.fields or {}
        case = _get_or_open(db, key, source=src, code=code, item_id=it.id, capture_id=c.id if c is not None else None, batch_id=it.batch_id, row=row, portfolio=pf,
                            document=_doc_text(c, it) + (f" · row {row}" if row is not None else ""), vendor=str(f.get("vendor") or "")[:40],
                            reference=str(f.get("invoice_no") or f.get("ref") or "")[:80], amount=_item_amount(it, row), twin_kind=tk,
                            twin_ref=str(x.get("text") or "")[:200], text=str(x.get("text") or "")[:400])
        keep.add(key)
        out.append(_apply_item(case, x))
    _close_gone(db, keep, item_id=it.id, source=("ivy", "bo"))
    return out


def _recount(it: ApItem) -> None:
    """The file's status from its findings, as service._check sets it."""
    from . import engine as E

    found = it.findings or []
    it.stops = sum(1 for x in found if x.get("severity") == "stop")
    it.checks = sum(1 for x in found if x.get("severity") == "check")
    if it.status in ("cleared", "rejected", "waiting"):
        return
    if it.kind == "unknown":
        it.status = "unknown"
    elif it.stops:
        it.status = "exception"
    elif it.checks or it.kind in E.FOUR_EYE:
        it.status = "check"
    else:
        it.status = "ok"


# ---- people decide ---------------------------------------------------------------------------------------------------------------
def get(db: Session, cid: int) -> ApDupCase:
    case = db.get(ApDupCase, int(cid or 0))
    if case is None:
        raise LookupError("No such duplicate case.")
    return case


def _doc_of(db: Session, case: ApDupCase):
    if case.capture_id:
        c = db.get(ApCapture, case.capture_id)
        if c is not None:
            return c
    if case.item_id:
        return db.get(ApItem, case.item_id)
    return None


def _require(db: Session, user: User, case: ApDupCase, role: str, what: str) -> None:
    """The role in the case's portfolio; on a document, never someone who touched it (a classification of another case
    on the same document is not a touch here)."""
    if not R.may(db, user, role, case.portfolio or None):
        raise R.Forbidden(f"{what} needs the {role} role" + (f" in {case.portfolio}" if case.portfolio else "") + ". An admin gives it in AP Settings → AP people.")
    doc = _doc_of(db, case)
    # 1.0.104 review: nor whoever dropped its folder (include_dropper) — the dropper never settles their own duplicate
    if doc is not None and user.id in R.touched_by(db, doc, include_dropper=True, exclude_actions=(ACTION,)):
        raise R.Forbidden("You worked on this document or dropped its folder, so a different person decides whether it is a duplicate.")


def threshold(db: Session, case: ApDupCase) -> float:
    return float(PF.setting(db, case.portfolio or None, "review_threshold_r", 50000) or 0)


def _target(case: ApDupCase) -> str:
    return f"ap_item:{case.item_id}" if case.item_id else f"ap_dup_case:{case.id}"


def _history(db: Session, case: ApDupCase, who: str, words: str) -> None:
    from . import capture as C

    it = db.get(ApItem, case.item_id) if case.item_id else None
    if it is not None:
        C._hist(it, who, words[:400])


def _effect(db: Session, user: User, case: ApDupCase) -> None:
    """What the outcome does to the document: genuine → it never posts (the capture is skipped; an AP team file is
    rejected, a payment run's row keeps its stop); valid → Tess / the checks test it again without the red."""
    from . import capture as C
    from . import service as S

    it = db.get(ApItem, case.item_id) if case.item_id else None
    c = db.get(ApCapture, case.capture_id) if case.capture_id else None
    if case.source in ("ivy", "bo") and it is not None:
        it.findings = [(_apply_item(case, x) if isinstance(x, dict) and x.get("code") == case.code and x.get("row") == case.row else x) for x in it.findings or []]
        if case.state == "genuine" and case.row is None and it.status not in ("cleared", "rejected"):
            it.status = "rejected"
            it.note = ((it.note + "\n") if it.note else "") + f"{_who(user)}: confirmed a duplicate: {case.reason}"[:1000]
        _recount(it)
        db.flush()
        C.retest(db, it)
        b = db.get(ApBatch, it.batch_id)
        if b is not None:
            S.refresh_batch(db, b)
        return
    if c is None:
        return
    if case.state == "genuine" and not c.uploaded_at:
        c.bucket, c.stage, c.needs = "skipped", "skipped", ""
        db.flush()
        return
    if c.stage == "ready" and not c.uploaded_at:
        b = db.get(ApBatch, c.batch_id)
        if b is not None:
            C.test_batch(db, b)


def decide(db: Session, user: User, case: ApDupCase, outcome: str, reason: str = "") -> ApDupCase:
    """genuine or valid, with a reason. A valid release at or above the review threshold waits for a second person."""
    outcome = (outcome or "").strip().lower()
    reason = (reason or "").strip()[:1000]
    if outcome not in ("genuine", "valid"):
        raise Refused("Genuine (a duplicate: it never posts) or valid (not a duplicate: it is released).")
    if case.state == "pending":
        raise Refused("This one waits for a second person to confirm it is not a duplicate.")
    if case.state not in ("open",):
        raise Refused("This case is closed already." if case.state != "gone" else "Tess no longer raises this one: nothing to decide.")
    if not reason:
        raise Refused("Say why.")
    _require(db, user, case, "reviewer", "Deciding a possible duplicate")
    case.decided_by_id, case.decided_by, case.decided_at, case.reason = user.id, _who(user)[:160], utcnow(), reason
    if outcome == "genuine":
        case.state, case.closed_at = "genuine", utcnow()
        words = f"Confirmed a duplicate ({_who(user)}): {reason}. It never posts."
    else:
        thr = threshold(db, case)
        if case.amount is not None and thr and abs(case.amount) >= thr:
            case.state = "pending"
            words = f"Cleared as not a duplicate ({_who(user)}): {reason}. Over {_r(thr)}: a second person confirms it."
        else:
            case.state, case.closed_at = "valid", utcnow()
            words = f"Cleared as not a duplicate ({_who(user)}): {reason}. Released."
    db.flush()
    log(db, ACTION, actor=user, target=_target(case), detail=f"case {case.id} ({case.source} {case.code}): {case.state}: {reason}"[:400])
    _history(db, case, "AP controls", words)
    _effect(db, user, case)
    return case


def second(db: Session, user: User, case: ApDupCase, agree: bool, reason: str = "") -> ApDupCase:
    """The second person on a valid release over the threshold: agree (released) or send it back (open again)."""
    reason = (reason or "").strip()[:1000]
    if case.state != "pending":
        raise Refused("Only a valid release waiting for a second person is confirmed here.")
    if case.decided_by_id == user.id:
        raise R.Forbidden("You cleared it: a different person confirms it.")
    _require(db, user, case, "approver", "Confirming a duplicate release")
    if agree:
        case.state, case.closed_at = "valid", utcnow()
        case.second_by_id, case.second_by, case.second_at = user.id, _who(user)[:160], utcnow()
        words = f"Confirmed not a duplicate ({_who(user)}, second person){': ' + reason if reason else ''}. Released."
    else:
        if not reason:
            raise Refused("Say why it goes back.")
        words = f"Sent back ({_who(user)}, second person): {reason}. It stays a possible duplicate."
        case.state = "open"
        case.decided_by_id, case.decided_by, case.decided_at = None, "", None
        case.reason = ""
    db.flush()
    log(db, ACTION, actor=user, target=_target(case), detail=f"case {case.id}: second person {'agreed' if agree else 'sent it back'}{': ' + reason if reason else ''}"[:400])
    _history(db, case, "AP controls", words)
    _effect(db, user, case)
    return case


def take(db: Session, user: User, case: ApDupCase, owner_id: Optional[int] = None) -> ApDupCase:
    """A case's owner: the person who takes it, or (admins) whoever they name."""
    if owner_id and owner_id != user.id:
        if not user.is_admin:
            raise R.Forbidden("Only an admin gives a case to someone else.")
        u = db.get(User, int(owner_id))
        if u is None or u.is_axl:
            raise LookupError("No such person.")
    elif not (R.may(db, user, "reviewer", case.portfolio or None) or user.is_admin):
        raise R.Forbidden("Taking a duplicate case needs the reviewer role.")
    case.owner_id = int(owner_id) if owner_id else user.id
    db.flush()
    log(db, "ap_dup_owner", actor=user, target=f"ap_dup_case:{case.id}", detail=f"owner: {_who(db.get(User, case.owner_id))}")
    return case


def confirm_bulk(db: Session, user: User, ids: Iterable[int], outcome: str = "", reason: str = "") -> dict:
    """Bulk: each case as proposed (an import's proposal) or as `outcome`; the reason is the one given, else the proposal's."""
    done, waiting, skipped = [], [], []
    for cid in list(ids)[:2000]:
        case = db.get(ApDupCase, int(cid))
        if case is None:
            skipped.append({"id": cid, "why": "not found"})
            continue
        o = (outcome or case.proposed or "").strip().lower()
        if o not in ("genuine", "valid"):
            skipped.append({"id": cid, "why": "no proposal: decide it on its own"})
            continue
        why = reason or (f"As proposed: {case.proposed_why}" if case.proposed == o and case.proposed_why else "")
        try:
            decide(db, user, case, o, why)
        except (Refused, R.Forbidden, PermissionError) as e:
            skipped.append({"id": cid, "why": str(e)})
            continue
        (waiting if case.state == "pending" else done).append(cid)
    return {"done": len(done), "waiting": len(waiting), "skipped": skipped[:50], "skipped_n": len(skipped)}


# ---- the register -------------------------------------------------------------------------------------------------------------
def _age(case: ApDupCase, now: Optional[datetime] = None) -> int:
    now = now or utcnow()
    t = case.opened_at or now
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return max(0, (now - t).days)


def age_band(days: int) -> str:
    for lo, hi, label in AGES:
        if days >= lo and (hi is None or days <= hi):
            return label
    return AGES[-1][2]


def case_out(db: Session, case: ApDupCase, names: Optional[dict] = None) -> dict:
    names = names if names is not None else {}
    if case.owner_id and case.owner_id not in names:
        u = db.get(User, case.owner_id)
        names[case.owner_id] = (u.name or u.email) if u is not None else ""
    d = _age(case)
    return {"id": case.id, "source": case.source, "code": case.code, "state": case.state, "portfolio": case.portfolio, "document": case.document,
            "vendor": case.vendor, "reference": case.reference, "amount": case.amount, "twin_kind": case.twin_kind, "twin": case.twin_ref, "text": case.text,
            "capture_id": case.capture_id, "item_id": case.item_id, "batch_id": case.batch_id, "row": case.row, "import_id": case.import_id,
            "opened_at": case.opened_at.isoformat() if case.opened_at else "", "age_days": d, "age": age_band(d),
            "owner_id": case.owner_id, "owner": names.get(case.owner_id, "") if case.owner_id else "", "proposed": case.proposed, "proposed_why": case.proposed_why,
            "decided_by": case.decided_by, "decided_at": case.decided_at.isoformat() if case.decided_at else "", "reason": case.reason,
            "second_by": case.second_by, "line": case.line or {}}


def listing(db: Session, state: str = "live", portfolio: str = "", owner: Optional[int] = None, q: str = "", import_id: Optional[int] = None,
            limit: int = 300) -> dict:
    """The Duplicates view: the cases (live = open + waiting for a second person), with the age bands per portfolio and
    owner, and the counts."""
    qy = db.query(ApDupCase)
    if state == "live":
        qy = qy.filter(ApDupCase.state.in_(LIVE))
    elif state in STATES:
        qy = qy.filter(ApDupCase.state == state)
    if portfolio:
        qy = qy.filter(ApDupCase.portfolio == portfolio.strip().upper())
    if owner:
        qy = qy.filter(ApDupCase.owner_id == owner)
    if import_id:
        qy = qy.filter(ApDupCase.import_id == import_id)
    q = (q or "").strip()[:60]
    if q:
        qy = qy.filter(ApDupCase.document.ilike(f"%{q}%") | ApDupCase.vendor.like(f"%{q}%") | ApDupCase.reference.ilike(f"%{q}%"))
    names: dict = {}
    rows = [case_out(db, c, names) for c in qy.order_by(ApDupCase.opened_at, ApDupCase.id).limit(max(1, min(int(limit or 300), 2000))).all()]
    # the bands: every live case (not only the page shown)
    bands: dict[str, dict] = {}
    owners: dict[str, int] = {}
    now = utcnow()
    for case in db.query(ApDupCase).filter(ApDupCase.state.in_(LIVE)).all():
        b = bands.setdefault(case.portfolio or "", {"portfolio": case.portfolio or "", **{label: 0 for _, _, label in AGES}, "total": 0})
        b[age_band(_age(case, now))] += 1
        b["total"] += 1
        if case.owner_id and case.owner_id not in names:
            u = db.get(User, case.owner_id)
            names[case.owner_id] = (u.name or u.email) if u is not None else ""
        k = f"{case.portfolio or ''}|{names.get(case.owner_id, '') if case.owner_id else ''}"
        owners[k] = owners.get(k, 0) + 1
    counts = dict(db.query(ApDupCase.state, func.count(ApDupCase.id)).group_by(ApDupCase.state).all())
    imports = [{"id": i.id, "filename": i.filename, "portfolio": i.portfolio, "rows": i.rows, "by": i.by_name, "at": i.created_at.isoformat() if i.created_at else ""}
               for i in db.query(ApDupImport).order_by(ApDupImport.id.desc()).limit(20).all()]
    return {"rows": rows, "counts": {s: counts.get(s, 0) for s in STATES}, "bands": sorted(bands.values(), key=lambda b: b["portfolio"]),
            "ages": [label for _, _, label in AGES], "owners": [{"portfolio": k.split("|")[0], "owner": k.split("|")[1] or "nobody", "open": n} for k, n in sorted(owners.items())],
            "imports": imports}


def open_count(db: Session) -> int:
    """KPI on Paula's board: possible duplicates nobody has settled (target nil at month end)."""
    return db.query(func.count(ApDupCase.id)).filter(ApDupCase.state.in_(LIVE)).scalar() or 0


def needs_line(db: Session, user: Optional[User]) -> Optional[dict]:
    """Needs you → one line: the possible duplicates this person may decide (reviewer) or confirm (approver)."""
    if user is None or not (R.may(db, user, "reviewer", None) or R.may(db, user, "approver", None)):
        return None
    n = 0
    seen: dict[tuple[str, str], bool] = {}
    for pf, state, by in db.query(ApDupCase.portfolio, ApDupCase.state, ApDupCase.decided_by_id).filter(ApDupCase.state.in_(LIVE)).all():
        role = "approver" if state == "pending" else "reviewer"
        if (pf, role) not in seen:
            seen[(pf, role)] = R.may(db, user, role, pf or None)
        if seen[(pf, role)] and not (state == "pending" and by == user.id):
            n += 1
    if not n:
        return None
    return {"key": "duplicates", "sub": "duplicates", "title": "Possible duplicates", "count": n,
            "text": "1 possible duplicate to settle" if n == 1 else f"{n} possible duplicates to settle"}


# ---- the backlog: a MOL / Workflow Basket listing ------------------------------------------------------------------------------------
HEADS = [("vendor", ("vendor", "supplier", "lifnr", "creditor", "vendor number", "supplier number", "vendor no", "account number of vendor or creditor")),
         ("name", ("vendor name", "supplier name", "name", "name 1", "name1", "payee")),
         ("reference", ("reference", "xblnr", "invoice", "invoice no", "invoice number", "ref", "reference number", "bill number", "invoice ref")),
         ("amount", ("amount", "amount in lc", "amount in local currency", "gross amount", "value", "invoice amount", "total")),
         ("date", ("document date", "invoice date", "doc date", "date", "posting date", "bldat")),
         ("company_code", ("company code", "bukrs", "cocd", "company")),
         ("doc_number", ("document number", "doc no", "doc number", "belnr", "document", "sap document", "workflow id", "work item")),
         ("account", ("account", "account number", "utility account", "municipal account", "account no")),
         ("municipality", ("municipality", "council", "municipal")),
         ("note", ("note", "notes", "comment", "comments", "text"))]
MAX_LINES = 20000


def read_listing(data: bytes, filename: str = "") -> tuple[list[dict], dict]:
    """The listing's lines as {field: value} and the mapping {field: the sheet's column}; the header row is found in
    the first 15 rows; it needs a reference and a vendor or account column."""
    from . import masters as M

    if len(data or b"") > M.MAX_MB * 1024 * 1024:
        raise Refused(f"The file is over {M.MAX_MB} MB.")
    try:
        raw = M._raw_rows(data, filename or "listing.xlsx")
    except M.Refused as e:
        raise Refused(str(e)) from None
    cols: dict[str, int] = {}
    start = 0
    for i, r in enumerate(raw[:15]):
        norm = [M._head(h) for h in r]
        got: dict[str, int] = {}
        for field, names in HEADS:
            for name in names:
                if name in norm and norm.index(name) not in got.values():
                    got[field] = norm.index(name)
                    break
        if "reference" in got and ("vendor" in got or "account" in got):
            cols, start = got, i + 1
            mapping = {f: str(r[c] or "")[:60] for f, c in got.items()}
            break
    if not cols:
        raise Refused("No header row with a reference (invoice number) and a vendor or account column in the first 15 rows.")
    out = []
    for r in raw[start:start + MAX_LINES]:
        rec = {f: (r[c] if c < len(r) else None) for f, c in cols.items()}
        if not any(v not in (None, "") for v in rec.values()):
            continue
        line = {f: K.text(v, 160) for f, v in rec.items() if f != "amount"}
        line["amount"] = _num(rec.get("amount"))
        if isinstance(rec.get("date"), datetime):
            line["date"] = rec["date"].date().isoformat()
        out.append(line)
    if not out:
        raise Refused("The listing has no lines.")
    return out, mapping


def propose(db: Session, line: dict, earlier: dict) -> tuple[str, str, Optional[ApPosted]]:
    """A listing line's proposed classification → (genuine | valid | "", why, the posted twin)."""
    from . import history as H

    ref, vendor, amt = line.get("reference") or "", line.get("vendor") or "", line.get("amount")
    rk = H.ref_key(ref)
    if len(rk) < 2:
        return "", "no reference to match on: a person looks", None
    if not vendor:
        return "", "no vendor number to match on: a person looks", None
    k = (tuple(sorted(K.vendor_variants(vendor))), rk)
    if k in earlier:
        return "genuine", f"the same vendor and reference as line {earlier[k]} of this listing", None
    vs = H._variants([vendor])
    docs = db.query(ApPosted).filter(ApPosted.ref_key == rk, ApPosted.vendor.in_(vs)).all() if vs else []
    own = str(line.get("doc_number") or "").strip()
    docs = [d for d in docs if not (own and d.doc_number == own)]
    if docs:
        d = max(docs, key=lambda x: (x.posting_date or "", x.id))
        if amt is None or d.amount is None or abs(abs(d.amount) - abs(amt)) <= NEAR_RAND:
            return "genuine", f"already posted in SAP: doc {d.doc_number} on {H._dmy(d.posting_date or '')} ({_r(d.amount)})", d
        return "", f"SAP posted doc {d.doc_number} with this reference for {_r(d.amount)} (this line {_r(amt)}): a person looks", d
    cref = re.sub(r"[^a-z0-9]", "", ref.lower())[:120]
    g = db.query(ApCapture).filter(ApCapture.ref_key == cref, ApCapture.vendor_no.in_(vs or ["-"]), ApCapture.uploaded_at.isnot(None)).order_by(ApCapture.uploaded_at).first() if cref else None
    if g is not None:
        return "genuine", f"went out in a CBX upload on {g.uploaded_at:%d.%m.%Y} ({_twin_name(db, g.id)})", None
    return "valid", "SAP and CBX have no other invoice with this vendor and reference", None


def import_listing(db: Session, user: User, lines: list[dict], mapping: dict, *, filename: str, portfolio: str = "", confirm: bool = False) -> dict:
    """Preview (confirm=False): how many lines, and the proposals; save: one case per line (source import)."""
    if not user.is_admin and not R.may(db, user, "reviewer", portfolio or None):
        raise R.Forbidden("Importing a duplicates listing needs the reviewer role.")
    code = PF.pick(db, portfolio) if portfolio else ""
    pf = code or ((PF.default(db).code if PF.default(db) else ""))
    earlier: dict = {}
    props = []
    for n, line in enumerate(lines, 1):
        o, why, d = propose(db, line, earlier)
        rk = (tuple(sorted(K.vendor_variants(line.get("vendor") or ""))), re.sub(r"[^A-Z0-9]", "", str(line.get("reference") or "").upper()).lstrip("0"))
        if line.get("vendor") and rk[1]:
            earlier.setdefault(rk, n)
        props.append((n, line, o, why, d))
    summary = {"lines": len(lines), "genuine": sum(1 for p in props if p[2] == "genuine"), "valid": sum(1 for p in props if p[2] == "valid"),
               "look": sum(1 for p in props if not p[2]), "mapping": mapping, "portfolio": pf, "saved": False,
               "sample": [{"line": n, **{k: line.get(k) for k in ("vendor", "reference", "amount")}, "proposed": o, "why": why} for n, line, o, why, _ in props[:20]]}
    if not confirm:
        return summary
    imp = ApDupImport(filename=(filename or "")[:255], portfolio=pf, rows=len(lines), mapping=mapping, by_id=user.id, by_name=_who(user)[:160])
    db.add(imp)
    db.flush()
    for n, line, o, why, d in props:
        doc = " · ".join(str(x) for x in (f"line {n}", line.get("name") or line.get("vendor") or "", line.get("account") or "", line.get("reference") or "") if x)
        db.add(ApDupCase(key=f"import:{imp.id}:{n}", source="import", code="listing", portfolio=pf, document=doc[:300], vendor=str(line.get("vendor") or "")[:40],
                         reference=str(line.get("reference") or "")[:80], amount=line.get("amount"), twin_kind="sap" if d is not None else "listing",
                         twin_ref=(f"SAP doc {d.doc_number} in {d.company_code}" if d is not None else why)[:200], twin_id=d.id if d is not None else None,
                         text=f"{Path(filename or 'listing').name} line {n}"[:400], state="open", proposed=o, proposed_why=why[:300], import_id=imp.id,
                         line={k: v for k, v in line.items()}, opened_at=utcnow()))
    db.flush()
    log(db, "ap_dup_import", actor=user, target=f"ap_dup_import:{imp.id}", detail=f"{filename}: {len(lines)} lines ({summary['genuine']} genuine, {summary['valid']} valid, {summary['look']} to look at)"[:400])
    summary.update(saved=True, import_id=imp.id)
    return summary


def export(db: Session, import_id: Optional[int] = None) -> bytes:
    """The conclusions as an Excel file to clear in SAP / MOL: an import's lines (or every case) with the outcome."""
    from openpyxl import Workbook

    from .. import xlsafe

    qy = db.query(ApDupCase)
    if import_id:
        qy = qy.filter(ApDupCase.import_id == import_id)
    rows = qy.order_by(ApDupCase.id).all()
    wb = Workbook()
    ws = wb.active
    ws.title = "Duplicates"
    heads = [f for f, _ in HEADS]
    ws.append(["Case", "Portfolio", "Raised by", "Document", *([h.replace("_", " ").title() for h in heads] if import_id else ["Vendor", "Reference", "Amount"]),
               "Twin", "Proposed", "Why proposed", "Outcome", "Reason", "Decided by", "Decided on", "Second person", "Opened", "Age (days)"])
    for c in rows:
        line = c.line or {}
        mid = [line.get(h) for h in heads] if import_id else [c.vendor, c.reference, c.amount]
        outcome = {"genuine": "Genuine duplicate: do not pay", "valid": "Valid: not a duplicate", "pending": "Valid, waiting for a second person",
                   "open": "", "gone": "No longer raised"}.get(c.state, c.state)
        ws.append([c.id, c.portfolio, c.source, c.document, *mid, c.twin_ref, c.proposed, c.proposed_why, outcome, c.reason, c.decided_by,
                   c.decided_at.strftime("%Y-%m-%d") if c.decided_at else "", c.second_by, c.opened_at.strftime("%Y-%m-%d") if c.opened_at else "", _age(c)])
    xlsafe.book(wb)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
