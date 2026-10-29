"""C-EXA's hands. A closed set of actions she may take while talking to an admin or owner.

Three tiers, decided by the action, never by the model:
  read    — look at anything (templates, batches, files, code). Always allowed. Logged.
  change  — fix data/configuration directly (add template columns, rerun a batch, open a bug,
            create a task). Allowed for admins/owners on the spot. Logged + recorded on a Fix.
  code    — anything that changes the app itself. Never direct: becomes a Proposal that is built
            and tested in a sandbox and shipped only by an owner (cbx.cexa.developer).

Every action returns a short plain-text result the model can read, and is appended to the Fix it
belongs to so the owner sees exactly what C-EXA did.
"""
from __future__ import annotations

import contextvars
import json
import re
import secrets
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from ..audit import log
from ..config import ROOT
from ..db import utcnow
from ..branding import ai_name
from ..models import Batch, CexaMessage, Column, Fix, Proposal, StoredFile, Task, TaskNote, Template, User
from ..warehouse import storage

TOOLS: dict[str, dict] = {}


def tool(name: str, tier: str, doc: str, args: dict[str, str]):
    def deco(fn: Callable):
        TOOLS[name] = {"fn": fn, "tier": tier, "doc": doc, "args": args}
        return fn

    return deco


FIRST = ("list_tools", "describe_tool", "find_rows", "add_row", "update_row", "give_task", "my_tasks", "waiting_for_me", "add_filter", "remove_filter", "add_column", "switch_rule")


def catalogue(only: Optional[tuple] = None) -> str:
    """Prompt text describing the tools: the everyday ones (the Builder tools) first, where a model
    reading a long list looks hardest; `only` limits it (what a non-admin may use)."""
    lines = []
    names = [n for n in FIRST if n in TOOLS] + [n for n in TOOLS if n not in FIRST]
    for n in names:
        if only is not None and n not in only:
            continue
        t = TOOLS[n]
        a = ", ".join(f"{k}: {v}" for k, v in t["args"].items()) or "no arguments"
        lines.append(f"- {n} ({t['tier']}): {t['doc']} Args: {a}")
    return "\n".join(lines)


# ---------------------------------------------------------------- helpers -----------------------
def _templates_for(db: Session, me: User):
    q = db.query(Template)
    if not me.is_admin:
        q = q.filter(Template.owner_id == me.id)
    return q.order_by(Template.created_at.desc()).all()


def _find_template(db: Session, me: User, ref: str) -> Optional[Template]:
    """Best match for what a person typed: exact name → most shared words (ties: newest) → the only one."""
    ref = (ref or "").strip()
    if ref.isdigit():
        return db.get(Template, int(ref))
    ts = _templates_for(db, me)
    if not ts:
        return None
    if not ref:
        return ts[0]
    for t in ts:
        if t.name.lower() == ref.lower():
            return t
    words = set(re.findall(r"[a-z0-9]+", ref.lower())) - {"the", "template", "a", "my"}
    best, best_score = None, 0
    for t in ts:  # ts is newest-first, so the first best wins ties
        tw = set(re.findall(r"[a-z0-9]+", t.name.lower()))
        score = len(words & tw) * 10 + (5 if ref.lower() in t.name.lower() else 0)
        if score > best_score:
            best, best_score = t, score
    if best is not None:
        return best
    return ts[0] if len(ts) == 1 else None


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:60]


def _excel_headers_for(db: Session, t: Template):
    if not t.source_file_id:
        return None, "This template was built in-app (no Excel file behind it)."
    f = db.get(StoredFile, t.source_file_id)
    if f is None:
        return None, "The Excel file behind this template is missing from the warehouse."
    from ..pdfstudio.excel_template import read_headers

    try:
        return read_headers(storage.read_bytes(f)), ""
    except Exception as e:  # noqa: BLE001
        return None, f"I couldn't read the Excel file: {e}"


# ---------------------------------------------------------------- read tools --------------------
@tool("list_templates", "read", "List the PDF Studio templates the person can see, with their columns.", {})
def list_templates(db: Session, me: User, fix: Optional[Fix], **_) -> str:
    ts = _templates_for(db, me)
    if not ts:
        return "No templates yet."
    return "\n".join(f"#{t.id} '{t.name}' — {len(t.columns)} columns: " + ", ".join(c.label for c in t.columns) for t in ts)


