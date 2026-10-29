"""One database file per tool: DATA_DIR/tools/<slug>.sqlite, one table per Data block.

The file is the tool's own. Nothing outside the tool reads it except through the engine's data API,
which checks the page's access first. Columns are what the Data block says (text, int, number, date,
bool, json, choice, link, file) plus id, created_at and created_by, which every table has. A block that gains a column
gets it added; a column that is removed from the block is left in the file, ignored, never dropped —
the data is never thrown away by a definition change.

On the company server the same blocks become a Postgres schema per tool; the blocks do not change.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Iterator, Optional

from ..config import settings
from ..db import utcnow

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
SQL_TYPE = {"text": "TEXT", "int": "INTEGER", "number": "REAL", "date": "TEXT", "bool": "INTEGER", "json": "TEXT", "choice": "TEXT", "link": "INTEGER", "file": "TEXT",
            "person": "INTEGER", "checklist": "TEXT", "calc": "REAL"}


def sql_type(col: dict) -> str:
    t = col.get("type", "text")
    if t == "calc" and ((col.get("calc") or {}).get("fn") in ("grade", "expiry")):
        return "TEXT"
    return SQL_TYPE.get(t, "TEXT")
# the columns a table can be filtered by (one value each): a pick-list, a link, yes/no
FILTERABLE = ("choice", "link", "bool", "person")
SYSTEM = ("id", "created_at", "created_by")
_LOCK = threading.Lock()


class StoreError(ValueError):
    pass


def path_for(slug: str) -> Path:
    return settings.data_dir / "tools" / f"{slug}.sqlite"


def _ident(name: str) -> str:
    if not NAME_RE.match(name or ""):
        raise StoreError(f"'{name}' is not a valid table or column name")
    return '"' + name + '"'


class Store:
    def __init__(self, slug: str):
        if not re.match(r"^[a-z][a-z0-9-]{1,40}$", slug or ""):
            raise StoreError("bad tool slug")
        self.slug = slug
        self.path = path_for(slug)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """One connection for one `with`: committed (or rolled back) and CLOSED at the end. 1.0.66: before,
        `with sqlite3.connect(...)` only committed — the file stayed open until Python got round to it, and
        on Windows an open file can't be removed or replaced (a tool's data reset, the test suite's clean-up)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(self.path, timeout=10)
        try:
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            with c:
                yield c
        finally:
            c.close()

    def exists(self, block: str) -> bool:
        if not self.path.exists():
            return False
        with self._conn() as c:
            return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (block,)).fetchone() is not None

    def ensure(self, block: str, columns: list[dict]) -> None:
        """Create the table, or add the columns the block has gained."""
        with _LOCK, self._conn() as c:
            cols = ", ".join(f"{_ident(col['key'])} {sql_type(col)}" for col in columns)
            c.execute(f"CREATE TABLE IF NOT EXISTS {_ident(block)} (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, created_by INTEGER{', ' + cols if cols else ''})")
            have = {r[1] for r in c.execute(f"PRAGMA table_info({_ident(block)})").fetchall()}
            for col in columns:
                if col["key"] not in have:
                    c.execute(f"ALTER TABLE {_ident(block)} ADD COLUMN {_ident(col['key'])} {sql_type(col)}")

    # ---- values in and out ------------------------------------------------------------------------

    @staticmethod
    def coerce(col: dict, v: Any) -> Any:
        """A value from a form, as the column wants it. Raises StoreError with a plain sentence."""
        t = col.get("type", "text")
        key = col.get("key", "?")
        if v is None or v == "":
            if col.get("required"):
                raise StoreError(f"'{key}' is required")
            return None
        try:
            if t == "int":
                return int(str(v).strip())
            if t == "number":
                return float(str(v).replace(",", ".").replace(" ", ""))
            if t == "bool":
                return 1 if str(v).lower() in ("1", "true", "yes", "on") else 0
            if t == "date":
                s = str(v).strip()[:10]
                date.fromisoformat(s)
                return s
            if t == "json":
                return json.dumps(v if not isinstance(v, str) else json.loads(v))
            if t == "choice":
                opts = [str(o) for o in (col.get("options") or [])]
                s = str(v).strip()
                hit = next((o for o in opts if o == s), None) or next((o for o in opts if o.lower() == s.lower()), None)
                if hit is None:
                    raise StoreError(f"'{key}' must be one of: {', '.join(opts)}")
                return hit
            if t == "link":
                n = int(str(v).strip().lstrip("#"))
                if n <= 0:
                    raise ValueError
                return n
            if t == "file":
                raise StoreError(f"'{key}' is a file — upload it on the row, it cannot be typed in")
            if t == "person":
                n = int(str(v).strip())
                if n <= 0:
                    raise ValueError
                return n
            if t == "checklist":
                items = json.loads(v) if isinstance(v, str) and v.strip().startswith("[") else v
                if isinstance(items, str):
                    items = [x for x in items.splitlines() if x.strip()]
                out = []
                for it in (items or [])[:100]:
                    if isinstance(it, str):
                        it = {"text": it}
                    if isinstance(it, dict) and str(it.get("text") or "").strip():
                        out.append({"text": str(it["text"]).strip()[:300], "done": bool(it.get("done"))})
                return json.dumps(out, ensure_ascii=False)
            if t == "calc":
                return None  # worked out on save, never typed in
            s = str(v)
            if len(s) > 4000:
                raise StoreError(f"'{key}' is too long (4000 characters at most)")
            return s
        except StoreError:
            raise
        except (TypeError, ValueError) as e:
            raise StoreError(f"'{key}' must be {'a row of ' + str(col.get('table')) if t == 'link' else 'a ' + t}") from e

    @staticmethod
    def _out(row: sqlite3.Row, columns: list[dict]) -> dict:
        d = dict(row)
        for col in columns:
            k, t = col["key"], col.get("type", "text")
            if k in d and d[k] is not None:
                if t == "bool":
                    d[k] = bool(d[k])
                elif t in ("json", "file", "checklist"):
                    try:
                        d[k] = json.loads(d[k])
                    except (TypeError, ValueError):
                        pass
        if any(c.get("type") == "calc" for c in columns):
            d.update(Store.calcs(columns, d))  # worked out again on reading: "days since" stays true
        return d

    # ---- CRUD --------------------------------------------------------------------------------------

    def rows(self, block: str, columns: list[dict], *, q: str = "", sort: str = "", desc: bool = False, limit: int = 500, offset: int = 0, mine: Optional[int] = None, eq: Optional[dict] = None, tables: Optional[dict] = None, after_id: int = 0, ids: Optional[list] = None) -> dict:
        """Rows, newest first unless sorted. `after_id` keeps the rows added after that one (a key's reader
        fetching what is new, 1.0.59). `eq` filters a choice, link or yes/no column to one value;
        `tables` (the tool's data blocks) lets each link column carry the label of the row it points at,
        in row["_labels"][column]."""
        self.ensure(block, columns)
        self._ensure_via(mine)
        keys = [c["key"] for c in columns]
        where, args = [], []
        self._scope(where, args, mine)
        if after_id:
            where.append("id > ?")
            args.append(int(after_id))
        if ids is not None:
            where.append(f"id IN ({', '.join('?' for _ in ids) or 'NULL'})")
            args.extend(int(i) for i in ids)
        for c in columns:
            k = c["key"]
            if eq and k in eq and c.get("type", "text") in FILTERABLE and eq[k] not in (None, ""):
                try:
                    v = self.coerce({**c, "required": False}, eq[k])
                except StoreError:
                    v = "\x00none"
                where.append(f"{_ident(k)} = ?")
                args.append(v)
        if q:
            text_cols = [c["key"] for c in columns if c.get("type", "text") in ("text", "json", "date", "choice")]
            if text_cols:
                where.append("(" + " OR ".join(f"{_ident(k)} LIKE ?" for k in text_cols) + ")")
                args += [f"%{q}%"] * len(text_cols)
        # empty values last, whichever way (a document with no date does not lead the "soonest first" list)
        order = f"ORDER BY ({_ident(sort)} IS NULL OR {_ident(sort)} = ''), {_ident(sort)} {'DESC' if desc else 'ASC'}" if sort in keys + ["id", "created_at"] else "ORDER BY id DESC"
        w = ("WHERE " + " AND ".join(where)) if where else ""
        with self._conn() as c:
            total = c.execute(f"SELECT COUNT(*) FROM {_ident(block)} {w}", args).fetchone()[0]
            rs = c.execute(f"SELECT * FROM {_ident(block)} {w} {order} LIMIT ? OFFSET ?", [*args, max(1, min(limit, 2000)), max(0, offset)]).fetchall()
        out = [self._out(r, columns) for r in rs]
        if tables:
            self.label_links(out, columns, tables)
        return {"rows": out, "total": total}

    def _ensure_via(self, mine: Any) -> None:
        """The members table a "members" scope looks in may have no rows yet — make sure it is there (and,
        for a linked scope, the table it follows)."""
        if not isinstance(mine, dict):
            return
        via = mine.get("via")
        if via and via.get("table") and via.get("person") and via.get("link"):
            self.ensure(via["table"], [{"key": via["person"], "type": "person"}, {"key": via["link"], "type": "link"}] + [{"key": k, "type": "text"} for k in (via.get("only") or {})])
        lk = mine.get("linked")
        if lk and lk.get("table"):
            self.ensure(lk["table"], lk.get("columns") or [])
            self._ensure_via(lk.get("inner"))

    @staticmethod
    def _scope(where: list, args: list, mine: Any) -> None:
        """Who sees which rows: None — all; an id — the rows that person added; a dict — the rows added by
        any of `users`, or with `me` in one of the person columns `cols` (1.0.54: "mine" and "team")."""
        if mine is None:
            return
        if isinstance(mine, int):
            where.append("created_by = ?")
            args.append(mine)
            return
        users = [int(u) for u in (mine.get("users") or [])] or [-1]
        parts = [f"created_by IN ({', '.join('?' for _ in users)})"]
        args.extend(users)
        for col in mine.get("cols") or []:
            parts.append(f"{_ident(col)} = ?")
            args.append(int(mine.get("me") or -1))
        sh = mine.get("share")
        if sh and sh.get("field"):
            # the rows marked for the team (e.g. Kind is Team) that someone of the person's team added
            su = [int(u) for u in (sh.get("users") or [])] or [-1]
            parts.append(f"({_ident(sh['field'])} = ? AND created_by IN ({', '.join('?' for _ in su)}))")
            args.extend([sh.get("value"), *su])
        via = mine.get("via")
        if via:
            # shared with the members (1.0.55): this row (or the row it links to) has the person as an
            # active member in the members table, e.g. a job's people
            only = via.get("only") or {}
            cond = " AND ".join([f"{_ident(via['person'])} = ?"] + [f"{_ident(k)} = ?" for k in only])
            parts.append(f"{_ident(via.get('through') or 'id')} IN (SELECT {_ident(via['link'])} FROM {_ident(via['table'])} WHERE {cond})")
            args.extend([int(mine.get("me") or -1), *only.values()])
        op = mine.get("open")
        if op and op.get("field"):
            # everyone sees the rows where this column has this value (1.0.63)
            v = op.get("value")
            parts.append(f"{_ident(op['field'])} = ?")
            args.append((1 if v else 0) if isinstance(v, bool) else v)
        lk = mine.get("linked")
        if lk and lk.get("col") and lk.get("table"):
            # whoever may see the row it links to (1.0.63): the rule of that table, as a sub-query
            iw: list = []
            ia: list = []
            Store._scope(iw, ia, lk.get("inner"))
            parts.append(f"{_ident(lk['col'])} IN (SELECT id FROM {_ident(lk['table'])}{(' WHERE ' + ' AND '.join(iw)) if iw else ''})")
            args.extend(ia)
        where.append("(" + " OR ".join(parts) + ")")

    def row_visible(self, block: str, columns: list[dict], rid: int, mine: Any) -> bool:
        if mine is None:
            return self.get(block, columns, rid) is not None
        self.ensure(block, columns)
        self._ensure_via(mine)
        where, args = ["id = ?"], [rid]
        self._scope(where, args, mine)
        try:
            with self._conn() as c:
                return c.execute(f"SELECT 1 FROM {_ident(block)} WHERE {' AND '.join(where)}", args).fetchone() is not None
        except sqlite3.OperationalError:
            return False

    def check_unique(self, block: str, groups: list, vals: dict, rid: Optional[int] = None, current: Optional[dict] = None) -> None:
        """A table may say some columns go together only once (e.g. one reward day per goal and person)."""
        for g in groups or []:
            keys = [k for k in g if isinstance(k, str)]
            if not keys:
                continue
            row = {**(current or {}), **vals}
            if any(row.get(k) in (None, "") for k in keys):
                continue
            where = " AND ".join(f"{_ident(k)} = ?" for k in keys) + (" AND id != ?" if rid else "")
            with self._conn() as c:
                hit = c.execute(f"SELECT 1 FROM {_ident(block)} WHERE {where}", [row[k] for k in keys] + ([rid] if rid else [])).fetchone()
            if hit:
                raise StoreError(f"There is a row with this {' and '.join(keys)} already — only one is allowed.")

    @staticmethod
    def calcs(columns: list[dict], row: dict) -> dict:
        """The worked-out columns of one row, from its other values."""
        from datetime import date as _d

        from ..util import local_today

        out: dict = {}
        vals = dict(row)
        for c in columns:
            if c.get("type") != "calc":
                continue
            cc = c.get("calc") or {}
            fn = cc.get("fn")
            v: Any = None
            if fn in ("count", "done"):
                items = vals.get(cc.get("of"))
                if isinstance(items, str):
                    try:
                        items = json.loads(items)
                    except ValueError:
                        items = []
                items = items if isinstance(items, list) else []
                v = float(len(items) if fn == "count" else sum(1 for x in items if isinstance(x, dict) and x.get("done")))
            elif fn == "days":
                def day(k):
                    if k in (None, "", "today"):
                        return local_today()
                    if k == "created":
                        s = str(vals.get("created_at") or "")[:10]
                    else:
                        s = str(vals.get(k) or "")[:10]
                    try:
                        return _d.fromisoformat(s)
                    except ValueError:
                        return None
                a, b = day(cc.get("from", "created")), day(cc.get("to", "today"))
                v = float((b - a).days) if a and b else None
            elif fn == "grade":
                n = vals.get(cc.get("of"))
                n = out.get(cc.get("of"), n)
                try:
                    n = float(n)
                except (TypeError, ValueError):
                    n = None
                if n is not None:
                    for top, label in cc.get("grades") or []:
                        if top is None or n <= float(top):
                            v = str(label)
                            break
            elif fn == "expiry":
                v = expiry_status(cc, vals)
            out[c["key"]] = v
            vals[c["key"]] = v
        return out

    def label_links(self, rows: list[dict], columns: list[dict], tables: dict) -> None:
        """Each link column's label, from the row it points at (one query per link column)."""
        from .loader import link_show

        for c in columns:
            if c.get("type") != "link" or c.get("table") not in tables:
                continue
            ids = sorted({r[c["key"]] for r in rows if isinstance(r.get(c["key"]), int)})
            if not ids:
                continue
            tgt = c["table"]
            show = c.get("show") or link_show(tables[tgt])
            labels: dict = {}
            if self.exists(tgt):
                with self._conn() as con:
                    col = "id" if show == "id" else show
                    have = {r[1] for r in con.execute(f"PRAGMA table_info({_ident(tgt)})").fetchall()}
                    if col in have:
                        for r in con.execute(f"SELECT id, {_ident(col)} FROM {_ident(tgt)} WHERE id IN ({', '.join('?' for _ in ids)})", ids).fetchall():
                            labels[r[0]] = "" if r[1] is None else str(r[1])
            for r in rows:
                v = r.get(c["key"])
                if isinstance(v, int):
                    r.setdefault("_labels", {})[c["key"]] = labels.get(v, f"#{v} (removed)") or f"#{v}"

    def choices(self, block: str, columns: list[dict], show: str, *, q: str = "", limit: int = 200, mine: Optional[int] = None) -> list[dict]:
        """The rows a link column may point at, as {id, label}, for the pick-list on a form."""
        self.ensure(block, columns)
        self._ensure_via(mine)
        col = "id" if show == "id" else show
        if col != "id" and col not in {c["key"] for c in columns}:
            col = "id"
        where, args = [], []
        self._scope(where, args, mine)
        if q:
            where.append(f"CAST({_ident(col)} AS TEXT) LIKE ?")
            args.append(f"%{q}%")
        w = ("WHERE " + " AND ".join(where)) if where else ""
        with self._conn() as c:
            rs = c.execute(f"SELECT id, {_ident(col)} FROM {_ident(block)} {w} ORDER BY {_ident(col)} ASC LIMIT ?", [*args, max(1, min(limit, 1000))]).fetchall()
        return [{"id": r[0], "label": f"#{r[0]}" if r[1] in (None, "") else str(r[1])} for r in rs]

    @staticmethod
    def _no_files(columns: list[dict], values: dict) -> None:
        for c in columns:
            if c.get("type") == "file" and values.get(c["key"]) not in (None, ""):
                raise StoreError(f"'{c['key']}' is a file — upload it on the row, it cannot be typed in")

    def check_links(self, columns: list[dict], vals: dict) -> None:
        """A link must point at a row that is there."""
        for c in columns:
            v = vals.get(c["key"])
            if c.get("type") != "link" or v is None:
                continue
            tgt = c.get("table") or ""
            ok = False
            if NAME_RE.match(tgt) and self.exists(tgt):
                with self._conn() as con:
                    ok = con.execute(f"SELECT 1 FROM {_ident(tgt)} WHERE id = ?", (v,)).fetchone() is not None
            if not ok:
                raise StoreError(f"'{c['key']}': there is no row {v} in {tgt}")

    def count_where(self, block: str, key: str, value: Any, exclude_id: Optional[int] = None) -> int:
        if not self.exists(block):
            return 0
        with self._conn() as c:
            have = {r[1] for r in c.execute(f"PRAGMA table_info({_ident(block)})").fetchall()}
            if key not in have:
                return 0
            if exclude_id is not None:
                return c.execute(f"SELECT COUNT(*) FROM {_ident(block)} WHERE {_ident(key)} = ? AND id != ?", (value, exclude_id)).fetchone()[0]
            return c.execute(f"SELECT COUNT(*) FROM {_ident(block)} WHERE {_ident(key)} = ?", (value,)).fetchone()[0]

    def set_file(self, block: str, columns: list[dict], rid: int, key: str, meta: Optional[dict]) -> Optional[dict]:
        """Put (or clear) the file record of one file column — the only way a file column is written."""
        self.ensure(block, columns)
        if not any(c["key"] == key and c.get("type") == "file" for c in columns):
            raise StoreError(f"'{key}' is not a file column")
        with _LOCK, self._conn() as c:
            c.execute(f"UPDATE {_ident(block)} SET {_ident(key)} = ? WHERE id = ?", (json.dumps(meta) if meta else None, rid))
        return self.get(block, columns, rid)

    def get(self, block: str, columns: list[dict], rid: int) -> Optional[dict]:
        self.ensure(block, columns)
        with self._conn() as c:
            r = c.execute(f"SELECT * FROM {_ident(block)} WHERE id = ?", (rid,)).fetchone()
        return self._out(r, columns) if r else None

    def insert(self, block: str, columns: list[dict], values: dict, by: Optional[int], unique: Optional[list] = None, at: Optional[str] = None) -> dict:
        self.ensure(block, columns)
        self._no_files(columns, values)
        values = dict(values)
        for c in columns:  # a column's starting value, when the row does not say (1.0.55); a form shows it filled in, so "" there means emptied
            if values.get(c["key"]) is None and c.get("default") not in (None, ""):
                d = c["default"]
                if d == "today" and c.get("type") == "date":
                    from ..util import local_today

                    d = local_today().isoformat()
                elif d == "me" and c.get("type") == "person":
                    d = by  # the person who adds the row
                values[c["key"]] = d
        vals = {c["key"]: self.coerce(c, values.get(c["key"])) for c in columns if c.get("type") not in ("file", "calc")}
        self.check_links(columns, vals)
        vals.update(self.calcs(columns, {**vals, "created_at": utcnow().isoformat()}))
        if unique:
            self.check_unique(block, unique, vals)
        with _LOCK, self._conn() as c:
            cols = ["created_at", "created_by", *vals.keys()]
            cur = c.execute(f"INSERT INTO {_ident(block)} ({', '.join(_ident(k) for k in cols)}) VALUES ({', '.join('?' for _ in cols)})", [at or utcnow().isoformat(), by, *vals.values()])
            rid = cur.lastrowid
        return self.get(block, columns, rid) or {"id": rid}

    def update(self, block: str, columns: list[dict], rid: int, values: dict, unique: Optional[list] = None) -> Optional[dict]:
        self.ensure(block, columns)
        self._no_files(columns, values)
        cols = [c for c in columns if c["key"] in values and c.get("type") not in ("file", "calc")]
        if not cols:
            return self.get(block, columns, rid)
        vals = {c["key"]: self.coerce(c, values.get(c["key"])) for c in cols}
        self.check_links(cols, vals)
        if any(c.get("type") == "calc" for c in columns):
            cur = self.get(block, columns, rid) or {}
            vals.update(self.calcs(columns, {**cur, **{k: (json.loads(v) if isinstance(v, str) and v.startswith("[") else v) for k, v in vals.items()}}))
        if unique:
            self.check_unique(block, unique, vals, rid, self.get(block, columns, rid) or {})
        with _LOCK, self._conn() as c:
            c.execute(f"UPDATE {_ident(block)} SET {', '.join(f'{_ident(k)} = ?' for k in vals)} WHERE id = ?", [*vals.values(), rid])
        return self.get(block, columns, rid)

    def delete(self, block: str, columns: list[dict], rid: int) -> bool:
        self.ensure(block, columns)
        with _LOCK, self._conn() as c:
            n = c.execute(f"DELETE FROM {_ident(block)} WHERE id = ?", (rid,)).rowcount
        return n > 0

    def count(self, block: str) -> int:
        if not self.exists(block):
            return 0
        with self._conn() as c:
            return c.execute(f"SELECT COUNT(*) FROM {_ident(block)}").fetchone()[0]


