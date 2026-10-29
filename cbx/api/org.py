"""The org chart: the shape of the organisation, and the wires that grant access across it.

Two rules are built into every read here, because the canvas can only draw what this returns:

* **Never the whole company.** An answer is one root, a few levels under it, and the line of
  managers above it so a breadcrumb can be drawn. 1 900 people on one canvas is not a picture
  anyone can read, and not a page any browser can draw.
* **A manager sees their own part of the chart.** Owners and admins build for everyone; a manager
  looks at themselves and the people below them, which is what they are answerable for.

Writes are admin-and-a-person work, with one deliberate exception: a manager may draw and remove
wires where BOTH ends sit inside their own reporting line — the team lead who hands one of their
own people a folder should not have to queue for an admin.

Who may see what is NOT decided here. `cbx.org.access` answers that, and every screen asks it the
same way, so the picture on this page and the rows a person actually gets can never drift apart.
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..audit import log
from ..db import get_db
from ..models import AccessGrant, OrgLayout, Team, Tool, User
from ..models.core import ROLES
from ..models.org import KIND_KEYS, KINDS
from ..org import access as A
from ..security.deps import client_ip, has_tool, require_admin, require_human, require_manager
from ..warehouse.folders import ensure_person_folders, ensure_team_folder
from .schemas import iso

router = APIRouter(prefix="/api/org", tags=["org"])
# Four levels is about as much as fits on a canvas and still reads as a chart. Deeper than that is
# a new root, not a bigger picture — so a request for more is quietly brought back to this.
MAX_GRAPH_DEPTH = 4


# ---- helpers ---------------------------------------------------------------------------------------
def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _kind_list() -> str:
    return ", ".join(f"{k} ({v['label']})" for k, v in KINDS.items())


def _node(u: User, layout: Optional[OrgLayout], direct_reports: int) -> dict:
    return {"id": u.id, "name": u.name, "title": u.title, "email": u.email, "role": u.role, "initials": u.initials(), "is_axl": u.is_axl, "team": {"id": u.team.id, "name": u.team.name} if u.team else None, "reports_to_id": u.reports_to_id, "direct_reports": direct_reports, "x": layout.x if layout else 0.0, "y": layout.y if layout else 0.0, "collapsed": bool(layout.collapsed) if layout else False}


def _nodes(db: Session, ids: list[int]) -> list[dict]:
    """Nodes for these people, in the order asked for.

    `direct_reports` counts EVERY direct report, not only the ones in this answer — that is how the
    canvas knows to offer "and 3 more below" on a node it has cut off at the depth limit."""
    if not ids:
        return []
    users = {u.id: u for u in db.query(User).filter(User.id.in_(ids)).all()}
    layouts = {row.user_id: row for row in db.query(OrgLayout).filter(OrgLayout.user_id.in_(ids)).all()}
    counts = dict(db.query(User.reports_to_id, func.count(User.id)).filter(User.reports_to_id.in_(ids)).group_by(User.reports_to_id).all())
    return [_node(users[i], layouts.get(i), counts.get(i, 0)) for i in ids if i in users]


def _grants_out(db: Session, grants: list[AccessGrant]) -> list[dict]:
    ids = {g.granted_by_id for g in grants if g.granted_by_id}
    who = {u.id: u.name for u in db.query(User).filter(User.id.in_(ids)).all()} if ids else {}
    return [{"id": g.id, "owner_id": g.owner_id, "viewer_id": g.viewer_id, "kind": g.kind, "resource_id": g.resource_id, "note": g.note, "granted_by": who.get(g.granted_by_id or 0, ""), "at": iso(g.created_at)} for g in grants]


def _person(db: Session, uid: int) -> User:
    u = db.get(User, uid)
    if u is None:
        raise HTTPException(404, "That person isn't here any more. Refresh the page and try again.")
    return u


def _descendants(db: Session, root_id: int, depth: int) -> list[int]:
    """Everyone under this person, but only `depth` levels down, breadth-first and cycle-guarded."""
    out: list[int] = []
    seen = {root_id}
    level = [root_id]
    for _ in range(depth):
        if not level:
            break
        kids = [r[0] for r in db.query(User.id).filter(User.reports_to_id.in_(level)).order_by(User.name).all() if r[0] not in seen]
        seen.update(kids)
        out += kids
        level = kids
    return out


def _my_subtree(db: Session, me: User) -> set[int]:
    """A manager's own part of the chart: themselves and everyone below them."""
    return {me.id, *A.reports_of(db, me.id)}


