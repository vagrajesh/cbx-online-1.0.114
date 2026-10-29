"""Server jobs a rule can run (1.0.56): "Read a Swiftnet quotation", "Work out an IFRS 16 month", "Read
PDFs with a profile" … — Core's know-how, offered to the Builder the way providers offer Core's data.

A rule step `{"type": "action", "action": "<name>", "data": "<table>", "row": "{{trigger.row.id}}",
"in": {"<param>": "<column or {{template}}>"}, "out": {"<result>": "<column>"}, "as": "<name>"}` runs one.
What goes in comes from the row's columns (a file column for a file) or from a template; what comes back
is written to the row's columns (a file into a file column) and is also {{<as>.<result>}} for the steps
after it. A job marked `slow` runs in the background: the rule waits ("working …") and carries on when
the job is done — the page does not hang for a two-minute calculation.

A job may need a tool grant or a role of the person the rule runs as; it never gets more than they have.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
import traceback
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Optional

log_ = logging.getLogger("cbx.actions")

KINDS = ("file", "text", "number", "date", "month", "checklist", "json")


@dataclass
class Param:
    key: str
    label: str
    kind: str = "text"  # file | text | number | date | month | checklist | json
    accept: tuple = ()  # file types for a file param
    required: bool = True
    hint: str = ""
    choices: Optional[Callable] = None  # (db, user) → [(value, label)]: a pick-list the Builder shows (1.0.57)


@dataclass
class Action:
    name: str
    title: str
    group: str
    description: str
    fn: Callable
    inputs: list = field(default_factory=list)
    outputs: list = field(default_factory=list)
    slow: bool = False
    tool: Optional[str] = None  # a grant the person the rule runs as must hold
    roles: tuple = ()
    outputs_for: Optional[Callable] = None  # (db, {param: value}) → more outputs, e.g. a PDF Studio template's columns

    def out(self, db=None, user=None) -> dict:
        p = lambda x: {"key": x.key, "label": x.label, "kind": x.kind, "accept": list(x.accept), "required": x.required, "hint": x.hint}  # noqa: E731
        d = {"name": self.name, "title": self.title, "group": self.group, "description": self.description, "slow": self.slow,
             "inputs": [p(x) for x in self.inputs], "outputs": [p(x) for x in self.outputs]}
        if db is not None:
            for x, px in zip(self.inputs, d["inputs"]):
                if x.choices:
                    try:
                        px["choices"] = [[str(v), str(lab)] for v, lab in x.choices(db, user)][:200]
                    except Exception:  # noqa: BLE001 — a catalogue must always answer
                        px["choices"] = []
            if self.outputs_for:
                ch = next((x for x in d["inputs"] if x.get("choices")), None)
                d["outputs_by"] = {}
                for v, _lab in (ch or {}).get("choices") or []:
                    try:
                        d["outputs_by"][v] = [p(x) for x in self.outputs_for(db, {ch["key"]: v})]
                    except Exception:  # noqa: BLE001
                        d["outputs_by"][v] = []
        return d


ACTIONS: dict[str, Action] = {}


def action(name: str, title: str, *, group: str, description: str, inputs=(), outputs=(), slow: bool = False, tool: Optional[str] = None, roles=(), outputs_for=None):
    def deco(fn):
        ACTIONS[name] = Action(name, title, group, description, fn, list(inputs), list(outputs), slow, tool, tuple(roles), outputs_for)
        return fn

    return deco


class ActionError(ValueError):
    pass


@dataclass
class Job:
    """What a job's function gets: the person, their inputs (a file as a local path + its name), a scratch
    folder that is removed afterwards."""
    db: Any
    user: Any
    inputs: dict
    names: dict
    tmp: Path


def catalogue(db=None, user=None) -> list[dict]:
    """Every job the server offers — with a person, only the ones they may run, and their pick-lists."""
    load()
    return [a.out(db, user) for a in sorted(ACTIONS.values(), key=lambda a: (a.group, a.title)) if db is None or user is None or may_run(db, user, a) is None]


def may_run(db, user, a: Action) -> Optional[str]:
    """None when the person the rule runs as may run this job, else why not."""
    if user is None:
        return "nobody to run it as"
    if a.roles and user.role not in a.roles and not user.is_admin:
        return f"{a.title} is for {', '.join(a.roles)} only"
    if a.tool:
        from ..security.deps import has_tool

        if not has_tool(db, user, a.tool):
            return f"{user.name} does not have the tool {a.title} needs ({a.tool})"
    return None


def check_step(t, at: str, s: dict) -> list[str]:
    """The loader's checks for an action step, in plain sentences."""
    load()
    errs = []
    a = ACTIONS.get(str(s.get("action") or ""))
    if a is None:
        return [f"{at}: choose which job to run ('{s.get('action')}' is not one this server knows)"]
    b = t.data.get(s.get("data")) if s.get("data") else None
    cols = {c.get("key"): c for c in ((b or {}).get("columns") or []) if isinstance(c, dict)}
    if s.get("data") and b is None:
        errs.append(f"{at}: the table '{s.get('data')}' is not in the tool")
    fr = s.get("from") if isinstance(s.get("from"), dict) and s["from"].get("data") else None
    icols = cols
    if fr:
        fb = t.data.get(fr["data"])
        if fb is None:
            errs.append(f"{at}: what goes in comes from '{fr['data']}', which is not in the tool")
        elif not fr.get("row"):
            errs.append(f"{at}: say which row of {fr['data']} it takes from")
        icols = {c.get("key"): c for c in ((fb or {}).get("columns") or []) if isinstance(c, dict)}
    ins = s.get("in") or {}
    for p in a.inputs:
        v = ins.get(p.key)
        if p.required and v in (None, ""):
            errs.append(f"{at}: {a.title} needs {p.label.lower()}")
        elif p.kind == "file" and v not in (None, ""):
            if (icols.get(v) or {}).get("type") != "file":
                errs.append(f"{at}: {p.label} comes from a file column of the row")
    for k in (s.get("fail") or {}) if isinstance(s.get("fail"), dict) else ():
        if k not in cols:
            errs.append(f"{at}: when it fails it sets '{k}', which is not a column of {s.get('data')}")
    for k, col in (s.get("out") or {}).items():
        p = next((x for x in a.outputs if x.key == k), None)
        if p is None and a.outputs_for:
            p = Param(k, k)  # what it gives back depends on a choice (checked when it runs)
        if p is None:
            errs.append(f"{at}: {a.title} gives back no '{k}'")
        elif col and col not in cols:
            errs.append(f"{at}: '{col}' is not a column of {s.get('data')}")
        elif col and p.kind == "file" and cols[col].get("type") != "file":
            errs.append(f"{at}: {p.label} is a file — it goes into a file column")
        elif col and p.kind == "checklist" and cols[col].get("type") != "checklist":
            errs.append(f"{at}: {p.label} is a list of things to check — it goes into a checklist column")
    if (any(p.kind == "file" for p in a.inputs) or s.get("out")) and not (s.get("data") and s.get("row")):
        errs.append(f"{at}: say which row (its table, and its id, e.g. {{{{trigger.row.id}}}})")
    return errs


