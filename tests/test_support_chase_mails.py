"""1.0.84: the support agents on mail as the Chase mailbox really gets it — forwards ("kindly assist with the
below"), reminders, a colleague chasing on the same chain, the same request sent twice, thank-yous, a forwarded
welcome mail with a password in it, banners, disclaimers and signature logos. Every name, address and number
here is made up; the shapes are the real ones."""
import pytest

from cbx.config import settings
from cbx.models import AppState, MailLog, SupportLead, SupportMail, SupportMessage, SupportPerson, SupportSystem, SupportTicket
from cbx.support import core as C
from cbx.support import inbox as I
from cbx.support import mailtext as X
from cbx.support import outlook

BANNER = ("CAUTION: This email originated from outside of the organization - be aware of attachments, links or suspicious payment requests. "
          "Please report suspicious emails to the IT department immediately.\n You don't often get email from a@b.example. Learn why this is important "
          "<https://aka.ms/LearnAboutSenderIdentification>\n\n")
DISCLAIMER = ("\nThis email is confidential and intended to be read only by the recipient specified. Without the written consent of the sender, sharing "
              "any part of this message is strictly prohibited. If you experience problems accessing the disclaimer, please email someone and a copy of "
              "the disclaimer will be e-mailed to you.\n")


def sig(name, title="Facilities Coordinator"):
    return f"\n\nKind regards,\n\n{name} | {title}\nCBRE Excellerate\nT: +27 11 000 0000 | M: +27 82 000 0000\n{name.split()[0].lower()}@example.com\nwww.example.com <https://www.example.com>\n{DISCLAIMER}"


def head(who, when, to="Support Desk <support@cbx.test>", subject="Chase Access"):
    return f"\n\nFrom: {who}\nSent: {when}\nTo: {to}\nSubject: {subject}\n\n"


@pytest.fixture()
def box(db, owner, monkeypatch):
    fake = outlook.Fake()
    monkeypatch.setattr(outlook, "BACKEND", fake)
    monkeypatch.setattr(settings, "mail_enabled", True)
    monkeypatch.setattr(settings, "mail_transport", "outlook")
    monkeypatch.setattr(settings, "outlook_mailbox", "support@cbx.test")
    monkeypatch.setattr(settings, "mail_only_to", "")
    monkeypatch.setattr(settings, "support_mail", True)
    monkeypatch.setattr(settings, "device_verification", False)
    for M in (SupportMessage, SupportMail, SupportTicket, SupportLead, SupportPerson):
        db.query(M).delete()
    db.commit()
    C.ensure_systems(db)
    db.commit()
    yield fake
    db.rollback()
    for M in (SupportMessage, SupportMail, SupportTicket, SupportLead, SupportPerson):
        db.query(M).delete()
    db.query(MailLog).filter(MailLog.kind == "support").delete()
    db.commit()


def _ticket(db, sender):
    return db.query(SupportTicket).filter(SupportTicket.requester_email == sender).order_by(SupportTicket.id.desc()).first()


def _sent_to(box, who):
    return [m for m in box.sent if m["to"] == [who]]


def test_a_forward_brings_the_request_from_further_down_the_chain(box, db):
    body = ("Good day\n\nKindly assist with the below.\n\nKind Regards," + sig("Palesa Dube", "QHSE Advisor")
            + head("Dube, Palesa", "Thursday, 1 October 2026 11:25 AM") + "Hi All\n\n@Support kindly assist.\n\nKind Regards,\nPalesa"
            + head("Otieno, Brian @ Nairobi <brian.otieno@example.com <mailto:brian.otieno@example.com> >", "02 September 2026 12:14")
            + "Hi Palesa,\n\nOur new QHSE supervisor, Tom Wekesa, has his e-mail already.\n\nHe needs to have access to CBX-ABC North.\n\nKind regards,\nBrian")
    box.deliver("palesa@example.com", "FW: QHSE Supervisor GTC-Chase Access", BANNER + body, name="Palesa Dube")
    I.poll(db)
    t = _ticket(db, "palesa@example.com")
    assert "needs to have access to CBX-ABC North" in t.description and "Brian Otieno" in t.description
    assert "CAUTION" not in t.description and "confidential" not in t.description and "+27 82" not in t.description and "aka.ms" not in t.description
    s = db.get(SupportSystem, t.system_id)
    assert s.key == "chase" and t.problem_key == "access"
    assert t.title == "QHSE Supervisor GTC-Chase Access"
    assert "kindly assist" not in (t.outcome or "").lower()[:20]


