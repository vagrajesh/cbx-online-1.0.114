"""Branding endpoints. `GET /api/branding`, `/logo` and `/favicon` are public — the sign-in page
needs them before anyone has signed in (they carry no secrets and no model names). Changing
anything is owner-only and must be a person."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import branding as B
from ..audit import log
from ..config import settings
from ..db import get_db
from ..util import read_capped
from ..models import User
from ..security.deps import client_ip, require_human, require_owner

router = APIRouter(prefix="/api/branding", tags=["branding"])


@router.get("")
def get_branding():
    return B.public()


def _serve(kind: str):
    p = B.logo_path() if kind == "logo" else B.favicon_path()
    if p is None:
        raise HTTPException(404, f"No {kind} has been set.")
    # the shell appends ?v=<stamp>, so a new upload shows straight away while the old one caches well
    return FileResponse(p, media_type=B.media_type(p), headers={"Cache-Control": "public, max-age=86400"})


@router.get("/logo")
def logo():
    return _serve("logo")


@router.get("/favicon")
def favicon():
    return _serve("favicon")


class BrandingIn(BaseModel):
    product: str | None = None
    org: str | None = None
    tagline: str | None = None
    ai_name: str | None = None
    accent: str | None = None


@router.patch("")
def patch_branding(body: BrandingIn, request: Request, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    before = B.current()
    try:
        after = B.save_text(**body.model_dump())
    except B.BrandingError as e:
        raise HTTPException(400, str(e)) from e
    changed = [f"{k} {before[k]!r} → {after[k]!r}" for k in B.KEYS if before[k] != after[k]]
    if before["ai_name"] != after["ai_name"]:
        from ..bootstrap import ensure_cexa

        ensure_cexa(db)  # the AI's account is renamed too, so the audit trail and Users page agree
    log(db, "branding_changed", actor=me, detail="; ".join(changed) or "no change", ip=client_ip(request))
    return B.public()


@router.post("/{kind}")
async def upload_image(kind: str, request: Request, files: list[UploadFile] = File(...), db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    if kind not in ("logo", "favicon"):
        raise HTTPException(404, "That isn't here.")
    up = files[0]
    data = await read_capped(up, settings.max_attachment_mb * 1024 * 1024)
    try:
        p = B.save_image(kind, data, up.filename or "")
    except B.BrandingError as e:
        raise HTTPException(400, str(e)) from e
    log(db, "branding_changed", actor=me, target=kind, detail=f"{kind} uploaded ({p.suffix.lstrip('.')}, {len(data)} bytes)", ip=client_ip(request))
    return B.public()


@router.delete("/{kind}")
def remove_image(kind: str, request: Request, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    if kind not in ("logo", "favicon"):
        raise HTTPException(404, "That isn't here.")
    B.clear_image(kind)
    log(db, "branding_changed", actor=me, target=kind, detail=f"{kind} removed — back to the default", ip=client_ip(request))
    return B.public()
