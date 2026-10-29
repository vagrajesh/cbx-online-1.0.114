# 1.0.104: new — the Valuence AP controls' foundation F0b (VALUENCE-AP-GAPS.md §2 F0b; SPEC-1104 Phase 1)
"""AP roles per portfolio, and separation of duties.

    capturer        drops folders, types and corrects documents
    reviewer        reviews what others captured (V05), new vendors (V01), duplicates (V09)
    poster          ticks "include yellow" and downloads the SAP upload
    approver        approves what a preparer made (estimates V08, a valid duplicate release above the threshold V09 …)
    recon_reviewer  reviews reconciliations (V11)

A row with portfolio "" holds the role in every portfolio. Bootstrap: while a portfolio has no role rows at all (none of
its own and none for every portfolio), every AP user with write access holds capturer, reviewer, poster and approver for
it — so nothing stops on day one. Separation of duties always applies: a reviewer or approver is never someone who
touched the document (`touched_by`). Owner and admins manage the roles but never pass the separation checks by being
admins."""
from __future__ import annotations

from typing import Iterable, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..audit import log
from ..models import AuditLog, User
from ..models.ap import ApBatch, ApCapture, ApItem, ApPortfolio, ApRole, ApUtilityAccount, ApVendorCode
from . import portfolio as PF

SLUG = "ap"
ROLES = ("capturer", "reviewer", "poster", "approver", "recon_reviewer")
LABELS = {"capturer": "Capturer", "reviewer": "Reviewer", "poster": "Poster (downloads the upload)", "approver": "Approver",
          "recon_reviewer": "Reconciliation reviewer"}
BOOTSTRAP = ("capturer", "reviewer", "poster", "approver")
# the roles a person never holds on a document they touched
INDEPENDENT = ("reviewer", "approver", "recon_reviewer")
# audit actions on a file that are not a touch (asking Quinn for a draft reply changes nothing on the document)
NOT_A_TOUCH = ("ap_draft",)


class Forbidden(HTTPException, PermissionError):
    """403 with plain words: an HTTPException (a route answers 403 by itself) and a PermissionError (the AP routes'
    `_refuse` answers 403 with the words; service code can catch it)."""

    def __init__(self, text: str):
        HTTPException.__init__(self, 403, text)
        self.args = (text,)

    def __str__(self) -> str:
        return str(self.detail)


def _code(portfolio) -> str:
    if isinstance(portfolio, ApPortfolio):
        return portfolio.code
    return str(portfolio or "").strip().upper()


def can_write(db: Session, user: Optional[User]) -> bool:
    """An AP user who may change things: the AP Tool, not a viewer, a person (not C-EXA), active."""
    if user is None or user.is_axl or not user.is_active or user.role == "viewer":
        return False
    from ..security.deps import has_tool

    return has_tool(db, user, SLUG)


def has_rows(db: Session, portfolio) -> bool:
    """True once the portfolio has any role row (its own, or one for every portfolio): bootstrap ends for it."""
    return db.query(ApRole.id).filter(ApRole.portfolio.in_((_code(portfolio), ""))).first() is not None


def may(db: Session, user: Optional[User], role: str, portfolio) -> bool:
    """Does the person hold the role in the portfolio? `portfolio` None or "" = in any portfolio. (Separation of duties
    on one document is `independent` / `require(..., capture=)`.)"""
    if role not in ROLES or not can_write(db, user):
        return False
    code = _code(portfolio)
    if not code:
        return any(may(db, user, role, p.code) for p in PF.portfolios(db)) or (not PF.portfolios(db) and _may_in(db, user, role, ""))
    return _may_in(db, user, role, code)


def _may_in(db: Session, user: User, role: str, code: str) -> bool:
    rows = db.query(ApRole.user_id, ApRole.role).filter(ApRole.portfolio.in_((code, ""))).all()
    if not rows:
        return role in BOOTSTRAP
    return any(uid == user.id and r == role for uid, r in rows)


