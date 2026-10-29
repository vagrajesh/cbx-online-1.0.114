"""C-EXA rings someone inside CBX (1.0.64).

A person always decides that a call happens: an admin or the owner asks her in the chat ("call Thandi and
ask whether the Telkom run went out"), or one of her agents suggests a call and the owner presses Call.

    ringing ─(they press Answer)─▶ live ─(she has her answers, or they say bye)─▶ done
       │                            └─(they hang up)─▶ ended
       ├─(Not now)─▶ declined
       └─(nobody answers in CBX_CALL_RING_SECONDS)─▶ missed

It rings in their browser (the app looks every few seconds while it is open). She greets them, says who asked
and why, asks her questions one at a time and listens — they speak (her ears are Whisper, on this server) or
type. When the call ends, whoever started it gets her note: each question with what was said. An agent's
call goes back to the agent's work. Only the words are kept, never the sound; the person sees that notice.
"""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Optional

from sqlalchemy.orm import Session

from .. import jobs
from ..audit import log
from ..branding import ai_name
from ..config import settings
from ..db import SessionLocal, utcnow
from ..models import CexaMessage, User, VoiceCall

LIVE = ("ringing", "live")
MAX_TURNS = 14
BYE = re.compile(r"\b(bye|goodbye|hang up|not now|i'?m busy|call (me )?back( later)?|stop)\b", re.I)
NEVER = re.compile(r"\b(password|pin|otp|one[- ]time|bank (account|details)|card number|id number)\b", re.I)


def _first(name: str) -> str:
    return (name or "").split()[0] if name else "there"


def start(db: Session, to: User, by: User, purpose: str, questions: list[str], *, work_id: Optional[int] = None, lang: str = "en") -> VoiceCall:
    purpose = re.sub(r"\s+", " ", purpose or "").strip()[:300]
    qs = [re.sub(r"\s+", " ", q).strip()[:200] for q in questions or [] if q and q.strip()][:6]
    if to is None or not to.is_active or to.is_axl:
        raise ValueError("That person isn't here (any more).")
    if not purpose:
        raise ValueError("Say what the call is about.")
    if not qs:
        raise ValueError("Give her at least one question to ask.")
    if any(NEVER.search(q) for q in qs + [purpose]):
        raise ValueError("She never asks for passwords, PINs, codes, bank or ID numbers on a call.")
    expire(db)
    if db.query(VoiceCall).filter(VoiceCall.to_user_id == to.id, VoiceCall.status.in_(LIVE)).first():
        raise ValueError(f"{to.name} already has a call from {ai_name()} — wait for it to end.")
    c = VoiceCall(to_user_id=to.id, started_by_id=by.id if by else None, work_id=work_id, purpose=purpose, questions=qs, status="ringing", transcript=[], answers={}, lang=lang or "en")
    db.add(c)
    db.flush()
    log(db, "cexa_call_started", actor=by, target=f"call:{c.id}", detail=f"to {to.name}: {purpose[:160]} ({len(qs)} question(s))")
    return c


def expire(db: Session) -> int:
    cut = utcnow() - timedelta(seconds=max(10, settings.call_ring_seconds))
    rows = db.query(VoiceCall).filter(VoiceCall.status == "ringing", VoiceCall.created_at < cut).all()
    for c in rows:
        c.status, c.ended_at = "missed", utcnow()
        _finish(db, c)
    return len(rows)


def ringing_for(db: Session, me: User) -> Optional[VoiceCall]:
    expire(db)
    return db.query(VoiceCall).filter(VoiceCall.to_user_id == me.id, VoiceCall.status == "ringing").order_by(VoiceCall.id).first()


def _say(c: VoiceCall, text: str) -> str:
    c.transcript = list(c.transcript or []) + [{"who": "cexa", "text": text, "at": utcnow().isoformat()}]
    return text


