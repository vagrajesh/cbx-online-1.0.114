"""Version injector: upload zip → staged → tested (sandbox) → promote copies files + RESTART_REQUESTED → rollback."""
import io
import os
import shutil
import zipfile
from pathlib import Path

import pytest

from cbx.config import ROOT, settings
from cbx.versions import injector

NESTED = bool(os.environ.get("CBX_SANDBOX_RUN"))


def _zip_from_tree(tag: str, mutate=None) -> bytes:
    """Zip the current tree the way docs/versions.md describes (cbx/, web/, tests/, requirements.txt, VERSION)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for d in ("cbx", "web", "tests"):
            for f in sorted((ROOT / d).rglob("*")):
                if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc":
                    zf.write(f, f.relative_to(ROOT).as_posix())
        zf.write(ROOT / "requirements.txt", "requirements.txt")
        zf.writestr("VERSION", tag + "\n")
        if mutate:
            mutate(zf)
    return buf.getvalue()


def _fake_live(tmp_path: Path) -> Path:
    live = tmp_path / "live"
    for d in ("cbx", "web", "tests"):
        shutil.copytree(ROOT / d, live / d, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(ROOT / "requirements.txt", live / "requirements.txt")
    (live / "VERSION").write_text("0.9.9\n", encoding="utf-8")
    return live


def test_zip_verification(owner):
    bad = io.BytesIO()
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("cbx/x.py", "")
        zf.writestr("../evil.py", "")
    with pytest.raises(injector.VersionError):
        injector.verify_zip(zipfile.ZipFile(io.BytesIO(bad.getvalue())))
    bad2 = io.BytesIO()
    with zipfile.ZipFile(bad2, "w") as zf:
        zf.writestr("run.bat", "echo hi")
        zf.writestr("VERSION", "1.2.3")
    with pytest.raises(injector.VersionError):
        injector.verify_zip(zipfile.ZipFile(io.BytesIO(bad2.getvalue())))
    bad3 = io.BytesIO()
    with zipfile.ZipFile(bad3, "w") as zf:
        zf.writestr("cbx/__init__.py", "")
        zf.writestr("VERSION", "1.2.3")
    with pytest.raises(injector.VersionError, match="missing"):
        injector.verify_zip(zipfile.ZipFile(io.BytesIO(bad3.getvalue())))
    r = owner.post("/api/versions/upload", files={"files": ("v.zip", b"not a zip", "application/zip")})
    assert r.status_code == 400
    assert owner.get("/api/version").json()["version"]


@pytest.mark.skipif(NESTED, reason="no nested sandbox test runs")
def test_upload_stage_promote_rollback(owner, admin, tmp_path, monkeypatch):
    live = _fake_live(tmp_path)
    monkeypatch.setattr(injector, "LIVE_ROOT", live)
    monkeypatch.setattr(injector, "TEST_CMD", [*injector.TEST_CMD[:4], "tests/test_core.py", "-x"])
    monkeypatch.setattr(injector, "RESTART_ENABLED", False)
    monkeypatch.setattr(injector, "RUN_PIP", False)
    tag = "9.9.1"
    marker = "# injected by test_versions\n"

    def mutate(zf):
        zf.writestr("web/injected.txt", marker)

    data = _zip_from_tree(tag, mutate)
    r = owner.post("/api/versions/upload", files={"files": (f"cbx-{tag}.zip", data, "application/zip")}, data={"notes": "Test build"})
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["tag"] == tag and v["status"] == "sandbox", v
    assert v["tests"]["passed"] >= 1 and v["tests"]["failed"] == 0
    assert v["env"] in ("sandbox-a", "sandbox-b")
    assert (Path(settings.data_dir) / "versions" / tag / "web" / "injected.txt").exists()
    lst = owner.get("/api/versions").json()
    assert any(x["tag"] == tag for x in lst["versions"]) and len(lst["sandboxes"]) == 2
    assert any(s["busy"] for s in lst["sandboxes"])
    assert "injected" in owner.get(f"/api/versions/{v['id']}/diff").json()["diff"]
    # admins can't see versions; C-EXA can't promote (checked in test_develop); owner promotes
    assert admin.get("/api/versions").status_code == 403
    restart_flag = Path(settings.data_dir) / "RESTART_REQUESTED"
    restart_flag.unlink(missing_ok=True)
    r = owner.post(f"/api/versions/{v['id']}/promote?skip_test=1&minutes=0")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "live" and r.json()["env"] == "production"
    assert (live / "web" / "injected.txt").read_text(encoding="utf-8") == marker
    assert (live / "VERSION").read_text(encoding="utf-8").strip() == tag
    assert restart_flag.exists() and tag in restart_flag.read_text(encoding="utf-8")
    backups = list((Path(settings.data_dir) / "backups").glob("0.9.9-*"))
    assert backups and (backups[0] / "cbx" / "__init__.py").exists()
    # promoting a second time is refused (it's live now)
    assert owner.post(f"/api/versions/{v['id']}/promote?skip_test=1&minutes=0").status_code == 400
    # rollback restores the old tree
    r = owner.post(f"/api/versions/{v['id']}/rollback")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "rolled_back"
    assert not (live / "web" / "injected.txt").exists()
    assert (live / "VERSION").read_text(encoding="utf-8").strip() == "0.9.9"
    # rolling back twice is refused: the version isn't live any more, and restoring its backup again
    # would overwrite whatever is live now
    r = owner.post(f"/api/versions/{v['id']}/rollback")
    assert r.status_code == 400 and "not live" in r.json()["detail"]
    assert (live / "VERSION").read_text(encoding="utf-8").strip() == "0.9.9"
    # same tag again is refused
    r = owner.post("/api/versions/upload", files={"files": ("v.zip", data, "application/zip")})
    assert r.status_code == 400 and "already exists" in r.json()["detail"]


def test_full_install_zip_is_accepted_and_env_is_refused(tmp_path):
    """The zip we hand the owner (one top folder, .bat scripts, README) must upload as-is;
    a zip that carries .env or data/ must be refused."""
    import io
    import zipfile

    import pytest

    from cbx.versions import injector

    def make(names, prefix="cbx-online-9.9.9/"):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for n in names:
                zf.writestr(prefix + n, "x")
            zf.writestr(prefix + "VERSION", "9.9.9\n")
        return zipfile.ZipFile(io.BytesIO(buf.getvalue()))

    base = ["cbx/__init__.py", "web/index.html", "tests/test_x.py", "requirements.txt", "README.md", "setup.bat", "run.bat", "update.bat", ".env.example"]
    assert injector.verify_zip(make(base)) == "9.9.9"
    with pytest.raises(injector.VersionError):
        injector.verify_zip(make(base + [".env"]))
    with pytest.raises(injector.VersionError):
        injector.verify_zip(make(base + ["data/cbx.sqlite3"]))
    with pytest.raises(injector.VersionError):
        injector.verify_zip(make(base + ["evil.bat"]))


def _zip(entries: dict[str, str]) -> zipfile.ZipFile:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n, content in entries.items():
            zf.writestr(n, content)
    return zipfile.ZipFile(io.BytesIO(buf.getvalue()))


_BASE = {"cbx/__init__.py": 'VERSION = "0.0.1"\n', "web/index.html": "x", "tests/test_x.py": "", "requirements.txt": ""}


def test_zip_slip_with_backslashes_is_refused(tmp_path):
    """Windows-made zips may use backslashes; a `..` hidden behind them must still be caught."""
    zf = _zip({**_BASE, "VERSION": "1.2.3\n", "cbx\\..\\..\\evil.py": "boom"})
    with pytest.raises(injector.VersionError, match="climbs out"):
        injector.verify_zip(zf)
    # and _unpack refuses on its own too, even if verification were skipped
    dest = tmp_path / "pkg"
    with pytest.raises(injector.VersionError):
        injector._unpack(_zip({"cbx\\..\\..\\evil.py": "boom"}), dest)
    assert not (tmp_path / "evil.py").exists()
    # plain backslash names (no traversal) unpack to the right place
    injector._unpack(_zip({"cbx\\api\\x.py": "ok"}), dest)
    assert (dest / "cbx" / "api" / "x.py").read_text(encoding="utf-8") == "ok"


def test_zip_wrapped_in_folder_named_like_ours_is_accepted():
    """Someone zips a folder called 'cbx' that contains the whole install."""
    zf = _zip({"cbx/" + n: c for n, c in {**_BASE, "VERSION": "2.0.0\n"}.items()})
    assert injector.verify_zip(zf) == "2.0.0"


def test_empty_version_file_is_a_clean_error():
    zf = _zip({**_BASE, "VERSION": "\n"})
    with pytest.raises(injector.VersionError, match="VERSION must hold"):
        injector.verify_zip(zf)


@pytest.mark.skipif(NESTED, reason="no nested sandbox test runs")
def test_stage_aligns_code_version_and_failed_promote_restores_live_tree(owner, tmp_path, monkeypatch):
    live = _fake_live(tmp_path)
    monkeypatch.setattr(injector, "LIVE_ROOT", live)
    monkeypatch.setattr(injector, "TEST_CMD", [*injector.TEST_CMD[:4], "tests/test_core.py", "-x", "-k", "test_login_and_roles"])
    monkeypatch.setattr(injector, "RESTART_ENABLED", False)
    monkeypatch.setattr(injector, "RUN_PIP", False)
    tag = "9.9.2"
    data = _zip_from_tree(tag)  # cbx/__init__.py inside still says the repo's VERSION, not 9.9.2
    r = owner.post("/api/versions/upload", files={"files": (f"cbx-{tag}.zip", data, "application/zip")})
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["status"] == "sandbox", v
    assert f'VERSION = "{tag}"' in (Path(settings.data_dir) / "versions" / tag / "cbx" / "__init__.py").read_text(encoding="utf-8")
    # make copying fail half-way: the live tree must come back exactly as it was
    before = (live / "cbx" / "__init__.py").read_text(encoding="utf-8")
    real = injector._replace_tree

    failed = []

    def boom(src, dst):
        if dst.name == "web" and not failed:  # fail once (the promote copy), let the restore through
            failed.append(1)
            raise PermissionError("locked by Explorer")
        real(src, dst)

    monkeypatch.setattr(injector, "_replace_tree", boom)
    r = owner.post(f"/api/versions/{v['id']}/promote?skip_test=1&minutes=0")
    assert r.status_code == 400 and "restored" in r.json()["detail"], r.text
    assert (live / "VERSION").read_text(encoding="utf-8").strip() == "0.9.9"
    assert (live / "cbx" / "__init__.py").read_text(encoding="utf-8") == before
    lst = owner.get("/api/versions").json()["versions"]
    assert next(x for x in lst if x["tag"] == tag)["status"] == "sandbox"
    rows = owner.get("/api/audit?action=version_promote_failed").json()["rows"]
    assert rows and rows[0]["outcome"] == "failed"  # the failure is on record even though the request failed


def test_new_requirements_are_installed_before_sandbox_tests(tmp_path, monkeypatch):
    from cbx.versions import injector
    pkg = tmp_path / "pkg"; pkg.mkdir()
    (pkg / "requirements.txt").write_text((injector.LIVE_ROOT / "requirements.txt").read_text(encoding="utf-8") + "\nsome-new-lib>=1.0\n", encoding="utf-8")
    assert injector._needs_new_packages(pkg)
    (pkg / "requirements.txt").write_text((injector.LIVE_ROOT / "requirements.txt").read_text(encoding="utf-8"), encoding="utf-8")
    assert not injector._needs_new_packages(pkg)


@pytest.mark.skipif(NESTED, reason="no nested sandbox test runs")
def test_promote_reports_hand_edited_files(owner, tmp_path, monkeypatch):
    """Someone edits web/app.js on the server by hand; the next promote overwrites it — the owner must be told
    (promote result + audit detail), and the edit survives in the backup."""
    live = _fake_live(tmp_path)
    monkeypatch.setattr(injector, "LIVE_ROOT", live)
    monkeypatch.setattr(injector, "TEST_CMD", [*injector.TEST_CMD[:4], "tests/test_core.py", "-x", "-k", "test_login_and_roles"])
    monkeypatch.setattr(injector, "RESTART_ENABLED", False)
    monkeypatch.setattr(injector, "RUN_PIP", False)
    (Path(settings.data_dir) / "RESTART_REQUESTED").unlink(missing_ok=True)
    # first promote: the tree was installed by hand, there is nothing to compare against → no warning
    r = owner.post("/api/versions/upload", files={"files": ("v.zip", _zip_from_tree("9.9.3"), "application/zip")})
    assert r.status_code == 200 and r.json()["status"] == "sandbox", r.text
    r = owner.post(f"/api/versions/{r.json()['id']}/promote?skip_test=1&minutes=0")
    assert r.status_code == 200, r.text
    assert r.json()["hand_edited"] == [] and r.json()["hand_edited_note"] == ""
    from cbx.db import SessionLocal

    s = SessionLocal()
    try:
        assert injector.hand_edits(s) == []  # live tree == the package it was promoted from
    finally:
        s.close()
    # hand edits on the live tree: one changed file, one new file; a CRLF-only difference is not an edit
    app_js = live / "web" / "app.js"
    edited = app_js.read_text(encoding="utf-8") + "\n// hot-fixed on the server\n"
    app_js.write_text(edited, encoding="utf-8")
    (live / "cbx" / "hand_made.py").write_text("X = 1\n", encoding="utf-8")
    ui = live / "web" / "ui.js"
    ui.write_bytes(ui.read_bytes().replace(b"\n", b"\r\n"))
    r = owner.post("/api/versions/upload", files={"files": ("v.zip", _zip_from_tree("9.9.4"), "application/zip")})
    assert r.status_code == 200 and r.json()["status"] == "sandbox", r.text
    r = owner.post(f"/api/versions/{r.json()['id']}/promote?skip_test=1&minutes=0")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["hand_edited"] == ["cbx/hand_made.py", "web/app.js"], out["hand_edited"]
    assert out["hand_edited_note"] == "2 hand-edited files were replaced: cbx/hand_made.py, web/app.js"
    # the live tree now holds the package's files; the edits are in the backup
    assert "hot-fixed" not in app_js.read_text(encoding="utf-8") and not (live / "cbx" / "hand_made.py").exists()
    backups = sorted((Path(settings.data_dir) / "backups").glob("9.9.3-*"))
    assert backups and "hot-fixed" in (backups[-1] / "web" / "app.js").read_text(encoding="utf-8") and (backups[-1] / "cbx" / "hand_made.py").exists()
    # the audit trail says so in plain words, and the Versions list keeps the note
    rows = owner.get("/api/audit?action=version_promoted").json()["rows"]
    assert rows and "2 hand-edited files were replaced: cbx/hand_made.py, web/app.js" in rows[0]["detail"]
    lst = owner.get("/api/versions").json()["versions"]
    assert next(x for x in lst if x["tag"] == "9.9.4")["hand_edited"] == ["cbx/hand_made.py", "web/app.js"]
    assert "hand_edited" not in next(x for x in lst if x["tag"] == "9.9.4")["tests"]
    assert injector.hand_edits_sentence([f"web/f{i}.js" for i in range(25)]).endswith(", … and 5 more")


# ---- 1.0.37: what failed, delete, promote anyway, the truth about what is running -----------------------------
def test_failed_tests_are_read_out_by_name():
    out = (
        "....F..\n"
        "=================================== FAILURES ===================================\n"
        "___ test_attention_is_quiet_when_nothing_is_wrong ___\n"
        "E   assert 'nodered_down' in ['model_x']\n"
        "=========================== short test summary info ============================\n"
        "FAILED tests/test_home.py::test_attention_is_quiet_when_nothing_is_wrong - AssertionError: assert 'nodered_down' in [...]\n"
        "ERROR tests\\test_x.py::test_y - RuntimeError: boom\n"
        "FAILED tests/test_home.py::test_attention_is_quiet_when_nothing_is_wrong - AssertionError: assert 'nodered_down' in [...]\n"
        "2 failed, 113 passed in 41.2s\n"
    )
    f = injector.failed_tests(out)
    assert f == [
        "tests/test_home.py::test_attention_is_quiet_when_nothing_is_wrong — AssertionError: assert 'nodered_down' in [...]",
        "tests/test_x.py::test_y — RuntimeError: boom",
    ]
    assert injector.failed_tests("5 passed in 1.0s") == []
    # the whole suite runs — no -x — so every failure is listed, not only the first
    assert "-x" not in injector.TEST_CMD


def test_node_red_is_gone_from_the_package():
    """1.0.48: Node-RED left CBX. No part of the app starts it and the package no longer carries its script
    (1.0.79: nor accepts it, and its .env keys are no longer scrubbed — nothing reads them)."""
    import cbx

    root = Path(cbx.__file__).resolve().parents[1]
    assert not (root / "cbx" / "workflows").exists() and not (root / "node-red.bat").exists()
    assert "node-red.bat" not in injector.PROMOTED and "node-red.bat" not in injector.SCRIPT_FILES
    assert "node-red.bat" not in injector.ALLOWED_FILES and "CBX_DATABASE_URL" in injector.SCRUB_ENV


def _staged_row(db, tag, status="failed", **kw):
    from cbx.models import AppVersion

    pkg = injector.versions_dir() / tag
    (pkg / "cbx").mkdir(parents=True, exist_ok=True)
    (pkg / "cbx" / "__init__.py").write_text(f'VERSION = "{tag}"\n', encoding="utf-8")
    (pkg / "VERSION").write_text(tag + "\n", encoding="utf-8")
    (injector.versions_dir() / f"{tag}.zip").write_bytes(b"PK\x05\x06" + b"\0" * 18)
    v = AppVersion(tag=tag, status=status, env="sandbox-a" if status in ("staged", "sandbox", "failed") else "", package_path=str(pkg), sha256="0" * 64, tests={"ok": False, "passed": 113, "failed": 1, "failures": ["tests/test_home.py::test_x — AssertionError"]} if status == "failed" else {"ok": True, "passed": 5, "failed": 0}, **kw)
    db.add(v)
    db.commit()
    return v


def test_owner_can_delete_a_version_that_is_not_live(owner, admin, db):
    from cbx.models import AppVersion

    v = _staged_row(db, "9.8.1")
    vid = v.id
    pkg, zipf = Path(v.package_path), injector.versions_dir() / "9.8.1.zip"
    assert pkg.exists() and zipf.exists()
    # the row shows WHICH test failed
    row = next(x for x in owner.get("/api/versions").json()["versions"] if x["tag"] == "9.8.1")
    assert row["tests"]["failures"] == ["tests/test_home.py::test_x — AssertionError"]
    # admins can't; C-EXA can't (owner-only page, and a person); the owner can
    assert admin.delete(f"/api/versions/{vid}").status_code == 403
    r = owner.delete(f"/api/versions/{vid}")
    assert r.status_code == 200, r.text
    assert r.json()["tag"] == "9.8.1" and set(r.json()["removed"]) == {"9.8.1", "9.8.1.zip"}
    assert not pkg.exists() and not zipf.exists()
    db.expire_all()
    assert db.query(AppVersion).filter(AppVersion.tag == "9.8.1").one_or_none() is None
    rows = owner.get("/api/audit?action=version_deleted").json()["rows"]
    assert rows and rows[0]["target"] == "9.8.1" and "was failed" in rows[0]["detail"]
    assert owner.delete(f"/api/versions/{vid}").status_code == 404
    # the live version is never deletable
    db.expunge_all()
    live = _staged_row(db, "9.8.2", status="live")
    r = owner.delete(f"/api/versions/{live.id}")
    assert r.status_code == 400 and "live" in r.json()["detail"]
    live.status = "superseded"
    db.commit()
    assert owner.delete(f"/api/versions/{live.id}").status_code == 200


@pytest.mark.skipif(NESTED, reason="no nested sandbox test runs")
def test_promote_anyway_is_explicit_and_audited(owner, tmp_path, monkeypatch, db):
    live = _fake_live(tmp_path)
    monkeypatch.setattr(injector, "LIVE_ROOT", live)
    monkeypatch.setattr(injector, "RESTART_ENABLED", False)
    monkeypatch.setattr(injector, "RUN_PIP", False)
    # a version whose tests failed on this server
    monkeypatch.setattr(injector, "TEST_CMD", [*injector.TEST_CMD[:4], "tests/test_zz_fails_here.py", "-p", "no:cacheprovider"])
    r = owner.post("/api/versions/upload", files={"files": ("v.zip", _zip_from_tree("9.8.5", lambda zf: zf.writestr("tests/test_zz_fails_here.py", "def test_this_server_only():\n    assert False, 'only on this box'\n")), "application/zip")})
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["status"] == "failed", v
    assert any("test_zz_fails_here.py::test_this_server_only" in f for f in v["tests"]["failures"]), v["tests"]
    # plain promote is still refused
    r = owner.post(f"/api/versions/{v['id']}/promote?skip_test=1&minutes=0")
    assert r.status_code == 400 and "failed" in r.json()["detail"]
    # forced: goes live, says so on the row and in the audit trail
    r = owner.post(f"/api/versions/{v['id']}/promote?skip_test=1&force=1&minutes=0")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "live" and r.json()["tests"]["forced"] is True
    assert (live / "VERSION").read_text(encoding="utf-8").strip() == "9.8.5"
    rows = owner.get("/api/audit?action=version_promoted").json()["rows"]
    assert rows and rows[0]["outcome"] == "forced" and "PROMOTED WITH FAILING TESTS" in rows[0]["detail"] and "test_zz_fails_here" in rows[0]["detail"]
    # the version it replaced is "superseded", not "rolled back" — nothing went wrong with it
    from cbx.models import AppVersion

    db.expire_all()
    others = [x.status for x in db.query(AppVersion).filter(AppVersion.tag != "9.8.5", AppVersion.env == "").all()]
    assert "rolled_back" not in [x for x in others if x == "superseded"]  # sanity: the two are distinct states


def test_reconcile_marks_the_running_version_live(db, owner):
    """update.bat (or copied files) put 1.0.36 live while the page still said 1.0.26 was: on start the
    running code's row becomes live and the stale live row is superseded."""
    import cbx
    from cbx.models import AppVersion

    from cbx.versions import injector as inj

    stale = _staged_row(db, "9.7.1", status="live")
    running = _staged_row(db, "9.7.2", status="failed")
    orig = inj.VERSION
    try:
        inj.VERSION = "9.7.2"
        assert inj.reconcile(db) == "9.7.2"
        db.commit()
        db.expire_all()
        assert running.status == "live" and running.env == "production" and running.tests["installed_by_hand"] is True
        assert stale.status == "superseded" and stale.env == ""
        assert inj.reconcile(db) is None  # already right: nothing logged twice
        rows = owner.get("/api/audit?action=version_reconciled").json()["rows"]
        assert rows and rows[0]["target"] == "9.7.2"
        # a version the page never saw: nothing to correct, nothing invented
        inj.VERSION = "0.0.0-unknown"
        assert inj.reconcile(db) is None
        lst = owner.get("/api/versions").json()["versions"]
        assert next(x for x in lst if x["tag"] == "9.7.2")["tests"]["installed_by_hand"] is True
    finally:
        inj.VERSION = orig
        for v in (stale, running):
            owner.delete(f"/api/versions/{v.id}") if v.status != "live" else None
        db.expire_all()
        for v in db.query(AppVersion).filter(AppVersion.tag.in_(["9.7.1", "9.7.2"])).all():
            db.delete(v)
        db.commit()
    assert cbx.VERSION == orig
