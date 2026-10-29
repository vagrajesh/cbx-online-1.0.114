"""The version injector. The owner is usually away from the server, so a new version arrives as a
zip (uploaded on the Versions page, or built by C-EXA from a sandbox), is unpacked to
DATA_DIR/versions/<tag>/, tested there, and only then promoted over the live tree.

    stage(db, zip_bytes, by, notes)  → AppVersion (status sandbox | failed)
    promote(db, version, by)         → backup live tree, copy new files, pip install, RESTART_REQUESTED, status live
    rollback(db, version, by)        → restore that version's backup
    package_from_dir(db, src, tag, notes, by) → build a zip from a sandbox dir and stage it

Module-level knobs (tests monkeypatch these):
    LIVE_ROOT        the tree that is replaced on promote (defaults to the repo root)
    TEST_CMD         the pytest command run inside a staged version
    RUN_PIP          whether to pip install requirements on promote
    RESTART_ENABLED  whether promote schedules os._exit(3) so run.bat restarts the process
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from .. import VERSION
from ..audit import log
from ..config import ROOT, settings
from ..db import utcnow
from ..models import AppVersion, Proposal, User
from .safe_env import minimal_env

log_ = logging.getLogger("cbx.versions")

LIVE_ROOT: Path = ROOT
# The whole suite, not "-x": one failure used to stop the run, so the Versions page said "1 failed"
# without saying which, and nobody could tell whether the other 240 tests were fine.
TEST_CMD: list[str] = [sys.executable, "-m", "pytest", "-q", "tests", "tools", "-p", "no:cacheprovider", "--tb=short"]
TEST_TIMEOUT = 1800  # 1.0.114: was 900 — the whole suite (1,161 tests) took 1,069 s on the build box; CBX 2 allows 25 min
# Anything about this server's own services must not leak into a version's test run: the tests
# would use the live database, models, mail or schedules.
SCRUB_ENV = ("CBX_DATABASE_URL", "CBX_MODEL_MODE", "CBX_MAIL_ENABLED", "CBX_MFA_REQUIRED", "CBX_CODE_FALLBACK", "CBX_FLOWS_SCHEDULE", "CBX_TEAM_SCREENSHOTS", "CBX_TEAMS_AUTO", "CBX_BACKUPS", "CBX_BACKUP_DIR", "CBX_MODEL_DEVELOPER", "CBX_MODEL_DESIGNER", "CBX_MODEL_HELPER")
RUN_PIP: bool = not os.environ.get("CBX_TESTING")
RESTART_ENABLED: bool = not os.environ.get("CBX_TESTING")
MAX_ZIP = 200 * 1024 * 1024

ALLOWED_DIRS = ("cbx", "web", "tests", "docs", "tools")
SEALED_DOC = "docs/OWNERS-DOCUMENT.md"
# The full install zip is accepted as-is: scripts and docs ride along and are updated on promote.
# Never accepted: .env (your settings), data/ (your database and files), .venv/.
ALLOWED_FILES = ("requirements.txt", "VERSION", "README.md", "CONVENTIONS.md", ".env.example", "setup.bat", "run.bat", "update.bat", ".gitignore")
SCRIPT_FILES = ("setup.bat", "run.bat", "update.bat")
FORBIDDEN = (".env", "data", ".venv", "dist", ".git")
REQUIRED = ("cbx/", "web/", "tests/", "requirements.txt", "VERSION")
PROMOTED = ("cbx", "web", "tests", "docs", "tools", "requirements.txt", "README.md", "CONVENTIONS.md", ".env.example", "setup.bat", "update.bat")
# run.bat is the process that is running us; it is written as run.bat.new and swapped by run.bat itself on restart.
SANDBOX_SLOTS = ("sandbox-a", "sandbox-b")
_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,30}$")


class VersionError(Exception):
    pass


# ---- paths ------------------------------------------------------------------------------------


def versions_dir() -> Path:
    return settings.path("versions")


def backups_dir() -> Path:
    return settings.path("backups")


def sandboxes_dir() -> Path:
    return settings.path("sandboxes")


# ---- zip verification -------------------------------------------------------------------------


def _norm_name(n: str) -> str:
    """Zip entries should use '/', but some Windows tools write '\\'. Treat both as separators."""
    return n.replace("\\", "/")


def _prefix(names: list[str]) -> str:
    """Some tools zip a single top folder (cbx-online-1.0.1/...). Tolerate exactly one such prefix —
    also when that folder happens to be called like one of ours (someone zipped a folder named 'cbx')."""
    names = [_norm_name(n) for n in names]
    tops = {n.split("/")[0] for n in names if n.strip("/")}
    if len(tops) != 1:
        return ""
    t = next(iter(tops))
    if t not in ALLOWED_DIRS + ALLOWED_FILES or (t + "/VERSION") in names:
        return t + "/"
    return ""


def verify_zip(zf: zipfile.ZipFile) -> str:
    """Refuse absolute paths, `..`, unknown top-level entries, .bat files. Returns the VERSION tag."""
    raw_names = zf.namelist()
    if not raw_names:
        raise VersionError("The zip is empty.")
    names = [_norm_name(n) for n in raw_names]
    prefix = _prefix(raw_names)
    for n in names:
        if n.startswith("/") or re.match(r"^[A-Za-z]:", n):
            raise VersionError(f"The zip contains an absolute path ({n}). Zip the folders, not the drive.")
        parts = n.split("/")
        if ".." in parts:
            raise VersionError(f"The zip contains a path that climbs out of the package ({n}).")
        rel = n[len(prefix):] if prefix and n.startswith(prefix) else n
        top = rel.split("/")[0]
        if top in FORBIDDEN:
            raise VersionError(f"'{rel}' must not be in a version package — your .env, data and .venv stay on the server. Zip the app folders, not the whole install.")
        if rel.lower().endswith((".cmd", ".ps1", ".exe")) or (rel.lower().endswith(".bat") and rel not in SCRIPT_FILES):
            raise VersionError(f"'{rel}' is not allowed in a version package; only the CBX scripts ({', '.join(SCRIPT_FILES)}) are.")
        if not rel or rel.endswith("/") and rel.count("/") == 1 and rel[:-1] in ALLOWED_DIRS:
            continue
        if top not in ALLOWED_DIRS and rel not in ALLOWED_FILES:
            raise VersionError(f"'{rel}' is not part of a version package. Allowed: {', '.join(ALLOWED_DIRS)}/ and {', '.join(ALLOWED_FILES)}.")
    have = {(n[len(prefix):] if prefix and n.startswith(prefix) else n) for n in names}
    missing = [r for r in REQUIRED if not any(h == r or h.startswith(r) for h in have)]
    if missing:
        raise VersionError("The zip is missing: " + ", ".join(missing) + ".")
    version_entry = next((raw for raw, n in zip(raw_names, names) if n == prefix + "VERSION"), None)
    tag = ""
    if version_entry is not None:
        lines = zf.read(version_entry).decode("utf-8", "replace").strip().splitlines()
        tag = lines[0].strip() if lines else ""
    if not _TAG_RE.match(tag):
        raise VersionError("VERSION must hold a short tag like 1.0.1 (letters, digits, dots, dashes).")
    return tag


# The identity of the product and of C-EXA, as the code declares it. A package that changes any of these
# is refused at stage unless its notes say "branding change" — the names are settings (Settings →
# Branding, CBX_PRODUCT_NAME, CBX_AI_NAME); the code's defaults stay CBX-Connect / C-EXA.
IDENTITY = (
    ("cbx/config.py", 'product_name: str = "CBX-Connect"'),
    ("cbx/config.py", 'ai_name: str = "C-EXA"'),
    ("cbx/cexa/persona.py", "You are {ai}, the controller of {product}"),
    ("web/index.html", "<title>CBX-Connect</title>"),
)
BRANDING_FLAG = "branding change"


def branding_changed(pkg: Path) -> list[str]:
    """Which identity strings the package no longer carries (empty = none, all good)."""
    missing = []
    for rel, needle in IDENTITY:
        f = pkg / rel
        if not f.is_file():
            continue  # a package that drops the file entirely fails REQUIRED/tests elsewhere
        try:
            if needle not in f.read_text(encoding="utf-8", errors="replace"):
                missing.append(f"{rel}: '{needle}'")
        except OSError:
            continue
    return missing


def _unpack(zf: zipfile.ZipFile, dest: Path) -> None:
    prefix = _prefix(zf.namelist())
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    for info in zf.infolist():
        name = _norm_name(info.filename)
        rel = name[len(prefix):] if prefix and name.startswith(prefix) else name
        if not rel or rel.endswith("/") or info.is_dir():
            continue
        target = (root / rel).resolve()
        if root not in target.parents:
            raise VersionError(f"Refusing to write outside the package: {rel}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)


# ---- tests --------------------------------------------------------------------------------------


def run_tests(cwd: Path, timeout: int = TEST_TIMEOUT) -> dict:
    """Run the package's own tests in isolation (stub model, temp DATA_DIR). Never raises."""
    if os.environ.get("CBX_SANDBOX_RUN"):
        return {"passed": 0, "failed": 0, "new": 0, "skipped": True, "note": "nested sandbox run skipped"}
    # 1.0.114: an approved short list of the server's environment (safe_env.minimal_env), not all of it minus a
    # blocklist — an uploaded zip's tests must never read the server's secrets (CBX_MS_CLIENT_SECRET, a proxy password)
    env = minimal_env(pythonpath=str(cwd), cbx={"CBX_TESTING": "1", "CBX_SANDBOX_RUN": "1", "CBX_DATA_DIR": tempfile.mkdtemp(prefix="cbx-sbx-")})
    for k in SCRUB_ENV:  # belt and braces: none of these is on the short list anyway
        env.pop(k, None)
    # a package from before the tool folders (1.0.42 and older) has no tools/ to collect
    cmd = [a for a in TEST_CMD if not (a == "tools" and not (cwd / "tools").is_dir())]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, timeout=timeout)
        out = (p.stdout or b"").decode("utf-8", "replace") + (p.stderr or b"").decode("utf-8", "replace")
        passed = sum(int(x) for x in re.findall(r"(\d+) passed", out))
        failed = sum(int(x) for x in re.findall(r"(\d+) failed", out)) + sum(int(x) for x in re.findall(r"(\d+) error", out))
        ok = p.returncode == 0
        failures = failed_tests(out)
        tail = "\n".join(out.strip().splitlines()[-40:])
        return {"passed": passed, "failed": failed, "ok": ok, "seconds": round(time.time() - t0, 1), "output": tail[-6000:], "returncode": p.returncode, "failures": failures[:20]}
    except subprocess.TimeoutExpired:
        return {"passed": 0, "failed": 1, "ok": False, "seconds": timeout, "output": f"tests did not finish in {timeout} s", "failures": [f"the whole run took longer than {timeout} s"]}
    except Exception as e:  # noqa: BLE001
        return {"passed": 0, "failed": 1, "ok": False, "output": f"could not run tests: {e}", "failures": [f"could not run tests: {e}"]}
    finally:
        shutil.rmtree(env["CBX_DATA_DIR"], ignore_errors=True)