def _check_can_open(db: Session, me: User, person_id: int, request: Request) -> None:
    """Owners and admins open any part of the chart. A manager opens their own part of it, and
    reaching outside it is a blocked action — so it goes in the audit trail like every other one."""
    if me.is_admin:
        return
    if person_id not in _my_subtree(db, me):
        log(db, "org_scope_blocked", actor=me, target=f"user:{person_id}", detail="tried to open part of the chart outside their own reporting line", outcome="blocked", ip=client_ip(request))
        raise HTTPException(403, "You can only see your own part of the chart — open it on yourself or on someone who reports to you.")


def _can_edit(db: Session, me: User, person_id: int) -> bool:
    """What the canvas should offer, never what is allowed: admins change anything, and a manager
    may wire up their own team, so the tools stay on for the part of the chart they answer for.
    Every write checks the real rule again for itself."""
    return bool(me.is_admin or person_id in _my_subtree(db, me))


def _check_can_wire(db: Session, me: User, owner: User, viewer: User, request: Request, kind: str = "") -> None:
    """Admins draw wires anywhere. A manager is the sub-admin of their own team: both ends have to
    sit inside it, or "sharing" becomes a way to hand a stranger somebody else's work."""
    if me.is_admin:
        return
    # A manager may wire two of their people together. They may NOT wire someone's work to
    # themselves: "both ends in my subtree" includes me, which would let any manager hand themselves
    # a report's To-do — the one thing the tree refuses on purpose. Helping yourself to work that was
    # kept private is not a sharing decision, so it goes to an owner or admin, where it is logged as
    # theirs. The ordinary kinds they already have through the reporting line, so nothing is lost.
    if viewer.id == me.id:
        log(db, "org_grant_blocked", actor=me, target=owner.email, detail=f"tried to give themselves {KINDS[kind]['label'] if kind in KINDS else kind}", outcome="blocked", ip=client_ip(request))
        raise HTTPException(403, f"You can't draw a wire to yourself. {owner.name}'s manager can already see the shared kinds; anything private has to be granted by an owner or admin, so it is on the record as their decision.")
    mine = _my_subtree(db, me)
    if owner.id not in mine or viewer.id not in mine:
        log(db, "org_grant_blocked", actor=me, target=owner.email, detail=f"tried to give {viewer.name} access outside their own reporting line", outcome="blocked", ip=client_ip(request))
        raise HTTPException(403, "You can only share work between people who report to you — both ends of the wire have to be in your own part of the chart.")


# ---- the canvas ------------------------------------------------------------------------------------
def _root_id(me: User, root: str) -> int:
    if root in ("", "me"):
        return me.id
    if not root.isdigit():
        raise HTTPException(400, "I don't know whose chart to draw — ask for 'me' or for a person's id.")
    return int(root)


