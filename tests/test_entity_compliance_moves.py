"""1.0.63: Entity Compliance moves into its tool folder — its own tables, changeable in the Builder — with
every company, document type, document, file (and the files they replaced) and who may see what."""
import json
import shutil
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from cbx import state
from cbx.config import settings
from cbx.docwatch import copy_in as CI
from cbx.docwatch import tool as EC
from cbx.engine import loader
from cbx.engine.store import Store
from cbx.main import app
from cbx.models import CexaMessage, User
from cbx.util import local_today
from tests._pdfs import certificate
from tests.conftest import _make, login

SLUG = "entity-compliance"


def _wipe(db):
    for k in (CI.KEY, CI.FROZEN, EC.SENT_KEY):
        state.set(db, k, "")
    db.commit()
    p = settings.data_dir / "tools" / f"{SLUG}.sqlite"
    try:
        for x in (p, p.with_name(p.name + "-wal"), p.with_name(p.name + "-shm")):
            x.unlink(missing_ok=True)
    except OSError:
        # Windows (the server's own test run): a file another connection still has open can't be removed —
        # empty it instead. A left-over company would switch every later test over to "read the tool".
        import sqlite3

        c = sqlite3.connect(p, timeout=10)
        try:
            for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                c.execute(f'DELETE FROM "{name}"')
            c.commit()
        finally:
            c.close()
    shutil.rmtree(settings.data_dir / "tools" / "_files" / SLUG, ignore_errors=True)


def _drop_old(owner):
    g = owner.get("/api/docwatch").json()
    archived = owner.get("/api/docwatch/entities/archived").json()
    rows = list(g.get("entities") or []) + list(archived.get("entities") or [])
    for e in rows:
        if str(e.get("name", "")).startswith("EC "):
            owner.delete(f"/api/docwatch/entities/{e['id']}?force=true")


@pytest.fixture()
def old_world(owner, db):
    """Built-in Entity Compliance as a live server has it: types, companies (one its team's only, one
    archived, one with a responsible account), a grant, documents with files and a renewal."""
    _wipe(db)
    _drop_old(owner)
    tools = [t["id"] for t in owner.get("/api/users").json()["tools"] if t["slug"] == "doc-watch"]
    pw_ctrl = _make(owner, "ec-ctrl@test.local", "Moz Controller", "user", tools=tools)
    pw_user = _make(owner, "ec-user@test.local", "Plain User", "user", tools=tools, team="EC Other Team")
    ctrl = db.query(User).filter(User.email == "ec-ctrl@test.local").one()
    mk = lambda name, **kw: owner.post("/api/docwatch/entities", json={"name": name, **kw}).json()  # noqa: E731
    moz = mk("EC Mozambique", responsible="Moz Controller")
    owner.patch(f"/api/docwatch/entities/{moz['entity']['id']}", json={"responsible_user_id": ctrl.id})
    sa = mk("EC South Africa", responsible="Finance Person")
    from cbx.models.docs import Entity

    db.query(Entity).filter(Entity.id == sa["entity"]["id"]).update({"responsible_email": "finance.person@cbreexcellerate.com"})
    db.commit()
    gone = mk("EC Closed Co")
    owner.post(f"/api/docwatch/entities/{gone['entity']['id']}/archive", json={"archived": True, "reason": "contract ended"})
    team_id = next(t["id"] for t in owner.get("/api/users").json()["teams"] if t["name"] == "Utilities billing")
    teamco = mk("EC Team Only", team_id=team_id)
    # Mozambique only for its controller
    owner.post(f"/api/docwatch/entities/{moz['entity']['id']}/access", json={"user_id": ctrl.id, "only": True})
    cell = lambda page, i=0: page["cells"][i]["doc"]  # noqa: E731
    up = lambda did, name, until: owner.post(f"/api/docwatch/{did}/upload", files=[("files", (name, certificate(valid_until=until, site="X"), "application/pdf"))])  # noqa: E731
    d1 = cell(moz, 0)
    up(d1["id"], "tax_2025.pdf", "Valid until: 31/12/2025")
    owner.patch(f"/api/docwatch/{d1['id']}", json={"expiry": (local_today() + timedelta(days=5)).isoformat()})
    owner.post(f"/api/docwatch/{d1['id']}/renew", files=[("files", ("tax_2026.pdf", certificate(valid_until="Valid until: 31/12/2026", site="X"), "application/pdf"))])
    owner.patch(f"/api/docwatch/{d1['id']}", json={"expiry": (local_today() + timedelta(days=5)).isoformat()})
    d2 = cell(sa, 1)
    owner.patch(f"/api/docwatch/{d2['id']}", json={"expiry": (local_today() - timedelta(days=3)).isoformat()})
    owner.patch(f"/api/docwatch/{cell(teamco, 0)['id']}", json={"expiry": (local_today() + timedelta(days=200)).isoformat()})
    d3 = cell(sa, 2)
    owner.patch(f"/api/docwatch/{d3['id']}", json={"required": False})
    yield {"moz": moz["entity"]["id"], "sa": sa["entity"]["id"], "team": teamco["entity"]["id"], "ctrl": ctrl, "pw": {"ctrl": pw_ctrl, "user": pw_user}}
    _wipe(db)
    _drop_old(owner)
    from cbx.models import MailLog

    db.query(MailLog).filter((MailLog.ref == "reminders") | (MailLog.ref.like(f"tool:{SLUG}/%"))).delete(synchronize_session=False)
    db.commit()