@tool("inspect_template", "read", "Compare a template's columns with the header row of the Excel file it was made from; report what is missing.", {"template": "name or id (optional if only one)"})
def inspect_template(db: Session, me: User, fix: Optional[Fix], template: str = "", **_) -> str:
    t = _find_template(db, me, template)
    if t is None:
        return "I can't find that template. Templates: " + list_templates(db, me, fix)
    have = {c.key for c in t.columns}
    hdr, err = _excel_headers_for(db, t)
    if hdr is None:
        return f"Template #{t.id} '{t.name}' has {len(t.columns)} columns ({', '.join(c.label for c in t.columns)}). {err}"
    excel = [h for h in hdr.headers if h]
    missing = [h for h in excel if (_slug(h) or "") not in have]
    out = [f"Template #{t.id} '{t.name}': {len(t.columns)} column(s): {', '.join(c.label for c in t.columns) or '—'}.",
           f"Excel sheet '{hdr.sheet}', header on row {hdr.row_index}: {len(excel)} column(s): {', '.join(excel)}."]
    if hdr.skipped:
        out.append("Rows above the header (titles, skipped): " + " | ".join(hdr.skipped[:3]))
    out.append(("MISSING from the template: " + ", ".join(missing)) if missing else "Nothing missing — the template matches the Excel header.")
    return "\n".join(out)


@tool("inspect_batch", "read", "Show a PDF Studio batch: status, accuracy, runs and C-EXA's log.", {"batch": "batch id"})
def inspect_batch(db: Session, me: User, fix: Optional[Fix], batch: str = "", **_) -> str:
    b = db.get(Batch, int(batch)) if str(batch).isdigit() else None
    if b is None:
        bs = db.query(Batch).order_by(Batch.created_at.desc()).limit(5).all()
        return "Which batch? Recent: " + "; ".join(f"#{x.id} {x.name or ''} ({x.status})" for x in bs)
    if not me.is_admin and b.user_id != me.id:
        return "That batch belongs to someone else."
    runs = "; ".join(f"run {r.get('n')}: {round((r.get('accuracy') or 0)*100,1)}%" for r in (b.runs or []))
    logl = "\n".join(f"  {e.get('text')}" for e in (b.log or [])[-8:])
    return f"Batch #{b.id} '{b.name}' status {b.status}, step {b.step}, accuracy {b.accuracy}, cells for user {b.cells_for_user}, {len(b.documents)} docs.\nRuns: {runs or '—'}\nLog:\n{logl or '  —'}"


@tool("what_i_know", "read", "What label text C-EXA has learned for a kind of column (the lexicon), best first — what she would try on a layout she has never seen.", {"column": "a column key or label, e.g. 'account_number' or 'Total due'"})
def what_i_know(db: Session, me: User, fix: Optional[Fix], column: str = "", **_) -> str:
    from ..pdfstudio import lexicon as LX

    name = str(column or "").strip()
    if not name:
        s = LX.stats(db, top=10)
        top = "\n".join(f"  - '{r['label']}' ({r['placement']}) for {r['group']} — confirmed {r['confirmed']}×" + (f", {r['corrections']} from a person's fix" if r["corrections"] else "") for r in s["top"])
        return f"I have {s['learned']} label(s) learned from real documents ({s['from_corrections']} taught by someone fixing a cell), across {s['groups']} kinds of column and {s['layouts']} layout(s), on top of {s['seeds']} shipped as a starting vocabulary.\n" + (top or "  — nothing learned yet; I would be working from the shipped vocabulary.") + "\nName a column to see what I would try for it."
    return LX.describe(db, {"key": name, "label": name}, limit=10)


@tool("read_code", "read", "Read part of the app's own source code (read-only) to understand a bug.", {"path": "file path under the app, e.g. cbx/api/pdfstudio.py", "search": "optional text to find; returns the lines around it"})
def read_code(db: Session, me: User, fix: Optional[Fix], path: str = "", search: str = "", **_) -> str:
    if not me.is_owner and not me.is_admin:
        return "Only admins may look at the code."
    try:
        p = (ROOT / str(path or "").strip().replace("\\", "/")).resolve()
    except (OSError, ValueError):
        return "I can only read the app's own source files (cbx/, web/, tests/)."
    if ROOT.resolve() not in p.parents or not p.is_file() or p.suffix not in (".py", ".js", ".css", ".html", ".md", ".sql", ".txt"):
        return "I can only read the app's own source files (cbx/, web/, tests/)."
    text = p.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    if search:
        hits = [i for i, l in enumerate(lines) if search.lower() in l.lower()]
        if not hits:
            return f"'{search}' does not appear in {path}."
        i = hits[0]
        lo, hi = max(0, i - 15), min(len(lines), i + 25)
        return f"{path} lines {lo+1}-{hi} ({len(hits)} match(es)):\n" + "\n".join(f"{n+1:4d}  {l}" for n, l in enumerate(lines[lo:hi], start=lo))
    return f"{path} ({len(lines)} lines), first 80:\n" + "\n".join(f"{n+1:4d}  {l}" for n, l in enumerate(lines[:80]))


