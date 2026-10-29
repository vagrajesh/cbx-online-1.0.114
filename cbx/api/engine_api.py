"""The engine's front door: what tools this person may see, what a page is, and its data.

    GET  /api/engine/menu?lang=              the tool folders this person may use, titled in their language
    GET  /api/engine/tools/{slug}/pages/{page}?lang=   the page block + the tool's words + who may do what
    GET  /api/engine/tools/{slug}/data/{block}         rows (a provider's, or the tool's own table)
    POST /api/engine/tools/{slug}/data/{block}         add a row to the tool's own table
    PATCH/DELETE …/data/{block}/{id}                   change or remove one (own rows; admins any)
    GET  …/data/{block}/choices/{column}               the rows a link column may point at ({id, label})
    POST/GET/DELETE …/data/{block}/{id}/files/{column} a row's file: upload, download, remove (1.0.51)

A page may be for some roles only (its access.roles); a table is then for whoever may open a page that
shows it, and a Run button's flow likewise.

Access is the manifest's: a role in access.roles, or a grant of the tool in access.tool — the same
grants Users & roles hands out today. A provider's own roles apply on top. Every write is audited.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import threading
import time

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..audit import log
from ..db import get_db, utcnow
from ..engine import loader, providers
from ..engine import files as FILES
from ..engine.loader import ASSET_SUFFIXES, DATA_WIDGETS, LANGUAGES, RTL, ToolDef, block_open, effective_layout, link_show, page_open, widget as find_widget, widgets_of
from ..engine.store import Store, StoreError
from ..models import User
from ..security.deps import client_ip, current_user, has_tool, require_human

router = APIRouter(prefix="/api/engine", tags=["engine"])


def lang_of(lang: Optional[str]) -> str:
    return lang if lang in LANGUAGES else "en"


def can_use(db: Session, me: User, t: ToolDef) -> bool:
    access = t.manifest.get("access") or {}
    if access.get("everyone"):
        return True
    if me.role in (access.get("roles") or []):
        return True
    slug = access.get("tool")
    return bool(slug) and has_tool(db, me, slug)


def _tool(db: Session, me: User, slug: str, request: Request) -> ToolDef:
    t = loader.get(slug)
    if t is None:
        raise HTTPException(404, "That tool isn't here.")
    if not can_use(db, me, t):
        log(db, "tool_denied", actor=me, target=slug, outcome="blocked", ip=client_ip(request))
        raise HTTPException(403, "You don't have access to this tool.")
    return t


def _page(t: ToolDef, name: str, me: Optional[User] = None, db: Optional[Session] = None, request: Optional[Request] = None) -> dict:
    p = t.pages.get(name)
    if p is None:
        raise HTTPException(404, "That page isn't in this tool.")
    if me is not None and not page_open(p, me.role):
        if db is not None:
            log(db, "page_denied", actor=me, target=f"{t.slug}/{name}", outcome="blocked", ip=client_ip(request) if request else None)
        raise HTTPException(403, "This page is not for your role.")
    return p


def _block(db: Session, me: User, t: ToolDef, block: str, request: Request) -> dict:
    """One of the tool's own tables, if the person may open a page that shows it."""
    b = t.data.get(block)
    if b is None:
        raise HTTPException(404, "That data block isn't in this tool.")
    if not block_open(t, block, me.role):
        log(db, "page_denied", actor=me, target=f"{t.slug}/{block}", detail="table on pages not for this role", outcome="blocked", ip=client_ip(request))
        raise HTTPException(403, "This table is on pages that are not for your role.")
    return {**b, "_slug": t.slug, "_name": block}


def _team_ids(db: Session, me: User) -> list[int]:
    if not me.team_id:
        return [me.id]
    return [u for (u,) in db.query(User.id).filter(User.team_id == me.team_id).all()] or [me.id]


def only_theirs(db: Session, me: User, t: Optional[ToolDef]) -> bool:
    """The person was given this tool "only for what is theirs" (Users & roles → the tool → only theirs):
    the rows open to everyone are not open to them (1.0.63; the Mozambique controller sees Mozambique)."""
    slug = ((t.manifest.get("access") or {}).get("tool") if t else None) or ""
    if not slug or me.is_admin:
        return False
    from ..models import Tool, ToolAccess

    row = db.query(ToolAccess).join(Tool, Tool.id == ToolAccess.tool_id).filter(Tool.slug == slug, ToolAccess.user_id == me.id).first()
    return bool(row is not None and row.scope == "granted")


def _mine_scope(db: Session, b: dict, me: User, t: Optional[ToolDef] = None, _depth: int = 0) -> Any:
    """Who sees which rows of a table (1.0.54). all: everyone. mine: the rows a person added or is named
    in (a person column), and a manager their team's. team: the rows of the person's own team. members:
    the members named in another table. linked (1.0.63): whoever may see the row it links to. A table may
    also say everyone sees the rows where one column has a value (open, 1.0.63). Admins all."""
    scope = b.get("scope", "all")
    if scope == "all" or me.is_admin:
        return None
    t = t or (loader.get(b["_slug"]) if b.get("_slug") else None)
    if scope == "linked":
        lk = next((c for c in (b.get("columns") or []) if isinstance(c, dict) and c.get("key") == b.get("link") and c.get("type") == "link"), None)
        tgt = (t.data.get(lk.get("table")) if (t and lk) else None)
        if tgt is None or _depth > 2:
            return {"users": [], "me": me.id, "cols": []}  # nothing to follow: nothing but admins
        inner = _mine_scope(db, {**tgt, "_slug": t.slug, "_name": lk["table"]}, me, t, _depth + 1)
        if inner is None:
            return None
        return {"users": [], "me": me.id, "cols": [], "linked": {"col": lk["key"], "table": lk["table"], "columns": tgt.get("columns") or [], "inner": inner}}
    cols = [c["key"] for c in (b.get("columns") or []) if isinstance(c, dict) and c.get("type") == "person"]
    op = b.get("open") if isinstance(b.get("open"), dict) and b["open"].get("field") and not only_theirs(db, me, t) else None
    if scope == "members":
        # shared with the active members named in another table (a job's people): only them, and whoever added it
        out = {"users": [me.id], "me": me.id, "cols": cols, "via": b.get("members") or {}}
    else:
        users = _team_ids(db, me) if scope == "team" or me.is_manager else [me.id]
        out = {"users": users, "me": me.id, "cols": cols}
        sh = b.get("share") if scope == "mine" else None
        if isinstance(sh, dict) and sh.get("field"):
            # "mine, and the whole team sees the rows where Kind is Team" (1.0.55): a personal list and a team board in one table
            out["share"] = {"field": sh["field"], "value": sh.get("value"), "users": _team_ids(db, me)}
    if op:
        out["open"] = {"field": op["field"], "value": op.get("value")}
    return out