def _tool_rows(block):
    t = loader.registry().tools[SLUG]
    return Store(SLUG).rows(block, t.data[block]["columns"], limit=5000, tables=t.data)["rows"]


def test_the_folder_has_its_own_tables_and_is_sound():
    t = loader.registry().tools.get(SLUG)
    assert t is not None and t.ok, (t.errors if t else "missing")
    assert set(t.data) == {"companies", "doc_types", "documents", "company_people"}
    assert set(t.pages) == {"grid", "company", "documents", "settings"}
    assert not any(t.missing().values()), t.missing()


def test_everything_is_copied_with_files_and_history(old_world, db, owner):
    got = CI.run_at_start(db, force=True)
    db.commit()
    assert got["companies"] >= 4 and got["documents"] >= 3 and got["files"] >= 1 and got["earlier files"] >= 1, got
    assert CI.moved(db) and CI.run_at_start(db, force=True) == {}  # once only
    comps = {c["name"]: c for c in _tool_rows("companies")}
    moz, sa = comps["EC Mozambique"], comps["EC South Africa"]
    assert moz["responsible"] == "Moz Controller" and moz["responsible_account"] == old_world["ctrl"].id and moz["visible"] == "Everyone with the tool"
    assert sa["responsible_email"] == "finance.person@cbreexcellerate.com" and comps["EC Closed Co"]["archived"] is True
    assert comps["EC Team Only"]["visible"] == "Only its people"
    assert any(p["company"] == moz["id"] and p["person"] == old_world["ctrl"].id for p in _tool_rows("company_people"))
    docs = [d for d in _tool_rows("documents") if d["company"] == moz["id"]]
    assert len(docs) == 1 and docs[0]["file"]["name"] == "tax_2026.pdf" and [e["name"] for e in docs[0]["file"]["earlier"]] == ["tax_2025.pdf"]
    assert docs[0]["status"] == "expiring"
    sa_docs = {d["status"] for d in _tool_rows("documents") if d["company"] == sa["id"]}
    assert {"expired", "not_required"} <= sa_docs
    # the files themselves came along — the current one and the one it replaced
    r = owner.get(f"/api/engine/tools/{SLUG}/data/documents/{docs[0]['id']}/files/file")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    e = docs[0]["file"]["earlier"][0]["id"]
    assert owner.get(f"/api/engine/tools/{SLUG}/data/documents/{docs[0]['id']}/files/file?v={e}").content.startswith(b"%PDF")
    # the old screens only read now
    assert owner.get("/api/docwatch").status_code == 200
    r = owner.post("/api/docwatch/entities", json={"name": "Too late"})
    assert r.status_code == 409 and "its own tool" in r.json()["detail"]
    assert "doc-watch" in owner.get("/api/auth/me").json()["moved"]


