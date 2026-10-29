"""Entity Compliance as a tool folder (1.0.63: its own tables): sound, visible to those who may see it, refused to the rest."""
from cbx.engine import loader


def test_folder_is_sound():
    t = loader.registry().tools.get("entity-compliance")
    assert t is not None and t.ok, (t.errors if t else "not loaded")
    assert set(t.pages) == {"grid", "company", "documents", "settings"} and t.manifest["home"] == "grid"
    assert [w["type"] for w in loader.widgets_of("grid", t.pages["grid"])] == ["links", "stats", "grid"]  # 1.0.99: Export above the tiles
    assert set(t.data) == {"companies", "doc_types", "documents", "company_people"}
    for lang in ("en", "ar", "pt", "fr"):
        assert t.languages.get(lang), lang
    assert not any(t.missing().values()), t.missing()


def test_owner_gets_the_grid_and_a_stranger_does_not(owner, user):
    from fastapi.testclient import TestClient

    from cbx.main import app
    from tests.conftest import _make, login

    menu = owner.get("/api/engine/menu").json()["tools"]
    assert any(x["slug"] == "entity-compliance" for x in menu)
    p = owner.get("/api/engine/tools/entity-compliance/pages/grid").json()
    types = [w["type"] for c in p["page"]["layout"]["sections"][0]["columns"] for w in c["widgets"]]
    assert types == ["links", "stats", "grid"] and p["strings"]["tool.title"] == "Entity Compliance" and p["dir"] == "ltr"
    assert p["widgets"]["w_grid"]["block"]["name"] == "documents"
    g = owner.get("/api/engine/tools/entity-compliance/data/rows?page=grid&widget=w_grid").json()
    assert "columns" in g and "rows" in g and "summary" in g and g["cells"]["block"] == "documents"
    assert owner.get("/api/engine/tools/entity-compliance/data/rows?page=grid&widget=nope").status_code == 404
    d = owner.get("/api/engine/tools/entity-compliance/data/documents?page=documents").json()
    assert d["columns"][0]["key"] == "company" and isinstance(d["rows"], list)
    # a user with the grant sees it; one without sees neither the menu item nor the page
    assert any(x["slug"] == "entity-compliance" for x in user.get("/api/engine/menu").json()["tools"])
    pw = _make(owner, "nobody@test.local", "No Grants", "user", tools=[])
    nobody = TestClient(app)
    login(nobody, "nobody@test.local", pw)
    assert not any(x["slug"] == "entity-compliance" for x in nobody.get("/api/engine/menu").json()["tools"])
    assert nobody.get("/api/engine/tools/entity-compliance/pages/grid").status_code == 403
    assert nobody.get("/api/engine/tools/entity-compliance/data/grid?page=grid").status_code == 403


def test_arabic_is_right_to_left(owner):
    p = owner.get("/api/engine/tools/entity-compliance/pages/grid?lang=ar").json()
    assert p["dir"] == "rtl" and p["strings"]["tool.title"] != "Entity Compliance"
    # a language the tool has no file for falls back to English, left-to-right
    p = owner.get("/api/engine/tools/entity-compliance/pages/grid?lang=xx").json()
    assert p["lang"] == "en" and p["dir"] == "ltr"
