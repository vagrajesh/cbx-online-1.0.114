"""Entity Compliance (tool slug `doc-watch`) and Site Schedules (quote PDF → agreement docx).

Entity Compliance is a grid: companies (Entity) down the side, document types (DocType) across the top,
one WatchedDoc per cell. The old free-text register columns on watched_docs (entity, doc_type, client)
are kept — they mirror the linked rows, so C-EXA's briefings and older code keep working — and the
migration in cbx/docwatch/service.py gives every legacy line a company and a column."""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, Date, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, UTCDateTime, utcnow


class DocType(Base):
    """A column of the grid: "Fire Certificate", "Tax Clearance", "Working Permit"… Admins add these;
    every company then gets one cell per type."""

    __tablename__ = "doc_types"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    prefix: Mapped[str] = mapped_column(String(12), default="")  # short label in the column heading: FIRE, TAX, WP
    order: Mapped[int] = mapped_column(Integer, default=100)  # lower numbers sit further left
    warn_days: Mapped[int] = mapped_column(Integer, default=90)  # default warning window for new cells
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)  # "shown" — hidden columns keep their cells
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    @property
    def label(self) -> str:
        return (self.prefix or self.name[:12]).upper()


class Entity(Base):
    """A company / site whose documents are watched. team_id empty = everyone with the tool sees it;
    set = that team only (and its manager manages it)."""

    __tablename__ = "entities"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    notes: Mapped[str] = mapped_column(String(300), default="")
    team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"), nullable=True)
    # The controller answerable for this entity's documents — shown on the grid so everyone can see
    # who to chase. A name, because the list comes from finance and most of these people have no
    # account here; `responsible_user_id` links one up when they do, and only then can the app reach
    # them. (Named `responsible`, not `responsible`: the live grid already carries this column
    # with all 32 controllers in it, and a rename would have been a data migration for nothing.)
    responsible: Mapped[str] = mapped_column(String(80), default="")
    responsible_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    # An address for a controller who has no account here — most of the finance list does not. With
    # this set they get the expiry mail; without it their name is only a label on the grid.
    responsible_email: Mapped[str] = mapped_column(String(255), default="")
    # Archived = we no longer service this entity. It leaves the grid; every document it ever filed
    # stays exactly where it is. Deleting is for an entity added by mistake, not one you stopped
    # working with — the certificates from the years you did are still the record.
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    archived_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    archived_reason: Mapped[str] = mapped_column(String(200), default="")
    # Training entities: the two practice companies the Training lessons file certificates against.
    # They exist so a person can learn on the real screen without touching a real entity, and they
    # are kept OFF the normal grid, the reminder run and the counts — only `?training=1` shows them.
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class EntityAccess(Base):
    """One person may see one company. The narrow grant Edwin asked for: "when someone needs access
    to only Mozambique, I give only them that one folder". A person with any of these sees exactly
    those companies and nothing else on the grid — not their team's, not the ones open to everyone.
    Admins are never listed here; they see everything."""

    __tablename__ = "entity_access"
    __table_args__ = (UniqueConstraint("entity_id", "user_id", name="uq_entity_user"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    granted_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    note: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class WatchedDoc(Base):
    """One cell of the grid (entity × doc type), or an "other document" of a company when doc_type_id is
    empty. Holds the current file, what C-EXA read from it, the expiry and the history of renewals."""

    __tablename__ = "watched_docs"
    __table_args__ = (UniqueConstraint("entity_id", "doc_type_id", name="uq_entity_doctype_cell"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[Optional[int]] = mapped_column(ForeignKey("entities.id"), nullable=True)
    doc_type_id: Mapped[Optional[int]] = mapped_column(ForeignKey("doc_types.id"), nullable=True)
    # untick when this company does not need this document — the grid shows a grey dot
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    title: Mapped[str] = mapped_column(String(200))  # the document's name (defaults to the type's name)
    # mirrors of the linked rows (and the free text of lines that predate the grid)
    doc_type: Mapped[str] = mapped_column(String(120), default="")
    entity: Mapped[str] = mapped_column(String(160), default="")
    client: Mapped[str] = mapped_column(String(64), default="")
    team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"), nullable=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))  # who filed it — they get the reminders
    file_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    expiry: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    # A person marked it "doesn't expire" (deed, once-off certificate): no date, never in the reminder cycle.
    no_expiry: Mapped[bool] = mapped_column(Boolean, default=False)
    warn_days: Mapped[int] = mapped_column(Integer, default=90)
    expiry_source: Mapped[str] = mapped_column(String(16), default="")  # axl | user
    expiry_quote: Mapped[str] = mapped_column(Text, default="")
    scan: Mapped[dict] = mapped_column(JSON, default=dict)  # what C-EXA read from the current file
    history: Mapped[list] = mapped_column(JSON, default=list)  # earlier files: [{file_id, file_name, expiry, filed_at, replaced_at, by}]
    reminders_sent: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[str] = mapped_column(Text, default="")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class ScheduleJob(Base):
    __tablename__ = "schedule_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    quote_file_id: Mapped[int] = mapped_column(ForeignKey("stored_files.id"))
    site: Mapped[str] = mapped_column(String(160), default="")
    quote_no: Mapped[str] = mapped_column(String(64), default="")
    fields: Mapped[dict] = mapped_column(JSON, default=dict)  # parsed values with quotes
    issues: Mapped[list] = mapped_column(JSON, default=list)  # [{field, text, suggestion}]
    status: Mapped[str] = mapped_column(String(16), default="parsed")  # parsed | needs_input | built
    output_file_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