@tool("search_code", "read", "Find which source files mention a word or phrase.", {"query": "text to search for"})
def search_code(db: Session, me: User, fix: Optional[Fix], query: str = "", **_) -> str:
    if not query.strip():
        return "Give me something to search for."
    hits = []
    for d in ("cbx", "web"):
        for f in (ROOT / d).rglob("*"):
            if f.suffix in (".py", ".js") and "__pycache__" not in f.parts:
                try:
                    for n, l in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                        if query.lower() in l.lower():
                            hits.append(f"{f.relative_to(ROOT)}:{n}: {l.strip()[:120]}")
                            if len(hits) >= 25:
                                return "\n".join(hits) + "\n(more…)"
                except OSError:
                    pass
    return "\n".join(hits) or f"'{query}' not found in the code."


# ---------------------------------------------------------------- change tools ------------------
@tool("fix_template_columns", "change", "Add every column from the template's Excel header row that is missing from the template.", {"template": "name or id (optional if only one)"})
def fix_template_columns(db: Session, me: User, fix: Optional[Fix], template: str = "", **_) -> str:
    t = _find_template(db, me, template)
    if t is None:
        return "I can't find that template."
    hdr, err = _excel_headers_for(db, t)
    if hdr is None:
        return err
    from ..pdfstudio.excel_template import guess_ctype

    have = {c.key for c in t.columns}
    added = []
    ordinal = max([c.ordinal for c in t.columns] or [0])
    for i, h in enumerate(hdr.headers):
        if not h:
            continue
        key = _slug(h) or f"col_{i+1}"
        if key in have:
            continue
        have.add(key)
        ordinal += 1
        sv = hdr.sample[i] if hdr.sample and i < len(hdr.sample) else None
        ct = guess_ctype(h, sv)
        db.add(Column(template_id=t.id, ordinal=ordinal, key=key, label=h, ctype=ct, required=True, critical=(ct == "money")))
        added.append(h)
    db.flush()
    db.refresh(t)
    log(db, "pdf_template_fixed", actor=_cexa(db), target=f"template:{t.id}", detail=f"added {len(added)} columns from Excel: {', '.join(added)}" if added else "nothing to add")
    if not added:
        return f"Template '{t.name}' already has every column from the Excel header."
    return f"Added {len(added)} column(s) to '{t.name}': {', '.join(added)}. It now has {len(t.columns)} columns. Next batch on this template will read all of them."


@tool("rename_template", "change", "Rename a PDF Studio template.", {"template": "current name or id", "new_name": "the new name"})
def rename_template(db: Session, me: User, fix: Optional[Fix], template: str = "", new_name: str = "", **_) -> str:
    t = _find_template(db, me, template)
    if t is None:
        return "I can't find that template. " + list_templates(db, me, fix)
    new_name = (new_name or "").strip()
    if not new_name:
        return "What should the new name be?"
    if t.owner_id != me.id and not me.is_admin:
        return "Only the person who made this template (or an admin) can rename it."
    old = t.name
    t.name = new_name[:160]
    db.flush()
    log(db, "pdf_template_renamed", actor=_cexa(db), target=f"template:{t.id}", detail=f"'{old}' → '{t.name}' for {me.name}")
    return f"Renamed template #{t.id} from '{old}' to '{t.name}'."


@tool("rerun_batch", "change", "Run a PDF Studio batch again (after a template or profile fix).", {"batch": "batch id"})
def rerun_batch(db: Session, me: User, fix: Optional[Fix], batch: str = "", **_) -> str:
    b = db.get(Batch, int(batch)) if str(batch).isdigit() else None
    if b is None:
        return "Which batch id?"
    if not me.is_admin and b.user_id != me.id:
        return "That batch belongs to someone else."
    if b.status in ("learning", "running"):
        return f"Batch #{b.id} is already running."
    from .. import jobs
    from ..pdfstudio import pipeline

    b.status = "learning"
    b.step = "Received"
    b.progress = 0.0
    db.commit()
    pipeline.submit_batch(b.id)
    log(db, "pdf_batch_rerun", actor=_cexa(db), target=f"batch:{b.id}")
    return f"Batch #{b.id} is running again. Watch it on PDF Studio."


