"""Store and fetch files. Inputs go to the uploader's folder and are mirrored into the shared
library; results stay with the person. Dedupe by sha256 (exact) and text fingerprint (near)."""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Folder, StoredFile, User
from . import folders as F

KIND_BY_EXT = {".pdf": "pdf", ".xlsx": "xlsx", ".xlsm": "xlsx", ".xls": "xlsx", ".csv": "csv", ".docx": "docx", ".doc": "docx", ".json": "flow"}
LIBRARY_FOR_KIND = {"pdf": "PDFs", "flow": "Workflows", "xlsx": "Master sheets"}


def root() -> Path:
    p = settings.data_dir / "warehouse"
    p.mkdir(parents=True, exist_ok=True)
    return p


def abs_path(f: StoredFile) -> Path:
    return root() / f.rel_path


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def text_fingerprint(text: str) -> str:
    """Near-duplicate key: lowercase, digits and letters only, whitespace collapsed."""
    norm = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
    return hashlib.sha256(norm.encode()).hexdigest() if norm else ""


def _unique(path: Path) -> Path:
    """The next free name — 'report.pdf', 'report (2).pdf', … — as a plain answer (kept for callers that
    only look). Writing goes through `_create_unique`, which claims the name atomically."""
    if not path.exists():
        return path
    stem, suf, i = path.stem, path.suffix, 2
    while (p := path.with_name(f"{stem} ({i}){suf}")).exists():
        i += 1
    return p


def _create_unique(path: Path, data: bytes) -> Path:
    """Write `data` under `path` or the next free numbered name, claiming the name with O_EXCL so two
    uploads of the same name at the same moment get two files, not one overwriting the other."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stem, suf, i = path.stem, path.suffix, 1
    candidate = path
    while True:
        try:
            fd = os.open(str(candidate), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0))
        except FileExistsError:
            i += 1
            candidate = path.with_name(f"{stem} ({i}){suf}")
            continue
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        return candidate


def store(
    db: Session,
    user: User,
    filename: str,
    data: bytes,
    *,
    sub: str = "Inbox",
    source: str = "upload",
    mirror: bool = True,
    text: str = "",
    pages: int = 0,
    meta: Optional[dict] = None,
) -> StoredFile:
    folder = F.person_sub(db, user, sub)
    return store_in(db, folder, user, filename, data, source=source, mirror=mirror, text=text, pages=pages, meta=meta)


def store_in(db: Session, folder: Folder, user: Optional[User], filename: str, data: bytes, *, source="upload", mirror=True, text="", pages=0, meta=None) -> StoredFile:
    name = F.safe_name(Path(filename).name)
    ext = Path(name).suffix.lower()
    kind = KIND_BY_EXT.get(ext, "other")
    target = _create_unique(root() / folder.path / name, data)
    f = StoredFile(
        folder_id=folder.id,
        name=target.name,
        rel_path=target.relative_to(root()).as_posix(),
        size=len(data),
        kind=kind,
        sha256=sha256_bytes(data),
        text_sha=text_fingerprint(text) if text else "",
        pages=pages,
        uploaded_by_id=user.id if user else None,
        team_id=folder.team_id,
        source=source,
        meta=meta or {},
    )
    db.add(f)
    db.flush()
    if mirror and source != "result" and kind in LIBRARY_FOR_KIND:
        lib = F.library(db, LIBRARY_FOR_KIND[kind])
        lt = _create_unique(root() / lib.path / target.name, data)
        db.add(StoredFile(folder_id=lib.id, name=lt.name, rel_path=lt.relative_to(root()).as_posix(), size=len(data), kind=kind, sha256=f.sha256, text_sha=f.text_sha, pages=pages, uploaded_by_id=f.uploaded_by_id, team_id=folder.team_id, source=source, library_copy_of=f.id, meta=meta or {}))
        db.flush()
    _sync_remote(f)
    return f


def duplicates_of(db: Session, f: StoredFile) -> list[StoredFile]:
    q = db.query(StoredFile).filter(StoredFile.id != f.id, StoredFile.library_copy_of.is_(None))
    exact = q.filter(StoredFile.sha256 == f.sha256).all()
    near = q.filter(StoredFile.text_sha == f.text_sha).all() if f.text_sha else []
    seen, out = set(), []
    for x in exact + near:
        if x.id not in seen:
            seen.add(x.id)
            out.append(x)
    return out


def read_bytes(f: StoredFile) -> bytes:
    return abs_path(f).read_bytes()


def _sync_remote(f: StoredFile) -> None:
    if not settings.onedrive_enabled:
        return
    try:
        from .onedrive import upload_file

        upload_file(f)
    except Exception as e:  # noqa: BLE001 — never block a local save; the onedrive-sync routine retries
        from .onedrive import record_error

        record_error(f"upload {f.name}", e)