@router.get("/graph")
def graph(request: Request, root: str = Query("me"), depth: int = Query(2), db: Session = Depends(get_db), me: User = Depends(require_manager)):
    root_id = _root_id(me, root)
    _check_can_open(db, me, root_id, request)
    _person(db, root_id)  # 404 before anything is drawn, rather than an empty canvas
    depth = max(0, min(depth, MAX_GRAPH_DEPTH))
    above = A.managers_of(db, root_id)  # nearest first; reversed below so the breadcrumb reads top-down
    ids = [*reversed(above), root_id, *_descendants(db, root_id, depth)]
    nodes = _nodes(db, ids)
    shown = [n["id"] for n in nodes]
    on_canvas = set(shown)
    grants = db.query(AccessGrant).filter(or_(AccessGrant.owner_id.in_(shown), AccessGrant.viewer_id.in_(shown))).order_by(AccessGrant.id).all() if shown else []
    if not me.is_admin:
        # The breadcrumb above a manager is there to be walked, not to show who shares what with the
        # people over them — so they get the wires that touch their own part of the chart, and no others.
        mine = _my_subtree(db, me)
        grants = [g for g in grants if g.owner_id in mine or g.viewer_id in mine]
    return {
        "root_id": root_id,
        "nodes": nodes,
        "edges": [{"from": n["reports_to_id"], "to": n["id"]} for n in nodes if n["reports_to_id"] in on_canvas],
        "grants": _grants_out(db, grants),
        "kinds": [{"key": k, "label": v["label"], "sensitive": v["sensitive"], "hint": v["hint"]} for k, v in KINDS.items()],
        "can_edit": _can_edit(db, me, root_id),
    }


@router.get("/roots")
def roots(db: Session = Depends(get_db), me: User = Depends(require_manager)):
    """Where the canvas starts. For an admin that is everyone nobody reports to; for a manager it is
    themselves — handing them a root they would only be refused on is not a starting point."""
    if me.is_team_limited:
        return {"roots": _nodes(db, [me.id])}
    ids = [r[0] for r in db.query(User.id).filter(User.reports_to_id.is_(None)).order_by(User.name).all()]
    return {"roots": _nodes(db, ids)}


@router.get("/search")
def search(q: str = Query(""), limit: int = Query(20), db: Session = Depends(get_db), me: User = Depends(require_manager)):
    term = q.strip()
    if not term:
        return {"people": []}
    like = f"%{term.lower()}%"
    rows = db.query(User).filter(or_(func.lower(User.name).like(like), func.lower(User.email).like(like)))
    if not me.is_admin:
        rows = rows.filter(User.id.in_(list(_my_subtree(db, me))))  # a manager searches their own part of the chart
    rows = rows.order_by(User.name).limit(max(1, min(limit, 100))).all()
    return {"people": [{"id": u.id, "name": u.name, "title": u.title, "email": u.email, "team": u.team.name if u.team else None, "reports_to_id": u.reports_to_id} for u in rows]}


# ---- one person ------------------------------------------------------------------------------------
@router.get("/people/{uid}")
def get_person(uid: int, request: Request, db: Session = Depends(get_db), me: User = Depends(require_manager)):
    _check_can_open(db, me, uid, request)
    u = _person(db, uid)
    mgr = db.get(User, u.reports_to_id) if u.reports_to_id else None
    reports = [r[0] for r in db.query(User.id).filter(User.reports_to_id == u.id).order_by(User.name).all()]
    out_wires = db.query(AccessGrant).filter(AccessGrant.owner_id == u.id).order_by(AccessGrant.id).all()
    in_wires = db.query(AccessGrant).filter(AccessGrant.viewer_id == u.id).order_by(AccessGrant.id).all()
    tools = db.query(Tool).filter(Tool.is_active.is_(True)).order_by(Tool.sort).all()
    return {
        "person": _nodes(db, [u.id])[0],
        "manager": {"id": mgr.id, "name": mgr.name} if mgr else None,
        "reports": _nodes(db, reports),
        "grants_out": _grants_out(db, out_wires),  # their work, seen by other people
        "grants_in": _grants_out(db, in_wires),  # other people's work, seen by them
        "tools": [{"id": t.id, "slug": t.slug, "name": t.name, "granted": has_tool(db, u, t.slug)} for t in tools],
        "can_edit": _can_edit(db, me, u.id),
    }


class PersonPatch(BaseModel):
    reports_to_id: Optional[int] = None
    role: Optional[str] = None
    team_id: Optional[int] = None
    title: Optional[str] = None


