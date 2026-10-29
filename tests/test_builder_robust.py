"""1.0.87: one malformed draft took the whole Builder down ("Something went wrong on our side"). A table had
been saved as {"columns": {"province": {...}}} — C-EXA meant to add one pick-list column and replaced the whole
table. Now: the Builder always opens and shows the problem, a table of the wrong shape is never saved, and a
table change from C-EXA is laid over the table instead of replacing it."""
import json

import pytest

from cbx.cexa import tools as T
from cbx.config import settings
from cbx.engine import loader
from cbx.models import User

from .test_teams_and_building import FOLDER


@pytest.fixture()
def drafts(owner, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    yield tmp_path


def _me(db):
    return db.query(User).filter(User.email == "owner@test.local").one()


def test_a_broken_draft_never_takes_the_builder_down(drafts, owner, db):
    me = _me(db)
    assert "Saved" in T.run(db, me, "build_tool", {"tool": "site-visits", "folder": json.dumps(FOLDER)}, None)
    db.commit()  # never hold the write lock while the app writes
    bad = drafts / "builder" / "tools" / "site-visits" / "data" / "visits.json"
    bad.write_text(json.dumps({"columns": {"province": {"type": "pick-list", "options": "Gauteng, Western Cape"}}}), encoding="utf-8")
    r = owner.get("/api/builder")
    assert r.status_code == 200, r.text
    t = next(x for x in r.json()["tools"] if x["slug"] == "site-visits")
    assert any("columns must be a list" in e for e in t["draft_errors"])
    r = owner.get("/api/builder/tools/site-visits")
    assert r.status_code == 200 and any("columns must be a list" in e for e in r.json()["errors"])
    # and it can be thrown away
    assert owner.delete("/api/builder/tools/site-visits/draft").status_code == 200


def test_a_table_of_the_wrong_shape_is_not_saved(drafts, owner, db):
    body = {"manifest": FOLDER["manifest"], "pages": FOLDER["pages"], "languages": FOLDER["languages"], "readme": "",
            "data": {"visits": {"columns": {"province": {"type": "choice"}}}}}
    r = owner.put("/api/builder/tools/site-visits", json=body)
    assert r.status_code == 400 and "Nothing was saved" in r.text


def test_c_exa_adds_one_column_without_losing_the_table(drafts, owner, db):
    me = _me(db)
    assert "Saved" in T.run(db, me, "build_tool", {"tool": "site-visits", "folder": json.dumps(FOLDER)}, None)
    out = T.run(db, me, "change_tool_part", {"tool": "site-visits", "part": "table", "name": "visits",
                                             "value": json.dumps({"columns": {"province": {"type": "pick-list", "options": "Gauteng, Western Cape, KwaZulu-Natal"}}})}, None)
    assert "Saved" in out, out
    got = json.loads(T.run(db, me, "tool_folder", {"tool": "site-visits"}, None))
    cols = got["data"]["visits"]["columns"]
    assert [c["key"] for c in cols] == ["site", "province"]  # the old column stays, the new one is added
    prov = cols[1]
    assert prov["type"] == "choice" and prov["options"] == ["Gauteng", "Western Cape", "KwaZulu-Natal"]
    # and changing a column changes only it
    T.run(db, me, "change_tool_part", {"tool": "site-visits", "part": "table", "name": "visits", "value": json.dumps({"columns": {"site": {"required": True}}})}, None)
    cols = json.loads(T.run(db, me, "tool_folder", {"tool": "site-visits"}, None))["data"]["visits"]["columns"]
    assert cols[0]["key"] == "site" and cols[0]["required"] is True and cols[0]["type"] == "text" and len(cols) == 2


def test_the_check_reports_shapes_instead_of_crashing(tmp_path):
    d = tmp_path / "x-tool"
    for sub in ("pages", "data", "flows", "languages"):
        (d / sub).mkdir(parents=True)
    (d / "tool.json").write_text(json.dumps({"slug": "x-tool", "title": "tool.title", "access": {"roles": ["owner"]}}), encoding="utf-8")
    (d / "data" / "t.json").write_text(json.dumps({"columns": "name, type"}), encoding="utf-8")
    (d / "flows" / "f.json").write_text(json.dumps({"steps": {"s1": "make it"}}), encoding="utf-8")
    t = loader.read_folder(d)
    assert any("columns must be a list" in e for e in t.errors) and any("steps must be named objects" in e for e in t.errors)
