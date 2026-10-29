"""Flows: "when this happens, do these steps" — the part of a tool folder that acts.

A flow is `tools/<slug>/flows/<name>.json`:

    {"title": "flow.<name>.title",            a language key (the Builder writes it)
     "enabled": true,
     "trigger": {"type": "button" | "row_added" | "row_changed" | "schedule", ...},
     "run_as": "starter" | "owner",           whose rights the steps use (a schedule is always "owner")
     "start": "s1",
     "steps": {"s1": {"type": "find_rows", ..., "next": "s2"}, "s2": {...}},
     "canvas": {...}}                          positions only

Steps are a graph: each has `next`; `if` and `approval` have `yes` / `no`; `for_each` has `body` (run
once per item) and `next` (after the last). A loop anywhere else is refused — For each is the loop.

    find_rows   rows from Core data (a provider) or the tool's own table, filtered (`where`), into `as`
    add_row     a row into the tool's own table            update_row   change one (by id)
    set         a value into a name                        if           yes / no on a condition
    for_each    the body once per item of a list           notify       a message in CBX to people
    email       an email (only to people CBX knows, or the company's own domain — the mailer's rule)
    approval    stop until a named person or role approves (yes) or declines (no)
    wait        stop for minutes, hours or days            api          call an API connection
    stop        end here, done or failed, with a sentence

Values are templates: `{{doc.entity}}`, `{{today}}`, `{{trigger.row.email}}`, `{{person.name}}`. A
template that is one `{{…}}` alone keeps its type (a list stays a list).

What holds it: a run uses the rights of the person who started it, or of the tool's owner (a
schedule, or `run_as: owner`) — the same providers, row rules and mailer checks as the screens.
Rows written by a flow never fire another flow. At most MAX_STEPS steps and MAX_ITEMS items per
For each. Every run is a FlowRun with each step logged; a failure is logged and never raises into the
page that started it. A dry run (the Builder's Test) writes, sends and calls nothing — it says what it
would have done.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..audit import log
from ..config import settings
from ..db import utcnow
from ..models import User
from ..models.flows import FlowRun

TRIGGERS = ("button", "row_added", "row_changed", "schedule")
STEP_TYPES = ("find_rows", "add_row", "update_row", "set", "if", "for_each", "notify", "email", "approval", "wait", "api", "action", "make_doc", "require", "stop")
OPS = ("equals", "not_equals", "gt", "gte", "lt", "lte", "contains", "not_contains", "empty", "not_empty",
       "days_from_today", "within_days", "past", "this_week", "this_month", "this_year")
EVERY = ("hour", "day", "weekday", "week", "month")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MAX_STEPS = 1000
MAX_ITEMS = 500
CTX_LIMIT = 200_000  # bytes of JSON kept on the run row
TPL = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\}\}")
class FlowError(Exception):
    """A step could not do its work — the sentence goes in the run log."""


class Pause(Exception):
    """The run waits (an approval, a timer). The runner saves where it is."""


class Stopped(Exception):
    """A Stop step: the whole run ends here, For each or not."""


# ---- time where the business is -----------------------------------------------------------------
def local_now() -> datetime:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(settings.timezone))
    except Exception:  # noqa: BLE001 — Windows without tzdata
        return datetime.now(timezone(timedelta(hours=2)))


def local_today() -> date:
    return local_now().date()


# ---- checking a flow (the loader calls this) ------------------------------------------------------
def check(t, name: str, f: dict) -> tuple[list[str], set]:
    """Problems in one flow, in plain sentences, and the language keys it uses."""
    from . import providers

    errs: list[str] = []
    used: set = set()
    where = f"flows/{name}"
    if not re.match(r"^[a-z][a-z0-9_]{0,40}$", name):
        errs.append(f"{where}: lowercase letters, digits and underscores only")
    if f.get("title"):
        used.add(f["title"])
    tr = f.get("trigger") or {}
    typ = tr.get("type")
    if typ not in TRIGGERS:
        errs.append(f"{where}: the trigger must be one of {', '.join(TRIGGERS)}")
    if typ in ("row_added", "row_changed") and tr.get("data") not in t.data:
        errs.append(f"{where}: the trigger watches a table the tool does not have ('{tr.get('data')}')")
    if typ == "schedule":
        if tr.get("every") not in EVERY:
            errs.append(f"{where}: a schedule runs every {' / '.join(EVERY)}")
        at = str(tr.get("at") or "07:00")
        if tr.get("every") != "hour" and not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", at):
            errs.append(f"{where}: the time must be HH:MM (24 hours), e.g. 07:00")
        if tr.get("every") == "week" and tr.get("day") not in DAYS:
            errs.append(f"{where}: a weekly schedule needs a day ({', '.join(DAYS)})")
        if tr.get("every") == "month" and not (isinstance(tr.get("day"), int) and 1 <= tr["day"] <= 28):
            errs.append(f"{where}: a monthly schedule needs a day of the month, 1 to 28")
        if f.get("run_as", "owner") != "owner":
            errs.append(f"{where}: a schedule has nobody to run as but the tool's owner — set 'run as' to the owner")
    if typ == "button" and tr.get("label"):
        used.add(tr["label"])
    if f.get("run_as", "starter") not in ("starter", "owner"):
        errs.append(f"{where}: run as 'starter' (the person who started it) or 'owner' (the tool's owner)")
    if (f.get("run_as") == "owner" or typ == "schedule") and not (t.manifest.get("owner") or "").strip():
        errs.append(f"{where}: runs as the tool's owner, but the Tool block has no owner email")
    steps = f.get("steps") or {}
    if not isinstance(steps, dict):
        return errs + [f"{where}: 'steps' must be an object of steps by id"], used
    start = f.get("start")
    if not steps:
        errs.append(f"{where}: the flow has no steps yet — pull one from the palette and wire the trigger to it")
    elif start not in steps:
        errs.append(f"{where}: the first step '{start}' is not one of the steps — wire the trigger to a step")
    for sid, s in steps.items():
        at = f"{where} · step {sid}"
        if not re.match(r"^[A-Za-z0-9_-]{1,40}$", str(sid)):
            errs.append(f"{where}: step ids are letters, digits, _ or -")
        st = (s or {}).get("type")
        if st not in STEP_TYPES:
            errs.append(f"{at}: type must be one of {', '.join(STEP_TYPES)}")
            continue
        for k in ("next", "yes", "no") + (("body",) if st == "for_each" else ()):
            if s.get(k) and s[k] not in steps:
                errs.append(f"{at}: '{k}' goes to a step that does not exist ('{s[k]}')")
        if st == "find_rows":
            if s.get("provider"):
                if s["provider"] not in providers.PROVIDERS:
                    errs.append(f"{at}: the provider '{s['provider']}' is not known to this server")
            elif s.get("sheet"):
                if not isinstance(s["sheet"], str):
                    errs.append(f"{at}: say which warehouse sheet")
            elif s.get("data") not in t.data:
                errs.append(f"{at}: choose where the rows come from (Core data, a warehouse sheet or one of the tool's tables)")
        if st in ("add_row", "update_row") and s.get("data") not in t.data:
            errs.append(f"{at}: choose which of the tool's tables to write to")
        tcols = {c.get("key"): c for c in ((t.data.get(s.get("data")) or {}).get("columns") or []) if isinstance(c, dict)}
        if st in ("add_row", "update_row"):
            for k, v in (s.get("values") or {}).items():
                c = tcols.get(k)
                if c and c.get("type") == "file":
                    errs.append(f"{at}: '{k}' is a file column — a flow cannot put a file on a row")
                elif c and c.get("type") == "choice" and isinstance(v, str) and "{{" not in v and v and v.lower() not in [str(o).lower() for o in c.get("options") or []]:
                    errs.append(f"{at}: '{v}' is not one of the choices of '{k}' ({', '.join(map(str, c.get('options') or []))})")
        if st == "find_rows" and not s.get("provider"):
            for c in s.get("where") or []:
                col = tcols.get((c or {}).get("field")) if isinstance(c, dict) else None
                v = (c or {}).get("value") if isinstance(c, dict) else None
                if col and col.get("type") == "choice" and c.get("op") in ("equals", "not_equals") and isinstance(v, str) and v and "{{" not in v and v.lower() not in [str(o).lower() for o in col.get("options") or []]:
                    errs.append(f"{at}: '{v}' is not one of the choices of '{c.get('field')}' ({', '.join(map(str, col.get('options') or []))})")
        if st == "update_row" and not s.get("row"):
            errs.append(f"{at}: say which row (its id, e.g. {{{{trigger.row.id}}}})")
        if st == "for_each" and not s.get("items"):
            errs.append(f"{at}: say which list to go through (e.g. docs, from a Find rows step)")
        if st == "for_each" and not s.get("body"):
            errs.append(f"{at}: wire the 'each' port to the step that runs for every item")
        if st == "if" and not (s.get("when") or []):
            errs.append(f"{at}: an If needs at least one condition")
        for c in (s.get("when") or []) + (s.get("where") or []):
            if isinstance(c, dict) and c.get("op") not in OPS:
                errs.append(f"{at}: '{c.get('op')}' is not a comparison ({', '.join(OPS)})")
        if st == "email" and not (s.get("to") and s.get("subject")):
            errs.append(f"{at}: an email needs who it goes to and a subject")
        if st in ("notify", "approval") and not (s.get("who") or {}).get("kind"):
            errs.append(f"{at}: say who (the person who started it, the owner, a role, or people by email)")
        if st == "approval" and not s.get("yes"):
            errs.append(f"{at}: wire the 'approved' port to what happens next")
        if st == "wait" and not any(int(s.get(k) or 0) > 0 for k in ("minutes", "hours", "days")):
            errs.append(f"{at}: wait how long? (minutes, hours or days)")
        if st == "api" and not s.get("connection"):
            errs.append(f"{at}: choose an API connection")
        if st == "require" and not (s.get("when") or []):
            errs.append(f"{at}: say what must hold (at least one condition)")
        if st == "action":
            from . import actions as A

            errs.extend(A.check_step(t, at, s))
        if st == "make_doc":
            from . import worddoc

            errs.extend(worddoc.check_step(t, at, s))
    # a loop that is not a For each: follow next/yes/no from each step without passing a for_each body
    for sid in steps:
        seen, stack = set(), [(sid, [])]
        while stack:
            cur, path = stack.pop()
            s = steps.get(cur) or {}
            for k in ("next", "yes", "no"):
                nx = s.get(k)
                if not nx or nx not in steps:
                    continue
                if nx == sid:
                    errs.append(f"{where}: step {sid} leads back to itself — use For each to repeat")
                    stack = []
                    break
                if nx not in seen:
                    seen.add(nx)
                    stack.append((nx, path + [nx]))
    return sorted(set(errs), key=errs.index), used


# ---- values -------------------------------------------------------------------------------------
def _dig(ctx: Any, path: str) -> Any:
    cur = ctx
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            i = int(part)
            cur = cur[i] if i < len(cur) else None
        elif isinstance(cur, list) and part == "count":
            cur = len(cur)
        else:
            return None
        if cur is None:
            return None
    return cur


def render(tpl: Any, ctx: dict) -> Any:
    """Fill `{{ }}`. One template alone keeps its value's type; mixed text becomes a string."""
    if isinstance(tpl, dict):
        return {k: render(v, ctx) for k, v in tpl.items()}
    if isinstance(tpl, list):
        return [render(v, ctx) for v in tpl]
    if not isinstance(tpl, str):
        return tpl
    m = TPL.fullmatch(tpl.strip())
    if m:
        return _dig(ctx, m.group(1))
    return TPL.sub(lambda mm: "" if _dig(ctx, mm.group(1)) is None else _text(_dig(ctx, mm.group(1))), tpl)