def test_the_grid_and_who_sees_what(old_world, db, owner):
    CI.run_at_start(db, force=True)
    db.commit()
    g = owner.get(f"/api/engine/tools/{SLUG}/data/rows?page=grid&widget=w_grid").json()
    names = [r["label"] for r in g["rows"]]
    assert "EC Mozambique" in names and "EC Closed Co" not in names  # archived companies leave the grid
    assert g["extra"][0]["key"] == "responsible"
    moz = next(r for r in g["rows"] if r["label"] == "EC Mozambique")
    assert moz["extra"][0] == {"key": "responsible", "value": "Moz Controller", "person": True}
    sa = next(r for r in g["rows"] if r["label"] == "EC South Africa")
    assert sa["worst"] == "expired" and names.index("EC South Africa") < names.index("EC Mozambique")  # worst first
    dots = {c["dot"] for c in sa["cells"].values()}
    assert {"bad", "grey", "open"} <= dots
    empty = next(c for c in sa["cells"].values() if c["status"] == "missing")
    assert "preset" in empty and empty["preset"]["company"] == sa["id"]
    s = owner.get(f"/api/engine/tools/{SLUG}/data/rows?page=grid&widget=w_stats").json()["summary"]
    assert s["companies"] == len(g["rows"]) and s["expired"] >= 1 and s["expiring"] >= 1
    # a company's page: only that company
    one = owner.get(f"/api/engine/tools/{SLUG}/data/rows?page=company&widget=w_cgrid&company={moz['id']}").json()
    assert [r["label"] for r in one["rows"]] == ["EC Mozambique"]
    docs = owner.get(f"/api/engine/tools/{SLUG}/data/rows?page=company&widget=w_cdocs&company={moz['id']}").json()
    assert docs["total"] == 1
    # the Mozambique controller was given only Mozambique: that is all they see
    ctrl = TestClient(app)
    login(ctrl, "ec-ctrl@test.local", old_world["pw"]["ctrl"])
    mine = ctrl.get(f"/api/engine/tools/{SLUG}/data/rows?page=grid&widget=w_grid").json()
    assert [r["label"] for r in mine["rows"]] == ["EC Mozambique"]
    # a plain user sees the companies open to everyone, but not the team-only one, nor its documents
    pu = TestClient(app)
    login(pu, "ec-user@test.local", old_world["pw"]["user"])
    seen = [r["label"] for r in pu.get(f"/api/engine/tools/{SLUG}/data/rows?page=grid&widget=w_grid").json()["rows"]]
    assert "EC Mozambique" in seen and "EC South Africa" in seen and "EC Team Only" not in seen
    # anyone who may see a document may set its date or file its renewal (edit: seen); a team-only company's is not theirs to see
    sa_doc = next(d for d in _tool_rows("documents") if d["company"] == sa["id"] and d["status"] == "expired")
    assert pu.patch(f"/api/engine/tools/{SLUG}/data/documents/{sa_doc['id']}", json={"values": {"expiry": (local_today() + timedelta(days=400)).isoformat()}}).status_code == 200
    assert pu.delete(f"/api/engine/tools/{SLUG}/data/documents/{sa_doc['id']}").status_code == 403  # changing is not deleting
    team_id = next(c["id"] for c in _tool_rows("companies") if c["name"] == "EC Team Only")
    hidden = next(d for d in _tool_rows("documents") if d["company"] == team_id)
    assert pu.get(f"/api/engine/tools/{SLUG}/data/documents/row/{hidden['id']}").status_code == 404
    assert pu.patch(f"/api/engine/tools/{SLUG}/data/documents/{hidden['id']}", json={"values": {"notes": "x"}}).status_code == 403
    assert ctrl.get(f"/api/engine/tools/{SLUG}/data/documents/row/{sa_doc['id']}").status_code == 404  # only theirs: not South Africa's
    # a renewal keeps the earlier file
    up = pu.post(f"/api/engine/tools/{SLUG}/data/documents/{sa_doc['id']}/files/file", files=[("files", ("renewed.pdf", certificate(), "application/pdf"))]).json()
    assert up["file"]["name"] == "renewed.pdf"
    up2 = pu.post(f"/api/engine/tools/{SLUG}/data/documents/{sa_doc['id']}/files/file", files=[("files", ("renewed2.pdf", certificate(), "application/pdf"))]).json()
    assert [e["name"] for e in up2["file"]["earlier"]] == ["renewed.pdf"]


