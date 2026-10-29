"""Site Schedules (1.0.89): one agreement per site, made from its Swiftnet PDFs — the same page as CBX-Connect 2
(the same table, ss_agreements, so the move to CBX-Connect 2 takes it as it is)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, UTCDateTime, utcnow


class SsAgreement(Base):
    """The fields and charge tables read from the site's PDFs (quotes, NTP, an earlier agreement), corrected by a
    person, who signs, and the Word document made from the template."""

    __tablename__ = "ss_agreements"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(10), default="draft")  # draft | built | archived
    fields: Mapped[dict] = mapped_column(JSON, default=dict)
    tables: Mapped[dict] = mapped_column(JSON, default=dict)  # t1..t4: [{item, req, desc, amt}], extra: [{title, cols, rows}]
    signing: Mapped[dict] = mapped_column(JSON, default=dict)  # swift/couser: {city, date, witnesses, people: [{name, title}]}
    notes: Mapped[list] = mapped_column(JSON, default=list)
    files: Mapped[list] = mapped_column(JSON, default=list)  # [{file_id, name, kind, pages, at, by}]
    history: Mapped[list] = mapped_column(JSON, default=list)  # [{at, by, what}] — every read, save and Word file
    agreement_file_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    template_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
