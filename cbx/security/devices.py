"""Signing in from a device we have not seen: email a code, then trust that device for a month.

The decision this implements, and why it is not an IP allowlist: in the 17–18 Sep sign-in logs every
single person appeared from more than one address inside 48 hours. Edwin from 197.221.96.214 and
100.96.120.76. Otsile from a rotating IPv6 mobile address. Even the two strangers used two each. An
allowlist would have generated a stream of approvals for the owner and left people standing at the
door in between — security nobody can live with gets switched off, which leaves you with none.

So the question asked is "is this a browser this person has already proved is theirs?", and it is
asked about once a month rather than once a cell tower.

This switches itself on. `challenge_needed` asks for a code only once a message has actually left
this machine — which one click on Settings -> Email -> Send test proves. Until then, and again if a
send fails later, sign-in proceeds WITHOUT the code and the gap is written to the audit trail and
raised on the owner's home page.

That is deliberate, and it is the whole safety story here. `.env` holding an SMTP host is a
statement of intent: it does not mean the app password is right or that 587 is open. If arming ran
off intent, the first restart after filling those lines in would refuse every person at once —
nobody has a confirmed device yet, so nobody is exempt, the owner included, and the only way back
in would be editing `.env` on the server itself. An unreachable lock on the front door is worse
than the risk it was meant to remove.
Since 1.0.47 the fail-safe is no longer "let them in": with CBX_CODE_FALLBACK=terminal (the default)
a code that cannot be emailed is shown in the CBX window on the server, and to owners and admins in
Users & roles for the people they manage, so the door stays locked but a key can always be handed
over. `let_in` brings the old behaviour back.
"""
from __future__ import annotations

import hashlib
import sys
import threading
import re
import secrets
from datetime import timedelta
from typing import Optional

from sqlalchemy.orm import Session

from .. import mail, state
from ..branding import product_name
from ..config import settings
from ..db import utcnow
from ..models import LoginCode, TrustedDevice, User

DEVICE_COOKIE = "cbx_device"
CODE_TTL_MINUTES = 10
MAX_CODE_ATTEMPTS = 5


def _hash(code: str) -> str:
    """Codes are stored hashed: a database someone can read must not be a list of working codes."""
    return hashlib.sha256(f"{settings.secret_key}:{code}".encode()).hexdigest()


def new_code() -> str:
    return f"{secrets.randbelow(1000000):06d}"


def describe(agent: str) -> str:
    """A label a person recognises in their device list. Best effort — never a fingerprint."""
    a = agent or ""
    browser = next((b for b in ("Edge", "Chrome", "Firefox", "Safari") if b in a and not (b == "Safari" and "Chrome" in a)), "")
    system = next((o for o, pat in (("Windows", r"Windows"), ("macOS", r"Mac OS"), ("Android", r"Android"), ("iPhone", r"iPhone"), ("iPad", r"iPad"), ("Linux", r"Linux")) if re.search(pat, a)), "")
    if browser and system:
        return f"{browser} on {system}"
    return (browser or system or "A browser")[:120]


def trusted(db: Session, user: User, token: Optional[str]) -> Optional[TrustedDevice]:
    """The live trusted-device row for this cookie and this person, or None."""
    if not token:
        return None
    d = db.query(TrustedDevice).filter(TrustedDevice.token == _hash(token), TrustedDevice.user_id == user.id, TrustedDevice.revoked.is_(False)).one_or_none()
    if d is None:
        return None
    if d.expires_at and d.expires_at <= utcnow():
        return None
    return d


def terminal_fallback() -> bool:
    return (settings.code_fallback or "terminal").lower() == "terminal"


def challenge_needed(db: Session, user: User, token: Optional[str]) -> tuple[bool, str]:
    """Does this sign-in need a code? Returns (needed, why) — `why` goes in the audit trail."""
    if not settings.device_verification:
        return False, "device verification is switched off on this server"
    if terminal_fallback():
        # the code always has somewhere to go: the inbox when mail works, the server's window when not
        if trusted(db, user, token) is not None:
            return False, "a device this person has already confirmed"
        return True, "a device we have not seen before"
    ready, why = mail.configured()
    if not ready:
        # The fallback that keeps the company able to work. Loud, not silent.
        return False, f"no second factor sent — {why}"
    if state.mail_proven(db) is None:
        # `.env` having values is a statement of intent, not proof. A wrong app password or a blocked
        # port reads as "configured" — and arming on that would refuse every person on the system at
        # once, because nobody has a confirmed device yet. So this switches itself on only after a
        # message has actually left this machine: Settings -> Email -> Send test does it in one click,
        # and until then sign-in carries on exactly as it did before, saying so in the audit trail.
        return False, ("no second factor sent — email has not been proven to work on this server yet. "
                       "Send a test from Settings → Email; sign-in codes start the moment one arrives.")
    if not mail.valid(user.email):
        return False, f"no second factor sent — {user.email or 'that account'} has no usable email address"
    if trusted(db, user, token) is not None:
        return False, "a device this person has already confirmed"
    return True, "a device we have not seen before"


# ---- the terminal fail-safe ----------------------------------------------------------------------
# Codes that could not be emailed, kept in this process only (never on disk, never in a log file) until
# they are used or run out. The owner or an admin can read the ones for people they manage in Users &
# roles, so a person outside the building can be given theirs over the phone.
_WAITING: dict[int, dict] = {}
_WLOCK = threading.Lock()