def _row_visible(db: Session, b: dict, me: User, row: Optional[dict]) -> bool:
    sc = _mine_scope(db, b, me)
    if row is None:
        return False
    if sc is None:
        return True
    if row.get("created_by") in sc["users"] or any(row.get(c) == me.id for c in sc["cols"]):
        return True
    sh = sc.get("share")
    if sh and str(row.get(sh["field"])) == str(sh.get("value")) and row.get("created_by") in sh["users"]:
        return True
    if (sc.get("via") or sc.get("linked") or sc.get("open")) and b.get("_slug") and b.get("_name"):
        return Store(b["_slug"]).row_visible(b["_name"], b.get("columns") or [], int(row.get("id") or 0), sc)
    return False


def _ctx(me: User) -> dict:
    """What a block's "only rows where …" may say: {{person.id}} is the person looking, {{today}} today."""
    from ..engine.flows import local_now, local_today

    return {"person": {"id": me.id, "name": me.name, "email": me.email, "role": me.role, "team_id": me.team_id}, "today": local_today().isoformat(), "now": local_now().isoformat(timespec="minutes")}


def label_people(db: Session, rows: list, columns: list) -> None:
    """A person column shows the person's name (row["_labels"][column])."""
    pcols = [c["key"] for c in columns or [] if isinstance(c, dict) and c.get("type") == "person"]
    if not pcols or not rows:
        return
    ids = {r.get(k) for r in rows if isinstance(r, dict) for k in pcols if isinstance(r.get(k), int)}
    names = {u.id: u.name for u in db.query(User).filter(User.id.in_(ids)).all()} if ids else {}
    for r in rows:
        for k in pcols:
            if isinstance(r.get(k), int):
                r.setdefault("_labels", {})[k] = names.get(r[k], f"#{r[k]} (gone)")


def _check_people(db: Session, me: User, columns: list, values: dict) -> None:
    """A person column takes an active person — for "who: team", one of the person's own team."""
    for c in columns or []:
        if c.get("type") != "person" or values.get(c["key"]) in (None, ""):
            continue
        try:
            uid = int(values[c["key"]])
        except (TypeError, ValueError):
            raise HTTPException(400, f"'{c['key']}' must be a person")
        u = db.get(User, uid)
        if u is None or not u.is_active or u.is_axl:
            raise HTTPException(400, f"'{c['key']}': that person is not here (any more)")
        if c.get("who") == "team" and not me.is_admin and u.team_id != me.team_id:
            raise HTTPException(400, f"'{c['key']}': only someone from your own team")


def _eq(b: dict, params: dict) -> dict:
    """The filters a table block takes from the query: one value per choice, link or yes/no column."""
    from ..engine.store import FILTERABLE

    keys = {c.get("key") for c in (b.get("columns") or []) if isinstance(c, dict) and c.get("type") in FILTERABLE}
    return {k: v for k, v in (params or {}).items() if k in keys}


def _ids(w: dict, params: dict) -> Optional[list]:
    """A block that shows one row of its table, named by the page's address (1.0.63: a company's page,
    #/tool?…&company=5 with row_param "company")."""
    k = w.get("row_param") if isinstance(w, dict) else None
    if not k or params.get(k) in (None, ""):
        return None
    try:
        return [int(params[k])]
    except (TypeError, ValueError):
        return [-1]


def _writes(t: ToolDef, me: User, block: dict) -> dict:
    """Who may add / edit / delete rows of a table block: the block's `write` — everyone (default),
    admins, or nobody — plus: a person edits and deletes their own rows, admins anyone's."""
    w = block.get("write", "everyone")
    if me.role == "viewer":  # 1.0.106: a viewer "can look, and change nothing at all" — the To-do's Add saved their rows
        return {"add": False, "edit": False, "delete": False, "any_row": False}
    add = w == "everyone" or (w == "admins" and me.is_admin) or (w == "managers" and (me.is_admin or me.is_manager))
    return {"add": add, "edit": add, "delete": add, "any_row": bool(me.is_admin)}


@router.get("/menu")
def menu(lang: str = "en", db: Session = Depends(get_db), me: User = Depends(current_user)):
    lang = lang_of(lang)
    reg = loader.registry()
    items = [x for x in (t.out(lang, role=me.role) for t in reg.tools.values() if can_use(db, me, t)) if not x.get("hidden") and x.get("pages")]
    items.sort(key=lambda x: (x["order"], x["title"].lower()))
    return {"tools": items, "lang": lang, "dir": "rtl" if lang in RTL else "ltr", "languages": list(LANGUAGES)}


@router.get("/tools/{slug}/pages/{page}")
def page(slug: str, page: str, request: Request, lang: str = "en", db: Session = Depends(get_db), me: User = Depends(current_user)):
    lang = lang_of(lang)
    t = _tool(db, me, slug, request)
    p = _page(t, page, me, db, request)
    return page_out(t, page, p, me, lang)


def page_out(t: ToolDef, name: str, p: dict, me: User, lang: str, *, all_pages: bool = False) -> dict:
    """The page as the renderer draws it: its layout, the tool's words, and for every block with a
    source what that source is and what the person may do with it."""
    lay = effective_layout(name, p)
    info: dict[str, Any] = {}
    for w in widgets_of(name, p):
        wid = str(w.get("id"))
        src = w.get("source") or {}
        if w.get("type") == "button":
            f = (t.flows or {}).get(str(w.get("flow") or "")) or {}
            info[wid] = {"flow": {"name": w.get("flow"), "title": t.text(f.get("title") or str(w.get("flow"))), "enabled": f.get("enabled", True)}}
            continue
        if w.get("type") == "metrics":
            from ..engine import metrics as MX

            info[wid] = {"metrics": [it.get("metric") for it in (w.get("items") or []) if isinstance(it, dict) and it.get("metric") in MX.METRICS]}
            continue
        if w.get("type") not in DATA_WIDGETS:
            continue
        if src.get("provider") and src["provider"] in providers.PROVIDERS:
            prov = providers.PROVIDERS[src["provider"]]
            info[wid] = {"provider": prov.describe(), "allowed": not prov.roles or me.role in prov.roles}
        elif src.get("data") and src["data"] in t.data:
            block = t.data[src["data"]]
            info[wid] = {"block": {"name": src["data"], **block}, "can": _writes(t, me, block)}
        elif src.get("api"):
            info[wid] = {"api": {"name": src["api"], "columns": src.get("columns") or []}}
        elif src.get("sheet"):
            info[wid] = {"sheet": {"name": src["sheet"], "columns": _sheet_columns(src["sheet"])}}
    return {"tool": t.out(lang, role=None if all_pages else me.role), "page": {"name": name, **{k: v for k, v in p.items() if k not in ("layout", "canvas")}, "layout": lay},
            "widgets": info, "strings": t.strings(lang), "lang": lang, "dir": "rtl" if lang in RTL else "ltr"}


