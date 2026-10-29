# Named to run after test_pdfstudio.py: a batch read here teaches PDF Studio's shared lexicon, which that file's scores assume untaught.
"""1.0.57: PDF Studio as a server job — a tool made in the Builder reads a PDF with a PDF Studio template;
the batch stays in PDF Studio (where answering its questions teaches the profile), the values come into the row."""
from pathlib import Path

import pytest

from cbx.config import ROOT
from cbx.engine import actions as A
from cbx.engine import loader
from tests.fixtures.make_bills import make_bills
from tests.test_builder_types import _tool
from tests.test_pdfstudio import COLUMNS


@pytest.fixture(scope="module")
def bills(tmp_path_factory):
    return make_bills(tmp_path_factory.mktemp("bills56"))


@pytest.fixture()
def reader(tmp_path, monkeypatch, user):
    tpl = user.post("/api/pdfstudio/templates", json={"name": "Bills for the Builder", "columns": COLUMNS}).json()
    T = "{{trigger.row.id}}"
    data = {"bills": {"columns": [{"key": "pdf", "type": "file", "label": "col.pdf", "accept": ["pdf"]}, {"key": "account_no", "type": "text", "label": "col.account_no"},
                                  {"key": "total", "type": "number", "label": "col.total", "format": "money"}, {"key": "to_check", "type": "checklist", "label": "col.to_check"},
                                  {"key": "batch", "type": "int", "label": "col.batch"}, {"key": "excel", "type": "file", "label": "col.excel", "accept": ["xlsx"]},
                                  {"key": "state", "type": "text", "label": "col.state"}]}}
    out = {"account_no": "account_no", "total": "total", "to_check": "to_check", "batch": "batch", "excel": "excel", "state": "state"}
    flows = {"read": {"title": "flow.read.title", "enabled": True, "trigger": {"type": "row_changed", "data": "bills"}, "run_as": "starter", "start": "if1", "steps": {
        "s1": {"type": "action", "action": "pdfstudio.read", "data": "bills", "row": T, "in": {"pdf": "pdf", "template": str(tpl["id"])}, "out": out},
        "if1": {"type": "if", "when": [{"left": "{{trigger.row.pdf}}", "op": "not_empty"}, {"left": "{{trigger.row.batch}}", "op": "empty"}], "yes": "s1"}}},
        "fetch": {"title": "flow.fetch.title", "enabled": True, "trigger": {"type": "button", "data": "bills"}, "run_as": "starter", "start": "s1", "steps": {
            "s1": {"type": "action", "action": "pdfstudio.fetch", "data": "bills", "row": T, "in": {"batch": "batch", "template": str(tpl["id"])}, "out": out}}}}
    pages = {"tasks": {"title": "page.tasks.title", "layout": {"sections": [{"id": "s1", "columns": [{"width": 12, "widgets": [
        {"id": "t", "type": "table", "source": {"data": "bills"}, "actions": [{"label": "w.f", "flow": "fetch"}]}]}]}]}}}
    _tool(tmp_path, "billsread", pages=pages, data=data, flows=flows, en={k: k for k in ("col.pdf", "col.account_no", "col.total", "col.to_check", "col.batch", "col.excel", "col.state", "flow.read.title", "flow.fetch.title", "w.f")})
    monkeypatch.setattr(loader, "TOOLS_ROOT", tmp_path)
    reg = loader.reload(tmp_path)
    assert "billsread" in reg.tools, reg.broken["billsread"].errors
    yield tpl
    loader.reload(ROOT / "tools")


def test_the_builder_lists_pdf_studio_templates_and_their_columns(reader, user, db):
    from cbx.models import User

    me = db.query(User).filter(User.email == "thandi@test.local").one()
    job = next(a for a in A.catalogue(db, me) if a["name"] == "pdfstudio.read")
    assert [str(reader["id"]), "Bills for the Builder"] in job["inputs"][1]["choices"]
    assert {o["key"] for o in job["outputs_by"][str(reader["id"])]} >= {"account_no", "total", "kwh"}


def test_a_pdf_on_a_row_is_read_by_pdf_studio(reader, user, bills):
    base = "/api/engine/tools/billsread/data/bills"
    b = next(x for x in bills if x["name"].startswith("emfuleni"))
    r = user.post(base, json={"values": {}}).json()
    up = user.post(f"{base}/{r['id']}/files/pdf", files=[("files", (b["name"], Path(b["file"]).read_bytes(), "application/pdf"))]).json()
    assert up.get("_working"), up  # read in the background; the page is told which run to watch
    row = next(x for x in user.get(base).json()["rows"] if x["id"] == r["id"])
    assert row["batch"] and row["account_no"] == b["gold"]["account_no"] and abs(row["total"] - b["gold"]["total"]) < 0.01, row
    assert row["excel"]["name"].endswith(".xlsx") and row["state"]
    # the batch is a real PDF Studio batch of this person
    assert user.get(f"/api/pdfstudio/batches/{row['batch']}").status_code == 200
    run = user.post("/api/flows/billsread/fetch/run", json={"values": {}, "block": "bills", "row": r["id"]}).json()["run"]
    assert run["status"] == "done", run


def test_someone_without_pdf_studio_cannot_run_it(reader, owner, db):
    from cbx.models import User
    from tests.conftest import _make

    _make(owner, "nopdf@test.local", "No PDF", "user", tools=[])
    me = db.query(User).filter(User.email == "nopdf@test.local").one()
    assert not any(a["name"] == "pdfstudio.read" for a in A.catalogue(db, me))
    assert "does not have the tool" in (A.may_run(db, me, A.ACTIONS["pdfstudio.read"]) or "")