def roles_of(db: Session, user: Optional[User]) -> dict[str, list[str]]:
    """{portfolio code: [roles]} the person holds (bootstrap included)."""
    if not can_write(db, user):
        return {}
    rows = db.query(ApRole.user_id, ApRole.portfolio, ApRole.role).all()  # one read (the AP page asks on every refresh)
    out = {}
    for p in PF.portfolios(db):
        mine = [(uid, r) for uid, code, r in rows if code in (p.code, "")]
        held = [r for r in ROLES if (r in BOOTSTRAP if not mine else any(uid == user.id and x == r for uid, x in mine))]
        if held:
            out[p.code] = held
    return out


def explicit_roles(db: Session, user: Optional[User]) -> set[str]:
    """The roles the person holds through rows of their own (no bootstrap)."""
    if user is None:
        return set()
    return {r for (r,) in db.query(ApRole.role).filter(ApRole.user_id == user.id).all()}


# ---- who touched a document ---------------------------------------------------------------------------------------------------
def _item_of(db: Session, capture) -> tuple[Optional[ApCapture], Optional[ApItem]]:
    if isinstance(capture, ApItem):
        c = db.query(ApCapture).filter(ApCapture.item_id == capture.id).one_or_none()
        return c, capture
    if isinstance(capture, ApCapture):
        return capture, db.get(ApItem, capture.item_id)
    c = db.get(ApCapture, int(capture)) if capture else None
    return c, (db.get(ApItem, c.item_id) if c is not None else None)


def touched_by(db: Session, capture, *, include_dropper: bool = False, exclude_actions: Iterable[str] = ()) -> set[int]:
    """The people who decided, typed, coded, cleared, checked or corrected the document (an ApCapture, its ApItem, or
    the capture's id): the capture's and the file's own marks, every audit row a person left on the file, and whoever
    coded its vendor (Needs you → Code this vendor). `include_dropper` adds who dropped the folder; `exclude_actions`
    leaves out audit actions (e.g. a builder's own review action when it re-checks) — and then the capture's and the file's
    own marks (decided / checked / approved by) are left out too: every one of those is also an audit row, which says
    what it was."""
    c, it = _item_of(db, capture)
    ids: set[int] = set()
    marks = not exclude_actions
    if marks and c is not None and c.decided_by_id:
        ids.add(c.decided_by_id)
    if it is not None:
        if marks:
            ids |= {x for x in (it.checked_by_id, it.approved_by_id) if x}
        skip = set(NOT_A_TOUCH) | set(exclude_actions or ())
        for uid, action in db.query(AuditLog.actor_id, AuditLog.action).filter(AuditLog.target == f"ap_item:{it.id}", AuditLog.actor_id.isnot(None),
                                                                                  AuditLog.action.like("ap\\_%", escape="\\")).all():
            if action not in skip:
                ids.add(uid)
        if include_dropper:
            b = db.get(ApBatch, it.batch_id)
            if b is not None and b.user_id:
                ids.add(b.user_id)
    if c is not None and c.vendor_no and "ap_vendor_learned" not in set(exclude_actions or ()):
        ids |= {uid for (uid,) in db.query(AuditLog.actor_id).filter(AuditLog.target == f"ap_vendor:{c.vendor_no}", AuditLog.action == "ap_vendor_learned",
                                                                     AuditLog.actor_id.isnot(None)).all()}
    return ids


def independent(db: Session, user: Optional[User], capture, *, include_dropper: bool = True, exclude_actions: Iterable[str] = ()) -> bool:
    """Not someone who touched the document — nor, by default, the person who dropped its folder."""
    return user is not None and user.id not in touched_by(db, capture, include_dropper=include_dropper, exclude_actions=exclude_actions)


def portfolio_of(db: Session, capture) -> str:
    """The document's portfolio code (stamped by Pip; else worked out now)."""
    c, it = _item_of(db, capture)
    if c is not None:
        if c.portfolio:
            return c.portfolio
        p = PF.for_capture(db, c)
        return p.code if p else ""
    b = db.get(ApBatch, it.batch_id) if it is not None else None
    p = PF.for_batch(db, b) if b is not None else PF.default(db)
    return p.code if p else ""


