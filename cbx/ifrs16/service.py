"""IFRS 16 service: keep the source files, scan them for months, run the monthly workflow or the
ledger check in the background, and file the output workbooks in the person's Results.

The engine (cbx/ifrs16/engine, unmodified) is only ever called from here, with the same arguments
the old portal used: `run_workflow_from_files(la, movements, sequentra, workflow_month=, steps_path=,
codes_path=, rates_path=)` → `write_import_workbooks` + `write_monthly_report`; and
`run_ledger_check(ledger, as_at=, months_back=)` → `write_consolidated_workbook`.
"""
from __future__ import annotations

import calendar
import logging
import shutil
import tempfile
import traceback
from datetime import date
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from .. import jobs
from ..audit import log
from ..config import settings
from ..db import session_scope, utcnow
from ..models import IfrsRun, SourceBatch, StoredFile, User
from ..warehouse import storage
from . import CODES_PATH, RATES_PATH, scanning, template_info

logger = logging.getLogger("cbx.ifrs16")

MAX_MB = 100
MAX_BYTES = MAX_MB * 1024 * 1024
SOURCE = "ifrs16"
REPORT_NAME = "IFRS16_Report_%s.xlsx"
LEDGER_REPORT_NAME = "IFRS16_Consolidated_Report.xlsx"
# ---- helpers ---------------------------------------------------------------------------------------
def _scratch() -> Path:
    """A fresh scratch folder under DATA_DIR (Windows-safe, cleaned up by the caller)."""
    return Path(tempfile.mkdtemp(prefix="ifrs16_", dir=str(settings.path("tmp"))))


def _path_of(db: Session, fid: Optional[int]) -> Optional[str]:
    if not fid:
        return None
    f = db.get(StoredFile, fid)
    if f is None:
        return None
    p = storage.abs_path(f)
    return str(p) if p.exists() else None


def month_date(month: str) -> date:
    """'YYYY-MM' → first day of that month. Raises ValueError for anything else."""
    y, m = int(month[:4]), int(month[5:7])
    if len(month) != 7 or month[4] != "-" or not (1 <= m <= 12):
        raise ValueError(month)
    return date(y, m, 1)


def parse_as_at(text: str) -> Optional[date]:
    """The ledger check's 'as at' box: blank → None (the engine uses today); 'YYYY-MM' → the last day
    of that month; 'YYYY-MM-DD' → that day. Anything else raises ValueError."""
    s = (text or "").strip()
    if not s:
        return None
    if len(s) == 7:
        d = month_date(s)
        return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])
    return date.fromisoformat(s[:10])


def _file_out(f: Optional[StoredFile]) -> Optional[dict]:
    if f is None:
        return None
    return {"id": f.id, "name": f.name, "size": f.size}


def batch_out(db: Session, b: SourceBatch, users: Optional[dict[int, User]] = None) -> dict:
    files = {}
    ids = [i for i in (b.la_file_id, b.movements_file_id, b.sequentra_file_id, b.steps_file_id) if i]
    if ids:
        files = {f.id: f for f in db.query(StoredFile).filter(StoredFile.id.in_(ids)).all()}
    owner = (users or {}).get(b.user_id) or db.get(User, b.user_id)
    scan = b.scan or {}
    return {
        "id": b.id,
        "user_id": b.user_id,
        "owner": owner.name if owner else "",
        "status": b.status,
        "scan_error": b.scan_error,
        "months": scan.get("months", []),
        "la_rows": scan.get("la_rows", 0),
        "move_rows": scan.get("move_rows", 0),
        "steps_rows": scan.get("steps_rows", 0),
        "sequentra_rows": scan.get("sequentra_rows", 0),
        "movement_label": scan.get("movement_label", ""),
        "la": _file_out(files.get(b.la_file_id)),
        "movements": _file_out(files.get(b.movements_file_id)),
        "sequentra": _file_out(files.get(b.sequentra_file_id)),
        "steps": _file_out(files.get(b.steps_file_id)),
        "created_at": b.created_at.isoformat() if b.created_at else None,
    }


