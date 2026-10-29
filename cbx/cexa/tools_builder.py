"""C-EXA builds (1.0.82): make, redesign, change, test, fix and deploy tools — the same steps a person takes in the
Builder (Save → Try → Test → Deploy), through the Builder's own API as the person she works for.

    tool_folder        read a tool whole: its pages, tables, rules and English words (the draft if there is one)
    build_tool         make a new tool, or redesign one, from a whole folder (JSON) — saved as a draft
    change_tool_part   set or remove ONE page, table, rule or a few words in a tool's draft
    try_tool           a page as the draft draws it (and its problems), or a rule dry-run (writes nothing)
    test_tools         package my drafts as a version and run the whole test suite on it — the result, with failures
    deploy_version     put a tested version in Test for people to try; going live waits for the owner's tap

Drafts are personal: what she saves is the draft of the person she works for (never someone else's). Nothing she
does here is live: Test is a copy, and Live is the owner's one tap (cbx/cexa/policy.py).
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..models import Fix, User
from .tools import api as _api
from .tools import tool

BIG = 400_000  # the Builder's answers are read whole here (parsed), never handed to the model as they are


def api(*a, **k) -> str:
    return _api(*a, limit=BIG, **k)

SLUG = re.compile(r"^[a-z][a-z0-9-]{1,39}$")


def _json(res: str) -> tuple[int, Any]:
    """(status, body) out of api()'s 'METHOD /path → 200. {...}'."""
    m = re.search(r"→ (\d{3})\. (.*)$", res, re.S)
    if not m:
        return 0, res
    try:
        return int(m.group(1)), json.loads(m.group(2))
    except ValueError:
        return int(m.group(1)), m.group(2)


def _folder(db: Session, me: User, slug: str) -> Optional[dict]:
    st, body = _json(api(db, me, "GET", f"/api/builder/tools/{slug}"))
    return body if st == 200 and isinstance(body, dict) else None


def _save(db: Session, me: User, slug: str, F: dict, why: str) -> str:
    body = {"manifest": F.get("manifest") or {}, "pages": F.get("pages") or {}, "data": F.get("data") or {}, "flows": F.get("flows") or {},
            "languages": F.get("languages") or {}, "readme": F.get("readme") or ""}
    res = api(db, me, "PUT", f"/api/builder/tools/{slug}", body, why=why)
    st, out = _json(res)
    if st != 200 or not isinstance(out, dict):
        return res[:800]
    errs = out.get("errors") or []
    miss = {k: len(v) for k, v in (out.get("missing") or {}).items() if v}
    return (f"Saved the draft of {slug}." + (f" {len(errs)} problem(s) to fix before it can be tested: " + "; ".join(errs[:8]) if errs else " No problems.")
            + (f" Words still to translate: {miss}." if miss else ""))


@tool("tool_folder", "read", "A tool whole, as the Builder holds it (the draft if there is one): its manifest, pages (layout and blocks), tables (columns), rules (flows) and its English words, plus its problems. Read it before you change or redesign a tool.", {"tool": "the tool's slug, e.g. todo"})
def tool_folder(db: Session, me: User, fix: Optional[Fix], tool: str = "", **_) -> str:
    slug = (tool or "").strip().lower()
    F = _folder(db, me, slug)
    if F is None:
        return f"No tool '{slug}'. list_tools shows them."
    out = {"slug": slug, "source": F.get("source"), "manifest": F.get("manifest"), "pages": F.get("pages"), "data": F.get("data"), "flows": F.get("flows"),
           "words_en": (F.get("languages") or {}).get("en", {}), "errors": F.get("errors"), "editor": F.get("editor")}
    text = json.dumps(out, ensure_ascii=False)
    return text if len(text) < 14000 else text[:14000] + " … (cut — ask for one part with change_tool_part's names)"


@tool("build_tool", "change", "Make a NEW tool or REDESIGN one from a whole folder: {manifest, pages, data, flows, languages:{en:{…}}, readme}. Keys are words' keys (e.g. 'page.home.title'); give their English in languages.en. Saved as the draft of the person I work for; nothing is live. Returns the problems to fix.", {"tool": "slug: lowercase letters, digits and dashes, e.g. site-visits", "folder": "the whole folder as JSON"})
def build_tool(db: Session, me: User, fix: Optional[Fix], tool: str = "", folder: str = "", **_) -> str:
    slug = (tool or "").strip().lower()
    if not SLUG.match(slug):
        return "The tool's name is lowercase letters, digits and dashes (2–40), e.g. site-visits."
    try:
        F = json.loads(folder) if isinstance(folder, str) else folder
    except ValueError as e:
        return f"The folder is not valid JSON ({e})."
    if not isinstance(F, dict) or not isinstance(F.get("manifest"), dict):
        return "The folder needs at least a manifest: {\"manifest\": {\"title\": \"tool.title\", \"home\": \"<page>\", …}, \"pages\": {…}, \"data\": {…}, \"languages\": {\"en\": {…}}}."
    old = _folder(db, me, slug)
    if old is not None and F.get("flows") is None:
        F["flows"] = old.get("flows") or {}
    langs = F.get("languages") or {}
    if old is not None:  # keep the other languages' words where the new folder does not give them
        for lg, words in (old.get("languages") or {}).items():
            langs.setdefault(lg, {})
            for k, v in (words or {}).items():
                langs[lg].setdefault(k, v)
    F["languages"] = langs
    return _save(db, me, slug, F, f"{'Redesign' if old else 'Build'} the tool {slug}")


