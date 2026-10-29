"""Support tickets (1.0.66): anyone reports a problem with a system (CBX-Connect, Chase, …) in the app or by
mail; C-EXA reads it, asks for what is missing, and writes a summary; the owner or an admin assigns it to a
support lead; the lead works it to done. See cbx/support/."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, UTCDateTime, utcnow


class SupportSystem(Base):
    """What a ticket is about (the first question): CBX-Connect, Chase, … — the owner adds more. Each has its
    problem areas, and each area the questions that identify the problem:
    problems = [{key, name, fields: [{key, label, kind: text|multiline|choice, choices: [], required: bool}]}]"""

    __tablename__ = "support_systems"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    blurb: Mapped[str] = mapped_column(String(200), default="")
    sort: Mapped[int] = mapped_column(Integer, default=100)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    problems: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class SupportPerson(Base):
    """A support lead (Support → Settings → Support leads): sees the Support menu and can be given tickets.
    Which systems they cover is SupportLead; a lead who covers none can be given any ticket by hand."""

    __tablename__ = "support_people"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    added_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class SupportLead(Base):
    """A support lead covers a system: its tickets are offered to them first."""

    __tablename__ = "support_leads"
    __table_args__ = (UniqueConstraint("system_id", "user_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    system_id: Mapped[int] = mapped_column(ForeignKey("support_systems.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class SupportTicket(Base):
    """One problem. status: new (she is reading it) | pending (waiting for the requester's answer) |
    ready (summary written, waiting to be assigned) | assigned | discussing | closed."""

    __tablename__ = "support_tickets"
    id: Mapped[int] = mapped_column(primary_key=True)
    system_id: Mapped[Optional[int]] = mapped_column(ForeignKey("support_systems.id"), nullable=True, index=True)
    problem_key: Mapped[str] = mapped_column(String(40), default="")
    title: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    answers: Mapped[dict] = mapped_column(JSON, default=dict)  # the problem area's questions → what they said
    requester_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    requester_name: Mapped[str] = mapped_column(String(160), default="")
    requester_email: Mapped[str] = mapped_column(String(255), default="", index=True)
    requester_department: Mapped[str] = mapped_column(String(160), default="")
    requester_location: Mapped[str] = mapped_column(String(160), default="")
    requester_phone: Mapped[str] = mapped_column(String(40), default="")
    source: Mapped[str] = mapped_column(String(8), default="app")  # app | mail
    problem_where: Mapped[str] = mapped_column(String(300), default="")  # 1.0.74: where the PROBLEM is (a site: country, city, building — or the page), not where they sit
    outcome: Mapped[str] = mapped_column(Text, default="")  # 1.0.74: what they need done, in their words
    todo_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 1.0.74: the support person's to-do (tools/todo, tasks)
    status: Mapped[str] = mapped_column(String(12), default="new", index=True)
    summary: Mapped[str] = mapped_column(Text, default="")  # C-EXA's, for the lead
    missing: Mapped[list] = mapped_column(JSON, default=list)  # what she asked for last
    asks: Mapped[int] = mapped_column(Integer, default=0)  # how many times she asked
    assigned_to_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    assigned_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    assigned_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    resolution: Mapped[str] = mapped_column(Text, default="")
    closed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    files: Mapped[list] = mapped_column(JSON, default=list)  # [{n, name, size, by, at}] under DATA_DIR/support/files/<id>/
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    # 1.0.98: from CBX-Connect 2.10.x (cbx/migrate.py adds them to an older install, nullable)
    accepted_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)  # the lead tapped Accept: the SLA starts here
    accepted_by: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, default="")
    accept_nonce: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, default="")  # a new one kills the old Accept link
    accept_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, default="", index=True)
    assign_seq: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=0)  # Nudge's rules fire once per assignment
    lead_ids: Mapped[Optional[list]] = mapped_column(JSON, nullable=True, default=list)  # every lead it has had (an invisible one stays hidden)
    seen: Mapped[Optional[list]] = mapped_column(JSON, nullable=True, default=list)  # what Iris read on the pictures (also the sha cache)
    sap: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=dict)  # the SAP block's fields, checked against CBX's SAP lists
    system_set_by: Mapped[Optional[str]] = mapped_column(String(8), nullable=True, default="")  # desk | person
    lessons: Mapped[Optional[list]] = mapped_column(JSON, nullable=True, default=list)  # lessons asked / route lessons used (+ "desk:…" marks)
    review: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=dict)  # who read it, Ada's check, C-EXA, the pictures note
    reader: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, default="")  # Mo, Lu or Kit
    # 1.0.103: which bot is on this ticket right now (cbx/busy.py): name, step, since, last sign of life (cbx/migrate.py adds them)
    busy_bot: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, default="")
    busy_step: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, default="")
    busy_since: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    busy_beat: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    # 1.0.103: the owner or an admin closed it as not relevant (status closed, the reason in resolution): never a solved
    # ticket — the dashboard's counts and the leads' figures leave it out; a reopen clears it (cbx/migrate.py adds it)
    closed_as: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, default="")
    # 1.0.109: removed by the owner or an admin (closed_as "removed"): {by, by_id, at, how: tell|silent|automatic, reason, was: {status,
    # closed_as, assigned_to_id}} — gone from every list and count; Restore brings it back (cbx/migrate.py adds it, nullable)
    removal: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=dict)
    # 1.0.109 (round 2, cbx/support/followup.py): the responsible person ({kind: user|other, id, name, email, by, at} — set on
    # the ticket; else the system's / the desk's default); the wait on the requester ({kind: axl|person, since, asked,
    # sent_at, escalated_at, escalated_to}) — waiting_since is set while the desk waits on the requester (the list's filter)
    owner: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=dict)
    followup: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=dict)
    waiting_since: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    # 1.0.109 (round 2, cbx/support/suggest.py): assigned by AXL ({lead_id, why, at, undone_by, undone_at})
    auto: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=dict)


class SupportMessage(Base):
    """The ticket's thread. who: requester | cexa | staff | note (staff only, never sent)."""

    __tablename__ = "support_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("support_tickets.id"), index=True)
    who: Mapped[str] = mapped_column(String(10), default="requester")
    author_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    author_name: Mapped[str] = mapped_column(String(160), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    via: Mapped[str] = mapped_column(String(8), default="app")  # app | mail
    mailed: Mapped[bool] = mapped_column(Boolean, default=False)  # sent to the requester by mail
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    # 1.0.109: pictures or files of this mail that were left out, and why ([{name, why}]) — an admin-only line under the message
    left_out: Mapped[Optional[list]] = mapped_column(JSON, nullable=True, default=list)
    # 1.0.109: a note about a mail (support_mail.id) — "New problem sent in this thread": the admins make it a ticket from here
    mail_ref: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # 1.0.109 (round 2): the message as written in the reply editor — safe HTML (cbx/support/rich.py: p br b i ul ol li and
    # <img data-n="N"> for ticket file N, where it was pasted); empty for a plain message or a mail that came in
    html: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default="")


class SupportMail(Base):
    """A mail read from the support mailbox — so it is never read twice, and what became of it."""

    __tablename__ = "support_mail"
    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[str] = mapped_column(String(400), unique=True)
    sender: Mapped[str] = mapped_column(String(255), default="", index=True)
    subject: Mapped[str] = mapped_column(String(300), default="")
    ticket_id: Mapped[Optional[int]] = mapped_column(ForeignKey("support_tickets.id"), nullable=True)
    outcome: Mapped[str] = mapped_column(String(24), default="")  # new_ticket | reply | ignored:<why>
    entry_id: Mapped[str] = mapped_column(String(400), default="")  # 1.0.71: where it is in Outlook — CBX answers in its thread
    conversation: Mapped[str] = mapped_column(String(200), default="", index=True)  # 1.0.73: Outlook's conversation — a reply without the ticket number still lands on its ticket
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    # 1.0.109: an automatic mail (an auto-reply, another helpdesk's acknowledgement): why, and its words — the admins' "Automatic mails"
    why: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, default="")
    text: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default="")


