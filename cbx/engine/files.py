"""Files on rows (1.0.51): a file column holds one file per row.

The file lives in DATA_DIR/tools/_files/<slug>/<table>/<row>/<column>-<random>.<ext>, never under a
name the uploader chose; the column holds {id, name, size, type, at, by}. What a column accepts is a
fixed list of document and picture types (loader.FILE_TYPES), checked by the file's first bytes as
well as its name, and at most max_mb (10 unless the column says, 100 at most). A file is always
downloaded, never shown in the page, and served with nosniff and a sandbox policy, so a file cannot
run as part of CBX-Connect.
"""
from __future__ import annotations

import re
import secrets
import shutil
from pathlib import Path
from typing import Optional

from ..config import settings
from .loader import FILE_TYPES

MEDIA = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp",
         "txt": "text/plain", "csv": "text/csv", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
         "doc": "application/msword", "xls": "application/vnd.ms-excel", "msg": "application/vnd.ms-outlook", "eml": "message/rfc822", "zip": "application/zip"}
ID_RE = re.compile(r"^[a-z0-9_]{1,40}-[A-Za-z0-9_-]{8,40}\.[a-z0-9]{2,5}$")


class FileError(ValueError):
    pass


def root_for(slug: str) -> Path:
    return settings.data_dir / "tools" / "_files" / slug


def row_dir(slug: str, block: str, rid: int) -> Path:
    return root_for(slug) / block / str(int(rid))


def accepts(col: dict) -> list[str]:
    acc = [str(a).lower().lstrip(".") for a in (col.get("accept") or [])]
    return [a for a in acc if a in FILE_TYPES] or list(FILE_TYPES)


def max_bytes(col: dict) -> int:
    try:
        mb = float(col.get("max_mb", 10))
    except (TypeError, ValueError):
        mb = 10
    from .loader import FILE_MAX_MB

    return int(max(0.1, min(mb, FILE_MAX_MB)) * 1024 * 1024)


def looks_like(ext: str, head: bytes) -> bool:
    """The first bytes agree with the name: a .pdf is a PDF, an .xlsx a zip, a .txt has no binary."""
    if ext == "pdf":
        return head.startswith(b"%PDF")
    if ext == "png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in ("jpg", "jpeg"):
        return head.startswith(b"\xff\xd8\xff")
    if ext == "gif":
        return head.startswith((b"GIF87a", b"GIF89a"))
    if ext == "webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    if ext in ("docx", "xlsx", "pptx", "zip"):
        return head.startswith(b"PK\x03\x04")
    if ext in ("doc", "xls", "msg"):
        return head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    if ext in ("txt", "csv", "eml"):
        if b"\x00" in head:
            return False
        low = head[:512].lower()
        return b"<script" not in low and b"<html" not in low
    return False


def clean_name(name: str) -> str:
    base = Path(str(name or "file").replace("\\", "/")).name
    base = re.sub(r"[\x00-\x1f\"<>|:*?/\\]+", "_", base).strip(" .") or "file"
    return base[:120]


def save(slug: str, block: str, rid: int, col: dict, filename: str, data: bytes, by: Optional[int], at: str, keep: Optional[set] = None) -> dict:
    """Check and keep one upload; the old file of that column on that row is removed — unless its id is in
    `keep` (1.0.63: a column that keeps earlier versions, e.g. last year's certificate)."""
    name = clean_name(filename)
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    ok = accepts(col)
    if ext not in ok:
        raise FileError(f"'{col.get('key')}' takes {', '.join(ok)} files — not .{ext or '(no extension)'}")
    if not data:
        raise FileError("The file is empty.")
    if len(data) > max_bytes(col):
        raise FileError(f"The file is over {max_bytes(col) // (1024 * 1024) or 1} MB.")
    if not looks_like(ext, data[:4096]):
        raise FileError(f"That file is not really a .{ext} — its contents say otherwise.")
    d = row_dir(slug, block, rid)
    d.mkdir(parents=True, exist_ok=True)
    fid = f"{col['key']}-{secrets.token_urlsafe(12)}.{ext}"
    (d / fid).write_bytes(data)
    for old in d.glob(f"{col['key']}-*"):
        if old.name != fid and old.name not in (keep or set()):
            try:
                old.unlink()
            except OSError:
                pass
    return {"id": fid, "name": name, "size": len(data), "type": ext, "at": at, "by": by}


def path_of(slug: str, block: str, rid: int, meta: Optional[dict]) -> Optional[Path]:
    fid = str((meta or {}).get("id") or "")
    if not ID_RE.match(fid):
        return None
    p = row_dir(slug, block, rid) / fid
    return p if p.is_file() else None


def remove(slug: str, block: str, rid: int, key: Optional[str] = None, only: Optional[str] = None) -> None:
    """One column's file, or (key None) every file of the row — when the row is deleted. `only`: just that
    one file (the current version of a column that keeps the earlier ones)."""
    d = row_dir(slug, block, rid)
    if not d.is_dir():
        return
    if key is None:
        shutil.rmtree(d, ignore_errors=True)
        return
    for f in d.glob(f"{key}-*"):
        if only and f.name != only:
            continue
        try:
            f.unlink()
        except OSError:
            pass


def earlier_of(meta: Optional[dict]) -> list[dict]:
    """The earlier versions a cell keeps, newest first (1.0.63)."""
    e = (meta or {}).get("earlier") if isinstance(meta, dict) else None
    return [x for x in e if isinstance(x, dict) and ID_RE.match(str(x.get("id") or ""))] if isinstance(e, list) else []


def with_new(meta_new: dict, meta_old: Optional[dict], keep: int) -> dict:
    """The cell after an upload: the new file, and — when the column keeps earlier versions — the one it
    replaced on top of the ones before it, at most `keep` of them."""
    if keep <= 0:
        return meta_new
    prev = []
    if isinstance(meta_old, dict) and meta_old.get("id"):
        prev.append({k: meta_old[k] for k in ("id", "name", "size", "type", "at", "by") if k in meta_old} | {"replaced": meta_new.get("at")})
    out = dict(meta_new)
    out["earlier"] = (prev + earlier_of(meta_old))[:keep]
    return out


def prune(slug: str, block: str, rid: int, key: str, ids: set) -> None:
    """Remove the files of one column that the cell no longer names (an earlier version past the limit)."""
    d = row_dir(slug, block, rid)
    if not d.is_dir():
        return
    for f in d.glob(f"{key}-*"):
        if f.name not in ids:
            try:
                f.unlink()
            except OSError:
                pass