PARTS = ("page", "table", "rule", "words", "manifest", "readme")


@tool("change_tool_part", "change", "Change ONE part of a tool's draft: a page, a table (data block), a rule (flow), some words, the manifest or the README. Give the part as JSON; 'remove' takes it out. For a table you may give only what changes: {columns: {province: {type: choice, options: [Gauteng, …]}}} adds or changes just those columns and keeps the rest. Saved as a draft; nothing is live.", {"tool": "slug", "part": "page | table | rule | words | manifest | readme", "name": "the page / table / rule name (not for words, manifest, readme)", "value": "the part as JSON (words: {key: English text}; readme: text), or 'remove'", "lang": "words only: en (default), ar, pt or fr"})
def change_tool_part(db: Session, me: User, fix: Optional[Fix], tool: str = "", part: str = "", name: str = "", value: str = "", lang: str = "en", **_) -> str:
    slug = (tool or "").strip().lower()
    part = (part or "").strip().lower()
    if part not in PARTS:
        return "Which part: " + ", ".join(PARTS) + "."
    F = _folder(db, me, slug)
    if F is None:
        return f"No tool '{slug}'. Make it with build_tool."
    remove = (value or "").strip().lower() == "remove"
    parsed: Any = None
    if not remove and part != "readme":
        try:
            parsed = json.loads(value)
        except ValueError as e:
            return f"The value is not valid JSON ({e})."
    key = {"page": "pages", "table": "data", "rule": "flows"}.get(part)
    if key:
        if not re.match(r"^[a-z][a-z0-9_]{0,40}$", name or ""):
            return "The name is lowercase letters, digits and underscores."
        F[key] = dict(F.get(key) or {})
        if remove:
            F[key].pop(name, None)
        elif isinstance(parsed, dict):
            if part == "table":
                merged = _merge_table(F[key].get(name), parsed)
                if isinstance(merged, str):
                    return merged
                parsed = merged
            F[key][name] = parsed
        else:
            return "A page, table or rule is a JSON object."
    elif part == "words":
        lg = (lang or "en").strip().lower()
        F["languages"] = dict(F.get("languages") or {})
        F["languages"][lg] = {**(F["languages"].get(lg) or {}), **({k: str(v) for k, v in parsed.items()} if isinstance(parsed, dict) else {})}
    elif part == "manifest":
        if not isinstance(parsed, dict):
            return "The manifest is a JSON object."
        F["manifest"] = {**(F.get("manifest") or {}), **parsed}
    else:
        F["readme"] = "" if remove else value
    return _save(db, me, slug, F, f"Change the {part} {name} of {slug}".strip())


def _merge_table(old: Any, new: dict) -> Any:
    """1.0.87: a table change is laid over the table, never in place of it. Columns may come as the whole list,
    or as {"key": {changes}} to change or add just those — the rest of the table and its other columns stay."""
    base = dict(old) if isinstance(old, dict) else {}
    cols = [dict(c) for c in (base.get("columns") or []) if isinstance(c, dict)]
    patch = new.get("columns")
    if isinstance(patch, dict):
        for k, ch in patch.items():
            if not isinstance(ch, dict):
                return f"Column '{k}': give its settings as an object, e.g. {{\"type\": \"choice\", \"options\": [...]}}."
            hit = next((c for c in cols if c.get("key") == k), None)
            if hit is not None:
                hit.update(ch)
            else:
                cols.append({"key": k, "label": ch.get("label") or f"col.{k}", "type": ch.get("type") or "text", **{x: v for x, v in ch.items() if x not in ("key",)}})
        new = {**new, "columns": cols}
    elif patch is not None and not isinstance(patch, list):
        return "columns is a list of columns (or {key: changes} to change some)."
    out = {**base, **new}
    if not isinstance(out.get("columns"), list) or not out.get("columns"):
        return "A table needs its columns (a list). Read it with tool_folder first and give the whole table, or {columns: {key: changes}}."
    for c in out["columns"]:
        if isinstance(c.get("options"), str):  # "Gauteng, Western Cape, …" → a list
            c["options"] = [o.strip() for o in c["options"].split(",") if o.strip()]
        if c.get("type") in ("pick-list", "picklist", "select", "dropdown"):
            c["type"] = "choice"
    return out


