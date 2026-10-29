"""1.0.38 — Entity Compliance: files in the company's own folder (no library copy), the old files moved
there once; one company for one person ("only Mozambique"); the responsible person is an account
(picked or created); mail about one document, and the Mail page's compose and outbox."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cbx import mail, mailbox, state
from cbx.config import settings
from cbx.db import SessionLocal
from cbx.main import app
from cbx.models import CexaMessage, Folder, MailLog, StoredFile, Tool, ToolAccess, User, WatchedDoc
from cbx.models.docs import Entity, EntityAccess
from cbx.warehouse import storage
from tests._pdfs import certificate
from tests.conftest import _make, _uid, login
from tests.test_docwatch import _cell, _company, _delete_company, _upload

ROOT = "Tools/Entity Compliance/"


def _fresh_user(owner, email, name, tools=()):
    """A user with the given tools, signed in."""
    ids = [t["id"] for t in owner.get("/api/users").json()["tools"] if t["slug"] in tools]
    pw = _make(owner, email, name, "user", tools=ids)
    c = TestClient(app)
    login(c, email, pw)
    return c


# ----------------------------------------------------------------------------------------------- files


def test_a_filed_certificate_lives_in_the_companys_folder_and_nowhere_else(owner, db):
    page = _company(owner, "Mozambique (files)")
    eid = page["entity"]["id"]
    cell = _cell(page, "Fire Certificate")
    r = _upload(owner, cell["id"], "fire.pdf", certificate())
    assert r.status_code == 200, r.text
    fid = r.json()["file_id"]
    f = db.get(StoredFile, fid)
    folder = db.get(Folder, f.folder_id)
    assert folder.path == ROOT + "Mozambique (files)/Fire Certificate" and folder.kind == "tool" and folder.entity_id == eid
    assert f.rel_path.startswith(ROOT + "Mozambique (files)/Fire Certificate/") and storage.abs_path(f).exists()
    assert f.meta["entity_id"] == eid and f.meta["doc_id"] == cell["id"] and f.source == "docwatch"
    # no copy in the shared library — a certificate belongs to its company
    assert db.query(StoredFile).filter(StoredFile.library_copy_of == fid).count() == 0
    # a renewal goes to the same folder; the first file stays there as history
    r = _upload(owner, cell["id"], "fire-2027.pdf", certificate("Valid until: 30/06/2028"))
    assert r.status_code == 200
    f2 = db.get(StoredFile, r.json()["file_id"])
    assert db.get(Folder, f2.folder_id).id == folder.id and storage.abs_path(f).exists()
    # the plan says everything is where it should be
    assert owner.get("/api/docwatch/storage").json()["plan"]["misplaced"] == 0
    _delete_company(owner, eid)


def test_old_files_are_moved_into_company_folders_once_and_the_library_copies_go(owner, db):
    from cbx.docwatch import housekeeping as HK

    page = _company(owner, "Botswana (old files)")
    eid = page["entity"]["id"]
    cell = _cell(page, "Tax Clearance")
    me = db.query(User).filter(User.email == "owner@test.local").one()
    # the way it used to be filed: the uploader's Work folder + a shared-library copy
    old = storage.store(db, me, "tax-old.pdf", certificate(), sub="Work", mirror=True, meta={"purpose": "docwatch"})
    doc = db.get(WatchedDoc, cell["id"])
    doc.file_id = old.id
    doc.history = [{"file_id": None, "file_name": "typed", "expiry": None}]
    db.commit()
    assert db.query(StoredFile).filter(StoredFile.library_copy_of == old.id).count() == 1
    before = HK.plan(db)
    assert before["misplaced"] >= 1 and before["library_copies"] >= 1
    out = HK.relocate_files(db)
    db.commit()
    assert out["moved"] >= 1 and out["library_copies_removed"] >= 1 and out["failed"] == 0
    db.expire_all()
    f = db.get(StoredFile, old.id)
    assert f.rel_path.startswith(ROOT + "Botswana (old files)/Tax Clearance/") and storage.abs_path(f).exists()
    assert db.query(StoredFile).filter(StoredFile.library_copy_of == old.id).count() == 0
    assert f.meta["entity_id"] == eid and f.meta["doc_id"] == doc.id
    # a second run has nothing to do and writes nothing
    assert HK.relocate_files(db) == {"moved": 0, "library_copies_removed": 0, "failed": 0, "files": HK.relocate_files(db)["files"]}
    rows = owner.get("/api/audit?action=docwatch_files_relocated").json()["rows"]
    assert rows and "moved into Tools/Entity Compliance" in rows[0]["detail"]
    # the admin endpoint reports the same and can run it
    r = owner.post("/api/docwatch/storage/relocate")
    assert r.status_code == 200 and r.json()["plan"]["misplaced"] == 0
    # a rename moves the folder with the company
    r = owner.patch(f"/api/docwatch/entities/{eid}", json={"name": "Botswana Holdings"})
    assert r.status_code == 200, r.text
    db.expire_all()
    f = db.get(StoredFile, old.id)
    assert f.rel_path.startswith(ROOT + "Botswana Holdings/Tax Clearance/") and storage.abs_path(f).exists()
    assert db.query(Folder).filter(Folder.path == ROOT + "Botswana (old files)").count() == 0
    _delete_company(owner, eid)


# ----------------------------------------------------------------------------------------------- one company for one person


def test_one_company_for_one_person(owner, db):
    moz = _company(owner, "Mozambique (grant)")
    bots = _company(owner, "Botswana (grant)")
    cell = _cell(moz, "Fire Certificate")
    assert _upload(owner, cell["id"], "fire.pdf", certificate()).status_code == 200
    fid = db.get(WatchedDoc, cell["id"]).file_id
    # a brand-new person with NO tools: given Mozambique "only", they get the tool and see exactly it
    devin = _fresh_user(owner, "devin@test.local", "Devin Puckree")
    duid = _uid(owner, "devin@test.local")
    assert devin.get("/api/docwatch").status_code == 403  # no tool yet
    r = owner.post(f"/api/docwatch/entities/{moz['entity']['id']}/access", json={"user_id": duid, "only": True})
    assert r.status_code == 200, r.text
    assert r.json()["added"] and any(p["id"] == duid and p["only"] for p in r.json()["people"])
    g = devin.get("/api/docwatch").json()
    assert [e["name"] for e in g["entities"]] == ["Mozambique (grant)"]
    assert devin.get(f"/api/docwatch/entities/{moz['entity']['id']}").status_code == 200
    assert devin.get(f"/api/docwatch/entities/{bots['entity']['id']}").status_code == 403
    # …and can open the company's file, and nothing else's
    assert devin.get(f"/api/docwatch/{cell['id']}/download").status_code == 200
    assert devin.get(f"/api/warehouse/files/{fid}/download").status_code == 200
    # a user on the ordinary rule (everyone + team) still sees both; given Mozambique too, nothing is taken away
    thandi_tools = [t["id"] for t in owner.get("/api/users").json()["tools"] if t["slug"] == "doc-watch"]
    pw = _make(owner, "thandi2@test.local", "Thandi Two", "user", tools=thandi_tools)
    thandi = TestClient(app)
    login(thandi, "thandi2@test.local", pw)
    names = {e["name"] for e in thandi.get("/api/docwatch").json()["entities"]}
    assert {"Mozambique (grant)", "Botswana (grant)"} <= names
    tuid = _uid(owner, "thandi2@test.local")
    r = owner.post(f"/api/docwatch/entities/{moz['entity']['id']}/access", json={"user_id": tuid})
    assert r.status_code == 200 and not any(p["id"] == tuid and p["only"] for p in r.json()["people"])
    assert {"Mozambique (grant)", "Botswana (grant)"} <= {e["name"] for e in thandi.get("/api/docwatch").json()["entities"]}
    # flip Thandi to "only their companies": now just Mozambique
    r = owner.post(f"/api/docwatch/entities/{moz['entity']['id']}/access/{tuid}/scope", json={"only": True})
    assert r.status_code == 200 and r.json()["changed"]
    assert [e["name"] for e in thandi.get("/api/docwatch").json()["entities"]] == ["Mozambique (grant)"]
    # take it away from Devin: he has "only" and now nothing — the grid is empty, the company refused
    r = owner.delete(f"/api/docwatch/entities/{moz['entity']['id']}/access/{duid}")
    assert r.status_code == 200 and r.json()["removed"]
    assert devin.get("/api/docwatch").json()["entities"] == []
    assert devin.get(f"/api/docwatch/{cell['id']}/download").status_code == 403
    # a user can't hand out companies; C-EXA can't be given one
    assert thandi.post(f"/api/docwatch/entities/{moz['entity']['id']}/access", json={"user_id": duid}).status_code == 403
    cexa = db.query(User).filter(User.is_axl.is_(True)).one()
    r = owner.post(f"/api/docwatch/entities/{moz['entity']['id']}/access", json={"user_id": cexa.id})
    assert r.status_code == 400 and "C-EXA" in r.json()["detail"]
    rows = owner.get("/api/audit?action=docwatch_access_granted").json()["rows"]
    assert rows and "Devin Puckree" in " ".join(x["detail"] for x in rows)
    for e in (moz, bots):
        _delete_company(owner, e["entity"]["id"])


# ----------------------------------------------------------------------------------------------- the responsible person is an account


def test_responsible_person_is_an_account_picked_or_created(owner, user, db):
    page = _company(owner, "Mozambique (people)", responsible="Somebody Written Down")
    eid = page["entity"]["id"]
    info = owner.get(f"/api/docwatch/entities/{eid}/access").json()
    assert info["can_add_people"] and info["responsible"] == "Somebody Written Down" and info["responsible_user_id"] is None
    # create the account from the grid: name + email → temp password once, tool + company granted, linked
    r = owner.post(f"/api/docwatch/entities/{eid}/responsible", json={"name": "Imraan Controller", "email": "imraan@test.local"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["temp_password"] and out["created"]["email"] == "imraan@test.local"
    assert out["entity"]["responsible"] == "Imraan Controller" and out["entity"]["responsible_user_id"] == out["created"]["id"]
    assert any(p["id"] == out["created"]["id"] and p["responsible"] and p["only"] for p in out["people"])
    # the new person signs in, must change the password, then sees exactly this company
    imraan = TestClient(app)
    login(imraan, "imraan@test.local", out["temp_password"])
    assert [e["name"] for e in imraan.get("/api/docwatch").json()["entities"]] == ["Mozambique (people)"]
    # the same email again is refused; a bad email is refused; picking an existing account works and grants
    assert owner.post(f"/api/docwatch/entities/{eid}/responsible", json={"name": "X Y", "email": "imraan@test.local"}).status_code == 400
    assert owner.post(f"/api/docwatch/entities/{eid}/responsible", json={"name": "X Y", "email": "not-an-address"}).status_code == 400
    tuid = _uid(owner, "thandi@test.local")
    r = owner.post(f"/api/docwatch/entities/{eid}/responsible", json={"user_id": tuid})
    assert r.status_code == 200 and r.json()["entity"]["responsible_user_id"] == tuid and r.json()["entity"]["responsible"] == "Thandi Mokoena"
    assert db.query(EntityAccess).filter(EntityAccess.entity_id == eid, EntityAccess.user_id == tuid).count() == 1
    # a plain user can't set it
    assert user.post(f"/api/docwatch/entities/{eid}/responsible", json={"user_id": tuid}).status_code == 403
    # the name-only import path is caught up by the backfill: a name that matches one account exactly
    from cbx.docwatch import housekeeping as HK

    e2 = _company(owner, "Botswana (people)", responsible="Thandi Mokoena")
    assert e2["entity"]["responsible_user_id"] is None or e2["entity"]["responsible_user_id"] == tuid
    n = HK.link_responsible(db)
    db.commit()
    ent2 = db.get(Entity, e2["entity"]["id"])
    assert ent2.responsible_user_id == tuid
    # ambiguous or unknown names are left alone
    e3 = _company(owner, "Angola (people)", responsible="Nobody Known")
    HK.link_responsible(db)
    db.commit()
    assert db.get(Entity, e3["entity"]["id"]).responsible_user_id is None
    for e in (eid, e2["entity"]["id"], e3["entity"]["id"]):
        _delete_company(owner, e)


# ----------------------------------------------------------------------------------------------- mail


@pytest.fixture()
def fake_smtp(monkeypatch):
    """Mail 'works': mail.send is captured, nothing leaves the machine."""
    sent = []

    def fake_send(to, subject, body, *, html=None, reply_to=""):
        ready, why = mail.configured()  # the real switch still counts
        if not ready:
            return mail.Result(False, why, tuple(to))
        sent.append({"to": list(to), "subject": subject, "body": body, "reply_to": reply_to})
        return mail.Result(True, f"Sent to {', '.join(to)}.", tuple(to))

    monkeypatch.setattr(settings, "mail_enabled", True)
    monkeypatch.setattr(settings, "smtp_host", "smtp.test.local")
    monkeypatch.setattr(settings, "mail_from", "cbx-connect@test.local")
    monkeypatch.setattr(settings, "public_url", "https://cbx.test.local")
    monkeypatch.setattr(mail, "send", fake_send)
    return sent


def test_mail_about_one_document_carries_the_message_the_document_and_the_links(owner, user, db, fake_smtp):
    page = _company(owner, "Mozambique (mail)")
    eid = page["entity"]["id"]
    cell = _cell(page, "Fire Certificate")
    tuid = _uid(owner, "thandi@test.local")
    owner.post(f"/api/docwatch/entities/{eid}/responsible", json={"user_id": tuid})
    assert owner.patch(f"/api/docwatch/{cell['id']}", json={"expiry": "2030-01-15"}).status_code == 200
    # the dialog's information: the responsible person is suggested, the link points at the document
    info = user.get(f"/api/docwatch/{cell['id']}/mail").json()
    assert info["suggested"]["email"] == "thandi@test.local" and info["mail_ready"] is True
    assert info["link"] == f"https://cbx.test.local/#/doc-watch-entity?id={eid}&doc={cell['id']}"
    # Devin (a user) presses Mail and types a message
    r = user.post(f"/api/docwatch/{cell['id']}/mail", json={"to": "thandi@test.local", "message": "Please send the renewed fire certificate when it arrives."})
    assert r.status_code == 200, r.text
    assert "Thandi" in r.json()["message"]
    assert len(fake_smtp) == 1
    m = fake_smtp[0]
    assert m["to"] == ["thandi@test.local"] and m["subject"] == "Fire Certificate — Mozambique (mail)" and m["reply_to"] == "thandi@test.local"
    body = m["body"]
    assert "Thandi Mokoena wrote:" in body and "Please send the renewed fire certificate" in body
    assert "Document: Fire Certificate for Mozambique (mail)" in body and "expires on 15 January 2030" in body
    assert f"Open it in CBX-Connect: https://cbx.test.local/#/doc-watch-entity?id={eid}&doc={cell['id']}" in body
    assert "CBX-Connect: https://cbx.test.local" in body and "Sent from CBX-Connect by Thandi Mokoena" in body
    # recorded: outbox row, audit, and the document's own sent list
    rows = db.query(MailLog).filter(MailLog.ref == f"doc:{cell['id']}").all()
    assert len(rows) == 1 and rows[0].ok and rows[0].kind == "docwatch"
    assert user.get(f"/api/docwatch/{cell['id']}/mail").json()["sent"][0]["to"] == "thandi@test.local"
    assert owner.get("/api/audit?action=docwatch_mailed").json()["rows"][0]["target"] == "Fire Certificate — Mozambique (mail)"
    # a stranger's address is refused before anything is sent; an empty message too
    r = user.post(f"/api/docwatch/{cell['id']}/mail", json={"to": "someone@elsewhere.example", "message": "hello there"})
    assert r.status_code == 400 and "not someone CBX knows" in r.json()["detail"] and len(fake_smtp) == 1
    assert user.post(f"/api/docwatch/{cell['id']}/mail", json={"to": "thandi@test.local", "message": ""}).status_code == 400
    # the company's own domain is fine even without an account
    r = user.post(f"/api/docwatch/{cell['id']}/mail", json={"to": "finance@test.local", "message": "For your files."})
    assert r.status_code == 200 and fake_smtp[-1]["to"] == ["finance@test.local"]
    # to a colleague with an account: an in-app copy lands in their Messages (sent by the owner here, so it isn't to self)
    n0 = db.query(CexaMessage).filter(CexaMessage.user_id == tuid).count()
    r = owner.post(f"/api/docwatch/{cell['id']}/mail", json={"to_user_id": tuid, "message": "Please chase the renewal."})
    assert r.status_code == 200 and "Messages" in r.json()["message"]
    db.expire_all()
    note = db.query(CexaMessage).filter(CexaMessage.user_id == tuid).order_by(CexaMessage.id.desc()).first()
    assert db.query(CexaMessage).filter(CexaMessage.user_id == tuid).count() == n0 + 1 and "Please chase the renewal." in note.text and note.link == f"#/doc-watch-entity?id={eid}&doc={cell['id']}"
    _delete_company(owner, eid)


def test_mailer_compose_and_sent(owner, admin, user, db, fake_smtp):
    d = admin.get("/api/mailer/sent").json()
    assert d["ready"] is True
    assert any(r["email"] == "thandi@test.local" for r in d["recipients"]) and d["domain"] == "test.local"
    assert user.get("/api/mailer/sent").status_code == 403
    # compose: known people only, subject and message required
    r = admin.post("/api/mailer/compose", json={"to": ["thandi@test.local", "owner@test.local"], "subject": "Month-end", "message": "Please file your certificates by Friday."})
    assert r.status_code == 200, r.text
    m = fake_smtp[-1]
    assert sorted(m["to"]) == ["owner@test.local", "thandi@test.local"] and m["subject"] == "Month-end"
    assert "Please file your certificates by Friday." in m["body"] and "CBX-Connect: https://cbx.test.local" in m["body"] and "Sent from CBX-Connect by Arnab Admin" in m["body"]
    assert admin.post("/api/mailer/compose", json={"to": ["stranger@elsewhere.example"], "subject": "x y", "message": "hello"}).status_code == 400
    assert admin.post("/api/mailer/compose", json={"to": ["thandi@test.local"], "subject": "", "message": "hello"}).status_code == 400
    assert user.post("/api/mailer/compose", json={"to": ["thandi@test.local"], "subject": "hi", "message": "hello"}).status_code == 403
    rows = admin.get("/api/mailer/sent").json()["mail"]
    assert rows[0]["subject"] == "Month-end" and rows[0]["ok"] and rows[0]["kind"] == "compose" and rows[0]["by"] == "Arnab Admin"
    # the mailer off: compose is refused and the outbox shows why
    settings.mail_enabled = False
    r = admin.post("/api/mailer/compose", json={"to": ["thandi@test.local"], "subject": "Later", "message": "This one can't go."})
    assert r.status_code == 200 and r.json()["ok"] is False and "switched off" in r.json()["message"]
    rows = admin.get("/api/mailer/sent").json()["mail"]
    assert rows[0]["subject"] == "Later" and rows[0]["ok"] is False and "switched off" in rows[0]["detail"]


def test_links_point_where_admins_sign_in_from(owner, db, monkeypatch):
    """No CBX_PUBLIC_URL: the address the last admin used (behind the tunnel: https, via X-Forwarded-Proto)."""
    monkeypatch.setattr(settings, "public_url", "")
    c = TestClient(app, base_url="http://testserver")
    from tests.conftest import STRONG

    r = c.post("/api/auth/login", json={"email": "owner@test.local", "password": STRONG}, headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "cbx.tunnel.example"})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert state.get(db, state.PUBLIC_URL) == "https://cbx.tunnel.example"
    assert state.public_url(db) == "https://cbx.tunnel.example"
    monkeypatch.setattr(settings, "public_url", "https://cbx.company.example/")
    assert state.public_url(db) == "https://cbx.company.example"
