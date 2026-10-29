"""Entity Compliance housekeeping, run at every start (and on demand from the grid page):

  relocate_files(db)        every filed certificate lives in its company's folder —
                            Tools/Entity Compliance/<Company>/<Document type>/ — and nowhere else.
                            Files filed before 1.0.38 sat in the uploader's Work folder with a copy
                            in the shared library; both are moved/removed here, once, and logged.
  link_responsible(db)      a responsible person named on a company who has an account here (same
                            name, or the company's responsible e-mail) is linked to it, so the app can
                            reach them instead of only showing a name.

Both are idempotent: a second run finds nothing to do and writes nothing.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from ..audit import log
from ..models import Folder, StoredFile, User, WatchedDoc
from ..models.docs import DocType, Entity
from ..warehouse import folders as F
from ..warehouse import storage

logger = logging.getLogger("cbx.docwatch.housekeeping")


def _move(db: Session, f: StoredFile, folder: Folder) -> bool:
    """Move one stored file (disk + row) into `folder`. False when the file is missing on disk or locked."""
    src = storage.abs_path(f)
    if not src.exists():
        return False
    target = storage._unique(storage.root() / folder.path / f.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(src), str(target))
    except OSError as e:
        logger.warning("could not move %s → %s: %s", src, target, e)
        return False
    f.folder_id = folder.id
    f.rel_path = target.relative_to(storage.root()).as_posix()
    f.name = target.name
    f.team_id = folder.team_id
    f.source = "docwatch"
    db.add(f)
    return True


def _drop_library_copies(db: Session, f: StoredFile) -> int:
    n = 0
    for c in db.query(StoredFile).filter(StoredFile.library_copy_of == f.id).all():
        p = storage.abs_path(c)
        try:
            if p.exists():
                p.unlink()
        except OSError as e:
            logger.warning("could not remove library copy %s: %s", p, e)
            continue
        db.delete(c)
        n += 1
    return n


def plan(db: Session) -> dict:
    """What relocate_files would do — the numbers the grid page shows before and after."""
    docs = db.query(WatchedDoc).filter(WatchedDoc.entity_id.isnot(None)).all()
    ents = {e.id: e for e in db.query(Entity).all()}
    misplaced = copies = missing = 0
    file_ids: set[int] = set()
    for d in docs:
        ids = [d.file_id] + [h.get("file_id") for h in (d.history or []) if h.get("file_id")]
        file_ids |= {i for i in ids if i}
    files = {f.id: f for f in db.query(StoredFile).filter(StoredFile.id.in_(file_ids or {0})).all()}
    folders = {f.id: f for f in db.query(Folder).filter(Folder.id.in_({f.folder_id for f in files.values()} or {0})).all()}
    for d in docs:
        ent = ents.get(d.entity_id)
        if ent is None:
            continue
        want = f"{F.TOOLS_ROOT}/{F.safe_name(F.COMPLIANCE)}/{F.safe_name(ent.name)}/"
        for fid in [d.file_id] + [h.get("file_id") for h in (d.history or []) if h.get("file_id")]:
            f = files.get(fid)
            if f is None:
                continue
            folder = folders.get(f.folder_id)
            if folder is None or not folder.path.startswith(want):
                misplaced += 1
            if not storage.abs_path(f).exists():
                missing += 1
    if file_ids:
        copies = db.query(StoredFile).filter(StoredFile.library_copy_of.in_(file_ids)).count()
    return {"documents": len(docs), "files": len(files), "misplaced": misplaced, "library_copies": copies, "missing_on_disk": missing}


def relocate_files(db: Session, *, by: Optional[User] = None) -> dict:
    """Put every certificate where it belongs. Returns counts; writes one audit row when anything moved."""
    docs = db.query(WatchedDoc).filter(WatchedDoc.entity_id.isnot(None)).all()
    ents = {e.id: e for e in db.query(Entity).all()}
    types = {t.id: t for t in db.query(DocType).all()}
    moved = dropped = failed = 0
    seen: set[int] = set()
    for d in docs:
        ent = ents.get(d.entity_id)
        if ent is None:
            continue
        dt = types.get(d.doc_type_id) if d.doc_type_id else None
        type_name = (dt.name if dt else d.doc_type) or "Other documents"
        want_prefix = f"{F.TOOLS_ROOT}/{F.safe_name(F.COMPLIANCE)}/{F.safe_name(ent.name)}/"
        for fid in [d.file_id] + [h.get("file_id") for h in (d.history or []) if h.get("file_id")]:
            if not fid or fid in seen:
                continue
            seen.add(fid)
            f = db.get(StoredFile, fid)
            if f is None:
                continue
            folder = db.get(Folder, f.folder_id)
            in_place = folder is not None and folder.path.startswith(want_prefix)
            if not in_place:
                dest = F.compliance_folder(db, ent, type_name)
                if _move(db, f, dest):
                    moved += 1
                else:
                    failed += 1
            meta = dict(f.meta or {})
            if meta.get("entity_id") != ent.id or meta.get("doc_id") != d.id:
                f.meta = {**meta, "purpose": "docwatch", "entity_id": ent.id, "doc_id": d.id, "entity": ent.name, "doc_type": type_name}
                db.add(f)
            dropped += _drop_library_copies(db, f)
    db.flush()
    out = {"moved": moved, "library_copies_removed": dropped, "failed": failed, "files": len(seen)}
    if moved or dropped or failed:
        log(db, "docwatch_files_relocated", actor=by, actor_label="" if by else "server", target=F.COMPLIANCE,
            detail=f"{moved} file(s) moved into Tools/Entity Compliance/<Company>/<Document type>; {dropped} shared-library cop{'y' if dropped == 1 else 'ies'} removed" + (f"; {failed} could not be moved (missing or locked) — run again later" if failed else ""),
            outcome="ok" if not failed else "partial")
    return out


def relocate_entity(db: Session, ent: Entity, *, by: Optional[User] = None) -> dict:
    """After a rename: the company's files follow it into a folder with the new name."""
    old = [f for f in db.query(Folder).filter(Folder.entity_id == ent.id).all()]
    out = relocate_files(db, by=by)
    # empty old folders (the previous name) go, disk and row — deepest first, so a company folder is
    # emptied of its document-type folders before it is itself judged empty
    keep_prefix = f"{F.TOOLS_ROOT}/{F.safe_name(F.COMPLIANCE)}/{F.safe_name(ent.name)}"
    for f in sorted(old, key=lambda x: -len(x.path)):
        db.flush()
        if f.path == keep_prefix or f.path.startswith(keep_prefix + "/"):
            continue
        if db.query(StoredFile).filter(StoredFile.folder_id == f.id).count() == 0 and db.query(Folder).filter(Folder.parent_id == f.id).count() == 0:
            try:
                p = storage.root() / f.path
                if p.is_dir() and not any(p.iterdir()):
                    p.rmdir()
            except OSError:
                pass
            db.delete(f)
    db.flush()
    return out