def _sheet_columns(slug: str) -> list[dict]:
    from ..db import SessionLocal
    from ..engine import sheets

    with SessionLocal() as db:
        s = sheets.get(db, slug)
        return sheets.columns_of(s) if s else []


def _params(request: Request) -> dict:
    return {k: v for k, v in request.query_params.items() if k not in ("lang", "q", "sort", "desc", "limit", "offset")}


def _source_for(t: ToolDef, page: str, wid: str, me: Optional[User] = None, db: Optional[Session] = None, request: Optional[Request] = None) -> tuple[dict, dict]:
    p = _page(t, page, me, db, request)
    if wid:
        w = find_widget(page, p, wid)
        if w is None:
            raise HTTPException(404, "That block isn't on this page.")
    else:
        ws = [w for w in widgets_of(page, p) if (w.get("source") or {})]
        if not ws:
            raise HTTPException(404, "This page has no block with data.")
        w = ws[0]
    return w, (w.get("source") or {})


def widget_rows(db: Session, me: User, t: ToolDef, w: dict, src: dict, params: dict, *, q: str = "", sort: str = "", desc: bool = False, limit: int = 500, offset: int = 0) -> dict:
    """Rows for one block, whatever feeds it: a provider (Core's data), one of the tool's own tables, a
    warehouse sheet, or an API connection — then the block's own "only rows where …", its sort, and for
    Totals and charts a summary. The same function serves the live page and the Builder's preview."""
    if w.get("type") == "progress":
        return _progress_rows(db, me, t, w, src)
    if w.get("type") == "grid" and src.get("data"):
        from ..engine import grid as G  # 1.0.63: a grid from the tool's own tables

        return G.build(db, me, t, w, params)
    if src.get("grid"):
        from ..engine import grid as G

        return G.summary_rows(db, me, t, src, params)
    cond = [c for c in (w.get("where") or []) if isinstance(c, dict) and c.get("field")]
    wsort = w.get("sort") if isinstance(w.get("sort"), dict) else {}
    whole = bool(cond) or w.get("show") == "totals" or w.get("type") == "chart"
    if whole:
        r = _source_rows(db, me, t, w, src, params, q=q, sort=sort or wsort.get("key", ""), desc=desc if sort else bool(wsort.get("desc")), limit=5000, offset=0)
    else:
        r = _source_rows(db, me, t, w, src, params, q=q, sort=sort or wsort.get("key", ""), desc=desc if sort else bool(wsort.get("desc")), limit=limit, offset=offset)
    if not whole or not isinstance(r, dict) or not isinstance(r.get("rows"), list):
        return r
    from ..engine import flows as FL

    ctx = _ctx(me)
    rows = [x for x in r["rows"] if isinstance(x, dict) and all(FL.holds(c, ctx, x) for c in cond)] if cond else r["rows"]
    if not src.get("data") and (sort or wsort.get("key")):
        k, dsc = (sort, desc) if sort else (wsort.get("key"), bool(wsort.get("desc")))
        rows = sorted(rows, key=lambda x: (x.get(k) is None, x.get(k) if not isinstance(x.get(k), (dict, list)) else str(x.get(k))), reverse=dsc)
    out = {**r, "total": len(rows)}
    if w.get("show") == "totals" or w.get("type") == "chart":
        sums = {}
        for k in (w.get("totals") or []) + ([(w.get("value") or {}).get("of")] if (w.get("value") or {}).get("fn") == "sum" else []):
            if isinstance(k, str) and k:
                sums[k] = round(sum(float(FL._num(x.get(k)) or 0) for x in rows), 2)
        out["summary"] = {"count": len(rows), "sums": sums}
    out["rows"] = rows if w.get("type") == "chart" else rows[offset: offset + max(1, min(limit, 2000))]
    return out


def _progress_rows(db: Session, me: User, t: ToolDef, w: dict, src: dict) -> dict:
    """Progress (1.0.55): each row of one table (a goal) with how many rows of another table count towards
    it — "rows of Tasks where Status is Done and Finished is between the goal's From and To" — and its target."""
    from ..engine import flows as FL

    b = t.data.get(src.get("data"))
    cnt = w.get("count") or {}
    cb = t.data.get(cnt.get("data"))
    if b is None or cb is None:
        raise HTTPException(404, "A table this progress block counts is not in the tool.")
    st = Store(t.slug)
    goals = st.rows(src["data"], b.get("columns") or [], limit=200, mine=_mine_scope(db, b, me, t), tables=t.data)["rows"]
    gconds = [c for c in (w.get("where") or []) if isinstance(c, dict) and c.get("field")]
    if gconds:  # only some goals in this block (e.g. the team's, not each person's)
        goals = [g for g in goals if all(FL.holds(c, _ctx(me), g) for c in gconds)]
    label_people(db, goals, b.get("columns") or [])
    rows = st.rows(cnt["data"], cb.get("columns") or [], limit=5000, mine=_mine_scope(db, cb, me, t))["rows"]
    base = _ctx(me)
    for g in goals:
        ctx = {**base, "row": g}
        conds = [c for c in (cnt.get("where") or []) if isinstance(c, dict) and c.get("field")]
        n = sum(1 for r in rows if all(FL.holds(c, ctx, r) for c in conds))
        try:
            target = float(g.get(w.get("target")) or 0)
        except (TypeError, ValueError):
            target = 0.0
        g["_progress"] = {"count": n, "target": target, "reached": bool(target) and n >= target}
    return {"columns": b.get("columns") or [], "rows": goals, "total": len(goals)}


