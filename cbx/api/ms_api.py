"""1.0.97: sign in with Microsoft (Edwin, 13 Oct: "can we make it use microsoft login?"). The browser routes
(/auth/microsoft and its callback) and the owner's settings card; the work is cbx/mssignin.py, the same file as
CBX-Connect 2.8.8. Company staff may use Microsoft or their CBX password; outside people keep email, password
and CBX's own second step."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import mssignin as M
from ..audit import log
from ..db import get_db, utcnow
from ..models import User
from ..security import auth as A
from ..security.deps import client_ip, current_user, is_https

router = APIRouter(tags=["microsoft"])


def _owner(me: User = Depends(current_user)) -> User:
    if not me.is_owner or me.is_axl:
        raise HTTPException(403, "Only the owner changes Microsoft sign-in.")
    return me


@router.get("/api/auth/microsoft")
def public(db: Session = Depends(get_db)):
    return M.public(db)


@router.get("/api/auth/microsoft/settings")
def card(me: User = Depends(_owner), db: Session = Depends(get_db)):
    return M.card(db)


@router.put("/api/auth/microsoft/settings")
def save(body: dict, me: User = Depends(_owner), db: Session = Depends(get_db)):
    try:
        out = M.save(db, me, body or {})
    except PermissionError as e:
        raise HTTPException(403, str(e)) from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    db.commit()
    return out


@router.post("/api/auth/microsoft/test")
def test(me: User = Depends(_owner), db: Session = Depends(get_db)):
    return M.test(db)


@router.get("/auth/microsoft")
def start(request: Request, db: Session = Depends(get_db)):
    try:
        url, cookie = M.start(db, request.query_params.get("next", "/"))
    except M.SignInError as e:
        return RedirectResponse(f"/?ms={e.code}", 303)
    r = RedirectResponse(url, 303)
    r.set_cookie(M.COOKIE, cookie, httponly=True, samesite="lax", secure=is_https(request), max_age=M.COOKIE_MAX_AGE, path=M.PATH)
    return r


@router.get("/auth/microsoft/callback")
def back(request: Request, db: Session = Depends(get_db)):
    from .auth import _set_cookie

    ip, q = client_ip(request), request.query_params
    try:
        user, nxt = M.finish(db, code=q.get("code", ""), state=q.get("state", ""), cookie=request.cookies.get(M.COOKIE, ""),
                             error=q.get("error_description") or q.get("error") or "")
    except M.SignInError as e:
        log(db, "login_microsoft_refused", actor_label=(e.detail or e.code)[:120], outcome="blocked", ip=ip, detail=e.code)
        db.commit()
        r = RedirectResponse(f"/?ms={e.code}", 303)
        r.delete_cookie(M.COOKIE, path=M.PATH)
        return r
    if hasattr(user, "last_seen_at"):
        user.last_seen_at = utcnow()
    s = A.open_session(db, user, ip=ip, agent=request.headers.get("user-agent", ""))  # no CBX second step: the company's MFA was Microsoft's
    log(db, "login", actor=user, ip=ip, detail="Microsoft")
    db.commit()
    r = RedirectResponse("/" + (f"#{nxt.lstrip('/#')}" if nxt not in ("/", "") else ""), 303)
    _set_cookie(r, s.id, request)
    r.delete_cookie(M.COOKIE, path=M.PATH)
    return r
