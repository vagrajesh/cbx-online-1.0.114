"""C-EXA as developer. She works *around* the system: reads requests, the audit trail, errors and
corrections (notice), comments on the Develop board (review_board), and builds approved proposals
in a sandbox copy of the app (build) where the tests must pass before the owner sees a diff.

Nothing here touches the live tree. Shipping goes through cbx.versions.injector and a person.
"""
from __future__ import annotations

import difflib
import re
import shutil
import subprocess
from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from .. import jobs
from ..audit import log
from ..config import ROOT, settings
from ..db import SessionLocal, utcnow
from ..models import AuditLog, CexaMessage, Batch, JournalEntry, ModelCall, Profile, Proposal, Request, StoredFile, Task, TaskNote, Ticket, User
from . import agents
from .brain import cexa_user, system_prompt
from .gateway import Gateway, ModelError
from .team import CEXA_CAN, remember, staff

STOP = set("the a an and or of to for in on at by with from is are be we i it this that as our my your can could would should please need needs want wants like every each all any some when what which who how do does did have has had not no yes so if then than into out up down over under again also just only more most very".split())
GATE = 0.99


# ---- helpers ---------------------------------------------------------------------------------------


def keywords(text: str) -> set[str]:
    words = re.findall(r"[a-z][a-z0-9-]{3,}", (text or "").lower())
    return {w for w in words if w not in STOP}


def slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return (s[:40] or "change").strip("-")


def dedupe_of(title: str) -> str:
    """A stable 'same problem' key: the volatile counts are stripped ("… (18× this week)" ≡ "… (22×
    this week)", "11 batches failed" ≡ "3 batches failed", "30% of files" ≡ "80% of files") so noticing
    the same problem again with a bigger count still matches the open card. Other numbers (an account
    name, a request number) stay — different numbers, different problems."""
    t = (title or "").lower()
    t = re.sub(r"\(\s*\d+\s*×[^)]*\)", " ", t)   # "(18× this week)"
    t = re.sub(r"\d+\s*%", " ", t)                # "30%"
    t = re.sub(r"\b\d+\s*×\b", " ", t)            # "18×"
    return slugify(t)[:160]


def _open_with_title(db: Session, title: str) -> Optional[Proposal]:
    key = dedupe_of(title)
    if key:
        hit = db.query(Proposal).filter(Proposal.dedupe_key == key, Proposal.status.in_(["noticed", "building", "signoff"])).first()
        if hit is not None:
            return hit
    return db.query(Proposal).filter(Proposal.title == title, Proposal.status.in_(["noticed", "building", "signoff"])).first()


def _recently_declined(db: Session, title: str, days: int = 30) -> bool:
    since = utcnow() - timedelta(days=days)
    return db.query(JournalEntry).filter(JournalEntry.kind == "plan", JournalEntry.subject == f"declined:{slugify(title)}", JournalEntry.at >= since).count() > 0


def propose(db: Session, title: str, evidence_kind: str, evidence: str, body: str, *, priority: int = 50) -> Optional[Proposal]:
    """Create a Proposal in Noticed unless one with the same title is open or was declined recently."""
    title = title.strip()[:200]
    if _open_with_title(db, title) or _recently_declined(db, title):
        return None
    p = Proposal(title=title, evidence_kind=evidence_kind, evidence=evidence[:4000], body=body[:4000], status="noticed", priority=priority, dedupe_key=dedupe_of(title))
    db.add(p)
    db.flush()
    log(db, "proposal_noticed", actor=cexa_user(db), target=f"proposal:{p.id}", detail=title)
    return p


# ---- notice ------------------------------------------------------------------------------------------


def notice(db: Session) -> list[Proposal]:
    """Read the evidence and park ideas in Noticed. Deterministic, idempotent."""
    created: list[Proposal] = []
    created += _notice_requests(db)
    created += _notice_audit(db)
    created += _notice_errors(db)
    created += _notice_corrections(db)
    return created


