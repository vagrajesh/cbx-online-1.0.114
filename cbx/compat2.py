"""1.0.98: the one shim between code ported from CBX-Connect 2.10.3 (the AP capture line, the Support desk's 2.10 features)
and the old CBX's framework. Ported modules call these instead of 2.x's job registry, agents office and JSON app state, so
their own code stays close to the new app's line for line.

    enqueue / handler / is_inline / routine / progress   2.x's job registry, on the old thread pool (cbx/jobs.py)
    working(db, name, task, ref)                          a bot busy: an AgentWork row the Office shows ("Working on: …")
    status_of(names) / say / notes                        the AP and Support pages' own live team views
    state_get / state_set                                 JSON over the old app_state text column
    audit(db, action, **kw)                               the old audit.log
    xlsafe                                                cbx/xlsafe.py (text never becomes a live formula)

Bots appear in the Office as "<Name> (AP)" / "<Name> (Support)" (old CBX already has a Sol, an Iris, a Tess and a Kit):
the first time a bot works, it gets a desk (a TeamMember) on the AP team, or on a Support team made for the desk's bots.
Their role "bot" has no hands (cbx/cexa/loop.py gives an unknown role only the look-tools), and work anyone else hands
them is closed at once by the tidy routine below — they work only inside their own tool.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterator, Optional

from . import jobs as _jobs
from . import xlsafe  # noqa: F401 — re-exported: compat2.xlsafe
from .db import SessionLocal, after_commit, session_scope, utcnow

logger = logging.getLogger("cbx.compat2")

# ---- jobs ----------------------------------------------------------------------------------------------------------------
HANDLERS: dict[str, Callable] = {}
_LOCK = threading.Lock()
_QUEUED: dict[str, float] = {}       # ref → when it was queued (not started yet)
JOBS: dict[str, dict] = {}           # ref → {kind, status queued|running|done|failed, queued, started, finished}
STALE_QUEUED = 30 * 60               # a queued ref whose request never committed (rolled back) stops blocking after this


class Job:
    """What enqueue gives back (2.x gives its Job row); `dropped` when the same ref was already waiting or running (with
    `again` when it was running: it runs once more when it finishes)."""

    id = None

    def __init__(self, ref: str = "", kind: str = "", dropped: bool = False, again: bool = False):
        self.ref, self.kind, self.dropped, self.again = ref, kind, dropped, again


MOST: dict[str, int] = {}            # kind → how many of its jobs may be on the pool at once (0 = no limit)
_LANE: dict[str, int] = {}           # kind → its jobs on the pool now (submitted or running)
_WAITING: dict[str, list] = {}       # kind → jobs held back by MOST, started as one of the kind finishes


def handler(kind: str, most: int = 0):
    """Register fn(job_id, payload) for a job kind (2.x's jobs.handler). 1.0.98: `most` caps how many jobs of this kind
    are on the old pool (4 threads) at once, so one kind never takes every thread from the other tools' jobs."""

    def deco(fn):
        HANDLERS[kind] = fn
        if most:
            MOST[kind] = most
        return fn

    return deco


def is_inline() -> bool:
    return bool(_jobs._inline)


def routine(name: str, seconds: int):
    return _jobs.routine(name, seconds)


def progress(job_id: Any = None, **kw) -> None:  # the old CBX has no job rows; the pages show their own state
    return None


def queued(ref: str) -> bool:
    """True when a job with this ref is waiting to start."""
    with _LOCK:
        t = _QUEUED.get(ref)
        return bool(t) and time.time() - t < STALE_QUEUED


def running(ref: str) -> bool:
    with _LOCK:
        return JOBS.get(ref, {}).get("status") == "running"


def enqueue(db, *, tool: str, kind: str, title: str = "", ref: str = "", payload: Optional[dict] = None, total: int = 1, user=None,
            priority: int = 5, watcher: str = "") -> Job:
    """Run HANDLERS[kind](0, payload) on the old pool once `db` has committed (the rows it reads must be there).

    Dedupe by ref (1.0.98 review): while a job with the same ref is waiting to start OR running, a second one is dropped —
    never two at once (the capture wake beside a running reader took every thread of the pool). One asked for while it
    runs is not lost: once the asking request has committed, the running job is marked to run once more when it finishes
    (so a reader re-queuing its own wake, or new work arriving as it finishes its last invoice, is picked up)."""
    if ref:
        try:
            db.connection()  # the session's transaction is begun, so a rollback of it is seen (_forget_refs)
        except Exception:  # noqa: BLE001
            pass
        with _LOCK:
            t = _QUEUED.get(ref)
            if t and time.time() - t < STALE_QUEUED:
                return Job(ref, kind, dropped=True)
            if JOBS.get(ref, {}).get("status") == "running":
                after_commit(db, _again, tool, kind, title, ref, dict(payload or {}))
                return Job(ref, kind, dropped=True, again=True)
            _QUEUED[ref] = time.time()
            db.info.setdefault("compat2_refs", []).append(ref)
            if len(JOBS) > 2000:  # what finished long ago is forgotten (the dict lives as long as the process)
                for r in [r for r, j in JOBS.items() if j.get("status") in ("done", "failed", "dropped")][:1000]:
                    JOBS.pop(r, None)
            JOBS[ref] = {"kind": kind, "title": title[:200], "status": "queued", "queued": utcnow().isoformat(timespec="seconds"), "started": None, "finished": None}
    after_commit(db, _start, _make_run(tool, kind, ref, payload))
    return Job(ref, kind)


def _make_run(tool: str, kind: str, ref: str, payload: Optional[dict]):
    def run():
        if ref:
            with _LOCK:
                _QUEUED.pop(ref, None)
                JOBS.setdefault(ref, {"kind": kind})
                JOBS[ref].update(status="running", started=utcnow().isoformat(timespec="seconds"))
        ok = False
        try:
            fn = HANDLERS.get(kind)
            if fn is None:
                raise RuntimeError(f"No handler for job kind '{kind}'.")
            fn(0, dict(payload or {}))
            ok = True
        finally:
            again = None
            if ref:
                with _LOCK:
                    j = JOBS.setdefault(ref, {"kind": kind})
                    j.update(status="done" if ok else "failed", finished=utcnow().isoformat(timespec="seconds"))
                    again = j.pop("again", None)
                    if again is not None:
                        _QUEUED[ref] = time.time()
                        j.update(status="queued", queued=utcnow().isoformat(timespec="seconds"))
            if again is not None:
                _start(_make_run(tool, kind, ref, again))

    run.__name__ = f"{tool}:{kind}"
    run.kind = kind
    return run


def _again(tool: str, kind: str, title: str, ref: str, payload: dict) -> None:
    """After the asking request committed: the job running for this ref runs once more when it finishes — or, if it has
    finished meanwhile, it starts now."""
    with _LOCK:
        j = JOBS.get(ref, {})
        if j.get("status") == "running":
            j["again"] = payload
            return
        t = _QUEUED.get(ref)
        if t and time.time() - t < STALE_QUEUED:
            return
        _QUEUED[ref] = time.time()
        JOBS[ref] = {"kind": kind, "title": title[:200], "status": "queued", "queued": utcnow().isoformat(timespec="seconds"), "started": None, "finished": None}
    _start(_make_run(tool, kind, ref, payload))


def _start(run) -> None:
    """On the pool now, unless its kind already has MOST jobs there: then it waits (holding no thread) for one to finish."""
    kind = getattr(run, "kind", "")
    most = MOST.get(kind, 0)
    if most and not is_inline():
        with _LOCK:
            if _LANE.get(kind, 0) >= most:
                _WAITING.setdefault(kind, []).append(run)
                return
            _LANE[kind] = _LANE.get(kind, 0) + 1

        def lane():
            try:
                run()
            finally:
                with _LOCK:
                    _LANE[kind] = max(0, _LANE.get(kind, 0) - 1)
                    nxt = _WAITING.get(kind, []).pop(0) if _WAITING.get(kind) else None
                if nxt is not None:
                    _start(nxt)

        lane.__name__ = run.__name__
        _jobs.submit(lane)
        return
    _jobs.submit(run)


def _forget_refs(session, *_a) -> None:
    """The request that queued a job rolled back: the job never starts, so its ref no longer blocks the next one."""
    refs = session.info.pop("compat2_refs", None) or []
    with _LOCK:
        for r in refs:
            if JOBS.get(r, {}).get("status") == "queued":
                _QUEUED.pop(r, None)
                JOBS[r]["status"] = "dropped"


def _committed(session) -> None:
    session.info.pop("compat2_refs", None)  # the job starts now (cbx/db.py runs the after_commit work)


try:
    from sqlalchemy import event as _event

    _event.listen(SessionLocal, "after_soft_rollback", _forget_refs)  # every Session.rollback(), even before any SQL ran
    _event.listen(SessionLocal, "after_commit", _committed)
except Exception:  # noqa: BLE001 — without it a rolled-back ref waits STALE_QUEUED instead
    logger.debug("compat2: no session events", exc_info=True)


# ---- JSON app state ------------------------------------------------------------------------------------------------------
def state_get(db, key: str, default: Any = None) -> Any:
    from .models import AppState

    row = db.get(AppState, key)
    if row is None or not row.value:
        return default
    try:
        return json.loads(row.value)
    except (TypeError, ValueError):
        return default


def state_set(db, key: str, value: Any) -> None:
    from .models import AppState

    text = json.dumps(value, default=str, ensure_ascii=False)
    row = db.get(AppState, key)
    if row is None:
        db.add(AppState(key=key, value=text))
    else:
        row.value, row.updated_at = text, utcnow()
    db.flush()


# ---- audit ---------------------------------------------------------------------------------------------------------------
def audit(db, action: str, *, actor=None, actor_label: str = "", target: str = "", detail: str = "", outcome: str = "ok", ip: str = "", **meta):
    """2.x's audit.log(...) on the old one (same keywords; 2.x cuts lengths itself, so this does too)."""
    from .audit import log

    return log(db, (action or "")[:64], actor=actor, actor_label=(actor_label or "")[:120], target=(target or "")[:255], detail=detail or "",
               outcome=outcome, ip=(ip or "")[:64], **meta)


# ---- the bots: the Office and the pages' own team views --------------------------------------------------------------------
SOURCE = "tool"  # AgentWork.source of the rows written here (the tidy routine knows its own by it)
TEAMS = {"AP": ("ap", "AP", "Paying", "work", "Accounts payable and banking"),
         "Support": ("support", "Support", "Answering", "work",
                     "The Support desk's bots: they read the requesters' mails, check the readings and remind the leads. They work only inside the Support desk.")}
LIVE: dict[str, dict] = {}            # name → {state, task, ref, since, work_id, last_task, last_at, done_today, day}
_MEMBERS: dict[str, Optional[int]] = {}
_ROWS: set[int] = set()  # AgentWork ids of work going on in this process right now
NOTES_KEEP = 50
DONE_KEY = "compat2.done."  # + the day (SAST): {bot name: work finished}
try:
    from zoneinfo import ZoneInfo

    _SAST = ZoneInfo("Africa/Johannesburg")
except Exception:  # noqa: BLE001 — no tz database (a bare Windows Python without tzdata): SAST is UTC+2 all year
    _SAST = timezone(timedelta(hours=2))


def _today() -> str:
    return datetime.now(_SAST).date().isoformat()


def short(name: str) -> str:
    """"Sol (AP)" → "Sol" (the AP and Support pages show the short name)."""
    return name.split(" (")[0] if name.endswith(")") and " (" in name else name


def team_of(name: str) -> str:
    """"Sol (AP)" → "ap", "Kit (Support)" → "support"; anything else "other"."""
    if name.endswith(")") and " (" in name:
        t = TEAMS.get(name.rsplit(" (", 1)[1][:-1])
        if t:
            return t[0]
    return "other"


def _member_id(db, name: str, does: str = "") -> Optional[int]:
    """The bot's desk in the Office: a TeamMember by that name (made the first time, on its team). None when there is no
    team to sit on (a fresh test database before C-EXA's teams were seeded) — the page's own view works regardless."""
    from .models import AgentTeam, TeamMember

    if _MEMBERS.get(name) and db.get(TeamMember, _MEMBERS[name]) is not None:
        return _MEMBERS[name]

    m = db.query(TeamMember).filter(TeamMember.name == name).first()
    if m is None:
        suffix = name.rsplit(" (", 1)[1][:-1] if name.endswith(")") and " (" in name else ""
        spec = TEAMS.get(suffix)
        if spec is None:
            return None
        t = db.query(AgentTeam).filter(AgentTeam.key == spec[0]).one_or_none()
        if t is None:
            if spec[0] == "ap":
                return None  # C-EXA's AP team is seeded by cbx/cexa/teams.py; never made here
            t = AgentTeam(key=spec[0], name=spec[1], word=spec[2], kind=spec[3], purpose=spec[4], enabled=True, schedule={}, state={}, sort=95)
            db.add(t)
            db.flush()
        n = db.query(TeamMember).filter(TeamMember.team_id == t.id).count()
        m = TeamMember(team_id=t.id, name=name[:40], role="bot", focus=(does or f"Works inside the {spec[1]} tool only.")[:200], lead=False, enabled=True, sort=50 + n)
        db.add(m)
        db.flush()
    _MEMBERS[name] = m.id
    return m.id


def _open_row(db, name: str, task: str, ref: str, does: str) -> Optional[int]:
    from .models import AgentWork

    mid = _member_id(db, name, does)
    if mid is None:
        return None
    w = AgentWork(kind="task", title=task[:300], detail=f"ref {ref}" if ref else "", status="working", to_member_id=mid, from_label=short(name)[:60],
                  source=SOURCE, fingerprint=(ref or "")[:80], started_at=utcnow())
    db.add(w)
    db.flush()
    with _LOCK:
        _ROWS.add(w.id)  # before the commit: the tidy routine never takes a row going on for one left by a restart
    return w.id


def _close_row(db, wid: int, ok: bool, name: str = "") -> None:
    """The work is over: its Office row goes (it was only there to show "Working on: …"; kept, every invoice would count as
    a piece of C-EXA's teams' work in the owner's daily digest and on the desks' plants). What the bot finished today is
    counted in app_state instead, for the page's live view after a restart."""
    from .models import AgentWork

    w = db.get(AgentWork, wid)
    if w is not None:
        db.delete(w)
    if ok and name:
        key = DONE_KEY + _today()
        cur = state_get(db, key, {}) or {}
        cur[name] = int(cur.get(name, 0)) + 1
        state_set(db, key, cur)
    db.flush()


class _Handle:
    def __init__(self, name: str, ref: str, db):
        self.name, self.ref, self.db = name, ref, db

    def say(self, text: str, **_) -> None:
        say(team_of(self.name), text, who=short(self.name), ref=self.ref, db=self.db)

    observe = say

    def note(self, text: str) -> None:
        """What the bot is on right now (the live view's task line; nothing written)."""
        with _LOCK:
            if self.name in LIVE and LIVE[self.name].get("state") == "working":
                LIVE[self.name]["task"] = text[:255]

    def progress(self, *a, **k) -> None:
        return None


@contextmanager
def working(db, name: str, task: str, ref: str = "", *, does: str = "") -> Iterator[_Handle]:
    """A bot busy for a piece of work. With `db` the Office's row goes in the caller's session (for work that holds a write
    while it runs; it shows once the caller commits); with db=None it is written on its own short transaction (the caller
    must not hold a write then). The page's own live view (status_of) follows at once either way. Never raises for
    its bookkeeping; the work's own exception passes through."""
    day = _today()
    with _LOCK:
        cur = LIVE.get(name) or {}
        done = cur.get("done_today", 0) if cur.get("day") == day else 0
        LIVE[name] = {**cur, "state": "working", "task": task[:255], "ref": ref, "since": utcnow().isoformat(timespec="seconds"), "day": day,
                      "done_today": done, "depth": int(cur.get("depth") or 0) + 1 if cur.get("state") == "working" else 1}
    wid = None
    try:
        if db is not None:
            wid = _open_row(db, name, task, ref, does)
        else:
            with session_scope() as s:
                wid = _open_row(s, name, task, ref, does)
    except Exception:  # noqa: BLE001 — the Office row is bookkeeping; the work goes on
        logger.debug("compat2 working: open", exc_info=True)
        wid = None
    ok = True
    try:
        yield _Handle(name, ref, db)
    except BaseException:
        ok = False
        raise
    finally:
        with _LOCK:
            cur = LIVE.get(name) or {}
            depth = max(0, int(cur.get("depth") or 1) - 1)
            today = _today()
            LIVE[name] = {**cur, "state": "working" if depth else "idle", "depth": depth, "task": cur.get("task", "") if depth else "",
                          "last_task": task[:255], "last_at": utcnow().isoformat(timespec="seconds"),
                          "done_today": (cur.get("done_today", 0) if cur.get("day") == today else 0) + (1 if ok else 0), "day": today}
            if wid:
                _ROWS.discard(wid)
        if wid:
            try:
                if db is not None and db.is_active:
                    _close_row(db, wid, ok, name)
                else:
                    with session_scope() as s:
                        _close_row(s, wid, ok, name)
            except Exception:  # noqa: BLE001 — left 'working': the tidy routine closes it
                logger.debug("compat2 working: close", exc_info=True)


def note(name: str, text: str) -> None:
    """What a working bot is on right now (the page's task line; nothing written — 2.x's capture.note)."""
    with _LOCK:
        if name in LIVE and LIVE[name].get("state") == "working":
            LIVE[name]["task"] = (text or "")[:255]


def status_of(names, db=None) -> dict:
    """{name: {state: idle|working, task, since, done_today, last_task, last_at}} for the page's own team view. With `db`,
    done_today also counts what was finished before a restart (app_state)."""
    today = _today()
    out = {}
    with _LOCK:
        for n in names:
            cur = dict(LIVE.get(n) or {})
            out[n] = {"state": cur.get("state") or "idle", "task": cur.get("task", "") if cur.get("state") == "working" else "",
                      "since": cur.get("since") if cur.get("state") == "working" else None,
                      "done_today": cur.get("done_today", 0) if cur.get("day") == today else 0,
                      "last_task": cur.get("last_task", ""), "last_at": cur.get("last_at")}
    if db is not None:
        try:
            done = state_get(db, DONE_KEY + today, {}) or {}
            for n in out:
                out[n]["done_today"] = max(out[n]["done_today"], int(done.get(n, 0) or 0))
        except Exception:  # noqa: BLE001
            logger.debug("compat2 status_of", exc_info=True)
    return out


def say(team: str, text: str, *, who: str = "", ref: str = "", db=None) -> None:
    """A line the AP / Support team view shows (the last 50 per team, in app_state as JSON). Never raises."""
    item = {"at": utcnow().isoformat(timespec="seconds"), "who": who[:40], "text": (text or "")[:500], "ref": (ref or "")[:64]}
    key = f"compat2.notes.{team}"

    def put(s):
        cur = state_get(s, key, []) or []
        state_set(s, key, (cur + [item])[-NOTES_KEEP:])

    try:
        if db is not None:
            put(db)
        else:
            with session_scope() as s:
                put(s)
    except Exception:  # noqa: BLE001
        logger.debug("compat2 say", exc_info=True)


def notes(db, team: str, n: int = NOTES_KEEP) -> list[dict]:
    """The team's last lines, newest first."""
    return list(reversed((state_get(db, f"compat2.notes.{team}", []) or [])[-n:]))


def tidy(db) -> int:
    """The Office's rows of this module, kept honest: work left 'working' by a restart (not going on in this process) is
    closed; a row going on is touched (the heartbeat's unstick never sends it back to 'open'); and work someone handed a
    tool bot (they have no hands) is closed with a plain answer. Returns how many rows were closed."""
    from .models import AgentTeam, AgentWork, TeamMember

    n = 0
    with _LOCK:
        live = set(_ROWS)
    for w in db.query(AgentWork).filter(AgentWork.source == SOURCE, AgentWork.status.in_(("working", "open", "held"))).all():
        if w.id in live and w.status == "working":
            w.updated_at = utcnow()
            continue
        db.delete(w)  # left by a restart (its work was never finished): the Office row goes
        n += 1
    keys = [t[0] for t in TEAMS.values()]
    bots = [m.id for m, _t in db.query(TeamMember, AgentTeam).join(AgentTeam, AgentTeam.id == TeamMember.team_id)
            .filter(AgentTeam.key.in_(keys), TeamMember.role == "bot").all()]
    if bots:
        for w in db.query(AgentWork).filter(AgentWork.to_member_id.in_(bots), AgentWork.source != SOURCE, AgentWork.kind == "task",
                                            AgentWork.status.in_(("open", "held"))).all():
            w.status, w.finished_at = "done", utcnow()
            w.result = "I work only inside my own tool (the AP Tool or the Support desk) and take no other work. Ask the team's lead."
            n += 1
    from .models import AppState

    keep = {DONE_KEY + _today(), DONE_KEY + (datetime.now(_SAST).date() - timedelta(days=1)).isoformat()}
    for row in db.query(AppState).filter(AppState.key.like(DONE_KEY + "%")).all():  # the day counts of days gone by
        if row.key not in keep:
            db.delete(row)
    db.flush()
    return n


@routine("compat2-office", 60)
def tidy_routine() -> None:
    with session_scope() as s:
        tidy(s)