@tool("try_tool", "read", "Try a tool's draft before testing it: a page as the draft draws it (its blocks and how many rows each shows, and its problems), or a rule run dry (every step says what it would do; nothing is written or sent).", {"tool": "slug", "page": "a page name, or empty", "rule": "a rule name to dry-run, or empty", "sample": "optional JSON: the row or button values to try the rule with"})
def try_tool(db: Session, me: User, fix: Optional[Fix], tool: str = "", page: str = "", rule: str = "", sample: str = "", **_) -> str:
    slug = (tool or "").strip().lower()
    F = _folder(db, me, slug)
    if F is None:
        return f"No tool '{slug}'."
    if rule:
        flow = (F.get("flows") or {}).get(rule)
        if flow is None:
            return f"{slug} has no rule '{rule}'. It has: {', '.join(F.get('flows') or {}) or 'none'}."
        try:
            smp = json.loads(sample) if sample else {}
        except ValueError:
            smp = {}
        return api(db, me, "POST", f"/api/builder/tools/{slug}/flows/{rule}/test", {"flow": flow, "data": F.get("data") or {}, "manifest": F.get("manifest") or {}, "sample": smp})[:2500]
    pg = page or (F.get("manifest") or {}).get("home") or next(iter(F.get("pages") or {}), "")
    st, out = _json(api(db, me, "GET", f"/api/builder/tools/{slug}/preview/{pg}"))
    if st != 200 or not isinstance(out, dict):
        return f"Page '{pg}': {out}"[:1500]
    rows = {k: (v.get("total") if isinstance(v, dict) else None) for k, v in (out.get("rows") or {}).items()}
    notes = {k: v.get("note") for k, v in (out.get("rows") or {}).items() if isinstance(v, dict) and v.get("note")}
    return json.dumps({"page": pg, "blocks": list((out.get("widgets") or {}).keys()), "rows_per_block": rows, "notes": notes, "problems": out.get("errors") or []}, ensure_ascii=False)[:2500]


@tool("test_tools", "change", "Test: package the drafts of the person I work for (or the tools named) as a new version and run the whole test suite on it — a few minutes. Returns the version, passed / failed and the failing tests. Nothing is live.", {"tools": "optional: slugs separated by commas (empty = all my drafts)", "notes": "one line: what changed"})
def test_tools(db: Session, me: User, fix: Optional[Fix], tools: str = "", notes: str = "", **_) -> str:
    slugs = [s.strip().lower() for s in (tools or "").split(",") if s.strip()]
    st, out = _json(api(db, me, "POST", "/api/builder/release", {"slugs": slugs, "notes": notes or ""}, why="Test my drafts"))
    if st != 200 or not isinstance(out, dict):
        return f"Not tested: {out}"[:1200]
    v = out.get("version") or {}
    t = v.get("tests") or {}
    fails = t.get("failures") or []
    return (f"v{out.get('tag')} (version #{v.get('id')}): {t.get('passed', 0)} passed, {t.get('failed', 0)} failed."
            + (" Failing: " + " | ".join(fails[:6]) if fails else " All green — deploy_version puts it in Test.")
            + (f" Council: {(t.get('council') or {}).get('verdict')}" if t.get("council") else ""))[:2500]


@tool("deploy_version", "change", "Deploy a tested version: 'test' puts it in the Test area for people to try (I do that); 'live' promotes it to production with 10 minutes' notice — that waits for the owner's tap.", {"version": "the version id (#) from test_tools", "to": "test | live"})
def deploy_version(db: Session, me: User, fix: Optional[Fix], version: str = "", to: str = "test", **_) -> str:
    vid = re.sub(r"\D", "", version or "")
    if not vid:
        return "Which version (the # from test_tools)?"
    if (to or "test").strip().lower().startswith("live"):
        return api(db, me, "POST", f"/api/versions/{vid}/promote?minutes=10", None, why=f"Put version #{vid} live (10 minutes' notice)")
    st, out = _json(api(db, me, "POST", f"/api/areas/test/{vid}", None, why=f"Put version #{vid} in Test"))
    if st != 200:
        return f"Not in Test: {out}"[:800]
    return f"Version #{vid} is in Test — everyone who tests has a sign-in on Work areas. When it is good, deploy_version to live (the owner taps)."
