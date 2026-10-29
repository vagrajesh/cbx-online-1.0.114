"""Entity Compliance service: the company × document-type grid, who may see and manage what, cell
statuses, filing a document (C-EXA reads the expiry, with provenance), renewals, and the migration that
gives the old free-text register lines a company and a column."""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from datetime import date
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from ..audit import log
from ..cexa.gateway import Gateway, ModelError
from ..branding import ai_name
from ..db import utcnow
from ..models import StoredFile, Team, User, WatchedDoc
from ..models.docs import DocType, Entity, EntityAccess
from ..util import local_today
from ..warehouse import storage
from ..warehouse.extract import ocr_pdf, text_of
from . import scanner

logger = logging.getLogger("cbx.docwatch")

SYSTEM = (
    "You are {ai}, reading a South African certificate, permit or contract to find the date it expires. "
    "Use only what is in the text. `quote` must be copied exactly from the text (the line that holds the date). "
    "If no expiry date is written, set found=false. Dates are day/month/year in South Africa. "
    "Return expiry as YYYY-MM-DD."
)

# A fresh install starts with these columns and no companies. Admins change them on the grid page.
DEFAULT_TYPES = [
    ("Fire Certificate", "FIRE"),
    ("Public Liability", "PL"),
    ("Tax Clearance", "TAX"),
    ("BBBEE Certificate", "BEE"),
    ("Letter of Good Standing", "LOGS"),
    ("Working Permit", "WP"),
]
UNFILED = "Unfiled documents"

# status → (dot colour, wording). Statuses, in the order the grid ranks a company by:
# archived | not_required (grey) | missing (open — nothing filed) | needs_date (open — filed, no date) |
# expired (red) | expiring (amber, days_left <= warn_days) | ok (green) | no_expiry (green — doesn't expire)
DOT = {
    "ok": ("ok", "In date"),
    "no_expiry": ("ok", "Doesn't expire"),
    "expiring": ("warn", "Expiring soon"),
    "expired": ("bad", "Expired"),
    "needs_date": ("open", "Uploaded, expiry not set"),
    "missing": ("open", "Nothing uploaded yet"),
    "not_required": ("grey", "Not required"),
    "archived": ("grey", "Archived"),
}
WORST_ORDER = {"expired": 0, "expiring": 1, "missing": 2, "ok": 3, "empty": 4}


# ---------------------------------------------------------------------------------------------- reading


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def quote_in_text(quote: str, text: str) -> bool:
    q = _norm(quote)
    return bool(q) and len(q) >= 6 and q in _norm(text)


def read_expiry(db: Session, text: str) -> dict:
    """Return {found, expiry(date|None), quote, doc_type, entity, source: 'axl'|'', reason, check, candidates}."""
    heur = scanner.find_expiry(text)
    hpayload = {"found": heur["found"], "expiry": heur["expiry"].isoformat() if heur["expiry"] else "", "quote": heur["quote"], "doc_type": heur["doc_type"], "entity": heur["entity"]}
    result = {"found": False, "expiry": None, "quote": "", "doc_type": heur["doc_type"], "entity": heur["entity"], "source": "", "reason": "", "check": "", "candidates": []}
    if not text.strip():
        result["reason"] = "The document has no readable text (it may be a photo or scan)."
        return result
    result["candidates"] = scanner.date_candidates(text)
    user_prompt = f"DOCUMENT TEXT (first part):\n{text[:6000]}\n\nHEURISTIC: {json.dumps(hpayload)}\n\nConfirm or correct the heuristic."
    try:
        ans = Gateway(db).json(task="read_expiry", system=SYSTEM.format(ai=ai_name()), user=user_prompt, schema={"expiry": "YYYY-MM-DD or empty", "quote": "exact line from the text", "doc_type": "string", "entity": "site or client", "found": "bool"})
    except ModelError as e:
        logger.warning("read_expiry model error: %s", e)
        ans = {"found": hpayload["found"], "expiry": hpayload["expiry"], "quote": hpayload["quote"], "doc_type": hpayload["doc_type"], "entity": hpayload["entity"]}
    if ans.get("doc_type"):
        result["doc_type"] = str(ans["doc_type"])[:120]
    if ans.get("entity"):
        result["entity"] = str(ans["entity"])[:160]
    if not ans.get("found"):
        result["reason"] = "I couldn't find an expiry date written in this document."
        return result
    exp = _parse_iso(str(ans.get("expiry", "")))
    quote = str(ans.get("quote", ""))[:400]
    if exp is None:
        result["reason"] = "I found something that looked like a date but couldn't make sense of it."
        return result
    if not quote_in_text(quote, text):
        # provenance failed: refuse the model's answer, fall back to heuristics if theirs is verifiable
        if heur["found"] and quote_in_text(heur["quote"], text):
            result.update({"found": True, "expiry": heur["expiry"], "quote": heur["quote"], "source": "axl", "check": heur.get("check", "")})
            return result
        result["reason"] = "I read a date, but couldn't point to where it appears in the document, so I won't guess."
        return result
    result.update({"found": True, "expiry": exp, "quote": quote, "source": "axl", "check": scanner.cross_check(text, exp)})
    return result


