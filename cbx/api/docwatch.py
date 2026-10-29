"""Entity Compliance API (tool slug stays `doc-watch` so grants survive the rename).

The grid: companies × document types, one cell each. Owners and admins see and manage everything; a
manager sees companies visible to everyone plus their team's and manages their team's; a user sees the
same set and may upload, renew and set dates. Only admins manage the columns and delete companies.
Deny by default: every route checks the company the cell belongs to."""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import mail as mail_mod
from ..audit import log
from ..db import get_db, utcnow
from ..docwatch import housekeeping as HK
from ..docwatch import people as P
from ..docwatch import service as S
from ..docwatch.scanner import parse_date
from ..models import StoredFile, Team, User, WatchedDoc
from ..models.docs import DocType, Entity
from ..security.deps import client_ip, require_human, require_tool
from ..util import human_date, read_capped
from ..warehouse import storage
from ..warehouse.extract import text_of

def read_only_once_moved(request: Request, db: Session = Depends(get_db)) -> None:
    """1.0.63: once Entity Compliance was copied into its tool folder, these old screens only read."""
    if request.method in ("POST", "PATCH", "PUT", "DELETE"):
        from ..docwatch.copy_in import moved

        if moved(db):
            raise HTTPException(409, "Entity Compliance now lives in its own tool (Entity Compliance in the menu) — change it there. These old screens are read-only.")


router = APIRouter(prefix="/api/docwatch", tags=["docwatch"], dependencies=[Depends(read_only_once_moved)])
need = require_tool("doc-watch")
MAX_MB = 100


# ---------------------------------------------------------------------------------------------- helpers


def _deny(db: Session, me: User, request: Request, target: str, what: str, detail: str = "") -> HTTPException:
    log(db, "docwatch_denied", actor=me, target=target, detail=what, outcome="blocked", ip=client_ip(request))
    return HTTPException(403, detail or ("That entity belongs to another team." if me.is_team_limited else "You don't have access to that entity."))


def _entity(db: Session, me: User, eid: int, request: Request) -> Entity:
    e = db.get(Entity, eid)
    if e is None:
        raise HTTPException(404, "That entity isn't on the grid.")
    if not S.can_see(me, e, db):
        raise _deny(db, me, request, e.name, "open entity")
    return e


def _manage(db: Session, me: User, e: Entity, request: Request, what: str) -> None:
    if not S.can_manage(me, e):
        raise _deny(db, me, request, e.name, what, "Only an admin or your team's manager can do that.")


def _manage_people(db: Session, me: User, e: Entity, request: Request, what: str) -> None:
    """Who may see a company, and who answers for it: admins and the manager of the company's team.
    Not the responsible person — they may correct the company, but handing it to others is access."""
    if me.is_admin or (me.is_manager and e.team_id and e.team_id == me.team_id):
        return
    raise _deny(db, me, request, e.name, what, "Only an admin or the team's manager can do that.")


def _admin(db: Session, me: User, request: Request, target: str, what: str) -> None:
    if not me.is_admin:
        raise _deny(db, me, request, target, what, "Only an admin can do that.")


def _get(db: Session, me: User, did: int, request: Request) -> WatchedDoc:
    d = db.get(WatchedDoc, did)
    if d is None:
        raise HTTPException(404, "That document isn't on the grid.")
    if not S.can_see_doc(db, me, d):
        raise _deny(db, me, request, d.title, "open document")
    return d


def _date(s: str) -> Optional[date]:
    s = s.strip()
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return parse_date(s)


def _check_size(data: bytes) -> None:
    if len(data) > MAX_MB * 1024 * 1024:
        raise HTTPException(400, f"That file is too large (max {MAX_MB} MB).")


def _teams(db: Session, me: User) -> list[dict]:
    if not me.is_admin:
        return []
    return [{"id": t.id, "name": t.name} for t in db.query(Team).order_by(Team.name).all()]


def _filed_message(doc: WatchedDoc, r: dict, renewed: bool) -> str:
    if doc.expiry:
        return f"{'Renewed' if renewed else 'Filed'}. Expires {human_date(doc.expiry, '%b')}."
    return f"{'Renewed' if renewed else 'Filed'} — now type the expiry date in the dialog."


def _with_read(db: Session, me: User, out: dict) -> dict:
    doc, r = out["doc"], out["read"]
    d = S.doc_out(doc, db, me, file=out["file"])
    d["read"] = S.scan_out(r)
    d["ask_user"] = doc.expiry is None and not doc.no_expiry
    d["renewed"] = out["renewed"]
    d["message"] = _filed_message(doc, r, out["renewed"])
    return d


# ---------------------------------------------------------------------------------------------- the grid


@router.get("")
def grid(training: bool = False, db: Session = Depends(get_db), me: User = Depends(need)):
    """`?training=1` is the same grid over the Training practice entities only — what a lesson opens."""
    if training:
        from ..training import demo as TD

        TD.ensure_entities(db, me)
    g = S.grid(db, me, demo=training)
    g["training"] = bool(training)
    s = g["summary"]
    g["attention"] = s["expired"] + s["expiring"]
    g["needs_date"] = s["outstanding"]
    g["teams"] = _teams(db, me)
    g["me"] = {"is_admin": me.is_admin, "is_manager": me.is_manager, "team_id": me.team_id}
    if me.is_admin:
        counts = dict(db.query(WatchedDoc.doc_type_id, func.count(WatchedDoc.id)).filter(WatchedDoc.doc_type_id.isnot(None)).group_by(WatchedDoc.doc_type_id).all())
        g["all_types"] = [S.type_out(t, counts.get(t.id, 0)) for t in S.all_types(db)]
    return g


