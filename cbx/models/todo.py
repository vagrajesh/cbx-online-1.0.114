"""Team To-do: a team's columns, its tasks (personal and team), the lead's goals and the reward days people
book. All new tables — `create_all` makes them; nothing in migrate.py is needed.

Privacy model (enforced in cbx/api/todo.py):
  personal task — seen by its owner and the team lead only;
  team task     — seen by the whole team (title, assignee, step progress) so the team can track and assist.

A board belongs to a team (`team_id`) OR to a temp job (`job_id`, cbx.models.todo.TodoJob) — never both.
A job is a temporary, cross-team board for one piece of work ("catch-up job"): its lead adds people from any
team (people from another team need that team's lead to approve), it has its own columns and tasks, and it is
finished when the work is done. Job tasks count towards each person's own stats and their home team's.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, Date, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, UTCDateTime, utcnow

TASK_KINDS = ("personal", "team")
JOB_STATUSES = ("active", "finished", "dissolved")
MEMBER_STATUSES = ("active", "requested", "declined", "released")
FINISHED_VISIBLE_DAYS = 7  # a finished job stays on members' Jobs tab this long; admins keep it for the record
TASK_STATUSES = ("open", "done", "dropped")
COMPLEXITIES = ("small", "medium", "complex", "long")
GOAL_CLASSES = ("any",) + COMPLEXITIES
REWARD_KINDS = ("day_off", "team_event", "other")
# A finished task is worth this many points — shown as a secondary number next to the counts.
SCORE = {"small": 1, "medium": 2, "complex": 4, "long": 6}
# How long a finished task may be pulled back out of Done (after that it stays counted).
UNCOMPLETE_HOURS = 24


class TodoColumn(Base):
    """One board column per team (or per job). `is_done` marks the column that finishes a task."""

    __tablename__ = "todo_columns"
    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"), nullable=True, index=True)
    job_id: Mapped[Optional[int]] = mapped_column(ForeignKey("todo_jobs.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(80))
    order: Mapped[int] = mapped_column(Integer, default=0)
    is_done: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class TodoTask(Base):
    __tablename__ = "todo_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"), nullable=True, index=True)
    job_id: Mapped[Optional[int]] = mapped_column(ForeignKey("todo_jobs.id"), nullable=True, index=True)  # a job task (always kind "team")
    title: Mapped[str] = mapped_column(String(200))
    notes: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(8), default="personal")  # personal | team
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    assignee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)  # None = open for taking
    column_id: Mapped[Optional[int]] = mapped_column(ForeignKey("todo_columns.id"), nullable=True, index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    complexity_override: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)  # lead's word beats the auto rule
    steps: Mapped[list] = mapped_column(JSON, default=list)  # [{id, text, done, done_at}]
    due: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    completed_complexity: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)  # frozen when finished
    status: Mapped[str] = mapped_column(String(8), default="open", index=True)  # open | done | dropped
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    # ---- complexity ------------------------------------------------------------------------
    def auto_complexity(self, today: Optional[date] = None) -> str:
        """steps ≤3 small, 4–7 medium, 8+ complex; `long` when the task spans 28+ days (due − start)
        or has 20+ steps."""
        n = len(self.steps or [])
        start = (self.started_at or self.created_at or utcnow()).date()
        if n >= 20 or (self.due and (self.due - start).days >= 28):
            return "long"
        if n >= 8:
            return "complex"
        if n >= 4:
            return "medium"
        return "small"

    def complexity(self) -> str:
        """What counts: the frozen value once finished, else the lead's override, else the auto rule."""
        if self.status == "done" and self.completed_complexity:
            return self.completed_complexity
        return self.complexity_override or self.auto_complexity()


class TodoGoal(Base):
    """A lead's objective for a period: "10 complex tasks this week → a work-from-home day".
    `per_person` — every person must reach the target themselves (day-off style) — or the team together."""

    __tablename__ = "todo_goals"
    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    target: Mapped[int] = mapped_column(Integer, default=1)
    complexity: Mapped[str] = mapped_column(String(8), default="any")  # any | small | medium | complex | long
    per_person: Mapped[bool] = mapped_column(Boolean, default=True)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    reward_kind: Mapped[str] = mapped_column(String(16), default="day_off")  # day_off | team_event | other
    reward_text: Mapped[str] = mapped_column(String(200), default="")
    reward_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)  # team_event: the lead sets the day
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class TodoRewardClaim(Base):
    """One booked reward day per person per goal."""

    __tablename__ = "todo_reward_claims"
    __table_args__ = (UniqueConstraint("goal_id", "user_id", name="uq_todo_claim_goal_user"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    goal_id: Mapped[int] = mapped_column(ForeignKey("todo_goals.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    day: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class TodoJob(Base):
    """A temp job: a cross-team board for one piece of work, dissolved (finished) when the work is done."""

    __tablename__ = "todo_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    purpose: Mapped[str] = mapped_column(Text, default="")
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(12), default="active", index=True)  # active | finished | dissolved
    due: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    finished_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)


class TodoJobMember(Base):
    """Who is on a job. Someone from another team is `requested` until that team's lead approves."""

    __tablename__ = "todo_job_members"
    __table_args__ = (UniqueConstraint("job_id", "user_id", name="uq_todo_job_member"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("todo_jobs.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    added_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="active", index=True)  # active | requested | declined | released
    requested_from_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)  # the member's team lead who must say yes
    decided_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    decided_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