@router.patch("/people/{uid}")
def patch_person(uid: int, body: PersonPatch, request: Request, db: Session = Depends(get_db), me: User = Depends(require_admin), _h: User = Depends(require_human)):
    u = _person(db, uid)
    if u.is_axl:
        raise HTTPException(400, "C-EXA isn't a person on the chart — her account can't be moved or renamed.")
    # `reports_to_id: null` means "nobody above them"; leaving the field out means "don't touch it".
    # Without this the two are the same value and dragging a node would quietly unseat its manager.
    sent = body.model_fields_set
    # validate everything first, change nothing until it all checks out
    if body.role and body.role != u.role:
        if body.role not in ROLES:
            raise HTTPException(400, "That role doesn't exist.")
        if not me.can_manage_user(u):
            log(db, "org_role_blocked", actor=me, target=u.email, detail=f"{u.role} → {body.role}", outcome="blocked", ip=client_ip(request))
            raise HTTPException(403, "You can't manage this account.")
        if not me.can_grant_role(body.role):
            log(db, "org_role_blocked", actor=me, target=u.email, detail=f"{u.role} → {body.role}", outcome="blocked", ip=client_ip(request))
            raise HTTPException(403, f"You can't make someone {body.role}.")
        if u.id == me.id and body.role != "owner":
            raise HTTPException(400, "You can't remove your own owner role.")
    if "team_id" in sent and body.team_id is not None and db.get(Team, body.team_id) is None:
        raise HTTPException(400, "That team doesn't exist any more. Refresh the page and try again.")
    if "reports_to_id" in sent and body.reports_to_id is not None:
        _person(db, body.reports_to_id)
    changes = []
    if body.title is not None and body.title.strip() != u.title:
        changes.append(f"title → {body.title.strip()}")
        u.title = body.title.strip()
    if body.role and body.role != u.role:
        changes.append(f"role {u.role} → {body.role}")
        u.role = body.role
    if "team_id" in sent and body.team_id != u.team_id:
        team = db.get(Team, body.team_id) if body.team_id else None
        changes.append(f"team → {team.name if team else 'none'}")
        u.team_id = body.team_id
        ensure_person_folders(db, u)  # their folders follow them, exactly as they do in Users & roles
    if "reports_to_id" in sent and body.reports_to_id != u.reports_to_id:
        # names, not ids: the audit trail is read by people, and "reports to 41 → 17" tells them nothing
        was = db.get(User, u.reports_to_id) if u.reports_to_id else None
        now = db.get(User, body.reports_to_id) if body.reports_to_id else None
        try:
            A.set_reports_to(db, u, body.reports_to_id)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        changes.append(f"reports to {was.name if was else 'nobody'} → {now.name if now else 'nobody'}")
    db.flush()
    log(db, "org_person_changed", actor=me, target=u.email, detail="; ".join(changes) or "nothing changed", ip=client_ip(request))
    return {"person": _nodes(db, [u.id])[0]}


# ---- wires -----------------------------------------------------------------------------------------
class GrantIn(BaseModel):
    owner_id: int
    viewer_id: int
    kind: str
    resource_id: str = ""
    note: str = ""


@router.post("/grants")
def add_grant(body: GrantIn, request: Request, db: Session = Depends(get_db), me: User = Depends(require_manager), _h: User = Depends(require_human)):
    if body.kind not in KIND_KEYS:
        raise HTTPException(400, f"'{body.kind}' isn't something that can be shared. Pick one of: {_kind_list()}.")
    owner, viewer = _person(db, body.owner_id), _person(db, body.viewer_id)
    if owner.id == viewer.id:
        raise HTTPException(400, "That wire runs from someone back to themselves — everybody can already see their own work. Pick a different person on one end.")
    _check_can_wire(db, me, owner, viewer, request, body.kind)
    resource_id = body.resource_id.strip()
    if db.query(AccessGrant).filter_by(owner_id=owner.id, viewer_id=viewer.id, kind=body.kind, resource_id=resource_id).count():
        raise HTTPException(400, f"{viewer.name} can already see this — that wire is on the canvas already.")
    g = AccessGrant(owner_id=owner.id, viewer_id=viewer.id, kind=body.kind, resource_id=resource_id, note=body.note.strip(), granted_by_id=me.id)
    db.add(g)
    db.flush()
    log(db, "org_grant_added", actor=me, target=owner.email, detail=f"{viewer.name} may see {KINDS[g.kind]['label']}" + (f" ({g.resource_id})" if g.resource_id else "") + (f" — {g.note}" if g.note else ""), ip=client_ip(request))
    return {"grant": _grants_out(db, [g])[0]}