def run_out(db: Session, r: IfrsRun, users: Optional[dict[int, User]] = None) -> dict:
    files = []
    if r.output_file_ids:
        rows = {f.id: f for f in db.query(StoredFile).filter(StoredFile.id.in_(r.output_file_ids)).all()}
        files = [_file_out(rows[i]) for i in r.output_file_ids if i in rows]
    owner = (users or {}).get(r.user_id) or db.get(User, r.user_id)
    return {
        "id": r.id,
        "batch_id": r.batch_id,
        "user_id": r.user_id,
        "owner": owner.name if owner else "",
        "kind": r.kind,
        "month": r.month,
        "status": r.status,
        "error": r.error,
        "outputs": files,
        "metrics": r.metrics or {},
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
    }


# ---- step 1: keep the files and scan them --------------------------------------------------------
def create_batch(db: Session, user: User, files: dict[str, tuple[str, bytes]]) -> SourceBatch:
    """`files` = {"la": (name, bytes), "movements": ..., "sequentra"?: ..., "steps"?: ...}. The files
    are kept in the person's Inbox (mirrored to the shared library like any upload) so the batch can
    be re-run for another month without uploading again. Caller commits, then calls `submit_scan`."""
    stored: dict[str, Optional[int]] = {}
    for key in ("la", "movements", "sequentra", "steps"):
        item = files.get(key)
        if not item:
            stored[key] = None
            continue
        name, data = item
        f = storage.store(db, user, name, data, sub="Inbox", source=SOURCE, meta={"ifrs16": key})
        stored[key] = f.id
    b = SourceBatch(user_id=user.id, team_id=user.team_id, la_file_id=stored["la"], movements_file_id=stored["movements"], sequentra_file_id=stored["sequentra"], steps_file_id=stored["steps"], status="queued")
    db.add(b)
    db.flush()
    return b


def submit_scan(batch_id: int) -> None:
    jobs.submit(scan_job, batch_id)


def scan_job(batch_id: int) -> None:
    with session_scope() as db:
        b = db.get(SourceBatch, batch_id)
        if b is None:
            return
        b.status = "scanning"
        db.commit()
        paths = (_path_of(db, b.la_file_id), _path_of(db, b.movements_file_id), _path_of(db, b.sequentra_file_id), _path_of(db, b.steps_file_id))
        try:
            if not paths[0] or not paths[1]:
                scan, err = {}, "The stored source files are no longer available — upload them again."
            else:
                scan, err = scanning.scan_sources(*paths)
        except Exception as e:  # noqa: BLE001
            logger.error("ifrs16 scan %s failed:\n%s", batch_id, traceback.format_exc())
            scan, err = {}, f"Reading the upload failed: {str(e)[:300]}"
        b.scan = scan or {}
        b.scan_error = (err or "")[:400]
        b.status = "failed" if err else "ready"
        detail = err if err else f"{scan['move_rows']} movement rows across {len(scan['months'])} month(s), {scan['la_rows']} LA register rows"
        log(db, "ifrs16_scanned", actor=db.get(User, b.user_id), target=f"ifrs16-batch:{b.id}", detail=detail[:400], outcome="failed" if err else "ok")


# ---- step 2: generate a month --------------------------------------------------------------------
def create_workflow_run(db: Session, user: User, batch: SourceBatch, month: str) -> IfrsRun:
    r = IfrsRun(batch_id=batch.id, user_id=user.id, kind="workflow", month=month, status="queued")
    db.add(r)
    db.flush()
    return r


def submit_workflow(run_id: int) -> None:
    jobs.submit(workflow_job, run_id)