def require(db: Session, user: Optional[User], role: str, portfolio=None, capture=None, *, what: str = "", include_dropper: bool = True,
            exclude_actions: Iterable[str] = ()) -> None:
    """Refuse (403, plain words) unless the person holds the role in the portfolio — and, for a reviewer, approver or
    reconciliation reviewer acting on a document, did not touch it nor drop its folder (`include_dropper`; leave out a
    control's own re-check actions with `exclude_actions`). `portfolio` None with a capture = the capture's."""
    if portfolio is None and capture is not None:
        portfolio = portfolio_of(db, capture)
    code = _code(portfolio)
    if not may(db, user, role, code):
        raise Forbidden(f"{what or 'This'} needs the {LABELS.get(role, role).split(' (')[0].lower()} role"
                        + (f" in {code}" if code else "") + ". An admin gives it in AP Settings → AP people.")
    if capture is not None and role in INDEPENDENT and not independent(db, user, capture, include_dropper=include_dropper, exclude_actions=exclude_actions):
        raise Forbidden(f"You worked on this document or dropped its folder, so a different person {'reviews' if role != 'approver' else 'approves'} it.")


# ---- closing today's self-approval paths ---------------------------------------------------------------------------------------
def check_self_clear(db: Session, user: User, it: ApItem) -> None:
    """service.sign "clear" of a file the checks stopped: the person who dropped the folder or typed / corrected the file
    can't clear it alone once the portfolio has role rows; before that (bootstrap) it is allowed and audited ap_self_clear."""
    if not it.stops:
        return
    b = db.get(ApBatch, it.batch_id)
    typed = {uid for (uid,) in db.query(AuditLog.actor_id).filter(AuditLog.target == f"ap_item:{it.id}", AuditLog.actor_id.isnot(None),
                                                                 AuditLog.action.in_(("ap_capture_save", "ap_fix"))).all()}
    if not ((b is not None and b.user_id == user.id) or user.id in typed):
        return
    code = portfolio_of(db, it)
    if has_rows(db, code):
        raise Forbidden("You dropped or typed this file: a second person clears what the checks stopped.")
    log(db, "ap_self_clear", actor=user, target=f"ap_item:{it.id}", detail=f"{it.name}: cleared by the person who dropped or typed it ({code or 'no portfolio'} has no AP roles yet)"[:400])


def check_confirm(db: Session, user: User, kind: str, rid: int) -> None:
    """Confirming a vendor coding or a utility account people added (Needs you → confirm): never the person who coded or
    added it, admin or not."""
    if kind == "utilities":
        a = db.get(ApUtilityAccount, int(rid or 0))
        if a is None or a.source != "person":
            return
        who = {uid for (uid,) in db.query(AuditLog.actor_id).filter(AuditLog.target == f"ap_account:{a.account_key}", AuditLog.action == "ap_utility_account",
                                                                    AuditLog.actor_id.isnot(None)).order_by(AuditLog.id.desc()).limit(1).all()}
        if user.id in who or (not who and a.updated_by and a.updated_by == (user.name or user.email)):
            raise Forbidden("You added this account: a second admin confirms it.")
        return
    if kind != "vendors":
        return
    v = db.get(ApVendorCode, int(rid or 0))
    if v is None or not v.learned:
        return
    who = {uid for (uid,) in db.query(AuditLog.actor_id).filter(AuditLog.target == f"ap_vendor:{v.vendor_no}", AuditLog.action == "ap_vendor_learned",
                                                                AuditLog.actor_id.isnot(None)).order_by(AuditLog.id.desc()).limit(1).all()}
    if user.id in who or (not who and v.learned_by and v.learned_by == (user.name or user.email)):
        raise Forbidden("You coded this vendor: a second admin confirms it.")