_FAILED_LINE = re.compile(r"^(?:FAILED|ERROR) ((?:tests|tools)[/\\][^\s]+?)(?: - (.*))?$", re.M)


def failed_tests(out: str) -> list[str]:
    """The failing tests by name, from pytest's short summary — 'tests/test_home.py::test_x — AssertionError: …'.
    This is what the Versions page shows on the row, so the owner sees WHAT failed, not only that one did."""
    seen: list[str] = []
    for m in _FAILED_LINE.finditer(out):
        name = m.group(1).replace("\\", "/")
        why = (m.group(2) or "").strip()
        line = name + (f" — {why[:160]}" if why else "")
        if line not in seen:
            seen.append(line)
    return seen


def _count_tests(root: Path) -> int:
    n = 0
    for f in (root / "tests").glob("test_*.py"):
        try:
            n += len(re.findall(r"^def test_", f.read_text(encoding="utf-8", errors="replace"), flags=re.M))
        except OSError:
            pass
    return n


# ---- sandboxes ----------------------------------------------------------------------------------


def free_slot(db: Session) -> Optional[str]:
    """The first idle sandbox slot, or None when every slot is in use (callers must say so, not double up)."""
    busy = {p.sandbox for p in db.query(Proposal).filter(Proposal.status.in_(["building", "signoff"])).all()}
    busy |= {v.env for v in db.query(AppVersion).filter(AppVersion.status.in_(["staged", "sandbox"])).all()}
    for s in SANDBOX_SLOTS:
        if s not in busy:
            return s
    return None


def sandboxes(db: Session) -> list[dict]:
    out = []
    for slot in SANDBOX_SLOTS:
        p = db.query(Proposal).filter(Proposal.sandbox == slot, Proposal.status.in_(["building", "signoff"])).order_by(Proposal.updated_at.desc()).first()
        v = db.query(AppVersion).filter(AppVersion.env == slot, AppVersion.status.in_(["staged", "sandbox"])).order_by(AppVersion.created_at.desc()).first()
        occupant = None
        if p:
            # the sandbox copy on disk, not the branch name — the Versions page opens and runs it from here
            slug = p.branch.split("/", 1)[-1] if p.branch else ""
            occupant = {"kind": "proposal", "id": p.id, "title": p.title, "status": p.status, "path": str(sandboxes_dir() / slug) if slug else ""}
        elif v:
            occupant = {"kind": "version", "id": v.id, "title": v.tag, "status": v.status, "path": v.package_path}
        dirs = [d.name for d in sandboxes_dir().iterdir() if d.is_dir()] if sandboxes_dir().exists() else []
        out.append({"slot": slot, "label": "Sandbox " + slot[-1].upper(), "busy": occupant is not None, "occupant": occupant, "dirs": dirs})
    return out


# ---- stage ----------------------------------------------------------------------------------------


def stage(db: Session, data: bytes, by: Optional[User], notes: str = "", *, run: bool = True) -> AppVersion:
    if len(data) > MAX_ZIP:
        raise VersionError("The zip is larger than 200 MB.")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise VersionError("That file is not a zip archive.") from e
    with zf:
        tag = verify_zip(zf)
        if db.query(AppVersion).filter(AppVersion.tag == tag).one_or_none():
            raise VersionError(f"Version {tag} already exists. Bump the VERSION file and upload again.")
        dest = versions_dir() / tag
        _rmtree(dest)
        _unpack(zf, dest)
    changed = branding_changed(dest)
    if changed and BRANDING_FLAG not in (notes or "").lower():
        _rmtree(dest)
        raise VersionError("This package changes the product's or C-EXA's identity in the code (" + "; ".join(changed) + "). Names are settings, not code — change them under Settings → Branding. If this really is a branding change, upload again with the words 'branding change' in the notes.")
    _sync_code_version(dest, tag)
    pkg = versions_dir() / f"{tag}.zip"
    pkg.write_bytes(data)
    v = AppVersion(tag=tag, notes=notes[:2000], status="staged", env=free_slot(db) or SANDBOX_SLOTS[0], package_path=str(dest), sha256=hashlib.sha256(data).hexdigest(), uploaded_by_id=by.id if by else None)
    db.add(v)
    db.flush()
    log(db, "version_staged", actor=by, target=tag, detail=f"{len(data)} bytes → {dest}")
    if run:
        test_version(db, v)
    return v


