"""1.0.80: Dev → Test → Live — a Dev area per builder, one shared Test area, promote after Test; Builder drafts
belong to the person who changes them; developers build without seeing production's rows."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cbx.main import app
from cbx.models import AppVersion, User, WorkArea
from cbx.versions import areas as A
from cbx.versions import injector
from cbx.versions import sandbox_runner as R

from .conftest import _make, login

FOLDER = {"manifest": {"title": "tool.title", "icon": "list", "menu": {"section": "My tools", "order": 9}, "access": {"roles": ["owner", "admin"]}, "home": "p"},
          "pages": {"p": {"title": "page.p.title", "layout": {"sections": [{"id": "s1", "columns": [{"width": 12, "widgets": [{"id": "t", "type": "table", "source": {"data": "items"}}]}]}]}}},
          "data": {"items": {"kind": "table", "title": "data.items.title", "columns": [{"key": "name", "label": "col.name", "type": "text"}]}},
          "languages": {"en": {"tool.title": "Areas test", "page.p.title": "P", "data.items.title": "Items", "col.name": "Name"}}, "readme": "# Areas\n"}


def _client(owner, email, name, role):
    pw = _make(owner, email, name, role)
    c = TestClient(app)
    login(c, email, pw)
    return c


@pytest.fixture()
def dev(owner):
    return _client(owner, "dev.one@test.local", "Devon Developer", "developer")


@pytest.fixture()
def fake_runner(monkeypatch):
    started = []

    def start(slot, path, owners=None, *, kind="version", title=""):
        started.append({"slot": slot, "path": path, "owners": list(owners or []), "kind": kind, "title": title})
        R.LOGINS[slot] = [{"email": e, "password": f"pw-{slot}-{e[:3]}", "at": "now"} for e, _ in owners or []]
        R.RUNNING[slot] = {"kind": kind, "title": title, "path": path}
        return {"ok": True, "url": "http://127.0.0.1", "login": [{"email": e, "password": "x"} for e, _ in owners or []]}

    monkeypatch.setattr(R, "start", start)
    monkeypatch.setattr(R, "status", lambda: [{"slot": sl, "port": R.ports().get(sl), "running": True, "healthy": True, "title": w["title"]} for sl, w in R.RUNNING.items()])
    monkeypatch.setattr(R, "stop", lambda slot: (R.RUNNING.pop(slot, None), R.LOGINS.pop(slot, None)) and {"ok": True})
    yield started
    R.LOGINS.clear()
    R.RUNNING.clear()


def _cleanup_drafts(owner):
    from cbx.api.builder_api import drafts_root
    import shutil

    shutil.rmtree(drafts_root() / "areas-test", ignore_errors=True)


def test_who_may_do_what(owner, admin, user, dev, db):
    me = lambda e: db.query(User).filter(User.email == e).one()  # noqa: E731
    from cbx.security.roles import attach

    o, a, u, d = (attach(db, me(e)) or me(e) for e in ("owner@test.local", "admin@test.local", "thandi@test.local", "dev.one@test.local"))
    for x in (o, a, u, d):
        attach(db, x)
    assert A.can_build(o) and A.can_build(a) and A.can_build(d) and not A.can_build(u)
    assert A.can_test(d) and not A.can_test(u)
    # the door: your own Dev area, Test if you test, Sandbox A/B for admins
    assert A.may_enter(d, f"dev-{d.id}") and not A.may_enter(d, f"dev-{a.id}") and A.may_enter(o, f"dev-{d.id}")
    assert A.may_enter(d, "test") and not A.may_enter(u, "test") and not A.may_enter(d, "sandbox-a") and A.may_enter(a, "sandbox-a")
    cexa = db.query(User).filter(User.is_axl.is_(True)).first()
    assert not A.may_enter(cexa, "test") and not A.can_build(cexa)
    # the menu flags
    assert dev.get("/api/auth/me").json()["user"]["can_build"] is True
    assert user.get("/api/auth/me").json()["user"]["can_test"] is False
    assert user.get("/api/areas").status_code == 403


def test_a_developer_builds_without_seeing_production_rows(owner, dev, admin):
    assert dev.get("/api/builder").status_code == 200
    r = dev.put("/api/builder/tools/areas-test", json=FOLDER)
    assert r.status_code == 200, r.text
    assert r.json()["editor"]["mine"] is True
    p = dev.get("/api/builder/tools/areas-test/preview/p").json()
    assert all(v["rows"] == [] and "Dev area" in v.get("note", "") for v in p["rows"].values())
    assert dev.post("/api/builder/tools/areas-test/flows/x/test", json={"flow": {}}).status_code == 403  # dry runs read live data: admins
    # someone else's draft: refused; the owner may take it over on purpose
    r = admin.put("/api/builder/tools/areas-test", json=FOLDER)
    assert r.status_code == 409 and "Devon Developer is changing this tool" in r.json()["detail"]
    assert admin.delete("/api/builder/tools/areas-test/draft").status_code == 409
    assert admin.post("/api/builder/release", json={"slugs": ["areas-test"]}).status_code == 409
    ov = admin.get("/api/builder").json()
    assert next(t for t in ov["tools"] if t["slug"] == "areas-test")["editor"] == {"name": "Devon Developer", "mine": False}
    assert owner.put("/api/builder/tools/areas-test", json=FOLDER).status_code == 409
    r = owner.put("/api/builder/tools/areas-test", json={**FOLDER, "take_over": True})
    assert r.status_code == 200 and r.json()["editor"]["mine"] is True
    assert owner.get("/api/audit?action=builder_draft_taken_over").json()["rows"]
    _cleanup_drafts(owner)


def test_my_dev_area_runs_the_live_app_with_my_drafts_and_only_i_sign_in(owner, dev, db, fake_runner):
    dev.put("/api/builder/tools/areas-test", json=FOLDER)
    d = dev.get("/api/areas").json()
    me = db.query(User).filter(User.email == "dev.one@test.local").one()
    assert d["can_build"] and d["dev"]["slot"] == f"dev-{me.id}" and d["dev"]["drafts"] == ["areas-test"] and d["dev"]["running"] is False
    assert d["test"]["slot"] == "test" and d["live"]["version"]
    area = db.query(WorkArea).filter(WorkArea.slot == f"dev-{me.id}").one()
    assert area.port > A.port_base() and area.port not in R.DEFAULT_PORTS.values()
    r = dev.post("/api/areas/dev/start").json()
    assert r["ok"] and r["drafts"] == ["areas-test"]
    run = fake_runner[-1]
    assert run["slot"] == f"dev-{me.id}" and run["owners"] == [("dev.one@test.local", "Devon Developer")] and run["kind"] == "dev"
    assert (Path(run["path"]) / "tools" / "areas-test" / "tool.json").exists() and (Path(run["path"]) / "cbx" / "main.py").exists()
    # the sign-in is shown to me — and to nobody else, the owner included
    mine = dev.get("/api/areas").json()["dev"]
    assert mine["running"] and mine["login"] == [{"email": "dev.one@test.local", "password": f"pw-dev-{me.id}-dev"}]
    others = owner.get("/api/areas").json()["others"]
    assert any(x["slot"] == f"dev-{me.id}" and x["login"] == [] for x in others)
    # a second builder gets another port
    o = db.query(User).filter(User.email == "owner@test.local").one()
    owner.get("/api/areas")
    assert db.query(WorkArea).filter(WorkArea.slot == f"dev-{o.id}").one().port != area.port
    assert dev.post("/api/areas/dev/stop").status_code == 200
    _cleanup_drafts(owner)


def _version(db, tag, ok=True, path="/nonexistent", **tests):
    v = AppVersion(tag=tag, status="sandbox" if ok else "failed", env="sandbox-a", package_path=str(path), tests={"ok": ok, **tests})
    db.add(v)
    db.commit()
    return v


def test_test_then_live(owner, admin, dev, user, db, fake_runner, tmp_path):
    v = _version(db, "9.7.1", path=tmp_path)
    bad = _version(db, "9.7.2", ok=False)
    try:
        assert dev.post(f"/api/areas/test/{v.id}").status_code == 403  # not theirs, and not an admin
        assert admin.post(f"/api/areas/test/{bad.id}").status_code == 400  # failed its tests: not for Test
        # promote straight to Live: only on purpose
        r = owner.post(f"/api/versions/{v.id}/promote?minutes=10")
        assert r.status_code == 409 and r.json()["detail"].startswith("test:")
        r = admin.post(f"/api/areas/test/{v.id}").json()
        assert r["ok"]
        run = fake_runner[-1]
        assert run["slot"] == "test" and run["kind"] == "test"
        testers = {e for e, _ in run["owners"]}
        assert {"owner@test.local", "admin@test.local", "dev.one@test.local"} <= testers and "thandi@test.local" not in testers
        d = dev.get("/api/areas").json()["test"]
        assert d["version"] == "9.7.1" and d["running"] and [x["email"] for x in d["login"]] == ["dev.one@test.local"]
        db.refresh(v)
        assert v.tests["in_test"]["by"] == "Arnab Admin"
        assert owner.get("/api/versions").json()["versions"]
        # now it may be announced for Live without skipping anything
        r = owner.post(f"/api/versions/{v.id}/promote?minutes=10")
        assert r.status_code == 200 and r.json()["announced"], r.text
        assert owner.post("/api/versions/pending/cancel").status_code == 200
        assert admin.post("/api/areas/test/stop").status_code == 200
    finally:
        db.query(WorkArea).filter(WorkArea.version_id.in_([v.id, bad.id])).update({WorkArea.version_id: None}, synchronize_session=False)
        for x in (v, bad):
            db.delete(db.get(AppVersion, x.id))
        db.commit()


def test_a_builder_version_made_before_another_went_live_is_not_promoted(owner, db):
    v = _version(db, "9.7.3", base="0.0.1", in_test={"at": "x", "by": "y"})
    try:
        u = db.query(User).filter(User.email == "owner@test.local").one()
        with pytest.raises(injector.VersionError, match="Release it again"):
            injector.promote(db, v, u)
    finally:
        db.delete(db.get(AppVersion, v.id))
        db.commit()


def test_the_door_knows_the_areas(owner, dev, db, fake_runner, monkeypatch):
    from cbx.api import sandbox_door as D

    dev.get("/api/areas")
    me = db.query(User).filter(User.email == "dev.one@test.local").one()
    slot = f"dev-{me.id}"
    assert R.known(slot) and R.known("test") and not R.known("dev-x")
    monkeypatch.setattr(R, "status", lambda: [{"slot": slot, "running": True}, {"slot": "test", "running": True}])
    r = dev.get(f"/sandbox/{slot}/enter", follow_redirects=False)
    assert r.status_code == 303 and D.ROUTE_COOKIE in r.headers.get("set-cookie", "")
    o = db.query(User).filter(User.email == "owner@test.local").one()
    r = dev.get(f"/sandbox/dev-{o.id}/enter", follow_redirects=False)
    assert r.status_code in (403, 404)  # someone else's Dev area
    assert dev.get("/sandbox/leave", follow_redirects=False).headers["location"] == "/#/areas"