def _parse_iso(s: str) -> Optional[date]:
    s = s.strip()[:10]
    try:
        return date.fromisoformat(s)
    except ValueError:
        return scanner.parse_date(s)


def title_from_filename(filename: str) -> str:
    """'fire_certificate_sandton-2026.pdf' → 'Fire certificate sandton 2026' — a register line, not a file name."""
    stem = re.sub(r"[_\-]+", " ", filename.rsplit("/", 1)[-1].rsplit(".", 1)[0]).strip()
    stem = re.sub(r"\s+", " ", stem)
    return (stem[:1].upper() + stem[1:]) if stem else "Document"


def client_of(entity: str) -> str:
    """The short key kept on every row ('Sandton campus · Absa' → 'Sandton campus'), else the first word."""
    e = (entity or "").strip()
    if not e:
        return ""
    for sep in (" · ", " - ", " – ", ","):
        if sep in e:
            return e.split(sep)[0].strip()[:64]
    return e.split(" ")[0][:64]


def initials(name: str, limit: int = 6) -> str:
    """'Fire Certificate' → 'FC'; a column label for a type that came from the old free-text register."""
    words = [w for w in re.split(r"[\s\-/]+", name or "") if w and w[0].isalnum()]
    return "".join(w[0] for w in words)[:limit].upper() or (name or "")[:4].upper()


# ---------------------------------------------------------------------------------------------- who sees what


def scope(db: Session, me: User) -> dict:
    """What this person may see on the grid, beyond the team rule:
        ids   the companies they were given one by one (EntityAccess)
        only  True when their Entity Compliance grant says "only those" (ToolAccess.scope == "granted"):
              then the team rule does not apply at all — they see exactly `ids`, nothing else.
    Admins are never scoped. Cached on the user for the request: the grid asks this for every cell."""
    cached = getattr(me, "_entity_scope", None)
    if cached is not None:
        return cached
    if me.is_admin:
        me._entity_scope = {"ids": set(), "only": False}
        return me._entity_scope
    from ..models import Tool, ToolAccess

    ids = {r[0] for r in db.query(EntityAccess.entity_id).filter(EntityAccess.user_id == me.id).all()}
    ta = db.query(ToolAccess).join(Tool, Tool.id == ToolAccess.tool_id).filter(Tool.slug == "doc-watch", ToolAccess.user_id == me.id).one_or_none()
    me._entity_scope = {"ids": ids, "only": bool(ta is not None and ta.scope == "granted")}
    return me._entity_scope


def forget_scope(me: User) -> None:
    if hasattr(me, "_entity_scope"):
        del me._entity_scope


def set_only(db: Session, user: User, only: bool, by: User) -> bool:
    """Flip a person's Entity Compliance grant between "the ordinary rule plus their companies" and
    "only their companies". Returns True when something changed."""
    from ..models import Tool, ToolAccess

    tool = db.query(Tool).filter(Tool.slug == "doc-watch").one_or_none()
    if tool is None or user.is_admin:
        return False
    ta = db.query(ToolAccess).filter(ToolAccess.tool_id == tool.id, ToolAccess.user_id == user.id).one_or_none()
    if ta is None:
        ta = ToolAccess(tool_id=tool.id, user_id=user.id, granted_by_id=by.id)
        db.add(ta)
    want = "granted" if only else "all"
    if ta.scope == want:
        return False
    ta.scope = want
    db.flush()
    forget_scope(user)
    log(db, "docwatch_scope_changed", actor=by, target=user.email, detail="sees only the companies given to them" if only else "sees the companies open to everyone and their team's, plus those given to them")
    return True


def visible_entities(db: Session, me: User, *, include_archived: bool = False, demo: bool = False) -> list[Entity]:
    """Admins: all. A person given specific companies (EntityAccess): exactly those, nothing else.
    Everyone else (managers included): companies visible to everyone plus their team's.

    Archived companies are left out unless asked for: they are off the grid, not gone, and the
    Archived list on the page is where they are found and brought back.

    `demo` picks which world you are in. False (everything real) hides the Training practice
    entities; True shows only them — the Training lessons open the grid that way so a person can
    file a certificate and archive an entity with nothing real within reach."""
    q = db.query(Entity).filter(Entity.is_demo.is_(bool(demo)))
    if not include_archived:
        q = q.filter(Entity.archived.is_(False))
    if not me.is_admin:
        sc = scope(db, me)
        given = Entity.id.in_(sc["ids"] or {0})
        if sc["only"]:
            q = q.filter(given)
        else:
            team_rule = (Entity.team_id.is_(None)) | (Entity.team_id == me.team_id) if me.team_id else Entity.team_id.is_(None)
            q = q.filter(team_rule | given)
    return q.order_by(Entity.name).all()


