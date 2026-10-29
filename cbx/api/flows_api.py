"""Flow runs and approvals (cbx/engine/flows.py).

    POST /api/flows/{slug}/{flow}/run          a Run button: start a flow started by a button
    GET  /api/flows/runs                        runs: owners/admins see all; everyone else what they started or must approve
    GET  /api/flows/runs/{id}                   one run with every step it took
    GET  /api/flows/waiting                     approvals waiting for me
    POST /api/flows/runs/{id}/decide            approve or decline (the people asked, or an owner)
    POST /api/flows/runs/{id}/cancel            owners/admins: stop a waiting or running run
    POST /api/flows/runs/{id}/retry             owners/admins: start the same flow again with the same trigger
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..audit import log
from ..db import get_db, utcnow
from ..engine import flows as FL
from ..engine import loader
from ..models import FlowRun, User
from ..security.deps import client_ip, current_user, require_admin, require_human
from .engine_api import can_use

router = APIRouter(prefix="/api/flows", tags=["flows"])


def _tool(db: Session, me: User, slug: str):
    t = loader.registry().tools.get(slug)
    if t is None or not can_use(db, me, t):
        raise HTTPException(404, "No such tool.")
    return t


class RunIn(BaseModel):
    values: dict = {}
    # a button on a table's row (1.0.54): the rule gets that row as {{trigger.row}}
    block: str = ""
    row: Optional[int] = None
    page: str = ""
    widget: str = ""


def _reached_gates(t, flow: str) -> list:
    from ..engine.loader import effective_layout

    out = []
    for name, p in (t.pages or {}).items():
        for sec in effective_layout(name, p).get("sections") or []:
            for col in sec.get("columns") or []:
                for w in (col or {}).get("widgets") or []:
                    if isinstance(w, dict) and w.get("type") == "progress" and any(isinstance(a, dict) and a.get("flow") == flow and a.get("reached") for a in w.get("actions") or []):
                        out.append(w)
    return out


@router.post("/{slug}/{flow}/run")
def run_button(slug: str, flow: str, body: Optional[RunIn] = None, request: Request = None, db: Session = Depends(get_db), me: User = Depends(current_user), _h: User = Depends(require_human)):
    t = _tool(db, me, slug)
    f = (t.flows or {}).get(flow)
    if f is None:
        raise HTTPException(404, "No such flow.")
    tr = f.get("trigger") or {}
    if tr.get("type") != "button":
        raise HTTPException(400, "This flow is not started by a button.")
    roles = tr.get("roles") or []
    if me.role == "viewer":  # 1.0.106: a viewer changes nothing — "Take it", "Done" … are changes (engine_api._writes says the same)
        raise HTTPException(403, "A viewer can look but not change anything.")
    if roles and me.role not in roles and not me.is_admin:
        raise HTTPException(403, "This button is not for your role.")
    if not loader.flow_open(t, flow, me.role):
        raise HTTPException(403, "This button is on pages that are not for your role.")
    data = {"values": (body.values if body else {}) or {}}
    if tr.get("data") and (not body or body.row is None or body.block != tr.get("data")):
        raise HTTPException(400, "This rule runs from a button on a row of its table.")
    if body and body.row is not None:
        from ..engine.store import Store
        from .engine_api import _block, _row_visible

        b = _block(db, me, t, body.block, request)
        row = Store(slug).get(body.block, b.get("columns") or [], int(body.row))
        if not _row_visible(db, b, me, row):
            raise HTTPException(404, "That row isn't here.")
        data["row"] = row
        data["data"] = body.block
        # a button that only shows once a goal is reached is also only run then (1.0.55) — wherever it is
        # started from: every progress block that holds this rule "only once reached" must say reached
        from .engine_api import _progress_rows

        gates = _reached_gates(t, flow)
        if gates:
            # the goal must be reached in the progress block(s) that show it (a block may show only some goals)
            seen = [g for w in gates for g in _progress_rows(db, me, t, w, w.get("source") or {})["rows"] if g.get("id") == row.get("id")]
            if not seen or not all((g.get("_progress") or {}).get("reached") for g in seen):
                raise HTTPException(400, "Not yet — the target is not reached.")
    try:
        run = FL.start(db, t, flow, trigger="button", starter=me, data=data)
    except FL.FlowError as e:
        raise HTTPException(400, str(e))
    return {"run": FL.run_out(db, run, me)}


def _visible(db: Session, me: User, run: FlowRun) -> bool:
    if me.is_admin:
        return True
    return run.started_by_id == me.id or me.id in ((run.waiting or {}).get("approvers") or []) or me.id in ((run.ctx or {}).get("_asked") or [])


@router.get("/runs")
def runs(tool: str = "", flow: str = "", status: str = "", limit: int = 100, db: Session = Depends(get_db), me: User = Depends(current_user)):
    q = db.query(FlowRun).filter(FlowRun.dry.is_(False))
    if tool:
        q = q.filter(FlowRun.tool == tool)
    if flow:
        q = q.filter(FlowRun.flow == flow)
    if status:
        q = q.filter(FlowRun.status == status)
    if not me.is_admin:
        pass  # filtered below: what they started, or were asked to approve
    rows = [r for r in q.order_by(FlowRun.id.desc()).limit(max(1, min(limit, 500)) * (1 if me.is_admin else 4)).all() if _visible(db, me, r)][:limit]
    return {"runs": [FL.run_out(db, r, me) for r in rows]}


@router.get("/waiting")
def waiting(db: Session = Depends(get_db), me: User = Depends(current_user)):
    rows = [r for r in db.query(FlowRun).filter(FlowRun.status == "waiting", FlowRun.dry.is_(False)).order_by(FlowRun.id.desc()).limit(500).all()
            if (r.waiting or {}).get("kind") == "approval" and me.id in ((r.waiting or {}).get("approvers") or [])]
    return {"runs": [FL.run_out(db, r, me, full=True) for r in rows]}


@router.get("/runs/{rid}")
def run_detail(rid: int, db: Session = Depends(get_db), me: User = Depends(current_user)):
    run = db.get(FlowRun, rid)
    if run is None or not _visible(db, me, run):
        raise HTTPException(404, "No such run.")
    return {"run": FL.run_out(db, run, me, full=True)}


class DecideIn(BaseModel):
    approve: bool
    note: str = ""


@router.post("/runs/{rid}/decide")
def decide(rid: int, body: DecideIn, request: Request, db: Session = Depends(get_db), me: User = Depends(current_user), _h: User = Depends(require_human)):
    run = db.get(FlowRun, rid)
    if run is None or not _visible(db, me, run):
        raise HTTPException(404, "No such run.")
    t = loader.registry().tools.get(run.tool)
    if t is None:
        raise HTTPException(409, "That tool is no longer here.")
    try:
        FL.decide(db, t, run, me, body.approve, body.note.strip())
    except FL.FlowError as e:
        raise HTTPException(400, str(e))
    return {"run": FL.run_out(db, run, me, full=True)}


@router.post("/runs/{rid}/cancel")
def cancel(rid: int, request: Request, db: Session = Depends(get_db), me: User = Depends(require_admin), _h: User = Depends(require_human)):
    run = db.get(FlowRun, rid)
    if run is None:
        raise HTTPException(404, "No such run.")
    if run.status not in ("waiting", "running"):
        raise HTTPException(400, "This run has already finished.")
    run.status, run.finished_at, run.waiting, run.resume_at = "cancelled", utcnow(), {}, None
    run.note = f"Cancelled by {me.name}"
    log(db, "flow_cancelled", actor=me, target=f"{run.tool}/{run.flow}", detail=f"run {run.id}", ip=client_ip(request))
    return {"run": FL.run_out(db, run, me, full=True)}


@router.post("/runs/{rid}/retry")
def retry(rid: int, request: Request, db: Session = Depends(get_db), me: User = Depends(require_admin), _h: User = Depends(require_human)):
    old = db.get(FlowRun, rid)
    if old is None:
        raise HTTPException(404, "No such run.")
    t = loader.registry().tools.get(old.tool)
    if t is None:
        raise HTTPException(409, "That tool is no longer here.")
    trig = dict((old.ctx or {}).get("trigger") or {})
    trig.pop("kind", None)
    starter = db.get(User, old.started_by_id) if old.started_by_id else me
    try:
        run = FL.start(db, t, old.flow, trigger="retry", starter=starter, data={**trig, "retry_of": old.id})
    except FL.FlowError as e:
        raise HTTPException(400, str(e))
    log(db, "flow_retried", actor=me, target=f"{old.tool}/{old.flow}", detail=f"run {old.id} → {run.id}", ip=client_ip(request))
    return {"run": FL.run_out(db, run, me, full=True)}
