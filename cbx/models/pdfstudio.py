"""PDF Studio: templates (columns), batches of PDFs, profiles (rulesets) with versions,
extractions with per-cell provenance and scores, corrections, model calls, bench results."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base, UTCDateTime, utcnow

# text/number/money/date/list are read off the page. `filename` is the PDF's own name (never
# extracted, never a miss); `formula` is worked out from other columns (config["expr"]).
COLUMN_TYPES = ("text", "number", "money", "date", "list", "filename", "formula")
NO_RULE_TYPES = ("filename", "formula")  # column kinds that never get extraction rules
BATCH_STATUSES = ("uploading", "learning", "running", "review", "done", "failed")


class Template(Base):
    """What the user wants out: one row per PDF, these columns."""

    __tablename__ = "pdf_templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"), nullable=True)
    shared: Mapped[bool] = mapped_column(Boolean, default=True)  # visible to the team
    source_file_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    master_sheet_id: Mapped[Optional[int]] = mapped_column(ForeignKey("master_sheets.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    columns: Mapped[list["Column"]] = relationship(back_populates="template", order_by="Column.ordinal", cascade="all, delete-orphan")


class Column(Base):
    __tablename__ = "pdf_columns"
    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("pdf_templates.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer, default=1)
    key: Mapped[str] = mapped_column(String(64))  # snake_case
    label: Mapped[str] = mapped_column(String(120))
    ctype: Mapped[str] = mapped_column(String(16), default="text")
    required: Mapped[bool] = mapped_column(Boolean, default=True)  # False: blank when not on the page is fine
    critical: Mapped[bool] = mapped_column(Boolean, default=False)  # money fields → 99.5% gate
    hint: Mapped[str] = mapped_column(Text, default="")  # user's description of where it is
    # {"expr": "material_excl_vat * 1.10"} for formula columns; {"optional_reason": "..."} when C-EXA
    # (or an admin) made the column optional
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    template: Mapped[Template] = relationship(back_populates="columns")


class Profile(Base):
    """A learned layout. `key` matches a document family (e.g. utilities_bill.emfuleni)."""

    __tablename__ = "pdf_profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(120), unique=True)
    label: Mapped[str] = mapped_column(String(160))
    template_id: Mapped[Optional[int]] = mapped_column(ForeignKey("pdf_templates.id"), nullable=True)
    team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"), nullable=True)
    shared: Mapped[bool] = mapped_column(Boolean, default=True)
    live_version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("pdf_profile_versions.id"), nullable=True)
    docs_seen: Mapped[int] = mapped_column(Integer, default=0)
    accuracy: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | live | patch | below_gate
    created_by: Mapped[str] = mapped_column(String(32), default="axl")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    versions: Mapped[list["ProfileVersion"]] = relationship(
        back_populates="profile", order_by="ProfileVersion.number", cascade="all, delete-orphan", foreign_keys="ProfileVersion.profile_id"
    )


class ProfileVersion(Base):
    __tablename__ = "pdf_profile_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("pdf_profiles.id"), index=True)
    number: Mapped[int] = mapped_column(Integer, default=1)
    ruleset: Mapped[dict] = mapped_column(JSON, default=dict)  # see cbx.pdfstudio.profile.RULESET_SCHEMA
    notes: Mapped[str] = mapped_column(Text, default="")  # C-EXA's human-readable notes on the layout
    reason: Mapped[str] = mapped_column(Text, default="")  # why this version exists
    author: Mapped[str] = mapped_column(String(32), default="axl")  # axl | user:<id>
    backtest: Mapped[dict] = mapped_column(JSON, default=dict)  # {docs, passed, accuracy, by_column}
    approved_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    profile: Mapped[Profile] = relationship(back_populates="versions", foreign_keys=[profile_id])


class Batch(Base):
    __tablename__ = "pdf_batches"
    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("pdf_templates.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(160), default="")
    status: Mapped[str] = mapped_column(String(16), default="uploading")
    step: Mapped[str] = mapped_column(String(32), default="")  # human-readable progress
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    runs: Mapped[list] = mapped_column(JSON, default=list)  # [{n, accuracy, note, at}]
    accuracy: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    by_column: Mapped[dict] = mapped_column(JSON, default=dict)  # {key: accuracy}
    cells_total: Mapped[int] = mapped_column(Integer, default=0)
    cells_filled: Mapped[int] = mapped_column(Integer, default=0)
    cells_for_user: Mapped[int] = mapped_column(Integer, default=0)
    spend_zar: Mapped[float] = mapped_column(Float, default=0.0)
    ceiling_zar: Mapped[float] = mapped_column(Float, default=50.0)
    log: Mapped[list] = mapped_column(JSON, default=list)  # C-EXA's log [{at, text}]
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    documents: Mapped[list["Document"]] = relationship(back_populates="batch", order_by="Document.ordinal", cascade="all, delete-orphan")


class Document(Base):
    __tablename__ = "pdf_documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("pdf_batches.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer, default=1)
    file_id: Mapped[int] = mapped_column(ForeignKey("stored_files.id"))
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    pages: Mapped[int] = mapped_column(Integer, default=0)
    scanned: Mapped[bool] = mapped_column(Boolean, default=False)
    profile_id: Mapped[Optional[int]] = mapped_column(ForeignKey("pdf_profiles.id"), nullable=True)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued | read | ok | review | failed
    batch: Mapped[Batch] = relationship(back_populates="documents")
    extraction: Mapped[Optional["Extraction"]] = relationship(back_populates="document", uselist=False, cascade="all, delete-orphan")


class Extraction(Base):
    """One row of the output sheet. `cells` is {col_key: {value, raw, page, bbox, quote, method,
    quality, score, status}}; status ∈ ok | miss | absent | filled | trusted | flagged.
    `absent` = an optional column with no evidence on the page (blank is the right answer)."""

    __tablename__ = "pdf_extractions"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("pdf_documents.id"), unique=True)
    profile_version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("pdf_profile_versions.id"), nullable=True)
    cells: Mapped[dict] = mapped_column(JSON, default=dict)
    row_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    validators: Mapped[dict] = mapped_column(JSON, default=dict)  # {vat_ok, sum_ok, ...}
    history: Mapped[list] = mapped_column(JSON, default=list)  # earlier reads [{at, profile_version_id, row_score, cells}] (re-runs)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    document: Mapped[Document] = relationship(back_populates="extraction")


class Correction(Base):
    """A user filled or changed a cell. C-EXA verifies it against the PDF, then learns from it."""

    __tablename__ = "pdf_corrections"
    id: Mapped[int] = mapped_column(primary_key=True)
    extraction_id: Mapped[int] = mapped_column(ForeignKey("pdf_extractions.id"), index=True)
    column_key: Mapped[str] = mapped_column(String(64))
    old_value: Mapped[str] = mapped_column(Text, default="")
    new_value: Mapped[str] = mapped_column(Text)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    verified: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)  # found verbatim on the PDF?
    verify_note: Mapped[str] = mapped_column(Text, default="")
    user_answer: Mapped[str] = mapped_column(String(32), default="")  # other_source | trust_me | retract
    source_note: Mapped[str] = mapped_column(Text, default="")
    learned_version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("pdf_profile_versions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class PdfLexicon(Base):
    """C-EXA's vocabulary: one row per (what the column means, the printed label, where the value sits).

    `column_group` is the normalised meaning (`account`, `total`, `reading_prev` — see
    `cbx.pdfstudio.lexicon.group_for`), so a label learned on `account_number` is offered for
    `acc_no` too. `column_key` is the key it was first seen under, kept for the admin page.
    `confirmed`/`failed` are how often the label gave the right answer / the wrong one; `corrections`
    is how many of those confirmations came from a person fixing a cell — the strongest signal there
    is. `layouts` is the layout signatures it has worked on."""

    __tablename__ = "pdf_lexicon"
    __table_args__ = (UniqueConstraint("column_group", "label", "placement", name="uq_pdf_lexicon_entry"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    column_group: Mapped[str] = mapped_column(String(48), index=True)
    column_key: Mapped[str] = mapped_column(String(64), default="")
    label: Mapped[str] = mapped_column(String(120))
    placement: Mapped[str] = mapped_column(String(8), default="right")  # right | below | left
    ctype: Mapped[str] = mapped_column(String(16), default="")
    confirmed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    corrections: Mapped[int] = mapped_column(Integer, default=0)  # confirmations that came from a person's fix
    layouts: Mapped[list] = mapped_column(JSON, default=list)  # layout signatures this label worked on
    example: Mapped[str] = mapped_column(String(160), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class ModelCall(Base):
    __tablename__ = "model_calls"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    task: Mapped[str] = mapped_column(String(32))  # profile_author | repair | residual_read | ocr | assess | interview | chat | dev
    model: Mapped[str] = mapped_column(String(64))
    batch_id: Mapped[Optional[int]] = mapped_column(ForeignKey("pdf_batches.id"), nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    cost_zar: Mapped[float] = mapped_column(Float, default=0.0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    schema_retries: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(String(255), default="")


class BenchResult(Base):
    __tablename__ = "bench_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("pdf_templates.id"))
    model: Mapped[str] = mapped_column(String(64))
    runs_on: Mapped[str] = mapped_column(String(64), default="")
    docs: Mapped[int] = mapped_column(Integer, default=0)
    precision: Mapped[float] = mapped_column(Float, default=0)
    recall: Mapped[float] = mapped_column(Float, default=0)
    hallucination_rate: Mapped[float] = mapped_column(Float, default=0)
    cost_per_1000: Mapped[float] = mapped_column(Float, default=0)
    latency_s: Mapped[float] = mapped_column(Float, default=0)
    role: Mapped[str] = mapped_column(String(32), default="")  # first_pass | scanned | misses | profiles | challenger
    recommended: Mapped[bool] = mapped_column(Boolean, default=False)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
