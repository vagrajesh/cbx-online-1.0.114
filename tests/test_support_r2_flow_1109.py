"""1.0.109 (round 2, 23 Oct) — the Flow items of Edwin's second support round: never thread by subject (2), a new problem
replied into an old thread (3), pictures inside the outgoing mail (4, the mail half), the follow-up timer and the escalation to
the ticket's responsible person (6), assignee suggestions that learn and AXL's auto-assign (8).

Outlook through outlook.Fake; the AI through a stand-in (C.JUDGE); the clock through followup.run(now=…). Everything made up."""
from datetime import datetime, timedelta, timezone

from cbx.db import SessionLocal, utcnow
from cbx.models import SupportMail, SupportMessage, SupportTicket, User
from cbx.models.support_flow import SupportPick
from cbx.support import core as C
from cbx.support import followup as FU
from cbx.support import inbox as I
from cbx.support import suggest as SG

from .test_support_1098 import BOX, _setup, assign, box, deliver, ticket, to, uid  # noqa: F401
from .test_support_flow_1109 import _desk, _ready, _thread, flat_png

REQ, LEAD1, LEAD2, LEAD3 = "naledi.r2@client.example", "merna.r2@test.local", "tebogo.r2@test.local", "sipho.r2@test.local"
UTC = timezone.utc


def _sys(key):
    with SessionLocal() as s:
        x = s.query(C.SupportSystem).filter(C.SupportSystem.key == key).one()
        return x.id


# ---- 2. never thread by subject ---------------------------------------------------------------------------------------------
def test_only_the_tag_or_the_thread_joins_a_ticket_never_a_similar_subject(box, owner):
    _setup(owner)
    tid = _ready(box, owner, REQ, "Generator Nile Tower weekly test", conv="CR2-a")
    with SessionLocal() as s:
        first = s.query(SupportMail).filter(SupportMail.ticket_id == tid).first()
        mid = first.message_id
    db = SessionLocal()
    try:
        assert I.find_ticket(db, {"id": "<x1@x>", "subject": "RE: Generator Nile Tower weekly test", "headers": "", "conversation": ""}) == (None, False)
        assert I.find_ticket(db, {"id": "<x2@x>", "subject": "FW: generator nile tower weekly", "headers": "", "conversation": "CR2-other"}) == (None, False)
        t, chain = I.find_ticket(db, {"id": "<x3@x>", "subject": f"Re: [T-{tid:04d}] anything", "headers": "", "conversation": ""})
        assert t.id == tid and chain is False  # the tag
        t, chain = I.find_ticket(db, {"id": "<x4@x>", "subject": "Something else", "headers": f"In-Reply-To: {mid}\r\n", "conversation": ""})
        assert t.id == tid and chain  # its own thread link
        t, chain = I.find_ticket(db, {"id": "<x5@x>", "subject": "Something else", "headers": f"References: <a@b>\r\n {mid}\r\n", "conversation": ""})
        assert t.id == tid and chain  # a folded References header
        t, chain = I.find_ticket(db, {"id": "<x6@x>", "subject": "Totally different", "headers": "", "conversation": "CR2-a"})
        assert t.id == tid and chain  # Outlook's conversation of a mail CBX kept for the ticket
    finally:
        db.close()
    # through the mailbox: a new mail with the same subject (no tag, another conversation) is a ticket of its own
    deliver(box, owner, REQ, "Generator Nile Tower weekly test", "The air-conditioning on floor 5 has stopped working since this morning, please send a technician.",
            "Naledi Dube", conv="CR2-b")
    with SessionLocal() as s:
        assert s.query(SupportTicket).filter(SupportTicket.requester_email == REQ).count() == 2


# ---- 3. a new problem in an old thread ----------------------------------------------------------------------------------------
NEW = "Separately, my SAP user is locked and I cannot log on to post any invoices at all. Please unlock it."