@tool("open_bug", "change", "Log a bug or problem in the Bugs & fixes tracker and put a card on the Develop board.", {"title": "short title", "detail": "what is wrong and how to reproduce", "area": "pdf-studio | requests | warehouse | builder | axl | other"})
def open_bug(db: Session, me: User, fix: Optional[Fix], title: str = "", detail: str = "", area: str = "other", **_) -> str:
    if fix is not None:
        fix.title = title or fix.title
        fix.detail = (fix.detail + "\n" + detail).strip()
        fix.area = area or fix.area
        f = fix
    else:
        f = Fix(title=title[:200] or "Untitled", detail=detail, area=area, reported_by_id=me.id, status="open")
        db.add(f)
        db.flush()
    if not f.task_id:
        t = Task(title=f"Bug: {f.title}"[:200], detail=f.detail, status="todo", assignee_axl=True, origin="bug", origin_ref=f"fix:{f.id}", created_by_id=me.id)
        db.add(t)
        db.flush()
        db.add(TaskNote(task_id=t.id, by="axl", text=f"Logged from a conversation with {me.name.split()[0]}. I'm on it."))
        f.task_id = t.id
    log(db, "bug_opened", actor=_cexa(db), target=f"fix:{f.id}", detail=f.title)
    return f"Logged as fix #{f.id} '{f.title}' (area {f.area}) and put on the Develop board."


@tool("resolve_fix", "change", "Mark the current bug fixed (or not fixable) with a one-line resolution.", {"status": "fixed | wont_fix | code_pending", "resolution": "what was done, in one or two sentences"})
def resolve_fix(db: Session, me: User, fix: Optional[Fix], status: str = "fixed", resolution: str = "", **_) -> str:
    if fix is None:
        return "There is no open fix in this conversation to resolve."
    if status not in ("fixed", "wont_fix", "code_pending", "investigating"):
        status = "fixed"
    fix.status = status
    fix.resolution = resolution[:2000]
    if fix.task_id and (t := db.get(Task, fix.task_id)):
        if status in ("fixed", "wont_fix"):
            t.status = "done"
            t.done_at = utcnow()
        db.add(TaskNote(task_id=t.id, by="axl", text=f"{status}: {resolution}"[:2000]))
    log(db, "fix_resolved", actor=_cexa(db), target=f"fix:{fix.id}", detail=f"{status}: {resolution[:160]}")
    return f"Fix #{fix.id} marked {status}."


# ---------------------------------------------------------------- harvest -----------------------
def _find_source(db: Session, me: User, ref: str):
    from ..models import HarvestSource

    ref = (ref or "").strip()
    q = db.query(HarvestSource)
    if not me.is_admin:
        q = q.filter(HarvestSource.owner_id == me.id)
    rows = q.order_by(HarvestSource.name).all()
    if ref.isdigit():
        return next((s for s in rows if s.id == int(ref)), None), rows
    for s in rows:
        if s.name.lower() == ref.lower():
            return s, rows
    hits = [s for s in rows if ref and ref.lower() in s.name.lower()]
    return (hits[0] if len(hits) == 1 else (rows[0] if len(rows) == 1 and not ref else None)), rows


@tool("list_harvest_sources", "read", "List the PDF Studio Harvest sources (folders watched continuously): status, last run, rows.", {})
def list_harvest_sources(db: Session, me: User, fix: Optional[Fix], **_) -> str:
    from ..models import HarvestRow

    _, rows = _find_source(db, me, "")
    if not rows:
        return "No harvest sources yet. An admin adds one on PDF Studio → Harvest."
    out = []
    for s in rows:
        n = db.query(HarvestRow).filter(HarvestRow.source_id == s.id).count()
        out.append(f"#{s.id} '{s.name}' — {s.kind} {s.path} · every {s.every_minutes} min · {'active' if s.active else 'paused'} · {s.status} · {n} rows · last run {s.last_run_at.strftime('%d %b %H:%M') if s.last_run_at else 'never'}")
    return "\n".join(out)


@tool("inspect_harvest_source", "read", "Show one Harvest source: folder, template, schedule, last run, row counts by month, last error.", {"name": "source name or id"})
def inspect_harvest_source(db: Session, me: User, fix: Optional[Fix], name: str = "", **_) -> str:
    from collections import Counter

    from ..models import HarvestRow, Template

    s, rows = _find_source(db, me, name)
    if s is None:
        return "Which source? " + (", ".join(f"'{x.name}'" for x in rows) or "there are none yet")
    t = db.get(Template, s.template_id)
    hr = db.query(HarvestRow).filter(HarvestRow.source_id == s.id).all()
    by_month = Counter((r.doc_date.strftime("%Y-%m") if r.doc_date else "undated") for r in hr)
    months = ", ".join(f"{k}: {v}" for k, v in sorted(by_month.items()))
    st = s.stats or {}
    return (
        f"Source #{s.id} '{s.name}': {s.kind} folder {s.path}{' (+subfolders)' if s.include_subfolders else ''}, template '{t.name if t else '?'}', every {s.every_minutes} min, {'active' if s.active else 'paused'}.\n"
        f"Status {s.status}; last run {s.last_run_at.isoformat() if s.last_run_at else 'never'}; files seen {st.get('files_seen', 0)}, new last time {st.get('last_new', 0)}, runs {st.get('runs', 0)}.\n"
        f"Rows: {len(hr)} ({months or 'none'}); needing a person: {sum(1 for r in hr if r.status == 'review')}.\n"
        + (f"Last error: {s.last_error}" if s.last_error else "No errors.")
    )


