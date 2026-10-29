"""Audit trail. Owners and admins see everything. A team-limited reader (a manager, or a viewer a manager
made) sees the actions of the people on their own team (and their own) — never the AI's, the admins' or
another team's."""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AuditLog, User
from ..security.deps import require_manager
from .schemas import audit_out

router = APIRouter(prefix="/api/audit", tags=["audit"])


def _team_actor_ids(db: Session, me: User) -> list[int]:
    """The reader and everyone on their team. Owners and admins who happen to sit on the same team are not
    included: what they do is admin business, not the team's. R8: "not an admin" rather than naming `user` and
    `manager`, so a viewer on the team shows up in its trail."""
    from ..security.roles import admin_slugs

    ids = {me.id}
    if me.team_id:
        ids |= {u.id for u in db.query(User.id).filter(User.team_id == me.team_id, User.is_axl.is_(False), User.role.notin_(admin_slugs(db))).all()}
    return sorted(ids)


def _base(db: Session, me: User):
    qs = db.query(AuditLog)
    if me.is_team_limited:
        qs = qs.filter(AuditLog.actor_id.in_(_team_actor_ids(db, me)))
    return qs


def _query(db: Session, me: User, actor: str | None, action: str | None, ai_only: bool, q: str | None):
    qs = _base(db, me)
    if ai_only:
        qs = qs.filter(AuditLog.actor_label.like("axl%"))
    if actor:
        qs = qs.filter(AuditLog.actor_label == actor)
    if action:
        qs = qs.filter(AuditLog.action == action)
    if q:
        like = f"%{q}%"
        qs = qs.filter((AuditLog.detail.like(like)) | (AuditLog.target.like(like)) | (AuditLog.action.like(like)))
    return qs.order_by(AuditLog.at.desc())


def _cell(v: str) -> str:
    """Excel treats a cell starting with = + - @ as a formula; file names and details come from users."""
    return ("'" + v) if v[:1] in ("=", "+", "-", "@") else v


@router.get("")
def list_audit(db: Session = Depends(get_db), me: User = Depends(require_manager), limit: int = Query(200, ge=1, le=2000), offset: int = Query(0, ge=0), actor: str | None = None, action: str | None = None, ai_only: bool = False, q: str | None = None):
    qs = _query(db, me, actor, action, ai_only, q)
    total = qs.count()
    rows = qs.offset(offset).limit(limit).all()
    actions = [r[0] for r in _base(db, me).with_entities(AuditLog.action).distinct().order_by(AuditLog.action)]
    # 1.0.83: people by their name, actions in words (the code stays in the export and on hover)
    ids = {a.actor_id for a in rows if a.actor_id}
    names = {u.id: u.name for u in db.query(User).filter(User.id.in_(ids or {0})).all()}
    out = []
    for a in rows:
        d = audit_out(a)
        d["actor_name"] = names.get(a.actor_id) or ""
        d["what"] = action_words(a.action)
        out.append(d)
    return {"total": total, "rows": out, "actions": actions, "action_words": {x: action_words(x) for x in actions}, "scope": "team" if me.is_team_limited else "all"}


WORDS = {"cexa": "C-EXA", "docwatch": "Entity Compliance", "mfa": "two-step", "axl": "C-EXA", "pdfstudio": "PDF Studio", "ifrs16": "IFRS 16", "api": "API", "csv": "CSV", "url": "address", "row": "item", "rows": "items"}


def action_words(code: str) -> str:
    """'tool_row_added' → 'Tool row added', 'cexa_team_created' → 'C-EXA team created'."""
    parts = [WORDS.get(p, p) for p in str(code or "").replace(".", "_").split("_") if p]
    text = " ".join(parts)
    return text[:1].upper() + text[1:] if text else ""


@router.get("/export.csv")
def export_csv(db: Session = Depends(get_db), me: User = Depends(require_manager), ai_only: bool = False):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["at", "actor", "action", "target", "detail", "outcome"])
    for a in _query(db, me, None, None, ai_only, None).limit(50000):
        w.writerow([a.at.isoformat(), a.actor_label, a.action, _cell(a.target), _cell(a.detail), a.outcome])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=cbx-audit.csv"})