def test_a_new_problem_in_an_old_thread_asks_the_sender_once_and_never_reopens(box, owner, admin, monkeypatch):
    _setup(owner)
    tid = _ready(box, owner, REQ, "Generator Nile Tower", conv="CR2-np")
    with SessionLocal() as s:
        t = s.get(SupportTicket, tid)
        t.status, t.resolution, t.closed_at = "closed", "Serviced the generator.", utcnow()
        s.commit()
    seen = []
    monkeypatch.setattr(C, "JUDGE", lambda about, words: seen.append(words) or {"different": True, "confidence": "high", "reason": "SAP log-on, not the generator"})
    quoted = NEW + "\n\nFrom: Support\nSent: Monday\nSubject: [T-0001] Your ticket is done\n\nThe generator was serviced."
    deliver(box, owner, REQ, f"RE: [T-{tid:04d}] Your ticket is done", quoted, "Naledi Dube", conv="CR2-np")
    deliver(box, owner, REQ, f"RE: [T-{tid:04d}] Your ticket is done", "And another thing: the printer on floor 2 jams on every page, please send someone.",
            "Naledi Dube", conv="CR2-np")
    assert "The generator was serviced" not in seen[0]  # only the new words are judged
    t = ticket(tid=tid)
    assert t.status == "closed"  # never reopened
    asks = [m for m in to(box, REQ) if "new e-mail" in m["body"]]
    assert len(asks) == 1, [m["subject"] for m in to(box, REQ)]  # asked once
    with SessionLocal() as s:
        notes = s.query(SupportMessage).filter(SupportMessage.ticket_id == tid, SupportMessage.mail_ref.isnot(None)).all()
        assert len(notes) == 2 and all(n.who == "note" for n in notes)  # each a quiet note with "Make it a new ticket"
    view = _thread(admin, tid)
    assert sum(1 for m in view["thread"] if m.get("new_problem")) == 2


# ---- 4. pictures inside the outgoing mail -------------------------------------------------------------------------------------
def _png(seed=4) -> bytes:
    return flat_png(seed=seed, lines=3)


def _rich_message(db, t, by, words_before, words_after, wait=False):
    """What the reply editor sends: words, a pasted picture, more words (through Screen's rich.attach when it is there)."""
    from cbx.support import rich as R

    name, data = R.picture("shot.png", _png())

    def files(m):
        R.attach(db, t, m, f"<p>{words_before}</p><p><img data-k=\"0\"></p><p>{words_after}</p>", [(name, data)], by.name, author_id=by.id, note=False)
        m.text = R.plain(m.html)

    return C.staff_message(db, t, by, f"{words_before}\n\n[picture]\n\n{words_after}", True, files=files, wait=wait)


def test_a_reply_with_a_pasted_picture_goes_as_html_with_the_picture_inline_where_it_was_pasted(box, owner):
    _setup(owner)
    l1, _ = _desk(owner, LEAD1, "Merna Pillay")
    tid = _ready(box, owner, REQ, "Journal Z320 will not post", conv="CR2-rich")
    assign(owner, ticket(tid=tid), l1)
    with SessionLocal() as s:
        t, me = s.get(SupportTicket, tid), s.get(User, l1)
        _rich_message(s, t, me, "Please look at the error below:", "Is this the screen you see?")
        s.commit()
    m = to(box, REQ)[-1]
    assert m["inline"] and m["inline"][0]["cid"].startswith(f"cbx-{tid}-") and not m["attachments"]
    cid = m["inline"][0]["cid"]
    html = m["html"]
    assert f'src="cid:{cid}"' in html and html.index("error below") < html.index(f"cid:{cid}") < html.index("screen you see")
    assert "[picture]" in m["body"] and "data-n" not in html  # the plain part says where the picture was


def test_the_smtp_mail_is_multipart_related_with_a_content_id(box, owner, monkeypatch):
    from cbx import mail
    from cbx.config import settings

    sent = []

    class S:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, **k):
            pass

        def login(self, *a):
            pass

        def send_message(self, msg):
            sent.append(msg)

    monkeypatch.setattr(mail.smtplib, "SMTP", S)
    monkeypatch.setattr(mail, "configured", lambda: (True, "ok"))
    monkeypatch.setattr(mail, "_cfg", lambda: {"enabled": True, "transport": "smtp", "send_store": ""})
    monkeypatch.setattr(settings, "smtp_port", 587)
    monkeypatch.setattr(settings, "mail_from", "desk@test.local")
    r = mail.send(["a@test.local"], "s", "Look: [picture] here", html='<p>Look:</p><img src="cid:cbx-1-2@cbx"><p>here</p>',
                  inline=[("shot.png", _png(), "cbx-1-2@cbx")], attachments=[("log.txt", b"x")])
    assert r.ok and sent
    msg = sent[0]
    kinds = [p.get_content_type() for p in msg.walk()]
    assert "multipart/related" in kinds and "text/plain" in kinds and "text/plain" in kinds
    pic = next(p for p in msg.walk() if p.get_content_type() == "image/png")
    assert pic["Content-ID"] == "<cbx-1-2@cbx>" and pic.get_content_disposition() == "inline"
    assert any(p.get_filename() == "log.txt" for p in msg.walk())


