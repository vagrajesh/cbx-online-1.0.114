"""Requests (part 1), C-EXA assessment, interviews (C-EXA DM or human meeting), rules, tickets."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base, UTCDateTime, utcnow

# Request lifecycle. "sent" = user just submitted; C-EXA scores it → "new" (admins see it);
# approved → "interview"; ticket opened → "working"; sandbox run → "testing"; published → "ready".
REQUEST_STATUSES = ("sent", "new", "interview", "working", "testing", "ready", "declined", "cancelled")
IMPORTANCE = ("low", "medium", "high", "critical")


class Request(Base):
    __tablename__ = "requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[int] = mapped_column(Integer, unique=True, index=True)  # human id, e.g. 2293
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    # Part 1 — the user's own words, never edited by anyone else.
    problem: Mapped[str] = mapped_column(Text)
    impact: Mapped[str] = mapped_column(Text, default="")
    wish: Mapped[str] = mapped_column(Text, default="")  # "how you'd like it fixed"
    attachment_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="sent", index=True)
    # "user" = asked for through New request; "admin" = an admin created the workflow directly
    # (no interview, never shown in the Requests queue or My requests).
    origin: Mapped[str] = mapped_column(String(8), default="user")
    # C-EXA's assessment
    importance: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    importance_reason: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 0..1, can C-EXA do it herself
    confidence_reason: Mapped[str] = mapped_column(Text, default="")
    would_ask: Mapped[str] = mapped_column(Text, default="")
    wont_guess: Mapped[str] = mapped_column(Text, default="")
    assessed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    # approval
    approved_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    interview_mode: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)  # "axl" | "human"
    assignee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    decline_note: Mapped[str] = mapped_column(Text, default="")
    # what they want back (captured in interview)
    result_kind: Mapped[str] = mapped_column(String(32), default="")  # filled_sheet | excel | pdf_report | check
    cadence: Mapped[str] = mapped_column(String(32), default="")  # once | monthly | weekly | on_file
    recipients: Mapped[str] = mapped_column(String(255), default="")
    done_looks_like: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    rules: Mapped[list["Rule"]] = relationship(back_populates="request", order_by="Rule.ordinal", cascade="all, delete-orphan")
    interview: Mapped[Optional["Interview"]] = relationship(back_populates="request", uselist=False, cascade="all, delete-orphan")
    ticket: Mapped[Optional["Ticket"]] = relationship(back_populates="request", uselist=False)


class Rule(Base):
    """A business rule. `source` says who it came from; user-sourced or user-changed rules render green."""

    __tablename__ = "rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer, default=1)  # R1, R2, ...
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(8), default="admin")  # user | admin | axl
    changed_by_user: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    history: Mapped[list] = mapped_column(JSON, default=list)  # [{at, by, text}]
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    request: Mapped[Request] = relationship(back_populates="rules")

    @property
    def green(self) -> bool:
        return self.source == "user" or self.changed_by_user


class Interview(Base):
    __tablename__ = "interviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id"), unique=True)
    mode: Mapped[str] = mapped_column(String(8))  # axl | human
    lead_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)  # human lead
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | done
    plan: Mapped[list] = mapped_column(JSON, default=list)  # C-EXA's question plan [{q, why, asked}]
    summary: Mapped[str] = mapped_column(Text, default="")
    confidence_now: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    request: Mapped[Request] = relationship(back_populates="interview")
    messages: Mapped[list["InterviewMessage"]] = relationship(back_populates="interview", order_by="InterviewMessage.at", cascade="all, delete-orphan")


class InterviewMessage(Base):
    __tablename__ = "interview_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    interview_id: Mapped[int] = mapped_column(ForeignKey("interviews.id"), index=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    sender: Mapped[str] = mapped_column(String(8))  # axl | user | admin
    sender_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    rule_ids: Mapped[list] = mapped_column(JSON, default=list)  # rules captured from this message
    interview: Mapped[Interview] = relationship(back_populates="messages")


class Ticket(Base):
    """A build ticket: the unit admins work on — the request, and the tool folder that answers it. Mirrors request status."""

    __tablename__ = "tickets"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id"), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="working")  # working | testing | ready
    tool_slug: Mapped[str] = mapped_column(String(64), default="")  # the tool folder that answers the request (1.0.48)
    node_red_flow_id: Mapped[str] = mapped_column(String(64), default="")  # before 1.0.48 (Node-RED); kept for rollback
    flow_json: Mapped[dict] = mapped_column(JSON, default=dict)  # {id, label, nodes:[...]} — C-EXA's draft, then whatever Node-RED holds
    workers: Mapped[list] = mapped_column(JSON, default=list)  # user ids working on it (+ "axl")
    sandbox: Mapped[str] = mapped_column(String(16), default="")
    last_test: Mapped[dict] = mapped_column(JSON, default=dict)
    published_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    request: Mapped[Request] = relationship(back_populates="ticket")


class WorkflowRun(Base):
    """One run of a workflow that was exposed as a tool. CBX posts to Node-RED, Node-RED posts
    back to /api/workflows/runs/{id}/done with the run's `secret` (no cookie involved)."""

    __tablename__ = "workflow_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    tool_id: Mapped[int] = mapped_column(ForeignKey("tools.id"), index=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued | running | done | failed
    input_file_ids: Mapped[list] = mapped_column(JSON, default=list)
    output_file_ids: Mapped[list] = mapped_column(JSON, default=list)
    message: Mapped[str] = mapped_column(Text, default="")
    result_text: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    secret: Mapped[str] = mapped_column(String(64), default="")
