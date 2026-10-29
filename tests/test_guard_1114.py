"""1.0.114 (Guard): an uploaded version's code never sees the server's secrets, and Promote backs up the database
beside the code so Roll back can put the data back too."""
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

from cbx.config import settings
from cbx.versions import injector, sandbox_runner
from cbx.versions.safe_env import minimal_env

NESTED = bool(os.environ.get("CBX_SANDBOX_RUN"))
SECRETS = {"CBX_MS_CLIENT_SECRET": "ms-secret-1114", "AWS_SECRET_ACCESS_KEY": "aws-secret-1114", "HTTPS_PROXY": "http://svc:proxy-pw@proxy:8080", "PIP_INDEX_URL": "https://tok@pkgs/simple"}


@pytest.fixture()
def secrets_in_env(monkeypatch):
    for k, v in SECRETS.items():
        monkeypatch.setenv(k, v)
    return SECRETS


# ---- the short list --------------------------------------------------------------------------------------


def test_minimal_env_keeps_only_the_short_list(secrets_in_env):
    env = minimal_env(pythonpath="/pkg", cbx={"CBX_TESTING": "1", "CBX_SANDBOX_RUN": "1", "CBX_DATA_DIR": "/tmp/x"})
    for k in SECRETS:
        assert k not in env, k
    assert not [v for v in env.values() if "secret-1114" in v or "proxy-pw" in v]
    assert env["PATH"] == os.environ["PATH"] and env["PYTHONPATH"] == "/pkg" and env["PYTHONIOENCODING"] == "utf-8"
    assert env["CBX_SANDBOX_RUN"] == "1" and env["CBX_DATA_DIR"] == "/tmp/x"
    assert {k for k in env if k.upper().startswith("CBX_")} == {"CBX_TESTING", "CBX_SANDBOX_RUN", "CBX_DATA_DIR"}
    # pip alone gets the proxy and PIP_* (it needs them to reach the index) — still none of the CBX_* secrets
    pip = minimal_env(pip=True)
    assert pip["HTTPS_PROXY"] == SECRETS["HTTPS_PROXY"] and pip["PIP_INDEX_URL"] == SECRETS["PIP_INDEX_URL"]
    assert "CBX_MS_CLIENT_SECRET" not in pip and "AWS_SECRET_ACCESS_KEY" not in pip


def test_minimal_env_keeps_what_windows_needs():
    """Windows hands the names back upper-cased; SYSTEMROOT, TEMP, USERNAME … must survive or Python can't start."""
    base = {"SYSTEMROOT": r"C:\Windows", "WINDIR": r"C:\Windows", "TEMP": r"C:\T", "TMP": r"C:\T", "USERPROFILE": r"C:\Users\cbx", "USERNAME": "cbx",
            "PATH": r"C:\Python", "PROGRAMFILES(X86)": r"C:\PF86", "PYTHONUTF8": "1", "PYTHONPATH": r"C:\elsewhere", "CBX_SMTP_PASSWORD": "x", "MSSQL_PASSWORD": "y",
            "PYTHONHOME": r"C:\Py", "PYTHONSTARTUP": r"C:\evil.py", "PYTHONWARNINGS": "error", "PYTHONBREAKPOINT": "x.y", "PYTHON_SECRET_TOKEN": "z"}
    env = minimal_env(base=base)
    assert {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "USERNAME", "PATH", "PROGRAMFILES(X86)", "PYTHONUTF8", "PYTHONHOME"} <= set(env)
    assert "PYTHONPATH" not in env and "CBX_SMTP_PASSWORD" not in env and "MSSQL_PASSWORD" not in env
    # 1.0.114: Python's switches are a fixed list, not every PYTHON* name
    assert not {"PYTHONSTARTUP", "PYTHONWARNINGS", "PYTHONBREAKPOINT", "PYTHON_SECRET_TOKEN"} & set(env)


