"""Reads tool folders, checks them, and keeps the result in memory.

    registry()          the loaded tools (loaded on first use, or by reload())
    reload(root=None)   read the folders again (the Builder calls this after a release lands)
    validate(folder)    the list of problems in one folder — empty when it is sound
    folder_tokens(dir)  the rough size of a folder in model tokens (bytes / 4 of its text files)

A folder with a problem is listed with its errors and left out of the menu; the others still load.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..config import ROOT

TOOLS_ROOT: Path = ROOT / "tools"
LANGUAGES = ("en", "ar", "pt", "fr")
RTL = {"ar"}
PAGE_TYPES = ("table", "grid", "form")
# what a page is made of: blocks pulled onto the canvas and wired to a page
WIDGET_TYPES = ("heading", "text", "metrics", "button", "stats", "table", "chart", "board", "progress", "grid", "form", "image", "embed", "code", "links", "divider", "spacer")
DATA_WIDGETS = ("stats", "table", "chart", "board", "progress", "grid", "form")
DATA_KINDS = ("table",)
ASSET_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".pdf", ".docx")  # .docx: a Word template for "Make a Word document" (1.0.56)
CODE_MAX = 60_000
# 1.0.99: a links block's item may be a download CBX itself serves (a Core route under /api/, no query, no "..")
DOWNLOAD_RE = re.compile(r"^(?!.*\.\.)/api/[a-z0-9_-]+(?:/[a-z0-9_.-]+)*$")
COLUMN_TYPES = ("text", "int", "number", "date", "bool", "json", "choice", "link", "file", "person", "checklist", "calc")
# a calculated column (1.0.54): how many checklist items, how many done, days between two dates, or a grade
CALC_FNS = ("count", "done", "days", "grade", "expiry")
# a link shows one column of the row it points at; these can be shown
SHOWABLE = ("text", "int", "number", "date", "choice")
# what a file column accepts (1.0.51): documents and pictures, never anything a browser would run
FILE_TYPES = ("pdf", "png", "jpg", "jpeg", "gif", "webp", "txt", "csv", "docx", "xlsx", "pptx", "doc", "xls", "msg", "eml", "zip")
FILE_MAX_MB = 100  # 1.0.57: IFRS 16's source workbooks run large (the old page took 100 MB)
ROLES = ("owner", "admin", "manager", "user")
SECTIONS = ("My tools", "Workspace", "Admin")
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
KEY_RE = re.compile(r"^[a-z][a-z0-9_.]{0,80}$")
TOKEN_LIMIT = 40_000  # per tool folder; a warning for now, a hard limit later (see tests/test_engine_tools.py)
TEXT_SUFFIXES = {".json", ".md", ".py", ".txt", ".yaml", ".yml", ".csv"}


def opt_slug(value) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")[:40] or "x"


def link_show(target: dict) -> str:
    """Which column of the target table a link shows when its column does not say: the first text or
    choice column, else the row's number."""
    for c in (target or {}).get("columns") or []:
        if isinstance(c, dict) and c.get("type", "text") in ("text", "choice"):
            return c.get("key") or "id"
    return "id"


def page_open(p: dict, role: str) -> bool:
    """A page with access.roles is only for those roles; one without is for everyone who may use the tool."""
    roles = (p.get("access") or {}).get("roles") if isinstance(p.get("access"), dict) else None
    return not roles or role in roles


def pages_using(t: "ToolDef", block: str) -> list[str]:
    """The pages that show or fill one of the tool's own tables."""
    out = []
    for name, p in t.pages.items():
        for w in widgets_of(name, p):
            if ((w or {}).get("source") or {}).get("data") == block:
                out.append(name)
                break
    return out


def block_open(t: "ToolDef", block: str, role: str) -> bool:
    """A table may be read and written by someone who may open at least one page that shows it. A table
    on no page (a flow's own) is for everyone who may use the tool, as before."""
    names = pages_using(t, block)
    return not names or any(page_open(t.pages[n], role) for n in names)


def flow_open(t: "ToolDef", flow: str, role: str) -> bool:
    """A flow's Run button counts only on pages the person may open."""
    names = [n for n, p in t.pages.items() if any((w or {}).get("type") == "button" and str((w or {}).get("flow")) == flow for w in widgets_of(n, p))]
    return not names or any(page_open(t.pages[n], role) for n in names)


