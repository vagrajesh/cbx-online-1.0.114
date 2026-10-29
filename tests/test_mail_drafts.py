"""1.0.71: mail a person checks before it goes (Mailer → Drafts, and Outlook → Drafts), ticket mails answered in
the requester's own thread or with the earlier messages underneath, and the wording of every ticket mail
editable on the Mailer."""
import pytest

from cbx import mailcfg
from cbx.config import settings
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


def _setup(owner, drafts):
    r = owner.put("/api/mailer/settings", json={"enabled": True, "transport": "outlook", "send_store": BOX, "drafts": drafts,
                                                "boxes": [{"store": BOX, "folder": "Inbox", "on": True, "since": "2020-01-01"}]})
    assert r.status_code == 200, r.text
    return r.json()


def _to(fake, who):
    return [m for m in fake.sent if m["to"] == [who]]


def test_ticket_mail_waits_as_a_draft_in_the_requester_s_thread_then_a_person_sends_it(box, owner, user, db):
    assert _setup(owner, ["support", "nonsense"])["settings"]["drafts"] == ["support"]
    m = box.deliver("vendor@acme.example", "Portal broken", "It is broken.", store=BOX)
    assert owner.post("/api/mailer/read", json={"store": BOX}).json()["tickets"] == 1
    t = db.query(SupportTicket).one()
    assert t.status == "pending" and not _to(box, "vendor@acme.example")  # asked — but nothing went out
    # waiting in CBX and in Outlook → Drafts, as a reply in their own mail's thread
    assert owner.get("/api/mailer").json()["drafts"] == 1
    d = owner.get("/api/mailer/drafts").json()["waiting"]
    assert len(d) == 1 and d[0]["thread"] and d[0]["outlook"] and d[0]["ticket"]["ref"] == C.ref(t) and d[0]["to"] == "vendor@acme.example"
    (eid, od), = box.outlook_drafts.items()
    assert od["thread"] == m["entry_id"] and od["to"] == ["vendor@acme.example"] and od["subject"] == f"RE: Portal broken [{C.ref(t)}]"  # the chain's own subject
    assert "Earlier on this ticket" not in od["body"]  # the chain is in the thread itself
    # only the owner and admins manage drafts
    assert user.get("/api/mailer/drafts").status_code == 403
    # changed: the Outlook draft is replaced by the changed one
    r = owner.put(f"/api/mailer/drafts/{d[0]['id']}", json={"subject": d[0]["subject"], "text": "Hi there,\n\nWhich vendor and which page, please?"})
    assert r.status_code == 200 and r.json()["draft"]["edited"]
    assert eid not in box.outlook_drafts and len(box.outlook_drafts) == 1
    assert "Which vendor and which page" in next(iter(box.outlook_drafts.values()))["body"]
    # sent by a person: it goes, the ticket's message shows as mailed
    r = owner.post(f"/api/mailer/drafts/{d[0]['id']}/send", json={}).json()
    assert r["ok"] and r["draft"]["status"] == "sent" and not box.outlook_drafts
    assert "Which vendor and which page" in _to(box, "vendor@acme.example")[-1]["body"]
    assert db.query(SupportMessage).filter(SupportMessage.ticket_id == t.id, SupportMessage.who == "cexa").one().mailed
    assert owner.post(f"/api/mailer/drafts/{d[0]['id']}/send", json={}).status_code == 400  # never twice
    assert any(x["subject"].startswith(f"[{C.ref(t)}]") and x["ok"] for x in owner.get("/api/mailer/sent").json()["mail"])


def test_thrown_away_or_handled_in_outlook(box, owner, db):
    _setup(owner, ["support"])
    box.deliver("a@acme.example", "Help", "Broken.", store=BOX)
    box.deliver("b@acme.example", "Help too", "Also broken.", store=BOX)
    owner.post("/api/mailer/read", json={"store": BOX})
    w = owner.get("/api/mailer/drafts").json()["waiting"]
    assert len(w) == 2 and len(box.outlook_drafts) == 2
    a = next(x for x in w if x["to"] == "a@acme.example")
    b = next(x for x in w if x["to"] == "b@acme.example")
    assert owner.post(f"/api/mailer/drafts/{a['id']}/discard", json={}).json()["draft"]["status"] == "discarded"
    # b was sent (or deleted) straight from Outlook: CBX notices
    eid = db.get(MailDraft, b["id"]).outlook_entry_id
    box.outlook_drafts.pop(eid)
    got = owner.get("/api/mailer/drafts").json()
    assert got["waiting"] == [] and {x["status"] for x in got["decided"]} == {"discarded", "outlook"}
    assert not [m for m in box.sent if m["to"][0].endswith("acme.example")]


def test_codes_and_tests_never_wait_and_unticked_mail_goes_straight(box, owner, db):
    _setup(owner, ["support", "reminder", "flow"])
    assert owner.post("/api/mailer/test", json={}).json()["ok"] and box.sent[-1]["subject"] == "CBX-Connect: test mail"
    _setup(owner, [])
    m = box.deliver("vendor@acme.example", "Portal broken", "It is broken.", store=BOX)
    owner.post("/api/mailer/read", json={"store": BOX})
    sent = _to(box, "vendor@acme.example")
    assert len(sent) == 1 and sent[0]["thread"] == m["entry_id"] and not box.outlook_drafts
    assert "Hi vendor" in sent[0]["body"] and sent[0]["html"] and "CBX-Connect Support" in sent[0]["html"]


