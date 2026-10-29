"""An API connection: an outside service (its address and its key) that tools and workflows may call.

The key itself is stored sealed (cbx/security/secretbox.py) and is never returned by the API — only a
hint (its last four characters) so the admin can tell two keys apart. Calls to the service go through
CBX, which adds the key on the way out: a tool page or a flow
uses the connection without ever holding the key.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, UTCDateTime, utcnow

AUTH_KINDS = ("none", "bearer", "header", "basic", "query")


class ApiConnection(Base):
    __tablename__ = "api_connections"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    base_url: Mapped[str] = mapped_column(String(500))
    # none | bearer (Authorization: Bearer <key>) | header (<header_name>: <key>) | basic (user:key) | query (?<header_name>=<key>)
    auth_kind: Mapped[str] = mapped_column(String(16), default="bearer")
    header_name: Mapped[str] = mapped_column(String(80), default="")   # header kind: e.g. X-API-Key; query kind: the parameter; basic: the username
    secret_sealed: Mapped[str] = mapped_column(Text, default="")        # sealed key; "" = none stored
    secret_hint: Mapped[str] = mapped_column(String(24), default="")
    extra_headers: Mapped[dict] = mapped_column(JSON, default=dict)     # sent on every call, e.g. {"Accept": "application/json"}
    tools: Mapped[list] = mapped_column(JSON, default=list)             # tool slugs that may use this connection
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_test: Mapped[dict] = mapped_column(JSON, default=dict)         # {at, ok, status, note}
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class DataKey(Base):
    """A key another system uses to READ one table of a tool folder (1.0.59 — "expose ours").

    The table says which of its columns may be read (data/<table>.expose in the folder); the key says
    who may read them, how often (calls a minute) and until when. The key itself is shown once when it
    is made and only its SHA-256 is kept: a lost key is revoked and a new one made. Reading only —
    nothing can be added or changed with a key.
    """

    __tablename__ = "data_keys"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))                      # who it is for, e.g. "Finance system"
    tool: Mapped[str] = mapped_column(String(64))
    table: Mapped[str] = mapped_column(String(64))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(16), default="")        # the first characters, to tell keys apart
    rate_per_min: Mapped[int] = mapped_column(default=60)
    allowed_ips: Mapped[list] = mapped_column(JSON, default=list)       # [] = from anywhere that reaches the server
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    revoked_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    last_ip: Mapped[str] = mapped_column(String(64), default="")
    calls: Mapped[int] = mapped_column(default=0)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
