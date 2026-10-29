"""1.0.81: C-EXA runs the app for one owner — people, files, everything through the app's own API — doing routine
work herself and queueing what is risky for the owner's one tap; the app (not her prompt) holds the line."""
import secrets

import pytest
from fastapi.testclient import TestClient

from cbx.cexa import loop as L
from cbx.cexa import policy as P
from cbx.cexa import teams as TM
from cbx.cexa import tools as T
from cbx.main import app
from cbx.models import AgentWork, CexaMessage, Folder, LoginSession, StoredFile, Tool, ToolAccess, User
from cbx.security.auth import SESSION_COOKIE
from cbx.warehouse import storage


def _owner(db):
    return db.query(User).filter(User.email == "owner@test.local").one()


def _waiting(db, path_part):
    db.expire_all()
    return [w for w in db.query(AgentWork).filter(AgentWork.kind == "api", AgentWork.status == "waiting").all() if path_part in (w.proposal or {}).get("path", "")]


def _approve(owner, wid, text="Approve"):
    r = owner.post(f"/api/cexa/work/{wid}/answer", json={"text": text})
    assert r.status_code == 200, r.text
    return r.json()


def test_the_line_is_drawn_by_rules():
    d = lambda *a: P.decide(*a)[0]  # noqa: E731
    assert d("GET", "/api/users") == P.DO
    assert d("POST", "/api/users", {"email": "a@b.co", "role": "user"}) == P.DO
    assert d("POST", "/api/users", {"email": "a@b.co", "role": "admin"}) == P.ASK
    assert d("POST", "/api/users", {"email": "a@b.co", "tools": [1]}) == P.ASK
    assert d("PATCH", "/api/users/7", {"name": "X"}) == P.DO and d("PATCH", "/api/users/7", {"role": "owner"}) == P.ASK and d("PATCH", "/api/users/7", {"is_active": False}) == P.ASK
    assert d("POST", "/api/users/7/temp-password") == P.DO and d("POST", "/api/users/7/mfa-reset") == P.ASK and d("POST", "/api/users/access") == P.ASK
    assert d("POST", "/api/versions/3/promote") == P.ASK and d("POST", "/api/builder/release") == P.DO and d("POST", "/api/areas/dev/start") == P.DO
    assert d("DELETE", "/api/warehouse/files/4") == P.ASK and d("DELETE", "/api/builder/tools/x-y/draft") == P.DO
    assert d("POST", "/api/mailer/compose") == P.ASK and d("PUT", "/api/mailer/drafts/3") == P.DO
    assert d("POST", "/api/auth/change-password") == P.NEVER and d("POST", "/api/connections") == P.NEVER and d("GET", "/api/mymail/items") == P.NEVER
    assert d("POST", "/api/view-as/start") == P.NEVER and d("POST", "/api/../x") == P.NEVER
    found = []
    assert P.redact({"user": {"name": "T"}, "temp_password": "Abc-123"}, found) == {"user": {"name": "T"}, "temp_password": "[given to the person, not to C-EXA]"} and found == [("temp_password", "Abc-123")]


def test_routine_people_work_she_does_herself_and_passwords_never_reach_her(owner, db):
    me = _owner(db)
    out = T.run(db, me, "add_person", {"email": "nandi.k@test.local", "name": "Nandi Khumalo", "team": "Utilities billing"}, None)
    db.commit()
    assert "→ 200" in out and "[given to the person, not to C-EXA]" in out
    u = db.query(User).filter(User.email == "nandi.k@test.local").one()
    assert u.role == "user" and u.team.name == "Utilities billing"
    note = db.query(CexaMessage).filter(CexaMessage.user_id == me.id, CexaMessage.title == "Hand this over").order_by(CexaMessage.id.desc()).first()
    assert note is not None and "temp password:" in note.text
    out = T.run(db, me, "change_person", {"who": "nandi.k@test.local", "title": "Billing clerk"}, None)
    db.commit()
    db.expire_all()
    assert "→ 200" in out and db.get(User, u.id).title == "Billing clerk"
    out = T.run(db, me, "reset_password", {"who": "Nandi Khumalo"}, None)
    db.commit()
    assert "→ 200" in out and "Messages" in out
    assert "nandi.k@test.local" in T.run(db, me, "list_people", {"search": "nandi"}, None)


def test_rights_access_and_disabling_wait_for_the_owners_tap(owner, db):
    me = _owner(db)
    T.run(db, me, "add_person", {"email": "lebo.a@test.local", "name": "Lebo Admin-to-be"}, None)
    db.commit()
    u = db.query(User).filter(User.email == "lebo.a@test.local").one()
    out = T.run(db, me, "change_person", {"who": "lebo.a@test.local", "role": "admin"}, None)
    db.commit()
    assert "waits for the owner's tap" in out
    db.expire_all()
    assert db.get(User, u.id).role == "user"  # nothing happened yet
    (w,) = _waiting(db, f"/api/users/{u.id}")
    assert w.options == ["Approve", "Not now"] and "admin" in w.title
    assert db.query(CexaMessage).filter(CexaMessage.kind == "needs_you", CexaMessage.user_id == me.id).count()
    # asked twice: one card
    assert "already waiting" in T.run(db, me, "change_person", {"who": "lebo.a@test.local", "role": "admin"}, None)
    db.commit()
    got = _approve(owner, w.id)
    assert got["status"] == "done", got
    db.expire_all()
    assert db.get(User, u.id).role == "admin"
    # tool access: waits; Not now: nothing happens
    t = db.query(Tool).filter(Tool.slug == "pdf-studio").one()
    T.run(db, me, "give_access", {"who": "lebo.a@test.local", "tool": "PDF Studio"}, None)
    db.commit()
    (w2,) = _waiting(db, "/api/users/access")
    assert _approve(owner, w2.id, "Not now")["status"] == "declined"
    assert not db.query(ToolAccess).filter(ToolAccess.user_id == u.id, ToolAccess.tool_id == t.id).count()
    # disabling: waits
    T.run(db, me, "change_person", {"who": "lebo.a@test.local", "active": "no"}, None)
    db.commit()
    assert _waiting(db, f"/api/users/{u.id}")