def _source_rows(db: Session, me: User, t: ToolDef, w: dict, src: dict, params: dict, *, q: str = "", sort: str = "", desc: bool = False, limit: int = 500, offset: int = 0) -> dict:
    if src.get("provider"):
        prov = providers.PROVIDERS.get(src["provider"])
        if prov is None:
            raise HTTPException(500, f"The block's provider '{src.get('provider')}' is not known to this server.")
        if prov.roles and me.role not in prov.roles:
            raise HTTPException(403, "This block's data is not for your role.")
        return prov.fn(db, me, {**(src.get("params") or {}), **(w.get("params") or {}), **params})
    if src.get("data"):
        b = t.data.get(src["data"])
        if b is None:
            raise HTTPException(404, "That data block isn't in this tool.")
        st = Store(t.slug)
        r = st.rows(src["data"], b.get("columns") or [], q=q, sort=sort, desc=desc, limit=limit, offset=offset, mine=_mine_scope(db, b, me, t), eq=_eq(b, params), tables=t.data, ids=_ids(w, params))
        label_people(db, r["rows"], b.get("columns") or [])
        return {"columns": b.get("columns") or [], **r}
    if src.get("sheet"):
        from ..engine import sheets

        return sheets.rows_for(db, me, src["sheet"])
    if src.get("api"):
        return api_rows(db, me, t, src, params)
    raise HTTPException(400, "This block has no source.")


# ---- API connections as a source ----------------------------------------------------------------
# One request per block per half-minute at most, whoever is looking: a page open on twenty screens
# must not be twenty calls to someone else's service.
API_CACHE_S = 30
_api_cache: dict[str, tuple[float, dict]] = {}
_api_lock = threading.Lock()


def _dig(obj: Any, path: str) -> Any:
    if not path or path in (".", "$"):
        return obj
    cur = obj
    for part in str(path).replace("[", ".").replace("]", "").split("."):
        if part == "":
            continue
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def api_rows(db: Session, me: User, t: ToolDef, src: dict, params: dict) -> dict:
    from ..connections import service as C
    from ..models import ApiConnection

    key = f"{t.slug}|{src.get('api')}|{src.get('path')}|{sorted((src.get('params') or {}).items())}|{sorted(params.items())}"
    with _api_lock:
        hit = _api_cache.get(key)
        if hit and time.time() - hit[0] < API_CACHE_S:
            return hit[1]
    c = db.query(ApiConnection).filter(ApiConnection.slug == str(src.get("api"))).one_or_none()
    if c is None:
        raise HTTPException(400, f"API connection '{src.get('api')}' does not exist — Admin → API connections.")
    if t.slug not in (c.tools or []):
        raise HTTPException(403, f"The connection '{c.name}' is not open to this tool — tick {t.slug} under Admin → API connections → Edit.")
    try:
        r = C.call(db, c, method=str(src.get("method") or "GET"), path=str(src.get("path") or ""), params={**(src.get("params") or {}), **params}, by=me, by_label=f"tool:{t.slug}")
    except C.ConnectionError_ as e:
        raise HTTPException(502, str(e))
    if not r.get("ok"):
        raise HTTPException(502, f"{c.name} answered {r.get('status')}.")
    rows_raw = _dig(r.get("json"), str(src.get("rows") or "."))
    if isinstance(rows_raw, dict):
        rows_raw = [rows_raw]
    if not isinstance(rows_raw, list):
        raise HTTPException(502, f"{c.name}'s answer has no list at '{src.get('rows')}'.")
    cols = [c_ for c_ in (src.get("columns") or []) if isinstance(c_, dict) and c_.get("key")]
    rows = []
    for i, item in enumerate(rows_raw[:2000]):
        row = {"id": i}
        for c_ in cols:
            v = _dig(item, str(c_.get("from") or c_["key"])) if isinstance(item, (dict, list)) else (item if c_["key"] == "value" else None)
            if isinstance(v, (dict, list)):
                v = json.dumps(v)[:500]
            row[c_["key"]] = v
        rows.append(row)
    out = {"columns": [{"key": c_["key"], "label": c_.get("label") or f"col.{c_['key']}", "type": c_.get("type", "text")} for c_ in cols], "rows": rows, "total": len(rows), "source": c.name}
    with _api_lock:
        _api_cache[key] = (time.time(), out)
    return out


@router.get("/tools/{slug}/data/{block}")
def data(slug: str, block: str, request: Request, page: str = "", widget: str = "", q: str = "", sort: str = "", desc: bool = False, limit: int = 500, offset: int = 0, db: Session = Depends(get_db), me: User = Depends(current_user)):
    """Rows for a block on a page (`page` + `widget`; without `widget`, the page's first block with
    data). Without `page`, `block` is one of the tool's own tables."""
    t = _tool(db, me, slug, request)
    if page:
        w, src = _source_for(t, page, widget, me, db, request)
        return widget_rows(db, me, t, w, src, _params(request), q=q, sort=sort, desc=desc, limit=limit, offset=offset)
    b = _block(db, me, t, block, request)
    st = Store(slug)
    r = st.rows(block, b.get("columns") or [], q=q, sort=sort, desc=desc, limit=limit, offset=offset, mine=_mine_scope(db, b, me, t), eq=_eq(b, _params(request)), tables=t.data)
    label_people(db, r["rows"], b.get("columns") or [])
    return {"columns": b.get("columns") or [], **r}


@router.get("/tools/{slug}/data/{block}/row/{rid}")
def one_row(slug: str, block: str, rid: int, request: Request, db: Session = Depends(get_db), me: User = Depends(current_user)):
    """One row of the tool's own table, if the person may see it (a grid's dot, a link in a mail)."""
    t = _tool(db, me, slug, request)
    b = _block(db, me, t, block, request)
    st = Store(slug)
    row = st.get(block, b.get("columns") or [], rid)
    if not _row_visible(db, b, me, row):
        raise HTTPException(404, "That row isn't here, or it is not yours to see.")
    st.label_links([row], b.get("columns") or [], t.data)
    label_people(db, [row], b.get("columns") or [])
    can = _writes(t, me, b)
    try:
        _own_or_admin(t, me, b, row, "edit", db)
        mine = True
    except HTTPException:
        mine = False
    return {"row": row, "block": {"name": block, **b}, "can": {**can, "this": mine}}


