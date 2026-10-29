"""PDF Studio → Harvest: point PDF Studio at a folder and let it run.

A HarvestSource is a folder (on this server / a network share, or in OneDrive) plus a template.
Every `every_minutes` the `harvest` routine scans it, stores the PDFs it has not seen (by sha256),
runs the EXISTING batch pipeline on them (pipeline.run_batch — no second extraction engine) and
keeps one HarvestRow per PDF with every value and its provenance. Rows also go to the source's own
master sheet ("Harvest · Telkom"). Date-range queries and exports are served from HarvestRow, so
"1 June to 1 July" is a query, not a re-read. A range can be re-run with the template's current
live profile; the previous cells stay in `history` and the master sheet rows are updated in place.

    harvest.run_source(source_id)            # scan + read new PDFs (never two at once per source)
    harvest.rerun_range(db, source, d1, d2)   # re-read stored PDFs with the current profile
    harvest.check_folder(kind, path, subs)    # "Check folder" button: how many PDFs would I see?
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import threading
import time
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from sqlalchemy.orm import Session

from .. import jobs
from ..audit import log as audit
from ..config import settings
from ..db import session_scope, utcnow
from ..jobs import routine
from ..models import Batch, Document, HarvestRow, HarvestSource, MasterSheet, StoredFile, Team, Template, User
from ..warehouse import storage
from . import export as E
from . import money as M
from .spans import quick_text

log = logging.getLogger("cbx.harvest")
_RUNNING: set[int] = set()
_lock = threading.Lock()
MAX_FILES_PER_RUN = 200  # a first run on a big folder proceeds in slices; the routine picks up the rest
SKIP_PREFIXES = ("~$", ".~")


class HarvestError(Exception):
    """A plain-English problem the source card can show."""


# ---- listing files -----------------------------------------------------------------------------------
class Found:
    """One PDF seen in a folder. `read()` fetches the bytes (may raise OSError / HarvestError)."""

    def __init__(self, name: str, origin: str, mtime: Optional[datetime], size: int, read: Callable[[], bytes]):
        self.name, self.origin, self.mtime, self.size, self.read = name, origin, mtime, size, read


def local_root(path: str) -> Path:
    """Tolerant of what people paste: quotes, forward or back slashes, UNC shares, long paths."""
    p = (path or "").strip().strip('"').strip("'")
    if os.name == "nt":
        p = p.replace("/", "\\")
        if len(p) > 240 and not p.startswith("\\\\?\\"):
            p = ("\\\\?\\UNC\\" + p[2:]) if p.startswith("\\\\") else "\\\\?\\" + p
    else:
        p = p.replace("\\", "/")
    return Path(p)


def list_local(path: str, include_subfolders: bool = True) -> tuple[list[Found], list[str]]:
    root = local_root(path)
    if not root.exists():
        raise HarvestError(f"I can't find the folder '{path}' from this server. Check the path, and that the server account can reach the share.")
    if not root.is_dir():
        raise HarvestError(f"'{path}' is a file, not a folder.")
    found: list[Found] = []
    skipped: list[str] = []
    try:
        it = root.rglob("*") if include_subfolders else root.glob("*")
        for p in sorted(it, key=lambda x: str(x).lower()):
            try:
                if not p.is_file() or p.suffix.lower() != ".pdf" or p.name.startswith(SKIP_PREFIXES):
                    continue
                st = p.stat()
            except OSError:
                skipped.append(p.name)
                continue
            mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
            found.append(Found(p.name, str(p), mtime, st.st_size, (lambda q=p: q.read_bytes())))
    except PermissionError:
        raise HarvestError(f"The server isn't allowed to read '{path}'. Give the service account read access to that folder.")
    except OSError as e:
        raise HarvestError(f"I couldn't read the folder '{path}': {str(e)[:120]}")
    return found, skipped


def list_onedrive(path: str, include_subfolders: bool = True) -> tuple[list[Found], list[str]]:
    """List PDFs under `path` (relative to the configured OneDrive root) through Microsoft Graph."""
    from ..warehouse import onedrive as OD

    if not settings.onedrive_enabled:
        raise HarvestError("OneDrive is switched off on this server (CBX_ONEDRIVE_ENABLED). Use a folder on the server or a network share, or ask an admin to connect OneDrive.")
    if not OD.configured():
        raise HarvestError("OneDrive is switched on but not fully set up (tenant, client id, secret and drive id). See docs/onedrive-setup.md.")
    import httpx

    found: list[Found] = []
    skipped: list[str] = []
    rel = (path or "").strip().strip("/").replace("\\", "/")

    def walk(folder_rel: str) -> None:
        url = OD._item_url(folder_rel, ":/children?$top=200")
        while url:
            try:
                r = httpx.get(url, headers=OD._headers(), timeout=30)
            except httpx.HTTPError as e:
                raise HarvestError(f"Could not reach Microsoft Graph: {e}")
            if r.status_code == 404:
                raise HarvestError(f"There is no folder '{path}' under '{settings.onedrive_root}' in OneDrive.")
            if r.status_code >= 400:
                raise HarvestError(f"OneDrive refused the folder listing ({r.status_code}): {OD._err(r)}")
            j = r.json()
            for item in j.get("value", []):
                name = item.get("name") or ""
                child_rel = f"{folder_rel}/{name}".strip("/")
                if "folder" in item:
                    if include_subfolders:
                        walk(child_rel)
                    continue
                if not name.lower().endswith(".pdf") or name.startswith(SKIP_PREFIXES):
                    continue
                mt = item.get("lastModifiedDateTime")
                try:
                    mtime = datetime.fromisoformat(mt.replace("Z", "+00:00")) if mt else None
                except ValueError:
                    mtime = None
                dl = item.get("@microsoft.graph.downloadUrl")
                content_url = OD._item_url(child_rel, ":/content")

                def read(dl=dl, content_url=content_url, name=name) -> bytes:
                    rr = httpx.get(dl, timeout=120, follow_redirects=True) if dl else httpx.get(content_url, headers=OD._headers(), timeout=120, follow_redirects=True)
                    if rr.status_code != 200:
                        raise HarvestError(f"Could not download '{name}' from OneDrive ({rr.status_code}).")
                    return rr.content

                found.append(Found(name, f"OneDrive:{settings.onedrive_root}/{child_rel}", mtime, int(item.get("size") or 0), read))
            url = j.get("@odata.nextLink")

    try:
        walk(rel)
    except OD.OneDriveError as e:
        raise HarvestError(str(e))
    return found, skipped


def list_files(kind: str, path: str, include_subfolders: bool = True) -> tuple[list[Found], list[str]]:
    if kind == "onedrive":
        return list_onedrive(path, include_subfolders)
    return list_local(path, include_subfolders)


def check_folder(kind: str, path: str, include_subfolders: bool = True) -> dict:
    """The 'Check folder' button. Never raises; says what it saw in plain English."""
    if not (path or "").strip():
        return {"ok": False, "count": 0, "sample": [], "message": "Type the folder path first."}
    try:
        found, skipped = list_files(kind, path, include_subfolders)
    except HarvestError as e:
        return {"ok": False, "count": 0, "sample": [], "message": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "count": 0, "sample": [], "message": f"I couldn't look in that folder: {str(e)[:140]}"}
    n = len(found)
    msg = f"I can see {n} PDF{'s' if n != 1 else ''}" + (" in that folder and its subfolders." if include_subfolders else " in that folder.")
    if not n:
        msg = "The folder is there but I see no PDFs in it" + (" or its subfolders." if include_subfolders else ". Tick 'include subfolders' if the PDFs are one level down.")
    if skipped:
        msg += f" {len(skipped)} file{'s' if len(skipped) != 1 else ''} could not be read (open in another program?)."
    return {"ok": True, "count": n, "sample": [f.name for f in found[:5]], "message": msg}


# ---- dates -------------------------------------------------------------------------------------------
def _iso_to_date(s: str) -> Optional[date]:
    m = re.match(r"^\s*(\d{4})-(\d{2})(?:-(\d{2}))?", str(s))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3) or 1))
    except ValueError:
        return None


def parse_doc_date(value) -> Optional[date]:
    """'2026-06-01', '2026-06', '2026-06-01 – 2026-06-30' (start), '01/06/2026', '01 Jun 2026'."""
    if value in (None, ""):
        return None
    d = _iso_to_date(value)
    if d:
        return d
    parsed = M.parse_date(str(value))
    return _iso_to_date(parsed) if parsed else None


def date_column_key(template: Template, source: Optional[HarvestSource] = None) -> Optional[str]:
    if source is not None and source.date_column and any(c.key == source.date_column for c in template.columns):
        return source.date_column
    return next((c.key for c in template.columns if c.ctype == "date"), None)


def doc_date_of(cells: dict, key: Optional[str], fallback: Optional[datetime]) -> Optional[date]:
    if key:
        cell = (cells or {}).get(key) or {}
        if cell.get("status") in E.ACCEPT:
            d = parse_doc_date(cell.get("value"))
            if d:
                return d
        d = parse_doc_date(cell.get("raw"))
        if d:
            return d
    return fallback.date() if fallback else None


def parse_range(frm: Optional[str], to: Optional[str]) -> tuple[Optional[date], Optional[date]]:
    def one(s, what):
        if not s:
            return None
        d = parse_doc_date(s)
        if d is None:
            raise HarvestError(f"'{s}' isn't a date I understand for '{what}'. Use yyyy-mm-dd.")
        return d

    a, b = one(frm, "from"), one(to, "to")
    if a and b and a > b:
        a, b = b, a
    return a, b


# ---- master sheet ------------------------------------------------------------------------------------
def _sheet_slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def ensure_master_sheet(db: Session, source: HarvestSource, template: Template) -> MasterSheet:
    if source.master_sheet_id:
        s = db.get(MasterSheet, source.master_sheet_id)
        if s is not None:
            if not s.is_active:
                s.is_active = True
            return s
    name = f"Harvest · {source.name}"
    s = db.query(MasterSheet).filter((MasterSheet.name == name) | (MasterSheet.slug == _sheet_slug(name))).one_or_none()
    if s is None:
        s = MasterSheet(name=name, slug=_sheet_slug(name), team_id=source.team_id, description=f"Every PDF harvested from {source.name}. Filled in by PDF Studio; one row per PDF.", columns=[{"key": c.key, "label": c.label, "ctype": c.ctype} for c in template.columns])
        db.add(s)
        db.flush()
    s.is_active = True
    source.master_sheet_id = s.id
    db.flush()
    return s


def _update_master_rows(db: Session, sheet: MasterSheet, template: Template, by_file: dict[int, HarvestRow]) -> int:
    """After a re-run: change the values of existing master rows in place (matched on the stored file)."""
    columns = E.columns_of(template)
    mapping, new_cols = E.map_columns(sheet, columns)
    if len(new_cols) != len(sheet.columns or []):
        sheet.columns = new_cols
    now = utcnow().isoformat()
    changed = 0
    for bucket in ("rows", "exceptions"):
        rows = [dict(r) for r in (getattr(sheet, bucket) or [])]
        for r in rows:
            fid = (r.get("_src") or {}).get("file")
            hr = by_file.get(fid)
            if hr is None:
                continue
            for c in columns:
                cell = (hr.cells or {}).get(c["key"]) or {}
                r[mapping[c["key"]]] = cell.get("value") if cell.get("status") in E.ACCEPT else None
            r["_at"] = now
            r["_src"] = dict(r.get("_src") or {}, doc=hr.document_id, rerun=True)
            changed += 1
        setattr(sheet, bucket, rows)
    sheet.updated_at = utcnow()
    return changed


# ---- the run -----------------------------------------------------------------------------------------
def is_running(source_id: int) -> bool:
    return source_id in _RUNNING


def submit_run(source_id: int) -> bool:
    """Queue a run (the "Run now" button and the routine). False when one is already going."""
    with _lock:
        if source_id in _RUNNING:
            return False
        _RUNNING.add(source_id)
    try:
        jobs.submit(_run_job, source_id)
    except Exception:
        with _lock:
            _RUNNING.discard(source_id)
        raise
    return True


def run_source(source_id: int) -> dict:
    """Run synchronously (tests, C-EXA). Refuses to overlap a live run of the same source."""
    with _lock:
        if source_id in _RUNNING:
            return {"skipped": "already running"}
        _RUNNING.add(source_id)
    return _run_job(source_id)


def _run_job(source_id: int) -> dict:
    try:
        with session_scope() as db:
            src = db.get(HarvestSource, source_id)
            if src is None:
                return {"skipped": "gone"}
            return _scan_and_read(db, src)
    finally:
        with _lock:
            _RUNNING.discard(source_id)


def _plain(e: BaseException) -> str:
    if isinstance(e, HarvestError):
        return str(e)
    return f"Something went wrong while harvesting: {str(e)[:200]}"


def _scan_and_read(db: Session, src: HarvestSource) -> dict:
    from . import pipeline

    t0 = time.time()
    owner = db.get(User, src.owner_id)
    template = db.get(Template, src.template_id)
    stats = dict(src.stats or {})
    src.status = "running"
    src.last_run_at = utcnow()
    db.commit()
    result = {"new": 0, "seen": 0, "skipped": 0, "rows": 0}
    try:
        if owner is None or template is None:
            raise HarvestError("This source's owner or template no longer exists. Edit the source and pick a template.")
        sheet = ensure_master_sheet(db, src, template)
        found, skipped = list_files(src.kind, src.path, src.include_subfolders)
        result["seen"] = len(found)
        result["skipped"] = len(skipped)
        have = {sha for (sha,) in db.query(HarvestRow.sha256).filter(HarvestRow.source_id == src.id).all()}
        batch: Optional[Batch] = None
        pending: dict[str, Found] = {}
        for item in found:
            if len(pending) >= MAX_FILES_PER_RUN:
                break
            try:
                data = item.read()
            except (OSError, HarvestError) as e:
                log.info("harvest %s: could not read %s: %s", src.name, item.name, e)
                result["skipped"] += 1
                continue
            if not data.startswith(b"%PDF"):
                result["skipped"] += 1
                continue
            sha = hashlib.sha256(data).hexdigest()
            if sha in have:
                continue
            have.add(sha)
            # a file stored by an earlier run that failed before its row was written is reused, not copied again
            f = next((x for x in db.query(StoredFile).filter(StoredFile.sha256 == sha, StoredFile.source == "harvest", StoredFile.library_copy_of.is_(None)).all() if (x.meta or {}).get("harvest_source") == src.id), None)
            if f is None:
                text, pages = quick_text(data)
                f = storage.store(db, owner, item.name, data, sub="Inbox", source="harvest", text=text, pages=pages, meta={"harvest_source": src.id, "origin": item.origin, "mtime": item.mtime.isoformat() if item.mtime else None})
                # Harvest reads these itself — keep them out of the inbox → detect pipeline
                f.pipeline_status, f.detected_as, f.goes_to = "added", f"Harvest · {src.name}", sheet.name
            pages = f.pages or 0
            if batch is None:
                batch = Batch(template_id=template.id, user_id=owner.id, team_id=src.team_id, name=f"Harvest · {src.name} · {utcnow().strftime('%d %b %Y')}", status="learning", step="Received", progress=0.02, ceiling_zar=settings.model_batch_ceiling_zar)
                db.add(batch)
                db.flush()
            d = Document(batch_id=batch.id, ordinal=len(pending) + 1, file_id=f.id, filename=f.name, sha256=sha, pages=pages, status="queued")
            db.add(d)
            pending[sha] = item
        db.commit()
        result["new"] = len(pending)
        if batch is not None:
            bid = batch.id
            audit(db, "harvest_batch_started", actor=owner, actor_label="axl→harvest", target=f"source:{src.id}", detail=f"{len(pending)} new PDFs → batch {bid}")
            db.commit()
            pipeline.run_batch(bid)  # the existing pipeline, in this thread, with its own session
            db.expire_all()
            batch = db.get(Batch, bid)
            if batch.status == "failed":
                raise HarvestError(f"Reading the new PDFs failed: {batch.error or 'unknown error'}")
            key = date_column_key(template, src)
            for d in batch.documents:
                item = pending.get(d.sha256)
                cells = (d.extraction.cells if d.extraction else {}) or {}
                row = HarvestRow(source_id=src.id, file_id=d.file_id, document_id=d.id, sha256=d.sha256, filename=d.filename, origin=item.origin if item else "", doc_date=doc_date_of(cells, key, item.mtime if item else None), cells=cells, row_score=d.score, status="ok" if d.status == "ok" else "review", profile_version_id=d.extraction.profile_version_id if d.extraction else None, history=[])
                db.add(row)
            db.flush()
            E.append_to_master(db, batch, template, sheet, owner)
            stats["last_batch_id"] = bid
            stats["runs"] = int(stats.get("runs") or 0) + 1
        result["rows"] = db.query(HarvestRow).filter(HarvestRow.source_id == src.id).count()
        stats.update({"files_seen": result["seen"], "last_new": result["new"], "new_files": int(stats.get("new_files") or 0) + result["new"], "skipped": result["skipped"], "rows": result["rows"], "last_duration_s": round(time.time() - t0, 1), "more_waiting": len(pending) >= MAX_FILES_PER_RUN})
        src.stats = stats
        src.status = "ok"
        src.last_error = ""
        db.commit()
        audit(db, "harvest_run", actor=owner, actor_label="axl→harvest", target=f"source:{src.id}", detail=f"{result['seen']} seen, {result['new']} new, {result['rows']} rows total")
        db.commit()
    except Exception as e:  # noqa: BLE001
        log.warning("harvest %s failed:\n%s", src.name, traceback.format_exc())
        db.rollback()
        src = db.get(HarvestSource, src.id)
        src.status = "error"
        src.last_error = _plain(e)[:500]
        src.stats = dict(src.stats or {}, last_duration_s=round(time.time() - t0, 1))
        db.commit()
        audit(db, "harvest_run", actor_label="axl→harvest", target=f"source:{src.id}", detail=src.last_error[:200], outcome="failed")
        db.commit()
        result["error"] = src.last_error
    return result


# ---- re-run a range with the current profile -------------------------------------------------------
def rows_in_range(db: Session, source: HarvestSource, d1: Optional[date], d2: Optional[date]):
    q = db.query(HarvestRow).filter(HarvestRow.source_id == source.id)
    if d1:
        q = q.filter(HarvestRow.doc_date >= d1)
    if d2:
        q = q.filter(HarvestRow.doc_date <= d2)
    return q


def rerun_range(db: Session, source: HarvestSource, d1: Optional[date], d2: Optional[date], user: Optional[User] = None) -> dict:
    """Re-read the stored PDFs whose doc_date falls in [d1, d2] with the template's current live
    profile. Previous cells are kept in each row's history; master sheet rows are updated in place."""
    from . import pipeline

    with _lock:
        if source.id in _RUNNING:
            raise HarvestError("This source is being harvested right now. Try again in a minute.")
        _RUNNING.add(source.id)
    try:
        rows = rows_in_range(db, source, d1, d2).order_by(HarvestRow.doc_date, HarvestRow.id).all()
        if not rows:
            raise HarvestError("There are no rows in that date range to re-run.")
        owner = db.get(User, source.owner_id)
        template = db.get(Template, source.template_id)
        sheet = ensure_master_sheet(db, source, template)
        span = f"{d1.isoformat() if d1 else 'start'} – {d2.isoformat() if d2 else 'today'}"
        batch = Batch(template_id=template.id, user_id=owner.id, team_id=source.team_id, name=f"Harvest · {source.name} · re-run {span}", status="learning", step="Received", progress=0.02, ceiling_zar=settings.model_batch_ceiling_zar)
        db.add(batch)
        db.flush()
        by_sha = {}
        for i, r in enumerate(rows, start=1):
            f = db.get(StoredFile, r.file_id)
            if f is None or not storage.abs_path(f).exists():
                continue
            db.add(Document(batch_id=batch.id, ordinal=i, file_id=r.file_id, filename=r.filename, sha256=r.sha256, pages=f.pages, status="queued"))
            by_sha[r.sha256] = r
        if not by_sha:
            db.delete(batch)
            raise HarvestError("The PDFs for those rows are missing from the warehouse, so they can't be re-read.")
        source.status = "running"
        db.commit()
        bid = batch.id
        pipeline.run_batch(bid)
        db.expire_all()
        batch = db.get(Batch, bid)
        source = db.get(HarvestSource, source.id)
        if batch.status == "failed":
            source.status = "error"
            source.last_error = f"Re-run failed: {batch.error or 'unknown error'}"
            db.commit()
            raise HarvestError(source.last_error)
        key = date_column_key(template, source)
        now = utcnow().isoformat()
        by_file: dict[int, HarvestRow] = {}
        changed = 0
        for d in batch.documents:
            r = by_sha.get(d.sha256)
            if r is None:
                continue
            cells = (d.extraction.cells if d.extraction else {}) or {}
            hist = list(r.history or [])
            hist.append({"at": now, "profile_version_id": r.profile_version_id, "row_score": r.row_score, "cells": r.cells or {}})
            r.history = hist[-10:]
            r.cells = cells
            r.row_score = d.score
            r.status = "ok" if d.status == "ok" else "review"
            r.document_id = d.id
            r.profile_version_id = d.extraction.profile_version_id if d.extraction else None
            r.doc_date = doc_date_of(cells, key, None) or r.doc_date
            by_file[r.file_id] = r
            changed += 1
        db.flush()
        n_master = _update_master_rows(db, sheet, template, by_file)
        stats = dict(source.stats or {})
        stats["last_rerun"] = {"at": now, "range": span, "rows": changed, "batch_id": bid, "accuracy": batch.accuracy}
        source.stats = stats
        source.status = "ok"
        source.last_error = ""
        db.commit()
        audit(db, "harvest_rerun", actor=user or owner, target=f"source:{source.id}", detail=f"{span}: {changed} rows re-read, {n_master} master rows updated, batch {bid}")
        db.commit()
        return {"rows": changed, "master_rows": n_master, "batch_id": bid, "accuracy": batch.accuracy, "range": span}
    finally:
        with _lock:
            _RUNNING.discard(source.id)


