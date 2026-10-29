"""The org chart API, tested for the two things that would make the page a liability:

* an answer that quietly grows into the whole company, and
* a picture that shows a manager more of the organisation than they are allowed to have.

Everything here goes through HTTP, because that is how the canvas will ask.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from cbx.main import app
from cbx.models import Team, User
from cbx.org import access as A
from tests.conftest import login


@pytest.fixture()
def chart(owner, db, request):
    """ceo → mgr → lead → staff → junior → intern, with one person on another branch.

    `mgr` is made through the API because they have to sign in; everybody else only has to exist.
    Names, emails and the team slug carry a per-test tag — the db fixture commits, so anything
    shared would collide with the next test."""
    tag = f"{abs(hash(request.node.name)) % 100000}"
    team = Team(name=f"Org API team {tag}", slug=f"org-api-team-{tag}")
    db.add(team)
    db.commit()
    r = owner.post("/api/users", json={"email": f"mgr{tag}@orgapi.test", "name": f"Mandla Manager {tag}", "role": "manager", "team_id": team.id})
    assert r.status_code == 200, r.text
    pw = r.json()["temp_password"]
    people = {"mgr": db.query(User).filter(User.email == f"mgr{tag}@orgapi.test").one()}
    for key, name in [("ceo", "Cleo Chief"), ("lead", "Lerato Lead"), ("staff", "Sipho Staff"), ("junior", "Jabu Junior"), ("intern", "Ingrid Intern"), ("outsider", "Pieter Peer")]:
        u = User(email=f"{key}{tag}@orgapi.test", name=f"{name} {tag}", role="user", team_id=team.id, password_hash="x")
        db.add(u)
        people[key] = u
    db.flush()
    for who, boss in [("mgr", "ceo"), ("lead", "mgr"), ("staff", "lead"), ("junior", "staff"), ("intern", "junior"), ("outsider", "ceo")]:
        A.set_reports_to(db, people[who], people[boss].id)
    db.commit()  # every request below reads through its own session
    c = TestClient(app)
    login(c, f"mgr{tag}@orgapi.test", pw)
    return SimpleNamespace(tag=tag, team=team, mgr_client=c, **people)


def _ids(nodes) -> set[int]:
    return {n["id"] for n in nodes}


def test_the_graph_never_reaches_deeper_than_it_was_asked(chart, owner):
    g = owner.get(f"/api/org/graph?root={chart.ceo.id}&depth=1").json()
    assert g["root_id"] == chart.ceo.id
    assert chart.mgr.id in _ids(g["nodes"])
    assert chart.lead.id not in _ids(g["nodes"]) and chart.staff.id not in _ids(g["nodes"])
    # the node that was cut off still says how many people are under it, so the canvas can offer more
    assert next(n for n in g["nodes"] if n["id"] == chart.mgr.id)["direct_reports"] == 1
    # and "give me everything" comes back capped, not as the whole company
    deep = _ids(owner.get(f"/api/org/graph?root={chart.ceo.id}&depth=99").json()["nodes"])
    assert chart.junior.id in deep  # four levels down
    assert chart.intern.id not in deep  # five is past the cap


def test_the_root_brings_its_line_of_managers_for_the_breadcrumb(chart, owner):
    g = owner.get(f"/api/org/graph?root={chart.staff.id}&depth=1").json()
    assert [n["id"] for n in g["nodes"]][:4] == [chart.ceo.id, chart.mgr.id, chart.lead.id, chart.staff.id]
    assert {"from": chart.lead.id, "to": chart.staff.id} in g["edges"]
    assert {"from": chart.staff.id, "to": chart.junior.id} in g["edges"]
    assert chart.outsider.id not in _ids(g["nodes"])  # another branch is not part of this answer
    assert [k["key"] for k in g["kinds"]] and all(set(k) == {"key", "label", "sensitive", "hint"} for k in g["kinds"])


def test_a_manager_only_gets_their_own_part_of_the_chart(chart, owner):
    c = chart.mgr_client
    assert c.get("/api/org/graph?root=me").status_code == 200
    assert c.get(f"/api/org/graph?root={chart.staff.id}").status_code == 200
    r = c.get(f"/api/org/graph?root={chart.ceo.id}")
    assert r.status_code == 403 and "your own part of the chart" in r.json()["detail"]
    assert c.get(f"/api/org/people/{chart.outsider.id}").status_code == 403
    mine = _ids(c.get(f"/api/org/search?q={chart.tag}").json()["people"])
    assert chart.staff.id in mine and chart.intern.id in mine
    assert chart.ceo.id not in mine and chart.outsider.id not in mine
    # an admin searching the same word gets the people the manager may not see
    assert chart.ceo.id in _ids(owner.get(f"/api/org/search?q={chart.tag}").json()["people"])
    # the breadcrumb hands them their managers' names, not the wires drawn above their heads
    g = owner.post("/api/org/grants", json={"owner_id": chart.outsider.id, "viewer_id": chart.ceo.id, "kind": "folders"}).json()["grant"]
    theirs = c.get("/api/org/graph?root=me&depth=1").json()
    assert chart.ceo.id in _ids(theirs["nodes"]) and g["id"] not in {x["id"] for x in theirs["grants"]}
    assert g["id"] in {x["id"] for x in owner.get(f"/api/org/graph?root={chart.ceo.id}&depth=1").json()["grants"]}


def test_a_manager_may_not_change_the_shape_of_the_chart(chart):
    c = chart.mgr_client
    assert c.patch(f"/api/org/people/{chart.staff.id}", json={"title": "Billing clerk"}).status_code == 403
    assert c.put("/api/org/layout", json={"positions": []}).status_code == 403
    assert c.post("/api/org/teams", json={"name": f"Manager's own team {chart.tag}"}).status_code == 403


def test_a_user_has_no_business_on_this_page(chart, user):
    assert user.get("/api/org/graph").status_code == 403
    assert user.get("/api/org/roots").status_code == 403


def test_roots_are_somewhere_the_person_is_allowed_to_start(chart, owner):
    assert chart.ceo.id in _ids(owner.get("/api/org/roots").json()["roots"])
    assert chart.staff.id not in _ids(owner.get("/api/org/roots").json()["roots"])
    assert [n["id"] for n in chart.mgr_client.get("/api/org/roots").json()["roots"]] == [chart.mgr.id]


def test_a_person_page_carries_what_the_side_panel_draws(chart, owner):
    d = owner.get(f"/api/org/people/{chart.lead.id}").json()
    assert d["person"]["id"] == chart.lead.id and d["person"]["initials"]
    assert d["manager"] == {"id": chart.mgr.id, "name": chart.mgr.name}
    assert [n["id"] for n in d["reports"]] == [chart.staff.id]
    assert d["grants_out"] == [] and d["grants_in"] == [] and d["can_edit"] is True
    assert d["tools"] and all(set(t) == {"id", "slug", "name", "granted"} for t in d["tools"])


def test_a_cycle_comes_back_as_a_sentence_not_a_stack_trace(chart, owner):
    r = owner.patch(f"/api/org/people/{chart.ceo.id}", json={"reports_to_id": chart.staff.id})
    assert r.status_code == 400
    assert r.json()["detail"] == "That would make someone their own manager — pick someone outside their own reporting line."
    assert owner.get(f"/api/org/people/{chart.ceo.id}").json()["person"]["reports_to_id"] is None  # nothing moved


def test_a_person_can_be_moved_and_the_chart_follows(chart, owner):
    r = owner.patch(f"/api/org/people/{chart.staff.id}", json={"reports_to_id": chart.mgr.id, "title": "Billing clerk"})
    assert r.status_code == 200, r.text
    assert r.json()["person"]["reports_to_id"] == chart.mgr.id and r.json()["person"]["title"] == "Billing clerk"
    assert chart.staff.id in _ids(owner.get(f"/api/org/graph?root={chart.mgr.id}&depth=1").json()["nodes"])


def test_a_role_change_still_goes_through_the_users_and_roles_rule(chart, admin):
    r = admin.patch(f"/api/org/people/{chart.staff.id}", json={"role": "owner"})
    assert r.status_code == 403 and "owner" in r.json()["detail"]
    assert admin.patch(f"/api/org/people/{chart.staff.id}", json={"role": "manager"}).status_code == 200


def test_a_wire_can_be_drawn_and_taken_away_and_explain_keeps_up(chart, owner):
    ask = f"/api/org/explain?viewer_id={chart.outsider.id}&owner_id={chart.staff.id}&kind=todo"
    before = owner.get(ask).json()
    assert before["allowed"] is False and before["why"].startswith("No.")
    wire = {"owner_id": chart.staff.id, "viewer_id": chart.outsider.id, "kind": "todo", "note": "covering while he is on leave"}
    r = owner.post("/api/org/grants", json=wire)
    assert r.status_code == 200, r.text
    g = r.json()["grant"]
    assert g["kind"] == "todo" and g["granted_by"] and g["at"] and g["note"] == "covering while he is on leave"
    after = owner.get(ask).json()
    assert after["allowed"] is True and "wire was drawn" in after["why"]
    assert g["id"] in {x["id"] for x in owner.get(f"/api/org/graph?root={chart.staff.id}&depth=0").json()["grants"]}
    dup = owner.post("/api/org/grants", json=wire)
    assert dup.status_code == 400 and "already" in dup.json()["detail"]
    d = owner.delete(f"/api/org/grants/{g['id']}")
    assert d.status_code == 200 and d.json() == {"ok": True, "deleted": g["id"]}
    assert owner.get(ask).json()["allowed"] is False
    # both ends of that are in the audit trail, in words, like every other change of access
    said = {r["detail"] for r in owner.get("/api/audit?action=org_grant_added").json()["rows"]}
    assert any("To-do" in s and "covering while he is on leave" in s for s in said)
    assert owner.get("/api/audit?action=org_grant_removed").json()["total"] >= 1


def test_a_wire_nobody_could_act_on_is_refused(chart, owner):
    bad = owner.post("/api/org/grants", json={"owner_id": chart.staff.id, "viewer_id": chart.lead.id, "kind": "salary"})
    assert bad.status_code == 400 and "isn't something that can be shared" in bad.json()["detail"]
    same = owner.post("/api/org/grants", json={"owner_id": chart.staff.id, "viewer_id": chart.staff.id, "kind": "folders"})
    assert same.status_code == 400 and "themselves" in same.json()["detail"]
    assert owner.get(f"/api/org/explain?viewer_id={chart.lead.id}&owner_id={chart.staff.id}&kind=salary").status_code == 400


def test_a_manager_is_the_sub_admin_of_their_own_team_and_no_further(chart):
    c = chart.mgr_client
    ok = c.post("/api/org/grants", json={"owner_id": chart.staff.id, "viewer_id": chart.lead.id, "kind": "todo", "note": "while Sipho is away"})
    assert ok.status_code == 200, ok.text
    for outside in ({"owner_id": chart.outsider.id, "viewer_id": chart.lead.id}, {"owner_id": chart.staff.id, "viewer_id": chart.ceo.id}):
        r = c.post("/api/org/grants", json={**outside, "kind": "folders"})
        assert r.status_code == 403 and "own part of the chart" in r.json()["detail"]
    assert ok.json()["grant"]["id"] in {x["id"] for x in c.get("/api/org/graph?root=me&depth=3").json()["grants"]}
    assert c.delete(f"/api/org/grants/{ok.json()['grant']['id']}").status_code == 200


def test_a_manager_cannot_pull_a_wire_that_is_not_theirs(chart, owner):
    g = owner.post("/api/org/grants", json={"owner_id": chart.outsider.id, "viewer_id": chart.ceo.id, "kind": "folders"}).json()["grant"]
    assert chart.mgr_client.delete(f"/api/org/grants/{g['id']}").status_code == 403
    assert owner.delete(f"/api/org/grants/{g['id']}").status_code == 200


def test_the_layout_is_saved_and_comes_back_on_the_next_graph_call(chart, owner):
    r = owner.put("/api/org/layout", json={"positions": [{"user_id": chart.mgr.id, "x": 120.5, "y": -40.0, "collapsed": True}, {"user_id": 99_000_000, "x": 1, "y": 1}]})
    assert r.status_code == 200 and r.json() == {"ok": True, "saved": 1}  # the node for a person who has gone is dropped, not fatal

    def node():
        return next(n for n in owner.get(f"/api/org/graph?root={chart.mgr.id}&depth=0").json()["nodes"] if n["id"] == chart.mgr.id)

    assert (node()["x"], node()["y"], node()["collapsed"]) == (120.5, -40.0, True)
    owner.put("/api/org/layout", json={"positions": [{"user_id": chart.mgr.id, "x": 5.0, "y": 6.0}]})
    assert (node()["x"], node()["y"], node()["collapsed"]) == (5.0, 6.0, False)  # moved, not duplicated


def test_a_team_cannot_be_put_inside_itself(chart, owner):
    a = owner.post("/api/org/teams", json={"name": f"Alpha {chart.tag}", "lead_id": chart.mgr.id}).json()["team"]
    b = owner.post("/api/org/teams", json={"name": f"Beta {chart.tag}", "parent_id": a["id"]}).json()["team"]
    assert b["parent_id"] == a["id"] and a["lead_id"] == chart.mgr.id
    r = owner.patch(f"/api/org/teams/{a['id']}", json={"parent_id": b["id"]})
    assert r.status_code == 400 and "inside itself" in r.json()["detail"]
    assert owner.patch(f"/api/org/teams/{a['id']}", json={"parent_id": a["id"]}).status_code == 400
    teams = {t["id"]: t for t in owner.get("/api/org/teams").json()["teams"]}
    assert teams[chart.team.id]["member_count"] == 7
    assert owner.post("/api/org/teams", json={"name": f"Alpha {chart.tag}"}).status_code == 400  # same team twice


def test_a_manager_cannot_wire_someone_elses_private_work_to_themselves(owner, db):
    """"Both ends inside my subtree" includes me. Without this rule any team lead could hand
    themselves a report's To-do, which is the one thing the reporting line refuses on purpose."""
    from fastapi.testclient import TestClient

    from cbx.main import app
    from cbx.models import User
    from cbx.org import access as A
    from tests.conftest import _make, login

    pw = _make(owner, "selfwire.lead@test.local", "Selfwire Lead", "manager")
    _make(owner, "selfwire.staff@test.local", "Selfwire Staff", "user")
    lead = db.query(User).filter(User.email == "selfwire.lead@test.local").one()
    staff = db.query(User).filter(User.email == "selfwire.staff@test.local").one()
    A.set_reports_to(db, staff, lead.id)
    db.commit()

    c = TestClient(app)
    login(c, "selfwire.lead@test.local", pw)

    r = c.post("/api/org/grants", json={"owner_id": staff.id, "viewer_id": lead.id, "kind": "todo"})
    assert r.status_code == 403, r.text
    assert "wire to yourself" in r.json()["detail"]
    db.expire_all()
    assert not A.can_see(db, db.get(User, lead.id), staff.id, "todo")

    # the block is on the record, not silent
    assert any(e["action"] == "org_grant_blocked" for e in owner.get("/api/audit?action=org_grant_blocked").json()["rows"])

    # an owner can still do it — and it is logged as their decision
    assert owner.post("/api/org/grants", json={"owner_id": staff.id, "viewer_id": lead.id, "kind": "todo"}).status_code == 200
    db.expire_all()
    assert A.can_see(db, db.get(User, lead.id), staff.id, "todo")
