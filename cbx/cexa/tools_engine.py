"""C-EXA's hands on the tools built in the Builder (1.0.61).

Before this she could inspect PDF Studio and call raw API paths, but she could not see a Builder tool,
read its rows, give someone a task, or add a filter — the things people actually ask her for.

Two kinds of hands, with the same limits a person has:

  rows   — find, add and change rows in a tool's tables (a task, a site, a goal). Done through the app's
           own API as the person she is talking to, so their access, the table's "who may add rows", the
           checks on every column and the tool's rules all apply exactly as if they had clicked. Anyone
           may ask for these; they can never do more through her than they can themselves.
  draft  — change how a tool looks and works (a filter, a column, a rule on or off). Admins only, and
           only ever in the tool's Builder draft: nothing reaches people until a person presses Release
           and then Promote. She never releases.
"""
from __future__ import annotations

import json
import re
import secrets
import shutil
from datetime import date, timedelta
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..audit import log
from ..models import Fix, User
from .tools import _cexa, tool

ROW_TOOLS = ("list_tools", "describe_tool", "find_rows", "add_row", "update_row", "give_task", "my_tasks", "waiting_for_me")
TYPE_WORDS = {"text": "text", "words": "text", "number": "number", "amount": "number", "money": "money", "rand": "money", "integer": "int", "whole number": "int",
              "date": "date", "day": "date", "yes/no": "bool", "yes no": "bool", "tick": "bool", "checkbox": "bool", "bool": "bool",
              "choice": "choice", "pick-list": "choice", "picklist": "choice", "dropdown": "choice", "list": "choice", "options": "choice",
              "person": "person", "people": "person", "who": "person", "checklist": "checklist", "steps": "checklist"}


# ---------------------------------------------------------------- finding things ----------------
def _words(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (s or "").lower())) - {"the", "a", "my", "tool", "table", "page", "of", "in", "list"}


def _best(ref: str, items: list[tuple[str, list[str]]]) -> Optional[str]:
    """The item whose names best match what a person typed: exact name, then most shared words."""
    r = (ref or "").strip().lower()
    if not r:
        return None
    for key, names in items:
        if any(r == n.lower() for n in names if n):
            return key
    want = _words(r)
    best, score = None, 0
    for key, names in items:
        s = max((len(want & _words(n)) * 10 + (3 if r in n.lower() else 0) for n in names if n), default=0)
        if s > score:
            best, score = key, s
    return best


def _usable(db: Session, me: User):
    from ..api.engine_api import can_use
    from ..engine import loader

    return [t for t in loader.registry().tools.values() if can_use(db, me, t)]


def _tool_for(db: Session, me: User, ref: str):
    ts = _usable(db, me)
    key = _best(ref, [(t.slug, [t.slug, t.text(t.manifest.get("title", ""), "en")]) for t in ts])
    if key is None and not (ref or "").strip() and len(ts) == 1:
        key = ts[0].slug
    return next((t for t in ts if t.slug == key), None)


def _table_for(t, ref: str) -> Optional[str]:
    items = []
    for name, d in (t.data or {}).items():
        names = [name, t.text(d.get("title") or "", "en")]
        for pn, p in t.pages.items():  # what the table is called on its pages ("Team board", "My list")
            from ..engine.loader import widgets_of

            for w in widgets_of(pn, p):
                if (w.get("source") or {}).get("data") == name:
                    names += [t.text(w.get("title") or "", "en"), t.text(p.get("title") or "", "en")]
        items.append((name, names))
    got = _best(ref, items)
    if got is None and len(t.data or {}) == 1:
        got = next(iter(t.data))
    return got


def _col_for(t, b: dict, ref: str) -> Optional[dict]:
    cols = [c for c in b.get("columns") or [] if isinstance(c, dict)]
    key = _best(ref, [(c["key"], [c["key"], c["key"].replace("_", " "), t.text(c.get("label") or "", "en")]) for c in cols])
    return next((c for c in cols if c["key"] == key), None)


def _label(t, c: dict) -> str:
    raw = c.get("label") or ""
    lab = t.text(raw, "en") if raw else ""
    return lab if lab and not (lab == raw and re.match(r"^[a-z0-9_]+(\.[a-z0-9_]+)+$", raw)) else c["key"].replace("_", " ").capitalize()


