"""C-EXA's team (1.0.49): a job from the brief to a sound Builder draft — her questions, the plan, Go, the
agents working their tasks (data, flows, pages, sample rows, looks, words, README, the test, her write-up),
and what the owner can do along the way. Model mode is stub, so every answer is deterministic."""
from __future__ import annotations

import json
import shutil

import pytest

from cbx.api.builder_api import drafts_root
from cbx.cexa import ceo, eyes
from cbx.engine import loader
from cbx.models import AuditLog, CexaMessage, Mission, MissionTask, Proposal

PM = "Build me a project manager tool: projects and their tasks, who has each task and when it is due, remind people when a task is late, and tell me every morning what slipped."


@pytest.fixture(autouse=True)
def _clean():
    yield
    if drafts_root().is_dir():
        for p in drafts_root().iterdir():
            if (p / ".cexa-job").exists():
                shutil.rmtree(p, ignore_errors=True)


def _job(owner, brief=PM) -> dict:
    r = owner.post("/api/cexa/missions", json={"brief": brief})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_job_from_brief_to_review(owner, db):
    j = _job(owner)
    # she read it and asked her questions
    assert j["status"] == "asking" and len(j["questions"]) == 2 and j["title"] == "Project Manager"
    assert any(e["kind"] == "model" and e["has_prompt"] for e in j["events"])
    assert db.query(CexaMessage).filter(CexaMessage.title == "C-EXA has questions").count() >= 1
    # answered → the plan, waiting for Go
    j = owner.post(f"/api/cexa/missions/{j['id']}/answers", json={"answers": ["Everyone", "Both"]}).json()
    assert j["status"] == "plan", j["state"]
    plan = j["plan"]
    assert plan["slug"] == "project-manager" and [d["name"] for d in plan["data"]] == ["projects", "tasks"]
    assert {a["role"] for a in plan["team"]} == {"teamlead", "developer", "designer", "tester", "helper"}  # the Build team, no core developer without core code
    assert {a["name"] for a in plan["team"]} == {"Nova", "Dev-1", "Dev-2", "Iris", "Tess", "Hal"}
    assert not (drafts_root() / "project-manager").exists()  # nothing is built before Go
    # Go → the team works it through (jobs run inline in the suite)
    j = owner.post(f"/api/cexa/missions/{j['id']}/go").json()
    assert j["status"] == "review", (j["state"], [(t["title"], t["status"], t["output"].get("problem")) for t in j["tasks"] if t["status"] != "done"])
    assert all(t["status"] == "done" for t in j["tasks"]), [(t["title"], t["output"]) for t in j["tasks"] if t["status"] != "done"]
    kinds = {t["kind"] for t in j["tasks"]}
    assert kinds >= {"data", "flow", "page", "sample", "look", "words", "readme", "test", "summary"}
    # every agent had work and is done
    assert {a["role"] for a in j["agents"]} == {"lead", "teamlead", "developer", "designer", "tester", "helper"}
    assert all(a["tasks"] > 0 for a in j["agents"] if a["role"] != "lead")
    # the draft is sound, the flows are switched off, nothing is live
    t = loader.read_folder(drafts_root() / "project-manager")
    assert t.ok, t.errors
    assert set(t.data) == {"projects", "tasks"} and set(t.pages) == {"projects", "tasks"} and set(t.flows) == {"remind_late", "morning_check"}
    assert all(f["enabled"] is False for f in t.flows.values())
    assert t.manifest["owner"] == "owner@test.local" and t.manifest["home"] == "projects"
    assert "project-manager" not in loader.registry().tools
    assert t.readme.startswith("# Project Manager")
    # 1.0.51: the status is a pick-list, a task links to its project, a task can carry a file
    tc = {c["key"]: c for c in t.data["tasks"]["columns"]}
    assert tc["project"]["type"] == "link" and tc["project"]["table"] == "projects"
    assert tc["status"]["type"] == "choice" and tc["status"]["options"] == ["Not started", "In progress", "Late", "Done"]
    assert tc["evidence"]["type"] == "file"
    assert "status" in (loader.widget("tasks", t.pages["tasks"], "tasks_list") or {}).get("filters", [])
    sample = ceo._sample_rows(db, db.get(Mission, j["id"]))
    assert all(r["status"] in tc["status"]["options"] and isinstance(r["project"], int) for r in sample["tasks"])
    pay = eyes.payload_for(ceo._read(db.get(Mission, j["id"])), "tasks", sample)
    assert pay["rows"]["tasks_list"]["rows"][0]["_labels"]["project"].startswith("Project ")
    # words were filed under keys; the page JSON holds keys, English holds the words
    assert t.languages["en"]["tool.title"] == "Project Manager" and t.pages["projects"]["title"] == "page.projects.title"
    # the tester's verdict and the write-up
    test = next(x for x in j["tasks"] if x["kind"] == "test")
    assert not test["output"]["errors"] and all(v == "ok" for v in test["output"]["pages"].values())
    assert j["summary"] and "Builder draft" in j["summary"]
    assert j["draft"]["exists"] and not j["draft"]["errors"] and "pages/projects.json" in j["draft"]["files"]
    # the audit trail says the team wrote the draft, as the AI
    rows = db.query(AuditLog).filter(AuditLog.action == "cexa_draft_written").all()
    assert rows and all(r.actor_label.startswith("axl→agent:") for r in rows)
    # the owner closes it
    j = owner.post(f"/api/cexa/missions/{j['id']}/close").json()
    assert j["status"] == "done"