@router.get("/tools/{slug}/data/{block}/choices/{column}")
def link_choices(slug: str, block: str, column: str, request: Request, q: str = "", db: Session = Depends(get_db), me: User = Depends(current_user)):
    """What a link column may point at: the rows of its table the person may see, as {id, label}."""
    t = _tool(db, me, slug, request)
    b = _block(db, me, t, block, request)
    col = next((c for c in (b.get("columns") or []) if c.get("key") == column and c.get("type") == "link"), None)
    if col is None:
        raise HTTPException(404, "That is not a link column.")
    tgt = col.get("table")
    tb = t.data.get(tgt)
    if tb is None:
        raise HTTPException(404, "The table it points at isn't in this tool.")
    if not block_open(t, tgt, me.role):
        raise HTTPException(403, "The table it points at is on pages that are not for your role.")
    show = col.get("show") or link_show(tb)
    return {"table": tgt, "show": show, "choices": Store(slug).choices(tgt, tb.get("columns") or [], show, q=q.strip()[:80], mine=_mine_scope(db, {**tb, "_slug": slug, "_name": tgt}, me, t))}


@router.get("/tools/{slug}/data/{block}/people/{column}")
def people_choices(slug: str, block: str, column: str, request: Request, db: Session = Depends(get_db), me: User = Depends(current_user)):
    """Who a person column may name: the person's own team, or everyone (active people, never the AI)."""
    t = _tool(db, me, slug, request)
    b = _block(db, me, t, block, request)
    col = next((c for c in (b.get("columns") or []) if c.get("key") == column and c.get("type") == "person"), None)
    if col is None:
        raise HTTPException(404, "That is not a person column.")
    q = db.query(User).filter(User.is_active.is_(True), User.is_axl.is_(False))
    if col.get("who") == "team" and not me.is_admin:
        q = q.filter(User.team_id == me.team_id) if me.team_id else q.filter(User.id == me.id)
    return {"me": me.id, "choices": [{"id": u.id, "label": u.name} for u in q.order_by(User.name).limit(1000).all()]}


# ---- what a block can carry besides data: a picture, a piece of code ------------------------------


def asset_response(folder, name: str) -> FileResponse:
    if "/" in name or "\\" in name or ".." in name or not name.lower().endswith(ASSET_SUFFIXES):
        raise HTTPException(404, "No such file.")
    f = folder / "assets" / name
    if not f.is_file():
        raise HTTPException(404, "No such file.")
    media = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml", ".pdf": "application/pdf",
             ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}[f.suffix.lower()]
    disp = f'attachment; filename="{f.name}"' if f.suffix.lower() == ".docx" else "inline"  # a Word template is downloaded, never shown
    return FileResponse(f, media_type=media, headers={"Cache-Control": "private, max-age=3600", "Content-Disposition": disp, "X-Content-Type-Options": "nosniff"})


@router.get("/tools/{slug}/assets/{name}")
def asset(slug: str, name: str, request: Request, db: Session = Depends(get_db), me: User = Depends(current_user)):
    t = _tool(db, me, slug, request)
    return asset_response(t.path, name)


# A code block is its own small web page, shown in a sandboxed frame: it may run its own scripts and
# load from the web, but it has no origin of its own — it cannot read CBX's cookies, its pages or its
# API as the signed-in person. The frame's policy is set here, not by the app's own (stricter) one.
CODE_CSP = "default-src 'self' https: data: blob: 'unsafe-inline' 'unsafe-eval'; frame-ancestors 'self'"


def code_response(w: dict) -> HTMLResponse:
    code = w.get("code") if isinstance(w.get("code"), str) else ""
    if "<html" not in code.lower():
        code = f"<!doctype html><html><head><meta charset='utf-8'><style>body{{margin:0;font:14px/1.5 Inter,system-ui,sans-serif;color:#21262C}}</style></head><body>{code}</body></html>"
    return HTMLResponse(code, headers={"Content-Security-Policy": CODE_CSP, "Cache-Control": "no-store", "X-Frame-Options": "SAMEORIGIN"})


@router.get("/tools/{slug}/html/{page}/{widget}")
def code_block(slug: str, page: str, widget: str, request: Request, db: Session = Depends(get_db), me: User = Depends(current_user)):
    t = _tool(db, me, slug, request)
    w = find_widget(page, _page(t, page, me, db, request), widget)
    if w is None or w.get("type") != "code":
        raise HTTPException(404, "That code block isn't on this page.")
    return code_response(w)


class RowIn(BaseModel):
    values: dict


@router.post("/tools/{slug}/data/{block}")
def add_row(slug: str, block: str, body: RowIn, request: Request, db: Session = Depends(get_db), me: User = Depends(current_user)):
    t = _tool(db, me, slug, request)
    b = _block(db, me, t, block, request)
    if not _writes(t, me, b)["add"]:
        raise HTTPException(403, "You can't add rows here.")
    try:
        _check_people(db, me, b.get("columns") or [], body.values or {})
        row = Store(slug).insert(block, b.get("columns") or [], body.values or {}, me.id, unique=b.get("unique"))
    except StoreError as e:
        raise HTTPException(400, str(e))
    log(db, "tool_row_added", actor=me, target=f"{slug}/{block}", detail=f"row {row.get('id')}", ip=client_ip(request))
    from ..engine import flows as FL

    return _with_working(db, FL.on_row(db, t, block, "row_added", row, me), row)


def _with_working(db: Session, run_ids: list, row: Optional[dict]) -> Optional[dict]:
    """A rule the change started may carry on in the background (a slow job): the page is told which, so it
    can show the row again when the job is done."""
    from ..models import FlowRun

    working = [r.id for r in db.query(FlowRun).filter(FlowRun.id.in_(run_ids or [0])).all() if r.status == "waiting" and (r.waiting or {}).get("kind") == "working"]
    if row is not None and working:
        return {**row, "_working": working}
    return row


def _own_or_admin(t: ToolDef, me: User, b: dict, row: Optional[dict], what: str, db: Optional[Session] = None) -> None:
    """A person changes the rows they added or are named in (a person column); a manager their team's
    (on a table that is theirs or their team's); admins any."""
    if row is None:
        raise HTTPException(404, "That row isn't here any more.")
    w = _writes(t, me, b)
    if b.get("edit") == "seen" and what == "edit" and w[what] and db is not None and _row_visible(db, b, me, row):
        return  # 1.0.63: anyone who may see the row may change it (a document's expiry, a renewal) — not delete it
    named = any(row.get(c["key"]) == me.id for c in (b.get("columns") or []) if isinstance(c, dict) and c.get("type") == "person")
    team = bool(db is not None and me.is_manager and b.get("scope", "all") != "all" and row.get("created_by") in _team_ids(db, me))
    if not w[what] or (not w["any_row"] and row.get("created_by") != me.id and not named and not team):
        raise HTTPException(403, f"You can't {what} this row.")


