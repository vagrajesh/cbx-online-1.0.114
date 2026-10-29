"""IFRS 16 as a page again (1.0.92), the same page as CBX-Connect 2 — the same tables (ifrs_uploads, ifrs_months,
ifrs_ledger_checks, ifrs_recons, ifrs_rates, ifrs_api_runs), so CBX-Connect 2 can take them as they are.
Keep the source files (an upload) → read them for their months → work out a month → the Lease Accelerator import
workbooks, the monthly report and the working papers; the ledger check; LA vs RR06. Each person sees their own;
admins see everyone's. The Rates table is shared. The Sequentra key stays in Settings → API connections."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, UTCDateTime, utcnow


class IfrsUpload(Base):
    """The source files, kept so a month can be worked out again without uploading them again."""

    __tablename__ = "ifrs_uploads"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    la_file_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    movements_file_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    sequentra_file_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    steps_file_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    assets_file_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    ciw_file_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="new")  # new | reading | ready | failed
    scan: Mapped[dict] = mapped_column(JSON, default=dict)  # {months:[{value,label,rows}], la_rows, move_rows, steps_rows, sequentra_rows, movement_label}
    note: Mapped[str] = mapped_column(Text, default="")
    old_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class IfrsMonth(Base):
    """One month worked out (only the engine adds these)."""

    __tablename__ = "ifrs_months"
    id: Mapped[int] = mapped_column(primary_key=True)
    upload_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ifrs_uploads.id"), nullable=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    month: Mapped[str] = mapped_column(String(7))  # YYYY-MM
    source: Mapped[str] = mapped_column(String(16), default="files")  # files | api
    use_charges: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="working")  # working | done | failed
    imports: Mapped[int] = mapped_column(Integer, default=0)
    workbooks_file_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    report_file_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    papers_file_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    recon: Mapped[str] = mapped_column(Text, default="")
    note: Mapped[str] = mapped_column(Text, default="")
    job_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    old_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)


class IfrsLedgerCheck(Base):
    __tablename__ = "ifrs_ledger_checks"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    ledger_file_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    # 2.8.3: the Sequentra validation export the report's discount rates came from
    sequentra_file_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    # 2.8.4: as the desktop's Report — the kept upload whose files fill the lease names, dates and entities; the
    # entity on the Recon tab; full life | from the as-at month | months back; the simple report beside the full one
    upload_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    entity: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, default="")
    span: Mapped[Optional[str]] = mapped_column(String(8), nullable=True, default="life")
    simple_file_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    as_at: Mapped[str] = mapped_column(String(10), default="")
    months_back: Mapped[int] = mapped_column(Integer, default=12)
    status: Mapped[str] = mapped_column(String(16), default="working")
    report_file_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    overview: Mapped[str] = mapped_column(Text, default="")
    note: Mapped[str] = mapped_column(Text, default="")
    job_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    old_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class IfrsRecon(Base):
    """2.8.3: the LA transaction report against the RR06 contract list (ABSA Bank Limited, South Africa)."""

    __tablename__ = "ifrs_recons"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    la_file_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    rr06_file_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    period: Mapped[str] = mapped_column(String(7), default="")  # YYYY-MM
    status: Mapped[str] = mapped_column(String(16), default="working")  # working | done | failed
    result: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=dict)  # overview, summary, the lists to drill into
    report_file_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    job_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class IfrsMaster(Base):
    """2.8.4: the desktop's Master report — a month worked out and (with a ledger export) the ledger check, every
    sheet in one workbook with a Contents page."""

    __tablename__ = "ifrs_masters"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    upload_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ifrs_uploads.id"), nullable=True)
    month: Mapped[str] = mapped_column(String(7), default="")
    ledger_file_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    as_at: Mapped[str] = mapped_column(String(10), default="")
    entity: Mapped[str] = mapped_column(String(160), default="")
    status: Mapped[str] = mapped_column(String(16), default="working")
    report_file_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    sheets: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(Text, default="")
    job_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class IfrsRate(Base):
    """SA discount rates, one row a month, bands A–F as treasury sends them (shared)."""

    __tablename__ = "ifrs_rates"
    id: Mapped[int] = mapped_column(primary_key=True)
    month: Mapped[str] = mapped_column(String(7), unique=True)
    a: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    b: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    c: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    d: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    e: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    f: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    note: Mapped[str] = mapped_column(String(300), default="")
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class IfrsApiRun(Base):
    """A Sequentra check or download (what each report returned; never the key)."""

    __tablename__ = "ifrs_api_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="check")  # check | download
    month: Mapped[str] = mapped_column(String(7), default="")
    to_month: Mapped[str] = mapped_column(String(7), default="")
    status: Mapped[str] = mapped_column(String(16), default="working")
    ok: Mapped[str] = mapped_column(String(8), default="")
    result: Mapped[str] = mapped_column(Text, default="")
    download_file_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    job_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