@router.delete("/grants/{gid}")
def remove_grant(gid: int, request: Request, db: Session = Depends(get_db), me: User = Depends(require_manager), _h: User = Depends(require_human)):
    g = db.get(AccessGrant, gid)
    if g is None:
        raise HTTPException(404, "That wire is already gone — somebody else may have removed it. Refresh the page.")
    owner, viewer = _person(db, g.owner_id), _person(db, g.viewer_id)
    _check_can_wire(db, me, owner, viewer, request)
    log(db, "org_grant_removed", actor=me, target=owner.email, detail=f"{viewer.name} can no longer see {KINDS[g.kind]['label'] if g.kind in KINDS else g.kind}", ip=client_ip(request))
    db.delete(g)
    db.flush()
    return {"ok": True, "deleted": gid}


@router.get("/explain")
def explain(viewer_id: int, owner_id: int, kind: str, db: Session = Depends(get_db), me: User = Depends(require_manager)):
    """Why can (or can't) she see that? The same answer the rest of the app acts on — never a
    second opinion written for the picture."""
    if kind not in KIND_KEYS:
        raise HTTPException(400, f"'{kind}' isn't something that can be shared. Pick one of: {_kind_list()}.")
    viewer, owner = _person(db, viewer_id), _person(db, owner_id)
    return {"allowed": A.can_see(db, viewer, owner.id, kind), "why": A.explain(db, viewer, owner.id, kind)}


# ---- teams -----------------------------------------------------------------------------------------
def _team_out(t: Team, members: int) -> dict:
    return {"id": t.id, "name": t.name, "slug": t.slug, "parent_id": t.parent_id, "lead_id": t.lead_id, "member_count": members}


def _member_counts(db: Session) -> dict[int, int]:
    return dict(db.query(User.team_id, func.count(User.id)).filter(User.team_id.isnot(None)).group_by(User.team_id).all())


def _team_ancestors(db: Session, team_id: Optional[int]) -> list[int]:
    """Every team above this one. Cycle-guarded: hand-edited data is not our promise to keep."""
    out: list[int] = []
    seen: set[int] = set()
    cur = team_id
    while cur and cur not in seen:
        out.append(cur)
        seen.add(cur)
        cur = db.query(Team.parent_id).filter(Team.id == cur).scalar()
    return out


def _check_team_parent(db: Session, team_id: Optional[int], parent_id: Optional[int]) -> None:
    if parent_id is None:
        return
    if db.get(Team, parent_id) is None:
        raise HTTPException(400, "That parent team doesn't exist any more. Refresh the page and try again.")
    if team_id is not None and (parent_id == team_id or team_id in _team_ancestors(db, parent_id)):
        raise HTTPException(400, "That would put the team inside itself — pick a team that isn't already underneath this one.")


@router.get("/teams")
def list_teams(db: Session = Depends(get_db), me: User = Depends(require_manager)):
    counts = _member_counts(db)
    return {"teams": [_team_out(t, counts.get(t.id, 0)) for t in db.query(Team).order_by(Team.name).all()]}


class TeamIn(BaseModel):
    name: str
    parent_id: Optional[int] = None
    lead_id: Optional[int] = None