# ---- 1.0.98: from CBX-Connect 2.10.x ------------------------------------------------------------------------

class SupportSettings(Base):
    """A support person's own desk settings ("My settings"): when their digest comes, and Invisible (requesters never
    see their name, address or signature). schedule = {kind: daily|every|weekly, every_days, weekday (0 = Monday),
    at: "HH:MM", anchor: "YYYY-MM-DD"} in Johannesburg time. Not the old support_people (the lead list)."""

    __tablename__ = "support_settings"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    schedule: Mapped[dict] = mapped_column(JSON, default=dict)
    invisible: Mapped[bool] = mapped_column(Boolean, default=False)
    digest_checked_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)  # the last digest slot used up
    digest_sent_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    updated_by: Mapped[str] = mapped_column(String(160), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class SupportNotice(Base):
    """What Nudge sent (or counted): one row per ticket, rule and person, per assignment — so each fires once.
    rule: accept6 | accept12 | accept2d | open48 | digest. mail_id: the outbox row (mail_log) that carried it."""

    __tablename__ = "support_notices"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[Optional[int]] = mapped_column(ForeignKey("support_tickets.id"), nullable=True, index=True)
    rule: Mapped[str] = mapped_column(String(16))
    seq: Mapped[int] = mapped_column(Integer, default=0)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    mail_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class SupportLesson(Base):
    """What the desk learned. kind: question (a lead had to ask the requester something the desk missed: it is asked on
    matching new tickets) | route (a person moved a ticket to another system or lead: it decides the system when the
    readers can't tell, and pre-selects the lead). system: the system's key; lead_id: the CBX user (routes)."""

    __tablename__ = "support_lessons"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(12), default="question", index=True)
    system: Mapped[str] = mapped_column(String(40), default="")
    lead_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tcode: Mapped[str] = mapped_column(String(16), default="")
    problem: Mapped[str] = mapped_column(String(255), default="")
    words: Mapped[list] = mapped_column(JSON, default=list)
    question: Mapped[str] = mapped_column(Text, default="")
    why: Mapped[str] = mapped_column(Text, default="")
    example_ticket_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    example: Mapped[str] = mapped_column(String(255), default="")
    learned_from: Mapped[str] = mapped_column(String(255), default="")
    source: Mapped[str] = mapped_column(String(12), default="rules")  # rules | ai | person
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    used: Mapped[int] = mapped_column(Integer, default=0)
    seen: Mapped[int] = mapped_column(Integer, default=1)
    overridden: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    edited_by: Mapped[str] = mapped_column(String(160), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
