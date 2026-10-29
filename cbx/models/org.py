"""The org graph: who reports to whom, and who may see whose work.

Two separate things, deliberately kept apart:

* **Structure** — `User.reports_to_id` and `Team.parent_id`. The shape of the organisation.
* **Permission** — `AccessGrant` rows, plus what the structure grants by default (see
  `cbx.org.access`). Structure is not permission: a support person can report to a manager
  without that manager seeing their To-do, and moving someone under a new manager must never
  silently expose the things a person was promised are private.

`OrgLayout` is only where a node sits on the canvas. It carries no authority at all.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, UTCDateTime, utcnow

# What can travel along a wire. `sensitive` kinds are NEVER granted by position in the tree —
# they need a grant someone drew on purpose. To-do is sensitive because Edwin's rule is that a
# person's tasks are their own; a reporting line is not consent.
KINDS: dict[str, dict] = {
    "folders": {"label": "Folders & files", "sensitive": False, "hint": "warehouse folders and the documents in them"},
    "pdf": {"label": "PDF Studio", "sensitive": False, "hint": "extraction batches and the sheets they produce"},
    "compliance": {"label": "Entity Compliance", "sensitive": False, "hint": "the document grid for their companies"},
    "todo": {"label": "To-do", "sensitive": True, "hint": "their tasks and their complexity-weighted numbers"},
}
KIND_KEYS = tuple(KINDS)
SENSITIVE = frozenset(k for k, v in KINDS.items() if v["sensitive"])


class AccessGrant(Base):
    """One wire on the canvas: `viewer` may see `owner`'s `kind`.

    `resource_id` empty means every resource of that kind the owner has; set, it means that one
    folder (or batch, or company) only — the "click a folder on their right pin, it appears on the
    next person's left pin" case.

    A grant is always a GRANT. There are no deny wires: a graph where some wires add and others
    subtract stops being readable at about twenty nodes, and nobody can then answer "why can she
    see this?". Where something must stay private, the kind is marked sensitive above and the tree
    does not grant it — only a wire does.
    """

    __tablename__ = "org_grants"
    __table_args__ = (UniqueConstraint("owner_id", "viewer_id", "kind", "resource_id", name="uq_org_grant"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)  # whose work
    viewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)  # who may see it
    kind: Mapped[str] = mapped_column(String(16))  # one of KIND_KEYS
    resource_id: Mapped[str] = mapped_column(String(64), default="")  # "" = all of this kind
    note: Mapped[str] = mapped_column(Text, default="")  # why, in the granter's words
    granted_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class OrgLayout(Base):
    """Where a person's node sits on the canvas. Presentation only — never consulted for access."""

    __tablename__ = "org_layout"
    __table_args__ = (UniqueConstraint("user_id", name="uq_org_layout_user"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    x: Mapped[float] = mapped_column(Float, default=0.0)
    y: Mapped[float] = mapped_column(Float, default=0.0)
    collapsed: Mapped[int] = mapped_column(Integer, default=0)  # 1 = children hidden by default
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