def _sync_code_version(pkg: Path, tag: str) -> None:
    """The VERSION file is the tag. If cbx/__init__.py still says an older number, the sidebar and
    /api/health would show the wrong version forever after promote — so align it in the package."""
    init = pkg / "cbx" / "__init__.py"
    try:
        txt = init.read_text(encoding="utf-8")
    except OSError:
        return
    new = re.sub(r'VERSION\s*=\s*"[^"]*"', f'VERSION = "{tag}"', txt, count=1)
    if new != txt:
        init.write_text(new, encoding="utf-8")


def _needs_new_packages(pkg: Path) -> bool:
    """True when the package's requirements.txt names something the live requirements don't."""
    try:
        new = {l.split("#")[0].strip().lower() for l in (pkg / "requirements.txt").read_text(encoding="utf-8").splitlines()}
        old = {l.split("#")[0].strip().lower() for l in (LIVE_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()}
    except OSError:
        return False
    return bool({x for x in new if x} - {x for x in old if x})


def test_version(db: Session, v: AppVersion) -> AppVersion:
    pkg = Path(v.package_path)
    pip_log = ""
    if _needs_new_packages(pkg):
        # A version that brings a new library (pandas for IFRS 16, say) must have it before its tests run,
        # otherwise the sandbox fails on an import and the version can never be promoted.
        pip_log = _pip_install(pkg / "requirements.txt")
    # The staged row is written; the tests take minutes. Let go of SQLite's single write lock now, or
    # every other writer on the server (the pulse, a batch, someone saving a form) sees "database is
    # locked" until they finish — which is how a heartbeat was tripping over an upload.
    db.commit()
    res = run_tests(pkg)
    if pip_log:
        res["pip"] = pip_log[-1500:]
    res["new"] = max(0, _count_tests(Path(v.package_path)) - _count_tests(LIVE_ROOT))
    v.tests = res
    v.status = "sandbox" if res.get("ok") or res.get("skipped") else "failed"
    db.flush()
    log(db, "version_tested", target=v.tag, detail=f"{res.get('passed', 0)} passed, {res.get('failed', 0)} failed", outcome="ok" if v.status == "sandbox" else "failed")
    try:  # a green version goes to C-EXA's Governance council before anyone promotes it (1.0.50)
        from ..cexa import teams

        db.commit()
        teams.version_tested(db, v)
    except Exception as e:  # noqa: BLE001 — the council is advice; it must never break staging
        log_.warning("council did not start for %s: %s", v.tag, e)
    return v


# ---- promote / rollback -------------------------------------------------------------------------------


def _copy_tree(src: Path, dst: Path) -> None:
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    elif src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _rmtree(p: Path, attempts: int = 5) -> None:
    """rmtree that survives Windows' transient locks (antivirus, indexer, a static file being served)."""
    for i in range(attempts):
        try:
            if p.is_dir():
                shutil.rmtree(p)
            elif p.exists():
                p.unlink()
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.4 * (i + 1))


def _replace_tree(src: Path, dst: Path) -> None:
    _rmtree(dst)
    _copy_tree(src, dst)


KEEP_BACKUPS = 5
_PROMOTE_LOCK = threading.Lock()
_restart_pending = False


def restart_pending() -> bool:
    """True from the moment a promotion or rollback has asked run.bat for a restart until the process exits."""
    return _restart_pending


def _prune_backups(keep_paths: set[str]) -> None:
    """Backups grow by one live tree per promotion; keep the newest KEEP_BACKUPS plus the one the
    live version points at (the only rollback the Versions page offers)."""
    try:
        # "data" is the nightly data backup (cbx/backup.py), not a live-tree backup: never pruned here
        dirs = sorted((d for d in backups_dir().iterdir() if d.is_dir() and d.name != "data"), key=lambda d: d.stat().st_mtime, reverse=True)
    except OSError:
        return
    for d in dirs[KEEP_BACKUPS:]:
        if str(d) in keep_paths:
            continue
        try:
            shutil.rmtree(d)
        except OSError as e:  # noqa: PERF203 — best effort, never fail a promotion over housekeeping
            log_.warning("could not remove old backup %s: %s", d, e)


def _restore_from(backup: Path) -> None:
    for name in PROMOTED + ("VERSION",):
        if (backup / name).exists():
            _replace_tree(backup / name, LIVE_ROOT / name)


# ---- 1.0.114: the database, backed up beside the code before a promote ----------------------------------------
# Before 1.0.114 a promote backed up only the code. A new version that migrates the database (new columns, moved
# rows) could not be undone by Roll back alone. Now the databases are copied first — SQLite's own online backup,
# consistent while the server keeps writing — into <code backup>/database/, and Roll back offers to put them back.

DATA_BACKUP = "database"
DATA_MANIFEST = "manifest.json"


def _main_db() -> Optional[Path]:
    """The live SQLite file, or None when the database is Postgres (backed up with its own tools)."""
    url = settings.db_url
    return Path(url[len("sqlite:///"):]) if url.startswith("sqlite:///") else None


def _live_dbs() -> list[tuple[str, Path]]:
    """(name in the backup, live file): the main database and each tool folder's tables."""
    out: list[tuple[str, Path]] = []
    main = _main_db()
    if main is not None and main.exists():
        out.append((main.name, main))
    tools = settings.data_dir / "tools"
    if tools.is_dir():
        out.extend((f"tools/{f.name}", f) for f in sorted(tools.glob("*.sqlite")))
    return out


def _sqlite_copy(src: Path, dst: Path) -> int:
    """A consistent copy of one SQLite database with SQLite's backup API, page by page while others write. Both
    connections are closed before this returns (Windows can't copy, move or delete a file that is still open), and
    the copy is left as one self-contained file (no -wal beside it) and checked."""
    import sqlite3
    from contextlib import closing

    dst.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(str(src), timeout=30)) as a, closing(sqlite3.connect(str(dst), timeout=30)) as b:
        a.backup(b)
        b.execute("PRAGMA journal_mode=DELETE")
        ok = b.execute("PRAGMA quick_check").fetchone()
    if not ok or ok[0] != "ok":
        raise VersionError(f"The copy of {src.name} did not pass SQLite's check ({ok[0] if ok else 'no answer'}).")
    return dst.stat().st_size


def backup_data(backup: Path) -> dict:
    """Copy the live databases into <backup>/database/ and write what is there. Raises VersionError when a copy fails
    (a promote then stops: nothing has been replaced yet). Postgres: nothing copied, the manifest says why."""
    import json

    d = backup / DATA_BACKUP
    m = {"at": utcnow().isoformat(timespec="seconds"), "databases": [], "bytes": 0, "note": ""}
    if _main_db() is None:
        m["note"] = "The database is Postgres — back it up with the database server's own tools before a promote."
    try:
        for name, f in _live_dbs():
            m["bytes"] += _sqlite_copy(f, d / name)
            m["databases"].append(name)
    except VersionError:
        raise
    except Exception as e:  # noqa: BLE001 — sqlite3/OS errors become one plain sentence
        raise VersionError(f"The database could not be backed up ({e}); nothing was changed. Try again in a minute.") from e
    if m["databases"] or m["note"]:
        d.mkdir(parents=True, exist_ok=True)
        (d / DATA_MANIFEST).write_text(json.dumps(m, indent=1), encoding="utf-8")
    return m


