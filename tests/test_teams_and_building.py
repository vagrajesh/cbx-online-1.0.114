"""1.0.82: more teams of agents (made by the owner or by C-EXA for him), and C-EXA building tools the way a person
does in the Builder — Save → Try → Test → Deploy — with going live still the owner's one tap."""
import json
import os

import pytest

from cbx.cexa import loop as L
from cbx.cexa import teams as TM
from cbx.cexa import tools as T
from cbx.config import settings
from cbx.models import AgentTeam, AgentWork, AppVersion, TeamMember, User, WorkArea
from cbx.versions import sandbox_runner as R

NESTED = bool(os.environ.get("CBX_SANDBOX_RUN"))

FOLDER = {"manifest": {"title": "tool.title", "icon": "list", "menu": {"section": "My tools", "order": 60}, "access": {"roles": ["owner"]}, "home": "rows"},
          "pages": {"rows": {"type": "table", "title": "page.rows.title", "source": {"data": "visits"}}},
          "data": {"visits": {"kind": "table", "columns": [{"key": "site", "type": "text", "label": "col.site"}]}},
          "languages": {"en": {"tool.title": "Site visits", "page.rows.title": "Visits", "col.site": "Site"}}, "readme": "# Site visits\n"}


def _owner(db):
    return db.query(User).filter(User.email == "owner@test.local").one()


def _drop(db, key):
    t = db.query(AgentTeam).filter(AgentTeam.key == key).first()
    if t is not None:
        ids = [m.id for m in db.query(TeamMember).filter(TeamMember.team_id == t.id).all()]
        db.query(AgentWork).filter(AgentWork.to_member_id.in_(ids or [0])).delete(synchronize_session=False)
        db.query(TeamMember).filter(TeamMember.team_id == t.id).delete(synchronize_session=False)
        db.delete(t)
        db.commit()


@pytest.fixture()
def fresh(db, owner):
    TM.ensure_teams(db)
    db.commit()
    for k in ("telkom-billing", "telkom-audit", "lease-desk"):
        _drop(db, k)
    yield db
    db.rollback()
    for k in ("telkom-billing", "telkom-audit", "lease-desk"):
        _drop(db, k)


def test_the_owner_adds_teams_gives_them_work_and_removes_them(fresh, owner):
    db = fresh
    r = owner.post("/api/cexa/teams", json={"name": "Telkom billing", "purpose": "Check the Telkom bills every month and chase the gaps", "kind": "work"})
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["custom"] and t["kind"] == "work" and (t["lead"] or {}).get("name") == "Telkom lead" and len(t["members"]) == 3, (t["lead"], t["members"])
    assert {m["role"] for m in t["members"]} == {"lead", "operator", "helper"}
    assert owner.post("/api/cexa/teams", json={"name": "Telkom billing", "purpose": "again"}).status_code == 400
    assert owner.post("/api/cexa/teams", json={"name": "X team", "purpose": "a line", "kind": "core"}).status_code == 400
    # a second team from the same first word: its agents get their own names, so work finds the right one
    t2 = owner.post("/api/cexa/teams", json={"name": "Telkom audit", "purpose": "Audit the Telkom accounts each quarter"}).json()
    assert t2["lead"]["name"] != "Telkom lead"
    assert owner.post(f"/api/cexa/teams/{t2['key']}/members", json={"name": "Telkom ops", "role": "operator"}).status_code == 400
    # work for the team: its lead takes it
    w = owner.post("/api/cexa/work", json={"to": "Telkom lead", "title": "File the March bills", "detail": "Into Inbox/Telkom"})
    assert w.status_code == 200, w.text
    lead = db.query(TeamMember).filter(TeamMember.name == "Telkom lead").one()
    assert db.get(AgentWork, w.json()["id"]).to_member_id == lead.id
    # not while work is open; then yes; and never a team that came with CBX
    assert owner.delete("/api/cexa/teams/telkom-billing").status_code == 400
    db.expire_all()
    db.get(AgentWork, w.json()["id"]).status = "done"
    db.commit()
    assert owner.delete("/api/cexa/teams/telkom-billing").status_code == 200
    assert owner.delete(f"/api/cexa/teams/{TM.TEAM_KEYS[0]}").status_code == 400
    assert all(x["key"] != "telkom-billing" for x in owner.get("/api/cexa/teams").json()["teams"])