# ---------------------------------------------------------------------------------------------- companies


class NewEntity(BaseModel):
    name: str
    team_id: Optional[int] = None  # admins only; None = everyone with the tool
    notes: str = ""
    responsible: str = ""


class EntityPatch(BaseModel):
    name: Optional[str] = None
    notes: Optional[str] = None
    team_id: Optional[int] = None
    everyone: Optional[bool] = None  # admins: True clears the team (visible to everyone)
    responsible: Optional[str] = None
    responsible_user_id: Optional[int] = None


@router.post("/entities")
def add_entity(body: NewEntity, request: Request, db: Session = Depends(get_db), me: User = Depends(need)):
    try:
        e = S.create_entity(db, me, body.name, team_id=body.team_id, notes=body.notes, responsible=body.responsible)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return S.entity_page(db, me, e)


@router.get("/entities/archived")
def archived_entities(request: Request, db: Session = Depends(get_db), me: User = Depends(need)):
    """Companies we no longer service. Off the grid, still here, and one click from coming back."""
    rows = [e for e in S.visible_entities(db, me, include_archived=True) if e.archived]
    counts = dict(db.query(WatchedDoc.entity_id, func.count(WatchedDoc.id)).filter(
        WatchedDoc.entity_id.in_([e.id for e in rows] or [0])).group_by(WatchedDoc.entity_id).all())
    teams = {t.id: t for t in db.query(Team).all()}
    return {"entities": [dict(S.entity_out(e, teams.get(e.team_id), me), documents=counts.get(e.id, 0)) for e in rows]}


@router.get("/entities/{eid}")
def entity_page(eid: int, request: Request, db: Session = Depends(get_db), me: User = Depends(need)):
    e = _entity(db, me, eid, request)
    out = S.entity_page(db, me, e)
    out["teams"] = _teams(db, me)
    return out


@router.patch("/entities/{eid}")
def patch_entity(eid: int, body: EntityPatch, request: Request, db: Session = Depends(get_db), me: User = Depends(need)):
    e = _entity(db, me, eid, request)
    _manage(db, me, e, request, "edit entity")
    changes = []
    if body.name is not None:
        new = " ".join(body.name.split())[:160]
        if not new:
            raise HTTPException(400, "Give the entity a name.")
        clash = db.query(Entity).filter(Entity.name.ilike(new), Entity.id != e.id).first()
        if clash is not None:
            raise HTTPException(400, f"'{clash.name}' is already on the grid.")
        if new != e.name:
            changes.append(f"renamed '{e.name}' → '{new}'")
            e.name = new
    if body.notes is not None and body.notes.strip()[:300] != (e.notes or ""):
        e.notes = body.notes.strip()[:300]
        changes.append("notes")
    if body.responsible is not None:
        who = " ".join(body.responsible.split())[:80]
        if who != (e.responsible or ""):
            e.responsible = who
            changes.append(f"responsible: {who or 'nobody'}")
    if body.responsible_user_id is not None:
        # Linking an account is what turns a name in a list into someone the app can actually reach —
        # and it gives them the company, so they can open what they answer for.
        u = db.get(User, body.responsible_user_id) if body.responsible_user_id else None
        if body.responsible_user_id and u is None:
            raise HTTPException(400, "That person doesn't have an account here.")
        try:
            changes.append(P.set_responsible(db, e, u, me))
        except ValueError as ex:
            raise HTTPException(400, str(ex))
    if body.everyone or body.team_id is not None:
        _admin(db, me, request, e.name, "change who sees an entity")
        team = None if body.everyone else db.get(Team, body.team_id)
        if not body.everyone and team is None:
            raise HTTPException(400, "That team doesn't exist.")
        new_team = None if body.everyone else team.id
        if new_team != e.team_id:
            e.team_id = new_team
            changes.append(f"visible to {team.name + ' only' if team else 'everyone with the tool'}")
    db.add(e)
    for d in db.query(WatchedDoc).filter(WatchedDoc.entity_id == e.id).all():
        d.entity, d.client, d.team_id = e.name, S.client_of(e.name), e.team_id
        db.add(d)
    db.flush()
    if any(c.startswith("renamed") for c in changes):
        from ..docwatch import housekeeping

        housekeeping.relocate_entity(db, e, by=me)  # the company's files follow the new name
    log(db, "docwatch_company_updated", actor=me, target=e.name, detail=", ".join(changes) or "no change", ip=client_ip(request))
    out = S.entity_page(db, me, e)
    out["teams"] = _teams(db, me)
    return out


# ---------------------------------------------------------------------------------------------- who sees a company


class AccessIn(BaseModel):
    user_id: int
    only: Optional[bool] = None  # True: they see only the companies given to them; None: leave as is
    note: str = ""


class ScopeIn(BaseModel):
    only: bool


class ResponsibleIn(BaseModel):
    user_id: Optional[int] = None   # an existing account …
    name: str = ""                  # … or a new one: name + email → the account is created
    email: str = ""
    team_id: Optional[int] = None


def _candidates(db: Session, me: User, e: Entity) -> list[dict]:
    q = db.query(User).filter(User.is_active.is_(True), User.is_axl.is_(False))
    if me.is_team_limited and not me.is_admin:
        q = q.filter(User.team_id == me.team_id)
    return [{"id": u.id, "name": u.name, "email": u.email, "role": u.role, "team_id": u.team_id} for u in q.order_by(User.name).all()]


