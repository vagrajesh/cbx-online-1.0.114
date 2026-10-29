"""The Builder: tool folders edited as forms, in a draft, previewed against live data, released as a version.

    GET    /api/builder                          everything the Builder page needs: tools, drafts, providers, pick-lists
    GET    /api/builder/tools/{slug}             one folder — the draft if there is one, else the live folder
    PUT    /api/builder/tools/{slug}             save the draft (always saved; problems come back as sentences)
    DELETE /api/builder/tools/{slug}/draft       throw the draft away
    GET    /api/builder/tools/{slug}/preview/{page}   the page's data, drawn from the DRAFT definition, live data
    POST   /api/builder/release                  package the drafts as a version → staged, tested → Versions page

A draft lives in DATA_DIR/builder/tools/<slug>/ — the same files a tool folder has. Nothing in the live
tree changes until a version made here is promoted, with the same tests, sign-off and notice as any
other version. A person, never the AI.

1.0.80 — Dev → Test → Live. A draft belongs to the person who first changes it (.editor.json): nobody else
saves over it until it is released or thrown away (the owner may take it over). Your Dev area runs the live
app with YOUR drafts (cbx/versions/areas.py); Release packages YOUR drafts. Builders are admins and anyone
with "Propose code changes" (the Developer admin role); a builder who is not an admin sees previews without
live rows — real data is for their Dev area, never production's.
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..audit import log
from ..config import ROOT, settings
from ..db import get_db
from ..engine import loader, providers
from ..engine.loader import ASSET_SUFFIXES, COLUMN_TYPES, LANGUAGES, PAGE_TYPES, SECTIONS, SLUG_RE, WIDGET_TYPES, ToolDef, effective_layout, widget as find_widget
from ..engine.store import Store
from ..models import ApiConnection, Tool, User
from ..security.deps import client_ip, current_user, require_admin, require_human
from ..util import read_capped
from .engine_api import asset_response, code_response, page_out, widget_rows
from ..versions import injector
from ..versions.injector import VersionError, version_out

router = APIRouter(prefix="/api/builder", tags=["builder"])

ICONS = ("tool", "shield", "pdf", "doc", "sheet", "tower", "ledger", "list", "check", "inbox", "board", "users", "flow", "warehouse", "clock", "grid", "key", "mail", "box", "blocks")


def require_builder(me: User = Depends(current_user)) -> User:
    from ..versions.areas import can_build

    if not can_build(me):
        raise HTTPException(403, "You don't have access to this.")
    return me


EDITOR = ".editor.json"


class DraftTaken(Exception):
    pass


def editor_of(d: Path) -> Optional[dict]:
    """Who is changing this draft: {user_id, name, at}, or None (unclaimed — C-EXA's or older drafts)."""
    try:
        e = json.loads((d / EDITOR).read_text(encoding="utf-8"))
        return e if isinstance(e, dict) and e.get("user_id") else None
    except (OSError, ValueError):
        return None


def claim(d: Path, me: User, *, take_over: bool = False) -> None:
    """Make this draft mine before I change it. Someone else's → DraftTaken (the owner may take it over)."""
    from ..db import utcnow

    e = editor_of(d)
    if e and e.get("user_id") != me.id and not (take_over and me.is_owner):
        raise DraftTaken(f"{e.get('name') or 'Someone'} is changing this tool in their Dev area. Ask them to release it or throw their draft away" + (" — or take it over." if me.is_owner else "."))
    d.mkdir(parents=True, exist_ok=True)
    (d / EDITOR).write_text(json.dumps({"user_id": me.id, "name": me.name, "at": utcnow().isoformat()}), encoding="utf-8")


def _may_change(d: Path, me: User) -> None:
    e = editor_of(d)
    if e and e.get("user_id") != me.id and not me.is_owner:
        raise HTTPException(409, f"{e.get('name') or 'Someone'} is changing this tool — it is their draft.")


def drafts_root() -> Path:
    return settings.data_dir / "builder" / "tools"


def _draft_dir(slug: str) -> Path:
    if not SLUG_RE.match(slug or ""):
        raise HTTPException(400, "A tool's folder name is lowercase letters, digits and dashes, 2 to 40 long.")
    return drafts_root() / slug


def _dump(d: Any) -> str:
    return json.dumps(d, indent=2, ensure_ascii=False) + "\n"


def write_folder(dest: Path, manifest: dict, pages: dict, data: dict, languages: dict, readme: str, flows: Optional[dict] = None) -> None:
    """Lay a tool folder down on disk exactly as the engine reads it. The blocks' files are rewritten
    whole; assets/ (uploaded pictures) and tests/ (hand-written tests) are kept as they are. `flows`
    None leaves flows/ as it is (a caller that does not know about flows)."""
    for sub in ("pages", "data", "languages") + (("flows",) if flows is not None else ()):
        if (dest / sub).exists():
            shutil.rmtree(dest / sub)
    for sub in ("pages", "data", "flows", "tests", "languages", "assets"):
        (dest / sub).mkdir(parents=True, exist_ok=True)
    (dest / "tool.json").write_text(_dump(manifest), encoding="utf-8")
    for name, p in pages.items():
        (dest / "pages" / f"{name}.json").write_text(_dump(p), encoding="utf-8")
    for name, b in data.items():
        (dest / "data" / f"{name}.json").write_text(_dump(b), encoding="utf-8")
    for lang in LANGUAGES:
        (dest / "languages" / f"{lang}.json").write_text(_dump({k: v for k, v in (languages.get(lang) or {}).items() if isinstance(v, str)}), encoding="utf-8")
    for name, f in (flows or {}).items():
        (dest / "flows" / f"{name}.json").write_text(_dump(f), encoding="utf-8")
    (dest / "flows" / ".keep").write_text("", encoding="utf-8")
    (dest / "data" / ".keep").write_text("", encoding="utf-8")
    (dest / "README.md").write_text(readme or "", encoding="utf-8")


def _assets(folder: Path) -> list[dict]:
    d = folder / "assets"
    if not d.is_dir():
        return []
    return [{"name": f.name, "bytes": f.stat().st_size} for f in sorted(d.iterdir()) if f.is_file() and f.suffix.lower() in ASSET_SUFFIXES]


def _job_marker(folder: Path) -> Optional[dict]:
    """A draft C-EXA's team built carries .cexa-job ({job, title, round}); the Builder links back to it."""
    try:
        d = json.loads((folder / ".cexa-job").read_text(encoding="utf-8"))
        return {"job": int(d.get("job")), "title": str(d.get("title") or ""), "round": int(d.get("round") or 1)}
    except (OSError, ValueError, TypeError):
        return None


def folder_out(t: ToolDef, *, source: str) -> dict:
    return {"job": _job_marker(t.path) if source == "draft" else None, "slug": t.slug, "source": source, "manifest": t.manifest, "pages": t.pages, "data": t.data, "flows": t.flows, "languages": {l: t.languages.get(l, {}) for l in LANGUAGES},
            "readme": t.readme, "errors": t.errors, "warnings": t.warnings, "missing": t.missing(), "tokens": t.tokens, "title": t.text(t.manifest.get("title", ""), "en"),
            "assets": _assets(t.path), "layouts": {n: effective_layout(n, p) for n, p in t.pages.items()}}


def _live(slug: str) -> Optional[ToolDef]:
    reg = loader.registry()
    return reg.tools.get(slug) or reg.broken.get(slug)


def _draft(slug: str) -> Optional[ToolDef]:
    d = _draft_dir(slug)
    return loader.read_folder(d) if (d / "tool.json").exists() else None


def _same(a: ToolDef, b: ToolDef) -> bool:
    key = lambda t: json.dumps({"m": t.manifest, "p": t.pages, "d": t.data, "f": t.flows, "l": {l: t.languages.get(l, {}) for l in LANGUAGES}, "r": t.readme}, sort_keys=True)  # noqa: E731
    return key(a) == key(b)


@router.get("")
def overview(db: Session = Depends(get_db), me: User = Depends(require_builder)):
    reg = loader.registry()
    tools = []
    seen = set()
    for t in reg.all():
        seen.add(t.slug)
        try:
            d = _draft(t.slug)
            changed = bool(d and not _same(d, t))
        except Exception as e:  # noqa: BLE001 — 1.0.87: a broken draft shows as broken; the Builder still opens
            d, changed = None, True
            tools.append({**t.out("en"), "live": True, "draft": True, "changed": True, "draft_errors": [f"this draft could not be read: {str(e)[:160]}"], "editor": _editor_out(t.slug, me)})
            continue
        tools.append({**t.out("en"), "live": True, "draft": d is not None, "changed": changed, "draft_errors": d.errors if d else [], "editor": _editor_out(t.slug, me)})
    if drafts_root().is_dir():
        for p in sorted(drafts_root().iterdir()):
            if p.is_dir() and p.name not in seen and (p / "tool.json").exists():
                try:
                    d = loader.read_folder(p)
                    tools.append({**d.out("en"), "live": False, "draft": True, "changed": True, "draft_errors": d.errors, "ok": d.ok, "editor": _editor_out(p.name, me)})
                except Exception as e:  # noqa: BLE001
                    tools.append({"slug": p.name, "name": p.name, "live": False, "draft": True, "changed": True, "ok": False,
                                  "draft_errors": [f"this draft could not be read: {str(e)[:160]}"], "editor": _editor_out(p.name, me)})
    grants = [{"slug": t.slug, "name": t.name} for t in db.query(Tool).filter(Tool.is_active.is_(True)).order_by(Tool.sort).all()]
    conns = [{"slug": c.slug, "name": c.name, "base_url": c.base_url, "description": c.description or ""} for c in db.query(ApiConnection).filter(ApiConnection.is_active.is_(True)).order_by(ApiConnection.name).all()]
    from ..engine import metrics as MX

    return {"tools": tools, "providers": providers.describe_all(), "metrics": [m.describe() for m in MX.METRICS.values()], "icons": list(ICONS), "sections": list(SECTIONS), "languages": list(LANGUAGES),
            "column_types": list(COLUMN_TYPES), "page_types": list(PAGE_TYPES), "widget_types": list(WIDGET_TYPES), "roles": ["owner", "admin", "manager", "user"], "grants": grants,
            "connections": conns, "token_limit": loader.TOKEN_LIMIT, "next_tag": injector.next_tag(db), "sheets": _sheets(db), "flow_ops": list(_flow_ops()), "actions": _actions(db, me),
            "mine": mine_released(db, me), "live_tag": injector.current_tag(), "can_promote": bool(me.is_owner)}


def mine_released(db: Session, me: User, limit: int = 6) -> list[dict]:
    """1.0.82: the versions I made in the Builder, newest first — the Test and Deploy steps of the step bar."""
    from ..models import AppVersion

    out = []
    for v in db.query(AppVersion).filter(AppVersion.uploaded_by_id == me.id).order_by(AppVersion.id.desc()).limit(40).all():
        t = v.tests or {}
        if not isinstance(t.get("builder"), dict):
            continue
        out.append({"id": v.id, "tag": v.tag, "status": v.status, "notes": (v.notes or "")[:200], "tools": t["builder"].get("tools") or [],
                    "ok": bool(t.get("ok")), "skipped": bool(t.get("skipped")), "passed": t.get("passed", 0), "failed": t.get("failed", 0),
                    "failures": list(t.get("failures") or [])[:12], "in_test": bool(t.get("in_test")), "stale": bool(t.get("base")) and t.get("base") != injector.current_tag() and v.status != "live",
                    "created_at": v.created_at.isoformat() if v.created_at else None})
        if len(out) >= limit:
            break
    return out


def _editor_out(slug: str, me: User) -> Optional[dict]:
    e = editor_of(drafts_root() / slug)
    return {"name": e.get("name", ""), "mine": e.get("user_id") == me.id} if e else None


def _actions(db: Session, me: User) -> list[dict]:
    from ..engine import actions

    return actions.catalogue(db, me)


def _sheets(db: Session) -> list[dict]:
    from ..engine import sheets

    return sheets.listing(db)


def _flow_ops() -> tuple:
    from ..engine.flows import OPS

    return OPS


def released_tag(slug: str) -> Optional[str]:
    """The version this draft was last packaged and tested as — None once it changed after that (1.0.82)."""
    d = _draft_dir(slug)
    r, tj = d / ".released", d / "tool.json"
    try:
        if r.exists() and tj.exists() and r.stat().st_mtime_ns >= tj.stat().st_mtime_ns:
            return r.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None
    return None


@router.get("/tools/{slug}")
def one(slug: str, me: User = Depends(require_builder)):
    d = _draft(slug)
    if d is not None:
        live = _live(slug)
        return {**folder_out(d, source="draft"), "live_exists": live is not None, "changed": live is None or not _same(d, live), "editor": _editor_out(slug, me), "released": released_tag(slug)}
    live = _live(slug)
    if live is None:
        raise HTTPException(404, "No tool folder by that name — start a new one.")
    return {**folder_out(live, source="live"), "live_exists": True, "changed": False}


class FolderIn(BaseModel):
    take_over: bool = False  # the owner, taking over someone else's draft on purpose
    manifest: dict
    pages: dict = {}
    data: dict = {}
    flows: Optional[dict] = None
    languages: dict = {}
    readme: str = ""


@router.put("/tools/{slug}")
def save(slug: str, body: FolderIn, request: Request, db: Session = Depends(get_db), me: User = Depends(require_builder), _h: User = Depends(require_human)):
    dest = _draft_dir(slug)
    taken_from = (editor_of(dest) or {}).get("name") if body.take_over else None
    try:
        claim(dest, me, take_over=body.take_over)
    except DraftTaken as e:
        raise HTTPException(409, str(e))
    if taken_from and taken_from != me.name:
        log(db, "builder_draft_taken_over", actor=me, target=slug, detail=f"from {taken_from}")
    body.manifest["slug"] = slug
    pages = {k: v for k, v in body.pages.items() if isinstance(v, dict)}
    data = {k: v for k, v in body.data.items() if isinstance(v, dict)}
    flows = {k: v for k, v in (body.flows or {}).items() if isinstance(v, dict)} if body.flows is not None else None
    for name in list(pages) + list(data) + list(flows or {}):
        if not re.match(r"^[a-z][a-z0-9_]{0,40}$", name):
            raise HTTPException(400, f"'{name}' is not a valid page, data block or flow name (lowercase, digits, underscores).")
    for name, d in data.items():  # 1.0.87: a table with the wrong shape is refused, never saved over the good one
        cols = d.get("columns", [])
        if not isinstance(cols, list) or any(not isinstance(c, dict) or not isinstance(c.get("key"), str) for c in cols):
            raise HTTPException(400, f"Table '{name}': columns must be a list, each column with a key, a label and a type. Nothing was saved.")
    if flows is None:
        # an older Builder page (cached) that knows nothing of flows must not wipe them
        src = _draft(slug) or _live(slug)
        flows = dict(src.flows) if src is not None else {}
    write_folder(dest, body.manifest, pages, data, body.languages or {}, body.readme or "", flows)
    t = loader.read_folder(dest)
    log(db, "builder_saved", actor=me, target=slug, detail=f"{len(pages)} page(s), {len(data)} data block(s), {len(flows)} flow(s); {len(t.errors)} problem(s)", ip=client_ip(request))
    live = _live(slug)
    return {**folder_out(t, source="draft"), "live_exists": live is not None, "changed": live is None or not _same(t, live), "editor": _editor_out(slug, me), "released": None}


@router.get("/released/{vid}/log")
def released_log(vid: int, db: Session = Depends(get_db), me: User = Depends(require_builder)):
    """The test run of a version I made in the Builder (the owner: anyone's) — the 'see the test' of the step bar."""
    from ..models import AppVersion

    v = db.get(AppVersion, vid)
    if v is None or not isinstance((v.tests or {}).get("builder"), dict) or (v.uploaded_by_id != me.id and not me.is_owner):
        raise HTTPException(404, "No such test run of yours.")
    t = v.tests or {}
    return {"id": v.id, "tag": v.tag, "ok": bool(t.get("ok")), "passed": t.get("passed", 0), "failed": t.get("failed", 0), "seconds": t.get("seconds"),
            "failures": list(t.get("failures") or []), "output": str(t.get("output") or "")[-6000:], "council": t.get("council")}


@router.delete("/tools/{slug}/draft")
def discard(slug: str, request: Request, db: Session = Depends(get_db), me: User = Depends(require_builder), _h: User = Depends(require_human)):
    d = _draft_dir(slug)
    _may_change(d, me)
    if d.exists():
        shutil.rmtree(d)
    log(db, "builder_draft_discarded", actor=me, target=slug, ip=client_ip(request))
    return {"ok": True, "live_exists": _live(slug) is not None}


@router.get("/tools/{slug}/preview/{page}")
def preview(slug: str, page: str, request: Request, lang: str = "en", db: Session = Depends(get_db), me: User = Depends(require_builder)):
    """The page as the DRAFT defines it, in the renderer's own shape (`page_out`), with the rows of every
    block that has data. Read-only — a preview never writes."""
    t = _draft(slug) or _live(slug)
    if t is None:
        raise HTTPException(404, "No such tool folder.")
    p = t.pages.get(page)
    if p is None:
        raise HTTPException(404, "That page isn't in the draft.")
    lang = lang if lang in LANGUAGES else "en"
    out = page_out(t, page, p, me, lang, all_pages=True)
    out["errors"] = t.errors
    out["draft"] = True
    rows: dict[str, Any] = {}
    from ..engine.loader import widgets_of

    for w in widgets_of(page, p):
        src = w.get("source") or {}
        if not src:
            continue
        if not me.is_admin:  # 1.0.80: a builder who is not an admin tries real rows in their Dev area, never production's
            rows[str(w.get("id"))] = {"columns": [], "rows": [], "total": 0, "note": "Rows show in your Dev area (Work areas)."}
            continue
        try:
            rows[str(w.get("id"))] = widget_rows(db, me, t, w, src, {}, limit=50)
        except HTTPException as e:
            rows[str(w.get("id"))] = {"columns": [], "rows": [], "total": 0, "note": str(e.detail)}
    out["rows"] = rows
    out["data"] = next(iter(rows.values()), None)  # 1.0.43 shape
    return out


class WidgetPreviewIn(BaseModel):
    page: str = ""
    widget: dict
    data: dict = {}
    params: dict = {}


@router.post("/tools/{slug}/preview-widget")
def preview_widget(slug: str, body: WidgetPreviewIn, db: Session = Depends(get_db), me: User = Depends(require_builder)):
    """Rows for ONE block as it stands on the canvas — before it is saved. The tool's data blocks may be
    passed along, so a table over a block that is not saved yet still answers (empty, with its columns)."""
    t = _draft(slug) or _live(slug)
    if t is None:
        t = ToolDef(slug=slug, path=_draft_dir(slug), manifest={"slug": slug})
    t2 = ToolDef(slug=t.slug, path=t.path, manifest=t.manifest, pages=t.pages, data={**t.data, **{k: v for k, v in body.data.items() if isinstance(v, dict)}}, languages=t.languages)
    src = body.widget.get("source") or {}
    if not src:
        return {"columns": [], "rows": [], "total": 0, "note": "no source wired"}
    if not me.is_admin:
        return {"columns": [], "rows": [], "total": 0, "note": "Rows show in your Dev area (Work areas)."}
    if src.get("data") and src["data"] in t2.data and not Store(slug).exists(src["data"]):
        return {"columns": t2.data[src["data"]].get("columns") or [], "rows": [], "total": 0, "note": "no rows yet"}
    try:
        return widget_rows(db, me, t2, body.widget, src, body.params or {}, limit=50)
    except HTTPException as e:
        return {"columns": [], "rows": [], "total": 0, "note": str(e.detail)}


@router.get("/tools/{slug}/html/{page}/{widget}")
def draft_code(slug: str, page: str, widget: str, me: User = Depends(require_builder)):
    t = _draft(slug) or _live(slug)
    if t is None or page not in t.pages:
        raise HTTPException(404, "No such page.")
    w = find_widget(page, t.pages[page], widget)
    if w is None or w.get("type") != "code":
        raise HTTPException(404, "That code block isn't on this page.")
    return code_response(w)


class CodeIn(BaseModel):
    code: str


MAX_ASSET = 5 * 1024 * 1024


@router.get("/tools/{slug}/assets")
def list_assets(slug: str, me: User = Depends(require_builder)):
    t = _draft(slug) or _live(slug)
    return {"assets": _assets(t.path) if t else _assets(_draft_dir(slug))}


@router.get("/tools/{slug}/assets/{name}")
def draft_asset(slug: str, name: str, me: User = Depends(require_builder)):
    d = _draft_dir(slug)
    if (d / "assets" / name).is_file():
        return asset_response(d, name)
    live = _live(slug)
    if live is None:
        raise HTTPException(404, "No such file.")
    return asset_response(live.path, name)


class TranslateIn(BaseModel):
    lang: str
    words: dict


LANG_NAMES = {"ar": "Arabic", "pt": "Portuguese (Portugal)", "fr": "French"}


@router.post("/translate")
def translate(body: TranslateIn, request: Request, db: Session = Depends(get_db), me: User = Depends(require_builder), _h: User = Depends(require_human)):
    """The Words panel's "Let C-EXA translate the missing words" (1.0.56): the words go to the local model,
    60 at a time; what comes back fills the empty boxes, and the person reads it before saving."""
    from ..branding import ai_name
    from ..cexa.gateway import Gateway, ModelError

    lname = LANG_NAMES.get(body.lang)
    if not lname:
        raise HTTPException(400, "Arabic, Portuguese or French.")
    words = {str(k): str(v) for k, v in (body.words or {}).items() if isinstance(v, str) and v.strip()}
    if not words:
        return {"words": {}}
    if len(words) > 600:
        raise HTTPException(400, "Too many words at once — translate a page at a time.")
    got: dict = {}
    items = list(words.items())
    try:
        for j in range(0, len(items), 60):
            chunk = dict(items[j:j + 60])
            out = Gateway(db).json(task="team_words", system=f"Translate every value into {lname} for business users. Keep the keys exactly; keep {{{{templates}}}}, {{Words in braces}} and ** as they are. Short, plain words.",
                                   user=json.dumps({"language": body.lang, "words": chunk}, ensure_ascii=False), schema={"words": {}})
            w = out.get("words") if isinstance(out.get("words"), dict) else {}
            got.update({k: v.strip()[:2000] for k, v in w.items() if k in chunk and isinstance(v, str) and v.strip()})
    except ModelError as e:
        raise HTTPException(503, f"{ai_name()} could not translate just now ({str(e)[:120]}). Is the model running? The words you typed are kept.")
    log(db, "builder_translated", actor=me, target=lname, detail=f"{len(got)} of {len(words)} words", ip=client_ip(request))
    return {"words": got, "asked": len(words)}


@router.post("/tools/{slug}/assets")
async def upload_asset(slug: str, request: Request, files: list[UploadFile] = File(...), db: Session = Depends(get_db), me: User = Depends(require_builder), _h: User = Depends(require_human)):
    """A picture for an Image block, into the draft's assets/. PNG, JPG, GIF, WEBP, SVG or PDF, 5 MB at most."""
    d = _draft_dir(slug)
    try:
        claim(d, me)
    except DraftTaken as e:
        raise HTTPException(409, str(e))
    (d / "assets").mkdir(parents=True, exist_ok=True)
    saved = []
    for f in files[:10]:
        name = re.sub(r"[^A-Za-z0-9._-]", "_", (f.filename or "file")).strip("._") or "file"
        if not name.lower().endswith(ASSET_SUFFIXES):
            raise HTTPException(400, f"'{f.filename}': only {', '.join(x.lstrip('.') for x in ASSET_SUFFIXES)} files can be used on a page.")
        data = await read_capped(f, MAX_ASSET, "image")
        if name.lower().endswith(".docx") and not data.startswith(b"PK\x03\x04"):
            raise HTTPException(400, f"'{f.filename}' is not really a Word document.")
        if name.lower().endswith(".svg") and (b"<script" in data.lower() or b"javascript:" in data.lower()):
            raise HTTPException(400, f"'{f.filename}': an SVG with a script in it is not accepted.")
        (d / "assets" / name).write_bytes(data)
        saved.append({"name": name, "bytes": len(data)})
    log(db, "builder_asset_uploaded", actor=me, target=slug, detail=", ".join(a["name"] for a in saved), ip=client_ip(request))
    return {"saved": saved, "assets": _assets(d)}


@router.delete("/tools/{slug}/assets/{name}")
def delete_asset(slug: str, name: str, request: Request, db: Session = Depends(get_db), me: User = Depends(require_builder), _h: User = Depends(require_human)):
    _may_change(_draft_dir(slug), me)
    f = _draft_dir(slug) / "assets" / name
    if "/" in name or ".." in name or not f.is_file():
        raise HTTPException(404, "No such file in the draft.")
    f.unlink()
    log(db, "builder_asset_removed", actor=me, target=slug, detail=name, ip=client_ip(request))
    return {"ok": True}


TEST_TEMPLATE = '''"""{title}: generated by the Builder — the folder is sound and its pages answer for an owner."""
from cbx.engine import loader


def test_{fn}_folder_is_sound():
    t = loader.registry().tools.get("{slug}")
    assert t is not None and t.ok, (t.errors if t else "not loaded")
    for lang in ("en", "ar", "pt", "fr"):
        assert lang in t.languages, lang


def test_{fn}_pages_answer(owner):
    for name in {pages!r}:
        r = owner.get(f"/api/engine/tools/{slug}/pages/{{name}}")
        assert r.status_code == 200, r.text


def test_{fn}_flows_run_dry(owner, db):
    """Every flow runs to its end (or its first approval) without writing, sending or calling anything."""
    from cbx.engine import flows as FL
    from cbx.models import User

    t = loader.registry().tools.get("{slug}")
    me = db.query(User).filter(User.email == "owner@test.local").one()
    for name, f in t.flows.items():
        run = FL.start(db, t, name, trigger=(f.get("trigger") or {{}}).get("type") or "button", starter=me, dry=True, flow=f)
        assert run.status in ("done", "waiting"), (name, run.note, run.log)
'''


class ReleaseIn(BaseModel):
    slugs: list[str] = []
    notes: str = ""


@router.post("/release")
def release(body: ReleaseIn, request: Request, db: Session = Depends(get_db), me: User = Depends(require_builder), _h: User = Depends(require_human)):
    """Copy the live app, lay the drafts into its tools/, give it the next version number and stage it:
    from here it is an ordinary version — tested in the sandbox, promoted by the owner with the notice."""
    from ..cexa import developer

    mine = lambda p: (editor_of(p) or {}).get("user_id") in ((me.id, None) if me.is_owner else (me.id,))  # noqa: E731 — unclaimed drafts are the owner's
    slugs = body.slugs or ([p.name for p in drafts_root().iterdir() if p.is_dir() and (p / "tool.json").exists() and mine(p)] if drafts_root().is_dir() else [])
    if not slugs:
        raise HTTPException(400, "There is no draft of yours to release. Save a tool first.")
    for sl in slugs:
        e = editor_of(_draft_dir(sl))
        if e and e.get("user_id") != me.id and not me.is_owner:
            raise HTTPException(409, f"'{sl}' is {e.get('name') or 'someone else'}'s draft — they release it.")
    drafts = []
    for s in slugs:
        d = _draft(s)
        if d is None:
            raise HTTPException(400, f"'{s}' has no draft.")
        if d.errors:
            raise HTTPException(400, f"'{s}' still has problems: " + "; ".join(d.errors[:5]))
        drafts.append(d)
    if injector.restart_pending():
        raise HTTPException(400, "The server is about to restart. Try again in a minute.")
    tag = injector.next_tag(db)
    work = Path(tempfile.mkdtemp(prefix="cbx-builder-"))
    try:
        src = developer.copy_live_tree(work / "app")
        for d in drafts:
            write_folder(src / "tools" / d.slug, d.manifest, d.pages, d.data, d.languages, d.readme, d.flows)
            if (d.path / "assets").is_dir():
                shutil.copytree(d.path / "assets", src / "tools" / d.slug / "assets", dirs_exist_ok=True)
            tests_dir = src / "tools" / d.slug / "tests"
            fn = re.sub(r"[^a-z0-9]", "_", d.slug)
            test_file = tests_dir / f"test_{fn}.py"
            live_test = ROOT / "tools" / d.slug / "tests" / f"test_{fn}.py"
            if live_test.exists():
                shutil.copy2(live_test, test_file)  # a hand-written test outlives the Builder's template
            elif not test_file.exists():
                test_file.write_text(TEST_TEMPLATE.format(title=d.text(d.manifest.get("title", ""), "en"), fn=fn, slug=d.slug, pages=sorted(d.pages)), encoding="utf-8")
        if not (src / "tools" / "conftest.py").exists() and (ROOT / "tools" / "conftest.py").exists():
            shutil.copy2(ROOT / "tools" / "conftest.py", src / "tools" / "conftest.py")
        notes = body.notes.strip() or ("Builder: " + ", ".join(d.text(d.manifest.get("title", ""), "en") for d in drafts))
        try:
            v = injector.package_from_dir(db, src, tag=tag, notes=notes, by=me, run=True)
        except VersionError as e:
            raise HTTPException(400, str(e))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    # what it was made from: promote refuses it once another version has gone live since (it would undo that one)
    v.tests = {**(v.tests or {}), "base": injector.current_tag(), "builder": {"by": me.id, "tools": [d.slug for d in drafts]}}
    for d in drafts:
        (_draft_dir(d.slug) / ".released").write_text(tag, encoding="utf-8")
    log(db, "builder_released", actor=me, target=tag, detail=f"tools: {', '.join(d.slug for d in drafts)}; tests {'passed' if (v.tests or {}).get('ok') else 'failed'}", ip=client_ip(request))
    return {"version": version_out(v, db), "tag": tag, "tools": [d.slug for d in drafts]}


# ---- testing a flow (dry run) ---------------------------------------------------------------------


class FlowTestIn(BaseModel):
    flow: dict
    data: dict = {}
    manifest: dict = {}
    sample: dict = {}  # a trigger row to test with (row_added / row_changed), or button values


@router.post("/tools/{slug}/flows/{name}/test")
def test_flow(slug: str, name: str, body: FlowTestIn, db: Session = Depends(get_db), me: User = Depends(require_admin), _h: User = Depends(require_human)):
    """Run the flow as it stands on the canvas (unsaved) with live data, writing, sending and calling
    nothing: every step says what it did or would have done. The run is not kept."""
    from ..engine import flows as FL

    base = _draft(slug) or _live(slug) or ToolDef(slug=slug, path=_draft_dir(slug), manifest={})
    t = ToolDef(slug=slug, path=base.path, manifest={**base.manifest, **(body.manifest or {})}, pages=base.pages,
                data={**base.data, **{k: v for k, v in (body.data or {}).items() if isinstance(v, dict)}},
                flows={**base.flows, name: body.flow}, languages=base.languages)
    problems, _ = FL.check(t, name, body.flow)
    if problems:
        return {"ok": False, "problems": problems, "log": [], "status": "not run"}
    kind = (body.flow.get("trigger") or {}).get("type") or "button"
    sample = body.sample or {}
    data = {"row": sample, "table": (body.flow.get("trigger") or {}).get("data")} if kind in ("row_added", "row_changed") else {"values": sample}
    try:
        run = FL.start(db, t, name, trigger=kind, starter=me, data=data, dry=True, flow=body.flow)
    except FL.FlowError as e:
        return {"ok": False, "problems": [str(e)], "log": [], "status": "not run"}
    out = {"ok": run.status in ("done", "waiting"), "status": run.status, "note": run.note, "log": run.log or [], "steps": run.steps_taken,
           "values": {k: v for k, v in (run.ctx or {}).items() if k not in ("trigger",)}}
    db.delete(run)
    db.flush()
    return out