def test_sandbox_instance_env_has_no_secrets(secrets_in_env):
    env = sandbox_runner.child_env("sandbox-a")
    for k in SECRETS:
        assert k not in env, k
    assert env["CBX_SANDBOX_RUN"] == "1" and env["CBX_HOST"] == "127.0.0.1" and env["CBX_MODEL_MODE"] == "stub"
    assert env["CBX_DATA_DIR"].endswith("sandbox-a-data") and env["CBX_DATABASE_URL"] == ""


def test_cexa_sandbox_commands_get_no_secrets_either(secrets_in_env, tmp_path):
    from cbx.cexa import hands

    env = hands.child_env(tmp_path, tmp_path / "d")
    assert not [k for k in SECRETS if k in env] and env["CBX_SANDBOX_RUN"] == "1" and env["PYTHONPATH"] == str(tmp_path)
    pip = hands.child_env(tmp_path, tmp_path / "d", pip=True)
    assert pip["HTTPS_PROXY"] == SECRETS["HTTPS_PROXY"] and "CBX_MS_CLIENT_SECRET" not in pip


@pytest.mark.skipif(NESTED, reason="no nested sandbox test runs")
def test_an_uploaded_versions_tests_cannot_read_a_server_secret(secrets_in_env, tmp_path, monkeypatch):
    """The real thing: a package whose test fails if it can see the secret, run the way an upload is run."""
    pkg = tmp_path / "pkg"
    (pkg / "tests").mkdir(parents=True)
    (pkg / "tests" / "test_peek.py").write_text(
        "import os, tempfile\n"
        "def test_peek():\n"
        "    leaked = [k for k in ('CBX_MS_CLIENT_SECRET', 'AWS_SECRET_ACCESS_KEY', 'HTTPS_PROXY', 'PIP_INDEX_URL') if k in os.environ]\n"
        "    assert not leaked, leaked\n"
        "    assert not [v for v in os.environ.values() if 'secret-1114' in v or 'proxy-pw' in v]\n"
        "    assert os.environ['CBX_SANDBOX_RUN'] == '1' and os.environ['CBX_TESTING'] == '1'\n"
        "    assert os.path.isdir(os.environ['CBX_DATA_DIR']) and os.path.isdir(tempfile.gettempdir())\n",
        encoding="utf-8")
    monkeypatch.setattr(injector, "TEST_CMD", [sys.executable, "-m", "pytest", "-q", "tests", "-p", "no:cacheprovider", "--tb=short"])
    res = injector.run_tests(pkg, timeout=300)
    assert res["ok"] and res["passed"] == 1 and res["failed"] == 0, res.get("output")


# ---- the database beside the code ----------------------------------------------------------------------------


def _fake_live(tmp_path: Path, tag: str) -> Path:
    live = tmp_path / "live"
    for d in ("cbx", "web", "tests"):
        (live / d).mkdir(parents=True)
    (live / "cbx" / "__init__.py").write_text('VERSION = "0.9.0"\n', encoding="utf-8")
    (live / "web" / "app.js").write_text("// old\n", encoding="utf-8")
    (live / "requirements.txt").write_text("", encoding="utf-8")
    (live / "VERSION").write_text("0.9.0\n", encoding="utf-8")
    return live


def _staged(db, tag: str):
    from cbx.models import AppVersion

    pkg = injector.versions_dir() / tag
    for d in ("cbx", "web", "tests"):
        (pkg / d).mkdir(parents=True, exist_ok=True)
    (pkg / "cbx" / "__init__.py").write_text(f'VERSION = "{tag}"\n', encoding="utf-8")
    (pkg / "web" / "app.js").write_text("// new\n", encoding="utf-8")
    (pkg / "requirements.txt").write_text("", encoding="utf-8")
    (pkg / "VERSION").write_text(tag + "\n", encoding="utf-8")
    v = AppVersion(tag=tag, status="sandbox", env="sandbox-a", package_path=str(pkg), sha256="0" * 64, tests={"ok": True, "passed": 1, "failed": 0, "in_test": True})
    db.add(v)
    db.commit()
    return v


@pytest.fixture()
def forget_my_versions():
    """Each test's version rows go again, so no other test finds a slot taken or a second live row."""
    yield
    from cbx.db import SessionLocal
    from cbx.models import AppVersion

    with SessionLocal() as s:
        s.query(AppVersion).filter(AppVersion.tag.like("8.114.%")).delete(synchronize_session=False)
        s.commit()


