"""FastAPI dependencies: who is calling, what may they do. Deny by default."""
from __future__ import annotations

from typing import Callable, Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..audit import log
from ..db import get_db
from ..models import Tool, ToolAccess, User
from .auth import SESSION_COOKIE, load_session


def _trusted_proxies() -> set[str]:
    from ..config import settings

    return {x.strip() for x in (settings.trusted_proxies or "").split(",") if x.strip()}


def client_ip(request: Request) -> str:
    """The address of the person, for rate limits, lockouts and the audit trail.

    X-Forwarded-For is believed only when the connection itself comes from a trusted proxy (the ngrok
    agent on this machine, by default) — and then its LAST entry, the one that proxy appended, not the
    first, which the client can write. A browser on the LAN talking to us directly is its own address,
    whatever headers it sends. Before this, one header per request was a new address per request, and
    the sign-in limits counted nothing."""
    peer = request.client.host if request.client else ""
    if peer in _trusted_proxies():
        fwd = [x.strip() for x in request.headers.get("x-forwarded-for", "").split(",") if x.strip()]
        if fwd:
            return fwd[-1]
    return peer


def is_https(request: Request) -> bool:
    """Did this request arrive over HTTPS — directly, or through a trusted proxy that says so."""
    if request.url.scheme == "https":
        return True
    peer = request.client.host if request.client else ""
    if peer in _trusted_proxies():
        return request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower() == "https"
    return False


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = load_session(db, request.cookies.get(SESSION_COOKIE), request)
    if user is None:
        raise HTTPException(401, "Please sign in.")
    if user.must_change_password and not request.url.path.startswith("/api/auth/") and getattr(request.state, "viewer", None) is None:
        raise HTTPException(403, "Please set your own password first.")
    if getattr(request.state, "pending_mfa", False) and not request.url.path.startswith("/api/auth/"):
        raise HTTPException(403, "Finish setting up two-step verification first.")
    from .roles import attach

    attach(db, user)  # so User.can()/is_admin read this server's roles table, not the shipped defaults
    request.state.user = user
    return user


def require_cap(capability: str) -> Callable:
    """Gate on a capability, not a role name — so a role invented in Settings opens the same doors a
    built-in does. The three names below are unchanged and still used in 152 places."""

    def dep(user: User = Depends(current_user)) -> User:
        if not user.can(capability):
            raise HTTPException(403, "You don't have access to this.")
        return user

    return dep


require_admin = require_cap("admin.all")
require_owner = require_cap("everything")
# Users & roles, Audit trail, Entity Compliance management. A manager gets in, but every endpoint
# behind this must then narrow what they see to their own team (see cbx/api/users.py, audit.py, rls.py).
require_manager = require_cap("people.view")


async def require_human(request: Request, user: User = Depends(current_user)) -> User:
    """Some things only a person may do (grant access, promote versions, approve or delete profiles).

    "A person" is not the same as "not the C-EXA account": C-EXA's hands call the app through a
    short-lived session belonging to the person she is helping, so row-level security and the audit
    trail see them and not her. That session is marked (`cexa-` prefix; `axl-` before 1.0.49) and is refused here — the
    check has to be on the session, not only on the account, or every one of these endpoints is
    reachable by asking C-EXA nicely.

    1.0.81: routine work is hers (cbx/cexa/policy.py decides, here, on the request itself — method, address and
    body): for a `cexa-` session only "do" passes; "ask" waits for the owner's tap, which then runs through a
    session that is a person's. The C-EXA account itself never passes."""
    sid = request.cookies.get(SESSION_COOKIE) or ""
    if user.is_axl or sid.startswith("axl-") or (sid.startswith("cexa-") and not await _routine(request)):
        from ..branding import ai_name

        raise HTTPException(403, f"{ai_name()} can't do this — a person has to.")
    return user


async def _routine(request: Request) -> bool:
    import json

    from ..cexa.policy import DO, decide

    body = None
    if "json" in (request.headers.get("content-type") or ""):
        try:
            body = json.loads((await request.body()) or b"null")
        except ValueError:
            body = None
    sid = request.cookies.get(SESSION_COOKIE) or ""
    # 1.0.114: an agent's work calls through `cexa-a-` sessions — C-EXA's own set-up is never theirs (cbx/cexa/policy.py)
    return decide(request.method, request.url.path, body, agent=sid.startswith("cexa-a-"))[0] == DO


def has_tool(db: Session, user: User, slug: str) -> bool:
    # Managers are deliberately not here: they need grants like users do.
    if user.is_admin or user.is_axl:
        return True
    tool = db.query(Tool).filter(Tool.slug == slug, Tool.is_active.is_(True)).one_or_none()
    if tool is None or tool.admin_only:
        return False
    return db.query(ToolAccess).filter(ToolAccess.tool_id == tool.id, ToolAccess.user_id == user.id).count() > 0


def require_tool(slug: str | None = None) -> Callable:
    """`require_tool("pdf-studio")` for a fixed tool; `require_tool()` reads the `{slug}` path
    parameter, so one route can serve every workflow tool (they're rows, not code)."""

    def dep(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> User:
        s = slug or str(request.path_params.get("slug") or "")
        if not s or not has_tool(db, user, s):
            log(db, "tool_denied", actor=user, target=s or "?", outcome="blocked", ip=client_ip(request))
            raise HTTPException(403, "You don't have access to this tool.")
        return user

    return dep


def person_from_cookie(sid: Optional[str]) -> Optional[User]:
    """Who is asking, outside a request's own dependencies (the sandbox door, 1.0.80): a person signed in to CBX
    — any role; the door then asks whether that person may enter that slot (cbx/versions/areas.may_enter).
    C-EXA's hands sessions and accounts that must still change their password are refused."""
    from ..db import session_scope
    from .roles import attach

    if not sid or sid.startswith(("cexa-", "axl-")):
        return None
    with session_scope() as db:
        u = load_session(db, sid)
        if u is None or u.is_axl or u.must_change_password:
            return None
        attach(db, u)
        db.expunge(u)
        return u


def admin_from_cookie(sid: Optional[str]) -> Optional[User]:
    """Who is asking, outside a request's own dependencies (the sandbox door): an admin signed in to CBX,
    or nobody. The session must be a person's own: C-EXA's short-lived hands sessions (`cexa-` prefix; `axl-` before 1.0.49)
    are refused, as they are for every gate that grants power."""
    from ..db import session_scope
    from .roles import attach

    if not sid or sid.startswith(("cexa-", "axl-")):
        return None
    with session_scope() as db:
        u = load_session(db, sid)
        if u is None or u.is_axl or u.must_change_password:
            return None
        attach(db, u)
        if not u.is_admin:
            return None
        db.expunge(u)
        return u
