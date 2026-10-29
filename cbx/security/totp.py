"""Two-step verification with an authenticator app (TOTP, RFC 6238) and recovery codes.

    new_secret()                    a fresh base32 secret for one person
    verify(secret, code, window=1)  is this six-digit code right for now (± one 30-second step)?
    match_step(secret, code, ...)   1.0.113: the 30-second step a code belongs to, searched around a learned clock difference
    server_step()                   1.0.113: the server's step now (cbx/security/clock.now — a test can move it)
    uri(secret, email, issuer)      the otpauth:// address an app reads from the QR code
    qr_svg(uri)                     that address as a small SVG QR code
    new_recovery_codes()            ten one-time codes, and their hashes to store
    use_recovery_code(hashes, code) the hashes without the one just used, or None if it was not one

The secret is stored sealed (cbx/security/secretbox.py); the recovery codes are stored hashed. Any
authenticator app works — Microsoft Authenticator, Google Authenticator, Authy, a password manager —
and a person outside the company needs nothing from IT to use one, which is the point.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from typing import Iterable, Optional
from urllib.parse import quote

STEP_SECONDS = 30
DIGITS = 6
RECOVERY_COUNT = 10


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _code_at(secret: str, counter: int) -> str:
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    n = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(n % (10 ** DIGITS)).zfill(DIGITS)


def code_now(secret: str, at: Optional[float] = None) -> str:
    return _code_at(secret, int((at if at is not None else time.time()) // STEP_SECONDS))


def _digits(code: str) -> str:
    return "".join(ch for ch in str(code or "") if ch.isdigit())


def _same(a: str, b: str) -> bool:
    # 1.0.113: bytes, so a non-ASCII digit (isdigit() lets "١" or "²" through) is a wrong code, not a crash
    return hmac.compare_digest(a.encode(), b.encode("utf-8", "surrogatepass"))


def verify(secret: str, code: str, *, window: int = 1, at: Optional[float] = None) -> bool:
    """True when `code` is the code for now, or for a step either side (clocks drift)."""
    digits = _digits(code)
    if len(digits) != DIGITS or not secret:
        return False
    counter = int((at if at is not None else time.time()) // STEP_SECONDS)
    return any(_same(_code_at(secret, counter + d), digits) for d in range(-window, window + 1))


# ---- 1.0.113: codes against a server clock that is not right -------------------------------------
# The CBX PC's Windows clock ran about 60 s fast. With ±1 step a phone's code then passed only in the
# first seconds after it appeared. So each person carries a learned difference (`users.mfa_drift`, in
# steps: the step their code matched minus the server's step) and a code is looked for ±2 steps around
# it. Every success re-learns it, so the window follows a clock that keeps wandering.
SIGN_IN_WINDOW = 2     # steps either side of the learned difference at sign-in, recovery codes, remove
SETUP_WINDOW = 10      # steps either side of the server's step while setting up (±5 minutes): finds the difference
MAX_DRIFT = 10         # a learned difference is never more than this many steps


def server_step() -> int:
    from . import clock

    return int(clock.now() // STEP_SECONDS)


def clamp_drift(d: int) -> int:
    return max(-MAX_DRIFT, min(MAX_DRIFT, int(d or 0)))


def match_step(secret: str, code: str, *, window: int = SIGN_IN_WINDOW, drift: int = 0, at: Optional[float] = None) -> Optional[int]:
    """The step `code` belongs to, searched `window` steps either side of (server step + drift) — the
    nearest first — or None when it matches none. The caller refuses a step it has already seen."""
    digits = _digits(code)
    if len(digits) != DIGITS or not secret:
        return None
    centre = (int(at // STEP_SECONDS) if at is not None else server_step()) + clamp_drift(drift)
    for d in sorted(range(-window, window + 1), key=lambda k: (abs(k), k)):
        if _same(_code_at(secret, centre + d), digits):
            return centre + d
    return None


def uri(secret: str, email: str, issuer: str = "CBX-Connect") -> str:
    label = quote(f"{issuer}:{email}", safe="")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer, safe='')}&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}"


def qr_svg(text: str) -> str:
    """The QR code as SVG markup (no PIL needed). Empty string if the qrcode library is missing."""
    try:
        import io

        import qrcode
        import qrcode.image.svg as svg
    except ImportError:
        return ""
    img = qrcode.make(text, image_factory=svg.SvgPathImage, box_size=6, border=2)
    buf = io.BytesIO()
    img.save(buf)
    s = buf.getvalue().decode("utf-8")
    return s[s.index("<svg"):] if "<svg" in s else s


def _norm(code: str) -> str:
    return "".join(ch for ch in str(code or "").lower() if ch.isalnum())


def hash_code(code: str) -> str:
    return hashlib.sha256(("recovery|" + _norm(code)).encode()).hexdigest()


def new_recovery_codes(n: int = RECOVERY_COUNT) -> tuple[list[str], list[str]]:
    """(codes to show once, hashes to store). A code looks like `k7pm-3xq2`."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o/1/l/i: nothing to misread from paper
    codes = ["".join(secrets.choice(alphabet) for _ in range(4)) + "-" + "".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(n)]
    return codes, [hash_code(c) for c in codes]


def looks_like_recovery(code: str) -> bool:
    return len(_norm(code)) == 8 and not _norm(code).isdigit()


def use_recovery_code(hashes: Iterable[str], code: str) -> Optional[list[str]]:
    """The remaining hashes once `code` has been spent — or None when it is not one of them."""
    h = hash_code(code)
    hs = list(hashes or [])
    for i, x in enumerate(hs):
        if hmac.compare_digest(x, h):
            return hs[:i] + hs[i + 1:]
    return None
