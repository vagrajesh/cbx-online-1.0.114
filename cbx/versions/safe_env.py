"""1.0.114: the environment code from an uploaded version runs in — an approved short list, never the server's own.

Before 1.0.114 an uploaded zip's tests (injector.run_tests) and a sandbox instance (sandbox_runner.child_env) got the
whole server environment minus a short blocklist, so a secret set on the server (CBX_MS_CLIENT_SECRET, a proxy
password, a cloud key) reached code nobody had read yet. Now only what Windows and Python need to start, find their
folders and print text is passed on, plus the throw-away CBX_* values the caller names (a temp data dir, CBX_TESTING,
CBX_SANDBOX_RUN, …). The proxy and PIP_* settings go to the pip step only (they often carry a password or a token).
Same idea as CBX-Connect 2's `cbx/inject/versions.py: minimal_env`.
"""
from __future__ import annotations

import os
from typing import Optional

# Compared in upper case: on Windows os.environ hands its names back upper-cased (SystemRoot → SYSTEMROOT).
KEEP = frozenset({
    # Windows: a process without SYSTEMROOT can't even seed its random numbers; the rest are folders and names
    # Python and the libraries look up (tempfile, getpass, user site-packages, Tesseract in Program Files)
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "OS",
    "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA",
    "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)",
    "USERNAME", "USER", "LOGNAME", "COMPUTERNAME", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
    # text and time
    "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "TZ",
    # where the OCR data and the test browser live (paths, not secrets)
    "TESSDATA_PREFIX", "PLAYWRIGHT_BROWSERS_PATH",
    # which certificates to trust (paths)
    "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "SSL_CERT_DIR", "CURL_CA_BUNDLE",
})
# 1.0.114: Python's own switches, a fixed list — not every PYTHON* name (PYTHONSTARTUP, PYTHONBREAKPOINT, PYTHONWARNINGS … are
# not needed to run, and PYTHONPATH is set by the caller)
PYTHON_KEEP = frozenset({"PYTHONHOME", "PYTHONUTF8", "PYTHONNOUSERSITE", "PYTHONDONTWRITEBYTECODE", "PYTHONHASHSEED", "PYTHONIOENCODING"})
# The proxy address often carries the service account's password, PIP_INDEX_URL a token: pip only.
PROXY = frozenset({"HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY"})


def minimal_env(*, pythonpath: str = "", cbx: Optional[dict] = None, pip: bool = False, base: Optional[dict] = None) -> dict:
    """A clean environment for a subprocess running code from a zip: the KEEP list, Python's own switches in PYTHON_KEEP
    (never PYTHONPATH — the caller says where the code is), and only the CBX_* values passed in `cbx`. With
    pip=True also the proxy and PIP_* settings. `base` is the environment to pick from (default os.environ)."""
    src = os.environ if base is None else base
    env: dict[str, str] = {}
    for k, v in src.items():
        u = k.upper()
        if u in KEEP or u in PYTHON_KEEP or (pip and (u in PROXY or u.startswith("PIP_"))):
            env[k] = v
    env["PYTHONIOENCODING"] = "utf-8"  # output comes back through a pipe: never a cp1252 surprise
    if pythonpath:
        env["PYTHONPATH"] = pythonpath
    for k, v in (cbx or {}).items():
        if v is not None:
            env[k] = str(v)
    return env