# 1.0.114: a database name in a backup's manifest is only ever a file in the data folder: cbx.sqlite3 or tools/<name>.sqlite
_DB_NAME_RE = re.compile(r"^(tools/)?[\w.-]+\.sqlite3?$")


def _db_target(name: str) -> Path:
    """The live file a manifest name stands for — refused unless the name has the expected shape and the file lies inside the
    data folder (the main database may sit where CBX_DATABASE_URL points)."""
    main = _main_db()
    if main is not None and name == main.name:
        return main
    if not _DB_NAME_RE.match(name or "") or ".." in name.split("/"):
        raise VersionError(f"The backup lists a database called '{name[:80]}', which is not a database name this server uses. Nothing was changed.")
    root = settings.data_dir.resolve()
    p = (settings.data_dir / name).resolve()
    if root not in p.parents:
        raise VersionError(f"The backup lists '{name[:80]}', which is outside the data folder. Nothing was changed.")
    return settings.data_dir / name


def data_backup_info(backup_path: str) -> Optional[dict]:
    """What a version's backup holds of the database ({at, databases, bytes}) — None when there is nothing to restore,
    or when its manifest names something that is not one of our databases (1.0.114: never trusted unchecked)."""
    import json

    if not backup_path:
        return None
    f = Path(backup_path) / DATA_BACKUP / DATA_MANIFEST
    try:
        m = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    names = m.get("databases") if isinstance(m, dict) else None
    if not names or not isinstance(names, list):
        return None
    try:
        for n in names:
            if not isinstance(n, str):
                return None
            _db_target(n)
    except VersionError:
        return None
    return {"at": str(m.get("at", "")), "databases": list(names), "bytes": int(m.get("bytes") or 0)}


def _backup_into(src: Path, dst: Path) -> None:
    """Write `src` over `dst` with SQLite's backup API: one step, so it lands whole or not at all; it takes the write lock
    and waits for other writers; `dst` stays the same open file (Windows refuses to copy over or move an open one)."""
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(str(src), timeout=30)) as a, closing(sqlite3.connect(str(dst), timeout=60)) as b:
        a.backup(b)


def _restore_data(backup: Path, keep: Path, info: dict) -> list[str]:
    """Put every database in <backup>/database/ back — all of them or none. What is live is copied into `keep` first; if
    one restore fails, the ones already done are put back from `keep`, so the data is either the backup's or what it
    was. Tool databases first, the main one last. Raises VersionError with what is true afterwards."""
    main = _main_db()
    src_root = (backup / DATA_BACKUP).resolve()
    names = sorted(info["databases"], key=lambda n: main is not None and n == main.name)
    targets = []
    for n in names:
        src = (backup / DATA_BACKUP / n).resolve()
        if src_root not in src.parents or not src.is_file():
            raise VersionError(f"The backup's copy of {n} is missing. Nothing was changed.")
        targets.append((n, src, _db_target(n)))
    existed: set[str] = set()
    try:
        for n, _src, live in targets:
            if live.exists():
                _sqlite_copy(live, keep / n)
                existed.add(n)
    except Exception as e:  # noqa: BLE001
        raise VersionError(f"A copy of the current data could not be kept ({e}), so the data was not touched.") from e
    done: list[str] = []
    try:
        for n, src, live in targets:
            live.parent.mkdir(parents=True, exist_ok=True)
            done.append(n)  # counted before the write: a failed write is undone below like the others
            _backup_into(src, live)
    except Exception as e:  # noqa: BLE001
        stuck = []
        for n, _src, live in targets:
            if n not in done:
                continue
            try:
                if n in existed:
                    _backup_into(keep / n, live)
                else:
                    for p in (live, Path(str(live) + "-wal"), Path(str(live) + "-shm")):
                        p.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                log_.exception("could not put %s back after a failed restore", n)
                stuck.append(n)
        if stuck:
            raise VersionError(f"The data could not be put back ({e}), and {', '.join(stuck)} could not be returned to how it was either. "
                               f"Copies of the data as it was are in {keep}: stop the server and copy them over by hand.") from e
        raise VersionError(f"The data could not be put back ({e}); every database is as it was before (copies in {keep}).") from e
    return names


def _now_sql() -> str:
    return utcnow().replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f")


def _role_rank(c, slug: str) -> Optional[int]:
    for schema in ("newer", "main"):
        try:
            r = c.execute(f"SELECT rank FROM {schema}.roles WHERE slug = ?", (slug,)).fetchone()
            if r is not None:
                return int(r[0])
        except Exception:  # noqa: BLE001 — a database from before the roles table
            pass
    try:
        from ..security import caps as C

        d = C.BUILTIN.get(slug) or C.PRESETS.get(slug)
        return int(d["rank"]) if d and "rank" in d else None
    except Exception:  # noqa: BLE001
        return None