def test_skip_questions_change_the_plan_and_stop(owner, db):
    j = _job(owner, "A tool for the laptops we hand out, who has which one.")
    j = owner.post(f"/api/cexa/missions/{j['id']}/answers", json={"answers": [], "skip": True}).json()
    assert j["status"] == "plan" and all("judgement" in q["a"] for q in j["questions"])
    j = owner.post(f"/api/cexa/missions/{j['id']}/replan", json={"text": "Call it Laptop register"}).json()
    assert j["status"] == "plan" and "Changed as you asked" in j["plan"]["summary"]
    assert owner.post(f"/api/cexa/missions/{j['id']}/go").json()["status"] == "review"
    # asking for changes: a round 2 on the same draft
    j = owner.post(f"/api/cexa/missions/{j['id']}/revise", json={"text": "Put the form on the left"}).json()
    assert j["status"] == "review" and j["round"] == 2 and any(t["round"] == 2 and t["kind"] == "fix" for t in j["tasks"])
    assert owner.post(f"/api/cexa/missions/{j['id']}/stop").json()["status"] == "stopped"


def test_only_the_owner_and_never_the_ai(admin, user, owner, db):
    assert admin.get("/api/cexa/missions").status_code == 403
    assert user.post("/api/cexa/missions", json={"brief": PM}).status_code == 403
    assert owner.post("/api/cexa/missions", json={"brief": "hi"}).status_code == 400
    j = _job(owner)
    assert owner.post(f"/api/cexa/missions/{j['id']}/go").status_code == 400  # no plan yet


def test_plan_is_made_safe(owner, db):
    m = Mission(title="x", brief="x")
    db.add(m)
    db.flush()
    p = ceo.normalise_plan(db, m, {"title": "Entity Compliance", "section": "Admin", "roles": ["user", "admin", "hacker"], "icon": "rocket",
                                    "data": [{"name": "Bad Name!", "columns": ["a:text"]}, {"name": "bad_name", "columns": []}] + [{"name": f"t{i}"} for i in range(9)],
                                    "pages": [], "flows": [{"name": "every day", "trigger": "every morning"}],
                                    "team": [{"name": "X", "role": "lead"}, {"name": "A", "role": "dev"}, {"name": "B", "role": "dev"}, {"name": "C", "role": "dev"}, {"name": "Q", "role": "core"}]})
    assert p["slug"] != "entity-compliance" and p["slug"].startswith("entity-compliance")  # the live folder is never the target
    assert p["roles"] == ["admin"] and p["icon"] == "board" and len(p["data"]) <= 4 and p["data"][0]["name"] == "bad_name"
    assert p["pages"] and p["flows"][0]["trigger"] == "schedule"
    assert sum(1 for a in p["team"] if a["role"] == "developer") == 2 and not any(a["role"] in ("lead", "core") for a in p["team"])
    assert {"designer", "tester", "helper"} <= {a["role"] for a in p["team"]}