def _text(v: Any) -> str:
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, default=str)[:2000]
    return str(v)


def _num(v: Any) -> Optional[float]:
    try:
        return float(str(v).replace(" ", "").replace(",", ""))
    except (TypeError, ValueError):
        return None


def _date(v: Any) -> Optional[date]:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def holds(c: dict, ctx: dict, row: Optional[dict] = None) -> bool:
    """One condition. `field` (a row's column, for Find rows) or `left` (a template), `op`, `value`."""
    left = (row or {}).get(c["field"]) if row is not None and c.get("field") else render(c.get("left", ""), ctx)
    right = render(c.get("value", ""), ctx)
    op = c.get("op", "equals")
    if c.get("skip_empty") and op not in ("empty", "not_empty") and right in (None, "", [], {}):
        return True  # "only when it has a value": an empty value does not narrow anything (a goal of any size)
    if op == "empty":
        return left in (None, "", [], {})
    if op == "not_empty":
        return left not in (None, "", [], {})
    if op in ("this_week", "this_month", "this_year"):
        d, t0 = _date(left), local_today()
        if d is None:
            return False
        if op == "this_week":
            return d.isocalendar()[:2] == t0.isocalendar()[:2]
        return d.year == t0.year and (op == "this_year" or d.month == t0.month)
    if op in ("days_from_today", "within_days", "past"):
        d = _date(left)
        if d is None:
            return False
        days = (d - local_today()).days
        if op == "past":
            return days < 0
        n = int(_num(right) or 0)
        return days == n if op == "days_from_today" else 0 <= days <= n
    if op in ("contains", "not_contains"):
        has = str(right).lower() in (str(left).lower() if not isinstance(left, list) else " ".join(map(str, left)).lower())
        return has if op == "contains" else not has
    a, b = _num(left), _num(right)
    if a is None or b is None:
        da, db_ = _date(left), _date(right)
        if da and db_:
            a, b = da.toordinal(), db_.toordinal()
    if a is not None and b is not None:
        return {"equals": a == b, "not_equals": a != b, "gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op]
    sa, sb = ("" if left is None else str(left)).strip().lower(), ("" if right is None else str(right)).strip().lower()
    return {"equals": sa == sb, "not_equals": sa != sb, "gt": sa > sb, "gte": sa >= sb, "lt": sa < sb, "lte": sa <= sb}[op]


# ---- who --------------------------------------------------------------------------------------------
def owner_of(db: Session, t) -> Optional[User]:
    email = (t.manifest.get("owner") or "").strip().lower()
    if not email:
        return None
    u = db.query(User).filter(User.email == email).one_or_none()
    return u if u is not None and u.is_active and not u.is_axl else None


def people(db: Session, who: dict, ctx: dict, run: FlowRun, t) -> list[User]:
    """`who`: {kind: starter | owner | roles | people, roles: [...], emails: "a@x, {{doc.email}}"}."""
    from ..security.roles import attach

    kind = (who or {}).get("kind")
    out: list[User] = []
    if kind == "starter" and run.started_by_id:
        u = db.get(User, run.started_by_id)
        out = [u] if u else []
    elif kind == "owner":
        u = owner_of(db, t)
        out = [u] if u else []
    elif kind == "roles":
        roles = [r for r in (who.get("roles") or []) if isinstance(r, str)]
        out = db.query(User).filter(User.role.in_(roles or ["-"]), User.is_active.is_(True), User.is_axl.is_(False)).all()
    elif kind == "people":
        emails = [e.strip().lower() for e in re.split(r"[,;\s]+", _text(render(who.get("emails", ""), ctx)) or "") if "@" in e]
        out = db.query(User).filter(User.email.in_(emails or ["-"]), User.is_active.is_(True), User.is_axl.is_(False)).all() if emails else []
    elif kind in ("person", "manager_of"):
        # a person named in the row ({{trigger.row.assignee}}), or that person's team lead (1.0.55)
        from ..models import Team

        try:
            uid = int(_num(render(who.get("person", ""), ctx)) or 0)
        except (TypeError, ValueError):
            uid = 0
        u = db.get(User, uid) if uid else None
        if kind == "person":
            out = [u] if u and u.is_active and not u.is_axl else []
        elif u is not None and u.team_id:
            team = db.get(Team, u.team_id)
            lead = db.get(User, team.lead_id) if team and team.lead_id else None
            out = [lead] if lead and lead.is_active else [x for x in db.query(User).filter(User.team_id == u.team_id, User.is_active.is_(True), User.is_axl.is_(False)).all() if x.is_manager][:3]
    for u in out:
        attach(db, u)
    return [u for u in out if u is not None][:50]


# ---- running ------------------------------------------------------------------------------------
def _note(run: FlowRun, sid: str, typ: str, ok: bool, note: str) -> None:
    run.log = [*(run.log or []), {"step": sid, "type": typ, "ok": ok, "note": note[:600], "at": utcnow().isoformat()}][-400:]


def _trim(ctx: dict) -> dict:
    s = json.dumps(ctx, default=str, ensure_ascii=False)
    if len(s) <= CTX_LIMIT:
        return json.loads(s)
    out = {}
    for k, v in ctx.items():
        vs = json.dumps(v, default=str, ensure_ascii=False)
        out[k] = v if len(vs) < CTX_LIMIT // 20 else f"<{len(vs):,} bytes, not kept>"
    return json.loads(json.dumps(out, default=str))


def base_ctx(db: Session, t, run: FlowRun, trigger: dict) -> dict:
    run_as = db.get(User, run.run_as_id) if run.run_as_id else None
    starter = db.get(User, run.started_by_id) if run.started_by_id else None
    who = lambda u: {"id": u.id, "name": u.name, "email": u.email, "role": u.role} if u else None  # noqa: E731
    return {"today": local_today().isoformat(), "now": local_now().isoformat(timespec="minutes"), "tool": t.slug, "run": run.id,
            "trigger": trigger, "person": who(run_as), "starter": who(starter)}


def start(db: Session, t, name: str, *, trigger: str, starter: Optional[User], data: Optional[dict] = None, dry: bool = False, flow: Optional[dict] = None) -> FlowRun:
    """Start a run and take it as far as it goes (the end, or a pause). Never raises for a step's
    failure — the run records it. Raises FlowError only when the run cannot begin at all."""
    f = flow if flow is not None else (t.flows or {}).get(name)
    if not f:
        raise FlowError(f"The flow '{name}' is not in {t.slug}.")
    if not dry and flow is None and not f.get("enabled", True):
        raise FlowError("This flow is switched off.")
    mode = "owner" if (f.get("trigger") or {}).get("type") == "schedule" else f.get("run_as", "starter")
    run_as = owner_of(db, t) if mode == "owner" else starter
    owner_stand_in = False
    if run_as is None and dry and starter is not None:
        run_as, owner_stand_in = starter, True  # a test on a copy where the owner has no account
    if run_as is None:
        raise FlowError("The tool's owner is not an active account here — set Owner on the Tool block." if mode == "owner" else "Nobody to run it as.")
    from ..security.roles import attach

    attach(db, run_as)
    run = FlowRun(tool=t.slug, flow=name, trigger=trigger, status="running", dry=dry, started_by_id=starter.id if starter else None, run_as_id=run_as.id)
    db.add(run)
    db.flush()
    run.ctx = base_ctx(db, t, run, {"kind": trigger, **(data or {})})
    if owner_stand_in:
        _note(run, "", "test", True, f"the tool's owner ({t.manifest.get('owner') or 'none set'}) has no account here — the test runs as {starter.name}")
    if not dry:
        log(db, "flow_started", actor=starter or run_as, target=f"{t.slug}/{name}", detail=f"run {run.id} · {trigger} · as {run_as.email}")
    _advance(db, t, f, run, f.get("start"))
    return run


def resume(db: Session, t, run: FlowRun, node: Optional[str], *, extra: Optional[dict] = None) -> FlowRun:
    f = (t.flows or {}).get(run.flow)
    if not f:
        run.status, run.note, run.finished_at = "failed", f"The flow '{run.flow}' is no longer in the tool.", utcnow()
        return run
    run.status, run.waiting, run.resume_at = "running", {}, None
    ctx = dict(run.ctx or {})
    ctx.update(extra or {})
    ctx["today"], ctx["now"] = local_today().isoformat(), local_now().isoformat(timespec="minutes")
    run.ctx = ctx
    _advance(db, t, f, run, node)
    return run


def _advance(db: Session, t, f: dict, run: FlowRun, node: Optional[str]) -> None:
    steps = f.get("steps") or {}
    ctx = dict(run.ctx or {})
    stack = list(run.stack or [])
    run_as = db.get(User, run.run_as_id)
    from ..security.roles import attach

    attach(db, run_as)
    try:
        while True:
            if not node:
                if stack:
                    fr = stack[-1]
                    fr["i"] += 1
                    if fr["i"] < len(fr["items"]):
                        ctx[fr["as"]] = fr["items"][fr["i"]]
                        ctx["index"] = fr["i"]
                        node = fr["body"]
                        continue
                    stack.pop()
                    node = fr["next"]
                    continue
                run.status = "done"
                break
            s = steps.get(node)
            if s is None:
                raise FlowError(f"Step '{node}' is missing.")
            run.steps_taken = (run.steps_taken or 0) + 1
            if run.steps_taken > MAX_STEPS:
                raise FlowError(f"Stopped after {MAX_STEPS} steps — a For each over too many items, or a flow that never ends.")
            typ = s.get("type")
            if typ == "for_each":
                items = render(s.get("items") if "{{" in str(s.get("items")) else "{{" + str(s.get("items")) + "}}", ctx)
                items = items if isinstance(items, list) else ([] if items in (None, "") else [items])
                if len(items) > MAX_ITEMS:
                    _note(run, node, typ, True, f"{len(items)} items — only the first {MAX_ITEMS} are done")
                    items = items[:MAX_ITEMS]
                _note(run, node, typ, True, f"{len(items)} item{'s' if len(items) != 1 else ''}")
                if not items or not s.get("body"):
                    node = s.get("next")
                    continue
                stack.append({"items": items, "i": 0, "as": s.get("as") or "item", "body": s["body"], "next": s.get("next")})
                ctx[s.get("as") or "item"] = items[0]
                ctx["index"] = 0
                node = s["body"]
                continue
            node = _step(db, t, run, run_as, node, s, ctx)
    except Pause:
        run.status = "waiting"
    except Stopped:
        run.status, stack = "done", []
    except FlowError as e:
        run.status, run.note = "failed", str(e)
        _note(run, node or "", (steps.get(node) or {}).get("type", ""), False, str(e))
    except Exception as e:  # noqa: BLE001 — a bug in a step must not take the page down
        run.status, run.note = "failed", f"{type(e).__name__}: {e}"[:500]
        _note(run, node or "", (steps.get(node) or {}).get("type", ""), False, run.note)
    run.ctx, run.stack, run.updated_at = _trim(ctx), stack, utcnow()
    if run.status in ("done", "failed", "stopped"):
        run.finished_at = utcnow()
        if not run.dry:
            log(db, f"flow_{run.status}", actor=run_as, target=f"{t.slug}/{run.flow}", detail=f"run {run.id} · {run.steps_taken} steps{' · ' + run.note if run.note else ''}"[:400],
                outcome="ok" if run.status == "done" else "failed")
    db.flush()


def _step(db: Session, t, run: FlowRun, me: User, sid: str, s: dict, ctx: dict) -> Optional[str]:
    """Do one step; return the next step's id (None at the end of a chain)."""
    from ..engine.store import Store, StoreError
    from . import providers

    typ = s["type"]
    dry = run.dry
    if typ == "find_rows":
        if s.get("provider"):
            prov = providers.PROVIDERS.get(s["provider"])
            if prov is None:
                raise FlowError(f"Unknown provider {s['provider']}.")
            if prov.roles and me.role not in prov.roles and not (me.is_admin and set(prov.roles) & {"owner", "admin"}):
                raise FlowError(f"{me.name} may not read {prov.title}.")
            data = prov.fn(db, me, {k: _text(render(v, ctx)) for k, v in (s.get("params") or {}).items()})
            rows = data.get("rows") or []
            src = prov.title
        elif s.get("sheet"):
            from . import sheets

            got = sheets.rows_for(db, me, s["sheet"])
            if got.get("note"):
                raise FlowError(got["note"])
            rows, src = got["rows"], got.get("source") or s["sheet"]
        else:
            b = t.data.get(s.get("data"))
            if b is None:
                raise FlowError(f"The tool has no table '{s.get('data')}'.")
            rows = Store(t.slug).rows(s["data"], b.get("columns") or [], limit=2000)["rows"]
            src = s["data"]
        conds = [c for c in (s.get("where") or []) if isinstance(c, dict)]
        rows = [r for r in rows if all(holds(c, ctx, r) for c in conds)][: int(s.get("limit") or 500)]
        name = s.get("as") or "rows"
        ctx[name] = rows
        ctx[name + "_count"] = len(rows)
        _note(run, sid, typ, True, f"{len(rows)} row{'s' if len(rows) != 1 else ''} from {src} → {name}")
        return s.get("next")
    if typ in ("add_row", "update_row"):
        b = t.data.get(s.get("data"))
        if b is None:
            raise FlowError(f"The tool has no table '{s.get('data')}'.")
        values = {k: render(v, ctx) for k, v in (s.get("values") or {}).items()}
        name = s.get("as") or "row"
        if dry:
            # a dry run still checks each value against its column: a choice that is not one of the
            # options, a link that is not a number, a file (which a flow cannot write) fail here
            for c in b.get("columns") or []:
                if c.get("key") in values and not (isinstance(values[c["key"]], str) and "{{" in values[c["key"]]):
                    try:
                        Store.coerce({**c, "required": False}, values[c["key"]])
                    except StoreError as e:
                        raise FlowError(str(e))
            ctx[name] = {"id": 0, **values}
            _note(run, sid, typ, True, f"would {'add a row to' if typ == 'add_row' else 'change a row in'} {s['data']}: {_text(values)[:300]}")
            return s.get("next")
        st = Store(t.slug)
        cols = b.get("columns") or []
        try:
            if typ == "add_row":
                row = st.insert(s["data"], cols, values, me.id, unique=b.get("unique"))
            else:
                rid = int(_num(render(s.get("row"), ctx)) or 0)
                row = st.update(s["data"], cols, rid, values, unique=b.get("unique"))
                if row is None:
                    raise FlowError(f"Row {rid} is not in {s['data']}.")
        except StoreError as e:
            raise FlowError(str(e))
        ctx[name] = row
        _note(run, sid, typ, True, f"{'added row' if typ == 'add_row' else 'changed row'} {row.get('id')} in {s['data']}")
        return s.get("next")
    if typ == "set":
        name = s.get("name") or "value"
        ctx[name] = render(s.get("value", ""), ctx)
        _note(run, sid, typ, True, f"{name} = {_text(ctx[name])[:200]}")
        return s.get("next")
    if typ == "if":
        conds = [c for c in (s.get("when") or []) if isinstance(c, dict)]
        any_ = s.get("match") == "any"
        ok = any(holds(c, ctx) for c in conds) if any_ else all(holds(c, ctx) for c in conds)
        _note(run, sid, typ, True, "yes" if ok else "no")
        return s.get("yes") if ok else s.get("no")
    if typ == "notify":
        to = people(db, s.get("who") or {}, ctx, run, t)
        title = _text(render(s.get("title") or "From a flow", ctx))[:160]
        text = _text(render(s.get("text") or "", ctx))[:4000]
        link = _text(render(s.get("link") or "#/runs", ctx))[:300]
        if dry:
            _note(run, sid, typ, True, f"would tell {', '.join(u.name for u in to) or 'nobody (no one matched)'}: {title}")
            return s.get("next")
        from ..requests_flow import notify

        for u in to:
            notify(db, u, title, text, link=link, kind="flow")
        _note(run, sid, typ, True, f"told {len(to)}: {', '.join(u.name for u in to)[:300]}" if to else "nobody matched — nothing sent")
        return s.get("next")
    if typ == "email":
        from .. import mailbox

        to = [a.strip() for a in re.split(r"[,;\s]+", _text(render(s.get("to", ""), ctx)) or "") if "@" in a]
        subject = " ".join(_text(render(s.get("subject", ""), ctx)).split())[:200]
        body = _text(render(s.get("text", ""), ctx))
        if not to:
            _note(run, sid, typ, True, "no address — nothing sent")
            return s.get("next")
        refused = [(a, why) for a in to for ok, why in [mailbox.allowed(db, a)] if not ok]
        if refused:
            raise FlowError(f"Refused {refused[0][0]}: {refused[0][1]}")
        if dry:
            _note(run, sid, typ, True, f"would email {', '.join(to)}: {subject}")
            return s.get("next")
        r = mailbox.send(db, to=to, subject=subject, body=body + "\n" + mailbox.footer(db, me), kind="flow", ref=f"{t.slug}/{run.flow}/{run.id}", by=me, hold="flow")
        _note(run, sid, typ, bool(r.ok), f"emailed {', '.join(to)}" if r.ok else f"not sent to {', '.join(to)} — {r.detail}")
        if not r.ok and s.get("must_send"):
            raise FlowError(f"The email did not go: {r.detail}")
        return s.get("next")
    if typ == "approval":
        who = people(db, s.get("who") or {}, ctx, run, t)
        question = _text(render(s.get("question") or "May this go ahead?", ctx))[:1000]
        if dry:
            ctx["approval"] = {"decision": "approved", "by": "(test)", "note": ""}
            _note(run, sid, typ, True, f"would ask {', '.join(u.name for u in who) or 'nobody (no one matched)'}: {question} — the test goes on as if approved")
            return s.get("yes")
        if not who:
            raise FlowError("Nobody to ask for approval — check who the step asks.")
        if (s.get("who") or {}).get("kind") in ("person", "manager_of") and run.started_by_id and [u.id for u in who] == [run.started_by_id]:
            # a lead is not asked about their own team's people: the one who would be asked (found from the
            # row) is the one who started it. Asking the owner, a role or named people always asks.
            ctx["approval"] = {"decision": "approved", "by": who[0].name, "note": "started it themselves"}
            _note(run, sid, typ, True, f"{who[0].name} started it and is the one to ask — approved")
            return s.get("yes")
        ctx["_asked"] = sorted(set((ctx.get("_asked") or []) + [u.id for u in who]))  # they may see this run for good
        run.waiting = {"kind": "approval", "step": sid, "approvers": [u.id for u in who], "question": question, "yes": s.get("yes"), "no": s.get("no"), "asked_at": utcnow().isoformat()}
        from ..requests_flow import notify

        for u in who:
            notify(db, u, "Approval needed", f"{question}\n\n{t.text(t.manifest.get('title', 'tool.title'))} · {t.text((t.flows or {}).get(run.flow, {}).get('title') or run.flow)}", link=f"#/runs?id={run.id}", kind="needs_you")
        _note(run, sid, typ, True, f"asked {', '.join(u.name for u in who)}: {question}")
        raise Pause()
    if typ == "wait":
        span = timedelta(minutes=int(s.get("minutes") or 0), hours=int(s.get("hours") or 0), days=int(s.get("days") or 0))
        if dry:
            _note(run, sid, typ, True, f"would wait {span} — the test goes straight on")
            return s.get("next")
        run.waiting = {"kind": "wait", "step": sid, "until": (utcnow() + span).isoformat()}
        run.resume_at = utcnow() + span
        run.resume_node = s.get("next") or ""
        _note(run, sid, typ, True, f"waiting until {(local_now() + span).strftime('%d %b %H:%M')}")
        raise Pause()
    if typ == "api":
        from ..connections import service as C
        from ..models import ApiConnection

        c = db.query(ApiConnection).filter(ApiConnection.slug == str(s.get("connection"))).one_or_none()
        if c is None:
            raise FlowError(f"API connection '{s.get('connection')}' does not exist.")
        if t.slug not in (c.tools or []):
            raise FlowError(f"The connection '{c.name}' is not open to this tool — tick {t.slug} under Admin → API connections.")
        method = str(s.get("method") or "GET").upper()
        path = _text(render(s.get("path") or "", ctx))
        body = render(s.get("payload"), ctx) if s.get("payload") not in (None, "") else None
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except ValueError:
                pass
        name = s.get("as") or "answer"
        if dry and method != "GET":
            ctx[name] = {}
            _note(run, sid, typ, True, f"would call {c.name}: {method} {path}")
            return s.get("next")
        try:
            r = C.call(db, c, method=method, path=path, json_body=body if isinstance(body, (dict, list)) else None, body=body if isinstance(body, str) else None, by=me, by_label=f"flow:{t.slug}/{run.flow}")
        except C.ConnectionError_ as e:
            raise FlowError(str(e))
        ctx[name] = r.get("json") if r.get("json") is not None else r.get("text")
        _note(run, sid, typ, bool(r.get("ok")), f"{c.name}: {method} {path} → {r.get('status')}")
        if not r.get("ok") and s.get("must_succeed", True):
            raise FlowError(f"{c.name} answered {r.get('status')}.")
        return s.get("next")
    if typ == "action":
        from . import actions as A

        A.load()
        a = A.ACTIONS.get(str(s.get("action") or ""))
        if a is None:
            raise FlowError(f"The job '{s.get('action')}' is not known to this server.")
        why = A.may_run(db, me, a)
        if why:
            raise FlowError(why)
        if dry:
            try:
                A.run_now(db, t, s, ctx, me, dry=True)
            except A.ActionError as e:
                raise FlowError(str(e))
            ctx[s.get("as") or "job"] = {}
            _note(run, sid, typ, True, f"would run {a.title}{' (in the background)' if a.slow else ''}")
            return s.get("next")
        if a.slow:
            # the work happens in the background; the run waits and carries on when it is done
            run.waiting = {"kind": "working", "step": sid, "since": utcnow().isoformat(), "question": f"{a.title} — working on it"}
            _note(run, sid, typ, True, f"{a.title}: started in the background")
            A.submit_after_commit(db, run.id)
            raise Pause()
        try:
            got = A.run_now(db, t, s, ctx, me)
        except A.ActionError as e:
            A.write_failure(db, t, s, ctx, str(e))
            raise FlowError(str(e))
        ctx[s.get("as") or "job"] = got
        _note(run, sid, typ, True, f"{a.title}: done")
        return s.get("next")
    if typ == "require":
        # "stop unless …": the rule ends here, failed, with its sentence — the person sees why
        conds = [c for c in (s.get("when") or []) if isinstance(c, dict)]
        ok = (any if s.get("match") == "any" else all)(holds(c, ctx) for c in conds) if conds else True
        msg = _text(render(s.get("message") or "", ctx))[:500] or "A condition of this rule does not hold."
        if ok:
            _note(run, sid, typ, True, "holds — carrying on")
            return s.get("next")
        if dry:
            _note(run, sid, typ, True, f"would stop here: {msg} — the test goes on")
            return s.get("next")
        raise FlowError(msg)
    if typ == "make_doc":
        from . import worddoc

        try:
            name = worddoc.run_step(db, t, s, ctx, me, dry=dry)
        except worddoc.DocError as e:
            raise FlowError(str(e))
        ctx[s.get("as") or "doc"] = {"name": name}
        _note(run, sid, typ, True, f"{'would make' if dry else 'made'} {name}")
        return s.get("next")
    if typ == "stop":
        msg = _text(render(s.get("message") or "", ctx))[:500]
        outcome = s.get("outcome", "done")
        _note(run, sid, typ, outcome != "failed", msg or ("done" if outcome != "failed" else "failed"))
        if outcome == "failed":
            raise FlowError(msg or "Stopped as failed.")
        run.note = msg
        raise Stopped()
    raise FlowError(f"Unknown step type {typ}.")


def decide(db: Session, t, run: FlowRun, by: User, approve: bool, note: str = "") -> FlowRun:
    w = run.waiting or {}
    if run.status != "waiting" or w.get("kind") != "approval":
        raise FlowError("This run is not waiting for an approval.")
    if by.id not in (w.get("approvers") or []) and not by.is_owner:
        raise FlowError("You are not one of the people this approval was asked of.")
    decision = "approved" if approve else "declined"
    _note(run, w.get("step", ""), "approval", True, f"{decision} by {by.name}{': ' + note if note else ''}")
    log(db, f"flow_{decision}", actor=by, target=f"{t.slug}/{run.flow}", detail=f"run {run.id}{': ' + note[:200] if note else ''}")
    return resume(db, t, run, w.get("yes") if approve else w.get("no"), extra={"approval": {"decision": decision, "by": by.name, "note": note[:500]}})


# ---- triggers the app fires -----------------------------------------------------------------------
def on_row(db: Session, t, block: str, kind: str, row: dict, by: User) -> list[int]:
    """A person added or changed a row: start every enabled flow that watches it. Never raises."""
    ids = []
    for name, f in (t.flows or {}).items():
        tr = f.get("trigger") or {}
        if tr.get("type") == kind and tr.get("data") == block and f.get("enabled", True):
            try:
                ids.append(start(db, t, name, trigger=kind, starter=by, data={"row": row, "table": block}).id)
            except FlowError as e:
                log(db, "flow_not_started", actor=by, target=f"{t.slug}/{name}", detail=str(e), outcome="failed")
    return ids


def due_slot(tr: dict, now: datetime) -> Optional[datetime]:
    """The most recent moment (local time) this schedule should have fired at, on or before `now`."""
    every = tr.get("every")
    hh, mm = (int(x) for x in str(tr.get("at") or "07:00").split(":")) if every != "hour" else (0, int(tr.get("minute") or 0))
    if every == "hour":
        slot = now.replace(minute=mm, second=0, microsecond=0)
        return slot if slot <= now else slot - timedelta(hours=1)
    base = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    for back in range(0, 40):
        d = base - timedelta(days=back)
        if d > now:
            continue
        wd = DAYS[d.weekday()]
        if every == "day" and (not tr.get("days") or wd in tr["days"]):
            return d
        if every == "weekday" and d.weekday() < 5:
            return d
        if every == "week" and wd == tr.get("day"):
            return d
        if every == "month" and d.day == int(tr.get("day") or 1):
            return d
    return None


WINDOW = timedelta(minutes=10)  # a slot missed by more than this (the server was off) is skipped, not caught up


def tick(db: Session, now_local: Optional[datetime] = None) -> dict:
    """Once a minute: start the scheduled flows that are due, and wake the runs whose wait is over."""
    from . import loader

    out = {"started": [], "resumed": []}
    if not settings.flows_schedule:
        return out
    now_local = now_local or local_now()
    reg = loader.registry()
    for t in reg.tools.values():
        for name, f in (t.flows or {}).items():
            tr = f.get("trigger") or {}
            if tr.get("type") != "schedule" or not f.get("enabled", True):
                continue
            slot = due_slot(tr, now_local)
            if slot is None or now_local - slot > WINDOW:
                continue
            slot_utc = slot.astimezone(timezone.utc)
            done = db.query(FlowRun).filter(FlowRun.tool == t.slug, FlowRun.flow == name, FlowRun.trigger == "schedule", FlowRun.dry.is_(False), FlowRun.started_at >= slot_utc).count()
            if done:
                continue
            try:
                r = start(db, t, name, trigger="schedule", starter=None, data={"slot": slot.isoformat(timespec="minutes")})
                out["started"].append(r.id)
            except FlowError as e:
                log(db, "flow_not_started", actor_label="scheduler", target=f"{t.slug}/{name}", detail=str(e), outcome="failed")
    from . import actions as A

    A.sweep(db)  # background jobs a restart left behind
    for run in db.query(FlowRun).filter(FlowRun.status == "waiting", FlowRun.resume_at.isnot(None), FlowRun.resume_at <= utcnow()).limit(50).all():
        t = reg.tools.get(run.tool)
        if t is None:
            continue
        node = run.resume_node or None
        run.resume_node = ""
        resume(db, t, run, node)
        out["resumed"].append(run.id)
    return out


def run_out(db: Session, run: FlowRun, me: Optional[User] = None, *, full: bool = False) -> dict:
    from . import loader

    t = loader.registry().tools.get(run.tool)
    by = db.get(User, run.started_by_id) if run.started_by_id else None
    as_ = db.get(User, run.run_as_id) if run.run_as_id else None
    w = run.waiting or {}
    d = {"id": run.id, "tool": run.tool, "tool_title": t.text(t.manifest.get("title", "tool.title")) if t else run.tool,
         "flow": run.flow, "flow_title": t.text((t.flows or {}).get(run.flow, {}).get("title") or run.flow) if t else run.flow,
         "trigger": run.trigger, "status": run.status, "dry": run.dry, "steps": run.steps_taken, "note": run.note,
         "started_at": run.started_at.isoformat() if run.started_at else None, "finished_at": run.finished_at.isoformat() if run.finished_at else None,
         "by": by.name if by else ("schedule" if run.trigger == "schedule" else ""), "as": as_.name if as_ else "",
         "waiting": {"kind": w.get("kind"), "question": w.get("question", ""), "until": w.get("until")} if w else None,
         "can_decide": bool(me and w.get("kind") == "approval" and run.status == "waiting" and (me.id in (w.get("approvers") or []) or me.is_owner))}
    if full:
        d["log"] = run.log or []
    return d


from ..jobs import routine  # noqa: E402


@routine("engine-flows", 60)
def flows_routine() -> None:
    from ..db import session_scope

    with session_scope() as db:
        tick(db)
