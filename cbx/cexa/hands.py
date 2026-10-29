"""C-EXA's hands inside a sandbox: read, search, write, patch, run tests, diff, commit, run the app.

The one rule that never changes: C-EXA never edits the live tree. Everything here works on the copy
of the app that `developer.make_sandbox` made for a proposal (data/sandboxes/<slug>, with its own git
repo and a baseline commit of the live tree). The owner still promotes; C-EXA still cannot.

What the jail is — and is not. Every path is resolved and must land inside the sandbox, under cbx/,
web/, tests/ or docs/; `..`, absolute paths, symlinks out and script extensions are refused. Every
command is a whitelisted programme with plain arguments (no shell, no `&&`, `|`, `>`); `python -c`
and `python <file>` are refused because a script can read anything the server can. The child gets a
scrubbed environment (no CBX_ settings, its own throwaway data directory) and a time limit. That is a
PATH jail: a test C-EXA writes still runs as the server's user, which is exactly what the build has
always done. A PRIVILEGE jail (a separate low-rights account, or a container) is an IT item, not a
line of Python — README says so. CBX_CEXA_HANDS=0 in .env switches every tool here off.

Every call is written to the audit trail (`cexa_hands`) and onto the Fix the conversation belongs to,
so the owner reads exactly what C-EXA did, command by command.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import tempfile
import shutil
import time
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from ..audit import log
from ..config import settings
from ..models import Fix, Proposal, User
from . import developer
from .tools import tool

SAFE_DIRS = ("cbx", "web", "tests", "docs")
SAFE_FILES = ("requirements.txt", "README.md", "VERSION")
BAD_SUFFIX = (".bat", ".cmd", ".ps1", ".exe", ".dll", ".sh", ".vbs")
TEXT_SUFFIX = (".py", ".js", ".css", ".html", ".md", ".sql", ".txt", ".json", ".yml", ".yaml", ".toml", ".cfg", ".ini")
MAX_OUT = 6000
MAX_WRITE = 400_000
MIN_TIMEOUT, MAX_TIMEOUT, DEFAULT_TIMEOUT = 10, 900, 300

# programme → the sub-commands / first arguments it may be given (None = any plain arguments)
ALLOWED: dict[str, Optional[set[str]]] = {
    "pytest": None,
    "ruff": {"check", "format", "--version"},
    "git": {"status", "diff", "log", "add", "commit", "show", "restore", "checkout", "stash", "branch"},
    "pip": {"install"},
    "python": {"-m"},           # python -m pytest / python -m ruff — never -c, never a file
}
PY_MODULES = {"pytest", "ruff", "pip"}
SHELL_CHARS = re.compile(r"[&|;<>`$\n\r]")


class HandsError(Exception):
    pass


def enabled() -> bool:
    # .env (via settings) or a real environment variable — either switches the hands off
    return bool(settings.cexa_hands) and all(os.environ.get(k, "1").strip().lower() not in ("0", "false", "no") for k in ("CBX_CEXA_HANDS", "CBX_AXL_HANDS"))


# ---- where ---------------------------------------------------------------------------------------


def resolve_sandbox(db: Session, ref: str) -> tuple[Proposal, Path]:
    """The proposal C-EXA is working on and its sandbox directory. `ref` is a slot (sandbox-a), a
    proposal id (#12 / 12) or a title fragment; empty = the one proposal being built or awaiting sign-off."""
    ref = (ref or "").strip()
    q = db.query(Proposal).filter(Proposal.status.in_(["building", "signoff"]))
    p: Optional[Proposal] = None
    if not ref:
        cands = q.order_by(Proposal.updated_at.desc()).all()
        if len(cands) == 1:
            p = cands[0]
        elif not cands:
            raise HandsError("No proposal is being built right now. Say go on a proposal first (C-EXA as developer → Noticed → Go ahead), then I have a sandbox to work in.")
        else:
            raise HandsError("More than one sandbox is in use — say which: " + ", ".join(f"#{c.id} '{c.title}' in {c.sandbox}" for c in cands))
    elif ref in developer_slots():
        p = q.filter(Proposal.sandbox == ref).order_by(Proposal.updated_at.desc()).first()
        if p is None:
            raise HandsError(f"{ref} holds no proposal being built. Nothing to work on there.")
    elif re.fullmatch(r"#?\d+", ref):
        p = db.get(Proposal, int(ref.lstrip("#")))
        if p is None or p.status not in ("building", "signoff"):
            raise HandsError(f"Proposal {ref} is not being built (it is {p.status if p else 'unknown'}).")
    else:
        p = q.filter(Proposal.title.ilike(f"%{ref}%")).order_by(Proposal.updated_at.desc()).first()
        if p is None:
            raise HandsError(f"No proposal being built matches '{ref}'.")
    slug = p.branch.split("/", 1)[-1] if p.branch else developer.slugify(p.title)
    root = (settings.path("sandboxes") / slug).resolve()
    if not (root / "cbx").is_dir():
        raise HandsError(f"The sandbox for #{p.id} '{p.title}' is not on disk ({root.name}). Say go again to rebuild it.")
    return p, root


def developer_slots() -> tuple[str, ...]:
    from ..versions.injector import SANDBOX_SLOTS

    return SANDBOX_SLOTS


def jail(root: Path, rel: str, *, for_write: bool = False) -> Path:
    """A path inside the sandbox, under one of the safe folders, or a refusal in plain words."""
    raw = str(rel or "").strip().replace("\\", "/")
    if not raw:
        raise HandsError("Give me a path inside the sandbox, e.g. cbx/api/home.py.")
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise HandsError(f"'{rel}' is an absolute path. I only work inside the sandbox: cbx/, web/, tests/, docs/.")
    parts = [x for x in raw.split("/") if x not in ("", ".")]
    if ".." in parts:
        raise HandsError(f"'{rel}' climbs out of the sandbox. Refused.")
    if not parts or (parts[0] not in SAFE_DIRS and raw not in SAFE_FILES):
        raise HandsError(f"'{rel}' is outside cbx/, web/, tests/ and docs/. Refused.")
    if raw.lower().endswith(BAD_SUFFIX):
        raise HandsError(f"'{rel}' is a script the server would run. Refused.")
    target = (root / raw)
    # resolve what exists (symlinks included) and make sure it is still inside
    probe = target if target.exists() else target.parent
    try:
        resolved = probe.resolve()
    except OSError as e:
        raise HandsError(f"Cannot resolve '{rel}': {e}") from e
    if resolved != root and root not in resolved.parents:
        raise HandsError(f"'{rel}' resolves outside the sandbox (a link?). Refused.")
    if for_write and target.exists() and target.is_symlink():
        raise HandsError(f"'{rel}' is a link. Refused.")
    return target


# ---- the environment a sandbox child runs in ------------------------------------------------------


def child_env(root: Path, data_dir: Optional[Path] = None, *, pip: bool = False) -> dict:
    """No CBX settings from this server (no mailbox, no database URL, no Node-RED), a throwaway data
    dir, the sandbox on the path. Same shape as the version injector's test runner."""
    from ..versions.safe_env import minimal_env  # 1.0.114: the approved short list — no other secret either (proxy password, cloud keys)

    env = minimal_env(pip=pip)  # pip alone also gets the proxy / PIP_* settings it needs to reach the index
    env["CBX_TESTING"] = "1"
    env["CBX_SANDBOX_RUN"] = "1"
    env["CBX_DATA_DIR"] = str(data_dir or Path(tempfile.mkdtemp(prefix="cbx-hands-")))
    env["PYTHONPATH"] = str(root)
    env["PYTHONIOENCODING"] = "utf-8"
    env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = "C-EXA"
    env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = "cexa@cbx.local"
    return env


def _tail(s: str, n: int = MAX_OUT) -> str:
    s = s or ""
    return s if len(s) <= n else "…(earlier output cut)…\n" + s[-n:]


def check_command(argv: list[str], root: Path) -> list[str]:
    """Whitelist the programme and its first argument, refuse shell syntax and paths that leave the
    sandbox. Returns the argv to run (python resolved to this interpreter)."""
    if not argv:
        raise HandsError("Give me a command, e.g. pytest tests/test_home.py -q.")
    prog = Path(argv[0]).name.lower()
    prog = prog[:-4] if prog.endswith(".exe") else prog
    if prog.startswith("python3"):
        prog = "python"
    if prog not in ALLOWED:
        raise HandsError(f"'{argv[0]}' is not on my list. I can run: " + ", ".join(sorted(ALLOWED)) + " — inside the sandbox only.")
    allowed_first = ALLOWED[prog]
    rest = argv[1:]
    if prog == "python":
        if len(rest) < 2 or rest[0] != "-m" or rest[1] not in PY_MODULES:
            raise HandsError("python may only run -m pytest, -m ruff or -m pip here (never -c, never a script — a script can read anything the server can).")
    elif allowed_first is not None:
        if not rest or rest[0] not in allowed_first:
            raise HandsError(f"{prog} may only be followed by: " + ", ".join(sorted(allowed_first)) + ".")
    if prog == "pip" or (prog == "python" and rest[:2] == ["-m", "pip"]):
        tokens = rest if prog == "pip" else rest[2:]   # ["install", "-r", "requirements.txt"]
        if tokens[:2] != ["install", "-r"] or len(tokens) != 3 or tokens[2] != "requirements.txt":
            raise HandsError("pip may only run: pip install -r requirements.txt (the sandbox's own).")
    if prog == "git" and rest and rest[0] in ("checkout", "restore", "stash", "branch"):
        # never leave the branch, never touch remotes — these are for undoing a bad edit only
        if any(t.startswith("-") and t not in ("--", "-q", "--quiet", "--staged", "-p", "--list") for t in rest[1:]) and rest[0] != "stash":
            raise HandsError(f"git {rest[0]} with options is not something I do here.")
    for t in argv[1:]:
        if SHELL_CHARS.search(t):
            raise HandsError("No shell syntax — one plain command with arguments, no &&, |, >, ; or $.")
        if t.startswith("/") or re.match(r"^[A-Za-z]:", t) or ".." in t.replace("\\", "/").split("/"):
            raise HandsError(f"'{t}' points outside the sandbox. Only relative paths inside it.")
    if prog == "python":
        return [sys.executable, *rest]
    if prog == "pytest":
        return [sys.executable, "-m", "pytest", *rest]
    if prog == "ruff":
        return [sys.executable, "-m", "ruff", *rest]
    if prog == "pip":
        return [sys.executable, "-m", "pip", *rest]
    return [shutil.which("git") or "git", *rest]


def run_command(root: Path, argv: list[str], timeout_s: int = DEFAULT_TIMEOUT) -> dict:
    """Run one whitelisted command with the sandbox as cwd. Never raises for the command's own failure."""
    argv = check_command(argv, root)
    timeout_s = max(MIN_TIMEOUT, min(MAX_TIMEOUT, int(timeout_s or DEFAULT_TIMEOUT)))
    data_dir = Path(tempfile.mkdtemp(prefix="cbx-hands-"))
    t0 = time.time()
    try:
        p = subprocess.run(argv, cwd=str(root), env=child_env(root, data_dir, pip=argv[1:3] == ["-m", "pip"]), capture_output=True, timeout=timeout_s)
        out = (p.stdout or b"").decode("utf-8", "replace") + (p.stderr or b"").decode("utf-8", "replace")
        return {"ok": p.returncode == 0, "code": p.returncode, "output": _tail(out), "seconds": round(time.time() - t0, 1), "argv": argv}
    except subprocess.TimeoutExpired as e:
        out = ((e.stdout or b"") + (e.stderr or b"")).decode("utf-8", "replace") if isinstance(e.stdout, (bytes, type(None))) else str(e.stdout)
        return {"ok": False, "code": -1, "output": _tail(out) + f"\n… stopped after {timeout_s} s (time limit).", "seconds": timeout_s, "argv": argv}
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)