def test_keys_are_filed_for_plain_words():
    F = {"manifest": {"title": "Orders"}, "pages": {"main": {"title": "All orders", "layout": {"sections": [{"id": "s1", "columns": [{"width": 12, "widgets": [
        {"id": "t", "type": "table", "title": "Orders", "columns": [{"key": "qty", "label": "How many"}]}, {"id": "h", "type": "heading", "text": "page.main.title"}]}]}]}}},
         "data": {"orders": {"columns": [{"key": "qty", "label": "Quantity"}]}}, "flows": {"f": {"title": "Nudge"}}, "languages": {"en": {"page.main.title": "All orders"}}}
    ceo.keyify(F)
    en = F["languages"]["en"]
    w = F["pages"]["main"]["layout"]["sections"][0]["columns"][0]["widgets"]
    assert F["manifest"]["title"] == "tool.title" and en["tool.title"] == "Orders"
    assert w[0]["title"] == "w.t.title" and en["w.t.title"] == "Orders" and w[0]["columns"][0]["label"] == "w.t.col.qty"
    assert w[1]["text"] == "page.main.title"  # already a key the English has: left alone
    assert F["data"]["orders"]["columns"][0]["label"] == "col.orders.qty" and F["flows"]["f"]["title"] == "flow.f.title"


def test_the_designer_asks_for_a_fix_below_the_bar(owner, db, monkeypatch):
    from cbx.cexa import team_stub

    looks = []

    def strict(d):
        looks.append(d)
        return {"score": 40 if len(looks) == 1 else 90, "verdict": "The form sits above the table.", "good": [], "problems": ["The form is above the table"], "fixes": [{"block": "add", "change": "put it beside the table"}]}

    monkeypatch.setattr(team_stub, "_team_look", strict)
    j = _job(owner, "A tool for the laptops we hand out, who has which one.")
    j = owner.post(f"/api/cexa/missions/{j['id']}/answers", json={"skip": True}).json()
    j = owner.post(f"/api/cexa/missions/{j['id']}/go").json()
    assert j["status"] == "review"
    fix = [t for t in j["tasks"] if t["kind"] == "fix"]
    again = [t for t in j["tasks"] if t["kind"] == "look" and t["parent_id"]]
    assert len(fix) == 1 and fix[0]["status"] == "done" and len(again) == 1 and again[0]["output"]["score"] == 90
    # the words and the test waited for the second look
    order = {t["key"]: i for i, t in enumerate(sorted(j["tasks"], key=lambda t: t["id"]))}
    assert again[0]["key"] in next(t for t in j["tasks"] if t["kind"] == "test")["depends"]


def test_core_code_goes_to_a_sandbox_proposal(owner, db, monkeypatch):
    from cbx.cexa import developer

    built = []

    def fake_go(db_, p, by):
        built.append(p.id)
        p.status = "signoff"
        p.tests = {"passed": 12, "failed": 0, "ok": True}
        p.sandbox = "sandbox-a"
        db_.flush()
        return p

    monkeypatch.setattr(developer, "go", fake_go)
    j = _job(owner, "Build a project manager tool with a new timeline block in core.")
    j = owner.post(f"/api/cexa/missions/{j['id']}/answers", json={"skip": True}).json()
    assert j["plan"]["code"] and any(a["role"] == "core" for a in j["plan"]["team"])
    j = owner.post(f"/api/cexa/missions/{j['id']}/go").json()
    code = next(t for t in j["tasks"] if t["kind"] == "code")
    assert code["status"] == "done" and built and db.get(Proposal, built[0]).evidence_kind == "job"
    assert j["status"] == "review"


def test_budget_pauses_the_job(owner, db, monkeypatch):
    j = _job(owner, "A tool for the laptops we hand out, who has which one.")
    owner.post(f"/api/cexa/missions/{j['id']}/answers", json={"skip": True})
    m = db.get(Mission, j["id"])
    db.refresh(m)
    m.max_calls = m.calls + 1
    db.commit()
    j = owner.post(f"/api/cexa/missions/{j['id']}/go").json()
    assert j["status"] == "working" and j["paused"] and "model calls" in j["state"]
    j = owner.post(f"/api/cexa/missions/{j['id']}/resume").json()
    assert j["max_calls"] >= j["calls"] + 1


