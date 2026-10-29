"""1.0.51: choice lists, links between tables, files on rows, and pages for some roles only."""
import json

import pytest

from cbx.config import ROOT, settings
from cbx.engine import files as FILES
from cbx.engine import loader

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def _tool(root, slug="pm", *, pages=None, data=None, flows=None, en=None):
    d = root / slug
    for sub in ("pages", "data", "languages", "flows"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    (d / "tool.json").write_text(json.dumps({"slug": slug, "title": "tool.title", "icon": "list", "owner": "owner@test.local", "menu": {"section": "My tools"}, "access": {"everyone": True}, "home": "tasks"}), encoding="utf-8")
    data = data if data is not None else {
        "projects": {"columns": [{"key": "name", "label": "col.name", "type": "text", "required": True}, {"key": "stage", "label": "col.stage", "type": "choice", "options": ["Idea", "Build", "Live"]}]},
        "tasks": {"columns": [{"key": "title", "label": "col.title", "type": "text"}, {"key": "project", "label": "col.project", "type": "link", "table": "projects"},
                              {"key": "status", "label": "col.status", "type": "choice", "options": ["Open", "In progress", "Done"]}, {"key": "evidence", "label": "col.evidence", "type": "file", "accept": ["pdf", "png"], "max_mb": 1}]},
        "notes": {"columns": [{"key": "text", "label": "col.text", "type": "text"}]},
    }
    tbl = lambda wid, block, **kw: {"title": f"page.{wid}.title", "layout": {"sections": [{"id": "s1", "columns": [{"width": 12, "widgets": [{"id": wid, "type": "table", "source": {"data": block}}] + kw.get("extra", [])}]}]}, **({"access": kw["access"]} if "access" in kw else {})}
    pages = pages if pages is not None else {
        "tasks": tbl("tasks", "tasks"), "projects": tbl("projects", "projects"),
        "gov": tbl("gov", "notes", access={"roles": ["owner", "admin"]}, extra=[{"id": "go", "type": "button", "flow": "tidy", "label": "w.go"}]),
    }
    flows = flows if flows is not None else {"tidy": {"title": "flow.tidy.title", "enabled": True, "trigger": {"type": "button"}, "start": "a", "steps": {"a": {"type": "stop", "outcome": "done", "text": "ok"}}}}
    for n, p in pages.items():
        (d / "pages" / f"{n}.json").write_text(json.dumps(p), encoding="utf-8")
    for n, b in data.items():
        (d / "data" / f"{n}.json").write_text(json.dumps(b), encoding="utf-8")
    for n, f in flows.items():
        (d / "flows" / f"{n}.json").write_text(json.dumps(f), encoding="utf-8")
    words = {"tool.title": "PM", "page.tasks.title": "Tasks", "page.projects.title": "Projects", "page.gov.title": "Governance", "w.go": "Tidy", "flow.tidy.title": "Tidy",
             "col.name": "Name", "col.stage": "Stage", "col.title": "Title", "col.project": "Project", "col.status": "Status", "col.evidence": "Evidence", "col.text": "Text", **(en or {})}
    (d / "languages" / "en.json").write_text(json.dumps(words), encoding="utf-8")
    (d / "README.md").write_text("# PM\n", encoding="utf-8")
    return d


@pytest.fixture()
def pm(tmp_path, monkeypatch):
    _tool(tmp_path)
    monkeypatch.setattr(loader, "TOOLS_ROOT", tmp_path)
    reg = loader.reload(tmp_path)
    assert "pm" in reg.tools, reg.broken.get("pm") and reg.broken["pm"].errors
    yield reg.tools["pm"]
    loader.reload(ROOT / "tools")  # the real folders (TOOLS_ROOT is still patched here)


def test_the_new_types_are_checked_in_plain_words(tmp_path):
    bad = {
        "a": {"columns": [{"key": "s", "type": "choice"}, {"key": "t", "type": "choice", "options": ["Open", "open"]}, {"key": "l", "type": "link", "table": "nowhere"},
                          {"key": "m", "type": "link", "table": "b", "show": "blob"}, {"key": "f", "type": "file", "accept": ["exe"]}, {"key": "g", "type": "file", "max_mb": 500},
                          {"key": "n", "type": "int", "options": ["1", "2"]}]},
        "b": {"columns": [{"key": "blob", "type": "json"}]},
    }
    pages = {"tasks": {"title": "page.tasks.title", "access": {"roles": ["boss"]}, "layout": {"sections": [{"id": "s1", "columns": [{"width": 12, "widgets": [{"id": "t", "type": "table", "source": {"data": "a"}}]}]}]}}}
    _tool(tmp_path, "bad", pages=pages, data=bad, flows={})
    errs = " | ".join(loader.load_all(tmp_path).broken["bad"].errors)
    for needle in ("choice column 's' needs its options", "has the same option twice", "points at a table this tool does not have ('nowhere')", "shows 'blob', which is not",
                   "file column 'f' accepts only", "max_mb is 1 to 100", "has options but is a int", "pages/tasks: access.roles must be a list"):
        assert needle in errs, needle


def test_choices_are_kept_to_their_options_and_filter_a_table(owner, pm):
    base = "/api/engine/tools/pm/data"
    assert owner.post(f"{base}/tasks", json={"values": {"title": "x", "status": "Maybe"}}).status_code == 400
    assert "must be one of: Open, In progress, Done" in owner.post(f"{base}/tasks", json={"values": {"title": "x", "status": "Maybe"}}).json()["detail"]
    r = owner.post(f"{base}/tasks", json={"values": {"title": "a", "status": "in progress"}}).json()
    assert r["status"] == "In progress"  # the option's own spelling is kept
    owner.post(f"{base}/tasks", json={"values": {"title": "b", "status": "Done"}})
    owner.post(f"{base}/tasks", json={"values": {"title": "c", "status": "Done"}})
    got = owner.get(f"{base}/rows?page=tasks&widget=tasks&status=Done").json()
    assert got["total"] == 2 and {x["title"] for x in got["rows"]} == {"b", "c"}
    assert owner.get(f"{base}/tasks?status=Open").json()["total"] == 0
    # a filter on a column that is not a choice, link or yes/no is ignored (not an injection point)
    assert owner.get(f"{base}/tasks?title=a").json()["total"] == 3


def test_links_point_at_real_rows_show_their_label_and_protect_them(owner, pm):
    base = "/api/engine/tools/pm/data"
    r = owner.post(f"{base}/tasks", json={"values": {"title": "orphan", "project": 99}})
    assert r.status_code == 400 and "there is no row 99 in projects" in r.json()["detail"]
    p1 = owner.post(f"{base}/projects", json={"values": {"name": "Website", "stage": "Build"}}).json()
    p2 = owner.post(f"{base}/projects", json={"values": {"name": "App"}}).json()
    t = owner.post(f"{base}/tasks", json={"values": {"title": "home page", "project": p1["id"], "status": "Open"}}).json()
    assert t["project"] == p1["id"]
    rows = owner.get(f"{base}/rows?page=tasks&widget=tasks").json()["rows"]
    assert rows[0]["_labels"]["project"] == "Website"
    ch = owner.get(f"{base}/tasks/choices/project").json()
    assert ch["show"] == "name" and [c["label"] for c in ch["choices"]] == ["App", "Website"]
    assert owner.get(f"{base}/tasks/choices/project?q=web").json()["choices"] == [{"id": p1["id"], "label": "Website"}]
    assert owner.get(f"{base}/tasks/choices/title").status_code == 404
    # filter by the link
    assert owner.get(f"{base}/rows?page=tasks&widget=tasks&project={p2['id']}").json()["total"] == 0
    # a row others point at is not deleted from under them
    d = owner.delete(f"{base}/projects/{p1['id']}")
    assert d.status_code == 409 and "1 in tasks" in d.json()["detail"]
    assert owner.delete(f"{base}/projects/{p2['id']}").status_code == 200
    owner.patch(f"{base}/tasks/{t['id']}", json={"values": {"project": ""}})
    assert owner.delete(f"{base}/projects/{p1['id']}").status_code == 200


def test_files_on_rows_are_checked_kept_apart_and_only_downloaded(owner, user, pm, db):
    base = "/api/engine/tools/pm/data"
    t = owner.post(f"{base}/tasks", json={"values": {"title": "with file"}}).json()
    up = f"{base}/tasks/{t['id']}/files/evidence"
    # a file column cannot be typed into
    assert "upload it on the row" in owner.patch(f"{base}/tasks/{t['id']}", json={"values": {"evidence": "x.pdf"}}).json()["detail"]
    bad_ext = owner.post(up, files={"files": ("run.exe", b"MZ....", "application/octet-stream")})
    assert bad_ext.status_code == 400 and "takes pdf, png" in bad_ext.json()["detail"]
    fake = owner.post(up, files={"files": ("report.pdf", b"<html><script>alert(1)</script></html>", "application/pdf")})
    assert fake.status_code == 400 and "not really a .pdf" in fake.json()["detail"]
    big = owner.post(up, files={"files": ("big.pdf", PDF + b"0" * (1024 * 1024 + 10), "application/pdf")})
    assert big.status_code == 400 and "over 1 MB" in big.json()["detail"]
    ok = owner.post(up, files={"files": ("../../Q3 report.pdf", PDF, "application/pdf")})
    assert ok.status_code == 200, ok.text
    meta = ok.json()["evidence"]
    assert meta["name"] == "Q3 report.pdf" and meta["size"] == len(PDF) and meta["type"] == "pdf"
    p = FILES.path_of("pm", "tasks", t["id"], meta)
    assert p is not None and p.read_bytes() == PDF and str(settings.data_dir) in str(p) and "report" not in p.name  # 1.0.112: not "Q3" — the random stored name can contain it by chance
    dl = owner.get(up)
    assert dl.status_code == 200 and dl.content == PDF
    assert dl.headers["content-disposition"].startswith("attachment;") and dl.headers["x-content-type-options"] == "nosniff" and "sandbox" in dl.headers["content-security-policy"]
    # someone else may read it (the table is for all) but not replace or remove it on another's row
    assert user.get(up).status_code == 200
    assert user.post(up, files={"files": ("x.pdf", PDF, "application/pdf")}).status_code == 403
    assert user.delete(up).status_code == 403
    # replacing keeps one file; deleting the row removes it
    owner.post(up, files={"files": ("second.pdf", PDF, "application/pdf")})
    assert len(list(FILES.row_dir("pm", "tasks", t["id"]).iterdir())) == 1
    assert owner.delete(f"{base}/tasks/{t['id']}").status_code == 200
    assert not FILES.row_dir("pm", "tasks", t["id"]).exists()
    from cbx.models import AuditLog

    db.expire_all()
    acts = {a.action for a in db.query(AuditLog).filter(AuditLog.target.like("pm/tasks/%")).all()}
    assert {"tool_file_added", "tool_file_refused", "tool_file_downloaded"} <= acts


def test_a_page_for_some_roles_is_hidden_and_closed_to_the_rest(owner, user, pm):
    menu = user.get("/api/engine/menu").json()["tools"]
    pages = [p["name"] for p in next(t for t in menu if t["slug"] == "pm")["pages"]]
    assert "gov" not in pages and "tasks" in pages
    assert "gov" in [p["name"] for p in next(t for t in owner.get("/api/engine/menu").json()["tools"] if t["slug"] == "pm")["pages"]]
    assert user.get("/api/engine/tools/pm/pages/gov").status_code == 403
    assert user.get("/api/engine/tools/pm/data/rows?page=gov&widget=gov").status_code == 403
    # the table behind it, straight — read or written — and its Run button, are closed too
    assert user.get("/api/engine/tools/pm/data/notes").status_code == 403
    assert user.post("/api/engine/tools/pm/data/notes", json={"values": {"text": "hi"}}).status_code == 403
    assert user.post("/api/flows/pm/tidy/run", json={"values": {}}).status_code == 403
    # the page they may open does not list the closed one as a tab
    tp = user.get("/api/engine/tools/pm/pages/tasks").json()
    assert "gov" not in [p["name"] for p in tp["tool"]["pages"]]
    assert owner.get("/api/engine/tools/pm/pages/gov").status_code == 200
    assert owner.post("/api/engine/tools/pm/data/notes", json={"values": {"text": "hi"}}).status_code == 200


def test_flows_are_held_to_the_choices(tmp_path):
    flows = {"f": {"title": "flow.tidy.title", "trigger": {"type": "button"}, "start": "a", "steps": {
        "a": {"type": "add_row", "data": "tasks", "values": {"title": "x", "status": "Finished", "evidence": "a.pdf"}, "next": "b"},
        "b": {"type": "find_rows", "data": "tasks", "where": [{"field": "status", "op": "equals", "value": "Closed"}]}}}}
    _tool(tmp_path, "fl", pages={"tasks": {"title": "page.tasks.title", "layout": {"sections": [{"id": "s1", "columns": [{"width": 12, "widgets": [{"id": "t", "type": "table", "source": {"data": "tasks"}}]}]}]}}}, flows=flows)
    errs = " | ".join(loader.load_all(tmp_path).broken["fl"].errors)
    assert "'Finished' is not one of the choices of 'status'" in errs and "'Closed' is not one of the choices" in errs and "a flow cannot put a file on a row" in errs


# ---- 1.0.53: the warehouse as a source, "only rows where", totals, charts ---------------------------------


def _sheet(db, slug="bills-test"):
    from cbx.models import MasterSheet

    s = db.query(MasterSheet).filter(MasterSheet.slug == slug).first()
    if s is None:
        cols = [{"key": "site", "label": "Site", "ctype": "text"}, {"key": "amount", "label": "Amount", "ctype": "money"}, {"key": "status", "label": "Status", "ctype": "text"}]
        rows = [{"_id": i + 1, "site": f"Site {i}", "amount": 100.0 * (i + 1), "status": "Unpaid" if i % 2 else "Paid"} for i in range(6)]
        s = MasterSheet(name="Bills test", slug=slug, columns=cols, rows=rows)
        db.add(s)
        db.commit()
    return s


def test_a_block_reads_a_warehouse_sheet_filtered_sorted_and_added_up(owner, user, db, tmp_path, monkeypatch):
    _sheet(db)
    pages = {"tasks": {"title": "page.tasks.title", "layout": {"sections": [{"id": "s1", "columns": [{"width": 12, "widgets": [
        {"id": "open", "type": "table", "source": {"sheet": "bills-test"}, "where": [{"field": "status", "op": "equals", "value": "unpaid"}], "sort": {"key": "amount", "desc": True}},
        {"id": "sum", "type": "table", "show": "totals", "totals": ["amount"], "source": {"sheet": "bills-test"}, "where": [{"field": "status", "op": "equals", "value": "Unpaid"}]},
        {"id": "bars", "type": "chart", "source": {"sheet": "bills-test"}, "group": "status", "value": {"fn": "sum", "of": "amount"}}]}]}]}}}
    _tool(tmp_path, "wh", pages=pages, data={}, flows={})
    monkeypatch.setattr(loader, "TOOLS_ROOT", tmp_path)
    reg = loader.reload(tmp_path)
    try:
        assert "wh" in reg.tools, reg.broken["wh"].errors
        p = owner.get("/api/engine/tools/wh/pages/tasks").json()
        assert [c["key"] for c in p["widgets"]["open"]["sheet"]["columns"]] == ["site", "amount", "status"]
        r = owner.get("/api/engine/tools/wh/data/rows?page=tasks&widget=open").json()
        assert [x["amount"] for x in r["rows"]] == [600.0, 400.0, 200.0] and r["total"] == 3
        s = owner.get("/api/engine/tools/wh/data/rows?page=tasks&widget=sum").json()
        assert s["summary"] == {"count": 3, "sums": {"amount": 1200.0}}
        c = owner.get("/api/engine/tools/wh/data/rows?page=tasks&widget=bars").json()
        assert c["summary"]["sums"]["amount"] == 2100.0 and len(c["rows"]) == 6
        # someone with no row rule on master sheets sees none of it (deny by default)
        assert user.get("/api/engine/tools/wh/data/rows?page=tasks&widget=open").json()["total"] == 0
    finally:
        loader.reload(ROOT / "tools")


def test_the_new_block_settings_are_checked(tmp_path):
    pages = {"tasks": {"title": "page.tasks.title", "layout": {"sections": [{"id": "s1", "columns": [{"width": 12, "widgets": [
        {"id": "a", "type": "chart", "source": {"sheet": "x"}},
        {"id": "b", "type": "table", "source": {"sheet": "x"}, "where": [{"field": "s", "op": "bigger"}], "show": "lots"},
        {"id": "c", "type": "form", "source": {"sheet": "x"}},
        {"id": "d", "type": "chart", "source": {"sheet": "x"}, "group": "s", "value": {"fn": "sum"}}]}]}]}}}
    _tool(tmp_path, "chk", pages=pages, data={}, flows={})
    errs = " | ".join(loader.load_all(tmp_path).broken["chk"].errors)
    for needle in ("a chart needs the column it groups by", "every condition needs a column and one of", "show is 'rows' or 'totals'", "a warehouse sheet is only read", "adds up a number column"):
        assert needle in errs, needle


def test_a_rule_goes_through_a_warehouse_sheet(owner, db, tmp_path, monkeypatch):
    _sheet(db)
    flows = {"remind": {"title": "flow.tidy.title", "enabled": True, "trigger": {"type": "button"}, "run_as": "starter", "start": "find", "steps": {
        "find": {"type": "find_rows", "sheet": "bills-test", "where": [{"field": "status", "op": "equals", "value": "Unpaid"}], "as": "rows", "next": "each"},
        "each": {"type": "for_each", "items": "rows", "as": "row", "body": "s1"},
        "s1": {"type": "notify", "who": {"kind": "starter"}, "title": "Unpaid: {{row.site}}", "text": "R{{row.amount}}"}}}}
    pages = {"tasks": {"title": "page.tasks.title", "layout": {"sections": [{"id": "s1", "columns": [{"width": 12, "widgets": [{"id": "go", "type": "button", "flow": "remind", "label": "w.go"}]}]}]}}}
    _tool(tmp_path, "whr", pages=pages, data={}, flows=flows)
    monkeypatch.setattr(loader, "TOOLS_ROOT", tmp_path)
    reg = loader.reload(tmp_path)
    try:
        assert "whr" in reg.tools, reg.broken["whr"].errors
        r = owner.post("/api/flows/whr/remind/run", json={"values": {}}).json()["run"]
        assert r["status"] == "done", r
        from cbx.models import CexaMessage

        db.expire_all()
        assert db.query(CexaMessage).filter(CexaMessage.title.like("Unpaid: Site %")).count() >= 3
    finally:
        loader.reload(ROOT / "tools")


def test_the_rule_shape_is_the_one_the_builder_can_open(tmp_path):
    from cbx.engine import rules as RULES

    ok = {"start": "find", "steps": {"find": {"type": "find_rows", "data": "t", "as": "rows", "next": "each"}, "each": {"type": "for_each", "items": "rows", "as": "row", "body": "a", "next": "z"},
                                     "a": {"type": "if", "when": [], "yes": "b"}, "b": {"type": "approval", "yes": "c"}, "c": {"type": "notify"}, "z": {"type": "notify"}}}
    assert RULES.why_not(ok) is None
    assert "no" in RULES.why_not({"start": "a", "steps": {"a": {"type": "if", "yes": "b", "no": "b"}, "b": {"type": "stop"}}})
    assert RULES.why_not({"start": "f", "steps": {"f": {"type": "find_rows", "data": "t", "as": "rows", "next": "i"}, "i": {"type": "if", "yes": "a"}, "a": {"type": "approval", "yes": "b", "no": "c"}, "b": {"type": "add_row"}, "c": {"type": "notify"}}}) is None
    assert "after an action" in RULES.why_not({"start": "a", "steps": {"a": {"type": "notify", "next": "b"}, "b": {"type": "if", "yes": "a"}}})
    flows = {"tidy": {"title": "flow.tidy.title", "trigger": {"type": "button"}, "start": "a", "steps": {"a": {"type": "if", "when": [{"left": "1", "op": "equals", "value": "1"}], "yes": "b", "no": "c"}, "b": {"type": "stop"}, "c": {"type": "stop"}}}}
    _tool(tmp_path, "shape", flows=flows)
    t = loader.load_all(tmp_path).tools["shape"]
    assert any("opens this rule read-only" in w for w in t.warnings)


def test_the_fix_team_sees_what_the_builder_says_is_broken(owner, db, tmp_path, monkeypatch):
    from cbx.cexa import teams as T

    _tool(tmp_path, "okay")
    bad = _tool(tmp_path, "brokenone")
    (bad / "data" / "tasks.json").write_text(json.dumps({"columns": [{"key": "status", "type": "choice"}]}), encoding="utf-8")
    monkeypatch.setattr(loader, "TOOLS_ROOT", tmp_path)
    loader.reload(tmp_path)
    try:
        found = T.broken_tools()
        assert any(b["tool"] == "brokenone" and "needs its options" in " ".join(b["problems"]) for b in found)
        assert not any(b["tool"] == "okay" for b in found)
    finally:
        loader.reload(ROOT / "tools")


# ---- 1.0.54: people, checklists, worked-out columns, team rows, boards, buttons on rows -------------------

TODO_DATA = {"tasks": {"scope": "team", "columns": [
    {"key": "title", "label": "col.title", "type": "text", "required": True},
    {"key": "assignee", "label": "col.assignee", "type": "person", "who": "team"},
    {"key": "status", "label": "col.status", "type": "choice", "options": ["Open", "Doing", "Done"]},
    {"key": "steps", "label": "col.steps", "type": "checklist"},
    {"key": "n", "label": "col.n", "type": "calc", "calc": {"fn": "count", "of": "steps"}},
    {"key": "size", "label": "col.size", "type": "calc", "calc": {"fn": "grade", "of": "n", "grades": [[3, "Small"], [7, "Medium"], [None, "Complex"]]}},
    {"key": "finished", "label": "col.finished", "type": "date"}]}}


def _todo(tmp_path):
    pages = {"tasks": {"title": "page.tasks.title", "layout": {"sections": [{"id": "s1", "columns": [{"width": 12, "widgets": [
        {"id": "mine", "type": "table", "source": {"data": "tasks"}, "where": [{"field": "assignee", "op": "equals", "value": "{{person.id}}"}], "actions": [{"label": "w.take", "flow": "take"}]},
        {"id": "board", "type": "board", "source": {"data": "tasks"}, "lane": "status", "card": "title"},
        {"id": "week", "type": "table", "show": "totals", "source": {"data": "tasks"}, "where": [{"field": "finished", "op": "this_week"}]}]}]}]}}}
    flows = {"take": {"title": "flow.take.title", "enabled": True, "trigger": {"type": "button", "data": "tasks"}, "run_as": "starter", "start": "s1", "steps": {
        "s1": {"type": "update_row", "data": "tasks", "row": "{{trigger.row.id}}", "values": {"assignee": "{{person.id}}", "status": "Doing"}}}}}
    _tool(tmp_path, "todo2", pages=pages, data=TODO_DATA, flows=flows, en={"w.take": "Take it", "flow.take.title": "Take it", "col.assignee": "Who", "col.steps": "Steps", "col.n": "Steps", "col.size": "Size", "col.finished": "Finished"})


def test_people_checklists_and_worked_out_columns(owner, user, db, tmp_path, monkeypatch):
    _todo(tmp_path)
    monkeypatch.setattr(loader, "TOOLS_ROOT", tmp_path)
    reg = loader.reload(tmp_path)
    try:
        assert "todo2" in reg.tools, reg.broken["todo2"].errors
        from cbx.models import User

        me = db.query(User).filter(User.email == "owner@test.local").one()
        base = "/api/engine/tools/todo2/data"
        r = owner.post(f"{base}/tasks", json={"values": {"title": "Write it", "status": "Open", "steps": json.dumps([{"text": "a"}, {"text": "b", "done": True}, {"text": "c"}, {"text": "d"}]), "assignee": me.id}}).json()
        assert r["n"] == 4 and r["size"] == "Medium" and r["steps"][1]["done"] is True
        assert "must be a person" in owner.post(f"{base}/tasks", json={"values": {"title": "x", "assignee": "bob"}}).json()["detail"]
        assert "not here" in owner.post(f"{base}/tasks", json={"values": {"title": "x", "assignee": 999999}}).json()["detail"]
        rows = owner.get(f"{base}/rows?page=tasks&widget=mine").json()["rows"]
        assert rows and rows[0]["_labels"]["assignee"] == me.name
        people = owner.get(f"{base}/tasks/people/assignee").json()
        assert any(p["id"] == me.id for p in people["choices"]) and people["me"] == me.id
        # a checklist ticked on: the worked-out columns follow
        r2 = owner.patch(f"{base}/tasks/{r['id']}", json={"values": {"steps": json.dumps([{"text": x} for x in "abcdefgh"])}}).json()
        assert r2["n"] == 8 and r2["size"] == "Complex"
        # period comparisons
        owner.patch(f"{base}/tasks/{r['id']}", json={"values": {"finished": __import__("cbx.engine.flows", fromlist=["local_today"]).local_today().isoformat()}})
        assert owner.get(f"{base}/rows?page=tasks&widget=week").json()["summary"]["count"] == 1
    finally:
        loader.reload(ROOT / "tools")


def test_team_rows_and_a_button_on_each_row(owner, user, db, tmp_path, monkeypatch):
    _todo(tmp_path)
    monkeypatch.setattr(loader, "TOOLS_ROOT", tmp_path)
    loader.reload(tmp_path)
    try:
        from cbx.models import Team, User

        base = "/api/engine/tools/todo2/data"
        o = db.query(User).filter(User.email == "owner@test.local").one()
        u = db.query(User).filter(User.email == "thandi@test.local").one()
        other = db.query(Team).filter(Team.name == "Somewhere else").first() or Team(name="Somewhere else", slug="somewhere-else")
        db.add(other); db.flush()
        o_team, u_team = o.team_id, u.team_id
        u.team_id = other.id
        db.commit()
        t1 = owner.post(f"{base}/tasks", json={"values": {"title": "Owner's", "status": "Open"}}).json()
        t2 = user.post(f"{base}/tasks", json={"values": {"title": "Thandi's", "status": "Open"}}).json()
        # a team table: Thandi (another team) sees her own team's rows only
        seen = [x["title"] for x in user.get(f"{base}/tasks").json()["rows"]]
        assert "Thandi's" in seen and "Owner's" not in seen
        assert user.patch(f"{base}/tasks/{t1['id']}", json={"values": {"title": "no"}}).status_code == 403
        # the button on her row: the rule gets the row, and takes it for her
        run = user.post("/api/flows/todo2/take/run", json={"values": {}, "block": "tasks", "row": t2["id"]}).json()["run"]
        assert run["status"] == "done", run
        got = user.get(f"{base}/tasks").json()["rows"][0]
        assert got["assignee"] == u.id and got["status"] == "Doing"
        # a row she may not see cannot be handed to a rule
        assert user.post("/api/flows/todo2/take/run", json={"values": {}, "block": "tasks", "row": t1["id"]}).status_code == 404
        u.team_id = u_team
        db.commit()
    finally:
        loader.reload(ROOT / "tools")


def test_boards_and_buttons_on_rows_are_checked(tmp_path):
    pages = {"tasks": {"title": "page.tasks.title", "layout": {"sections": [{"id": "s1", "columns": [{"width": 12, "widgets": [
        {"id": "b1", "type": "board", "source": {"data": "tasks"}, "lane": "title"},
        {"id": "b2", "type": "table", "source": {"data": "tasks"}, "actions": [{"label": "w.x", "flow": "nope"}]}]}]}]}}}
    data = {"tasks": {"scope": "company", "columns": [{"key": "title", "type": "text"}, {"key": "c", "type": "calc", "calc": {"fn": "count", "of": "title"}}, {"key": "p", "type": "person", "who": "nobody"}]}}
    _tool(tmp_path, "bad2", pages=pages, data=data, flows={}, en={"w.x": "X"})
    errs = " | ".join(loader.load_all(tmp_path).broken["bad2"].errors)
    for needle in ("a board's columns come from a pick-list column", "starts the rule 'nope'", "who sees the rows is all, mine", "counts a checklist column", "who is 'team'"):
        assert needle in errs, needle


# ---- 1.0.55: what To-do needed, made general — each checked in plain words ------------------------------

def test_the_1055_parts_are_checked(tmp_path):
    data = {
        "tasks": {"scope": "team", "share": {"field": "kind", "value": "Team"}, "write": "bosses", "unique": [["title", "nope"]], "columns": [
            {"key": "title", "type": "text"}, {"key": "kind", "type": "choice", "options": ["Mine", "Team"], "default": "Other"},
            {"key": "who", "type": "person", "default": "someone"}, {"key": "l", "type": "link", "table": "tasks", "default": 3}]},
        "jobs": {"scope": "members", "members": {"table": "tasks", "person": "title", "link": "l"}, "columns": [{"key": "title", "type": "text"}]},
    }
    pages = {"tasks": {"title": "page.tasks.title", "layout": {"sections": [{"id": "s1", "columns": [{"width": 12, "widgets": [
        {"id": "p1", "type": "progress", "source": {"data": "tasks"}, "count": {"where": [{"field": "title", "op": "nope"}]}},
        {"id": "t1", "type": "table", "source": {"data": "tasks"}, "preset": {"kind": "Nobody", "gone": "x"}, "actions": [{"label": "w.x", "flow": "go", "ask": [{"key": "a", "type": "colour"}]}]}]}]}]}}}
    flows = {"go": {"title": "flow.go.title", "trigger": {"type": "button", "data": "tasks"}, "start": "a", "steps": {"a": {"type": "stop", "outcome": "done"}}}}
    _tool(tmp_path, "bad55", pages=pages, data=data, flows=flows, en={"w.x": "X", "flow.go.title": "Go"})
    errs = " | ".join(loader.load_all(tmp_path).broken["bad55"].errors)
    for needle in ("the team sees the rows where", "who may add rows is everyone, managers, admins or nobody", "'only once' is a list of this table's columns",
                   "starts as 'Other', which is not one of its options", "can only start as 'me'", "column 'l' cannot have a starting value",
                   "shared with members needs the members table", "progress shows the rows of one of the tool's tables", "progress needs the column that holds each row's target",
                   "every condition of the count needs", "new rows start with kind 'Nobody'", "new rows start with 'gone'", "asks for a date, a month, a line of words or a pick from a list"):
        assert needle in errs, needle


def test_a_starting_value_only_when_the_row_says_nothing(tmp_path, monkeypatch):
    from cbx.engine import store as STORE

    monkeypatch.setattr(STORE, "path_for", lambda slug: tmp_path / f"{slug}.sqlite")
    cols = [{"key": "s", "type": "choice", "options": ["A", "B"], "default": "A"}, {"key": "who", "type": "person", "default": "me"}, {"key": "d", "type": "date", "default": "today"}]
    st = STORE.Store("defaults")
    r = st.insert("t", cols, {}, 7)
    assert r["s"] == "A" and r["who"] == 7 and r["d"]
    r2 = st.insert("t", cols, {"s": "B", "who": "", "d": ""}, 7)  # emptied on the form: stays empty
    assert r2["s"] == "B" and r2["who"] is None and r2["d"] is None


def test_empty_goal_values_do_not_narrow_and_dates_compare():
    from cbx.engine import flows as FL

    ctx = {"row": {"size": ""}}
    assert FL.holds({"field": "size", "op": "equals", "value": "{{row.size}}", "skip_empty": True}, ctx, {"size": "Small"})
    assert not FL.holds({"field": "size", "op": "equals", "value": "{{row.size}}"}, ctx, {"size": "Small"})
    ctx = {"row": {"starts": "2026-03-01", "ends": "2026-03-31"}}
    assert FL.holds({"field": "finished", "op": "gte", "value": "{{row.starts}}"}, ctx, {"finished": "2026-03-05"})
    assert not FL.holds({"field": "finished", "op": "lte", "value": "{{row.ends}}"}, ctx, {"finished": "2026-04-01"})