def test_a_changed_template_still_sends_the_pictures_as_attachments(box, owner):
    _setup(owner)
    l1, _ = _desk(owner, LEAD1, "Merna Pillay")
    tid = _ready(box, owner, REQ, "Journal Z320 will not post", conv="CR2-rich2")
    assign(owner, ticket(tid=tid), l1)
    r = owner.put("/api/mailer/templates/message", json={"subject": "Message from {staff_first}", "body": "Hi {first_name}, {message} — {staff_name}"})
    assert r.status_code == 200, r.text
    with SessionLocal() as s:
        _rich_message(s, s.get(SupportTicket, tid), s.get(User, l1), "See the error:", "Thanks.")
        s.commit()
    m = to(box, REQ)[-1]
    assert not m["inline"] and len(m["attachments"]) == 1  # never lost


def test_the_requesters_rich_reply_reaches_the_lead_with_its_picture_inline(box, owner, user):
    from cbx.support import rich as R

    _setup(owner)
    l1, _ = _desk(owner, LEAD1, "Merna Pillay")
    with SessionLocal() as s:
        me = s.query(User).filter(User.email == "user@test.local").first() or s.query(User).filter(User.role == "user").first()
        t = C.create(s, system=s.query(C.SupportSystem).filter(C.SupportSystem.key == "sap").one(), problem_key="posting", description="MIRO fails for invoice 5100012345",
                     requester=me, source="app")
        t.status, t.summary = "ready", "MIRO fails."
        s.commit()
        tid, mine = t.id, me.id
    assign(owner, ticket(tid=tid), l1)
    with SessionLocal() as s:
        t, me = s.get(SupportTicket, tid), s.get(User, mine)
        name, data = R.picture("err.png", _png(7))

        def files(m):
            R.attach(s, t, m, '<p>This is what I get:</p><p><img data-k="0"></p>', [(name, data)], me.name, author_id=None, note=False)
            m.text = R.plain(m.html)

        C.requester_reply(s, t, "This is what I get:\n\n[picture]", author=me, files=files)
        s.commit()
    m = [x for x in to(box, LEAD1) if "what I get" in x["body"]][-1]  # in Merna's own chain of the ticket
    assert m["inline"] and f'cid:{m["inline"][0]["cid"]}' in m["html"] and not m["attachments"]


# ---- 6. the follow-up timer and the escalation ----------------------------------------------------------------------------
def test_working_days_skip_weekends_and_sa_public_holidays():
    j = lambda y, mo, d, h=10: datetime(y, mo, d, h - 2, 0, tzinfo=UTC)  # noqa: E731 — Johannesburg 10:00
    # Thursday 2 April 2026 + 2: Good Friday 3 Apr, the weekend and Family Day 6 Apr are skipped
    assert FU.add_working_days(j(2026, 4, 2), 2) == j(2026, 4, 8)
    # a Saturday start counts from Monday 08:00
    assert FU.add_working_days(j(2026, 10, 24), 2) == j(2026, 10, 28, 8)
    # 9 August 2026 is a Sunday: Monday 10 August is the holiday
    assert not FU.is_working(datetime(2026, 8, 10).date()) and FU.is_working(datetime(2026, 8, 11).date())
    h27 = FU.holidays(2027)
    assert datetime(2027, 3, 26).date() in h27 and datetime(2027, 3, 29).date() in h27 and datetime(2027, 12, 27).date() in h27


def _waiting(box, owner, conv, *, person=None):
    """A ticket the desk is waiting on the requester for (AXL's questions, or a person's question marked waiting)."""
    tid = _ready(box, owner, REQ, "Generator Nile Tower " + conv, conv=conv)
    with SessionLocal() as s:
        t = s.get(SupportTicket, tid)
        if person is None:
            t.status, t.missing = "pending", ["Which generator — the one in the basement or on the roof?"]
            FU.start(s, t, "axl", "Which generator — the one in the basement or on the roof?")
        else:
            C.staff_message(s, t, s.get(User, person), "Which generator is it — basement or roof?", True, wait=True)
        s.commit()
    return tid