def test_preview_page_needs_a_live_token(client, owner):
    F = {"manifest": {"title": "tool.title"}, "pages": {"main": {"title": "page.main.title", "layout": {"sections": [{"id": "s", "columns": [{"width": 12, "widgets": [{"id": "t", "type": "table", "source": {"data": "items"}}]}]}]}}},
         "data": {"items": {"columns": [{"key": "name", "type": "text"}]}}, "flows": {}, "languages": {"en": {"tool.title": "Tool", "page.main.title": "Main"}}}
    payload = eyes.payload_for(F, "main", {"items": [{"id": 1, "name": "Laptop A"}]})
    assert payload["rows"]["t"]["rows"][0]["name"] == "Laptop A" and payload["title"] == "Main"
    tok = eyes.issue(payload)
    r = client.get(f"/cexa-preview/{tok}")
    assert r.status_code == 200 and "cexa-preview.js" in r.text and "Laptop A" in r.text
    assert client.get(f"/cexa-preview/{tok}").status_code == 404  # used once
    assert client.get("/cexa-preview/not-a-token-at-all-000000").status_code == 404


# ---- 1.0.50: the standing teams ------------------------------------------------------------------------------

from cbx.cexa import teams as T  # noqa: E402
from cbx.config import settings  # noqa: E402
from cbx.models import AgentTeam, AppVersion  # noqa: E402


def test_the_teams_are_there_with_leads(owner, db):
    org = owner.get("/api/cexa/teams").json()
    # 1.0.98: the ported Support desk bots (Mo, Lu, Kit, Ada, Nudge, Iris) sit on a "support" team, made the first time one
    # works (cbx/compat2.py): the Office shows a desk only for a member of a team on this roster. It has no lead and no hands.
    keys = [t["key"] for t in org["teams"] if t["key"] != "support"]
    assert keys == list(T.TEAM_KEYS) and len(keys) == 11  # 1.0.94: AP
    for t in org["teams"]:
        if t["key"] == "support":
            assert all(m["role"] == "bot" for m in t["members"])
            continue
        assert t["lead"] and len(t["members"]) >= 3 and t["owner"]["name"], t["key"]
    assert org["ceo"]["name"] == "C-EXA" and org["can_act"]


def test_the_ap_team_four_bots_that_prepare_and_check(owner, db):
    """1.0.94: AP — the SSC job descriptions and the AP managers' KPAs in four bots; people approve and pay.
    1.0.95: Quinn for supplier and internal queries; the team runs the AP Tool."""
    ap = next(t for t in owner.get("/api/cexa/teams").json()["teams"] if t["key"] == "ap")
    assert ap["name"] == "AP" and ap["kind"] == "work"
    # 1.0.98: the AP Tool's capture bots ("Sol (AP)" … "Tess (AP)") get desks on this team the first time they work, so the
    # Office shows them (cbx/compat2.py); they are role "bot": no hands, and work handed to them is closed at once
    team = [m for m in ap["members"] if m["role"] != "bot"]
    assert all(m["name"].endswith(" (AP)") for m in ap["members"] if m["role"] == "bot")
    assert [m["name"] for m in team] == ["Paula", "Ivy", "Bo", "Rae", "Quinn"] and team[0]["lead"]
    assert "Four-eye principle" in ap["purpose"] and "releases a payment" in ap["purpose"]
    assert {m["role"] for m in team} <= set(T.ROLES)


def test_a_new_job_is_read_before_any_question_box(owner, db, monkeypatch):
    # with jobs not run inline, the job sits in "reading": no questions yet, so answers are refused
    from cbx import jobs

    monkeypatch.setattr(jobs, "_inline", False)
    monkeypatch.setattr(jobs, "submit", lambda *a, **k: None)
    j = _job(owner, PM)
    assert j["status"] == "reading" and not j["questions"]
    r = owner.post(f"/api/cexa/missions/{j['id']}/answers", json={"answers": []})
    assert r.status_code == 400


def test_advice_gives_the_document_asked_for(owner, db):
    j = _job(owner, "Recommend an ideal structure for the automation team. Just give me a list, and make sure it complies with our policies.")
    assert j["kind"] == "advice" and j["team"]["key"] == "advice" and j["status"] == "asking"
    j = owner.post(f"/api/cexa/missions/{j['id']}/answers", json={"answers": ["The board"]}).json()
    assert j["status"] == "review" and "## The list" in j["document"] and "owner: the lead" in j["document"]
    assert [t["kind"] for t in j["tasks"]] == ["outline", "draft", "acheck", "final", "tsummary"]
    assert {a["name"] for a in j["agents"]} >= {"Ava", "Ana", "Rhea", "Wes"}
    r = owner.get(f"/api/cexa/missions/{j['id']}/document.md")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    # ask for changes: round 2 of the same document
    j = owner.post(f"/api/cexa/missions/{j['id']}/revise", json={"text": "Add who owns each item"}).json()
    assert j["status"] == "review" and j["round"] == 2 and "Changed as asked" in j["document"]


