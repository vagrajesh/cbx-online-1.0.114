"""The owner's document and the data backups (1.0.58). Owner only, a person (not C-EXA)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from .. import backup as BK
from .. import owner_doc
from ..audit import log
from ..config import settings
from ..db import get_db
from ..models import User
from ..security.deps import client_ip, require_human, require_owner

router = APIRouter(prefix="/api/owner", tags=["owner"])


@router.get("/document")
def document(request: Request, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    md = owner_doc.compile(db)
    log(db, "owner_document_viewed", actor=me, ip=client_ip(request))
    return {"markdown": md, "html": owner_doc.body_html(md)}


@router.get("/document.{fmt}")
def document_file(fmt: str, request: Request, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    from .. import VERSION

    md = owner_doc.compile(db)
    log(db, "owner_document_downloaded", actor=me, detail=fmt, ip=client_ip(request))
    name = f"CBX-Connect owner's document {VERSION}"
    if fmt == "html":
        return Response(owner_doc.to_html(md, name), media_type="text/html; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{name}.html"', "X-Content-Type-Options": "nosniff"})
    return Response(md, media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{name}.md"', "X-Content-Type-Options": "nosniff"})


@router.get("/backups")
def backups(me: User = Depends(require_owner)):
    return {"folder": str(BK.root()), "on": settings.backups, "at": settings.backup_at, "keep_days": settings.backup_keep_days, "snapshots": BK.snapshots()[:30]}


@router.post("/backups/run")
def backup_now(request: Request, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    from .. import jobs

    log(db, "data_backup_asked", actor=me, ip=client_ip(request))
    jobs.submit(BK.run, "asked", me.email)
    return {"started": True}