def workflow_job(run_id: int) -> None:
    import lease_engine  # noqa: engine module, unmodified

    with session_scope() as db:
        r = db.get(IfrsRun, run_id)
        if r is None:
            return
        b = db.get(SourceBatch, r.batch_id) if r.batch_id else None
        user = db.get(User, r.user_id)
        r.status = "running"
        db.commit()
        la_p, mv_p = (_path_of(db, b.la_file_id), _path_of(db, b.movements_file_id)) if b else (None, None)
        seq_p = _path_of(db, b.sequentra_file_id) if b else None
        steps_p = _path_of(db, b.steps_file_id) if b else None
        tmp = _scratch()
        try:
            if not la_p or not mv_p:
                raise ValueError("The stored source files for that upload are no longer available — upload them again.")
            wm = month_date(r.month)
            outdir = tmp / "out"
            outdir.mkdir()
            results = lease_engine.run_workflow_from_files(la_p, mv_p, seq_p, workflow_month=wm, steps_path=steps_p, codes_path=str(CODES_PATH), rates_path=str(RATES_PATH))
            written = lease_engine.write_import_workbooks(results, str(outdir))
            report = outdir / (REPORT_NAME % r.month)
            lease_engine.write_monthly_report(results, str(report))
            summary = template_info.run_summary(results, written)
            ids = []
            for p in sorted(outdir.iterdir()):
                if p.is_file():
                    f = storage.store(db, user, p.name, p.read_bytes(), sub="Results", source="result", meta={"ifrs16": "workflow", "month": r.month, "run": r.id})
                    ids.append(f.id)
            r.output_file_ids = ids
            r.metrics = {"workbooks": summary["files"], "recon": summary["recon"], "output_files": len(ids), "import_workbooks": len(written)}
            r.status = "done"
            r.error = ""
            if not written:
                r.error = f"{r.month} ran, but the engine found nothing to import for that month — only the report was written. Check you picked the right month and that the movement file holds the rows you expect."
            log(db, "ifrs16_generated", actor=user, target=f"ifrs16-run:{r.id}", detail=f"{r.month}: {len(ids)} workbook(s) from batch #{r.batch_id}")
        except Exception as e:  # noqa: BLE001
            logger.error("ifrs16 workflow run %s failed:\n%s", run_id, traceback.format_exc())
            r.status = "failed"
            r.error = f"Generating {r.month} failed: {str(e)[:400]}"
            log(db, "ifrs16_generated", actor=user, target=f"ifrs16-run:{r.id}", detail=r.error[:400], outcome="failed")
        finally:
            r.finished_at = utcnow()
            shutil.rmtree(tmp, ignore_errors=True)


# ---- ledger check ----------------------------------------------------------------------------------
def create_ledger_run(db: Session, user: User, filename: str, data: bytes, as_at: Optional[date], months_back: int) -> tuple[IfrsRun, str]:
    """Keep the ledger export in the person's Inbox (so the run can be traced) and queue the check.
    Returns (run, ledger_path)."""
    f = storage.store(db, user, filename, data, sub="Inbox", source=SOURCE, meta={"ifrs16": "ledger"})
    r = IfrsRun(user_id=user.id, kind="ledger", status="queued", metrics={"as_at": as_at.isoformat() if as_at else "", "months_back": months_back, "ledger_file_id": f.id})
    db.add(r)
    db.flush()
    return r, str(storage.abs_path(f))


def submit_ledger(run_id: int, ledger_path: str) -> None:
    jobs.submit(ledger_job, run_id, ledger_path)


def ledger_job(run_id: int, ledger_path: str) -> None:
    import ledger_check  # noqa: engine module, unmodified

    with session_scope() as db:
        r = db.get(IfrsRun, run_id)
        if r is None:
            return
        user = db.get(User, r.user_id)
        r.status = "running"
        db.commit()
        m = dict(r.metrics or {})
        as_at = date.fromisoformat(m["as_at"]) if m.get("as_at") else None
        months_back = int(m.get("months_back") or 12)
        tmp = _scratch()
        try:
            outdir = tmp / "out"
            outdir.mkdir()
            res = ledger_check.run_ledger_check(ledger_path, as_at=as_at, months_back=months_back)
            out_path = outdir / LEDGER_REPORT_NAME
            ledger_check.write_consolidated_workbook(res, str(out_path))
            f = storage.store(db, user, out_path.name, out_path.read_bytes(), sub="Results", source="result", meta={"ifrs16": "ledger", "run": r.id})
            overview = []
            try:
                for _, row in res["Report Overview"].iterrows():
                    overview.append({"item": str(row["Item"]), "value": str(row["Value"])})
            except Exception:  # noqa: BLE001
                pass
            r.output_file_ids = [f.id]
            m.update({"output_files": 1, "overview": overview[:20]})
            r.metrics = m
            r.status = "done"
            r.error = ""
            log(db, "ifrs16_ledger_checked", actor=user, target=f"ifrs16-run:{r.id}", detail=f"{Path(ledger_path).name}: report written")
        except Exception as e:  # noqa: BLE001
            logger.error("ifrs16 ledger run %s failed:\n%s", run_id, traceback.format_exc())
            r.status = "failed"
            r.error = f"Ledger check failed: {str(e)[:400]}"
            log(db, "ifrs16_ledger_checked", actor=user, target=f"ifrs16-run:{r.id}", detail=r.error[:400], outcome="failed")
        finally:
            r.finished_at = utcnow()
            shutil.rmtree(tmp, ignore_errors=True)
