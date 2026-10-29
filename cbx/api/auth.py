from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from ..audit import log
from .. import VERSION
from .. import branding as B
from ..config import settings
from ..db import get_db, utcnow
from ..models import SignInTicket, TrustedDevice, User
from ..security import auth as A
from ..security import devices as D
from ..security import clock
from ..security import ratelimit as RL
from ..security import secretbox, totp
from ..security.deps import client_ip
from .schemas import user_out

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str
    password: str


class CodeIn(BaseModel):
    email: str
    code: str


class ChangePasswordIn(BaseModel):
    new_password: str
    repeat: str
    current_password: str | None = None


def _set_device_cookie(resp: Response, token: str, days: int, request: Optional[Request] = None) -> None:
    resp.set_cookie(D.DEVICE_COOKIE, token, httponly=True, samesite="lax", secure=_secure(request), max_age=days * 24 * 3600, path="/")


def _guard(ip: str, what: str) -> None:
    """Per-IP limit on the doors. Raises 429 with the wait, so a person sees a sentence, not a code."""
    try:
        RL.check(ip, limit=settings.login_rate_limit, window=settings.login_rate_window_seconds, what=what)
    except RL.TooMany as e:
        raise HTTPException(429, e.message, headers={"Retry-After": str(e.retry_after)}) from e


def _finish(db: Session, response: Response, request: Request, user: User, ip: str, *, pending: bool = False) -> dict:
    """Open the session and answer — the one place a sign-in actually completes. `pending` opens a
    session that can only set up the second step (and change a temporary password): nothing else
    answers it until two-step verification is in place."""
    s = A.open_session(db, user, ip=ip, agent=request.headers.get("user-agent", ""))
    if pending:
        s.pending_mfa = True
        db.flush()
    _set_cookie(response, s.id, request)
    RL.forget(ip)
    log(db, "login", actor=user, ip=ip, detail="second step still to be set up" if pending else "")
    if not pending:
        clock.note(request, user.id)  # 1.0.113: a signed-in person's browser time, for the owner's clock line only
    if user.is_admin and not pending:
        from .. import state

        state.seen_from(db, state.base_of(request))  # where links in emails should point
    return {"user": user_out(user, full=True), "must_change_password": user.must_change_password, "must_enrol_mfa": pending}


# ---- two-step verification: who needs it, how long a browser is remembered -------------------------
def mfa_required_for(user: User) -> bool:
    req = (settings.mfa_required or "none").lower()
    return req == "everyone" or (req == "admins" and user.is_admin)


def trust_days_for(user: User) -> int:
    """Owners and admins are asked every time unless CBX_MFA_ADMIN_TRUST_DAYS says otherwise."""
    return int(settings.mfa_admin_trust_days if user.is_admin else settings.device_trust_days)


def _expired(user: User) -> bool:
    return bool(user.expires_at and user.expires_at <= utcnow())


def _mfa_lock(db: Session, user: User, ip: str, why: str) -> None:
    """A wrong code. At CBX_MFA_MAX_FAILED in a row the account locks until an owner or admin unlocks it
    — the Password policy's rule — except the last owner who can still sign in (see A._may_lock)."""
    user.mfa_failed = (user.mfa_failed or 0) + 1
    if user.mfa_failed >= settings.mfa_max_failed and A._may_lock(db, user):
        user.locked = True
        user.locked_at = None  # an admin lifts this one; it does not lift itself
        log(db, "mfa_locked", actor=user, ip=ip, detail=f"{user.mfa_failed} wrong codes in a row", outcome="blocked")
    log(db, "mfa_failed", actor=user, ip=ip, detail=why, outcome="failed")
    db.commit()


# 1.0.113: an authenticator code is looked for around the person's learned clock difference, and a step
# that was already used (or an earlier one) is refused. Same rules for sign-in, set-up, recovery codes, remove.
USED_CODE = "That code was used already. Wait for the next one in the app."


def _claim_step(db: Session, user: User, step: int, drift: int) -> bool:
    """1.0.113: remember `step` as used — only if it is later than the last one, in one UPDATE, so two requests
    holding the same code cannot both pass (the second finds the step already written). False = used."""
    sql = text("UPDATE users SET mfa_last_step = :s, mfa_drift = :d WHERE id = :i AND COALESCE(mfa_last_step, -1) < :s")
    params = {"s": step, "d": drift, "i": user.id}
    try:
        n = db.execute(sql, params).rowcount
    except OperationalError:
        # SQLite: another request wrote this row after this one read it ("database is locked" on a stale snapshot).
        # Start again from what is committed now; the step it wrote makes this one "used".
        db.rollback()
        n = db.execute(sql, params).rowcount
    if n:
        set_committed_value(user, "mfa_last_step", step)
        set_committed_value(user, "mfa_drift", drift)
    return bool(n)


