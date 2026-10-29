"""My mail (1.0.69): the owner links a mailbox from the CBX server's Outlook (his personal e-mail) and three
agents look after it — the Scanner reads, the Sorter files, the Writer drafts and keeps his to-do.
See cbx/maildesk/core.py. Only the person who linked a mailbox ever sees it."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, UTCDateTime, utcnow


class MailLink(Base):
    """A mailbox in the server's Outlook, linked to a person."""

    __tablename__ = "mail_links"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    store: Mapped[str] = mapped_column(String(255))  # the mailbox's name in Outlook
    address: Mapped[str] = mapped_column(String(255), default="")
    folder: Mapped[str] = mapped_column(String(120), default="Inbox")
    since: Mapped[datetime] = mapped_column(UTCDateTime)  # mail received from here on
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_drafts: Mapped[bool] = mapped_column(Boolean, default=True)  # the Writer drafts replies on its own (into Drafts — never sent)
    auto_todo: Mapped[bool] = mapped_column(Boolean, default=True)
    last_scan_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    agents: Mapped[dict] = mapped_column(JSON, default=dict)  # per agent: {state, did, at}
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class MailItem(Base):
    """One mail the Scanner brought in, and what the agents made of it."""

    __tablename__ = "mail_items"
    __table_args__ = (UniqueConstraint("link_id", "message_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    link_id: Mapped[int] = mapped_column(ForeignKey("mail_links.id"), index=True)
    message_id: Mapped[str] = mapped_column(String(400))
    entry_id: Mapped[str] = mapped_column(String(400), default="")
    sender: Mapped[str] = mapped_column(String(255), default="", index=True)
    sender_name: Mapped[str] = mapped_column(String(160), default="")
    subject: Mapped[str] = mapped_column(String(300), default="")
    received_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    body: Mapped[str] = mapped_column(Text, default="")  # kept only until it is read, then cleared
    summary: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(24), default="", index=True)
    priority: Mapped[str] = mapped_column(String(8), default="normal")
    needs_reply: Mapped[bool] = mapped_column(Boolean, default=False)
    actions: Mapped[list] = mapped_column(JSON, default=list)  # [{task, due}]
    stage: Mapped[str] = mapped_column(String(10), default="new", index=True)  # new | read | sorted | done | failed
    filed_to: Mapped[str] = mapped_column(String(120), default="")
    rule_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    draft: Mapped[str] = mapped_column(Text, default="")
    draft_state: Mapped[str] = mapped_column(String(10), default="")  # "" | saved (in Outlook's Drafts) | failed | skipped
    todos: Mapped[list] = mapped_column(JSON, default=list)  # to-do titles added
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class MailRule(Base):
    """A filter: when a mail matches, give it a category, flag it, or move it into a folder.
    The Sorter proposes rules; it only ever applies the ones the owner approved."""

    __tablename__ = "mail_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    link_id: Mapped[int] = mapped_column(ForeignKey("mail_links.id"), index=True)
    name: Mapped[str] = mapped_column(String(160), default="")
    when: Mapped[dict] = mapped_column(JSON, default=dict)  # {sender: "contains", subject: "contains", category: "equals"}
    then: Mapped[dict] = mapped_column(JSON, default=dict)  # {category, folder, flag: bool, no_draft: bool}
    status: Mapped[str] = mapped_column(String(10), default="proposed", index=True)  # proposed | on | off | declined
    why: Mapped[str] = mapped_column(Text, default="")
    proposed_by: Mapped[str] = mapped_column(String(20), default="owner")
    hits: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class MailTemplate(Base):
    """A reply the Writer starts from: {first_name}, {subject}, {my_name} are filled in."""

    __tablename__ = "mail_templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    when: Mapped[str] = mapped_column(String(300), default="")  # when to use it, in words (the Writer reads this)
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(10), default="on")  # on | proposed | off
    used: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
