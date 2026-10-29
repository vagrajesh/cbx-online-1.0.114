"""Shared serialisers. Keep API shapes stable: the SPA depends on these keys."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ..models import AuditLog, Team, Tool, User


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _areas():
    from ..versions import areas

    return areas


def user_out(u: User, *, full: bool = False) -> dict[str, Any]:
    d = {
        "id": u.id,
        "email": u.email,
        "name": u.name,
        "title": u.title,
        "role": u.role,
        "is_axl": u.is_axl,
        "initials": u.initials(),
        "team_id": u.team_id,
        "team": u.team.name if u.team else None,
    }
    if full:
        d.update(
            {
                "is_active": u.is_active,
                "can_see_teams": bool(not u.is_axl and (u.is_owner or u.can("teams.view"))),
                "can_build": _areas().can_build(u),
                "can_test": _areas().can_test(u),
                "locked": u.locked,
                "must_change_password": u.must_change_password,
                "last_seen": iso(u.last_seen),
                "reset_requested_at": iso(u.reset_requested_at),
                "created_at": iso(u.created_at),
                "prefs": u.prefs or {},
                "mfa_method": u.mfa_method or "",
                "mfa_enrolled_at": iso(u.mfa_enrolled_at),
                "expires_at": iso(u.expires_at),
                "team_limited": bool(u.team_limited),  # R8: sees only their own team (made by a manager)
            }
        )
    return d


def team_out(t: Team) -> dict[str, Any]:
    return {"id": t.id, "name": t.name, "slug": t.slug}


def tool_out(t: Tool) -> dict[str, Any]:
    return {"id": t.id, "slug": t.slug, "name": t.name, "description": t.description, "icon": t.icon, "admin_only": t.admin_only,
            "kind": t.kind or "builtin", "ticket_id": t.ticket_id, "accepts_files": bool((t.config or {}).get("accepts_files"))}


def audit_out(a: AuditLog) -> dict[str, Any]:
    return {"id": a.id, "at": iso(a.at), "actor": a.actor_label, "actor_id": a.actor_id, "action": a.action, "target": a.target, "detail": a.detail, "outcome": a.outcome, "meta": a.meta or {}}
