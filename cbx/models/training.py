"""Where each person is in each lesson. One row per person per lesson; nothing else is stored —
a lesson is a script in the browser (web/training/lessons.js), not data."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, UTCDateTime, utcnow


class TrainingProgress(Base):
    __tablename__ = "training_progress"
    __table_args__ = (UniqueConstraint("user_id", "lesson_id", name="uq_training_user_lesson"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    lesson_id: Mapped[str] = mapped_column(String(64), index=True)
    step: Mapped[int] = mapped_column(Integer, default=0)       # the step they are on (0-based)
    total: Mapped[int] = mapped_column(Integer, default=0)      # how many steps the lesson had when they last touched it
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    done_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    times_done: Mapped[int] = mapped_column(Integer, default=0)  # a lesson can be taken again