def _after_data_restore(main: Path, replaced: Path, *, vid: int, tag: str, restored: str, actor_id: Optional[int], actor_label: str, said: str) -> list[str]:
    """Plain SQL on the restored main database — never the ORM: the running code's models may expect columns the restored
    database does not have. Never raises; returns what could not be done.
      · security made since the promote stays: disabled, locked, deleted accounts stay out; access and grants removed since
        stay removed; a lower role stays lower; passwords and two-step settings are the current ones
      · every session is revoked (everyone signs in again)
      · the audit rows written since the backup are copied back, with a marker row
      · the version rows and the rollback's own audit row"""
    import json
    import sqlite3
    from contextlib import closing

    problems: list[str] = []
    with closing(sqlite3.connect(str(main), timeout=60)) as c:
        c.execute("ATTACH DATABASE ? AS newer", (str(replaced),))

        def cols(schema: str, table: str) -> list[str]:
            try:
                return [r[1] for r in c.execute(f"PRAGMA {schema}.table_info({table})")]
            except Exception:  # noqa: BLE001
                return []

        def both(table: str) -> list[str]:
            n = set(cols("newer", table))
            return [x for x in cols("main", table) if x in n]

        def step(what: str, fn) -> None:
            try:
                fn()
                c.commit()
            except Exception as e:  # noqa: BLE001
                c.rollback()
                log_.exception("after the data restore: %s failed", what)
                problems.append(f"{what}: {e}")

        def users() -> None:
            uc = both("users")
            if not {"id", "email"} <= set(uc):
                return
            for col in ("password_hash", "must_change_password", "mfa_method", "mfa_secret", "mfa_enrolled_at", "mfa_recovery",
                        "mfa_failed", "mfa_setup_at", "mfa_proven_at", "mfa_drift", "mfa_last_step", "reset_requested_at", "expires_at"):
                if col in uc:
                    c.execute(f"UPDATE main.users SET {col} = n.{col} FROM newer.users AS n WHERE n.id = users.id AND n.email = users.email")
            if "is_active" in uc:
                c.execute("UPDATE main.users SET is_active = 0 FROM newer.users AS n WHERE n.id = users.id AND n.email = users.email AND n.is_active = 0")
                c.execute("UPDATE main.users SET is_active = 0 WHERE NOT EXISTS (SELECT 1 FROM newer.users AS n WHERE n.id = users.id AND n.email = users.email)")
            if "locked" in uc:
                c.execute("UPDATE main.users SET locked = 1" + (", locked_at = n.locked_at" if "locked_at" in uc else "") + " FROM newer.users AS n WHERE n.id = users.id AND n.email = users.email AND n.locked = 1")
            if "role" in uc:
                for uid, now_role, then_role in c.execute("SELECT u.id, n.role, u.role FROM main.users AS u JOIN newer.users AS n ON n.id = u.id AND n.email = u.email WHERE n.role <> u.role").fetchall():
                    a, b = _role_rank(c, now_role), _role_rank(c, then_role)
                    if a is None or b is None or a < b:  # lowered since (or unknown): the current, lower role stays
                        c.execute("UPDATE main.users SET role = ? WHERE id = ?", (now_role, uid))

        def access() -> None:
            if {"user_id", "tool_id"} <= set(both("tool_access")):
                c.execute("DELETE FROM main.tool_access WHERE NOT EXISTS (SELECT 1 FROM newer.tool_access AS n WHERE n.user_id = tool_access.user_id AND n.tool_id = tool_access.tool_id)")
            if {"owner_id", "viewer_id", "kind", "resource_id"} <= set(both("org_grants")):
                c.execute("DELETE FROM main.org_grants WHERE NOT EXISTS (SELECT 1 FROM newer.org_grants AS n WHERE n.owner_id = org_grants.owner_id AND n.viewer_id = org_grants.viewer_id AND n.kind = org_grants.kind AND n.resource_id = org_grants.resource_id)")
            if {"token", "revoked"} <= set(both("trusted_devices")):
                c.execute("UPDATE main.trusted_devices SET revoked = 1 WHERE NOT EXISTS (SELECT 1 FROM newer.trusted_devices AS n WHERE n.token = trusted_devices.token AND n.revoked = 0)")

        def sessions() -> None:
            if "revoked" in cols("main", "login_sessions"):
                c.execute("UPDATE main.login_sessions SET revoked = 1")

        def insert_audit(action: str, target: str, detail: str, meta: dict) -> None:
            have = set(cols("main", "audit_log"))
            row = {"at": _now_sql(), "actor_id": actor_id, "actor_label": actor_label or "system", "action": action, "target": target[:255],
                   "detail": detail, "outcome": "ok", "meta": json.dumps(meta), "ip": ""}
            row = {k: v for k, v in row.items() if k in have}
            c.execute(f"INSERT INTO main.audit_log ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})", list(row.values()))

        def audit() -> None:
            ac = both("audit_log")
            if "id" not in ac:
                return
            mx = c.execute("SELECT COALESCE(MAX(id), 0) FROM main.audit_log").fetchone()[0]
            n = c.execute(f"INSERT INTO main.audit_log ({', '.join(ac)}) SELECT {', '.join(ac)} FROM newer.audit_log WHERE id > ? ORDER BY id", (mx,)).rowcount
            insert_audit("audit_carried_over", "audit_log", f"— the data was put back to the copy made before {tag} went live; the {n} audit row(s) written after that copy (ids after {mx}) were copied back from the replaced data, so the trail has no gap —", {"rows": n, "after_id": mx})

        def versions() -> None:
            c.execute("UPDATE main.app_versions SET status = 'rolled_back', env = '' WHERE id = ?", (vid,))
            c.execute("UPDATE main.app_versions SET status = 'live', env = 'production' WHERE tag = ?", (restored,))
            insert_audit("version_rolled_back", tag, f"restored {restored}; {said}; everyone was signed out", {"data_restored": True})

        step("keeping the security changes made since", users)
        step("keeping access removed since", access)
        step("signing everyone out", sessions)
        step("copying the audit trail back", audit)
        step("recording the rollback", versions)
        c.commit()
        c.execute("DETACH DATABASE newer")
    return problems


def current_tag() -> str:
    f = LIVE_ROOT / "VERSION"
    try:
        return f.read_text(encoding="utf-8").strip().splitlines()[0].strip() if f.exists() else VERSION
    except OSError:
        return VERSION


HAND_EDIT_DIRS = ("cbx", "web")
HAND_EDIT_LIST_MAX = 20


