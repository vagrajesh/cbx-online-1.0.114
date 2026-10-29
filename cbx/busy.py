"""1.0.103 (CBX-Connect 2.10.8's cbx/busy.py; Edwin, 19 Oct: "I want to be able to see if a bot is busy with a document,
and make sure these work."): which bot is on which document, right now.

When a bot starts work on a document it marks that document's row: busy_bot (its name), busy_step (reading, checking,
matching, …) and busy_since. The mark is cleared when the step ends — in a `finally`, so a crash clears it too. While a
step runs, one heartbeat thread writes busy_beat every BEAT_EVERY seconds (a model call can take minutes); never a write
per token, one small commit per change, and never on the caller's transaction (it holds no write lock across a model call).

A mark with no beat for STUCK_AFTER shows amber on the page ("● Ivy — no sign of life for 4 min"); the wake routines put
that document back in its queue (once: the mark is taken by a conditional update, so two rounds never both do it), with a
history line and an audit row, and clear it. Nothing can be busy before the workers start: the routines' start clears
every mark (cbx/jobs.py start_scheduler, before the first job or routine runs).

The rows that carry a mark ("kinds"):
    ap_capture  ApCapture      a capture stage: Uma, Cara / Dex / Eli, Seth, Mia, Pip, Tess
    ap_item     ApItem         the AP team's reading of a batch (the rules, Paula, AXL)
    ticket      SupportTicket  the desk's reading of a ticket: Iris (pictures), Mo / Lu / Kit

The columns are added to an older install by cbx/migrate.py (add_missing_columns: nullable, default ''); a NULL busy_bot is
"not busy" like ''.
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterator, Optional

from sqlalchemy import and_, or_, update
from sqlalchemy.orm import Session

from . import jobs
from .db import session_scope, utcnow

logger = logging.getLogger("cbx.busy")

BEAT_EVERY = 20.0  # seconds between two beats of one long step (at most)
STUCK_AFTER = timedelta(minutes=3)  # no beat for this long: "no sign of life"
MAX_BEAT = timedelta(minutes=30)  # a step that runs longer stops being beaten: a hung call then shows as stuck
STEPS = ("sorting", "reading", "checking", "matching", "sap", "testing", "pictures")
KINDS = ("ap_capture", "ap_item", "ticket")
TEAM = {"ap_capture": "ap", "ap_item": "ap", "ticket": "support"}


def model(kind: str):
    from .models.ap import ApCapture, ApItem
    from .models.support import SupportTicket

    return {"ap_capture": ApCapture, "ap_item": ApItem, "ticket": SupportTicket}[kind]


# ---- the marks ---------------------------------------------------------------------------------------------------------
def _vals(M, **vals) -> dict:
    """What a mark writes — and updated_at kept as it was (a bot's mark is not a change people made to the document)."""
    return {**vals, "updated_at": M.updated_at}


_CLEARED = {"busy_bot": "", "busy_step": "", "busy_since": None, "busy_beat": None}
_live: dict[tuple[str, int], tuple[datetime, float]] = {}  # (kind, id) → (busy_since, started, monotonic): what the heartbeat beats
_lock = threading.Lock()
_thread: Optional[threading.Thread] = None
_wake = threading.Event()


def mark(kind: str, rid: int, bot: str, step: str, db: Optional[Session] = None) -> Optional[datetime]:
    """Mark the row busy (its own short transaction, or the caller's `db`, committed at once: only for callers that commit
    per document anyway). Returns the mark's busy_since (its token), or None when it could not be written."""
    M = model(kind)
    now = utcnow()
    vals = {"busy_bot": (bot or "")[:32], "busy_step": (step or "")[:32], "busy_since": now, "busy_beat": now}
    try:
        if db is not None:
            db.execute(update(M).where(M.id == rid).values(**_vals(M, **vals)).execution_options(synchronize_session=False))
            db.commit()
        else:
            with session_scope() as s:
                s.execute(update(M).where(M.id == rid).values(**_vals(M, **vals)).execution_options(synchronize_session=False))
    except Exception:  # noqa: BLE001 — the mark is bookkeeping: the work goes on without it
        logger.debug("busy mark %s:%s", kind, rid, exc_info=True)
        return None
    with _lock:
        _live[(kind, int(rid))] = (now, time.monotonic())
    _start_heartbeat()
    return now


def current(kind: str, rid: int) -> Optional[datetime]:
    """The mark this process set on the row and has not cleared yet (its busy_since), or None."""
    with _lock:
        cur = _live.get((kind, int(rid)))
    return cur[0] if cur else None


def clear(kind: str, rid: int, since: Optional[datetime] = None, db: Optional[Session] = None) -> bool:
    """Clear the mark — only the one this step set (`since`), never a newer one another worker set meanwhile."""
    M = model(kind)
    with _lock:
        cur = _live.get((kind, int(rid)))
        if cur is not None and (since is None or cur[0] == since):
            _live.pop((kind, int(rid)), None)
    cond = [M.id == rid] + ([M.busy_since == since] if since is not None else [])
    stmt = update(M).where(*cond).values(**_vals(M, **_CLEARED)).execution_options(synchronize_session=False)
    try:
        if db is not None:
            n = db.execute(stmt).rowcount
            db.commit()
        else:
            with session_scope() as s:
                n = s.execute(stmt).rowcount
    except Exception:  # noqa: BLE001
        logger.debug("busy clear %s:%s", kind, rid, exc_info=True)
        return False
    return bool(n)


@contextmanager
def working(kind: str, rid: int, bot: str, step: str, db: Optional[Session] = None) -> Iterator[Optional[datetime]]:
    """The row is busy for the length of the block. With `db`, the mark and its clearing are committed on the caller's
    session (for callers that commit per document anyway); when the block fails, the caller's work is rolled back first
    and the mark is cleared on a session of its own."""
    since = mark(kind, rid, bot, step, db=db)
    ok = True
    try:
        yield since
    except BaseException:
        ok = False
        raise
    finally:
        if since is not None:
            if db is not None and not ok:
                try:
                    db.rollback()
                except Exception:  # noqa: BLE001
                    logger.debug("rollback before clearing the busy mark", exc_info=True)
            clear(kind, rid, since, db=db if ok else None)


def relabel(kind: str, rid: int, since: Optional[datetime], bot: str, step: str, db: Optional[Session] = None) -> bool:
    """The same mark, another bot or step (Paula hands the file to AXL; Iris hands the ticket to a reader): its time
    counts on, and its clearing still matches. With `db` it commits the caller's session (no lock across the next call)."""
    if since is None:
        return False
    M = model(kind)
    stmt = update(M).where(M.id == rid, M.busy_since == since).values(**_vals(M, busy_bot=(bot or "")[:32], busy_step=(step or "")[:32], busy_beat=utcnow()))
    try:
        if db is not None:
            n = db.execute(stmt.execution_options(synchronize_session=False)).rowcount
            db.commit()
        else:
            with session_scope() as s:
                n = s.execute(stmt.execution_options(synchronize_session=False)).rowcount
    except Exception:  # noqa: BLE001
        logger.debug("busy relabel %s:%s", kind, rid, exc_info=True)
        return False
    return bool(n)


@contextmanager
def working_many(kind: str, ids: list, bot: str, step: str, db: Optional[Session] = None) -> Iterator[None]:
    """Several rows busy with one bot at once (Tess tests a batch's documents together): one mark, one clear."""
    ids = [int(i) for i in ids or []]
    if not ids:
        yield
        return
    M = model(kind)
    now = utcnow()
    vals = _vals(M, busy_bot=(bot or "")[:32], busy_step=(step or "")[:32], busy_since=now, busy_beat=now)
    marked = False
    try:
        if db is not None:
            db.execute(update(M).where(M.id.in_(ids)).values(**vals).execution_options(synchronize_session=False))
            db.commit()
        else:
            with session_scope() as s:
                s.execute(update(M).where(M.id.in_(ids)).values(**vals).execution_options(synchronize_session=False))
        marked = True
    except Exception:  # noqa: BLE001
        logger.debug("busy mark_many %s", kind, exc_info=True)
    if marked:
        with _lock:
            for i in ids:
                _live[(kind, i)] = (now, time.monotonic())
        _start_heartbeat()
    ok = True
    try:
        yield
    except BaseException:
        ok = False
        raise
    finally:
        if marked:
            if db is not None and not ok:
                try:
                    db.rollback()
                except Exception:  # noqa: BLE001
                    logger.debug("rollback before clearing the busy marks", exc_info=True)
            with _lock:
                for i in ids:
                    if _live.get((kind, i), (None,))[0] == now:
                        _live.pop((kind, i), None)
            stmt = update(M).where(M.id.in_(ids), M.busy_since == now).values(**_vals(M, **_CLEARED)).execution_options(synchronize_session=False)
            try:
                if db is not None and ok:
                    db.execute(stmt)
                    db.commit()
                else:
                    with session_scope() as s:
                        s.execute(stmt)
            except Exception:  # noqa: BLE001
                logger.debug("busy clear_many %s", kind, exc_info=True)


def clear_all() -> int:
    """At the start (before the routines and the pool's first job): nothing can be busy before the workers run."""
    n = 0
    with _lock:
        _live.clear()
    for kind in KINDS:
        M = model(kind)
        try:
            with session_scope() as s:
                n += s.execute(update(M).where(or_(M.busy_bot != "", M.busy_since.isnot(None))).values(
                    **_vals(M, **_CLEARED)).execution_options(synchronize_session=False)).rowcount or 0
        except Exception:  # noqa: BLE001 — a table that isn't there yet (a first start) has nothing to clear
            logger.debug("busy clear_all %s", kind, exc_info=True)
    if n:
        logger.info("cleared %d busy marks left from before the start", n)
    return n


# ---- the heartbeat -------------------------------------------------------------------------------------------------------
def beat_now() -> int:
    """One beat for every step running in this process (younger than MAX_BEAT); a small commit per kind."""
    now_m = time.monotonic()
    with _lock:
        todo = [(k, rid, since) for (k, rid), (since, started) in _live.items() if now_m - started < MAX_BEAT.total_seconds()]
    n = 0
    by_kind: dict[str, list] = {}
    for k, rid, since in todo:
        by_kind.setdefault(k, []).append((rid, since))
    for k, rows in by_kind.items():
        M = model(k)
        try:
            with session_scope() as s:
                now = utcnow()
                for rid, since in rows:
                    n += s.execute(update(M).where(M.id == rid, M.busy_since == since).values(**_vals(M, busy_beat=now)).execution_options(synchronize_session=False)).rowcount or 0
        except Exception:  # noqa: BLE001 — a missed beat is not the step's failure
            logger.debug("busy beat %s", k, exc_info=True)
    return n


def _loop() -> None:
    while True:
        _wake.wait(BEAT_EVERY)
        _wake.clear()
        with _lock:
            idle = not _live
        if idle:
            continue
        beat_now()


def _start_heartbeat() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _thread = threading.Thread(target=_loop, name="cbx-busy-beat", daemon=True)
        _thread.start()


# ---- what the pages show -------------------------------------------------------------------------------------------------
def _aware(t: Optional[datetime]) -> Optional[datetime]:
    return None if t is None else (t if t.tzinfo else t.replace(tzinfo=timezone.utc))


def out(row, now: Optional[datetime] = None) -> Optional[dict]:
    """A row's mark for a page: who, which step, how long (secs, counted on by the page) and whether it is stuck."""
    if row is None or not getattr(row, "busy_bot", ""):
        return None
    now = now or utcnow()
    since, beat = _aware(row.busy_since), _aware(row.busy_beat) or _aware(row.busy_since)
    quiet = int((now - beat).total_seconds()) if beat else 0
    return {"bot": row.busy_bot, "step": row.busy_step or "", "since": since.isoformat(timespec="seconds") if since else "",
            "secs": max(0, int((now - since).total_seconds())) if since else 0, "quiet": max(0, quiet),
            "stuck": bool(beat and now - beat > STUCK_AFTER)}


def busy_rows(db: Session, kind: str, limit: int = 500) -> list:
    M = model(kind)
    return db.query(M).filter(M.busy_bot != "").order_by(M.id).limit(limit).all()


# ---- stuck: back in the queue -------------------------------------------------------------------------------------------
# kind → fn(db, row, bot, step): what "back in the queue" means for that kind (registered by its module); the mark is
# already taken (cleared) when it is called, in the same transaction
HANDLERS: dict[str, Callable[[Session, object, str, str], None]] = {}


def on_stuck(kind: str):
    def deco(fn):
        HANDLERS[kind] = fn
        return fn

    return deco


def stale(db: Session, kind: str) -> list:
    M = model(kind)
    cut = utcnow() - STUCK_AFTER
    return db.query(M).filter(M.busy_bot != "", or_(M.busy_beat < cut, and_(M.busy_beat.is_(None), M.busy_since < cut))).order_by(M.id).limit(200).all()


def requeue_stuck(kinds: tuple = KINDS) -> int:
    """Every stuck mark of these kinds: taken by a conditional update (the same mark, still quiet — so it happens once),
    then its kind's handler puts the document back in its queue. One transaction per document."""
    n = 0
    for kind in kinds:
        fn = HANDLERS.get(kind)
        M = model(kind)
        try:
            with session_scope() as db:
                ids = [(r.id, r.busy_since, r.busy_beat) for r in stale(db, kind)]
        except Exception:  # noqa: BLE001
            logger.debug("busy stale %s", kind, exc_info=True)
            continue
        for rid, since, beat in ids:
            try:
                with session_scope() as db:
                    row = db.get(M, rid)
                    if row is None:
                        continue
                    bot, step = row.busy_bot, row.busy_step
                    cond = [M.id == rid, M.busy_since == since, (M.busy_beat == beat) if beat is not None else M.busy_beat.is_(None)]
                    got = db.execute(update(M).where(*cond).values(**_vals(M, **_CLEARED))
                                     .execution_options(synchronize_session=False)).rowcount
                    if got != 1:
                        continue  # a beat came in, or another round took it
                    with _lock:
                        _live.pop((kind, int(rid)), None)
                    db.expire(row)
                    if fn is not None:
                        fn(db, row, bot, step)
                    n += 1
            except Exception:  # noqa: BLE001 — one document never stops the others
                logger.exception("busy requeue %s:%s", kind, rid)
    return n


@jobs.routine("bot-busy", 60)
def stuck_routine() -> None:
    """Every minute: a bot with no sign of life for STUCK_AFTER loses its document back to the queue."""
    requeue_stuck()





def docs_by_bot(db: Session) -> dict:
    """The Office: each bot's document now, by its Office name ("Cara (AP)", "Mo (Support)") → {bot, step, secs, stuck,
    label, link}; a click opens it."""
    from pathlib import Path

    from .models.ap import ApItem

    now, found = utcnow(), {}
    for kind in KINDS:
        for r in busy_rows(db, kind, limit=200):
            m = out(r, now)
            if kind == "ticket":
                label, link, name = f"T-{r.id:04d} {r.title or ''}".strip(), f"#/support-tickets?t={r.id}", f"{m['bot']} (Support)"
            elif kind == "ap_capture":
                it = db.get(ApItem, r.item_id)
                label, link, name = (Path(it.name).name if it else ""), f"#/ap?capture={r.id}", f"{m['bot']} (AP)"
            else:
                label, link, name = Path(r.name).name, f"#/ap?item={r.id}", f"{m['bot']} (AP)"
            found.setdefault(name, {**m, "label": label[:200], "link": link})
    return found
