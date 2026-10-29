"""C-EXA as a team member: chat, suggestions, briefs, check-ins, pulse, journal."""
from datetime import datetime, timedelta, timezone

from cbx.cexa import team
from cbx.models import CexaMessage, JournalEntry, User

SAST = timezone(timedelta(hours=2))
MORNING = datetime(2026, 9, 3, 7, 30, tzinfo=SAST)  # 07:30 SAST


def _uid(db, email):
    return db.query(User).filter(User.email == email).one().id


def test_chat_stores_messages_owner_gets_thinking(owner, admin, db):
    r = owner.post("/api/cexa/chat", json={"text": "What needs me today?"})
    assert r.status_code == 200, r.text
    assert r.json()["answer"]
    assert r.json()["thinking"]  # owners see the reasoning
    t = owner.get("/api/cexa/thread").json()["messages"]
    assert [m["from_axl"] for m in t[-2:]] == [False, True]
    assert t[-1]["thinking"]

    r = admin.post("/api/cexa/ask", json={"question": "Who is overloaded?", "route": "develop", "params": {}})
    assert r.status_code == 200, r.text
    assert r.json()["answer"] and r.json()["thinking"] == ""
    uid = _uid(db, "admin@test.local")
    stored = db.query(CexaMessage).filter(CexaMessage.user_id == uid).all()
    assert len(stored) >= 2 and all(m.thinking == "" for m in stored)
    # admins may not use the owner 1-on-1 endpoints
    assert admin.get("/api/cexa/journal").status_code == 403


def test_suggestions_per_route(owner, user):
    routes = {"req-queue": "request", "users": "60 days", "develop": "overloaded", "home": "since yesterday"}
    for route, word in routes.items():
        s = owner.get(f"/api/cexa/suggestions?route={route}").json()["suggestions"]
        assert 2 <= len(s) <= 3
        assert any(word in x["q"] for x in s), (route, s)
        assert all("hint" in x for x in s)
    assert user.get("/api/cexa/suggestions?route=home").status_code == 403


def test_brief_once_per_admin_per_day(owner, admin, db):
    day = MORNING.date().isoformat()
    people = team.staff(db)
    before = {u.id: db.query(CexaMessage).filter(CexaMessage.user_id == u.id, CexaMessage.kind == "brief").count() for u in people}
    made = team.send_briefs(db, now=MORNING)
    db.commit()
    made2 = team.send_briefs(db, now=MORNING)
    db.commit()
    assert made2 == []
    for u in people:
        n = db.query(CexaMessage).filter(CexaMessage.user_id == u.id, CexaMessage.kind == "brief").count()
        assert n == before[u.id] + (1 if any(m.user_id == u.id for m in made) else 0)
        assert db.query(JournalEntry).filter(JournalEntry.kind == "plan", JournalEntry.subject == f"brief:{day}:{u.id}").count() == 1
    # before 06:00 nothing goes out
    assert team.send_briefs(db, now=datetime(2026, 9, 4, 5, 0, tzinfo=SAST)) == []


def test_team_pulse_shape(owner, admin, db):
    aid = _uid(db, "admin@test.local")
    for i in range(3):
        owner.post("/api/develop/tasks", json={"title": f"Load {i}", "assignee_id": aid, "status": "working"})
    p = team.team_pulse(db)
    assert set(p) == {"people", "note"} and p["note"]
    names = [x["name"] for x in p["people"]]
    assert "C-EXA" in names and "Arnab" in names
    for x in p["people"]:
        assert set(x) >= {"name", "load", "level", "label"}
        assert 0 <= x["load"] <= 100 and x["level"] in ("", "warm", "hot")
    arnab = next(x for x in p["people"] if x["name"] == "Arnab")
    assert arnab["load"] >= 45 and "working" in arnab["label"]


def test_journal_refuses_personal(owner, db):
    assert team.remember(db, "team", "user:9", "Thandi is pregnant and will be off in May") is None
    assert team.remember(db, "learned", "user:9", "Thandi votes for a party I won't name") is None
    e = team.remember(db, "learned", "profile:emfuleni", "Emfuleni scans drop the case on kVA; NMD sits on page 2 when there are two meters.")
    assert e is not None
    db.commit()
    j = owner.get("/api/cexa/journal").json()["entries"]
    assert any(x["id"] == e.id for x in j)
    assert owner.delete(f"/api/cexa/journal/{e.id}").status_code == 200
    assert not any(x["id"] == e.id for x in owner.get("/api/cexa/journal").json()["entries"])