def can_see(me: User, ent: Optional[Entity], db: Optional[Session] = None) -> bool:
    """May this person see this company? Admins: yes. Someone with named companies: only those. Anyone
    else: the team rule. `db` is needed to learn about named companies the first time; after that the
    answer is on the user object for the rest of the request."""
    if me.is_admin:
        return True
    if ent is None:
        return False
    sc = getattr(me, "_entity_scope", None)
    if sc is None:
        sc = scope(db, me) if db is not None else {"ids": set(), "only": False}
    if ent.id in sc["ids"]:
        return True
    if sc["only"]:
        return False
    return ent.team_id is None or ent.team_id == me.team_id


def can_manage(me: User, ent: Optional[Entity]) -> bool:
    """Renaming, required/not required, archiving: admins, and the manager of the company's team."""
    if me.is_admin:
        return True
    if ent is None:
        return False
    if me.is_manager and ent.team_id and ent.team_id == me.team_id:
        return True
    # An account linked to the entity is the exact answer and is tried first.
    if ent.responsible_user_id and ent.responsible_user_id == me.id:
        return True
    # Otherwise the name on the grid, matched on the first 4 letters of the first name, so short
    # forms and spelling variants count ("Philo" ↔ "Philothee", "Lauriane" ↔ "Lauraine") while
    # different people ("Ravi" vs "Phillip") never collide. Link an account to replace this guess
    # with a certainty.
    if not ent.responsible or not me.name:
        return False
    a = ent.responsible.strip().lower()[:4]
    b = me.name.split()[0].strip().lower()[:4]
    return bool(a) and a == b


def can_see_doc(db: Session, me: User, d: WatchedDoc) -> bool:
    if me.is_admin:
        return True
    if d.entity_id:
        return can_see(me, db.get(Entity, d.entity_id), db)
    return d.owner_id == me.id  # a line with no company yet: only the person who filed it


def active_types(db: Session) -> list[DocType]:
    return db.query(DocType).filter(DocType.is_active.is_(True)).order_by(DocType.order, DocType.name).all()


def all_types(db: Session) -> list[DocType]:
    return db.query(DocType).order_by(DocType.order, DocType.name).all()


# ---------------------------------------------------------------------------------------------- the grid


def _mirror(d: WatchedDoc, ent: Optional[Entity], dt: Optional[DocType]) -> None:
    """Keep the free-text columns in step with the linked rows (C-EXA's briefings read them)."""
    if ent is not None:
        d.entity, d.client, d.team_id = ent.name, client_of(ent.name), ent.team_id
    if dt is not None:
        d.doc_type = dt.name


def sync_matrix(db: Session, me: User, entities: Optional[Iterable[Entity]] = None, types: Optional[Iterable[DocType]] = None) -> int:
    """Make sure every company has one cell per active document type. Cheap and idempotent — safe on
    every grid load. A legacy line of the same name (untyped) is adopted as the cell instead of a new one."""
    types = list(types) if types is not None else active_types(db)
    ents = list(entities) if entities is not None else db.query(Entity).all()
    if not types or not ents:
        return 0
    ent_ids = [e.id for e in ents]
    docs = db.query(WatchedDoc).filter(WatchedDoc.entity_id.in_(ent_ids)).all()
    have = {(d.entity_id, d.doc_type_id) for d in docs if d.doc_type_id}
    untyped: dict[int, list[WatchedDoc]] = {}
    for d in docs:
        if d.doc_type_id is None and not d.archived:
            untyped.setdefault(d.entity_id, []).append(d)
    made = 0
    for e in ents:
        for t in types:
            if (e.id, t.id) in have:
                continue
            legacy = next((d for d in untyped.get(e.id, []) if _norm(d.doc_type) == _norm(t.name) or _norm(d.title) == _norm(t.name)), None)
            if legacy is not None:
                legacy.doc_type_id = t.id
                _mirror(legacy, e, t)
                untyped[e.id].remove(legacy)
                db.add(legacy)
            else:
                db.add(WatchedDoc(entity_id=e.id, doc_type_id=t.id, title=t.name, doc_type=t.name, entity=e.name, client=client_of(e.name), team_id=e.team_id, owner_id=me.id, warn_days=t.warn_days))
                made += 1
            have.add((e.id, t.id))
    db.flush()
    return made


