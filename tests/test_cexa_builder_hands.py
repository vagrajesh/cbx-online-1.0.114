"""1.0.61: C-EXA works in the tools built in the Builder — finds and changes rows, gives tasks (as the
person, with their access), and changes a tool (filters, columns, rules) only in its Builder draft."""
import json
import shutil

import pytest

from cbx.cexa import tools as T
from cbx.models import User
from tests.conftest import _make


@pytest.fixture()
def people(owner, db):
    ids = [t["id"] for t in owner.get("/api/users").json()["tools"] if not t["admin_only"] and t["slug"] != "support-desk"]
    _make(owner, "thandi@test.local", "Thandi Mokoena", "user", tools=ids)
    _make(owner, "mpho@test.local", "Mpho Manager", "manager", tools=ids)
    db.query(User).filter(User.email.in_(["thandi@test.local", "mpho@test.local"])).update({"must_change_password": False}, synchronize_session=False)
    db.commit()
    ow = db.query(User).filter(User.email == "owner@test.local").one()
    th = db.query(User).filter(User.email == "thandi@test.local").one()
    mg = db.query(User).filter(User.email == "mpho@test.local").one()
    return ow, th, mg


@pytest.fixture()
def no_draft():
    from cbx.api.builder_api import drafts_root

    shutil.rmtree(drafts_root() / "todo", ignore_errors=True)
    yield
    shutil.rmtree(drafts_root() / "todo", ignore_errors=True)


def test_she_sees_the_builder_tools_and_their_tables(people, db):
    ow, _, _ = people
    assert "[todo]" in T.run(db, ow, "list_tools", {}, None)
    d = T.run(db, ow, "describe_tool", {"tool": "To-do"}, None)
    assert "TABLE tasks" in d and "Status [status, choice (To do | Doing | Done)]" in d and "RULE" in d and "filters: status" in d


def test_she_gives_tasks_and_changes_them_as_the_person(people, db):
    ow, th, mg = people
    out = T.run(db, ow, "give_task", {"to": "me", "title": "Write the board note", "steps": "draft, review, send", "due": "2026-10-09"}, None)
    assert out.startswith("Added to tasks") and "Write the board note" in out and "0/3 steps" in out
    mine = T.run(db, ow, "my_tasks", {}, None)
    assert "Write the board note" in mine
    out = T.run(db, ow, "update_row", {"tool": "todo", "table": "tasks", "row": "Write the board note", "values": "Status: Doing; Due: 2026-10-12"}, None)
    assert out.startswith("Changed") and "Doing" in out and "2026-10-12" in out
    # a task for someone in the team lands on their list
    out = T.run(db, ow, "give_task", {"to": "Thandi", "title": "Check the Telkom bill run"}, None)
    assert out.startswith("Added to tasks") or "team" in out.lower(), out
    if out.startswith("Added"):
        assert "Check the Telkom bill run" in T.run(db, th, "my_tasks", {}, None)
    # what she can't read, she says
    assert "Status takes one of" in T.run(db, ow, "update_row", {"tool": "todo", "table": "tasks", "row": "Write the board note", "values": "Status: Finished-ish"}, None)
    assert "can't find an active person" in T.run(db, ow, "give_task", {"to": "Nobody Here", "title": "x"}, None)


def test_a_manager_gets_row_hands_but_not_the_rest(people, db):
    _, _, mg = people
    assert T.run(db, mg, "give_task", {"to": "me", "title": "Plan the week"}, None).startswith("Added")
    assert "Only admins" in T.run(db, mg, "add_filter", {"tool": "todo", "block": "Team board", "column": "status"}, None)


def test_the_chat_does_it(people, db, owner, client):
    from fastapi.testclient import TestClient

    from cbx.main import app
    from tests.conftest import login

    r = owner.post("/api/cexa/ask", json={"question": "add a task: Call Eskom about the meter"}).json()
    assert r["actions"] and r["actions"][0]["tool"] == "give_task" and "Call Eskom about the meter" in r["answer"]
    ids = [t["id"] for t in owner.get("/api/users").json()["tools"] if not t["admin_only"] and t["slug"] != "support-desk"]
    pw = _make(owner, "mpho@test.local", "Mpho Manager", "manager", tools=ids)
    mc = TestClient(app)
    login(mc, "mpho@test.local", pw)
    r = mc.post("/api/cexa/ask", json={"question": "what are my tasks"}).json()
    assert r["actions"] and r["actions"][0]["tool"] == "my_tasks"


def test_tool_changes_go_to_the_draft_never_live(people, db, no_draft):
    from cbx.api.builder_api import drafts_root
    from cbx.engine import loader

    ow, _, _ = people
    live_before = json.dumps(loader.get("todo").pages, sort_keys=True)
    out = T.run(db, ow, "add_filter", {"tool": "To-do", "block": "Team board", "column": "Who"}, None)
    assert "Saved in the Builder draft" in out and "not live yet" in out, out
    d = loader.read_folder(drafts_root() / "todo")
    assert not d.errors and any("who" in (w.get("filters") or []) for p in d.pages.values() for w in loader.widgets_of("x", p))
    assert json.dumps(loader.get("todo").pages, sort_keys=True) == live_before
    assert "a filter works on a pick-list" in T.run(db, ow, "add_filter", {"tool": "todo", "block": "Team board", "column": "Title"}, None)
    out = T.run(db, ow, "add_column", {"tool": "todo", "table": "tasks", "label": "Priority", "type": "pick-list", "options": "Low, Normal, High"}, None)
    assert "Added the column 'Priority'" in out
    d = loader.read_folder(drafts_root() / "todo")
    assert any(c["key"] == "priority" and c["options"] == ["Low", "Normal", "High"] for c in d.data["tasks"]["columns"]) and d.text("col.priority") == "Priority"
    out = T.run(db, ow, "switch_rule", {"tool": "todo", "rule": "Take it", "on": "off"}, None)
    assert "switched off" in out
    assert loader.read_folder(drafts_root() / "todo").flows["take"]["enabled"] is False
    assert loader.get("todo").flows["take"]["enabled"] is True  # live untouched


def test_the_old_todo_tools_are_gone_and_the_new_ones_are_listed_first():
    assert not {"add_todo", "list_my_todo", "todo_team_summary", "list_jobs", "job_summary"} & set(T.TOOLS)
    assert T.catalogue().startswith("- list_tools")