def test_no_reply_follow_up_once_then_escalate_to_the_responsible_person(box, owner, admin, monkeypatch):
    _setup(owner)
    monkeypatch.setattr(C, "LOOP_MAX", 50)  # days pass on the stand-in clock, not on the outbox's: the loop guard's hour is real time
    l1, lc = _desk(owner, LEAD1, "Merna Pillay")
    tid = _ready(box, owner, REQ, "Generator Nile Tower", conv="CR2-fu")
    assign(owner, ticket(tid=tid), l1)
    # the system's default responsible person: someone not on CBX
    r = owner.post("/api/support/followup-settings", json={"follow_days": 2, "escalate_days": 2,
                                                           "systems": [{"id": ticket(tid=tid).system_id, "owner": {"kind": "other", "name": "Kagiso Molefe", "email": "kagiso@client.example"}}]})
    assert r.status_code == 200, r.text
    assert lc.post(f"/api/support/tickets/{tid}/message", json={"text": "Which generator is it — basement or roof?", "to_requester": True, "wait": True}).status_code == 200
    t = ticket(tid=tid)
    since = t.waiting_since.replace(tzinfo=UTC)
    view = _thread(lc, tid)
    assert view["followup"]["state"] == "waiting" and view["owner"]["name"] == "Kagiso Molefe" and view["owner"]["from"] == "system"
    n0 = len(to(box, REQ))
    with SessionLocal() as s:
        assert FU.run(s, now=since + timedelta(hours=20))["followed"] == 0
        due = FU.add_working_days(since, 2)
        assert FU.run(s, now=due + timedelta(minutes=1))["followed"] == 1
        assert FU.run(s, now=due + timedelta(hours=3))["followed"] == 0  # once
    fups = to(box, REQ)[n0:]
    assert len(fups) == 1 and "just checking in" in fups[0]["body"] and "basement or roof" in fups[0]["body"] and f"[T-{tid:04d}]" in fups[0]["subject"]
    with SessionLocal() as s:
        sent = FU._dt(s.get(SupportTicket, tid).followup["sent_at"])
        esc_at = FU.add_working_days(sent, 2)
        assert FU.run(s, now=esc_at - timedelta(minutes=5))["escalated"] == 0
        assert FU.run(s, now=esc_at + timedelta(minutes=1))["escalated"] == 1
        assert FU.run(s, now=esc_at + timedelta(days=3))["escalated"] == 0
    esc = to(box, "kagiso@client.example")
    assert len(esc) == 1 and "over to you" in esc[0]["subject"] and "basement or roof" in esc[0]["body"]
    view = _thread(owner, tid)
    assert view["word"] == "Escalated to Kagiso Molefe" and view["followup"]["state"] == "escalated"
    assert any("Escalated to Kagiso Molefe" in m["text"] and m.get("quiet") for m in view["thread"])
    assert any("was escalated to Kagiso Molefe" in m["body"] for m in to(box, LEAD1))  # the assignee is told
    # the requester replies: the clock stops and the escalation ends
    deliver(box, owner, REQ, f"RE: [T-{tid:04d}] Just checking in", "It is the basement one, next to the parking entrance.", "Naledi Dube", conv="CR2-fu")
    t = ticket(tid=tid)
    assert t.waiting_since is None and not t.followup
    assert _thread(owner, tid)["word"].startswith("With Merna")


def test_the_clock_pauses_on_a_reply_a_plain_message_or_done_and_axl_questions_start_it(box, owner):
    _setup(owner)
    l1, lc = _desk(owner, LEAD1, "Merna Pillay")
    tid = _waiting(box, owner, "CR2-p1")
    assert ticket(tid=tid).waiting_since is not None
    assert owner.get("/api/support/queue?status=requester").json()["tickets"][0]["id"] == tid
    assign(owner, ticket(tid=tid), l1)
    # a message to the requester without "waiting" ends the wait
    assert lc.post(f"/api/support/tickets/{tid}/message", json={"text": "We are on it.", "to_requester": True}).status_code == 200
    assert ticket(tid=tid).waiting_since is None
    assert lc.post(f"/api/support/tickets/{tid}/message", json={"text": "Which floor?", "to_requester": True, "wait": True}).status_code == 200
    assert ticket(tid=tid).waiting_since is not None
    assert lc.post(f"/api/support/tickets/{tid}/message", json={"text": "internal", "to_requester": False}).status_code == 200
    assert ticket(tid=tid).waiting_since is not None  # a note never changes it
    assert lc.post(f"/api/support/tickets/{tid}/status", json={"status": "closed", "note": "Serviced the generator."}).status_code == 200
    assert ticket(tid=tid).waiting_since is None
    # removed while waiting: the run stops the clock and mails nobody
    tid2 = _waiting(box, owner, "CR2-p2")
    assert owner.post(f"/api/support/tickets/{tid2}/remove", json={"tell": False}).status_code == 200
    n = len(to(box, REQ))
    with SessionLocal() as s:
        FU.run(s, now=utcnow().replace(tzinfo=UTC) + timedelta(days=30))
    assert ticket(tid=tid2).waiting_since is None and len(to(box, REQ)) == n


