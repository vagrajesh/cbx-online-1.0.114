"""The Develop board: the team's tasks in To do / Working / Done, with notes. C-EXA reads every
card (cbx.cexa.developer.review_board) and comments on the card itself."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..audit import log
from ..db import get_db, utcnow
from ..models import Proposal, Task, TaskNote, User
from ..models.develop import TASK_STATUSES
from ..security.deps import require_admin
from .schemas import iso

router = APIRouter(prefix="/api/develop", tags=["develop"])


class TaskIn(BaseModel):
    title: str
    detail: str = ""
    status: str = "todo"
    assignee_id: Optional[int] = None
    assignee_axl: bool = False
    due: Optional[str] = None


class TaskPatch(BaseModel):
    title: Optional[str] = None
    detail: Optional[str] = None
    status: Optional[str] = None
    assignee_id: Optional[int] = None
    assignee_axl: Optional[bool] = None
    due: Optional[str] = None  # "" clears
    order: Optional[int] = None


class NoteIn(BaseModel):
    text: str


def _due(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, "The due date isn't a date I understand. Use YYYY-MM-DD.")
    if d.tzinfo is None:
        from datetime import timezone

        d = d.replace(hour=16, minute=0, tzinfo=timezone.utc)  # 18:00 SAST
    return d


def task_out(t: Task, db: Session, now: Optional[datetime] = None) -> dict:
    now = now or utcnow()
    assignee = db.get(User, t.assignee_id) if t.assignee_id else None
    return {
        "id": t.id,
        "title": t.title,
        "detail": t.detail,
        "status": t.status,
        "assignee_id": t.assignee_id,
        "assignee_axl": t.assignee_axl,
        "assignee": "C-EXA" if t.assignee_axl else (assignee.name if assignee else ""),
        "due": iso(t.due),
        "overdue": bool(t.due and t.due < now and t.status != "done"),
        "origin": t.origin,
        "origin_ref": t.origin_ref,
        "order": t.order,
        "created_at": iso(t.created_at),
        "started_at": iso(t.started_at),
        "done_at": iso(t.done_at),
        "trace": t.trace or [],
        "notes": [{"id": n.id, "at": iso(n.at), "by": n.by, "by_name": "C-EXA" if n.by == "axl" else (db.get(User, n.by_id).name if n.by_id and db.get(User, n.by_id) else "Someone"), "text": n.text} for n in t.notes],
    }


def _move(t: Task, to: str, by: User) -> None:
    if to not in TASK_STATUSES:
        raise HTTPException(400, "Status must be one of: to do, working, done.")
    if to == t.status:
        return
    now = utcnow()
    t.trace = list(t.trace or []) + [{"at": now.isoformat(), "by": "axl" if by.is_axl else by.email, "from": t.status, "to": to}]
    if to == "working" and not t.started_at:
        t.started_at = now
    if to == "done":
        t.done_at = now
    elif t.status == "done":
        t.done_at = None
    t.status = to


@router.get("/tasks")
def list_tasks(db: Session = Depends(get_db), me: User = Depends(require_admin)):
    now = utcnow()
    week = now.replace(hour=0, minute=0, second=0, microsecond=0)
    from datetime import timedelta

    week = week - timedelta(days=week.weekday())
    cols: dict[str, list] = {s: [] for s in TASK_STATUSES}
    for t in db.query(Task).order_by(Task.order, Task.due.is_(None), Task.due, Task.created_at.desc()).all():
        if t.status == "done" and t.done_at and t.done_at < week:
            continue
        cols[t.status].append(task_out(t, db, now))
    cols["done"].sort(key=lambda d: d["done_at"] or "", reverse=True)
    people = [{"id": u.id, "name": u.name} for u in db.query(User).filter(User.role.in_(["owner", "admin"]), User.is_axl.is_(False), User.is_active.is_(True)).order_by(User.name)]
    return {"columns": cols, "people": people, "done_total": db.query(Task).filter(Task.status == "done").count()}


@router.post("/tasks")
def create_task(body: TaskIn, db: Session = Depends(get_db), me: User = Depends(require_admin)):
    if not body.title.strip():
        raise HTTPException(400, "Give the task a title.")
    if body.status not in TASK_STATUSES:
        raise HTTPException(400, "Status must be one of: to do, working, done.")
    t = Task(title=body.title.strip()[:200], detail=body.detail.strip(), status=body.status, assignee_id=None if body.assignee_axl else body.assignee_id, assignee_axl=body.assignee_axl, due=_due(body.due), created_by_id=me.id, origin="manual", trace=[{"at": utcnow().isoformat(), "by": me.email, "from": "", "to": body.status}])
    if body.status == "working":
        t.started_at = utcnow()
    db.add(t)
    db.flush()
    log(db, "task_created", actor=me, target=f"task:{t.id}", detail=t.title)
    return task_out(t, db)


@router.get("/tasks/{tid}")
def get_task(tid: int, db: Session = Depends(get_db), me: User = Depends(require_admin)):
    t = db.get(Task, tid)
    if not t:
        raise HTTPException(404, "That task doesn't exist.")
    return task_out(t, db)


@router.patch("/tasks/{tid}")
def patch_task(tid: int, body: TaskPatch, db: Session = Depends(get_db), me: User = Depends(require_admin)):
    t = db.get(Task, tid)
    if not t:
        raise HTTPException(404, "That task doesn't exist.")
    changes = []
    if body.title is not None and body.title.strip():
        t.title = body.title.strip()[:200]
        changes.append("title")
    if body.detail is not None:
        t.detail = body.detail
        changes.append("detail")
    if body.assignee_axl is not None:
        t.assignee_axl = body.assignee_axl
        if body.assignee_axl:
            t.assignee_id = None
        changes.append("assignee")
    if body.assignee_id is not None and not (body.assignee_axl or False):
        t.assignee_id = body.assignee_id or None
        t.assignee_axl = False
        changes.append("assignee")
    if body.due is not None:
        t.due = _due(body.due) if body.due else None
        changes.append("due")
    if body.order is not None:
        t.order = body.order
    if body.status is not None:
        old = t.status
        _move(t, body.status, me)
        if old != t.status:
            changes.append(f"{old}→{t.status}")
            if t.status == "done" and t.origin_ref.startswith("proposal:"):
                p = db.get(Proposal, int(t.origin_ref.split(":")[1]))
                if p and p.status in ("noticed",):
                    p.status = "declined"
    db.flush()
    if changes:
        log(db, "task_updated", actor=me, target=f"task:{t.id}", detail=", ".join(changes))
    return task_out(t, db)


@router.post("/tasks/{tid}/notes")
def add_note(tid: int, body: NoteIn, db: Session = Depends(get_db), me: User = Depends(require_admin)):
    t = db.get(Task, tid)
    if not t:
        raise HTTPException(404, "That task doesn't exist.")
    if not body.text.strip():
        raise HTTPException(400, "Write something first.")
    n = TaskNote(task_id=t.id, by="axl" if me.is_axl else "user", by_id=me.id, text=body.text.strip()[:2000])
    db.add(n)
    db.flush()
    db.refresh(t)
    return task_out(t, db)


@router.delete("/tasks/{tid}")
def delete_task(tid: int, db: Session = Depends(get_db), me: User = Depends(require_admin)):
    t = db.get(Task, tid)
    if not t:
        raise HTTPException(404, "That task doesn't exist.")
    log(db, "task_deleted", actor=me, target=f"task:{t.id}", detail=t.title)
    db.delete(t)
    return {"ok": True}


