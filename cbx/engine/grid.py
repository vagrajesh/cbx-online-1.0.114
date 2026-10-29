"""A grid from the tool's own tables (1.0.63) — the Entity Compliance grid, made a Builder part.

Rows come from one table (companies), columns from another (document types), and each cell is the row of a
third table that links to both (the document), coloured by a worked-out status column (expiry):

    {"type": "grid", "source": {"data": "documents"},
     "rows":  {"data": "companies", "label": "name", "hide": "archived"},
     "cols":  {"data": "doc_types", "label": "short", "title": "name", "order": "order", "show": "shown"},
     "cell":  {"row": "company", "col": "doc_type", "status": "status", "date": "expiry"},
     "extra": [{"key": "responsible", "account": "responsible_account"}],   # shown next to the name; a
                                          # person (account) column makes a name "has an account here"
     "row_page": "company"}               # the page a name opens, with ?company=<id>

What a person sees is what the three tables' own rules let them see. A cell nobody filed yet is outstanding
(an empty dot that opens a new row with both links filled in). A page address ?company=5 narrows the grid to
that one row. The answer has the same shape as a grid provider's, plus each cell's row id or preset.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..models import User
from .loader import ToolDef, link_show, widgets_of
from .store import Store

DOT = {"ok": ("ok", "In date"), "no_expiry": ("ok", "Doesn't expire"), "expiring": ("warn", "Expiring soon"), "expired": ("bad", "Expired"),
       "needs_date": ("open", "Uploaded, expiry not set"), "missing": ("open", "Nothing filed yet"), "not_required": ("grey", "Not required")}
WORST = {"expired": 0, "expiring": 1, "missing": 2, "ok": 3, "empty": 4}
LEGEND = [{"dot": "ok", "label": "legend.in_date"}, {"dot": "warn", "label": "legend.expiring"}, {"dot": "bad", "label": "legend.expired"},
          {"dot": "open", "label": "legend.outstanding"}, {"dot": "grey", "label": "legend.not_required"}]


def problems(t: ToolDef, w: dict) -> list[str]:
    """What is wrong with a grid's settings, in plain words (the loader and the Builder say these)."""
    src = (w.get("source") or {}).get("data")
    rows, cols, cell = w.get("rows") or {}, w.get("cols") or {}, w.get("cell") or {}
    out = []
    cb = t.data.get(src or "")
    rb, kb = t.data.get(rows.get("data") or ""), t.data.get(cols.get("data") or "")
    if cb is None or rb is None or kb is None:
        return ["a grid needs three tables of the tool: the cells (source), the rows and the columns"]
    ccols = {c.get("key"): c for c in cb.get("columns") or [] if isinstance(c, dict)}
    if (ccols.get(cell.get("row")) or {}).get("type") != "link" or (ccols.get(cell.get("row")) or {}).get("table") != rows["data"]:
        out.append(f"the cells' link to {rows['data']} (cell.row) is not a link column of {src}")
    if (ccols.get(cell.get("col")) or {}).get("type") != "link" or (ccols.get(cell.get("col")) or {}).get("table") != cols["data"]:
        out.append(f"the cells' link to {cols['data']} (cell.col) is not a link column of {src}")
    st = ccols.get(cell.get("status")) or {}
    if st.get("type") != "calc" or (st.get("calc") or {}).get("fn") != "expiry":
        out.append(f"the cells' status (cell.status) must be a worked-out 'status of a dated document' column of {src}")
    rk = {c.get("key"): c for c in rb.get("columns") or [] if isinstance(c, dict)}
    for x in w.get("extra") or []:
        k, acc = _extra(x)
        if k not in rk:
            out.append(f"'{k}' (shown next to the name) is not a column of {rows['data']}")
        if acc and (rk.get(acc) or {}).get("type") != "person":
            out.append(f"'{acc}' (whether the name has an account) is not a person column of {rows['data']}")
    if rows.get("hide") and (rk.get(rows["hide"]) or {}).get("type") != "bool":
        out.append(f"rows.hide must be a yes/no column of {rows['data']}")
    kk = {c.get("key"): c for c in kb.get("columns") or [] if isinstance(c, dict)}
    if cols.get("show") and (kk.get(cols["show"]) or {}).get("type") != "bool":
        out.append(f"cols.show must be a yes/no column of {cols['data']}")
    if w.get("row_page") and w["row_page"] not in t.pages:
        out.append(f"the page a name opens ('{w['row_page']}') is not a page of this tool")
    return out


def _extra(x) -> tuple[str, str]:
    if isinstance(x, dict):
        return str(x.get("key") or ""), str(x.get("account") or "")
    return str(x or ""), ""


def _title(status: str, d: Optional[dict], date_col: str) -> str:
    colour, title = DOT.get(status, ("open", status))
    raw = (d or {}).get(date_col) if date_col else None
    if raw and status not in ("not_required", "no_expiry"):
        try:
            from ..util import local_today

            x = date.fromisoformat(str(raw)[:10])
            n = (x - local_today()).days
            title += f" — {'expired' if n < 0 else 'expires'} {x.day} {x.strftime('%b %Y')}" + (f" ({abs(n)} day{'s' if abs(n) != 1 else ''}{' ago' if n < 0 else ''})" if n else " (today)")
        except ValueError:
            pass
    return title