def test_axls_questions_start_the_clock_by_themselves(box, owner):
    _setup(owner)
    deliver(box, owner, REQ, "Help", "It is broken again, please help.", "Naledi Dube", conv="CR2-axl")
    t = ticket(REQ)
    assert t.status == "pending" and t.waiting_since is not None and t.followup["kind"] == "axl"
    deliver(box, owner, REQ, f"RE: [T-{t.id:04d}] We need a little more information",
            "It is the Chase job card for the generator at Nile Tower in Cairo; please arrange a service visit this week.", "Naledi Dube", conv="CR2-axl")
    t2 = ticket(tid=t.id)
    if t2.status == "pending":  # read again and AXL asked again: a new wait, from now
        assert t2.waiting_since > t.waiting_since and t2.followup["kind"] == "axl"
    else:  # read again and ready: the clock stopped
        assert t2.waiting_since is None and not t2.followup


def test_the_responsible_person_picker_and_its_rights(box, owner, user):
    _setup(owner)
    l1, lc = _desk(owner, LEAD1, "Merna Pillay")
    tid = _ready(box, owner, REQ, "Generator Nile Tower", conv="CR2-own")
    assert lc.post(f"/api/support/tickets/{tid}/owner", json={"kind": "user", "id": l1}).status_code == 200  # a free ticket: anyone on the desk who sees it
    d = _thread(owner, tid)
    assert d["owner"] == {"kind": "user", "id": l1, "name": "Merna Pillay", "email": LEAD1, "from": "ticket"} and d["owner_own"]
    r = owner.post(f"/api/support/tickets/{tid}/owner", json={"kind": "other", "name": "K", "email": "x"})
    assert r.status_code == 400
    r = owner.post(f"/api/support/tickets/{tid}/owner", json={"kind": "other", "name": "Kagiso Molefe", "email": "kagiso@client.example"})
    assert r.status_code == 200 and r.json()["owner"]["kind"] == "other"
    assert owner.post(f"/api/support/tickets/{tid}/owner", json={"kind": "none"}).json()["owner_own"] is False
    assert user.post(f"/api/support/tickets/{tid}/owner", json={"kind": "user", "id": l1}).status_code == 403  # not on the desk
    assert owner.get("/api/support/owner-people").json()["people"]
    s = owner.get("/api/support/followup-settings").json()
    assert s["follow_days"] == 2 and s["escalate_days"] == 2 and s["holidays"]
    assert owner.post("/api/support/followup-settings", json={"follow_days": 0}).status_code == 400
    assert user.get("/api/support/followup-settings").status_code == 403


# ---- 8. suggestions that learn, and auto-assign -------------------------------------------------------------------------------
def _history(sys_key, lead_id, n, words, done=True, area=""):
    """Made-up past tickets of one person."""
    with SessionLocal() as s:
        sid = s.query(C.SupportSystem).filter(C.SupportSystem.key == sys_key).one().id
        for i in range(n):
            t = SupportTicket(system_id=sid, problem_key=area, title=f"{words} {i}", description=words, requester_email=f"p{i}@client.example",
                              requester_name="P", source="mail", status="closed" if done else "assigned", assigned_to_id=lead_id,
                              closed_at=utcnow() if done else None, summary=words,
                              closed_by_id=lead_id if done else None)  # 1.0.111: only what a person finished themselves counts
            s.add(t)
        s.commit()


def _fresh(sys_key, title, area=""):
    with SessionLocal() as s:
        sid = s.query(C.SupportSystem).filter(C.SupportSystem.key == sys_key).one().id
        t = SupportTicket(system_id=sid, problem_key=area, title=title, description=title, requester_email=REQ, requester_name="Naledi Dube", source="mail",
                          status="ready", summary=title)
        s.add(t)
        s.commit()
        return t.id


def test_three_suggestions_with_a_why_and_the_first_preselected(box, owner):
    _setup(owner)
    l1, _ = _desk(owner, LEAD1, "Merna Pillay")
    l2, _ = _desk(owner, LEAD2, "Tebogo Mabaso")
    l3, _ = _desk(owner, LEAD3, "Sipho Ndlovu")
    _history("sap", l1, 7, "SAP journal posting reallocation error", area="posting")
    _history("sap", l1, 2, "SAP journal posting stuck", done=False)
    _history("sap", l2, 2, "SAP vendor master bank change")
    _history("chase", l3, 6, "Chase job card")
    tid = _fresh("sap", "SAP journal posting reallocation fails", area="posting")
    d = _thread(owner, tid)
    sug = d["suggestions"]
    assert 1 <= len(sug) <= 3 and sug[0]["id"] == l1
    assert "7 done" in sug[0]["why"] and "2 open now" in sug[0]["why"] and "like this one" in sug[0]["why"]  # 1.0.111: "7 done (…) · 2 open now"
    with SessionLocal() as s:  # deterministic
        assert SG.suggest(s, s.get(SupportTicket, tid)) == SG.suggest(s, s.get(SupportTicket, tid))