def create_entity(db: Session, me: User, name: str, *, team_id: Optional[int] = None, notes: str = "", responsible: str = "", scope_from_user: bool = True) -> Entity:
    """Admins choose who sees the company (None = everyone with the tool); everyone else adds companies
    for their own team. Raises ValueError with a sentence the person can act on."""
    name = re.sub(r"\s+", " ", (name or "")).strip()[:160]
    if not name:
        raise ValueError("Give the company a name.")
    if db.query(Entity).filter(Entity.name.ilike(name)).first() is not None:
        raise ValueError(f"'{name}' is already on the grid.")
    if me.is_admin:
        if team_id is not None and db.get(Team, team_id) is None:
            raise ValueError("That team doesn't exist.")
    elif scope_from_user:
        team_id = me.team_id
    ent = Entity(name=name, notes=(notes or "").strip()[:300], responsible=(responsible or "").strip()[:80], team_id=team_id, created_by_id=me.id)
    db.add(ent)
    db.flush()
    n = sync_matrix(db, me, entities=[ent])
    team = db.get(Team, team_id) if team_id else None
    log(db, "docwatch_company_added", actor=me, target=ent.name, detail=(f"visible to {team.name} only" if team else "visible to everyone with the tool") + f" · {n} cell(s) created")
    return ent


def find_or_create_entity(db: Session, me: User, name: str) -> Entity:
    """For the free-text ways in (the old upload/add-line calls): an existing company by name, else a
    new one scoped to the person's team. Raises PermissionError when the company exists but is another team's."""
    name = re.sub(r"\s+", " ", (name or "")).strip()[:160] or UNFILED
    ent = db.query(Entity).filter(Entity.name.ilike(name)).first()
    if ent is not None:
        if not can_see(me, ent, db):
            raise PermissionError(f"'{ent.name}' belongs to another team.")
        return ent
    return create_entity(db, me, name, team_id=me.team_id, scope_from_user=True)


def type_by_name(db: Session, name: str) -> Optional[DocType]:
    n = _norm(name)
    if not n:
        return None
    return next((t for t in db.query(DocType).all() if _norm(t.name) == n or _norm(t.prefix) == n), None)


def cell(db: Session, ent: Entity, dt: DocType) -> Optional[WatchedDoc]:
    return db.query(WatchedDoc).filter(WatchedDoc.entity_id == ent.id, WatchedDoc.doc_type_id == dt.id).one_or_none()


def create_type(db: Session, me: User, name: str, *, prefix: str = "", order: int = 100, warn_days: int = 90) -> tuple[DocType, int]:
    name = re.sub(r"\s+", " ", (name or "")).strip()[:120]
    if not name:
        raise ValueError("Give the document type a name, e.g. 'Fire Certificate'.")
    if db.query(DocType).filter(DocType.name.ilike(name)).first() is not None:
        raise ValueError(f"'{name}' is already a column.")
    if not (1 <= warn_days <= 730):
        raise ValueError("Warn between 1 and 730 days ahead.")
    dt = DocType(name=name, prefix=(prefix or "").strip()[:12], order=int(order), warn_days=warn_days)
    db.add(dt)
    db.flush()
    n = sync_matrix(db, me, types=[dt])
    log(db, "docwatch_column_added", actor=me, target=dt.name, detail=f"label {dt.label} · {n} company cell(s) created")
    return dt, n


# ---------------------------------------------------------------------------------------------- statuses


def days_left(d: WatchedDoc, today: Optional[date] = None) -> Optional[int]:
    if d.expiry is None:
        return None
    return (d.expiry - (today or local_today())).days


def status_of(d: WatchedDoc, today: Optional[date] = None) -> str:
    """archived | not_required | no_expiry | missing | needs_date | expired | expiring | ok — calendar days
    in the business's own day (SAST), never UTC."""
    if getattr(d, "archived", False):
        return "archived"
    if not getattr(d, "required", True):
        return "not_required"
    if getattr(d, "no_expiry", False):
        return "no_expiry"
    n = days_left(d, today)
    if n is None:
        return "needs_date" if getattr(d, "file_id", None) else "missing"
    if n < 0:
        return "expired"
    if n <= d.warn_days:
        return "expiring"
    return "ok"


def dot_of(status: str, d: Optional[WatchedDoc] = None, today: Optional[date] = None) -> tuple[str, str]:
    colour, title = DOT.get(status, ("open", status))
    if d is not None and d.expiry and status not in ("not_required", "archived", "no_expiry"):
        n = days_left(d, today)
        title += f" — {'expired' if n is not None and n < 0 else 'expires'} {d.expiry.day} {d.expiry.strftime('%b %Y')}"
        if n is not None:
            title += f" ({abs(n)} day{'s' if abs(n) != 1 else ''}{' ago' if n < 0 else ''})" if n != 0 else " (today)"
    return colour, title