def _tree_files(base: Path) -> dict[str, Path]:
    if not base.is_dir():
        return {}
    return {f.relative_to(base).as_posix(): f for f in base.rglob("*") if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc"}


def _same_file(a: Path, b: Path) -> bool:
    try:
        if a.stat().st_size == b.stat().st_size and a.read_bytes() == b.read_bytes():
            return True
        # line endings differ between a zip made on Linux and a file saved on Windows: not a hand edit
        return a.read_bytes().replace(b"\r\n", b"\n") == b.read_bytes().replace(b"\r\n", b"\n")
    except OSError:
        return False


def hand_edits(db: Session, exclude: Optional[AppVersion] = None) -> list[str]:
    """Files under cbx/ and web/ in the live tree that no longer match what the live version was promoted
    from — i.e. someone edited them by hand on the server. Promote overwrites them, so the owner is told.

    The reference is the unpacked package of the version that is live (`AppVersion.status == "live"`,
    `package_path`); a tree installed by setup.bat has no such reference and nothing is reported."""
    live = db.query(AppVersion).filter(AppVersion.status == "live").order_by(AppVersion.promoted_at.desc()).first()
    if live is None or (exclude is not None and live.id == exclude.id) or not live.package_path:
        return []
    ref_root = Path(live.package_path)
    if not ref_root.is_dir():
        return []
    changed: list[str] = []
    for d in HAND_EDIT_DIRS:
        ref = _tree_files(ref_root / d)
        now = _tree_files(LIVE_ROOT / d)
        if not ref and not now:
            continue
        for rel in sorted(set(ref) | set(now)):
            a, b = now.get(rel), ref.get(rel)
            if a is None or b is None or not _same_file(a, b):
                changed.append(f"{d}/{rel}")
    return changed


def hand_edits_sentence(files: list[str]) -> str:
    if not files:
        return ""
    n = len(files)
    shown = ", ".join(files[:HAND_EDIT_LIST_MAX]) + (f", … and {n - HAND_EDIT_LIST_MAX} more" if n > HAND_EDIT_LIST_MAX else "")
    return f"{n} hand-edited file{'s were' if n != 1 else ' was'} replaced: {shown}"


PROMOTABLE = ("sandbox", "rolled_back", "superseded")


def promote(db: Session, v: AppVersion, by: User, *, force: bool = False) -> AppVersion:
    """Backup the live tree, copy the new files over it, pip install, ask run.bat for a restart.

    `force` promotes a version whose sandbox tests failed. Only the owner can ask for it, only from
    the Versions page after being shown which tests failed, and the audit row says so — it exists so a
    test that is wrong for THIS server (not the code) cannot lock the owner out of updating."""
    if by.is_axl:
        raise VersionError("C-EXA can't promote a version — a person has to.")
    if v.status == "failed" and force:
        pass
    elif v.status not in PROMOTABLE:
        raise VersionError(f"Version {v.tag} is {v.status}; only a tested (sandbox) version can be promoted.")
    base = (v.tests or {}).get("base")
    if base and base != current_tag() and not force:
        raise VersionError(f"Version {v.tag} was made from v{base}, but v{current_tag()} is live now — promoting it would undo what went live since. Release it again from the Builder.")
    src = Path(v.package_path)
    if not src.exists():
        raise VersionError("The unpacked package is missing on disk. Upload it again.")
    if not _PROMOTE_LOCK.acquire(blocking=False):
        raise VersionError("Another promotion or rollback is running right now. Wait for the restart and try again.")
    from .. import jobs

    try:
        if _restart_pending:
            raise VersionError("The server is about to restart on the version just promoted. Sign in again in a minute and try then.")
        # 1.0.114: hold the background still while the database is copied and the files are replaced (best effort: a job
        # that is still running after HOLD_WAIT s keeps running — the copy is consistent either way, only a moment older)
        jobs.pause(HOLD_WAIT)
        old_tag = current_tag()
        stamp = utcnow().strftime("%Y%m%d-%H%M%S")
        backup = backups_dir() / f"{old_tag}-{stamp}"
        n = 1
        while backup.exists():  # 1.0.114: always a folder of its own — a failed promote removes it again
            n += 1
            backup = backups_dir() / f"{old_tag}-{stamp}-{n}"
        backup.mkdir(parents=True, exist_ok=True)
        try:
            edited = hand_edits(db, exclude=v)  # before anything is copied: what the live tree has that the live version didn't
        except Exception as e:  # noqa: BLE001 — the warning must never stop a promotion
            log_.warning("hand-edit check failed: %s", e)
            edited = []
        for name in PROMOTED + ("VERSION", "run.bat"):  # run.bat too, so a rollback can put it back (via run.bat.new)
            _copy_tree(LIVE_ROOT / name, backup / name)
        try:  # 1.0.114: the database too, before anything is replaced — no copy, no promote
            data_note = backup_data(backup)
        except VersionError as e:
            _rmtree(backup)
            log(db, "version_promote_failed", actor=by, target=v.tag, detail=f"database backup failed: {e}; nothing changed", outcome="failed")
            raise
        try:
            for name in PROMOTED:
                if (src / name).exists():
                    _replace_tree(src / name, LIVE_ROOT / name)
            if (src / "VERSION").exists():
                shutil.copy2(src / "VERSION", LIVE_ROOT / "VERSION")
        except Exception as e:  # noqa: BLE001 — a half-copied tree must never be left live
            log_.exception("promote of %s failed while copying; restoring %s", v.tag, old_tag)
            try:
                _restore_from(backup)
            except Exception as e2:  # noqa: BLE001
                log_.exception("restore after failed promote also failed")
                log(db, "version_promote_failed", actor=by, target=v.tag, detail=f"copy failed: {e}; restore failed: {e2}; backup {backup}", outcome="failed")
                raise VersionError(f"Copying the new files failed ({e}) and restoring the old ones failed too. The backup is at {backup}; restore it by hand before restarting.") from e
            log(db, "version_promote_failed", actor=by, target=v.tag, detail=f"copy failed: {e}; restored {old_tag}", outcome="failed")
            raise VersionError(f"Copying the new files failed ({e}); the previous version was restored and nothing changed. A file was probably locked — close Explorer windows in the install folder and try again.") from e
        if (src / "run.bat").exists():
            # can't overwrite the batch file that is running us; run.bat swaps it in on restart
            shutil.copy2(src / "run.bat", LIVE_ROOT / "run.bat.new")
        pip_log = _pip_install(LIVE_ROOT / "requirements.txt")
        (settings.data_dir / "RESTART_REQUESTED").write_text(f"{v.tag} {stamp} by {by.email}\n", encoding="utf-8")
        now = utcnow()
        for other in db.query(AppVersion).filter(AppVersion.status == "live", AppVersion.id != v.id).all():
            other.status = "superseded"  # replaced by a newer promotion — not rolled back, nothing went wrong with it
            other.env = ""
        forced = v.status == "failed"
        v.status = "live"
        v.env = "production"
        v.promoted_by_id = by.id
        v.promoted_at = now
        v.backup_path = str(backup)
        v.tests = {**(v.tests or {}), "pip": pip_log[-1500:], "hand_edited": edited[:200], **({"forced": True} if forced else {})}
        for p in db.query(Proposal).filter(Proposal.version_id == v.id).all():
            p.status = "shipped"
            p.decided_by_id = by.id
            from ..cexa.team import remember  # local import: avoid a cycle at import time

            remember(db, "self", f"proposal:{p.id}", f"Shipped “{p.title}” as {v.tag}. Watch the audit trail for a week before proposing the next thing in that area.")
        db.flush()
        note = hand_edits_sentence(edited)
        fails = list((v.tests or {}).get("failures") or [])
        forced_note = (f"; PROMOTED WITH FAILING TESTS ({len(fails)}): " + "; ".join(fails[:5])) if forced else ""
        data_said = (f"; database backed up ({', '.join(data_note['databases'])}, {data_note['bytes']:,} bytes)" if data_note.get("databases") else "") + (f"; {data_note['note']}" if data_note.get("note") else "")  # 1.0.114
        log(db, "version_promoted", actor=by, target=v.tag, detail=f"replaced {old_tag}; backup {backup}" + data_said + (f"; {note} (the edits are in the backup)" if note else "") + forced_note, hand_edited=edited[:200], outcome="ok" if not forced else "forced")
        _prune_backups({x.backup_path for x in db.query(AppVersion).filter(AppVersion.status == "live").all()})
        snap = git_snapshot(f"promote {v.tag} (replaced {old_tag}) by {by.email}")
        log(db, "version_snapshot", actor=by, target=v.tag, detail=snap, outcome="ok" if snap == "snapshot committed" else "skipped")
        _schedule_restart()
        return v
    finally:
        if not _restart_pending:  # 1.0.114: a restart is on its way: the background stays still until the process exits
            jobs.resume()
        _PROMOTE_LOCK.release()


GIT_IGNORE = "data/\n.env\n__pycache__/\n*.pyc\n.pytest_cache/\nnode_modules/\n*.log\nrun.bat.new\ndist/\nserver-tools/\n"


def git_snapshot(message: str) -> str:
    """One commit of the live tree per promote or rollback — a history to read and to recover from,
    next to the backup folders. Needs git on the server; without it this is a no-op that says so.
    data/ and .env are never in it. Never raises: a snapshot that fails must not fail a promotion."""
    git = shutil.which("git")
    if not git:
        return "git is not installed on this server — no snapshot"
    try:
        ignore = LIVE_ROOT / ".gitignore"
        if not ignore.exists():
            ignore.write_text(GIT_IGNORE, encoding="utf-8")
        else:
            have = ignore.read_text(encoding="utf-8", errors="replace")
            missing = [line for line in GIT_IGNORE.splitlines() if line and line not in have.splitlines()]
            if missing:
                ignore.write_text(have.rstrip("\n") + "\n" + "\n".join(missing) + "\n", encoding="utf-8")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0  # type: ignore[attr-defined]

        def run(*args: str) -> subprocess.CompletedProcess:
            return subprocess.run([git, *args], cwd=str(LIVE_ROOT), capture_output=True, text=True, timeout=120, creationflags=flags)

        if not (LIVE_ROOT / ".git").exists():
            r = run("init", "-q")
            if r.returncode != 0:
                return f"git init failed: {(r.stderr or r.stdout).strip()[:200]}"
        run("add", "-A")
        r = run("-c", "user.email=cbx@cbx.local", "-c", "user.name=CBX-Connect", "commit", "-q", "-m", message[:200])
        if r.returncode != 0:
            out = (r.stderr or r.stdout).strip()
            return "nothing changed — no snapshot" if "nothing to commit" in out else f"git commit failed: {out[:200]}"
        return "snapshot committed"
    except (OSError, subprocess.SubprocessError) as e:
        return f"snapshot failed: {e}"


def rollback(db: Session, v: AppVersion, by: User) -> AppVersion:
    """Restore the tree this version replaced (its backup_path). The data is not touched (1.0.114: rollback_data puts it back too)."""
    if by.is_axl:
        raise VersionError("C-EXA can't roll back a version — a person has to.")
    if v.status != "live":
        # rolling back a version that isn't live would overwrite whatever IS live with an older tree
        raise VersionError(f"Version {v.tag} is {v.status}, not live; only the live version can be rolled back.")
    if not v.backup_path or not Path(v.backup_path).exists():
        raise VersionError("There is no backup for this version to restore.")
    if not _PROMOTE_LOCK.acquire(blocking=False):
        raise VersionError("Another promotion or rollback is running right now. Wait for the restart and try again.")
    from .. import jobs

    try:
        jobs.pause(HOLD_WAIT)  # 1.0.114: the background holds still while the files go back
        b = Path(v.backup_path)
        _restore_from(b)
        if (b / "run.bat").exists():
            shutil.copy2(b / "run.bat", LIVE_ROOT / "run.bat.new")
        (settings.data_dir / "RESTART_REQUESTED").write_text(f"rollback {v.tag} by {by.email}\n", encoding="utf-8")
        v.status = "rolled_back"
        v.env = ""
        restored = current_tag()
        prev = db.query(AppVersion).filter(AppVersion.tag == restored).one_or_none()
        if prev:
            prev.status = "live"
            prev.env = "production"
        db.flush()
        log(db, "version_rolled_back", actor=by, target=v.tag, detail=f"restored {restored} from {b}")
        git_snapshot(f"rollback to {restored} by {by.email}")
        _schedule_restart()
        return v
    finally:
        if not _restart_pending:
            jobs.resume()
        _PROMOTE_LOCK.release()


PAUSE_WAIT = 60.0  # 1.0.114: how long a data rollback waits for running background work before it says "try again"
HOLD_WAIT = 20.0  # 1.0.114: how long a promote or a code-only rollback waits for it (then goes ahead)


def rollback_data(db: Session, v: AppVersion, by: User) -> dict:
    """1.0.114: Roll back the code AND put the database back as it was just before this version was promoted.

    Order, so new code never runs on old data: the background is held still; the code goes back first; then every
    database is put back (all or none, the current data kept beside the backup); then — in plain SQL, because the
    running code's models may not fit the restored tables — the security changes made since are kept, everyone is signed
    out, the audit rows since are copied back and the rollback is recorded; and the restart always follows the code.
    Returns a plain dict (no ORM after the restore)."""
    if by.is_axl:
        raise VersionError("C-EXA can't roll back a version — a person has to.")
    if v.status != "live":
        raise VersionError(f"Version {v.tag} is {v.status}, not live; only the live version can be rolled back.")
    if not v.backup_path or not Path(v.backup_path).exists():
        raise VersionError("There is no backup for this version to restore.")
    info = data_backup_info(v.backup_path)
    if not info:
        raise VersionError("This version's backup has no usable copy of the database (it was promoted before 1.0.114, the database is Postgres, or its list of databases is not valid). Roll back the code only.")
    main = _main_db()
    if main is None or main.name not in info["databases"]:
        raise VersionError("This version's backup has no copy of the main database. Roll back the code only.")
    if not _PROMOTE_LOCK.acquire(blocking=False):
        raise VersionError("Another promotion or rollback is running right now. Wait for the restart and try again.")
    from .. import jobs

    paused = False
    code_back = False
    b = Path(v.backup_path)
    vid, tag, who, actor_id = v.id, v.tag, by.email, by.id
    actor_label = who.split("@")[0]
    out: dict = {"id": vid, "tag": tag, "status": "rolled_back", "data_restored": False, "note": ""}
    try:
        if _restart_pending:
            raise VersionError("The server is about to restart. Sign in again in a minute and try then.")
        db.commit()  # this request's own reads are done: its connection must hold no lock the restore waits for
        paused = True
        if not jobs.pause(PAUSE_WAIT):
            raise VersionError(f"Background work is still running ({jobs.running()} job(s)) and could write to the data while it is put back. Nothing was changed — try again in a minute.")
        _restore_from(b)  # the code first: from here on the restart always follows
        code_back = True
        if (b / "run.bat").exists():
            shutil.copy2(b / "run.bat", LIVE_ROOT / "run.bat.new")
        restored = current_tag()
        stamp = utcnow().strftime("%Y%m%d-%H%M%S")
        keep = b / f"{DATA_BACKUP}-replaced-{stamp}"
        try:
            _restore_data(b, keep, info)
        except VersionError as e:
            # the database is the one from before, so the ORM still fits it
            out["note"] = f"The code was rolled back; the data was not: {e}"
            try:
                v.status, v.env = "rolled_back", ""
                prev = db.query(AppVersion).filter(AppVersion.tag == restored).one_or_none()
                if prev:
                    prev.status, prev.env = "live", "production"
                db.flush()
                log(db, "version_rolled_back", actor=by, target=tag, detail=f"restored {restored} from {b}; the data was NOT put back: {e}", outcome="failed", data_restored=False)
                db.commit()
            except Exception:  # noqa: BLE001
                log_.exception("rollback of %s: could not record it", tag)
                db.rollback()
            return out
        said = f"data put back as it was at {info['at'][:16].replace('T', ' ')} UTC ({', '.join(info['databases'])}); the data it replaced is kept in {keep}"
        problems = _after_data_restore(main, keep / main.name, vid=vid, tag=tag, restored=restored, actor_id=actor_id, actor_label=actor_label, said=said)
        out.update(data_restored=True, note=said + ("; NOT done: " + "; ".join(problems) if problems else ""), signed_out=True)
        return out
    finally:
        try:
            if code_back:
                try:
                    (settings.data_dir / "RESTART_REQUESTED").write_text(f"rollback {tag} by {who}\n", encoding="utf-8")
                except OSError:
                    log_.exception("could not write RESTART_REQUESTED")
                git_snapshot(f"rollback to {current_tag()} by {who}")
                _schedule_restart()
        finally:
            if paused and not _restart_pending:
                jobs.resume()
            _PROMOTE_LOCK.release()
def delete(db: Session, v: AppVersion, by: User) -> dict:
    """Remove a version that is not live: its row, its unpacked package and its zip. The live version
    can't be deleted (roll it back or promote another first), and a backup made when this version
    was promoted stays — it is the way back for whatever replaced it."""
    if by.is_axl:
        raise VersionError("C-EXA can't delete a version — a person has to.")
    if v.status == "live":
        raise VersionError(f"Version {v.tag} is live. Promote another version (or roll back) before deleting this one.")
    if _restart_pending:
        raise VersionError("The server is about to restart. Try again in a minute.")
    removed = []
    for p in (Path(v.package_path) if v.package_path else None, versions_dir() / f"{v.tag}.zip", versions_dir() / v.tag):
        if p is None or not p.exists():
            continue
        try:
            _rmtree(p)
            removed.append(p.name)
        except OSError as e:
            raise VersionError(f"Could not remove {p.name}: {e}. Close anything using it (a running sandbox, an Explorer window) and try again.") from e
    for p in db.query(Proposal).filter(Proposal.version_id == v.id).all():
        p.version_id = None
    tag, was = v.tag, v.status
    db.delete(v)
    db.flush()
    log(db, "version_deleted", actor=by, target=tag, detail=f"was {was}; removed {', '.join(removed) or 'nothing on disk (already gone)'}")
    return {"ok": True, "tag": tag, "removed": removed}


def reconcile(db: Session) -> Optional[str]:
    """Make the Versions page tell the truth about what is running. If the code that is running
    (`VERSION`) is a version the page knows about but does not show as live — because it was applied
    with update.bat, or its files were copied over by hand — mark it live and the old live row
    superseded. Returns the tag that was corrected, or None when nothing needed doing."""
    running = VERSION
    v = db.query(AppVersion).filter(AppVersion.tag == running).one_or_none()
    lives = db.query(AppVersion).filter(AppVersion.status == "live").all()
    if v is None:
        return None  # installed from a zip the page never saw: the header shows the running code anyway
    if v.status == "live" and len(lives) == 1:
        return None
    for other in lives:
        if other.id != v.id:
            other.status = "superseded"
            other.env = ""
    if v.status != "live":
        v.tests = {**(v.tests or {}), "installed_by_hand": True}
        v.status = "live"
        v.env = "production"
    db.flush()
    log(db, "version_reconciled", actor_label="server", target=running, detail="this version is what is running (applied outside the Versions page); marked live" + (f"; {len([o for o in lives if o.id != v.id])} older live row(s) marked superseded" if lives else ""))
    return running


def _pip_install(req: Path) -> str:
    if not RUN_PIP or not req.exists():
        return "pip skipped"
    try:
        # 1.0.114: pip installs what an uploaded requirements.txt names (it may build a package): the short list plus the
        # proxy and PIP_* settings it needs to reach the index — none of the server's own CBX_* secrets
        p = subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(req)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900, env=minimal_env(pip=True))
        out = (p.stdout or "") + (p.stderr or "")
        log_.info("pip install (rc=%s):\n%s", p.returncode, out[-3000:])
        return out
    except Exception as e:  # noqa: BLE001 — pip trouble must not fail the promotion
        log_.warning("pip install failed: %s", e)
        return f"pip failed: {e}"


