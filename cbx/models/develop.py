from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base, UTCDateTime, utcnow

TASK_STATUSES = ("todo", "working", "done")


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    detail: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="todo", index=True)
    assignee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    assignee_axl: Mapped[bool] = mapped_column(default=False)
    due: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    origin: Mapped[str] = mapped_column(String(32), default="manual")  # manual | request | proposal
    origin_ref: Mapped[str] = mapped_column(String(64), default="")
    order: Mapped[int] = mapped_column(Integer, default=0)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    done_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    trace: Mapped[list] = mapped_column(JSON, default=list)  # [{at, by, from, to}] status moves — feeds team pulse
    notes: Mapped[list["TaskNote"]] = relationship(back_populates="task", order_by="TaskNote.at", cascade="all, delete-orphan")


class TaskNote(Base):
    __tablename__ = "task_notes"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    by: Mapped[str] = mapped_column(String(16), default="user")  # user | axl
    by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    task: Mapped[Task] = relationship(back_populates="notes")