def summarise(docs: Iterable[WatchedDoc], today: Optional[date] = None) -> dict:
    """Roll-up for a company (or the whole grid): counts per status, outstanding = missing + needs_date,
    in_date = ok + no_expiry, worst = what the company's row is ranked by."""
    out = {k: 0 for k in ("total", "expired", "expiring", "ok", "no_expiry", "needs_date", "missing", "not_required")}
    soonest = None
    for d in docs:
        if d.archived:
            continue
        s = status_of(d, today)
        out["total"] += 1
        out[s] = out.get(s, 0) + 1
        if d.expiry and d.required and not d.no_expiry and (soonest is None or d.expiry < soonest):
            soonest = d.expiry
    out["outstanding"] = out["missing"] + out["needs_date"]
    out["in_date"] = out["ok"] + out["no_expiry"]
    out["soonest"] = soonest.isoformat() if soonest else None
    out["worst"] = "expired" if out["expired"] else "expiring" if out["expiring"] else "missing" if out["outstanding"] else "ok" if out["in_date"] else "empty"
    return out


def due_count(db: Session, me: User) -> int:
    """Sidebar count: cells the person can see that have expired or are inside their warning window."""
    from .copy_in import use_tool

    if use_tool(db):  # 1.0.63: the tool folder holds Entity Compliance now
        from .tool import due_count as tool_due

        return tool_due(db, me)
    q = db.query(WatchedDoc).filter(WatchedDoc.archived.is_(False), WatchedDoc.required.is_(True), WatchedDoc.no_expiry.is_(False), WatchedDoc.expiry.isnot(None))
    if not me.is_admin:
        ids = [e.id for e in visible_entities(db, me)]
        q = q.filter((WatchedDoc.entity_id.in_(ids)) | ((WatchedDoc.entity_id.is_(None)) & (WatchedDoc.owner_id == me.id)))
    today = local_today()
    return sum(1 for d in q.all() if status_of(d, today) in ("expired", "expiring"))


# ---------------------------------------------------------------------------------------------- filing


def _read_file(filename: str, data: bytes) -> tuple[str, int]:
    text, pages = text_of(filename, data)
    if not text.strip() and filename.lower().endswith(".pdf"):
        text = ocr_pdf(data, max_pages=3)
    return text, pages


def scan_out(r: dict) -> dict:
    return {"found": bool(r.get("found")), "expiry": r["expiry"].isoformat() if isinstance(r.get("expiry"), date) else (r.get("expiry") or None), "quote": r.get("quote", ""), "reason": r.get("reason", ""), "check": r.get("check", ""), "candidates": r.get("candidates", []), "at": utcnow().isoformat()}


def file_document(db: Session, me: User, doc: WatchedDoc, filename: str, data: bytes, *, text: Optional[str] = None, pages: int = 0, read: Optional[dict] = None) -> dict:
    """The one way a file gets onto a cell. First file: attach it (a date a person typed only gives way
    to a later one on paper — a typed date is a promise, a later one is a renewal). Renewal: the old file
    goes into the cell's history and the new file's date replaces the old one; if nothing can be read the
    date is cleared so the cell asks for it rather than showing a stale colour."""
    ent = db.get(Entity, doc.entity_id) if doc.entity_id else None
    dt = db.get(DocType, doc.doc_type_id) if doc.doc_type_id else None
    if text is None:
        text, pages = _read_file(filename, data)
    meta = {"purpose": "docwatch", "entity": ent.name if ent else doc.entity, "doc_type": dt.name if dt else doc.doc_type, "entity_id": ent.id if ent else None, "doc_id": doc.id}
    if ent is not None:
        # The company's own folder: Tools/Entity Compliance/<Company>/<Document type>. No copy in the
        # shared library — a certificate belongs to the company it certifies, and who may open it is
        # who may see the company.
        from ..warehouse import folders as F

        folder = F.compliance_folder(db, ent, (dt.name if dt else doc.doc_type) or "Other documents")
        f = storage.store_in(db, folder, me, filename, data, source="docwatch", mirror=False, text=text, pages=pages, meta=meta)
    else:
        f = storage.store(db, me, filename, data, sub="Work", source="docwatch", mirror=False, text=text, pages=pages, meta=meta)
    # Deliberately NOT read_expiry(): filing no longer guesses the date off the certificate. A wrong
    # date shows a confident colour on the grid and nobody checks it — the person types it in the
    # dialog instead, and the old date stays until they do.
    r = read if read is not None else {"found": False, "expiry": None, "quote": "", "doc_type": "", "entity": "", "source": "", "reason": "", "check": "", "candidates": []}
    renewed = doc.file_id is not None
    if renewed:
        old = db.get(StoredFile, doc.file_id)
        doc.history = [*(doc.history or []), {"file_id": doc.file_id, "file_name": old.name if old else "", "expiry": doc.expiry.isoformat() if doc.expiry else None, "filed_at": old.created_at.isoformat() if old and old.created_at else None, "replaced_at": utcnow().isoformat(), "by": me.name}]
    doc.file_id = f.id
    doc.owner_id = me.id
    doc.scan = {}
    doc.archived = False
    doc.required = True
    db.add(doc)
    db.flush()
    log(db, "docwatch_renewed" if renewed else "docwatch_filed", actor=me, target=f"{doc.title} — {doc.entity or 'no company'}", detail=(f"expiry {doc.expiry} (set by hand)" if doc.expiry else "no expiry set yet — person to type it"))
    return {"doc": doc, "file": f, "read": r, "renewed": renewed}