# ---- export ------------------------------------------------------------------------------------------
def export_rows(rows: list[HarvestRow], template: Template) -> list[dict]:
    """HarvestRows → the row shape export.workbook_bytes understands."""
    from . import scoring as SC

    columns = E.columns_of(template)
    out = []
    for i, r in enumerate(rows, start=1):
        cells = r.cells or {}
        _, k, n = SC.row_score(cells, columns)
        out.append({"document_id": r.document_id, "ordinal": i, "filename": r.filename, "date": r.doc_date.isoformat() if r.doc_date else "", "pages": 0, "scanned": False, "score": r.row_score, "correct": k, "of": n, "status": r.status, "cells": {c["key"]: cells.get(c["key"]) or {"value": None, "status": "miss"} for c in columns}, "validators": {}})
    return out


def workbook_bytes(source: HarvestSource, template: Template, rows: list[HarvestRow]) -> bytes:
    return E.workbook_bytes(None, template, rows=export_rows(rows, template))


# ---- the routine -------------------------------------------------------------------------------------
def due(src: HarvestSource, now: Optional[datetime] = None) -> bool:
    if not src.active or is_running(src.id):
        return False
    if src.last_run_at is None:
        return True
    now = now or utcnow()
    last = src.last_run_at if src.last_run_at.tzinfo else src.last_run_at.replace(tzinfo=timezone.utc)
    every = src.every_minutes if src.every_minutes is not None else 15
    return now - last >= timedelta(minutes=max(1, int(every)))


@routine("harvest", 60)
def harvest_routine() -> None:
    with session_scope() as db:
        ids = [s.id for s in db.query(HarvestSource).filter(HarvestSource.active.is_(True)).all() if due(s)]
    for sid in ids:
        submit_run(sid)