def _knobs(monkeypatch, live):
    monkeypatch.setattr(injector, "LIVE_ROOT", live)
    monkeypatch.setattr(injector, "RESTART_ENABLED", False)
    monkeypatch.setattr(injector, "RUN_PIP", False)
    monkeypatch.setattr(injector, "git_snapshot", lambda msg: "skipped in test")


def _count(path: Path, action: str) -> int:
    with closing(sqlite3.connect(str(path))) as c:
        return c.execute("SELECT count(*) FROM audit_log WHERE action = ?", (action,)).fetchone()[0]


def _sql(path: Path, q: str, args=()):
    with closing(sqlite3.connect(str(path))) as c:
        return c.execute(q, args).fetchall()


def _promote_with_backup(owner, db, tmp_path, monkeypatch, tag):
    from cbx.db import SessionLocal
    from cbx.models import AppVersion

    live = _fake_live(tmp_path, tag)
    _knobs(monkeypatch, live)
    v = _staged(db, tag)
    r = owner.post(f"/api/versions/{v.id}/promote?minutes=0")
    assert r.status_code == 200, r.text
    with SessionLocal() as s:
        backup = Path(s.get(AppVersion, v.id).backup_path)
    return v, live, backup


def _put_back(main: Path, backup: Path) -> None:
    """Undo a test's data restore for the rest of the suite: the data as it was just before the rollback."""
    kept = sorted(backup.glob("database-replaced-*"))
    if kept and (kept[-1] / main.name).is_file():
        injector._backup_into(kept[-1] / main.name, main)