# ---- running ------------------------------------------------------------------------------------------
def _inputs(t, s: dict, ctx: dict, row: Optional[dict], tmp: Path) -> tuple[dict, dict]:
    from . import files as FILES
    from .flows import render

    a = ACTIONS[s["action"]]
    b = t.data.get(s.get("data")) if s.get("data") else None
    cols = {c.get("key"): c for c in ((b or {}).get("columns") or []) if isinstance(c, dict)}
    vals, names = {}, {}
    for p in a.inputs:
        src = (s.get("in") or {}).get(p.key)
        if src in (None, ""):
            vals[p.key] = None
            continue
        if isinstance(src, str) and src in cols and row is not None:
            v = row.get(src)
            if p.kind == "file":
                path = FILES.path_of(t.slug, s["data"], int(row["id"]), v if isinstance(v, dict) else None)
                if path is None:
                    if p.required:
                        raise ActionError(f"The row has no {cols[src].get('key')} file yet.")
                    vals[p.key] = None
                    continue
                # under its own name (prefixed): some of Core's readers look at the name (an ARO06 export)
                dst = tmp / f"{p.key}_{FILES.clean_name((v or {}).get('name') or path.name)}"
                shutil.copyfile(path, dst)
                vals[p.key], names[p.key] = dst, (v or {}).get("name") or dst.name
                continue
            vals[p.key] = v
        else:
            vals[p.key] = render(src, ctx)
        if p.required and vals[p.key] in (None, ""):
            raise ActionError(f"{a.title} needs {p.label.lower()}.")
    return vals, names