@router.post("/teams")
def create_team(body: TeamIn, request: Request, db: Session = Depends(get_db), me: User = Depends(require_admin), _h: User = Depends(require_human)):
    name = body.name.strip()[:120]
    if not name or not _slug(name):
        raise HTTPException(400, "Give the team a name with letters or digits in it.")
    if db.query(Team).filter(Team.slug == _slug(name)).count():
        raise HTTPException(400, "That team already exists.")
    _check_team_parent(db, None, body.parent_id)
    if body.lead_id is not None:
        _person(db, body.lead_id)
    t = Team(name=name, slug=_slug(name), parent_id=body.parent_id, lead_id=body.lead_id)
    db.add(t)
    db.flush()
    ensure_team_folder(db, t)  # a team made on the chart gets its folder like one made in Users & roles
    log(db, "org_team_created", actor=me, target=name, detail=f"parent={body.parent_id or 'none'} lead={body.lead_id or 'none'}", ip=client_ip(request))
    return {"team": _team_out(t, 0)}


class TeamPatch(BaseModel):
    name: Optional[str] = None
    parent_id: Optional[int] = None
    lead_id: Optional[int] = None


@router.patch("/teams/{tid}")
def patch_team(tid: int, body: TeamPatch, request: Request, db: Session = Depends(get_db), me: User = Depends(require_admin), _h: User = Depends(require_human)):
    t = db.get(Team, tid)
    if t is None:
        raise HTTPException(404, "That team isn't here any more. Refresh the page and try again.")
    sent = body.model_fields_set  # null means "no parent" / "no lead"; leaving it out means "don't touch it"
    if "parent_id" in sent:
        _check_team_parent(db, t.id, body.parent_id)
    if "lead_id" in sent and body.lead_id is not None:
        _person(db, body.lead_id)
    changes = []
    if body.name is not None and body.name.strip() and body.name.strip() != t.name:
        # the slug stays as it was: folders and row rules are keyed on it, and a rename is a label
        changes.append(f"name {t.name} → {body.name.strip()}")
        t.name = body.name.strip()[:120]
    if "parent_id" in sent and body.parent_id != t.parent_id:
        changes.append(f"parent → {body.parent_id or 'none'}")
        t.parent_id = body.parent_id
    if "lead_id" in sent and body.lead_id != t.lead_id:
        changes.append(f"lead → {body.lead_id or 'none'}")
        t.lead_id = body.lead_id
    db.flush()
    log(db, "org_team_changed", actor=me, target=t.name, detail="; ".join(changes) or "nothing changed", ip=client_ip(request))
    return {"team": _team_out(t, _member_counts(db).get(t.id, 0))}


# ---- where the nodes sit ---------------------------------------------------------------------------
class PositionIn(BaseModel):
    user_id: int
    x: float = 0.0
    y: float = 0.0
    collapsed: bool = False


class LayoutIn(BaseModel):
    positions: list[PositionIn] = []


@router.put("/layout")
def save_layout(body: LayoutIn, request: Request, db: Session = Depends(get_db), me: User = Depends(require_admin), _h: User = Depends(require_human)):
    """Where each node sits. Presentation only — a layout grants nothing and hides nothing."""
    ids = [p.user_id for p in body.positions]
    rows = {row.user_id: row for row in db.query(OrgLayout).filter(OrgLayout.user_id.in_(ids)).all()} if ids else {}
    known = {r[0] for r in db.query(User.id).filter(User.id.in_(ids)).all()} if ids else set()
    saved = 0
    for p in body.positions:
        if p.user_id not in known:
            continue  # a node for somebody who has since gone: drop that one rather than refuse the whole drag
        row = rows.get(p.user_id)
        if row is None:
            row = OrgLayout(user_id=p.user_id)
            db.add(row)
            rows[p.user_id] = row
        row.x, row.y, row.collapsed = p.x, p.y, 1 if p.collapsed else 0
        saved += 1
    db.flush()
    log(db, "org_layout_saved", actor=me, target="org chart", detail=f"{saved} node{'s' if saved != 1 else ''} placed", ip=client_ip(request))
    return {"ok": True, "saved": saved}