def test_mail_reminders_and_counts(old_world, db, owner):
    CI.run_at_start(db, force=True)
    db.commit()
    moz_doc = next(d for d in _tool_rows("documents") if d["status"] == "expiring")
    info = owner.get(f"/api/engine/tools/{SLUG}/data/documents/{moz_doc['id']}/mail?page=documents&widget=w_docs&action=0").json()
    assert info["suggested"]["email"] == "ec-ctrl@test.local" and any(x.get("email") == "finance.person@cbreexcellerate.com" for x in info["recipients"])
    r = owner.post(f"/api/engine/tools/{SLUG}/data/documents/{moz_doc['id']}/mail", json={"page": "documents", "widget": "w_docs", "action": 0, "to": "ec-ctrl@test.local", "message": "Please renew this."})
    assert r.status_code == 200
    ctrl = old_world["ctrl"]
    assert db.query(CexaMessage).filter(CexaMessage.user_id == ctrl.id, CexaMessage.text.like("%Please renew%")).count() == 1
    bad = owner.post(f"/api/engine/tools/{SLUG}/data/documents/{moz_doc['id']}/mail", json={"page": "documents", "widget": "w_docs", "action": 0, "to": "stranger@gmail.com", "message": "hi"})
    assert bad.status_code == 400
    # reminders: not again for the stages the old reminders already sent; the new ones once
    before = db.query(CexaMessage).filter(CexaMessage.kind == "needs_you", CexaMessage.meta.isnot(None)).count()
    first = EC.remind(db)
    db.commit()
    again = EC.remind(db)
    db.commit()
    assert first["messages"] >= 1 and again["messages"] == 0
    assert db.query(CexaMessage).filter(CexaMessage.kind == "needs_you").count() > before
    # the menu count and Home read the tool
    assert owner.get("/api/home/counts").json()["docs_due"] >= 2
    assert EC.counts(db)["expired"] >= 1


def test_the_engine_rules_on_their_own(tmp_path):
    from cbx.engine.store import expiry_status

    today = local_today()
    cc = {"date": "expiry", "warn": "warn", "required": "required", "never": "never", "file": "file", "off": "off"}
    assert expiry_status(cc, {"expiry": (today + timedelta(days=100)).isoformat(), "warn": 90}) == "ok"
    assert expiry_status(cc, {"expiry": (today + timedelta(days=90)).isoformat(), "warn": 90}) == "expiring"
    assert expiry_status(cc, {"expiry": (today - timedelta(days=1)).isoformat()}) == "expired"
    assert expiry_status(cc, {}) == "missing" and expiry_status(cc, {"file": {"id": "x"}}) == "needs_date"
    assert expiry_status(cc, {"required": False}) == "not_required" and expiry_status(cc, {"never": True}) == "no_expiry" and expiry_status(cc, {"off": True}) == "archived"
    # the loader says what is wrong with a grid or a rule in plain words
    from tests.test_builder_types import _tool

    data = {"a": {"columns": [{"key": "n", "type": "text"}]}, "b": {"columns": [{"key": "a", "type": "link", "table": "a"}, {"key": "s", "type": "calc", "calc": {"fn": "expiry", "date": "a"}}],
            "scope": "linked", "link": "n", "open": {"field": "a", "value": "x"}, "edit": "anyone"}}
    pages = {"p": {"title": "page.p.title", "layout": {"sections": [{"id": "s", "columns": [{"width": 12, "widgets": [{"id": "g", "type": "grid", "source": {"data": "b"}, "rows": {"data": "a"}, "cols": {"data": "a"}, "cell": {}}]}]}]}}}
    _tool(tmp_path, "badgrid", data=data, pages=pages, flows={})
    errs = " | ".join(loader.load_all(tmp_path).broken["badgrid"].errors)
    for needle in ("status of a dated document needs its date column", "whoever may see the row it links to", "everyone sees the rows where", "who may change a row", "cell.row"):
        assert needle in errs, needle