@router.get("/entities/{eid}/access")
def entity_access(eid: int, request: Request, db: Session = Depends(get_db), me: User = Depends(need)):
    """Who may see this company, one by one — and who could be added. Managers see their own team."""
    e = _entity(db, me, eid, request)
    _manage_people(db, me, e, request, "see who has access")
    return {"people": P.access(db, e), "candidates": _candidates(db, me, e), "can_add_people": bool(me.can("people.add")), "responsible_user_id": e.responsible_user_id, "responsible": e.responsible or "", "responsible_email": e.responsible_email or ""}


@router.post("/entities/{eid}/access")
def grant_access(eid: int, body: AccessIn, request: Request, db: Session = Depends(get_db), me: User = Depends(need), _h: User = Depends(require_human)):
    e = _entity(db, me, eid, request)
    _manage_people(db, me, e, request, "give someone a company")
    u = db.get(User, body.user_id)
    if u is None or not u.is_active:
        raise HTTPException(400, "That person doesn't have an active account here.")
    if me.is_team_limited and not me.is_admin and u.team_id != me.team_id:
        raise _deny(db, me, request, e.name, "give a company to someone on another team", "You can only give companies to people on your own team.")
    try:
        added = P.grant(db, e, u, me, body.note or "", only=body.only)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return {"added": added, "people": P.access(db, e), "message": (f"{u.name} can now see {e.name}." if added else f"{u.name} already sees {e.name}.") + (" They see only the companies given to them." if body.only else "")}


@router.delete("/entities/{eid}/access/{uid}")
def revoke_access(eid: int, uid: int, request: Request, db: Session = Depends(get_db), me: User = Depends(need), _h: User = Depends(require_human)):
    e = _entity(db, me, eid, request)
    _manage_people(db, me, e, request, "take a company away")
    u = db.get(User, uid)
    if u is None:
        raise HTTPException(404, "No such person.")
    removed = P.revoke(db, e, u, me)
    still = S.can_see(u, e, db) if removed else True
    return {"removed": removed, "people": P.access(db, e), "message": (f"{u.name} no longer has {e.name} by name." + (" They still see it through their team." if still else "")) if removed else "Nothing to remove."}


@router.post("/entities/{eid}/access/{uid}/scope")
def access_scope(eid: int, uid: int, body: ScopeIn, request: Request, db: Session = Depends(get_db), me: User = Depends(need), _h: User = Depends(require_human)):
    e = _entity(db, me, eid, request)
    _manage_people(db, me, e, request, "change how far someone sees")
    u = db.get(User, uid)
    if u is None:
        raise HTTPException(404, "No such person.")
    changed = S.set_only(db, u, body.only, me)
    return {"changed": changed, "people": P.access(db, e), "message": f"{u.name} now sees only the companies given to them." if body.only else f"{u.name} sees the companies open to everyone and their team's, plus those given to them."}


@router.post("/entities/{eid}/responsible")
def set_responsible(eid: int, body: ResponsibleIn, request: Request, db: Session = Depends(get_db), me: User = Depends(need), _h: User = Depends(require_human)):
    """The responsible person as an ACCOUNT: pick an existing one, or give a name and email and the
    account is made — temporary password shown once, tool and company granted, link set."""
    e = _entity(db, me, eid, request)
    _manage_people(db, me, e, request, "set the responsible person")
    temp = None
    created = None
    try:
        if body.user_id:
            u = db.get(User, body.user_id)
            if u is None or not u.is_active:
                raise HTTPException(400, "That person doesn't have an active account here.")
            what = P.set_responsible(db, e, u, me)
        elif body.user_id == 0:
            what = P.set_responsible(db, e, None, me)
        else:
            u, temp = P.new_responsible(db, e, name=body.name, email=body.email, by=me, team_id=body.team_id)
            created = {"id": u.id, "name": u.name, "email": u.email}
            what = f"account created for {u.name} and linked"
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    except PermissionError as ex:
        raise _deny(db, me, request, e.name, "add a person", str(ex))
    for d in db.query(WatchedDoc).filter(WatchedDoc.entity_id == e.id).all():
        d.entity = e.name
        db.add(d)
    log(db, "docwatch_company_updated", actor=me, target=e.name, detail=what, ip=client_ip(request))
    out = S.entity_page(db, me, e)
    out["teams"] = _teams(db, me)
    out["people"] = P.access(db, e)
    out["created"] = created
    out["temp_password"] = temp
    out["message"] = what[0].upper() + what[1:] + "."
    return out


# ---------------------------------------------------------------------------------------------- where the files are


@router.get("/storage")
def storage_status(request: Request, db: Session = Depends(get_db), me: User = Depends(need)):
    """Admins: are all certificates in Tools/Entity Compliance/<Company>/<Document type>/ yet?"""
    _admin(db, me, request, "storage", "see where the files are")
    return {"plan": HK.plan(db), "root": "Tools/Entity Compliance/<Company>/<Document type>/"}


@router.post("/storage/relocate")
def storage_relocate(request: Request, db: Session = Depends(get_db), me: User = Depends(need), _h: User = Depends(require_human)):
    _admin(db, me, request, "storage", "move files into company folders")
    out = HK.relocate_files(db, by=me)
    out["linked"] = HK.link_responsible(db)
    out["plan"] = HK.plan(db)
    return out


class EntityArchive(BaseModel):
    archived: bool
    reason: Optional[str] = ""


