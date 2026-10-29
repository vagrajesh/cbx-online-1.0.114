"""Email + password sign-in for launch (Microsoft sign-in slots in later by replacing
`verify_login`). argon2 hashing, temporary passwords, forced change, lockout, idle sessions."""
from __future__ import annotations

import hashlib
import secrets
import string
from datetime import timedelta
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy.orm import Session

from ..config import settings
from ..db import utcnow
from ..models import LoginSession, User
from . import viewas  # noqa: F401 — registers the read-only guard for "View as"

_ph = PasswordHasher()
SESSION_COOKIE = "cbx_session"


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def check_password(user: User, pw: str) -> bool:
    if not user.password_hash:
        return False
    try:
        return _ph.verify(user.password_hash, pw)
    except VerifyMismatchError:
        return False


def temp_password() -> str:
    """Readable temporary password: three words-ish blocks + digits. Shown once to the owner/admin."""
    alphabet = string.ascii_lowercase
    blocks = ["".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(2)]
    return f"{blocks[0]}-{blocks[1]}-{secrets.randbelow(900)+100}"


# A small list of the most common leaked passwords; the real check should be against a
# larger local list (data/leaked_passwords.txt, one per line) if present.
_COMMON = {"password", "password1", "123456", "12345678", "qwerty123", "welcome1", "letmein", "changeme123!", "admin123"}


def password_problem(pw: str) -> Optional[str]:
    if len(pw) < 12:
        return "Use at least 12 characters."
    if pw.lower() in _COMMON:
        return "That password appears in leaked-password lists. Pick another."
    leaked = settings.data_dir / "leaked_passwords.txt"
    if leaked.exists():
        h = hashlib.sha1(pw.encode()).hexdigest().upper()
        with leaked.open(encoding="utf-8", errors="replace") as f:  # 1.0.100: Windows reads cp1252 without it
            for line in f:
                ln = line.strip()
                if ln == pw or ln.upper() == h:  # the list may hold plain passwords or SHA-1 hashes
                    return "That password appears in leaked-password lists. Pick another."
    return None


def mask_email(address: str) -> str:
    """"o****r@cbreexcellerate.com" — enough for a person to recognise their own inbox, not enough
    to hand someone else's address to whoever is at the keyboard."""
    a = (address or "").strip()
    if "@" not in a:
        return "your email"
    name, _, domain = a.partition("@")
    if len(name) <= 2:
        return f"{name[:1]}*@{domain}"
    return f"{name[0]}{'*' * min(6, len(name) - 2)}{name[-1]}@{domain}"


class LoginError(Exception):
    pass


def _lock_minutes_left(user: User) -> Optional[int]:
    """Minutes still to wait on a wrong-password lock: 0 = the wait is over, None = this lock does
    not lift on its own (an admin set it, or CBX_LOGIN_LOCKOUT_MINUTES is 0)."""
    mins = settings.login_lockout_minutes
    if mins <= 0 or user.locked_at is None:
        return None
    waited = (utcnow() - user.locked_at).total_seconds() / 60
    return max(0, int(mins - waited + 0.999))


def _may_lock(db: Session, user: User) -> bool:
    """Never lock the last owner who can still sign in.

    The owner is the one person with nobody above them to ask, so locking the last one leaves the
    whole system with no way back in — which is exactly what happened on 16 Sep 2026. They are still
    rate-limited by every other rule; they just cannot be shut out permanently."""
    if user.role != "owner":
        return True
    others = (
        db.query(User)
        .filter(User.role == "owner", User.is_axl.is_(False), User.id != user.id, User.locked.is_(False), User.is_active.is_(True))
        .count()
    )
    return others > 0


def verify_login(db: Session, email: str, pw: str, ip: str = "") -> User:
    user = db.query(User).filter(User.email == email.strip().lower()).one_or_none()
    if user is None or not user.is_active or user.is_axl:
        raise LoginError("That email and password don't match.")
    if user.locked:
        left = _lock_minutes_left(user)
        if left is None:  # an admin locked this account by hand — only an admin lifts it
            raise LoginError("This account is locked. Ask an owner or admin to unlock it.")
        if left > 0:
            raise LoginError(f"Too many wrong passwords. Try again in {left} minute{'s' if left != 1 else ''}.")
        user.locked = False  # the wait is over: let them try again
        user.locked_at = None
        user.failed_logins = 0
        db.flush()
    if not check_password(user, pw):
        user.failed_logins += 1
        if user.failed_logins >= settings.login_max_attempts and _may_lock(db, user):
            user.locked = True
            user.locked_at = utcnow()
        db.flush()
        raise LoginError("That email and password don't match.")
    user.failed_logins = 0
    user.locked_at = None
    user.last_seen = utcnow()
    return user


def open_session(db: Session, user: User, ip: str = "", agent: str = "") -> LoginSession:
    s = LoginSession(id=secrets.token_urlsafe(32), user_id=user.id, ip=ip, agent=agent[:255])
    db.add(s)
    db.flush()
    return s


def load_session(db: Session, sid: Optional[str], request=None) -> Optional[User]:
    if not sid:
        return None
    s = db.get(LoginSession, sid)
    if s is None or s.revoked:
        return None
    if request is not None:
        request.state.pending_mfa = bool(getattr(s, "pending_mfa", False))
    now = utcnow()
    if now - s.last_seen > timedelta(hours=settings.session_idle_hours):
        s.revoked = True
        return None
    user = db.get(User, s.user_id)
    if user is None or not user.is_active:
        return None
    # Touch `last_seen` at most once a minute, not on every call. Every request used to write it,
    # which made every request a writer — and a browser fires several at once (the page, the sidebar
    # counts, the drawer). Two of them reading the same session row and then both trying to update
    # it is SQLite's one unwaitable lock: the second transaction's snapshot is stale, so it fails at
    # once, whatever busy_timeout says. That was the "database is locked" on a fresh install with
    # one person signed in. A minute's granularity is plenty for an 8-hour idle timeout.
    if now - s.last_seen > timedelta(seconds=60):
        s.last_seen = now
        user.last_seen = now
    if getattr(s, "view_as_id", None):
        from . import viewas

        return viewas.apply(db, s, user, request)
    return user


def revoke_session(db: Session, sid: Optional[str]) -> None:
    if sid and (s := db.get(LoginSession, sid)):
        s.revoked = True


def revoke_other_sessions(db: Session, user: User, keep: str) -> int:
    n = 0
    for s in db.query(LoginSession).filter(LoginSession.user_id == user.id, LoginSession.revoked.is_(False)):
        if s.id != keep:
            s.revoked = True
            n += 1
    return n