# ---- the tools ----------------------------------------------------------------------------------------


def _guard(me: User) -> Optional[str]:
    if not enabled():
        return "My sandbox hands are switched off on this server (CBX_CEXA_HANDS=0)."
    if not me.is_admin:
        return "Only admins and owners can ask me to work in a sandbox."
    return None


def _note(db: Session, what: str, detail: str, ok: bool = True) -> None:
    log(db, "cexa_hands", actor=developer.cexa_user(db), target=what, detail=detail[:400], outcome="ok" if ok else "failed")


@tool("sandbox_status", "read", "Which sandbox I am working in: the proposal, its branch, last test result, the diff size, whether its app is running.", {"sandbox": "optional: slot (sandbox-a), proposal #id or title"})
def sandbox_status(db: Session, me: User, fix: Optional[Fix], sandbox: str = "", **_) -> str:
    if (g := _guard(me)):
        return g
    from ..versions import sandbox_runner

    rows = db.query(Proposal).filter(Proposal.status.in_(["building", "signoff"])).order_by(Proposal.updated_at.desc()).all()
    if not rows:
        return "No sandbox is in use. Nothing is being built or waiting for sign-off."
    running = {r["slot"]: r for r in sandbox_runner.status()}
    lines = []
    for p in rows:
        slug = p.branch.split("/", 1)[-1] if p.branch else developer.slugify(p.title)
        root = settings.path("sandboxes") / slug
        t = p.tests or {}
        run = running.get(p.sandbox or "", {})
        lines.append(f"#{p.id} '{p.title}' — {p.status} in {p.sandbox or '?'}; dir sandboxes/{slug}{' (missing on disk!)' if not (root / 'cbx').is_dir() else ''}; branch {p.branch or '-'}; tests: {t.get('passed', '?')} passed, {t.get('failed', '?')} failed"
                     + (f" ({len(t.get('rounds', []))} round(s))" if t.get("rounds") else "") + f"; diff {len(p.diff or '')} chars" + (f"; app running at {run['url']}" if run.get("running") else ""))
    return "\n".join(lines)


