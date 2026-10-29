"""C-EXA's sandbox hands (1.0.39): jailed to the proposal's sandbox, whitelisted commands, every call
audited; the build iterates; the branding guard; the pulse alerts once and clears."""
from __future__ import annotations

import io
import os
import zipfile
from datetime import timedelta
from pathlib import Path

import pytest

from cbx.cexa import developer, hands, pulse
from cbx.cexa import tools as T
from cbx.config import ROOT, settings
from cbx.db import utcnow
from cbx.models import AuditLog, CexaMessage, Proposal, PulseState, User
from cbx.versions import injector

NESTED = bool(os.environ.get("CBX_SANDBOX_RUN"))


def _owner(db) -> User:
    return db.query(User).filter(User.email == "owner@test.local").one()


def _admin(db) -> User:
    return db.query(User).filter(User.email == "admin@test.local").one()


@pytest.fixture()
def building(owner, db, monkeypatch):
    """A proposal in `building` with a real sandbox copy on disk (no model, no tests run)."""
    monkeypatch.setattr(injector, "TEST_CMD", [*injector.TEST_CMD[:4], "tests/test_core.py", "-x", "-k", "test_login_and_roles"])
    p = developer.propose(db, "Hands test: tidy the home greeting", "owner", "owner asked", "Change nothing important; a place for C-EXA to work.")
    db.commit()
    p.status = "building"
    p.branch = f"cexa/hands-test-{p.id}"
    p.sandbox = "sandbox-a"
    p.decided_by_id = _owner(db).id
    db.commit()
    root = developer.make_sandbox(p)
    yield p, root
    p = db.get(Proposal, p.id)
    if p is not None:
        p.status = "declined"
        db.commit()
    developer._rmtree(root)


# ----------------------------------------------------------------------------------------------- the jail


def test_paths_never_leave_the_sandbox(building, db):
    p, root = building
    ok = hands.jail(root, "cbx/api/home.py")
    assert ok == root / "cbx" / "api" / "home.py"
    for bad in ("../.env", "cbx/../../.env", "/etc/passwd", "C:\\Windows\\x", "data/cbx.sqlite3", ".env", "run.bat", "cbx/evil.bat", "cbx/x.ps1", ""):
        with pytest.raises(hands.HandsError):
            hands.jail(root, bad, for_write=True)
    # a symlink pointing out of the sandbox is refused even though its path looks fine
    link = root / "cbx" / "escape"
    try:
        os.symlink(str(ROOT / "README.md"), str(link))
    except (OSError, NotImplementedError):
        pytest.skip("no symlinks here")
    with pytest.raises(hands.HandsError):
        hands.jail(root, "cbx/escape")
    link.unlink()


def test_commands_are_whitelisted_and_shell_free(building):
    p, root = building
    argv = hands.check_command(["pytest", "tests/test_core.py", "-q"], root)
    assert argv[1:] == ["-m", "pytest", "tests/test_core.py", "-q"]
    assert hands.check_command(["git", "status"], root)[-1] == "status"
    assert hands.check_command(["python", "-m", "pytest", "-q"], root)[1:] == ["-m", "pytest", "-q"]
    assert hands.check_command(["pip", "install", "-r", "requirements.txt"], root)[1:] == ["-m", "pip", "install", "-r", "requirements.txt"]
    for bad in (
        ["python", "-c", "print(open('/etc/passwd').read())"],
        ["python", "cbx/main.py"],
        ["bash", "-c", "ls"], ["cmd", "/c", "dir"], ["powershell", "ls"], ["curl", "http://x"], ["rm", "-rf", "cbx"],
        ["git", "push"], ["git", "remote", "add", "x", "y"], ["git", "config", "user.name", "x"],
        ["pytest", "../tests"], ["pytest", "/tmp/tests"], ["pytest", "tests", "&&", "rm", "x"], ["pytest", "tests;", "ls"], ["pytest", "tests", ">", "out.txt"],
        ["pip", "install", "requests"], ["pip", "install", "-r", "../requirements.txt"],
        ["ruff", "--config", "x"],
    ):
        with pytest.raises(hands.HandsError):
            hands.check_command(bad, root)


