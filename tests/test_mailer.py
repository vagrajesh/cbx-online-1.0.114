"""1.0.70: the Mailer — every support mailbox's mail and what became of it, tickets by hand, mail settings
kept in CBX (no .env edit), and the owner's own mailbox beside them."""
import pytest
from fastapi.testclient import TestClient

from cbx import mailcfg, state
from cbx.config import settings
from cbx.main import app
from cbx.models import AppState, MailItem, MailLink, MailLog, SupportLead, SupportMail, SupportMessage, SupportPerson, SupportTicket, User
from cbx.support import outlook

from .conftest import _make, login

SUPPORT = "Support (CBX)"
TICKETS = "tickets@cbx.test"
PERSONAL = "edwin@home.example"


@pytest.fixture()
def box(db, monkeypatch):
    fake = outlook.Fake()
    fake.stores_ = {}
    fake.add_store(SUPPORT, "cbx-connect@cbx.test")
    fake.add_store(TICKETS)
    fake.add_store(PERSONAL)
    monkeypatch.setattr(outlook, "BACKEND", fake)
    monkeypatch.setattr(settings, "outlook_mailbox", "")
    monkeypatch.setattr(settings, "support_mail", False)
    monkeypatch.setattr(settings, "mail_enabled", False)
    monkeypatch.setattr(settings, "mail_only_to", "")
    monkeypatch.setattr(settings, "device_verification", False)

    def clean():
        for M in (SupportMessage, SupportMail, SupportTicket, SupportLead, SupportPerson, MailItem, MailLink):
            db.query(M).delete()
        db.query(AppState).filter(AppState.key.in_((mailcfg.KEY, "support.mail.last", "support.mail.last.boxes"))).delete(synchronize_session=False)
        db.query(MailLog).filter(MailLog.kind.in_(("support", "test"))).delete()
        db.commit()
        mailcfg.forget()

    clean()
    yield fake
    db.rollback()
    clean()