def test_a_reminder_on_a_ticket_with_the_team_is_not_a_new_round_of_questions(box, db):
    first = "Hi Support,\n\nPlease load the below site on Chase.\n\nPID : 7001\nSite: Hilltop Mast\n\nRegards\nKabelo"
    box.deliver("kabelo@example.com", "Site on Chase", first, conversation="C-SITE", name="Kabelo Moloi")
    I.poll(db)
    t = _ticket(db, "kabelo@example.com")
    assert t.status == "ready" and t.problem_key == "sites" and "Hilltop Mast" in t.problem_where and "7001" in t.problem_where
    asks = t.asks
    box.deliver("kabelo@example.com", "RE: Site on Chase", "Hi Team,\n\nA polite reminder, kindly provide feedback on the below.\n\nRegards\nKabelo"
                + head("Kabelo Moloi", "yesterday") + first, conversation="C-SITE")
    got = I.poll(db)
    db.refresh(t)
    assert got["tickets"] == 0 and t.status == "ready" and t.asks == asks
    assert db.query(SupportMail).filter(SupportMail.subject == "RE: Site on Chase").one().outcome == "follow_up"
    told = db.query(SupportMessage).filter(SupportMessage.ticket_id == t.id, SupportMessage.who == "requester").count()
    assert told == 2


def test_someone_else_on_the_chain_chasing_it_is_a_note_not_a_ticket(box, db):
    box.deliver("lerato@vendor.example", "Chase Access", "Hi,\n\nMay you kindly assist with granting Thandi (copied) access to Chase for our vendor team.\n\nBest regards\nLerato",
                conversation="C-ACC", name="Lerato M")
    I.poll(db)
    t = _ticket(db, "lerato@vendor.example")
    box.deliver("boss@vendor.example", "RE: Chase Access", "Hi,\n\nPlease urgently assist Thandi.\n\nBest Regards\nSam" + head("Lerato M", "Monday") + "…",
                conversation="C-ACC", name="Sam Boss")
    got = I.poll(db)
    assert got["tickets"] == 0 and db.query(SupportTicket).count() == 1
    note = db.query(SupportMessage).filter(SupportMessage.ticket_id == t.id, SupportMessage.who == "note").order_by(SupportMessage.id.desc()).first()
    assert "Please urgently assist Thandi" in note.text and "boss@vendor.example" in note.text
    assert not _sent_to(box, "boss@vendor.example")  # only ever the requester is written to


def test_the_same_request_sent_twice_with_no_thread_link_is_two_tickets(box, db):
    """1.0.109 (Edwin: never thread a mail onto a ticket by its subject or its words): it landed on one ticket by its subject and
    words; with no tag and no thread link it is a ticket of its own (the desk closes one as a duplicate)."""
    a = ("Greetings,\n\nPlease note that I cannot reassign job cards to complete the quality check, which is blocking the GRN creation. "
         "Could you please assist in resolving this issue?\n\nPO 4500000001\nPO 4500000002\n\nBr,\nAmani")
    box.deliver("amani@example.com", "Unable to re assign job cards.", a)
    box.deliver("amani@example.com", "Unable to re assign job card", a.replace("quality check", "fulfilled Qtn check"))
    got = I.poll(db)
    assert got["tickets"] == 2 and db.query(SupportTicket).count() == 2
    t = db.query(SupportTicket).filter(SupportTicket.requester_email == "amani@example.com").order_by(SupportTicket.id).first()
    assert t.problem_key == "work-orders" and "cannot reassign job cards to complete the" in t.description  # "card…" is not a card number
    assert {r.outcome for r in db.query(SupportMail).filter(SupportMail.outcome != "sent").all()} == {"new_ticket"}  # 1.0.109: "sent" — CBX's own


def test_a_thank_you_is_never_a_ticket_and_never_reopens_one(box, db):
    box.deliver("kim@example.com", "RE: Office Refresh - PO", "Hi Tumi,\n\nYes, Thank you.\n\nKind Regards,\nKim" + head("Tumi", "Monday") + "Is this what you need?")
    got = I.poll(db)
    assert got["tickets"] == 0 and db.query(SupportMail).one().outcome == "ignored:thanks"
    # on a closed ticket it is kept, and the ticket stays closed
    box.deliver("rina@example.com", "Report broken", "Hi, the Chase observations report for September does not open, it shows an error when I press Export.", conversation="C-R")
    I.poll(db)
    t = _ticket(db, "rina@example.com")
    t.status = "closed"
    db.commit()
    box.deliver("rina@example.com", f"RE: [{C.ref(t)}] Report broken", "Thank you, that works now.\n\nRegards\nRina", conversation="C-R")
    I.poll(db)
    db.refresh(t)
    assert t.status == "closed"
    assert db.query(SupportMail).filter(SupportMail.subject.like("RE:%Report broken")).one().outcome == "thanks"


