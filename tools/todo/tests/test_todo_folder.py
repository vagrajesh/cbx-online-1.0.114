"""To-do as a tool folder (1.0.55): made only of Builder parts — tables, pages, blocks and rules.

My to-do (own list, Done button), Team board (shared team rows, Take it), Jobs (members only, the member's
team lead says yes), Stats (this week / this month), Goals (progress, a reward day once reached, once).
"""
import json
import uuid

from fastapi.testclient import TestClient

from cbx.engine import loader

BASE = "/api/engine/tools/todo/data"


def _person(owner, email, name, role="user", team="Utilities billing"):
    from cbx.main import app
    from tests.conftest import _make, _uid, login

    tools = [t["id"] for t in owner.get("/api/users").json()["tools"] if not t["admin_only"]]
    pw = _make(owner, email, name, role, tools=tools, team=team)
    c = TestClient(app)
    login(c, email, pw)
    return c, _uid(owner, email)


def _rows(c, page, widget):
    r = c.get(f"{BASE}/rows?page={page}&widget={widget}")
    assert r.status_code == 200, r.text
    return r.json()


def _run(c, flow, block, row, values=None, **kw):
    return c.post(f"/api/flows/todo/{flow}/run", json={"values": values or {}, "block": block, "row": row, **kw})


def test_folder_is_sound():
    t = loader.registry().tools.get("todo")
    assert t is not None and t.ok, (t.errors if t else "not loaded")
    assert set(t.pages) == {"mine", "team", "jobs", "stats", "goals"} and t.manifest["home"] == "mine"
    assert set(t.data) == {"tasks", "jobs", "job_members", "job_tasks", "goals", "reward_days"}
    for lang in ("en", "ar", "pt", "fr"):
        assert t.languages.get(lang), lang
    assert not any(t.missing().values()), t.missing()
    # every rule opens in the Builder as a plain rule (none read-only)
    from cbx.engine.rules import why_not

    assert {n: why_not(f) for n, f in t.flows.items()} == {n: None for n in t.flows}


def test_my_list_done_button_and_stats(owner, db):
    tag = uuid.uuid4().hex[:6]
    me, uid = _person(owner, f"ana{tag}@test.local", f"Ana {tag}")
    week0 = _rows(me, "stats", "w_week")["summary"]["count"]
    # a new row starts as the table says: Mine, To do, Who = the person adding it
    r = me.post(f"{BASE}/tasks", json={"values": {"title": f"Pay the bill {tag}", "steps": json.dumps(["read", "pay"])}}).json()
    assert r["kind"] == "Mine" and r["status"] == "To do" and r["who"] == uid and r["size"] == "Small"
    assert any(x["id"] == r["id"] for x in _rows(me, "mine", "w_mylist")["rows"])
    run = _run(me, "done", "tasks", r["id"]).json()["run"]
    assert run["status"] == "done", run
    got = me.get(f"{BASE}/tasks").json()["rows"]
    row = next(x for x in got if x["id"] == r["id"])
    assert row["status"] == "Done" and row["finished"] and row["done_by"] == uid
    assert not any(x["id"] == r["id"] for x in _rows(me, "mine", "w_mylist")["rows"])  # done: off the list
    assert _rows(me, "stats", "w_week")["summary"]["count"] == week0 + 1


