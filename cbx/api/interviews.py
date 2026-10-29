"""Interviews: C-EXA's message thread with the requester (mode axl) or the admin's meeting page
(mode human). Both end the same way — a ticket."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import requests_flow as F
from ..audit import log
from ..db import get_db
from ..models import CexaMessage, Interview, Request, User
from ..security.deps import current_user, require_admin
from .schemas import iso

router = APIRouter(prefix="/api/interviews", tags=["interviews"])


def _get(db: Session, iid: int, me: User) -> Interview:
    iv = db.get(Interview, iid)
    if iv is None or (not me.is_admin and not me.is_axl and iv.request.user_id != me.id):
        raise HTTPException(404, "That conversation isn't here, or it isn't yours.")
    return iv


@router.get("")
def my_conversations(db: Session = Depends(get_db), me: User = Depends(current_user)):
    """Left-hand list on Messages: interviews on my requests (everything, for admins), plus C-EXA's notes to me."""
    q = db.query(Interview).join(Request, Request.id == Interview.request_id)
    if not me.is_admin:
        q = q.filter(Request.user_id == me.id)
    ivs = q.order_by(Interview.started_at.desc()).all()
    notes = db.query(CexaMessage).filter(CexaMessage.user_id == me.id, CexaMessage.cleared.is_(False)).order_by(CexaMessage.at.desc()).limit(30).all()
    return {
        "interviews": [F.interview_out(db, iv, with_messages=False) for iv in ivs],
        "notes": [{"id": m.id, "at": iso(m.at), "title": m.title, "text": m.text, "link": m.link, "read": m.read, "from_axl": m.from_axl} for m in reversed(notes)],
    }


@router.get("/{iid}")
def get_interview(iid: int, db: Session = Depends(get_db), me: User = Depends(current_user)):
    iv = _get(db, iid, me)
    return {"interview": F.interview_out(db, iv), "request": F.request_out(db, iv.request, full=True, admin=me.is_admin)}


class ReplyIn(BaseModel):
    text: str


@router.post("/{iid}/reply")
def reply(iid: int, body: ReplyIn, db: Session = Depends(get_db), me: User = Depends(current_user)):
    iv = _get(db, iid, me)
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Type a message first.")
    if iv.request.user_id != me.id and not me.is_admin:
        raise HTTPException(403, "Only the person who asked can answer here.")
    if iv.request.status in ("cancelled", "declined"):
        raise HTTPException(400, f"This request was {iv.request.status}, so the conversation is closed. Send a new request if you still need this.")
    if iv.mode == "human" and iv.status == "open":
        raise HTTPException(400, "This request is being worked out in a meeting — the team will come back to you.")
    sender = "user" if iv.request.user_id == me.id else "admin"
    F.say(db, iv, text, sender=sender, sender_id=me.id)
    log(db, "interview_reply", actor=me, target=f"interview:{iv.id}", detail=text[:160])
    if sender == "admin":
        return {"message": None, "done": iv.status == "done", "rules": [F.rule_out(r) for r in iv.request.rules if r.active], "ticket_id": iv.request.ticket.id if iv.request.ticket else None}
    return F.cexa_turn(db, iv, user=me)


# ---- meeting mode (admin) ---------------------------------------------------------------
@router.get("/{iid}/suggest")
def suggest(iid: int, db: Session = Depends(get_db), me: User = Depends(require_admin)):
    iv = _get(db, iid, me)
    return F.suggest(db, iv)


class AskedIn(BaseModel):
    text: str
    skipped: bool = False


@router.post("/{iid}/asked")
def asked(iid: int, body: AskedIn, db: Session = Depends(get_db), me: User = Depends(require_admin)):
    iv = _get(db, iid, me)
    iv.plan = (iv.plan or []) + [{"q": body.text.strip(), "asked": not body.skipped, "by": me.name}]
    return {"plan": iv.plan}


@router.post("/{iid}/finish")
def finish(iid: int, db: Session = Depends(get_db), me: User = Depends(require_admin)):
    iv = _get(db, iid, me)
    if iv.request.status in ("cancelled", "declined"):
        raise HTTPException(400, f"This request was {iv.request.status} — no ticket can be created from it.")
    if iv.status == "done":
        raise HTTPException(400, "This interview is already finished.")
    if not any(r.active for r in iv.request.rules):
        raise HTTPException(400, "Capture at least one rule before creating the ticket.")
    t = F.finish_human(db, iv, actor=me)
    return {"ticket": F.ticket_out(db, t), "interview": F.interview_out(db, iv)}


class AskIn(BaseModel):
    text: str


@router.post("/{iid}/ask")
def ask_requester(iid: int, body: AskIn, db: Session = Depends(get_db), me: User = Depends(require_admin)):
    """From the flow page: 'Ask <first name>' — C-EXA relays the builder's question into the thread."""
    iv = _get(db, iid, me)
    if len(body.text.strip()) < 3:
        raise HTTPException(400, "Type the question first.")
    if iv.request.status in ("cancelled", "declined"):
        raise HTTPException(400, f"This request was {iv.request.status} — there's nobody waiting on it any more.")
    m = F.relay_question(db, iv, admin=me, text=body.text)
    return {"message": F.message_out(m)}