@tool("run_harvest", "change", "Scan a Harvest source's folder now and read any new PDFs.", {"name": "source name or id"})
def run_harvest(db: Session, me: User, fix: Optional[Fix], name: str = "", **_) -> str:
    from ..pdfstudio import harvest as H

    s, rows = _find_source(db, me, name)
    if s is None:
        return "Which source? " + (", ".join(f"'{x.name}'" for x in rows) or "there are none yet")
    if H.is_running(s.id):
        return f"'{s.name}' is being harvested right now."
    sid, sname = s.id, s.name
    db.commit()
    started = H.submit_run(sid)
    log(db, "harvest_run_requested", actor=_cexa(db), target=f"source:{sid}", detail=f"asked by {me.name}")
    return f"Harvesting '{sname}' now. New PDFs become rows on PDF Studio → Harvest within a few minutes." if started else f"'{sname}' is already running."


@tool("rerun_harvest", "change", "Re-read a Harvest source's stored PDFs in a date range with the template's current profile (earlier values are kept in each row's history).", {"name": "source name or id", "from": "start date, e.g. 2026-06-01", "to": "end date, e.g. 2026-06-30"})
def rerun_harvest(db: Session, me: User, fix: Optional[Fix], name: str = "", **kw) -> str:
    from ..pdfstudio import harvest as H

    s, rows = _find_source(db, me, name)
    if s is None:
        return "Which source? " + (", ".join(f"'{x.name}'" for x in rows) or "there are none yet")
    try:
        d1, d2 = H.parse_range(kw.get("from") or kw.get("frm") or "", kw.get("to") or "")
    except H.HarvestError as e:
        return str(e)
    n = H.rows_in_range(db, s, d1, d2).count()
    if not n:
        return f"'{s.name}' has no rows between {d1 or 'the start'} and {d2 or 'today'}."
    if H.is_running(s.id):
        return f"'{s.name}' is being harvested right now — try again in a minute."
    sid, sname, uid = s.id, s.name, me.id
    db.commit()

    def job():
        from ..db import session_scope
        from ..models import HarvestSource, User as U

        with session_scope() as sdb:
            src = sdb.get(HarvestSource, sid)
            try:
                H.rerun_range(sdb, src, d1, d2, sdb.get(U, uid))
            except H.HarvestError as e:
                src = sdb.get(HarvestSource, sid)
                src.status, src.last_error = "error", str(e)

    from .. import jobs

    jobs.submit(job)
    log(db, "harvest_rerun_requested", actor=_cexa(db), target=f"source:{sid}", detail=f"{n} rows {d1} – {d2}, asked by {me.name}")
    return f"Re-reading {n} row{'s' if n != 1 else ''} of '{sname}' ({d1 or 'start'} – {d2 or 'today'}) with the current profile. Earlier values stay in each row's history."


# ---------------------------------------------------------------- code tier ---------------------
@tool("propose_code_change", "code", "When the fix needs a change to the app's code: describe it as a proposal. It is built and tested in a sandbox and only an owner can ship it — never applied directly.", {"title": "short title", "body": "what to change, which files, why", "files": "comma-separated file paths involved"})
def propose_code_change(db: Session, me: User, fix: Optional[Fix], title: str = "", body: str = "", files: str = "", **_) -> str:
    from . import developer

    # No proposals built on imagined code: every file named must exist, and C-EXA must have read it
    # in this conversation (read_code/search_code) so the plan quotes real lines, not guesses.
    tokens = (t.strip("`'\"()[]{}<>:;.,").replace("\\", "/").lstrip("./") for t in re.split(r"[,\s]+", f"{files} {body}"))
    named = {t for t in tokens if re.search(r"\.(py|js|css|html|sql)$", t)}
    missing = sorted(f for f in named if not (ROOT / f).is_file())
    if missing:
        return ("STOP: these files do not exist in the app: " + ", ".join(missing) + ". Do not invent paths or line numbers. Use search_code to find where the text really lives, read_code to see it, then propose again naming only real files. "
                "If the text is not in the code at all (e.g. it is composed at runtime from settings), say so instead of proposing.")
    read = {str(a.get("args", {}).get("path", "")).replace("\\", "/").lstrip("./") for a in (fix.actions if fix is not None and fix.actions else []) if a.get("tool") == "read_code"}
    unread = sorted(f for f in named if f not in read and f.startswith(("cbx/", "web/")))
    if unread:
        return "STOP: read these files first with read_code so the proposal quotes the real lines: " + ", ".join(unread)
    p = developer.propose(db, title[:200] or "Code fix", "owner" if me.is_owner else "requests", f"Reported by {me.name} in chat. Files: {', '.join(sorted(named)) or files}", body, priority=30)
    if p is None:
        return "A proposal with that title already exists (or was declined recently). Check C-EXA as developer."
    if fix is not None:
        fix.proposal_id = p.id
        fix.kind = "code"
        fix.status = "code_pending"
    log(db, "proposal_from_fix", actor=_cexa(db), target=f"proposal:{p.id}", detail=title)
    return f"Proposal #{p.id} '{p.title}' created on the C-EXA-as-developer board (Noticed). An owner can say 'Go ahead' and I'll build and test it in a sandbox."