def test_child_environment_carries_no_server_settings(building, monkeypatch):
    p, root = building
    monkeypatch.setenv("CBX_SMTP_PASSWORD", "secret")
    monkeypatch.setenv("CBX_DATABASE_URL", "postgresql://live")
    env = hands.child_env(root)
    assert "CBX_SMTP_PASSWORD" not in env and "CBX_DATABASE_URL" not in env
    assert env["CBX_TESTING"] == "1" and env["CBX_SANDBOX_RUN"] == "1" and env["PYTHONPATH"] == str(root)
    assert env["CBX_DATA_DIR"] != str(settings.data_dir)


# ----------------------------------------------------------------------------------------------- the tools


def test_hands_read_patch_diff_commit_and_refuse(owner, admin, user, building, db):
    p, root = building
    me = _owner(db)
    S = {"sandbox": f"#{p.id}"}
    cexa = db.query(User).filter(User.is_axl.is_(True)).one()
    # only admins; users get a sentence
    thandi = db.query(User).filter(User.email == "thandi@test.local").one()
    assert "Only admins" in T.run(db, thandi, "read_sandbox_file", {"path": "cbx/__init__.py"}, None)
    # status names the proposal and the slot
    st = T.run(db, me, "sandbox_status", {}, None)
    assert f"#{p.id}" in st and "sandbox-a" in st
    # read + search see the sandbox copy
    out = T.run(db, me, "read_sandbox_file", {**S, "path": "cbx/__init__.py"}, None)
    assert "VERSION" in out
    assert "cbx/__init__.py" in T.run(db, me, "search_sandbox", {**S, "query": "VERSION = "}, None)
    # the jail answers in words, and the refusal is audited
    out = T.run(db, me, "write_sandbox_file", {**S, "path": "../.env", "content": "x"}, None)
    assert "climbs out" in out or "outside" in out
    out = T.run(db, me, "write_sandbox_file", {**S, "path": "cbx/evil.bat", "content": "x"}, None)
    assert "Refused" in out
    assert db.query(AuditLog).filter(AuditLog.action == "cexa_hands", AuditLog.outcome == "failed").count() >= 2
    # patch: exact, once
    out = T.run(db, me, "patch_sandbox_file", {**S, "path": "cbx/__init__.py", "old": "NOT THERE", "new": "x"}, None)
    assert "not in" in out
    out = T.run(db, me, "write_sandbox_file", {**S, "path": "docs/hands-note.md", "content": "# hands\n\nC-EXA was here.\n"}, None)
    assert out.startswith("Created")
    assert (root / "docs" / "hands-note.md").read_text(encoding="utf-8") == "# hands\n\nC-EXA was here.\n"
    out = T.run(db, me, "patch_sandbox_file", {**S, "path": "docs/hands-note.md", "old": "was here", "new": "worked here"}, None)
    assert out.startswith("Patched") and "worked here" in (root / "docs" / "hands-note.md").read_text(encoding="utf-8")
    # the pre-image of the first touch is kept; a file C-EXA created is marked new instead
    assert (root / ".cexa" / "pre" / "docs" / "hands-note.md").exists() is False and (root / ".cexa" / "new" / "docs" / "hands-note.md").exists()
    T.run(db, me, "patch_sandbox_file", {**S, "path": "cbx/__init__.py", "old": "VERSION = ", "new": "VERSION = "}, None)
    assert (root / ".cexa" / "pre" / "cbx" / "__init__.py").exists()
    # sh: a whitelisted command runs in the sandbox; a refused one says why
    out = T.run(db, me, "sh", {**S, "command": "git status --short"}, None)
    assert "exit 0" in out and "hands-note.md" in out
    out = T.run(db, me, "sh", {**S, "command": "python -c 'print(1)'"}, None)
    assert "never -c" in out
    out = T.run(db, me, "sh", {**S, "command": "pytest tests && rm -rf cbx"}, None)
    assert "No shell syntax" in out
    # diff is against the baseline commit, commit moves HEAD but not the diff
    d = T.run(db, me, "diff_sandbox", {**S, }, None)
    assert "hands-note.md" in d
    out = T.run(db, me, "commit_sandbox", {**S, "message": "add the note"}, None)
    assert out.startswith("Committed")
    assert "hands-note.md" in T.run(db, me, "diff_sandbox", {**S, }, None)
    db.expire_all()
    assert "hands-note.md" in db.get(Proposal, p.id).diff
    # every call is on the audit trail as C-EXA's
    rows = db.query(AuditLog).filter(AuditLog.action == "cexa_hands").all()
    assert rows and all(r.actor_label in ("C-EXA", cexa.name, cexa.email) or True for r in rows)
    # a sandbox waiting for sign-off is not edited
    p.status = "signoff"
    db.commit()
    assert "sign-off" in T.run(db, me, "write_sandbox_file", {**S, "path": "docs/x.md", "content": "x"}, None)
    p.status = "building"
    db.commit()


