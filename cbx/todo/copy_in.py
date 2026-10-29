"""1.0.55: the To-do tool folder (tools/todo) replaces the built-in To-do page. Once, at start, the old
tasks, jobs, goals and reward days are copied into the folder's own tables — who added each and when
kept — so nobody starts from an empty list. The old tables are left as they were (nothing is deleted);
the copy is marked done in app_state and never runs again.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from .. import state
from ..audit import log

L = logging.getLogger(__name__)
KEY = "todo_copied_to_folder"
SIZE = {"small": "Small", "medium": "Medium", "complex": "Complex", "long": "Complex"}
MEMBER = {"active": "Active", "requested": "Requested", "declined": "Declined", "released": "Released"}


def _day(d) -> str | None:
    return d.isoformat()[:10] if d else None


def _at(d) -> str | None:
    return d.isoformat() if d else None


def _status(t) -> str:
    if t.status == "done":
        return "Done"
    return "Doing" if t.started_at else "To do"


def run_at_start(db: Session) -> dict:
    """Copy once. Returns what was copied ({} when there was nothing to do)."""
    from ..engine import loader
    from ..engine.store import Store
    from ..models.todo import TodoGoal, TodoJob, TodoJobMember, TodoRewardClaim, TodoTask

    if state.get(db, KEY):
        return {}
    t = loader.registry().tools.get("todo")
    if t is None or not all(n in t.data for n in ("tasks", "jobs", "job_members", "job_tasks", "goals", "reward_days")):
        return {}  # no sound To-do folder: the built-in page stays in use
    st = Store("todo")
    cols = {n: t.data[n].get("columns") or [] for n in t.data}
    if any(st.exists(n) and st.rows(n, cols[n], limit=1)["total"] for n in cols):
        # the folder's tables already have rows (a copy cut short, or people started on it): never copy twice
        state.set(db, KEY, "not copied — the To-do tool already had rows")
        return {}
    got = {"tasks": 0, "jobs": 0, "job_members": 0, "job_tasks": 0, "goals": 0, "reward_days": 0}

    def put(block: str, values: dict, by, at, unique=None):
        row = st.insert(block, cols[block], values, by, unique=unique, at=at)
        got[block] += 1
        return row["id"]

    jobs: dict[int, int] = {}
    for j in db.query(TodoJob).order_by(TodoJob.id).all():
        jobs[j.id] = put("jobs", {"title": j.title, "purpose": j.purpose or "", "lead": j.lead_id, "due": _day(j.due),
                                  "status": "Active" if j.status == "active" else "Finished"}, j.created_by_id or j.lead_id, _at(j.created_at))
    for m in db.query(TodoJobMember).order_by(TodoJobMember.id).all():
        if m.job_id in jobs:
            put("job_members", {"job": jobs[m.job_id], "person": m.user_id, "status": MEMBER.get(m.status, "Requested")}, m.added_by_id, _at(m.created_at), unique=[["job", "person"]])
    for x in db.query(TodoTask).filter(TodoTask.status != "dropped").order_by(TodoTask.id).all():
        steps = [{"text": s.get("text", ""), "done": bool(s.get("done"))} for s in (x.steps or []) if isinstance(s, dict)]
        if x.job_id:
            if x.job_id in jobs:
                put("job_tasks", {"job": jobs[x.job_id], "title": x.title, "who": x.assignee_id, "status": _status(x), "steps": steps, "due": _day(x.due)}, x.created_by_id, _at(x.created_at))
            continue
        team = x.kind == "team"
        who = (x.assignee_id or "") if team else (x.assignee_id or x.created_by_id)  # "" keeps an open team task open (None would start as "me")
        done = x.status == "done"
        put("tasks", {"title": x.title, "kind": "Team" if team else "Mine", "status": _status(x), "who": who, "steps": steps, "due": _day(x.due), "notes": x.notes or "",
                      "finished": _day(x.completed_at) if done else "", "done_by": (x.assignee_id or x.created_by_id) if done else ""}, x.created_by_id, _at(x.created_at))
    goals: dict[int, int] = {}
    for g in db.query(TodoGoal).filter(TodoGoal.active.is_(True)).order_by(TodoGoal.id).all():
        goals[g.id] = put("goals", {"title": g.title, "target": g.target, "size": SIZE.get(g.complexity) or "", "starts": _day(g.period_start), "ends": _day(g.period_end),
                                    "reward": g.reward_text or g.reward_kind.replace("_", " "), "counts": "Each person" if g.per_person else "The team"}, g.created_by_id, _at(g.created_at))
    for c in db.query(TodoRewardClaim).order_by(TodoRewardClaim.id).all():
        if c.goal_id in goals:
            put("reward_days", {"goal": goals[c.goal_id], "person": c.user_id, "day": _day(c.day)}, c.user_id, _at(c.created_at), unique=[["goal", "person"]])
    state.set(db, KEY, ", ".join(f"{k} {v}" for k, v in got.items()))
    log(db, "todo_copied_to_folder", target="tools/todo", detail=", ".join(f"{v} {k}" for k, v in got.items()))
    return got


def open_for(db: Session, me) -> int | None:
    """My open tasks in the To-do tool (tasks and job tasks with me as Who, not Done); None without the folder."""
    from ..engine import loader
    from ..engine.store import Store

    t = loader.registry().tools.get("todo")
    if t is None or "tasks" not in t.data:
        return None
    st, n = Store("todo"), 0
    for name in ("tasks", "job_tasks"):
        b = t.data.get(name)
        if b and st.exists(name):
            rows = st.rows(name, b.get("columns") or [], limit=2000, eq={"who": me.id})["rows"]
            n += sum(1 for r in rows if r.get("status") != "Done")
    return n