def test_plan_status_agents(owner):
    p = owner.get("/api/cexa/plan").json()
    assert p["total"] >= 2 and 0 <= p["done"] <= p["total"]
    st = owner.get("/api/cexa/status").json()
    assert st["mode"] == "stub" and st["model"]
    a = owner.get("/api/cexa/agents").json()
    assert {x["kind"] for x in a["agents"]} >= {"harvester", "dev"}


def test_go_refuses_a_third_build_when_both_sandboxes_are_busy(owner, db, monkeypatch):
    """free_slot falls back to sandbox-a when everything is taken; go() must refuse instead of stacking."""
    from cbx import jobs
    from cbx.cexa import developer
    from cbx.models import Proposal

    monkeypatch.setattr(jobs, "submit", lambda fn, *a, **k: None)  # no real builds here
    me = db.query(User).filter(User.role == "owner", User.is_axl.is_(False)).first()
    held = [Proposal(title=f"Busy {s}", evidence_kind="owner", status="signoff", sandbox=s) for s in ("sandbox-a", "sandbox-b")]
    db.add_all(held)
    db.commit()
    p = developer.propose(db, "Third build", "owner", "x", "y")
    db.commit()
    r = owner.post(f"/api/cexa/proposals/{p.id}/go")
    assert r.status_code == 400 and "sandbox" in r.json()["detail"].lower()
    db.expire_all()
    assert db.get(Proposal, p.id).status == "noticed"
    # "go ahead" in chat gets the same plain answer instead of a 500
    from cbx.models import Fix
    db.add(Fix(title="t", detail="t", area="axl", reported_by_id=me.id, status="code_pending", kind="code", proposal_id=p.id))
    db.commit()
    r = owner.post("/api/cexa/ask", json={"question": "go ahead", "route": "cexa-dev"})
    assert r.status_code == 200 and "can't start" in r.json()["answer"]
    for h in held:  # free the slots for the rest of the suite
        db.get(Proposal, h.id).status = "declined"
    db.get(Proposal, p.id).status = "declined"
    db.commit()


def test_failed_build_goes_back_to_noticed_and_stuck_builds_recover(owner, db, monkeypatch):
    import pytest

    from cbx import jobs
    from cbx.cexa import developer
    from cbx.models import Proposal, Task

    monkeypatch.setattr(jobs, "submit", lambda fn, *a, **k: None)
    monkeypatch.setattr(developer, "make_sandbox", lambda p: (_ for _ in ()).throw(RuntimeError("disk full")))
    p = developer.propose(db, "Build that dies", "owner", "x", "y")
    db.commit()
    me = db.query(User).filter(User.role == "owner", User.is_axl.is_(False)).first()
    developer.go(db, p, me)
    assert p.status == "building" and p.tests == {}
    with pytest.raises(RuntimeError):
        developer.build_job(p.id)
    db.expire_all()
    p = db.get(Proposal, p.id)
    assert p.status == "noticed" and "Build failed" in p.body and p.tests["ok"] is False
    assert db.get(Task, p.task_id).status == "todo"
    # a build the process lost (no dev agent on it, hours old, no verdict) is handed back too
    q = developer.propose(db, "Build the server forgot", "owner", "x", "y")
    q.status, q.sandbox = "building", "sandbox-b"
    q.updated_at = datetime.now(timezone.utc) - timedelta(hours=5)
    db.commit()
    assert [x.id for x in developer.recover_stuck_builds(db)] == [q.id]
    db.commit()
    db.expire_all()
    assert db.get(Proposal, q.id).status == "noticed"
    # but one whose tests failed (a finished build) waits for the owner's Stop
    q.status, q.tests = "building", {"ok": False, "failed": 2}
    q.updated_at = datetime.now(timezone.utc) - timedelta(hours=5)
    db.commit()
    assert developer.recover_stuck_builds(db) == []
    q.status = "declined"
    db.commit()


def test_clear_chat_hides_thread_and_resets_memory(owner, db):
    from cbx.models import CexaMessage
    owner.post("/api/cexa/chat", json={"text": "hello there"})
    assert any("hello there" in m["text"] for m in owner.get("/api/cexa/thread").json()["messages"])
    r = owner.post("/api/cexa/thread/clear")
    assert r.status_code == 200 and r.json()["cleared"] >= 2
    assert owner.get("/api/cexa/thread").json()["messages"] == []
    db.expire_all()
    assert db.query(CexaMessage).filter(CexaMessage.text == "hello there").count() == 1  # kept for the audit trail