def _setup(owner, **over):
    body = {"enabled": True, "transport": "outlook", "send_store": SUPPORT, "boxes": [{"store": SUPPORT, "folder": "Inbox", "on": True, "since": "2020-01-01"}], **over}
    r = owner.put("/api/mailer/settings", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_mail_settings_live_in_cbx_not_env(box, owner, user, db):
    assert user.get("/api/mailer/settings").status_code == 403
    st = owner.get("/api/mailer/settings").json()
    assert {s["name"] for s in st["stores"]} == {SUPPORT, TICKETS, PERSONAL} and st["sending"]["ready"] is False
    r = _setup(owner)
    assert r["sending"]["ready"] is True and r["settings"]["send_address"] == "cbx-connect@cbx.test"
    # mail now goes out through Outlook from that mailbox — no restart
    t = owner.post("/api/mailer/test", json={}).json()
    assert t["ok"] and box.sent[-1]["subject"] == "CBX-Connect: test mail" and box.sent[-1]["to"] == ["owner@test.local"]
    assert not owner.post("/api/mailer/test", json={"to": "stranger@elsewhere.example"}).json()["ok"]  # the recipient rules still apply
    # someone's own mailbox can't be a support mailbox; an unknown one is refused
    owner.post("/api/mymail/link", json={"store": PERSONAL})
    assert owner.put("/api/mailer/settings", json={"enabled": True, "transport": "outlook", "send_store": SUPPORT, "boxes": [{"store": PERSONAL}]}).status_code == 400
    assert owner.put("/api/mailer/settings", json={"enabled": True, "transport": "outlook", "send_store": SUPPORT, "boxes": [{"store": "nope@x"}]}).status_code == 400
    # switched on without a date: read from now on, never the whole history
    r = _setup(owner, boxes=[{"store": SUPPORT, "on": True}, {"store": TICKETS, "on": False}])
    assert r["settings"]["boxes"][0]["since"] and r["settings"]["boxes"][1]["since"] is None


def test_each_mailbox_shows_what_became_of_every_mail(box, owner, db):
    _setup(owner)
    box.deliver("vendor@acme.example", "Vendor portal problem", "The vendor portal won't accept our invoice upload, error 500 on Upload, vendor Acme Plumbing.", store=SUPPORT)
    box.deliver("boss@co.example", "Automatic reply: away", "I am out of the office", store=SUPPORT)
    box.deliver("late@co.example", "Hello", "Something arrived after the last read.", store=SUPPORT)
    # the agents read the first two
    first_two = box.stores_[SUPPORT]["items"][:2]
    box.stores_[SUPPORT]["items"] = first_two
    got = owner.post("/api/mailer/read", json={"store": SUPPORT}).json()
    assert got["tickets"] == 1 and got["ignored"] == 1
    box.deliver("late@co.example", "Hello", "Something arrived after the last read, please help with the roof quote page.", store=SUPPORT)
    d = owner.get("/api/mailer").json()
    assert [b["store"] for b in d["boxes"]] == [SUPPORT] and d["mine"] is not None and d["can_settings"]
    mail = {m["subject"]: m for m in owner.get(f"/api/mailer/box?store={SUPPORT}").json()["mail"]}
    assert mail["Vendor portal problem"]["what"] == "ticket" and mail["Vendor portal problem"]["ticket"]["ref"].startswith("T-")
    assert mail["Automatic reply: away"]["what"] == "skipped" and "automatic" in mail["Automatic reply: away"]["why"]
    assert mail["Hello"]["what"] == "unread"
    # the agents skipped it, a person makes it a ticket anyway
    r = owner.post("/api/mailer/ticket", json={"entry_id": mail["Automatic reply: away"]["entry_id"]})
    assert r.status_code == 200
    assert owner.post("/api/mailer/ticket", json={"entry_id": mail["Automatic reply: away"]["entry_id"]}).status_code == 400  # not twice
    # a mail put on an existing ticket: a note when it isn't the requester's
    tid = mail["Vendor portal problem"]["ticket"]["id"]
    r = owner.post("/api/mailer/attach", json={"entry_id": mail["Hello"]["entry_id"], "ticket_id": tid})
    assert r.status_code == 200
    notes = [m.text for m in db.query(SupportMessage).filter(SupportMessage.ticket_id == tid, SupportMessage.who == "note").all()]
    assert any("late@co.example" in n and "roof quote" in n for n in notes)
    mail = {m["subject"]: m for m in owner.get(f"/api/mailer/box?store={SUPPORT}").json()["mail"]}
    assert mail["Hello"]["what"] == "attached" and mail["Automatic reply: away"]["what"] == "ticket"


def test_a_support_lead_sees_support_mailboxes_but_never_someone_s_own(box, owner, db):
    _setup(owner)
    owner.post("/api/mymail/link", json={"store": PERSONAL})
    mine = box.put(PERSONAL, "aunt@family.example", "Private", "Personal things")
    pw = _make(owner, "lebo@test.local", "Lebo Lead", "manager")
    owner.post("/api/support/leads", json={"user_id": db.query(User).filter(User.email == "lebo@test.local").one().id, "system_ids": []})
    c = TestClient(app)
    login(c, "lebo@test.local", pw)
    d = c.get("/api/mailer").json()
    assert [b["store"] for b in d["boxes"]] == [SUPPORT] and d["mine"] is None and not d["can_settings"]
    assert c.get("/api/mailer/settings").status_code == 403
    assert c.get(f"/api/mailer/mail?entry_id={mine['entry_id']}").status_code == 404
    assert c.post("/api/mailer/ticket", json={"entry_id": mine["entry_id"]}).status_code == 404
    assert c.get(f"/api/mailer/box?store={PERSONAL}").status_code == 404
    pw2 = _make(owner, "sipho@test.local", "Sipho Plain", "user")
    c2 = TestClient(app)
    login(c2, "sipho@test.local", pw2)
    assert c2.get("/api/mailer").status_code == 403


def test_a_closed_outlook_is_said_on_the_mailbox(box, owner, db, monkeypatch):
    _setup(owner)

    def boom(*a, **k):
        raise outlook.OutlookError("Outlook did not answer — is it open and signed in on this PC?")

    monkeypatch.setattr(box, "fetch_since", boom)
    got = owner.post("/api/mailer/read", json={"store": SUPPORT}).json()
    assert "Outlook did not answer" in got["error"]
    b = owner.get("/api/mailer").json()["boxes"][0]
    assert "Outlook did not answer" in b["last"]
    assert "Outlook did not answer" in owner.get(f"/api/mailer/box?store={SUPPORT}").json()["error"]
