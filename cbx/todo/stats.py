"""Completion counting by complexity — not "how many tasks" but "how many small / medium / complex / long".

Periods are business-local (SAST, `cbx.util.local_today`): a week runs Monday–Sunday, a month and a year
are the calendar ones, lifetime is everything. A task counts in the period its `completed_at` falls in,
with the complexity frozen at that moment (`completed_complexity`) — later edits never move a number.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..models import TodoGoal, TodoTask, User
from ..models.todo import COMPLEXITIES, SCORE
from ..util import local_today

PERIODS = ("week", "month", "year", "lifetime")


def _tz():
    from ..config import settings

    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(settings.timezone)
    except Exception:  # noqa: BLE001 — Windows without tzdata
        return timezone(timedelta(hours=2))


def period_range(period: str, today: Optional[date] = None) -> tuple[Optional[date], Optional[date]]:
    """Inclusive local dates for a period; (None, None) for lifetime."""
    today = today or local_today()
    if period == "week":
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6)
    if period == "month":
        start = today.replace(day=1)
        nxt = (start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1))
        return start, nxt - timedelta(days=1)
    if period == "year":
        return today.replace(month=1, day=1), today.replace(month=12, day=31)
    if period == "lifetime":
        return None, None
    raise ValueError("Period must be week, month, year or lifetime.")


def utc_bounds(start: Optional[date], end: Optional[date]) -> tuple[Optional[datetime], Optional[datetime]]:
    """Local calendar days → the UTC instants that bound them (start 00:00 inclusive, end+1 00:00 exclusive)."""
    tz = _tz()
    lo = datetime.combine(start, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc) if start else None
    hi = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=tz).astimezone(timezone.utc) if end else None
    return lo, hi


def empty_counts() -> dict:
    return {c: 0 for c in COMPLEXITIES}


def _completed(db: Session, team_id: Optional[int], start: Optional[date], end: Optional[date], user_id: Optional[int] = None) -> list[TodoTask]:
    """Finished tasks in the window. For a person: everything they finished, on their team's board or inside
    a temp job. For a team: its own board plus the job tasks its people finished (a job is cross-team, so the
    work goes home with the person)."""
    q = db.query(TodoTask).filter(TodoTask.status == "done", TodoTask.completed_at.isnot(None))
    if user_id is not None:
        q = q.filter(TodoTask.assignee_id == user_id)
    else:
        members = db.query(User.id).filter(User.team_id == team_id).scalar_subquery()
        q = q.filter(or_(TodoTask.team_id == team_id, and_(TodoTask.job_id.isnot(None), TodoTask.assignee_id.in_(members))))
    lo, hi = utc_bounds(start, end)
    if lo is not None:
        q = q.filter(TodoTask.completed_at >= lo)
    if hi is not None:
        q = q.filter(TodoTask.completed_at < hi)
    return q.all()


def tally(tasks: Iterable[TodoTask]) -> dict:
    counts = empty_counts()
    for t in tasks:
        c = t.completed_complexity or t.complexity()
        if c in counts:
            counts[c] += 1
    total = sum(counts.values())
    return {"counts": counts, "total": total, "score": sum(SCORE[c] * n for c, n in counts.items())}


def _block(period: str, start: Optional[date], end: Optional[date], tasks: Iterable[TodoTask]) -> dict:
    out = tally(tasks)
    out.update({"period": period, "start": start.isoformat() if start else None, "end": end.isoformat() if end else None})
    return out


def person_stats(db: Session, user: User, period: str, today: Optional[date] = None) -> dict:
    """One person's finished tasks by class for the period (lead-only on the API, except for yourself)."""
    start, end = period_range(period, today)
    return _block(period, start, end, _completed(db, user.team_id, start, end, user_id=user.id))


def team_stats(db: Session, team_id: int, period: str, today: Optional[date] = None) -> dict:
    """The whole team's finished tasks by class — everyone on the team may see this."""
    start, end = period_range(period, today)
    return _block(period, start, end, _completed(db, team_id, start, end))


def people_stats(db: Session, team_id: int, people: Iterable[User], period: str, today: Optional[date] = None) -> list[dict]:
    """Per-person rows for the lead's table, one query for the whole team."""
    start, end = period_range(period, today)
    tasks = _completed(db, team_id, start, end)
    by_user: dict[int, list[TodoTask]] = {}
    for t in tasks:
        if t.assignee_id:
            by_user.setdefault(t.assignee_id, []).append(t)
    rows = []
    for u in people:
        b = tally(by_user.get(u.id, []))
        b.update({"user_id": u.id, "name": u.name, "initials": u.initials()})
        rows.append(b)
    rows.sort(key=lambda r: (-r["score"], r["name"]))
    return rows


def goal_count(db: Session, goal: TodoGoal, user_id: Optional[int] = None) -> int:
    """Finished tasks of the goal's class inside its period — for one person or the team."""
    tasks = _completed(db, goal.team_id, goal.period_start, goal.period_end, user_id=user_id)
    if goal.complexity == "any":
        return len(tasks)
    return sum(1 for t in tasks if (t.completed_complexity or t.complexity()) == goal.complexity)