def _to_terminal(user: User, code: str, why: str, ip: str, where: str) -> None:
    until = utcnow() + timedelta(minutes=CODE_TTL_MINUTES)
    with _WLOCK:
        _WAITING[user.id] = {"user_id": user.id, "name": user.name, "email": user.email, "code": code, "until": until, "why": why, "ip": ip, "where": where}
    line = "=" * 64
    out = sys.__stdout__ or sys.stdout
    try:
        print(f"\n{line}\n  SIGN-IN CODE (the email could not be sent)\n"
              f"  {user.name} <{user.email}>  ·  {where}{' · ' + ip if ip else ''}\n\n"
              f"      {code[:3]} {code[3:]}\n\n"
              f"  Works once, for {CODE_TTL_MINUTES} minutes. Give it only to that person.\n"
              f"  Why not emailed: {why[:150]}\n{line}\n", file=out, flush=True)
    except Exception:  # noqa: BLE001 — no console (a service): Users & roles still has it
        pass


def waiting(viewer: User) -> list[dict]:
    """Codes waiting to be handed over, for the people this viewer may manage."""
    now = utcnow()
    with _WLOCK:
        for k in [k for k, v in _WAITING.items() if v["until"] <= now]:
            _WAITING.pop(k, None)
        rows = list(_WAITING.values())
    return rows


def forget_waiting(user_id: int) -> None:
    with _WLOCK:
        _WAITING.pop(user_id, None)


SIGNIN = "signin"
USE_EMAIL = "use_email"  # 1.0.113: the code that proves an inbox before email codes become someone's second step


def issue(db: Session, user: User, *, ip: str = "", agent: str = "", purpose: str = SIGNIN) -> tuple[LoginCode, bool, str]:
    """Create a code, email it, and return (row, delivered, detail). When the email cannot go and the
    terminal fail-safe is on, the code is shown in the server's window instead: delivered, with
    detail starting "terminal:"."""
    # Any earlier code for this person is spent the moment a new one is asked for.
    for old in db.query(LoginCode).filter(LoginCode.user_id == user.id, LoginCode.used_at.is_(None)).all():
        old.used_at = utcnow()
    code = new_code()
    row = LoginCode(user_id=user.id, code_hash=_hash(code), ip=ip, agent=(agent or "")[:255], expires_at=utcnow() + timedelta(minutes=CODE_TTL_MINUTES),
                    purpose=purpose)  # 1.0.113: a code works only for what it was made for
    db.add(row)
    db.flush()
    where = describe(agent)
    body = (
        f"Your {product_name()} sign-in code is {code}\n\n"
        f"It works once, for {CODE_TTL_MINUTES} minutes.\n\n"
        f"It was asked for by {where}"
        + (f" at {ip}" if ip else "")
        + ".\n\nIf that was not you, do not enter the code — change your password and tell Edwin.\n"
    )
    ready, why = mail.configured()
    if ready and mail.valid(user.email):
        r = mail.send([user.email], f"{product_name()} sign-in code: {code}", body)
        state.record_send(db, r.ok)
        ok, detail = r.ok, r.detail
    else:
        ok, detail = False, why if not ready else f"{user.email or 'that account'} has no usable email address"
    if ok:
        forget_waiting(user.id)
        return row, True, detail
    if terminal_fallback():
        _to_terminal(user, code, detail or "email is not working", ip, where)
        return row, True, f"terminal: {detail}"
    return row, False, detail


def verify(db: Session, user: User, code: str, *, purpose: str = SIGNIN) -> LoginCode:
    """Check a code. Raises ValueError with a sentence the person can act on."""
    now = utcnow()
    row = (
        db.query(LoginCode)
        .filter(LoginCode.user_id == user.id, LoginCode.used_at.is_(None), LoginCode.purpose == purpose)  # 1.0.113: purpose
        .order_by(LoginCode.id.desc())
        .first()
    )
    if row is None:
        raise ValueError("That code has already been used, or none was sent. Ask for a new one.")
    if row.expires_at <= now:
        raise ValueError(f"That code has expired — they last {CODE_TTL_MINUTES} minutes. Ask for a new one.")
    if row.attempts >= MAX_CODE_ATTEMPTS:
        row.used_at = now
        db.flush()
        raise ValueError("Too many wrong codes. Ask for a new one.")
    if _hash((code or "").strip()) != row.code_hash:
        row.attempts += 1
        db.flush()
        left = MAX_CODE_ATTEMPTS - row.attempts
        raise ValueError(f"That code is not right. {left} {'try' if left == 1 else 'tries'} left." if left > 0 else "Too many wrong codes. Ask for a new one.")
    row.used_at = now
    db.flush()
    forget_waiting(user.id)
    return row


def remember(db: Session, user: User, *, ip: str = "", agent: str = "", days: Optional[int] = None) -> tuple[TrustedDevice, str]:
    """Trust this browser for `days` (default `device_trust_days`). Returns (row, cookie value): the row
    holds only the hash of the value — like the codes, a readable database must not be a list of
    working cookies."""
    raw = secrets.token_urlsafe(32)
    d = TrustedDevice(
        user_id=user.id,
        token=_hash(raw),
        label=describe(agent),
        first_ip=ip,
        last_ip=ip,
        expires_at=utcnow() + timedelta(days=max(1, days if days is not None else settings.device_trust_days)),
    )
    db.add(d)
    db.flush()
    return d, raw


def token_hash(raw: Optional[str]) -> str:
    """The stored form of a device cookie value (for comparisons outside this module)."""
    return _hash(raw or "")


def touch(db: Session, device: TrustedDevice, ip: str) -> None:
    device.last_seen = utcnow()
    if ip:
        device.last_ip = ip
    db.flush()


def revoke_all(db: Session, user: User) -> int:
    """Every device this person has confirmed stops being trusted — used when they change their
    password, alongside the existing session revocation."""
    n = 0
    for d in db.query(TrustedDevice).filter(TrustedDevice.user_id == user.id, TrustedDevice.revoked.is_(False)).all():
        d.revoked = True
        n += 1
    db.flush()
    return n
