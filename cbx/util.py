"""Small cross-platform helpers."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone


def clip(text: str, n: int) -> str:
    """At most `n` characters, cut at a word and ended with "…" when something was left out (1.0.83: no
    title stops mid-word, as "…and the PDF ex" did)."""
    t = " ".join(str(text or "").split())
    if len(t) <= n:
        return t
    cut = t[: max(1, n - 1)]
    if " " in cut[n // 2:]:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:—-–") + "…"


def local_today() -> date:
    """Today's date where the business is (CBX_TIMEZONE, default Africa/Johannesburg) — not UTC,
    which is still 'yesterday' until 02:00 SAST. Falls back to a fixed +02:00 if tzdata is missing
    (Windows has no system zoneinfo unless the tzdata package is installed)."""
    from .config import settings

    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(settings.timezone)
    except Exception:  # noqa: BLE001
        tz = timezone(timedelta(hours=2))
    return datetime.now(tz).date()


def human_date(d: date | datetime | None, month: str = "%B") -> str:
    """'4 September 2026' on every OS (Windows strftime has no %-d)."""
    if d is None:
        return ""
    return f"{d.day} {d.strftime(month)} {d.year}"


async def read_capped(up, cap: int, what: str = "file") -> bytes:
    """Read an upload up to `cap` bytes and refuse the rest — instead of reading everything a client
    chose to send and only then looking at its size. A few oversized uploads at once used to be a
    memory problem; now they are a 413 at the cap. `what` names the thing in the message."""
    from fastapi import HTTPException

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await up.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > cap:
            mb = cap // (1024 * 1024)
            raise HTTPException(413, f"{getattr(up, 'filename', None) or what} is bigger than {mb} MB, the most this door takes.")
        chunks.append(chunk)
    return b"".join(chunks)