@router.post("/entities/{eid}/archive")
def archive_entity(eid: int, body: EntityArchive, request: Request, db: Session = Depends(get_db), me: User = Depends(need)):
    """Stop watching a company without losing what it filed.

    This is what "we don't do them anymore" means: the company leaves the grid, its reminders stop,
    and every certificate it ever filed stays exactly where it is. Deleting is a different thing —
    it is for a company added by mistake, and it throws the record away.
    """
    e = _entity(db, me, eid, request)
    _manage(db, me, e, request, "archive entity")
    if bool(e.archived) == bool(body.archived):
        return S.entity_page(db, me, e)
    e.archived = bool(body.archived)
    e.archived_at = utcnow() if e.archived else None
    e.archived_reason = (body.reason or "").strip()[:200] if e.archived else ""
    db.add(e)
    db.flush()
    n = db.query(WatchedDoc).filter(WatchedDoc.entity_id == e.id).count()
    verb = "archived" if e.archived else "brought back"
    log(db, f"docwatch_company_{'archived' if e.archived else 'restored'}", actor=me, target=e.name,
        detail=f"{n} document record(s) kept" + (f" — {e.archived_reason}" if e.archived_reason else ""), ip=client_ip(request))
    out = S.entity_page(db, me, e)
    out["message"] = f"'{e.name}' {verb}." + (f" Its {n} document record{'s' if n != 1 else ''} stayed — nothing was thrown away." if e.archived and n else "")
    return out


class EntityImport(BaseModel):
    text: str
    team_id: Optional[int] = None


