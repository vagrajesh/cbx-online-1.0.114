"""Background work: a small thread pool for long jobs (PDF batches, interviews, repair loops)
and a scheduler thread for C-EXA's routines (morning brief, check-ins, reminders, pipeline).
Kept deliberately simple — one process, one box."""
from __future__ import annotations

import logging
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

log = logging.getLogger("cbx.jobs")
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="cbx-job")
_stop = threading.Event()
_sched: threading.Thread | None = None
_inline = False  # tests set True so jobs run synchronously

ROUTINES: list[tuple[str, int, Callable[[], None]]] = []  # (name, every_seconds, fn)


def _record(kind: str, target: str, e: BaseException) -> None:
    """A failed job or routine becomes an audit row the watcher can report — best effort."""
    try:
        from .audit import record_error

        record_error(kind, target, f"{type(e).__name__}: {str(e)[:300]}")
    except Exception:  # noqa: BLE001
        pass


def set_inline(v: bool) -> None:
    global _inline
    _inline = v


# 1.0.114: a promote or a rollback that puts the database back holds the background still: no new job or routine starts
# while it is paused, and pause() waits for the ones already running, so nothing writes between the safety copy and the restore.
_paused = threading.Event()
_running = 0
_running_cv = threading.Condition()


def _enter() -> None:
    global _running
    with _running_cv:
        while _paused.is_set():
            _running_cv.wait(5)
        _running += 1


def _leave() -> None:
    global _running
    with _running_cv:
        _running -= 1
        _running_cv.notify_all()


def pause(timeout: float = 60.0) -> bool:
    """Stop new background work and wait (up to `timeout` s) for what is running. True when all is quiet."""
    _paused.set()
    deadline = time.time() + timeout
    with _running_cv:
        while _running > 0:
            left = deadline - time.time()
            if left <= 0:
                return False
            _running_cv.wait(min(left, 1.0))
    return True


def resume() -> None:
    _paused.clear()
    with _running_cv:
        _running_cv.notify_all()


def paused() -> bool:
    return _paused.is_set()


def running() -> int:
    return _running


def submit(fn: Callable, *args, **kwargs):
    if _inline:
        return fn(*args, **kwargs)

    def wrapped():
        _enter()  # 1.0.114: waits while a promote/rollback holds the background still
        try:
            fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            log.error("job %s failed:\n%s", getattr(fn, "__name__", fn), traceback.format_exc())
            _record("job_failed", getattr(fn, "__name__", str(fn)), e)
        finally:
            _leave()

    return _pool.submit(wrapped)


def routine(name: str, every_seconds: int):
    def deco(fn):
        ROUTINES.append((name, every_seconds, fn))
        return fn

    return deco


LAST_TICK: dict[str, float] = {"loop": 0.0}   # when the scheduler last woke (the pulse reads this)


def last_tick() -> float:
    return LAST_TICK["loop"]


def _loop():
    last: dict[str, float] = {}
    while not _stop.is_set():
        now = time.time()
        LAST_TICK["loop"] = now
        for name, every, fn in list(ROUTINES):
            if _paused.is_set():  # 1.0.114: a promote/rollback holds the background still; the routines run after it
                break
            if now - last.get(name, 0) >= every:
                last[name] = now
                _enter()
                try:
                    fn()
                except Exception as e:  # noqa: BLE001
                    log.error("routine %s failed:\n%s", name, traceback.format_exc())
                    _record("routine_failed", name, e)
                finally:
                    _leave()
        _stop.wait(15)


def start_scheduler():
    global _sched
    if _inline or _sched:
        return
    try:  # 1.0.103: nothing can be busy before the routines and the pool's first job: a bot's mark left by a stop or a crash goes
        from . import busy

        busy.clear_all()
    except Exception as e:  # noqa: BLE001 — never stops the start
        _record("routine_failed", "busy.clear_all", e)
    # import routine modules so they register
    for mod in ("cbx.cexa.routines", "cbx.cexa.watcher", "cbx.cexa.pulse", "cbx.cexa.heartbeat", "cbx.cexa.ceo", "cbx.warehouse.pipeline", "cbx.docwatch.reminders", "cbx.docwatch.tool", "cbx.engine.flows", "cbx.voice.calls", "cbx.support.core", "cbx.support.inbox", "cbx.maildesk.core",
                "cbx.ap.capture",  # 1.0.98: the AP capture wake, the AP lists' seed and cbx/compat2.py's office tidy
                "cbx.support.nudge", "cbx.support.learn", "cbx.support.followup"):  # 1.0.98: Nudge's reminders and digests; the desk's learning job
        try:
            __import__(mod)
        except ModuleNotFoundError:
            pass
    _stop.clear()
    _sched = threading.Thread(target=_loop, name="cbx-scheduler", daemon=True)
    _sched.start()


def stop_scheduler():
    _stop.set()
