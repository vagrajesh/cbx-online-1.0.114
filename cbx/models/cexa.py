"""C-EXA as a team member: messages to people, check-ins, journal (her memory), developer proposals."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, UTCDateTime, utcnow


class CexaMessage(Base):
    """A message in a 1-on-1 between C-EXA and a person (either direction)."""

    __tablename__ = "axl_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    from_axl: Mapped[bool] = mapped_column(Boolean, default=True)
    kind: Mapped[str] = mapped_column(String(24), default="chat")  # chat | brief | needs_you | team | checkin | aside
    title: Mapped[str] = mapped_column(String(160), default="")
    text: Mapped[str] = mapped_column(Text)
    thinking: Mapped[str] = mapped_column(Text, default="")  # shown to owners only
    link: Mapped[str] = mapped_column(String(160), default="")  # in-app route, e.g. "#/cexa-dev"
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    cleared: Mapped[bool] = mapped_column(Boolean, default=False)  # 'Clear chat' hides it from the thread; audit keeps it
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class CheckIn(Base):
    __tablename__ = "axl_checkins"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    question: Mapped[str] = mapped_column(Text)
    options: Mapped[list] = mapped_column(JSON, default=list)
    answer: Mapped[str] = mapped_column(String(64), default="")
    answered_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    context: Mapped[dict] = mapped_column(JSON, default=dict)  # {task_id, load, ...}


class JournalEntry(Base):
    """C-EXA's memory. Scoped to work: layouts, preferences about how people like to work, lessons.
    Owners can read and delete any entry; C-EXA never stores personal or sensitive details."""

    __tablename__ = "axl_journal"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(24))  # learned | team | self | plan | idea
    subject: Mapped[str] = mapped_column(String(160), default="")  # "profile:emfuleni", "user:12"
    text: Mapped[str] = mapped_column(Text)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)


class Proposal(Base):
    """C-EXA as developer: Noticed → Building → Sign-off → Shipped (or Declined)."""

    __tablename__ = "axl_proposals"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    evidence_kind: Mapped[str] = mapped_column(String(32))  # requests | audit | errors | corrections | owner
    evidence: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="noticed")  # noticed | building | signoff | shipped | declined
    # stable "same problem" key (counts left out) so noticing the same thing again never makes a second card
    dedupe_key: Mapped[str] = mapped_column(String(160), default="", index=True)
    branch: Mapped[str] = mapped_column(String(120), default="")
    sandbox: Mapped[str] = mapped_column(String(16), default="")
    diff: Mapped[str] = mapped_column(Text, default="")
    tests: Mapped[dict] = mapped_column(JSON, default=dict)  # {passed, failed, new}
    version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("app_versions.id"), nullable=True)
    decided_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    task_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    priority: Mapped[int] = mapped_column(Integer, default=50)


class Fix(Base):
    """C-EXA's bug & fix tracker. Anything a person reports (or C-EXA notices) that is wrong, with the
    exact actions C-EXA took, so the owner can see what was fixed, how, and give feedback."""

    __tablename__ = "axl_fixes"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    area: Mapped[str] = mapped_column(String(48), default="")  # pdf-studio | requests | warehouse | ...
    reported_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    # open | investigating | fixed (data/config changed directly) | code_pending (proposal in sandbox) | shipped | wont_fix
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    kind: Mapped[str] = mapped_column(String(16), default="data")  # data | config | code
    actions: Mapped[list] = mapped_column(JSON, default=list)  # [{at, tool, args, result}] — what C-EXA did
    resolution: Mapped[str] = mapped_column(Text, default="")
    proposal_id: Mapped[Optional[int]] = mapped_column(ForeignKey("axl_proposals.id"), nullable=True)
    task_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    feedback: Mapped[str] = mapped_column(Text, default="")  # owner's verdict: works / still broken / note
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class VoiceCall(Base):
    """C-EXA rings someone inside CBX (1.0.64): it rings in their browser, she speaks, they answer by voice
    (or by typing), she asks what she was asked to ask, and the person who started the call gets the note.
    Only the words are kept — never the sound."""

    __tablename__ = "cexa_calls"
    id: Mapped[int] = mapped_column(primary_key=True)
    to_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    started_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    work_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # an agent's work it is for (cexa_work)
    purpose: Mapped[str] = mapped_column(String(300), default="")
    questions: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(12), default="ringing", index=True)  # ringing | live | done | declined | missed | ended
    transcript: Mapped[list] = mapped_column(JSON, default=list)  # [{who: cexa|them, text, at}]
    answers: Mapped[dict] = mapped_column(JSON, default=dict)  # question → what they said
    summary: Mapped[str] = mapped_column(Text, default="")
    lang: Mapped[str] = mapped_column(String(5), default="en")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    answered_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
