"""View as (1.0.46): an owner or admin sees the app as one person sees it — read-only, audited,
only people they may manage."""
from __future__ import annotations

from fastapi.testclient import TestClient

from cbx.main import app
from cbx.models import AuditLog, User
from tests.conftest import STRONG, _make, _uid


def _person(owner, email="viewed@test.local", name="Vee Wed", role="user", tools=None):
    pw = _make(owner, email, name, role, tools=tools)
    c = TestClient(app)
    c.post("/api/auth/login", json={"email": email, "password": pw})
    c.post("/api/auth/change-password", json={"new_password": STRONG, "repeat": STRONG})
    return c


def test_owner_sees_the_person_and_cannot_change_anything(owner, db):
    tools = owner.get("/api/users").json()["tools"]
    one = [t["id"] for t in tools if t["slug"] == "todo"]
    _person(owner, tools=one)
    uid = _uid(owner, "viewed@test.local")
    r = owner.post(f"/api/view-as/{uid}")
    assert r.status_code == 200, r.text
    me = owner.get("/api/auth/me").json()
    assert me["user"]["email"] == "viewed@test.local" and me["viewing_as"]["by"]["email"] == "owner@test.local"
    assert [t["slug"] for t in me["tools"]] == ["todo"]  # their tools, not the owner's
    assert owner.get("/api/users").status_code == 403  # an ordinary user cannot open Users & roles
    # nothing can be written, not even their own settings
    r = owner.patch("/api/settings", json={"name": "Changed"})
    assert r.status_code == 403 and "viewing as" in r.json()["detail"].lower()
    assert owner.post("/api/auth/change-password", json={"new_password": "x" * 14, "repeat": "x" * 14}).status_code == 403
    assert owner.post(f"/api/view-as/{uid}").status_code == 403  # stop first
    db.expire_all()
    assert db.get(User, uid).name == "Vee Wed"
    # stop: back to the owner
    assert owner.post("/api/view-as/stop").status_code == 200
    me = owner.get("/api/auth/me").json()
    assert me["user"]["email"] == "owner@test.local" and me["viewing_as"] is None
    acts = [a.action for a in db.query(AuditLog).filter(AuditLog.target == "viewed@test.local").order_by(AuditLog.id)]
    assert "view_as_started" in acts and "view_as_stopped" in acts


def test_reads_that_would_change_something_are_not_saved_and_the_trail_names_the_viewer(owner, db):
    _person(owner)
    uid = _uid(owner, "viewed@test.local")
    before = db.get(User, uid).last_seen
    owner.post(f"/api/view-as/{uid}")
    assert owner.get("/api/home/counts").status_code == 200
    db.expire_all()
    assert db.get(User, uid).last_seen == before  # the person did not "sign in" because someone looked
    from cbx.audit import log
    from cbx.db import SessionLocal

    s = SessionLocal()
    s.info["viewer"] = {"real_id": 1, "real_label": "owner", "target_id": uid, "target_label": "viewed"}
    row = log(s, "anything", actor=s.get(User, uid))
    assert row.actor_id == 1 and row.actor_label == "owner (as viewed)" and row.meta["viewing_as"] == uid
    s.rollback()
    s.close()
    owner.post("/api/view-as/stop")


def test_only_people_you_manage_and_never_the_ai_or_yourself(owner, admin, db):
    _person(owner)
    uid = _uid(owner, "viewed@test.local")
    owner_id = _uid(owner, "owner@test.local")
    assert admin.post(f"/api/view-as/{owner_id}").status_code == 403  # an admin never views as an owner
    admin_id = _uid(owner, "admin@test.local")
    assert owner.post(f"/api/view-as/{owner_id}").status_code == 400  # yourself
    cexa = db.query(User).filter(User.is_axl.is_(True)).first()
    if cexa:
        assert owner.post(f"/api/view-as/{cexa.id}").status_code == 404
    assert admin.post(f"/api/view-as/{uid}").status_code == 200  # a user, yes
    admin.post("/api/view-as/stop")
    user = _person(owner, "plain@test.local", "Plain User")
    assert user.post(f"/api/view-as/{admin_id}").status_code == 403  # users cannot view as anyone