# ---- Settings → AP people (admins) ---------------------------------------------------------------------------------------------
def people(db: Session) -> list[User]:
    """Everyone who can write in the AP Tool (and anyone holding a role row), by name."""
    from ..models import Tool, ToolAccess

    tool = db.query(Tool).filter(Tool.slug == SLUG).one_or_none()
    ids = {uid for (uid,) in db.query(ToolAccess.user_id).filter(ToolAccess.tool_id == tool.id).all()} if tool else set()
    ids |= {uid for (uid,) in db.query(ApRole.user_id).distinct().all()}
    users = db.query(User).filter(User.is_active.is_(True), User.is_axl.is_(False)).all()
    from ..security.roles import attach

    out = []
    for u in users:
        attach(db, u)
        if u.id in ids or u.is_admin:
            if u.role != "viewer":
                out.append(u)
    return sorted(out, key=lambda u: (u.name or u.email).lower())


def grid(db: Session) -> dict:
    """People × roles per portfolio, and which portfolios are still in bootstrap."""
    rows = db.query(ApRole).all()
    held: dict[int, dict[str, list[str]]] = {}
    for r in rows:
        held.setdefault(r.user_id, {}).setdefault(r.portfolio or "", []).append(r.role)
    ps = PF.portfolios(db)
    return {"roles": [{"key": k, "label": LABELS[k]} for k in ROLES],
            "portfolios": [{"code": "", "name": "Every portfolio"}] + [{"code": p.code, "name": p.name} for p in ps],
            "bootstrap": [p.code for p in ps if not has_rows(db, p.code)],
            "people": [{"id": u.id, "name": u.name or u.email, "email": u.email, "admin": bool(u.is_admin),
                        "roles": {k: sorted(v, key=ROLES.index) for k, v in held.get(u.id, {}).items()}} for u in people(db)]}


def set_roles(db: Session, actor: User, user_id: int, portfolio: str, roles: list[str]) -> dict:
    """An admin sets one person's roles in one portfolio ("" = every portfolio). Audited ap_role (added / removed)."""
    if not actor.is_admin:
        raise Forbidden("Only an admin gives AP roles.")
    u = db.get(User, int(user_id or 0))
    if u is None or u.is_axl:
        raise LookupError("No such person.")
    code = _code(portfolio)
    if code and PF.get(db, code) is None:
        raise PF.Refused(f"{code} is not a portfolio.")
    want = []
    for r in roles or []:
        r = str(r or "").strip()
        if r not in ROLES:
            raise PF.Refused(f"'{r}' is not an AP role ({', '.join(ROLES)}).")
        if r not in want:
            want.append(r)
    if want and not can_write(db, u):
        raise PF.Refused(f"{u.name or u.email} can't change things in the AP Tool: give them the AP Tool (not as a viewer) first.")
    have = {r.role: r for r in db.query(ApRole).filter(ApRole.user_id == u.id, ApRole.portfolio == code).all()}
    added = [r for r in want if r not in have]
    removed = [r for r in have if r not in want]
    for r in removed:
        db.delete(have[r])
    for r in added:
        db.add(ApRole(user_id=u.id, portfolio=code, role=r, granted_by=actor.name or actor.email))
    db.flush()
    if added or removed:
        log(db, "ap_role", actor=actor, target=f"user:{u.id}", detail=f"{u.name or u.email} in {code or 'every portfolio'}: "
            + "; ".join(filter(None, ("added " + ", ".join(added) if added else "", "removed " + ", ".join(removed) if removed else ""))))
    return {"user_id": u.id, "portfolio": code, "roles": sorted(want, key=ROLES.index), "added": added, "removed": removed}


def me_out(db: Session, user: Optional[User]) -> dict:
    """For the AP page: the person's roles per portfolio, and whether the Controls tab shows (admins, and people with a
    reviewer / approver / poster / reconciliation-reviewer row of their own)."""
    held = roles_of(db, user)
    own = explicit_roles(db, user)
    return {"roles": held, "controls": bool(user is not None and (user.is_admin or own & {"reviewer", "approver", "poster", "recon_reviewer"})),
            "labels": LABELS}