def link_responsible(db: Session, entities: Optional[list[Entity]] = None) -> int:
    """Name → account, where it is certain: the whole name matches one active account (case-insensitive),
    or the company's responsible e-mail is an account's address. Guesses are not made here — a wrong
    link would send someone else's reminders to the wrong person."""
    ents = entities if entities is not None else db.query(Entity).filter(Entity.responsible_user_id.is_(None), Entity.is_demo.is_(False)).all()
    people = db.query(User).filter(User.is_active.is_(True), User.is_axl.is_(False)).all()
    by_name: dict[str, list[User]] = {}
    by_mail: dict[str, User] = {}
    for u in people:
        by_name.setdefault(" ".join((u.name or "").lower().split()), []).append(u)
        if u.email:
            by_mail[u.email.lower()] = u
    linked = 0
    for e in ents:
        if e.responsible_user_id:
            continue
        who = None
        if e.responsible_email and e.responsible_email.lower() in by_mail:
            who = by_mail[e.responsible_email.lower()]
        else:
            hits = by_name.get(" ".join((e.responsible or "").lower().split()), [])
            if len(hits) == 1:
                who = hits[0]
        if who is None:
            continue
        e.responsible_user_id = who.id
        if not e.responsible:
            e.responsible = who.name[:80]
        db.add(e)
        linked += 1
    if linked:
        db.flush()
        log(db, "docwatch_responsible_linked", actor_label="server", target=F.COMPLIANCE, detail=f"{linked} responsible person(s) linked to their accounts by name or e-mail")
    return linked


def run_at_start(db: Session) -> dict:
    """Everything above, quietly, at boot. Never raises."""
    out: dict = {}
    try:
        out["relocate"] = relocate_files(db)
    except Exception as e:  # noqa: BLE001
        logger.exception("relocate_files failed")
        out["relocate"] = {"error": str(e)[:200]}
    try:
        out["linked"] = link_responsible(db)
    except Exception as e:  # noqa: BLE001
        logger.exception("link_responsible failed")
        out["linked"] = 0
    return out
