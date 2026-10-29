"""C-EXA's team of agents. An "agent" is a named worker doing one job (harvester-02 reading a batch,
recon-01 running a nightly workflow, dev-01 building a patch). Features register their work here so
the owner's Agents page and C-EXA's own notes can see who is busy with what.

    with agents.working("harvester", task="Batch #12 · Ekurhuleni July", ref="batch:12") as a:
        a.progress(0.4, "9 of 14 pages")
        ...
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator, Optional

_lock = threading.Lock()
_agents: dict[str, "Agent"] = {}
_seq: dict[str, int] = {}
_history: list[dict] = []  # last finished jobs


@dataclass
class Agent:
    name: str  # harvester-02
    kind: str  # harvester
    task: str = ""
    ref: str = ""
    started: float = field(default_factory=time.time)
    progress_v: float = 0.0
    note: str = ""
    state: str = "busy"  # busy | idle
    last_task: str = ""
    last_finished: Optional[float] = None
    notes: list[str] = field(default_factory=list)  # C-EXA's observations about this agent

    def progress(self, v: float, note: str = "") -> None:
        self.progress_v = max(0.0, min(1.0, v))
        if note:
            self.note = note

    def observe(self, text: str) -> None:
        self.notes.append(text)
        self.notes = self.notes[-5:]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "task": self.task if self.state == "busy" else "",
            "last_task": self.last_task,
            "ref": self.ref,
            "state": self.state,
            "progress": round(self.progress_v, 3),
            "note": self.note,
            "since": self.started,
            "notes": self.notes,
        }


def _acquire(kind: str) -> Agent:
    with _lock:
        for a in _agents.values():
            if a.kind == kind and a.state == "idle":
                a.state = "busy"
                a.started = time.time()
                a.progress_v = 0.0
                a.note = ""
                return a
        _seq[kind] = _seq.get(kind, 0) + 1
        a = Agent(name=f"{kind}-{_seq[kind]:02d}", kind=kind)
        _agents[a.name] = a
        return a


@contextmanager
def working(kind: str, task: str, ref: str = "") -> Iterator[Agent]:
    a = _acquire(kind)
    a.task, a.ref = task, ref
    t0 = time.time()
    ok = True
    try:
        yield a
    except Exception:
        ok = False
        raise
    finally:
        with _lock:
            a.state = "idle"
            a.last_task = task + ("" if ok else " (failed)")
            a.last_finished = time.time()
            a.progress_v = 1.0 if ok else a.progress_v
            _history.append({"agent": a.name, "task": task, "ok": ok, "seconds": round(time.time() - t0, 1), "at": time.time()})
            del _history[:-50]


def snapshot() -> dict:
    with _lock:
        return {"agents": [a.to_dict() for a in _agents.values()], "history": list(reversed(_history))}


def running_count(db=None) -> int:
    with _lock:
        return sum(1 for a in _agents.values() if a.state == "busy")


# What each standing agent is for, in a sentence the owner can read on the Agents page. Only kinds
# that real code claims are here: `recon` and `sheets` used to be seeded too and nothing ever ran
# as them — an agent that never works is decoration, and decoration on a status page is a lie.
KINDS = {
    "harvester": "reads PDF batches (cbx/pdfstudio/pipeline.py)",
    "dev": "builds proposals in a sandbox (cbx/cexa/developer.py)",
    "watcher": "reads the audit trail every five minutes; alerts on sign-in bursts, refusals and errors; one report a day (cbx/cexa/watcher.py)",
}


def seed_defaults() -> None:
    """Make the standing team visible even before any work has run."""
    with _lock:
        for kind in KINDS:
            if not any(a.kind == kind for a in _agents.values()):
                _seq[kind] = _seq.get(kind, 0) + 1
                _agents[f"{kind}-{_seq[kind]:02d}"] = Agent(name=f"{kind}-{_seq[kind]:02d}", kind=kind, state="idle")


seed_defaults()
