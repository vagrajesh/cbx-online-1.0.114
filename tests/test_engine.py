"""The tool engine: folders load and validate, per-tool tables store rows with ownership, the menu and
pages follow access, the Builder saves drafts, previews them and releases them as a version."""
import json
import os
from pathlib import Path

import pytest

from cbx.config import settings
from cbx.engine import loader, providers
from cbx.engine.store import Store, StoreError

NESTED = bool(os.environ.get("CBX_SANDBOX_RUN"))


def _folder(root: Path, slug="sample", **over) -> Path:
    d = root / slug
    (d / "pages").mkdir(parents=True)
    (d / "data").mkdir()
    (d / "languages").mkdir()
    manifest = {"slug": slug, "title": "tool.title", "icon": "list", "menu": {"section": "My tools", "order": 5}, "access": {"roles": ["owner", "admin"]}, "home": "rows"}
    manifest.update(over.get("manifest", {}))
    (d / "tool.json").write_text(json.dumps(manifest), encoding="utf-8")
    (d / "data" / "items.json").write_text(json.dumps({"kind": "table", "write": "everyone", "scope": "all", "columns": [
        {"key": "name", "type": "text", "label": "col.name", "required": True}, {"key": "qty", "type": "int", "label": "col.qty"}, {"key": "due", "type": "date", "label": "col.due"}, {"key": "done", "type": "bool", "label": "col.done"}]}), encoding="utf-8")
    (d / "pages" / "rows.json").write_text(json.dumps({"type": "table", "title": "page.rows.title", "source": {"data": "items"}, "menu": True}), encoding="utf-8")
    (d / "pages" / "add.json").write_text(json.dumps({"type": "form", "title": "page.add.title", "source": {"data": "items"}}), encoding="utf-8")
    (d / "languages" / "en.json").write_text(json.dumps({"tool.title": "Sample", "page.rows.title": "Rows", "page.add.title": "Add", "col.name": "Name", "col.qty": "Qty", "col.due": "Due", "col.done": "Done"}), encoding="utf-8")
    (d / "languages" / "fr.json").write_text(json.dumps({"tool.title": "Exemple", "col.name": "Nom"}), encoding="utf-8")
    (d / "README.md").write_text("# Sample\n", encoding="utf-8")
    return d


def test_loader_reads_validates_and_reports(tmp_path):
    _folder(tmp_path)
    reg = loader.load_all(tmp_path)
    t = reg.tools["sample"]
    assert t.ok and set(t.pages) == {"rows", "add"} and t.text("tool.title", "fr") == "Exemple" and t.text("col.qty", "fr") == "Qty"
    assert t.missing()["fr"] == ["col.done", "col.due", "col.qty", "page.add.title", "page.rows.title"] and t.missing()["ar"]
    assert any("fr:" in w for w in t.warnings) and any("ar:" in w for w in t.warnings)
    # a broken folder is listed with its problems, in plain sentences, and kept out of the menu
    bad = _folder(tmp_path, "bad-one")
    (bad / "pages" / "grid.json").write_text(json.dumps({"type": "grid", "title": "page.grid.title", "source": {"data": "items"}}), encoding="utf-8")
    (bad / "pages" / "prov.json").write_text(json.dumps({"type": "table", "title": "nope title", "source": {"provider": "no.such"}}), encoding="utf-8")
    (bad / "tool.json").write_text(json.dumps({"slug": "bad-one", "title": "tool.title", "menu": {"section": "Admin"}, "access": {"roles": ["user"]}}), encoding="utf-8")
    reg = loader.load_all(tmp_path)
    b = reg.broken["bad-one"]
    errs = " | ".join(b.errors)
    assert "Admin section can only be for owner and admin" in errs and "provider 'no.such' does not exist" in errs and "a grid needs three tables of the tool" in errs
    assert "is not a language key" in errs and "languages/en.json has no 'page.grid.title'" in errs
    assert "bad-one" not in reg.tools