def test_promote_backs_up_the_database_and_rollback_can_put_it_back(owner, db, tmp_path, monkeypatch, forget_my_versions):
    from cbx.audit import log
    from cbx.db import SessionLocal
    from cbx.models import AppVersion, AuditLog, Tool, ToolAccess, User
    from cbx.security.auth import hash_password

    main = injector._main_db()
    assert main is not None and main.exists()
    # people as they are before the promote
    with SessionLocal() as s:
        tool = s.query(Tool).order_by(Tool.id).first()
        made = {}
        for key, role in (("off", "user"), ("lock", "user"), ("acc", "user"), ("pw", "user"), ("demote", "admin")):
            u = User(email=f"guard.{key}@test.local", name=f"Guard {key}", role=role, password_hash=hash_password("Old-pass-1114!x"), must_change_password=False, is_active=True)
            s.add(u)
            s.flush()
            made[key] = u.id
        s.add(ToolAccess(tool_id=tool.id, user_id=made["acc"]))
        log(s, "guard_before_promote", actor_label="test")
        s.commit()

    v, live, backup = _promote_with_backup(owner, db, tmp_path, monkeypatch, "8.114.1")
    out = owner.get("/api/versions").json()
    row = next(x for x in out["versions"] if x["tag"] == "8.114.1")
    assert row["status"] == "live" and row["data_backup"] and "cbx.sqlite3" in row["data_backup"]["databases"]
    with SessionLocal() as s:
        promoted = s.query(AuditLog).filter_by(action="version_promoted", target="8.114.1").one()
        assert "database backed up" in promoted.detail
    copy = backup / "database" / "cbx.sqlite3"
    assert copy.is_file() and (backup / "cbx" / "__init__.py").is_file()  # beside the code backup
    assert not (backup / "database" / "cbx.sqlite3-wal").exists()  # one closed, self-contained file
    assert _sql(copy, "PRAGMA integrity_check")[0][0] == "ok" and _sql(copy, "PRAGMA journal_mode")[0][0] == "delete"
    assert _count(copy, "guard_before_promote") == 1
    assert _sql(copy, "SELECT status FROM app_versions WHERE id = ?", (v.id,))[0][0] == "sandbox"  # copied before the promote's rows

    # after the promote: ordinary work, and security changes
    new_pw = hash_password("New-pass-1114!x")
    with SessionLocal() as s:
        s.add(AppVersion(tag="8.114.9", status="failed", env="", package_path="", sha256="9" * 64))  # ordinary data: goes
        s.add(User(email="guard.new@test.local", name="Guard new", role="user", password_hash=new_pw, is_active=True))  # added since: goes
        s.get(User, made["off"]).is_active = False
        s.get(User, made["lock"]).locked = True
        s.query(ToolAccess).filter_by(user_id=made["acc"]).delete()
        s.get(User, made["pw"]).password_hash = new_pw
        s.get(User, made["demote"]).role = "user"
        log(s, "guard_after_promote", actor_label="test")
        s.commit()

    r = owner.post(f"/api/versions/{v.id}/rollback?data=1")
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["status"] == "rolled_back" and res["data_restored"] is True and res["signed_out"] is True, res
    assert (live / "VERSION").read_text(encoding="utf-8").strip() == "0.9.0" and (live / "web" / "app.js").read_text(encoding="utf-8") == "// old\n"
    assert (Path(settings.data_dir) / "RESTART_REQUESTED").read_text(encoding="utf-8").startswith("rollback 8.114.1")
    # ordinary data since the promote is gone; it is kept beside the backup
    assert not _sql(main, "SELECT 1 FROM app_versions WHERE tag = '8.114.9'")
    kept = list(backup.glob("database-replaced-*/cbx.sqlite3"))
    assert len(kept) == 1 and _sql(kept[0], "SELECT 1 FROM app_versions WHERE tag = '8.114.9'")
    # the security made since stays
    users = {r[0]: r[1:] for r in _sql(main, "SELECT email, is_active, locked, password_hash, role FROM users WHERE email LIKE 'guard.%'")}
    assert "guard.new@test.local" not in users
    assert users["guard.off@test.local"][0] == 0 and users["guard.lock@test.local"][1] == 1
    assert users["guard.pw@test.local"][2] == new_pw and users["guard.demote@test.local"][3] == "user"
    assert not _sql(main, "SELECT 1 FROM tool_access WHERE user_id = ?", (made["acc"],))
    # the audit trail since is copied back, with a marker, and the rollback is recorded — in plain SQL
    assert _count(main, "guard_after_promote") == 1 and _count(main, "guard_before_promote") == 1 and _count(main, "version_promoted") >= 1
    assert _count(main, "audit_carried_over") >= 1
    rb = _sql(main, "SELECT detail FROM audit_log WHERE action = 'version_rolled_back' AND target = '8.114.1' ORDER BY id DESC LIMIT 1")
    assert rb and "data put back" in rb[0][0]
    assert _sql(main, "SELECT status FROM app_versions WHERE id = ?", (v.id,))[0][0] == "rolled_back"
    assert _sql(main, "PRAGMA journal_mode")[0][0] == "wal" and _sql(main, "PRAGMA quick_check")[0][0] == "ok"
    # everyone is signed out
    assert not _sql(main, "SELECT 1 FROM login_sessions WHERE revoked = 0")
    assert owner.get("/api/versions").status_code == 401


def test_after_the_data_restore_nothing_uses_the_models_and_the_restart_still_happens(owner, db, tmp_path, monkeypatch, forget_my_versions):
    """The restored database may lack columns the running code's models expect: the rollback still finishes (plain SQL),
    the code is back and the restart is asked for — never new code on old data."""
    main = injector._main_db()
    v, live, backup = _promote_with_backup(owner, db, tmp_path, monkeypatch, "8.114.5")
    copy = backup / "database" / "cbx.sqlite3"
    with closing(sqlite3.connect(str(copy))) as c:
        c.execute("ALTER TABLE app_versions DROP COLUMN notes")  # as if the backup came from an older schema
        c.commit()
    flag = Path(settings.data_dir) / "RESTART_REQUESTED"
    flag.unlink(missing_ok=True)
    try:
        r = owner.post(f"/api/versions/{v.id}/rollback?data=1")
        assert r.status_code == 200, r.text
        assert r.json()["data_restored"] is True
        assert (live / "VERSION").read_text(encoding="utf-8").strip() == "0.9.0"
        assert flag.read_text(encoding="utf-8").startswith("rollback 8.114.5")
        assert "notes" not in [x[1] for x in _sql(main, "PRAGMA table_info(app_versions)")]
        assert _sql(main, "SELECT status FROM app_versions WHERE id = ?", (v.id,))[0][0] == "rolled_back"
    finally:
        _put_back(main, backup)


