"""Support tickets (1.0.66) — see cbx/support/core.py.

Anyone signed in (Ticket request):
    GET  /api/support/systems                 the systems, their problem areas and questions
    GET  /api/support/me                      the details the form fills in (name, e-mail, department, location)
    POST /api/support/tickets                 open a ticket
    GET  /api/support/tickets/mine            my tickets
    GET  /api/support/tickets/{id}            mine — or one a support person may see
    POST /api/support/tickets/{id}/reply      answer on my ticket
    POST /api/support/tickets/{id}/files      add files (mine, or as support)
    GET  /api/support/tickets/{id}/files/{n}
Support (the owner, admins, support leads):
    GET  /api/support/dashboard   GET /api/support/queue
    POST /api/support/tickets/{id}/assign {user_id}         owner/admin, a person
    POST /api/support/tickets/{id}/status {status, note}    the lead (or owner/admin)
    POST /api/support/tickets/{id}/message {text, to_requester}
    POST /api/support/tickets/{id}/reread                   owner/admin: C-EXA reads it again
Settings (owner/admin): GET /api/support/settings, POST /systems, PUT /systems/{id},
POST /leads {user_id, system_ids}, PUT /leads/{user_id} {system_ids}, DELETE /leads/{user_id} (the support-lead list). Reading the mailbox now: Mailer → a mailbox → Read now.

1.0.98 (from CBX-Connect 2.10.x):
    POST /api/support/tickets/upload                    open a ticket with its files in one go (Iris reads the pictures)
    POST /api/support/tickets/{id}/accept               the lead it is assigned to takes it on (the SLA starts)
    GET|POST /api/support/accept/{token}                Accept from the lead's mail (no sign-in; the page, then the button)
    POST /api/support/tickets/{id}/system {system_id}   the desk / a lead / owner / admin says which system it is
    GET|POST /api/support/my-settings                   a support person's own digest schedule and Invisible
    GET /api/support/people, POST /api/support/people   owner/admin: everyone's settings; POST /api/support/escalation {user_id}
    GET /api/support/lessons, POST|DELETE /api/support/lessons/{id}   owner/admin: what the desk has learned
    GET /api/support/team                               the support team: the readers, Ada, Iris, Nudge, live

1.0.103 (from CBX-Connect 2.10.8): the support team (not the owner or an admin) sees only its own tickets and the free ones
(cbx/support/own.py) — someone else's is a 404 everywhere — and:
    POST /api/support/tickets/{id}/take                 a free ticket becomes theirs (accepted at once); "Already taken by …"
    POST /api/support/tickets/{id}/give-back            their own open ticket back to the desk, free for anyone to take
    POST /api/support/tickets/{id}/not-relevant {reason, tell}   owner/admin: close it as not relevant without assigning it

1.0.109 (Edwin, 21 Oct: screenshots, removing a ticket, a desk that just flows):
    GET  /api/support/tickets/{id}/files/{n}?view=1       a picture shown on the page (thumbnails, full size) — not a download
    POST /api/support/tickets/{id}/remove {tell, reason, message}   owner/admin: remove it — the requester told once, or silently
    POST /api/support/tickets/{id}/restore                owner/admin: back from Removed
    GET  /api/support/queue?status=removed                owner/admin: the removed tickets
    GET  /api/support/automatic                           owner/admin: Automatic mails (auto-replies, other helpdesks' acknowledgements)
    POST /api/support/automatic/{id}/dismiss | /ticket    owner/admin: dismiss one, or make it a ticket after all
    POST /api/support/new-problem/{mail id}               owner/admin: a new problem sent in an old ticket's thread → its own ticket (nobody told)
    GET  /api/support/queue: status also not_accepted, done, not_relevant; `assignees` — the people tickets are with (the filter's picker)
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..audit import log
from ..db import get_db
from ..models import SupportLead, SupportLesson, SupportPerson, SupportSystem, SupportTicket, User
from ..security.deps import current_user, require_human
from ..support import core as C
from ..support import inbox as I
from ..support import own as OWN  # 1.0.103: the support team sees its own tickets and the free ones; Take it, Give back
from ..util import read_capped

router = APIRouter(prefix="/api/support", tags=["support"])


class TicketIn(BaseModel):
    system: str
    problem: str = ""
    description: str
    answers: dict = {}
    location: str = ""
    phone: str = ""
    department: str = ""
    where: str = ""     # 1.0.74: where the problem is
    outcome: str = ""   # 1.0.74: what they need done


class TextIn(BaseModel):
    text: str


class AssignIn(BaseModel):
    user_id: int
    why_not: str = ""   # 1.0.109 (round 2): why not the first suggestion — wrong_area | too_busy | on_leave | other (optional)
    why_text: str = ""


class StatusIn(BaseModel):
    status: str
    note: str = ""


class MessageIn(BaseModel):
    text: str
    to_requester: bool = True
    wait: bool = False  # 1.0.109 (round 2): a question — the desk now waits on the requester (the follow-up clock)


class SystemIn(BaseModel):
    name: str
    blurb: str = ""
    active: bool = True
    sort: int = 100
    problems: list = []


class LeadsIn(BaseModel):
    user_ids: list[int] = []


def _ticket(db: Session, tid: int) -> SupportTicket:
    t = db.get(SupportTicket, tid)
    if t is None:
        raise HTTPException(404, "No such ticket.")
    return t


def _mine(t: SupportTicket, me: User) -> bool:
    return t.requester_id == me.id or (not t.requester_id and t.requester_email and t.requester_email == (me.email or "").lower())


def _staff(db: Session, me: User) -> User:
    if not C.is_staff(db, me):
        raise HTTPException(403, "This is for the support team.")
    return me


def require_support(db: Session = Depends(get_db), me: User = Depends(current_user)) -> User:
    return _staff(db, me)


def require_assigner(me: User = Depends(current_user)) -> User:
    if not C.can_assign(me):
        raise HTTPException(403, "Only the owner or an admin does this.")
    return me


def _do(fn, *a):
    try:
        return fn(*a)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---- anyone signed in ---------------------------------------------------------------------------------

@router.get("/systems")
def list_systems(db: Session = Depends(get_db), me: User = Depends(current_user)):
    return {"systems": [C.system_out(s) for s in C.systems(db)]}


@router.get("/me")
def my_details(db: Session = Depends(get_db), me: User = Depends(current_user)):
    return C.details_of(db, me)


@router.post("/tickets")
def open_ticket(body: TicketIn, db: Session = Depends(get_db), me: User = Depends(require_human)):
    return _open(db, me, body, [])


def _open(db: Session, me: User, body: TicketIn, files: list) -> dict:
    s = next((x for x in C.systems(db) if x.key == body.system), None)
    if s is None:
        raise HTTPException(400, "Choose what the problem is with.")
    prob = C.problem_of(s, body.problem)
    if prob is None and (s.problems or []):
        raise HTTPException(400, "Choose what kind of problem it is.")
    if len(re.findall(r"\w+", body.description or "")) < 3:
        raise HTTPException(400, "Tell us a little more about what happened.")
    for f in (prob or {}).get("fields", []):
        if f.get("required") and not str((body.answers or {}).get(f["key"], "")).strip():
            raise HTTPException(400, f"Please answer: {f['label']}")
    p = dict(me.prefs or {})
    changed = False
    for k in ("location", "phone"):
        v = (getattr(body, k) or "").strip()[:160]
        if v and p.get(k) != v:
            p[k], changed = v, True
    if changed:
        me.prefs = p  # remembered for the next ticket
    det = C.details_of(db, me)
    t = C.create(db, system=s, problem_key=body.problem, description=body.description, answers=body.answers, requester=me, name=me.name, email=me.email,
                 department=(body.department or det["department"]), location=body.location or det["location"], phone=body.phone or det["phone"], source="app",
                 where=body.where, outcome=body.outcome)
    for name, data in files:  # 1.0.98: before the reading, so Iris sees the pictures
        try:
            C.add_file(db, t, name, data, me.name)
        except ValueError as e:
            raise HTTPException(400, str(e))
    if files:
        log(db, "support_ticket_files", actor=me, target=f"ticket:{C.ref(t)}", detail=", ".join(n for n, _ in files)[:300])
    C.start_reading(db, t)
    db.refresh(t)
    return {"ticket": C.ticket_out(db, t, full=True)}


@router.post("/tickets/upload")
async def open_ticket_with_files(ticket: str = Form(...), files: list[UploadFile] = File(default=[]), db: Session = Depends(get_db),
                                 me: User = Depends(require_human)):
    """1.0.98: the ticket and its files in one go, so Iris reads the pictures before the ticket is summed up."""
    import json

    if len(files or []) > C.MAX_FILES:
        raise HTTPException(400, f"A ticket holds at most {C.MAX_FILES} files.")
    try:
        body = TicketIn(**json.loads(ticket))
    except (ValueError, TypeError) as e:
        raise HTTPException(400, "The ticket form didn't come through — try again.") from e
    got = []
    for f in files or []:
        got.append((f.filename or "file", await read_capped(f, C.MAX_FILE, "file")))
    for name, data in got:  # every file is checked before the ticket is made
        if Path(name).suffix.lower() not in C.FILE_EXT or not data:
            raise HTTPException(400, f"{name}: that kind of file isn't accepted (pictures, PDF, Office files, text, zip), or it is empty.")
    return _open(db, me, body, got)


@router.get("/tickets/mine")
def my_tickets(db: Session = Depends(get_db), me: User = Depends(current_user)):
    rows = db.query(SupportTicket).filter((SupportTicket.requester_id == me.id) | ((SupportTicket.requester_id.is_(None)) & (SupportTicket.requester_email == (me.email or "").lower()))) \
        .filter(C.live()).order_by(SupportTicket.updated_at.desc()).limit(200).all()  # 1.0.109: never a removed one
    return {"tickets": [C.ticket_out(db, t) for t in rows]}


@router.get("/tickets/{tid}")
def one(tid: int, db: Session = Depends(get_db), me: User = Depends(current_user)):
    t = _ticket(db, tid)
    staff = C.is_staff(db, me) and C.staff_may_see(db, me, t)
    if not staff and (not _mine(t, me) or C.is_removed(t)):  # 1.0.109: a removed ticket is gone for the requester
        raise HTTPException(404, "No such ticket.")
    d = C.ticket_out(db, t, staff=staff, full=True, viewer=me)
    if staff and OWN.restricted(db, me) and OWN.is_free(t):
        d["word"] = "Free to take"  # 1.0.106: the support team's word for it (not "Ready to assign" — they don't assign)
    d["mine"] = _mine(t, me)
    d["can_assign"] = staff and C.can_assign(me)
    d["can_work"] = staff and C.can_work(db, me, t)
    # 1.0.103: a free ticket shows Take it (anyone on the desk); their own open one, Give back
    human = not me.is_axl and me.role != "viewer"
    d["free"] = staff and OWN.is_free(t)
    d["can_take"] = bool(staff and human and OWN.is_free(t))
    d["can_give_back"] = bool(staff and human and OWN.is_mine(t, me) and t.status in ("assigned", "discussing"))
    d["can_not_relevant"] = bool(staff and human and C.can_assign(me) and t.status != "closed")  # 1.0.103: the owner and admins only
    d["can_remove"] = bool(staff and human and C.can_assign(me) and not C.is_removed(t))  # 1.0.109
    d["can_restore"] = bool(staff and human and C.can_assign(me) and C.is_removed(t))
    if d["can_remove"]:
        d["remove_notice"] = OWN.notice(t)
    # 1.0.98: the lead it is assigned to accepts it (in the app too)
    d["can_accept"] = bool(t.assigned_to_id == me.id and not t.accepted_at and t.status in ("assigned", "discussing"))
    return d


@router.post("/tickets/{tid}/reply")
def reply(tid: int, body: TextIn, db: Session = Depends(get_db), me: User = Depends(require_human)):
    t = _ticket(db, tid)
    if not _mine(t, me) or C.is_removed(t):
        raise HTTPException(404, "No such ticket.")
    _do(C.requester_reply, db, t, body.text)
    if t.status == "new":
        C.start_reading(db, t)
    db.refresh(t)
    return C.ticket_out(db, t, full=True)


@router.post("/tickets/{tid}/files")
async def add_files(tid: int, files: list[UploadFile] = File(...), db: Session = Depends(get_db), me: User = Depends(require_human)):
    t = _ticket(db, tid)
    if not (_mine(t, me) or (C.is_staff(db, me) and C.staff_may_see(db, me, t))):
        raise HTTPException(404, "No such ticket.")
    got = []
    staff_upload = not _mine(t, me)
    for f in files[:C.MAX_FILES]:
        data = await read_capped(f, C.MAX_FILE, "file")
        try:
            got.append(C.add_file(db, t, f.filename or "file", data, me.name, author_id=me.id if staff_upload else None))
        except ValueError as e:
            raise HTTPException(400, str(e))
    log(db, "support_ticket_files", actor=me, target=f"ticket:{C.ref(t)}", detail=", ".join(x["name"] for x in got)[:300])
    return {"files": t.files}


@router.get("/tickets/{tid}/files/{n}")
def get_file(tid: int, n: int, view: bool = False, db: Session = Depends(get_db), me: User = Depends(current_user)):
    t = _ticket(db, tid)
    if not ((_mine(t, me) and not C.is_removed(t)) or (C.is_staff(db, me) and C.staff_may_see(db, me, t))):
        raise HTTPException(404, "No such ticket.")
    staff = C.is_staff(db, me) and C.staff_may_see(db, me, t)
    if not any(f.get("n") == n for f in C.files_seen(db, t, staff)):  # 1.0.98 fix: an invisible lead's file is not the requester's
        raise HTTPException(404, "That file isn't there.")
    p = C.file_path(t, n)
    if p is None:
        raise HTTPException(404, "That file isn't there.")
    name = p.name.split("_", 1)[-1]
    if view and C.is_picture(name):  # 1.0.109: a picture shown on the page (a thumbnail, full size) — only the kinds a browser shows
        kind = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "bmp": "image/bmp", "webp": "image/webp"}[name.rsplit(".", 1)[-1].lower()]
        return FileResponse(p, media_type=kind, filename=name, content_disposition_type="inline",
                            headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'", "Cache-Control": "private, max-age=3600"})
    return FileResponse(p, filename=name, headers={"X-Content-Type-Options": "nosniff"}, content_disposition_type="attachment")


# ---- support ------------------------------------------------------------------------------------------

@router.get("/dashboard")
def dash(db: Session = Depends(get_db), me: User = Depends(require_support)):
    from ..support import nudge as N

    if C.can_assign(me):
        I.repair_once(db)  # 1.0.109: an open ticket a robot opened (T-0018) moves to Automatic mails, once
    d = C.dashboard(db, me)
    if C.can_assign(me):
        d["mail"] = I.status(db)
        d["automatic"] = I.automatic_count(db)  # 1.0.109
    d["nudge"] = OWN.counts(db, me) if OWN.restricted(db, me) else N.counts(db)  # 1.0.98: waiting to be accepted; open over 48 h (1.0.103: their own)
    d["own_only"] = OWN.restricted(db, me)
    if d["own_only"]:  # 1.0.106: the support team sees its own queue, not the desk's workings — theirs, then the free ones by age
        from datetime import timedelta

        from ..db import utcnow

        open_ = C.queue(db, me, status="open", limit=200)
        month = utcnow() - timedelta(days=30)
        done = db.query(SupportTicket).filter(SupportTicket.assigned_to_id == me.id, SupportTicket.status == "closed", SupportTicket.closed_at >= month,
                                              (SupportTicket.closed_as == "") | SupportTicket.closed_as.is_(None)).count()
        d["mine"] = [x for x in open_ if x.get("assigned_to_id") == me.id][:20]
        d["free"] = [x for x in open_ if not x.get("assigned_to_id")][:20]
        d["mine_counts"] = {"open": sum(1 for x in open_ if x.get("assigned_to_id") == me.id), "free": sum(1 for x in open_ if not x.get("assigned_to_id")),
                            "done_30d": done}
    return d


@router.get("/queue")
def list_queue(system: str = "", status: str = "open", lead: str = "", mine: bool = False, age: int = 0, db: Session = Depends(get_db),
               me: User = Depends(require_support)):
    if status == C.REMOVED and not C.can_assign(me):
        raise HTTPException(403, "Only the owner or an admin sees the removed tickets.")
    own = OWN.restricted(db, me)
    # 1.0.109 (round 2): `age` — opened more than N days ago; status "with" — with someone
    # 1.0.111 (round 3): `status`, `system` and `lead` take comma lists (any of a filter's values; unknown values left out)
    return {"tickets": C.queue(db, me, system_id=system[:400], status=status[:400], lead_id=None if own else lead[:400], mine_only=mine, age=age),
            "systems": [{"id": s.id, "name": s.name} for s in C.systems(db, active_only=False)],
            # 1.0.109: the person picker — the support leads and everyone a ticket is with (the desk grant, an admin who took one by mail)
            "leads": [] if own else C.assignees(db), "own_only": own}  # 1.0.103: their own and the free ones


@router.post("/tickets/{tid}/assign")
def assign(tid: int, body: AssignIn, db: Session = Depends(get_db), me: User = Depends(require_assigner), _h: User = Depends(require_human)):
    t = _ticket(db, tid)
    lead = db.get(User, body.user_id)
    if lead is None or not lead.is_active:
        raise HTTPException(400, "Pick a support lead.")
    from ..support import suggest as SG

    before = SG.suggest(db, t) if t.status != "closed" else []  # 1.0.109 (round 2): what the desk suggested, before it changes
    _do(C.assign, db, t, lead, me)
    SG.record(db, t, before, lead.id, me, reason=body.why_not, text=body.why_text)
    d = C.ticket_out(db, t, staff=True, full=True, viewer=me)
    d["ask_why"] = SG.ask_why(db, before, lead.id, body.why_not)  # "Why not <name>?" — asked once, optional
    return d


@router.post("/tickets/{tid}/status")
def status(tid: int, body: StatusIn, db: Session = Depends(get_db), me: User = Depends(require_support), _h: User = Depends(require_human)):
    t = _ticket(db, tid)
    if not C.staff_may_see(db, me, t):
        raise HTTPException(404, "No such ticket.")
    _do(C.set_status, db, t, body.status, me, body.note)
    return C.ticket_out(db, t, staff=True, full=True, viewer=me)


@router.post("/tickets/{tid}/message")
def message(tid: int, body: MessageIn, db: Session = Depends(get_db), me: User = Depends(require_support), _h: User = Depends(require_human)):
    t = _ticket(db, tid)
    if not C.staff_may_see(db, me, t):
        raise HTTPException(404, "No such ticket.")
    if body.to_requester and not C.can_work(db, me, t):
        raise HTTPException(403, "Take it first: only the person it is assigned to, or the owner or an admin, writes to the requester. Add a note instead.")  # 1.0.103
    _do(lambda: C.staff_message(db, t, me, body.text, body.to_requester, wait=body.wait))
    return C.ticket_out(db, t, staff=True, full=True, viewer=me)


@router.post("/tickets/{tid}/reread")
def reread(tid: int, db: Session = Depends(get_db), me: User = Depends(require_assigner), _h: User = Depends(require_human)):
    t = _ticket(db, tid)
    if t.status not in ("new", "pending", "ready"):
        raise HTTPException(400, "It is already with a lead.")
    t.status = "new"
    log(db, "support_ticket_reread", actor=me, target=f"ticket:{C.ref(t)}")
    C.start_reading(db, t)
    db.refresh(t)
    return C.ticket_out(db, t, staff=True, full=True, viewer=me)


# ---- settings (owner / admin) ------------------------------------------------------------------------------

def _clean_problems(problems: list) -> list:
    out, keys = [], set()
    for p in problems[:30]:
        name = re.sub(r"\s+", " ", str((p or {}).get("name", ""))).strip()[:80]
        if not name:
            continue
        key = re.sub(r"[^a-z0-9]+", "-", str(p.get("key") or name).lower()).strip("-")[:40] or "area"
        while key in keys:
            key += "-2"
        keys.add(key)
        fields, fkeys = [], set()
        for f in (p.get("fields") or [])[:12]:
            label = re.sub(r"\s+", " ", str((f or {}).get("label", ""))).strip()[:120]
            if not label:
                continue
            fk = re.sub(r"[^a-z0-9]+", "_", str(f.get("key") or label).lower()).strip("_")[:40] or "q"
            while fk in fkeys:
                fk += "_2"
            fkeys.add(fk)
            kind = f.get("kind") if f.get("kind") in ("text", "multiline", "choice") else "text"
            choices = [str(c).strip()[:80] for c in (f.get("choices") or []) if str(c).strip()][:20] if kind == "choice" else []
            fields.append({"key": fk, "label": label, "kind": kind if (kind != "choice" or choices) else "text", "required": bool(f.get("required")), "choices": choices})
        out.append({"key": key, "name": name, "fields": fields})
    return out


class LeadIn(BaseModel):
    user_id: int
    system_ids: list[int] = []


class CoverIn(BaseModel):
    system_ids: list[int] = []


def _leads_out(db: Session) -> list[dict]:
    out = []
    for u in C.lead_people(db):
        cover = sorted(r.system_id for r in db.query(SupportLead).filter(SupportLead.user_id == u.id).all())
        open_n = db.query(SupportTicket).filter(SupportTicket.assigned_to_id == u.id, SupportTicket.status != "closed").count()
        out.append({"id": u.id, "name": u.name, "email": u.email, "role": u.role, "team": u.team.name if u.team else "", "systems": cover, "open": open_n})
    return out


@router.get("/settings")
def get_settings(db: Session = Depends(get_db), me: User = Depends(require_assigner)):
    people = db.query(User).filter(User.is_active.is_(True), User.is_axl.is_(False)).order_by(User.name).all()
    pool = {u.id for u in C.lead_people(db)}
    return {"systems": [C.system_out(s, db) for s in C.systems(db, active_only=False)], "leads": _leads_out(db),
            "people": [{"id": u.id, "name": u.name, "email": u.email, "role": u.role, "team": u.team.name if u.team else ""} for u in people if u.id not in pool],
            "mail": I.status(db)}


def _cover(db: Session, uid: int, system_ids: list[int]) -> None:
    want = {s.id for s in db.query(SupportSystem).filter(SupportSystem.id.in_(system_ids or [0])).all()}
    have = {r.system_id: r for r in db.query(SupportLead).filter(SupportLead.user_id == uid).all()}
    for sid, r in have.items():
        if sid not in want:
            db.delete(r)
    for sid in want - set(have):
        db.add(SupportLead(system_id=sid, user_id=uid))
    db.flush()


@router.post("/leads")
def add_lead(body: LeadIn, db: Session = Depends(get_db), me: User = Depends(require_assigner), _h: User = Depends(require_human)):
    u = db.get(User, body.user_id)
    if u is None or not u.is_active or u.is_axl:
        raise HTTPException(400, "Pick a person.")
    if not db.query(SupportPerson).filter(SupportPerson.user_id == u.id).first():
        db.add(SupportPerson(user_id=u.id, added_by_id=me.id))
        db.flush()
    if body.system_ids:  # 1.0.111 (review): no systems sent — Who works each system stays as it is (never wiped)
        _cover(db, u.id, body.system_ids)
    log(db, "support_lead_added", actor=me, target=f"user:{u.id}", detail=u.name)
    return {"leads": _leads_out(db)}


@router.put("/leads/{uid}")
def cover_lead(uid: int, body: CoverIn, db: Session = Depends(get_db), me: User = Depends(require_assigner), _h: User = Depends(require_human)):
    if not db.query(SupportPerson).filter(SupportPerson.user_id == uid).first():
        raise HTTPException(404, "That person isn't a support lead.")
    _cover(db, uid, body.system_ids)
    u = db.get(User, uid)
    names = ", ".join(s.name for s in db.query(SupportSystem).filter(SupportSystem.id.in_(body.system_ids or [0])).all()) or "no system"
    log(db, "support_lead_covers", actor=me, target=f"user:{uid}", detail=f"{u.name if u else uid}: {names}")
    return {"leads": _leads_out(db)}


@router.delete("/leads/{uid}")
def remove_lead(uid: int, db: Session = Depends(get_db), me: User = Depends(require_assigner), _h: User = Depends(require_human)):
    p = db.query(SupportPerson).filter(SupportPerson.user_id == uid).first()
    if p is None:
        raise HTTPException(404, "That person isn't a support lead.")
    if uid not in C.desk_ids(db):  # 1.0.111 (review): still on the desk (the Support desk grant) — they keep working their systems
        db.query(SupportLead).filter(SupportLead.user_id == uid).delete()
    db.delete(p)
    db.flush()
    u = db.get(User, uid)
    open_n = db.query(SupportTicket).filter(SupportTicket.assigned_to_id == uid, SupportTicket.status != "closed").count()
    log(db, "support_lead_removed", actor=me, target=f"user:{uid}", detail=f"{u.name if u else uid}{f' — still has {open_n} open ticket(s)' if open_n else ''}")
    return {"leads": _leads_out(db), "still_open": open_n}


@router.post("/systems")
def add_system(body: SystemIn, db: Session = Depends(get_db), me: User = Depends(require_assigner), _h: User = Depends(require_human)):
    name = re.sub(r"\s+", " ", body.name).strip()[:80]
    if not name:
        raise HTTPException(400, "Give it a name.")
    key = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "system"
    if db.query(SupportSystem).filter((SupportSystem.key == key) | (SupportSystem.name.ilike(name))).first():
        raise HTTPException(400, "There is one with that name already.")
    probs = _clean_problems(body.problems) or [{"key": "other", "name": "Something", "fields": []}]
    s = SupportSystem(key=key, name=name, blurb=body.blurb.strip()[:200], sort=body.sort, active=body.active, problems=probs)
    db.add(s)
    db.flush()
    log(db, "support_system_added", actor=me, target=f"support:{key}", detail=name)
    return C.system_out(s, db)


@router.put("/systems/{sid}")
def edit_system(sid: int, body: SystemIn, db: Session = Depends(get_db), me: User = Depends(require_assigner), _h: User = Depends(require_human)):
    s = db.get(SupportSystem, sid)
    if s is None:
        raise HTTPException(404, "No such system.")
    name = re.sub(r"\s+", " ", body.name).strip()[:80]
    if not name:
        raise HTTPException(400, "Give it a name.")
    s.name, s.blurb, s.active, s.sort = name, body.blurb.strip()[:200], body.active, body.sort
    s.problems = _clean_problems(body.problems) or [{"key": "other", "name": "Something", "fields": []}]
    log(db, "support_system_changed", actor=me, target=f"support:{s.key}", detail=f"{name} · {len(s.problems)} problem area(s){'' if s.active else ' · hidden'}")
    return C.system_out(s, db)


# ---- 1.0.74: "Mark as done" from the support person's mail ------------------------------------------------
# No sign-in: the link itself (signed, theirs, that ticket while it is theirs, 30 days) is the key. Opening it
# only shows the page — mail scanners open links by themselves — the ticket closes when they press the button.

def _done_page(title: str, body: str, status: int = 200) -> HTMLResponse:
    import html as H

    from ..branding import product_name

    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>{H.escape(title)} · {H.escape(product_name())}</title>
<style>body{{margin:0;background:#f4f6f5;font:15px/1.5 'Segoe UI',Arial,sans-serif;color:#1f2937}}main{{max-width:560px;margin:48px auto;padding:0 16px}}
.card{{background:#fff;border:1px solid #e3e8e5;border-radius:12px;padding:24px}}h1{{font-size:20px;margin:0 0 6px}}.muted{{color:#6b7280;font-size:13px}}
textarea{{width:100%;box-sizing:border-box;font:inherit;padding:10px;border:1px solid #cfd8d3;border-radius:8px;margin:6px 0 12px}}
button{{background:#0b7a52;color:#fff;border:0;border-radius:8px;padding:10px 18px;font:inherit;font-weight:600;cursor:pointer}}
.k{{color:#6b7280;font-size:13px;margin-top:10px}}.err{{color:#b42318}}</style></head>
<body><main><div class="card">{body}</div><p class="muted" style="text-align:center">{H.escape(product_name())} Support</p></main></body></html>"""
    return HTMLResponse(page, status_code=status, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


def _done_form(t, token: str, err: str = "") -> HTMLResponse:
    import html as H

    return _done_page(f"{C.ref(t)} — mark as done", f"""<h1>Mark {H.escape(C.ref(t))} as done</h1><div class="muted">{H.escape(t.title)}</div>
<div class="k">Where the problem is</div><div>{H.escape(t.problem_where or '(not said)')}</div>
<div class="k">What must be done</div><div>{H.escape(t.outcome or '(not said)')}</div>
<form method="post" action="/api/support/done/{H.escape(token, quote=True)}" style="margin-top:16px">
<label for="note"><b>What was done?</b> <span class="muted">— {H.escape(t.requester_name.split()[0] if t.requester_name else 'the requester')} gets this in the mail</span></label>
<textarea id="note" name="note" rows="4" maxlength="3000" required minlength="5"></textarea>{f'<p class="err">{H.escape(err)}</p>' if err else ''}
<button type="submit">Mark as done</button></form>""")


@router.get("/done/{token}", response_class=HTMLResponse, include_in_schema=False)
def done_page(token: str, db: Session = Depends(get_db)):
    t, u, why = C.read_done_token(db, token)
    if why:
        return _done_page("Mark as done", f"<h1>Mark as done</h1><p>{why}</p>", 410 if t is not None else 404)
    return _done_form(t, token)


@router.post("/done/{token}", response_class=HTMLResponse, include_in_schema=False)
def done_press(token: str, note: str = Form(""), db: Session = Depends(get_db)):
    import html as H

    t, u, why = C.read_done_token(db, token)
    if why:
        return _done_page("Mark as done", f"<h1>Mark as done</h1><p>{why}</p>", 410 if t is not None else 404)
    note = (note or "").strip()
    if len(note) < 5:
        return _done_form(t, token, "Say in a line what was done — the requester is told.")
    try:
        C.set_status(db, t, "closed", u, note)
    except (ValueError, PermissionError) as e:
        return _done_form(t, token, str(e))
    log(db, "support_ticket_done_by_link", actor=u, target=f"ticket:{C.ref(t)}", detail=note[:200])
    db.commit()
    first = t.requester_name.split()[0] if t.requester_name else "The requester"
    return _done_page(f"{C.ref(t)} done", f"<h1>Done — thank you</h1><p>{H.escape(C.ref(t))} is closed and off your to-do. {H.escape(first)} has been told what was done.</p>")


# ---- 1.0.98 (from CBX-Connect 2.10.x): Accept, My settings, the escalation person, what the desk learned, the team -------

class SystemPick(BaseModel):
    system_id: Optional[int] = None


class SettingsIn(BaseModel):
    schedule: str = "daily"
    every_days: int = 3
    weekday: int = 0
    at_time: str = "07:45"
    invisible: Optional[bool] = None


class PersonSettingsIn(SettingsIn):
    user_id: int


class EscalationIn(BaseModel):
    user_id: Optional[int] = None


class LessonIn(BaseModel):
    active: Optional[bool] = None
    question: Optional[str] = None
    words: Optional[list] = None


@router.post("/tickets/{tid}/accept")
def accept_in_app(tid: int, db: Session = Depends(get_db), me: User = Depends(require_human)):
    """The lead it is assigned to takes it on (the SLA starts; it goes on their To-do). Twice does no harm."""
    t = _ticket(db, tid)
    if not (C.is_staff(db, me) and C.staff_may_see(db, me, t)) and t.assigned_to_id != me.id:
        if _mine(t, me):
            raise HTTPException(403, "Only the lead it is assigned to accepts it.")
        raise HTTPException(404, "No such ticket.")
    got = _do(C.accept, db, t, me)
    return {"result": got, "ticket": C.ticket_out(db, t, staff=True, full=True, viewer=me)}


@router.post("/tickets/{tid}/system")
def set_system(tid: int, body: SystemPick, db: Session = Depends(get_db), me: User = Depends(require_support), _h: User = Depends(require_human)):
    t = _ticket(db, tid)
    if not C.staff_may_see(db, me, t):
        raise HTTPException(404, "No such ticket.")
    _do(C.set_system, db, t, body.system_id, me)
    return C.ticket_out(db, t, staff=True, full=True, viewer=me)


@router.post("/tickets/{tid}/take")
def take(tid: int, db: Session = Depends(get_db), me: User = Depends(require_support), _h: User = Depends(require_human)):
    """1.0.103: anyone on the desk takes a free ticket: it is theirs (as an assign), accepted at once. Of two people pressing
    at once, the second hears who was first."""
    if me.role == "viewer":
        raise HTTPException(403, "Viewers only look.")
    t = db.get(SupportTicket, tid)
    if t is None or (not C.staff_may_see(db, me, t) and not OWN.taken_lately(db, t)):
        raise HTTPException(404, "No such ticket.")
    try:
        OWN.take(db, t, me)
    except OWN.Taken as e:
        if OWN.restricted(db, me) and not OWN.taken_lately(db, t):  # 1.0.103 (review): only a fresh Take names who has it
            raise HTTPException(404, "No such ticket.")
        raise HTTPException(409, f"Already taken by {e.name}.")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return C.ticket_out(db, t, staff=True, full=True, viewer=me)


@router.post("/tickets/{tid}/give-back")
def give_back(tid: int, db: Session = Depends(get_db), me: User = Depends(require_support), _h: User = Depends(require_human)):
    """1.0.103: their own open ticket back to the desk, free for anyone to take (a taken ticket is never stuck)."""
    t = _ticket(db, tid)
    if not C.staff_may_see(db, me, t):
        raise HTTPException(404, "No such ticket.")
    try:
        OWN.give_back(db, t, me)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return C.ticket_out(db, t, staff=True, full=True, viewer=me)


class NotRelevantIn(BaseModel):
    reason: str = ""
    tell: bool = False


@router.post("/tickets/{tid}/not-relevant")
def not_relevant(tid: int, body: NotRelevantIn, db: Session = Depends(get_db), me: User = Depends(require_assigner), _h: User = Depends(require_human)):
    """1.0.103: the owner or an admin closes a ticket that doesn't make sense (spam, not a support request, a duplicate)
    without assigning it. The requester is told only when ticked."""
    t = _ticket(db, tid)
    _do(OWN.close_not_relevant, db, t, me, body.reason, body.tell)
    return C.ticket_out(db, t, staff=True, full=True, viewer=me)


class RemoveIn(BaseModel):
    tell: bool = False
    reason: str = ""
    message: str = ""


@router.post("/tickets/{tid}/remove")
def remove(tid: int, body: RemoveIn, db: Session = Depends(get_db), me: User = Depends(require_assigner), _h: User = Depends(require_human)):
    """1.0.109: the owner or an admin removes a ticket — the requester told once (their message, through the outbox) or silently.
    It is gone from every list and count; the owner and admins find it under Removed."""
    t = _ticket(db, tid)
    _do(lambda: OWN.remove(db, t, me, tell=body.tell, reason=body.reason, message=body.message))
    return C.ticket_out(db, t, staff=True, full=True, viewer=me)


@router.post("/tickets/{tid}/restore")
def restore(tid: int, db: Session = Depends(get_db), me: User = Depends(require_assigner), _h: User = Depends(require_human)):
    t = _ticket(db, tid)
    _do(OWN.restore, db, t, me)
    return C.ticket_out(db, t, staff=True, full=True, viewer=me)


@router.post("/new-problem/{mid}")
def new_problem(mid: int, db: Session = Depends(get_db), me: User = Depends(require_assigner), _h: User = Depends(require_human)):
    """1.0.109: "Make it a new ticket" — from the note on the old ticket; it waits free to take, nobody is told."""
    t = _do(I.new_problem_ticket, db, mid, me)
    return {"ticket": C.ticket_out(db, t, staff=True, viewer=me)}


@router.get("/automatic")
def automatic(db: Session = Depends(get_db), me: User = Depends(require_assigner)):
    """1.0.109: the admins' Automatic mails — one line each (auto-replies, another helpdesk's acknowledgements)."""
    I.repair_once(db)
    return {"mails": I.automatic(db)}


@router.post("/automatic/{mid}/dismiss")
def automatic_dismiss(mid: int, db: Session = Depends(get_db), me: User = Depends(require_assigner), _h: User = Depends(require_human)):
    _do(I.dismiss, db, mid, me)
    return {"mails": I.automatic(db)}


@router.post("/automatic/{mid}/ticket")
def automatic_ticket(mid: int, db: Session = Depends(get_db), me: User = Depends(require_assigner), _h: User = Depends(require_human)):
    t = _do(I.automatic_to_ticket, db, mid, me)
    return {"ticket": C.ticket_out(db, t, staff=True, viewer=me), "mails": I.automatic(db)}


def _accept_page(t, token: str, err: str = "") -> HTMLResponse:
    import html as H

    return _done_page(f"{C.ref(t)} — accept", f"""<h1>Accept {H.escape(C.ref(t))}</h1><div class="muted">{H.escape(t.title)}</div>
<div class="k">Where the problem is</div><div>{H.escape(t.problem_where or '(not said)')}</div>
<div class="k">What must be done</div><div>{H.escape(t.outcome or '(not said)')}</div>
<form method="post" action="/api/support/accept/{H.escape(token, quote=True)}" style="margin-top:16px">{f'<p class="err">{H.escape(err)}</p>' if err else ''}
<p class="muted">Accept it when you take it on — the time it takes counts from then, and it goes on your To-do.</p>
<button type="submit">Accept</button></form>""")


DEAD_ACCEPT = "This link has expired or isn't valid any more — the ticket may have gone to someone else. Open it in CBX-Connect instead."


@router.get("/accept/{token}", response_class=HTMLResponse, include_in_schema=False)
def accept_page(token: str, db: Session = Depends(get_db)):
    """Opening the link only shows the page (mail scanners open links by themselves); the button accepts."""
    t = C.by_accept_token(db, token)
    if t is None:
        return _done_page("Accept", f"<h1>Accept</h1><p>{DEAD_ACCEPT}</p>", 404)
    if t.status == "closed":
        return _done_page("Accept", f"<h1>Accept</h1><p>{C.ref(t)} is already done.</p>")
    if t.accepted_at:
        return _done_page("Accept", f"<h1>Accepted</h1><p>{C.ref(t)} was already accepted. Thank you.</p>")
    return _accept_page(t, token)


@router.post("/accept/{token}", response_class=HTMLResponse, include_in_schema=False)
def accept_press(token: str, db: Session = Depends(get_db)):
    import html as H

    t = C.by_accept_token(db, token)
    if t is None:
        return _done_page("Accept", f"<h1>Accept</h1><p>{DEAD_ACCEPT}</p>", 404)
    lead = db.get(User, t.assigned_to_id) if t.assigned_to_id else None
    if lead is None or not lead.is_active:
        return _done_page("Accept", f"<h1>Accept</h1><p>{DEAD_ACCEPT}</p>", 404)
    try:
        got = C.accept(db, t, lead)
    except (ValueError, PermissionError) as e:
        return _accept_page(t, token, str(e))
    db.commit()
    if got == "already":
        return _done_page(f"{C.ref(t)} accepted", f"<h1>Accepted</h1><p>{H.escape(C.ref(t))} was already accepted — nothing changed. Thank you.</p>")
    if got == "done":
        return _done_page(f"{C.ref(t)} done", f"<h1>Done</h1><p>{H.escape(C.ref(t))} is already done.</p>")
    log(db, "support_ticket_accepted_by_link", actor=lead, target=f"ticket:{C.ref(t)}")
    db.commit()
    return _done_page(f"{C.ref(t)} accepted", f"<h1>Accepted. Thank you.</h1><p>{H.escape(C.ref(t))} is on your To-do. Mark it done from its mail or in CBX-Connect.</p>")


# ---- My settings ----------------------------------------------------------------------------------------------------

def _person(db: Session, me: User) -> User:
    from ..support import people as P

    if not P.is_person(db, me):
        raise HTTPException(403, "This is for the support team.")
    return me


@router.get("/my-settings")
def my_settings(db: Session = Depends(get_db), me: User = Depends(current_user)):
    from ..support import people as P

    if not P.is_person(db, me):
        return {"person": False}
    return {"person": True, "settings": P.out(db, me), "days": list(P.DAYS)}


@router.post("/my-settings")
def save_my_settings(body: SettingsIn, db: Session = Depends(get_db), me: User = Depends(require_human)):
    from ..support import people as P

    _person(db, me)
    try:
        P.save(db, P.get(db, me), body.model_dump(exclude_none=True), me.name or me.email)
    except P.Bad as e:
        raise HTTPException(400, str(e))
    return {"person": True, "settings": P.out(db, me)}


@router.get("/people")
def people_settings(db: Session = Depends(get_db), me: User = Depends(require_assigner)):
    from ..support import people as P

    esc = P.escalation(db)
    admins = [{"id": u.id, "name": u.name} for u in C.assigners(db)]
    return {"people": P.everyone(db), "escalate_to": esc.id if esc else 0, "admins": admins, "days": list(P.DAYS)}


@router.post("/people")
def save_person_settings(body: PersonSettingsIn, db: Session = Depends(get_db), me: User = Depends(require_assigner), _h: User = Depends(require_human)):
    from ..support import people as P

    u = db.get(User, body.user_id)
    if u is None or not u.is_active or u.is_axl or not P.is_person(db, u):
        raise HTTPException(404, "That person isn't on the support desk.")
    try:
        P.save(db, P.get(db, u), body.model_dump(exclude_none=True, exclude={"user_id"}), me.name or me.email)
    except P.Bad as e:
        raise HTTPException(400, str(e))
    return people_settings(db, me)


@router.post("/escalation")
def set_escalation(body: EscalationIn, db: Session = Depends(get_db), me: User = Depends(require_assigner), _h: User = Depends(require_human)):
    from ..support import people as P

    try:
        u = P.set_escalation(db, body.user_id, me)
    except P.Bad as e:
        raise HTTPException(400, str(e))
    return {"escalate_to": u.id if u else 0}


# ---- what the desk has learned (owner / admin) -----------------------------------------------------------------------

def _lessons_out(db: Session, page: int = 1, kind: str = "") -> dict:
    from ..support import learn as L

    q = db.query(SupportLesson)
    if kind in ("question", "route"):
        q = q.filter(SupportLesson.kind == kind)
    total = q.count()
    pages = max(1, -(-total // L.PAGE))
    page = min(max(1, page), pages)
    rows = q.order_by(SupportLesson.active.desc(), SupportLesson.id.desc()).offset((page - 1) * L.PAGE).limit(L.PAGE).all()
    return {"lessons": [L.lesson_out(x, db) for x in rows], "page": page, "pages": pages, "total": total,
            "active": db.query(SupportLesson).filter(SupportLesson.active.is_(True)).count()}


@router.get("/lessons")
def lessons(page: int = 1, kind: str = "", db: Session = Depends(get_db), me: User = Depends(require_assigner)):
    return _lessons_out(db, page, kind)


@router.post("/lessons/{lid}")
def edit_lesson(lid: int, body: LessonIn, db: Session = Depends(get_db), me: User = Depends(require_assigner), _h: User = Depends(require_human)):
    from ..support import learn as L

    ls = db.get(SupportLesson, lid)
    if ls is None:
        raise HTTPException(404, "No such lesson.")
    if body.question is not None and (ls.kind or "question") == "question":
        q = L.clean_question(body.question)
        if not q:
            raise HTTPException(400, "A lesson's question goes to requesters by itself: no links, addresses or numbers, and never a password, code, bank or ID detail.")
        ls.question = q
    if body.words is not None:
        ls.words = L.words_of(" ".join(str(w) for w in body.words))[:L.MAX_WORDS + 4]
    if body.active is not None:
        ls.active = bool(body.active)
        if ls.active:
            ls.overridden = 0
            db.flush()
            L.cap(db, keep=ls)
    ls.edited_by = (me.name or me.email or "")[:160]
    log(db, "support_lesson_changed", actor=me, target=f"lesson:{ls.id}", detail=f"{'on' if ls.active else 'off'}: {(ls.question or ', '.join(ls.words or []))[:200]}")
    db.flush()
    return _lessons_out(db)


@router.delete("/lessons/{lid}")
def delete_lesson(lid: int, db: Session = Depends(get_db), me: User = Depends(require_assigner), _h: User = Depends(require_human)):
    ls = db.get(SupportLesson, lid)
    if ls is None:
        raise HTTPException(404, "No such lesson.")
    log(db, "support_lesson_deleted", actor=me, target=f"lesson:{ls.id}", detail=(ls.question or ", ".join(ls.words or []))[:200])
    db.delete(ls)
    db.flush()
    return _lessons_out(db)


# ---- the team, live (the support team and admins) ---------------------------------------------------------------------

@router.get("/team")
def team(db: Session = Depends(get_db), me: User = Depends(require_support)):
    from ..support import nudge as N
    from ..support import team as TM

    d = TM.live(db)
    d["counts"] = N.counts(db)
    if OWN.restricted(db, me):  # 1.0.103: the support team never sees another person's ticket here either
        d["counts"] = OWN.counts(db, me)
        cache: dict[int, bool] = {}

        def ok(ticket, text: str) -> bool:
            """A line is shown only when every ticket it is about (its ref, and each T-0042 it names) is one they may see."""
            ids = ({int(ticket["id"])} if ticket else set()) | TM.tickets_named(text)
            for i in ids:
                if i not in cache:
                    t = db.get(SupportTicket, i)
                    cache[i] = bool(t and C.staff_may_see(db, me, t))
                if not cache[i]:
                    return False
            return True

        for b in d.get("bots", []):
            if not ok(b.get("ticket"), b.get("task", "")):
                b["ticket"], b["task"] = None, ""
            if not ok(b.get("last_ticket"), b.get("last", "")):
                b["last"], b["last_at"], b["last_ticket"] = "", "", None
        for k in ("ada", "axl"):
            d[k] = [x for x in d.get(k, []) if ok(x.get("ticket"), x.get("text", ""))]
        if isinstance(d.get("nudge"), dict):
            d["nudge"] = {**d["nudge"], "lines": []}  # whom Nudge reminded about which tickets: for the owner and the admins
    return d