def test_a_password_in_a_mail_is_never_kept_or_sent_on(box, db):
    welcome = ("Dear Aisha\n\nWelcome to the CBX Chase application.\n\nYour login credentials are as follows:\nMobile app Client Code: CBX\n"
               "Username: aiMokoena\nPassword: Tq7#mk29Lp\n\nIf you require any assistance, please log a ticket.")
    body = ("Hi\n\nPlease can you assist.\n\nI have recently started and my Chase profile is set up, but I am unable to pull the PO report. There are no companies "
            "in the Financial company drop down for my portfolio. Please can you give me the same rights as Priya Naidoo.\n\nKind Regards\nAisha"
            + head("chase@example.com <chase@example.com>", "Tuesday, 22 September 2026 11:14", subject="Welcome to Chase") + welcome)
    box.deliver("ayesha@example.com", "Chase Profile set-up", body)
    I.poll(db)
    t = _ticket(db, "ayesha@example.com")
    everything = t.description + " ".join(m.text for m in db.query(SupportMessage).filter(SupportMessage.ticket_id == t.id).all()) + t.summary
    assert "Tq7#mk29Lp" not in everything
    assert any("password or code was in this mail" in m.text for m in db.query(SupportMessage).filter(SupportMessage.ticket_id == t.id, SupportMessage.who == "note").all())
    assert all("Tq7#mk29Lp" not in (m.get("body") or "") + (m.get("html") or "") for m in box.sent)
    assert t.problem_key == "rights"
    # and the reader that gives the Mailer and the personal mailbox agents a mail's words removes it too
    assert "Tq7#mk29Lp" not in X.readable(body) and "Tq7#mk29Lp" not in I.strip_quoted("Password: Tq7#mk29Lp\n")


def test_details_written_in_the_body_stay_and_a_new_login_is_access(box, db):
    box.deliver("gerrit@example.com", "Login Details for CHASE", "Good day Support team,\n\nOne of our handymen does not have login details for CHASE.\n\n"
                "Here are his details:\n\nEmail: handyman.one@example.com\nCellphone number: 0820000000\n\nKind Regards,\n\nGerrit" + sig("Gerrit Smit"))
    I.poll(db)
    t = _ticket(db, "gerrit@example.com")
    assert "handyman.one@example.com" in t.description and "0820000000" in t.description and "+27 82 000" not in t.description
    assert t.problem_key == "access" and t.outcome  # what is wanted follows from the area; not asked again


@pytest.mark.parametrize("text,area", [
    ("I still don't have Ghana and Malawi on my list and we have PO's to create", "locations"),
    ("please can I have PO report access to all of the Northwind Bank company codes listed below", "locations"),
    ("Kindly assist in providing Chase reporting access to the Contoso account, below-listed countries", "locations"),
    ("Zola is unable to approve quotations for the Riverside site", "rights"),
    ("Mpume is no longer with us and has been removed as a quote approver", "rights"),
    ("It appears that the client profile is incorrect: it pulls Tailspin instead of Fabrikam", "sites"),
    ("Please load the below site on Chase. PID: 7001", "sites"),
    ("A list of PO line items that reflect as expected on the PO report but were cancelled", "pos"),
    ("Please confirm the client code; I need it and my username to reset the PW", "signin"),
    ("I am writing to kindly request chase training", "training"),
    ("Please share observations for September", "reports"),
    ("Kindly share the revised invoice for the management fee", "invoices"),
])
def test_chase_areas_are_guessed_from_the_words(box, db, text, area):
    s = db.query(SupportSystem).filter(SupportSystem.key == "chase").one()
    assert C._guess_problem(s, text.lower())["key"] == area


def test_signature_pictures_are_not_attachments():
    assert not outlook.keep_attachment("image001.png", "image001.png@01DD", 4000)
    assert not outlook.keep_attachment("ClientLogo.png", "logo", 30000)
    assert not outlook.keep_attachment("photo.jpg", "", 40000, hidden=True)
    assert outlook.keep_attachment("Book2.xlsx", "x@y", 9000)  # Outlook sometimes gives documents a content id too
    assert outlook.keep_attachment("RE SAP PO Approval.msg")
    assert outlook.keep_attachment("Screenshot of the error.png", "", 120000)