def test_a_restore_that_fails_halfway_puts_every_database_back(owner, db, tmp_path, monkeypatch, forget_my_versions):
    from cbx.audit import log
    from cbx.db import SessionLocal
    from cbx.models import AppVersion

    main = injector._main_db()
    tooldb = Path(settings.data_dir) / "tools" / "guard1114.sqlite"
    tooldb.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(str(tooldb))) as c:
        c.execute("CREATE TABLE t (x TEXT)")
        c.execute("INSERT INTO t VALUES ('before')")
        c.commit()
    try:
        v, live, backup = _promote_with_backup(owner, db, tmp_path, monkeypatch, "8.114.6")
        assert (backup / "database" / "tools" / "guard1114.sqlite").is_file()
        with closing(sqlite3.connect(str(tooldb))) as c:
            c.execute("INSERT INTO t VALUES ('after')")
            c.commit()
        with SessionLocal() as s:
            log(s, "guard_after_promote_6", actor_label="test")
            s.commit()
        real = injector._backup_into

        def main_fails(src, dst):
            if Path(dst) == main and "database-replaced" not in str(src):
                raise sqlite3.OperationalError("database is locked")
            return real(src, dst)

        monkeypatch.setattr(injector, "_backup_into", main_fails)
        r = owner.post(f"/api/versions/{v.id}/rollback?data=1")
        assert r.status_code == 200, r.text
        res = r.json()
        assert res["data_restored"] is False and "the data was not" in res["note"] and "every database is as it was" in res["note"], res
        # the tool database was restored first — and put back again; the main one never changed
        assert [x[0] for x in _sql(tooldb, "SELECT x FROM t ORDER BY rowid")] == ["before", "after"]
        assert _count(main, "guard_after_promote_6") == 1
        # the code went back, the restart was asked for, and the row says what happened
        assert (live / "VERSION").read_text(encoding="utf-8").strip() == "0.9.0"
        db.expire_all()
        assert db.get(AppVersion, v.id).status == "rolled_back"
        assert owner.get("/api/versions").status_code == 200  # nobody was signed out
    finally:
        tooldb.unlink(missing_ok=True)


def test_a_data_rollback_waits_for_the_background_and_refuses_while_it_runs(owner, db, tmp_path, monkeypatch, forget_my_versions):
    from cbx import jobs

    v, live, backup = _promote_with_backup(owner, db, tmp_path, monkeypatch, "8.114.7")
    monkeypatch.setattr(injector, "PAUSE_WAIT", 0.2)
    jobs._enter()  # a background job is running
    try:
        r = owner.post(f"/api/versions/{v.id}/rollback?data=1")
        assert r.status_code == 400 and "Background work is still running" in r.json()["detail"]
        assert (live / "VERSION").read_text(encoding="utf-8").strip() == "8.114.7"  # nothing changed
        assert not jobs.paused()  # and the background runs on
    finally:
        jobs._leave()


def test_pause_holds_new_background_work_and_waits_for_running_work():
    import threading

    from cbx import jobs

    jobs._enter()
    try:
        assert jobs.pause(0.2) is False  # one is still running
    finally:
        jobs._leave()
    assert jobs.pause(1.0) is True
    started = threading.Event()
    t = threading.Thread(target=lambda: (jobs._enter(), started.set(), jobs._leave()), daemon=True)
    t.start()
    assert not started.wait(0.5)  # held while paused
    jobs.resume()
    assert started.wait(5) and not jobs.paused()
    t.join(5)