def _notice_requests(db: Session) -> list[Proposal]:
    """One proposal per group of requests that ask for the same thing in different words. A request and
    its own ticket are the same work, never a pair; a group is proposed once ever (any status)."""
    since = utcnow() - timedelta(days=90)
    items: list[tuple[str, str, set[str]]] = []
    reqs = db.query(Request).filter(Request.created_at >= since, Request.status.notin_(["declined", "cancelled"])).all()
    with_ticket = {r.id for r in reqs}
    for r in reqs:
        items.append((f"#{r.number}", r.title or r.problem[:60], keywords(f"{r.title} {r.problem} {r.wish}")))
    for t in db.query(Ticket).filter(Ticket.created_at >= since).all():
        if t.request_id in with_ticket:
            continue  # already represented by its request
        items.append((f"ticket {t.id}", t.title, keywords(f"{t.title} {t.summary}")))
    out: list[Proposal] = []
    used: set[str] = set()
    for i, (ia, ta, ka) in enumerate(items):
        if ia in used:
            continue
        group = [(ia, ta)]
        shared: Counter = Counter()
        for ib, tb, kb in items[i + 1:]:
            common = ka & kb
            if len(common) >= 2 and ib not in used:
                group.append((ib, tb))
                shared.update(common)
        if len(group) < 2:
            continue
        for ref, _ in group:
            used.add(ref)
        key = "group: " + ", ".join(sorted(ref for ref, _ in group))
        if db.query(Proposal).filter(Proposal.evidence.like(key + "%")).first():
            continue
        top = sorted(w for w, _ in shared.most_common(3))
        title = f"{len(group)} people asked for {' '.join(top[:2])} in different words"
        evidence = key + "\n" + "\n".join(f"{ref}: {t}" for ref, t in group)
        body = f"{', '.join(ref for ref, _ in group)} all touch “{' / '.join(top)}”. One build would close them together instead of {len(group)} separate ones."
        p = propose(db, title, "requests", evidence, body, priority=60)
        if p:
            out.append(p)
    return out


def _notice_audit(db: Session) -> list[Proposal]:
    out: list[Proposal] = []
    since = utcnow() - timedelta(days=7)
    rows = db.query(AuditLog).filter(AuditLog.at >= since, AuditLog.action.in_(["tool_denied", "login_failed"])).all()
    by: Counter = Counter((a.action, a.actor_label) for a in rows)
    for (action, who), n in by.most_common(5):
        if n >= 3:
            if action == "tool_denied":
                title = f"{who} keeps hitting a tool they don't have ({n}× this week)"
                body = f"{who} was refused {n} times in 7 days. Either they need the tool (an owner grants it — I can't) or the link shouldn't show for them. I can hide it."
            else:
                title = f"Repeated failed sign-ins for {who} ({n}× this week)"
                body = f"{n} failed attempts in 7 days. Worth a temp password and a word — or, if it's not them, worth knowing."
            p = propose(db, title, "audit", f"{action} × {n} for {who} since {since:%d %b}", body, priority=55)
            if p:
                out.append(p)
    recent = db.query(StoredFile).filter(StoredFile.created_at >= utcnow() - timedelta(days=30), StoredFile.source == "upload", StoredFile.library_copy_of.is_(None)).all()
    if len(recent) >= 5:
        unnamed = [f for f in recent if re.match(r"^(scan|img|image|doc|document|file|untitled|new ?doc|[0-9]+)[ _-]?[0-9]*\.[a-z]{3,4}$", f.name.lower())]
        share = len(unnamed) / len(recent)
        if share > 0.30:
            title = f"Warehouse inbox: {int(share * 100)}% of files arrive unnamed"
            body = f"{len(unnamed)} of the last {len(recent)} uploads are called things like “{unnamed[0].name}”. I can name files from their content (municipality · account · period) on arrival."
            p = propose(db, title, "audit", "\n".join(f.name for f in unnamed[:10]), body, priority=50)
            if p:
                out.append(p)
    return out


def _notice_errors(db: Session) -> list[Proposal]:
    out: list[Proposal] = []
    since = utcnow() - timedelta(days=7)
    bad_batches = db.query(Batch).filter(Batch.created_at >= since, Batch.error != "").all()
    if bad_batches:
        msgs = Counter(b.error.split(":")[0][:80] for b in bad_batches)
        msg, n = msgs.most_common(1)[0]
        title = f"{len(bad_batches)} batch{'es' if len(bad_batches) != 1 else ''} failed this week — mostly “{msg}”"
        body = f"Same failure {n} time{'s' if n != 1 else ''}. I'd like to add a guard for it and a clearer message so the person knows what to do."
        p = propose(db, title, "errors", "\n".join(f"batch {b.id} ({b.name}): {b.error[:120]}" for b in bad_batches[:8]), body, priority=70)
        if p:
            out.append(p)
    bad_calls = db.query(ModelCall).filter(ModelCall.at >= since, ModelCall.ok.is_(False)).all()
    if len(bad_calls) >= 3:
        by_task = Counter(c.task for c in bad_calls).most_common(1)[0]
        title = f"Model calls failing on “{by_task[0]}” ({by_task[1]}× this week)"
        body = "Most of the failures are the same task. Either the prompt is too long for the context window or the model is going away mid-call; I'd add a retry with a smaller prompt and log the size."
        p = propose(db, title, "errors", f"{len(bad_calls)} failed calls since {since:%d %b}", body, priority=65)
        if p:
            out.append(p)
    return out


