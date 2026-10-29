"""Agents → Now (1.0.62): C-EXA's heartbeat, the work her agents pass between them, what waits for the owner,
and which model each kind of bot uses. Seeing it: the owner (and a role with "See C-EXA's teams").
Answering, giving work, models: the owner, as a person (never the AI).

    GET  /api/cexa/now                    heartbeat + waiting (questions, proposals) + active + recent
    GET  /api/cexa/work/{id}              one piece of work: plan, every step, the colleagues it handed on to
    POST /api/cexa/work                   {to, title, detail}  give an agent (or C-EXA) a piece of work
    POST /api/cexa/work/{id}/answer       {text}  answer a question / Hire / Use it / Download / Give it / Not now
    POST /api/cexa/work/{id}/stop         stop it (and what it handed on)
    POST /api/cexa/heartbeat/beat         a light beat now
    GET  /api/cexa/models                 installed models, what each kind of bot uses, suggestions, downloads
    POST /api/cexa/models/pick            {tier, model}  this kind of bot uses this model
    POST /api/cexa/models/pull            {name}  download a model from Ollama's library

1.0.114 — the top-down company (cbx/cexa/goals.py; shapes in tools/MERGE-1114-API.md):
    GET  /api/cexa/goals/summary          Home: goals in progress (top 5) + what waits for you
    GET  /api/cexa/goals                  the owner's board: goals, waiting for you, done today
    GET  /api/cexa/goals/{id}             one goal and its tree (team parts → items → requests)
    POST /api/cexa/goals                  {title, why?, done_when?, priority?}  an admin starts a goal (a person, never the AI)
    POST /api/cexa/goals/{id}/answer      {text}  the owner answers a goal that waits for him: Retry | Close it partly | Stop | a direction
    POST /api/cexa/goals/{id}/stop        stop it and its open work
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..audit import log
from ..cexa import heartbeat as HB
from ..cexa import loop as L
from ..cexa import models_fit as F
from ..db import get_db
from ..cexa import goals as G
from ..models import AgentWork, Goal, User
from ..security.deps import current_user, require_admin, require_human, require_owner
from .missions_api import require_teams_view

router = APIRouter(prefix="/api/cexa", tags=["cexa"])


class WorkIn(BaseModel):
    to: str = ""
    title: str
    detail: str = ""


class AnswerIn(BaseModel):
    text: str


class PickIn(BaseModel):
    tier: str
    model: str = ""


class PullIn(BaseModel):
    name: str


class GoalIn(BaseModel):
    title: str
    why: str = ""
    done_when: str = ""
    priority: int = 2


def _work(db: Session, wid: int) -> AgentWork:
    w = db.get(AgentWork, wid)
    if w is None:
        raise HTTPException(404, "No such work.")
    return w


@router.get("/now")
def now(db: Session = Depends(get_db), me: User = Depends(require_teams_view)):
    q = db.query(AgentWork)
    waiting = G.owner_asks(db).limit(30).all()  # 1.0.114: a goal's stuck work is AXL's to solve, not the owner's
    active = q.filter(AgentWork.status.in_((L.OPEN, L.WORKING, L.HELD))).order_by(AgentWork.id.desc()).limit(40).all()
    recent = q.filter(AgentWork.status.in_(L.ENDED)).order_by(AgentWork.finished_at.desc()).limit(20).all()
    return {"heartbeat": HB.status(db), "waiting": [L.out(db, w) for w in waiting], "active": [L.out(db, w) for w in active],
            "recent": [L.out(db, w) for w in recent], "desks": desks(db), "can_act": me.is_owner,
            "docs": _busy_docs(db),  # 1.0.103: the document each AP / Support bot is on now (a click opens it)
            "goals": G.board(db, me) if me.is_admin and not me.is_axl else None}  # 1.0.114: the owner's board (owner and admins only)


def _busy_docs(db: Session) -> dict:
    from .. import busy

    try:
        return busy.docs_by_bot(db)
    except Exception:  # noqa: BLE001 — the Office never fails on it
        return {}


def desks(db: Session) -> dict:
    """Per agent (by name; C-EXA under her own): how much work it has had — the office view's filing rack
    (every piece of work is a folder) and its plant (it grows with work done lately, droops when idle long)."""
    from datetime import timedelta

    from sqlalchemy import case, func

    from ..db import utcnow

    out: dict = {}
    since = utcnow() - timedelta(days=30)
    rows = (db.query(AgentWork.to_member_id, AgentWork.status, func.count(AgentWork.id), func.max(AgentWork.finished_at),
                     func.sum(case((AgentWork.finished_at >= since, 1), else_=0)))
            .filter(AgentWork.kind == "task").group_by(AgentWork.to_member_id, AgentWork.status).all())
    names: dict = {}
    for mid, status, n, last, recent in rows:
        if mid not in names:
            names[mid] = L._who(db, mid)
        d = out.setdefault(names[mid], {"total": 0, "open": 0, "done": 0, "done_30d": 0, "last_done": None})
        d["total"] += n
        if status in L.ENDED:
            if status == "done":
                d["done"] += n
                d["done_30d"] += int(recent or 0)
                if last and (d["last_done"] is None or last.isoformat() > d["last_done"]):
                    d["last_done"] = last.isoformat()
        else:
            d["open"] += n
    return out


@router.get("/work/{wid}")
def work_one(wid: int, db: Session = Depends(get_db), me: User = Depends(require_teams_view)):
    return L.out(db, _work(db, wid), full=True)


@router.post("/work")
def work_give(body: WorkIn, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    try:
        w = L.give(db, body.to, body.title, body.detail, me)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return L.out(db, w, full=True)


@router.post("/work/{wid}/answer")
def work_answer(wid: int, body: AnswerIn, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    w = _work(db, wid)
    try:
        L.answer(db, w, me, body.text)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return L.out(db, w, full=True)


@router.post("/work/{wid}/stop")
def work_stop(wid: int, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    w = _work(db, wid)
    L.stop(db, w, me)
    db.commit()
    return L.out(db, w, full=True)


@router.post("/heartbeat/beat")
def beat_now(db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    r = HB.beat(db, force_light=True)
    log(db, "cexa_heartbeat_pressed", actor=me, target="light", detail=f"{len((r.get('light') or {}).get('signals') or [])} signal(s)")
    db.commit()
    light = r.get("light") or {}
    return {"signals": [s["title"] for s in light.get("signals", [])], "made": light.get("made", []), "heartbeat": HB.status(db)}


@router.get("/models")
def models(fresh: bool = False, me: User = Depends(require_owner)):
    return F.advise(force=fresh)


@router.post("/models/pick")
def models_pick(body: PickIn, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    m = (body.model or "").strip()
    if m and not F.name_ok(m):
        raise HTTPException(400, "That is not an Ollama model name (e.g. qwen3:8b).")
    if m and F.installed() and F._find(F.installed(), m) is None:
        raise HTTPException(400, f"{m} is not installed yet — download it first.")
    try:
        F.set_pick(body.tier, m)
    except ValueError as e:
        raise HTTPException(400, str(e))
    log(db, "cexa_model_picked", actor=me, target=body.tier, detail=m or "(back to .env)")
    db.commit()
    return F.advise()


@router.post("/models/pull")
def models_pull(body: PullIn, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    try:
        started = F.pull(body.name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    log(db, "cexa_model_download", actor=me, target=body.name.strip().lower()[:80], detail="started" if started else "already downloading")
    db.commit()
    return F.advise()


# ---- 1.0.114: goals ----------------------------------------------------------------------------------------------------


def _goal(db: Session, gid: int) -> Goal:
    g = db.get(Goal, gid)
    if g is None:
        raise HTTPException(404, "No such goal.")
    return g


def require_company(user: User = Depends(current_user)) -> User:
    """The board: the owner and admins only (1.0.114 review — a role with "See C-EXA's teams" that is not admin-level sees
    the teams, never the goals and their trees). Plain users never see the company."""
    if user.is_axl or not user.is_admin:
        raise HTTPException(403, "You don't have access to this.")
    return user


@router.get("/goals/summary")
def goals_summary(db: Session = Depends(get_db), me: User = Depends(require_company)):
    return G.summary(db, me)


@router.get("/goals")
def goals_board(db: Session = Depends(get_db), me: User = Depends(require_company)):
    return G.board(db, me)


@router.get("/goals/{gid}")
def goal_one(gid: int, db: Session = Depends(get_db), me: User = Depends(require_company)):
    return G.out(db, _goal(db, gid), full=True)


@router.post("/goals")
def goal_start(body: GoalIn, db: Session = Depends(get_db), me: User = Depends(require_admin), _h: User = Depends(require_human)):
    try:
        g = G.create(db, body.title, why=body.why, done_when=body.done_when, priority=body.priority, source="owner" if me.is_owner else "admin", by=me)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return G.out(db, g, full=True)


@router.post("/goals/{gid}/answer")
def goal_answer(gid: int, body: AnswerIn, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    g = _goal(db, gid)
    try:
        G.answer(db, g, me, body.text)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return G.out(db, g, full=True)


@router.post("/goals/{gid}/stop")
def goal_stop(gid: int, db: Session = Depends(get_db), me: User = Depends(require_admin), _h: User = Depends(require_human)):
    g = _goal(db, gid)
    G.stop(db, g, me)
    db.commit()
    return G.out(db, g, full=True)