def test_backup_manifest_names_are_checked(owner, db, tmp_path, monkeypatch, forget_my_versions):
    import json

    v, live, backup = _promote_with_backup(owner, db, tmp_path, monkeypatch, "8.114.8")
    m = backup / "database" / "manifest.json"
    good = json.loads(m.read_text(encoding="utf-8"))
    for bad in ("../../evil.sqlite", "tools/../../x.sqlite", "/etc/passwd.sqlite", "C:/Windows/x.sqlite3", "cbx.db", "tools/sub/x.sqlite"):
        m.write_text(json.dumps({**good, "databases": ["cbx.sqlite3", bad]}), encoding="utf-8")
        assert injector.data_backup_info(str(backup)) is None, bad
        with pytest.raises(injector.VersionError):
            injector._db_target(bad)
    r = owner.post(f"/api/versions/{v.id}/rollback?data=1")
    assert r.status_code == 400 and "not valid" in r.json()["detail"]
    assert (live / "VERSION").read_text(encoding="utf-8").strip() == "8.114.8"
    assert injector._db_target("tools/entity-compliance.sqlite") == Path(settings.data_dir) / "tools" / "entity-compliance.sqlite"
    m.write_text(json.dumps(good), encoding="utf-8")
    assert injector.data_backup_info(str(backup))


def test_rollback_without_data_leaves_the_database_alone(owner, db, tmp_path, monkeypatch, forget_my_versions):
    from cbx.audit import log
    from cbx.db import SessionLocal

    v, live, backup = _promote_with_backup(owner, db, tmp_path, monkeypatch, "8.114.2")
    with SessionLocal() as s:
        log(s, "guard_kept_after_code_rollback", actor_label="test")
        s.commit()
    r = owner.post(f"/api/versions/{v.id}/rollback")
    assert r.status_code == 200, r.text
    assert _count(injector._main_db(), "guard_kept_after_code_rollback") == 1
    assert (live / "VERSION").read_text(encoding="utf-8").strip() == "0.9.0"
    assert owner.get("/api/versions").status_code == 200


def test_a_failed_database_backup_stops_the_promote(owner, db, tmp_path, monkeypatch, forget_my_versions):
    live = _fake_live(tmp_path, "8.114.3")
    _knobs(monkeypatch, live)
    v = _staged(db, "8.114.3")

    def boom(src, dst):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(injector, "_sqlite_copy", boom)
    before = {p.name for p in injector.backups_dir().iterdir()} if injector.backups_dir().exists() else set()
    r = owner.post(f"/api/versions/{v.id}/promote?minutes=0")
    assert r.status_code == 400 and "database could not be backed up" in r.json()["detail"]
    assert (live / "VERSION").read_text(encoding="utf-8").strip() == "0.9.0"  # nothing replaced
    assert {p.name for p in injector.backups_dir().iterdir()} == before  # no half backup left behind
    db.expire_all()
    assert db.get(type(v), v.id).status == "sandbox"


def test_rollback_with_data_is_refused_when_the_backup_has_none(owner, db, tmp_path, monkeypatch, forget_my_versions):
    from cbx.models import AppVersion

    live = _fake_live(tmp_path, "8.114.4")
    _knobs(monkeypatch, live)
    old = tmp_path / "old-backup"
    (old / "cbx").mkdir(parents=True)
    (old / "VERSION").write_text("0.9.0\n", encoding="utf-8")
    v = _staged(db, "8.114.4")
    v.status, v.env, v.backup_path = "live", "production", str(old)  # promoted before 1.0.114: code only
    db.commit()
    assert injector.version_out(v)["data_backup"] is None
    r = owner.post(f"/api/versions/{v.id}/rollback?data=1")
    assert r.status_code == 400 and "no usable copy of the database" in r.json()["detail"]
    db.expire_all()
    assert db.get(AppVersion, v.id).status == "live"


def test_the_versions_page_says_who_should_upload_and_what_a_data_rollback_does():
    from cbx.owner_doc import HOW_TO

    js = (Path(__file__).resolve().parents[1] / "web" / "views" / "versions.js").read_text(encoding="utf-8")
    assert "Only upload versions you trust" in js and "server's own Windows account" in js
    assert "Everyone is signed out" in js and "stays that way" in js and "audit trail since is copied back" in js
    steps = " ".join(" ".join(s) for _t, s in HOW_TO)
    assert "Only upload versions you trust" in steps and "separate Windows account" in steps and "secret.key" in steps
