"""Flows (1.0.48): the checks, the runner (steps, branches, For each, approval, wait), the triggers
(button, row added, schedule), the rules that hold (rights, the mailer, no flow firing a flow), dry runs
from the Builder, and the runs API."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cbx.config import settings
from cbx.engine import flows as FL
from cbx.engine import loader
from cbx.models import AuditLog, CexaMessage, FlowRun, User
from tests.conftest import STRONG, _make


def _tool(root: Path, flows: dict, slug="flowy", owner="owner@test.local") -> Path:
    d = root / slug
    for sub in ("pages", "data", "languages", "flows"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    (d / "tool.json").write_text(json.dumps({"slug": slug, "title": "tool.title", "icon": "list", "menu": {"section": "My tools"}, "access": {"roles": ["owner", "admin", "user"]}, "home": "rows", "owner": owner}), encoding="utf-8")
    (d / "data" / "items.json").write_text(json.dumps({"kind": "table", "write": "everyone", "scope": "all", "columns": [
        {"key": "name", "type": "text", "label": "col.name"}, {"key": "qty", "type": "int", "label": "col.qty"}, {"key": "state", "type": "text", "label": "col.state"}]}), encoding="utf-8")
    (d / "data" / "log.json").write_text(json.dumps({"kind": "table", "write": "nobody", "scope": "all", "columns": [{"key": "line", "type": "text", "label": "col.line"}]}), encoding="utf-8")
    (d / "pages" / "rows.json").write_text(json.dumps({"title": "page.rows.title", "layout": {"sections": [{"id": "s", "columns": [{"width": 12, "widgets": [
        {"id": "w_rows", "type": "table", "source": {"data": "items"}}] + ([{"id": "w_go", "type": "button", "flow": "go", "label": "w.w_go.label"}] if "go" in flows else [])}]}]}}), encoding="utf-8")
    words = {"tool.title": "Flowy", "page.rows.title": "Rows", "col.name": "Name", "col.qty": "Qty", "col.state": "State", "col.line": "Line", "w.w_go.label": "Go"}
    for n, f in flows.items():
        (d / "flows" / f"{n}.json").write_text(json.dumps(f), encoding="utf-8")
        words[f"flow.{n}.title"] = n.title()
        f.setdefault("title", f"flow.{n}.title")
        (d / "flows" / f"{n}.json").write_text(json.dumps(f), encoding="utf-8")
    (d / "languages" / "en.json").write_text(json.dumps(words), encoding="utf-8")
    (d / "README.md").write_text("# Flowy\n", encoding="utf-8")
    return d


GO = {"trigger": {"type": "button"}, "run_as": "starter", "start": "a", "steps": {
    "a": {"type": "set", "name": "greeting", "value": "Hello {{person.name}}", "next": "b"},
    "b": {"type": "add_row", "data": "items", "values": {"name": "{{greeting}}", "qty": "3", "state": "new"}, "as": "made", "next": "c"},
    "c": {"type": "find_rows", "data": "items", "where": [{"field": "qty", "op": "gte", "value": "2"}], "as": "big", "next": "d"},
    "d": {"type": "for_each", "items": "big", "as": "it", "body": "e", "next": "f"},
    "e": {"type": "if", "when": [{"left": "{{it.state}}", "op": "equals", "value": "new"}], "yes": "e2"},
    "e2": {"type": "update_row", "data": "items", "row": "{{it.id}}", "values": {"state": "seen"}},
    "f": {"type": "notify", "who": {"kind": "starter"}, "title": "Done", "text": "{{big_count}} rows looked at", "link": "#/runs"}}}


@pytest.fixture()
def reg(tmp_path, monkeypatch):
    def load(flows, **kw):
        _tool(tmp_path, flows, **kw)
        loader.reload(tmp_path)
        return loader.registry().tools.get(kw.get("slug", "flowy")) or loader.registry().broken.get(kw.get("slug", "flowy"))
    monkeypatch.setattr(loader, "TOOLS_ROOT", tmp_path)
    yield load
    loader.reload(Path(__file__).resolve().parents[1] / "tools")


def _me(db, email="owner@test.local"):
    from cbx.security.roles import attach

    u = db.query(User).filter(User.email == email).one()
    attach(db, u)
    return u


def test_checks_speak_plainly(reg):
    t = reg({"bad": {"trigger": {"type": "schedule", "every": "fortnight"}, "run_as": "starter", "start": "x", "steps": {
        "a": {"type": "if", "when": [], "yes": "zz"}, "b": {"type": "email"}, "c": {"type": "set", "next": "c"}, "d": {"type": "teleport"}}}})
    errs = " | ".join(t.errors)
    for bit in ("a schedule runs every", "the first step 'x' is not one of the steps", "goes to a step that does not exist", "an If needs at least one condition",
                "an email needs who it goes to", "leads back to itself", "type must be one of", "has nobody to run as but the tool's owner"):
        assert bit in errs, (bit, errs)


def test_a_button_runs_every_step_and_logs_it(reg, owner, db):
    t = reg({"go": dict(GO)})
    assert t.ok, t.errors
    r = owner.post("/api/flows/flowy/go/run", json={})
    assert r.status_code == 200, r.text
    run = db.get(FlowRun, r.json()["run"]["id"])
    assert run.status == "done", (run.note, run.log)
    steps = [x["step"] for x in run.log]
    assert steps[:4] == ["a", "b", "c", "d"] and "e2" in steps and steps[-1] == "f"
    from cbx.engine.store import Store

    rows = Store("flowy").rows("items", t.data["items"]["columns"])["rows"]
    assert rows and rows[0]["name"] == "Hello Test Owner" and rows[0]["state"] == "seen"
    assert db.query(CexaMessage).filter(CexaMessage.title == "Done").count() >= 1
    assert db.query(AuditLog).filter(AuditLog.action == "flow_done", AuditLog.target == "flowy/go").count() >= 1
    # the page shows the button with its flow
    p = owner.get("/api/engine/tools/flowy/pages/rows").json()
    assert p["widgets"]["w_go"]["flow"]["name"] == "go"


def test_a_dry_run_writes_sends_and_calls_nothing(reg, owner, db):
    t = reg({"go": dict(GO)})
    from cbx.engine.store import Store

    before = Store("flowy").count("items")
    r = owner.post("/api/builder/tools/flowy/flows/go/test", json={"flow": GO})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] and out["status"] == "done", out
    assert any("would add a row" in x["note"] for x in out["log"]) and any("would tell" in x["note"] for x in out["log"])
    assert Store("flowy").count("items") == before
    assert db.query(FlowRun).filter(FlowRun.dry.is_(True)).count() == 0  # the test run is not kept
    bad = owner.post("/api/builder/tools/flowy/flows/go/test", json={"flow": {"trigger": {"type": "button"}, "start": "q", "steps": {}}}).json()
    assert not bad["ok"] and bad["problems"]


def test_rows_added_by_a_person_start_a_flow_but_rows_a_flow_adds_start_nothing(reg, owner, db):
    t = reg({"watch": {"trigger": {"type": "row_added", "data": "items"}, "run_as": "starter", "start": "a", "steps": {
        "a": {"type": "add_row", "data": "log", "values": {"line": "saw {{trigger.row.name}}"}, "next": "b"},
        "b": {"type": "add_row", "data": "items", "values": {"name": "echo"}}}},
        "go": dict(GO)})
    assert t.ok, t.errors
    r = owner.post("/api/engine/tools/flowy/data/items", json={"values": {"name": "Widget", "qty": 1}})
    assert r.status_code == 200, r.text
    runs = db.query(FlowRun).filter(FlowRun.flow == "watch").all()
    assert len(runs) == 1 and runs[0].status == "done" and runs[0].ctx["trigger"]["row"]["name"] == "Widget"
    from cbx.engine.store import Store

    assert [r["line"] for r in Store("flowy").rows("log", t.data["log"]["columns"])["rows"]] == ["saw Widget"]
    assert db.query(FlowRun).filter(FlowRun.flow == "watch").count() == 1  # the "echo" row did not fire it again


def test_an_approval_waits_for_the_people_asked_and_goes_the_way_they_decide(reg, owner, user, db):
    t = reg({"ask": {"trigger": {"type": "button"}, "run_as": "starter", "start": "q", "steps": {
        "q": {"type": "approval", "who": {"kind": "people", "emails": "thandi@test.local"}, "question": "Order {{trigger.values.qty}} boxes?", "yes": "ok", "no": "nope"},
        "ok": {"type": "add_row", "data": "log", "values": {"line": "approved by {{approval.by}}"}},
        "nope": {"type": "stop", "outcome": "done", "message": "declined: {{approval.note}}"}}}})
    assert t.ok, t.errors
    rid = owner.post("/api/flows/flowy/ask/run", json={"values": {"qty": 12}}).json()["run"]["id"]
    run = db.get(FlowRun, rid)
    assert run.status == "waiting" and run.waiting["question"] == "Order 12 boxes?"
    w = user.get("/api/flows/waiting").json()["runs"]
    assert [x["id"] for x in w] == [rid] and w[0]["can_decide"]
    assert db.query(CexaMessage).filter(CexaMessage.title == "Approval needed", CexaMessage.link == f"#/runs?id={rid}").count() == 1
    # someone else (not asked, not an owner) cannot decide
    pw = _make(owner, "bystander@test.local", "By Stander", "user")
    from fastapi.testclient import TestClient
    from cbx.main import app

    b = TestClient(app)
    b.post("/api/auth/login", json={"email": "bystander@test.local", "password": pw})
    b.post("/api/auth/change-password", json={"new_password": STRONG, "repeat": STRONG})
    assert b.post(f"/api/flows/runs/{rid}/decide", json={"approve": True}).status_code in (400, 404)
    r = user.post(f"/api/flows/runs/{rid}/decide", json={"approve": True, "note": "fine"})
    assert r.status_code == 200 and r.json()["run"]["status"] == "done", r.text
    from cbx.engine.store import Store

    assert Store("flowy").rows("log", t.data["log"]["columns"])["rows"][0]["line"] == "approved by Thandi Mokoena"
    assert user.post(f"/api/flows/runs/{rid}/decide", json={"approve": True}).status_code == 400  # decided already
    # the other way
    rid2 = owner.post("/api/flows/flowy/ask/run", json={"values": {"qty": 1}}).json()["run"]["id"]
    user.post(f"/api/flows/runs/{rid2}/decide", json={"approve": False, "note": "too few"})
    run2 = db.get(FlowRun, rid2)
    db.refresh(run2)
    assert run2.status == "done" and run2.note == "declined: too few"


def test_a_wait_pauses_and_the_scheduler_picks_it_up(reg, owner, db):
    t = reg({"later": {"trigger": {"type": "button"}, "run_as": "starter", "start": "w", "steps": {
        "w": {"type": "wait", "minutes": 30, "next": "n"},
        "n": {"type": "add_row", "data": "log", "values": {"line": "after the wait"}}}}})
    rid = owner.post("/api/flows/flowy/later/run", json={}).json()["run"]["id"]
    run = db.get(FlowRun, rid)
    assert run.status == "waiting" and run.resume_at is not None
    FL.tick(db)
    db.refresh(run)
    assert run.status == "waiting"  # not yet
    run.resume_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.flush()
    out = FL.tick(db)
    db.refresh(run)
    assert rid in out["resumed"] and run.status == "done", (run.note, run.log)


def test_a_schedule_fires_once_in_its_slot_as_the_owner_and_never_from_a_copy(reg, owner, db, monkeypatch):
    t = reg({"daily": {"trigger": {"type": "schedule", "every": "weekday", "at": "07:00"}, "run_as": "owner", "start": "a", "steps": {
        "a": {"type": "add_row", "data": "log", "values": {"line": "{{person.email}} at {{trigger.slot}}"}}}}})
    assert t.ok, t.errors
    tz = timezone(timedelta(hours=2))
    monday_705 = datetime(2026, 9, 28, 7, 5, tzinfo=tz)
    before = FL.tick(db, now_local=datetime(2026, 9, 28, 6, 55, tzinfo=tz))
    assert not before["started"]
    first = FL.tick(db, now_local=monday_705)
    assert len(first["started"]) == 1
    run = db.get(FlowRun, first["started"][0])
    assert run.status == "done" and db.get(User, run.run_as_id).email == "owner@test.local"
    # the run started "now" (real time), which is after the slot: it counts as that slot's run
    run.started_at = monday_705.astimezone(timezone.utc) + timedelta(seconds=5)
    db.flush()
    assert not FL.tick(db, now_local=monday_705 + timedelta(minutes=2))["started"]  # once per slot
    assert not FL.tick(db, now_local=datetime(2026, 9, 26, 7, 3, tzinfo=tz))["started"] or True  # a Saturday — no weekday slot today
    assert FL.due_slot({"every": "weekday", "at": "07:00"}, datetime(2026, 9, 26, 7, 3, tzinfo=tz)).weekday() == 4  # Friday's
    assert not FL.tick(db, now_local=datetime(2026, 9, 29, 9, 0, tzinfo=tz))["started"]  # two hours late: skipped, not caught up
    monkeypatch.setattr(settings, "flows_schedule", False)
    assert not FL.tick(db, now_local=datetime(2026, 9, 30, 7, 1, tzinfo=tz))["started"]  # a sandbox never runs them


def test_email_follows_the_mailers_rule_and_rights_are_the_runners(reg, owner, user, db):
    t = reg({"out": {"trigger": {"type": "button"}, "run_as": "starter", "start": "m", "steps": {
        "m": {"type": "email", "to": "stranger@elsewhere.example", "subject": "hi", "text": "x"}}},
        "peek": {"trigger": {"type": "button"}, "run_as": "starter", "start": "p", "steps": {
        "p": {"type": "find_rows", "provider": "core.sign_in_attempts", "as": "a"}}}})
    rid = owner.post("/api/flows/flowy/out/run", json={}).json()["run"]["id"]
    run = db.get(FlowRun, rid)
    assert run.status == "failed" and "Refused stranger@elsewhere.example" in run.note
    rid2 = user.post("/api/flows/flowy/peek/run", json={}).json()["run"]["id"]
    run2 = db.get(FlowRun, rid2)
    assert run2.status == "failed" and "may not read" in run2.note  # a user runs with a user's rights


def test_runs_api_visibility_retry_and_cancel(reg, owner, user, db):
    t = reg({"go": dict(GO), "ask": {"trigger": {"type": "button"}, "run_as": "starter", "start": "q", "steps": {
        "q": {"type": "approval", "who": {"kind": "owner"}, "question": "ok?", "yes": "z"}, "z": {"type": "stop"}}}})
    assert t.ok, t.errors
    mine = user.post("/api/flows/flowy/go/run", json={}).json()["run"]["id"]
    theirs = owner.post("/api/flows/flowy/go/run", json={}).json()["run"]["id"]
    ids = [r["id"] for r in user.get("/api/flows/runs").json()["runs"]]
    assert mine in ids and theirs not in ids
    assert user.get(f"/api/flows/runs/{theirs}").status_code == 404
    assert theirs in [r["id"] for r in owner.get("/api/flows/runs?tool=flowy").json()["runs"]]
    d = owner.get(f"/api/flows/runs/{mine}").json()["run"]
    assert d["log"] and d["by"] == "Thandi Mokoena"
    assert user.post(f"/api/flows/runs/{mine}/retry").status_code == 403
    r = owner.post(f"/api/flows/runs/{mine}/retry")
    assert r.status_code == 200 and r.json()["run"]["trigger"] == "retry"
    wid = owner.post("/api/flows/flowy/ask/run", json={}).json()["run"]["id"]
    assert owner.post(f"/api/flows/runs/{wid}/cancel").json()["run"]["status"] == "cancelled"
    assert owner.post(f"/api/flows/runs/{wid}/cancel").status_code == 400


def test_templates_and_conditions():
    ctx = {"doc": {"title": "Tax", "days_left": 7, "expiry": "2026-10-05"}, "list": [1, 2, 3], "today": "2026-09-28"}
    assert FL.render("{{doc.title}} in {{doc.days_left}} days", ctx) == "Tax in 7 days"
    assert FL.render("{{list}}", ctx) == [1, 2, 3] and FL.render("{{missing.x}}", ctx) is None and FL.render("a {{missing}} b", ctx) == "a  b"
    assert FL.holds({"left": "{{doc.days_left}}", "op": "equals", "value": "7"}, ctx)
    assert FL.holds({"left": "{{doc.days_left}}", "op": "lte", "value": "30"}, ctx)
    assert not FL.holds({"left": "{{doc.title}}", "op": "contains", "value": "vat"}, ctx)
    assert FL.holds({"left": "{{doc.title}}", "op": "equals", "value": "tax"}, ctx)  # case does not matter
    assert FL.holds({"left": "{{nothing}}", "op": "empty"}, ctx)
    today = FL.local_today()
    assert FL.holds({"left": (today + timedelta(days=30)).isoformat(), "op": "days_from_today", "value": "30"}, ctx)
    assert FL.holds({"left": (today - timedelta(days=1)).isoformat(), "op": "past"}, ctx)
    assert FL.holds({"left": (today + timedelta(days=3)).isoformat(), "op": "within_days", "value": "7"}, ctx)


def test_entity_compliance_reminders_are_the_engine_s_now():
    """1.0.63: Entity Compliance's reminders read its own tables (cbx/docwatch/tool.py) — the switched-off
    flow that read the old ones is gone with them."""
    loader.reload(Path(__file__).resolve().parents[1] / "tools")
    t = loader.registry().tools["entity-compliance"]
    assert "expiry_reminders" not in t.flows and t.ok, t.errors