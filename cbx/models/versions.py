from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, UTCDateTime, utcnow


class AppVersion(Base):
    """A version package (zip) uploaded through the version injector, staged, tested, promoted or rolled back."""

    __tablename__ = "app_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    tag: Mapped[str] = mapped_column(String(32), unique=True)  # "1.0.1"
    notes: Mapped[str] = mapped_column(Text, default="")
    # staged (unpacked, not tested) | sandbox (tested, ready) | failed (tests failed) | live | superseded (was live,
    # a newer version replaced it) | rolled_back (was live, the owner rolled it back)
    status: Mapped[str] = mapped_column(String(16), default="staged")
    env: Mapped[str] = mapped_column(String(16), default="")  # sandbox-a | sandbox-b | production
    package_path: Mapped[str] = mapped_column(String(600), default="")
    sha256: Mapped[str] = mapped_column(String(64), default="")
    tests: Mapped[dict] = mapped_column(JSON, default=dict)
    uploaded_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    promoted_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    promoted_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    backup_path: Mapped[str] = mapped_column(String(600), default="")


class WorkArea(Base):
    """1.0.80: where people work before Live — a Dev area per person (their own running copy of the app with
    their Builder drafts, their own throwaway data) and one shared Test area (the version everyone tries before
    the owner promotes it). Each is a sandbox slot with a port of its own; what runs there is in memory
    (cbx/versions/sandbox_runner.py), the slot and its port are kept here so they survive a restart."""

    __tablename__ = "work_areas"
    id: Mapped[int] = mapped_column(primary_key=True)
    slot: Mapped[str] = mapped_column(String(32), unique=True)      # "test" | "dev-<user id>"
    kind: Mapped[str] = mapped_column(String(8), default="dev")     # dev | test
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    port: Mapped[int] = mapped_column(default=0)
    version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("app_versions.id"), nullable=True)  # Test: what is in it
    started_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
