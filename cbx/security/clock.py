"""1.0.113: is this server's clock right? Only ever told to the owner and admins — never used to decide a sign-in.

Authenticator codes are 30-second steps worked out from the clock, so a server clock that is off makes
a phone's code look early or late. The CBX PC's Windows clock ran about 60 s fast (Oct 2026): codes
then passed only in the first seconds after they appeared, and people gave up on the app. Sign-in now
copes with that by itself (a learned difference per person, cbx/security/totp.match_step); this module
only notices it so someone can fix the clock.

    now()                     the server's time — the one place sign-in codes read it (a test moves it here)
    note(request, user_id)    a signed-in person's browser said what time it is (header X-Client-Time, ms since 1970)
    offset(db)                seconds this server is ahead (+) or behind (-), or None when nothing agrees on it
    sentence(seconds)         the one line the owner sees on Home
    check(db)                 that line or "", and the `server_clock_off` audit line (at most once a day)

The samples live in this process only: one per person (the latest), from signed-in sessions that are past
the second step (/api/auth/me, and right after a passed step) — never from the sign-in form, which anyone can
post to. At most 20 people, none older than 6 hours. The server counts as off when the median of the people's
samples is past 20 s AND at least two different people each say so (one person's PC with a wrong clock of its
own is not enough) — or when two people's learned authenticator differences agree that it is a minute or more out.
"""
from __future__ import annotations

import statistics
import threading
import time
from collections import deque
from typing import Optional

LIMIT_SECONDS = 20      # past this the owner is told
KEEP = 20               # people whose latest sample is kept
MAX_AGE = 6 * 3600      # seconds a sample counts
STATE_KEY = "clock.off_audited_on"

_SAMPLES: deque = deque(maxlen=KEEP)  # (person's id, server - browser seconds, when)
_LOCK = threading.Lock()


def now() -> float:
    return time.time()


def reset() -> None:
    with _LOCK:
        _SAMPLES.clear()


def add_sample(key, diff: float, at: Optional[float] = None) -> None:
    """One sample per person: a new one replaces their last."""
    at = now() if at is None else at
    with _LOCK:
        for old in [x for x in _SAMPLES if x[0] == key]:
            _SAMPLES.remove(old)
        _SAMPLES.append((key, float(diff), at))


def note(request, user_id: Optional[int], db=None) -> None:
    """Record a signed-in person's browser time from `X-Client-Time` (sent by web/api.js on /api/auth/* calls). The
    caller has checked the session is past the second step. A missing or silly value (more than a day out) is
    ignored. Never raises."""
    try:
        if not user_id:
            return
        raw = (request.headers.get("x-client-time") or "").strip()
        if not raw or not raw.isdigit() or len(raw) > 16:
            return
        diff = now() - int(raw) / 1000.0
        if abs(diff) > 86400:
            return
        add_sample(int(user_id), diff)
        if db is not None:
            check(db)
    except Exception:  # noqa: BLE001 — a clock hint must never break a sign-in
        return


def _from_people() -> tuple[Optional[float], bool]:
    """(seconds off, enough samples to judge)."""
    cut = now() - MAX_AGE
    with _LOCK:
        rows = [s for s in _SAMPLES if s[2] >= cut]
    if len(rows) < 2:  # one per person: two different people at least
        return None, False
    med = statistics.median(d for _, d, _ in rows)
    if abs(med) <= LIMIT_SECONDS:
        return None, True
    agree = sum(1 for _, d, _ in rows if abs(d) > LIMIT_SECONDS and (d > 0) == (med > 0))
    return (med if agree >= 2 else None), True


def _from_drift(db) -> Optional[float]:
    """Two people whose phones both say the server is the same way out, a minute or more (a learned
    difference of one step is ordinary typing time, so it does not count)."""
    from ..models import User

    drifts = [d for (d,) in db.query(User.mfa_drift).filter(User.mfa_method == "totp", User.is_active.is_(True)).all() if d and abs(d) >= 2]
    for sign in (-1, 1):
        same = [d for d in drifts if (d > 0) == (sign > 0)]
        if len(same) >= 2:
            # drift = phone's step - server's step: negative means the server is ahead (fast)
            return -statistics.median(same) * 30.0
    return None


def offset(db) -> Optional[float]:
    secs, judged = _from_people()
    if judged:
        return secs  # people's browsers are the fresher witness: when they say the clock is fine, it is
    try:
        return _from_drift(db)
    except Exception:  # noqa: BLE001 — an old database without the column
        return None


def size_words(seconds: float) -> str:
    s = abs(seconds)
    if s < 50:
        return f"about {max(5, int(round(s / 5.0)) * 5)} seconds"
    if s < 90:
        return "about 1 minute"
    if s < 3600:
        return f"about {int(round(s / 60.0))} minutes"
    h = int(round(s / 3600.0))
    return f"about {h} hour{'s' if h != 1 else ''}"


def sentence(seconds: float) -> str:
    way = "fast" if seconds > 0 else "slow"
    return (f"This server's clock is {size_words(seconds)} {way}. Authenticator sign-in codes depend on it. "
            "On the server: Settings → Time & language → Date & time → Sync now (keep 'Set time automatically' on).")


def check(db) -> str:
    """The owner's line, or "" when the clock looks right. Writes `server_clock_off` at most once a day."""
    secs = offset(db)
    if secs is None:
        return ""
    try:
        from .. import state
        from ..audit import log
        from ..db import utcnow

        day = utcnow().date().isoformat()
        if state.get(db, STATE_KEY) != day:
            # 1.0.113 (review): its own savepoint — two requests racing to write today's line cannot spoil the caller's session
            with db.begin_nested():
                state.set(db, STATE_KEY, day)
                log(db, "server_clock_off", actor_label="system", detail=f"{secs:+.0f} s ({'fast' if secs > 0 else 'slow'})", outcome="blocked", seconds=round(secs))
    except Exception:  # noqa: BLE001 — the line still shows
        pass
    return sentence(secs)