@dataclass
class ToolDef:
    slug: str
    path: Path
    manifest: dict
    pages: dict = field(default_factory=dict)
    data: dict = field(default_factory=dict)
    flows: dict = field(default_factory=dict)
    languages: dict = field(default_factory=dict)
    readme: str = ""
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    tokens: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors

    def text(self, key: str, lang: str = "en") -> str:
        """A string by key in `lang`, falling back to English, then to the key itself."""
        for l in (lang, "en"):
            v = (self.languages.get(l) or {}).get(key)
            if isinstance(v, str) and v:
                return v
        return key

    def strings(self, lang: str) -> dict:
        out = dict(self.languages.get("en") or {})
        out.update({k: v for k, v in (self.languages.get(lang) or {}).items() if isinstance(v, str) and v})
        return out

    def missing(self) -> dict:
        """Per language: the English keys it has no word for."""
        en = set((self.languages.get("en") or {}).keys())
        return {l: sorted(en - {k for k, v in (self.languages.get(l) or {}).items() if v}) for l in LANGUAGES if l != "en"}

    def out(self, lang: str = "en", role: Optional[str] = None) -> dict:
        """The tool as the menu and the renderer see it. With `role`, only the pages that role may open
        (and the home page is the first of those)."""
        m = self.manifest
        home = m.get("home") or next(iter(self.pages), "")
        # the home page first, then the others by their own order, then by name
        names = sorted(self.pages, key=lambda n: (n != home, (self.pages[n].get("order") or 100), n))
        if role is not None:
            names = [n for n in names if page_open(self.pages[n], role)]
            if home not in names:
                home = names[0] if names else ""
        return {"slug": self.slug, "title": self.text(m.get("title", "tool.title"), lang), "icon": m.get("icon") or "tool", "badge": m.get("badge") or "", "theme": m.get("theme") or {},
                "section": (m.get("menu") or {}).get("section", "My tools"), "order": (m.get("menu") or {}).get("order", 100),
                "hidden": bool((m.get("menu") or {}).get("hidden")), "home": home,
                "flows": [{"name": n, "title": self.text(f.get("title") or n, lang), "trigger": (f.get("trigger") or {}).get("type"), "enabled": f.get("enabled", True)} for n, f in sorted(self.flows.items())], "pages": [{"name": n, "type": self.pages[n].get("type"), "title": self.text(self.pages[n].get("title", f"page.{n}.title"), lang), "menu": bool(self.pages[n].get("menu", False)), "roles": list(((self.pages[n].get("access") or {}).get("roles")) or [])} for n in names],
                "ok": self.ok, "errors": self.errors, "warnings": self.warnings, "tokens": self.tokens}


@dataclass
class Registry:
    root: Path
    tools: dict = field(default_factory=dict)  # slug → ToolDef (sound ones only)
    broken: dict = field(default_factory=dict)  # slug → ToolDef with errors

    def all(self) -> list[ToolDef]:
        return sorted(list(self.tools.values()) + list(self.broken.values()), key=lambda t: ((t.manifest.get("menu") or {}).get("order", 100), t.slug))


_REG: Optional[Registry] = None
_LOCK = threading.Lock()


def _read_json(p: Path, errors: list, what: str) -> Optional[dict]:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append(f"{what}: missing ({p.name})")
        return None
    except (OSError, ValueError) as e:
        errors.append(f"{what}: not valid JSON ({e})")
        return None
    if not isinstance(d, dict):
        errors.append(f"{what}: must be an object")
        return None
    return d


def folder_tokens(folder: Path) -> int:
    n = 0
    try:
        for f in folder.rglob("*"):
            if f.is_file() and f.suffix.lower() in TEXT_SUFFIXES and "__pycache__" not in f.parts:
                try:
                    n += f.stat().st_size
                except OSError:
                    pass
    except OSError:  # a draft being rewritten while it is read (C-EXA's team writes drafts in the background)
        pass
    return n // 4


def read_folder(folder: Path) -> ToolDef:
    """Read one folder into a ToolDef, collecting every problem instead of stopping at the first."""
    errors: list[str] = []
    slug = folder.name
    manifest = _read_json(folder / "tool.json", errors, "tool.json") or {}
    t = ToolDef(slug=slug, path=folder, manifest=manifest)
    t.errors = errors
    for p in sorted((folder / "pages").glob("*.json")) if (folder / "pages").is_dir() else []:
        d = _read_json(p, errors, f"pages/{p.name}")
        if d is not None:
            t.pages[p.stem] = d
    for p in sorted((folder / "data").glob("*.json")) if (folder / "data").is_dir() else []:
        d = _read_json(p, errors, f"data/{p.name}")
        if d is not None:
            t.data[p.stem] = d
    for p in sorted((folder / "flows").glob("*.json")) if (folder / "flows").is_dir() else []:
        d = _read_json(p, errors, f"flows/{p.name}")
        if d is not None:
            t.flows[p.stem] = d
    for lang in LANGUAGES:
        p = folder / "languages" / f"{lang}.json"
        if p.exists():
            d = _read_json(p, errors, f"languages/{lang}.json")
            if d is not None:
                t.languages[lang] = {k: v for k, v in d.items() if isinstance(v, str)}
    rd = folder / "README.md"
    t.readme = rd.read_text(encoding="utf-8", errors="replace") if rd.exists() else ""
    t.tokens = folder_tokens(folder)
    try:
        t.errors = validate(t)
    except Exception as e:  # noqa: BLE001 — 1.0.87: one malformed part must never take the Builder down
        t.errors = list(t.errors) + [f"this folder could not be checked ({type(e).__name__}: {str(e)[:160]}) — a part has the wrong shape"]
    if t.tokens > TOKEN_LIMIT:
        t.warnings.append(f"folder is about {t.tokens:,} tokens; the limit is {TOKEN_LIMIT:,}")
    from . import rules as RULES

    for fname, f in t.flows.items():
        why = RULES.why_not(f)
        if why:
            t.warnings.append(f"flows/{fname}: the Builder opens this rule read-only — {why}")
    for lang, keys in t.missing().items():
        if keys:
            t.warnings.append(f"{lang}: {len(keys)} word(s) not translated yet (English is shown)")
    return t


