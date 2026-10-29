"""IFRS 16: one uploaded set of source files (kept in the warehouse, scanned for the months it
holds) and the runs made from it (a monthly workflow, or a ledger check)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, UTCDateTime, utcnow

BATCH_STATUSES = ("queued", "scanning", "ready", "failed")
RUN_KINDS = ("workflow", "ledger")
RUN_STATUSES = ("queued", "running", "done", "failed")


class SourceBatch(Base):
    """The LA register + RR06/ARO06 movements (+ optional Sequentra export and step-payments report).
    Kept so a month can be re-run without uploading again."""

    __tablename__ = "ifrs16_batches"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"), nullable=True)
    la_file_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    movements_file_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    sequentra_file_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    steps_file_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    scan: Mapped[dict] = mapped_column(JSON, default=dict)  # {months:[{value,label,rows}], la_rows, move_rows, ...}
    scan_error: Mapped[str] = mapped_column(String(400), default="")
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued | scanning | ready | failed
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)  # admins can hide history; audit keeps everything
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)

    @property
    def months(self) -> list[dict]:
        return (self.scan or {}).get("months", [])


class IfrsRun(Base):
    """One run of the engine: the monthly workflow for a batch + month, or a ledger check."""

    __tablename__ = "ifrs16_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ifrs16_batches.id"), nullable=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="workflow")  # workflow | ledger
    month: Mapped[str] = mapped_column(String(7), default="")  # YYYY-MM (workflow runs)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued | running | done | failed
    output_file_ids: Mapped[list] = mapped_column(JSON, default=list)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)  # {workbooks:[{workbook,rows}], recon:[{item,value}], ...}
    error: Mapped[str] = mapped_column(Text, default="")
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