def test_a_working_team_on_a_schedule_hands_its_lead_the_standing_order(fresh, owner):
    db = fresh
    me = _owner(db)
    t = TM.create_team(db, {"name": "Lease desk", "purpose": "Every Monday: the leases that end within 90 days", "kind": "work", "schedule": {"every": "week", "day": "mon", "at": "07:00"}}, me)
    db.commit()
    assert TM.next_run(t) and "Mondays" in TM.next_run(t)
    w = TM.give_work(db, t, reason="the schedule")
    assert w is not None and w.to_member_id == TM.lead_of(db, t).id and "90 days" in w.detail
    assert TM.give_work(db, t, reason="the schedule") is None  # one standing turn at a time


def test_cexa_makes_teams_but_never_answers_her_own_asks(fresh, owner):
    db = fresh
    me = _owner(db)
    out = T.run(db, me, "make_team", {"name": "Telkom audit", "purpose": "Audit the Telkom accounts each quarter"}, None)
    db.commit()
    assert "→ 200" in out, out
    out = T.run(db, me, "give_team_work", {"team": "Telkom audit", "title": "Start with Q1", "detail": "The three accounts"}, None)
    db.commit()
    assert "→ 200" in out, out
    out = T.run(db, me, "call_api", {"method": "DELETE", "path": "/api/cexa/teams/telkom-audit", "body": "", "why": "not needed"}, None)
    db.commit()
    assert "waits for the owner's tap" in out
    ask = db.query(AgentWork).filter(AgentWork.kind == "api", AgentWork.status == "waiting").order_by(AgentWork.id.desc()).first()
    for path in (f"/api/cexa/work/{ask.id}/answer", "/api/cexa/missions/1/go", "/api/voice/calls/1/answer"):
        assert "can't do that one" in T.run(db, me, "call_api", {"method": "POST", "path": path, "body": '{"text": "Approve"}'}, None)
    db.expire_all()
    assert db.get(AgentWork, ask.id).status == "waiting"
    assert {"make_team", "give_team_work"} <= set(L.Agent(db, None).hands(False)) and "make_team" not in L.Agent(db, None).hands(True)


