"""Who may talk to whom, who may read what, and how C-EXA joins in.

The rules, so they are in one place:

* You can message the people you would recognise in the corridor: your own team, and every admin
  and owner (so anyone can reach the people who run the place). Admins can message anyone.
* A DM is read by its two people and nobody else — not an admin, not the owner. The audit trail
  records that a conversation exists and that messages were sent, never their text.
* A team room belongs to the team: being on the team is being in the room. Admins may read team
  rooms, because they manage teams. Leaving the team leaves the room.
* A named room is whoever was put in it, by an admin or a manager.
* C-EXA speaks when spoken to. Address her ("@C-EXA …", or start with her name) and she answers in the
  room, seeing exactly what the person who asked may see — never more. She does not read along
  silently and volunteer; a room full of colleagues is not a place for an AI to interrupt.
* Nothing is edited or deleted. Like the audit trail: what was said was said.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .. import branding, jobs
from ..audit import log
from ..db import SessionLocal, after_commit, utcnow
from ..models import ChatMessage, Conversation, ConversationMember, Team, User

MAX_LEN = 4000


# ---- who -------------------------------------------------------------------------------------------

def people_for(db: Session, me: User) -> list[User]:
    """Colleagues this person may start a conversation with."""
    q = db.query(User).filter(User.is_active.is_(True), User.is_axl.is_(False), User.id != me.id)
    if not me.is_admin:
        conds = [User.role.in_(("owner", "admin"))]
        if me.team_id:
            conds.append(User.team_id == me.team_id)
        q = q.filter(or_(*conds))
    return q.order_by(User.name).all()


def can_message(db: Session, me: User, other: User) -> bool:
    return any(p.id == other.id for p in people_for(db, me))


# ---- conversations ---------------------------------------------------------------------------------

def _dm_key(a: int, b: int) -> str:
    return f"{min(a, b)}:{max(a, b)}"


def get_or_create_dm(db: Session, me: User, other: User) -> Conversation:
    if other.id == me.id or other.is_axl:
        raise ValueError("Pick a colleague.")
    if not can_message(db, me, other):
        raise ValueError(f"You can message people on your own team and the admins. {other.name.split()[0]} is neither.")
    key = _dm_key(me.id, other.id)
    c = db.query(Conversation).filter(Conversation.kind == "dm", Conversation.dm_key == key).one_or_none()
    if c is None:
        c = Conversation(kind="dm", dm_key=key, created_by_id=me.id)
        db.add(c)
        db.flush()
        db.add(ConversationMember(conversation_id=c.id, user_id=me.id, last_read_at=utcnow()))
        db.add(ConversationMember(conversation_id=c.id, user_id=other.id))
        db.flush()
        log(db, "chat_started", actor=me, target=other.name, detail="direct message")
    return c


def team_room(db: Session, team: Team) -> Conversation:
    c = db.query(Conversation).filter(Conversation.kind == "team", Conversation.team_id == team.id).one_or_none()
    if c is None:
        c = Conversation(kind="team", team_id=team.id, name=team.name)
        db.add(c)
        db.flush()
    elif c.name != team.name:
        c.name = team.name
    return c


def create_room(db: Session, me: User, name: str, member_ids: list[int]) -> Conversation:
    name = re.sub(r"\s+", " ", name or "").strip()[:120]
    if not name:
        raise ValueError("Give the room a name.")
    if not (me.is_admin or me.is_manager):
        raise ValueError("Admins and managers make rooms. Anyone can start a direct message.")
    allowed = {p.id for p in people_for(db, me)} | {me.id}
    ids = [i for i in dict.fromkeys(member_ids) if i in allowed]
    if len(ids) < 1:
        raise ValueError("Add at least one colleague you can message.")
    c = Conversation(kind="room", name=name, created_by_id=me.id)
    db.add(c)
    db.flush()
    for uid in dict.fromkeys([me.id, *ids]):
        db.add(ConversationMember(conversation_id=c.id, user_id=uid, last_read_at=utcnow() if uid == me.id else None))
    db.flush()
    log(db, "chat_room_created", actor=me, target=name, detail=f"{len(ids)} member(s)")
    return c


def can_access(db: Session, me: User, c: Conversation) -> bool:
    if c.kind == "team":
        return bool(c.team_id) and (c.team_id == me.team_id or me.is_admin)
    return db.query(ConversationMember).filter(ConversationMember.conversation_id == c.id, ConversationMember.user_id == me.id).count() > 0


def _member_row(db: Session, me: User, c: Conversation) -> ConversationMember:
    """The person's membership row; created on first open for team rooms (membership is the team)."""
    m = db.query(ConversationMember).filter(ConversationMember.conversation_id == c.id, ConversationMember.user_id == me.id).one_or_none()
    if m is None:
        m = ConversationMember(conversation_id=c.id, user_id=me.id)
        db.add(m)
        db.flush()
    return m


def members(db: Session, c: Conversation) -> list[User]:
    if c.kind == "team":
        return db.query(User).filter(User.team_id == c.team_id, User.is_active.is_(True), User.is_axl.is_(False)).order_by(User.name).all()
    ids = [m.user_id for m in db.query(ConversationMember).filter(ConversationMember.conversation_id == c.id).all()]
    return db.query(User).filter(User.id.in_(ids)).order_by(User.name).all() if ids else []


