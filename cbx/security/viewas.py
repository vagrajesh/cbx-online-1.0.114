"""View as: an owner or admin sees the app exactly as one other person sees it — their menu, their
tools, their rows, their Home — without being able to change anything.

How it holds:
  * The owner's own session carries `view_as_id` (and `view_as_until`, an hour at most). While it is
    set, `load_session` hands back the other person, so every permission check, row rule and screen
    is theirs — the same code path they get, not an imitation of it.
  * Nothing can be written. Any request that is not a read is refused before it reaches an endpoint
    (only "stop viewing" and "sign out" pass), and the request's database session is read-only: a
    read that would normally change something (mark a message read) is discarded, not saved.
  * The audit trail names the real person: "edwin (as thandi)", with the viewer's id.
  * Only people you may manage (Users & roles' own rule — an admin never views as an admin or an
    owner), never the AI account, never yourself.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import event

from ..db import SessionLocal, utcnow

MAX_MINUTES = 60
READS = {"GET", "HEAD", "OPTIONS"}
WRITES_ALLOWED = ("/api/view-as/stop", "/api/auth/logout")


def label(user) -> str:
    return "axl" if user.is_axl else (user.email or "").split("@")[0]


def apply(db, s, real, request):
    """The user this request acts as. Called by load_session for a live session."""
    vid = getattr(s, "view_as_id", None)
    if not vid:
        return real
    from ..models import User

    until = getattr(s, "view_as_until", None)
    target = db.get(User, vid)
    if target is None or not target.is_active or (until is not None and until <= utcnow()):
        s.view_as_id = None
        s.view_as_until = None
        return real
    if request is None:
        # a caller that cannot say what the request is may not act for anyone while viewing
        return None
    if request.method not in READS and not request.url.path.startswith(WRITES_ALLOWED):
        raise HTTPException(403, f"You are viewing as {target.name} — nothing can be changed from here. Stop viewing first.")
    request.state.viewer = real
    request.state.viewing_as = target
    db.info["read_only"] = True
    db.info["viewer"] = {"real_id": real.id, "real_label": label(real), "target_id": target.id, "target_label": label(target)}
    return target


def start(db, s, real, target) -> None:
    s.view_as_id = target.id
    s.view_as_until = utcnow() + timedelta(minutes=MAX_MINUTES)


def stop(db, s) -> Optional[int]:
    was = getattr(s, "view_as_id", None)
    s.view_as_id = None
    s.view_as_until = None
    return was


@event.listens_for(SessionLocal, "before_flush")
def _read_only(session, _ctx, _instances) -> None:
    """A viewing request writes only its audit rows and its own session row; anything else a read
    tried to change is dropped here."""
    if not session.info.get("read_only"):
        return
    from ..models import AuditLog, LoginSession

    keep = (AuditLog, LoginSession)
    for obj in list(session.new) + list(session.dirty) + list(session.deleted):
        if not isinstance(obj, keep):
            session.expunge(obj)
