"""1.0.92: what CBX-Connect 2's IFRS 16 service asks of its jobs registry, on the old CBX's thread pool — so
cbx/ifrs16/page.py stays line for line the 2.x service. A job starts once the request that asked for it has
committed (the row it reads must be there)."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Callable

from .. import jobs as _jobs
from ..db import after_commit

HANDLERS: dict[str, Callable] = {}


def handler(kind: str):
    def deco(fn):
        HANDLERS[kind] = fn
        return fn

    return deco


class _Job:
    id = None


def enqueue(db, *, tool: str, kind: str, title: str, user=None, ref: str = "", total: int = 1, payload: dict | None = None) -> _Job:
    after_commit(db, _jobs.submit, lambda: HANDLERS[kind](0, payload or {}))
    return _Job()


def progress(job_id, **kw) -> None:  # the old CBX shows the row's own status instead
    return None


class _Say:
    def say(self, *a, **k):
        return None

    def progress(self, *a, **k):
        return None


class company:  # noqa: N801 — stands in for 2.x's cbx.agents.company (the office) here
    @staticmethod
    @contextmanager
    def working(*a, **k):
        yield _Say()
