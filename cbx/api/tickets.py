"""Build tickets: the unit admins work on — a request and the tool folder that answers it.

    working   the ticket exists; someone builds the tool in the Builder (pages, flows)
    testing   a version with it is in the sandbox, or the requester is trying it
    ready     the tool is live (promoted) and the requester has been told where it is

Every status change is mirrored onto the request so the requester sees the same word. Since 1.0.48 a
ticket points at a tool folder (`tool_slug`) instead of a Node-RED flow."""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import requests_flow as F
from ..audit import log
from ..db import get_db
from ..engine import loader
from ..models import Ticket, User
from ..security.deps import require_admin, require_human

router = APIRouter(prefix="/api/tickets", tags=["tickets"])
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,40}$")


def _get(db: Session, tid: int) -> Ticket:
    t = db.get(Ticket, tid)
    if t is None:
        raise HTTPException(404, "No such ticket.")
    return t


def _open(db: Session, tid: int) -> Ticket:
    """A ticket you may still work on. Cancelled/declined requests stay readable but frozen."""
    t = _get(db, tid)
    if F.ticket_closed(t):
        raise HTTPException(400, "This request was cancelled — its build can't be changed any more.")
    return t


def _join(t: Ticket, me: User) -> None:
    if me.id not in (t.workers or []):
        t.workers = (t.workers or []) + [me.id]


@router.get("")
def list_tickets(db: Session = Depends(get_db), me: User = Depends(require_admin)):
    rows = db.query(Ticket).order_by(Ticket.created_at.desc()).all()
    return {"tickets": [F.ticket_out(db, t) for t in rows]}


@router.get("/{tid}")
def get_ticket(tid: int, db: Session = Depends(get_db), me: User = Depends(require_admin)):
    t = _get(db, tid)
    d = F.ticket_out(db, t, full=True)
    d["tools"] = [{"slug": x.slug, "title": x.text(x.manifest.get("title", "tool.title"))} for x in loader.registry().all()]
    from .builder_api import _draft

    d["draft"] = bool(t.tool_slug) and _draft(t.tool_slug) is not None
    return d


class TicketIn(BaseModel):
    summary: Optional[str] = None
    tool_slug: Optional[str] = None


@router.patch("/{tid}")
def save(tid: int, body: TicketIn, db: Session = Depends(get_db), me: User = Depends(require_admin)):
    t = _open(db, tid)
    changes = []
    if body.summary is not None:
        t.summary = body.summary
        changes.append("summary")
    if body.tool_slug is not None:
        slug = body.tool_slug.strip().lower()
        if slug and not SLUG_RE.match(slug):
            raise HTTPException(400, "A tool's folder name is lowercase letters, digits and dashes, 2 to 40 long.")
        if slug != (t.tool_slug or ""):
            t.tool_slug = slug
            changes.append(f"tool → {slug or 'none'}")
            if t.status == "ready":
                F.set_ticket_status(db, t, "working", actor=me, detail="pointed at another tool")
    _join(t, me)
    log(db, "ticket_saved", actor=me, target=f"ticket:{t.id}", detail=", ".join(changes) or "no change")
    return {"ticket": F.ticket_out(db, t, full=True)}


@router.post("/{tid}/join")
def join(tid: int, db: Session = Depends(get_db), me: User = Depends(require_admin)):
    t = _get(db, tid)
    if me.id not in (t.workers or []):
        _join(t, me)
        log(db, "ticket_joined", actor=me, target=f"ticket:{t.id}")
    return {"ticket": F.ticket_out(db, t)}


class NoteIn(BaseModel):
    note: str = ""


@router.post("/{tid}/test")
def test(tid: int, body: Optional[NoteIn] = None, db: Session = Depends(get_db), me: User = Depends(require_admin)):
    """Testing: a version with the tool is in the sandbox, or the requester is trying it."""
    t = _open(db, tid)
    if t.status == "ready":
        raise HTTPException(400, "This is already live. Reopen it first to change it.")
    if not t.tool_slug:
        raise HTTPException(400, "Say which tool answers this request first (its folder name).")
    _join(t, me)
    note = (body.note if body else "").strip()
    t.last_test = {"note": note[:500], "by": me.name}
    F.set_ticket_status(db, t, "testing", actor=me, detail=note[:200])
    return {"ticket": F.ticket_out(db, t, full=True)}


@router.post("/{tid}/publish")
def publish(tid: int, db: Session = Depends(get_db), me: User = Depends(require_admin), _h: User = Depends(require_human)):
    """Live: the tool folder is in the running app. The requester is told where to find it."""
    t = _open(db, tid)
    if t.status == "ready":
        raise HTTPException(400, "Already live.")
    tool = loader.registry().tools.get(t.tool_slug or "")
    if tool is None:
        raise HTTPException(400, f"The tool '{t.tool_slug or '—'}' is not live yet — release it from the Builder and promote the version first.")
    F.set_ticket_status(db, t, "ready", actor=me, detail=f"tool {tool.slug}")
    req = t.request
    if req.origin == "admin":  # created directly by an admin — nobody is waiting for a message
        return {"ticket": F.ticket_out(db, t, full=True)}
    requester = db.get(User, req.user_id)
    where = f"#/tool?t={tool.slug}&p={tool.manifest.get('home') or next(iter(tool.pages), '')}" if tool.pages else "#/home"
    text = f"Your request “{req.title}” is live: {tool.text(tool.manifest.get('title', 'tool.title'))}. {F.wants_back(req)}. If something looks wrong, message me here."
    if req.interview:
        F.say(db, req.interview, text)
    F.notify(db, requester, f"Your request #{req.number} is live", text, link=where)
    return {"ticket": F.ticket_out(db, t, full=True)}


@router.post("/{tid}/reopen")
def reopen(tid: int, db: Session = Depends(get_db), me: User = Depends(require_admin)):
    t = _open(db, tid)
    if t.status == "working":
        raise HTTPException(400, "It is being worked on already.")
    F.set_ticket_status(db, t, "working", actor=me, detail="reopened")
    _join(t, me)
    return {"ticket": F.ticket_out(db, t, full=True)}
