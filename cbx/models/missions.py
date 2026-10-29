"""C-EXA's jobs (1.0.49). The owner gives C-EXA a job ("build a Project Manager tool"); she asks what she
needs to know, writes a plan, waits for Go, then runs a team of agents that each take tasks from the plan
and build it — a Builder draft of a tool, and core-code changes in a sandbox copy. Four new tables,
`create_all` makes them:

    cexa_missions   one row per job: the brief, her questions and the answers, the plan, where it stands
    cexa_agents     the team she put on the job: name, role, model, what each is on
    cexa_tasks      the plan broken into tasks (and the small steps inside each), who has them, what came out
    cexa_events     everything that happened, in order: what an agent said, wrote, tested, looked at — with
                    the prompt and the reply behind it, for the owner
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, UTCDateTime, utcnow

MISSION_STATUSES = ("reading", "asking", "planning", "plan", "working", "review", "done", "stopped", "failed")
TASK_STATUSES = ("todo", "working", "testing", "done", "problem", "skipped")
ROLES = ("lead", "developer", "core", "designer", "tester", "helper")


class Mission(Base):
    __tablename__ = "cexa_missions"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    team_key: Mapped[str] = mapped_column(String(32), default="build", index=True)  # which of C-EXA's teams has it (1.0.50)
    kind: Mapped[str] = mapped_column(String(16), default="build")  # build | change | report (a standing team's check) | advice | review
    document: Mapped[str] = mapped_column(Text, default="")  # what a report / advice / review job hands over (markdown)
    brief: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="asking", index=True)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    tool_slug: Mapped[str] = mapped_column(String(64), default="")  # the Builder draft the team works on
    questions: Mapped[list] = mapped_column(JSON, default=list)  # [{q, a}]
    plan: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[list] = mapped_column(JSON, default=list)  # what the owner told the team while it worked [{at, text}]
    round: Mapped[int] = mapped_column(Integer, default=1)  # 2, 3 … after "ask for changes"
    summary: Mapped[str] = mapped_column(Text, default="")  # her write-up at review
    state: Mapped[str] = mapped_column(Text, default="")  # one sentence: where it stands right now
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    calls: Mapped[int] = mapped_column(Integer, default=0)  # model calls spent on the job
    max_calls: Mapped[int] = mapped_column(Integer, default=150)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)


class MissionAgent(Base):
    __tablename__ = "cexa_agents"
    id: Mapped[int] = mapped_column(primary_key=True)
    mission_id: Mapped[int] = mapped_column(ForeignKey("cexa_missions.id"), index=True)
    name: Mapped[str] = mapped_column(String(40))  # "Dev-1"
    role: Mapped[str] = mapped_column(String(16))  # lead | developer | core | designer | tester | helper
    focus: Mapped[str] = mapped_column(String(200), default="")
    model: Mapped[str] = mapped_column(String(80), default="")
    status: Mapped[str] = mapped_column(String(16), default="waiting")  # waiting | working | done | problem
    doing: Mapped[str] = mapped_column(String(300), default="")
    task_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class MissionTask(Base):
    __tablename__ = "cexa_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    mission_id: Mapped[int] = mapped_column(ForeignKey("cexa_missions.id"), index=True)
    agent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("cexa_agents.id"), nullable=True)
    parent_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # a fix the designer asked for, under its page
    key: Mapped[str] = mapped_column(String(80))  # "page:overview"
    kind: Mapped[str] = mapped_column(String(16))  # data | flow | page | sample | words | readme | look | fix | test | code | summary
    target: Mapped[str] = mapped_column(String(64), default="")  # the data block / page / flow it is about
    title: Mapped[str] = mapped_column(String(300))
    detail: Mapped[str] = mapped_column(Text, default="")
    depends: Mapped[list] = mapped_column(JSON, default=list)  # keys of tasks that must be done first
    status: Mapped[str] = mapped_column(String(16), default="todo", index=True)
    steps: Mapped[list] = mapped_column(JSON, default=list)  # the small steps inside: [{text, done}]
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    output: Mapped[dict] = mapped_column(JSON, default=dict)  # what came out: files, problems, a score, a proposal id
    round: Mapped[int] = mapped_column(Integer, default=1)
    order: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)


class MissionEvent(Base):
    __tablename__ = "cexa_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    mission_id: Mapped[int] = mapped_column(ForeignKey("cexa_missions.id"), index=True)
    agent_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    task_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(16), default="said")  # said | plan | write | test | look | fix | problem | owner | done
    text: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)  # {prompt, reply, files, shot, problems, …}
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class AgentTeam(Base):
    """One of C-EXA's standing teams (1.0.50): Build, Change, Fix, Operations, Data care, Security,
    Governance, Advice, Reports, Knowledge — or one the owner adds. Seeded by cbx/cexa/teams.py."""

    __tablename__ = "cexa_teams"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(60))
    word: Mapped[str] = mapped_column(String(30), default="")  # the board's word for it: Growing, Fixing, Cleaning …
    purpose: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(16), default="report")  # the pipeline its jobs run: build | change | report | advice | review
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)  # the person accountable for it
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    schedule: Mapped[dict] = mapped_column(JSON, default=dict)  # {every: hour|day|weekday|week|month, at, day} — empty: only when given a job
    state: Mapped[dict] = mapped_column(JSON, default=dict)  # last_slot, last_seen_error, last_run_job …
    sort: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class TeamMember(Base):
    __tablename__ = "cexa_team_members"
    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("cexa_teams.id"), index=True)
    name: Mapped[str] = mapped_column(String(40))
    role: Mapped[str] = mapped_column(String(20))  # see cbx/cexa/teams.py ROLES
    focus: Mapped[str] = mapped_column(String(200), default="")
    model: Mapped[str] = mapped_column(String(80), default="")  # empty: the role's model
    lead: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sort: Mapped[int] = mapped_column(Integer, default=100)


class AgentWork(Base):
    """Work between C-EXA's agents (1.0.62): one agent hands another a task, the heartbeat hands a lead
    what it found, the owner hands anyone a job — and the questions and proposals that need a person.

    kind      task        an agent works it with its tools (cbx/cexa/loop.py) until it finishes or asks
              question    (not used on its own: a task that asks goes to status "waiting" with question/options)
              hire        an agent proposes a new agent — the owner presses Hire
              model       an agent proposes a model for a kind of bot — Use it / Download
              person      an agent proposes a task for a person — the owner presses Give it
    status    open → working → done | failed | stopped;  waiting (a person must answer);  declined
    """

    __tablename__ = "cexa_work"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(12), default="task", index=True)
    title: Mapped[str] = mapped_column(String(300))
    detail: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(12), default="open", index=True)
    to_member_id: Mapped[Optional[int]] = mapped_column(ForeignKey("cexa_team_members.id"), nullable=True, index=True)
    from_member_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    from_label: Mapped[str] = mapped_column(String(60), default="")  # heartbeat · the owner's name · an agent's name · chat
    source: Mapped[str] = mapped_column(String(12), default="agent")  # heartbeat | owner | agent | chat
    parent_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    fingerprint: Mapped[str] = mapped_column(String(80), default="", index=True)  # the heartbeat's: one open work per signal
    question: Mapped[str] = mapped_column(Text, default="")
    options: Mapped[list] = mapped_column(JSON, default=list)
    answer: Mapped[str] = mapped_column(Text, default="")
    answered_by_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    proposal: Mapped[dict] = mapped_column(JSON, default=dict)  # hire {name, role, team, model, focus} · model {tier, model, download} · person {to, title, due}
    plan: Mapped[list] = mapped_column(JSON, default=list)  # the agent's own steps [{text, done}]
    transcript: Mapped[list] = mapped_column(JSON, default=list)  # the loop's messages (trimmed), so a waiting task resumes
    result: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[int] = mapped_column(Integer, default=0)  # 0-100, the agent's own
    steps: Mapped[int] = mapped_column(Integer, default=0)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    tainted: Mapped[bool] = mapped_column(Boolean, default=False)  # read the web: no changing tools for the rest of it
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    answered_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    # 1.0.114: top-down — the goal this work is part of, the team that has the part, and where it sits in the goal's
    # tree (team: AXL's part for a team's lead · item: a lead's assignment to a member · request: a lead asking another
    # team's lead); the ids of the siblings that must be done first; what "done" means; how often it was redone; why it is stuck
    goal_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    team_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    level: Mapped[str] = mapped_column(String(8), default="")
    depends: Mapped[list] = mapped_column(JSON, default=list)
    done_when: Mapped[str] = mapped_column(Text, default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    blocker: Mapped[str] = mapped_column(Text, default="")


GOAL_STATUSES = ("planning", "running", "blocked", "waiting_you", "done", "failed", "stopped")
GOAL_SOURCES = ("owner", "admin", "schedule", "problem")


class Goal(Base):
    """1.0.114: a goal AXL breaks down from the top — the owner's or an admin's ("get the Telkom profiles fixed"), a team's
    schedule, or a problem the heartbeat found. AXL plans it into parts for team leads (cbx/cexa/goals.py); the leads assign
    items to their members and ask other teams; problems are solved inside (agent → lead → AXL) and only a goal still blocked
    after AXL's third try, or out of its budget, waits for a person (status waiting_you, with one recommendation)."""

    __tablename__ = "cexa_goals"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    why: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(12), default="owner", index=True)  # owner | admin | schedule | problem
    fingerprint: Mapped[str] = mapped_column(String(120), default="", index=True)  # one open goal per signal; cooldown after
    done_when: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[int] = mapped_column(Integer, default=2)  # 1 high · 2 normal · 3 low
    budget_calls: Mapped[int] = mapped_column(Integer, default=60)
    calls_used: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(12), default="planning", index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    tries: Mapped[int] = mapped_column(Integer, default=0)  # AXL's attempts at a blocker
    recommend: Mapped[str] = mapped_column(Text, default="")  # waiting_you: the one recommendation
    log: Mapped[list] = mapped_column(JSON, default=list)  # [{at, text}] what AXL decided, in order
    plan: Mapped[dict] = mapped_column(JSON, default=dict)  # {how: model|table, parts: [...]}
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)


class GoalPrecedent(Base):
    """1.0.114 (CBX 2's idea): an answer AXL or a person gave once, kept by key, so the same question is answered the
    same way without asking again. Never holds the owner's taps (policy ASK) — those are a person's every time."""

    __tablename__ = "cexa_precedents"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(120), index=True)
    question: Mapped[str] = mapped_column(Text, default="")
    answer: Mapped[str] = mapped_column(Text, default="")
    by: Mapped[str] = mapped_column(String(60), default="")  # AXL | a person's name
    uses: Mapped[int] = mapped_column(Integer, default=0)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