# ---------------------------------------------------------------- board & everything else --------
@tool("list_proposals", "read", "List what is on the C-EXA-as-developer board (Noticed / Building / For sign-off).", {"status": "optional: noticed | building | signoff | shipped | all"})
def list_proposals(db: Session, me: User, fix: Optional[Fix], status: str = "", **_) -> str:
    q = db.query(Proposal)
    if status and status != "all":
        q = q.filter(Proposal.status == status)
    elif not status:
        q = q.filter(Proposal.status.in_(["noticed", "building", "signoff"]))
    rows = q.order_by(Proposal.id.desc()).limit(40).all()
    if not rows:
        return "The board is empty."
    return "\n".join(f"#{p.id} [{p.status}] {p.title} — {p.evidence_kind}" for p in rows)


@tool("withdraw_proposals", "change", "Take proposals off the board — mine or anyone's, at any stage before shipping. A build in progress is abandoned. Use ids, or 'all' for every open one, or 'mine' for the ones I noticed myself.", {"which": "comma-separated ids, or all, or mine"})
def withdraw_proposals(db: Session, me: User, fix: Optional[Fix], which: str = "", **_) -> str:
    from . import developer

    which = (which or "").strip().lower()
    q = db.query(Proposal).filter(Proposal.status.in_(["noticed", "building", "signoff"]))
    if which == "all":
        rows = q.all()
    elif which == "mine":
        rows = q.filter(Proposal.evidence_kind != "owner").all()
    else:
        ids = [int(x) for x in re.findall(r"\d+", which)]
        if not ids:
            return "Which ones? Give ids, or say all / mine. " + list_proposals(db, me, fix)
        rows = q.filter(Proposal.id.in_(ids)).all()
    if not rows:
        return "Nothing open matched."
    for p in rows:
        developer.not_now(db, p, me)
    log(db, "proposals_withdrawn", actor=_cexa(db), target="board", detail=f"{len(rows)} withdrawn for {me.name}: " + ", ".join(f"#{p.id}" for p in rows))
    return f"Took {len(rows)} off the board: " + "; ".join(f"#{p.id} {p.title}" for p in rows) + ". Builds in progress are abandoned; the sandboxes free up when they finish."


@tool("list_endpoints", "read", "List the app's own API endpoints (what every button in CBX calls), optionally filtered by a word, each with what happens when I call it: (I do it) · (waits for the owner's tap) · (never mine). Use before call_api.", {"search": "optional word, e.g. users, warehouse, support, requests, tasks"})
def list_endpoints(db: Session, me: User, fix: Optional[Fix], search: str = "", **_) -> str:
    from ..main import app
    from . import policy as P

    out = []
    tag = {P.DO: "", P.ASK: " (waits for the owner's tap)", P.NEVER: " (never mine)"}
    for path, ops in sorted(app.openapi().get("paths", {}).items()):
        if not path.startswith("/api/"):
            continue
        if search and search.lower() not in path.lower():
            continue
        methods = sorted(m.upper() for m in ops if m in ("get", "post", "put", "patch", "delete"))
        changes = [m for m in methods if m != "GET"]
        verdicts = {P.decide(m, re.sub(r"\{[^}]+\}", "1", path))[0] for m in changes}
        note = " (some calls wait for the owner's tap)" if P.ASK in verdicts and len(verdicts) > 1 else tag[P.ASK] if verdicts == {P.ASK} else tag[P.NEVER] if verdicts == {P.NEVER} else ""
        out.append(f"{'/'.join(methods)} {path}{note}")
    return "\n".join(out[:150]) or "Nothing matched."


def _api_blocked(method: str, path: str) -> Optional[str]:
    """Kept for older callers: why a call is not hers to make alone (None when it is)."""
    from . import policy as P

    verdict, why = P.decide(method, path)
    return None if verdict == P.DO else why