def create_line(db: Session, me: User, *, title: str, entity_name: str = "", doc_type_name: str = "", expiry: Optional[date] = None, warn_days: Optional[int] = None, notes: str = "", no_expiry: bool = False, reuse_cell: bool = False) -> WatchedDoc:
    """A document named in words (company, type): when the type matches a column, that company's cell is
    used — an empty one is filled in; one that already holds something is refused unless `reuse_cell`
    (an upload, which then files a renewal into it). Anything else goes under the company's "Other
    documents". Raises ValueError / PermissionError with a sentence for the person."""
    title = re.sub(r"\s+", " ", (title or "")).strip()[:200]
    if not title:
        raise ValueError("Give the document a name, e.g. 'Fire certificate'.")
    ent = find_or_create_entity(db, me, entity_name)
    dt = type_by_name(db, doc_type_name) or type_by_name(db, title)
    if dt is not None:
        c = cell(db, ent, dt)
        if c is None:
            sync_matrix(db, me, entities=[ent], types=[dt])
            c = cell(db, ent, dt)
        if c.file_id or c.expiry or c.archived:
            if not reuse_cell:
                raise ValueError(f"'{ent.name}' already has a {dt.name} cell — open it on the grid instead.")
            return c
        doc = c
        doc.title = title
    else:
        doc = WatchedDoc(entity_id=ent.id, title=title, doc_type=(doc_type_name or "").strip()[:120], owner_id=me.id)
        _mirror(doc, ent, None)
    doc.warn_days = warn_days if warn_days else (dt.warn_days if dt else 90)
    doc.no_expiry = bool(no_expiry)
    doc.expiry = None if no_expiry else expiry
    doc.expiry_source = "user" if (expiry and not no_expiry) else ""
    doc.notes = (notes or "").strip()[:2000]
    doc.owner_id = me.id
    db.add(doc)
    db.flush()
    log(db, "docwatch_added", actor=me, target=f"{doc.title} — {ent.name}", detail="added without a file, doesn't expire" if no_expiry else f"added without a file, expiry {doc.expiry or 'not set'}")
    return doc


# ---------------------------------------------------------------------------------------------- output


def _file_out(f: Optional[StoredFile]) -> dict:
    if f is None:
        return {"file_id": None, "file_name": "", "file_kind": "", "file_size": 0, "filed_at": None}
    return {"file_id": f.id, "file_name": f.name, "file_kind": f.kind, "file_size": f.size, "filed_at": f.created_at.isoformat() if f.created_at else None}


def doc_out(d: WatchedDoc, db: Session, me: Optional[User] = None, *, ent: Optional[Entity] = None, dt: Optional[DocType] = None, file: Optional[StoredFile] = None, owner: Optional[User] = None, today: Optional[date] = None, compact: bool = False) -> dict:
    ent = ent or (db.get(Entity, d.entity_id) if d.entity_id else None)
    dt = dt or (db.get(DocType, d.doc_type_id) if d.doc_type_id else None)
    s = status_of(d, today)
    colour, title = dot_of(s, d, today)
    out = {
        "id": d.id,
        "entity_id": d.entity_id,
        "entity": ent.name if ent else (d.entity or ""),
        "doc_type_id": d.doc_type_id,
        "doc_type": dt.name if dt else (d.doc_type or ""),
        "label": dt.label if dt else initials(d.doc_type) if d.doc_type else "",
        "title": d.title,
        "required": bool(d.required),
        "expiry": d.expiry.isoformat() if d.expiry else None,
        "no_expiry": bool(d.no_expiry),
        "days_left": None if d.no_expiry else days_left(d, today),
        "warn_days": d.warn_days,
        "status": s,
        "dot": colour,
        "dot_title": title,
        "file_id": d.file_id,
        "archived": bool(d.archived),
    }
    if compact:
        return out
    f = file or (db.get(StoredFile, d.file_id) if d.file_id else None)
    o = owner or db.get(User, d.owner_id)
    out.update(_file_out(f))
    out.update({
        "filed_by": o.name if o else "",
        "owner_id": d.owner_id,
        "team_id": ent.team_id if ent else d.team_id,
        "expiry_source": d.expiry_source or "",
        "expiry_quote": d.expiry_quote or "",
        "scan": d.scan or {},
        "history": d.history or [],
        "notes": d.notes or "",
        "reminders_sent": d.reminders_sent or [],
        "can_manage": can_manage(me, ent) if me else False,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
    })
    return out


