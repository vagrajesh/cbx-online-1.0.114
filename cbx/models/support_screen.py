"""1.0.109 (round 2, 23 Oct): Ask AXL inside a ticket — the short chat a desk person has with AXL about one ticket
(cbx/support/askaxl.py). Staff and admins only, never the requester. A new table (create_all makes it)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, UTCDateTime, utcnow


class SupportAxlChat(Base):
    """One line of the chat. role: person | axl. card (AXL's lines only): what AXL proposes, for a person to confirm —
    {kind: draft|reassign|system|not_relevant, …, state: open|sent|done|discarded|closed, by, at}."""

    __tablename__ = "support_axl_chat"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("support_tickets.id"), index=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(160), default="")
    role: Mapped[str] = mapped_column(String(8), default="person")
    text: Mapped[str] = mapped_column(Text, default="")
    card: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=dict)
    turn: Mapped[int] = mapped_column(Integer, default=0)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