def test_a_standing_team_runs_and_hands_a_finding_on(owner, db):
    j = owner.post("/api/cexa/teams/security/run").json()
    assert j["status"] == "done" and j["kind"] == "report" and j["document"].startswith("## Security")
    assert [t["kind"] for t in j["tasks"]] == ["gather", "analyse", "check", "write", "tsummary"] and all(t["status"] == "done" for t in j["tasks"])
    assert j["findings"], "the owner has no two-step in the suite, so Security has something to say"
    # one run at a time
    # the owner hands a finding to the Advice team
    r = owner.post(f"/api/cexa/missions/{j['id']}/findings/0/handoff", json={"to": "advice"})
    assert r.status_code == 200 and r.json()["job"]
    assert db.get(Mission, r.json()["job"]).team_key == "advice"
    assert r.json()["mission"]["findings"][0]["handed"]["to"] == "advice"


def test_fix_is_quiet_when_nothing_broke(owner, db):
    t = T.team(db, "fix")
    # mark everything so far as seen
    from cbx.models import AuditLog

    last = db.query(AuditLog.id).order_by(AuditLog.id.desc()).first()
    from cbx.db import utcnow

    t.state = {"last_error_id": last[0] if last else 0, "last_check": utcnow().isoformat()}
    db.commit()
    assert not T.has_new_errors(db, t)
    j = owner.post("/api/cexa/teams/fix/run").json()
    assert j["status"] == "done" and "all quiet" in j["summary"].lower() and j["calls"] == 0
    assert [t_["status"] for t_ in j["tasks"]].count("skipped") == 3


def test_the_schedule_starts_a_team_once_per_slot(owner, db, monkeypatch):
    from datetime import datetime, timezone

    monkeypatch.setattr(settings, "teams_auto", True)
    t = T.team(db, "ops")
    # 1.0.52: the timer teams ship switched off — nothing runs until the owner switches one on
    assert t.enabled is False
    t.schedule = {"every": "day", "at": "06:30"}
    t.state = {}
    db.commit()
    assert T.tick(db, datetime(2026, 9, 29, 6, 33, tzinfo=timezone.utc)) == []
    t.enabled = True
    t.state = {}
    db.commit()
    now = datetime(2026, 9, 29, 6, 33, tzinfo=timezone.utc)
    started = T.tick(db, now)
    assert len(started) == 1 and db.get(Mission, started[0]).team_key == "ops"
    assert T.tick(db, now) == []  # the same slot is not run twice
    monkeypatch.setattr(settings, "teams_auto", False)
    assert T.tick(db, datetime(2026, 9, 30, 6, 33, tzinfo=timezone.utc)) == []


def test_the_council_reviews_a_tested_version_and_can_block(owner, db, monkeypatch):
    from cbx.cexa import team_stub

    monkeypatch.setattr(settings, "teams_auto", True)
    from cbx.config import ROOT

    v = AppVersion(tag="9.9.1", notes="test", status="sandbox", package_path=str(ROOT), sha256="x", tests={"passed": 3, "failed": 0, "ok": True})
    db.add(v)
    db.commit()
    m = T.version_tested(db, v)
    db.expire_all()
    m = db.get(Mission, m.id)
    assert m.status == "done" and m.kind == "review" and m.plan["verdict"] == "pass"
    assert [t.kind for t in db.query(MissionTask).filter(MissionTask.mission_id == m.id).order_by(MissionTask.order)] == ["gather", "lens", "lens", "lens", "debate", "debate", "debate", "verdict"]
    assert db.get(AppVersion, v.id).tests["council"]["verdict"] == "pass"
    # a high finding that nobody disputes blocks Promote until the owner overrides it on purpose
    monkeypatch.setattr(team_stub, "_team_lens", lambda d: {"verdict": "block", "findings": [{"file": "cbx/x.py", "line": 3, "severity": "high", "problem": "An endpoint without a permission check", "fix": "Add require_admin"}]})
    v2 = AppVersion(tag="9.9.2", notes="test", status="sandbox", package_path=str(ROOT), sha256="y", tests={"ok": True})
    db.add(v2)
    db.commit()
    m2 = T.version_tested(db, v2)
    db.expire_all()
    assert db.get(Mission, m2.id).plan["verdict"] == "block"
    r = owner.post(f"/api/versions/{v2.id}/promote?skip_test=1&minutes=10")
    assert r.status_code == 409 and r.json()["detail"].startswith("council:")
    assert T.council_verdict([{"findings": [{"id": "sec1", "severity": "high"}], "verdict": "block"}], [{"responses": [{"id": "sec1", "agree": False}, ], "verdict": "pass"}, {"responses": [{"id": "sec1", "agree": False}], "verdict": "pass"}])[0] == "pass"
    for x in (v, v2):
        db.delete(db.get(AppVersion, x.id))
    db.commit()