def _notice_corrections(db: Session) -> list[Proposal]:
    out: list[Proposal] = []
    cutoff = utcnow() - timedelta(days=14)
    for prof in db.query(Profile).filter(Profile.status == "below_gate", Profile.created_at <= cutoff).all():
        owner = None
        if prof.template_id:
            from ..models import Template

            t = db.get(Template, prof.template_id)
            owner = db.get(User, t.owner_id) if t else None
        who = owner.name.split()[0] if owner else "the team"
        acc = f"{prof.accuracy * 100:.1f}%" if prof.accuracy is not None else "below the gate"
        title = f"{prof.label} profile stuck at {acc}"
        body = f"Below the {int(GATE * 100)}% gate for more than two weeks. I need more labelled documents — shall I ask {who}?"
        p = propose(db, title, "corrections", f"profile {prof.key}: {prof.docs_seen} docs seen, accuracy {acc}", body, priority=45)
        if p:
            out.append(p)
    return out


# ---- board review -----------------------------------------------------------------------------------------


def _note_once(db: Session, task: Task, text: str) -> Optional[TaskNote]:
    if any(n.by == "axl" and n.text == text for n in task.notes):
        return None
    n = TaskNote(task_id=task.id, by="axl", by_id=cexa_user(db).id, text=text)
    db.add(n)
    db.flush()
    db.refresh(task)
    return n


def review_board(db: Session) -> list[TaskNote]:
    """Look at every open card once; comment when there is something useful to say. Same text is
    never posted twice on one card."""
    now = utcnow()
    notes: list[TaskNote] = []
    model_budget = 3
    proposals = db.query(Proposal).filter(Proposal.status.in_(["noticed", "building", "signoff"])).all()
    for task in db.query(Task).filter(Task.status != "done").all():
        if task.assignee_axl:
            continue
        # overdue → offer help
        if task.due and task.due < now:
            days = (now - task.due).days
            who = "you" if task.assignee_id else "whoever picks this up"
            text = f"This is {days} day{'s' if days != 1 else ''} late. Want me to take a piece of it, or is it waiting on something I can chase?" if days else f"Due today — shout if {who} need{'s' if who != 'you' else ''} a hand."
            n = _note_once(db, task, text)
            if n:
                notes.append(n)
        # duplicate of a proposal → merge
        tk = keywords(task.title)
        for p in proposals:
            ratio = difflib.SequenceMatcher(None, task.title.lower(), p.title.lower()).ratio()
            if (ratio >= 0.6 or len(tk & keywords(p.title)) >= 2) and task.origin_ref != f"proposal:{p.id}":
                task.origin_ref = f"proposal:{p.id}"
                if task.origin == "manual":
                    task.origin = "proposal"
                n = _note_once(db, task, "Same as my proposal — merging so it isn't done twice. It'll move with the proposal on the C-EXA as developer board.")
                if n:
                    notes.append(n)
                break
        # something C-EXA can do herself, unassigned → offer to take it
        if not task.assignee_id and CEXA_CAN.search(task.title):
            text = "I can do this one myself — say “Take it” on the card and I'll start."
            n = _note_once(db, task, text)
            if n:
                notes.append(n)
                mine = propose(db, task.title, "owner", f"task:{task.id}", f"From the Develop board: “{task.title}”. Unassigned and the kind of thing I can do. I'll take it if you say go.", priority=40)
                if mine:
                    mine.task_id = task.id
                    task.origin_ref = f"proposal:{mine.id}"  # so the duplicate check above doesn't merge it with itself
        # live mode: ask the model for one more observation, at most once per card per day — and only a
        # few cards per tick, so one slow model does not hold the scheduler (and the morning brief) for an hour
        if settings.model_mode != "stub" and model_budget > 0:
            key = f"review:{task.id}:{now.date().isoformat()}"
            if not db.query(JournalEntry).filter(JournalEntry.kind == "plan", JournalEntry.subject == key).count():
                model_budget -= 1
                db.add(JournalEntry(kind="plan", subject=key, text="reviewed"))
                db.commit()  # don't hold the write lock while the model thinks about this card
                try:
                    out = Gateway(db).json(task="dev", system=system_prompt("You are reviewing one card on the team's board. Reply with a short helpful comment (or empty string if nothing useful) and whether you could do the task yourself."), user=f"Title: {task.title}\nDetail: {task.detail}\nStatus: {task.status}\nNotes: {[n.text for n in task.notes][-3:]}", schema={"comment": "string", "take": "bool"})
                    if out.get("comment"):
                        n = _note_once(db, task, str(out["comment"])[:600])
                        if n:
                            notes.append(n)
                except ModelError:
                    pass
    db.flush()
    return notes


# ---- lifecycle --------------------------------------------------------------------------------------


