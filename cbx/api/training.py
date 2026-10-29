"""Training: the lessons this person can take, where they are in each, and the practice material.

Nothing here changes anyone's access. A lesson is offered only for a tool the person already holds
(or a capability they already have) — training is how to use what you were given, not a way to get
more. The one write is progress, and it is the person's own.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..audit import log
from ..db import get_db, utcnow
from ..models import Tool, ToolAccess, TrainingProgress, User
from ..security.deps import client_ip, current_user, require_human, require_owner
from ..training import catalog as CAT
from ..training import demo as DEMO

router = APIRouter(prefix="/api/training", tags=["training"])


def _my_tools(db: Session, me: User) -> set[str]:
    if me.is_admin:
        return {t.slug for t in db.query(Tool).filter(Tool.is_active.is_(True)).all()}
    rows = db.query(Tool.slug).join(ToolAccess, ToolAccess.tool_id == Tool.id).filter(ToolAccess.user_id == me.id, Tool.is_active.is_(True)).all()
    return {r[0] for r in rows}


def _progress_out(p: TrainingProgress | None) -> dict:
    if p is None:
        return {"step": 0, "total": 0, "done": False, "done_at": None, "times_done": 0, "started": False}
    return {"step": p.step, "total": p.total, "done": p.done_at is not None, "done_at": p.done_at.isoformat() if p.done_at else None,
            "times_done": p.times_done, "started": True}


@router.get("")
def my_lessons(db: Session = Depends(get_db), me: User = Depends(current_user)):
    """Every lesson for this person, grouped, with progress. Unavailable ones say why."""
    tools = _my_tools(db, me)
    prog = {p.lesson_id: p for p in db.query(TrainingProgress).filter(TrainingProgress.user_id == me.id).all()}
    out = []
    for lesson, ok, why in CAT.available(me, tools):
        out.append({
            "id": lesson.id, "title": lesson.title, "blurb": lesson.blurb, "minutes": lesson.minutes,
            "group": lesson.group, "tool": lesson.tool, "available": ok, "why_not": why,
            "progress": _progress_out(prog.get(lesson.id)),
        })
    done = sum(1 for l in out if l["progress"]["done"])
    avail = sum(1 for l in out if l["available"])
    return {"lessons": out, "groups": list(dict.fromkeys(l["group"] for l in out)), "done": done, "available": avail,
            "demo_files": [{"name": k, "label": v[0]} for k, v in DEMO.FILES.items()],
            "can_reset": me.is_owner}


@router.post("/reset")
def reset(request: Request, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    """Lay the practice entities out fresh. Owner only; everyone's lesson progress is untouched."""
    r = DEMO.reset(db, me)
    r["message"] = f"Practice entities reset · {r['cleared']} filed cell{'s' if r['cleared'] != 1 else ''} cleared."
    return r


# Declared after /reset on purpose: FastAPI matches routes in order, and "reset" would otherwise
# be read as a lesson id.
class ProgressIn(BaseModel):
    step: int = 0
    total: int = 0
    done: bool = False
    restart: bool = False


@router.post("/{lesson_id}")
def progress(lesson_id: str, body: ProgressIn, request: Request, db: Session = Depends(get_db), me: User = Depends(current_user)):
    if lesson_id not in CAT.BY_ID:
        raise HTTPException(404, "That lesson doesn't exist.")
    p = db.query(TrainingProgress).filter(TrainingProgress.user_id == me.id, TrainingProgress.lesson_id == lesson_id).one_or_none()
    if p is None:
        p = TrainingProgress(user_id=me.id, lesson_id=lesson_id, step=0, total=0, times_done=0)
        db.add(p)
    if body.restart:
        p.step, p.done_at = 0, None
    else:
        p.step = max(0, body.step)
        p.total = max(p.total or 0, body.total)
        if body.done and p.done_at is None:
            p.done_at = utcnow()
            p.times_done = (p.times_done or 0) + 1
            log(db, "training_done", actor=me, target=lesson_id, detail=CAT.BY_ID[lesson_id].title, ip=client_ip(request))
    db.flush()
    return _progress_out(p)


@router.get("/demo/{name}")
def demo_file(name: str, me: User = Depends(current_user)):
    """A practice PDF, generated on the spot. Holds no real account, amount or person."""
    spec = DEMO.FILES.get(name)
    if spec is None:
        raise HTTPException(404, "No such practice file.")
    data = spec[1]()
    return Response(content=data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="Training - {name}"'})
