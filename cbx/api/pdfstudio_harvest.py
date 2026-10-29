"""PDF Studio → Harvest API. Included by cbx.api.pdfstudio (same /api/pdfstudio prefix).

Users: see the sources of their team, query rows by date, open the PDF behind a row, export.
Admins: add / edit / pause sources, check a folder, run now, re-run a date range."""
from __future__ import annotations

import io
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..audit import log
from ..db import get_db
from ..models import HarvestRow, HarvestSource, StoredFile, Template, User
from ..models.harvest import SOURCE_KINDS
from ..pdfstudio import export as E
from ..pdfstudio import harvest as H
from ..security.deps import client_ip, require_admin, require_human, require_tool
from ..security.rls import filter_rows
from ..warehouse import storage
from .schemas import iso

router = APIRouter(prefix="/harvest", tags=["pdfstudio-harvest"])
TOOL = require_tool("pdf-studio")
PAGE_MAX = 500


# ---- access ------------------------------------------------------------------------------------------------
def _source_q(db: Session, me: User):
    q = db.query(HarvestSource)
    if me.is_admin or me.is_axl:
        return q
    conds = [HarvestSource.owner_id == me.id]
    if me.team_id:
        conds.append(HarvestSource.team_id == me.team_id)
    return q.filter(or_(*conds))


def _get_source(db: Session, me: User, sid: int) -> HarvestSource:
    s = _source_q(db, me).filter(HarvestSource.id == sid).one_or_none()
    if s is None:
        raise HTTPException(404, "No such harvest source.")
    return s


def _can_see_all_rows(me: User, s: HarvestSource) -> bool:
    return me.is_admin or me.is_axl or s.owner_id == me.id


def _visible_rows(db: Session, me: User, s: HarvestSource, rows: list[HarvestRow]) -> list[HarvestRow]:
    """Owner and admins see every row. Team members see the rows their master-sheet row rules grant
    (the same rules as Master Sheets) — deny by default."""
    if _can_see_all_rows(me, s):
        return rows
    template = db.get(Template, s.template_id)
    columns = E.columns_of(template) if template else []
    shaped = []
    for r in rows:
        cells = r.cells or {}
        d = {"_by": s.owner_id, "_client": E._client_of(cells, columns), "_team": "", "_row": r}
        for c in columns:
            d[c["key"]] = (cells.get(c["key"]) or {}).get("value")
        shaped.append(d)
    return [d["_row"] for d in filter_rows(db, me, "master_rows", shaped)]


# ---- shapes ------------------------------------------------------------------------------------------------
def _source_out(s: HarvestSource, db: Session, me: User) -> dict:
    t = db.get(Template, s.template_id)
    n_rows = db.query(HarvestRow).filter(HarvestRow.source_id == s.id).count()
    n_review = db.query(HarvestRow).filter(HarvestRow.source_id == s.id, HarvestRow.status == "review").count()
    first = db.query(HarvestRow.doc_date).filter(HarvestRow.source_id == s.id, HarvestRow.doc_date.isnot(None)).order_by(HarvestRow.doc_date.asc()).first()
    last = db.query(HarvestRow.doc_date).filter(HarvestRow.source_id == s.id, HarvestRow.doc_date.isnot(None)).order_by(HarvestRow.doc_date.desc()).first()
    running = H.is_running(s.id)
    status = "running" if running else ("paused" if not s.active else s.status)
    out = {
        "id": s.id, "name": s.name, "template_id": s.template_id, "template": t.name if t else "", "kind": s.kind, "path": s.path if me.role != "user" else "",
        "include_subfolders": s.include_subfolders, "active": s.active, "every_minutes": s.every_minutes, "master_sheet_id": s.master_sheet_id,
        "date_column": s.date_column or (H.date_column_key(t, s) if t else "") or "", "last_run_at": iso(s.last_run_at), "status": status, "rows": n_rows, "review": n_review,
        "first_date": first[0].isoformat() if first and first[0] else None, "last_date": last[0].isoformat() if last and last[0] else None,
        "files_seen": (s.stats or {}).get("files_seen", 0), "last_new": (s.stats or {}).get("last_new", 0), "owner_id": s.owner_id, "can_manage": me.is_admin,
        "columns": [{"key": c.key, "label": c.label, "ctype": c.ctype} for c in t.columns] if t else [],
    }
    if me.role != "user":
        out.update({"last_error": s.last_error, "stats": s.stats or {}, "created_at": iso(s.created_at)})
    else:
        out["problem"] = _user_problem(s)
    return out


