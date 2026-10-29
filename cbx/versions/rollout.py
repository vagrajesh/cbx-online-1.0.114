"""The update ceremony: a promotion that gives people time to save before the server switches.

    announce(db, v, by, minutes, force)  → write the marker, start the timer; nothing on disk changes yet
    cancel(db, by)                        → remove the marker, stop the timer
    pending()                             → what is announced, with the seconds left (or None)
    resume_on_boot()                      → after a restart: forget a marker we have arrived at, or re-arm it

While a version is announced the live app is untouched: the running code, its files and the browser
scripts all stay as they are. Every open page polls /api/version, shows the countdown and, when the
timer runs out, saves what the person was typing (web/carry.js) and waits for the new version to come
up before it refreshes itself. At the switch the ordinary promote() runs: backup, copy, pip, restart.

The marker is a file in the data dir, not a database row, so a server that restarts for another reason
in the middle of the wait still knows what was promised — and a switch that already happened (the
running VERSION is the announced tag) is recognised and the marker dropped.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from .. import VERSION
from ..audit import log
from ..config import settings
from ..db import session_scope, utcnow
from ..models import AppVersion, User
from . import injector
from .injector import VersionError

log_ = logging.getLogger("cbx.versions.rollout")

DEFAULT_MINUTES = 10
MAX_MINUTES = 240
_timer: Optional[threading.Timer] = None
_lock = threading.Lock()


def marker_path():
    return settings.data_dir / "PENDING_UPDATE.json"


def _read() -> Optional[dict]:
    p = marker_path()
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and d.get("tag") and d.get("switch_at") else None
    except (OSError, ValueError):
        return None


def _write(d: dict) -> None:
    marker_path().parent.mkdir(parents=True, exist_ok=True)
    marker_path().write_text(json.dumps(d, indent=1), encoding="utf-8")


def _clear() -> None:
    try:
        marker_path().unlink()
    except FileNotFoundError:
        pass
    except OSError as e:  # noqa: BLE001 — a marker that cannot be removed is reported, not fatal
        log_.warning("could not remove the pending-update marker: %s", e)


def _parse(s: str) -> datetime:
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _now() -> datetime:
    n = utcnow()
    return n if n.tzinfo else n.replace(tzinfo=timezone.utc)


def pending() -> Optional[dict]:
    """The announced update as the browsers see it: tag, when it switches, how many seconds are left."""
    d = _read()
    if not d:
        return None
    switch_at = _parse(d["switch_at"])
    left = int((switch_at - _now()).total_seconds())
    return {"tag": d["tag"], "switch_at": switch_at.isoformat(), "seconds_left": max(0, left), "minutes": d.get("minutes", DEFAULT_MINUTES),
            "by": d.get("by_email", ""), "announced_at": d.get("announced_at", ""), "switching": left <= 0}


def _arm(seconds: float) -> None:
    global _timer
    with _lock:
        if _timer is not None:
            _timer.cancel()
        _timer = threading.Timer(max(0.5, seconds), _fire)
        _timer.daemon = True
        _timer.name = "cbx-rollout"
        _timer.start()


def _disarm() -> None:
    global _timer
    with _lock:
        if _timer is not None:
            _timer.cancel()
            _timer = None


def announce(db: Session, v: AppVersion, by: User, *, minutes: int = DEFAULT_MINUTES, force: bool = False) -> dict:
    """Promise the switch for `minutes` from now. Same rules as promote — only a tested version, only a
    person — checked now so a refusal comes back to the owner at once, not at the end of the timer."""
    if by.is_axl:
        raise VersionError("C-EXA can't promote a version — a person has to.")
    if not (v.status == "failed" and force) and v.status not in injector.PROMOTABLE:
        raise VersionError(f"Version {v.tag} is {v.status}; only a tested (sandbox) version can be promoted.")
    if not Path(v.package_path or "").exists():
        raise VersionError("The unpacked package is missing on disk. Upload it again.")
    cur = _read()
    if cur:
        raise VersionError(f"Version {cur['tag']} is already announced for {cur['switch_at'][11:16]} UTC. Cancel that first if you want a different one.")
    if injector.restart_pending():
        raise VersionError("The server is about to restart. Sign in again in a minute and try then.")
    minutes = max(1, min(int(minutes), MAX_MINUTES))
    now = _now()
    d = {"tag": v.tag, "version_id": v.id, "by_id": by.id, "by_email": by.email, "force": bool(force),
         "announced_at": now.isoformat(), "switch_at": (now + timedelta(minutes=minutes)).isoformat(), "minutes": minutes}
    _write(d)
    log(db, "version_announced", actor=by, target=v.tag, detail=f"switches in {minutes} min (at {d['switch_at'][11:16]} UTC); people were told to save" + ("; WITH FAILING TESTS" if force and v.status == "failed" else ""))
    _arm(minutes * 60)
    return pending() or d


def cancel(db: Session, by: User) -> dict:
    d = _read()
    _disarm()
    _clear()
    if d:
        log(db, "version_announce_cancelled", actor=by, target=d.get("tag", ""), detail="the announced update was called off before the switch; nothing changed")
    return {"ok": True, "was": d.get("tag") if d else None}


def _fire() -> None:
    """The timer ran out: do the promotion the owner asked for. Runs on its own thread with its own session."""
    d = _read()
    if not d:
        return
    try:
        with session_scope() as db:
            v = db.get(AppVersion, int(d.get("version_id") or 0)) or db.query(AppVersion).filter(AppVersion.tag == d["tag"]).one_or_none()
            by = db.get(User, int(d.get("by_id") or 0))
            if v is None or by is None:
                log(db, "version_switch_failed", actor_label="server", target=d.get("tag", ""), detail="the announced version or the person who announced it no longer exists; nothing changed", outcome="failed")
                _clear()
                return
            try:
                injector.promote(db, v, by, force=bool(d.get("force")))
                log(db, "version_switched", actor=by, target=v.tag, detail=f"the announced switch ran after {d.get('minutes', DEFAULT_MINUTES)} min; the server restarts now")
            except VersionError as e:
                log(db, "version_switch_failed", actor=by, target=v.tag, detail=str(e), outcome="failed")
    except Exception:  # noqa: BLE001 — the thread must not die silently
        log_.exception("announced switch failed")
    finally:
        # promote() has requested the restart; the new process sees VERSION == tag and forgets the marker.
        # A failed switch clears it too, so the browsers stop counting down and the owner sees the audit row.
        if VERSION == d.get("tag") or not injector.restart_pending():
            _clear()


def resume_on_boot() -> Optional[str]:
    """Called from the app's lifespan. Returns a one-line note for the console, or None."""
    d = _read()
    if not d:
        return None
    if d.get("tag") == VERSION:
        _clear()
        return f"the announced update to {VERSION} has arrived"
    left = (_parse(d["switch_at"]) - _now()).total_seconds()
    # a restart in the middle of the wait: keep the promise, but give the browsers a minute to notice
    _arm(max(60.0, left))
    return f"{d['tag']} is announced — switching in {int(max(60.0, left) // 60)} min"


def stop() -> None:
    _disarm()
