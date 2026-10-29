"""Warehouse: folders (shared library + teams/people), stored files, master sheets."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base, UTCDateTime, utcnow

# Folder kinds: library (shared: pdfs, workflows, master_sheets), team, person, person_sub (Inbox/Work/Results),
# tool (Tools/<tool>/<thing>/<sub-thing> — a tool's own files, e.g. Tools/Entity Compliance/<Company>/<Document type>)
FOLDER_KINDS = ("library", "team", "person", "sub", "tool")


class Folder(Base):
    __tablename__ = "folders"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(160))
    path: Mapped[str] = mapped_column(String(600), unique=True)  # "Shared library/PDFs", "Teams/Utilities billing/Thandi Mokoena/Inbox"
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("folders.id"), nullable=True)
    team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"), nullable=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    # tool folders: the company (Entity Compliance) this folder belongs to — who may see the folder is
    # decided by who may see the company, nothing else
    entity_id: Mapped[Optional[int]] = mapped_column(ForeignKey("entities.id"), nullable=True)
    remote_id: Mapped[str] = mapped_column(String(255), default="")  # OneDrive item id once synced
    synced_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    files: Mapped[list["StoredFile"]] = relationship(back_populates="folder")


class StoredFile(Base):
    """Every input is stored in the uploader's folder AND mirrored to the shared library
    (library_copy_of points at the original). Results stay with the person only."""

    __tablename__ = "stored_files"
    id: Mapped[int] = mapped_column(primary_key=True)
    folder_id: Mapped[int] = mapped_column(ForeignKey("folders.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    rel_path: Mapped[str] = mapped_column(String(700))  # under DATA_DIR/warehouse
    size: Mapped[int] = mapped_column(Integer, default=0)
    mime: Mapped[str] = mapped_column(String(120), default="")
    kind: Mapped[str] = mapped_column(String(16), default="pdf")  # pdf | xlsx | csv | docx | flow | other
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    text_sha: Mapped[str] = mapped_column(String(64), default="", index=True)  # near-duplicate fingerprint
    pages: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"), nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="upload")  # upload | pdfstudio | workflow | result
    detected_as: Mapped[str] = mapped_column(String(120), default="")
    goes_to: Mapped[str] = mapped_column(String(120), default="")
    pipeline_status: Mapped[str] = mapped_column(String(16), default="")  # queued | reading | added | merged | check | ocr | unknown
    library_copy_of: Mapped[Optional[int]] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    remote_id: Mapped[str] = mapped_column(String(255), default="")
    synced_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    folder: Mapped[Folder] = relationship(back_populates="files")


class MasterSheet(Base):
    """A living workbook. Rows live in `rows` (JSON list) with provenance; exported to xlsx on demand.
    Row-level rules apply on the `client`/`team` fields of each row."""

    __tablename__ = "master_sheets"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    slug: Mapped[str] = mapped_column(String(160), unique=True)
    team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"), nullable=True)
    columns: Mapped[list] = mapped_column(JSON, default=list)  # [{key,label,ctype}]
    rows: Mapped[list] = mapped_column(JSON, default=list)  # [{_id,_src:{doc,file,batch},_by,_at,_client,_team, ...cols}]
    exceptions: Mapped[list] = mapped_column(JSON, default=list)  # rows that failed checks
    description: Mapped[str] = mapped_column(Text, default="")
    file_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
