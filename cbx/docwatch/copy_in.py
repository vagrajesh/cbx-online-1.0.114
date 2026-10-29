"""1.0.63: Entity Compliance is a tool folder (tools/entity-compliance) with its own tables — companies,
document types, documents, and who may see which company — so everything about it can be changed in the
Builder. Once, at start, the built-in tables are copied in:

    doc_types      → doc_types       name, short heading, order, warning days, shown on the grid
    entities       → companies       name, responsible person (the name, the account, the address), notes,
                                     archived; a company that was its team's only becomes "Only its people"
    entity_access  → company_people  who was given which company — plus, for a team's company, the team's
                                     people as they are today
    watched_docs   → documents       every document that holds something (a file, a date, "doesn't expire",
                                     "not required", a note): with its current file and the earlier ones it
                                     replaced, who filed it and when. Empty cells are not copied: an empty
                                     cell is outstanding either way.

The practice companies (training) are not copied. The old rows and files are left exactly as they were and
the old screens become read-only (cbx/api/docwatch.py). Marked done in app_state; it never runs again.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from .. import state
from ..audit import log

KEY = "entity_compliance_copied_to_folder"
SLUG = "entity-compliance"
TABLES = ("companies", "doc_types", "documents", "company_people")


FROZEN = "entity_compliance_old_read_only"


def moved(db: Session) -> bool:
    """True once the built-in Entity Compliance was copied into its tool folder (or the folder already had
    its own rows): from then on the old screens are read-only and everything reads the tool."""
    return bool(state.get(db, FROZEN))


def use_tool(db: Session) -> bool:
    """Whether counts, reminders and mail read the tool: once moved, or as soon as the tool has companies."""
    if moved(db):
        return True
    try:
        from ..engine import loader
        from ..engine.store import Store

        t = loader.registry().tools.get(SLUG)
        return bool(t and "companies" in t.data and Store(SLUG).count("companies"))
    except Exception:  # noqa: BLE001
        return False


def run_at_start(db: Session, force: bool = False) -> dict:
    from ..engine import files as FILES
    from ..engine import loader
    from ..engine.store import Store
    from ..models import StoredFile, User
    from ..models.docs import DocType, Entity, EntityAccess, WatchedDoc
    from ..warehouse import storage

    from ..config import settings

    if state.get(db, KEY) or not (force or settings.entity_compliance_copy):
        return {}
    t = loader.registry().tools.get(SLUG)
    if t is None or not all(n in t.data for n in TABLES):
        return {}
    st = Store(SLUG)
    cols = {n: t.data[n].get("columns") or [] for n in TABLES}
    if any(st.exists(n) and st.rows(n, c, limit=1)["total"] for n, c in cols.items()):
        state.set(db, KEY, "not copied — the Entity Compliance tool already had rows")
        state.set(db, FROZEN, "1")
        log(db, "entity_compliance_copied_to_folder", target=f"tools/{SLUG}", detail="not copied — the tool already had rows")
        return {}
    got = {"companies": 0, "document types": 0, "documents": 0, "files": 0, "earlier files": 0, "people": 0, "files not copied": 0}
    at = lambda d: d.isoformat() if d else None  # noqa: E731

    types: dict[int, int] = {}
    for dt in db.query(DocType).order_by(DocType.order, DocType.id).all():
        r = st.insert("doc_types", cols["doc_types"], {"name": dt.name, "short": dt.prefix or "", "order": dt.order, "warn_days": dt.warn_days, "shown": bool(dt.is_active)}, None, at=at(dt.created_at))
        types[dt.id] = r["id"]
        got["document types"] += 1

    companies: dict[int, int] = {}
    team_of: dict[int, int] = {}
    for e in db.query(Entity).filter(Entity.is_demo.is_(False)).order_by(Entity.id).all():
        r = st.insert("companies", cols["companies"], {
            "name": e.name, "responsible": e.responsible or "", "responsible_account": e.responsible_user_id, "responsible_email": e.responsible_email or "",
            "visible": "Only its people" if e.team_id else "Everyone with the tool", "notes": e.notes or "", "archived": bool(e.archived), "archived_reason": e.archived_reason or ""},
            e.created_by_id, at=at(e.created_at))
        companies[e.id] = r["id"]
        if e.team_id:
            team_of[r["id"]] = e.team_id
        got["companies"] += 1

    seen: set = set()

    def person(cid: int, uid: Optional[int], note: str, by: Optional[int] = None) -> None:
        if not uid or (cid, uid) in seen:
            return
        seen.add((cid, uid))
        st.insert("company_people", cols["company_people"], {"company": cid, "person": uid, "note": note[:200]}, by)
        got["people"] += 1

    for a in db.query(EntityAccess).order_by(EntityAccess.id).all():
        if a.entity_id in companies:
            person(companies[a.entity_id], a.user_id, a.note or "", a.granted_by_id)
    from ..models import Tool, ToolAccess

    only = {u for (u,) in db.query(ToolAccess.user_id).join(Tool, Tool.id == ToolAccess.tool_id).filter(Tool.slug == "doc-watch", ToolAccess.scope == "granted").all()}
    for cid, team_id in team_of.items():
        for (uid,) in db.query(User.id).filter(User.team_id == team_id, User.is_active.is_(True), User.is_axl.is_(False)).all():
            if uid not in only:  # someone given only their own companies never saw their team's
                person(cid, uid, "was this company's team")

    sent: dict = {}
    fcol = next(c for c in cols["documents"] if c["key"] == "file")
    keep = int(fcol.get("keep") or 0)

    def read(fid):
        f = db.get(StoredFile, fid) if fid else None
        p = storage.abs_path(f) if f is not None else None
        return (f.name, p.read_bytes(), f.created_at) if p is not None and p.is_file() else None

    for d in db.query(WatchedDoc).order_by(WatchedDoc.id).all():
        if d.entity_id not in companies:
            continue  # a practice company's, or a document that belonged to no company
        hist = [h for h in (d.history or []) if isinstance(h, dict) and h.get("file_id")]
        holds = d.file_id or d.expiry or d.no_expiry or not d.required or (d.notes or "").strip() or hist or d.archived
        if not holds:
            continue
        r = st.insert("documents", cols["documents"], {
            "company": companies[d.entity_id], "doc_type": types.get(d.doc_type_id) if d.doc_type_id else None, "title": d.title or "",
            "expiry": d.expiry.isoformat() if d.expiry else None, "no_expiry": bool(d.no_expiry), "warn_days": d.warn_days or 90, "required": bool(d.required),
            "notes": d.notes or "", "filed_by": d.owner_id, "archived": bool(d.archived)}, d.owner_id, at=at(d.created_at))
        got["documents"] += 1
        if d.expiry and d.reminders_sent:
            # the reminders already sent for this date go along, so nobody hears about it twice
            from ..util import local_today

            done = [str(x) for x in d.reminders_sent]
            if "0" in done and d.expiry < local_today():
                done.append("expired")
            sent[f"{r['id']}:{d.expiry.isoformat()}"] = sorted(set(done))
        # the earlier files first (oldest first), then the current one on top of them
        kept: set = set()
        earlier: list = []
        for h in sorted(hist, key=lambda x: str(x.get("filed_at") or "")):
            item = read(h.get("file_id"))
            if not item:
                got["files not copied"] += 1
                continue
            try:
                m = FILES.save(SLUG, "documents", r["id"], fcol, item[0], item[1], h.get("by") if isinstance(h.get("by"), int) else d.owner_id, str(h.get("filed_at") or at(item[2]) or ""), keep=kept)
            except FILES.FileError:
                got["files not copied"] += 1
                continue
            kept.add(m["id"])
            earlier.insert(0, {**m, "replaced": h.get("replaced_at")})
            got["earlier files"] += 1
        meta = None
        item = read(d.file_id)
        if item:
            try:
                meta = FILES.save(SLUG, "documents", r["id"], fcol, item[0], item[1], d.owner_id, at(item[2]) or at(d.updated_at) or "", keep=kept)
                got["files"] += 1
            except FILES.FileError:
                got["files not copied"] += 1
        elif d.file_id:
            got["files not copied"] += 1
        if meta is not None or earlier:
            cell = dict(meta or {})
            if earlier and keep:
                cell["earlier"] = earlier[:keep]
            st.set_file("documents", cols["documents"], r["id"], "file", cell)
    if sent:
        import json

        from .tool import SENT_KEY

        state.set(db, SENT_KEY, json.dumps(sent))
    text = ", ".join(f"{v} {k}" for k, v in got.items() if v or k in ("companies", "documents"))
    state.set(db, KEY, text)
    if got["companies"] or got["documents"]:
        state.set(db, FROZEN, "1")
    log(db, "entity_compliance_copied_to_folder", target=f"tools/{SLUG}", detail=text)
    return got
