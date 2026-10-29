"""Entity Compliance: the company × document-type grid. Who sees which companies (owner/admin, manager,
user, team-only companies), adding a company gives it a cell per column, adding a column gives every
company a cell, required / not required, the cell statuses (incl. the expiring boundary in SA time),
filing a document (C-EXA reads the expiry from the two PDFs the old DocWatch was validated against),
renewals keeping the old file in history, the migration of the old free-text register, and reminders."""
from datetime import date, timedelta
from pathlib import Path

import pytest

from tests._pdfs import certificate, certificate_no_date

from cbx.db import SessionLocal
from cbx.models import CexaMessage, StoredFile, Team, User, WatchedDoc
from cbx.models.docs import DocType, Entity

FIX = Path(__file__).resolve().parent / "fixtures" / "docwatch"
DEFAULTS = ["Fire Certificate", "Public Liability", "Tax Clearance", "BBBEE Certificate", "Letter of Good Standing", "Working Permit"]


# ----------------------------------------------------------------------------------------------- helpers


def _grid(c):
    r = c.get("/api/docwatch")
    assert r.status_code == 200, r.text
    return r.json()


def _company(c, name, **kw):
    r = c.post("/api/docwatch/entities", json={"name": name, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def _cell(page, type_name):
    return next(c["doc"] for c in page["cells"] if c["type"]["name"] == type_name)


def _upload(c, did, name, data):
    return c.post(f"/api/docwatch/{did}/upload", files=[("files", (name, data, "application/pdf"))])


def _delete_company(c, eid):
    """Test cleanup, so it forces. A company that has filed anything is refused without ?force —
    see test_delete_refuses_a_company_that_has_filed_something_and_points_at_archive."""
    assert c.delete(f"/api/docwatch/entities/{eid}?force=true").status_code == 200


def _team_id(owner, name):
    return next(t["id"] for t in owner.get("/api/users").json()["teams"] if t["name"] == name)


def _manager(owner, email="dw-mgr@test.local", team="Utilities billing"):
    """A manager on `team` holding the tool, signed in."""
    from fastapi.testclient import TestClient

    from cbx.main import app
    from tests.conftest import _make, login

    tools = [t["id"] for t in owner.get("/api/users").json()["tools"] if t["slug"] == "doc-watch"]
    pw = _make(owner, email, "Mpho Manager", "manager", tools=tools, team=team)
    c = TestClient(app)
    login(c, email, pw)
    return c


# ----------------------------------------------------------------------------------------------- the grid


def test_fresh_install_has_the_default_columns(owner):
    g = _grid(owner)
    names = [t["name"] for t in g["types"]]
    assert names[: len(DEFAULTS)] == DEFAULTS or set(DEFAULTS) <= set(names)
    fire = next(t for t in g["types"] if t["name"] == "Fire Certificate")
    assert fire["label"] == "FIRE" and fire["warn_days"] == 90
    assert set(g["summary"]) >= {"companies", "expired", "expiring", "outstanding", "in_date"}
    assert g["me"]["is_admin"] is True and isinstance(g["teams"], list) and g["teams"]
    assert all("cells" in t for t in g["all_types"])  # admin-only column table with cell counts


def test_add_company_creates_a_cell_for_every_column(owner):
    page = _company(owner, "Sandton Campus", notes="Head office")
    e = page["entity"]
    assert e["name"] == "Sandton Campus" and e["team"] == "" and e["notes"] == "Head office"
    types = _grid(owner)["types"]
    assert len(page["cells"]) == len(types)
    for c in page["cells"]:
        assert c["doc"] is not None and c["doc"]["status"] == "missing" and c["doc"]["dot"] == "open" and c["doc"]["required"] is True
        assert c["doc"]["warn_days"] == c["type"]["warn_days"] and c["doc"]["title"] == c["type"]["name"]
    assert page["summary"]["outstanding"] == len(types) and page["summary"]["worst"] == "missing"
    row = next(r for r in _grid(owner)["entities"] if r["id"] == e["id"])
    assert [c["type_id"] for c in row["cells"]] == [t["id"] for t in types]
    assert all(c["doc"]["dot"] == "open" for c in row["cells"])
    # names are unique, blanks refused
    assert owner.post("/api/docwatch/entities", json={"name": "sandton campus"}).status_code == 400
    assert owner.post("/api/docwatch/entities", json={"name": "   "}).status_code == 400
    _delete_company(owner, e["id"])
    assert owner.get(f"/api/docwatch/entities/{e['id']}").status_code == 404


def test_add_column_adds_a_cell_to_every_company_and_remove_keeps_files(owner):
    a = _company(owner, "Column Test A")["entity"]
    b = _company(owner, "Column Test B")["entity"]
    n_companies = _grid(owner)["summary"]["companies"]
    r = owner.post("/api/docwatch/types", json={"name": "Asbestos Register", "prefix": "ASB", "order": 5, "warn_days": 60})
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["label"] == "ASB" and t["created_cells"] == n_companies
    g = _grid(owner)
    assert g["types"][0]["name"] == "Asbestos Register"  # order 5 sits left of the defaults
    cell = _cell(owner.get(f"/api/docwatch/entities/{a['id']}").json(), "Asbestos Register")
    assert cell["warn_days"] == 60 and cell["status"] == "missing"
    # file something into it, then edit the column: rename follows onto cells still carrying the old name
    up = _upload(owner, cell["id"], "asbestos.pdf", certificate(valid_until="Valid until: 31/12/2030", site="Column Test A"))
    assert up.status_code == 200, up.text
    fid = up.json()["file_id"]
    p = owner.patch(f"/api/docwatch/types/{t['id']}", json={"name": "Asbestos Survey", "prefix": "AS", "warn_days": 45})
    assert p.status_code == 200, p.text
    cell2 = _cell(owner.get(f"/api/docwatch/entities/{a['id']}").json(), "Asbestos Survey")
    assert cell2["id"] == cell["id"] and cell2["title"] == "Asbestos Survey" and cell2["label"] == "AS" and cell2["warn_days"] == 45
    # hidden columns leave the grid but keep their cells
    assert owner.patch(f"/api/docwatch/types/{t['id']}", json={"is_active": False}).status_code == 200
    assert "Asbestos Survey" not in [x["name"] for x in _grid(owner)["types"]]
    assert next(x for x in _grid(owner)["all_types"] if x["id"] == t["id"])["is_active"] is False
    assert owner.patch(f"/api/docwatch/types/{t['id']}", json={"is_active": True}).status_code == 200
    # duplicates and bad values are refused in plain English
    assert "already" in owner.post("/api/docwatch/types", json={"name": "asbestos survey"}).json()["detail"]
    assert owner.post("/api/docwatch/types", json={"name": "Bad", "warn_days": 0}).status_code == 400
    # remove the column: cells go, the stored file stays
    d = owner.delete(f"/api/docwatch/types/{t['id']}")
    assert d.status_code == 200 and d.json()["removed"] == n_companies
    assert owner.get(f"/api/docwatch/{cell['id']}").status_code == 404
    assert owner.get(f"/api/warehouse/files/{fid}").status_code == 200
    _delete_company(owner, a["id"])
    _delete_company(owner, b["id"])


# ----------------------------------------------------------------------------------------------- who sees what


def test_grid_visibility_per_role(owner, admin, user):
    mgr = _manager(owner)
    util = _team_id(owner, "Utilities billing")
    fin_pw = __import__("tests.conftest", fromlist=["_make"])._make(owner, "dw-fin@test.local", "Fin User", "user", team="Finance")
    assert fin_pw
    fin = _team_id(owner, "Finance")
    everyone = _company(owner, "Vis Everyone Co")["entity"]
    utilities = _company(owner, "Vis Utilities Co", team_id=util)["entity"]
    finance = _company(owner, "Vis Finance Co", team_id=fin)["entity"]
    assert utilities["team"] == "Utilities billing" and finance["team"] == "Finance"
    ids = lambda c: {e["id"] for e in _grid(c)["entities"]}  # noqa: E731
    assert {everyone["id"], utilities["id"], finance["id"]} <= ids(owner)
    assert {everyone["id"], utilities["id"], finance["id"]} <= ids(admin)
    assert {everyone["id"], utilities["id"]} <= ids(user) and finance["id"] not in ids(user)
    assert {everyone["id"], utilities["id"]} <= ids(mgr) and finance["id"] not in ids(mgr)
    assert _grid(mgr)["teams"] == [] and "all_types" not in _grid(mgr)
    # the team-only company's row says so
    row = next(e for e in _grid(user)["entities"] if e["id"] == utilities["id"])
    assert row["team"] == "Utilities billing" and row["can_manage"] is False
    # opening a company you can't see: refused and audited
    assert user.get(f"/api/docwatch/entities/{finance['id']}").status_code == 403
    assert mgr.get(f"/api/docwatch/entities/{finance['id']}").status_code == 403
    assert any(r["action"] == "docwatch_denied" for r in owner.get("/api/audit?action=docwatch_denied").json()["rows"])
    # a cell of a hidden company is hidden too, download included
    fcell = _cell(owner.get(f"/api/docwatch/entities/{finance['id']}").json(), "Tax Clearance")
    assert user.get(f"/api/docwatch/{fcell['id']}").status_code == 403
    assert user.patch(f"/api/docwatch/{fcell['id']}", json={"warn_days": 10}).status_code == 403
    assert user.get(f"/api/docwatch/{fcell['id']}/download").status_code == 403
    # managing: rename — manager on their team's company yes, on a shared one no; users never
    assert mgr.patch(f"/api/docwatch/entities/{utilities['id']}", json={"name": "Vis Utilities Co (renamed)", "notes": "by the manager"}).status_code == 200
    assert mgr.patch(f"/api/docwatch/entities/{everyone['id']}", json={"name": "x"}).status_code == 403
    assert user.patch(f"/api/docwatch/entities/{utilities['id']}", json={"name": "y"}).status_code == 403
    assert next(e for e in _grid(user)["entities"] if e["id"] == utilities["id"])["name"] == "Vis Utilities Co (renamed)"
    # who sees a company is the admin's call, deleting too
    assert mgr.patch(f"/api/docwatch/entities/{utilities['id']}", json={"everyone": True}).status_code == 403
    assert mgr.delete(f"/api/docwatch/entities/{utilities['id']}").status_code == 403
    assert user.delete(f"/api/docwatch/entities/{everyone['id']}").status_code == 403
    assert owner.patch(f"/api/docwatch/entities/{finance['id']}", json={"everyone": True}).json()["entity"]["team"] == ""
    assert finance["id"] in ids(user)
    assert owner.patch(f"/api/docwatch/entities/{finance['id']}", json={"team_id": fin}).json()["entity"]["team"] == "Finance"
    # columns are admin-only
    assert user.post("/api/docwatch/types", json={"name": "Nope"}).status_code == 403
    assert mgr.post("/api/docwatch/types", json={"name": "Nope"}).status_code == 403
    assert mgr.get("/api/docwatch/types").status_code == 403
    # adding a company: a user's or manager's lands on their own team, whatever they send
    mine = _company(user, "Vis Added By User", team_id=fin)["entity"]
    assert mine["team"] == "Utilities billing"
    theirs = _company(mgr, "Vis Added By Manager")["entity"]
    assert theirs["team"] == "Utilities billing" and theirs["can_manage"] is True
    for e in (everyone, utilities, finance, mine, theirs):
        _delete_company(owner, e["id"])


# ----------------------------------------------------------------------------------------------- statuses


def test_required_toggle_and_cell_statuses(owner):
    from cbx.util import local_today

    today = local_today()
    e = _company(owner, "Status Co")["entity"]
    page = owner.get(f"/api/docwatch/entities/{e['id']}").json()
    fire, tax, bee, wp = (_cell(page, n) for n in ("Fire Certificate", "Tax Clearance", "BBBEE Certificate", "Working Permit"))
    # not required → grey dot, out of the outstanding count
    p = owner.patch(f"/api/docwatch/{fire['id']}", json={"required": False}).json()
    assert p["status"] == "not_required" and p["dot"] == "grey" and p["dot_title"] == "Not required"
    assert owner.patch(f"/api/docwatch/{fire['id']}", json={"required": True}).json()["status"] == "missing"
    # dates: in date / expiring (boundary = warn_days) / expired, in the business's own day
    ok = owner.patch(f"/api/docwatch/{tax['id']}", json={"expiry": (today + timedelta(days=tax["warn_days"] + 1)).isoformat()}).json()
    assert ok["status"] == "ok" and ok["dot"] == "ok" and ok["days_left"] == tax["warn_days"] + 1 and ok["expiry_source"] == "user"
    edge = owner.patch(f"/api/docwatch/{tax['id']}", json={"expiry": (today + timedelta(days=tax["warn_days"])).isoformat()}).json()
    assert edge["status"] == "expiring" and edge["dot"] == "warn" and edge["dot_title"].startswith("Expiring soon — expires")
    exp = owner.patch(f"/api/docwatch/{bee['id']}", json={"expiry": (today - timedelta(days=1)).isoformat()}).json()
    assert exp["status"] == "expired" and exp["dot"] == "bad" and exp["days_left"] == -1 and "1 day ago" in exp["dot_title"]
    # doesn't expire: an in-date dot, no days
    never = owner.patch(f"/api/docwatch/{wp['id']}", json={"no_expiry": True}).json()
    assert never["status"] == "no_expiry" and never["dot"] == "ok" and never["dot_title"] == "Doesn't expire" and never["expiry"] is None and never["days_left"] is None
    # a bad date and a bad warning window are refused in plain English
    assert "day/month/year" in owner.patch(f"/api/docwatch/{tax['id']}", json={"expiry": "someday"}).json()["detail"]
    assert owner.patch(f"/api/docwatch/{tax['id']}", json={"warn_days": 0}).status_code == 400
    # SA-style dates are read
    assert owner.patch(f"/api/docwatch/{tax['id']}", json={"expiry": "14/09/2031"}).json()["expiry"] == "2031-09-14"
    # the company's roll-up and the "needs attention" list
    page = owner.get(f"/api/docwatch/entities/{e['id']}").json()
    s = page["summary"]
    assert s["expired"] == 1 and s["expiring"] == 0 and s["in_date"] == 2 and s["no_expiry"] == 1 and s["outstanding"] == len(page["cells"]) - 3 and s["worst"] == "expired"
    assert [a["id"] for a in page["attention"]] == [bee["id"]]
    row = next(r for r in _grid(owner)["entities"] if r["id"] == e["id"])
    assert row["summary"]["expired"] == 1 and _grid(owner)["entities"][0]["id"] == e["id"]  # worst company first
    # archive → grey dot, out of every count
    a = owner.patch(f"/api/docwatch/{bee['id']}", json={"archived": True}).json()
    assert a["status"] == "archived" and a["dot"] == "grey"
    assert owner.get(f"/api/docwatch/entities/{e['id']}").json()["summary"]["expired"] == 0
    assert owner.patch(f"/api/docwatch/{bee['id']}", json={"archived": False}).json()["status"] == "expired"
    _delete_company(owner, e["id"])


def test_status_function_boundaries_and_sast_day():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from cbx.docwatch.service import status_of
    from cbx.util import local_today

    class D:
        def __init__(self, expiry=None, file_id=None, warn_days=90, required=True, no_expiry=False, archived=False):
            self.expiry, self.file_id, self.warn_days, self.required, self.no_expiry, self.archived = expiry, file_id, warn_days, required, no_expiry, archived

    t = date(2026, 9, 10)
    assert status_of(D(), t) == "missing"
    assert status_of(D(file_id=5), t) == "needs_date"
    assert status_of(D(date(2026, 9, 9), 5), t) == "expired"
    assert status_of(D(date(2026, 9, 10), 5), t) == "expiring"  # expires today: due, not expired
    assert status_of(D(date(2026, 12, 9), 5), t) == "expiring"  # exactly warn_days (90) away counts as due
    assert status_of(D(date(2026, 12, 10), 5), t) == "ok"
    assert status_of(D(date(2026, 12, 10), 5, warn_days=91), t) == "expiring"
    assert status_of(D(date(2026, 9, 9), 5, required=False), t) == "not_required"  # not required beats expired
    assert status_of(D(date(2026, 9, 9), 5, archived=True), t) == "archived"
    assert status_of(D(no_expiry=True), t) == "no_expiry"
    assert status_of(D(file_id=None, no_expiry=True, required=False), t) == "not_required"
    # days are counted in South African time, not UTC
    assert local_today() == datetime.now(ZoneInfo("Africa/Johannesburg")).date()


# ----------------------------------------------------------------------------------------------- filing


def test_filing_attaches_the_file_and_asks_for_the_date_renewals_keep_the_old_one(owner):
    """Filing no longer guesses the expiry off the certificate — the person types it.

    That was a deliberate change made from live use: a date read wrongly shows a confident colour on
    the grid and nobody re-checks it, so a wrong date is worse than a blank one. The file, the
    history and the renewal chain all still work; only the guess is gone."""
    e = _company(owner, "Filing Co")["entity"]
    page = owner.get(f"/api/docwatch/entities/{e['id']}").json()
    fire = _cell(page, "Fire Certificate")
    r = _upload(owner, fire["id"], "fire_certificate_sandton.pdf", (FIX / "fire_certificate_sandton.pdf").read_bytes())
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["expiry"] is None and d["status"] == "needs_date" and d["renewed"] is False
    assert "type the expiry date" in d["message"]
    # the person types it, and from then on the cell behaves exactly as before
    typed = owner.patch(f"/api/docwatch/{fire['id']}", json={"expiry": "2027-07-16"}).json()
    assert typed["expiry"] == "2027-07-16" and typed["expiry_source"] == "user" and typed["status"] == "ok"
    assert d["file_id"] and d["file_name"].endswith(".pdf") and d["filed_by"] == "Test Owner" and d["history"] == []
    old_fid = d["file_id"]
    # the file opens through the tool's own door, as a download or inline
    assert owner.get(f"/api/docwatch/{fire['id']}/download").status_code == 200
    inline = owner.get(f"/api/docwatch/{fire['id']}/download?inline=1")
    assert inline.status_code == 200 and inline.headers["content-disposition"].startswith("inline")
    # renewal: same cell, new file, old one archived into history and still in the warehouse
    r2 = _upload(owner, fire["id"], "fire_2028.pdf", certificate(valid_until="Valid until: 30/06/2028", site="Filing Co"))
    assert r2.status_code == 200, r2.text
    n = r2.json()
    assert n["id"] == fire["id"] and n["renewed"] is True and n["file_id"] != old_fid
    assert n["expiry"] == "2027-07-16", "a renewal keeps the date already on the cell until the person types the new one"
    n = owner.patch(f"/api/docwatch/{fire['id']}", json={"expiry": "2028-06-30"}).json()
    assert n["expiry"] == "2028-06-30"
    assert len(n["history"]) == 1 and n["history"][0]["file_id"] == old_fid and n["history"][0]["expiry"] == "2027-07-16" and n["history"][0]["by"] == "Test Owner"
    assert owner.get(f"/api/warehouse/files/{old_fid}").status_code == 200
    assert owner.get(f"/api/docwatch/{fire['id']}/download?file={old_fid}").status_code == 200  # earlier files open from the history
    assert owner.get(f"/api/docwatch/{fire['id']}/download?file=999999").status_code == 404
    assert "warning" not in n
    # a further renewal keeps stacking history, and leaves the date alone until someone types it
    r3 = _upload(owner, fire["id"], "fire_old.pdf", certificate(valid_until="Valid until: 30/06/2027", site="Filing Co")).json()
    assert r3["expiry"] == "2028-06-30" and len(r3["history"]) == 2
    # nothing readable: the person is asked, and offered the dates in the document
    tax = _cell(page, "Tax Clearance")
    a = _upload(owner, tax["id"], "asbestos_survey_no_expiry.pdf", (FIX / "asbestos_survey_no_expiry.pdf").read_bytes()).json()
    assert a["expiry"] is None and a["status"] == "needs_date" and a["dot"] == "open" and a["dot_title"] == "Uploaded, expiry not set"
    assert "type the expiry date" in a["message"]
    picked = owner.patch(f"/api/docwatch/{tax['id']}", json={"expiry": "2026-05-17"}).json()
    assert picked["expiry"] == "2026-05-17" and picked["expiry_source"] == "user" and picked["status"] == "expired"
    # a renewal keeps the date already on the cell — nothing is guessed, so nothing is un-guessed
    r4 = _upload(owner, tax["id"], "service.pdf", certificate_no_date()).json()
    assert r4["renewed"] is True and r4["expiry"] == "2026-05-17"
    # an empty upload is refused
    assert owner.post(f"/api/docwatch/{tax['id']}/upload", files=[("files", ("x.pdf", b"", "application/pdf"))]).status_code == 400
    # what C-EXA saw (admin diagnostic)
    assert "30/06/2027" in owner.get(f"/api/docwatch/{fire['id']}/text").json()["text"]  # the current (renewed) file
    _delete_company(owner, e["id"])
    assert owner.get(f"/api/warehouse/files/{old_fid}").status_code == 200  # files outlive the company


def test_user_uploads_and_sets_dates_but_does_not_manage(owner, user):
    e = _company(owner, "User Filing Co")["entity"]
    page = user.get(f"/api/docwatch/entities/{e['id']}").json()
    assert page["can_manage"] is False and page["is_admin"] is False
    wp = _cell(page, "Working Permit")
    r = _upload(user, wp["id"], "permit.pdf", certificate(valid_until="Expiry date: 16/07/2031", site="User Filing Co"))
    assert r.status_code == 200, r.text
    assert r.json()["expiry"] is None and r.json()["filed_by"] == "Thandi Mokoena" and r.json()["can_manage"] is False
    assert user.patch(f"/api/docwatch/{wp['id']}", json={"expiry": "2031-07-16"}).json()["expiry"] == "2031-07-16"
    assert user.patch(f"/api/docwatch/{wp['id']}", json={"warn_days": 45, "notes": "Renewed by Bidvest"}).status_code == 200
    assert user.patch(f"/api/docwatch/{wp['id']}", json={"no_expiry": True}).json()["status"] == "no_expiry"
    assert user.patch(f"/api/docwatch/{wp['id']}", json={"no_expiry": False, "expiry": "2031-07-16"}).json()["status"] == "ok"
    assert user.patch(f"/api/docwatch/{wp['id']}", json={"required": False}).status_code == 403
    assert user.patch(f"/api/docwatch/{wp['id']}", json={"title": "Something else"}).status_code == 403
    assert user.patch(f"/api/docwatch/{wp['id']}", json={"archived": True}).status_code == 403
    assert user.delete(f"/api/docwatch/{wp['id']}").status_code == 403
    assert user.get(f"/api/docwatch/{wp['id']}/download").status_code == 200
    assert user.get(f"/api/docwatch/{wp['id']}/text").status_code == 200
    # the sidebar count is per person: nothing due for the user here
    assert isinstance(user.get("/api/home/counts").json()["docs_due"], int)
    _delete_company(owner, e["id"])


def test_user_without_tool_gets_403(owner):
    from fastapi.testclient import TestClient

    from cbx.main import app
    from tests.conftest import login

    pw = owner.post("/api/users", json={"email": "notool@test.local", "name": "No Tool", "role": "user", "team_name": "Finance"}).json().get("temp_password")
    if not pw:
        uid = next(u["id"] for u in owner.get("/api/users").json()["users"] if u["email"] == "notool@test.local")
        pw = owner.post(f"/api/users/{uid}/temp-password").json()["temp_password"]
    c = TestClient(app)
    login(c, "notool@test.local", pw)
    assert c.get("/api/docwatch").status_code == 403
    assert c.post("/api/docwatch/entities", json={"name": "Nope"}).status_code == 403


# ----------------------------------------------------------------------------------------------- the old ways in


def test_free_text_lines_land_on_the_grid(owner, user):
    """The old register calls (a line by company and type in words) still work: the company is found or
    created for the caller's team, a known type fills that cell, anything else is an 'other document'."""
    d = user.post("/api/docwatch", json={"title": "Generator permit", "entity": "Legacy Site A", "expiry": "2031-01-01", "warn_days": 60}).json()
    assert d["entity"] == "Legacy Site A" and d["doc_type_id"] is None and d["status"] == "ok" and d["warn_days"] == 60
    ent = user.get(f"/api/docwatch/entities/{d['entity_id']}").json()
    assert ent["entity"]["team"] == "Utilities billing"  # the user's team, so the company is theirs
    assert [x["id"] for x in ent["extra"]] == [d["id"]] and ent["summary"]["total"] == len(ent["cells"]) + 1
    t = user.post("/api/docwatch", json={"title": "Tax clearance 2031", "entity": "Legacy Site A", "doc_type": "Tax Clearance", "expiry": "2031-03-31"}).json()
    assert t["doc_type_id"] and t["doc_type"] == "Tax Clearance" and t["title"] == "Tax clearance 2031"
    assert _cell(user.get(f"/api/docwatch/entities/{d['entity_id']}").json(), "Tax Clearance")["id"] == t["id"]
    assert "already has" in user.post("/api/docwatch", json={"title": "Again", "entity": "Legacy Site A", "doc_type": "TAX"}).json()["detail"]
    assert user.post("/api/docwatch", json={"title": "  ", "entity": "Legacy Site A"}).status_code == 400
    assert user.post("/api/docwatch", json={"title": "x", "entity": "Legacy Site A", "expiry": "someday"}).status_code == 400
    ids = {x["id"] for x in _grid(user)["docs"]}
    assert d["id"] in ids and t["id"] in ids
    # upload the old way: the file names the site, the type lands in its cell
    r = owner.post("/api/docwatch/upload", files=[("files", ("coc.pdf", certificate(site="Legacy Site B"), "application/pdf"))], data={"entity": "Legacy Site B", "doc_type": "Public Liability"})
    assert r.status_code == 200, r.text
    u = r.json()["docs"][0]
    assert u["expiry"] is None and u["doc_type"] == "Public Liability" and u["entity"] == "Legacy Site B"
    # again into the same cell = a renewal, not a duplicate
    r2 = owner.post("/api/docwatch/upload", files=[("files", ("coc2.pdf", certificate(valid_until="Valid until: 31/03/2029", site="Legacy Site B"), "application/pdf"))], data={"entity": "Legacy Site B", "doc_type": "Public Liability"}).json()["docs"][0]
    assert r2["id"] == u["id"] and r2["renewed"] is True and r2["expiry"] is None
    # other documents can be deleted (a grid cell can't — mark it not required instead)
    assert owner.delete(f"/api/docwatch/{t['id']}").status_code == 400
    assert owner.delete(f"/api/docwatch/{d['id']}").status_code == 200
    _delete_company(owner, d["entity_id"])
    _delete_company(owner, u["entity_id"])


def test_migration_from_free_text_rows(owner, db):
    """The live server's rows: free-text entity/doc_type strings, no company or column yet."""
    from cbx.docwatch.service import migrate_legacy

    me = db.query(User).filter(User.email == "owner@test.local").one()
    util = db.query(Team).filter(Team.name == "Utilities billing").one()
    mk = lambda **kw: WatchedDoc(owner_id=me.id, **kw)  # noqa: E731
    rows = [
        mk(title="Fire cert Old Site", entity="Old Site", doc_type="Fire Certificate", team_id=util.id, warn_days=60, expiry=date(2031, 1, 1)),  # an existing column
        mk(title="Lift A", entity="Old Site", doc_type="Lift Inspection", team_id=util.id, warn_days=30, expiry=date(2031, 2, 1)),
        mk(title="Lift B", entity="Old Site", doc_type="Lift Inspection", team_id=util.id, warn_days=30, expiry=date(2031, 3, 1)),  # second live line, same pair
        mk(title="Lift old", entity="Old Site", doc_type="Lift Inspection", team_id=util.id, warn_days=45, expiry=date(2025, 1, 1), archived=True),  # a renewed one
        mk(title="Title deed", entity="Old Site", doc_type="", team_id=util.id, no_expiry=True),  # no type → other document
        mk(title="Loose paper", entity="", doc_type="", team_id=util.id, expiry=date(2031, 6, 1)),  # no site
    ]
    db.add_all(rows)
    db.commit()
    try:
        out = migrate_legacy(db)
        db.commit()
        assert out["entities"] == 2 and out["types"] == 1 and out["docs"] == 6
        old = db.query(Entity).filter(Entity.name == "Old Site").one()
        assert old.team_id == util.id
        lift = db.query(DocType).filter(DocType.name == "Lift Inspection").one()
        assert lift.prefix == "LI" and lift.warn_days == 30 and lift.is_active
        fire = db.query(DocType).filter(DocType.name == "Fire Certificate").one()
        for r in rows:
            db.refresh(r)
        assert rows[0].entity_id == old.id and rows[0].doc_type_id == fire.id
        winners = [r for r in rows[1:3] if r.doc_type_id == lift.id]
        assert len(winners) == 1 and winners[0].title == "Lift B"  # the newest live line is the cell
        loser = next(r for r in rows[1:3] if r is not winners[0])
        assert loser.entity_id == old.id and loser.doc_type_id is None and loser.doc_type == "Lift Inspection"
        assert rows[3].doc_type_id is None and rows[3].archived and (winners[0].history or [])[0]["expiry"] == "2025-01-01"
        assert rows[4].entity_id == old.id and rows[4].doc_type_id is None
        unfiled = db.query(Entity).filter(Entity.name == "Unfiled documents · Utilities billing").one()
        assert rows[5].entity_id == unfiled.id and unfiled.team_id == util.id
        # idempotent
        assert migrate_legacy(db) == {"entities": 0, "types": 0, "docs": 0}
        # and what the owner now sees: Old Site with its cells, the loose lines under "Other documents"
        page = owner.get(f"/api/docwatch/entities/{old.id}").json()
        assert _cell(page, "Lift Inspection")["title"] == "Lift B" and len(_cell(page, "Lift Inspection")["history"]) == 1
        assert {x["title"] for x in page["extra"]} == {"Lift A", "Title deed"}
        assert _cell(page, "Fire Certificate")["warn_days"] == 60
        assert "Lift Inspection" in [t["name"] for t in _grid(owner)["types"]]
    finally:  # leave the shared test database as it was (cells first — the database enforces the FKs)
        for r in db.query(WatchedDoc).filter(WatchedDoc.id.in_([x.id for x in rows])).all():
            db.delete(r)
        db.commit()
        for name in ("Old Site", "Unfiled documents · Utilities billing"):
            e = db.query(Entity).filter(Entity.name == name).first()
            if e:
                for d in db.query(WatchedDoc).filter(WatchedDoc.entity_id == e.id).all():
                    db.delete(d)
                db.flush()
                db.delete(e)
        db.commit()
        lift = db.query(DocType).filter(DocType.name == "Lift Inspection").first()
        if lift:
            for d in db.query(WatchedDoc).filter(WatchedDoc.doc_type_id == lift.id).all():
                db.delete(d)
            db.flush()
            db.delete(lift)
        db.commit()


def test_run_migrations_is_idempotent_with_the_grid_steps():
    from cbx.db import engine
    from cbx.migrate import run_migrations

    run_migrations(engine)
    run_migrations(engine)
    db = SessionLocal()
    try:
        assert db.query(DocType).count() >= len(DEFAULTS)
    finally:
        db.close()


# ----------------------------------------------------------------------------------------------- reminders


def test_reminders_go_to_the_filer_and_the_teams_managers(owner, user):
    from cbx.docwatch import reminders
    from cbx.util import local_today

    mgr = _manager(owner)
    today = local_today()
    util = _team_id(owner, "Utilities billing")
    e = _company(owner, "Reminder Co", team_id=util)["entity"]
    page = user.get(f"/api/docwatch/entities/{e['id']}").json()
    fire, tax, wp = (_cell(page, n) for n in ("Fire Certificate", "Tax Clearance", "Working Permit"))
    soon = (today + timedelta(days=6)).strftime("%d/%m/%Y")
    _upload(user, fire["id"], "lift.pdf", certificate(valid_until=f"Valid until: {soon}", site="Reminder Co"))
    d = user.patch(f"/api/docwatch/{fire['id']}", json={"expiry": (today + timedelta(days=6)).isoformat()}).json()
    assert d["status"] == "expiring" and d["days_left"] == 6
    # not required and doesn't-expire cells never remind, even with a date on them
    owner.patch(f"/api/docwatch/{tax['id']}", json={"expiry": (today + timedelta(days=2)).isoformat()})
    owner.patch(f"/api/docwatch/{tax['id']}", json={"required": False})
    owner.patch(f"/api/docwatch/{wp['id']}", json={"no_expiry": True})
    db = SessionLocal()
    try:
        n = reminders.run_once(db, today)
        db.commit()
        assert n >= 2
        thandi = db.query(User).filter(User.email == "thandi@test.local").one()
        mpho = db.query(User).filter(User.email == "dw-mgr@test.local").one()
        for who in (thandi, mpho):
            msgs = [m for m in db.query(CexaMessage).filter(CexaMessage.user_id == who.id, CexaMessage.kind == "needs_you").all() if m.meta.get("doc_id") == fire["id"]]
            assert len(msgs) == 1 and "6 days" in msgs[0].text and "Reminder Co" in msgs[0].text and msgs[0].link == f"#/doc-watch-entity?id={e['id']}"
        assert not any(m.meta.get("doc_id") in (tax["id"], wp["id"]) for m in db.query(CexaMessage).filter(CexaMessage.kind == "needs_you").all())
        doc = db.get(WatchedDoc, fire["id"])
        assert "7" in doc.reminders_sent and "30" in doc.reminders_sent and "90" in doc.reminders_sent
        before = db.query(CexaMessage).count()
        reminders.run_once(db, today)
        db.commit()
        assert db.query(CexaMessage).count() == before  # each stage once
    finally:
        db.close()
    assert mgr.get("/api/home/counts").json()["docs_due"] >= 1
    _delete_company(owner, e["id"])


def test_reminder_fires_when_entering_the_warning_window(owner):
    """A 90-day warning window means a reminder at 90 days, not silence until 30."""
    from cbx.docwatch import reminders
    from cbx.util import local_today

    today = local_today()
    e = _company(owner, "Window Co")["entity"]
    cell = _cell(owner.get(f"/api/docwatch/entities/{e['id']}").json(), "Letter of Good Standing")
    d = owner.patch(f"/api/docwatch/{cell['id']}", json={"expiry": (today + timedelta(days=80)).isoformat()}).json()
    assert d["status"] == "expiring" and d["warn_days"] == 90
    db = SessionLocal()
    try:
        reminders.run_once(db, today)
        db.commit()
        me = db.query(User).filter(User.email == "owner@test.local").one()
        msgs = [m for m in db.query(CexaMessage).filter(CexaMessage.user_id == me.id, CexaMessage.kind == "needs_you").all() if m.meta.get("doc_id") == d["id"]]
        assert len(msgs) == 1 and msgs[0].meta["stage"] == 90 and "80 days" in msgs[0].text
        assert db.get(WatchedDoc, d["id"]).reminders_sent == ["90"]
        reminders.run_once(db, today + timedelta(days=10))
        db.commit()
        assert len([m for m in db.query(CexaMessage).filter(CexaMessage.user_id == me.id).all() if m.meta.get("doc_id") == d["id"]]) == 1
        reminders.run_once(db, today + timedelta(days=51))
        db.commit()
        assert db.get(WatchedDoc, d["id"]).reminders_sent == ["90", "30"]
    finally:
        db.close()
    _delete_company(owner, e["id"])


# ----------------------------------------------------------------------------------------------- the reading engine


def test_scanner_date_formats_cross_check_and_candidates():
    from cbx.docwatch.scanner import cross_check, date_candidates, find_expiry

    assert find_expiry("Expiry date: 5 March 2028")["expiry"] == date(2028, 3, 5)
    assert find_expiry("Valid to 2027-11-30 inclusive")["expiry"] == date(2027, 11, 30)
    assert find_expiry("Renewal date 01.02.2027")["expiry"] == date(2027, 2, 1)
    r = find_expiry("Date of issue: 15 January 2026. This permit is valid for 12 months.")
    assert r["found"] and r["expiry"] == date(2027, 1, 14) and r["how"] == "issued+valid_for"
    assert find_expiry("Nothing dated here")["found"] is False
    fire = "FIRE COMPLIANCE CERTIFICATE\nInspected on: 11/07/2026\nDate of issue: 16/07/2026\nThis certificate is valid for 12 months from the date of issue.\nExpiry date: 16/07/2027\n"
    assert find_expiry(fire)["check"] == "verified"
    assert cross_check(fire.replace("16/07/2027", "16/09/2027"), date(2027, 9, 16)) == "disagrees"
    assert cross_check("Expiry date: 16/07/2027", date(2027, 7, 16)) == ""
    c = date_candidates("Survey completed: 17/05/2026\nReport reference: AS-4471\nNext review 17 May 2028\nPrinted 17/05/2026")
    assert [x["date"] for x in c] == ["2028-05-17", "2026-05-17"] and "Survey completed" in c[1]["quote"]


@pytest.mark.parametrize("name,expected", [("Fire Certificate", "FC"), ("Certificate of compliance", "COC"), ("B-BBEE certificate", "BBC"), ("x", "X")])
def test_initials_for_migrated_column_labels(name, expected):
    from cbx.docwatch.service import initials

    assert initials(name) == expected


# ---- removing a company, and loading the list finance sends -------------------------------------
# Names are prefixed so this test does not collide with companies other tests leave behind — the
# point here is the parsing and the dedupe, not the names themselves.
ULINDA = """Entity\tresponsible person
ZZ A&T Morocco\tLauriane
ZZ Algeria\tPhilo
ZZ Angola\tOtsile
ZZ Botswana\tImraan
ZZ CBRE Excellerate SA\tRavi
ZZ Nigeria\tOtsile"""

DOC_TYPES = """1. Updated bank letters
2. Tax clearance certificates
3. Vat certificate or Equivalent
4. COIDA/CNS/Social contribution certificate/related letter of good standing
5. BBBEE certificate SA"""


def test_the_finance_list_pastes_in_whole_and_twice_is_not_double(owner):
    r = owner.post("/api/docwatch/entities/import", json={"text": ULINDA}).json()
    assert len(r["added"]) == 6 and "ZZ A&T Morocco" in r["added"]
    assert "Entity" not in r["added"]  # the pasted header row is not a company
    ang = next(e for e in _grid(owner)["entities"] if e["name"] == "ZZ Angola")
    assert ang["responsible"] == "Otsile"

    # pasting the same mail again adds nothing and says so
    again = owner.post("/api/docwatch/entities/import", json={"text": ULINDA}).json()
    assert again["added"] == [] and len(again["skipped"]) == 6
    assert "already on the grid" in again["message"]


def test_document_types_paste_in_with_their_numbering_stripped(owner):
    r = owner.post("/api/docwatch/types/import", json={"text": DOC_TYPES}).json()
    assert len(r["added"]) == 5
    assert "Updated bank letters" in r["added"]  # "1. " gone
    assert not any(n[0].isdigit() for n in r["added"])


def test_archiving_a_company_keeps_every_document_it_ever_filed(owner, db):
    from cbx.models.docs import Entity
    from cbx.models import WatchedDoc

    owner.post("/api/docwatch/entities", json={"name": "Ceased Trading Ltd"})
    e = db.query(Entity).filter(Entity.name == "Ceased Trading Ltd").one()
    before = db.query(WatchedDoc).filter(WatchedDoc.entity_id == e.id).count()
    assert before > 0  # the grid gave it a cell per column

    r = owner.post(f"/api/docwatch/entities/{e.id}/archive", json={"archived": True, "reason": "contract ended June"})
    assert r.status_code == 200, r.text
    assert "nothing was thrown away" in r.json()["message"]

    # off the grid, still here, with its record intact
    assert not any(row["name"] == "Ceased Trading Ltd" for row in _grid(owner)["entities"])
    arch = owner.get("/api/docwatch/entities/archived").json()["entities"]
    row = next(x for x in arch if x["name"] == "Ceased Trading Ltd")
    assert row["documents"] == before and row["archived_reason"] == "contract ended June"
    db.expire_all()
    assert db.query(WatchedDoc).filter(WatchedDoc.entity_id == e.id).count() == before

    # and it comes back
    owner.post(f"/api/docwatch/entities/{e.id}/archive", json={"archived": False})
    assert any(row["name"] == "Ceased Trading Ltd" for row in _grid(owner)["entities"])


def test_delete_refuses_a_company_that_has_filed_something_and_points_at_archive(owner, db):
    from cbx.db import utcnow
    from cbx.models import WatchedDoc
    from cbx.models.docs import Entity

    owner.post("/api/docwatch/entities", json={"name": "Has History Ltd"})
    e = db.query(Entity).filter(Entity.name == "Has History Ltd").one()
    cell = db.query(WatchedDoc).filter(WatchedDoc.entity_id == e.id).first()
    cell.expiry = utcnow().date()
    db.add(cell)
    db.commit()

    r = owner.delete(f"/api/docwatch/entities/{e.id}")
    assert r.status_code == 400
    assert "archive the entity instead" in r.json()["detail"]
    assert db.get(Entity, e.id) is not None

    # a company added by mistake, with nothing filed, still goes
    owner.post("/api/docwatch/entities", json={"name": "Typo Ltd"})
    t = db.query(Entity).filter(Entity.name == "Typo Ltd").one()
    assert owner.delete(f"/api/docwatch/entities/{t.id}").status_code == 200
