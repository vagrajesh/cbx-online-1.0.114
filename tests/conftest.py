import os
import tempfile

os.environ["CBX_TESTING"] = "1"
# the suite signs in a dozen people a hundred times; the second step has its own tests (test_mfa.py)
os.environ.setdefault("CBX_MFA_REQUIRED", "none")
os.environ.setdefault("CBX_TEAMS_AUTO", "false")  # standing teams run when a test says so
os.environ.setdefault("CBX_STRICT_DB_LOCKS", "true")  # a model call holding the write lock fails the test (gateway.guard_write_lock)
os.environ.setdefault("CBX_ENTITY_COMPLIANCE_COPY", "false")  # tests/test_entity_compliance_moves.py runs the copy itself
os.environ.setdefault("CBX_BACKUPS", "false")  # the nightly backup runs when a test says so (tests/test_backup.py)
os.environ.setdefault("CBX_TEAM_SCREENSHOTS", "false")  # C-EXA's designer reads layouts in the suite; tests/test_team.py drives the preview page itself
os.environ.setdefault("CBX_CODE_FALLBACK", "let_in")  # the suite signs in hundreds of times with no mailer; tests/test_devices.py tests "terminal"
os.environ.setdefault("CBX_MAIL_DRAFTS", "")  # mail goes straight out in the suite; tests/test_mail_drafts.py holds it
_tmp = tempfile.mkdtemp(prefix="cbxtest-")
os.environ["CBX_DATA_DIR"] = _tmp


def _cleanup_test_dir(path: str) -> None:
    """1.0.112 / 2.10.15: the suite's throwaway data folder (~1 GB) is removed when the run ends — each Versions "Upload &
    test" on the CBX PC used to leave one behind in %TEMP%. The database is closed first (Windows won't delete an open file);
    anything still held is left, never an error."""
    import shutil

    try:
        from cbx.db import engine

        engine.dispose()
    except Exception:  # noqa: BLE001
        pass
    shutil.rmtree(path, ignore_errors=True)


import atexit  # noqa: E402

atexit.register(_cleanup_test_dir, _tmp)


def _install_missing_requirements() -> None:
    """When the version injector runs this package's tests (CBX_SANDBOX_RUN=1), the server's Python may
    lack a library this version newly needs (pandas for IFRS 16). Install what is missing first, so a
    version can be tested — and promoted — without anyone opening a terminal on the server."""
    if not os.environ.get("CBX_SANDBOX_RUN"):
        return
    import importlib.util
    import re
    import subprocess
    import sys
    from pathlib import Path

    req = Path(__file__).resolve().parents[1] / "requirements.txt"
    if not req.exists():
        return
    # distribution name → import name where they differ
    alias = {"pyyaml": "yaml", "pillow": "PIL", "python-multipart": "multipart", "pymupdf": "fitz", "argon2-cffi": "argon2", "pydantic-settings": "pydantic_settings", "python-docx": "docx", "pdfplumber": "pdfplumber", "pywin32": "win32api"}
    missing = []
    for line in req.read_text(encoding="utf-8").splitlines():
        name = re.split(r"[<>=!~\[; #]", line.strip(), 1)[0].strip().lower()
        if not name:
            continue
        mod = alias.get(name, name.replace("-", "_"))
        if name == "psycopg":
            continue  # Postgres driver — only needed when CBX_DATABASE_URL points at Postgres
        if importlib.util.find_spec(mod) is None:
            missing.append(line.split("#")[0].strip())
    if missing:
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", *missing], check=False, timeout=900)


_install_missing_requirements()

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from cbx import jobs  # noqa: E402
from cbx.bootstrap import bootstrap  # noqa: E402
from cbx.db import SessionLocal  # noqa: E402
from cbx.main import app  # noqa: E402
from cbx.models import User  # noqa: E402

jobs.set_inline(True)

STRONG = "correct-horse-battery-9"


@pytest.fixture(scope="session")
def owner_pw():
    return bootstrap("owner@test.local", "Test Owner")


@pytest.fixture()
def db():
    s = SessionLocal()
    try:
        yield s
        s.commit()
    finally:
        s.close()


@pytest.fixture()
def client(owner_pw):
    with TestClient(app) as c:
        yield c


def login(client: TestClient, email: str, password: str) -> dict:
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    if r.json()["must_change_password"]:
        r2 = client.post("/api/auth/change-password", json={"new_password": STRONG, "repeat": STRONG})
        assert r2.status_code == 200, r2.text
    return r.json()["user"]


@pytest.fixture()
def owner(client, owner_pw, db):
    u = db.query(User).filter(User.email == "owner@test.local").one()
    pw = owner_pw if u.must_change_password else STRONG
    login(client, "owner@test.local", pw)
    return client


def _make(owner_client, email, name, role, tools=None, team="Utilities billing"):
    r = owner_client.post("/api/users", json={"email": email, "name": name, "role": role, "team_name": team, "tools": tools or []})
    if r.status_code == 400 and "already" in r.text:
        r = owner_client.post(f"/api/users/{_uid(owner_client, email)}/temp-password")
    assert r.status_code == 200, r.text
    return r.json()["temp_password"]


def _uid(owner_client, email):
    return next(u["id"] for u in owner_client.get("/api/users").json()["users"] if u["email"] == email)


@pytest.fixture()
def admin(owner):
    pw = _make(owner, "admin@test.local", "Arnab Admin", "admin")
    c = TestClient(app)
    login(c, "admin@test.local", pw)
    return c


@pytest.fixture()
def user(owner):
    tools = owner.get("/api/users").json()["tools"]
    ids = [t["id"] for t in tools if not t["admin_only"] and t["slug"] != "support-desk"]  # 1.0.96: the Support desk is a grant, not a tool
    pw = _make(owner, "thandi@test.local", "Thandi Mokoena", "user", tools=ids)
    c = TestClient(app)
    login(c, "thandi@test.local", pw)
    return c


# 1.0.104 (Builder R): the AP review gates — the SAP upload waits for the review of the documents picked for it (V05) and an
# invoice that is not a valid tax invoice is red until a reviewer decides (V06) — are on in every portfolio by default
# (cbx/ap/portfolio.py DEFAULTS). The AP tests written before them describe the flow without them, so every test runs with
# both off unless it asks for `ap_review_gates` (tests/test_ap_v1104_review.py does): the gates have their own tests.
@pytest.fixture(autouse=True)
def _ap_review_gates_off(monkeypatch):
    from cbx.ap import portfolio as _PF

    monkeypatch.setitem(_PF.DEFAULTS, "review_before_upload", False)
    monkeypatch.setitem(_PF.DEFAULTS, "vat_invalid", "off")


@pytest.fixture()
def ap_review_gates(monkeypatch):
    """The 1.0.104 review gates as they ship (on)."""
    from cbx.ap import portfolio as _PF

    monkeypatch.setitem(_PF.DEFAULTS, "review_before_upload", True)
    monkeypatch.setitem(_PF.DEFAULTS, "vat_invalid", "red")