def expiry_status(cc: dict, vals: dict) -> str:
    """Where a dated document stands (1.0.63, Entity Compliance's rule, for any table):
    archived · not required · doesn't expire · no date yet (needs a date when a file is there, missing when
    not) · expired · expiring (inside its warning days) · ok. `cc` names the columns: date, warn (a number
    column, or warn_days a fixed number), required, never, file, off."""
    from datetime import date as _d

    from ..util import local_today

    def on(k):
        v = vals.get(k) if k else None
        return v not in (None, "", 0, False, "0", "false")

    if cc.get("off") and on(cc["off"]):
        return "archived"
    if cc.get("required") and vals.get(cc["required"]) in (0, False, "0", "false"):
        return "not_required"
    if cc.get("never") and on(cc["never"]):
        return "no_expiry"
    try:
        d = _d.fromisoformat(str(vals.get(cc.get("date")) or "")[:10])
    except ValueError:
        d = None
    if d is None:
        f = vals.get(cc.get("file")) if cc.get("file") else None
        return "needs_date" if isinstance(f, dict) and f.get("id") or (isinstance(f, str) and '"id"' in f) else "missing"
    left = (d - local_today()).days
    try:
        warn = int(vals.get(cc["warn"])) if cc.get("warn") and vals.get(cc["warn"]) not in (None, "") else int(cc.get("warn_days", 90))
    except (TypeError, ValueError):
        warn = 90
    return "expired" if left < 0 else "expiring" if left <= warn else "ok"