def go(db: Session, p: Proposal, by: User) -> Proposal:
    """Owner said go: create the board card, mark building, start the sandbox build."""
    if p.status not in ("noticed", "declined"):
        raise ValueError(f"Proposal is already {p.status}.")
    from ..versions.injector import SANDBOX_SLOTS, free_slot

    from ..models import AppVersion

    slot = free_slot(db)
    busy = {q.sandbox for q in db.query(Proposal).filter(Proposal.status.in_(["building", "signoff"]), Proposal.id != p.id).all()}
    busy |= {v.env for v in db.query(AppVersion).filter(AppVersion.status.in_(["staged", "sandbox"])).all()}
    if slot in busy or slot not in SANDBOX_SLOTS:
        # free_slot falls back to the first slot when every one is taken — never stack two builds on one sandbox
        raise ValueError("Both sandboxes are in use (a build in progress or a proposal waiting for sign-off). Promote or say Not now to one of them first, then say go again.")
    p.status = "building"
    p.decided_by_id = by.id
    p.branch = f"cexa/{slugify(p.title)}-{p.id}"
    p.sandbox = slot
    p.tests = {}  # a retry starts clean — the old verdict must not show as this build's
    p.diff = ""
    if not p.task_id:
        t = Task(title=p.title, detail=p.body, status="working", assignee_axl=True, origin="proposal", origin_ref=f"proposal:{p.id}", created_by_id=by.id, started_at=utcnow(), trace=[{"at": utcnow().isoformat(), "by": "axl", "from": "", "to": "working"}])
        db.add(t)
        db.flush()
        p.task_id = t.id
    elif (t := db.get(Task, p.task_id)) and t.status != "working":
        # a retry after a failed build or a Not now: the card comes back to Working
        t.status = "working"
        t.done_at = None
        t.trace = list(t.trace or []) + [{"at": utcnow().isoformat(), "by": "axl", "from": "", "to": "working", "note": "go again"}]
    db.flush()
    log(db, "proposal_go", actor=by, target=f"proposal:{p.id}", detail=p.title)
    db.commit()
    jobs.submit(build_job, p.id)
    return p


def not_now(db: Session, p: Proposal, by: User) -> Proposal:
    """Decline (or withdraw) a proposal at any stage. A build in progress is abandoned: build_job checks
    the status before writing anything to the sandbox and before moving to sign-off."""
    p.status = "declined"
    p.decided_by_id = by.id
    db.add(JournalEntry(kind="plan", subject=f"declined:{slugify(p.title)}", text=f"{by.name} said not now to “{p.title}”; don't re-propose for 30 days."))
    if p.task_id:
        t = db.get(Task, p.task_id)
        if t and t.status != "done":
            t.status = "done"
            t.done_at = utcnow()
            t.trace = list(t.trace or []) + [{"at": utcnow().isoformat(), "by": by.email, "from": "working", "to": "done", "note": "declined"}]
    db.flush()
    log(db, "proposal_declined", actor=by, target=f"proposal:{p.id}", detail=p.title)
    return p


def build_job(proposal_id: int) -> None:
    db = SessionLocal()
    try:
        p = db.get(Proposal, proposal_id)
        if p:
            build(db, p)
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        p = db.get(Proposal, proposal_id)
        if p is not None:
            _build_failed(db, p, f"{type(e).__name__}: {e}")
            db.commit()
        raise
    finally:
        db.close()


def _build_failed(db: Session, p: Proposal, why: str) -> None:
    """A build that died (exception, killed process) must not sit in Building forever holding a
    sandbox: it goes back to Noticed with the failure on the card so the owner can say go again."""
    if p.status != "building":
        return
    p.status = "noticed"
    p.tests = {"passed": 0, "failed": 1, "new": 0, "ok": False, "output": why[:2000]}
    p.body = (p.body.split("\n\nBuild failed")[0] + f"\n\nBuild failed: {why[:600]}\nSay go again and I'll retry.")[:4000]
    if p.task_id and (t := db.get(Task, p.task_id)) and t.status != "done":
        t.status = "todo"
        db.add(TaskNote(task_id=t.id, by="axl", text=f"The sandbox build failed ({why[:200]}). Back to Noticed — say go again when you want me to retry."))
    log(db, "proposal_build_failed", actor=cexa_user(db), target=f"proposal:{p.id}", detail=why[:200], outcome="failed")


STUCK_AFTER = timedelta(hours=2)