def api(db: Session, me: User, method: str, path: str, payload: Any = None, *, why: str = "", tap: bool = False, asked_by: str = "", limit: int = 1500) -> str:
    """One call of the app's own API for `me`, decided by cbx/cexa/policy.py: done now (as `me`, through a `cexa-`
    session), queued for the owner's tap, or refused. `tap` = the owner pressed Approve: it runs through a session
    that is a person's. Passwords, keys and codes in the answer go to `me` in Messages, never into the model."""
    from fastapi.testclient import TestClient

    from ..main import app
    from ..models import LoginSession
    from ..security.auth import SESSION_COOKIE
    from . import policy as P

    method = (method or "GET").upper()
    agent = AGENT_WORK.get() is not None  # 1.0.114: called from an agent's work (cbx/cexa/loop.py), not a person's chat
    verdict, reason = P.decide(method, path, payload, agent=agent)
    if me.is_axl and verdict != P.DO and not tap:
        # 1.0.114: AXL's service identity (a schedule's or a problem's goal) does routine work only. A tap waits for the
        # owner as always and then runs as him; what is never hers stays never hers.
        if verdict == P.NEVER:
            return f"I can't do that one — {reason}. A person has to do it."
        return _ask_owner(db, me, method, path, payload, why or reason, asked_by or ai_name())
    if verdict == P.NEVER:
        return f"I can't do that one — {reason}. {me.name.split()[0]} has to do it."
    if verdict == P.ASK and not tap:
        return _ask_owner(db, me, method, path, payload, why or reason, asked_by)
    sess = LoginSession(id=("tap-" if tap else "cexa-a-" if agent else "cexa-") + secrets.token_urlsafe(24), user_id=me.id, ip="cexa",
                        agent="C-EXA hands" + (" · approved by the owner" if tap else " · an agent's work" if agent else ""))
    db.add(sess)
    db.commit()
    try:
        c = TestClient(app, raise_server_exceptions=False)  # no `with`: never re-run the app's startup
        c.cookies.set(SESSION_COOKIE, sess.id)
        r = c.request(method, path, json=payload)
        found: list = []
        try:
            j = P.redact(r.json(), found)
            text = json.dumps(j)[:limit] if not isinstance(j, dict) or "detail" not in j else str(j["detail"])
        except ValueError:
            text = r.text[:limit]
        if found:
            _hand_over(db, me, path, found)
            text += " (Passwords or codes in the answer went to " + me.name.split()[0] + " in Messages.)"
        return f"{method} {path} → {r.status_code}. {text}"
    finally:
        sess.revoked = True
        db.commit()
        log(db, "axl_api_call", actor=_cexa(db), target=path, detail=f"{method} on behalf of {me.name}" + (" — approved by the owner" if tap else "") + (f" body {json.dumps(payload)[:120]}" if payload else ""))


# 1.0.114: set by the agents' loop while it runs a tool for a piece of work (its id) — policy and require_human then treat
# the call as an agent's, never a person's chat
AGENT_WORK: "contextvars.ContextVar[Optional[int]]" = contextvars.ContextVar("cexa_agent_work", default=None)


def _ask_owner(db: Session, me: User, method: str, path: str, payload: Any, why: str, asked_by: str) -> str:
    from ..models import AgentWork
    from .loop import WAITING, tell_owner

    body = payload if isinstance(payload, (dict, list)) else None
    same = [w for w in db.query(AgentWork).filter(AgentWork.kind == "api", AgentWork.status == WAITING).all()
            if (w.proposal or {}).get("method") == method and (w.proposal or {}).get("path") == path and (w.proposal or {}).get("body") == body]
    if same:
        return f"That is already waiting for the owner's tap (#{same[0].id})."
    title = (why or f"{method} {path}").strip()[:300]
    w = AgentWork(kind="api", title=title, detail=f"{method} {path}" + (f"\n{json.dumps(body, ensure_ascii=False)[:1500]}" if body else ""), status=WAITING,
                  from_label=asked_by or ai_name(), source="chat", proposal={"method": method, "path": path, "body": body, "asked_by_id": me.id, "why": why},
                  question=f"{title} — {method} {path}", options=["Approve", "Not now"])
    db.add(w)
    db.flush()
    tell_owner(db, w)
    log(db, "cexa_asked_owner", actor=_cexa(db), target=path, detail=f"{method} for {me.name}: {title[:150]}")
    return f"That one waits for the owner's tap — it is on C-EXA → Jobs & teams → For you (#{w.id}). It runs the moment it is approved."


