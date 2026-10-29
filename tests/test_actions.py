"""1.0.56: server jobs in rules (fast and slow), "stop unless …", Word documents from a template, money."""
import json

import pytest

from cbx.config import ROOT
from cbx.engine import actions as A
from cbx.engine import loader
from tests.test_builder_types import _tool


@pytest.fixture()
def jobs_tool(tmp_path, monkeypatch):
    A.load()
    calls = []

    @A.action("test.double", "Double a number", group="Tests", description="x2", inputs=[A.Param("n", "The number", "number")],
              outputs=[A.Param("twice", "Twice", "number"), A.Param("note", "A note"), A.Param("todo", "To check", "checklist")])
    def _double(job):
        calls.append(("fast", job.user.email))
        return {"twice": float(job.inputs["n"]) * 2, "note": "doubled", "todo": ["look at it"]}

    @A.action("test.slow", "A slow one", group="Tests", description="slow", slow=True, inputs=[A.Param("n", "The number", "number")],
              outputs=[A.Param("twice", "Twice", "number"), A.Param("paper", "A paper", "file")])
    def _slow(job):
        calls.append(("slow", job.user.email))
        return {"twice": float(job.inputs["n"]) * 2, "paper": ("paper.txt", b"hello")}

    @A.action("test.admins", "Admins only", group="Tests", description="", roles=("owner", "admin"))
    def _adm(job):
        return {}

    data = {"items": {"columns": [{"key": "n", "type": "number", "label": "col.n", "format": "money"}, {"key": "twice", "type": "number", "label": "col.twice"},
                                  {"key": "note", "type": "text", "label": "col.note"}, {"key": "todo", "type": "checklist", "label": "col.todo"},
                                  {"key": "paper", "type": "file", "label": "col.paper", "accept": ["txt"]}]}}
    T = "{{trigger.row.id}}"
    flows = {
        "fast": {"title": "flow.fast.title", "enabled": True, "trigger": {"type": "button", "data": "items"}, "run_as": "starter", "start": "s1", "steps": {
            "s1": {"type": "require", "when": [{"left": "{{trigger.row.n}}", "op": "gt", "value": "0"}], "message": "Give it a number above nought first.", "next": "s2"},
            "s2": {"type": "action", "action": "test.double", "data": "items", "row": T, "in": {"n": "n"}, "out": {"twice": "twice", "note": "note", "todo": "todo"}, "as": "job", "next": "s3"},
            "s3": {"type": "set", "name": "seen", "value": "{{job.twice}}"}}},
        "slow": {"title": "flow.slow.title", "enabled": True, "trigger": {"type": "button", "data": "items"}, "run_as": "starter", "start": "s1", "steps": {
            "s1": {"type": "action", "action": "test.slow", "data": "items", "row": T, "in": {"n": "n"}, "out": {"twice": "twice", "paper": "paper"}, "next": "s2"},
            "s2": {"type": "update_row", "data": "items", "row": T, "values": {"note": "slow done"}}}},
        "adm": {"title": "flow.adm.title", "enabled": True, "trigger": {"type": "button"}, "run_as": "starter", "start": "s1", "steps": {
            "s1": {"type": "action", "action": "test.admins"}}},
    }
    pages = {"tasks": {"title": "page.tasks.title", "layout": {"sections": [{"id": "s1", "columns": [{"width": 12, "widgets": [
        {"id": "t", "type": "table", "source": {"data": "items"}, "actions": [{"label": "w.f", "flow": "fast"}, {"label": "w.s", "flow": "slow"}]},
        {"id": "b", "type": "button", "flow": "adm", "label": "w.a"}]}]}]}}}
    _tool(tmp_path, "jobs", pages=pages, data=data, flows=flows, en={"col.n": "N", "col.twice": "Twice", "col.note": "Note", "col.todo": "To check", "col.paper": "Paper",
                                                                     "flow.fast.title": "Fast", "flow.slow.title": "Slow", "flow.adm.title": "Adm", "w.f": "F", "w.s": "S", "w.a": "A"})
    monkeypatch.setattr(loader, "TOOLS_ROOT", tmp_path)
    reg = loader.reload(tmp_path)
    assert "jobs" in reg.tools, reg.broken["jobs"].errors
    yield calls
    for k in ("test.double", "test.slow", "test.admins"):
        A.ACTIONS.pop(k, None)
    loader.reload(ROOT / "tools")


