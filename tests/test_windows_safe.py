"""1.0.98: from CBX-Connect 2.10.3 tests/test_windows_safe.py. 2.10.1: the CBX PC runs Windows. A test file that imports a Linux/mac-only module at its top can't even be loaded
there, so Inject's test run fails before any test runs (2.10.0 did that with `import resource`). This keeps every test
file and every module of the app loadable on Windows."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POSIX_ONLY = {"resource", "fcntl", "pwd", "grp", "termios", "tty", "pty", "posix", "crypt", "spwd", "syslog", "readline"}


def _top_level_imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    for node in tree.body:  # only the module's own top level: an import inside a function or an `if` is guarded
        if isinstance(node, ast.Import):
            for a in node.names:
                yield node.lineno, a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            yield node.lineno, node.module.split(".")[0]


def test_no_linux_only_import_at_the_top_of_any_file():
    bad = []
    for folder in ("cbx", "tests", "scripts", "tools", "bridge", "migrations"):
        for p in sorted((ROOT / folder).rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            bad += [f"{p.relative_to(ROOT)}:{n} imports {m}" for n, m in _top_level_imports(p) if m in POSIX_ONLY]
    assert not bad, "Windows can't load these (put the import inside an `if sys.platform != 'win32':`):\n" + "\n".join(bad)


def test_every_sqlite_connection_is_closed():
    """2.10.2: `with sqlite3.connect(...) as c:` commits but leaves the file open. On Windows an open file can't be
    deleted or replaced, so the nightly backup's restore check failed on the CBX PC. Use closing(...) or .close()."""
    bad = []
    for folder in ("cbx", "scripts", "tools"):
        for p in sorted((ROOT / folder).rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if "with sqlite3.connect(" in line and "`with sqlite3.connect(" not in line:  # 1.0.98: a docstring naming it (`…`) is not a use
                    bad.append(f"{p.relative_to(ROOT)}:{n}")
    assert not bad, "These leave the database file open (Windows then can't delete or copy it):\n" + "\n".join(bad)


def test_every_text_file_names_its_encoding():
    """2.10.5 / 1.0.100: on Windows a text file opened without an encoding is read as cp1252, so a file with "→" or "é"
    in it fails there (it read fine on Linux): 1.0.99's test step stopped on the CBX PC with UnicodeDecodeError.
    Every Path.read_text / write_text and every built-in open() in text mode says encoding="utf-8"."""
    bad = []
    for folder in ("cbx", "tests", "tools", "bridge", "scripts"):
        for p in sorted((ROOT / folder).rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            tree = ast.parse(p.read_text(encoding="utf-8"), str(p))
            for n in ast.walk(tree):
                if not isinstance(n, ast.Call) or any(k.arg in ("encoding", None) for k in n.keywords):
                    continue
                f = n.func
                if isinstance(f, ast.Attribute) and f.attr == "read_text" and not n.args:
                    bad.append(f"{p.relative_to(ROOT)}:{n.lineno} read_text")
                elif isinstance(f, ast.Attribute) and f.attr == "write_text" and len(n.args) < 2:
                    bad.append(f"{p.relative_to(ROOT)}:{n.lineno} write_text")
                elif isinstance(f, ast.Name) and f.id == "open":
                    mode = n.args[1] if len(n.args) > 1 else next((k.value for k in n.keywords if k.arg == "mode"), None)
                    m = mode.value if isinstance(mode, ast.Constant) and isinstance(mode.value, str) else ("" if mode is None else "?")
                    if "b" not in m and m != "?" and len(n.args) < 4:
                        bad.append(f"{p.relative_to(ROOT)}:{n.lineno} open")
    assert not bad, "Say encoding=\"utf-8\" (Windows reads cp1252 otherwise):\n" + "\n".join(bad)
