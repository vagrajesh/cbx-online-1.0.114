"""AI check (Edwin, 12 Oct: "if we get any problems in the app, you must let ai check to fix it, or use ai if
needed, a small llm to test or check").

When something in the app fails, this looks at it before a person has to:

  1. Known problems, by rule — fixed by the app itself when that is safe, otherwise said in plain words:
       a reader library missing on the server (xlrd for old .xls files, python-calamine …) → installed, then the
       step is tried again;  a file open in Excel;  the wrong file in a slot;  not really an Excel file.
  2. Anything else → the small model on the AI box reads the error and says, in two plain sentences, what went
     wrong and what to do (or that Edwin must look at it). In stub mode (tests, a box without the AI) step 2 is
     skipped and the rule words stand.

    check(where, error, context="", db=None) -> {"fix": str, "fixed": bool, "words": str, "by": "rule"|"ai"|""}
    note(where, error, ...)                  -> the line shown on the failed row: the error, then "AI check: …"
    with_retry(fn, where)                    -> runs fn; on a fixable failure fixes it and runs fn once more

Never raises. The same file in the old CBX and CBX-Connect 2 (only the model call differs, see _ask_model).
"""
from __future__ import annotations

import logging
import re
import subprocess
import sys
import threading
from typing import Callable, Optional

log = logging.getLogger("cbx.aicheck")

# packages the app may install by itself when a step needs them (pip name → import name). Nothing else, ever.
SAFE_PACKAGES = {"xlrd": "xlrd", "python-calamine": "python_calamine", "openpyxl": "openpyxl", "pandas": "pandas", "python-docx": "docx", "lxml": "lxml"}
_IMPORT_TO_PIP = {v: k for k, v in SAFE_PACKAGES.items()}
_installed_now: set[str] = set()
_lock = threading.Lock()

RULES: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"(?:Import|install|No module named)\W+['`\"]?(xlrd|python[-_]calamine|openpyxl|pandas|docx|lxml)", re.I), "package",
     "The server was missing the {pkg} library it needs to read this file."),
    (re.compile(r"Permission denied|being used by another process|PermissionError", re.I), "",
     "The file is open somewhere else (Excel?). Close it and try again."),
    (re.compile(r"Could not find a 'Transaction #' header", re.I), "",
     "This file doesn't look like the LA transaction report: no 'Transaction #' column. Check the file in the LA slot."),
    # 1.0.106: said the real RR06 wasn't one (its move type column has no heading; the engine reads it now)
    (re.compile(r"No movement sheet found", re.I), "",
     "The movements slot needs the RR06 / ARO06 export with its Contract Movement tab (move types and lease references). If the note says the file belongs in another slot, upload it there."),
    (re.compile(r"does not look like the LA ledger export", re.I), "",
     "The ledger check needs the Lease Accelerator ledger export (its Ledger Entries tab). If the note says the file is another one, use it in that slot instead."),
    (re.compile(r"File is not a zip file|Excel file format cannot be determined|Unsupported format|not supported|BOF record|Expected BOF|not a valid Excel file", re.I), "",
     "The file isn't a real Excel workbook (it may be a web page saved with an .xls name). Open it in Excel, Save As .xlsx, and upload that."),
    (re.compile(r"no longer (?:on the server|available)", re.I), "",
     "The stored file is gone from the server. Upload it again."),
    (re.compile(r"database is locked", re.I), "",
     "The database was busy for a moment. Try again; if it keeps happening, tell Edwin."),
    (re.compile(r"timed out|No answer within", re.I), "",
     "It took too long to answer. Try again in a minute."),
]


def _pip_name(token: str) -> str:
    t = token.lower().replace("_", "-")
    if t == "docx":
        return "python-docx"
    return t if t in SAFE_PACKAGES else _IMPORT_TO_PIP.get(token.lower(), "")


