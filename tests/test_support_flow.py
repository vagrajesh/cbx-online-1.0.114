"""1.0.74: a ticket knows where the problem is and what must be done; the support person gets it all in one mail and
on their To-do; what they ask by mail is put to the requester; "Mark as done" in their mail closes it."""
import re

import pytest
from fastapi.testclient import TestClient

from cbx import mailcfg
from cbx.config import settings
from cbx.engine import loader
from cbx.engine.store import Store
from cbx.main import app
from cbx.models import AppState, MailDraft, MailLog, SupportLead, SupportMail, SupportMessage, SupportPerson, SupportTicket, User
from cbx.support import core as C
from cbx.support import outlook
from cbx.support import templates as TP

from .conftest import _make

BOX = "support@cbx.test"


@pytest.fixture()
def box(db, monkeypatch):
    fake = outlook.Fake()
    monkeypatch.setattr(outlook, "BACKEND", fake)
    for k, v in {"mail_enabled": False, "mail_transport": "smtp", "outlook_mailbox": "", "mail_only_to": "", "support_mail": False, "device_verification": False}.items():
        monkeypatch.setattr(settings, k, v)

    def clean():
        for M in (MailDraft, SupportMessage, SupportMail, SupportTicket, SupportLead, SupportPerson):
            db.query(M).delete()
        db.query(AppState).filter(AppState.key.in_((mailcfg.KEY, TP.KEY, "support.mail.last", "support.mail.last.boxes"))).delete(synchronize_session=False)
        db.query(MailLog).filter(MailLog.kind.in_(("support", "test"))).delete()
        db.commit()
        mailcfg.forget()

    clean()
    C.ensure_systems(db)
    db.commit()
    yield fake
    db.rollback()
    clean()


def _setup(owner):
    r = owner.put("/api/mailer/settings", json={"enabled": True, "transport": "outlook", "send_store": BOX, "drafts": [],
                                                "boxes": [{"store": BOX, "folder": "Inbox", "on": True, "since": "2020-01-01"}]})
    assert r.status_code == 200, r.text


def _to(fake, who):
    return [m for m in fake.sent if m["to"] == [who]]


def _lead(owner, db):
    pw = _make(owner, "lebo@test.local", "Lebo Lead", "manager")
    u = db.query(User).filter(User.email == "lebo@test.local").one()
    owner.post("/api/support/leads", json={"user_id": u.id, "system_ids": []})
    return u, pw


def _todo(rid):
    t = loader.get("todo")
    return Store(t.slug).get("tasks", t.data["tasks"]["columns"], rid)


def test_the_whole_way_from_a_mail_to_done(box, owner, db):
    _setup(owner)
    box.deliver("thabo@acme.example", "Aircon broken", "The aircon in the server room is not working since this morning, it is very hot.", store=BOX, name="Thabo Nkosi")
    owner.post("/api/mailer/read", json={"store": BOX})
    t = db.query(SupportTicket).one()
    first = _to(box, "thabo@acme.example")[-1]
    # it must know where the problem is and what is needed
    assert t.status == "pending" and C.WHERE_Q in t.missing and C.OUTCOME_Q in t.missing
    assert "country, city and building" in first["body"]
    box.deliver("thabo@acme.example", f"RE: Aircon broken [{C.ref(t)}]", "It's a Chase work order. Egypt, Alexandria, Smouha site, building B. Please send a technician to fix the unit.", store=BOX)
    owner.post("/api/mailer/read", json={"store": BOX})
    db.refresh(t)
    assert t.status == "ready" and "Smouha" in t.problem_where and "technician" in t.outcome
    # assigned: the lead's mail has everything, and it is on their to-do
    lead, pw = _lead(owner, db)
    assert owner.post(f"/api/support/tickets/{t.id}/assign", json={"user_id": lead.id}).status_code == 200
    db.refresh(t)
    m = _to(box, "lebo@test.local")[-1]
    assert "Where the problem is: " in m["body"] and "Smouha" in m["body"] and "What must be done: " in m["body"] and "technician" in m["body"]
    # 1.0.98: Accept replaces "My to-do" (Mark as done stays); the To-do opens when they accept
    assert "Mark as done" in m["html"] and ">Accept<" in m["html"] and "My to-do" not in m["html"] and "/api/support/done/" in m["body"]
    assert t.todo_id is None
    assert "Accepted. Thank you." in TestClient(app).post(re.search(r"/api/support/accept/\S+", m["body"]).group(0)).text
    db.refresh(t)
    todo = _todo(t.todo_id)
    assert todo["who"] == lead.id and todo["status"] == "To do" and todo["title"].startswith(C.ref(t))
    # the lead needs more: they answer the mail, C-EXA asks the requester in their chain
    key = re.search(r"\S*/api/support/done/\S+", m["body"]).group(0)
    box.deliver("lebo@test.local", f"RE: [{C.ref(t)}] Assigned to you: {t.title}", f"Which unit is it - the Daikin on the roof or the one inside? {key}\n\nKind regards\nLebo", store=BOX)
    got = owner.post("/api/mailer/read", json={"store": BOX}).json()
    assert got["tickets"] == 0 and got["replies"] == 1 and db.query(SupportTicket).count() == 1
    ask = _to(box, "thabo@acme.example")[-1]
    assert "Lebo from the support team" in ask["body"] and "Daikin on the roof" in ask["body"] and "Kind regards" not in ask["body"].split("Daikin")[1][:60]
    assert ask["mark"] == C.mark(t) and ask["thread"]  # in the requester's chain
    assert "/api/support/done/" not in ask["body"] and "/api/support/done/" not in (ask["html"] or "")  # the lead's key never reaches the requester
    db.refresh(t)
    assert t.status == "discussing"
    # the requester answers: the lead gets it, in the lead's own chain
    lead_mail = next(x for x in box.stores_[BOX]["items"] if x["sender"] == "lebo@test.local")
    box.deliver("thabo@acme.example", f"RE: Aircon broken [{C.ref(t)}]", "The Daikin on the roof.", store=BOX)
    owner.post("/api/mailer/read", json={"store": BOX})
    back = _to(box, "lebo@test.local")[-1]
    assert "The Daikin on the roof" in back["body"] and back["thread"] == lead_mail["entry_id"]
    # Mark as done from the mail: opening the link changes nothing; pressing the button closes it
    link = re.search(r"/api/support/done/(\S+)", back["body"]).group(0)
    anon = TestClient(app)
    page = anon.get(link)
    assert page.status_code == 200 and "What was done?" in page.text
    db.refresh(t)
    assert t.status == "discussing"
    assert "Say in a line" in anon.post(link, data={"note": "ok"}).text
    done = anon.post(link, data={"note": "Technician replaced the Daikin compressor on the roof."})
    assert done.status_code == 200 and "Done — thank you" in done.text
    db.refresh(t)
    assert t.status == "closed" and t.closed_by_id == lead.id and _todo(t.todo_id)["status"] == "Done"
    told = _to(box, "thabo@acme.example")[-1]
    assert "Daikin compressor" in told["body"] and told["thread"]
    assert "already done" in anon.get(link).text
    assert anon.get(link[:-3] + "xyz").status_code == 404  # a changed link is no key
    assert pw