def _write(db, t, s: dict, row: Optional[dict], result: dict, by: Optional[int]) -> Optional[dict]:
    """Put what the job gave back into the row's columns."""
    from ..db import utcnow
    from . import files as FILES
    from .store import Store

    out = s.get("out") or {}
    if not out or row is None:
        return row
    b = t.data[s["data"]]
    cols = b.get("columns") or []
    bycol = {c["key"]: c for c in cols}
    st = Store(t.slug)
    values = {}
    for k, col in out.items():
        if not col or k not in result:
            continue
        v = result[k]
        if bycol[col].get("type") == "file":
            if v:
                name, data = v
                meta = FILES.save(t.slug, s["data"], int(row["id"]), {**bycol[col], "max_mb": 100}, name, data, by, utcnow().isoformat())
                st.set_file(s["data"], cols, int(row["id"]), col, meta)
            continue
        if bycol[col].get("type") == "checklist" and isinstance(v, list):
            v = [x if isinstance(x, dict) else {"text": str(x)} for x in v]
        values[col] = v
    if values:
        st.update(s["data"], cols, int(row["id"]), values, unique=b.get("unique"))
    return st.get(s["data"], cols, int(row["id"]))


def run_now(db, t, s: dict, ctx: dict, user, *, dry: bool = False) -> dict:
    """Run a job and write its results; returns what it gave back (files as their names)."""
    from .flows import _num, render
    from .store import Store

    load()
    a = ACTIONS.get(str(s.get("action") or ""))
    if a is None:
        raise ActionError(f"The job '{s.get('action')}' is not known to this server.")
    why = may_run(db, user, a)
    if why:
        raise ActionError(why)
    row = _row_of(t, s.get("data"), s.get("row"), ctx)
    # what goes in may come from another row ("from"): e.g. a month's row takes the source files of its upload
    fr = s.get("from") if isinstance(s.get("from"), dict) and s["from"].get("data") else None
    src_row = _row_of(t, fr["data"], fr.get("row"), ctx) if fr else row
    from ..config import settings

    tmp = Path(tempfile.mkdtemp(prefix="action_", dir=str(settings.path("tmp"))))
    try:
        vals, names = _inputs(t, {**s, "data": fr["data"]} if fr else s, ctx, src_row, tmp)
        if dry:
            return {"_dry": True}
        try:
            result = a.fn(Job(db, user, vals, names, tmp)) or {}
        except ActionError:
            raise
        except Exception as e:  # noqa: BLE001 — Core's code failing is a plain sentence on the run, the trace in the log
            log_.error("job %s failed:\n%s", a.name, traceback.format_exc())
            raise ActionError(f"{a.title} did not finish: {str(e)[:300] or type(e).__name__}")
        _write(db, t, s, row, result, user.id if user else None)
        return {k: (v[0] if isinstance(v, tuple) else v) for k, v in result.items()}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _row_of(t, data, rid_tpl, ctx) -> Optional[dict]:
    from .flows import _num, render
    from .store import Store

    if not (data and rid_tpl):
        return None
    b = t.data.get(data)
    if b is None:
        raise ActionError(f"The tool has no table '{data}'.")
    rid = int(_num(render(rid_tpl, ctx)) or 0)
    row = Store(t.slug).get(data, b.get("columns") or [], rid)
    if row is None:
        raise ActionError(f"Row {rid} is not in {data}.")
    return row


