"""Per-IP rate limiting for the doors: sign-in and code entry.

Account lockout was never the right defence on its own. It stops one account being guessed at, and
it does nothing about someone walking the address list — which is what the 17 Sep logs looked like:
two addresses that have no account here, tried nine times between them from four different IPs.
Worse, lockout without a limit is itself the weapon: guess five times against every
firstname.surname@ and the whole company is locked out.

A sliding window per IP, in memory. One server process, a few hundred people — a table would cost a
write on every sign-in to defend against something a dictionary handles. It resets on restart, which
is the honest trade: a determined attacker gets a fresh window when the server restarts, and nobody
is ever locked out by a rate limiter that outlived the attack.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

_lock = threading.Lock()
_hits: dict[str, deque] = defaultdict(deque)
_LAST_SWEEP = [0.0]


class TooMany(Exception):
    """Raised with a sentence the person can act on, and the seconds until they may try again."""

    def __init__(self, message: str, retry_after: int):
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after


def _sweep(now: float, window: int) -> None:
    """Drop IPs with nothing recent, so a long-running server does not grow a bucket per visitor."""
    if now - _LAST_SWEEP[0] < 300:
        return
    _LAST_SWEEP[0] = now
    for ip in [k for k, v in _hits.items() if not v or now - v[-1] > window]:
        _hits.pop(ip, None)


def check(key: str, *, limit: int, window: int, what: str = "attempts") -> None:
    """Record one attempt from `key` and raise TooMany if it is over the limit for this window."""
    if not key:
        return  # no address to attribute it to — do not punish everyone behind a missing header
    now = time.monotonic()
    with _lock:
        _sweep(now, window)
        q = _hits[key]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            wait = int(window - (now - q[0])) + 1
            mins = max(1, round(wait / 60))
            raise TooMany(
                f"Too many {what} from this connection. Try again in {mins} minute{'s' if mins != 1 else ''}.",
                wait,
            )
        q.append(now)


def forget(key: str) -> None:
    """Clear an address's history — called when a sign-in succeeds, so a person who fumbled their
    password twice and then got it right is not still carrying those attempts an hour later."""
    if not key:
        return
    with _lock:
        _hits.pop(key, None)


def peek(key: str, *, window: int) -> int:
    """How many attempts this address has on record. For the audit line, not for a decision."""
    now = time.monotonic()
    with _lock:
        q = _hits.get(key) or deque()
        return sum(1 for t in q if now - t <= window)


def reset_all() -> None:
    """Tests only."""
    with _lock:
        _hits.clear()
        _LAST_SWEEP[0] = 0.0