def effective_layout(name: str, p: dict) -> dict:
    """A page as sections → columns → widgets. A page written the 1.0.43 way (one `type`, one `source`)
    is one full-width widget; the renderer and the Builder only ever see this shape."""
    lay = p.get("layout")
    if isinstance(lay, dict) and isinstance(lay.get("sections"), list):
        return lay
    if p.get("type") in PAGE_TYPES:
        w = {k: v for k, v in p.items() if k not in ("title", "intro", "menu", "order", "canvas", "style", "layout")}
        w["id"] = "main"
        return {"sections": [{"id": "s1", "columns": [{"width": 12, "widgets": [w]}]}]}
    return {"sections": []}


def widgets_of(name: str, p: dict) -> list[dict]:
    out = []
    for sec in effective_layout(name, p).get("sections") or []:
        for col in sec.get("columns") or []:
            for w in col.get("widgets") or []:
                if isinstance(w, dict):
                    out.append(w)
    return out


def widget(name: str, p: dict, wid: str) -> Optional[dict]:
    for w in widgets_of(name, p):
        if str(w.get("id")) == wid:
            return w
    return None


def _check_source(t: ToolDef, where: str, typ: str, src: dict, errs: list) -> None:
    from . import providers

    if src.get("provider"):
        if src["provider"] not in providers.PROVIDERS:
            errs.append(f"{where}: provider '{src['provider']}' does not exist (known: {', '.join(sorted(providers.PROVIDERS))})")
        elif typ in ("table", "grid", "chart") and providers.PROVIDERS[src["provider"]].kind != ("table" if typ == "chart" else typ):
            errs.append(f"{where}: provider '{src['provider']}' gives a {providers.PROVIDERS[src['provider']].kind}, not a {typ}")
        elif typ == "stats" and providers.PROVIDERS[src["provider"]].kind != "grid":
            errs.append(f"{where}: stats tiles read a grid provider's summary; '{src['provider']}' is a table")
        if typ == "form":
            errs.append(f"{where}: a form writes to one of the tool's own data blocks, not a provider")
    elif src.get("data"):
        if src["data"] not in t.data:
            errs.append(f"{where}: data block '{src['data']}' does not exist in data/")
        elif typ == "stats":
            errs.append(f"{where}: totals tiles read a grid — source: {{\"grid\": \"<the grid's id>\"}} — or a grid provider")
    elif src.get("grid"):
        if typ != "stats":
            errs.append(f"{where}: only totals tiles read another block's grid")
    elif src.get("sheet"):
        if not isinstance(src["sheet"], str) or not re.match(r"^[a-z0-9][a-z0-9-]{0,159}$", src["sheet"]):
            errs.append(f"{where}: sheet needs a master sheet of the warehouse (its short name)")
        if typ == "form":
            errs.append(f"{where}: a form writes to a table of the tool's own; a warehouse sheet is only read")
        elif typ in ("grid", "stats"):
            errs.append(f"{where}: a {typ} needs Core data; a warehouse sheet can feed a table or a chart")
    elif src.get("api"):
        if typ not in ("table", "stats", "chart"):
            errs.append(f"{where}: an API connection can feed a table or stats, not a {typ}")
        if not isinstance(src["api"], str) or not src["api"]:
            errs.append(f"{where}: api needs the connection's name (Admin → API connections)")
        if not src.get("rows"):
            errs.append(f"{where}: api needs 'rows' — where in the answer the list is (e.g. data.items, or . for the whole answer)")
        cols = src.get("columns") or []
        if not isinstance(cols, list) or not cols:
            errs.append(f"{where}: api needs 'columns' — which fields of each row to show")
    else:
        errs.append(f"{where}: source needs a provider, a data block, a warehouse sheet or an API connection")