def opening(db: Session, c: VoiceCall) -> str:
    to = db.get(User, c.to_user_id)
    by = db.get(User, c.started_by_id) if c.started_by_id else None
    who = f"{_first(by.name)} asked me to" if by and by.id != c.to_user_id else "I'm calling to"
    p = c.purpose.rstrip(".")
    p = p[0].lower() + p[1:] if p and not p[:2].isupper() else p
    return f"Hi {_first(to.name if to else '')}, it's {ai_name()}. {who} {p}. It's quick — {len(c.questions)} question{'s' if len(c.questions) != 1 else ''}. {c.questions[0]}"


def answer(db: Session, c: VoiceCall, me: User) -> str:
    if c.to_user_id != me.id:
        raise PermissionError("This call isn't for you.")
    if c.status != "ringing":
        raise ValueError("This call has ended.")
    c.status, c.answered_at = "live", utcnow()
    log(db, "cexa_call_answered", actor=me, target=f"call:{c.id}")
    return _say(c, opening(db, c))


def decline(db: Session, c: VoiceCall, me: User) -> None:
    if c.to_user_id != me.id:
        raise PermissionError("This call isn't for you.")
    if c.status == "ringing":
        c.status, c.ended_at = "declined", utcnow()
        _finish(db, c)


def hang_up(db: Session, c: VoiceCall, me: User) -> None:
    if c.to_user_id != me.id:
        raise PermissionError("This call isn't for you.")
    if c.status == "live":
        c.status, c.ended_at = "ended", utcnow()
        _finish(db, c)


def _asked(c: VoiceCall) -> int:
    """How many of her questions are answered (the scripted path)."""
    return len([q for q in c.questions if q in (c.answers or {})])


def _scripted(c: VoiceCall, text: str, who: Optional[User]) -> tuple[str, bool]:
    if BYE.search(text) and len(text) < 60:
        return f"No problem — I'll let them know. Thanks, {_first(who.name if who else '')}. Bye!", True
    i = _asked(c)
    if i < len(c.questions):
        c.answers = {**(c.answers or {}), c.questions[i]: text}
        i += 1
    if i < len(c.questions):
        return f"Thanks. {c.questions[i]}", False
    return f"That's everything — thank you, {_first(who.name if who else '')}. I'll pass it on. Bye!", True


def _model(db: Session, c: VoiceCall, who: Optional[User]) -> Optional[tuple[str, bool]]:
    from ..cexa.gateway import Gateway, ModelError

    lines = "\n".join(f"{'YOU' if t['who'] == 'cexa' else (who.name if who else 'THEM')}: {t['text']}" for t in c.transcript or [])
    system = (f"You are {ai_name()}, on a short voice call inside CBX-Connect with {who.name if who else 'a colleague'}. Purpose: {c.purpose}.\n"
              "Your questions, in order:\n" + "\n".join(f"{i + 1}. {q}" for i, q in enumerate(c.questions))
              + "\nSpeak like a person on the phone: one or two short sentences, one question at a time. If an answer is unclear, ask once more; "
                "then move on. Never invent an answer, never ask for passwords, PINs, codes, bank or ID numbers. When every question is answered, "
                "or they want to stop, say a short thank-you and goodbye and set done. 'answers' maps each question (its exact words) to what they said, in their words.")
    db.commit()  # never hold the write lock while the model thinks
    try:
        out = Gateway(db).json("call", system, lines, {"say": "string", "done": "boolean", "answers": "object"}, required=["say", "done"])
    except ModelError:
        return None
    say = re.sub(r"\s+", " ", str(out.get("say") or "")).strip()[:500]
    if not say:
        return None
    ans = out.get("answers") if isinstance(out.get("answers"), dict) else {}
    keep = {q: str(ans[q])[:500] for q in c.questions if q in ans and str(ans[q]).strip()}
    c.answers = {**(c.answers or {}), **keep}
    return say, bool(out.get("done"))