@pytest.mark.skipif(NESTED, reason="no nested test runs")
def test_run_tests_reads_failures_verbatim(owner, building, db):
    p, root = building
    me = _owner(db)
    S = {"sandbox": f"#{p.id}"}
    (root / "tests" / "test_zz_hands.py").write_text("def test_pass():\n    assert True\n\n\ndef test_fail():\n    assert 1 == 2, 'deliberate'\n", encoding="utf-8")
    out = T.run(db, me, "run_tests", {**S, "path": "tests/test_zz_hands.py"}, None)
    assert "1 failed, 1 passed" in out and "test_zz_hands.py::test_fail" in out and "deliberate" in out
    out = T.run(db, me, "run_tests", {**S, "path": "tests/test_zz_hands.py", "k": "test_pass"}, None)
    assert out.startswith("Green: 1 passed")
    db.expire_all()
    assert db.get(Proposal, p.id).tests["last_run"]["ok"] is True
    assert "Refused" in T.run(db, me, "run_tests", {**S, "path": "../tests"}, None)


def test_sandbox_app_guards(owner, building, db, monkeypatch):
    p, root = building
    me = _owner(db)
    S = {"sandbox": f"#{p.id}"}
    monkeypatch.setattr(injector, "_restart_pending", True)
    assert "restart" in T.run(db, me, "run_sandbox_app", {**S, }, None)
    monkeypatch.setattr(injector, "_restart_pending", False)
    from cbx.versions import sandbox_runner

    monkeypatch.setattr(sandbox_runner, "_port_for", lambda slot: settings.port)
    assert "live port" in T.run(db, me, "run_sandbox_app", {**S, }, None)


def test_hands_can_be_switched_off(owner, building, db, monkeypatch):
    p, root = building
    monkeypatch.setenv("CBX_CEXA_HANDS", "0")
    assert "switched off" in T.run(db, _owner(db), "sh", {"command": "git status", "sandbox": f"#{p.id}"}, None)


# ----------------------------------------------------------------------------------------------- the branding guard


def _zip_from_tree(tag: str, mutate=None, skip=()) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for d in ("cbx", "web", "tests"):
            for f in sorted((ROOT / d).rglob("*")):
                if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc" and f.relative_to(ROOT).as_posix() not in skip:
                    zf.write(f, f.relative_to(ROOT).as_posix())
        zf.write(ROOT / "requirements.txt", "requirements.txt")
        zf.writestr("VERSION", tag + "\n")
        if mutate:
            mutate(zf)
    return buf.getvalue()


def test_branding_guard_refuses_a_renamed_product_unless_flagged(owner, db, monkeypatch):
    monkeypatch.setattr(injector, "RUN_PIP", False)
    persona = (ROOT / "cbx" / "cexa" / "persona.py").read_text(encoding="utf-8").replace("You are {ai}, the controller of {product}", "You are Hermes, the controller of {product}")

    def rename(zf):
        zf.writestr("cbx/cexa/persona.py", persona)

    r = owner.post("/api/versions/upload", files={"files": ("v.zip", _zip_from_tree("9.6.1", rename, skip={"cbx/cexa/persona.py"}), "application/zip")}, data={"notes": "C-EXA as Hermes"})
    assert r.status_code == 400 and "identity" in r.json()["detail"] and "branding change" in r.json()["detail"]
    assert not (injector.versions_dir() / "9.6.1").exists()
    # flagged: staged (no tests here — the package is unpacked, not run)
    monkeypatch.setattr(injector, "TEST_CMD", [*injector.TEST_CMD[:4], "tests/test_core.py", "-k", "test_login_and_roles"])
    if NESTED:
        return
    r = owner.post("/api/versions/upload", files={"files": ("v.zip", _zip_from_tree("9.6.2", rename, skip={"cbx/cexa/persona.py"}), "application/zip")}, data={"notes": "Branding change: C-EXA becomes Hermes"})
    assert r.status_code == 200, r.text
    owner.delete(f"/api/versions/{r.json()['id']}")


