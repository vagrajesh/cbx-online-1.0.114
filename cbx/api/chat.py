"""Chat API. The rules live in cbx/chat/service.py; this only checks who is asking and answers."""
from __future__ import annotations


from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..chat import service as C
from ..db import get_db
from ..models import Conversation, User
from ..security.deps import current_user, require_human

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _conv(db: Session, me: User, cid: int) -> Conversation:
    c = db.get(Conversation, cid)
    if c is None or not C.can_access(db, me, c):
        raise HTTPException(404, "That conversation isn't here.")  # never confirm one exists
    return c


@router.get("")
def inbox(db: Session = Depends(get_db), me: User = Depends(current_user)):
    return {
        "conversations": C.my_conversations(db, me),
        "people": [{"id": u.id, "name": u.name, "title": u.title or "", "team": u.team.name if u.team else "", "role": u.role} for u in C.people_for(db, me)],
        "can_make_rooms": bool(me.is_admin or me.is_manager),
        "me": me.id,
        "ai": C.branding.ai_name(),
    }


class DmIn(BaseModel):
    user_id: int


@router.post("/dm")
def start_dm(body: DmIn, db: Session = Depends(get_db), me: User = Depends(current_user), _h: User = Depends(require_human)):
    other = db.get(User, body.user_id)
    if other is None or not other.is_active:
        raise HTTPException(404, "That person isn't here.")
    try:
        c = C.get_or_create_dm(db, me, other)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return C.conversation_out(db, c, me)


class RoomIn(BaseModel):
    name: str
    member_ids: list[int] = []


@router.post("/rooms")
def make_room(body: RoomIn, db: Session = Depends(get_db), me: User = Depends(current_user), _h: User = Depends(require_human)):
    try:
        c = C.create_room(db, me, body.name, body.member_ids)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return C.conversation_out(db, c, me)


@router.get("/{cid}")
def thread(cid: int, after: int = 0, db: Session = Depends(get_db), me: User = Depends(current_user)):
    """The conversation, oldest first. `?after=<id>` returns only what arrived since — the page polls with it."""
    c = _conv(db, me, cid)
    rows = C.messages(db, me, c, after_id=after)
    people = C.members(db, c)
    names = {u.id: u.name for u in people}
    from ..cexa.brain import cexa_user

    ax = cexa_user(db)
    names[ax.id] = ax.name
    if rows:
        C.mark_read(db, me, c)
    out = {"messages": [C.message_out(m, names) for m in rows]}
    if not after:
        out["conversation"] = C.conversation_out(db, c, me)
        out["members"] = [{"id": u.id, "name": u.name, "title": u.title or ""} for u in people]
    return out


class SendIn(BaseModel):
    text: str


@router.post("/{cid}")
def send(cid: int, body: SendIn, db: Session = Depends(get_db), me: User = Depends(current_user), _h: User = Depends(require_human)):
    c = _conv(db, me, cid)
    try:
        m = C.send(db, me, c, body.text)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"message": C.message_out(m, {me.id: me.name}), "asked_axl": C.addressed_to_cexa(body.text) is not None}