def test_app_ticket_mails_chain_onto_the_last_one_sent_to_the_requester_never_the_lead_s(box, owner, user, db):
    _setup(owner, [])
    r = user.post("/api/support/tickets", json={"system": "vendor-portal", "problem": "invoices", "description": "the portal is broken",
                                                   "answers": {"vendor": "Acme Plumbing", "doing": "Upload"}})
    t = r.json()["ticket"]
    first = _to(box, "thandi@test.local")[-1]
    assert not first["thread"] and first["mark"] == f"{t['ref']}:requester" and "Open your ticket" in first["html"]
    user.post(f"/api/support/tickets/{t['id']}/reply", json={"text": "When I press Upload it spins and then says error 500, since Monday."})
    second = _to(box, "thandi@test.local")[-1]
    assert second["thread"] == first["entry_id"] and second["subject"] == f"RE: {first['subject']}"  # a reply on the first: one chain
    assert "Earlier on this ticket" not in second["body"]  # the chain is underneath in the mail program
    # the lead is mailed too — the requester's next mail still replies on the requester's chain, never on the lead's mail
    pw = _make(owner, "lebo@test.local", "Lebo Lead", "manager")
    owner.post("/api/support/leads", json={"user_id": db.query(User).filter(User.email == "lebo@test.local").one().id, "system_ids": []})
    lead_id = db.query(User).filter(User.email == "lebo@test.local").one().id
    assert owner.post(f"/api/support/tickets/{t['id']}/assign", json={"user_id": lead_id}).status_code == 200
    to_lead = _to(box, "lebo@test.local")[-1]
    told = _to(box, "thandi@test.local")[-1]
    assert to_lead["mark"] == f"{t['ref']}:lead:{lead_id}" and told["thread"] == second["entry_id"]  # the lead's own chain, apart
    assert pw


def test_older_mail_and_a_reply_without_the_ticket_number(box, owner, db):
    _setup(owner, [])
    m = box.deliver("vendor@acme.example", "Portal broken", "It is broken.", store=BOX, conversation="CONV1")
    owner.post("/api/mailer/read", json={"store": BOX})
    t = db.query(SupportTicket).one()
    # a mail read before 1.0.71 kept no Outlook id: found again by its Message-ID
    db.query(SupportMail).update({SupportMail.entry_id: ""})
    db.commit()
    assert C.thread_mail(db, t) in (m["entry_id"], _to(box, "vendor@acme.example")[-1]["entry_id"])
    # they answer in the chain but took the ticket number out of the subject: it still lands on the ticket
    box.deliver("vendor@acme.example", "RE: Portal broken", "Vendor is Acme Plumbing, page is the invoice upload, error 500.", store=BOX, conversation="CONV1")
    got = owner.post("/api/mailer/read", json={"store": BOX}).json()
    assert got["tickets"] == 0 and got["replies"] == 1 and db.query(SupportTicket).count() == 1
    # someone else in the same conversation is not the requester: never their answer — 1.0.84: a note on the
    # same ticket (a colleague chasing it), not a second ticket; they are never mailed
    box.deliver("other@elsewhere.example", "RE: Portal broken", "Me too.", store=BOX, conversation="CONV1")
    sent = len(_to(box, "other@elsewhere.example"))
    owner.post("/api/mailer/read", json={"store": BOX})
    assert db.query(SupportTicket).count() == 1
    assert db.query(SupportMail).filter(SupportMail.sender == "other@elsewhere.example").one().outcome == "follow_up"
    notes = [m.text for m in db.query(SupportMessage).filter(SupportMessage.ticket_id == t.id).all()]
    assert any("Me too." in x and "same mail chain" in x for x in notes)
    assert not db.query(SupportMessage).filter(SupportMessage.ticket_id == t.id, SupportMessage.who == "requester", SupportMessage.text.like("%Me too%")).count()
    assert len(_to(box, "other@elsewhere.example")) == sent


def test_the_wording_of_each_ticket_mail_is_editable(box, owner, user, db):
    _setup(owner, [])
    assert user.get("/api/mailer/templates").status_code == 403
    ts = {t["key"]: t for t in owner.get("/api/mailer/templates").json()["templates"]}
    assert set(ts) == set(TP.DEFAULTS) and not ts["asked"]["changed"]
    assert owner.put("/api/mailer/templates/asked", json={"subject": "", "body": "x"}).status_code == 400
    r = owner.put("/api/mailer/templates/asked", json={"subject": "Quick question on {ref}", "body": "Dear {first_name}, {first_name.__class__}\n\nPlease tell us:\n{questions}"})
    assert r.status_code == 200 and r.json()["template"]["changed"]
    box.deliver("vendor@acme.example", "Portal broken", "It is broken.", store=BOX)
    owner.post("/api/mailer/read", json={"store": BOX})
    t = db.query(SupportTicket).one()
    m = _to(box, "vendor@acme.example")[-1]
    assert m["subject"] == f"RE: Portal broken [{C.ref(t)}]"  # in their thread the chain's subject is kept
    assert m["body"].startswith("Dear vendor, {first_name.__class__}") and "1. " in m["body"]  # words only — never attributes
    assert not owner.post("/api/mailer/templates/asked/reset", json={}).json()["template"]["changed"]
    assert owner.put("/api/mailer/templates/nope", json={"subject": "a", "body": "hello"}).status_code == 404
