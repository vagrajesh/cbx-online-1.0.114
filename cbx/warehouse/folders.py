"""Folder structure: Shared library/{PDFs,Workflows,Master sheets}, Teams/<team>/<person>/{Inbox,Work,Results},
and Tools/<tool>/… for files that belong to a tool rather than a person (Tools/Entity Compliance/<Company>/<Document type>).
Local disk under DATA_DIR/warehouse mirrors the same paths; OneDrive sync (cbx.warehouse.onedrive) creates
the same tree remotely and records remote ids on the Folder rows."""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Folder, Team, User

PERSON_SUBS = ("Inbox", "Work", "Results")


def safe_name(s: str) -> str:
    """A name Windows, OneDrive and a URL are all happy with: no separators or reserved characters,
    no control characters, no leading/trailing dots or spaces (so '..' can never become a path step)."""
    s = re.sub(r"[\\/:*?\"<>|]+", "-", s)
    s = re.sub(r"[\x00-\x1f]", "", s).strip(" .")
    if s.split(".")[0].upper() in _WIN_RESERVED:  # CON.pdf can't be written on Windows
        s = "_" + s
    return s[:150] or "unnamed"


_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def _get_or_create(db: Session, *, kind: str, name: str, path: str, parent_id=None, team_id=None, user_id=None, entity_id=None) -> Folder:
    f = db.query(Folder).filter(Folder.path == path).one_or_none()
    if f is None:
        f = Folder(kind=kind, name=name, path=path, parent_id=parent_id, team_id=team_id, user_id=user_id, entity_id=entity_id)
        db.add(f)
        db.flush()
        (settings.data_dir / "warehouse" / path).mkdir(parents=True, exist_ok=True)
        _sync_remote(f)
    elif entity_id and f.entity_id != entity_id:
        f.entity_id = entity_id
    return f


TOOLS_ROOT = "Tools"
COMPLIANCE = "Entity Compliance"


def tools_root(db: Session) -> Folder:
    return _get_or_create(db, kind="tool", name=TOOLS_ROOT, path=TOOLS_ROOT)


def tool_folder(db: Session, tool_label: str) -> Folder:
    """Tools/<tool> — a tool's own files, split the way the tool thinks, not by who uploaded them."""
    root = tools_root(db)
    return _get_or_create(db, kind="tool", name=tool_label, path=f"{TOOLS_ROOT}/{safe_name(tool_label)}", parent_id=root.id)


def compliance_folder(db: Session, entity, doc_type_name: str = "") -> Folder:
    """Tools/Entity Compliance/<Company>[/<Document type>] — one folder per company, one under it per
    column of the grid. The company folder carries the entity id, so who may open it is exactly who
    may see the company. Giving someone Mozambique gives them this folder and nothing else."""
    tool = tool_folder(db, COMPLIANCE)
    company = _get_or_create(db, kind="tool", name=entity.name, path=f"{tool.path}/{safe_name(entity.name)}", parent_id=tool.id, team_id=entity.team_id, entity_id=entity.id)
    if not doc_type_name:
        return company
    return _get_or_create(db, kind="tool", name=doc_type_name, path=f"{company.path}/{safe_name(doc_type_name)}", parent_id=company.id, team_id=entity.team_id, entity_id=entity.id)


def ensure_team_folder(db: Session, team: Team) -> Folder:
    teams_root = _get_or_create(db, kind="team", name="Teams", path="Teams")
    return _get_or_create(db, kind="team", name=team.name, path=f"Teams/{safe_name(team.name)}", parent_id=teams_root.id, team_id=team.id)


def ensure_person_folders(db: Session, user: User) -> Folder:
    if user.team_id:
        team = db.get(Team, user.team_id)
    else:
        team = db.query(Team).filter(Team.slug == "unassigned").one_or_none()
        if team is None:
            team = Team(name="Unassigned", slug="unassigned")
            db.add(team)
            db.flush()
    tf = ensure_team_folder(db, team)
    pf = _get_or_create(db, kind="person", name=user.name, path=f"{tf.path}/{safe_name(user.name)}", parent_id=tf.id, team_id=team.id, user_id=user.id)
    for sub in PERSON_SUBS:
        _get_or_create(db, kind="sub", name=sub, path=f"{pf.path}/{sub}", parent_id=pf.id, team_id=team.id, user_id=user.id)
    return pf


def person_sub(db: Session, user: User, sub: str) -> Folder:
    pf = ensure_person_folders(db, user)
    return db.query(Folder).filter(Folder.path == f"{pf.path}/{sub}").one()


def library(db: Session, name: str) -> Folder:
    lib = _get_or_create(db, kind="library", name="Shared library", path="Shared library")
    return _get_or_create(db, kind="library", name=name, path=f"Shared library/{name}", parent_id=lib.id)


def _sync_remote(folder: Folder) -> None:
    if not settings.onedrive_enabled:
        return
    try:
        from .onedrive import ensure_remote_folder

        ensure_remote_folder(folder)
    except Exception as e:  # noqa: BLE001 — never block local work on a sync problem; the routine retries
        from .onedrive import record_error

        record_error(f"folder {folder.path}", e)
