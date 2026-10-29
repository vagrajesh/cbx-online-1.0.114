"""Chat: people talking to people inside the app, with C-EXA in the room when she is asked.

Three kinds of conversation and one shape for all of them:
  dm    two people. Private — nobody else, admins included, can read it.
  team  one per team, everyone on the team is in it by virtue of being on the team. Admins may read.
  room  a named group an admin or manager put together.

Messages are append-only, like the audit trail: no edit, no delete. What was said was said.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, UTCDateTime, utcnow


class Conversation(Base):
    __tablename__ = "chat_conversations"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(8), index=True)            # dm | team | room
    name: Mapped[str] = mapped_column(String(120), default="")          # rooms only
    team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"), nullable=True, index=True)
    dm_key: Mapped[str] = mapped_column(String(40), default="", index=True)  # "min:max" of the two user ids, so a pair has one DM
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_message_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)


class ConversationMember(Base):
    __tablename__ = "chat_members"
    __table_args__ = (UniqueConstraint("conversation_id", "user_id", name="uq_chat_member"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("chat_conversations.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    joined_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_read_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_conv_at", "conversation_id", "at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("chat_conversations.id"), index=True)
    sender_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)  # None = system line
    kind: Mapped[str] = mapped_column(String(8), default="text")        # text | axl | system
    text: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