@pytest.fixture()
def drafts(owner, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    yield tmp_path


def test_cexa_builds_changes_and_tries_a_tool(drafts, owner, db):
    me = _owner(db)
    out = T.run(db, me, "build_tool", {"tool": "site-visits", "folder": json.dumps(FOLDER)}, None)
    assert out.startswith("Saved the draft of site-visits") and "No problems" in out, out
    assert "Make the folder" not in out
    got = json.loads(T.run(db, me, "tool_folder", {"tool": "site-visits"}, None))
    assert got["source"] == "draft" and got["words_en"]["tool.title"] == "Site visits"
    # change one part: a column, and its words
    out = T.run(db, me, "change_tool_part", {"tool": "site-visits", "part": "table", "name": "visits",
                                             "value": json.dumps({"kind": "table", "columns": [{"key": "site", "type": "text", "label": "col.site"}, {"key": "when", "type": "date", "label": "col.when"}]})}, None)
    assert "Saved" in out and "col.when" in out  # the new label's words are missing
    out = T.run(db, me, "change_tool_part", {"tool": "site-visits", "part": "words", "value": json.dumps({"col.when": "Visited on"})}, None)
    assert "No problems" in out, out
    out = T.run(db, me, "change_tool_part", {"tool": "site-visits", "part": "colour", "value": "{}"}, None)
    assert out.startswith("Which part")
    tried = json.loads(T.run(db, me, "try_tool", {"tool": "site-visits"}, None))
    assert tried["page"] == "rows" and tried["problems"] == []
    db.commit()
    # it is the owner's draft, saved as him — and the Builder shows its step state
    one = owner.get("/api/builder/tools/site-visits").json()
    assert one["source"] == "draft" and one["released"] is None and one["editor"]["mine"]
    ov = owner.get("/api/builder").json()
    assert all(x["tools"] for x in ov["mine"]) and ov["can_promote"] is True
    assert owner.delete("/api/builder/tools/site-visits/draft").json()["ok"]


@pytest.fixture()
def fake_runner(monkeypatch):
    def start(slot, path, owners=None, *, kind="version", title=""):
        R.RUNNING[slot] = {"kind": kind, "title": title, "path": path}
        return {"ok": True, "url": "http://127.0.0.1", "login": []}

    monkeypatch.setattr(R, "start", start)
    monkeypatch.setattr(R, "status", lambda: [{"slot": sl, "running": True, "healthy": True, "title": w["title"]} for sl, w in R.RUNNING.items()])
    monkeypatch.setattr(R, "stop", lambda slot: R.RUNNING.pop(slot, None) and {"ok": True})
    yield
    R.RUNNING.clear()


def test_the_step_bar_shows_my_test_runs_and_cexa_deploys_to_test_but_live_waits(owner, admin, db, fake_runner, tmp_path):
    me = _owner(db)
    from cbx.versions import injector

    v = AppVersion(tag="9.8.2", status="sandbox", env="sandbox-a", package_path=str(tmp_path), uploaded_by_id=me.id,
                   tests={"ok": True, "passed": 41, "failed": 0, "output": "41 passed in 9s", "base": injector.current_tag(), "builder": {"by": me.id, "tools": ["site-visits"]}})
    bad = AppVersion(tag="9.8.1", status="failed", env="sandbox-a", package_path=str(tmp_path), uploaded_by_id=me.id,
                     tests={"ok": False, "passed": 40, "failed": 1, "failures": ["tools/site-visits/tests/test_site_visits.py::test_pages"], "output": "1 failed", "builder": {"by": me.id, "tools": ["site-visits"]}})
    db.add(bad)
    db.commit()
    db.add(v)
    db.commit()
    try:
        mine = owner.get("/api/builder").json()["mine"]
        assert [x["tag"] for x in mine[:2]] == ["9.8.2", "9.8.1"] and mine[1]["failures"] and not mine[0]["stale"]
        log = owner.get(f"/api/builder/released/{bad.id}/log").json()
        assert log["failed"] == 1 and "1 failed" in log["output"]
        assert admin.get(f"/api/builder/released/{bad.id}/log").status_code == 404  # not theirs
        assert admin.get("/api/builder").json()["mine"] == []
        # C-EXA: a failed version is not for Test; a passed one she puts in Test herself
        assert "Not in Test" in T.run(db, me, "deploy_version", {"version": str(bad.id), "to": "test"}, None)
        assert "is in Test" in T.run(db, me, "deploy_version", {"version": str(v.id), "to": "test"}, None)
        db.commit()
        db.expire_all()
        assert db.get(AppVersion, v.id).tests["in_test"]
        assert owner.get("/api/builder").json()["mine"][0]["in_test"]
        # … live waits for the owner's tap
        out = T.run(db, me, "deploy_version", {"version": str(v.id), "to": "live"}, None)
        db.commit()
        assert "waits for the owner's tap" in out
        db.expire_all()
        assert db.get(AppVersion, v.id).status == "sandbox"
        ask = db.query(AgentWork).filter(AgentWork.kind == "api", AgentWork.status == "waiting").order_by(AgentWork.id.desc()).first()
        assert ask.proposal["path"].startswith(f"/api/versions/{v.id}/promote")
        assert owner.post(f"/api/cexa/work/{ask.id}/answer", json={"text": "Not now"}).json()["status"] == "declined"
    finally:
        db.query(WorkArea).filter(WorkArea.version_id.in_([v.id, bad.id])).update({WorkArea.version_id: None}, synchronize_session=False)
        db.query(AppVersion).filter(AppVersion.id.in_([v.id, bad.id])).delete(synchronize_session=False)
        db.commit()


@pytest.mark.skipif(NESTED, reason="no nested sandbox test runs")
def test_cexa_tests_her_build_for_real(drafts, owner, db, monkeypatch):
    from cbx.versions import injector

    monkeypatch.setattr(injector, "TEST_CMD", [*injector.TEST_CMD[:4], "tests/test_core.py", "-x", "-k", "test_login_and_roles"])
    monkeypatch.setattr(injector, "RESTART_ENABLED", False)
    monkeypatch.setattr(injector, "RUN_PIP", False)
    me = _owner(db)
    assert "No problems" in T.run(db, me, "build_tool", {"tool": "site-visits", "folder": json.dumps(FOLDER)}, None)
    out = T.run(db, me, "test_tools", {"tools": "site-visits", "notes": "Site visits, by C-EXA"}, None)
    db.commit()
    assert " passed, " in out and "version #" in out, out
    db.commit()
    one = owner.get("/api/builder/tools/site-visits").json()
    mine = owner.get("/api/builder").json()["mine"]
    assert one["released"] and mine[0]["tag"] == one["released"] and mine[0]["tools"] == ["site-visits"]
    # a change after the test: the step bar asks for a new test
    assert "Saved" in T.run(db, me, "change_tool_part", {"tool": "site-visits", "part": "readme", "value": "# Site visits\nChanged."}, None)
    db.commit()
    assert owner.get("/api/builder/tools/site-visits").json()["released"] is None
    vid = mine[0]["id"]
    db.query(WorkArea).filter(WorkArea.version_id == vid).update({WorkArea.version_id: None}, synchronize_session=False)
    db.query(AppVersion).filter(AppVersion.id == vid).delete(synchronize_session=False)
    db.commit()