def test_picking_someone_else_asks_why_once_and_the_reason_is_learnt(box, owner):
    _setup(owner)
    l1, _ = _desk(owner, LEAD1, "Merna Pillay")
    l2, _ = _desk(owner, LEAD2, "Tebogo Mabaso")
    _history("sap", l1, 3, "SAP journal posting error")
    _history("sap", l2, 2, "SAP journal posting error")
    tid = _fresh("sap", "SAP journal posting error again")
    with SessionLocal() as s:
        before = SG.score(s, s.get(SupportTicket, tid))
    assert before[0]["id"] == l1
    r = owner.post(f"/api/support/tickets/{tid}/assign", json={"user_id": l2})
    assert r.status_code == 200 and r.json()["ask_why"] == {"id": l1, "name": "Merna Pillay"}
    assert owner.post(f"/api/support/tickets/{tid}/why-not", json={"reason": "bogus"}).status_code == 400
    assert owner.post(f"/api/support/tickets/{tid}/why-not", json={"reason": "wrong_area", "text": "she does vendor master"}).status_code == 200
    with SessionLocal() as s:
        p = s.query(SupportPick).filter(SupportPick.ticket_id == tid).one()
        assert p.suggested_id == l1 and p.picked_id == l2 and p.reason == "wrong_area"
        tid2 = _fresh("sap", "SAP journal posting error once more")
        after = {x["id"]: x["score"] for x in SG.score(s, s.get(SupportTicket, tid2))}
    old = {x["id"]: x["score"] for x in before}
    assert after[l1] < old[l1] and after[l2] > old[l2] - 1.5  # the reason weighs on Merna; the pick counts for Tebogo
    # the first suggestion picked: nothing to ask
    r = owner.post(f"/api/support/tickets/{tid2}/assign", json={"user_id": SG.suggest(SessionLocal(), ticket(tid=tid2))[0]["id"]})
    assert r.json()["ask_why"] is None
    # on leave: left out until picked again
    tid3 = _fresh("sap", "SAP journal posting error third")
    first = SG.suggest(SessionLocal(), ticket(tid=tid3))[0]["id"]
    other = l2 if first == l1 else l1
    owner.post(f"/api/support/tickets/{tid3}/assign", json={"user_id": other, "why_not": "on_leave"})
    tid4 = _fresh("sap", "SAP journal posting error fourth")
    assert first not in [x["id"] for x in SG.suggest(SessionLocal(), ticket(tid=tid4))]
    st = owner.get("/api/support/assign-settings").json()["stats"]
    assert st["suggested"] >= 3 and st["rate"] is not None and st["lines"]


def test_auto_assign_when_clearly_ahead_and_an_admin_undoes_it(box, owner, user):
    _setup(owner)
    l1, _ = _desk(owner, LEAD1, "Merna Pillay")
    l2, _ = _desk(owner, LEAD2, "Tebogo Mabaso")
    _history("chase", l1, 8, "Chase generator service visit job card")
    _history("chase", l2, 1, "Chase quote approval")
    chase = _sys("chase")
    # 1.0.111: AXL only ever auto-assigns someone set for the system
    assert owner.post("/api/support/system-people", json={"systems": [{"id": chase, "people": [l1, l2]}]}).status_code == 200
    # off by default: nothing happens
    tid0 = _ready(box, owner, REQ, "Generator Nile Tower service", conv="CR2-auto0")
    assert ticket(tid=tid0).assigned_to_id is None
    r = owner.post("/api/support/assign-settings", json={"systems": [{"id": chase, "auto": True}], "margin": 3, "min_history": 5})
    assert r.status_code == 200 and any(x["auto"] for x in r.json()["systems"])
    assert user.post("/api/support/assign-settings", json={"margin": 1}).status_code == 403
    box.deliver("omar.r2@client.example", "Generator Nile Tower weekly test fails",
                "Good day,\n\nThe generator at the Cairo, Egypt site (Nile Tower, basement) fails its weekly test since Friday. The Chase job card is open.\n"
                "Please arrange a service visit this week.\n\nKind regards,\nOmar", name="Omar Hassan", store=BOX, conversation="CR2-auto")
    assert owner.post("/api/mailer/read", json={"store": BOX}).status_code == 200
    t = ticket("omar.r2@client.example")
    assert t.assigned_to_id == l1 and t.auto and "works Chase · 8 done" in t.auto["why"], (t.status, t.assigned_to_id, t.system_id)
    assert any("Assigned to you" in m["subject"] for m in to(box, LEAD1))  # the usual mail
    d = _thread(owner, t.id)
    assert d["auto_assigned"]["can_undo"] and any(m["text"].startswith("Assigned by AXL: Merna Pillay") for m in d["thread"])
    r = owner.post(f"/api/support/tickets/{t.id}/undo-auto")
    assert r.status_code == 200 and r.json()["assigned_to_id"] is None and r.json()["status"] == "ready"
    with SessionLocal() as s:
        assert s.query(SupportPick).filter(SupportPick.ticket_id == t.id, SupportPick.kind == "undo").count() == 1
    assert owner.post(f"/api/support/tickets/{t.id}/undo-auto").status_code == 400  # once
    st = owner.get("/api/support/assign-settings").json()["stats"]
    assert st["auto"] == 1 and st["undone"] == 1