@tool("read_sandbox_file", "read", "Read a file in the sandbox copy of the app (the one being built), with line numbers.", {"path": "e.g. cbx/api/home.py", "search": "optional text to find; shows the lines around it", "sandbox": "optional: slot, #id or title"})
def read_sandbox_file(db: Session, me: User, fix: Optional[Fix], path: str = "", search: str = "", sandbox: str = "", **_) -> str:
    if (g := _guard(me)):
        return g
    try:
        p, root = resolve_sandbox(db, sandbox)
        f = jail(root, path)
    except HandsError as e:
        return str(e)
    if not f.is_file():
        return f"{path} is not in the sandbox for #{p.id}."
    if f.suffix.lower() not in TEXT_SUFFIX:
        return f"{path} is not a text file I read ({f.suffix})."
    lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
    if search:
        hits = [i for i, l in enumerate(lines) if search.lower() in l.lower()]
        if not hits:
            return f"'{search}' does not appear in {path}."
        i = hits[0]
        lo, hi = max(0, i - 15), min(len(lines), i + 30)
        return f"{path} lines {lo + 1}-{hi} ({len(hits)} match(es)):\n" + "\n".join(f"{n + 1:4d}  {l}" for n, l in enumerate(lines[lo:hi], start=lo))
    return f"{path} ({len(lines)} lines), first 120:\n" + "\n".join(f"{n + 1:4d}  {l}" for n, l in enumerate(lines[:120]))