def test_store_is_one_file_per_tool_with_typed_columns(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    cols = [{"key": "name", "type": "text", "required": True}, {"key": "qty", "type": "int"}, {"key": "due", "type": "date"}, {"key": "done", "type": "bool"}]
    st = Store("sample")
    r = st.insert("items", cols, {"name": "Bolts", "qty": "12", "due": "2026-10-01", "done": "yes"}, by=7)
    assert r["id"] == 1 and r["qty"] == 12 and r["done"] is True and r["created_by"] == 7
    assert (tmp_path / "tools" / "sample.sqlite").exists()
    with pytest.raises(StoreError, match="required"):
        st.insert("items", cols, {"qty": 1}, by=7)
    with pytest.raises(StoreError, match="must be a int"):
        st.insert("items", cols, {"name": "x", "qty": "twelve"}, by=7)
    with pytest.raises(StoreError, match="must be a date"):
        st.insert("items", cols, {"name": "x", "due": "next week"}, by=7)
    st.insert("items", cols, {"name": "Nuts", "qty": 3}, by=8)
    assert st.rows("items", cols)["total"] == 2
    assert st.rows("items", cols, q="bol")["rows"][0]["name"] == "Bolts"
    assert [r["name"] for r in st.rows("items", cols, sort="qty")["rows"]] == ["Nuts", "Bolts"]
    assert st.rows("items", cols, mine=8)["total"] == 1
    # a new column is added, never dropped
    cols2 = cols + [{"key": "note", "type": "text"}]
    assert st.update("items", cols2, 1, {"note": "urgent"})["note"] == "urgent"
    assert st.delete("items", cols2, 2) and st.count("items") == 1
    # another tool is another file
    Store("other").insert("things", [{"key": "a", "type": "text"}], {"a": "b"}, by=1)
    assert (tmp_path / "tools" / "other.sqlite").exists() and st.count("things") == 0


def test_engine_api_menu_pages_and_rows(owner, admin, user, tmp_path, monkeypatch):
    _folder(tmp_path)
    monkeypatch.setattr(loader, "TOOLS_ROOT", tmp_path)
    loader.reload(tmp_path)
    try:
        m = owner.get("/api/engine/menu?lang=fr").json()
        s = next(t for t in m["tools"] if t["slug"] == "sample")
        assert s["title"] == "Exemple" and s["home"] == "rows" and s["section"] == "My tools"
        assert not any(t["slug"] == "sample" for t in user.get("/api/engine/menu").json()["tools"])
        assert user.get("/api/engine/tools/sample/pages/rows").status_code == 403
        p = admin.get("/api/engine/tools/sample/pages/rows").json()
        w = next(x for x in p["widgets"].values() if x.get("block"))
        assert p["page"]["type"] == "table" and w["block"]["name"] == "items" and w["can"]["add"] is True and w["can"]["any_row"] is True
        # rows: add as admin, add as owner; the owner edits anyone's, a plain add-only user only their own
        r = admin.post("/api/engine/tools/sample/data/items", json={"values": {"name": "Bolts", "qty": 12, "due": "2026-10-01", "done": True}})
        assert r.status_code == 200 and r.json()["qty"] == 12, r.text
        rid = r.json()["id"]
        assert owner.post("/api/engine/tools/sample/data/items", json={"values": {"qty": 1}}).status_code == 400  # name required
        rows = owner.get("/api/engine/tools/sample/data/items?page=rows").json()
        assert rows["total"] == 1 and rows["columns"][0]["key"] == "name"
        assert owner.patch(f"/api/engine/tools/sample/data/items/{rid}", json={"values": {"qty": 13}}).json()["qty"] == 13
        assert owner.get("/api/audit?action=tool_row_edited").json()["rows"]
        assert admin.delete(f"/api/engine/tools/sample/data/items/{rid}").json()["ok"] is True
        assert owner.get("/api/engine/tools/sample/data/items/999").status_code in (404, 405)
        assert owner.get("/api/engine/tools/nope/pages/rows").status_code == 404
    finally:
        from cbx.config import ROOT

        loader.reload(ROOT / "tools")  # the real folders again, whatever the monkeypatch still says


def test_providers_answer_in_their_shapes(owner, db):
    from cbx.models import User

    me = db.query(User).filter(User.email == "owner@test.local").one()
    g = providers.PROVIDERS["compliance.grid"].fn(db, me, {})
    assert set(g) >= {"columns", "rows", "summary", "legend"}
    t = providers.PROVIDERS["compliance.documents"].fn(db, me, {})
    assert t["columns"][0]["key"] == "entity" and isinstance(t["rows"], list)
    # a failed sign-in from an unknown address shows up as unregistered
    from fastapi.testclient import TestClient

    from cbx.main import app

    TestClient(app).post("/api/auth/login", json={"email": "ghost@nowhere.test", "password": "wrong-wrong-wrong"})
    a = providers.PROVIDERS["core.sign_in_attempts"].fn(db, me, {"registered": "no"})
    row = next((r for r in a["rows"] if r["email"] == "ghost@nowhere.test"), None)
    assert row and row["attempts"] >= 1 and row["registered"] is False
    assert all(r["registered"] for r in providers.PROVIDERS["core.sign_in_attempts"].fn(db, me, {"registered": "yes"})["rows"])
    assert [p["name"] for p in providers.describe_all()] == sorted(providers.PROVIDERS)


def test_builder_saves_drafts_previews_and_reports(owner, admin, user, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    ov = owner.get("/api/builder").json()
    assert any(p["name"] == "core.sign_in_attempts" for p in ov["providers"]) and "shield" in ov["icons"] and ov["next_tag"]
    assert user.get("/api/builder").status_code == 403
    folder = {"manifest": {"title": "tool.title", "icon": "list", "menu": {"section": "Admin", "order": 90}, "access": {"roles": ["owner", "admin"]}, "home": "attempts"},
              "pages": {"attempts": {"type": "table", "title": "page.attempts.title", "source": {"provider": "core.sign_in_attempts"}, "params": {"registered": "no"}, "columns": [{"key": "email", "label": "col.email"}, {"key": "attempts", "label": "col.attempts", "type": "int"}], "filters": ["days"]}},
              "data": {}, "languages": {"en": {"tool.title": "Sign-in attempts", "page.attempts.title": "Unregistered", "col.email": "Email", "col.attempts": "Attempts", "filter.days": "Days"}, "ar": {"tool.title": "محاولات تسجيل الدخول"}}, "readme": "# Sign-in attempts\n"}
    r = admin.put("/api/builder/tools/sign-in-attempts", json=folder)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["source"] == "draft" and d["errors"] == [] and d["live_exists"] is False and d["changed"] is True
    assert (tmp_path / "builder" / "tools" / "sign-in-attempts" / "pages" / "attempts.json").exists()
    assert any("pt:" in w for w in d["warnings"])  # not translated yet — a warning, not a stop
    one = owner.get("/api/builder/tools/sign-in-attempts").json()
    assert one["title"] == "Sign-in attempts" and one["missing"]["pt"]
    # the draft page previews with live data, in Arabic, right to left
    pv = owner.get("/api/builder/tools/sign-in-attempts/preview/attempts?lang=ar").json()
    assert pv["dir"] == "rtl" and pv["strings"]["tool.title"] != "Sign-in attempts" and "rows" in pv["data"]
    # a broken draft is saved, and says what is wrong
    bad = {**folder, "pages": {"attempts": {"type": "grid", "title": "page.attempts.title", "source": {"provider": "core.sign_in_attempts"}}}}
    d2 = admin.put("/api/builder/tools/sign-in-attempts", json=bad).json()  # the draft is the admin's (1.0.80): they change it
    assert any("gives a table, not a grid" in e for e in d2["errors"])
    assert owner.post("/api/builder/release", json={"slugs": ["sign-in-attempts"]}).status_code == 400  # problems → no release
    assert owner.get("/api/audit?action=builder_saved").json()["rows"]
    ov2 = owner.get("/api/builder").json()
    row = next(t for t in ov2["tools"] if t["slug"] == "sign-in-attempts")
    assert row["live"] is False and row["draft"] is True
    assert owner.delete("/api/builder/tools/sign-in-attempts/draft").json()["ok"]
    assert owner.get("/api/builder/tools/sign-in-attempts").status_code == 404


@pytest.mark.skipif(NESTED, reason="no nested sandbox test runs")
def test_builder_release_packages_a_version_with_the_tool_and_its_test(owner, tmp_path, monkeypatch):
    from cbx.versions import injector

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(injector, "TEST_CMD", [*injector.TEST_CMD[:4], "tests/test_core.py", "-x", "-k", "test_login_and_roles"])
    monkeypatch.setattr(injector, "RESTART_ENABLED", False)
    monkeypatch.setattr(injector, "RUN_PIP", False)
    folder = {"manifest": {"title": "tool.title", "icon": "list", "menu": {"section": "My tools", "order": 50}, "access": {"roles": ["owner"]}, "home": "rows"},
              "pages": {"rows": {"type": "table", "title": "page.rows.title", "source": {"data": "items"}}}, "data": {"items": {"kind": "table", "columns": [{"key": "name", "type": "text", "label": "col.name"}]}},
              "languages": {"en": {"tool.title": "Built here", "page.rows.title": "Rows", "col.name": "Name"}}, "readme": "# Built here\n"}
    assert owner.put("/api/builder/tools/built-here", json=folder).json()["errors"] == []
    r = owner.post("/api/builder/release", json={"notes": "first tool from the Builder"})
    assert r.status_code == 200, r.text
    v = r.json()["version"]
    assert v["tag"] == r.json()["tag"] and v["status"] in ("sandbox", "failed"), v
    pkg = Path(v["package_path"]) if v.get("package_path") else Path(settings.data_dir) / "versions" / v["tag"]
    assert (pkg / "tools" / "built-here" / "tool.json").exists()
    assert (pkg / "tools" / "built-here" / "tests" / "test_built_here.py").exists()
    assert (pkg / "tools" / "entity-compliance" / "tool.json").exists()  # the live folders travel too
    assert (pkg / "tools" / "conftest.py").exists()
    assert (tmp_path / "builder" / "tools" / "built-here" / ".released").read_text(encoding="utf-8") == v["tag"]
    assert owner.get("/api/audit?action=builder_released").json()["rows"]