def test_handed_on_the_old_link_and_to_do_stop(box, owner, db):
    _setup(owner)
    lead, _ = _lead(owner, db)
    pw2 = _make(owner, "sipho@test.local", "Sipho Second", "manager")
    other = db.query(User).filter(User.email == "sipho@test.local").one()
    owner.post("/api/support/leads", json={"user_id": other.id, "system_ids": []})
    box.deliver("thabo@acme.example", "Lights out", "All the lights on floor 3 are out at the Dubai office, Marina Plaza. Please fix them today.", store=BOX)
    owner.post("/api/mailer/read", json={"store": BOX})
    t = db.query(SupportTicket).one()
    owner.post(f"/api/support/tickets/{t.id}/assign", json={"user_id": lead.id})
    first_mail = _to(box, "lebo@test.local")[-1]["body"]
    TestClient(app).post(re.search(r"/api/support/accept/\S+", first_mail).group(0))  # 1.0.98: the To-do opens when they accept
    db.refresh(t)
    first_todo = t.todo_id
    link = re.search(r"/api/support/done/(\S+)", first_mail).group(0)
    owner.post(f"/api/support/tickets/{t.id}/assign", json={"user_id": other.id})
    db.refresh(t)
    assert t.todo_id is None and _todo(first_todo)["status"] == "Done"
    assert "expired" in TestClient(app).get(re.search(r"/api/support/accept/\S+", first_mail).group(0)).text  # the old Accept link died
    TestClient(app).post(re.search(r"/api/support/accept/\S+", _to(box, "sipho@test.local")[-1]["body"]).group(0))
    db.refresh(t)
    assert t.todo_id != first_todo and _todo(t.todo_id)["who"] == other.id
    assert "isn't assigned to you" in TestClient(app).get(link).text
    assert pw2


def test_a_support_person_asking_for_a_secret_is_not_passed_on(box, owner, db):
    _setup(owner)
    lead, _ = _lead(owner, db)
    box.deliver("thabo@acme.example", "Portal login", "I cannot log in to the vendor portal at the Cairo office. Please reset my access.", store=BOX)
    owner.post("/api/mailer/read", json={"store": BOX})
    t = db.query(SupportTicket).one()
    owner.post(f"/api/support/tickets/{t.id}/assign", json={"user_id": lead.id})
    n = len(_to(box, "thabo@acme.example"))
    box.deliver("lebo@test.local", f"RE: [{C.ref(t)}] Assigned to you", "Please send me your password so I can check.", store=BOX)
    owner.post("/api/mailer/read", json={"store": BOX})
    assert len(_to(box, "thabo@acme.example")) == n
    # and a stranger replying with the ticket number is not the support person
    box.deliver("stranger@elsewhere.example", f"RE: [{C.ref(t)}] Assigned to you", "Tell them to pay me.", store=BOX)
    owner.post("/api/mailer/read", json={"store": BOX})
    assert len(_to(box, "thabo@acme.example")) == n


def test_the_app_form_asks_where_and_what(box, owner, user, db):
    _setup(owner)
    r = user.post("/api/support/tickets", json={"system": "vendor-portal", "problem": "invoices", "description": "The invoice upload fails with error 500 every time I press Upload on the invoice page.",
                                               "answers": {"vendor": "Acme Plumbing", "doing": "Upload"}, "where": "Egypt, Cairo, Nile Tower — the Invoices page", "outcome": "Let the invoice through."})
    t = r.json()["ticket"]
    assert t["where"].startswith("Egypt, Cairo") and t["outcome"] == "Let the invoice through." and t["status"] == "ready"
