"""PDF Studio → Harvest: a folder (local or OneDrive) watched continuously. Every new PDF is read
with the source's template, kept as a HarvestRow (all values + provenance) and appended to the
source's own master sheet. Sources never mix: Telkom's rows live apart from Swiftnet's."""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, Date, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, UTCDateTime, utcnow

SOURCE_KINDS = ("local", "onedrive")
SOURCE_STATUSES = ("idle", "running", "ok", "error", "paused")


class HarvestSource(Base):
    __tablename__ = "harvest_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))  # "Telkom"
    template_id: Mapped[int] = mapped_column(ForeignKey("pdf_templates.id"))
    kind: Mapped[str] = mapped_column(String(16), default="local")  # local | onedrive
    path: Mapped[str] = mapped_column(Text, default="")  # C:\Bills\Telkom, \\server\share\Telkom, or a OneDrive path under the configured root
    include_subfolders: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    every_minutes: Mapped[int] = mapped_column(Integer, default=15)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"), nullable=True)
    master_sheet_id: Mapped[Optional[int]] = mapped_column(ForeignKey("master_sheets.id"), nullable=True)
    date_column: Mapped[str] = mapped_column(String(64), default="")  # template column key used for date ranges; "" = first date column, else file date
    last_run_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)  # {files_seen, new_files, rows, skipped, runs, last_batch_id, last_duration_s}
    status: Mapped[str] = mapped_column(String(16), default="idle")
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class HarvestRow(Base):
    """One PDF of one source. `cells` is the extraction's cells (value, quote, page, score, status
    per column); `history` keeps earlier reads when a range is re-run with a newer profile."""

    __tablename__ = "harvest_rows"
    __table_args__ = (UniqueConstraint("source_id", "sha256", name="uq_harvest_source_sha"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("harvest_sources.id"), index=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("stored_files.id"))
    document_id: Mapped[Optional[int]] = mapped_column(ForeignKey("pdf_documents.id"), nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    origin: Mapped[str] = mapped_column(Text, default="")  # where it was found (folder path or OneDrive path)
    doc_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, index=True)
    cells: Mapped[dict] = mapped_column(JSON, default=dict)
    row_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok | review
    profile_version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("pdf_profile_versions.id"), nullable=True)
    history: Mapped[list] = mapped_column(JSON, default=list)  # [{at, profile_version_id, cells, row_score}]
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