def test_auto_assign_holds_back_without_enough_history_or_margin(box, owner):
    _setup(owner)
    l1, _ = _desk(owner, LEAD1, "Merna Pillay")
    l2, _ = _desk(owner, LEAD2, "Tebogo Mabaso")
    _history("chase", l1, 3, "Chase generator service visit job card")
    owner.post("/api/support/system-people", json={"systems": [{"id": _sys("chase"), "people": [l1, l2]}]})  # 1.0.111: set people only
    owner.post("/api/support/assign-settings", json={"systems": [{"id": _sys("chase"), "auto": True}], "min_history": 5})
    tid = _fresh("chase", "Chase generator service visit job card needed")
    with SessionLocal() as s:
        assert SG.maybe_auto(s, s.get(SupportTicket, tid)) is None  # 3 < 5 finished tickets
    _history("chase", l1, 3, "Chase generator service visit job card")
    _history("chase", l2, 6, "Chase generator service visit job card")
    with SessionLocal() as s:
        assert SG.maybe_auto(s, s.get(SupportTicket, tid)) is None  # neither is clearly ahead


# ---- 2 (coordinator, 23 Oct): CBX's own sent ticket mails are remembered, so a reply without the tag still threads ----------
def _app_ticket(s, requester_email="thandi@test.local"):
    me = s.query(User).filter(User.email == requester_email).one()
    t = C.create(s, system=s.query(C.SupportSystem).filter(C.SupportSystem.key == "sap").one(), problem_key="posting",
                 description="MIRO fails for invoice 5100012345 in company code Z320", requester=me, source="app")
    t.status, t.summary = "ready", "MIRO fails."
    s.commit()
    return t.id


def test_a_reply_to_an_app_tickets_mail_without_the_tag_threads_by_in_reply_to(box, owner, user):
    _setup(owner)
    l1, _ = _desk(owner, LEAD1, "Merna Pillay")
    with SessionLocal() as s:
        tid = _app_ticket(s)
    assign(owner, ticket(tid=tid), l1)  # "Someone is working on your ticket" goes to Thandi as a new mail (no chain of hers yet)
    sent = [m for m in to(box, "thandi@test.local") if "working on your ticket" in m["subject"]][-1]
    with SessionLocal() as s:
        row = s.query(SupportMail).filter(SupportMail.ticket_id == tid, SupportMail.outcome == "sent", SupportMail.why == C.mark(ticket(tid=tid))).one()
        assert row.message_id.startswith("cbx-sent:") and row.conversation == sent["conversation"]  # Outlook's id comes from Sent Items later
    assert owner.post("/api/mailer/read", json={"store": BOX}).status_code == 200  # the read fills it in
    with SessionLocal() as s:
        assert s.get(SupportMail, row.id).message_id == sent["message_id"]
    n = 0
    with SessionLocal() as s:
        n = s.query(SupportTicket).count()
    # her reply: the tag taken out of the subject, a conversation CBX never saw — only In-Reply-To links it
    box.deliver("thandi@test.local", "RE: Someone is working on your ticket", "The error still shows when I post it this morning, same message as before.",
                name="Thandi Mokoena", store=BOX, conversation="CR2-unknown", headers=f"In-Reply-To: {sent['message_id']}\r\nReferences: {sent['message_id']}\r\n")
    assert owner.post("/api/mailer/read", json={"store": BOX}).status_code == 200
    with SessionLocal() as s:
        assert s.query(SupportTicket).count() == n
        assert s.query(SupportMail).filter(SupportMail.ticket_id == tid, SupportMail.outcome.in_(("reply", "follow_up"))).count() == 1
    # and one in the same Outlook conversation as CBX's mail, no tag, no headers
    box.deliver("thandi@test.local", "Re: working on your ticket", "Any news on the posting? I still cannot post invoice 5100012345.", name="Thandi Mokoena",
                store=BOX, conversation=sent["conversation"])
    assert owner.post("/api/mailer/read", json={"store": BOX}).status_code == 200
    with SessionLocal() as s:
        assert s.query(SupportTicket).count() == n
        assert s.query(SupportMail).filter(SupportMail.ticket_id == tid, SupportMail.outcome.in_(("reply", "follow_up"))).count() == 2


