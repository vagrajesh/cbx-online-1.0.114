"""Users & roles: people, tool-access matrix, row-level rules, teams.

Who may do what (deny by default — see User.can_manage_role / can_manage_user):
  owner    everything.
  admin    create and manage managers and users (never admins/owners); teams and row rules.
  manager  their OWN TEAM only: sees its people, adds users to it, edits/unlocks/resets them, grants
           the tools the manager themself holds. Cannot change roles or teams, cannot touch other
           teams, cannot make managers/admins, no row rules, no new teams.

R8: "their own team" follows whoever a team-limited person makes. An account they create is limited to the
team as well (`User.team_limited`), whatever role it has — so a Viewer a manager makes sees that team, not the
company — and they may only give roles within their own powers (`User.can_grant_role`).
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..audit import log
from ..db import get_db
from ..models import Role, RowRule, Team, Tool, ToolAccess, User
from ..models.core import ROLES
from ..security import auth as A
from ..security import devices as D
from ..security.deps import client_ip, has_tool, require_admin, require_human, require_manager
from ..warehouse.folders import ensure_person_folders, ensure_team_folder, safe_name


def _role_exists(db, slug: str) -> bool:
    """Any role in the table, built-in or invented on the Roles tab."""
    if slug in ROLES:
        return True
    return db.query(Role).filter(Role.slug == slug).one_or_none() is not None
from .schemas import team_out, tool_out, user_out

router = APIRouter(prefix="/api/users", tags=["users"])


def _slug(s: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _visible_users(db: Session, me: User) -> list[User]:
    """Admins/owners: everyone. Team-limited people (managers, and anyone a manager made): everyone on their team
    (and themselves) — not the AI, not owners or admins who happen to sit on the same team. R8: by "not an admin",
    not by naming `user` and `manager`, which hid a Viewer on the team from the manager who made it."""
    q = db.query(User)
    if me.is_team_limited:
        if not me.team_id:
            return [me]
        from ..security.roles import admin_slugs

        q = q.filter(User.is_axl.is_(False), ((User.team_id == me.team_id) & User.role.notin_(admin_slugs(db))) | (User.id == me.id))
    return q.order_by(User.is_axl.desc(), User.role, User.name).all()


def _grantable_tools(db: Session, me: User) -> list[Tool]:
    """The tools this person may hand out: admins any active tool, team-limited people only what they hold."""
    tools = db.query(Tool).filter(Tool.is_active.is_(True), (Tool.kind.is_(None)) | (Tool.kind != "workflow")).order_by(Tool.sort).all()
    if me.is_team_limited:
        tools = [t for t in tools if not t.admin_only and has_tool(db, me, t.slug)]
    return tools


def _expiry(value: str | None) -> datetime | None:
    """`expires_at` from the form: a date (access ends at the end of that day, UTC), or empty for none."""
    if value is None or not str(value).strip():
        return None
    try:
        d = date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        raise HTTPException(400, "Give the end date as YYYY-MM-DD.")
    if d < date.today():
        raise HTTPException(400, "That date is in the past.")
    return datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=timezone.utc)


def _check_scope(db: Session, me: User, u: User, request: Request, what: str) -> None:
    """A manager reaching outside their team is a blocked action, and it is logged."""
    if not me.can_manage_user(u):
        log(db, "user_scope_blocked", actor=me, target=u.email, detail=what, outcome="blocked", ip=client_ip(request))
        raise HTTPException(403, "You can't manage this account." if me.is_team_limited else "You can't edit this account.")


@router.get("")
def list_users(db: Session = Depends(get_db), me: User = Depends(require_manager)):
    users = _visible_users(db, me)
    tools = _grantable_tools(db, me)
    access = db.query(ToolAccess).all()
    grants = {}
    for a in access:
        grants.setdefault(a.user_id, []).append(a.tool_id)
    out = []
    for u in users:
        d = user_out(u, full=True)
        d["tools"] = "all" if u.is_admin else grants.get(u.id, [])
        d["tool_count"] = "All" if u.is_admin else len(grants.get(u.id, []))
        d["can_edit"] = me.can_manage_user(u)
        out.append(d)
    from ..security.roles import grantable

    # What to SHOW: the roles this person may hand out, plus their own — a manager's team is full of
    # managers and users, and a list that omits "manager" gives them a team count that misses people.
    # What they may GRANT is a separate question, answered by `me_can_create` below.
    roles = [r.slug for r in grantable(db, me)]
    if me.role not in roles:
        roles.append(me.role)
    roles = roles or [r for r in ROLES if not me.is_team_limited or r in ("manager", "user")]
    counts = {r: sum(1 for u in users if u.role == r and not u.is_axl) for r in roles}
    # Names and one-liners for whatever roles exist on THIS server, invented ones included — the
    # screen used to carry a hard-coded list of four, which showed "user_admin" as a raw slug.
    info = {r.slug: {"name": r.name, "description": r.description or "", "rank": r.rank, "locked": bool(r.locked)}
            for r in db.query(Role).order_by(Role.rank.desc()).all()}
    teams = db.query(Team).order_by(Team.name)
    if me.is_team_limited:
        teams = teams.filter(Team.id == (me.team_id or 0))
    return {
        "users": out,
        "tools": [tool_out(t) for t in tools],
        "teams": [team_out(t) for t in teams],
        "counts": counts,
        "role_info": info,
        "can_manage_roles": me.can("people.roles") and not me.is_axl,
        "me_can_create": [r for r in roles if me.can_grant_role(r)],
        "scope": {"team_id": me.team_id, "team": me.team.name if me.team else None} if me.is_team_limited else None,
        "can_set_team_limit": bool(me.is_admin and not me.is_axl),  # R8 (D6): owners and admins take the limit off
    }


class UserIn(BaseModel):
    email: str
    name: str
    title: str = ""
    role: str = "user"
    team_id: int | None = None
    team_name: str | None = None
    tools: list[int | None] = []  # 1.0.96: a stray null (1.0.93's welcome tick) is ignored, not a refusal
    expires_at: str | None = None  # YYYY-MM-DD; outside people (a supplier, a client) whose access should end
    welcome_mail: bool = True  # 1.0.93: send them the welcome mail with how to sign in


@router.post("")
def create_user(body: UserIn, request: Request, db: Session = Depends(get_db), me: User = Depends(require_manager), _h: User = Depends(require_human)):
    if not _role_exists(db, body.role):
        raise HTTPException(400, "That role doesn't exist.")
    if not me.can_grant_role(body.role):
        log(db, "role_grant_blocked", actor=me, target=body.email, detail=f"tried to create {body.role}", outcome="blocked", ip=client_ip(request))
        raise HTTPException(403, f"You can't create {body.role} accounts.")
    email = body.email.strip().lower()
    if "@" not in email:
        raise HTTPException(400, "That doesn't look like an email address.")
    if not body.name.strip():
        raise HTTPException(400, "Give the person a name.")
    if db.query(User).filter(User.email == email).count():
        raise HTTPException(400, "There is already an account with that email.")
    team_id = body.team_id
    team_name = body.team_name
    if me.is_team_limited:
        # a manager's people land on the manager's team, whatever the request says
        if not me.team_id:
            raise HTTPException(400, "You're not on a team yet, so you can't add people. Ask an admin to put you on one.")
        if (team_id and team_id != me.team_id) or (team_name and not team_id and _slug(team_name) != (me.team.slug if me.team else "")):
            log(db, "user_scope_blocked", actor=me, target=email, detail="tried to create a user on another team", outcome="blocked", ip=client_ip(request))
            raise HTTPException(403, "You can only add people to your own team.")
        team_id, team_name = me.team_id, None
    if team_id and db.get(Team, team_id) is None:
        raise HTTPException(400, "That team doesn't exist any more. Refresh the page and try again.")
    tool_ids = {t for t in body.tools if isinstance(t, int)}
    if tool_ids and db.query(Tool).filter(Tool.id.in_(tool_ids)).count() != len(tool_ids):
        raise HTTPException(400, "One of those tools doesn't exist any more. Refresh the page and try again.")
    if tool_ids:
        allowed = {t.id for t in _grantable_tools(db, me)}
        if not tool_ids <= allowed:
            log(db, "access_grant_blocked", actor=me, target=email, detail="tried to grant a tool they don't hold", outcome="blocked", ip=client_ip(request))
            raise HTTPException(403, "You can only grant tools you have yourself.")
    if team_name and not team_id:
        if not _slug(team_name):
            raise HTTPException(400, "Give the team a name with letters or digits in it.")
        team = db.query(Team).filter(Team.slug == _slug(team_name)).one_or_none()
        if team is None:
            team = Team(name=team_name.strip(), slug=_slug(team_name))
            db.add(team)
            db.flush()
            ensure_team_folder(db, team)
            log(db, "team_created", actor=me, target=team.name)
        team_id = team.id
    expires = _expiry(body.expires_at)
    pw = A.temp_password()
    # R8: someone a team-limited person makes sees no further than they do
    limited = bool(me.is_team_limited)
    u = User(email=email, name=body.name.strip(), title=body.title.strip(), role=body.role, team_id=team_id, password_hash=A.hash_password(pw), must_change_password=True, created_by_id=me.id, expires_at=expires, team_limited=limited)
    db.add(u)
    db.flush()
    for tid in tool_ids:
        db.add(ToolAccess(tool_id=tid, user_id=u.id, granted_by_id=me.id))
    ensure_person_folders(db, u)
    log(db, "account_created", actor=me, target=email, detail=f"role={body.role} tools={len(body.tools)}" + (f" ends={expires.date()}" if expires else "") + (" team-limited" if limited else ""), ip=client_ip(request))
    from .. import welcome  # 1.0.93: the welcome mail, in Edwin's words

    sent = welcome.send(db, u, pw, me) if body.welcome_mail else {"ok": False, "held": False, "detail": "not sent (unticked)"}
    return {"user": user_out(u, full=True), "temp_password": pw, "welcome_mail": sent}


class UserPatch(BaseModel):
    name: str | None = None
    title: str | None = None
    role: str | None = None
    team_id: int | None = None
    is_active: bool | None = None
    unlock: bool | None = None
    expires_at: str | None = None  # YYYY-MM-DD, or "" to remove the end date
    team_limited: bool | None = None  # R8: owners and admins only


@router.patch("/{uid}")
def patch_user(uid: int, body: UserPatch, request: Request, db: Session = Depends(get_db), me: User = Depends(require_manager), _h: User = Depends(require_human)):
    u = db.get(User, uid)
    if u is None or u.is_axl:
        raise HTTPException(404, "No such user.")
    _check_scope(db, me, u, request, "edit")
    # validate everything first, change nothing until it all checks out
    if body.role and body.role != u.role:
        if not _role_exists(db, body.role):
            raise HTTPException(400, "That role doesn't exist.")
        if me.is_team_limited or not me.can_grant_role(body.role):
            log(db, "role_grant_blocked", actor=me, target=u.email, detail=f"{u.role} → {body.role}", outcome="blocked", ip=client_ip(request))
            raise HTTPException(403, "You can't change roles." if me.is_team_limited else f"You can't make someone {body.role}.")
        if u.id == me.id and body.role != "owner":
            raise HTTPException(400, "You can't remove your own owner role.")
    if body.team_limited is not None and bool(body.team_limited) != bool(u.team_limited) and not (me.is_admin and not me.is_axl):
        # R8 (D6): the limit a manager's people inherit is lifted by an owner or an admin, never by the manager
        log(db, "user_scope_blocked", actor=me, target=u.email, detail="tried to change 'Limited to their team'", outcome="blocked", ip=client_ip(request))
        raise HTTPException(403, "Only an owner or an admin can change whether someone is limited to their team.")
    if body.is_active is False and u.id == me.id:
        raise HTTPException(400, "You can't disable your own account.")
    if body.team_id is not None:
        if me.is_team_limited and body.team_id != me.team_id:
            log(db, "user_scope_blocked", actor=me, target=u.email, detail="tried to move a user to another team", outcome="blocked", ip=client_ip(request))
            raise HTTPException(403, "You can't move people to another team.")
        if db.get(Team, body.team_id) is None:
            raise HTTPException(400, "That team doesn't exist any more. Refresh the page and try again.")
    changes = []
    if body.role and body.role != u.role:
        changes.append(f"role {u.role} → {body.role}")
        u.role = body.role
    for f in ("name", "title", "team_id", "is_active"):
        v = getattr(body, f)
        if v is not None and v != getattr(u, f):
            changes.append(f"{f} → {v}")
            setattr(u, f, v)
    if body.is_active is False:
        # a disabled account is signed out everywhere, immediately
        A.revoke_other_sessions(db, u, keep="")
    if body.unlock:
        u.locked = False
        u.failed_logins = 0
        u.mfa_failed = 0
        changes.append("unlocked")
    if body.expires_at is not None:
        expires = _expiry(body.expires_at)
        if expires != u.expires_at:
            if expires is not None and u.id == me.id:
                raise HTTPException(400, "You can't put an end date on your own account.")
            changes.append(f"access ends → {expires.date() if expires else 'never'}")
            u.expires_at = expires
    if body.team_limited is not None and bool(body.team_limited) != bool(u.team_limited):
        changes.append(f"limited to their team → {'yes' if body.team_limited else 'no'}")
        u.team_limited = bool(body.team_limited)
    if body.team_id is not None:
        ensure_person_folders(db, u)
    log(db, "account_changed", actor=me, target=u.email, detail="; ".join(changes), ip=client_ip(request))
    return {"user": user_out(u, full=True)}


@router.get("/codes-waiting")
def codes_waiting(request: Request, db: Session = Depends(get_db), me: User = Depends(require_admin), _h: User = Depends(require_human)):
    """Sign-in codes that could not be emailed (CBX_CODE_FALLBACK=terminal), for the people this owner or
    admin manages — so a person outside the building can be given theirs by phone. Reading them is
    logged."""
    out = []
    for w in D.waiting(me):
        u = db.get(User, w["user_id"])
        if u is None or u.id == me.id or not me.can_manage_user(u):
            continue
        out.append({"user_id": u.id, "name": u.name, "email": u.email, "code": w["code"], "until": w["until"].isoformat(), "where": w["where"], "ip": w["ip"], "why": w["why"]})
    if out:
        log(db, "login_codes_viewed", actor=me, detail=", ".join(x["email"] for x in out)[:300], ip=client_ip(request))
    return {"codes": out}


class MfaResetIn(BaseModel):
    reason: str = ""


@router.post("/{uid}/mfa-reset")
def reset_mfa(uid: int, body: MfaResetIn, request: Request, db: Session = Depends(get_db), me: User = Depends(require_manager), _h: User = Depends(require_human)):
    """Lost phone, new phone: clear the person's two-step verification so they enrol again at their next
    sign-in. Their remembered devices and other sessions go with it — whoever holds the old phone holds
    nothing. Always logged with the reason given."""
    u = db.get(User, uid)
    if u is None or u.is_axl:
        raise HTTPException(404, "No such user.")
    _check_scope(db, me, u, request, "mfa-reset")
    if not body.reason.strip():
        raise HTTPException(400, "Say why (a lost phone, a new phone) — it goes in the audit trail.")
    had = u.mfa_method or "none"
    u.mfa_method = ""
    u.mfa_secret = ""
    u.mfa_recovery = []
    u.mfa_failed = 0
    u.mfa_enrolled_at = None
    u.locked = False
    u.failed_logins = 0
    D.revoke_all(db, u)
    A.revoke_other_sessions(db, u, keep="")
    log(db, "mfa_reset", actor=me, target=u.email, detail=f"was {had}: {body.reason.strip()[:200]}", ip=client_ip(request))
    return {"user": user_out(u, full=True)}


@router.post("/{uid}/temp-password")
def issue_temp_password(uid: int, request: Request, db: Session = Depends(get_db), me: User = Depends(require_manager), _h: User = Depends(require_human)):
    u = db.get(User, uid)
    if u is None or u.is_axl:
        raise HTTPException(403, "You can't reset this account.")
    _check_scope(db, me, u, request, "temp-password")
    pw = A.temp_password()
    u.password_hash = A.hash_password(pw)
    u.must_change_password = True
    u.locked = False
    u.failed_logins = 0
    u.reset_requested_at = None
    A.revoke_other_sessions(db, u, keep="")
    log(db, "temp_password_issued", actor=me, target=u.email, ip=client_ip(request))
    return {"temp_password": pw}


class AccessIn(BaseModel):
    user_id: int
    tool_id: int
    granted: bool


@router.post("/access")
def set_access(body: AccessIn, request: Request, db: Session = Depends(get_db), me: User = Depends(require_manager), _h: User = Depends(require_human)):
    u, t = db.get(User, body.user_id), db.get(Tool, body.tool_id)
    if u is None or t is None:
        raise HTTPException(404, "No such user or tool.")
    if u.is_admin or u.is_axl:
        raise HTTPException(400, "Admins and owners already have every tool.")
    if t.admin_only and body.granted:
        raise HTTPException(400, f"{t.name} is admin-only; make the person an admin instead.")
    _check_scope(db, me, u, request, f"access {t.slug}")
    if me.is_team_limited and not has_tool(db, me, t.slug):
        log(db, "access_grant_blocked", actor=me, target=u.email, detail=f"tried to grant {t.slug}, which they don't hold", outcome="blocked", ip=client_ip(request))
        raise HTTPException(403, "You can only grant tools you have yourself.")
    existing = db.query(ToolAccess).filter_by(user_id=u.id, tool_id=t.id).one_or_none()
    if body.granted and existing is None:
        db.add(ToolAccess(user_id=u.id, tool_id=t.id, granted_by_id=me.id))
        log(db, "access_granted", actor=me, target=u.email, detail=t.slug, ip=client_ip(request))
    elif not body.granted and existing is not None:
        db.delete(existing)
        log(db, "access_removed", actor=me, target=u.email, detail=t.slug, ip=client_ip(request))
    return {"ok": True}


# ---- row-level rules (owner/admin) ------------------------------------------------
class RuleIn(BaseModel):
    table: str
    field: str
    op: str = "eq"
    value: str
    user_id: int | None = None
    team_id: int | None = None
    note: str = ""


ALLOWED_TABLES = {"master_rows": ["client", "team", "municipality", "vendor", "vendor_group"], "watched_docs": ["client", "entity"], "stored_files": ["team", "kind"]}


@router.get("/rules")
def list_rules(db: Session = Depends(get_db), me: User = Depends(require_admin)):
    rows = []
    for r in db.query(RowRule).order_by(RowRule.table, RowRule.id):
        if r.user_id:
            u = db.get(User, r.user_id)
            who = u.name if u else "(deleted user)"
        elif r.team_id:
            t = db.get(Team, r.team_id)
            who = (t.name + " (team)") if t else "(deleted team)"
        else:
            who = "Everyone"
        rows.append({"id": r.id, "table": r.table, "field": r.field, "op": r.op, "value": r.value, "who": who, "user_id": r.user_id, "team_id": r.team_id, "note": r.note})
    return {"rules": rows, "tables": ALLOWED_TABLES}


@router.post("/rules")
def add_rule(body: RuleIn, db: Session = Depends(get_db), me: User = Depends(require_admin), _h: User = Depends(require_human)):
    if body.table not in ALLOWED_TABLES or body.field not in ALLOWED_TABLES[body.table] or body.op not in ("eq", "in", "startswith"):
        raise HTTPException(400, "Unknown table, field or operator.")
    if not body.user_id and not body.team_id:
        raise HTTPException(400, "A rule applies to a person or a team.")
    if body.user_id and db.get(User, body.user_id) is None or body.team_id and db.get(Team, body.team_id) is None:
        raise HTTPException(400, "That person or team doesn't exist any more. Refresh the page and try again.")
    if not body.value.strip():
        raise HTTPException(400, "Give the rule a value, e.g. Absa.")
    r = RowRule(table=body.table, field=body.field, op=body.op, value=body.value.strip(), user_id=body.user_id, team_id=body.team_id, note=body.note, created_by_id=me.id)
    db.add(r)
    db.flush()
    log(db, "row_rule_added", actor=me, target=body.table, detail=f"{body.field} {body.op} {body.value}")
    return {"id": r.id}


@router.delete("/rules/{rid}")
def delete_rule(rid: int, db: Session = Depends(get_db), me: User = Depends(require_admin), _h: User = Depends(require_human)):
    r = db.get(RowRule, rid)
    if r:
        log(db, "row_rule_removed", actor=me, target=r.table, detail=f"{r.field} {r.op} {r.value}")
        db.delete(r)
    return {"ok": True}


# ---- teams (owner/admin) ---------------------------------------------------------
@router.post("/teams")
def create_team(body: dict, db: Session = Depends(get_db), me: User = Depends(require_admin), _h: User = Depends(require_human)):
    name = (body.get("name") or "").strip()[:120]
    if not name or not _slug(name):
        raise HTTPException(400, "Give the team a name with letters or digits in it.")
    if db.query(Team).filter(Team.slug == _slug(name)).count():
        raise HTTPException(400, "That team already exists.")
    t = Team(name=name, slug=_slug(name))
    db.add(t)
    db.flush()
    ensure_team_folder(db, t)
    log(db, "team_created", actor=me, target=name, detail=f"folder Teams/{safe_name(name)} created")
    return team_out(t)