@router.post("/entities/import")
def import_entities(body: EntityImport, request: Request, db: Session = Depends(get_db), me: User = Depends(need)):
    """Paste a list of companies, one per line, optionally `Company<TAB>Responsible person`.

    Finance keeps this list in a mail or a spreadsheet, not in here. Retyping thirty-two legal
    entities is how a grid ends up with 'Cote d Ivoire' and 'Ivory Coast' as two companies, so the
    list goes in the way it arrives. Names already on the grid are reported, never duplicated and
    never silently overwritten.
    """
    added, updated, skipped = [], [], []
    for raw in (body.text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = [p.strip() for p in re.split(r"\t|\s{2,}|\s*[;|]\s*", line, maxsplit=1)]
        name = parts[0]
        who = parts[1] if len(parts) > 1 else ""
        if not name or name.lower() in ("entity", "company", "entity responsible person"):
            continue  # a pasted header row is not a company
        existing = db.query(Entity).filter(Entity.name.ilike(name)).first()
        if existing is not None:
            if who and not existing.responsible:
                existing.responsible = who[:120]
                db.add(existing)
                updated.append(existing.name)
            else:
                skipped.append(existing.name)
            continue
        try:
            e = S.create_entity(db, me, name, team_id=body.team_id, notes="")
        except ValueError as ex:
            skipped.append(f"{name} ({ex})")
            continue
        if who:
            e.responsible = who[:120]
            db.add(e)
        added.append(e.name)
    db.flush()
    log(db, "docwatch_companies_imported", actor=me, target="entities",
        detail=f"{len(added)} added, {len(updated)} given a responsible person, {len(skipped)} already there", ip=client_ip(request))
    bits = []
    if added:
        bits.append(f"{len(added)} entit{'ies' if len(added) != 1 else 'y'} added")
    if updated:
        bits.append(f"{len(updated)} given a responsible person")
    if skipped:
        bits.append(f"{len(skipped)} already on the grid")
    return {"added": added, "updated": updated, "skipped": skipped,
            "message": (", ".join(bits) + ".") if bits else "Nothing in that list — paste one company per line."}


class TypeImport(BaseModel):
    text: str


@router.post("/types/import")
def import_types(body: TypeImport, request: Request, db: Session = Depends(get_db), me: User = Depends(need)):
    """Paste the document types, one per line. A leading '1.' or '- ' is stripped, so a list can be
    pasted straight out of the mail it arrived in."""
    _admin(db, me, request, "document types", "add document types")
    added, skipped = [], []
    order = (db.query(func.max(DocType.order)).scalar() or 100)
    for raw in (body.text or "").splitlines():
        name = re.sub(r"^\s*(\d+[.)]|[-*\u2022])\s*", "", raw).strip()[:120]
        if not name:
            continue
        if db.query(DocType).filter(DocType.name.ilike(name)).first() is not None:
            skipped.append(name)
            continue
        order += 10
        # Short label for the rotated column heading. An initialism turns "COIDA / letter of good
        # standing" into CLOG and "Company reg documents / business licence" into CRDB, which mean
        # nothing until you hover — and a compliance grid is read by scanning, not hovering. Take the
        # part before the first "/" or "(" instead: COIDA, Company reg, AFS, BBBEE certif.
        short = re.split(r"[/(]", name)[0].strip()
        dt = DocType(name=name, prefix=(short or name)[:12].strip(), order=order)
        db.add(dt)
        db.flush()
        S.sync_matrix(db, me, types=[dt])
        added.append(name)
    db.flush()
    log(db, "docwatch_types_imported", actor=me, target="document types", detail=f"{len(added)} added, {len(skipped)} already there", ip=client_ip(request))
    return {"added": added, "skipped": skipped,
            "message": f"{len(added)} column{'s' if len(added) != 1 else ''} added" + (f", {len(skipped)} already there." if skipped else ".")}


@router.delete("/entities/{eid}")
def delete_entity(eid: int, request: Request, force: bool = False, db: Session = Depends(get_db), me: User = Depends(need)):
    """Admins only. The document records go; every stored file stays in the warehouse.

    Refuses a company that has filed anything unless `force=true` — see the message below."""
    e = _entity(db, me, eid, request)
    _admin(db, me, request, e.name, "delete entity")
    docs = db.query(WatchedDoc).filter(WatchedDoc.entity_id == e.id).all()
    n = len(docs)
    # Deleting is for a company added by mistake. One that has actually filed something is a record
    # of work done, and "we stopped servicing them" is not a reason to destroy it — that is archive.
    filed = [d for d in docs if d.file_id or d.expiry or d.history]
    if filed and not force:
        raise HTTPException(400, f"'{e.name}' has {len(filed)} document{'s' if len(filed) != 1 else ''} on file. If you have stopped working with them, archive the entity instead — it leaves the grid and keeps the record. Delete is for an entity added by mistake.")
    for d in docs:
        db.delete(d)
    # the company's grants go with it; its warehouse folder stays (the files are the record) but no
    # longer points at a company that isn't there
    from ..models import Folder
    from ..models.docs import EntityAccess

    for a in db.query(EntityAccess).filter(EntityAccess.entity_id == e.id).all():
        db.delete(a)
    for f in db.query(Folder).filter(Folder.entity_id == e.id).all():
        f.entity_id = None
        db.add(f)
    db.flush()  # cells first — there is no ORM relationship to order the deletes, and the database enforces the FK
    name = e.name
    db.delete(e)
    db.flush()
    log(db, "docwatch_company_deleted", actor=me, target=name, detail=f"{n} document record(s) removed — stored files kept in the warehouse", ip=client_ip(request))
    return {"ok": True, "removed": n}


# ---------------------------------------------------------------------------------------------- columns (admin)


class NewType(BaseModel):
    name: str
    prefix: str = ""
    order: int = 100
    warn_days: int = 90


class TypePatch(BaseModel):
    name: Optional[str] = None
    prefix: Optional[str] = None
    order: Optional[int] = None
    warn_days: Optional[int] = None
    is_active: Optional[bool] = None


@router.get("/types")
def list_types(request: Request, db: Session = Depends(get_db), me: User = Depends(need)):
    _admin(db, me, request, "columns", "list columns")
    counts = dict(db.query(WatchedDoc.doc_type_id, func.count(WatchedDoc.id)).filter(WatchedDoc.doc_type_id.isnot(None)).group_by(WatchedDoc.doc_type_id).all())
    return {"types": [S.type_out(t, counts.get(t.id, 0)) for t in S.all_types(db)]}


@router.post("/types")
def add_type(body: NewType, request: Request, db: Session = Depends(get_db), me: User = Depends(need)):
    _admin(db, me, request, body.name, "add column")
    try:
        dt, n = S.create_type(db, me, body.name, prefix=body.prefix, order=body.order, warn_days=body.warn_days)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return {**S.type_out(dt, n), "created_cells": n}


@router.patch("/types/{tid}")
def patch_type(tid: int, body: TypePatch, request: Request, db: Session = Depends(get_db), me: User = Depends(need)):
    dt = db.get(DocType, tid)
    if dt is None:
        raise HTTPException(404, "That column isn't on the grid.")
    _admin(db, me, request, dt.name, "edit column")
    changes = []
    cells = db.query(WatchedDoc).filter(WatchedDoc.doc_type_id == dt.id).all()
    if body.name is not None:
        new = " ".join(body.name.split())[:120]
        if not new:
            raise HTTPException(400, "Give the document type a name.")
        clash = db.query(DocType).filter(DocType.name.ilike(new), DocType.id != dt.id).first()
        if clash is not None:
            raise HTTPException(400, f"'{clash.name}' is already a column.")
        if new != dt.name:
            for c in cells:  # cells still called by the old type name follow it
                if c.title == dt.name:
                    c.title = new
                c.doc_type = new
                db.add(c)
            changes.append(f"renamed '{dt.name}' → '{new}'")
            dt.name = new
    if body.prefix is not None and body.prefix.strip()[:12] != dt.prefix:
        dt.prefix = body.prefix.strip()[:12]
        changes.append(f"label {dt.label}")
    if body.order is not None and body.order != dt.order:
        dt.order = body.order
        changes.append(f"order {dt.order}")
    if body.warn_days is not None and body.warn_days != dt.warn_days:
        if not (1 <= body.warn_days <= 730):
            raise HTTPException(400, "Warn between 1 and 730 days ahead.")
        for c in cells:  # cells still at the column's old default follow it; ones a person changed keep theirs
            if c.warn_days == dt.warn_days:
                c.warn_days = body.warn_days
                db.add(c)
        dt.warn_days = body.warn_days
        changes.append(f"warn {dt.warn_days} days")
    if body.is_active is not None and body.is_active != dt.is_active:
        dt.is_active = body.is_active
        changes.append("shown" if dt.is_active else "hidden")
    db.add(dt)
    db.flush()
    made = S.sync_matrix(db, me, types=[dt]) if dt.is_active else 0
    log(db, "docwatch_column_updated", actor=me, target=dt.name, detail=(", ".join(changes) or "no change") + (f" · {made} cell(s) created" if made else ""), ip=client_ip(request))
    return S.type_out(dt, len(cells) + made)


@router.delete("/types/{tid}")
def delete_type(tid: int, request: Request, db: Session = Depends(get_db), me: User = Depends(need)):
    """The column and its cells go for every company; stored files stay in the warehouse."""
    dt = db.get(DocType, tid)
    if dt is None:
        raise HTTPException(404, "That column isn't on the grid.")
    _admin(db, me, request, dt.name, "remove column")
    cells = db.query(WatchedDoc).filter(WatchedDoc.doc_type_id == dt.id).all()
    for c in cells:
        db.delete(c)
    db.flush()  # cells first (FK)
    name, n = dt.name, len(cells)
    db.delete(dt)
    db.flush()
    log(db, "docwatch_column_deleted", actor=me, target=name, detail=f"{n} cell(s) removed — stored files kept in the warehouse", ip=client_ip(request))
    return {"ok": True, "removed": n}


# ---------------------------------------------------------------------------------------------- cells


class NewCell(BaseModel):
    entity_id: int
    doc_type_id: int


@router.post("/cells")
def get_or_make_cell(body: NewCell, request: Request, db: Session = Depends(get_db), me: User = Depends(need)):
    """The grid shows an open dot for a company × type that has no record yet; clicking it makes one."""
    e = _entity(db, me, body.entity_id, request)
    dt = db.get(DocType, body.doc_type_id)
    if dt is None:
        raise HTTPException(404, "That column isn't on the grid.")
    c = S.cell(db, e, dt)
    if c is None:
        S.sync_matrix(db, me, entities=[e], types=[dt])
        c = S.cell(db, e, dt)
    return S.doc_out(c, db, me, ent=e, dt=dt)


@router.get("/{did}")
def one(did: int, request: Request, db: Session = Depends(get_db), me: User = Depends(need)):
    d = _get(db, me, did, request)
    return S.doc_out(d, db, me)


class Patch(BaseModel):
    title: Optional[str] = None
    expiry: Optional[str] = None
    warn_days: Optional[int] = None
    notes: Optional[str] = None
    required: Optional[bool] = None
    archived: Optional[bool] = None
    no_expiry: Optional[bool] = None  # "this document doesn't expire": clears the date, leaves the reminder cycle


@router.patch("/{did}")
def patch(did: int, body: Patch, request: Request, db: Session = Depends(get_db), me: User = Depends(need)):
    """Dates, warning window, notes and "doesn't expire": anyone who can see the company. Name, required
    and archive: admins and the team's manager."""
    d = _get(db, me, did, request)
    ent = db.get(Entity, d.entity_id) if d.entity_id else None
    manage = S.can_manage(me, ent) or (ent is None and (me.is_admin or d.owner_id == me.id))
    changes = []
    if body.title is not None and body.title.strip() and body.title.strip()[:200] != d.title:
        if not manage:
            raise _deny(db, me, request, d.title, "rename document", "Only an admin or your team's manager can rename a document.")
        d.title = " ".join(body.title.split())[:200]
        changes.append("name")
    if body.required is not None and body.required != d.required:
        if not manage:
            raise _deny(db, me, request, d.title, "set required", "Only an admin or your team's manager can change whether this is required.")
        d.required = body.required
        changes.append("required" if d.required else "not required")
    if body.no_expiry is not None and body.no_expiry != d.no_expiry:
        d.no_expiry = body.no_expiry
        if d.no_expiry:
            d.expiry, d.expiry_source, d.expiry_quote, d.reminders_sent = None, "", "", []
            changes.append("doesn't expire")
        else:
            changes.append("expires again")
    if body.expiry is not None and not d.no_expiry:
        if body.expiry.strip() == "":
            if d.expiry is not None:
                d.expiry, d.expiry_source, d.expiry_quote, d.reminders_sent = None, "", "", []
                changes.append("expiry cleared")
        else:
            exp = _date(body.expiry)
            if exp is None:
                raise HTTPException(400, "I couldn't read that date. Use day/month/year, e.g. 31/03/2027.")
            if exp != d.expiry:
                d.expiry, d.expiry_source, d.expiry_quote, d.reminders_sent = exp, "user", "", []
                changes.append(f"expiry {exp.isoformat()}")
    if body.warn_days is not None and body.warn_days != d.warn_days:
        if not (1 <= body.warn_days <= 730):
            raise HTTPException(400, "Warn between 1 and 730 days ahead.")
        d.warn_days = body.warn_days
        changes.append(f"warn {d.warn_days} days")
    if body.notes is not None and body.notes.strip()[:2000] != (d.notes or ""):
        d.notes = body.notes.strip()[:2000]
        changes.append("notes")
    if body.archived is not None and body.archived != d.archived:
        if not manage:
            raise _deny(db, me, request, d.title, "archive", "Only an admin or your team's manager can archive a document.")
        d.archived = body.archived
        changes.append("archived" if d.archived else "restored")
    db.add(d)
    db.flush()
    log(db, "docwatch_updated", actor=me, target=f"{d.title} — {d.entity or 'no company'}", detail=", ".join(changes) or "no change", ip=client_ip(request))
    return S.doc_out(d, db, me, ent=ent)


@router.delete("/{did}")
def delete(did: int, request: Request, db: Session = Depends(get_db), me: User = Depends(need)):
    """Only a company's "other documents" can be deleted (admins anywhere, a manager on their team's
    companies). A grid cell is marked not required instead, or its column removed for everyone."""
    d = _get(db, me, did, request)
    ent = db.get(Entity, d.entity_id) if d.entity_id else None
    if not (me.is_admin or (ent is not None and S.can_manage(me, ent))):
        raise _deny(db, me, request, d.title, "delete", "Only an admin or your team's manager can delete a document record. Archive it instead.")
    if d.doc_type_id:
        raise HTTPException(400, "This is a grid column for this company. Mark it 'not required' instead, or remove the column for everyone.")
    title = d.title
    db.delete(d)
    db.flush()
    log(db, "docwatch_deleted", actor=me, target=f"{title} — {ent.name if ent else 'no company'}", detail="record removed — the file stays in the warehouse", ip=client_ip(request))
    return {"ok": True}


async def _first_file(files: list[UploadFile]) -> tuple[str, bytes]:
    for up in files:
        data = await read_capped(up, MAX_MB * 1024 * 1024)
        if data:
            return up.filename or "document.pdf", data
    raise HTTPException(400, "That file was empty.")


@router.post("/{did}/upload")
@router.post("/{did}/renew")
async def upload_to_cell(did: int, request: Request, files: list[UploadFile] = File(...), db: Session = Depends(get_db), me: User = Depends(need)):
    """Drop a file onto a cell: the first file is attached, a later one is a renewal (the old file goes
    into the cell's history). C-EXA reads the expiry; if it can't, the person is asked."""
    d = _get(db, me, did, request)
    name, data = await _first_file(files)
    out = S.file_document(db, me, d, name, data)
    r = _with_read(db, me, out)
    if out["renewed"]:
        prev = (d.history or [])[-1].get("expiry") if d.history else None
        if prev and d.expiry and d.expiry.isoformat() <= prev:
            r["warning"] = f"The new document's expiry ({human_date(d.expiry, '%b')}) is not later than the old one ({human_date(date.fromisoformat(prev), '%b')}). Check it."
    return r


@router.get("/{did}/download")
def download(did: int, request: Request, inline: bool = False, file: Optional[int] = None, db: Session = Depends(get_db), me: User = Depends(need)):
    """Download (or open in the browser with ?inline=1) through Entity Compliance's own visibility rules
    — a person who can see the company can open its files even though they sit in someone else's folder.
    `?file=<id>` opens one of the cell's earlier files (its renewal history); nothing else is reachable."""
    d = _get(db, me, did, request)
    fid = d.file_id
    if file is not None and file != d.file_id:
        if file not in {h.get("file_id") for h in (d.history or [])}:
            raise HTTPException(404, "That file isn't part of this document's history.")
        fid = file
    f = db.get(StoredFile, fid) if fid else None
    if f is None:
        raise HTTPException(404, "Nothing has been filed for this document yet.")
    p = storage.abs_path(f)
    if not p.exists():
        raise HTTPException(404, "The file is missing on disk. Ask an admin to check the warehouse.")
    log(db, "file_downloaded", actor=me, target=f.rel_path, detail=f"via Entity Compliance: {d.title} — {d.entity}", ip=client_ip(request))
    return FileResponse(p, filename=f.name, content_disposition_type="inline" if inline else "attachment")


# ---------------------------------------------------------------------------------------------- mail about one document


class DocMailIn(BaseModel):
    to: str = ""                 # an address CBX knows (an account, a responsible person) or on the company domain …
    to_user_id: Optional[int] = None  # … or an account by id
    message: str


@router.get("/{did}/mail")
def doc_mail_info(did: int, request: Request, db: Session = Depends(get_db), me: User = Depends(need)):
    """What the Mail dialog needs: who to suggest, and what has been sent about this document before."""
    from .. import mailbox, state
    from ..models import MailLog

    d = _get(db, me, did, request)
    ent = db.get(Entity, d.entity_id) if d.entity_id else None
    suggested = None
    if ent is not None and ent.responsible_user_id:
        u = db.get(User, ent.responsible_user_id)
        if u is not None and u.is_active and u.email:
            suggested = {"email": u.email, "name": u.name, "why": "responsible person"}
    if suggested is None and ent is not None and ent.responsible_email:
        suggested = {"email": ent.responsible_email, "name": ent.responsible or ent.responsible_email, "why": "responsible person (no account)"}
    ready, why = mail_mod.configured()
    sent = db.query(MailLog).filter(MailLog.ref == f"doc:{d.id}").order_by(MailLog.created_at.desc()).limit(10).all()
    users = {u.id: u.name for u in db.query(User).filter(User.id.in_({r.sent_by_id for r in sent if r.sent_by_id} or {0})).all()}
    return {
        "doc": {"id": d.id, "title": d.title, "entity": d.entity, "entity_id": d.entity_id},
        "suggested": suggested,
        "recipients": mailbox.recipients_for_picker(db),
        "domain": mailbox.company_domain(),
        "mail_ready": ready, "mail_note": why,
        "link": f"{state.public_url(db)}/#/doc-watch-entity?id={d.entity_id}&doc={d.id}" if d.entity_id else f"{state.public_url(db)}/#/doc-watch",
        "sent": [{"to": r.to, "at": r.created_at.isoformat() if r.created_at else None, "by": users.get(r.sent_by_id, ""), "ok": r.ok, "preview": r.preview[:200]} for r in sent],
    }


def _status_sentence(d: WatchedDoc) -> str:
    if not d.required:
        return "not required for this entity"
    if d.no_expiry:
        return "filed; it doesn't expire"
    if d.file_id is None and d.expiry is None:
        return "nothing uploaded yet"
    if d.expiry is None:
        return "uploaded, expiry date not set yet"
    from ..util import local_today

    left = (d.expiry - local_today()).days
    when = human_date(d.expiry)
    if left < 0:
        return f"EXPIRED on {when} ({-left} day{'s' if left != -1 else ''} ago)"
    if left == 0:
        return f"expires today, {when}"
    return f"expires on {when} (in {left} day{'s' if left != 1 else ''})"


@router.post("/{did}/mail")
def doc_mail(did: int, body: DocMailIn, request: Request, db: Session = Depends(get_db), me: User = Depends(need), _h: User = Depends(require_human)):
    """A message about this one document, from the person pressing Mail, to someone CBX knows. The mail
    carries their words, which document and company it is about, its status, a link straight to it and a
    link to CBX-Connect — signed with who sent it. Recorded in the outbox; a recipient with an account
    also sees it in their Messages."""
    from .. import mailbox, state
    from ..requests_flow import notify

    d = _get(db, me, did, request)
    ent = db.get(Entity, d.entity_id) if d.entity_id else None
    message = (body.message or "").strip()
    if len(message) < 2:
        raise HTTPException(400, "Write the message first.")
    to_user = None
    to = (body.to or "").strip()
    if body.to_user_id:
        to_user = db.get(User, body.to_user_id)
        if to_user is None or not to_user.is_active or not to_user.email:
            raise HTTPException(400, "That person doesn't have an active account with an email address.")
        to = to_user.email
    if not to:
        raise HTTPException(400, "Choose who it goes to.")
    ok, why = mailbox.allowed(db, to)
    if not ok:
        raise HTTPException(400, why)
    if to_user is None:
        to_user = db.query(User).filter(User.email.ilike(to), User.is_active.is_(True)).first()
    company = ent.name if ent else (d.entity or "no company")
    subject = f"{d.title} — {company}"
    link = f"#/doc-watch-entity?id={d.entity_id}&doc={d.id}" if d.entity_id else "#/doc-watch"
    filed = ""
    if d.file_id:
        f = db.get(StoredFile, d.file_id)
        owner = db.get(User, d.owner_id) if d.owner_id else None
        filed = f"Filed: {f.name if f else 'file'}" + (f" on {human_date(f.created_at.date())}" if f and f.created_at else "") + (f" by {owner.name}" if owner else "")
    text = (
        f"{me.name} wrote:\n\n{message}\n\n"
        f"Document: {d.title} for {company}\n"
        f"Status: {_status_sentence(d)}\n"
        + (filed + "\n" if filed else "")
        + mailbox.footer(db, me, link)
    )
    r = mailbox.send(db, to=[to], subject=subject, body=text, kind="docwatch", ref=f"doc:{d.id}", by=me, reply_to=me.email or "")
    log(db, "docwatch_mailed", actor=me, target=f"{d.title} — {company}", detail=f"to {to}: {message[:160]}" + ("" if r.ok else f" — NOT SENT: {r.detail[:120]}"), outcome="ok" if r.ok else "failed", ip=client_ip(request))
    # the in-app copy: a person with an account sees it in Messages whether or not the mail server was up
    if to_user is not None and to_user.id != me.id:
        notify(db, to_user, f"{me.name} — {d.title} ({company})", f"{message}\n\n{d.title} for {company}: {_status_sentence(d)}.", link=link)
    # a failure is answered, not raised: the outbox row and the in-app copy must survive the request
    if not r.ok:
        return {"ok": False, "message": f"Not sent — {r.detail}" + (" The note was delivered inside CBX-Connect instead." if to_user is not None and to_user.id != me.id else ""), "to": to}
    return {"ok": True, "message": f"Sent to {to_user.name if to_user else to}." + (" They also have it in their Messages here." if to_user is not None and to_user.id != me.id else ""), "to": to}


@router.get("/{did}/text")
def doc_text(did: int, request: Request, db: Session = Depends(get_db), me: User = Depends(need)):
    """The extracted text, so a person can see what C-EXA saw (admins only see this in the UI)."""
    d = _get(db, me, did, request)
    if not d.file_id:
        return {"text": ""}
    f = db.get(StoredFile, d.file_id)
    if f is None:
        return {"text": ""}
    try:
        text, _ = text_of(f.name, storage.read_bytes(f))
    except FileNotFoundError:
        raise HTTPException(404, "The file is missing on disk. Ask an admin to check the warehouse.")
    return {"text": text[:20000]}


# ---------------------------------------------------------------------------------------------- the free-text ways in
# Kept for C-EXA and older callers: a document named by company and type in words. The company is found or
# created (for the caller's team), a type that matches a column lands in that cell, anything else under the
# company's "Other documents".


class NewDoc(BaseModel):
    title: str
    entity: str = ""
    doc_type: str = ""
    expiry: Optional[str] = None
    warn_days: Optional[int] = None
    notes: str = ""
    no_expiry: bool = False


@router.post("")
def create(body: NewDoc, request: Request, db: Session = Depends(get_db), me: User = Depends(need)):
    if body.warn_days is not None and not (1 <= body.warn_days <= 730):
        raise HTTPException(400, "Warn between 1 and 730 days ahead.")
    exp = None
    if body.expiry and body.expiry.strip() and not body.no_expiry:
        exp = _date(body.expiry)
        if exp is None:
            raise HTTPException(400, "I couldn't read that date. Use day/month/year, e.g. 31/03/2027.")
    try:
        d = S.create_line(db, me, title=body.title, entity_name=body.entity, doc_type_name=body.doc_type, expiry=exp, warn_days=body.warn_days, notes=body.notes, no_expiry=body.no_expiry)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    except PermissionError as ex:
        raise _deny(db, me, request, body.entity, "add to company", str(ex))
    return S.doc_out(d, db, me)


@router.post("/upload")
async def upload(request: Request, files: list[UploadFile] = File(...), title: str = Form(""), entity: str = Form(""), doc_type: str = Form(""), warn_days: Optional[int] = Form(None), db: Session = Depends(get_db), me: User = Depends(need)):
    if warn_days is not None and not (1 <= warn_days <= 730):
        raise HTTPException(400, "Warn between 1 and 730 days ahead.")
    out = []
    for up in files:
        data = await read_capped(up, MAX_MB * 1024 * 1024)
        if not data:
            continue
        name = up.filename or "document.pdf"
        text, pages = S._read_file(name, data)
        r = S.read_expiry(db, text)  # once: the company/type it names, and the expiry
        try:
            d = S.create_line(db, me, title=(title if len(files) == 1 and title else "") or S.title_from_filename(name), entity_name=entity or r.get("entity") or "", doc_type_name=doc_type or r.get("doc_type") or "", warn_days=warn_days, reuse_cell=True)
        except ValueError as ex:
            raise HTTPException(400, str(ex))
        except PermissionError as ex:
            raise _deny(db, me, request, entity, "add to company", str(ex))
        res = S.file_document(db, me, d, name, data, text=text, pages=pages, read=r)
        out.append(_with_read(db, me, res))
    if not out:
        raise HTTPException(400, "Those files were empty.")
    return {"docs": out}