SETUP_MINUTES = 10  # 1.0.113: how long after /mfa/setup the wide search at /mfa/confirm stays open


def _setting_up(user: User) -> bool:
    return bool(user.mfa_setup_at and utcnow() - user.mfa_setup_at <= timedelta(minutes=SETUP_MINUTES))


def _app_code(db: Session, user: User, code: str, *, setup: bool = False) -> str:
    """'ok' (and the difference is learned, the step remembered) | 'used' | 'wrong'."""
    try:
        secret = secretbox.open_(user.mfa_secret) if user.mfa_secret else ""
    except secretbox.SecretBoxError:
        secret = ""
    at = clock.now()
    server = int(at // totp.STEP_SECONDS)
    if setup:
        step = totp.match_step(secret, code, window=totp.SETUP_WINDOW, drift=0, at=at)
    else:
        step = totp.match_step(secret, code, window=totp.SIGN_IN_WINDOW, drift=user.mfa_drift or 0, at=at)
    if step is None:
        return "wrong"
    return "ok" if _claim_step(db, user, step, totp.clamp_drift(step - server)) else "used"


def _unproven_email(user: User) -> bool:
    """Email codes this person chose but never proved with a code (every one switched before 1.0.113)."""
    return user.mfa_method == "email" and user.mfa_proven_at is None


def _secure(request: Optional[Request]) -> bool:
    """Secure on every cookie that travels over HTTPS (the tunnel), never on the LAN's plain HTTP —
    a Secure cookie set over HTTP is one the browser will not send back, i.e. nobody could sign in."""
    from ..security.deps import is_https

    return bool(settings.secure_cookies or (request is not None and is_https(request)))


def _set_cookie(resp: Response, sid: str, request: Optional[Request] = None) -> None:
    # The idle timeout is enforced server-side (load_session). The cookie itself must outlive it, or a
    # person who has been busy all day is thrown out exactly 8 h after signing in, mid-task.
    resp.set_cookie(A.SESSION_COOKIE, sid, httponly=True, samesite="lax", secure=_secure(request), max_age=14 * 24 * 3600, path="/")


# ---- 1.0.113: the "password passed" ticket ----------------------------------------------------------
TICKET_COOKIE = "cbx_signin"
TICKET_MINUTES = 10
TICKET_ATTEMPTS = 5


def _issue_ticket(db: Session, response: Response, request: Request, user: User, ip: str) -> None:
    """/login has checked the password and a code is still to come: a single-use ticket for this browser (HttpOnly
    cookie; the row keeps only the hash). Any earlier live ticket for this person is spent."""
    now = utcnow()
    for old in db.query(SignInTicket).filter(SignInTicket.user_id == user.id, SignInTicket.used_at.is_(None)).all():
        old.used_at = now
    raw = secrets.token_urlsafe(32)
    db.add(SignInTicket(user_id=user.id, token_hash=D.token_hash(raw), ip=ip, expires_at=now + timedelta(minutes=TICKET_MINUTES)))
    db.flush()
    response.set_cookie(TICKET_COOKIE, raw, httponly=True, samesite="lax", secure=_secure(request), max_age=TICKET_MINUTES * 60, path="/api/auth/")


def _live_ticket(db: Session, request: Request) -> Optional[SignInTicket]:
    raw = request.cookies.get(TICKET_COOKIE)
    if not raw:
        return None
    t = db.query(SignInTicket).filter(SignInTicket.token_hash == D.token_hash(raw), SignInTicket.used_at.is_(None)).one_or_none()
    if t is None or t.expires_at <= utcnow():
        return None
    return t


NO_TICKET = "Your sign-in has timed out. Sign in again with your password."


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = client_ip(request)
    agent = request.headers.get("user-agent", "")
    _guard(ip, "sign-in attempts")
    try:
        user = A.verify_login(db, body.email, body.password, ip)
    except A.LoginError as e:
        log(db, "login_failed", actor_label=body.email.strip().lower()[:120], outcome="failed", ip=ip, detail=str(e))
        db.commit()  # keep the failed-attempt counter even though we return an error
        raise HTTPException(401, str(e)) from e

    if _expired(user):
        log(db, "login_expired", actor=user, ip=ip, outcome="blocked", detail=f"access ended {user.expires_at.date().isoformat()}")
        db.commit()
        raise HTTPException(403, f"This account's access ended on {user.expires_at.strftime('%d %b %Y')}. Ask the person who invited you to extend it.")
    if user.mfa_method == "totp":
        # an authenticator app: no email involved; a remembered browser skips it for its trust period
        dev = D.trusted(db, user, request.cookies.get(D.DEVICE_COOKIE))
        if dev is not None and trust_days_for(user) > 0:
            D.touch(db, dev, ip)
            return _finish(db, response, request, user, ip)
        _issue_ticket(db, response, request, user, ip)  # 1.0.113
        db.commit()
        return {"needs_code": True, "method": "totp", "email": body.email.strip().lower(), "recovery": bool(user.mfa_recovery),
                "message": f"Type the 6-digit code the app shows for {B.product_name()}. No email is involved."}
    if not user.mfa_method and mfa_required_for(user):
        # first sign-in after 1.0.45, or a new person: the second step is set up before anything else opens
        return _finish(db, response, request, user, ip, pending=True)
    # 1.0.113: email codes that were never proven (switched to without a code) are proven now: an emailed code on
    # every browser, a remembered one included, and no fallback lets this person all the way in
    # 1.0.113 (review): whatever CBX_MFA_REQUIRED says — an unproven email method is never a second step anywhere
    must_prove = _unproven_email(user)
    if must_prove:
        needed, why = True, "email codes not proven yet"
    else:
        needed, why = D.challenge_needed(db, user, request.cookies.get(D.DEVICE_COOKIE))
    if not needed:
        dev = D.trusted(db, user, request.cookies.get(D.DEVICE_COOKIE))
        if dev is not None:
            D.touch(db, dev, ip)
        elif "no second factor sent" in why:
            # The company can still work, and the gap is on the record rather than assumed away.
            log(db, "login_unverified", actor=user, ip=ip, detail=why, outcome="blocked")
        return _finish(db, response, request, user, ip)

    row, sent, detail = D.issue(db, user, ip=ip, agent=agent)
    log(db, "login_code_sent", actor=user, ip=ip, detail=f"{D.describe(agent)} · {detail}", outcome="ok" if sent else "failed")
    if not sent and must_prove:
        # 1.0.113: the "let them in and say so" fallback below is for people whose second step was proven
        # before. Someone whose never was gets the set-up screen only (an authenticator app), nothing else.
        log(db, "login_unverified", actor=user, ip=ip, outcome="blocked",
            detail=f"no second factor sent and this person's email codes were never proven — set-up only. {detail}")
        return _finish(db, response, request, user, ip, pending=True)
    if not sent:
        # The same fallback as "this server cannot send email at all", for the case that is far more
        # likely on the day the mailer is switched on: the settings are filled in but the password is
        # wrong, or the port is blocked. Without this, the first restart after adding SMTP details
        # locks out every person at once — nobody has a confirmed device yet, so nobody is exempt,
        # including the owner, and the only way back in is editing .env on the server itself.
        # A door nobody can open is not security. So: let them in, and make the gap loud.
        log(db, "login_unverified", actor=user, ip=ip, outcome="blocked",
            detail=f"no second factor sent — the code could not be emailed. {detail}")
        return _finish(db, response, request, user, ip)
    _issue_ticket(db, response, request, user, ip)  # 1.0.113
    db.commit()
    if detail.startswith("terminal:"):
        log(db, "login_code_terminal", actor=user, ip=ip, detail=f"{D.describe(agent)} · email failed, code shown on the server: {detail[9:].strip()[:200]}", outcome="blocked")
        db.commit()
        return {"needs_code": True, "email": body.email.strip().lower(), "sent_to": "the server", "channel": "terminal",
                "message": f"I couldn't email your code, so it is showing in the {B.product_name()} window on the server — ask the owner or an admin to read it to you (they can also see it in Users & roles). It lasts {D.CODE_TTL_MINUTES} minutes."}
    masked = A.mask_email(user.email)
    return {"needs_code": True, "email": body.email.strip().lower(), "sent_to": masked,
            "message": f"This browser is new to me, so I have emailed a 6-digit code to {masked}. It lasts {D.CODE_TTL_MINUTES} minutes."}


@router.post("/verify")
def verify_code(body: CodeIn, request: Request, response: Response, db: Session = Depends(get_db)):
    """Second step of a sign-in from an unrecognised browser."""
    ip = client_ip(request)
    agent = request.headers.get("user-agent", "")
    _guard(ip, "code attempts")
    # 1.0.113: a code is checked only for the person whose password this browser has just passed (the ticket /login
    # set), never for whoever's email is typed here; wrong codes count on the ticket, and success spends it
    ticket = _live_ticket(db, request)
    if ticket is None:
        raise HTTPException(401, NO_TICKET)
    user = db.get(User, ticket.user_id)
    if user is None or not user.is_active or user.is_axl or user.email != body.email.strip().lower():
        raise HTTPException(401, "That code is not right.")  # never confirm an address exists
    if ticket.attempts >= TICKET_ATTEMPTS:
        ticket.used_at = utcnow()
        db.commit()
        raise HTTPException(401, "Too many wrong codes. " + NO_TICKET)
    ticket.attempts += 1  # kept by the commit on every wrong-code path below; spent on success
    if user.locked:
        raise HTTPException(401, "This account is locked. Ask an owner or admin to unlock it.")
    if user.mfa_method == "totp":
        code = body.code.strip()
        if totp.looks_like_recovery(code):
            left = totp.use_recovery_code(user.mfa_recovery or [], code)
            if left is None:
                _mfa_lock(db, user, ip, "recovery code not recognised")
                raise HTTPException(401, "That code is not right.")
            user.mfa_recovery = left
            log(db, "mfa_recovery_used", actor=user, ip=ip, detail=f"{len(left)} recovery code(s) left")
        else:
            got = _app_code(db, user, code)
            if got == "used":
                _mfa_lock(db, user, ip, "authenticator code used before")
                raise HTTPException(401, USED_CODE)
            if got != "ok":
                _mfa_lock(db, user, ip, "authenticator code wrong")
                raise HTTPException(401, "That code is not right. Codes change every 30 seconds — try the current one.")
        user.mfa_failed = 0
        _spend(db, response, ticket)
        days = trust_days_for(user)
        if days > 0:
            dev, cookie_value = D.remember(db, user, ip=ip, agent=agent, days=days)
            _set_device_cookie(response, cookie_value, days, request)
            log(db, "device_trusted", actor=user, ip=ip, detail=f"{dev.label} · trusted for {days} days")
        out = _finish(db, response, request, user, ip)
        if user.mfa_recovery is not None and len(user.mfa_recovery) <= 2:
            out["recovery_low"] = len(user.mfa_recovery)
        return out
    try:
        D.verify(db, user, body.code)
    except ValueError as e:
        log(db, "login_code_failed", actor=user, ip=ip, detail=str(e), outcome="failed")
        db.commit()
        raise HTTPException(401, str(e)) from e
    _spend(db, response, ticket)
    if _unproven_email(user):
        user.mfa_proven_at = utcnow()  # 1.0.113: a code reached their inbox and came back: email codes are proven
        log(db, "mfa_email_proven", actor=user, ip=ip, detail="an emailed sign-in code was typed")
    dev, cookie_value = D.remember(db, user, ip=ip, agent=agent)
    _set_device_cookie(response, cookie_value, settings.device_trust_days, request)
    log(db, "device_trusted", actor=user, ip=ip, detail=f"{dev.label} · trusted for {settings.device_trust_days} days")
    return _finish(db, response, request, user, ip)


def _spend(db: Session, response: Response, ticket: SignInTicket) -> None:
    ticket.used_at = utcnow()
    db.flush()
    response.delete_cookie(TICKET_COOKIE, path="/api/auth/")


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    sid = request.cookies.get(A.SESSION_COOKIE)
    user = A.load_session(db, sid, request)
    A.revoke_session(db, sid)
    response.delete_cookie(A.SESSION_COOKIE, path="/")
    if user:
        log(db, "logout", actor=user, ip=client_ip(request))
    return {"ok": True}


@router.get("/me")
def me(request: Request, db: Session = Depends(get_db)):
    user = A.load_session(db, request.cookies.get(A.SESSION_COOKIE), request)
    if user is None:
        raise HTTPException(401, "Please sign in.")
    if not getattr(request.state, "pending_mfa", False) and getattr(request.state, "viewer", None) is None:
        clock.note(request, user.id, db)  # 1.0.113: the app shell's time sample (signed in, not pending, not viewing as)
    from ..security.deps import has_tool
    from ..models import Tool

    # workflow tools (Node-RED, before 1.0.48) are gone: their rows stay for a rollback, but nobody is shown them
    tools = [t for t in db.query(Tool).filter(Tool.is_active.is_(True), (Tool.kind.is_(None)) | (Tool.kind != "workflow")).order_by(Tool.sort) if has_tool(db, user, t.slug)]
    from .schemas import tool_out

    b = B.current()
    pending = bool(getattr(request.state, "pending_mfa", False))
    from ..support import core as SC

    u_out = user_out(user, full=True)
    u_out["support_staff"] = SC.is_staff(db, user)  # 1.0.66: the Support menu
    u_out["support_assign"] = SC.can_assign(user)
    return {"user": u_out, "tools": [tool_out(t) for t in tools], "product": b["product"], "org": b["org"], "tagline": b["tagline"], "ai_name": b["ai_name"], "version": VERSION, "model": _model_label(),
            "must_enrol_mfa": pending, "mfa": mfa_status(user, pending), "viewing_as": _viewing(request), "moved": _moved(db)}


def _moved(db) -> list:
    """Built-in pages whose data now lives in a tool folder (1.0.63: Entity Compliance) — their old addresses open the tool."""
    try:
        from ..docwatch.copy_in import moved

        return ["doc-watch"] if moved(db) else []
    except Exception:  # noqa: BLE001
        return []


def _viewing(request: Request):
    """Who is really looking, when an owner or admin is viewing the app as this person."""
    real = getattr(request.state, "viewer", None)
    if real is None:
        return None
    from ..models import LoginSession

    s = request.state.db.get(LoginSession, request.cookies.get(A.SESSION_COOKIE) or "") if getattr(request.state, "db", None) else None
    return {"by": {"id": real.id, "name": real.name, "email": real.email}, "until": s.view_as_until.isoformat() if s is not None and s.view_as_until else None}


def mfa_status(user: User, pending: bool = False) -> dict:
    return {"method": user.mfa_method or "", "enrolled_at": user.mfa_enrolled_at.isoformat() if user.mfa_enrolled_at else None,
            "recovery_left": len(user.mfa_recovery or []) if user.mfa_method == "totp" else None,
            "required": mfa_required_for(user), "email_allowed": bool(settings.mfa_email_fallback), "pending": pending,
            "trust_days": trust_days_for(user), "proven": bool(user.mfa_method) and not _unproven_email(user)}


@router.post("/change-password")
def change_password(body: ChangePasswordIn, request: Request, db: Session = Depends(get_db)):
    sid = request.cookies.get(A.SESSION_COOKIE)
    user = A.load_session(db, sid, request)
    if user is None:
        raise HTTPException(401, "Please sign in.")
    if body.new_password != body.repeat:
        raise HTTPException(400, "The two passwords don't match.")
    if (p := A.password_problem(body.new_password)) is not None:
        raise HTTPException(400, p)
    if not user.must_change_password:
        if not body.current_password or not A.check_password(user, body.current_password):
            raise HTTPException(400, "Your current password is wrong.")
    if A.check_password(user, body.new_password):
        raise HTTPException(400, "Choose a password you haven't used here before.")
    user.password_hash = A.hash_password(body.new_password)
    user.must_change_password = False
    user.reset_requested_at = None
    # A password change ends every other session: anyone holding a stolen or shared cookie is out.
    n = A.revoke_other_sessions(db, user, keep=sid)
    # ...and every other confirmed browser. If someone changes their password because they think
    # somebody got in, a browser that never has to ask for a code again is exactly what they are
    # trying to shut. The one they are sitting at keeps its trust, so they are not asked mid-change.
    keep = D.token_hash(request.cookies.get(D.DEVICE_COOKIE))
    devices = 0
    for d in db.query(TrustedDevice).filter(TrustedDevice.user_id == user.id, TrustedDevice.revoked.is_(False)).all():
        if d.token != keep:
            d.revoked = True
            devices += 1
    db.flush()
    bits = [f"{n} other session(s) signed out"] if n else []
    if devices:
        bits.append(f"{devices} confirmed device(s) will be asked for a code again")
    log(db, "password_changed", actor=user, ip=client_ip(request), detail=" · ".join(bits))
    return {"ok": True}


@router.post("/forgot")
def forgot(body: dict, request: Request, db: Session = Depends(get_db)):
    """No self-service reset. Marks the account so owners/admins see 'Reset requested'."""
    _guard(client_ip(request), "forgot")  # the same per-IP limit as the sign-in door
    email = (body.get("email") or "").strip().lower()
    user = db.query(User).filter(User.email == email).one_or_none()
    if user and not user.is_axl:
        user.reset_requested_at = utcnow()
        log(db, "reset_requested", actor=user, ip=client_ip(request))
    return {"ok": True, "message": "If that account exists, the owner has been asked to issue a temporary password."}


@router.post("/sign-out-others")
def sign_out_others(request: Request, db: Session = Depends(get_db)):
    sid = request.cookies.get(A.SESSION_COOKIE)
    user = A.load_session(db, sid, request)
    if user is None:
        raise HTTPException(401, "Please sign in.")
    n = A.revoke_other_sessions(db, user, keep=sid)
    log(db, "sessions_revoked", actor=user, detail=f"{n} other sessions")
    return {"revoked": n}


# ---- two-step verification: set up, confirm, recovery codes, switch, remove -------------------------


class MfaCodeIn(BaseModel):
    code: str = ""
    current_password: str | None = None


def _session_user(request: Request, db: Session) -> tuple[User, str]:
    sid = request.cookies.get(A.SESSION_COOKIE)
    user = A.load_session(db, sid, request)
    if user is None:
        raise HTTPException(401, "Please sign in.")
    if user.must_change_password:
        raise HTTPException(403, "Please set your own password first.")
    if user.locked:  # 1.0.113: wrong codes at set-up, recovery codes and remove lock the account too
        raise HTTPException(403, "This account is locked. Ask an owner or admin to unlock it.")
    return user, sid


def _clear_pending(db: Session, sid: str) -> None:
    from ..models import LoginSession

    s = db.get(LoginSession, sid)
    if s is not None and s.pending_mfa:
        s.pending_mfa = False
        db.flush()


@router.get("/mfa")
def mfa_get(request: Request, db: Session = Depends(get_db)):
    user, _ = _session_user(request, db)
    return mfa_status(user, bool(getattr(request.state, "pending_mfa", False)))


@router.post("/mfa/setup")
def mfa_setup(body: MfaCodeIn, request: Request, db: Session = Depends(get_db)):
    """Start (or start over): a new secret, the QR code, the letters to type instead. Nothing changes
    for sign-in until /mfa/confirm proves the app has it. Someone who already has an app set up must
    give their current password to replace it."""
    user, _ = _session_user(request, db)
    if user.mfa_method == "totp" and not (body.current_password and A.check_password(user, body.current_password)):
        raise HTTPException(400, "Type your current password to set up a new authenticator.")
    secret = totp.new_secret()
    user.mfa_secret = secretbox.seal(secret)  # unconfirmed while mfa_method is not "totp"
    user.mfa_drift = 0  # 1.0.113: a new phone: its difference is found again at /mfa/confirm
    user.mfa_last_step = 0
    user.mfa_setup_at = utcnow()  # 1.0.113: the wide search at /mfa/confirm is open for SETUP_MINUTES from now
    db.flush()
    u = totp.uri(secret, user.email, B.product_name())
    return {"secret": " ".join(secret[i:i + 4] for i in range(0, len(secret), 4)), "uri": u, "qr_svg": totp.qr_svg(u), "issuer": B.product_name()}


@router.post("/mfa/confirm")
def mfa_confirm(body: MfaCodeIn, request: Request, response: Response, db: Session = Depends(get_db)):
    """The first code from the app proves the secret landed. Switches the account to the app, hands
    out the recovery codes (this is the only time they are shown), and opens the session fully."""
    user, sid = _session_user(request, db)
    ip = client_ip(request)
    _guard(ip, "code attempts")  # 1.0.113
    try:
        secret = secretbox.open_(user.mfa_secret) if user.mfa_secret else ""
    except secretbox.SecretBoxError:
        secret = ""
    if not secret:
        raise HTTPException(400, "Start with 'Set up' first.")
    # 1.0.113: ±10 steps (±5 minutes) here — the person is signed in with their password and the secret is minutes
    # old — so a server clock that is off is found and learned, not fought
    # 1.0.113 (review): only while a set-up is in progress (SETUP_MINUTES after /mfa/setup); otherwise ±2 like sign-in.
    # A wrong code counts towards the lock like any other.
    got = _app_code(db, user, body.code, setup=_setting_up(user))
    if got != "ok":
        _mfa_lock(db, user, ip, "wrong code while setting up" if got == "wrong" else "code used before while setting up")
        raise HTTPException(400, "That code is not right. Wait for the next one in the app and try again." if got == "wrong" else USED_CODE)
    diff = (user.mfa_drift or 0) * totp.STEP_SECONDS
    clock_note = (f"; clock difference ~{abs(diff)} s (the {'phone is behind the server' if diff < 0 else 'phone is ahead of the server'})"
                  if abs(diff) > 30 else "")
    codes, hashes = totp.new_recovery_codes()
    was = user.mfa_method
    user.mfa_method = "totp"
    user.mfa_enrolled_at = utcnow()
    user.mfa_proven_at = utcnow()
    user.mfa_setup_at = None
    user.mfa_recovery = hashes
    user.mfa_failed = 0
    _clear_pending(db, sid)
    RL.forget(ip)
    clock.note(request, user.id)  # 1.0.113: a passed step
    # the browser the person set it up on is a browser they hold — remembered like one that passed a code
    days = trust_days_for(user)
    if days > 0:
        dev, cookie_value = D.remember(db, user, ip=ip, agent=request.headers.get("user-agent", ""), days=days)
        _set_device_cookie(response, cookie_value, days, request)
    if was and was != "totp":
        A.revoke_other_sessions(db, user, keep=sid)
    log(db, "mfa_enrolled", actor=user, ip=ip, detail=("switched from email codes to " if was == "email" else "replaced the app with a new one" if was == "totp" else "") + "an authenticator app; 10 recovery codes issued" + clock_note)
    return {"ok": True, "recovery_codes": codes, "mfa": mfa_status(user)}


@router.post("/mfa/use-email")
def mfa_use_email(request: Request, db: Session = Depends(get_db)):
    """No authenticator app: a code to the inbox at each sign-in from a browser we have not seen — when
    this server allows it. External people without a company phone need this door.

    1.0.113: the switch is proven first. This sends a 6-digit code the way sign-in codes go (the inbox, or the
    server's window where that is how this server works); nothing changes until /mfa/use-email/confirm gets it
    back. Before, this switched the account and opened a pending session without any code at all."""
    user, _ = _session_user(request, db)
    ip, agent = client_ip(request), request.headers.get("user-agent", "")
    _email_allowed(user)
    _guard(ip, "code requests")
    row, sent, detail = D.issue(db, user, ip=ip, agent=agent, purpose=D.USE_EMAIL)  # 1.0.113: never a sign-in code
    if not sent:
        row.used_at = utcnow()  # a code nobody received is spent at once
        log(db, "mfa_email_unsent", actor=user, ip=ip, detail=f"switching to email codes: {detail}"[:400], outcome="failed")
        db.commit()
        raise HTTPException(400, "Email codes can't be sent from this server right now — set up an authenticator app.")
    log(db, "mfa_email_code_sent", actor=user, ip=ip, detail=f"switching to email codes · {D.describe(agent)} · {detail}"[:400])
    db.commit()
    if detail.startswith("terminal:"):
        return {"sent_to": "the server", "channel": "terminal",
                "message": f"I couldn't email your code, so it is showing in the {B.product_name()} window on the server — ask the owner or an admin to read it to you. It lasts {D.CODE_TTL_MINUTES} minutes."}
    masked = A.mask_email(user.email)
    return {"sent_to": masked, "message": f"I have emailed a 6-digit code to {masked}. It lasts {D.CODE_TTL_MINUTES} minutes."}


def _email_allowed(user: User) -> None:
    if user.locked:
        raise HTTPException(400, "This account is locked. Ask an owner or admin to unlock it.")
    if not settings.mfa_email_fallback:
        raise HTTPException(400, "Email codes are not allowed on this server — set up an authenticator app.")
    if user.mfa_method == "totp":
        raise HTTPException(400, "You have an authenticator app set up; remove it first if you want email codes.")


class EmailCodeIn(BaseModel):
    code: str = ""


@router.post("/mfa/use-email/confirm")
def mfa_use_email_confirm(body: EmailCodeIn, request: Request, response: Response, db: Session = Depends(get_db)):
    """1.0.113: the code from /mfa/use-email. Right: the account is on email codes (proven), the pending session
    opens, and this browser is remembered like one that passed a sign-in code. Wrong: counted like any wrong code."""
    user, sid = _session_user(request, db)
    ip, agent = client_ip(request), request.headers.get("user-agent", "")
    _email_allowed(user)
    _guard(ip, "code attempts")
    try:
        D.verify(db, user, body.code, purpose=D.USE_EMAIL)  # 1.0.113: only a code made by /mfa/use-email, for this session's person
    except ValueError as e:
        msg = str(e)
        if msg.startswith(("That code is not right", "Too many wrong codes")):
            _mfa_lock(db, user, ip, "email code wrong while switching to email codes")
            if msg.startswith("That code is not right"):
                msg = "That code is not right."  # the account's wrong-code lock comes first: "N tries left" would mislead
        else:
            log(db, "mfa_failed", actor=user, ip=ip, detail=f"switching to email codes: {msg}"[:400], outcome="failed")
            db.commit()
        raise HTTPException(400, msg) from e
    was = user.mfa_method
    user.mfa_method = "email"
    user.mfa_enrolled_at = utcnow()
    user.mfa_proven_at = utcnow()
    user.mfa_secret = ""
    user.mfa_recovery = []
    user.mfa_failed = 0
    user.mfa_drift = 0
    user.mfa_last_step = 0
    _clear_pending(db, sid)
    RL.forget(ip)
    clock.note(request, user.id)  # 1.0.113: a passed step
    dev, cookie_value = D.remember(db, user, ip=ip, agent=agent)
    _set_device_cookie(response, cookie_value, settings.device_trust_days, request)
    log(db, "mfa_enrolled", actor=user, ip=ip, detail=("email codes proven with a code" if was == "email" else "codes by email at each new browser — proven with a code") + f" · {dev.label} trusted for {settings.device_trust_days} days")
    return {"ok": True, "mfa": mfa_status(user)}


@router.post("/mfa/recovery")
def mfa_recovery(body: MfaCodeIn, request: Request, db: Session = Depends(get_db)):
    """New recovery codes (the old ones stop working). Needs a code from the app right now."""
    user, _ = _session_user(request, db)
    if user.mfa_method != "totp":
        raise HTTPException(400, "Recovery codes go with an authenticator app.")
    ip = client_ip(request)
    _guard(ip, "code attempts")  # 1.0.113
    got = _app_code(db, user, body.code)
    if got != "ok":
        _mfa_lock(db, user, ip, "authenticator code wrong for new recovery codes" if got == "wrong" else "authenticator code used before")
        raise HTTPException(400, "That code is not right." if got == "wrong" else USED_CODE)
    user.mfa_failed = 0
    codes, hashes = totp.new_recovery_codes()
    user.mfa_recovery = hashes
    log(db, "mfa_recovery_reset", actor=user, ip=client_ip(request), detail="10 new recovery codes; the old ones are void")
    return {"ok": True, "recovery_codes": codes}


@router.post("/mfa/remove")
def mfa_remove(body: MfaCodeIn, request: Request, db: Session = Depends(get_db)):
    """Back to no second step — only where this server does not require one for this person."""
    user, _ = _session_user(request, db)
    if mfa_required_for(user):
        raise HTTPException(400, "Two-step verification is required for your account." + (" You can switch to email codes instead." if settings.mfa_email_fallback else ""))
    if user.mfa_method == "totp":
        ip = client_ip(request)
        _guard(ip, "code attempts")  # 1.0.113
        got = _app_code(db, user, body.code)
        if got != "ok":
            _mfa_lock(db, user, ip, "authenticator code wrong to remove the app" if got == "wrong" else "authenticator code used before")
            raise HTTPException(400, "Type the current code from your app to remove it." if got == "wrong" else USED_CODE)
    user.mfa_failed = 0
    user.mfa_method = ""
    user.mfa_proven_at = None
    user.mfa_secret = ""
    user.mfa_recovery = []
    user.mfa_enrolled_at = None
    log(db, "mfa_removed", actor=user, ip=client_ip(request))
    return {"ok": True, "mfa": mfa_status(user)}


def _model_label() -> str:
    """Short label for the sidebar: 'C-EXA · qwen3.8:27b' or 'C-EXA offline (rules only)'."""
    from ..cexa.gateway import ollama_status

    ai = B.ai_name()
    st = ollama_status()
    if st.get("mode") == "stub":
        return f"{ai} · no model"
    if st.get("online") and st.get("cexa_ready", True):
        return f"{ai} · {settings.cexa_model}"
    return f"{ai} · offline"