def test_the_app_holds_the_line_not_her_prompt(owner, db):
    """A C-EXA session calling a guarded route directly: routine passes, risky is refused by the app itself."""
    me = _owner(db)
    sess = LoginSession(id="cexa-" + secrets.token_urlsafe(16), user_id=me.id, ip="cexa", agent="test")
    db.add(sess)
    db.commit()
    c = TestClient(app)
    c.cookies.set(SESSION_COOKIE, sess.id)
    assert c.post("/api/users", json={"email": "direct.u@test.local", "name": "Direct U", "role": "user"}).status_code == 200
    r = c.post("/api/users", json={"email": "direct.a@test.local", "name": "Direct A", "role": "admin"})
    assert r.status_code == 403 and "a person has to" in r.json()["detail"]
    assert c.post("/api/users/access", json={"user_id": me.id, "tool_id": 1, "granted": True}).status_code == 403
    for p in ("/api/connections", "/api/auth/change-password"):
        assert "can't do that one" in T.run(db, me, "call_api", {"method": "POST", "path": p, "body": "{}"}, None)
    sess.revoked = True
    db.commit()


def test_files_she_finds_files_moves_renames_and_bins_with_a_tap(owner, db):
    me = _owner(db)
    f = storage.store(db, me, "Telkom March bills.pdf", b"%PDF-1.4 test", sub="Inbox", mirror=False)
    db.commit()
    assert f"#{f.id}" in T.run(db, me, "list_files", {"search": "telkom march"}, None)
    inbox = db.get(Folder, f.folder_id)
    out = T.run(db, me, "make_folder", {"inside": inbox.path, "name": "Telkom"}, None)
    db.commit()
    assert "→ 200" in out, out
    out = T.run(db, me, "move_file", {"file": f"#{f.id}", "folder": f"{inbox.path}/Telkom"}, None)
    db.commit()
    assert "→ 200" in out, out
    out = T.run(db, me, "rename_file", {"file": f"#{f.id}", "name": "Telkom 2026-03"}, None)
    db.commit()
    db.expire_all()
    f = db.get(StoredFile, f.id)
    assert f.name == "Telkom 2026-03.pdf" and f.rel_path.endswith("/Telkom/Telkom 2026-03.pdf") and storage.abs_path(f).exists()
    assert "waits for the owner's tap" in T.run(db, me, "delete_file", {"file": f"#{f.id}", "why": "duplicate"}, None)
    db.commit()
    (w,) = _waiting(db, f"/api/warehouse/files/{f.id}")
    assert _approve(owner, w.id)["status"] == "done"
    db.expire_all()
    f = db.get(StoredFile, f.id)
    assert f.rel_path.startswith("Bin/") and storage.abs_path(f).exists()  # in the Bin, not gone
    # a tool's own files are the tool's
    tf = db.query(Folder).filter(Folder.kind == "tool").first()
    if tf is not None:
        assert "→ 400" in T.run(db, me, "move_file", {"file": f"#{f.id}", "folder": tf.path}, None)


@pytest.fixture()
def agents(db, owner, monkeypatch, tmp_path):
    from cbx.config import settings

    db.query(AgentWork).delete()
    db.commit()
    TM.ensure_teams(db)
    db.commit()
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    yield db
    db.rollback()


def test_agents_have_real_hands_as_the_owner(agents, owner):
    db = agents
    me = _owner(db)
    w = L.give(db, "", "Onboard the new clerk", '>> add_person {"email": "sipho.n@test.local", "name": "Sipho Ndlovu"}\n>> change_person {"who": "sipho.n@test.local", "role": "admin"}', me)
    db.commit()
    L.work_all()
    db.expire_all()
    u = db.query(User).filter(User.email == "sipho.n@test.local").one()
    assert u.role == "user"  # the plain user she made herself …
    (ask,) = _waiting(db, f"/api/users/{u.id}")  # … the admin right waits for the owner
    assert ask.proposal["asked_by_id"] == me.id
    from cbx.models import AuditLog

    row = db.query(AuditLog).filter(AuditLog.action == "account_created", AuditLog.target == "sipho.n@test.local").one()
    assert row.actor_id == me.id  # as the owner the work was for, never as the C-EXA account
    # still never: a shell or the sandbox
    assert "sh" not in L.Agent(db, None).hands(False) and "call_api" in L.Agent(db, None).hands(False)
    assert "call_api" not in L.Agent(db, None).hands(True)  # read the web: no changing hands for the rest of the work
    assert db.get(AgentWork, w.id).status in ("waiting", "done")
