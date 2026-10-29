"""Flow runs: every time a Flow (tools/<slug>/flows/<name>.json) runs, one row — who started it, as
whom it ran, every step it took, and where it is waiting (an approval, a timer). A new table:
`create_all` makes it."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, UTCDateTime, utcnow

RUN_STATUSES = ("running", "waiting", "done", "failed", "stopped", "cancelled")


class FlowRun(Base):
    __tablename__ = "flow_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    tool: Mapped[str] = mapped_column(String(64), index=True)
    flow: Mapped[str] = mapped_column(String(64), index=True)
    trigger: Mapped[str] = mapped_column(String(24), default="button")  # button | row_added | row_changed | schedule | retry
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    dry: Mapped[bool] = mapped_column(Boolean, default=False)  # a Builder test: nothing written, sent or called
    started_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    run_as_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    ctx: Mapped[dict] = mapped_column(JSON, default=dict)  # the values the steps made, trimmed
    log: Mapped[list] = mapped_column(JSON, default=list)  # [{step, type, ok, note, at}]
    resume_node: Mapped[str] = mapped_column(String(64), default="")
    resume_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    waiting: Mapped[dict] = mapped_column(JSON, default=dict)  # {kind: approval|wait, step, approvers: [ids], question, yes, no}
    stack: Mapped[list] = mapped_column(JSON, default=list)  # for_each frames to come back to after a pause
    note: Mapped[str] = mapped_column(Text, default="")
    steps_taken: Mapped[int] = mapped_column(Integer, default=0)