def _check_rows_rules(where: str, typ: str, w: dict, errs: list) -> None:
    """What a table or chart does with its rows (1.0.53): only the rows where …, sorted by, totals, groups."""
    from .flows import OPS

    cond = w.get("where")
    if cond is not None:
        if not isinstance(cond, list):
            errs.append(f"{where}: 'where' is a list of conditions")
        else:
            for c in cond:
                if not isinstance(c, dict) or not c.get("field") or c.get("op") not in OPS:
                    errs.append(f"{where}: every condition needs a column and one of: {', '.join(OPS)}")
                    break
    if w.get("show") not in (None, "rows", "totals"):
        errs.append(f"{where}: show is 'rows' or 'totals'")
    if w.get("sort") is not None and not (isinstance(w["sort"], dict) and isinstance(w["sort"].get("key"), str)):
        errs.append(f"{where}: sort is {{\"key\": <column>, \"desc\": true|false}}")
    acts = w.get("actions")
    if acts is not None and (not isinstance(acts, list) or not all(isinstance(a, dict) and (a.get("flow") or a.get("kind") == "mail") and a.get("label") for a in acts)):
        errs.append(f"{where}: each button on a row needs its words (label) and the rule it starts (flow) — or is a Mail button (kind: mail)")
    for a in acts or []:
        if isinstance(a, dict) and a.get("kind") == "mail":
            tos = a.get("to") if isinstance(a.get("to"), list) else [a.get("to")] if a.get("to") else []
            if not all(isinstance(x, str) and re.match(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)?$", x) for x in tos) or not isinstance(a.get("fields", []), list):
                errs.append(f"{where}: a Mail button's 'to' is a column (or link.column) that holds the person or address, and 'fields' a list of columns")
    if typ == "progress":
        cnt = w.get("count") or {}
        if not (w.get("source") or {}).get("data") or not cnt.get("data"):
            errs.append(f"{where}: progress shows the rows of one of the tool's tables (e.g. goals) against a count of rows of another")
        if not w.get("target"):
            errs.append(f"{where}: progress needs the column that holds each row's target")
        for c in cnt.get("where") or []:
            if not isinstance(c, dict) or not c.get("field") or c.get("op") not in OPS:
                errs.append(f"{where}: every condition of the count needs a column and a comparison")
                break
    pre = w.get("preset")
    if pre is not None and (not isinstance(pre, dict) or not all(isinstance(k, str) and isinstance(v, (str, int, float)) for k, v in pre.items())):
        errs.append(f"{where}: 'new rows start with' is a list of column: value")
    if typ == "board" and not (w.get("source") or {}).get("data"):
        errs.append(f"{where}: a board shows one of the tool's own tables")
    if typ == "board" and not w.get("lane"):
        errs.append(f"{where}: a board needs the pick-list column its columns come from (lane)")
    if typ == "chart":
        if not isinstance(w.get("group"), str) or not w.get("group"):
            errs.append(f"{where}: a chart needs the column it groups by (group)")
        v = w.get("value") or {"fn": "count"}
        if not isinstance(v, dict) or v.get("fn") not in ("count", "sum") or (v.get("fn") == "sum" and not v.get("of")):
            errs.append(f"{where}: a chart counts rows, or adds up a number column (value: {{\"fn\": \"sum\", \"of\": <column>}})")


def shape_errors(t: ToolDef) -> list[str]:
    """1.0.87: the shape every part must have before anything looks inside it. A table whose columns came as
    {"province": {...}} instead of a list once crashed the check and with it the whole Builder."""
    errs: list[str] = []
    for name, d in t.data.items():
        if not isinstance(d, dict):
            errs.append(f"data/{name}: must be an object")
            continue
        cols = d.get("columns", [])
        if not isinstance(cols, list):
            errs.append(f"data/{name}: columns must be a list of columns, each with a key, a label and a type — this one is a {type(cols).__name__}")
        elif any(not isinstance(c, dict) or not isinstance(c.get("key"), str) for c in cols):
            errs.append(f"data/{name}: every column needs a key, a label and a type")
    for name, pg in t.pages.items():
        if not isinstance(pg, dict):
            errs.append(f"pages/{name}: must be an object")
            continue
        lay = pg.get("layout")
        if lay is not None and (not isinstance(lay, dict) or not isinstance(lay.get("sections", []), list)):
            errs.append(f"pages/{name}: layout must have a list of sections")
    for name, f in t.flows.items():
        if not isinstance(f, dict) or not isinstance(f.get("steps", {}), dict) or any(not isinstance(x, dict) for x in (f.get("steps") or {}).values()):
            errs.append(f"flows/{name}: steps must be named objects")
    return errs


def validate(t: ToolDef) -> list[str]:
    """Everything a sound folder must have. Plain checks, plain sentences — the Builder shows them."""
    errs = list(t.errors)
    shape = shape_errors(t)
    if shape:
        return errs + shape  # the deeper checks assume the shapes
    m = t.manifest
    if not SLUG_RE.match(t.slug):
        errs.append(f"folder name '{t.slug}': lowercase letters, digits and dashes only")
    if not m:
        return errs
    if m.get("slug") and m["slug"] != t.slug:
        errs.append(f"tool.json says slug '{m['slug']}' but the folder is '{t.slug}'")
    if not isinstance(m.get("title"), str) or not m.get("title"):
        errs.append("tool.json: 'title' (a language key) is required")
    menu = m.get("menu") or {}
    if menu.get("section", "My tools") not in SECTIONS:
        errs.append(f"tool.json: menu.section must be one of {', '.join(SECTIONS)}")
    access = m.get("access") or {}
    roles = access.get("roles") or []
    if not isinstance(roles, list) or any(r not in ("owner", "admin", "manager", "user") for r in roles):
        errs.append("tool.json: access.roles must be a list of owner / admin / manager / user")
    if not roles and not access.get("tool") and not access.get("everyone"):
        errs.append("tool.json: access needs roles, a tool grant (access.tool), or both — or everyone: true")
    if menu.get("section") == "Admin" and set(roles) - {"owner", "admin"}:
        errs.append("tool.json: a tool in the Admin section can only be for owner and admin")
    if not t.pages and not t.flows:
        errs.append("pages/: at least one page (or a flow, for a tool that only works in the background)")
    if m.get("home") and m["home"] not in t.pages:
        errs.append(f"tool.json: home page '{m['home']}' does not exist in pages/")
    if not (t.languages.get("en") or {}):
        errs.append("languages/en.json: required, and must have the words the blocks use")
    en = t.languages.get("en") or {}
    used = {m.get("title", "")}
    for name, d in t.data.items():
        if not NAME_RE.match(name):
            errs.append(f"data/{name}: lowercase letters, digits and underscores only")
        if d.get("kind", "table") not in DATA_KINDS:
            errs.append(f"data/{name}: kind must be one of {', '.join(DATA_KINDS)}")
        if d.get("write", "everyone") not in ("everyone", "managers", "admins", "nobody"):
            errs.append(f"data/{name}: who may add rows is everyone, managers, admins or nobody")
        if d.get("scope", "all") not in ("all", "mine", "team", "members", "linked"):
            errs.append(f"data/{name}: who sees the rows is all, mine (each person their own), team (each team its own), members or linked")
        own_cols = {c.get("key"): c for c in d.get("columns") or [] if isinstance(c, dict)}
        if d.get("scope") == "linked":
            # 1.0.63: each row is seen by whoever may see the row it links to (a company's documents)
            lk = own_cols.get(d.get("link") or "")
            if not lk or lk.get("type") != "link" or lk.get("table") not in t.data or lk.get("table") == name or (t.data.get(lk.get("table")) or {}).get("scope") == "linked":
                errs.append(f"data/{name}: 'whoever may see the row it links to' needs one of this table's link columns, to a table with its own rule")
        op = d.get("open")
        if op is not None:
            # 1.0.63: everyone with the tool sees the rows where a column has a value (a company "visible to everyone")
            oc = own_cols.get((op or {}).get("field") if isinstance(op, dict) else "")
            if d.get("scope") not in ("mine", "team", "members") or not oc or oc.get("type") not in ("choice", "bool") or op.get("value") in (None, ""):
                errs.append(f"data/{name}: 'everyone sees the rows where …' goes with mine, team or members, and needs a pick-list or yes/no column and its value")
            elif oc.get("type") == "choice" and op["value"] not in (oc.get("options") or []):
                errs.append(f"data/{name}: everyone sees the rows where {op['field']} is '{op['value']}', which is not one of its options")
        if d.get("edit") not in (None, "own", "seen"):
            errs.append(f"data/{name}: who may change a row is own (their own rows; admins any) or seen (anyone who may see it)")
        if d.get("scope") == "members":
            mm = d.get("members") or {}
            mt = t.data.get(mm.get("table")) or {}
            mcols = {c.get("key"): c for c in mt.get("columns") or [] if isinstance(c, dict)}
            own = {c.get("key"): c for c in d.get("columns") or [] if isinstance(c, dict)}
            if not mt or (mcols.get(mm.get("person")) or {}).get("type") != "person" or (mcols.get(mm.get("link")) or {}).get("type") != "link" \
                    or (mm.get("through", "id") != "id" and (own.get(mm.get("through")) or {}).get("type") != "link") or any(k not in mcols for k in (mm.get("only") or {})):
                errs.append(f"data/{name}: shared with members needs the members table, its person column, its link column, and (for rows of a linked row) this table's link column")
        sh = d.get("share")
        if sh is not None:
            own = {c.get("key"): c for c in d.get("columns") or [] if isinstance(c, dict)}
            if d.get("scope") != "mine" or not isinstance(sh, dict) or (own.get(sh.get("field")) or {}).get("type") != "choice" or sh.get("value") in (None, ""):
                errs.append(f"data/{name}: 'the team sees the rows where …' goes with 'each person their own', and needs a pick-list column and one of its values")
            elif sh["value"] not in (own[sh["field"]].get("options") or []):
                errs.append(f"data/{name}: the team sees the rows where {sh['field']} is '{sh['value']}', which is not one of its options")
        ex = d.get("expose")
        if ex is not None:
            # 1.0.59: other systems may read these columns, with a key the owner makes (Connections)
            own = {c.get("key"): c for c in d.get("columns") or [] if isinstance(c, dict)}
            ecols = ex.get("columns") if isinstance(ex, dict) else None
            if not isinstance(ecols, list) or not ecols:
                errs.append(f"data/{name}: 'other systems may read it' needs the columns they may read")
            else:
                for k in ecols:
                    if k not in own:
                        errs.append(f"data/{name}: other systems may read '{k}', which is not a column of this table")
                    elif own[k].get("type") == "file":
                        errs.append(f"data/{name}: '{k}' is a file column — files are not given out with a key")
        for g in d.get("unique") or []:
            known = {c.get("key") for c in d.get("columns") or [] if isinstance(c, dict)}
            if not isinstance(g, list) or not g or any(k not in known for k in g):
                errs.append(f"data/{name}: 'only once' is a list of this table's columns")
        cols = d.get("columns") or []
        if not isinstance(cols, list) or not cols:
            errs.append(f"data/{name}: at least one column")
        seen = set()
        for c in cols if isinstance(cols, list) else []:
            k = (c or {}).get("key", "")
            if not NAME_RE.match(str(k)) or k in ("id", "created_at", "created_by"):
                errs.append(f"data/{name}: column key '{k}' is not allowed (lowercase, underscores; not id / created_at / created_by)")
            if k in seen:
                errs.append(f"data/{name}: column '{k}' twice")
            seen.add(k)
            ctype = (c or {}).get("type", "text")
            if ctype not in COLUMN_TYPES:
                errs.append(f"data/{name}: column '{k}' type must be one of {', '.join(COLUMN_TYPES)}")
            elif ctype == "choice":
                opts = (c or {}).get("options")
                if not isinstance(opts, list) or not opts:
                    errs.append(f"data/{name}: choice column '{k}' needs its options (the values to pick from)")
                elif len(opts) > 50:
                    errs.append(f"data/{name}: choice column '{k}' has more than 50 options — make it a link to a table instead")
                else:
                    low = [str(o).strip().lower() for o in opts]
                    if any(not isinstance(o, str) or not o.strip() or len(o) > 60 for o in opts):
                        errs.append(f"data/{name}: choice column '{k}': every option is a word or two (60 characters at most)")
                    elif len(set(low)) != len(low):
                        errs.append(f"data/{name}: choice column '{k}' has the same option twice")
                    elif len({opt_slug(o) for o in opts}) != len(opts):
                        errs.append(f"data/{name}: choice column '{k}': two options differ only in punctuation — make them different words")
            elif ctype == "link":
                tgt = (c or {}).get("table")
                if tgt not in t.data:
                    errs.append(f"data/{name}: link column '{k}' points at a table this tool does not have ('{tgt}')")
                else:
                    show = (c or {}).get("show")
                    tcols = {x.get("key"): x.get("type", "text") for x in (t.data[tgt].get("columns") or []) if isinstance(x, dict)}
                    if show and show != "id" and tcols.get(show) not in SHOWABLE:
                        errs.append(f"data/{name}: link column '{k}' shows '{show}', which is not a text, number, date or choice column of '{tgt}'")
            elif ctype == "file":
                acc = (c or {}).get("accept")
                if acc is not None and (not isinstance(acc, list) or not acc or any(str(a).lower().lstrip(".") not in FILE_TYPES for a in acc)):
                    errs.append(f"data/{name}: file column '{k}' accepts only {', '.join(FILE_TYPES)}")
                kp = (c or {}).get("keep", 0)
                if not isinstance(kp, int) or not 0 <= kp <= 50:
                    errs.append(f"data/{name}: file column '{k}': keeps 0 to 50 earlier versions")
                mb = (c or {}).get("max_mb", 10)
                if not isinstance(mb, (int, float)) or not 0 < mb <= FILE_MAX_MB:
                    errs.append(f"data/{name}: file column '{k}': max_mb is 1 to {FILE_MAX_MB}")
            elif ctype == "person":
                if (c or {}).get("who", "everyone") not in ("team", "everyone"):
                    errs.append(f"data/{name}: person column '{k}': who is 'team' (the person's own team) or 'everyone'")
            elif ctype == "calc":
                cc = (c or {}).get("calc") if isinstance((c or {}).get("calc"), dict) else {}
                fn = cc.get("fn")
                tcols = {x.get("key"): x for x in cols if isinstance(x, dict)}
                if fn not in CALC_FNS:
                    errs.append(f"data/{name}: worked-out column '{k}' says how: {', '.join(CALC_FNS)}")
                elif fn in ("count", "done") and (tcols.get(cc.get("of")) or {}).get("type") != "checklist":
                    errs.append(f"data/{name}: worked-out column '{k}' counts a checklist column of this table")
                elif fn == "days" and not all(x in ("created", "today", "") or (tcols.get(x) or {}).get("type") == "date" for x in (cc.get("from", "created"), cc.get("to", "today"))):
                    errs.append(f"data/{name}: worked-out column '{k}': days between two date columns (or when the row was added, or today)")
                elif fn == "expiry" and ((tcols.get(cc.get("date")) or {}).get("type") != "date"
                                         or (cc.get("warn") and (tcols.get(cc.get("warn")) or {}).get("type") not in ("int", "number"))
                                         or any(cc.get(x) and (tcols.get(cc.get(x)) or {}).get("type") != "bool" for x in ("required", "never", "off"))
                                         or (cc.get("file") and (tcols.get(cc.get("file")) or {}).get("type") != "file")):
                    errs.append(f"data/{name}: worked-out column '{k}': the status of a dated document needs its date column (and, if named, a number column for the warning days, yes/no columns for required / doesn't expire / archived, and its file column)")
                elif fn == "grade":
                    g = cc.get("grades")
                    if (tcols.get(cc.get("of")) or {}).get("type") not in ("int", "number", "calc"):
                        errs.append(f"data/{name}: worked-out column '{k}' grades a number column")
                    if not isinstance(g, list) or not g or not all(isinstance(x, list) and len(x) == 2 and (x[0] is None or isinstance(x[0], (int, float))) and isinstance(x[1], str) and 0 < len(x[1]) <= 40 for x in g) or g[-1][0] is not None:
                        errs.append(f"data/{name}: worked-out column '{k}': grades are [up to, word] pairs, the last one [null, word] for everything above")
            fmt = (c or {}).get("format")
            if fmt is not None and (fmt != "money" or ctype not in ("int", "number", "calc")):
                errs.append(f"data/{name}: column '{k}': a number can be shown as money (format: money); nothing else has a format")
            dv = (c or {}).get("default")
            if dv not in (None, ""):
                if ctype == "choice" and dv not in ((c or {}).get("options") or []):
                    errs.append(f"data/{name}: column '{k}' starts as '{dv}', which is not one of its options")
                elif ctype == "person" and dv != "me":
                    errs.append(f"data/{name}: person column '{k}' can only start as 'me' (the person who adds the row)")
                elif ctype in ("file", "calc", "checklist", "link"):
                    errs.append(f"data/{name}: column '{k}' cannot have a starting value")
            if ctype != "choice" and (c or {}).get("options") and ctype not in ("text",):
                errs.append(f"data/{name}: column '{k}' has options but is a {ctype} — make it a choice column")
            if (c or {}).get("label"):
                used.add(c["label"])
    ids_seen: dict = {}  # block id → page: ids are unique in the whole folder (the canvas has one node per id)
    for name, p in t.pages.items():
        # a 1.0.43 page (type + source) gets its one block's id made up for it; only drawn pages count
        for w in (widgets_of(name, p) if isinstance(p.get("layout"), dict) else []):
            wid = str((w or {}).get("id") or "")
            if wid and wid in ids_seen and ids_seen[wid] != name:
                errs.append(f"pages/{name}: block id '{wid}' is also on page '{ids_seen[wid]}' — give each block its own id")
            ids_seen.setdefault(wid, name)
        if not NAME_RE.match(name):
            errs.append(f"pages/{name}: lowercase letters, digits and underscores only")
        pacc = p.get("access")
        if pacc is not None:
            proles = pacc.get("roles") if isinstance(pacc, dict) else None
            if not isinstance(proles, list) or not proles or any(r not in ROLES for r in proles):
                errs.append(f"pages/{name}: access.roles must be a list of {' / '.join(ROLES)} (leave access out for everyone who may use the tool)")
        used.add(p.get("title", f"page.{name}.title"))
        if p.get("intro"):
            used.add(p["intro"])
        if not isinstance(p.get("layout"), dict) and p.get("type") not in PAGE_TYPES:
            errs.append(f"pages/{name}: a page needs a layout (blocks on the canvas) or a type ({', '.join(PAGE_TYPES)})")
        lay = effective_layout(name, p)
        seen_ids: set = set()
        n_widgets = 0
        for si, sec in enumerate(lay.get("sections") or []):
            cols = sec.get("columns") or []
            if not isinstance(cols, list) or not cols:
                errs.append(f"pages/{name}: section {si + 1} has no columns")
                continue
            if sum(int((c or {}).get("width", 12) or 12) for c in cols) > 12:
                errs.append(f"pages/{name}: section {si + 1}: column widths add up to more than 12")
            for col in cols:
                for w in (col or {}).get("widgets") or []:
                    n_widgets += 1
                    wid = str((w or {}).get("id") or "")
                    where = f"pages/{name} · block {wid or '?'}"
                    if not re.match(r"^[A-Za-z0-9_-]{1,40}$", wid):
                        errs.append(f"pages/{name}: every block needs an id (letters, digits, _ or -)")
                    elif wid in seen_ids:
                        errs.append(f"pages/{name}: block id '{wid}' twice")
                    seen_ids.add(wid)
                    typ = (w or {}).get("type")
                    if typ not in WIDGET_TYPES:
                        errs.append(f"{where}: type must be one of {', '.join(WIDGET_TYPES)}")
                        continue
                    if (w or {}).get("title"):
                        used.add(w["title"])
                    if typ in DATA_WIDGETS:
                        _check_source(t, where, typ, (w.get("source") or {}), errs)
                        _check_rows_rules(where, typ, w, errs)
                        if typ == "grid" and (w.get("source") or {}).get("data") in t.data:
                            from .grid import problems as grid_problems

                            errs.extend(f"{where}: {x}" for x in grid_problems(t, w))
                        if typ == "stats" and (w.get("source") or {}).get("grid"):
                            from .grid import find as grid_find

                            if grid_find(t, str(w["source"]["grid"])) is None:
                                errs.append(f"{where}: totals tiles read the grid '{w['source']['grid']}', which is not a grid block of this tool")
                        for a in w.get("actions") or []:
                            if isinstance(a, dict):
                                if a.get("label"):
                                    used.add(a["label"])
                                for q in a.get("ask") or []:
                                    if not isinstance(q, dict) or q.get("type") not in ("date", "month", "text", "choice") or not q.get("key"):
                                        errs.append(f"{where}: a button asks for a date, a month, a line of words or a pick from a list first (ask: [{{key, type, label}}])")
                                        continue
                                    if q.get("label"):
                                        used.add(q["label"])
                                    if q["type"] == "choice":  # 1.0.80: a pick-list — [{value, label}]
                                        opts = q.get("options")
                                        if not isinstance(opts, list) or not opts or not all(isinstance(o, dict) and str(o.get("value") or "") and o.get("label") for o in opts):
                                            errs.append(f"{where}: a pick-list question needs its choices (options: [{{value, label}}])")
                                        else:
                                            used.update(o["label"] for o in opts)
                                if a.get("kind") == "mail":
                                    continue  # 1.0.63: a Mail button — a message about the row, to someone CBX knows
                                fl = t.flows.get(str(a.get("flow") or ""))
                                if fl is None:
                                    errs.append(f"{where}: a button on its rows starts the rule '{a.get('flow')}', which the tool does not have")
                                elif (fl.get("trigger") or {}).get("type") != "button":
                                    errs.append(f"{where}: the rule '{a.get('flow')}' on its rows is not started by a button")
                        pb = t.data.get((w.get("source") or {}).get("data")) or {}
                        if isinstance(w.get("preset"), dict) and pb:
                            pcols = {c.get("key"): c for c in pb.get("columns") or [] if isinstance(c, dict)}
                            for k, v in w["preset"].items():
                                if k not in pcols:
                                    errs.append(f"{where}: new rows start with '{k}', which is not a column of its table")
                                elif pcols[k].get("type") == "choice" and v not in (pcols[k].get("options") or []):
                                    errs.append(f"{where}: new rows start with {k} '{v}', which is not one of its options")
                        if typ == "board":
                            b = t.data.get((w.get("source") or {}).get("data")) or {}
                            lane = next((c for c in b.get("columns") or [] if c.get("key") == w.get("lane")), None)
                            if b and (lane is None or lane.get("type") != "choice"):
                                errs.append(f"{where}: a board's columns come from a pick-list column of its table")
                        for c in w.get("columns") or []:
                            if isinstance(c, dict) and c.get("label"):
                                used.add(c["label"])
                        for f in w.get("fields") or []:
                            if isinstance(f, dict) and f.get("label"):
                                used.add(f["label"])
                        for it in w.get("items") or []:
                            if isinstance(it, dict) and it.get("label"):
                                used.add(it["label"])
                        for c in (w.get("source") or {}).get("columns") or []:
                            if isinstance(c, dict) and c.get("label"):
                                used.add(c["label"])
                    elif typ == "metrics":
                        from . import metrics as MX

                        items = w.get("items") or []
                        if not isinstance(items, list) or not items:
                            errs.append(f"{where}: a Numbers block needs at least one number (items[].metric)")
                        for it in items if isinstance(items, list) else []:
                            k = (it or {}).get("metric") if isinstance(it, dict) else None
                            if k not in MX.METRICS:
                                errs.append(f"{where}: '{k}' is not a number this server knows")
                            if isinstance(it, dict) and it.get("label"):
                                used.add(it["label"])
                    elif typ == "button":
                        fl = t.flows.get(str(w.get("flow") or ""))
                        if fl is None:
                            errs.append(f"{where}: a Run button needs a flow of this tool to start")
                        elif (fl.get("trigger") or {}).get("type") != "button":
                            errs.append(f"{where}: the flow '{w.get('flow')}' is not started by a button (its trigger is {(fl.get('trigger') or {}).get('type')})")
                        if w.get("label"):
                            used.add(w["label"])
                        if w.get("confirm"):
                            used.add(w["confirm"])
                    elif typ in ("heading", "text"):
                        if not w.get("text"):
                            errs.append(f"{where}: needs 'text' (a language key)")
                        else:
                            used.add(w["text"])
                    elif typ == "image":
                        a = str(w.get("asset") or "")
                        if not a or "/" in a or "\\" in a or not a.lower().endswith(ASSET_SUFFIXES):
                            errs.append(f"{where}: needs an uploaded image ('asset' = a file name in assets/)")
                        elif not (t.path / "assets" / a).exists():
                            errs.append(f"{where}: assets/{a} is not in the folder")
                        if w.get("alt"):
                            used.add(w["alt"])
                    elif typ == "embed":
                        u = str(w.get("url") or "")
                        if not u.startswith("https://"):
                            errs.append(f"{where}: an embedded website needs an https:// address")
                    elif typ == "code":
                        code = w.get("code")
                        if not isinstance(code, str) or not code.strip():
                            errs.append(f"{where}: the code block is empty")
                        elif len(code) > CODE_MAX:
                            errs.append(f"{where}: the code is over {CODE_MAX:,} characters — put it in a file and link it")
                    elif typ == "links":
                        for it in w.get("items") or []:
                            if isinstance(it, dict):
                                if it.get("label"):
                                    used.add(it["label"])
                                # 1.0.99: or a download from CBX itself (/api/…, e.g. Entity Compliance's /api/ec/export) — a GET
                                # the person's own session makes, with their own rights; nothing else on the server
                                if it.get("url") and not (str(it["url"]).startswith(("https://", "http://", "#/")) or DOWNLOAD_RE.match(str(it["url"]))):
                                    errs.append(f"{where}: a link needs an https:// address, a page (#/…) or a CBX download (/api/…)")
        if n_widgets == 0:
            errs.append(f"pages/{name}: the page has no blocks yet — pull one onto the canvas and wire it to the page")
    from . import flows as FL

    for fname, f in t.flows.items():
        ferrs, fused = FL.check(t, fname, f)
        errs.extend(ferrs)
        used |= fused
    for k in sorted(used):
        if k and not KEY_RE.match(str(k)):
            errs.append(f"'{k}' is not a language key (lowercase, digits, dots and underscores); put the words in languages/")
        elif k and k not in en:
            errs.append(f"languages/en.json has no '{k}'")
    return errs


def load_all(root: Optional[Path] = None) -> Registry:
    root = Path(root or TOOLS_ROOT)
    reg = Registry(root=root)
    if not root.is_dir():
        return reg
    for folder in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith((".", "_")) and p.name != "__pycache__"):
        if not (folder / "tool.json").exists() and not any(folder.iterdir()):
            continue
        t = read_folder(folder)
        (reg.tools if t.ok else reg.broken)[t.slug] = t
    return reg


def registry() -> Registry:
    global _REG
    with _LOCK:
        if _REG is None:
            _REG = load_all()
        return _REG


def reload(root: Optional[Path] = None) -> Registry:
    global _REG
    with _LOCK:
        _REG = load_all(root)
        return _REG


def get(slug: str) -> Optional[ToolDef]:
    return registry().tools.get(slug)