def type_out(t: DocType, cells: Optional[int] = None) -> dict:
    o = {"id": t.id, "name": t.name, "prefix": t.prefix, "label": t.label, "order": t.order, "warn_days": t.warn_days, "is_active": bool(t.is_active)}
    if cells is not None:
        o["cells"] = cells
    return o


def entity_out(e: Entity, team: Optional[Team], me: User) -> dict:
    return {"id": e.id, "name": e.name, "notes": e.notes or "", "team_id": e.team_id, "team": team.name if team else "",
            "responsible": e.responsible or "", "responsible_user_id": e.responsible_user_id,
            "archived": bool(e.archived), "archived_reason": e.archived_reason or "",
            "archived_at": e.archived_at.isoformat() if e.archived_at else None,
            "can_manage": can_manage(me, e), "created_at": e.created_at.isoformat() if e.created_at else None}


def grid(db: Session, me: User, *, demo: bool = False) -> dict:
    """The dashboard: companies the person can see × active document types, one dot per cell, ranked
    with the worst company first. Also the flat list of live documents (what the old register returned).
    `demo=True` is the Training grid: only the practice entities."""
    types = active_types(db)
    ents = visible_entities(db, me, demo=demo)
    sync_matrix(db, me, entities=ents, types=types)
    teams = {t.id: t for t in db.query(Team).all()}
    today = local_today()
    docs = db.query(WatchedDoc).filter(WatchedDoc.entity_id.in_([e.id for e in ents])).all() if ents else []
    by_ent: dict[int, list[WatchedDoc]] = {}
    for d in docs:
        by_ent.setdefault(d.entity_id, []).append(d)
    tmap = {t.id: t for t in types}
    rows = []
    totals = {"companies": len(ents), "expired": 0, "expiring": 0, "outstanding": 0, "in_date": 0, "ok": 0, "no_expiry": 0, "missing": 0, "needs_date": 0, "not_required": 0, "total": 0}
    flat: list[dict] = []
    for e in ents:
        mine = by_ent.get(e.id, [])
        live = [d for d in mine if not d.archived]
        s = summarise(live, today)
        for k in ("expired", "expiring", "outstanding", "in_date", "ok", "no_expiry", "missing", "needs_date", "not_required", "total"):
            totals[k] += s.get(k, 0)
        by_type = {d.doc_type_id: d for d in mine if d.doc_type_id}
        cells = [{"type_id": t.id, "doc": doc_out(by_type[t.id], db, me, ent=e, dt=t, today=today, compact=True) if t.id in by_type else None} for t in types]
        extra = [doc_out(d, db, me, ent=e, today=today, compact=True) for d in live if d.doc_type_id is None]
        rows.append({**entity_out(e, teams.get(e.team_id), me), "summary": s, "cells": cells, "extra": extra})
        flat.extend(doc_out(d, db, me, ent=e, dt=tmap.get(d.doc_type_id), today=today, compact=True) for d in live)
    rows.sort(key=lambda r: (WORST_ORDER.get(r["summary"]["worst"], 9), r["name"].lower()))
    return {"types": [type_out(t) for t in types], "entities": rows, "summary": totals, "docs": flat}


def entity_page(db: Session, me: User, e: Entity) -> dict:
    types = active_types(db)
    sync_matrix(db, me, entities=[e], types=types)
    today = local_today()
    docs = db.query(WatchedDoc).filter(WatchedDoc.entity_id == e.id).all()
    users = {u.id: u for u in db.query(User).filter(User.id.in_({d.owner_id for d in docs} or {0})).all()}
    files = {f.id: f for f in db.query(StoredFile).filter(StoredFile.id.in_({d.file_id for d in docs if d.file_id} or {0})).all()}
    by_type = {d.doc_type_id: d for d in docs if d.doc_type_id}
    full = lambda d, t=None: doc_out(d, db, me, ent=e, dt=t, file=files.get(d.file_id), owner=users.get(d.owner_id), today=today)  # noqa: E731
    cells = [{"type": type_out(t), "doc": full(by_type[t.id], t) if t.id in by_type else None} for t in types]
    extra = [full(d) for d in docs if d.doc_type_id is None and not d.archived]
    live = [d for d in docs if not d.archived]
    team = db.get(Team, e.team_id) if e.team_id else None
    attention = [c["doc"] for c in cells if c["doc"] and c["doc"]["status"] in ("expired", "expiring")] + [x for x in extra if x["status"] in ("expired", "expiring")]
    attention.sort(key=lambda x: (x["days_left"] if x["days_left"] is not None else 10**6))
    return {"entity": entity_out(e, team, me), "summary": summarise(live, today), "cells": cells, "extra": extra, "attention": attention, "can_manage": can_manage(me, e), "is_admin": me.is_admin}