def _schedule_restart() -> None:
    """The running process cannot replace itself on Windows; run.bat loops and restarts on exit code 3."""
    global _restart_pending
    if not RESTART_ENABLED:
        return
    _restart_pending = True

    def _bye():
        # give the request that triggered us time to commit and answer before the process goes away
        time.sleep(3.0)
        log_.warning("restart requested — exiting with code 3 for run.bat")
        os._exit(3)

    threading.Thread(target=_bye, name="cbx-restart", daemon=True).start()


# ---- packaging from a sandbox --------------------------------------------------------------------------


def next_tag(db: Session) -> str:
    tags = [v.tag for v in db.query(AppVersion).all()] + [current_tag(), VERSION]
    best = (0, 0, 0)
    for t in tags:
        m = re.match(r"^(\d+)\.(\d+)\.(\d+)", t or "")
        if m:
            best = max(best, tuple(int(x) for x in m.groups()))
    return f"{best[0]}.{best[1]}.{best[2] + 1}"


def build_zip(src: Path, tag: str) -> bytes:
    """Zip cbx/, web/, tests/, docs/, requirements.txt and a VERSION file from `src`."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for d in ALLOWED_DIRS:
            root = src / d
            if not root.is_dir():
                continue
            for f in sorted(root.rglob("*")):
                if f.is_dir() or "__pycache__" in f.parts or f.suffix in (".pyc", ".bat"):
                    continue
                if f.relative_to(src).as_posix() == SEALED_DOC:
                    continue  # written fresh below
                zf.write(f, f.relative_to(src).as_posix())
        for f in ALLOWED_FILES:
            if f == "VERSION":
                continue
            if (src / f).exists():
                zf.write(src / f, f)
            elif f == "requirements.txt":
                zf.writestr("requirements.txt", (LIVE_ROOT / "requirements.txt").read_text(encoding="utf-8") if (LIVE_ROOT / "requirements.txt").exists() else "")
        zf.writestr("VERSION", tag + "\n")
        from ..owner_doc import sealed

        # the owner's document, sealed into every release (1.0.58) — inside docs/, which every server since
        # 1.0.0 accepts in a package (a new top-level file would be refused by the server that checks it)
        zf.writestr(SEALED_DOC, sealed(src))
    return buf.getvalue()


def package_from_dir(db: Session, src: Path, *, tag: Optional[str] = None, notes: str = "", by: Optional[User] = None, run: bool = True) -> AppVersion:
    """Used by C-EXA's developer flow: turn a sandbox into a staged AppVersion (then tested)."""
    tag = tag or next_tag(db)
    init = src / "cbx" / "__init__.py"
    if init.exists():
        txt = init.read_text(encoding="utf-8")
        init.write_text(re.sub(r'VERSION\s*=\s*"[^"]*"', f'VERSION = "{tag}"', txt), encoding="utf-8")
    return stage(db, build_zip(src, tag), by, notes, run=run)