@tool("search_sandbox", "read", "Find which sandbox source files mention a word or phrase.", {"query": "text to search for", "sandbox": "optional: slot, #id or title"})
def search_sandbox(db: Session, me: User, fix: Optional[Fix], query: str = "", sandbox: str = "", **_) -> str:
    if (g := _guard(me)):
        return g
    if not query.strip():
        return "Give me something to search for."
    try:
        p, root = resolve_sandbox(db, sandbox)
    except HandsError as e:
        return str(e)
    hits = []
    for d in SAFE_DIRS:
        base = root / d
        if not base.is_dir():
            continue
        for f in base.rglob("*"):
            if f.suffix.lower() in TEXT_SUFFIX and "__pycache__" not in f.parts and f.is_file():
                try:
                    for n, l in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                        if query.lower() in l.lower():
                            hits.append(f"{f.relative_to(root).as_posix()}:{n}: {l.strip()[:120]}")
                            if len(hits) >= 30:
                                return "\n".join(hits) + "\n(more…)"
                except OSError:
                    pass
    return "\n".join(hits) or f"'{query}' not found in the sandbox."


def _pre_image(root: Path, f: Path) -> None:
    """Keep the file as it was before C-EXA's first touch, so the change is reconstructible even without
    git. A file C-EXA created has no "before" — it is marked as new instead."""
    rel = f.relative_to(root)
    pre = root / ".cexa" / "pre" / rel
    new = root / ".cexa" / "new" / rel
    if pre.exists() or new.exists():
        return
    if f.exists():
        pre.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, pre)
    else:
        new.parent.mkdir(parents=True, exist_ok=True)
        new.write_text("", encoding="utf-8")


