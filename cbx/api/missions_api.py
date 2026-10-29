"""C-EXA's jobs and her team (1.0.49) — the Agents page. The owner only, and a person (never the AI).

    GET  /api/cexa/missions                       the jobs, newest first, and whether screenshots are on
    POST /api/cexa/missions                       {brief} give C-EXA a job
    GET  /api/cexa/missions/{id}                  everything: the plan, the team, the tasks, what happened
    GET  /api/cexa/missions/{id}/events/{eid}     one event with the prompt and the reply behind it
    GET  /api/cexa/missions/{id}/files/{name}     a screenshot the designer took
    POST /api/cexa/missions/{id}/answers          {answers: [..], skip} her questions answered
    POST /api/cexa/missions/{id}/go               the plan is good: start the team
    POST /api/cexa/missions/{id}/replan           {text} change the plan before Go
    POST /api/cexa/missions/{id}/pause|resume     hold the team after the step it is on / carry on
    POST /api/cexa/missions/{id}/stop             stop the job (the draft stays)
    POST /api/cexa/missions/{id}/note             {text} tell the team something while it works
    POST /api/cexa/missions/{id}/revise           {text} ask for changes once it is in for review
    POST /api/cexa/missions/{id}/close            done with it
    POST /api/cexa/missions/{id}/retry            a step that failed, again
    POST /api/cexa/screenshots/setup              install Playwright + Chromium for the designer (owner's click)
    GET  /cexa-preview/{token}                    the page the designer photographs (one-time token)
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import VERSION, jobs
from ..audit import log
from ..cexa import ceo, eyes, teams
from ..db import get_db
from ..models import AgentTeam, Mission, MissionEvent, User
from ..security.deps import client_ip, current_user, require_human, require_owner


def require_teams_view(user: User = Depends(current_user)) -> User:
    """The owner, or anyone whose role has "See C-EXA's teams" (the board's view — read-only)."""
    if not (user.is_owner or user.can("teams.view")) or user.is_axl:
        raise HTTPException(403, "You don't have access to this.")
    return user

router = APIRouter(prefix="/api/cexa", tags=["cexa"])
preview_router = APIRouter(tags=["cexa"])
extra_routers = (preview_router,)


class BriefIn(BaseModel):
    brief: str
    team: str = ""


class TeamIn(BaseModel):
    name: str | None = None
    word: str | None = None
    purpose: str | None = None
    enabled: bool | None = None
    owner_id: int | None = None
    schedule: dict | None = None


class TeamNew(BaseModel):
    name: str
    purpose: str = ""
    kind: str = "work"
    word: str = ""
    members: list[dict] = []
    schedule: dict | None = None


class MemberIn(BaseModel):
    name: str = ""
    role: str = "analyst"
    focus: str | None = None
    model: str | None = None
    lead: bool = False
    enabled: bool | None = None


class HandIn(BaseModel):
    to: str


class TextIn(BaseModel):
    text: str = ""


class AnswersIn(BaseModel):
    answers: list[str] = []
    skip: bool = False


def _mission(db: Session, mid: int) -> Mission:
    m = db.get(Mission, mid)
    if m is None:
        raise HTTPException(404, "No such job.")
    return m


def _full(db: Session, m: Mission) -> dict:
    db.expire_all()
    m = db.get(Mission, m.id)
    return ceo.mission_out(db, m, full=True)


def _do(db: Session, fn, *args):
    try:
        return fn(*args)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/missions")
def missions(team: str = "", db: Session = Depends(get_db), me: User = Depends(require_teams_view)):
    q = db.query(Mission)
    if team:
        q = q.filter(Mission.team_key == team)
    rows = q.order_by(Mission.id.desc()).limit(80).all()
    return {"missions": [ceo.mission_out(db, m) for m in rows], "screenshots": eyes.status(), "can_act": me.is_owner,
            "roles": {k: {"label": v[0], "what": v[1], "model": ceo.model_for_role(k)} for k, v in ceo.ROLE_INFO.items()}}


@router.post("/missions")
def create(body: BriefIn, request: Request, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    try:
        m = ceo.create(db, me, body.brief, team_key=body.team or None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _full(db, m)


@router.get("/missions/{mid}")
def one(mid: int, db: Session = Depends(get_db), me: User = Depends(require_teams_view)):
    out = ceo.mission_out(db, _mission(db, mid), full=True)
    out["can_act"] = me.is_owner
    if not me.is_owner:
        for e in out["events"]:
            e["has_prompt"] = False  # the prompts and replies are the owner's
    return out


@router.get("/missions/{mid}/document.md")
def document(mid: int, db: Session = Depends(get_db), me: User = Depends(require_teams_view)):
    from fastapi.responses import Response

    m = _mission(db, mid)
    if not m.document:
        raise HTTPException(404, "This job has no document.")
    name = re.sub(r"[^A-Za-z0-9]+", "-", m.title).strip("-")[:60] or f"job-{m.id}"
    return Response(m.document, media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{name}.md"', "X-Content-Type-Options": "nosniff"})


@router.post("/missions/{mid}/findings/{idx}/handoff")
def handoff(mid: int, idx: int, body: HandIn, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    m = _mission(db, mid)
    try:
        out = teams.finding_to_work(db, m, idx, me, body.to)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {**out, "mission": _full(db, m)}


# ---- the teams ------------------------------------------------------------------------------------------------


def _team(db: Session, key: str) -> AgentTeam:
    t = teams.team(db, key)
    if t is None:
        raise HTTPException(404, "No such team.")
    return t


@router.get("/teams")
def org(db: Session = Depends(get_db), me: User = Depends(require_teams_view)):
    out = teams.org_out(db)
    out["can_act"] = me.is_owner
    if me.is_owner:
        out["people"] = [{"id": u.id, "name": u.name} for u in db.query(User).filter(User.is_active.is_(True), User.is_axl.is_(False)).order_by(User.name).all()]
    return out


@router.post("/teams")
def team_create(body: TeamNew, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    """1.0.82: a new team of agents — a working team takes work with its hands; or one that builds, changes or advises."""
    try:
        t = teams.create_team(db, body.model_dump(exclude_none=True), me)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return teams.team_out(db, t, full=True)


@router.delete("/teams/{key}")
def team_delete(key: str, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    t = _team(db, key)
    try:
        teams.delete_team(db, t, me)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return {"ok": True}


@router.get("/teams/{key}")
def team_one(key: str, db: Session = Depends(get_db), me: User = Depends(require_teams_view)):
    return teams.team_out(db, _team(db, key), full=True)


@router.patch("/teams/{key}")
def team_update(key: str, body: TeamIn, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    t = _team(db, key)
    try:
        teams.update_team(db, t, body.model_dump(exclude_none=True), me)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return teams.team_out(db, t, full=True)


@router.post("/teams/{key}/members")
def member_add(key: str, body: MemberIn, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    t = _team(db, key)
    try:
        teams.set_member(db, t, body.model_dump(exclude_none=True), me)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return teams.team_out(db, t, full=True)


@router.patch("/teams/{key}/members/{member_id}")
def member_set(key: str, member_id: int, body: MemberIn, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    t = _team(db, key)
    try:
        teams.set_member(db, t, body.model_dump(exclude_none=True), me, member_id=member_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return teams.team_out(db, t, full=True)


@router.delete("/teams/{key}/members/{member_id}")
def member_remove(key: str, member_id: int, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    t = _team(db, key)
    try:
        teams.remove_member(db, t, member_id, me)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return teams.team_out(db, t, full=True)


@router.post("/teams/{key}/run")
def team_run(key: str, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    t = _team(db, key)
    if t.kind == "work":  # 1.0.82: a working team's lead gets its standing order now
        from ..cexa import loop as L

        w = teams.give_work(db, t, reason="you pressed Run now", by=me)
        if w is None:
            raise HTTPException(400, f"{t.name} is already on its standing work, or has no lead.")
        return {"work": L.out(db, w, full=True)}
    try:
        m = teams.start_run(db, t, reason="you pressed Run now", by=me)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if m is None:
        raise HTTPException(400, f"{t.name} is already on a run — it takes one at a time.")
    return _full(db, m)


class ContextIn(BaseModel):
    text: str


@router.put("/teams-context")
def context(body: ContextIn, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    teams.set_context(body.text)
    log(db, "cexa_team_context", actor=me, detail=f"{len(body.text)} characters")
    return {"context": teams.context_text()}


@router.get("/missions/{mid}/events/{eid}")
def event(mid: int, eid: int, db: Session = Depends(get_db), me: User = Depends(require_owner)):
    e = db.get(MissionEvent, eid)
    if e is None or e.mission_id != mid:
        raise HTTPException(404, "No such event.")
    return {"id": e.id, "kind": e.kind, "text": e.text, "detail": e.detail or {}, "at": e.at.isoformat() if e.at else None}


@router.get("/missions/{mid}/files/{name}")
def file(mid: int, name: str, db: Session = Depends(get_db), me: User = Depends(require_teams_view)):
    m = _mission(db, mid)
    if not re.match(r"^[A-Za-z0-9_.-]{1,120}\.png$", name):
        raise HTTPException(404, "No such file.")
    f = ceo.shots_dir(m) / name
    if not f.is_file():
        raise HTTPException(404, "No such file.")
    return FileResponse(f, media_type="image/png", headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"})


@router.post("/missions/{mid}/answers")
def answers(mid: int, body: AnswersIn, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    m = _mission(db, mid)
    try:
        ceo.answer(db, m, body.answers, skip=body.skip)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _full(db, m)


@router.post("/missions/{mid}/go")
def go(mid: int, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    m = _mission(db, mid)
    _do(db, ceo.go, db, m, me)
    return _full(db, m)


@router.post("/missions/{mid}/replan")
def replan(mid: int, body: TextIn, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    m = _mission(db, mid)
    _do(db, ceo.replan, db, m, body.text)
    return _full(db, m)


@router.post("/missions/{mid}/pause")
def pause(mid: int, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    m = _mission(db, mid)
    _do(db, ceo.pause, db, m, True)
    return _full(db, m)


@router.post("/missions/{mid}/resume")
def resume(mid: int, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    m = _mission(db, mid)
    _do(db, ceo.pause, db, m, False)
    return _full(db, m)


@router.post("/missions/{mid}/stop")
def stop(mid: int, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    m = _mission(db, mid)
    _do(db, ceo.stop, db, m, me)
    return _full(db, m)


@router.post("/missions/{mid}/note")
def note(mid: int, body: TextIn, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    m = _mission(db, mid)
    _do(db, ceo.note, db, m, body.text)
    return _full(db, m)


@router.post("/missions/{mid}/revise")
def revise(mid: int, body: TextIn, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    m = _mission(db, mid)
    _do(db, ceo.revise, db, m, body.text)
    return _full(db, m)


@router.post("/missions/{mid}/close")
def close(mid: int, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    m = _mission(db, mid)
    _do(db, ceo.close, db, m, me)
    return _full(db, m)


@router.post("/missions/{mid}/retry")
def retry(mid: int, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    m = _mission(db, mid)
    _do(db, ceo.retry, db, m)
    return _full(db, m)


@router.post("/screenshots/setup")
def screenshots_setup(request: Request, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    if eyes.SETUP["running"]:
        return eyes.status()
    log(db, "cexa_screenshots_setup", actor=me, detail="pip install playwright; playwright install chromium", ip=client_ip(request))
    db.commit()
    jobs.submit(eyes.setup_screenshots)
    return eyes.status()


@preview_router.get("/cexa-preview/{token}", include_in_schema=False)
def preview(token: str):
    """The page the designer photographs. Only with a live one-time token the server itself issued a
    moment ago; it carries a draft page's layout and made-up rows — nothing from the database."""
    payload = eyes.take(token) if re.match(r"^[A-Za-z0-9_-]{20,64}$", token or "") else None
    if payload is None:
        raise HTTPException(404, "Not found.")
    return HTMLResponse(eyes.preview_html(payload, VERSION), headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})
