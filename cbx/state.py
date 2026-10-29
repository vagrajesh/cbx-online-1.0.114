"""Small facts about this particular server that have to survive a restart.

Not settings — settings live in `.env` and say what someone intended. These are things the server
has *observed about itself*, which is a different question and a more reliable one. The first of
them is "has email ever actually left this machine", because `.env` saying `CBX_SMTP_HOST=...` is a
statement of intent and proves nothing about whether the password is right or the port is open.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from .db import utcnow
from .models import AppState

MAIL_PROVEN = "mail.proven_at"
PUBLIC_URL = "app.public_url"   # the address the last admin signed in from (scheme + host[:port])


def get(db: Session, key: str) -> str:
    row = db.query(AppState).filter(AppState.key == key).one_or_none()
    return row.value if row is not None else ""


def set(db: Session, key: str, value: str) -> None:  # noqa: A001 — reads better than set_value here
    row = db.query(AppState).filter(AppState.key == key).one_or_none()
    if row is None:
        db.add(AppState(key=key, value=value))
    else:
        row.value, row.updated_at = value, utcnow()
    db.flush()


def record_send(db: Session, ok: bool) -> None:
    """Call after any real send attempt that had a database session to hand.

    Only success is written. A later failure does not un-prove the mailbox — a mail server can be
    down for ten minutes — and the sign-in path has its own fallback for a send that fails today.
    """
    if ok and not get(db, MAIL_PROVEN):
        set(db, MAIL_PROVEN, utcnow().isoformat())


def mail_proven(db: Session) -> Optional[datetime]:
    """When email was first seen to work on this server, or None if it never has."""
    raw = get(db, MAIL_PROVEN)
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def seen_from(db: Session, base: str) -> None:
    """Remember the address an admin reached the app at, so links in emails sent by a routine (which
    has no request to look at) can point somewhere that works. A configured CBX_PUBLIC_URL wins."""
    base = (base or "").strip().rstrip("/")
    if base and get(db, PUBLIC_URL) != base:
        set(db, PUBLIC_URL, base)


def public_url(db: Optional[Session] = None) -> str:
    """Where links in emails point: CBX_PUBLIC_URL, else the last admin's address, else this machine."""
    from .config import settings

    if settings.public_url:
        return settings.public_url.rstrip("/")
    if db is not None:
        remembered = get(db, PUBLIC_URL)
        if remembered:
            return remembered
    import socket

    return f"http://{socket.gethostname()}:{settings.port}"


def base_of(request) -> str:
    """scheme://host[:port] as the browser sees it — behind the tunnel the app hears http, the
    browser speaks https, and X-Forwarded-Proto is how the tunnel says so."""
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "http").split(",")[0].strip()
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc or "").split(",")[0].strip()
    return f"{proto}://{host}" if host else ""
