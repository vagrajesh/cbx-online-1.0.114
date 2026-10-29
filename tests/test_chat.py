"""Chat: who may talk to whom, who may read what, C-EXA when addressed, and nothing ever deleted."""
from __future__ import annotations

from fastapi.testclient import TestClient

from cbx.main import app
from cbx.models import ChatMessage
from tests.conftest import _make, _uid, login


def _client(owner, email, name, role, team="Utilities billing"):
    pw = _make(owner, email, name, role, team=team)
    c = TestClient(app)
    login(c, email, pw)
    return c


def test_you_can_message_your_team_and_the_admins_and_nobody_else(owner, db):
    a = _client(owner, "chat-a@test.local", "Ann Chat", "user", team="Chat team A")
    b = _client(owner, "chat-b@test.local", "Ben Chat", "user", team="Chat team A")
    z = _client(owner, "chat-z@test.local", "Zed Other", "user", team="Chat team Z")
    people = {p["name"] for p in a.get("/api/chat").json()["people"]}
    assert "Ben Chat" in people and "Test Owner" in people       # own team, and the owner
    assert "Zed Other" not in people                              # another team: not offered
    r = a.post("/api/chat/dm", json={"user_id": _uid(owner, "chat-z@test.local")})
    assert r.status_code == 400 and "own team" in r.json()["detail"]
    r = a.post("/api/chat/dm", json={"user_id": _uid(owner, "chat-b@test.local")})
    assert r.status_code == 200 and r.json()["kind"] == "dm" and r.json()["name"] == "Ben Chat"
    # admins can reach anyone
    assert "Zed Other" in {p["name"] for p in owner.get("/api/chat").json()["people"]}


def test_a_dm_is_private_even_from_the_owner(owner, db):
    a = _client(owner, "dm-a@test.local", "Dee Em", "user", team="DM team")
    b = _client(owner, "dm-b@test.local", "Emm Dee", "user", team="DM team")
    cid = a.post("/api/chat/dm", json={"user_id": _uid(owner, "dm-b@test.local")}).json()["id"]
    a.post(f"/api/chat/{cid}", json={"text": "between us"})
    assert b.get(f"/api/chat/{cid}").status_code == 200
    assert owner.get(f"/api/chat/{cid}").status_code == 404      # not 403: its existence is not confirmed either
    assert owner.post(f"/api/chat/{cid}", json={"text": "hello?"}).status_code == 404
    # and the audit trail never carries the text
    from cbx.models import AuditLog

    assert not db.query(AuditLog).filter(AuditLog.detail.contains("between us")).count()


def test_the_team_room_is_the_team(owner, db):
    a = _client(owner, "tr-a@test.local", "Tee Ay", "user", team="Room team")
    b = _client(owner, "tr-b@test.local", "Tee Bee", "user", team="Room team")
    z = _client(owner, "tr-z@test.local", "Tee Zed", "user", team="Not that team")
    convs = a.get("/api/chat").json()["conversations"]
    room = next(c for c in convs if c["kind"] == "team")
    assert room["name"] == "Room team"
    a.post(f"/api/chat/{room['id']}", json={"text": "morning all"})
    assert z.get(f"/api/chat/{room['id']}").status_code == 404
    assert owner.get(f"/api/chat/{room['id']}").status_code == 200   # admins may read team rooms
    # unread counts: Ben has one, Ann (who sent it) has none — until Ben opens it
    assert next(c for c in b.get("/api/chat").json()["conversations"] if c["id"] == room["id"])["unread"] == 1
    assert next(c for c in a.get("/api/chat").json()["conversations"] if c["id"] == room["id"])["unread"] == 0
    assert b.get("/api/home/counts").json()["chat"] >= 1
    assert any(m["text"] == "morning all" for m in b.get(f"/api/chat/{room['id']}").json()["messages"])  # opening it reads it
    assert next(c for c in b.get("/api/chat").json()["conversations"] if c["id"] == room["id"])["unread"] == 0


def test_cexa_answers_when_addressed_and_only_then(owner, db, monkeypatch):
    from cbx.cexa import brain

    monkeypatch.setattr(brain, "ask", lambda db, me, q, route="", params=None, **k: {"answer": f"You asked: {q}", "thinking": "", "id": None})
    a = _client(owner, "ax-a@test.local", "Ax Ay", "user", team="Ax team")
    room = next(c for c in a.get("/api/chat").json()["conversations"] if c["kind"] == "team")
    r = a.post(f"/api/chat/{room['id']}", json={"text": "anyone seen the Absa file?"})
    assert r.json()["asked_axl"] is False
    r = a.post(f"/api/chat/{room['id']}", json={"text": "@C-EXA what is expiring this week"})
    assert r.json()["asked_axl"] is True
    msgs = a.get(f"/api/chat/{room['id']}").json()["messages"]          # jobs run inline in tests
    cexa = [m for m in msgs if m["kind"] == "axl"]
    assert len(cexa) == 1 and cexa[0]["text"] == "You asked: what is expiring this week" and cexa[0]["sender"] == "C-EXA"
    r = a.post(f"/api/chat/{room['id']}", json={"text": "C-EXA, who is on the team?"})
    assert r.json()["asked_axl"] is True
    assert len([m for m in a.get(f"/api/chat/{room['id']}").json()["messages"] if m["kind"] == "axl"]) == 2


def test_rooms_are_made_by_admins_and_managers_and_polling_returns_only_new_lines(owner, db):
    a = _client(owner, "rm-a@test.local", "Ar Em", "user", team="Rm team")
    assert a.post("/api/chat/rooms", json={"name": "Secret", "member_ids": []}).status_code == 400
    ids = [_uid(owner, "rm-a@test.local")]
    r = owner.post("/api/chat/rooms", json={"name": "Capitec close", "member_ids": ids})
    assert r.status_code == 200 and r.json()["kind"] == "room"
    cid = r.json()["id"]
    m1 = owner.post(f"/api/chat/{cid}", json={"text": "first"}).json()["message"]["id"]
    m2 = owner.post(f"/api/chat/{cid}", json={"text": "second"}).json()["message"]["id"]
    got = a.get(f"/api/chat/{cid}?after={m1}").json()["messages"]
    assert [m["id"] for m in got] == [m2]
    assert owner.post(f"/api/chat/{cid}", json={"text": ""}).status_code == 400
    assert owner.post(f"/api/chat/{cid}", json={"text": "x" * 5000}).status_code == 400


def test_cexas_hands_cannot_speak_as_a_person(owner, db, owner_pw):
    """A message from C-EXA's own session is refused: she answers in her own name (kind=axl, the stored marker) or not at all."""
    from cbx.security.auth import SESSION_COOKIE

    room = next(c for c in owner.get("/api/chat").json()["conversations"] if c["kind"] == "team")
    c = TestClient(app)
    c.cookies.set(SESSION_COOKIE, "cexa-" + owner.cookies.get(SESSION_COOKIE))
    assert c.post(f"/api/chat/{room['id']}", json={"text": "I am Edwin"}).status_code in (401, 403)
