"""Who may see whose work — the one place that answers it.

The canvas draws this. It does not decide it. Every screen that shows one person's work to
another goes through `can_see()` or `visible_owner_ids()`, so the picture on the Org page and the
rows a person actually gets can never drift apart.

The order of the checks matters and is deliberate:

1. **Yourself** — always.
2. **Owner or admin** — they build for everyone; this is the existing model, unchanged.
3. **An explicit grant** — a wire someone drew on purpose, on this kind (or this one resource).
4. **The reporting line**, but only for kinds that are not sensitive. Your manager, and their
   manager, can see your folders without anyone drawing a wire. Nobody inherits your To-do.
5. Otherwise: no.

A peer never sees a peer. Reporting runs one way — upward — because "can see" following the
tree sideways is how an org chart quietly becomes a surveillance tool.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from ..models import AccessGrant, User
from ..models.org import KIND_KEYS, SENSITIVE

MAX_DEPTH = 64  # a reporting chain deeper than this is a cycle or a mistake; either way, stop


def managers_of(db: Session, user_id: int) -> list[int]:
    """Everyone above this person, nearest first. Stops on a cycle instead of hanging —
    `set_reports_to` refuses to create one, but a database edited by hand is not our promise."""
    out: list[int] = []
    seen = {user_id}
    cur: Optional[int] = db.query(User.reports_to_id).filter(User.id == user_id).scalar()
    while cur and cur not in seen and len(out) < MAX_DEPTH:
        out.append(cur)
        seen.add(cur)
        cur = db.query(User.reports_to_id).filter(User.id == cur).scalar()
    return out


def reports_of(db: Session, user_id: int, *, direct_only: bool = False) -> list[int]:
    """Everyone below this person. Breadth-first, cycle-guarded."""
    direct = [r[0] for r in db.query(User.id).filter(User.reports_to_id == user_id).all()]
    if direct_only:
        return direct
    out: list[int] = []
    seen = {user_id}
    queue = list(direct)
    while queue:
        uid = queue.pop(0)
        if uid in seen:
            continue
        seen.add(uid)
        out.append(uid)
        queue += [r[0] for r in db.query(User.id).filter(User.reports_to_id == uid).all()]
    return out


def can_see(db: Session, viewer: User, owner_id: int, kind: str, resource_id: str = "") -> bool:
    """May `viewer` see `owner_id`'s `kind` (optionally one named resource)?"""
    if kind not in KIND_KEYS:
        raise ValueError(f"unknown kind {kind!r}")
    if viewer.id == owner_id:
        return True
    if viewer.is_admin or viewer.is_axl:
        return True
    # a grant on the whole kind ("" ) covers every resource; a grant naming one covers only that one
    ids = {r[0] for r in db.query(AccessGrant.resource_id).filter(
        AccessGrant.viewer_id == viewer.id, AccessGrant.owner_id == owner_id, AccessGrant.kind == kind).all()}
    if "" in ids or (resource_id and resource_id in ids):
        return True
    if kind in SENSITIVE:
        return False  # the tree never grants this one — only a wire does
    return viewer.id in managers_of(db, owner_id)


def visible_owner_ids(db: Session, viewer: User, kind: str) -> Optional[set[int]]:
    """Whose work this person may see, for building a query filter.

    `None` means "everything" (owner/admin) — the caller should not filter at all. Otherwise a set
    that always contains the viewer themself.
    """
    if kind not in KIND_KEYS:
        raise ValueError(f"unknown kind {kind!r}")
    if viewer.is_admin or viewer.is_axl:
        return None
    out = {viewer.id}
    # ONLY whole-kind wires (resource_id == "") widen this set. A wire drawn to one folder must not
    # hand over the owner's other folders — a caller filtering by owner id alone cannot express
    # "this one resource", so those grants are answered by `resource_grants()` and `can_see()`.
    out.update(r[0] for r in db.query(AccessGrant.owner_id).filter(
        AccessGrant.viewer_id == viewer.id, AccessGrant.kind == kind, AccessGrant.resource_id == "").all())
    if kind not in SENSITIVE:
        out.update(reports_of(db, viewer.id))
    return out


def resource_grants(db: Session, viewer: User, kind: str) -> set[tuple[int, str]]:
    """The narrow wires: (owner_id, resource_id) pairs this person was given one at a time.

    A caller that filters by owner must union these in by resource, or the named folder someone was
    deliberately given stays invisible. `visible_owner_ids` deliberately leaves them out.
    """
    if kind not in KIND_KEYS:
        raise ValueError(f"unknown kind {kind!r}")
    if viewer.is_admin or viewer.is_axl:
        return set()
    return {(r[0], r[1]) for r in db.query(AccessGrant.owner_id, AccessGrant.resource_id).filter(
        AccessGrant.viewer_id == viewer.id, AccessGrant.kind == kind, AccessGrant.resource_id != "").all()}


def would_cycle(db: Session, user_id: int, new_manager_id: Optional[int]) -> bool:
    """Would making `new_manager_id` this person's manager make them their own boss?"""
    if new_manager_id is None:
        return False
    if new_manager_id == user_id:
        return True
    return user_id in managers_of(db, new_manager_id)


def set_reports_to(db: Session, user: User, new_manager_id: Optional[int]) -> None:
    """Move someone in the tree. Refuses a cycle; does NOT touch any grant.

    Moving a person changes what their new managers can see through rule 4 — folders, PDF work,
    compliance — and changes nothing about the sensitive kinds. That is the whole point of keeping
    the two apart: a reorganisation cannot quietly hand someone's private work to a new boss.
    """
    if would_cycle(db, user.id, new_manager_id):
        raise ValueError("That would make someone their own manager — pick someone outside their own reporting line.")
    user.reports_to_id = new_manager_id
    db.flush()


def explain(db: Session, viewer: User, owner_id: int, kind: str) -> str:
    """Why can (or can't) this person see that? Shown on the canvas when a wire is selected, so a
    permission is never a mystery."""
    if viewer.id == owner_id:
        return "Their own work."
    if viewer.is_axl:
        return "C-EXA reads everything she is asked to act on; every action she takes is in the audit trail."
    if viewer.is_admin:
        return f"{viewer.name} is an {viewer.role} — admins see everything."
    named = db.query(AccessGrant).filter(
        AccessGrant.viewer_id == viewer.id, AccessGrant.owner_id == owner_id, AccessGrant.kind == kind).all()
    # A whole-kind wire answers the broad question; narrow wires do NOT, and saying "a wire was
    # drawn" for them reads as a yes next to a can_see() of no. Answer the question that was asked.
    whole = next((g for g in named if not g.resource_id), None)
    if whole:
        return "A wire was drawn for this — everything of this kind." + (f" Note: {whole.note}" if whole.note else "")
    if named:
        only = ", ".join(sorted(g.resource_id for g in named))
        return f"Only {only} — a wire was drawn for that alone, not for the rest of this kind."
    if kind in SENSITIVE:
        return "No. This kind is private unless someone draws a wire — being someone's manager is not enough."
    chain = managers_of(db, owner_id)
    if viewer.id in chain:
        step = chain.index(viewer.id) + 1
        return "They report to them directly." if step == 1 else f"They are {step} steps up the reporting line."
    return "No. Not their manager, and no wire has been drawn."
