"""The outbox: one row per email the app tried to send — to whom, about what, sent or not, by whom.

Mail used to leave the server with only an audit line behind it. Now that people send mail from
inside the tool (a message about one certificate, from the grid), the Mail page under Admin shows
this list, so "did it go?" has an answer without opening a log file. Bodies are kept in short (the
first few hundred characters), never the mailbox password, never a recipient's reply.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, UTCDateTime, utcnow


class MailLog(Base):
    __tablename__ = "mail_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    to: Mapped[str] = mapped_column(String(500))                 # where it was addressed (before any CBX_MAIL_ONLY_TO redirect)
    delivered_to: Mapped[str] = mapped_column(String(500), default="")  # where it actually went (the redirect, when one is set)
    subject: Mapped[str] = mapped_column(String(300))
    preview: Mapped[str] = mapped_column(Text, default="")        # first 600 characters of the body
    kind: Mapped[str] = mapped_column(String(24), default="compose")  # compose | docwatch | reminder | test | code
    ref: Mapped[str] = mapped_column(String(64), default="")      # e.g. "doc:123"
    sent_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    detail: Mapped[str] = mapped_column(String(400), default="")  # the mailer's sentence
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class MailDraft(Base):
    """1.0.71: automatic mail held for a person to check before it goes (Mailer → Drafts).

    Held when its kind is ticked "check first" on the Mailer's Settings. When mail goes out through Outlook,
    the draft is also saved in Outlook (the sending mailbox's Drafts) — as a reply in the requester's own
    mail thread when the ticket came by mail, so the chain is there. It can be sent, changed or thrown away
    in CBX; sent or deleted in Outlook instead, CBX notices and says so."""

    __tablename__ = "mail_drafts"
    id: Mapped[int] = mapped_column(primary_key=True)
    hold: Mapped[str] = mapped_column(String(40), default="")       # what held it: support:asked, reminder, flow
    kind: Mapped[str] = mapped_column(String(24), default="")
    ref: Mapped[str] = mapped_column(String(64), default="")
    ticket_id: Mapped[Optional[int]] = mapped_column(ForeignKey("support_tickets.id"), nullable=True, index=True)
    to: Mapped[str] = mapped_column(String(500), default="")       # the real recipients (before any test redirect)
    subject: Mapped[str] = mapped_column(String(300), default="")
    text: Mapped[str] = mapped_column(Text, default="")            # what a person edits: the message itself
    extra: Mapped[dict] = mapped_column(JSON, default=dict)        # how it is dressed: link, note, earlier messages (templates.build)
    reply_to: Mapped[str] = mapped_column(String(255), default="")
    thread_entry_id: Mapped[str] = mapped_column(String(400), default="")  # the mail it answers, in Outlook
    outlook_entry_id: Mapped[str] = mapped_column(String(400), default="")  # the draft saved in Outlook
    status: Mapped[str] = mapped_column(String(12), default="waiting", index=True)  # waiting | sent | failed | discarded | outlook
    detail: Mapped[str] = mapped_column(String(400), default="")
    edited: Mapped[bool] = mapped_column(Boolean, default=False)
    by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    decided_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
