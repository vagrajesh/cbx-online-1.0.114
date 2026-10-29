"""Data warehouse API: folder tree, folder contents, uploads (with duplicate detection), downloads,
previews, the inbox pipeline and OneDrive status. Admins see everything; users only their own folders."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..audit import log
from ..config import settings
from ..db import get_db
from ..util import read_capped
from ..models import Folder, StoredFile, Team, User
from ..security.deps import client_ip, current_user, require_admin, require_human, require_owner
from ..warehouse import folders as F
from ..warehouse import onedrive, storage
from ..warehouse.extract import text_of
from .schemas import iso

router = APIRouter(prefix="/api/warehouse", tags=["warehouse"])

MAX_UPLOAD = 200 * 1024 * 1024


# ----------------------------------------------------------------------------- helpers
def _file_out(f: StoredFile, db: Session, uploader: Optional[User] = None) -> dict:
    u = uploader or (db.get(User, f.uploaded_by_id) if f.uploaded_by_id else None)
    return {
        "id": f.id,
        "name": f.name,
        "kind": f.kind,
        "size": f.size,
        "pages": f.pages,
        "source": f.source,
        "folder_id": f.folder_id,
        "detected_as": f.detected_as,
        "goes_to": f.goes_to,
        "pipeline_status": f.pipeline_status,
        "uploaded_by": u.name if u else "",
        "uploaded_by_id": f.uploaded_by_id,
        "in_library": bool(f.library_copy_of) or (f.source != "result" and f.kind in storage.LIBRARY_FOR_KIND),
        "library": storage.LIBRARY_FOR_KIND.get(f.kind, "") if f.source != "result" else "",
        "synced": bool(f.remote_id),
        "created_at": iso(f.created_at),
    }


def _can_see_folder(db: Session, user: User, folder: Folder) -> bool:
    if user.is_admin or user.is_axl:
        return True
    return folder.user_id == user.id


def _can_download(db: Session, user: User, f: StoredFile) -> bool:
    if user.is_admin or user.is_axl:
        return True
    if f.uploaded_by_id == user.id:
        return True
    folder = db.get(Folder, f.folder_id)
    if folder is not None and folder.user_id == user.id:
        return True
    # a shared-library copy of a file from the user's own team
    if f.library_copy_of and user.team_id and f.team_id == user.team_id:
        return True
    # a certificate in a company's folder: whoever may see the company may open it
    if folder is not None and folder.entity_id:
        from ..docwatch import service as S
        from ..models.docs import Entity

        return S.can_see(user, db.get(Entity, folder.entity_id), db)
    return False


def _display_root() -> str:
    return f"OneDrive/{settings.onedrive_root}" if onedrive.enabled() else "Warehouse"


# ----------------------------------------------------------------------------- tree
@router.get("/tree")
def tree(db: Session = Depends(get_db), me: User = Depends(require_admin)):
    folders = db.query(Folder).order_by(Folder.path).all()
    counts = dict(db.query(StoredFile.folder_id, func.count(StoredFile.id)).group_by(StoredFile.folder_id).all())
    by_id = {f.id: f for f in folders}
    nodes = {}
    for f in folders:
        nodes[f.id] = {"id": f.id, "kind": f.kind, "name": f.name, "path": f.path, "team_id": f.team_id, "user_id": f.user_id, "files": counts.get(f.id, 0), "children": [], "synced": bool(f.remote_id), "created_at": iso(f.created_at)}
    roots = []
    for f in folders:
        n = nodes[f.id]
        if f.parent_id and f.parent_id in by_id:
            nodes[f.parent_id]["children"].append(n)
        else:
            roots.append(n)

    def total(n):
        n["total"] = n["files"] + sum(total(c) for c in n["children"])
        return n["total"]

    order = {"Inbox": 0, "Work": 1, "Results": 2}

    def sort_children(n):
        n["children"].sort(key=lambda c: (order.get(c["name"], 9) if c["kind"] == "sub" else 9, c["name"].lower()))
        for c in n["children"]:
            sort_children(c)

    for r in roots:
        total(r)
        sort_children(r)
    # Shared library first, Tools (a tool's own files, e.g. Entity Compliance/<Company>) second, Teams third
    roots.sort(key=lambda n: (0 if n["path"] == "Shared library" else 1 if n["path"] == "Tools" else 2 if n["path"] == "Teams" else 3, n["name"]))
    return {"roots": roots, "display_root": _display_root(), "onedrive": onedrive.status(db)}


@router.get("/mine")
def mine(db: Session = Depends(get_db), me: User = Depends(current_user)):
    pf = F.ensure_person_folders(db, me)
    subs = db.query(Folder).filter(Folder.parent_id == pf.id).order_by(Folder.id).all()
    counts = dict(db.query(StoredFile.folder_id, func.count(StoredFile.id)).group_by(StoredFile.folder_id).all())
    out = []
    for s in subs:
        last = db.query(func.max(StoredFile.created_at)).filter(StoredFile.folder_id == s.id).scalar()
        out.append({"id": s.id, "name": s.name, "path": s.path, "files": counts.get(s.id, 0), "modified": iso(last), "library": {"Inbox": "PDFs", "Work": "Workflows", "Results": ""}.get(s.name, "")})
    return {"folder": {"id": pf.id, "name": pf.name, "path": pf.path}, "subs": out, "display_root": _display_root(), "team": pf.team_id and db.get(Team, pf.team_id).name}


@router.get("/stats")
def stats(db: Session = Depends(get_db), me: User = Depends(require_admin)):
    originals = db.query(StoredFile).filter(StoredFile.library_copy_of.is_(None))
    n_files = originals.count()
    size = db.query(func.coalesce(func.sum(StoredFile.size), 0)).scalar() or 0
    teams = db.query(Folder).filter(Folder.kind == "team", Folder.path != "Teams").count()
    people = db.query(Folder).filter(Folder.kind == "person").count()
    waiting = originals.join(Folder, Folder.id == StoredFile.folder_id).filter(Folder.name == "Inbox", Folder.kind == "sub", StoredFile.pipeline_status.in_(("", "queued", "reading", "ocr"))).count()
    from datetime import datetime, timedelta, timezone

    month = datetime.now(timezone.utc) - timedelta(days=30)
    new_teams = db.query(Team).filter(Team.created_at >= month).count()
    return {"files": n_files, "gb": round(size / 1e9, 2), "size_label": (f"{size / 1e9:.1f} GB" if size >= 1e8 else f"{size / 1e6:.1f} MB"), "teams": teams, "people": people, "inbox_waiting": waiting, "teams_this_month": new_teams, "onedrive": onedrive.status(db)}


# ----------------------------------------------------------------------------- folder contents
@router.get("/folders/{fid}/files")
def folder_files(fid: int, request: Request, db: Session = Depends(get_db), me: User = Depends(current_user)):
    folder = db.get(Folder, fid)
    if folder is None:
        raise HTTPException(404, "That folder doesn't exist.")
    if not _can_see_folder(db, me, folder):
        log(db, "folder_denied", actor=me, target=folder.path, outcome="blocked", ip=client_ip(request))
        raise HTTPException(403, "That folder belongs to someone else.")
    subs = db.query(Folder).filter(Folder.parent_id == folder.id).order_by(Folder.id).all()
    counts = dict(db.query(StoredFile.folder_id, func.count(StoredFile.id)).group_by(StoredFile.folder_id).all())
    rows = []
    for s in subs:
        last = db.query(func.max(StoredFile.created_at)).filter(StoredFile.folder_id == s.id).scalar()
        lib = {"Inbox": "PDFs", "Work": "Workflows"}.get(s.name, "") if s.kind == "sub" else ""
        rows.append({"id": s.id, "row": "folder", "name": s.name, "kind": "Folder", "items": counts.get(s.id, 0), "library": lib, "own_only": s.kind == "sub" and s.name == "Results", "modified": iso(last), "path": s.path})
    files = db.query(StoredFile).filter(StoredFile.folder_id == folder.id).order_by(StoredFile.created_at.desc()).limit(500).all()
    users = {u.id: u for u in db.query(User).filter(User.id.in_({f.uploaded_by_id for f in files if f.uploaded_by_id} or {0})).all()}
    for f in files:
        d = _file_out(f, db, users.get(f.uploaded_by_id))
        d.update({"row": "file", "items": f.pages or None, "modified": d["created_at"], "own_only": f.source == "result"})
        rows.append(d)
    crumbs = folder.path.split("/")
    return {"folder": {"id": folder.id, "name": folder.name, "path": folder.path, "kind": folder.kind, "crumbs": crumbs}, "display_path": f"{_display_root()}/{folder.path}", "rows": rows}


# ----------------------------------------------------------------------------- upload
@router.post("/upload")
async def upload(request: Request, files: list[UploadFile] = File(...), folder_id: Optional[int] = Form(None), db: Session = Depends(get_db), me: User = Depends(current_user)):
    target: Optional[Folder] = None
    if folder_id:
        target = db.get(Folder, folder_id)
        if target is None:
            raise HTTPException(404, "That folder doesn't exist.")
        if not me.is_admin and target.user_id != me.id:
            raise HTTPException(403, "You can only upload into your own folders.")
        if target.kind not in ("sub", "library"):
            raise HTTPException(400, "Pick a folder inside a person (Inbox, Work or Results) or the shared library.")
    stored, dups = [], []
    for up in files:
        data = await read_capped(up, MAX_UPLOAD)
        if not data:
            continue
        text, pages = text_of(up.filename or "file", data)
        if target is not None:
            # already in the library → don't copy it into the library again; Results stay with the person
            mirror = target.kind != "library" and target.name != "Results"
            f = storage.store_in(db, target, me, up.filename or "file", data, text=text, pages=pages, mirror=mirror, source="result" if target.name == "Results" else "upload")
        else:
            f = storage.store(db, me, up.filename or "file", data, sub="Inbox", text=text, pages=pages)
        f.pipeline_status = "queued" if f.kind in ("pdf", "csv") and db.get(Folder, f.folder_id).name == "Inbox" else f.pipeline_status
        d = _file_out(f, db, me)
        d["duplicates"] = []
        for x in storage.duplicates_of(db, f):
            who = db.get(User, x.uploaded_by_id) if x.uploaded_by_id else None
            d["duplicates"].append({"id": x.id, "name": x.name, "uploaded_by": who.name if who else "someone", "at": iso(x.created_at), "exact": x.sha256 == f.sha256})
        if d["duplicates"]:
            x = d["duplicates"][0]
            d["duplicate_note"] = f"Looks like a copy of {x['name']} (uploaded by {x['uploaded_by']} on {(x['at'] or '')[:10]})."
            dups.append(d)
        stored.append(d)
        log(db, "file_uploaded", actor=me, target=f.rel_path, detail=f"{f.size} bytes" + (" · possible duplicate" if d["duplicates"] else ""), ip=client_ip(request))
    if not stored:
        raise HTTPException(400, "Those files were empty.")
    return {"files": stored, "duplicates": len(dups), "message": f"{len(stored)} file{'s' if len(stored) != 1 else ''} saved to your Inbox and the shared library." if target is None else f"{len(stored)} file{'s' if len(stored) != 1 else ''} saved."}


# ----------------------------------------------------------------------------- download / preview
def _get_file(db: Session, me: User, fid: int, request: Request) -> StoredFile:
    f = db.get(StoredFile, fid)
    if f is None:
        raise HTTPException(404, "That file doesn't exist any more.")
    if not _can_download(db, me, f):
        log(db, "file_denied", actor=me, target=f.rel_path, outcome="blocked", ip=client_ip(request))
        raise HTTPException(403, "That file belongs to someone else.")
    return f


@router.get("/files/{fid}")
def file_info(fid: int, request: Request, db: Session = Depends(get_db), me: User = Depends(current_user)):
    f = _get_file(db, me, fid, request)
    return _file_out(f, db)


@router.get("/files/{fid}/download")
def download(fid: int, request: Request, db: Session = Depends(get_db), me: User = Depends(current_user)):
    f = _get_file(db, me, fid, request)
    p = storage.abs_path(f)
    if not p.exists():
        raise HTTPException(404, "The file is missing on disk. Ask an admin to check the warehouse.")
    log(db, "file_downloaded", actor=me, target=f.rel_path, ip=client_ip(request))
    return FileResponse(p, filename=f.name)


# ----------------------------------------------------------------------------- pipeline
@router.get("/pipeline")
def pipeline_rows(db: Session = Depends(get_db), me: User = Depends(require_admin)):
    from ..warehouse.pipeline import _inbox_candidates  # noqa: F401 (import registers the routine)

    q = (
        db.query(StoredFile)
        .join(Folder, Folder.id == StoredFile.folder_id)
        .filter(Folder.kind == "sub", Folder.name == "Inbox", StoredFile.library_copy_of.is_(None), StoredFile.kind.in_(("pdf", "csv")))
        .order_by(StoredFile.created_at.desc())
        .limit(50)
        .all()
    )
    users = {u.id: u for u in db.query(User).all()}
    rows = []
    for f in q:
        u = users.get(f.uploaded_by_id)
        rows.append({"id": f.id, "file": f.name, "from": (u.name.split()[0] if u else ""), "detected_as": f.detected_as, "goes_to": f.goes_to, "status": f.pipeline_status or "queued", "at": iso(f.created_at), "meta": {k: v for k, v in (f.meta or {}).items() if k in ("ocr", "batch_id")}})
    return {"rows": rows}


# ----------------------------------------------------------------------------- onedrive
@router.get("/onedrive/status")
def onedrive_status(db: Session = Depends(get_db), me: User = Depends(current_user)):
    st = onedrive.status(db)
    if not me.is_admin:
        st["last_error"] = ""  # Graph error text is for admins, not for everyone
    return st


@router.post("/onedrive/test")
def onedrive_test(db: Session = Depends(get_db), me: User = Depends(require_owner)):
    res = onedrive.test_connection()
    log(db, "onedrive_test", actor=me, detail=res["message"][:200], outcome="ok" if res["ok"] else "failed")
    return res


# ---- 1.0.81: looking after files — rename, move, a new folder, the Bin (admins, and C-EXA for them) ---------------
# A tool's own folders (Tools/…, e.g. Entity Compliance's certificates) belong to the tool: nothing here moves a file
# into or out of them. "Delete" is the Bin: the file moves to Bin/ and keeps every link to it, so nothing that points
# at it breaks and it can be moved back. Emptying the Bin is not offered.

BIN = "Bin"


def _bin(db: Session) -> Folder:
    f = db.query(Folder).filter(Folder.path == BIN).one_or_none()
    if f is None:
        f = Folder(kind="library", name=BIN, path=BIN)
        db.add(f)
        db.flush()
    return f


def _movable(f: StoredFile, folder: Optional[Folder]) -> None:
    if folder is not None and folder.kind == "tool":
        raise HTTPException(400, "That file belongs to a tool (it is under Tools/) — change it in the tool itself.")


def _relocate(f: StoredFile, dest: Folder, name: Optional[str] = None) -> None:
    import shutil
    from pathlib import Path

    src = storage.abs_path(f)
    new_name = F.safe_name(Path(name or f.name).name)
    target = storage._unique(storage.root() / dest.path / new_name)
    target.parent.mkdir(parents=True, exist_ok=True)
    if src.exists():
        shutil.move(str(src), str(target))
    f.name, f.rel_path, f.folder_id, f.team_id = target.name, target.relative_to(storage.root()).as_posix(), dest.id, dest.team_id


class FileChange(BaseModel):
    name: Optional[str] = None
    folder_id: Optional[int] = None


@router.patch("/files/{fid}")
def change_file(fid: int, body: FileChange, request: Request, db: Session = Depends(get_db), me: User = Depends(require_admin), _h: User = Depends(require_human)):
    f = _get_file(db, me, fid, request)
    here = db.get(Folder, f.folder_id)
    _movable(f, here)
    dest = here
    if body.folder_id is not None and body.folder_id != f.folder_id:
        dest = db.get(Folder, body.folder_id)
        if dest is None:
            raise HTTPException(404, "No such folder.")
        _movable(f, dest)
    name = (body.name or "").strip() or None
    if name and "." not in name and "." in f.name:
        name += "." + f.name.rsplit(".", 1)[1]
    old = f.rel_path
    _relocate(f, dest, name)
    log(db, "file_moved", actor=me, target=old, detail=f"→ {f.rel_path}", ip=client_ip(request))
    return _file_out(f, db)


class FolderNew(BaseModel):
    parent_id: int
    name: str


@router.post("/folders")
def new_folder(body: FolderNew, request: Request, db: Session = Depends(get_db), me: User = Depends(require_admin), _h: User = Depends(require_human)):
    parent = db.get(Folder, body.parent_id)
    if parent is None:
        raise HTTPException(404, "No such folder.")
    if parent.kind == "tool":
        raise HTTPException(400, "Folders under Tools/ belong to their tool.")
    name = F.safe_name(body.name.strip())[:120]
    if not name:
        raise HTTPException(400, "Give the folder a name.")
    path = f"{parent.path}/{name}"
    if db.query(Folder).filter(Folder.path == path).one_or_none():
        raise HTTPException(400, f"{path} is already there.")
    f = Folder(kind="sub", name=name, path=path, parent_id=parent.id, team_id=parent.team_id, user_id=parent.user_id, entity_id=None)
    db.add(f)
    db.flush()
    (storage.root() / path).mkdir(parents=True, exist_ok=True)
    log(db, "folder_made", actor=me, target=path, ip=client_ip(request))
    return {"id": f.id, "name": f.name, "path": f.path}


@router.delete("/files/{fid}")
def to_bin(fid: int, request: Request, db: Session = Depends(get_db), me: User = Depends(require_admin), _h: User = Depends(require_human)):
    f = _get_file(db, me, fid, request)
    _movable(f, db.get(Folder, f.folder_id))
    old = f.rel_path
    _relocate(f, _bin(db))
    log(db, "file_binned", actor=me, target=old, detail=f"→ {f.rel_path}", ip=client_ip(request))
    return {"ok": True, "file": _file_out(f, db)}


@router.get("/find")
def find_files(q: str = "", limit: int = 50, db: Session = Depends(get_db), me: User = Depends(require_admin)):
    """Files by name (and folder path), newest first — for C-EXA and for the admin looking for one."""
    words = [w for w in (q or "").lower().split() if w][:6]
    rows = db.query(StoredFile).filter(StoredFile.library_copy_of.is_(None)).order_by(StoredFile.created_at.desc())
    for w in words:
        rows = rows.filter(func.lower(StoredFile.rel_path).contains(w))
    out = []
    for f in rows.limit(max(1, min(200, limit))).all():
        out.append({**_file_out(f, db), "path": f.rel_path})
    return {"files": out}