# ---------------------------------------------------------------------------------------------- migration & seed


def seed_default_types(db: Session) -> int:
    """A fresh install (no columns, no register lines) starts with a few sensible columns."""
    if db.query(DocType).count() or db.query(WatchedDoc).count():
        return 0
    for i, (name, prefix) in enumerate(DEFAULT_TYPES):
        db.add(DocType(name=name, prefix=prefix, order=(i + 1) * 10, warn_days=90))
    db.flush()
    return len(DEFAULT_TYPES)


def migrate_legacy(db: Session) -> dict:
    """Give every free-text register line a company and a column. Companies come from the distinct
    `entity` strings (team from the line's team_id; blank → "Unfiled documents"), columns from the distinct
    `doc_type` strings (label = initials, order of first appearance, warn days = the most common on that
    type, else 90). One live line per company × type becomes the cell; further live lines of the same pair
    and archived lines stay as the company's other documents (archived ones also go into the cell's
    history). Idempotent: only lines without a company are touched."""
    rows = db.query(WatchedDoc).filter(WatchedDoc.entity_id.is_(None)).order_by(WatchedDoc.id).all()
    out = {"entities": 0, "types": 0, "docs": 0}
    if not rows:
        return out
    # columns
    types: dict[str, DocType] = {_norm(t.name): t for t in db.query(DocType).all()}
    for p in list(types.values()):
        if p.prefix:
            types.setdefault(_norm(p.prefix), p)
    next_order = (max((t.order for t in types.values()), default=0) // 10 + 1) * 10
    warns: dict[str, Counter] = {}
    for d in rows:
        k = _norm(d.doc_type)
        if k:
            warns.setdefault(k, Counter())[int(d.warn_days or 90)] += 1
    for d in rows:
        k = _norm(d.doc_type)
        if not k or k in types:
            continue
        name = re.sub(r"\s+", " ", d.doc_type.strip())[:120]
        warn = warns[k].most_common(1)[0][0] if warns.get(k) else 90
        t = DocType(name=name, prefix=initials(name), order=next_order, warn_days=max(1, min(730, warn)))
        db.add(t)
        db.flush()
        types[k] = t
        next_order += 10
        out["types"] += 1
    # companies
    teams = {t.id: t for t in db.query(Team).all()}
    ents: dict[str, Entity] = {_norm(e.name): e for e in db.query(Entity).all()}
    team_votes: dict[str, Counter] = {}
    for d in rows:
        team_votes.setdefault(_norm(d.entity) or "", Counter())[d.team_id] += 1

    def ent_name(d: WatchedDoc) -> str:
        n = re.sub(r"\s+", " ", (d.entity or "").strip())[:160]
        if n:
            return n
        t = teams.get(d.team_id)
        return f"{UNFILED} · {t.name}" if t else UNFILED

    for d in rows:
        name = ent_name(d)
        k = _norm(name)
        if k in ents:
            continue
        votes = team_votes.get(_norm(d.entity) or "", Counter())
        team_id = votes.most_common(1)[0][0] if votes else d.team_id
        if not (d.entity or "").strip():
            team_id = d.team_id
        e = Entity(name=name, team_id=team_id if team_id in teams else None, created_by_id=d.owner_id)
        db.add(e)
        db.flush()
        ents[k] = e
        out["entities"] += 1
    # link — newest live line per (company, type) wins the cell
    taken = {(d.entity_id, d.doc_type_id) for d in db.query(WatchedDoc).filter(WatchedDoc.doc_type_id.isnot(None)).all()}
    winners: dict[tuple[int, int], WatchedDoc] = {}
    ordered = sorted(rows, key=lambda d: (d.archived, -(d.created_at.timestamp() if d.created_at else 0), -d.id))
    for d in ordered:
        e = ents[_norm(ent_name(d))]
        d.entity_id = e.id
        t = types.get(_norm(d.doc_type))
        if t is not None and not d.archived and (e.id, t.id) not in taken:
            d.doc_type_id = t.id
            taken.add((e.id, t.id))
            winners[(e.id, t.id)] = d
        elif t is not None and d.archived and (e.id, t.id) in winners:
            w = winners[(e.id, t.id)]
            w.history = [*(w.history or []), {"file_id": d.file_id, "file_name": "", "expiry": d.expiry.isoformat() if d.expiry else None, "filed_at": d.created_at.isoformat() if d.created_at else None, "replaced_at": None, "by": "", "note": f"before the grid: line #{d.id}"}]
            db.add(w)
        _mirror(d, e, t if d.doc_type_id else None)
        db.add(d)
        out["docs"] += 1
    db.flush()
    return out
