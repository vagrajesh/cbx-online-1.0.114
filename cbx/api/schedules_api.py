"""Site Schedules API (1.0.89): agreements made from the site's Swiftnet PDFs, the template (admins), the people who
sign. The rules are in cbx/schedules/service.py — the same as CBX-Connect 2."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import StoredFile, User
from ..models.schedules import SsAgreement
from ..schedules import service as S
from ..schedules import template as T
from ..security.deps import require_tool
from ..warehouse import storage

router = APIRouter(prefix="/api/schedules", tags=["site-schedules"])
READ = require_tool(S.SLUG)
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def WRITE(me: User = Depends(READ)) -> User:  # noqa: N802 — reads like the other dependency
    if me.role == "viewer":
        raise HTTPException(403, "Viewers look; they don't change agreements.")
    return me


def _do(fn, *a):
    try:
        return fn(*a)
    except S.Refused as e:
        raise HTTPException(400, str(e)) from None
    except PermissionError as e:
        raise HTTPException(403, str(e)) from None


def _ag(db: Session, me: User, aid: int) -> SsAgreement:
    a = db.get(SsAgreement, aid)
    if a is None or not S.can_see(me, a) or a.status == "archived":
        raise HTTPException(404, "That agreement isn't there.")
    return a


async def _uploads(files: list[UploadFile]) -> list[tuple[str, bytes]]:
    return [(f.filename or "file.pdf", await f.read(S.MAX_MB * 1024 * 1024 + 1)) for f in files or []]


@router.get("")
def home(me: User = Depends(READ), db: Session = Depends(get_db)):
    n, _ = S.current_template(db)
    return {"agreements": [S.out(db, a) for a in S.visible(db, me)], "is_admin": me.is_admin, "can_write": me.role != "viewer", "template_version": n,
            "form": [{"section": s, "fields": [{"key": k, "label": lab, "kind": kd} for k, lab, kd in items]} for s, items in S.FORM],
            "leases": list(S.LEASE_CODES), "required": S.REQUIRED}


@router.post("")
async def create(files: list[UploadFile] = File(default=[]), me: User = Depends(WRITE), db: Session = Depends(get_db)):
    a = _do(S.create, db, me, await _uploads(files))
    return S.out(db, a, full=True)


@router.get("/template")
def template(me: User = Depends(READ), db: Session = Depends(get_db)):
    v = S.template_state(db)
    n, tpl = S.current_template(db)
    return {"current": n, "template": tpl, "versions": [{k: x[k] for k in ("n", "at", "by", "note")} for x in reversed(v["versions"])],
            "tokens": [{"key": k, "label": lab} for k, lab in T.TOKENS], "tables": T.TABLES, "sample": T.values(T.sample_agreement(), tpl=tpl),
            "sample_tables": T.sample_agreement()["tables"], "is_admin": me.is_admin}


class TemplateIn(BaseModel):
    template: dict
    note: str = ""


@router.post("/template")
def save_template(body: TemplateIn, me: User = Depends(WRITE), db: Session = Depends(get_db)):
    return {"current": _do(S.save_template, db, me, body.template, body.note)}


class VersionIn(BaseModel):
    n: int


@router.post("/template/use")
def use_version(body: VersionIn, me: User = Depends(WRITE), db: Session = Depends(get_db)):
    return {"current": _do(S.use_version, db, me, body.n)}


@router.post("/template/try")
def try_template(body: TemplateIn, me: User = Depends(READ), db: Session = Depends(get_db)):
    probs = T.check(body.template)
    if probs:
        raise HTTPException(400, " ".join(probs[:5]))
    data = T.to_docx(body.template, T.sample_agreement(), S.people(db))
    return Response(data, media_type=DOCX, headers={"Content-Disposition": 'attachment; filename="Site Schedule template - sample.docx"'})


@router.get("/people")
def people(me: User = Depends(READ), db: Session = Depends(get_db)):
    return S.people(db)


class PeopleIn(BaseModel):
    swift: list[dict] = []
    couser: list[dict] = []


@router.post("/people")
def save_people(body: PeopleIn, me: User = Depends(WRITE), db: Session = Depends(get_db)):
    return _do(S.save_people, db, me, body.model_dump())


@router.get("/files/{fid}")
def download(fid: int, me: User = Depends(READ), db: Session = Depends(get_db)):
    sf = db.get(StoredFile, fid)
    if sf is None or not S.can_get_file(db, me, sf):
        raise HTTPException(404, "There's no file here.")
    p = storage.abs_path(sf)
    if not p.is_file():
        raise HTTPException(410, "The file is no longer on the server.")
    S.downloaded(db, me, sf)
    db.commit()
    return FileResponse(p, filename=sf.name, headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"})


@router.get("/{aid}")
def one(aid: int, me: User = Depends(READ), db: Session = Depends(get_db)):
    return S.out(db, _ag(db, me, aid), full=True)


class AgreementIn(BaseModel):
    fields: Optional[dict] = None
    tables: Optional[dict] = None
    signing: Optional[dict] = None


@router.post("/{aid}")
def save(aid: int, body: AgreementIn, me: User = Depends(WRITE), db: Session = Depends(get_db)):
    a = _do(S.save, db, me, _ag(db, me, aid), body.model_dump(exclude_none=True))
    return S.out(db, a, full=True)


@router.post("/{aid}/read")
async def read(aid: int, files: list[UploadFile] = File(default=[]), me: User = Depends(WRITE), db: Session = Depends(get_db)):
    a = _ag(db, me, aid)
    _do(S.read, db, me, a, await _uploads(files))
    return S.out(db, a, full=True)


@router.post("/{aid}/build")
def build(aid: int, me: User = Depends(WRITE), db: Session = Depends(get_db)):
    a, _sf = _do(S.build, db, me, _ag(db, me, aid))
    return S.out(db, a, full=True)


@router.post("/{aid}/archive")
def archive(aid: int, me: User = Depends(WRITE), db: Session = Depends(get_db)):
    _do(S.archive, db, me, _ag(db, me, aid))
    return {"ok": True}
