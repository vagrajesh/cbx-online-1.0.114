"""The warehouse as a source for tool blocks and rules (1.0.53): its master sheets.

A block with `"source": {"sheet": "<slug>"}` shows the rows of that master sheet — only the rows the
person's row-level rules allow (security.rls, deny by default: without a rule a person sees the rows
they added). Blocks only read a sheet; PDF Studio and Harvest are what write to it.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from ..models import MasterSheet, User

# a master sheet's column kinds → the engine's column types
CTYPE = {"text": "text", "number": "number", "money": "number", "date": "date", "list": "text", "filename": "text", "formula": "text"}


def columns_of(s: MasterSheet) -> list[dict]:
    out = []
    for c in s.columns or []:
        if isinstance(c, dict) and c.get("key"):
            out.append({"key": str(c["key"]), "label": str(c.get("label") or c["key"]), "type": CTYPE.get(str(c.get("ctype") or "text"), "text")})
    return out


def listing(db: Session) -> list[dict]:
    """Every active master sheet: what the Builder offers under "Warehouse"."""
    return [{"slug": s.slug, "name": s.name, "description": s.description or "", "columns": columns_of(s), "rows": len(s.rows or [])}
            for s in db.query(MasterSheet).filter(MasterSheet.is_active.is_(True)).order_by(MasterSheet.name).all()]


def get(db: Session, slug: str) -> Optional[MasterSheet]:
    return db.query(MasterSheet).filter(MasterSheet.slug == str(slug or ""), MasterSheet.is_active.is_(True)).first()


def rows_for(db: Session, me: User, slug: str) -> dict[str, Any]:
    """The rows of one sheet that `me` may see, as the engine's {columns, rows, total}."""
    from ..security.rls import filter_rows

    s = get(db, slug)
    if s is None:
        return {"columns": [], "rows": [], "total": 0, "note": f"There is no master sheet '{slug}' in the warehouse (any more)."}
    cols = columns_of(s)
    keys = [c["key"] for c in cols]
    rows = []
    for i, r in enumerate(filter_rows(db, me, "master_rows", s.rows or [])):
        if not isinstance(r, dict):
            continue
        row = {k: r.get(k) for k in keys}
        row["id"] = r.get("_id") or i + 1
        rows.append(row)
    rows.reverse()  # newest first, as the tool's own tables
    return {"columns": cols, "rows": rows, "total": len(rows), "source": s.name}
