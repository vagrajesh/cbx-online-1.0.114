"""The nightly backup of the data (1.0.58).

Every night at CBX_BACKUP_AT (01:30), and whenever the owner asks, a snapshot folder is made in the backup
folder (CBX_BACKUP_DIR, default data\\backups\\data):

    2026-10-01_0130\\
        cbx.sqlite3            the main database, copied with SQLite's own online backup (consistent while in use)
        tools\\<slug>.sqlite    each tool folder's tables, the same way
        secret.key             the key that seals the stored secrets (without it they cannot be read back)
        branding\\, builder\\, cexa\\context.md     settings kept as files
        manifest.json          what is in it, and how big

The files people uploaded (the warehouse, files on rows) are large and rarely change, so they are not copied
every night: one mirror beside the snapshots (files\\warehouse, files\\tool-files) gets what is new or
changed. Nothing is deleted from the mirror. Snapshots older than CBX_BACKUP_KEEP_DAYS (14) are removed.

A backup holds everything the server holds — keep the backup folder as protected as the data folder.
Restoring is by hand, with the server stopped (the owner's document says how).
"""
from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .config import settings

log_ = logging.getLogger("cbx.backup")
_LOCK = threading.Lock()
SETTINGS_DIRS = ("branding", "builder")
SETTINGS_FILES = ("secret.key", "cexa/context.md")


def root() -> Path:
    p = Path(settings.backup_dir) if settings.backup_dir else settings.data_dir / "backups" / "data"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _sqlite_path() -> Optional[Path]:
    url = settings.db_url
    if not url.startswith("sqlite:///"):
        return None
    return Path(url[len("sqlite:///"):])


def _copy_db(src: Path, dst: Path) -> int:
    dst.parent.mkdir(parents=True, exist_ok=True)
    s = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True, timeout=30)
    d = sqlite3.connect(str(dst))
    try:
        s.backup(d)  # page by page, while the server keeps writing
    finally:
        d.close()
        s.close()
    return dst.stat().st_size


def _mirror(src: Path, dst: Path) -> tuple[int, int]:
    """Copy what is new or changed (by size and time). Returns (files copied, bytes copied)."""
    n = b = 0
    if not src.is_dir():
        return 0, 0
    for f in src.rglob("*"):
        if not f.is_file() or "__pycache__" in f.parts:
            continue
        t = dst / f.relative_to(src)
        try:
            st = f.stat()
            if t.exists():
                tt = t.stat()
                if tt.st_size == st.st_size and int(tt.st_mtime) >= int(st.st_mtime):
                    continue
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, t)
            n, b = n + 1, b + st.st_size
        except OSError as e:  # a file being written right now: tomorrow's run takes it
            log_.warning("backup could not copy %s: %s", f, e)
    return n, b


def snapshots() -> list[dict]:
    out = []
    for d in sorted((x for x in root().iterdir() if x.is_dir() and x.name[:4].isdigit()), reverse=True):
        m = {}
        try:
            m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        out.append({"name": d.name, "at": m.get("at") or "", "bytes": m.get("bytes") or 0, "databases": len(m.get("databases") or []),
                    "files_copied": m.get("files_copied", 0), "ok": bool(m.get("ok")), "note": m.get("note") or "", "reason": m.get("reason") or ""})
    return out


def run(reason: str = "nightly", by: str = "") -> dict:
    """Make one snapshot now. Never raises: a failed backup is written down (manifest, audit) instead."""
    from .engine.flows import local_now

    if not _LOCK.acquire(blocking=False):
        return {"ok": False, "note": "A backup is already running."}
    try:
        now = local_now()
        d = root() / now.strftime("%Y-%m-%d_%H%M%S")
        d.mkdir(parents=True, exist_ok=True)
        m = {"at": now.isoformat(timespec="seconds"), "reason": reason, "by": by, "version": _version(), "databases": [], "bytes": 0, "ok": False, "note": ""}
        notes = []
        try:
            main = _sqlite_path()
            if main is None:
                notes.append("The database is not SQLite (Postgres): back it up with the database server's own tools.")
            elif main.exists():
                m["bytes"] += _copy_db(main, d / main.name)
                m["databases"].append(main.name)
            tools = settings.data_dir / "tools"
            for f in sorted(tools.glob("*.sqlite")) if tools.is_dir() else []:
                m["bytes"] += _copy_db(f, d / "tools" / f.name)
                m["databases"].append(f"tools/{f.name}")
            for rel in SETTINGS_FILES:
                f = settings.data_dir / rel
                if f.is_file():
                    (d / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, d / rel)
                    m["bytes"] += f.stat().st_size
            for rel in SETTINGS_DIRS:
                src = settings.data_dir / rel
                if src.is_dir():
                    shutil.copytree(src, d / rel, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
            n1, b1 = _mirror(settings.data_dir / "warehouse", root() / "files" / "warehouse")
            n2, b2 = _mirror(settings.data_dir / "tools" / "_files", root() / "files" / "tool-files")
            m["files_copied"], m["files_bytes"] = n1 + n2, b1 + b2
            m["ok"] = True
        except Exception as e:  # noqa: BLE001 — written down, never raised into a request or the scheduler
            log_.error("backup failed", exc_info=True)
            notes.append(f"The backup did not finish: {str(e)[:300]}")
        m["note"] = " ".join(notes)
        (d / "manifest.json").write_text(json.dumps(m, indent=2), encoding="utf-8")
        removed = _prune()
        _audit(m, removed)
        return {**m, "name": d.name, "removed": removed}
    finally:
        _LOCK.release()


def _prune() -> int:
    keep = max(1, int(settings.backup_keep_days or 14))
    cutoff = datetime.now() - timedelta(days=keep)
    n = 0
    for d in [x for x in root().iterdir() if x.is_dir() and x.name[:4].isdigit()]:
        try:
            when = datetime.strptime(d.name[:10], "%Y-%m-%d")
        except ValueError:
            continue
        if when < cutoff:
            shutil.rmtree(d, ignore_errors=True)
            n += 1
    return n


def _version() -> str:
    from . import VERSION

    return VERSION


def _audit(m: dict, removed: int) -> None:
    from .audit import log
    from .db import session_scope

    try:
        with session_scope() as db:
            log(db, "data_backup", actor_label=m.get("by") or "scheduler", target=str(root()),
                detail=(f"{len(m['databases'])} database(s), {m['bytes']:,} bytes; {m.get('files_copied', 0)} new or changed file(s) mirrored; {removed} old snapshot(s) removed" + (f"; {m['note']}" if m.get("note") else ""))[:400],
                outcome="ok" if m.get("ok") else "failed")
    except Exception:  # noqa: BLE001
        log_.warning("could not write the backup to the audit trail", exc_info=True)


def due(now: Optional[datetime] = None) -> bool:
    """Past today's backup time, and no snapshot yet today."""
    from .engine.flows import local_now

    now = now or local_now()
    try:
        hh, mm = (int(x) for x in str(settings.backup_at or "01:30").split(":"))
    except ValueError:
        hh, mm = 1, 30
    if (now.hour, now.minute) < (hh, mm):
        return False
    today = now.strftime("%Y-%m-%d")
    return not any(s["name"].startswith(today) for s in snapshots())


from .jobs import routine  # noqa: E402


@routine("data-backup", 600)
def backup_routine() -> None:
    if settings.backups and due():
        run("nightly")