def write_failure(db, t, s: dict, ctx: dict, error: str) -> None:
    """A job that did not finish: the row says so ("fail": {column: value or {{error}}}), so nobody waits for it."""
    from .flows import render
    from .store import Store

    fail = s.get("fail") if isinstance(s.get("fail"), dict) else None
    if not fail or not s.get("data") or not s.get("row"):
        return
    try:
        row = _row_of(t, s["data"], s["row"], ctx)
        b = t.data[s["data"]]
        vals = {k: render(v, {**ctx, "error": error[:400]}) for k, v in fail.items() if any(c.get("key") == k for c in b.get("columns") or [])}
        if row is not None and vals:
            Store(t.slug).update(s["data"], b.get("columns") or [], int(row["id"]), vals)
    except Exception:  # noqa: BLE001 — the run records the error either way
        log_.warning("could not mark the row of a failed job", exc_info=True)


def submit_after_commit(db, run_id: int) -> None:
    """A slow job starts once the waiting run is saved (the worker reads it from its own session)."""
    from sqlalchemy import event

    from .. import jobs

    @event.listens_for(db, "after_commit", once=True)
    def _go(_session):  # noqa: ANN001
        jobs.submit(work, run_id)


def work(run_id: int) -> None:
    """The background half of a slow job: run it, write its results, and let the rule carry on."""
    from ..db import session_scope, utcnow
    from ..models import User
    from ..models.flows import FlowRun
    from . import flows as FL
    from . import loader

    with session_scope() as db:
        run = db.get(FlowRun, run_id)
        w = dict((run.waiting or {}) if run else {})
        if run is None or run.status != "waiting" or w.get("kind") != "working" or w.get("claimed"):
            return
        w["claimed"] = utcnow().isoformat()
        run.waiting = w
        db.commit()
        t = loader.registry().tools.get(run.tool)
        f = (t.flows or {}).get(run.flow) if t else None
        s = ((f or {}).get("steps") or {}).get(w.get("step")) if f else None
        if t is None or s is None:
            run.status, run.note, run.finished_at, run.waiting = "failed", "The rule changed while its job was running.", utcnow(), {}
            return
        user = db.get(User, run.run_as_id)
        from ..security.roles import attach

        attach(db, user)
        ctx = dict(run.ctx or {})
        try:
            got = run_now(db, t, s, ctx, user)
        except Exception as e:  # noqa: BLE001 — the run records it; the page does not
            if not isinstance(e, ActionError):
                log_.error("action %s in run %s failed:\n%s", s.get("action"), run_id, traceback.format_exc())
            note = str(e)[:500] or type(e).__name__
            write_failure(db, t, s, ctx, note)
            FL._note(run, w.get("step", ""), "action", False, note)
            run.status, run.note, run.finished_at, run.waiting = "failed", note, utcnow(), {}
            return
        FL._note(run, w.get("step", ""), "action", True, f"{ACTIONS[s['action']].title}: done")
        FL.resume(db, t, run, s.get("next"), extra={s.get("as") or "job": got})


def sweep(db) -> int:
    """Once a minute (flows' routine): a slow job nobody picked up (a restart in between) is started again;
    one that has been running for more than six hours is marked failed."""
    from ..db import utcnow
    from ..models.flows import FlowRun
    from .. import jobs

    n = 0
    for run in db.query(FlowRun).filter(FlowRun.status == "waiting").limit(200).all():
        w = run.waiting or {}
        if w.get("kind") != "working":
            continue
        try:
            from datetime import datetime

            since = datetime.fromisoformat(w.get("claimed") or w.get("since") or "")
        except ValueError:
            since = None
        if since is not None and since.tzinfo is None:
            from datetime import timezone

            since = since.replace(tzinfo=timezone.utc)
        if not w.get("claimed") and (since is None or utcnow() - since > timedelta(minutes=2)):
            jobs.submit(work, run.id)
            n += 1
        elif w.get("claimed") and since is not None and utcnow() - since > timedelta(hours=6):
            run.status, run.note, run.finished_at, run.waiting = "failed", "The job did not finish (the server may have restarted).", utcnow(), {}
    return n


_LOADED = False


def load() -> None:
    """The jobs live next to the Core code they use; import them once."""
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    from . import actions_core  # noqa: F401
