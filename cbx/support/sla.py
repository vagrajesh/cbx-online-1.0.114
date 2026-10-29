# 1.0.98: from CBX-Connect 2.10.3 cbx/support/sla.py (state() and of() fitted to the old desk's statuses)
"""The desk's clock (1.0.98). Times are kept in UTC and shown and scheduled in Johannesburg time (CBX_TIMEZONE).

    assigned  → the lead's ticket mail is written (assigned_at)
    accepted  → the lead taps Accept (accepted_at): the SLA starts here
    done      → Mark as done / closed (closed_at)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from ..db import utcnow


def tz():
    from ..config import settings

    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(settings.timezone)
    except Exception:  # noqa: BLE001 — no tzdata (Windows without the package): Johannesburg has no summer time
        return timezone(timedelta(hours=2))


def aware(d: Optional[datetime]) -> Optional[datetime]:
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def local(d: Optional[datetime]) -> Optional[datetime]:
    d = aware(d)
    return d.astimezone(tz()) if d else None


def shown(d: Optional[datetime]) -> str:
    """'14 Oct 09:12' in Johannesburg time."""
    x = local(d)
    return f"{x.day} {x:%b %H:%M}" if x else ""


def span(seconds: float) -> str:
    """'45 min', '7 h', '2 d 4 h' — how long, in a few characters."""
    s = max(0, int(seconds))
    if s < 3600:
        return f"{max(1, s // 60)} min"
    h = s // 3600
    if h < 48:
        return f"{h} h"
    d, rest = divmod(h, 24)
    return f"{d} d" + (f" {rest} h" if rest else "")


def hours_since(d: Optional[datetime], now: Optional[datetime] = None) -> float:
    d = aware(d)
    if d is None:
        return 0.0
    return (aware(now or utcnow()) - d).total_seconds() / 3600


def state(t) -> str:
    """waiting (to be accepted) · accepted · done · '' (not with a lead yet). The old desk: assigned and discussing are
    with the lead; closed is done when a lead had it."""
    if t.status == "closed":
        return "done" if t.assigned_to_id else ""
    if t.status not in ("assigned", "discussing") or not t.assigned_to_id:
        return ""
    return "accepted" if t.accepted_at else "waiting"


def of(t, now: Optional[datetime] = None) -> dict:
    """What the ticket page and the staff list show: the three times and how long each step took."""
    now = aware(now or utcnow())
    st = state(t)
    out = {"state": st, "assigned": shown(t.assigned_at), "accepted": shown(t.accepted_at), "done": shown(t.closed_at) if st == "done" else "",
           "accepted_by": t.accepted_by or "", "waiting_for": "", "to_accept": "", "open_for": ""}
    if st == "waiting" and t.assigned_at:
        out["waiting_for"] = span((now - aware(t.assigned_at)).total_seconds())
    if t.assigned_at and t.accepted_at:
        out["to_accept"] = span((aware(t.accepted_at) - aware(t.assigned_at)).total_seconds())
    if st == "accepted":
        out["open_for"] = span((now - aware(t.accepted_at)).total_seconds())
    if st == "done" and t.accepted_at and t.closed_at:
        out["open_for"] = span((aware(t.closed_at) - aware(t.accepted_at)).total_seconds())
    return out
