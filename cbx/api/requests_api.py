"""Requests: a person asks for something (Part 1), C-EXA assesses it, an admin approves and picks
who does the interview. Rules live here too, editable later by the admin or the requester."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request as HttpRequest, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import jobs
from .. import requests_flow as F
from ..audit import log
from ..config import settings
from ..db import get_db
from ..models import Request, Rule, User
from ..security.deps import client_ip, current_user, require_admin, require_human
from ..security.rls import scope
from ..util import read_capped
from .schemas import user_out

router = APIRouter(prefix="/api/requests", tags=["requests"])


def _get(db: Session, rid: int, me: User) -> Request:
    req = scope(db.query(Request), Request, me).filter(Request.id == rid).one_or_none()
    if req is None:
        raise HTTPException(404, "That request isn't here, or it isn't yours.")
    return req


def _claim(db: Session, req: Request, expect: str, new_status: str) -> bool:
    """Move a request from `expect` to `new_status` only if nobody else did first: one UPDATE with the
    status in its WHERE, so two admins clicking the same button in the same moment cannot both win.
    The ORM object is refreshed so the caller sees what really happened."""
    n = db.query(Request).filter(Request.id == req.id, Request.status == expect).update({"status": new_status}, synchronize_session=False)
    db.flush()
    db.refresh(req)
    return n == 1


# ---- Part 1 ------------------------------------------------------------------------
class RequestIn(BaseModel):
    problem: str
    impact: str = ""
    wish: str = ""
    attachment_id: Optional[int] = None


@router.post("")
def create_request(body: RequestIn, request: HttpRequest, db: Session = Depends(get_db), me: User = Depends(require_admin)):  # 1.0.66: Requests are for the owner and admins; everyone else uses Ticket request
    if len(body.problem.strip()) < 10:
        raise HTTPException(400, "Tell us a little more about the problem — a sentence or two.")
    from sqlalchemy.exc import IntegrityError

    # Two people pressing Send in the same second used to pick the same next number and one got a 500.
    # The number is unique in the database; a collision is caught and the next one taken, up to a few times.
    for attempt in range(5):
        req = Request(number=F.next_number(db), user_id=me.id, team_id=me.team_id, problem=body.problem.strip(), impact=body.impact.strip(), wish=body.wish.strip(), attachment_id=body.attachment_id, status="sent")
        try:
            with db.begin_nested():
                db.add(req)
                db.flush()
            break
        except IntegrityError:
            if attempt == 4:
                raise HTTPException(503, "The request numbers are busy right now — press Send once more.")
            continue
    log(db, "request_sent", actor=me, target=f"request:{req.number}", ip=client_ip(request))
    if settings.model_mode == "stub":
        db.commit()  # never ask a model while holding the write lock (gateway.guard_write_lock)
        F.assess(db, req)  # instant and deterministic — no need for a thread
    else:
        db.commit()  # the job runs in its own session and must see the row
        jobs.submit(F.assess_job, req.id)
    return {"request": F.request_out(db, req, full=True)}


@router.post("/attachment")
async def attach(files: list[UploadFile] = File(...), db: Session = Depends(get_db), me: User = Depends(require_admin)):
    from ..warehouse.storage import store

    f = files[0]
    data = await read_capped(f, settings.max_attachment_mb * 1024 * 1024, "attachment")
    if not data:
        raise HTTPException(400, "That file is empty.")
    sf = store(db, me, f.filename or "attachment", data, sub="Work", source="upload")
    log(db, "request_attachment", actor=me, target=sf.name, detail=f"{len(data)} bytes")
    return {"id": sf.id, "name": sf.name, "size": sf.size}


@router.get("/mine")
def mine(db: Session = Depends(get_db), me: User = Depends(current_user)):
    rows = db.query(Request).filter(Request.user_id == me.id, Request.origin != "admin").order_by(Request.created_at.desc()).all()
    return {"requests": [F.request_out(db, r) for r in rows]}


# ---- admin queue -------------------------------------------------------------------
@router.get("/queue")
def queue(db: Session = Depends(get_db), me: User = Depends(require_admin)):
    # workflows an admin created directly live on the Workflows page, not in this queue
    rows = db.query(Request).filter(Request.origin != "admin").order_by(Request.created_at.desc()).all()
    counts = {s: sum(1 for r in rows if r.status == s) for s in ("sent", "new", "interview", "working", "testing", "ready", "declined", "cancelled")}
    return {"requests": [F.request_out(db, r, admin=True) for r in rows], "counts": counts}


@router.get("/{rid}")
def detail(rid: int, db: Session = Depends(get_db), me: User = Depends(current_user)):
    req = _get(db, rid, me)
    d = F.request_out(db, req, full=True, admin=me.is_admin)
    if me.is_admin:
        d["related"] = F.related(db, req)
        admins = db.query(User).filter(User.role.in_(["owner", "admin"]), User.is_axl.is_(False), User.is_active.is_(True)).order_by(User.name).all()
        d["admins"] = [user_out(u) for u in admins]
        d["recommend"] = "axl" if (req.confidence or 0) >= 0.75 else "human"
    return d


class ApproveIn(BaseModel):
    mode: str
    assignee_id: Optional[int] = None


@router.post("/{rid}/approve")
def approve(rid: int, body: ApproveIn, request: HttpRequest, db: Session = Depends(get_db), me: User = Depends(require_admin), _h: User = Depends(require_human)):
    req = _get(db, rid, me)
    if req.status != "new":
        raise HTTPException(400, "Only a new request can be approved.")
    if body.mode not in ("axl", "human"):
        raise HTTPException(400, "Choose who does the interview: C-EXA or a person.")
    assignee = db.get(User, body.assignee_id) if body.assignee_id else me
    if assignee is None or not assignee.is_admin or assignee.is_axl or not assignee.is_active:
        raise HTTPException(400, "Assign it to an admin or owner (a person, not C-EXA).")
    if not _claim(db, req, "new", "approving"):
        raise HTTPException(409, "Someone else just took this request. Reload to see who.")
    iv = F.open_interview(db, req, mode=body.mode, assignee=assignee, actor=me)
    return {"request": F.request_out(db, req, full=True, admin=True), "interview": F.interview_out(db, iv)}


class DeclineIn(BaseModel):
    note: str = ""


@router.post("/{rid}/decline")
def decline(rid: int, body: DeclineIn, db: Session = Depends(get_db), me: User = Depends(require_admin), _h: User = Depends(require_human)):
    req = _get(db, rid, me)
    if req.status not in ("new", "sent"):
        raise HTTPException(400, "This request has already moved on.")
    if not _claim(db, req, req.status, "declined"):
        raise HTTPException(409, "Someone else just took this request. Reload to see who.")
    req.status = "declined"
    req.decline_note = body.note.strip()
    req.approved_by_id = me.id
    requester = db.get(User, req.user_id)
    F.notify(db, requester, f"About your request #{req.number}", (body.note.strip() or "We won't be taking this one forward right now.") + f" — {F.first_name(me)}", link="#/requests")
    log(db, "request_declined", actor=me, target=f"request:{req.number}", detail=body.note[:200])
    return {"request": F.request_out(db, req, full=True, admin=True)}


class CancelIn(BaseModel):
    note: str = ""


@router.post("/{rid}/cancel")
def cancel(rid: int, body: CancelIn, db: Session = Depends(get_db), me: User = Depends(current_user), _h: User = Depends(require_human)):
    """The requester or an admin can cancel at any stage — even after it was approved, interviewed
    or built. A live workflow is switched off; nothing is deleted, so the history stays readable."""
    req = _get(db, rid, me)
    if req.status in ("declined", "cancelled"):
        raise HTTPException(400, "This request is already closed.")
    if req.user_id != me.id and not me.is_admin:
        raise HTTPException(403, "Only the person who asked, or an admin, can cancel this.")
    was = req.status
    note = body.note.strip()
    req.status = "cancelled"
    req.decline_note = note
    if req.interview and req.interview.status != "done":
        req.interview.status = "done"
        req.interview.summary = (req.interview.summary or "") + f"\n[Cancelled by {me.name} while {was}.]"
    switched_off = []
    if req.ticket:
        t = req.ticket
        t.status = "cancelled"
        from ..models import Tool

        for tool in db.query(Tool).filter(Tool.ticket_id == t.id, Tool.is_active.is_(True)).all():
            tool.is_active = False
            switched_off.append(tool.name)
    from ..models import Task

    for task in db.query(Task).filter(Task.origin == "request", Task.origin_ref == f"request:{req.id}", Task.status != "done").all():
        task.status = "done"
    requester = db.get(User, req.user_id)
    who = F.first_name(me)
    if me.id != req.user_id and requester:
        F.notify(db, requester, f"Request #{req.number} cancelled", (note or "This request has been cancelled.") + f" — {who}", link="#/requests")
    if req.assignee_id and req.assignee_id != me.id and (a := db.get(User, req.assignee_id)):
        F.notify(db, a, f"Request #{req.number} cancelled by {who}", (note or f"{who} cancelled it while it was {was}.") + (f" Tool switched off: {', '.join(switched_off)}." if switched_off else ""), link="#/req-queue")
    log(db, "request_cancelled", actor=me, target=f"request:{req.number}", detail=f"while {was}" + (f"; tools off: {', '.join(switched_off)}" if switched_off else "") + (f"; note: {note[:160]}" if note else ""))
    return {"request": F.request_out(db, req, full=True, admin=me.is_admin), "switched_off": switched_off}


# ---- rules -------------------------------------------------------------------------
def _can_edit_rules(req: Request, me: User) -> bool:
    return me.is_admin or req.user_id == me.id


@router.get("/{rid}/rules")
def list_rules(rid: int, db: Session = Depends(get_db), me: User = Depends(current_user)):
    req = _get(db, rid, me)
    return {"rules": [F.rule_out(r) for r in req.rules if r.active]}


class RuleIn(BaseModel):
    text: str
    source: str = "admin"  # user | admin — ignored for the requester, always "user"


@router.post("/{rid}/rules")
def add_rule(rid: int, body: RuleIn, db: Session = Depends(get_db), me: User = Depends(current_user)):
    req = _get(db, rid, me)
    if not _can_edit_rules(req, me):
        raise HTTPException(403, "You can't change the rules on this request.")
    if len(body.text.strip()) < 3:
        raise HTTPException(400, "Write the rule first.")
    by_user = not me.is_admin
    source = "user" if by_user or body.source == "user" else "admin"
    r = F.add_rule(db, req, body.text, source=source, actor=me, by_user=by_user)
    if by_user:
        r.changed_by_user = True
    return {"rule": F.rule_out(r), "rules": [F.rule_out(x) for x in req.rules if x.active]}


class RulePatch(BaseModel):
    text: Optional[str] = None
    source: Optional[str] = None


@router.patch("/{rid}/rules/{rule_id}")
def patch_rule(rid: int, rule_id: int, body: RulePatch, db: Session = Depends(get_db), me: User = Depends(current_user)):
    req = _get(db, rid, me)
    if not _can_edit_rules(req, me):
        raise HTTPException(403, "You can't change the rules on this request.")
    rule = db.get(Rule, rule_id)
    if rule is None or rule.request_id != req.id or not rule.active:
        raise HTTPException(404, "That rule isn't on this request.")
    if body.text is not None:
        F.edit_rule(db, rule, body.text, actor=me, by_user=not me.is_admin)
    if body.source in ("user", "admin") and me.is_admin:
        rule.source = body.source
    return {"rule": F.rule_out(rule)}


@router.delete("/{rid}/rules/{rule_id}")
def delete_rule(rid: int, rule_id: int, db: Session = Depends(get_db), me: User = Depends(current_user)):
    req = _get(db, rid, me)
    if not _can_edit_rules(req, me):
        raise HTTPException(403, "You can't change the rules on this request.")
    rule = db.get(Rule, rule_id)
    if rule is None or rule.request_id != req.id:
        raise HTTPException(404, "That rule isn't on this request.")
    F.remove_rule(db, rule, actor=me)
    return {"ok": True, "rules": [F.rule_out(x) for x in req.rules if x.active]}