@tool("write_sandbox_file", "code", "Create or replace one file in the sandbox (inside cbx/, web/, tests/ or docs/). Prefer patch_sandbox_file for small edits.", {"path": "e.g. tests/test_thing.py", "content": "the whole file", "sandbox": "optional: slot, #id or title"})
def write_sandbox_file(db: Session, me: User, fix: Optional[Fix], path: str = "", content: str = "", sandbox: str = "", **_) -> str:
    if (g := _guard(me)):
        return g
    try:
        p, root = resolve_sandbox(db, sandbox)
        if p.status != "building":
            return f"#{p.id} is waiting for sign-off — I don't edit a sandbox the owner is reviewing. Ask them to send it back to building (Not now, then Go) if it needs more work."
        f = jail(root, path, for_write=True)
    except HandsError as e:
        _note(db, "write", f"refused {path}: {e}", ok=False)
        return str(e)
    if len(content or "") > MAX_WRITE:
        return "That file is too big to write in one go (400 KB limit)."
    if f.suffix.lower() not in TEXT_SUFFIX:
        return f"I only write text files ({', '.join(TEXT_SUFFIX)})."
    _pre_image(root, f)
    f.parent.mkdir(parents=True, exist_ok=True)
    existed = f.exists()
    f.write_text(content or "", encoding="utf-8")
    _note(db, "write", f"#{p.id} {path}: {'replaced' if existed else 'created'} ({len(content or '')} chars)")
    return f"{'Replaced' if existed else 'Created'} {path} ({len((content or '').splitlines())} lines). Next: run_tests, then diff_sandbox."


@tool("patch_sandbox_file", "code", "Replace one exact passage in a sandbox file with another (must match exactly once).", {"path": "file in the sandbox", "old": "the exact text to replace", "new": "what it becomes", "sandbox": "optional: slot, #id or title"})
def patch_sandbox_file(db: Session, me: User, fix: Optional[Fix], path: str = "", old: str = "", new: str = "", sandbox: str = "", **_) -> str:
    if (g := _guard(me)):
        return g
    try:
        p, root = resolve_sandbox(db, sandbox)
        if p.status != "building":
            return f"#{p.id} is waiting for sign-off — I don't edit a sandbox the owner is reviewing."
        f = jail(root, path, for_write=True)
    except HandsError as e:
        _note(db, "patch", f"refused {path}: {e}", ok=False)
        return str(e)
    if not f.is_file():
        return f"{path} is not in the sandbox — write_sandbox_file creates a new file."
    if not old:
        return "Give me the exact text to replace (old)."
    text = f.read_text(encoding="utf-8", errors="replace")
    n = text.count(old)
    if n == 0:
        return f"That passage is not in {path} — read it again with read_sandbox_file and copy the lines exactly."
    if n > 1:
        return f"That passage appears {n} times in {path}; include more surrounding lines so it matches once."
    _pre_image(root, f)
    f.write_text(text.replace(old, new or "", 1), encoding="utf-8")
    _note(db, "patch", f"#{p.id} {path}: {len(old)} → {len(new or '')} chars")
    return f"Patched {path}. Next: run_tests."


@tool("sh", "code", "Run one command in the sandbox: pytest, ruff, git (status/diff/log/add/commit/show), pip install -r requirements.txt, python -m pytest. No shell syntax, no paths outside the sandbox.", {"command": "e.g. pytest tests/test_home.py -q -k attention", "timeout_s": "10-900, default 300", "sandbox": "optional: slot, #id or title"})
def sh(db: Session, me: User, fix: Optional[Fix], command: str = "", timeout_s: str = "", sandbox: str = "", **_) -> str:
    if (g := _guard(me)):
        return g
    try:
        p, root = resolve_sandbox(db, sandbox)
        try:
            argv = shlex.split(command or "", posix=True)
        except ValueError as e:
            raise HandsError(f"I could not read that command: {e}") from e
        r = run_command(root, argv, int(timeout_s) if str(timeout_s).strip().isdigit() else DEFAULT_TIMEOUT)
    except HandsError as e:
        _note(db, "sh", f"refused: {command[:120]} — {e}", ok=False)
        return str(e)
    _note(db, "sh", f"#{p.id} $ {command[:160]} → exit {r['code']} in {r['seconds']} s", ok=r["ok"])
    return f"$ {command}\n(exit {r['code']}, {r['seconds']} s)\n{r['output'] or '(no output)'}"