@router.patch("/tools/{slug}/data/{block}/{rid}")
def edit_row(slug: str, block: str, rid: int, body: RowIn, request: Request, db: Session = Depends(get_db), me: User = Depends(current_user)):
    t = _tool(db, me, slug, request)
    b = _block(db, me, t, block, request)
    st = Store(slug)
    cols = b.get("columns") or []
    _own_or_admin(t, me, b, st.get(block, cols, rid), "edit", db)
    try:
        _check_people(db, me, cols, body.values or {})
        row = st.update(block, cols, rid, body.values or {}, unique=b.get("unique"))
    except StoreError as e:
        raise HTTPException(400, str(e))
    log(db, "tool_row_edited", actor=me, target=f"{slug}/{block}", detail=f"row {rid}: {', '.join(sorted((body.values or {}).keys()))}", ip=client_ip(request))
    from ..engine import flows as FL

    return _with_working(db, FL.on_row(db, t, block, "row_changed", row or {}, me), row)


@router.delete("/tools/{slug}/data/{block}/{rid}")
def delete_row(slug: str, block: str, rid: int, request: Request, db: Session = Depends(get_db), me: User = Depends(current_user)):
    t = _tool(db, me, slug, request)
    b = _block(db, me, t, block, request)
    st = Store(slug)
    cols = b.get("columns") or []
    _own_or_admin(t, me, b, st.get(block, cols, rid), "delete", db)
    refs = referrers(t, block, rid)
    if refs:
        raise HTTPException(409, "Other rows point at this one: " + ", ".join(f"{n} in {tb}" for tb, n in refs) + ". Change or remove those first.")
    ok = st.delete(block, cols, rid)
    if ok:
        FILES.remove(slug, block, rid)
    log(db, "tool_row_deleted", actor=me, target=f"{slug}/{block}", detail=f"row {rid}", ip=client_ip(request))
    return {"ok": ok}


def referrers(t: ToolDef, block: str, rid: int) -> list[tuple[str, int]]:
    """The rows of the tool's tables whose link columns point at this row."""
    st = Store(t.slug)
    out = []
    for name, b2 in t.data.items():
        n = sum(st.count_where(name, c["key"], rid, exclude_id=rid if name == block else None) for c in (b2.get("columns") or []) if isinstance(c, dict) and c.get("type") == "link" and c.get("table") == block)
        if n:
            out.append((name, n))
    return out


# ---- files on rows (1.0.51) ---------------------------------------------------------------------


def _file_col(b: dict, column: str) -> dict:
    col = next((c for c in (b.get("columns") or []) if c.get("key") == column and c.get("type") == "file"), None)
    if col is None:
        raise HTTPException(404, "That is not a file column.")
    return col


@router.post("/tools/{slug}/data/{block}/{rid}/files/{column}")
async def upload_file(slug: str, block: str, rid: int, column: str, request: Request, files: list[UploadFile] = File(...), db: Session = Depends(get_db), me: User = Depends(current_user)):
    """Put a file on a row (replacing the one there). Whoever may edit the row may do this."""
    t = _tool(db, me, slug, request)
    b = _block(db, me, t, block, request)
    col = _file_col(b, column)
    st = Store(slug)
    cols = b.get("columns") or []
    _own_or_admin(t, me, b, st.get(block, cols, rid), "edit", db)
    if len(files) != 1:
        raise HTTPException(400, "One file at a time.")
    file = files[0]
    limit = FILES.max_bytes(col)
    data = await file.read(limit + 1)
    keep = int(col.get("keep") or 0)
    cur = (st.get(block, cols, rid) or {}).get(column)
    cur = cur if isinstance(cur, dict) else None
    old_ids = ({str(cur.get("id"))} if cur and cur.get("id") else set()) | {str(x["id"]) for x in FILES.earlier_of(cur)}
    try:
        meta = FILES.save(slug, block, rid, col, file.filename or "file", data, me.id, utcnow().isoformat(), keep=old_ids if keep else None)
    except FILES.FileError as e:
        log(db, "tool_file_refused", actor=me, target=f"{slug}/{block}/{rid}", detail=f"{column}: {e}"[:300], outcome="blocked", ip=client_ip(request))
        raise HTTPException(400, str(e))
    if keep:
        # 1.0.63: the column keeps earlier versions — the file it replaced goes on top of them
        meta = FILES.with_new(meta, cur, keep)
        FILES.prune(slug, block, rid, column, {meta["id"], *(str(x["id"]) for x in meta.get("earlier") or [])})
    row = st.set_file(block, cols, rid, column, meta)
    log(db, "tool_file_added", actor=me, target=f"{slug}/{block}/{rid}", detail=f"{column}: {meta['name']} ({meta['size']:,} bytes)", ip=client_ip(request))
    from ..engine import flows as FL

    return _with_working(db, FL.on_row(db, t, block, "row_changed", row or {}, me), row)


@router.get("/tools/{slug}/data/{block}/{rid}/files/{column}")
def download_file(slug: str, block: str, rid: int, column: str, request: Request, v: str = "", db: Session = Depends(get_db), me: User = Depends(current_user)):
    t = _tool(db, me, slug, request)
    b = _block(db, me, t, block, request)
    _file_col(b, column)
    row = Store(slug).get(block, b.get("columns") or [], rid)
    if not _row_visible(db, b, me, row):
        raise HTTPException(404, "That row isn't here.")
    meta = row.get(column) if isinstance(row.get(column), dict) else None
    if v:  # an earlier version (1.0.63)
        meta = next((x for x in FILES.earlier_of(meta) if x.get("id") == v), None)
    p = FILES.path_of(slug, block, rid, meta)
    if p is None:
        raise HTTPException(404, "There is no file here.")
    from urllib.parse import quote

    name = str(meta.get("name") or p.name)
    log(db, "tool_file_downloaded", actor=me, target=f"{slug}/{block}/{rid}", detail=f"{column}: {name}", ip=client_ip(request))
    return FileResponse(p, media_type=FILES.MEDIA.get(str(meta.get("type")), "application/octet-stream"), headers={
        "Content-Disposition": f"attachment; filename=\"{quote(name, safe='')}\"; filename*=UTF-8''{quote(name, safe='')}",
        "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox", "Cache-Control": "private, no-store"})