def _person(db: Session, me: User, ref: str) -> tuple[Optional[int], str]:
    r = (ref or "").strip()
    if r.lower() in ("me", "myself", "i", "mine") or (r.isdigit() and int(r) == me.id):
        return me.id, me.name
    people = db.query(User).filter(User.is_active.is_(True), User.is_axl.is_(False)).all()
    if r.isdigit():
        u = next((p for p in people if p.id == int(r)), None)
        return (u.id, u.name) if u else (None, f"nobody has id {r}")
    rl = r.lower()
    for test in (lambda p: p.email.lower() == rl, lambda p: p.name.lower() == rl,
                 lambda p: p.name.lower().split()[0] == rl if p.name else False, lambda p: rl in p.name.lower() or rl in p.email.lower()):
        hits = [p for p in people if test(p)]
        if len(hits) == 1:
            return hits[0].id, hits[0].name
        if len(hits) > 1:
            return None, f"'{r}' could be " + ", ".join(f"{p.name} ({p.email})" for p in hits[:5]) + " — which one?"
    return None, f"I can't find an active person called '{r}'."


def _day(v: str) -> Optional[str]:
    s = (v or "").strip().lower()
    today = date.today()
    if s in ("today", "now"):
        return today.isoformat()
    if s == "tomorrow":
        return (today + timedelta(days=1)).isoformat()
    if s in ("next week", "in a week"):
        return (today + timedelta(days=7)).isoformat()
    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    m = re.match(r"^(?:on |by |next )?(" + "|".join(days) + r")$", s)
    if m:
        ahead = (days.index(m.group(1)) - today.weekday()) % 7 or 7
        return (today + timedelta(days=ahead)).isoformat()
    m = re.match(r"^in (\d+) days?$", s)
    if m:
        return (today + timedelta(days=int(m.group(1)))).isoformat()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    m = re.match(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$", s)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
        except ValueError:
            return None
    months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    m = re.match(r"^(\d{1,2}) ([a-z]{3})[a-z]* ?(\d{4})?$", s)
    if m and m.group(2) in months:
        y = int(m.group(3) or today.year)
        try:
            return date(y, months.index(m.group(2)) + 1, int(m.group(1))).isoformat()
        except ValueError:
            return None
    return None


def _pairs(values: str) -> dict:
    """What to put in a row: a JSON object, or "Column: value; Column: value" (also = and new lines)."""
    v = (values or "").strip()
    if v.startswith("{"):
        try:
            d = json.loads(v)
            return {str(k): d[k] for k in d} if isinstance(d, dict) else {}
        except ValueError:
            pass
    out = {}
    for part in re.split(r"[;\n]+", v):
        m = re.match(r"^\s*([^:=]+?)\s*[:=]\s*(.*)$", part)
        if m:
            out[m.group(1).strip()] = m.group(2).strip()
    return out


class Problem(Exception):
    pass


def _values(db: Session, me: User, t, block: str, raw: dict) -> dict:
    """Turn what a person said into what the table takes: names into people, words into options and
    linked rows, "Friday" into a date, "a, b, c" into checklist steps. A word that does not fit says why."""
    b = t.data[block]
    out = {}
    for k, v in raw.items():
        c = _col_for(t, b, k)
        if c is None:
            raise Problem(f"'{k}' is not a column of {block}. Its columns: " + ", ".join(_label(t, x) for x in b.get("columns") or []))
        typ = c.get("type", "text")
        s = "" if v is None else (v if isinstance(v, str) else json.dumps(v) if isinstance(v, (list, dict)) else str(v))
        if typ in ("file", "calc"):
            raise Problem(f"{_label(t, c)} is {'a file — upload it on the page' if typ == 'file' else 'worked out by itself'}; I can't set it.")
        if s.strip() == "":
            out[c["key"]] = None
        elif typ == "person":
            uid, why = _person(db, me, s)
            if uid is None:
                raise Problem(why)
            out[c["key"]] = uid
        elif typ == "choice":
            opts = c.get("options") or []
            hit = [o for o in opts if o.lower() == s.lower()] or [o for o in opts if o.lower().startswith(s.lower())]
            if len(hit) != 1:
                raise Problem(f"{_label(t, c)} takes one of: {', '.join(opts)}.")
            out[c["key"]] = hit[0]
        elif typ == "date":
            d = _day(s)
            if d is None:
                raise Problem(f"I can't read '{s}' as a date for {_label(t, c)} (try 2026-10-09, 9/10/2026, Friday or tomorrow).")
            out[c["key"]] = d
        elif typ == "checklist":
            items = v if isinstance(v, list) else [x.strip() for x in re.split(r"[,\n]+", s) if x.strip()]
            out[c["key"]] = [{"text": str(x.get("text") if isinstance(x, dict) else x)[:200], "done": bool(x.get("done")) if isinstance(x, dict) else False} for x in items][:30]
        elif typ in ("number", "int"):
            n = re.sub(r"[^\d,.\-]", "", s.replace(" ", ""))
            n = n.replace(",", ".") if n.count(",") == 1 and "." not in n else n.replace(",", "")
            try:
                out[c["key"]] = int(float(n)) if typ == "int" else float(n)
            except ValueError:
                raise Problem(f"{_label(t, c)} takes a number; '{s}' isn't one.") from None
        elif typ == "bool":
            out[c["key"]] = s.strip().lower() in ("yes", "y", "true", "1", "ticked", "done", "on")
        elif typ == "link":
            st, res = _as_person(db, me, "GET", f"/api/engine/tools/{t.slug}/data/{block}/choices/{c['key']}", params={"q": s[:80]})
            opts = (res or {}).get("choices") or [] if st == 200 else []
            hit = [o for o in opts if str(o.get("label", "")).lower() == s.lower()] or (opts if len(opts) == 1 else [])
            if len(hit) != 1:
                raise Problem(f"{_label(t, c)} points at a row of {c.get('table')}; " + (f"'{s}' matches {', '.join(str(o.get('label')) for o in opts[:6])} — which one?" if opts else f"none is called '{s}'."))
            out[c["key"]] = hit[0]["id"]
        else:
            out[c["key"]] = s
    return out


def _as_person(db: Session, me: User, method: str, path: str, *, body: Any = None, params: Optional[dict] = None) -> tuple[int, Any]:
    """The app's own API, as the person: a short-lived session of theirs (marked as C-EXA's, so anything
    only a person may do is refused), revoked as soon as the call returns."""
    from fastapi.testclient import TestClient

    from ..main import app
    from ..models import LoginSession
    from ..security.auth import SESSION_COOKIE

    from .tools import AGENT_WORK

    sess = LoginSession(id=("cexa-a-" if AGENT_WORK.get() is not None else "cexa-") + secrets.token_urlsafe(24), user_id=me.id, ip="cexa", agent="C-EXA hands")  # 1.0.114: an agent's work is marked
    db.add(sess)
    db.commit()
    try:
        c = TestClient(app, raise_server_exceptions=False)
        c.cookies.set(SESSION_COOKIE, sess.id)
        r = c.request(method, path, json=body, params=params)
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, {"detail": r.text[:300]}
    finally:
        sess.revoked = True
        db.commit()


def _detail(res: Any) -> str:
    return str((res or {}).get("detail") if isinstance(res, dict) else res)[:300]


def _row_line(t, b: dict, row: dict, keys: Optional[list] = None) -> str:
    labels = row.get("_labels") or {}
    bits = []
    for c in b.get("columns") or []:
        k = c["key"]
        if (keys and k not in keys) or c.get("type") == "calc":
            continue
        v = labels.get(k, row.get(k))
        if c.get("type") == "person" and isinstance(v, int):
            from ..db import SessionLocal

            with SessionLocal() as s:
                u = s.get(User, v)
                v = u.name if u else f"#{v}"
        if v in (None, "", []):
            continue
        if c.get("type") == "checklist" and isinstance(v, list):
            v = f"{sum(1 for x in v if isinstance(x, dict) and x.get('done'))}/{len(v)} steps"
        elif c.get("type") == "file" and isinstance(v, dict):
            v = v.get("name", "file")
        bits.append(f"{_label(t, c)}: {str(v)[:60]}")
        if len(bits) >= 8:
            break
    return f"#{row.get('id')} · " + " · ".join(bits)


def _where(t, b: dict, where: str) -> tuple[dict, list]:
    """'Status = To do, Who = Thandi' → the table's own filters (choice, link, yes/no, person) and
    the rest to check here."""
    from ..engine.store import FILTERABLE

    eq, rest = {}, []
    for part in re.split(r"[,;\n]+|\band\b", where or ""):
        m = re.match(r"^\s*(.+?)\s*(!=|is not|=|is|:)\s*(.+?)\s*$", part)
        if not m:
            continue
        c = _col_for(t, b, m.group(1))
        if c is None:
            raise Problem(f"'{m.group(1)}' is not a column of this table.")
        neg = m.group(2) in ("!=", "is not")
        rest.append((c, m.group(3), neg))
        if not neg and c.get("type", "text") in FILTERABLE:
            eq[c["key"]] = m.group(3)
    return eq, rest


def _matches(t, row: dict, c: dict, want: str, neg: bool) -> bool:
    v = (row.get("_labels") or {}).get(c["key"], row.get(c["key"]))
    hit = str(v if v is not None else "").strip().lower() == want.strip().lower() or (c.get("type") not in ("choice", "bool") and want.strip().lower() in str(v or "").lower())
    return hit != neg


# ---------------------------------------------------------------- rows (anyone) -----------------
@tool("list_tools", "read", "List the tools built in the Builder that the person can open (To-do, Site Schedules, IFRS 16, Entity Compliance and any new ones), with their pages and tables. Start here for anything about those tools.", {})
def list_tools(db: Session, me: User, fix: Optional[Fix], **_) -> str:
    ts = sorted(_usable(db, me), key=lambda x: x.text(x.manifest.get("title", ""), "en").lower())
    if not ts:
        return "There are no Builder tools this person can open."
    out = []
    for t in ts:
        pages = ", ".join(t.text(p.get("title") or n, "en") for n, p in t.pages.items())
        tables = ", ".join(f"{n} ({len(d.get('columns') or [])} columns)" for n, d in (t.data or {}).items()) or "no tables of its own"
        out.append(f"{t.text(t.manifest.get('title', ''), 'en')} [{t.slug}] — pages: {pages}; tables: {tables}; {len(t.flows or {})} rule(s)")
    return "\n".join(out)


@tool("describe_tool", "read", "One Builder tool in detail: each table with its columns (type, options), each page's blocks (which table, filters, sort) and each rule (on/off, when it runs). Read this before adding rows or changing the tool.", {"tool": "tool name, e.g. To-do"})
def describe_tool(db: Session, me: User, fix: Optional[Fix], tool: str = "", **_) -> str:
    from ..engine.loader import widgets_of

    t = _tool_for(db, me, tool)
    if t is None:
        return f"I can't find a tool called '{tool}' that this person can open. " + list_tools(db, me, fix)
    out = [f"{t.text(t.manifest.get('title', ''), 'en')} [{t.slug}]"]
    for name, d in (t.data or {}).items():
        cols = []
        for c in d.get("columns") or []:
            typ = c.get("type", "text")
            extra = f" ({' | '.join(c.get('options') or [])})" if typ == "choice" else f" → {c.get('table')}" if typ == "link" else ""
            cols.append(f"{_label(t, c)} [{c['key']}, {typ}{extra}]")
        who = {"all": "everyone sees all", "mine": "each person their own", "team": "each team its own", "members": "the members"}.get(d.get("scope", "all"), d.get("scope"))
        out.append(f"TABLE {name}: {who}; adds rows: {d.get('write', 'everyone')}\n  " + "; ".join(cols))
    for pn, p in t.pages.items():
        bl = []
        for w in widgets_of(pn, p):
            src = (w.get("source") or {}).get("data") or (w.get("source") or {}).get("provider") or ""
            bits = [w.get("type", "?"), f"'{t.text(w.get('title') or '', 'en')}'" if w.get("title") else "", f"of {src}" if src else "",
                    f"filters: {', '.join(w['filters'])}" if w.get("filters") else "", f"sorted by {w['sort'].get('key')}" if isinstance(w.get("sort"), dict) else ""]
            bl.append(" ".join(b for b in bits if b) + f" [{w.get('id')}]")
        out.append(f"PAGE {t.text(p.get('title') or pn, 'en')} [{pn}]: " + "; ".join(bl))
    for fn, f in (t.flows or {}).items():
        tr = f.get("trigger") or {}
        when = {"button": "a button", "row_added": "a row is added", "row_changed": "a row changes", "schedule": "on a schedule"}.get(tr.get("type"), tr.get("type"))
        out.append(f"RULE {t.text(f.get('title') or fn, 'en')} [{fn}]: {'ON' if f.get('enabled') else 'off'}, runs when {when}" + (f" on {tr['data']}" if tr.get("data") else ""))
    return "\n".join(out)


@tool("find_rows", "read", "Rows of a Builder tool's table that the person may see, e.g. open tasks, a site, a goal. Optional search words and 'where' (Status = To do, Who = Thandi).", {"tool": "tool name", "table": "table name (or what it is called on its page)", "search": "optional words", "where": "optional: Column = value, Column != value", "limit": "optional, default 20"})
def find_rows(db: Session, me: User, fix: Optional[Fix], tool: str = "", table: str = "", search: str = "", where: str = "", limit: str = "20", **_) -> str:
    t = _tool_for(db, me, tool)
    if t is None:
        return f"I can't find a tool called '{tool}' that this person can open."
    block = _table_for(t, table)
    if block is None:
        return f"{t.slug} has these tables: {', '.join(t.data)}. Which one?"
    b = t.data[block]
    try:
        eq, rest = _where(t, b, where)
    except Problem as e:
        return str(e)
    params = {"q": (search or "")[:80], "limit": 500, **{k: v for k, v in eq.items()}}
    # a person filter by name: the table filters by id
    for c in b.get("columns") or []:
        if c["key"] in params and c.get("type") == "person":
            uid, why = _person(db, me, str(params[c["key"]]))
            if uid is None:
                return why
            params[c["key"]] = uid
    st, res = _as_person(db, me, "GET", f"/api/engine/tools/{t.slug}/data/{block}", params=params)
    if st != 200:
        return f"I couldn't read {block}: {_detail(res)}"
    rows = [r for r in res.get("rows") or [] if all(_matches(t, r, c, w, n) or (c["key"] in eq and c.get("type") == "person") for c, w, n in rest)]
    n = max(1, min(int(limit) if str(limit).isdigit() else 20, 50))
    if not rows:
        return f"No rows in {block}" + (f" matching '{search}'" if search else "") + (f" where {where}" if where else "") + "."
    return f"{len(rows)} row(s) in {block}" + (f" (first {n})" if len(rows) > n else "") + ":\n" + "\n".join(_row_line(t, b, r) for r in rows[:n])


def _one_row(db: Session, me: User, t, block: str, ref: str) -> tuple[Optional[dict], str]:
    r = (ref or "").strip().lstrip("#")
    st, res = _as_person(db, me, "GET", f"/api/engine/tools/{t.slug}/data/{block}", params={"q": "" if r.isdigit() else r[:80], "limit": 500})
    if st != 200:
        return None, f"I couldn't read {block}: {_detail(res)}"
    rows = res.get("rows") or []
    if r.isdigit():
        hit = [x for x in rows if x.get("id") == int(r)]
    else:
        exact = [x for x in rows if any(str(v).strip().lower() == r.lower() for v in x.values() if isinstance(v, str))]
        hit = exact or rows
    if len(hit) == 1:
        return hit[0], ""
    if not hit:
        return None, f"No row in {block} matches '{ref}' (or this person can't see it)."
    return None, f"'{ref}' matches {len(hit)} rows — which one?\n" + "\n".join(_row_line(t, t.data[block], x) for x in hit[:6])


@tool("add_row", "row", "Add a row to a Builder tool's table as the person (e.g. a task, a site, a goal): the table's own checks and rules apply as if they had clicked Add. Values as 'Column: value; Column: value' — names for people, words for pick-lists, Friday/tomorrow/2026-10-09 for dates, 'a, b, c' for checklist steps.", {"tool": "tool name", "table": "table name", "values": "Column: value; Column: value (or a JSON object)"})
def add_row(db: Session, me: User, fix: Optional[Fix], tool: str = "", table: str = "", values: str = "", **_) -> str:
    t = _tool_for(db, me, tool)
    if t is None:
        return f"I can't find a tool called '{tool}' that this person can open."
    block = _table_for(t, table)
    if block is None:
        return f"{t.slug} has these tables: {', '.join(t.data)}. Which one?"
    try:
        vals = _values(db, me, t, block, _pairs(values))
    except Problem as e:
        return str(e)
    if not vals:
        return "What should the row say? e.g. Title: Check the Telkom bill; Due: Friday"
    st, res = _as_person(db, me, "POST", f"/api/engine/tools/{t.slug}/data/{block}", body={"values": vals})
    if st != 200:
        return f"{block} didn't take it: {_detail(res)}"
    log(db, "cexa_row_added", actor=_cexa(db), target=f"{t.slug}/{block}/{res.get('id')}", detail=f"for {me.name}: {json.dumps(vals, default=str)[:200]}")
    started = len(res.get("_working") or [])
    return f"Added to {block}: {_row_line(t, t.data[block], res)}" + (f" ({started} rule(s) started)" if started else "")


@tool("update_row", "row", "Change a row in a Builder tool's table as the person (e.g. mark a task Done, move a due date, hand it to someone). The row is its #id or words that find exactly one row.", {"tool": "tool name", "table": "table name", "row": "#id or words that find it", "values": "Column: value; Column: value"})
def update_row(db: Session, me: User, fix: Optional[Fix], tool: str = "", table: str = "", row: str = "", values: str = "", **_) -> str:
    t = _tool_for(db, me, tool)
    if t is None:
        return f"I can't find a tool called '{tool}' that this person can open."
    block = _table_for(t, table)
    if block is None:
        return f"{t.slug} has these tables: {', '.join(t.data)}. Which one?"
    found, why = _one_row(db, me, t, block, row)
    if found is None:
        return why
    try:
        vals = _values(db, me, t, block, _pairs(values))
    except Problem as e:
        return str(e)
    if not vals:
        return "What should change? e.g. Status: Done"
    st, res = _as_person(db, me, "PATCH", f"/api/engine/tools/{t.slug}/data/{block}/{found['id']}", body={"values": vals})
    if st != 200:
        return f"{block} didn't take the change: {_detail(res)}"
    log(db, "cexa_row_changed", actor=_cexa(db), target=f"{t.slug}/{block}/{found['id']}", detail=f"for {me.name}: {json.dumps(vals, default=str)[:200]}")
    return f"Changed: {_row_line(t, t.data[block], res)}"


@tool("give_task", "row", "Give someone a task in To-do (or add one to the person's own list): it lands on that person's list, with optional steps and a due date. A task for someone else is a team task on the Team board, so they must be in the person's team.", {"to": "who: a name, an email, or 'me'", "title": "what needs doing", "steps": "optional: step, step, step", "due": "optional: Friday, tomorrow, 2026-10-09"})
def give_task(db: Session, me: User, fix: Optional[Fix], to: str = "me", title: str = "", steps: str = "", due: str = "", **_) -> str:
    from ..engine import loader

    t = loader.get("todo")
    if t is None or "tasks" not in (t.data or {}):
        return "The To-do tool isn't installed here."
    if not (title or "").strip():
        return "What is the task? Give me a title."
    from ..api.engine_api import can_use

    if not can_use(db, me, t):
        return f"{me.name.split()[0]} doesn't have the To-do tool."
    uid, why = _person(db, me, to or "me")
    if uid is None:
        return why
    vals = {"title": title.strip()[:200], "kind": "Mine" if uid == me.id else "Team", "who": str(uid)}
    if steps:
        vals["steps"] = steps
    if due:
        vals["due"] = due
    return add_row(db, me, fix, tool="todo", table="tasks", values=json.dumps(vals))


@tool("my_tasks", "read", "The person's open To-do tasks (their own and the team tasks they hold), with status, steps and due date.", {})
def my_tasks(db: Session, me: User, fix: Optional[Fix], **_) -> str:
    return find_rows(db, me, fix, tool="todo", table="tasks", where=f"Who = {me.id}, Status != Done", limit="30")


@tool("waiting_for_me", "read", "The rules waiting for the person to decide (approvals they must say yes or no to). Only they can decide, on the page (Requests → Approvals).", {})
def waiting_for_me(db: Session, me: User, fix: Optional[Fix], **_) -> str:
    st, res = _as_person(db, me, "GET", "/api/flows/waiting")
    runs = (res or {}).get("runs") or [] if st == 200 else []
    if not runs:
        return "Nothing is waiting for this person's decision."
    return "Waiting for you to decide (on the Approvals page):\n" + "\n".join(f"run {r.get('id')}: {(r.get('waiting') or {}).get('question') or r.get('title') or r.get('flow')} ({r.get('tool_title') or r.get('tool')})" for r in runs[:15])


# ---------------------------------------------------------------- draft (admins) ----------------
def _edit_draft(db: Session, me: User, ref: str, what: str, change) -> str:
    """Copy the live tool into its Builder draft (or use the draft already there), make one change,
    and keep it only if the folder is still sound. Never live: a person releases."""
    from ..api.builder_api import DraftTaken, _draft_dir, claim, write_folder
    from ..engine import loader
    from .ceo import _DRAFT_LOCK

    if not me.is_admin:
        return "Only admins can change how a tool works; I can add and change rows for you."
    t = _tool_for(db, me, ref)
    if t is None:
        return f"I can't find a tool called '{ref}'."
    d = _draft_dir(t.slug)
    with _DRAFT_LOCK:
        had = (d / "tool.json").exists()
        try:
            claim(d, me)  # 1.0.80: the draft is the person's she works for — never someone else's
        except DraftTaken as e:
            return str(e)
        if not had:
            shutil.copytree(t.path, d, ignore=shutil.ignore_patterns("__pycache__"), dirs_exist_ok=True)
        before = loader.read_folder(d)
        F = {"manifest": json.loads(json.dumps(before.manifest)), "pages": json.loads(json.dumps(before.pages)), "data": json.loads(json.dumps(before.data)),
             "flows": json.loads(json.dumps(before.flows)), "languages": json.loads(json.dumps(before.languages)), "readme": before.readme}
        try:
            note = change(before, F)
        except Problem as e:
            if not had:
                shutil.rmtree(d, ignore_errors=True)
            return str(e)
        write_folder(d, F["manifest"], F["pages"], F["data"], F["languages"], F["readme"], F["flows"])
        after = loader.read_folder(d)
        new = [e for e in after.errors if e not in before.errors]
        if new:
            write_folder(d, before.manifest, before.pages, before.data, before.languages, before.readme, before.flows)
            if not had:
                shutil.rmtree(d, ignore_errors=True)
            return "I didn't keep that change — the tool would break: " + "; ".join(new[:3])
    log(db, "cexa_draft_changed", actor=_cexa(db), target=t.slug, detail=f"for {me.name}: {what}"[:300])
    return f"{note} Saved in the Builder draft of {t.text(t.manifest.get('title', ''), 'en')} — not live yet. Open Admin → Builder → {t.text(t.manifest.get('title', ''), 'en')}, check it, then Release (and Promote on Versions)."


def _blocks_of(t, F: dict, ref: str) -> list[dict]:
    """The table blocks a person means: by the block's title or id, or every table block showing a table."""
    from ..engine.loader import widgets_of

    found = []
    for pn, p in F["pages"].items():
        for w in widgets_of(pn, p):
            if w.get("type") not in ("table", "board"):
                continue
            names = [str(w.get("id")), t.text(w.get("title") or "", "en"), (w.get("source") or {}).get("data") or "", t.text(p.get("title") or pn, "en")]
            found.append((w, names))
    key = _best(ref, [(str(id(w)), n) for w, n in found])
    if key is None:
        return []
    hit = next(w for w, _ in found if str(id(w)) == key)
    return [hit]


@tool("add_filter", "change", "Add a filter (a drop-down above a table) to a Builder tool's table block, e.g. filter the Team board by Status. Saved in the Builder draft; a person releases it.", {"tool": "tool name", "block": "the table as it is called on the page (or its table name)", "column": "the column to filter by (a pick-list, yes/no, person or link column)"})
def add_filter(db: Session, me: User, fix: Optional[Fix], tool: str = "", block: str = "", column: str = "", **_) -> str:
    from ..engine.store import FILTERABLE

    def change(t, F):
        ws = _blocks_of(t, F, block)
        if not ws:
            raise Problem(f"I can't find a table block called '{block}' in {t.slug}. " + describe_tool(db, me, None, tool=t.slug).split("\n", 1)[-1][:600])
        w = ws[0]
        data = (w.get("source") or {}).get("data")
        if not data:
            raise Problem("That block isn't showing one of the tool's own tables, so it can't take a filter.")
        c = _col_for(t, F["data"][data], column)
        if c is None:
            raise Problem(f"'{column}' is not a column of {data}.")
        if c.get("type", "text") not in FILTERABLE:
            raise Problem(f"{_label(t, c)} is {c.get('type', 'text')}; a filter works on a pick-list, yes/no, person or link column. Use the search box for text.")
        fl = list(w.get("filters") or [])
        if c["key"] in fl:
            raise Problem(f"That block already filters by {_label(t, c)}.")
        w["filters"] = fl + [c["key"]]
        return f"Added a {_label(t, c)} filter to '{t.text(w.get('title') or '', 'en') or w.get('id')}'."

    return _edit_draft(db, me, tool, f"filter {column} on {block}", change)


@tool("remove_filter", "change", "Take a filter off a Builder tool's table block. Saved in the Builder draft.", {"tool": "tool name", "block": "the table as it is called on the page", "column": "the filter's column"})
def remove_filter(db: Session, me: User, fix: Optional[Fix], tool: str = "", block: str = "", column: str = "", **_) -> str:
    def change(t, F):
        ws = _blocks_of(t, F, block)
        if not ws or not (ws[0].get("source") or {}).get("data"):
            raise Problem(f"I can't find a table block called '{block}' in {t.slug}.")
        w = ws[0]
        c = _col_for(t, F["data"][w["source"]["data"]], column)
        if c is None or c["key"] not in (w.get("filters") or []):
            raise Problem(f"That block has no {column} filter. Its filters: {', '.join(w.get('filters') or []) or 'none'}.")
        w["filters"] = [k for k in w["filters"] if k != c["key"]]
        if not w["filters"]:
            w.pop("filters")
        return f"Took the {_label(t, c)} filter off."

    return _edit_draft(db, me, tool, f"remove filter {column} on {block}", change)


@tool("add_column", "change", "Add a column to a Builder tool's table (and show it in the tables on its pages). Saved in the Builder draft; a person releases it.", {"tool": "tool name", "table": "table name", "label": "what the column is called", "type": "text | number | money | date | yes/no | pick-list | person | checklist", "options": "for a pick-list: the options, separated by commas"})
def add_column(db: Session, me: User, fix: Optional[Fix], tool: str = "", table: str = "", label: str = "", type: str = "text", options: str = "", **_) -> str:  # noqa: A002
    from ..engine.loader import widgets_of

    def change(t, F):
        block = _table_for(t, table)
        if block is None:
            raise Problem(f"{t.slug} has these tables: {', '.join(F['data'])}. Which one?")
        lab = (label or "").strip()
        if not lab:
            raise Problem("What is the column called?")
        typ = TYPE_WORDS.get((type or "text").strip().lower(), (type or "text").strip().lower())
        if typ not in ("text", "number", "money", "int", "date", "bool", "choice", "person", "checklist"):
            raise Problem("A column can be text, number, money, date, yes/no, pick-list, person or checklist.")
        cols = F["data"][block].setdefault("columns", [])
        key = re.sub(r"[^a-z0-9]+", "_", lab.lower()).strip("_")[:40] or "column"
        if not re.match(r"^[a-z]", key):
            key = "c_" + key
        base, i = key, 2
        while any(c.get("key") == key for c in cols):
            key, i = f"{base}_{i}", i + 1
        col: dict = {"key": key, "label": f"col.{key}", "type": "number" if typ == "money" else typ}
        if typ == "money":
            col["format"] = "money"
        if typ == "choice":
            opts = [o.strip() for o in re.split(r"[,;\n]+", options or "") if o.strip()]
            if len(opts) < 2:
                raise Problem("A pick-list needs at least two options, e.g. options: Open, In progress, Done.")
            col["options"] = opts[:50]
        cols.append(col)
        F["languages"].setdefault("en", {})[f"col.{key}"] = lab
        shown = 0
        for pn, p in F["pages"].items():
            for w in widgets_of(pn, p):
                if w.get("type") == "table" and (w.get("source") or {}).get("data") == block and isinstance(w.get("columns"), list):
                    w["columns"].append({"key": key, "label": f"col.{key}"})
                    shown += 1
        return f"Added the column '{lab}' ({type or 'text'}) to {block}" + (f", shown in {shown} table(s)." if shown else ".") + " The other three languages show the English until someone translates it (Builder → Words)."

    return _edit_draft(db, me, tool, f"add column {label} to {table}", change)


@tool("switch_rule", "change", "Switch a Builder tool's rule on or off. Saved in the Builder draft; a person releases it.", {"tool": "tool name", "rule": "the rule's name", "on": "on or off"})
def switch_rule(db: Session, me: User, fix: Optional[Fix], tool: str = "", rule: str = "", on: str = "on", **_) -> str:
    def change(t, F):
        key = _best(rule, [(n, [n, t.text(f.get("title") or "", "en")]) for n, f in F["flows"].items()])
        if key is None:
            raise Problem(f"{t.slug} has these rules: " + ", ".join(t.text(f.get("title") or n, "en") for n, f in F["flows"].items()))
        want = str(on).strip().lower() not in ("off", "false", "no", "0", "disable", "disabled")
        F["flows"][key]["enabled"] = want
        return f"Rule '{t.text(F['flows'][key].get('title') or key, 'en')}' switched {'on' if want else 'off'}."

    return _edit_draft(db, me, tool, f"rule {rule} {on}", change)