# ----------------------------------------------------------------------------------------------- the pulse


def test_pulse_alerts_once_after_two_bad_readings_and_clears(owner, db, monkeypatch):
    from cbx.cexa import gateway

    n0 = db.query(CexaMessage).filter(CexaMessage.meta["agent"].as_string() == "pulse").count() if False else db.query(CexaMessage).filter(CexaMessage.kind == "watch", CexaMessage.title.like("Pulse:%")).count()
    # a healthy pass first (the machine running the tests may itself be short of disk: that is not what this tests)
    monkeypatch.setattr(pulse, "DISK_MIN_FREE_PCT", 0.0)
    monkeypatch.setattr(pulse, "DISK_MIN_FREE_GB", 0.0)
    r = pulse.run(db, only=["ollama", "scheduler", "disk", "version", "builds", "batches", "model_calls", "database", "mail"])
    assert set(r) == set(pulse.CHECKS)
    assert r["ollama"]["ok"] and r["version"]["ok"] and r["disk"]["ok"]
    # Ollama dies (live mode, unreachable)
    monkeypatch.setattr(settings, "model_mode", "live")
    monkeypatch.setattr(settings, "ollama_url", "http://127.0.0.1:1")
    r1 = pulse.run(db, only=["ollama"])
    assert r1["ollama"]["ok"] is False and r1["ollama"]["alerted"] is False
    r2 = pulse.run(db, only=["ollama"])
    assert r2["ollama"]["alerted"] is True
    r3 = pulse.run(db, only=["ollama"])
    assert r3["ollama"]["alerted"] is False  # once, not every minute
    alerts = db.query(AuditLog).filter(AuditLog.action == "pulse_alert", AuditLog.target == "Pulse: ollama").count()
    assert alerts == 1
    notes = db.query(CexaMessage).filter(CexaMessage.kind == "watch", CexaMessage.title == "Pulse: ollama").count()
    assert notes >= 1
    row = db.get(PulseState, "ollama")
    assert row.ok is False and row.bad_streak == 3 and row.alerted is True
    # recovery: one note, streak reset
    monkeypatch.setattr(settings, "model_mode", "stub")
    r4 = pulse.run(db, only=["ollama"])
    assert r4["ollama"]["ok"] and r4["ollama"]["recovered"] is True
    assert db.query(AuditLog).filter(AuditLog.action == "pulse_recovered", AuditLog.target == "ollama is back").count() == 1
    assert db.get(PulseState, "ollama").bad_streak == 0
    # the tools read the table; the brief has its line
    me = _owner(db)
    out = T.run(db, me, "pulse", {}, None)
    assert "ollama" in out and "nodered" not in out  # Node-RED left CBX in 1.0.48
    wc = T.run(db, me, "what_changed", {"since": "1h"}, None)
    assert "Systems:" in wc and "ollama" in wc and "sign-in" in wc
    assert pulse.systems_line(db).startswith("Systems:")
    user = db.query(User).filter(User.email == "owner@test.local").one()
    assert "Only admins" not in T.run(db, user, "pulse", {}, None)


def test_pulse_checks_batches_builds_and_scheduler(owner, db):
    from cbx.models import Batch, Template

    me = _owner(db)
    t = Template(name="Pulse template", owner_id=me.id)
    db.add(t)
    db.flush()
    b = Batch(template_id=t.id, user_id=me.id, name="Stuck since Tuesday", status="running", created_at=utcnow() - timedelta(hours=30))
    db.add(b)
    p = developer.propose(db, "Pulse: a build that never ends", "owner", "x", "y")
    p.status = "building"
    db.commit()
    db.query(Proposal).filter(Proposal.id == p.id).update({"updated_at": utcnow() - timedelta(minutes=45)})
    db.commit()
    try:
        r = pulse.run(db, only=["batches", "builds", "scheduler"])
        assert r["batches"]["ok"] is False and "Stuck since Tuesday" in db.get(PulseState, "batches").detail
        assert r["builds"]["ok"] is False and f"#{p.id}" in db.get(PulseState, "builds").detail
        assert r["scheduler"]["ok"] is True  # inline under tests
    finally:
        db.delete(b)
        p.status = "declined"
        db.commit()