def turn(db: Session, c: VoiceCall, me: User, text: str) -> tuple[str, bool]:
    """They said something: what she says next, and whether the call is over."""
    if c.to_user_id != me.id:
        raise PermissionError("This call isn't for you.")
    if c.status != "live":
        raise ValueError("This call has ended.")
    text = re.sub(r"\s+", " ", text or "").strip()[:1000]
    if not text:
        return _say(c, "Sorry, I didn't catch that — could you say it again?"), False
    c.transcript = list(c.transcript or []) + [{"who": "them", "text": text, "at": utcnow().isoformat()}]
    got = None if settings.model_mode == "stub" else _model(db, c, me)
    say, done = got if got else _scripted(c, text, me)
    if len(c.transcript or []) >= MAX_TURNS * 2 and not done:
        say, done = f"I'll stop there — thank you, {_first(me.name)}. I'll pass on what you said. Bye!", True
    _say(c, say)
    if done:
        c.status, c.ended_at = "done", utcnow()
        _finish(db, c)
    return say, done


def note(c: VoiceCall, to_name: str) -> str:
    head = {"done": f"Call with {to_name}", "ended": f"Call with {to_name} (they hung up early)", "declined": f"{to_name} said not now",
            "missed": f"{to_name} didn't answer"}.get(c.status, f"Call with {to_name}")
    lines = [f"{head} — {c.purpose}"]
    if c.status in ("done", "ended"):
        for q in c.questions:
            a = (c.answers or {}).get(q)
            lines.append(f"- {q} → {a if a else '(not answered)'}")
    return "\n".join(lines)


def _finish(db: Session, c: VoiceCall) -> None:
    to = db.get(User, c.to_user_id)
    c.summary = note(c, to.name if to else "them")
    log(db, "cexa_call_" + c.status, target=f"call:{c.id}", detail=c.summary[:400])
    if c.started_by_id and c.started_by_id != c.to_user_id:
        db.add(CexaMessage(user_id=c.started_by_id, from_axl=True, kind="call", title=c.summary.splitlines()[0][:160], text=c.summary, link="#/agents?tab=now", meta={"call": c.id}))
    if c.work_id:
        from ..models import AgentWork

        w = db.get(AgentWork, c.work_id)
        if w is not None:
            w.transcript = list(w.transcript or []) + [{"role": "user", "content": "The call you suggested:\n" + c.summary}]
            if w.status in ("done", "held"):
                w.status = "open"  # the agent carries on with what was said


def out(db: Session, c: VoiceCall, *, full: bool = False) -> dict:
    to = db.get(User, c.to_user_id)
    by = db.get(User, c.started_by_id) if c.started_by_id else None
    d = {"id": c.id, "to": to.name if to else "", "by": by.name if by else "", "purpose": c.purpose, "status": c.status, "questions": c.questions, "lang": c.lang,
         "summary": c.summary, "created_at": c.created_at.isoformat() if c.created_at else None, "ai": ai_name(),
         "ring_seconds": settings.call_ring_seconds}
    if full:
        d["transcript"] = c.transcript or []
        d["answers"] = c.answers or {}
    return d


@jobs.routine("cexa-calls", 20)
def calls_routine() -> None:
    db = SessionLocal()
    try:
        if expire(db):
            db.commit()
    finally:
        db.close()


# ---- C-EXA's hand for it (the chat) -------------------------------------------------------------------

from ..cexa import tools as T  # noqa: E402


@T.tool("call_person", "change", "Ring someone inside CBX-Connect and ask them by voice (they answer in their browser). Use when an admin or the owner asks you to call, ring or ask someone something. You get the answers when the call ends.",
        {"to": "who (a name or e-mail)", "purpose": "what the call is about, one line", "questions": "1-6 short questions, separated by |"})
def call_person(db: Session, me: User, fix, to: str = "", purpose: str = "", questions: str = "", **_) -> str:
    from ..cexa.tools_engine import _person

    uid, label = _person(db, me, to)
    if uid is None:
        return label
    try:
        c = start(db, db.get(User, uid), me, purpose, [q for q in re.split(r"\||\n", questions or "")])
    except ValueError as e:
        return str(e)
    return f"Ringing {label} now in CBX (call #{c.id}). If they answer, I'll ask: " + "; ".join(c.questions) + ". You'll get my note when it ends — or that they didn't pick up."
