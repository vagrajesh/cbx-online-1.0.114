"""One function every part of the system calls. Append-only by design."""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from .models import AuditLog, User


def log(
    db: Session,
    action: str,
    *,
    actor: Optional[User] = None,
    actor_label: str = "",
    target: str = "",
    detail: str = "",
    outcome: str = "ok",
    ip: str = "",
    **meta: Any,
) -> AuditLog:
    if actor is not None and not actor_label:
        actor_label = "axl" if actor.is_axl else actor.email.split("@")[0]
    actor_id = actor.id if actor else None
    viewer = db.info.get("viewer") if db is not None else None
    if viewer and actor_id == viewer["target_id"]:
        # "View as": whatever this request does is the viewer's doing, not the person being viewed
        actor_id = viewer["real_id"]
        actor_label = f"{viewer['real_label']} (as {viewer['target_label']})"
        meta = {**meta, "viewing_as": viewer["target_id"]}
    row = AuditLog(
        actor_id=actor_id,
        actor_label=actor_label or "system",
        action=action,
        target=target,
        detail=detail,
        outcome=outcome,
        ip=ip,
        meta=meta,
    )
    db.add(row)
    db.flush()
    return row


def record_error(kind: str, target: str, detail: str) -> None:
    """Write an error the app noticed about itself — a 500, a routine that died — as an audit row,
    on its own connection so it lands even when the request's transaction is being rolled back.
    Best effort: recording an error must never raise one."""
    try:
        from .db import SessionLocal

        with SessionLocal() as s:
            s.add(AuditLog(actor_id=None, actor_label="system", action=kind, target=(target or "")[:255], detail=(detail or "")[:2000], outcome="failed"))
            s.commit()
    except Exception:  # noqa: BLE001
        pass
    try:  # the AI check looks at it on its own thread and leaves its words beside the error (cbx/aicheck.py)
        from . import aicheck

        aicheck.background(kind, target, detail)
    except Exception:  # noqa: BLE001
        pass