def version_out(v: AppVersion, db: Optional[Session] = None) -> dict:
    d = {
        "id": v.id,
        "tag": v.tag,
        "notes": v.notes,
        "status": v.status,
        "env": v.env,
        "tests": {k: val for k, val in (v.tests or {}).items() if k not in ("output", "pip", "hand_edited")},
        "test_output": (v.tests or {}).get("output", ""),
        # files under cbx/ and web/ that had been edited by hand on the server and were overwritten when this
        # version was promoted (they survive in the backup). Empty when nothing was, or the version isn't promoted.
        "hand_edited": list((v.tests or {}).get("hand_edited") or []),
        "hand_edited_note": hand_edits_sentence(list((v.tests or {}).get("hand_edited") or [])),
        "created_at": v.created_at.isoformat() if v.created_at else None,
        "promoted_at": v.promoted_at.isoformat() if v.promoted_at else None,
        "has_backup": bool(v.backup_path and Path(v.backup_path).exists()),
        # 1.0.114: the database as it was just before this version went live — Roll back offers to put it back
        "data_backup": data_backup_info(v.backup_path or "") if v.status == "live" else None,
        "sha256": v.sha256[:12],
    }
    if db is not None:
        p = db.query(Proposal).filter(Proposal.version_id == v.id).first()
        d["proposal"] = {"id": p.id, "title": p.title, "diff": p.diff} if p else None
    return d


def diff_against_live(v: AppVersion, limit: int = 8000) -> str:
    """Short unified diff between a staged package and the live tree (cbx/ web/ tests/ requirements)."""
    import difflib

    src = Path(v.package_path)
    out: list[str] = []
    for name in PROMOTED:
        a, b = LIVE_ROOT / name, src / name
        files = set()
        for base in (a, b):
            if base.is_dir():
                files |= {f.relative_to(base).as_posix() for f in base.rglob("*") if f.is_file() and "__pycache__" not in f.parts}
            elif base.is_file():
                files.add("")
        for rel in sorted(files):
            fa = a / rel if rel else a
            fb = b / rel if rel else b
            ta = fa.read_text(encoding="utf-8", errors="replace").splitlines() if fa.is_file() else []
            tb = fb.read_text(encoding="utf-8", errors="replace").splitlines() if fb.is_file() else []
            if ta == tb:
                continue
            label = f"{name}/{rel}" if rel else name
            out.extend(difflib.unified_diff(ta, tb, fromfile="live/" + label, tofile=f"{v.tag}/" + label, lineterm="", n=2))
            if sum(len(l) for l in out) > limit:
                break
    text = "\n".join(out)
    return text[:limit] + ("\n… (truncated)" if len(text) > limit else "")