@router.delete("/tools/{slug}/data/{block}/{rid}/files/{column}")
def delete_file(slug: str, block: str, rid: int, column: str, request: Request, db: Session = Depends(get_db), me: User = Depends(current_user)):
    t = _tool(db, me, slug, request)
    b = _block(db, me, t, block, request)
    _file_col(b, column)
    st = Store(slug)
    cols = b.get("columns") or []
    cur = st.get(block, cols, rid)
    _own_or_admin(t, me, b, cur, "edit", db)
    meta = (cur or {}).get(column) if isinstance((cur or {}).get(column), dict) else None
    earlier = FILES.earlier_of(meta)
    if earlier:  # the current version goes; the earlier ones stay where they were
        FILES.remove(slug, block, rid, column, only=str(meta.get("id") or "-"))
        row = st.set_file(block, cols, rid, column, {"earlier": earlier})
    else:
        FILES.remove(slug, block, rid, column)
        row = st.set_file(block, cols, rid, column, None)
    log(db, "tool_file_removed", actor=me, target=f"{slug}/{block}/{rid}", detail=column, ip=client_ip(request))
    return row


# ---- Mail about a row (1.0.63) -------------------------------------------------------------------
# A table's row button {"label": "Mail", "kind": "mail", "to": "company.responsible", "fields": [...]}: press
# it, type a message, it goes — to someone CBX knows (mailbox.allowed) — with which row it is about, the
# fields named, and a link straight back to the row. Recorded in the outbox; an account also gets it in
# Messages. The person pressing it must be able to see the row.


def _mail_action(t: ToolDef, page: str, wid: str, idx: int, me: User, db: Session, request: Request) -> tuple[dict, dict]:
    w, _src = _source_for(t, page, wid, me, db, request)
    acts = [a for a in (w.get("actions") or []) if isinstance(a, dict)]
    if not 0 <= idx < len(acts) or acts[idx].get("kind") != "mail":
        raise HTTPException(404, "That block has no Mail button there.")
    return w, acts[idx]


def _follow(db: Session, t: ToolDef, block: str, row: dict, path: str) -> tuple[str, str]:
    """An address and a name from a row: a column (a person → their account, text → the address), or
    link.column to follow one link first (company.responsible)."""
    b = t.data.get(block) or {}
    head, _, rest = (path or "").partition(".")
    col = next((c for c in b.get("columns") or [] if isinstance(c, dict) and c.get("key") == head), None)
    if col is None:
        return "", ""
    v = row.get(head)
    if rest and col.get("type") == "link" and isinstance(v, int):
        tgt = col.get("table")
        r2 = Store(t.slug).get(tgt, (t.data.get(tgt) or {}).get("columns") or [], v) if tgt in t.data else None
        return _follow(db, t, tgt, r2 or {}, rest) if r2 else ("", "")
    if col.get("type") == "person" and isinstance(v, int):
        u = db.get(User, v)
        return (u.email, u.name) if u is not None and u.is_active and u.email else ("", "")
    if col.get("type", "text") == "text" and v:
        return str(v), str(row.get("responsible_name") or v)
    return "", ""


def _row_lines(t: ToolDef, block: str, row: dict, fields: list, lang_: str = "en") -> list[str]:
    b = t.data.get(block) or {}
    cols = {c["key"]: c for c in b.get("columns") or [] if isinstance(c, dict) and c.get("key")}
    out = []
    for k in fields or []:
        c = cols.get(k)
        if not c:
            continue
        v = (row.get("_labels") or {}).get(k) if c.get("type") in ("link", "person") else row.get(k)
        if isinstance(v, dict):
            v = v.get("name") or ""
        if v in (None, ""):
            continue
        out.append(f"{t.text(c.get('label') or k, lang_)}: {t.text(f'status.{v}', lang_) if c.get('type') == 'calc' and t.text(f'status.{v}', lang_) != f'status.{v}' else v}")
    return out


@router.get("/tools/{slug}/data/{block}/{rid}/mail")
def row_mail_info(slug: str, block: str, rid: int, request: Request, page: str, widget: str, action: int = 0, db: Session = Depends(get_db), me: User = Depends(current_user)):
    from .. import mail as mail_mod
    from .. import mailbox
    from ..models import MailLog

    t = _tool(db, me, slug, request)
    b = _block(db, me, t, block, request)
    _w, a = _mail_action(t, page, widget, action, me, db, request)
    st = Store(slug)
    row = st.get(block, b.get("columns") or [], rid)
    if not _row_visible(db, b, me, row):
        raise HTTPException(404, "That row isn't here.")
    st.label_links([row], b.get("columns") or [], t.data)
    label_people(db, [row], b.get("columns") or [])
    email, name = "", ""
    for path in (a.get("to") if isinstance(a.get("to"), list) else [a.get("to")]):
        email, name = _follow(db, t, block, row, str(path or ""))
        if email:
            break
    ready, why = mail_mod.configured()
    sent = db.query(MailLog).filter(MailLog.ref == f"tool:{slug}/{block}/{rid}").order_by(MailLog.created_at.desc()).limit(10).all()
    users = {u.id: u.name for u in db.query(User).filter(User.id.in_({r.sent_by_id for r in sent if r.sent_by_id} or {0})).all()}
    return {"suggested": {"email": email, "name": name} if email else None, "recipients": mailbox.recipients_for_picker(db), "domain": mailbox.company_domain(),
            "mail_ready": ready, "mail_note": why, "about": _row_lines(t, block, row, a.get("fields") or []),
            "sent": [{"to": r.to, "at": r.created_at.isoformat() if r.created_at else None, "by": users.get(r.sent_by_id, ""), "ok": r.ok, "preview": r.preview[:200]} for r in sent]}


class RowMailIn(BaseModel):
    page: str
    widget: str
    action: int = 0
    to: str = ""
    message: str