@tool("run_tests", "code", "Run the sandbox's tests and read the failures verbatim. Green tests are the price of sign-off.", {"path": "optional: a test file or folder, default tests", "k": "optional -k expression", "sandbox": "optional: slot, #id or title"})
def run_tests(db: Session, me: User, fix: Optional[Fix], path: str = "", k: str = "", sandbox: str = "", **_) -> str:
    if (g := _guard(me)):
        return g
    from ..versions.injector import failed_tests

    try:
        p, root = resolve_sandbox(db, sandbox)
        target = (path or "tests").strip()
        jail(root, target)  # a test path outside the sandbox is refused like any other
        argv = ["pytest", "-q", target, "-p", "no:cacheprovider", "--tb=short"] + (["-k", k] if k.strip() else [])
        r = run_command(root, argv, MAX_TIMEOUT)
    except HandsError as e:
        return str(e)
    fails = failed_tests(r["output"])
    passed = sum(int(x) for x in re.findall(r"(\d+) passed", r["output"]))
    _note(db, "run_tests", f"#{p.id} {target}{' -k ' + k if k else ''}: {passed} passed, {len(fails)} failed in {r['seconds']} s", ok=r["ok"])
    p.tests = {**(p.tests or {}), "last_run": {"passed": passed, "failed": len(fails), "ok": r["ok"], "seconds": r["seconds"], "target": target}}
    db.add(p)
    if r["ok"]:
        return f"Green: {passed} passed in {r['seconds']} s ({target}{' -k ' + k if k else ''})."
    head = "\n".join(fails[:10]) if fails else "(no FAILED lines — read the output)"
    return f"{len(fails)} failed, {passed} passed in {r['seconds']} s.\nFailures:\n{head}\n\nOutput (tail):\n{_tail(r['output'], 3500)}"


@tool("diff_sandbox", "read", "What has changed in the sandbox against the live tree it was copied from — the diff the owner reviews at sign-off.", {"sandbox": "optional: slot, #id or title"})
def diff_sandbox(db: Session, me: User, fix: Optional[Fix], sandbox: str = "", **_) -> str:
    if (g := _guard(me)):
        return g
    try:
        p, root = resolve_sandbox(db, sandbox)
    except HandsError as e:
        return str(e)
    d = developer._diff(root, [], limit=6000)
    if d.strip():
        p.diff = d
        db.add(p)
    return d.strip() or "No changes in the sandbox yet."


@tool("commit_sandbox", "code", "Commit the sandbox's current changes on its branch with a short message (one working step).", {"message": "what this step did", "sandbox": "optional: slot, #id or title"})
def commit_sandbox(db: Session, me: User, fix: Optional[Fix], message: str = "", sandbox: str = "", **_) -> str:
    if (g := _guard(me)):
        return g
    try:
        p, root = resolve_sandbox(db, sandbox)
    except HandsError as e:
        return str(e)
    if not (root / ".git").exists():
        return "This sandbox has no git repository (git is not installed on the server) — the diff is still tracked from the pre-images."
    msg = " ".join((message or "").split())[:120] or "step"
    if SHELL_CHARS.search(msg):
        return "Keep the commit message to plain words."
    r1 = run_command(root, ["git", "add", "-A"], 60)
    r2 = run_command(root, ["git", "commit", "-q", "-m", msg], 60)
    if not r2["ok"] and "nothing to commit" in r2["output"]:
        return "Nothing to commit — the sandbox is clean."
    _note(db, "commit", f"#{p.id}: {msg}", ok=r1["ok"] and r2["ok"])
    return f"Committed: {msg}" if r2["ok"] else f"Commit failed:\n{r2['output']}"


