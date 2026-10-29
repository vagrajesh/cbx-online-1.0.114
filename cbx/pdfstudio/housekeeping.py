"""PDF Studio housekeeping: nothing sits silently.

Every hour (`pdf-housekeeping` routine, registered on import — cbx.api.pdfstudio imports this
module the way it imports harvest):
  - batches with NO documents older than 24 h are deleted (audit `pdf_batch_pruned`) — a browser
    that created a batch and never posted a file, or a drop that failed on the first chunk;
  - batches stuck in `uploading` WITH documents but never started for 24 h are marked `failed` with
    the error "Upload never finished — start again" (audit `pdf_batch_stuck`), so the person sees
    a "Try again" button instead of a batch that looks alive.

    housekeeping.prune(db, now=None) → {"pruned": [...ids], "failed": [...ids]}   (tests call this)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..audit import log as audit
from ..db import session_scope, utcnow
from ..jobs import routine
from ..models import Batch, Document, User

log = logging.getLogger("cbx.pdfstudio.housekeeping")
EMPTY_AFTER = timedelta(hours=24)
STUCK_AFTER = timedelta(hours=24)
STUCK_ERROR = "Upload never finished — start again"


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def prune(db: Session, now: Optional[datetime] = None) -> dict:
    now = _aware(now) or utcnow()
    axl = db.query(User).filter(User.is_axl.is_(True)).one_or_none()
    pruned: list[int] = []
    failed: list[int] = []
    for b in db.query(Batch).filter(Batch.status.in_(["uploading", "queued", "failed"])).all():
        created = _aware(b.created_at)
        if created is None:
            continue
        n_docs = db.query(Document).filter(Document.batch_id == b.id).count()
        if n_docs == 0 and now - created >= EMPTY_AFTER:  # uploading / queued / failed-with-nothing
            pruned.append(b.id)
            audit(db, "pdf_batch_pruned", actor=axl, actor_label="axl→housekeeping", target=f"batch:{b.id}", detail=f"'{b.name}' had no PDFs after {int((now - created).total_seconds() // 3600)} h (status {b.status})")
            db.delete(b)
            continue
        if n_docs and b.status in ("uploading", "queued") and not (b.runs or []) and now - created >= STUCK_AFTER:
            b.status = "failed"
            b.step = "Failed"
            b.error = STUCK_ERROR
            failed.append(b.id)
            audit(db, "pdf_batch_stuck", actor=axl, actor_label="axl→housekeeping", target=f"batch:{b.id}", detail=f"'{b.name}': {n_docs} PDFs uploaded, never handed to C-EXA in {int((now - created).total_seconds() // 3600)} h", outcome="failed")
    db.commit()
    if pruned or failed:
        log.info("pdf housekeeping: pruned %s, marked stuck %s", pruned, failed)
    return {"pruned": pruned, "failed": failed}


@routine("pdf-housekeeping", 3600)
def housekeeping_routine() -> None:
    with session_scope() as db:
        prune(db)