def _user_problem(s: HarvestSource) -> str:
    if s.status != "error" or not s.last_error:
        return ""
    return "The last run had a problem. An admin can see the details on this page."


def _row_out(r: HarvestRow, columns: list[dict], me: User) -> dict:
    cells = r.cells or {}
    out = {
        "id": r.id, "filename": r.filename, "date": r.doc_date.isoformat() if r.doc_date else None, "status": r.status, "file_id": r.file_id,
        "open_url": f"/api/pdfstudio/harvest/rows/{r.id}/pdf", "download_url": f"/api/warehouse/files/{r.file_id}/download",
        "cells": {c["key"]: _cell_out(cells.get(c["key"]), me) for c in columns}, "reruns": len(r.history or []), "updated_at": iso(r.updated_at),
    }
    if me.role != "user":
        out.update({"row_score": r.row_score, "profile_version_id": r.profile_version_id, "origin": r.origin, "document_id": r.document_id})
    return out


def _cell_out(cell: Optional[dict], me: User) -> dict:
    cell = cell or {"value": None, "status": "miss"}
    keys = ("value", "status", "page", "quote", "note") if me.role == "user" else ("value", "status", "page", "quote", "note", "score", "method", "quality", "raw")
    return {k: cell.get(k) for k in keys if k in cell or k in ("value", "status")}


# ---- sources -----------------------------------------------------------------------------------------------
@router.get("/sources")
def list_sources(db: Session = Depends(get_db), me: User = Depends(TOOL)):
    rows = _source_q(db, me).order_by(HarvestSource.name).all()
    return {"sources": [_source_out(s, db, me) for s in rows], "can_manage": me.is_admin, "onedrive": _onedrive_state()}


def _onedrive_state() -> dict:
    from ..config import settings
    from ..warehouse import onedrive as OD

    return {"enabled": bool(settings.onedrive_enabled), "connected": OD.enabled(), "root": settings.onedrive_root}


class SourceIn(BaseModel):
    name: str
    template_id: int
    kind: str = "local"
    path: str
    include_subfolders: bool = True
    every_minutes: int = 15
    active: bool = True
    date_column: str = ""


class SourcePatch(BaseModel):
    name: Optional[str] = None
    template_id: Optional[int] = None
    kind: Optional[str] = None
    path: Optional[str] = None
    include_subfolders: Optional[bool] = None
    every_minutes: Optional[int] = None
    active: Optional[bool] = None
    date_column: Optional[str] = None


def _check_kind(kind: str) -> str:
    if kind not in SOURCE_KINDS:
        raise HTTPException(400, "Pick 'local' (a folder or network share this server can reach) or 'onedrive'.")
    return kind


def _check_every(n: int) -> int:
    if n < 1 or n > 24 * 60:
        raise HTTPException(400, "Run every 1 to 1440 minutes.")
    return int(n)


def _check_date_column(t: Template, key: str) -> str:
    key = (key or "").strip()
    if key and not any(c.key == key and c.ctype == "date" for c in t.columns):
        raise HTTPException(400, "The date column must be one of the template's date columns.")
    return key


