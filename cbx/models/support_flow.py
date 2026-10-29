"""1.0.109 (round 2): who the desk suggested for a ticket and who a person (or AXL) picked — the assignee suggestions learn
from these rows (cbx/support/suggest.py). One row per assignment of a ticket that had suggestions, an AXL auto-assign, or an
undo of one. kind: pick (a person assigned it) | auto (AXL assigned it) | undo (an admin undid AXL's) | axl (a person confirmed
an "Ask AXL" reassign). reason: wrong_area | too_busy | on_leave | other | '' (why not the first suggestion)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, UTCDateTime, utcnow


class SupportPick(Base):
    __tablename__ = "support_picks"
    id: Mapped[int] = mapped_column(primary_key=True)
    # no foreign key: the picks are the desk's history (the learning reads them), kept when a ticket row is ever deleted
    ticket_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(8), default="pick", index=True)
    system_key: Mapped[str] = mapped_column(String(40), default="")
    area: Mapped[str] = mapped_column(String(40), default="")
    suggested: Mapped[list] = mapped_column(JSON, default=list)  # [{id, score}] as they were, best first
    suggested_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # the first suggestion
    picked_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    reason: Mapped[str] = mapped_column(String(16), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    by_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    by_name: Mapped[str] = mapped_column(String(160), default="")
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