def build(db: Session, me: User, t: ToolDef, w: dict, params: Optional[dict] = None) -> dict:
    from ..api.engine_api import _mine_scope, label_people

    bad = problems(t, w)
    if bad:
        return {"columns": [], "rows": [], "summary": {}, "legend": LEGEND, "note": "; ".join(bad)}
    params = params or {}
    src = w["source"]["data"]
    R, C, X = w["rows"], w["cols"], w["cell"]
    st = Store(t.slug)
    blk = lambda name: {**t.data[name], "_slug": t.slug, "_name": name}  # noqa: E731
    cb, rb, xb = t.data[C["data"]], t.data[R["data"]], t.data[src]
    clabel = C.get("label") or link_show(cb)
    kcols = st.rows(C["data"], cb.get("columns") or [], limit=500, mine=_mine_scope(db, blk(C["data"]), me, t), sort=C.get("order") or clabel)["rows"]
    if C.get("show"):
        kcols = [c for c in kcols if c.get(C["show"]) is not False]
    columns = [{"id": str(c["id"]), "label": str(c.get(clabel) or c.get(C.get("title") or "") or f"#{c['id']}"), "title": str(c.get(C.get("title") or clabel) or "")} for c in kcols]
    ids = None
    if params.get(X["row"]) not in (None, ""):
        try:
            ids = [int(params[X["row"]])]
        except (TypeError, ValueError):
            ids = [-1]
    rlabel = R.get("label") or link_show(rb)
    rrows = st.rows(R["data"], rb.get("columns") or [], limit=2000, mine=_mine_scope(db, blk(R["data"]), me, t), sort=rlabel, ids=ids, tables=t.data)["rows"]
    if R.get("hide") and ids is None:
        rrows = [r for r in rrows if not r.get(R["hide"])]
    label_people(db, rrows, rb.get("columns") or [])
    docs = st.rows(src, xb.get("columns") or [], limit=10000, mine=_mine_scope(db, blk(src), me, t))["rows"]
    by: dict[tuple, dict] = {}
    for d in reversed(docs):  # newest last wins: the newest row of a pair is the one shown
        if d.get(X["row"]) and d.get(X["col"]):
            by[(d[X["row"]], d[X["col"]])] = d
    rcols = {c.get("key"): c for c in rb.get("columns") or [] if isinstance(c, dict)}
    total = {"companies": 0, "expired": 0, "expiring": 0, "outstanding": 0, "in_date": 0}
    out_rows = []
    for r in rrows:
        cells, n = {}, {k: 0 for k in ("expired", "expiring", "ok", "no_expiry", "needs_date", "missing", "not_required")}
        for c in columns:
            d = by.get((r["id"], int(c["id"])))
            s = str((d or {}).get(X["status"]) or "missing")
            if s == "archived":
                s, d = "missing", None
            n[s] = n.get(s, 0) + 1
            cells[c["id"]] = {"dot": DOT.get(s, ("open", s))[0], "status": s, "title": _title(s, d, X.get("date") or ""),
                              **({"row": d["id"]} if d else {"preset": {X["row"]: r["id"], X["col"]: int(c["id"])}})}
        summ = {"expired": n["expired"], "expiring": n["expiring"], "outstanding": n["missing"] + n["needs_date"], "in_date": n["ok"] + n["no_expiry"]}
        summ["worst"] = "expired" if summ["expired"] else "expiring" if summ["expiring"] else "missing" if summ["outstanding"] else "ok" if summ["in_date"] else "empty"
        extra = []
        for x in w.get("extra") or []:
            k, acc = _extra(x)
            col = rcols.get(k) or {}
            v = (r.get("_labels") or {}).get(k) if col.get("type") in ("person", "link") else r.get(k)
            if v in (None, "") and acc:
                v = (r.get("_labels") or {}).get(acc)  # no name written down: the account's name
            linked = (col.get("type") == "person" and r.get(k) is not None) or bool(acc and r.get(acc))
            extra.append({"key": k, "value": "" if v in (None, "") else str(v), "person": linked})
        out_rows.append({"id": r["id"], "label": str(r.get(rlabel) or f"#{r['id']}"), "cells": cells, "summary": summ, "worst": summ["worst"], "extra": extra,
                         "link": {"route": "tool", "params": {"t": t.slug, "p": w["row_page"], X["row"]: r["id"]}} if w.get("row_page") else None})
        total["companies"] += 1
        for k in ("expired", "expiring", "outstanding", "in_date"):
            total[k] += summ[k]
    out_rows.sort(key=lambda x: (WORST.get(x["worst"], 9), x["label"].lower()))
    return {"columns": columns, "rows": out_rows, "summary": total, "legend": LEGEND,
            "extra": [{"key": _extra(x)[0], "label": (rcols.get(_extra(x)[0]) or {}).get("label") or _extra(x)[0]} for x in w.get("extra") or []],
            "cells": {"block": src, "row": X["row"], "col": X["col"]}, "total": len(out_rows)}


def find(t: ToolDef, wid: str) -> Optional[dict]:
    """The grid part a Totals-tiles block reads its numbers from (source: {"grid": "<its id>"})."""
    for name, p in t.pages.items():
        for w in widgets_of(name, p):
            if str(w.get("id")) == str(wid) and w.get("type") == "grid":
                return w
    return None


def summary_rows(db: Session, me: User, t: ToolDef, src: dict, params: Optional[dict] = None) -> dict[str, Any]:
    g = find(t, str(src.get("grid") or ""))
    if g is None:
        return {"summary": {}, "rows": [], "columns": [], "note": f"no grid called '{src.get('grid')}' in this tool"}
    out = build(db, me, t, g, params)
    return {"summary": out.get("summary") or {}, "rows": [], "columns": [], "total": out.get("total", 0)}