def test_a_change_job_works_on_a_copy_of_a_live_tool(owner, db):
    live_before = (loader.registry().tools["entity-compliance"].path / "pages").iterdir()
    before = {p.name: p.read_text(encoding="utf-8") for p in live_before}
    j = _job(owner, "Change Entity Compliance: add a line under the grid saying who to call about missing certificates.")
    assert j["team"]["key"] == "change" and j["kind"] == "change", j
    assert j["status"] == "plan" and j["plan"]["slug"] == "entity-compliance" and j["plan"]["changes"]
    j = owner.post(f"/api/cexa/missions/{j['id']}/go").json()
    assert j["status"] == "review", [(t["title"], t["status"], t["output"].get("problem")) for t in j["tasks"] if t["status"] != "done"]
    d = loader.read_folder(drafts_root() / "entity-compliance")
    assert d.ok, d.errors
    grid = [w for p in d.pages.values() for s in p["layout"]["sections"] for c in s["columns"] for w in c["widgets"] if w.get("type") == "grid"]
    assert grid, "the grid (a block the team may not write) was kept"
    after = {p.name: p.read_text(encoding="utf-8") for p in (loader.registry().tools["entity-compliance"].path / "pages").iterdir()}
    assert before == after  # the live tool is untouched


def test_a_board_viewer_sees_the_teams_and_changes_nothing(owner, user, db):
    uid = next(u["id"] for u in owner.get("/api/users").json()["users"] if u["email"] == "thandi@test.local")
    assert owner.patch(f"/api/users/{uid}", json={"role": "viewer"}).status_code == 200
    assert user.get("/api/cexa/teams").status_code == 200
    assert user.get("/api/cexa/missions").json()["can_act"] is False
    assert user.post("/api/cexa/missions", json={"brief": PM}).status_code == 403
    assert user.post("/api/cexa/teams/ops/run").status_code == 403
    assert user.patch("/api/cexa/teams/ops", json={"enabled": False}).status_code == 403
    me = user.get("/api/auth/me").json()
    assert me["user"]["can_see_teams"] is True
    owner.patch(f"/api/users/{uid}", json={"role": "user"})


def test_the_owner_shapes_a_team(owner, db):
    t = owner.get("/api/cexa/teams/data").json()
    t = owner.post("/api/cexa/teams/data/members", json={"name": "Neat", "role": "reviewer", "focus": "second opinion", "model": "qwen3.8:27b"}).json()
    new = next(m for m in t["members"] if m["name"] == "Neat")
    assert new["model"] == "qwen3.8:27b"
    t = owner.patch(f"/api/cexa/teams/data/members/{new['id']}", json={"name": "Neat", "role": "reviewer", "lead": True}).json()
    assert t["lead"]["name"] == "Neat"
    old_lead = next(m for m in t["members"] if m["name"] == "Clea")
    assert owner.delete(f"/api/cexa/teams/data/members/{new['id']}").status_code == 400  # the lead goes last
    owner.patch(f"/api/cexa/teams/data/members/{old_lead['id']}", json={"name": "Clea", "role": "lead", "lead": True})
    assert owner.delete(f"/api/cexa/teams/data/members/{new['id']}").status_code == 200
    t = owner.patch("/api/cexa/teams/data", json={"schedule": {"every": "week", "day": "fri", "at": "21:00"}, "enabled": False}).json()
    assert t["when"] == "Fridays at 21:00" and t["enabled"] is False
    assert owner.patch("/api/cexa/teams/data", json={"schedule": {"every": "fortnight"}}).status_code == 400
    owner.patch("/api/cexa/teams/data", json={"schedule": {"every": "week", "day": "sun", "at": "20:00"}, "enabled": True})