def test_a_fast_job_writes_the_row_and_stop_unless_says_why(jobs_tool, owner):
    base = "/api/engine/tools/jobs/data/items"
    zero = owner.post(base, json={"values": {"n": 0}}).json()
    run = owner.post("/api/flows/jobs/fast/run", json={"values": {}, "block": "items", "row": zero["id"]}).json()["run"]
    assert run["status"] == "failed" and run["note"] == "Give it a number above nought first." and not jobs_tool
    r = owner.post(base, json={"values": {"n": 21}}).json()
    run = owner.post("/api/flows/jobs/fast/run", json={"values": {}, "block": "items", "row": r["id"]}).json()["run"]
    assert run["status"] == "done", run
    row = next(x for x in owner.get(base).json()["rows"] if x["id"] == r["id"])
    assert row["twice"] == 42 and row["note"] == "doubled" and row["todo"] == [{"text": "look at it", "done": False}]
    assert jobs_tool == [("fast", "owner@test.local")]


def test_a_slow_job_runs_in_the_background_and_the_rule_carries_on(jobs_tool, owner, db):
    base = "/api/engine/tools/jobs/data/items"
    r = owner.post(base, json={"values": {"n": 5}}).json()
    run = owner.post("/api/flows/jobs/slow/run", json={"values": {}, "block": "items", "row": r["id"]}).json()["run"]
    assert run["status"] == "waiting" and run["waiting"]["kind"] == "working"
    # the job ran once the waiting run was saved (jobs run in line in the suite)
    after = owner.get(f"/api/flows/runs/{run['id']}").json()["run"]
    assert after["status"] == "done", after
    row = next(x for x in owner.get(base).json()["rows"] if x["id"] == r["id"])
    assert row["twice"] == 10 and row["note"] == "slow done" and row["paper"]["name"] == "paper.txt"
    assert owner.get(f"{base}/{r['id']}/files/paper").content == b"hello"


def test_a_job_is_only_for_those_it_is_for(jobs_tool, owner, user):
    run = user.post("/api/flows/jobs/adm/run", json={}).json()["run"]
    assert run["status"] == "failed" and "is for owner, admin only" in run["note"]
    assert owner.post("/api/flows/jobs/adm/run", json={}).json()["run"]["status"] == "done"


def test_jobs_and_documents_are_checked_in_plain_words(tmp_path):
    A.load()
    data = {"items": {"columns": [{"key": "title", "type": "text"}, {"key": "f", "type": "file"}, {"key": "x", "type": "text", "format": "money"}]}}
    flows = {"bad": {"title": "flow.bad.title", "trigger": {"type": "button", "data": "items"}, "start": "a", "steps": {
        "a": {"type": "action", "action": "swiftnet.read_quote", "data": "items", "row": "{{trigger.row.id}}", "in": {"quote": "title"}, "out": {"site": "nope", "checks": "title"}, "next": "b"},
        "b": {"type": "make_doc", "template": "missing.docx", "data": "items", "row": "{{trigger.row.id}}", "into": "title", "next": "c"},
        "c": {"type": "action", "action": "no.such.job", "next": "d"},
        "d": {"type": "require", "when": []}}}}
    _tool(tmp_path, "bad56", data=data, flows=flows, en={"flow.bad.title": "Bad"})
    errs = " | ".join(loader.load_all(tmp_path).broken["bad56"].errors)
    for needle in ("comes from a file column of the row", "'nope' is not a column", "goes into a checklist column", "assets/missing.docx is not in the folder",
                   "which file column of items gets the document", "'no.such.job' is not one this server knows", "say what must hold", "a number can be shown as money"):
        assert needle in errs, needle


def test_word_values_and_money():
    from cbx.engine import worddoc

    assert worddoc.money(1234567.5) == "R 1 234 567.50"
    assert worddoc.shown({"key": "a", "type": "number", "format": "money"}, 4850, {}) == "R 4 850.00"
    assert worddoc.shown({"key": "d", "type": "date"}, "2026-03-12", {}) == "12 March 2026"
    assert worddoc.shown({"key": "p", "type": "person"}, 3, {"_labels": {"p": "Thandi"}}) == "Thandi"
    assert worddoc.shown({"key": "c", "type": "checklist"}, [{"text": "a"}, {"text": "b"}], {}) == "a; b"


def test_the_builder_translates_missing_words(owner, monkeypatch):
    from cbx.cexa import gateway

    monkeypatch.setattr(gateway.Gateway, "json", lambda self, **kw: {"words": {"page.a.title": "Página A", "nope": "x"}})
    r = owner.post("/api/builder/translate", json={"lang": "pt", "words": {"page.a.title": "Page A", "w.b": "B"}}).json()
    assert r["words"] == {"page.a.title": "Página A"} and r["asked"] == 2
    assert owner.post("/api/builder/translate", json={"lang": "de", "words": {"a": "b"}}).status_code == 400