def test_an_smtp_ticket_mail_keeps_its_message_id(box, owner, user, monkeypatch):
    from cbx import mail, mailbox
    from cbx.config import settings

    class S:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, **k):
            pass

        def login(self, *a):
            pass

        def send_message(self, msg):
            got.append(msg)

    got = []
    monkeypatch.setattr(mail.smtplib, "SMTP", S)
    monkeypatch.setattr(mail, "configured", lambda: (True, "ok"))
    monkeypatch.setattr(mail, "_cfg", lambda: {"enabled": True, "transport": "smtp", "send_store": ""})
    monkeypatch.setattr(settings, "smtp_port", 587)
    monkeypatch.setattr(settings, "mail_from", "desk@test.local")
    with SessionLocal() as s:
        tid = _app_ticket(s)
        row = mailbox.send(s, to=["thandi@test.local"], subject=f"[T-{tid:04d}] Hello", body="Hi", kind="support", check=False, ticket_id=tid)
        s.commit()
        assert row.ok and got
        mid = got[0]["Message-ID"]
        assert mid and s.query(SupportMail).filter(SupportMail.message_id == mid, SupportMail.ticket_id == tid, SupportMail.outcome == "sent").count() == 1
        t, chain = I.find_ticket(s, {"id": "<r@x>", "subject": "RE: Hello", "headers": f"References: <other@x>\r\n\t{mid}\r\n", "conversation": ""})
        assert t.id == tid and chain


# ---- 4 (coordinator): the requester's own page shows the pictures pasted into a staff reply ---------------------------------
def test_the_requester_sees_the_pictures_of_a_staff_reply_on_their_page(box, owner, user):
    from cbx.support import people as P

    _setup(owner)
    l1, _ = _desk(owner, LEAD1, "Merna Pillay")
    with SessionLocal() as s:
        tid = _app_ticket(s)
    assign(owner, ticket(tid=tid), l1)
    with SessionLocal() as s:
        _rich_message(s, s.get(SupportTicket, tid), s.get(User, l1), "Please look at this:", "Is it the same?")
        C.staff_message(s, s.get(SupportTicket, tid), s.get(User, l1), "internal: check the GL first", False)
        s.commit()
    mine = _thread(user, tid)
    reply = next(m for m in mine["thread"] if m["who"] == "staff" and "look at this" in m["text"])
    n = reply["files"][0]["n"] if reply["files"] else None
    assert n and f'<img data-n="{n}">' in reply["html"]  # the page draws the picture in place (not "[picture]")
    assert user.get(f"/api/support/tickets/{tid}/files/{n}?view=1").status_code == 200
    assert not any(m["who"] in ("note", "admin") for m in mine["thread"])  # never the team's notes
    # an invisible lead: the requester still gets the pictures, in their places, with the words scrubbed; the mail carries them as files
    with SessionLocal() as s:
        st = P.get(s, s.get(User, l1))
        st.invisible = True
        s.commit()
    with SessionLocal() as s:
        _rich_message(s, s.get(SupportTicket, tid), s.get(User, l1), "Second look, Merna here:", "Thanks.")
        s.commit()
    mine = _thread(user, tid)
    reply = [m for m in mine["thread"] if m["who"] == "staff"][-1]
    n2 = max(f["n"] for f in reply["files"])
    assert f'<img data-n="{n2}">' in reply["html"] and "Merna" not in reply["html"] and reply["name"] == "Support"
    assert user.get(f"/api/support/tickets/{tid}/files/{n2}?view=1").status_code == 200
    m = to(box, "thandi@test.local")[-1]
    assert m["attachments"] and not m["inline"]