def recover_stuck_builds(db: Session, now=None) -> list[Proposal]:
    """Proposals left in Building with no dev agent working on them (the process was restarted or the
    job thread died) are handed back to Noticed. Runs from the board routine."""
    now = now or utcnow()
    out = []
    busy_refs = {a["ref"] for a in agents.snapshot()["agents"] if a["state"] == "busy"}
    for p in db.query(Proposal).filter(Proposal.status == "building").all():
        if f"proposal:{p.id}" in busy_refs:
            continue
        if p.tests:  # the build did finish (tests failed) — that one waits for the owner's Stop, by design
            continue
        if (p.updated_at or p.created_at or now) > now - STUCK_AFTER:
            continue
        _build_failed(db, p, "the build stopped without finishing (server restarted or the job died)")
        out.append(p)
    db.flush()
    return out


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=120)


def _have_git() -> bool:
    return shutil.which("git") is not None


def _rmtree(path: Path) -> None:
    """rmtree that copes with Windows: git writes read-only pack files, which plain rmtree refuses to
    delete (PermissionError) — clear the bit and retry."""
    import os
    import stat

    def _onerror(fn, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            fn(p)
        except OSError:
            pass

    shutil.rmtree(path, onerror=_onerror)
    if path.exists():
        raise OSError(f"could not remove the old sandbox at {path} — is something still using it?")


def copy_live_tree(dest: Path) -> Path:
    """The app as it runs — cbx/, web/, tests/, docs/, tools/, requirements, VERSION, README — copied to `dest`
    (emptied first). Never data/, never .env, never the bat files."""
    if dest.exists():
        _rmtree(dest)
    dest.mkdir(parents=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", "data", ".git", "node_modules")
    for name in ("cbx", "web", "tests", "docs", "tools"):
        if (ROOT / name).is_dir():
            shutil.copytree(ROOT / name, dest / name, ignore=ignore)
    for f in ("requirements.txt", "VERSION", "README.md"):
        if (ROOT / f).is_file():
            shutil.copy2(ROOT / f, dest / f)
    (dest / ".gitignore").write_text("__pycache__/\n*.pyc\n.pytest_cache/\ndata/\n", encoding="utf-8")
    return dest


def make_playground(slot: str) -> Path:
    """A sandbox with no proposal behind it: the live app as it is, in DATA_DIR/sandboxes/playground-<slot>,
    for the owner to open on its own port and empty database and simply look around. Nothing C-EXA does
    here is a change to anything; stopping it is the end of it."""
    return copy_live_tree(settings.path("sandboxes") / f"playground-{slot}")


def make_sandbox(p: Proposal) -> Path:
    """A copy of the app in DATA_DIR/sandboxes/<slug>. If git is available the copy gets its own repo and
    a branch cexa/<slug> so the change is a real diff; otherwise the diff is computed by difflib later.
    (A worktree of HEAD would miss uncommitted work in the live tree, so the copy is of the tree as it runs.)"""
    slug = p.branch.split("/", 1)[-1] if p.branch else slugify(p.title)
    dest = copy_live_tree(settings.path("sandboxes") / slug)
    if _have_git():
        _git(["init", "-q"], dest)
        _git(["-c", "user.email=cexa@cbx.local", "-c", "user.name=C-EXA", "add", "-A"], dest)
        _git(["-c", "user.email=cexa@cbx.local", "-c", "user.name=C-EXA", "commit", "-q", "-m", "baseline (copy of live tree)"], dest)
        _git(["checkout", "-q", "-b", p.branch or f"cexa/{slug}"], dest)
    return dest


_SAFE_PREFIX = ("cbx/", "web/", "tests/", "docs/")


def _safe_path(sandbox: Path, rel: str) -> Optional[Path]:
    rel = rel.replace("\\", "/").lstrip("/")
    if not rel.startswith(_SAFE_PREFIX) or ".." in rel.split("/") or rel.lower().endswith((".bat", ".cmd", ".ps1", ".exe")):
        return None
    target = (sandbox / rel).resolve()
    if sandbox.resolve() not in target.parents:
        return None
    return target


def _write_patch(db: Session, p: Proposal, sandbox: Path) -> tuple[list[str], str, int]:
    """Ask the model for files (live) or write the harmless proposal doc (stub). Returns (paths, summary, tests_added)."""
    slug = sandbox.name
    written: list[str] = []
    summary, tests_added = "", 0
    if settings.model_mode != "stub":
        try:
            out = Gateway(db).json(task="dev", system=system_prompt("You are implementing an approved proposal in a FastAPI + vanilla-JS app. Return complete file contents for the files you change or add under cbx/, web/ or tests/. Keep changes small and add a test. Never touch run.bat or anything outside those folders."), user=f"Proposal: {p.title}\nEvidence: {p.evidence}\nPlan: {p.body}", schema={"files": [{"path": "string", "content": "string"}], "summary": "string", "tests_added": 0})
            files = out.get("files")
            if isinstance(files, dict):  # {"path": "content", ...} is a shape the model sometimes picks
                files = [{"path": k, "content": v} for k, v in files.items()]
            for f in files if isinstance(files, list) else []:
                if not isinstance(f, dict) or not isinstance(f.get("content"), str):
                    continue
                t = _safe_path(sandbox, str(f.get("path", "")))
                if t is None:
                    continue
                t.parent.mkdir(parents=True, exist_ok=True)
                t.write_text(f["content"], encoding="utf-8")
                written.append(t.relative_to(sandbox).as_posix())
            summary = str(out.get("summary") or "")
            try:
                tests_added = int(out.get("tests_added") or 0)
            except (TypeError, ValueError):
                tests_added = 0
        except (ModelError, ValueError, TypeError, AttributeError, OSError) as e:
            summary = f"Model did not produce a patch ({e}); only PROPOSAL.md written."
    else:
        doc = sandbox / "docs" / "proposals" / f"{slug}.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(f"# {p.title}\n\n{p.body}\n\nEvidence:\n\n{p.evidence}\n", encoding="utf-8")
        written.append(doc.relative_to(sandbox).as_posix())
        summary = "Stub mode: described the change in docs/proposals; no code generated."
    (sandbox / "PROPOSAL.md").write_text(f"# {p.title}\n\nBranch: {p.branch}\nSandbox: {p.sandbox}\n\n## Why\n\n{p.evidence}\n\n## What\n\n{p.body}\n\n## Files\n\n" + "\n".join(f"- {w}" for w in written) + f"\n\n{summary}\n", encoding="utf-8")
    return written, summary, tests_added


def _baseline(sandbox: Path) -> str:
    """The commit that is the copy of the live tree — every diff the owner reviews is against this,
    however many working commits C-EXA made on top."""
    r = _git(["rev-list", "--max-parents=0", "HEAD"], sandbox)
    return (r.stdout or "").strip().splitlines()[0] if r.returncode == 0 and (r.stdout or "").strip() else "HEAD"


def _commit(sandbox: Path, message: str) -> bool:
    """One working step on the sandbox branch. False when there was nothing to commit or no git."""
    if not (_have_git() and (sandbox / ".git").exists()):
        return False
    _git(["add", "-A"], sandbox)
    r = _git(["-c", "user.email=cexa@cbx.local", "-c", "user.name=C-EXA", "commit", "-q", "-m", message[:120]], sandbox)
    return r.returncode == 0


def _diff(sandbox: Path, written: list[str], limit: int = 8192) -> str:
    if _have_git() and (sandbox / ".git").exists():
        _git(["add", "-A"], sandbox)
        base = _baseline(sandbox)
        r = _git(["diff", base, "--stat=80", "--", ".", ":(exclude)PROPOSAL.md", ":(exclude).cexa"], sandbox)
        r2 = _git(["diff", base, "--", ".", ":(exclude)PROPOSAL.md", ":(exclude).cexa"], sandbox)
        text = (r.stdout or "") + "\n" + (r2.stdout or "")
    else:
        parts = []
        for rel in written:
            new = (sandbox / rel).read_text(encoding="utf-8", errors="replace").splitlines()
            old_p = ROOT / rel
            old = old_p.read_text(encoding="utf-8", errors="replace").splitlines() if old_p.exists() else []
            parts.extend(difflib.unified_diff(old, new, fromfile="live/" + rel, tofile="sandbox/" + rel, lineterm="", n=2))
        text = "\n".join(parts)
    return text[:limit] + ("\n… (truncated at 8 KB)" if len(text) > limit else "")


MAX_ROUNDS = 3   # first patch + up to two fixes after reading the failures


def _fix_round(db: Session, p: Proposal, sandbox: Path, written: list[str], res: dict, round_no: int) -> tuple[list[str], str]:
    """The developer's loop: read the failures, fix the files, try again. Returns (paths written, summary)."""
    from ..versions.injector import failed_tests

    fails = failed_tests(res.get("output") or "")
    shown = []
    for rel in written[:6]:
        f = sandbox / rel
        if f.is_file():
            txt = f.read_text(encoding="utf-8", errors="replace")
            shown.append(f"--- {rel} ---\n{txt[:6000]}" + ("\n…(cut)" if len(txt) > 6000 else ""))
    user = (
        f"Proposal: {p.title}\nPlan: {p.body[:1500]}\n\n"
        f"Round {round_no}: the tests FAILED after the last patch.\n"
        + ("Failing tests:\n" + "\n".join(fails[:12]) + "\n\n" if fails else "")
        + "pytest output (tail):\n" + (res.get("output") or "")[-3500:]
        + "\n\nThe files you wrote, as they are now:\n" + "\n\n".join(shown)
        + "\n\nReturn the corrected complete contents of only the files that must change. Fix the code or the test, whichever is wrong; never delete a failing test to make it pass."
    )
    out = Gateway(db).json(task="dev", system=system_prompt("You are fixing your own patch in a FastAPI + vanilla-JS app so its tests pass. Return complete file contents for the files you change under cbx/, web/ or tests/. Keep the change small. Never touch anything outside those folders."), user=user, schema={"files": [{"path": "string", "content": "string"}], "summary": "string"})
    files = out.get("files")
    if isinstance(files, dict):
        files = [{"path": k, "content": v} for k, v in files.items()]
    done: list[str] = []
    for f in files if isinstance(files, list) else []:
        if not isinstance(f, dict) or not isinstance(f.get("content"), str):
            continue
        t = _safe_path(sandbox, str(f.get("path", "")))
        if t is None:
            continue
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_text(f["content"], encoding="utf-8")
        done.append(t.relative_to(sandbox).as_posix())
    return done, str(out.get("summary") or "")


def build(db: Session, p: Proposal) -> Proposal:
    """Sandbox → patch → pytest → (read the failures → fix → pytest, up to MAX_ROUNDS) → signoff if green,
    else stays in building with the failure. Each round is a commit on the sandbox branch."""
    from ..versions.injector import run_tests

    cexa = cexa_user(db)
    with agents.working("dev", task=f"Build: {p.title}", ref=f"proposal:{p.id}") as a:
        a.progress(0.1, "copying the app to a sandbox")
        sandbox = make_sandbox(p)
        a.progress(0.3, "writing the change")
        written, summary, tests_added = _write_patch(db, p, sandbox)
        _commit(sandbox, "round 1: first patch")
        # The model call above logged a ModelCall row, which opened a write transaction. pytest can
        # take minutes — commit now so nobody else's request queues behind this session on SQLite.
        db.commit()
        a.progress(0.5, "running the tests")
        res = run_tests(sandbox)
        rounds = [{"round": 1, "passed": res.get("passed", 0), "failed": res.get("failed", 0), "ok": bool(res.get("ok") or res.get("skipped")), "files": list(written)}]
        for n in range(2, MAX_ROUNDS + 1):
            if rounds[-1]["ok"] or settings.model_mode == "stub":
                break
            db.refresh(p, ["status"])
            if p.status != "building":
                break
            a.progress(0.5 + 0.1 * n, f"tests failed — fixing (round {n})")
            try:
                fixed, note = _fix_round(db, p, sandbox, written, res, n)
            except (ModelError, ValueError, TypeError, AttributeError, OSError) as e:
                rounds.append({"round": n, "passed": 0, "failed": 0, "ok": False, "files": [], "note": f"could not fix: {e}"})
                break
            if not fixed:
                rounds.append({"round": n, "passed": 0, "failed": 0, "ok": False, "files": [], "note": "the model returned no files"})
                break
            written = sorted(set(written) | set(fixed))
            summary = note or summary
            _commit(sandbox, f"round {n}: fix after tests")
            db.commit()
            res = run_tests(sandbox)
            rounds.append({"round": n, "passed": res.get("passed", 0), "failed": res.get("failed", 0), "ok": bool(res.get("ok") or res.get("skipped")), "files": fixed})
        res["new"] = tests_added
        p.tests = {"passed": res.get("passed", 0), "failed": res.get("failed", 0), "new": tests_added, "ok": bool(res.get("ok") or res.get("skipped")), "seconds": res.get("seconds"), "output": (res.get("output") or "")[-2000:], "rounds": rounds, "failures": (res.get("failures") or [])[:10]}
        p.diff = _diff(sandbox, written)
        a.progress(0.9, "writing up")
        db.refresh(p, ["status"])
        if p.status != "building":  # withdrawn while we worked — keep the diff for the record, change nothing else
            a.observe(f"{p.title}: withdrawn during build")
            return p
        task = db.get(Task, p.task_id) if p.task_id else None
        if p.tests["ok"]:
            p.status = "signoff"
            body_note = f"Built in {p.sandbox}: {len(written)} file{'s' if len(written) != 1 else ''} changed, {p.tests['passed']} tests passed" + (f", {tests_added} new" if tests_added else "") + (f", green after {len(rounds)} rounds" if len(rounds) > 1 else "") + f". {summary}"
            if task:
                db.add(TaskNote(task_id=task.id, by="axl", by_id=cexa.id, text=body_note + " Waiting for sign-off."))
            for o in [u for u in staff(db) if u.is_owner]:
                db.add(CexaMessage(user_id=o.id, from_axl=True, kind="needs_you", title="Needs you", text=f"“{p.title}” is built and tested ({p.tests['passed']} passed). It's on the sign-off column when you have a minute.", link="#/cexa-dev"))
            a.observe(f"{p.title}: {p.tests['passed']} passed in {res.get('seconds')} s")
            # the owner must be able to click through the change before promoting: start the sandbox
            # instance on its own port (own throwaway database — the live data is untouchable)
            try:
                from ..versions import sandbox_runner

                owners = [u for u in staff(db) if u.is_owner and not u.is_axl]
                run = sandbox_runner.start(p.sandbox, str(sandbox), owners=[(u.email, u.name) for u in owners], kind="proposal", title=p.title)
                if run.get("ok"):
                    body_note += f" · running in {p.sandbox}"
                    for o in owners:
                        db.add(CexaMessage(user_id=o.id, from_axl=True, kind="needs_you", title="Sandbox is running", text=f"“{p.title}” is running in {p.sandbox}. Go in from Versions & sandboxes — sign in with your own email and the sandbox password shown on its card — click through, and promote only if you're happy.", link="#/versions"))
            except Exception as e:  # noqa: BLE001 — a sandbox that won't start must not fail the build
                a.observe(f"sandbox instance did not start: {e}")
        else:
            names = "\n".join((res.get("failures") or [])[:6])
            p.body = (p.body.split("\n\nTests failed")[0] + f"\n\nTests failed in the sandbox ({p.tests['failed']} failed after {len(rounds)} round{'s' if len(rounds) != 1 else ''}). I'm staying in Building until they pass.\n{names or (res.get('output') or '')[-600:]}")[:4000]
            if task:
                db.add(TaskNote(task_id=task.id, by="axl", by_id=cexa.id, text=f"Tests failed in the sandbox ({p.tests['failed']} failed). Not bringing this to you yet."))
            a.observe(f"{p.title}: tests failed")
        p.body = p.body if p.tests["ok"] else p.body
        if p.tests["ok"] and summary and summary not in p.body:
            p.body = (p.body + "\n\n" + summary)[:4000]
    db.flush()
    log(db, "proposal_built", actor=cexa, target=f"proposal:{p.id}", detail=f"{p.status}; {p.tests}", outcome="ok" if p.tests["ok"] else "failed")
    return p


def ship(db: Session, p: Proposal, by: User):
    """Owner ships: package the sandbox into a staged AppVersion (tests run again there). The proposal
    becomes `shipped` only when that version is promoted (injector.promote does that)."""
    from ..versions import injector

    if p.status != "signoff":
        raise ValueError("Only a proposal on the sign-off column can be shipped.")
    slug = p.branch.split("/", 1)[-1] if p.branch else slugify(p.title)
    sandbox = settings.path("sandboxes") / slug
    if not sandbox.exists():
        raise ValueError("The sandbox for this proposal is gone. Say go again and I'll rebuild it.")
    v = injector.package_from_dir(db, sandbox, notes=f"C-EXA: {p.title}", by=by)
    p.version_id = v.id
    db.flush()
    log(db, "proposal_shipped_to_version", actor=by, target=f"proposal:{p.id}", detail=f"version {v.tag} ({v.status})")
    return v


def proposal_out(p: Proposal, db: Optional[Session] = None) -> dict:
    d = {
        "id": p.id,
        "title": p.title,
        "evidence_kind": p.evidence_kind,
        "evidence": p.evidence,
        "body": p.body,
        "status": p.status,
        "branch": p.branch,
        "sandbox": p.sandbox,
        "diff": p.diff,
        "tests": {k: v for k, v in (p.tests or {}).items() if k != "output"},
        "test_output": (p.tests or {}).get("output", ""),
        "version_id": p.version_id,
        "task_id": p.task_id,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        "priority": p.priority,
    }
    if db is not None and p.version_id:
        from ..models import AppVersion

        v = db.get(AppVersion, p.version_id)
        d["version"] = {"id": v.id, "tag": v.tag, "status": v.status} if v else None
    return d


def board(db: Session) -> dict:
    month = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    cols = {}
    for st in ("noticed", "building", "signoff"):
        cols[st] = [proposal_out(p, db) for p in db.query(Proposal).filter(Proposal.status == st).order_by(Proposal.priority.desc(), Proposal.created_at.desc()).all()]
    cols["shipped"] = [proposal_out(p, db) for p in db.query(Proposal).filter(Proposal.status == "shipped", Proposal.updated_at >= month).order_by(Proposal.updated_at.desc()).all()]
    cols["shipped_total"] = db.query(Proposal).filter(Proposal.status == "shipped").count()
    cols["declined"] = db.query(Proposal).filter(Proposal.status == "declined").count()
    cols["branch"] = next((p.branch for p in db.query(Proposal).filter(Proposal.status.in_(["building", "signoff"])).order_by(Proposal.updated_at.desc()).limit(1)), "")
    return cols