def _hand_over(db: Session, me: User, path: str, found: list) -> None:
    lines = "\n".join(f"{k.replace('_', ' ')}: {v}" for k, v in found[:6])
    db.add(CexaMessage(user_id=me.id, from_axl=True, kind="needs_you", title="Hand this over", text=f"From {path}:\n{lines}\nGive it to the person yourself — it is not kept anywhere else."[:1500]))


@tool("call_api", "change", "Do anything in CBX by calling the app's own API as the person I'm talking to: people (add a plain user, change details, reset a password), files and folders in the warehouse, tickets and support, requests, Builder drafts and releases, work areas, PDF Studio, To-do, teams, mail drafts — every button in the UI is one of these. Routine calls I make myself; giving rights or access, disabling people, deleting, promoting and mail that leaves the building wait for the owner's one tap (I say so). Find the path with list_endpoints first.", {"method": "GET | POST | PATCH | PUT | DELETE", "path": "e.g. /api/requests/12/approve", "body": "JSON object as text, or empty", "why": "what this does, in plain words — the owner reads it if it needs his tap"})
def call_api(db: Session, me: User, fix: Optional[Fix], method: str = "GET", path: str = "", body: str = "", why: str = "", **_) -> str:
    if not path.startswith("/api/") or ".." in path:
        return "Give me an /api/... path (see list_endpoints)."
    try:
        payload = json.loads(body) if body and body.strip() else None
    except ValueError:
        return "The body must be a JSON object, e.g. {\"note\": \"...\"}."
    return api(db, me, method, path, payload, why=why)


@tool("model_health", "read", "Why am I slow? Check my own model: is Ollama up, is the model fully on the GPU or partly on the CPU, the context window in use, and average call time per task over the last 24 hours.", {})
def model_health(db: Session, me: User, fix: Optional[Fix], **_) -> str:
    from .gateway import model_health_text

    return model_health_text(db)


def _cexa(db: Session) -> Optional[User]:
    return db.query(User).filter(User.is_axl.is_(True)).one_or_none()


# ---------------------------------------------------------------- runner ------------------------
def _arg_text(v: Any) -> str:
    """Tool arguments are strings. The model sometimes sends numbers, booleans or whole objects
    (e.g. a JSON body for call_api) — keep those readable and parseable rather than Python reprs."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    return str(v)


def run(db: Session, me: User, name: str, args: Any, fix: Optional[Fix]) -> str:
    """Run one tool. Never raises for a bad tool name or bad arguments — the model reads the
    result and can correct itself; a crash here would take the whole conversation down."""
    name = str(name or "").strip()
    spec = TOOLS.get(name)
    if spec is None:
        result = f"I don't have a tool called '{name}'. Tools I do have: " + ", ".join(TOOLS)
        log(db, "axl_action", actor=_cexa(db), target=name or "?", detail="unknown tool", outcome="failed")
        return result
    if spec["tier"] in ("change", "code") and not me.is_admin:
        return "Only admins and owners can ask me to change things."
    if not isinstance(args, dict):
        # a bare value for a one-argument tool is obvious enough; anything else is dropped
        args = {next(iter(spec["args"])): args} if args not in (None, "", [], {}) and len(spec["args"]) == 1 else {}
    clean = {str(k): _arg_text(v) for k, v in args.items() if str(k) in spec["args"]}
    try:
        result = spec["fn"](db, me, fix, **clean)
    except Exception as e:  # noqa: BLE001
        result = f"That didn't work: {e}"
    if fix is not None:
        acts = list(fix.actions or [])
        acts.append({"at": utcnow().isoformat(), "tool": name, "args": clean, "result": result[:600]})
        fix.actions = acts
        if fix.status == "open" and spec["tier"] != "read":
            fix.status = "investigating"
    log(db, "axl_action", actor=_cexa(db), target=name, detail=json.dumps(clean)[:200] + " → " + result[:120], outcome="ok")
    db.flush()
    return result


# C-EXA's sandbox hands register their tools here too (cbx/cexa/hands.py, cbx/cexa/pulse.py). Imported last,
# after `tool` and TOOLS exist, so the registration order is: the tools above, then the hands.
from . import hands  # noqa: E402,F401
from . import pulse  # noqa: E402,F401
from . import tools_engine  # noqa: E402,F401 — the Builder tools' rows and drafts (1.0.61)
from . import web  # noqa: E402,F401 — web search and read (1.0.62)
from . import loop  # noqa: E402,F401 — the agents' loop; hand_to_agent, agents_now, calculate, look_at_models (1.0.62)
from ..voice import calls as _calls  # noqa: E402,F401 — call_person (1.0.64)
from . import tools_ops  # noqa: E402,F401 — people and files (1.0.81)
from . import tools_builder  # noqa: E402,F401 — build, change, test, deploy tools (1.0.82)