def my_conversations(db: Session, me: User) -> list[dict]:
    """Everything this person is in, newest activity first, with a preview and an unread count."""
    out: list[Conversation] = []
    if me.team_id:
        team = db.get(Team, me.team_id)
        if team is not None:
            out.append(team_room(db, team))
    mine = db.query(Conversation).join(ConversationMember, ConversationMember.conversation_id == Conversation.id).filter(ConversationMember.user_id == me.id, Conversation.kind != "team").all()
    out.extend(mine)
    seen, uniq = set(), []
    for c in out:
        if c.id not in seen:
            seen.add(c.id)
            uniq.append(c)
    reads = {m.conversation_id: m.last_read_at for m in db.query(ConversationMember).filter(ConversationMember.user_id == me.id).all()}
    rows = []
    for c in uniq:
        last = db.query(ChatMessage).filter(ChatMessage.conversation_id == c.id).order_by(ChatMessage.id.desc()).first()
        since = reads.get(c.id)
        q = db.query(func.count(ChatMessage.id)).filter(ChatMessage.conversation_id == c.id, ChatMessage.sender_id != me.id)
        if since is not None:
            q = q.filter(ChatMessage.at > since)
        unread = q.scalar() or 0
        rows.append({**conversation_out(db, c, me), "unread": int(unread),
                     "last": {"text": last.text[:120], "at": last.at.isoformat(), "sender_id": last.sender_id, "kind": last.kind} if last else None})
    rows.sort(key=lambda r: (r["last"]["at"] if r["last"] else "0", r["name"]), reverse=True)
    return rows


def unread_total(db: Session, me: User) -> int:
    return sum(r["unread"] for r in my_conversations(db, me))


def conversation_out(db: Session, c: Conversation, me: User) -> dict:
    if c.kind == "dm":
        other = next((u for u in members(db, c) if u.id != me.id), None)
        name = other.name if other else "Direct message"
        sub = (other.title or other.team.name if other and other.team else "") if other else ""
    elif c.kind == "team":
        name, sub = c.name or "Team", "Team room — everyone on the team"
    else:
        ms = members(db, c)
        name, sub = c.name, f"{len(ms)} people"
    return {"id": c.id, "kind": c.kind, "name": name, "sub": sub or "", "team_id": c.team_id}


# ---- messages --------------------------------------------------------------------------------------

def messages(db: Session, me: User, c: Conversation, *, after_id: int = 0, limit: int = 200) -> list[ChatMessage]:
    q = db.query(ChatMessage).filter(ChatMessage.conversation_id == c.id)
    if after_id:
        q = q.filter(ChatMessage.id > after_id).order_by(ChatMessage.id.asc()).limit(limit)
        return q.all()
    rows = q.order_by(ChatMessage.id.desc()).limit(limit).all()
    return list(reversed(rows))


def mark_read(db: Session, me: User, c: Conversation) -> None:
    m = _member_row(db, me, c)
    m.last_read_at = utcnow()
    db.flush()


_MENTION = re.compile(r"^\s*@?(?P<name>[A-Za-z][\w\-]*)\b[\s,:\-]*", re.I)


def addressed_to_cexa(text: str) -> Optional[str]:
    """If the message speaks to C-EXA, the question with her name taken off; otherwise None.
    "@C-EXA what's expiring this week" and "C-EXA, who is on Utilities?" both count. Her name follows
    branding, so a renamed AI is still recognised."""
    if not text:
        return None
    ai = branding.ai_name().strip().lower()
    t = text.strip()
    m = _MENTION.match(t)
    if m and m.group("name").lower() == ai:
        rest = t[m.end():].strip()
        return rest or "Hello"
    if f"@{ai}" in t.lower():
        return re.sub(rf"@{re.escape(ai)}", "", t, flags=re.I).strip() or "Hello"
    return None


def send(db: Session, me: User, c: Conversation, text: str) -> ChatMessage:
    text = (text or "").strip()
    if not text:
        raise ValueError("Type something first.")
    if len(text) > MAX_LEN:
        raise ValueError(f"That is too long — {MAX_LEN} characters is the limit.")
    msg = ChatMessage(conversation_id=c.id, sender_id=me.id, kind="text", text=text)
    db.add(msg)
    c.last_message_at = utcnow()
    _member_row(db, me, c).last_read_at = c.last_message_at
    db.flush()
    q = addressed_to_cexa(text)
    if q is not None:
        # Answer in the background, and only once the person's message is committed: a model call
        # can take a while, and the reply must not sit on the lock the request is holding. The
        # answer arrives as C-EXA's own line a few seconds later.
        after_commit(db, jobs.submit, _cexa_reply, c.id, me.id, q)
    return msg


def _cexa_reply(conversation_id: int, asker_id: int, question: str) -> None:
    from ..cexa import brain

    with SessionLocal() as db:
        me = db.get(User, asker_id)
        c = db.get(Conversation, conversation_id)
        if me is None or c is None:
            return
        try:
            r = brain.ask(db, me, question, "chat", {"conversation": conversation_id}, kind="chat")
            answer = (r.get("answer") or "").strip() or "I don't have anything useful on that."
        except Exception as e:  # noqa: BLE001 — a failed model call becomes a plain sentence, never a missing reply
            answer = f"I couldn't answer just now ({str(e)[:80]}). Try again in a minute."
        cexa = brain.cexa_user(db)
        db.add(ChatMessage(conversation_id=c.id, sender_id=cexa.id, kind="axl", text=answer[:MAX_LEN]))
        c.last_message_at = utcnow()
        db.commit()


def message_out(m: ChatMessage, names: dict[int, str]) -> dict:
    return {"id": m.id, "sender_id": m.sender_id, "sender": names.get(m.sender_id, "") if m.sender_id else "", "kind": m.kind, "text": m.text, "at": m.at.isoformat()}