def test_personal_stays_personal_team_rows_are_shared_and_taken(owner, db):
    tag = uuid.uuid4().hex[:6]
    a, aid = _person(owner, f"ben{tag}@test.local", f"Ben {tag}")
    b, bid = _person(owner, f"cleo{tag}@test.local", f"Cleo {tag}")
    far, _ = _person(owner, f"dan{tag}@test.local", f"Dan {tag}", team=f"Elsewhere {tag}")
    mine = a.post(f"{BASE}/tasks", json={"values": {"title": f"Ben private {tag}"}}).json()
    team = a.post(f"{BASE}/tasks", json={"values": {"title": f"Team job {tag}", "kind": "Team", "who": ""}}).json()
    assert team["who"] is None  # emptied on the form stays empty: open for taking
    seen_b = {x["title"] for x in b.get(f"{BASE}/tasks").json()["rows"]}
    assert f"Team job {tag}" in seen_b and f"Ben private {tag}" not in seen_b
    assert f"Team job {tag}" not in {x["title"] for x in far.get(f"{BASE}/tasks").json()["rows"]}
    assert any(x["id"] == team["id"] for x in _rows(b, "team", "w_open")["rows"])
    # Cleo takes it; after that nobody else can
    assert _run(b, "take", "tasks", team["id"]).json()["run"]["status"] == "done"
    got = next(x for x in b.get(f"{BASE}/tasks").json()["rows"] if x["id"] == team["id"])
    assert got["who"] == bid and got["status"] == "Doing"
    _run(a, "take", "tasks", team["id"])
    assert next(x for x in b.get(f"{BASE}/tasks").json()["rows"] if x["id"] == team["id"])["who"] == bid
    # someone from another team cannot hand a row they do not see to a rule, nor start it without a row
    assert _run(far, "take", "tasks", team["id"]).status_code == 404
    assert _run(far, "take", "", None).status_code == 400
    # moved to Done on the board: the finish date is noted; moved back: cleared
    assert b.patch(f"{BASE}/tasks/{team['id']}", json={"values": {"status": "Done"}}).status_code == 200
    got = next(x for x in b.get(f"{BASE}/tasks").json()["rows"] if x["id"] == team["id"])
    assert got["finished"] and got["done_by"] == bid
    b.patch(f"{BASE}/tasks/{team['id']}", json={"values": {"status": "Doing"}})
    got = next(x for x in b.get(f"{BASE}/tasks").json()["rows"] if x["id"] == team["id"])
    assert got["finished"] is None and got["done_by"] is None
    assert mine["kind"] == "Mine"


def test_goals_a_reward_day_once_reached_and_only_once(owner, db):
    tag = uuid.uuid4().hex[:6]
    lead, _ = _person(owner, f"lee{tag}@test.local", f"Lee {tag}", role="manager", team=f"Goal team {tag}")
    me, uid = _person(owner, f"eve{tag}@test.local", f"Eve {tag}", team=f"Goal team {tag}")
    from cbx.engine.flows import local_today

    today = local_today().isoformat()
    # only a manager sets goals
    assert me.post(f"{BASE}/goals", json={"values": {"title": "x", "target": 1, "ends": today}}).status_code == 403
    g = lead.post(f"{BASE}/goals", json={"values": {"title": f"One task {tag}", "target": 1, "ends": today, "reward": "A day at home"}}).json()
    assert g["starts"] == today
    prog = next(x for x in _rows(me, "goals", "w_goals")["rows"] if x["id"] == g["id"])
    assert prog["_progress"] == {"count": 0, "target": 1.0, "reached": False}
    kw = {"page": "goals", "widget": "w_goals"}
    assert _run(me, "book_day", "goals", g["id"], {"answer": today}, **kw).status_code == 400
    assert _run(me, "book_day", "goals", g["id"], {"answer": today}).status_code == 400  # not from somewhere else either
    t = me.post(f"{BASE}/tasks", json={"values": {"title": f"Eve's {tag}"}}).json()
    _run(me, "done", "tasks", t["id"])
    prog = next(x for x in _rows(me, "goals", "w_goals")["rows"] if x["id"] == g["id"])
    assert prog["_progress"]["reached"] is True
    assert _run(me, "book_day", "goals", g["id"], {"answer": today}, **kw).json()["run"]["status"] == "done"
    days = me.get(f"{BASE}/reward_days").json()["rows"]
    assert any(d["goal"] == g["id"] and d["person"] == uid and d["day"] == today for d in days)
    again = _run(me, "book_day", "goals", g["id"], {"answer": today}, **kw).json()["run"]
    assert again["status"] == "failed" and "only one" in again["note"]
    # nobody books a day by hand
    assert me.post(f"{BASE}/reward_days", json={"values": {"goal": g["id"], "day": today}}).status_code == 403