@router.post("/sources")
def create_source(body: SourceIn, request: Request, db: Session = Depends(get_db), me: User = Depends(require_admin), _h: User = Depends(require_human)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Give the source a name — usually the client or supplier, e.g. Telkom.")
    if db.query(HarvestSource).filter(HarvestSource.name == name).count():
        raise HTTPException(400, f"There is already a source called '{name}'. Sources are kept separate — pick another name.")
    t = db.get(Template, body.template_id)
    if t is None:
        raise HTTPException(404, "No such template.")
    if not body.path.strip():
        raise HTTPException(400, "Type the folder path the PDFs arrive in.")
    s = HarvestSource(name=name, template_id=t.id, kind=_check_kind(body.kind), path=body.path.strip(), include_subfolders=body.include_subfolders, active=body.active, every_minutes=_check_every(body.every_minutes), owner_id=me.id, team_id=me.team_id, date_column=_check_date_column(t, body.date_column), status="idle")
    db.add(s)
    db.flush()
    H.ensure_master_sheet(db, s, t)
    db.flush()
    log(db, "harvest_source_created", actor=me, target=f"source:{s.id}", detail=f"{name}: {s.kind} {s.path} every {s.every_minutes} min → template '{t.name}'", ip=client_ip(request))
    return _source_out(s, db, me)


@router.patch("/sources/{sid}")
def patch_source(sid: int, body: SourcePatch, request: Request, db: Session = Depends(get_db), me: User = Depends(require_admin), _h: User = Depends(require_human)):
    s = db.get(HarvestSource, sid)
    if s is None:
        raise HTTPException(404, "No such harvest source.")
    changes = []
    if body.name is not None and body.name.strip() and body.name.strip() != s.name:
        if db.query(HarvestSource).filter(HarvestSource.id != s.id, HarvestSource.name == body.name.strip()).count():
            raise HTTPException(400, "There is already a source with that name.")
        s.name = body.name.strip()
        changes.append("renamed")
    if body.template_id is not None and body.template_id != s.template_id:
        if db.get(Template, body.template_id) is None:
            raise HTTPException(404, "No such template.")
        if db.query(HarvestRow).filter(HarvestRow.source_id == s.id).count():
            raise HTTPException(400, "This source already has rows read with its template. Add a new source for a different template.")
        s.template_id = body.template_id
        changes.append("template")
    if body.kind is not None:
        s.kind = _check_kind(body.kind)
        changes.append(f"kind {s.kind}")
    if body.path is not None and body.path.strip():
        s.path = body.path.strip()
        changes.append("path")
    if body.include_subfolders is not None:
        s.include_subfolders = body.include_subfolders
    if body.every_minutes is not None:
        s.every_minutes = _check_every(body.every_minutes)
        changes.append(f"every {s.every_minutes} min")
    if body.date_column is not None:
        s.date_column = _check_date_column(db.get(Template, s.template_id), body.date_column)
        changes.append("date column")
    if body.active is not None and body.active != s.active:
        s.active = body.active
        s.status = "idle" if s.active else "paused"
        changes.append("resumed" if s.active else "paused")
    db.flush()
    log(db, "harvest_source_changed", actor=me, target=f"source:{s.id}", detail="; ".join(changes) or "no change", ip=client_ip(request))
    return _source_out(s, db, me)


@router.delete("/sources/{sid}")
def delete_source(sid: int, request: Request, db: Session = Depends(get_db), me: User = Depends(require_admin), _h: User = Depends(require_human)):
    s = db.get(HarvestSource, sid)
    if s is None:
        raise HTTPException(404, "No such harvest source.")
    if H.is_running(s.id):
        raise HTTPException(400, "This source is running right now. Pause it and try again in a minute.")
    n = db.query(HarvestRow).filter(HarvestRow.source_id == s.id).delete()
    name = s.name
    db.delete(s)
    db.flush()
    log(db, "harvest_source_deleted", actor=me, target=f"source:{sid}", detail=f"{name}: {n} rows removed; PDFs and the master sheet are kept", ip=client_ip(request))
    return {"ok": True, "rows_removed": n}


class CheckIn(BaseModel):
    kind: str = "local"
    path: str
    include_subfolders: bool = True


@router.post("/check-folder")
def check_folder(body: CheckIn, db: Session = Depends(get_db), me: User = Depends(require_admin)):
    return H.check_folder(_check_kind(body.kind), body.path, body.include_subfolders)


@router.post("/sources/{sid}/run")
def run_now(sid: int, request: Request, db: Session = Depends(get_db), me: User = Depends(require_admin), _h: User = Depends(require_human)):
    s = db.get(HarvestSource, sid)
    if s is None:
        raise HTTPException(404, "No such harvest source.")
    db.commit()
    started = H.submit_run(s.id)
    log(db, "harvest_run_requested", actor=me, target=f"source:{s.id}", detail="started" if started else "already running", ip=client_ip(request))
    db.commit()
    db.expire_all()
    s = db.get(HarvestSource, sid)
    return {"started": started, "source": _source_out(s, db, me)}


# ---- rows --------------------------------------------------------------------------------------------------
def _range(frm: Optional[str], to: Optional[str]):
    try:
        return H.parse_range(frm, to)
    except H.HarvestError as e:
        raise HTTPException(400, str(e))


@router.get("/sources/{sid}/rows")
def list_rows(sid: int, request: Request, frm: Optional[str] = Query(None, alias="from"), to: Optional[str] = None, q: str = "", page: int = 1, size: int = 100, db: Session = Depends(get_db), me: User = Depends(TOOL)):
    s = _get_source(db, me, sid)
    t = db.get(Template, s.template_id)
    columns = E.columns_of(t) if t else []
    d1, d2 = _range(frm, to)
    rows = H.rows_in_range(db, s, d1, d2).order_by(HarvestRow.doc_date.desc(), HarvestRow.id.desc()).all()
    rows = _visible_rows(db, me, s, rows)
    if q.strip():
        needle = q.strip().lower()
        rows = [r for r in rows if needle in r.filename.lower() or any(needle in str((c or {}).get("value", "")).lower() for c in (r.cells or {}).values())]
    total = len(rows)
    size = max(1, min(PAGE_MAX, size))
    page = max(1, page)
    chunk = rows[(page - 1) * size : page * size]
    undated = sum(1 for r in chunk if r.doc_date is None)
    return {"source": {"id": s.id, "name": s.name}, "columns": columns, "from": d1.isoformat() if d1 else None, "to": d2.isoformat() if d2 else None, "total": total, "page": page, "size": size, "rows": [_row_out(r, columns, me) for r in chunk], "undated": undated, "date_column": H.date_column_key(t, s) if t else None}


@router.get("/rows/{rid}/pdf")
def open_pdf(rid: int, request: Request, db: Session = Depends(get_db), me: User = Depends(TOOL)):
    r = db.get(HarvestRow, rid)
    if r is None:
        raise HTTPException(404, "No such row.")
    s = _get_source(db, me, r.source_id)
    if not _visible_rows(db, me, s, [r]):
        log(db, "file_denied", actor=me, target=f"harvest_row:{rid}", outcome="blocked", ip=client_ip(request))
        raise HTTPException(403, "That PDF belongs to a row you can't see.")
    f = db.get(StoredFile, r.file_id)
    if f is None or not storage.abs_path(f).exists():
        raise HTTPException(404, "The PDF is missing from the warehouse. Ask an admin to check the folder.")
    log(db, "file_downloaded", actor=me, target=f.rel_path, detail=f"harvest row {rid}", ip=client_ip(request))
    safe = re.sub(r'[^A-Za-z0-9 ._()-]+', "", f.name) or "document.pdf"
    return Response(content=storage.read_bytes(f), media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{safe}"', "Cache-Control": "private, max-age=600"})


@router.get("/sources/{sid}/export.xlsx")
def export_xlsx(sid: int, request: Request, frm: Optional[str] = Query(None, alias="from"), to: Optional[str] = None, db: Session = Depends(get_db), me: User = Depends(TOOL)):
    s = _get_source(db, me, sid)
    t = db.get(Template, s.template_id)
    if t is None:
        raise HTTPException(400, "This source's template is gone.")
    d1, d2 = _range(frm, to)
    rows = _visible_rows(db, me, s, H.rows_in_range(db, s, d1, d2).order_by(HarvestRow.doc_date, HarvestRow.id).all())
    data = H.workbook_bytes(s, t, rows)
    span = (f" {d1.isoformat()}" if d1 else "") + (f" to {d2.isoformat()}" if d2 else "")
    name = re.sub(r"[^A-Za-z0-9 ._()-]+", "", f"Harvest - {s.name}{span}.xlsx").strip()
    log(db, "harvest_export", actor=me, target=f"source:{s.id}", detail=f"{len(rows)} rows{span}", ip=client_ip(request))
    return StreamingResponse(io.BytesIO(data), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/sources/{sid}/rerun")
def rerun(sid: int, request: Request, frm: Optional[str] = Query(None, alias="from"), to: Optional[str] = None, db: Session = Depends(get_db), me: User = Depends(require_admin), _h: User = Depends(require_human)):
    s = db.get(HarvestSource, sid)
    if s is None:
        raise HTTPException(404, "No such harvest source.")
    d1, d2 = _range(frm, to)
    n = H.rows_in_range(db, s, d1, d2).count()
    if not n:
        raise HTTPException(400, "There are no rows in that date range to re-run.")
    if H.is_running(s.id):
        raise HTTPException(400, "This source is running right now. Try again in a minute.")
    log(db, "harvest_rerun_requested", actor=me, target=f"source:{s.id}", detail=f"{n} rows, {frm or 'start'} – {to or 'today'}", ip=client_ip(request))
    db.commit()
    from .. import jobs

    def job(source_id=s.id, uid=me.id):
        from ..db import session_scope

        with session_scope() as sdb:
            src = sdb.get(HarvestSource, source_id)
            try:
                H.rerun_range(sdb, src, d1, d2, sdb.get(User, uid))
            except H.HarvestError as e:
                src = sdb.get(HarvestSource, source_id)
                src.status, src.last_error = "error", str(e)
                sdb.commit()

    jobs.submit(job)
    db.expire_all()
    s = db.get(HarvestSource, sid)
    return {"started": True, "rows": n, "source": _source_out(s, db, me)}