@router.post("/tools/{slug}/data/{block}/{rid}/mail")
def row_mail(slug: str, block: str, rid: int, body: RowMailIn, request: Request, db: Session = Depends(get_db), me: User = Depends(current_user), _h: User = Depends(require_human)):
    from .. import mailbox
    from ..requests_flow import notify

    t = _tool(db, me, slug, request)
    b = _block(db, me, t, block, request)
    _w, a = _mail_action(t, body.page, body.widget, body.action, me, db, request)
    st = Store(slug)
    row = st.get(block, b.get("columns") or [], rid)
    if not _row_visible(db, b, me, row):
        raise HTTPException(404, "That row isn't here.")
    msg = (body.message or "").strip()
    if len(msg) < 2:
        raise HTTPException(400, "Write the message first.")
    to = (body.to or "").strip()
    if not to:
        raise HTTPException(400, "Choose who it goes to.")
    ok, why = mailbox.allowed(db, to)
    if not ok:
        raise HTTPException(400, why)
    st.label_links([row], b.get("columns") or [], t.data)
    label_people(db, [row], b.get("columns") or [])
    label = str(row.get(link_show(b)) or f"#{rid}")
    about = _row_lines(t, block, row, a.get("fields") or [])
    link = f"#/tool?t={slug}&p={body.page}&open={rid}"
    subject = f"{label} — {t.text(t.manifest.get('title', 'tool.title'))}"
    text = f"{me.name} wrote:\n\n{msg}\n\n" + ("\n".join(about) + "\n" if about else "") + mailbox.footer(db, me, link)
    r = mailbox.send(db, to=[to], subject=subject[:300], body=text, kind="tool", ref=f"tool:{slug}/{block}/{rid}", by=me, reply_to=me.email or "")
    log(db, "tool_row_mailed", actor=me, target=f"{slug}/{block}/{rid}", detail=f"to {to}: {msg[:160]}" + ("" if r.ok else f" — NOT SENT: {r.detail[:120]}"), outcome="ok" if r.ok else "failed", ip=client_ip(request))
    to_user = db.query(User).filter(User.email.ilike(to), User.is_active.is_(True)).first()
    if to_user is not None and to_user.id != me.id:
        notify(db, to_user, f"{me.name} — {label}", msg + ("\n\n" + "\n".join(about) if about else ""), link=link)
    if not r.ok:
        return {"ok": False, "message": f"Not sent — {r.detail}" + (" The note was delivered inside CBX-Connect instead." if to_user is not None and to_user.id != me.id else ""), "to": to}
    return {"ok": True, "message": f"Sent to {to_user.name if to_user else to}." + (" They also have it in their Messages here." if to_user is not None and to_user.id != me.id else ""), "to": to}


# ---- numbers and Home (1.0.46) ------------------------------------------------------------------
# Home is the tool folder `tools/home/`: one page per role, rows of Numbers blocks and lists, designed
# on the Builder's canvas. Each person may then rearrange their own copy: which numbers each Numbers
# block shows and in what order, and which blocks are hidden. That is kept in their prefs
# ("dashboard"), never in the folder, and "Reset" drops it.

HOME_SLUG = "home"


@router.get("/metrics")
def metric_values(keys: str = "", db: Session = Depends(get_db), me: User = Depends(current_user)):
    from ..engine import metrics as MX

    ks = [k for k in (keys or "").split(",") if k][:60]
    return {"values": MX.values(db, me, ks)}


@router.get("/metrics/catalogue")
def metric_catalogue(db: Session = Depends(get_db), me: User = Depends(current_user)):
    from ..engine import metrics as MX

    return {"metrics": MX.available(db, me), "groups": list(MX.GROUPS)}


def home_page_for(t: ToolDef, me: User) -> str:
    for name in (me.role, "admin" if me.is_admin else "", "manager" if me.is_manager else "", "user"):
        if name and name in t.pages:
            return name
    return t.manifest.get("home") or next(iter(t.pages), "")


def _mine(me: User, page: str) -> dict:
    d = (me.prefs or {}).get("dashboard") or {}
    if not isinstance(d, dict) or d.get("page") != page:
        return {}
    return {"tiles": d.get("tiles") or {}, "hidden": d.get("hidden") or []}


@router.get("/home")
def home(lang: str = "en", db: Session = Depends(get_db), me: User = Depends(current_user)):
    lang = lang_of(lang)
    t = loader.registry().tools.get(HOME_SLUG)
    if t is None:
        raise HTTPException(404, "The Home dashboard folder (tools/home) is missing or has problems.")
    name = home_page_for(t, me)
    out = page_out(t, name, t.pages[name], me, lang)
    # a list the person cannot read (a role or a grant they lack) is left off their Home, not shown empty
    for wid, inf in out["widgets"].items():
        prov = inf.get("provider") or {}
        if prov.get("tool") and not has_tool(db, me, prov["tool"]):
            inf["allowed"] = False
    out["mine"] = _mine(me, name)
    # 1.0.113: the owner's and admins' one line when this server's clock is off (cbx/security/clock.py); nobody else
    out["clock"] = ""
    if me.is_admin and not me.is_axl:
        from ..security import clock

        out["clock"] = clock.check(db)
    return out


class MineIn(BaseModel):
    tiles: dict = {}
    hidden: list = []
    reset: bool = False


@router.put("/home/mine")
def save_mine(body: MineIn, request: Request, db: Session = Depends(get_db), me: User = Depends(current_user)):
    from ..engine import metrics as MX

    t = loader.registry().tools.get(HOME_SLUG)
    if t is None:
        raise HTTPException(404, "The Home dashboard folder is missing.")
    name = home_page_for(t, me)
    prefs = dict(me.prefs or {})
    if body.reset:
        prefs.pop("dashboard", None)
        me.prefs = prefs
        log(db, "dashboard_reset", actor=me, target=name, ip=client_ip(request))
        return {"mine": {}}
    ids = {str(w.get("id")) for w in widgets_of(name, t.pages[name])}
    tiles = {}
    for wid, keys in (body.tiles or {}).items():
        if str(wid) in ids and isinstance(keys, list):
            tiles[str(wid)] = [k for k in keys if isinstance(k, str) and k in MX.METRICS][:24]
    hidden = [str(x) for x in (body.hidden or []) if str(x) in ids]
    prefs["dashboard"] = {"page": name, "tiles": tiles, "hidden": hidden}
    me.prefs = prefs
    log(db, "dashboard_saved", actor=me, target=name, detail=f"{sum(len(v) for v in tiles.values())} numbers, {len(hidden)} hidden", ip=client_ip(request))
    return {"mine": _mine(me, name)}


# 1.0.99: Entity Compliance's Export (GET /api/ec/export, cbx/api/ec_export_api.py) rides on the engine's router — the
# tool folder's pages are served from here, and registry.py is not changed for one route.
from .ec_export_api import router as _ec_export_router  # noqa: E402

extra_routers = (_ec_export_router,)
