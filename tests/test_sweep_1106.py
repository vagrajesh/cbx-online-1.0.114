"""1.0.106 (Sweeper O): what the browser sweep of the old CBX found — every page and tool as the owner, an everyday user,
a viewer and a support person, at desktop and phone width — and was fixed. Each test names the page it guards."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from cbx.main import app

from .conftest import _make, login

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _person(owner, email, name, role, slugs):
    ids = [t["id"] for t in owner.get("/api/users").json()["tools"] if t["slug"] in slugs]
    pw = _make(owner, email, name, role, tools=ids)
    c = TestClient(app)
    login(c, email, pw)
    return c


# ---- To-do (a tool folder): a viewer "can look, and change nothing at all" ---------------------------------------------------
def test_a_viewer_gets_no_add_button_and_cannot_add_or_press_a_rule_on_the_todo(owner):
    v = _person(owner, "sweep-viewer@test.local", "Sweep Viewer", "viewer", ["todo"])
    page = v.get("/api/engine/tools/todo/pages/mine")
    assert page.status_code == 200, page.text
    cans = [w["can"] for w in page.json()["widgets"].values() if isinstance(w, dict) and "can" in w]
    assert cans and not any(c["add"] or c["edit"] or c["delete"] for c in cans)
    r = v.post("/api/engine/tools/todo/data/tasks", json={"values": {"title": "A viewer's task"}})
    assert r.status_code == 403, r.text
    assert v.post("/api/flows/todo/take/run", json={"values": {}}).status_code == 403

    u = _person(owner, "sweep-user@test.local", "Sweep User", "user", ["todo"])
    assert u.post("/api/engine/tools/todo/data/tasks", json={"values": {"title": "Mine"}}).status_code == 200
    assert u.post("/api/flows/todo/take/run", json={"values": {}}).status_code != 403  # a person may press it (400: no row)


# ---- C-EXA hub: the Standing agents tab only for the owner (its data is require_owner; a viewer got a 403 box) ---------------
def test_the_standing_agents_tab_is_the_owners_only():
    line = next(ln for ln in _read("web/views/index.js").splitlines() if "key: 'standing'" in ln)
    assert "roles: ['owner']" in line and "also:" not in line


# ---- the engine's add dialog names the empty required box, in the four languages -----------------------------------------------
def test_the_missing_field_words_are_in_every_language():
    js = _read("web/lang.js")
    for key in ("fill_in", "none_to_pick"):
        assert len(re.findall(rf"\b{key}: '[^']*\{{name\}}[^']*'", js)) == 4, key
    tool = _read("web/views/tool.js")
    assert "'none_to_pick' : 'fill_in'" in tool


# ---- the smaller screen fixes ------------------------------------------------------------------------------------------------
def test_the_screen_fixes_stay():
    # AP Tool: a viewer with no runs got a blank page under "Make SAP upload"
    assert "id: 'apsempty'" in _read("web/views/ap_simple.js")
    # IFRS 16: a viewer's form-only cards are hidden; "1 rows" reads "1 row"
    ifrs = _read("web/views/ifrs16.js")
    assert 's.classList.add("hide")' in ifrs and 'm.rows === 1 ? "" : "s"' in ifrs
    # Site Schedules: the charge lines have room for the amount; the headings are hidden on a phone
    css = _read("web/app.css")
    assert ".ssv .ss-row.ss-charge{" in css and ".ssv .ss-row.ss-charge-hd{display:none}" in css
    assert 'class: "ss-row ss-charge"' in _read("web/views/site-schedules.js")
    # Help: the "you don't have the tool" line no longer squeezes the lesson to a word a line on a phone
    assert "row tr-act" in _read("web/views/training.js") and ".tr-list li.off .tr-act" in css
    # Support set-up: Add with nobody picked says so
    assert "Pick the person first." in _read("web/views/support.js")
    # People: View as reloads without a hash change (the old page asked for the drawer's suggestions as the new person)
    assert "history.replaceState(null, '', '#/home'); location.reload();" in _read("web/views/users.js")