@tool("run_sandbox_app", "code", "Start the sandbox copy of the app on its own port and database so the owner can click through it (sandbox-a → 8081, sandbox-b → 8082).", {"sandbox": "optional: slot, #id or title"})
def run_sandbox_app(db: Session, me: User, fix: Optional[Fix], sandbox: str = "", **_) -> str:
    if (g := _guard(me)):
        return g
    from ..versions import injector, sandbox_runner

    try:
        p, root = resolve_sandbox(db, sandbox)
    except HandsError as e:
        return str(e)
    if injector._PROMOTE_LOCK.locked() or injector._restart_pending:
        return "A promotion or rollback is in progress — I'll not start a sandbox while the server is about to restart."
    slot = p.sandbox or ""
    if slot not in developer_slots():
        return f"#{p.id} has no sandbox slot; say go on it again."
    if sandbox_runner._port_for(slot) == settings.port:
        return "That sandbox would use the live port — refused."
    owners = [(u.email, u.name) for u in db.query(User).filter(User.role == "owner", User.is_axl.is_(False), User.is_active.is_(True)).all()]
    out = sandbox_runner.start(slot, str(root), owners=owners, kind="proposal", title=p.title)
    _note(db, "run_app", f"#{p.id} in {slot} → {out.get('url', '')} {out.get('note', '')}", ok=bool(out.get("ok")))
    if out.get("ok"):
        # the sandbox passwords are for the Versions page, never for this transcript
        return f"Sandbox app for #{p.id} is up in {slot} (its own empty database — the live data is untouchable). The owner goes in from Versions & sandboxes; the sign-in is on that card. {out.get('note', '')}".strip()
    return f"Could not start the sandbox app: {out.get('note', 'unknown reason')}"


@tool("open_playground", "change", "Start a sandbox for the owner with NOTHING to build: a copy of the live app as it is, on its own port and empty database, with a sandbox sign-in of their own — for looking around, trying things, showing someone. Use when an owner asks for a sandbox, a test environment or a playground without naming a change. No proposal is made and nothing is changed anywhere.", {})
def open_playground(db: Session, me: User, fix: Optional[Fix], **_) -> str:
    if (g := _guard(me)):
        return g
    if not me.is_owner:
        return "A playground is the owner's: only an owner can ask for one (it carries a sign-in with owner rights inside the sandbox)."
    from ..versions import injector, sandbox_runner

    if injector._PROMOTE_LOCK.locked() or injector._restart_pending:
        return "A promotion or rollback is in progress — not while the server is about to restart."
    slot = injector.free_slot(db)
    if slot is None:
        return "Both sandbox slots are in use (a build or a staged version in each). Stop or promote one first — Versions & sandboxes."
    if sandbox_runner._port_for(slot) == settings.port:
        return "That sandbox would use the live port — refused."
    dest = developer.make_playground(slot)
    owners = [(u.email, u.name) for u in db.query(User).filter(User.role == "owner", User.is_axl.is_(False), User.is_active.is_(True)).all()]
    out = sandbox_runner.start(slot, str(dest), owners=owners, kind="playground", title="Playground · copy of the live app")
    log(db, "playground_opened", actor=developer.cexa_user(db), target=slot, detail=f"asked by {me.name}; {out.get('note', '')}".strip("; "), outcome="ok" if out.get("ok") else "failed")
    if out.get("ok"):
        return (f"Your playground is up in {slot}: a copy of the live app on its own empty database — nothing you do in there touches anything real. "
                f"Open Versions & sandboxes and press Go in on the {slot} card; sign in with your own email and the sandbox password shown there (the password is on that page only, not here). "
                f"Stop from the same card when you're done. {out.get('note', '')}").strip()
    return f"Could not start the playground: {out.get('note', 'unknown reason')}"


@tool("stop_sandbox_app", "code", "Stop the sandbox copy of the app.", {"sandbox": "optional: slot, #id or title"})
def stop_sandbox_app(db: Session, me: User, fix: Optional[Fix], sandbox: str = "", **_) -> str:
    if (g := _guard(me)):
        return g
    from ..versions import sandbox_runner

    try:
        p, root = resolve_sandbox(db, sandbox)
    except HandsError as e:
        return str(e)
    out = sandbox_runner.stop(p.sandbox or "")
    _note(db, "stop_app", f"#{p.id} in {p.sandbox}: {out.get('note', '')}")
    return out.get("note", "stopped").capitalize() + "."