def test_a_reminder_does_not_carry_the_same_spreadsheet_twice(box, db):
    body = "Good day,\n\nPlease could you review the Fabrikam profile on the Chase system. It pulls Tailspin as the client instead of Fabrikam.\n\nRegards\nWanda"
    box.deliver("wanda@example.com", "URGENT: Fabrikam Chase profile incorrect", body, files=[("PurchaseOrderLineReport (4).xlsx", b"x" * 900)], conversation="C-V")
    box.deliver("wanda@example.com", "FW: URGENT: Fabrikam Chase profile incorrect", "\n" + head("Wanda", "Tuesday") + body,
                files=[("PurchaseOrderLineReport (4).xlsx", b"x" * 900)], conversation="C-V")
    I.poll(db)
    t = _ticket(db, "wanda@example.com")
    assert db.query(SupportTicket).count() == 1 and len(t.files) == 1


def test_an_older_install_gets_the_new_chase_areas_once_and_keeps_its_own(box, db):
    s = db.query(SupportSystem).filter(SupportSystem.key == "chase").one()
    s.problems = [{"key": "work-orders", "name": "Work orders / jobs", "fields": []}, {"key": "my-area", "name": "Our own area", "fields": []},
                  {"key": "other", "name": "Something else", "fields": []}]
    db.query(AppState).filter(AppState.key == "support.chase_areas").delete()
    db.commit()
    C.ensure_systems(db)
    keys = [p["key"] for p in s.problems]
    assert {"access", "locations", "rights", "sites", "pos", "training", "my-area"} <= set(keys) and keys[-1] == "other"
    assert next(p for p in s.problems if p["key"] == "work-orders")["name"] == "Work orders / jobs"  # the one they had stays as it was
    s.problems = [p for p in s.problems if p["key"] != "training"]
    db.commit()
    C.ensure_systems(db)
    assert "training" not in [p["key"] for p in s.problems]  # once: an area someone removed stays removed


def test_when_the_model_says_it_is_not_for_support_nobody_is_mailed(box, db, monkeypatch):
    monkeypatch.setattr(C, "_model", lambda db, t, asked: {"system": "Chase", "problem": "", "title": "Raise a quote for the Gulf client", "questions": [],
                                                          "summary": "A colleague asks another to raise a quote; nothing for support.", "where": "", "outcome": "", "kind": "not_support"})
    box.deliver("lisa@example.com", "RE: Gulf Sites", "Thank you. @Rana please raise the quote for the client.\n\nKind Regards,\nLisa")
    I.poll(db)
    t = _ticket(db, "lisa@example.com")
    assert t.status == "ready" and t.summary.startswith("Probably not for support")
    assert not _sent_to(box, "lisa@example.com")


def test_on_a_mail_what_the_model_read_wins_over_the_rules_guess(box, db, monkeypatch):
    monkeypatch.setattr(C, "_model", lambda db, t, asked: {"system": "Chase", "problem": "Access for a new person", "title": "GTC access for a new QHSE supervisor",
                                                          "questions": [], "summary": "New supervisor needs access to CBX-ABC North.", "where": "Kenya, CBX-ABC North",
                                                          "outcome": "Give Tom Wekesa access to CBX-ABC North", "kind": "request"})
    box.deliver("palesa@example.com", "FW: Chase", "Kindly assist with the below.\n\nRegards" + head("Brian", "Monday") + "He needs to have access to CBX-ABC North, please.")
    I.poll(db)
    t = _ticket(db, "palesa@example.com")
    assert t.status == "ready" and t.problem_key == "access" and t.problem_where == "Kenya, CBX-ABC North" and t.outcome.startswith("Give Tom")


def test_redaction_keeps_order_numbers_and_removes_secrets():
    t = "PO 4500012345\nCard number: 4111 1111 1111 1111\nZoom https://x.example/j/123?pwd=abcDEF123\nPIN: 4321\nRef 4111111111111112\nAccount number: 62000000000"
    r = X.redact(t)
    assert "4500012345" in r and "4111111111111112" in r  # not a card (fails the check) — an order number stays
    assert "4111 1111 1111 1111" not in r and "abcDEF123" not in r and "4321" not in r and "62000000000" not in r


def test_kinds_of_mail():
    assert X.kind("Good day\n\nKindly assist with the below.\n\nKind Regards,") == "follow_up"
    assert X.kind("Hi Team,\nA polite reminder, we are kindly requesting your support.\nBr\nAmani") == "follow_up"
    assert X.kind("Hi Tumi,\n\nYes, Thank you.\n\nKind Regards,\nKim") == "thanks"
    assert X.kind("Thank you. Unfortunately it still does not work for Algeria — can you check again?") == "request"
    assert X.kind("Please load the below site on Chase. PID: 7001 Site: Hilltop Mast") == "request"
    assert I.subject_of("RE: [External]RE: URGENT: Fabrikam Chase profile incorrect") == "Fabrikam Chase profile incorrect"