def test_jobs_are_for_their_members_and_the_team_lead_says_yes(owner, db):
    tag = uuid.uuid4().hex[:6]
    lead, lid = _person(owner, f"joy{tag}@test.local", f"Joy {tag}", role="manager", team=f"Job team {tag}")
    other_lead, _ = _person(owner, f"kay{tag}@test.local", f"Kay {tag}", role="manager", team=f"Far team {tag}")
    helper, hid = _person(owner, f"lou{tag}@test.local", f"Lou {tag}", team=f"Far team {tag}")
    assert helper.post(f"{BASE}/jobs", json={"values": {"title": "no"}}).status_code == 403  # a lead starts a job
    job = lead.post(f"{BASE}/jobs", json={"values": {"title": f"Catch-up {tag}"}}).json()
    assert job["lead"] == lid and job["status"] == "Active"
    members = lead.get(f"{BASE}/job_members").json()["rows"]
    assert any(m["job"] == job["id"] and m["person"] == lid and m["status"] == "Active" for m in members)  # the lead is on it
    assert not any(j["id"] == job["id"] for j in helper.get(f"{BASE}/jobs").json()["rows"])
    m = lead.post(f"{BASE}/job_members", json={"values": {"job": job["id"], "person": hid}}).json()
    assert m["status"] == "Requested"
    assert "only one" in lead.post(f"{BASE}/job_members", json={"values": {"job": job["id"], "person": hid}}).json()["detail"]
    # Lou's own team lead is asked
    runs = other_lead.get("/api/flows/runs?status=waiting").json()["runs"]
    run = next(r for r in runs if r["tool"] == "todo" and r["flow"] == "member_added")
    assert other_lead.post(f"/api/flows/runs/{run['id']}/decide", json={"approve": True}).status_code == 200
    assert any(j["id"] == job["id"] for j in helper.get(f"{BASE}/jobs").json()["rows"])
    # someone from Joy's own team: she is their lead, so she is not asked herself — they are on at once
    _, mate = _person(owner, f"max{tag}@test.local", f"Max {tag}", team=f"Job team {tag}")
    lead.post(f"{BASE}/job_members", json={"values": {"job": job["id"], "person": mate}})
    assert any(x["person"] == mate and x["status"] == "Active" for x in lead.get(f"{BASE}/job_members").json()["rows"])
    jt = lead.post(f"{BASE}/job_tasks", json={"values": {"job": job["id"], "title": f"Sort the files {tag}", "who": hid}}).json()
    assert any(x["id"] == jt["id"] for x in helper.get(f"{BASE}/job_tasks").json()["rows"])


def test_a_team_goal_counts_the_team_tasks(owner, db):
    tag = uuid.uuid4().hex[:6]
    lead, _ = _person(owner, f"tia{tag}@test.local", f"Tia {tag}", role="manager", team=f"Team goal {tag}")
    a, _ = _person(owner, f"uma{tag}@test.local", f"Uma {tag}", team=f"Team goal {tag}")
    b, _ = _person(owner, f"vic{tag}@test.local", f"Vic {tag}", team=f"Team goal {tag}")
    from cbx.engine.flows import local_today

    today = local_today().isoformat()
    g = lead.post(f"{BASE}/goals", json={"values": {"title": f"Two together {tag}", "target": 2, "ends": today, "counts": "The team"}}).json()
    assert not any(x["id"] == g["id"] for x in _rows(a, "goals", "w_goals")["rows"])  # not an each-person goal
    for c in (a, b):
        t = c.post(f"{BASE}/tasks", json={"values": {"title": f"Shared {tag}", "kind": "Team"}}).json()
        _run(c, "done", "tasks", t["id"])
    prog = next(x for x in _rows(a, "goals", "w_teamgoals")["rows"] if x["id"] == g["id"])
    assert prog["_progress"]["count"] == 2 and prog["_progress"]["reached"] is True
    assert _run(a, "book_day", "goals", g["id"], {"answer": today}, page="goals", widget="w_teamgoals").json()["run"]["status"] == "done"