def install(pkg: str) -> bool:
    """pip install one package from SAFE_PACKAGES (once per run). True when it imports afterwards."""
    if pkg not in SAFE_PACKAGES:
        return False
    import importlib

    mod = SAFE_PACKAGES[pkg]
    with _lock:
        try:
            importlib.import_module(mod)
            return True
        except ImportError:
            pass
        if pkg in _installed_now:
            return False
        _installed_now.add(pkg)
        try:
            from .config import settings

            if getattr(settings, "testing", False) or "pytest" in sys.modules:
                return False  # never pip during tests
        except Exception:  # noqa: BLE001
            pass
        try:
            r = subprocess.run([sys.executable, "-m", "pip", "install", "--no-input", "--quiet", pkg], capture_output=True, text=True, timeout=600)
            log.warning("aicheck: pip install %s -> %s", pkg, r.returncode)
        except Exception as e:  # noqa: BLE001
            log.warning("aicheck: pip install %s failed: %s", pkg, e)
            return False
        importlib.invalidate_caches()
        try:
            importlib.import_module(mod)
            return True
        except ImportError:
            return False


def _model_on() -> bool:
    try:
        from .config import settings

        return (settings.model_mode or "live") != "stub"
    except Exception:  # noqa: BLE001
        return False


SYSTEM = ("You check a step that failed inside CBX-Connect, an internal business app used by finance and facilities people. "
          "You get where it failed and the error. Answer in at most two short plain sentences for a non-technical person: "
          "what went wrong, and what they should do next. No jargon, no code, no dashes. If it looks like a fault in the app "
          "itself rather than the person's file or input, say: Edwin needs to look at this.")


def _ask_model(where: str, error: str, context: str, db=None) -> str:
    """The small model's two sentences ('' when the AI box is off or doesn't answer)."""
    if not _model_on():
        return ""
    try:
        try:
            from .ai.gateway import Gateway  # CBX-Connect 2
        except ImportError:
            from .cexa.gateway import Gateway  # the old CBX
        own = db is None
        if own:
            from .db import SessionLocal

            db = SessionLocal()
        try:
            out = Gateway(db).text("problem_check", SYSTEM, f"Where: {where}\nError: {error[:1500]}\n" + (f"Context: {context[:800]}\n" if context else ""))
        finally:
            if own:
                db.close()
        out = re.sub(r"\s+", " ", (out or "").strip())
        return "" if out.lower() in ("", "ok", "{}") else out[:400]
    except Exception as e:  # noqa: BLE001 — the check must never become the problem
        log.info("aicheck model skipped: %s", e)
        return ""


def check(where: str, error: str, context: str = "", db=None, *, fix: bool = True) -> dict:
    err = str(error or "")
    for rx, kind, words in RULES:
        m = rx.search(err)
        if not m:
            continue
        if kind == "package":
            pkg = _pip_name(m.group(1))
            ok = bool(pkg) and fix and install(pkg)
            w = words.format(pkg=pkg or m.group(1)) + (" The app installed it; it's being tried again." if ok else
                                                          " Ask IT or Edwin to run: pip install " + (pkg or m.group(1)) + ".")
            return {"fix": f"install:{pkg}", "fixed": ok, "words": w, "by": "rule"}
        return {"fix": "", "fixed": False, "words": words, "by": "rule"}
    words = _ask_model(where, err, context, db)
    return {"fix": "", "fixed": False, "words": words, "by": "ai" if words else ""}


def note(where: str, error: str, context: str = "", db=None, *, limit: int = 1000) -> str:
    """The failed row's note: the error as it was, then the AI check's words."""
    c = check(where, error, context, db, fix=False)
    base = str(error or "").strip()
    return (base + (f"\nAI check: {c['words']}" if c["words"] else ""))[:limit]


def with_retry(fn: Callable, where: str, context: str = "", db=None):
    """Run fn(); if it fails on something the app can fix by itself (a missing reader library), fix it and run fn()
    once more. The second failure (or an unfixable first one) is raised as it was."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        c = check(where, str(e), context, db, fix=True)
        if c["fixed"]:
            log.warning("aicheck: %s fixed (%s); trying again", where, c["fix"])
            return fn()
        raise


def background(kind: str, target: str, detail: str) -> None:
    """For an error the app noticed about itself (a crashed job or request): the AI check runs on its own thread
    and leaves its words in the audit trail beside the error. Never blocks, never raises."""
    if "pytest" in sys.modules:
        return

    def run():
        try:
            c = check(f"{kind} {target}", detail)
            if not c["words"]:
                return
            from .db import SessionLocal
            from .models import AuditLog

            with SessionLocal() as s:
                s.add(AuditLog(actor_label="AI check", action="ai_check", target=(target or "")[:255], detail=c["words"][:2000],
                               outcome="ok" if c["fixed"] else "failed"))
                s.commit()
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=run, name="aicheck", daemon=True).start()
