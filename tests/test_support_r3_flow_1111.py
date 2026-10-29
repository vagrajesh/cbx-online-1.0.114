"""1.0.111 (round 3, 23 Oct afternoon) — the Flow items of Edwin's third support round:

    1  who works each system (set-up stores anyone who can work tickets), suggestions that respect it (never an owner or admin
       unless set; "done" = assigned AND closed by them; no system → none; auto-assign only a set person)
    3  the list route takes comma lists (status, system, person)
    4  T-0087 / T-0086: the new-problem check only on a finished ticket; the one-time repair; "It belongs here"; an empty reply;
       never the same question twice in 24 h / one questions mail per 30 min; smarter first questions; a colleague in the
       thread; "New — AXL is reading it (since …)" and the 10-minute watchdog

Outlook through outlook.Fake; the AI through stand-ins (C.JUDGE, C._model); the clock through watch_new(now=…). Every person,
address, number and mail here is made up."""
from datetime import timedelta

from fastapi.testclient import TestClient

from cbx.db import SessionLocal, engine, utcnow
from cbx.main import app
from cbx.models import AuditLog, SupportMail, SupportMessage, SupportTicket
from cbx.support import core as C
from cbx.support import inbox as I
from cbx.support import suggest as SG

from .conftest import _make, login
from .test_support_1098 import BOX, _setup, box, deliver, ticket, to, uid  # noqa: F401
from .test_support_flow_1109 import _desk, _thread
from .test_support_r2_flow_1109 import _sys

DOM = "client-co.example"


# ---- helpers ----------------------------------------------------------------------------------------------------------------
def _deskp(owner, email, name, role="user"):
    """A person on the desk only through the Support desk grant (not a support lead)."""
    desk_id = next(t["id"] for t in owner.get("/api/users").json()["tools"] if t["slug"] == "support-desk")
    pw = _make(owner, email, name, role, tools=[desk_id])
    c = TestClient(app)
    login(c, email, pw)
    return uid(email), c


def _done(sys_key, who, n, words="Chase invoice approval", closed_by=None, status="closed", closed_as=""):
    """Made-up finished tickets: assigned to `who`, closed by `closed_by` (default: themselves)."""
    with SessionLocal() as s:
        sid = s.query(C.SupportSystem).filter(C.SupportSystem.key == sys_key).one().id
        for i in range(n):
            s.add(SupportTicket(system_id=sid, title=f"{words} {i}", description=words, requester_email=f"h{i}@{DOM}", requester_name="H", source="mail",
                                status=status, assigned_to_id=who, closed_at=utcnow() if status == "closed" else None, summary=words,
                                closed_by_id=(closed_by or who) if status == "closed" else None, closed_as=closed_as))
        s.commit()


def _fresh(sys_key, title="Chase invoice still shows after approval"):
    with SessionLocal() as s:
        sid = s.query(C.SupportSystem).filter(C.SupportSystem.key == sys_key).one().id if sys_key else None
        t = SupportTicket(system_id=sid, title=title, description=title, requester_email=f"req@{DOM}", requester_name="Rina Patel", source="mail",
                          status="ready", summary=title)
        s.add(t)
        s.commit()
        return t.id


def _ids(c, tid):
    return [x["id"] for x in _thread(c, tid)["suggestions"]]


def _owner_id():
    return uid("owner@test.local")


# ---- 1. who works each system, suggestions that respect it -----------------------------------------------------------------
def test_an_owner_with_many_finished_chase_tickets_is_never_suggested(box, owner, admin):
    _setup(owner)
    assert owner.post("/api/support/leads", json={"user_id": _owner_id(), "system_ids": []}).status_code == 200  # the owner is a "support lead"
    nq, _ = _deskp(owner, "naomi.r3@test.local", "Naomi Sitole")
    adm = uid("admin@test.local")
    _done("chase", _owner_id(), 6)  # took and closed them as owner
    _done("chase", adm, 4)
    _done("chase", nq, 2)
    tid = _fresh("chase")
    got = _ids(owner, tid)
    assert got == [nq], got  # only the desk person who did Chase — never the owner or an admin
    why = _thread(owner, tid)["suggestions"][0]["why"]
    assert why.startswith("2 done") and why.endswith(" · 0 open now") and "works" not in why, why
    # set for Chase by an admin: then the owner may be suggested
    chase = _sys("chase")
    assert owner.post("/api/support/system-people", json={"systems": [{"id": chase, "people": [_owner_id()]}]}).status_code == 200
    assert _ids(owner, tid) == [_owner_id()]
    assert _thread(owner, tid)["suggestions"][0]["why"].startswith("works Chase · 6 done")


def test_set_people_only_and_the_why_line(box, owner):
    _setup(owner)
    a, _ = _deskp(owner, "ann.r3@test.local", "Ann Mokoena")
    b, _ = _deskp(owner, "ben.r3@test.local", "Ben Dlamini")
    t_, _ = _desk(owner, "tumi.r3@test.local", "Tumi Lead")  # a support lead with lots of Chase
    _done("chase", t_, 9, words="Chase invoice approval refresh")
    _done("chase", a, 3, words="Chase invoice approval refresh")
    _done("chase", b, 1, words="Chase job card")
    _done("chase", a, 1, status="assigned")
    chase = _sys("chase")
    r = owner.post("/api/support/system-people", json={"systems": [{"id": chase, "people": [a, b]}]})
    assert r.status_code == 200, r.text
    row = next(x for x in r.json()["systems"] if x["id"] == chase)
    assert {p["id"] for p in row["people"]} == {a, b} and row["guess"] == []
    tid = _fresh("chase", "Chase invoice approval refresh does not clear")
    sug = _thread(owner, tid)["suggestions"]
    assert [x["id"] for x in sug] == [a, b]  # never Tumi: not set for Chase
    assert sug[0]["why"] == "works Chase · 3 done (3 like this one) · 1 open now", sug[0]["why"]


def test_the_fallback_is_desk_people_who_finished_that_system_never_admins_and_closed_by_someone_else_counts_for_nobody(box, owner, admin):
    _setup(owner)
    a, _ = _deskp(owner, "ann2.r3@test.local", "Ann Two")
    c_, _ = _deskp(owner, "cleo.r3@test.local", "Cleo Three")
    d, _ = _deskp(owner, "dumi.r3@test.local", "Dumi Four")  # never did Chase
    adm = uid("admin@test.local")
    assert owner.post("/api/support/leads", json={"user_id": adm, "system_ids": []}).status_code == 200
    _done("chase", adm, 5)
    _done("chase", a, 2)
    _done("chase", c_, 4, closed_by=_owner_id())  # the owner closed Cleo's tickets: not Cleo's work
    _done("chase", c_, 3, closed_as="not_relevant")  # not relevant: not work done
    _done("sap", d, 5, words="SAP posting")
    tid = _fresh("chase")
    assert _ids(owner, tid) == [a]
    with SessionLocal() as s:
        assert SG.guess(s, _sys("chase")) == [{"id": a, "name": "Ann Two", "done": 2}]


def test_no_system_yet_no_suggestions_and_the_line_says_so(box, owner):
    _setup(owner)
    a, _ = _deskp(owner, "ann3.r3@test.local", "Ann Three")
    _done("chase", a, 3)
    tid = _fresh(None, "Something is broken")
    d = _thread(owner, tid)
    assert d["suggestions"] == [] and d["suggest_note"] == "AXL suggests once the system is known"
    tid2 = _fresh("sap", "SAP posting fails")
    d2 = _thread(owner, tid2)
    assert d2["suggestions"] == [] and "set who works SAP" in d2["suggest_note"]


def test_auto_assign_only_ever_picks_a_set_person_never_from_the_fallback(box, owner):
    _setup(owner)
    a, _ = _deskp(owner, "ann4.r3@test.local", "Ann Four")
    b, _ = _deskp(owner, "ben4.r3@test.local", "Ben Four")
    _done("chase", a, 9, words="Chase invoice approval refresh")
    chase = _sys("chase")
    owner.post("/api/support/assign-settings", json={"systems": [{"id": chase, "auto": True}], "margin": 3, "min_history": 5})
    tid = _fresh("chase", "Chase invoice approval refresh again")
    with SessionLocal() as s:
        t = s.get(SupportTicket, tid)
        assert SG.score(s, t)[0]["id"] == a  # the fallback suggests her …
        assert SG.maybe_auto(s, t) is None  # … but AXL never assigns from it
        s.rollback()
    owner.post("/api/support/system-people", json={"systems": [{"id": chase, "people": [a, b]}]})
    with SessionLocal() as s:
        t = s.get(SupportTicket, tid)
        assert SG.maybe_auto(s, t).id == a  # set, clearly ahead, enough history
        s.commit()
    assert ticket(tid=tid).assigned_to_id == a


def test_set_up_saves_desk_grant_people_refuses_others_and_is_audited(box, owner, admin, user):
    _setup(owner)
    a, desk = _deskp(owner, "ann5.r3@test.local", "Ann Five")
    chase, sap = _sys("chase"), _sys("sap")
    got = owner.get("/api/support/system-people").json()
    pool = {p["id"]: p for p in got["pool"]}
    assert pool[a]["desk"] and not pool[a]["lead"] and uid("thandi@test.local") not in pool
    assert desk.get("/api/support/system-people").status_code == 403 and user.get("/api/support/system-people").status_code == 403
    r = admin.post("/api/support/system-people", json={"systems": [{"id": chase, "people": [uid("thandi@test.local")]}]})
    assert r.status_code == 400 and "can't work tickets" in r.text
    r = admin.post("/api/support/system-people", json={"systems": [{"id": chase, "people": [a]}, {"id": sap, "people": []}]})
    assert r.status_code == 200
    with SessionLocal() as s:
        assert [u.id for u in C.covering(s, chase)] == [a] and C.lead_systems(s, s.get(C.User, a)) == {chase}
        n = s.query(AuditLog).filter(AuditLog.action == "support_system_people", AuditLog.detail.like("Chase:%Ann Five%")).count()
    assert n >= 1
    # the ticket's Assign list marks her as covering Chase
    tid = _fresh("chase")
    assert any(x["id"] == a and x["covers"] for x in _thread(owner, tid)["leads"])


def test_use_axls_guess_proposes_the_non_admin_people_who_finished_most(box, owner, admin):
    _setup(owner)
    a, _ = _deskp(owner, "ann6.r3@test.local", "Ann Six")
    b, _ = _deskp(owner, "ben6.r3@test.local", "Ben Six")
    c_, _ = _deskp(owner, "cleo6.r3@test.local", "Cleo Six", role="admin")  # an admin with the grant: never proposed
    _done("chase", a, 2)
    _done("chase", b, 5)
    _done("chase", c_, 9)
    chase = _sys("chase")
    g = owner.get(f"/api/support/system-people/{chase}/guess").json()["people"]
    assert [x["id"] for x in g] == [b, a] and g[0]["done"] == 5
    row = next(x for x in owner.get("/api/support/system-people").json()["systems"] if x["id"] == chase)
    assert row["people"] == [] and [x["id"] for x in row["guess"]] == [b, a]
    assert owner.get("/api/support/system-people/99999/guess").status_code == 404
    with SessionLocal() as s:  # nothing saved by a guess
        assert C.covering(s, chase) == []


# ---- 3. the list takes comma lists ----------------------------------------------------------------------------------------------
def test_the_list_takes_several_statuses_people_and_systems(box, owner):
    _setup(owner)
    a, ac = _desk(owner, "anele.r3@test.local", "Anele Lead")
    b, _ = _desk(owner, "merna.r3@test.local", "Merna Lead")
    t_free = _fresh("chase", "Free one")
    t_a = _fresh("chase", "Anele's")
    t_b = _fresh("sap", "Merna's")
    t_done = _fresh("chase", "Done one")
    t_nr = _fresh(None, "Not relevant one")
    with SessionLocal() as s:
        for tid, who in ((t_a, a), (t_b, b), (t_done, b)):
            C.assign(s, s.get(SupportTicket, tid), s.get(C.User, who), s.get(C.User, _owner_id()))
        x = s.get(SupportTicket, t_done)
        x.status, x.closed_at, x.closed_by_id = "closed", utcnow(), b
        y = s.get(SupportTicket, t_nr)
        y.status, y.closed_at, y.closed_as = "closed", utcnow(), "not_relevant"
        s.commit()
    q = lambda c, qs: sorted(x["id"] for x in c.get("/api/support/queue?" + qs).json()["tickets"])  # noqa: E731
    assert q(owner, "status=done") == [t_done]
    assert q(owner, "status=done,not_relevant") == sorted([t_done, t_nr])
    assert q(owner, "status=free,not_accepted,with,requester") == sorted([t_free, t_a, t_b])
    assert q(owner, f"status=open,done&lead={a},{b}") == sorted([t_a, t_b, t_done])
    assert q(owner, f"status=with&lead={b}") == [t_b]
    assert q(owner, f"system={_sys('sap')},0&status=all") == sorted([t_b, t_nr])
    # bad values are left out (only bad: the default "open"); never an error
    assert q(owner, "status=done,bogus%3Bdrop%20table") == [t_done]
    assert q(owner, "status=bogus") == sorted([t_free, t_a, t_b])
    assert q(owner, "lead=x,1e9,-3&status=done") == [t_done]
    assert owner.get("/api/support/queue?system=a,b").status_code == 200
    # the rest of the desk: still only their own and the free ones, whatever they ask for
    assert q(ac, f"status=open,done,not_relevant&lead={b}") == sorted([t_free, t_a])
    assert ac.get("/api/support/queue?status=removed").status_code == 403
    assert q(ac, "status=removed,done") == []


# ---- 4.1 the new-problem check only on a finished ticket --------------------------------------------------------------------
def _model_says(monkeypatch, *answers):
    """A stand-in for the readers: each reading takes the next answer (the last one repeats)."""
    seq = list(answers)
    calls = []

    def fake(db, t, asked):
        calls.append(t.id)
        return dict(seq.pop(0) if len(seq) > 1 else seq[0])

    monkeypatch.setattr(C, "_model", fake)
    return calls


T87 = ("Hi\n\nPlease assist and see below feedback from Reflex.\n\nThanks\n\nFrom: REFLEX Support\nSent: Friday, 23 October 2026 10:57\n"
       "Subject: Ticket Resolved\n\nDear General User,\n\nYour ticket ID:4410001 has been resolved.\nSummary: PO PO7700112026 - Chase\n")


def test_on_an_open_ticket_a_reply_is_always_a_reply_never_judged(box, owner, monkeypatch):
    _setup(owner)
    judged = []
    monkeypatch.setattr(C, "JUDGE", lambda about, words: judged.append((about, words)) or {"different": True, "confidence": "high", "reason": "x"})
    _model_says(monkeypatch, {"system": "Chase", "questions": ["What is the invoice number?"], "summary": "Chase invoice still shows."},
                {"system": "Chase", "questions": [], "summary": "Chase invoice INV-5501 still shows after approval."})
    req = f"rina@{DOM}"
    deliver(box, owner, req, "FW: PO PO7700112026 - Chase - Ticket Resolved", T87, "Rina Patel", conv="C87")
    t = ticket(req)
    assert t.status == "pending" and t.missing == ["What is the invoice number?"]
    deliver(box, owner, req, f"RE: PO PO7700112026 - Chase [T-{t.id:04d}]",
            "Invoice number is INV-5501\n\nFrom: Support\nSent: Friday\nSubject: [T-0001]\n\nWhat is the invoice number? Your ticket was resolved.", "Rina Patel", conv="C87")
    t = ticket(req)
    assert judged == [] and t.status == "ready", (t.status, t.missing)
    with SessionLocal() as s:
        assert s.query(SupportMail).filter(SupportMail.ticket_id == t.id, SupportMail.outcome == "new_problem").count() == 0
        assert any("INV-5501" in m.text for m in s.query(SupportMessage).filter(SupportMessage.ticket_id == t.id, SupportMessage.who == "requester"))
    # a finished one is judged — and the judge gets CBX's own state and the open questions, never the quoted first mail
    with SessionLocal() as s:
        x = s.get(SupportTicket, t.id)
        x.status, x.closed_at, x.resolution, x.summary = "closed", utcnow(), "Cleared the stuck approval.", ""
        x.missing = ["What is the invoice number?"]
        s.commit()
    deliver(box, owner, req, f"RE: [T-{t.id:04d}] Done", "Separately the generator at the Dubai office fails its weekly test, please send a technician.",
            "Rina Patel", conv="C87")
    assert len(judged) == 1
    about = judged[0][0]
    assert about.startswith("Ticket state (CBX's own): Done (closed): Cleared the stuck approval.") and "Open questions" in about
    assert "4410001" not in about and "General User" not in about  # the forwarded helpdesk mail is not the ticket's state


def test_a_mail_that_answers_an_open_question_is_never_a_new_problem():
    assert C.answers_open(["What is the invoice number?"], "Invoice number is Inv55501-26")
    assert not C.answers_open(["What is the invoice number?"], "The generator in the basement makes a loud noise every morning")
    assert not C.answers_open([], "Invoice number is Inv55501-26")


# ---- 4.2 the one-time repair (T-0087, made up) ------------------------------------------------------------------------------
def _t87_state(box, owner, monkeypatch, req):
    """T-0087 as it was: pending for the invoice number; the answer kept aside as a "new problem" (an old ticket's verdict)."""
    _model_says(monkeypatch, {"system": "Chase", "questions": ["What is the invoice number?"], "summary": "Chase invoice still shows."})
    deliver(box, owner, req, "FW: PO PO7700112026 - Chase - Ticket Resolved", T87, "Rina Patel", conv="C87r")
    t = ticket(req)
    assert t.status == "pending"
    with SessionLocal() as s:
        row = SupportMail(message_id=f"<np-{t.id}@test>", sender=req, subject=f"RE: PO PO7700112026 - Chase [T-{t.id:04d}]", ticket_id=t.id, outcome="new_problem",
                          why="Ticket resolved, new mail provides distinct invoice number",
                          text="Invoice number is INV-55501-26\n\n— Earlier, from Support (Friday, 23 October 2026 11:19):\nWhat is the invoice number?")
        s.add(row)
        s.flush()
        n = C.add_message(s, s.get(SupportTicket, t.id), "note", "New problem sent in this thread — asked to log a new call: Ticket resolved", name="Support desk", via="mail")
        n.mail_ref = row.id
        s.commit()
        return t.id, row.id


def test_the_one_time_repair_adds_the_answer_to_the_open_ticket_and_mails_nobody(box, owner, monkeypatch):
    _setup(owner)
    req = f"rina2@{DOM}"
    tid, mid = _t87_state(box, owner, monkeypatch, req)
    _model_says(monkeypatch, {"system": "Chase", "questions": [], "summary": "Chase invoice INV-55501-26 still shows after approval."})
    sent = len(box.sent)
    from cbx.migrate import _support_new_problem_undone

    with engine.begin() as c:  # as the migration runs it
        _support_new_problem_undone(c)
    with SessionLocal() as s:
        t = s.get(SupportTicket, tid)
        assert t.status == "new" and C.is_quiet_read(s, t)
        msgs = s.query(SupportMessage).filter(SupportMessage.ticket_id == tid).order_by(SupportMessage.id).all()
        assert msgs[-1].who == "requester" and msgs[-1].text == "Invoice number is INV-55501-26"  # the new words only
        assert any(m.mail_ref == mid and m.text.endswith(I.UNDONE_LINE) for m in msgs)
        assert s.get(SupportMail, mid).outcome == "reply_after_all"
        assert s.query(AuditLog).filter(AuditLog.action == "support_new_problem_undone", AuditLog.target == f"ticket:T-{tid:04d}").count() == 1
        C.read_safely(s, tid)  # the reader runs again (the support-triage routine does it on the server)
        s.commit()
    t = ticket(tid=tid)
    assert t.status == "ready" and t.missing == [] and t.waiting_since is None  # no longer waiting for the invoice number
    assert len(box.sent) == sent  # nobody mailed — not by the repair, not by the reading after it
    with SessionLocal() as s:
        assert not C.is_quiet_read(s, s.get(SupportTicket, tid))  # once
        assert I.undo_new_problems(s) == 0  # and it is done


def test_the_repair_leaves_a_real_new_problem_on_a_finished_ticket(box, owner, monkeypatch):
    _setup(owner)
    req = f"rina3@{DOM}"
    tid, mid = _t87_state(box, owner, monkeypatch, req)
    with SessionLocal() as s:
        t = s.get(SupportTicket, tid)
        t.status, t.closed_at = "closed", utcnow() - timedelta(days=1)
        s.commit()
        assert I.undo_new_problems(s) == 0 and s.get(SupportMail, mid).outcome == "new_problem"


# ---- 4.3 "It belongs here" ----------------------------------------------------------------------------------------------------
def test_it_belongs_here_adds_the_mail_as_a_reply_for_admins_only(box, owner, admin, monkeypatch):
    _setup(owner)
    req = f"rina4@{DOM}"
    tid, mid = _t87_state(box, owner, monkeypatch, req)
    _, stc = _deskp(owner, "staff.r3@test.local", "Staff Person")
    view = _thread(admin, tid)
    assert any(m.get("new_problem") == mid for m in view["thread"])  # the note offers both buttons
    assert stc.post(f"/api/support/new-problem/{mid}/belongs").status_code == 403
    _model_says(monkeypatch, {"system": "Chase", "questions": [], "summary": "Chase invoice INV-55501-26."})
    r = admin.post(f"/api/support/new-problem/{mid}/belongs")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "ready" and not any(m.get("new_problem") for m in d["thread"])
    assert any(m["who"] == "requester" and m["text"] == "Invoice number is INV-55501-26" for m in d["thread"])
    assert any("Added to the ticket after all by Arnab Admin" in m["text"] for m in d["thread"])
    assert admin.post(f"/api/support/new-problem/{mid}/belongs").status_code == 400  # once
    with SessionLocal() as s:
        assert s.query(AuditLog).filter(AuditLog.action == "support_new_problem_belongs", AuditLog.target == f"ticket:T-{tid:04d}").count() == 1
        from cbx.models import SupportLesson

        assert s.query(SupportLesson).count() == 0  # learnt nothing


# ---- 4.4 an empty reply ---------------------------------------------------------------------------------------------------------
def test_an_empty_reply_changes_nothing(box, owner, monkeypatch):
    _setup(owner)
    calls = _model_says(monkeypatch, {"system": "Chase", "questions": ["What is the invoice number?"], "summary": "x"})
    req = f"rina5@{DOM}"
    deliver(box, owner, req, "FW: PO PO7700112026 - Chase - Ticket Resolved", T87, "Rina Patel", conv="C87e")
    t = ticket(req)
    before = (t.status, t.asks, t.waiting_since, len(to(box, req)), len(calls))
    got = deliver(box, owner, req, f"RE: [T-{t.id:04d}] We need a little more information",
                  "Hi\n\nKind regards,\nRina Patel\nT: +971 4 000 0000\n\nFrom: Support\nSent: Friday\n\nWhat is the invoice number?", "Rina Patel", conv="C87e")
    assert got["replies"] == 0
    t = ticket(req)
    assert (t.status, t.asks, t.waiting_since, len(to(box, req)), len(calls)) == before  # no mail back, no re-ask, nothing moved
    view = _thread(owner, t.id)
    note = view["thread"][-1]
    assert note["who"] == "note" and note["text"] == "Rina replied with nothing new" and note.get("quiet")
    with SessionLocal() as s:
        assert s.query(SupportMail).filter(SupportMail.ticket_id == t.id, SupportMail.outcome == "empty").count() == 1


# ---- 4.5 never the same question twice in 24 hours; one questions mail per 30 minutes -----------------------------------------
def _age(tid, minutes):
    """Move the ticket's past back in time (the stand-in for the clock): its messages and its "asked" audit rows."""
    with SessionLocal() as s:
        for m in s.query(SupportMessage).filter(SupportMessage.ticket_id == tid).all():
            m.at = m.at - timedelta(minutes=minutes)
        for a in s.query(AuditLog).filter(AuditLog.target == f"ticket:T-{tid:04d}").all():
            a.at = a.at - timedelta(minutes=minutes)
        t = s.get(SupportTicket, tid)
        t.created_at = t.created_at - timedelta(minutes=minutes)
        s.commit()


def test_the_same_question_is_never_sent_twice_in_a_day_and_one_questions_mail_per_half_hour(box, owner, monkeypatch):
    _setup(owner)
    Q1, Q2 = "What is the invoice number?", "Which approver should see it?"
    _model_says(monkeypatch, {"system": "Chase", "questions": [Q1], "summary": "x"}, {"system": "Chase", "questions": [Q1], "summary": "x"},
                {"system": "Chase", "questions": [Q2], "summary": "x"}, {"system": "Chase", "questions": [Q1, Q2], "summary": "x"})
    req = f"rina6@{DOM}"
    deliver(box, owner, req, "FW: PO PO7700112026 - Chase - Ticket Resolved", T87, "Rina Patel", conv="C87q")
    t = ticket(req)
    asked = lambda: [m for m in to(box, req) if "needs a little more" in m["body"]]  # noqa: E731 — AXL's questions mail
    assert len(asked()) == 1
    reply = lambda words: deliver(box, owner, req, f"RE: [T-{t.id:04d}] We need a little more information", words, "Rina Patel", conv="C87q")  # noqa: E731
    reply("I am checking with the supplier and will come back to you on this.")
    x = ticket(tid=t.id)
    assert len(asked()) == 1 and x.status == "pending" and x.missing == [Q1] and x.waiting_since is not None  # not asked again
    assert "AXL didn't ask again" in _thread(owner, t.id)["thread"][-1]["text"]
    _age(t.id, 5)
    reply("The supplier says the approval went through on their side yesterday.")
    x = ticket(tid=t.id)
    assert len(asked()) == 1 and x.missing == [Q2]  # a new question, but a questions mail went 5 minutes ago: it waits
    assert "AXL's next question waits" in _thread(owner, t.id)["thread"][-1]["text"]
    _age(t.id, 40)
    reply("Still nothing from the approver on our side, sorry for the delay.")
    x = ticket(tid=t.id)
    assert len(asked()) == 2 and x.missing == [Q1, Q2]
    last = asked()[-1]["body"]
    assert Q2 in last and Q1 not in last  # only what wasn't asked in the last 24 hours


# ---- 4.6 smarter first questions ---------------------------------------------------------------------------------------------
def test_a_mail_naming_chase_and_a_po_gets_no_site_question(box, owner, monkeypatch):
    _setup(owner)
    # rules only (the AI box off)
    req = f"rina7@{DOM}"
    deliver(box, owner, req, "FW: PO PO7700112026 - Chase - Ticket Resolved", T87, "Rina Patel", conv="C87s")
    t = ticket(req)
    assert C.WHERE_Q not in (t.missing or []), t.missing
    # a stand-in AI that asks the old generic questions and things the mail already says
    _model_says(monkeypatch, {"system": "Chase", "summary": "x", "questions": [
        C.WHERE_Q, "Which site or page is affected?", "What is the PO number?", "What is the invoice number?", "What is the invoice number for this?"]})
    req2 = f"rina8@{DOM}"
    deliver(box, owner, req2, "FW: PO PO7700112026 - Chase - Ticket Resolved", T87, "Rina Patel", conv="C87s2")
    t = ticket(req2)
    assert t.missing == ["What is the invoice number?"], t.missing
    with SessionLocal() as s:
        sys_ = s.query(C.SupportSystem).filter(C.SupportSystem.key == "chase").one()
        assert C.sharpen([C.WHERE_Q, "Which approver?"], None, "") == [C.WHERE_Q, "Which approver?"]  # no system: where is asked
        assert C.sharpen(["Which site is it?", C.WHERE_Q], None, "") == ["Which site is it?"]  # never two that ask the same
        assert C.sharpen(["What is the vendor number?"], sys_, "vendor number 400123") == []


def test_what_a_forwarded_msg_on_the_ticket_says_is_not_asked(box, owner, monkeypatch):
    _setup(owner)
    _model_says(monkeypatch, {"system": "Chase", "summary": "x", "questions": ["What is the job card number?"]})
    with SessionLocal() as s:
        t = C.create(s, system=None, description="Please help with the job card in the attached mail.", title="Job card", email=f"rina9@{DOM}", name="Rina", source="mail")
        C.add_file(s, t, "fwd.txt", b"Forwarded: Job card no 7781234 at the Dubai office is stuck.", f"rina9@{DOM}")
        s.commit()
        C.read_safely(s, t.id)
        s.commit()
        assert "What is the job card number?" not in (s.get(SupportTicket, t.id).missing or [])


# ---- 4.7 a colleague in the thread (T-0086) ------------------------------------------------------------------------------
def _read_in(box, owner, sender, subject, body, name, conv="", headers=""):
    box.deliver(sender, subject, body, name=name, store=BOX, conversation=conv, headers=headers)
    assert owner.post("/api/mailer/read", json={"store": BOX}).status_code == 200


def test_a_colleague_in_the_thread_joins_the_ticket(box, owner):
    _setup(owner)
    req, mate = f"sheryl@{DOM}", f"kirsty@{DOM}"
    deliver(box, owner, req, "PO name approver to be changed", "Hi,\n\nPlease change the PO approver on Chase for the Dubai portfolio to John Mbeki.\n\nThanks\nSheryl",
            "Sheryl Rao", conv="C86")
    first = box.inbox[-1]["id"]  # Sheryl's own mail (its Message-ID)
    t = ticket(req)
    n_before = len(to(box, mate))
    # 1.0.111 (review): Outlook's conversation alone is not a thread link for a colleague — a new ticket as before
    _read_in(box, owner, f"lerato@{DOM}", f"Re: PO name approver to be changed [T-{t.id:04d}]", "Hi, please also add me as an approver for the Oman sites.",
             "Lerato Moyo", conv="C86")
    _read_in(box, owner, mate, f"Re: PO name approver to be changed [T-{t.id:04d}]", "Hi, John's e-mail is john.mbeki@client-co.example and it is for company code AE10.",
             "Kirsty Reid", conv="C86", headers=f"In-Reply-To: {first}\r\nReferences: {first}\r\n")
    with SessionLocal() as s:
        assert s.query(SupportTicket).filter(SupportTicket.requester_email == mate).count() == 0  # no new ticket
        assert s.query(SupportTicket).filter(SupportTicket.requester_email == f"lerato@{DOM}").count() == 1
        x = s.get(SupportTicket, t.id)
        assert x.requester_email == req  # the requester unchanged
    kirsty_mail = box.inbox[-1]["id"]
    view = _thread(owner, t.id)
    m = next(m for m in view["thread"] if m["name"] == "Kirsty Reid (in the thread)")
    assert m["who"] == "requester" and m.get("in_thread") and "AE10" in m["text"]
    assert len(to(box, mate)) == n_before  # no CBX mails to her
    # a link to the colleague's own mail is not a link to the requester's thread
    _read_in(box, owner, f"thabo@{DOM}", f"Re: [T-{t.id:04d}] approver", "Hi, the same goes for the Kuwait sites, please change them too.", "Thabo Ngema",
             headers=f"In-Reply-To: {kirsty_mail}\r\n")
    # the tag without a thread link → a new ticket as before; another domain → a new ticket as before
    deliver(box, owner, f"zola@{DOM}", f"Re: [T-{t.id:04d}] something", "Hi, please also give me access to the Chase reports for Qatar sites.", "Zola Ngema", conv="C-other")
    _read_in(box, owner, "kim@other-co.example", f"Re: PO name approver to be changed [T-{t.id:04d}]", "Hi, we also need the approver changed for our Bahrain sites please.",
             "Kim Lee", conv="C86", headers=f"In-Reply-To: {first}\r\n")
    with SessionLocal() as s:
        for who in (f"thabo@{DOM}", f"zola@{DOM}", "kim@other-co.example"):
            assert s.query(SupportTicket).filter(SupportTicket.requester_email == who).count() == 1, who
    assert SG._domain("someone@mweb.co.za") == "" and SG._domain("a@telkomsa.net") == "" and SG._domain("b@client-co.example") == "client-co.example"


def test_a_generic_mail_domain_is_never_a_colleague(box, owner):
    _setup(owner)
    req = "pat.one@gmail.com"
    deliver(box, owner, req, "Vendor portal sign-in", "Hi, I cannot sign in to the vendor portal with my supplier login since Monday, please help.", "Pat One", conv="CG")
    t = ticket(req)
    deliver(box, owner, "sam.two@gmail.com", f"Re: Vendor portal sign-in [T-{t.id:04d}]", "Same for me, I also cannot sign in to the vendor portal today.", "Sam Two", conv="CG")
    with SessionLocal() as s:
        assert s.query(SupportTicket).filter(SupportTicket.requester_email == "sam.two@gmail.com").count() == 1


# ---- 4.8 "New — AXL is reading it (since 11:04)" and the 10-minute watchdog ----------------------------------------------
def test_the_new_word_says_since_when_and_the_watchdog_never_leaves_a_ticket_new_over_10_minutes(box, owner, monkeypatch):
    _setup(owner)
    req = f"rina10@{DOM}"
    with monkeypatch.context() as mp:
        mp.setattr(C, "start_reading", lambda db, t: db.commit())  # the reading "hangs": it never starts here
        deliver(box, owner, req, "Chase invoice approval", "Hi, the Chase invoice INV-5501 for PO PO7700112026 still shows after I approved it. Please help.",
                "Rina Patel", conv="CW")
    t = ticket(req)
    assert t.status == "new"
    word = _thread(owner, t.id)["word"]
    assert word.startswith(f"New — {C.ai_name()} is reading it (since ") and word.endswith(")")  # AXL on Edwin's server
    with SessionLocal() as s:  # a reader holds it, still beating (a hung model call)
        x = s.get(SupportTicket, t.id)
        x.busy_bot, x.busy_since, x.busy_beat = "Mo", utcnow(), utcnow()
        s.commit()
        assert C.watch_new(s, now=utcnow() + timedelta(minutes=5)) == []  # 5 minutes: a normal (slow) reading
        assert C.watch_new(s, now=utcnow() + timedelta(minutes=11)) == [(t.id, "rules")]
        s.commit()
    x = ticket(tid=t.id)
    assert x.status != "new" and not x.busy_bot
    assert any("took over 10 minutes" in m["text"] for m in _thread(owner, t.id)["thread"])
    # one nobody holds is read the usual way
    with monkeypatch.context() as mp:
        mp.setattr(C, "start_reading", lambda db, t: db.commit())
        deliver(box, owner, f"rina11@{DOM}", "Chase invoice approval 2", "Hi, the Chase invoice INV-5502 for PO PO7700112027 still shows after I approved it. Please help.",
                "Rina Patel", conv="CW2")
    t2 = ticket(f"rina11@{DOM}")
    assert t2.status == "new"
    with SessionLocal() as s:
        got = C.watch_new(s, now=utcnow() + timedelta(minutes=11))
        s.commit()
    assert (t2.id, "read") in got and ticket(tid=t2.id).status != "new"



# ---- the review's fixes (1.0.111) -----------------------------------------------------------------------------------------------
def test_short_answers_are_never_empty_and_signatures_are():
    for words in ("Hi, Inv55501-26", "Good day, PO9990001234", "4500012345", "Thanks\nInv55501-26", "Hi, done", "Hi Rina,\n\nDone, thanks"):
        assert I.new_words(words), words
    for empty in ("", "Hi\n\nKind regards,\nRina Patel\nT: +971 4 000 0000\n", "Hi Rina,\n\nThanks\nRina", "+27 11 000 0000",
                  "Good day Rina Patel,\n\nRegards\nRina\nMobile: 082 000 0000"):
        assert not I.new_words(empty), empty


def test_a_short_answer_by_mail_is_a_reply(box, owner, monkeypatch):
    _setup(owner)
    _model_says(monkeypatch, {"system": "Chase", "questions": ["What is the invoice number?"], "summary": "x"},
                {"system": "Chase", "questions": [], "summary": "Chase invoice Inv55501-26."})
    req = f"rina12@{DOM}"
    deliver(box, owner, req, "FW: PO PO7700112026 - Chase - Ticket Resolved", T87, "Rina Patel", conv="C87sa")
    t = ticket(req)
    deliver(box, owner, req, f"RE: [T-{t.id:04d}] We need a little more information", "Hi, Inv55501-26\n\nFrom: Support\nSent: Friday\n\nWhat is the invoice number?",
            "Rina Patel", conv="C87sa")
    assert ticket(tid=t.id).status == "ready"


def test_the_repair_on_a_ticket_with_someone_leaves_no_quiet_mark_and_later_mails_go(box, owner, monkeypatch):
    _setup(owner)
    a, ac = _desk(owner, "lead12.r3@test.local", "Lebo Lead")
    req = f"rina13@{DOM}"
    tid, mid = _t87_state(box, owner, monkeypatch, req)
    with SessionLocal() as s:
        C.assign(s, s.get(SupportTicket, tid), s.get(C.User, a), s.get(C.User, _owner_id()))
        s.commit()
        assert I.undo_new_problems(s) == 1
        s.commit()
        t = s.get(SupportTicket, tid)
        assert t.status in ("assigned", "discussing") and not C.is_quiet_read(s, t)
    before = len(to(box, req))
    assert ac.post(f"/api/support/tickets/{tid}/status", json={"status": "closed", "note": "Cleared the stuck approval."}).status_code == 200
    assert len(to(box, req)) == before + 1  # the "done" mail still goes
    # a mark is gone after an hour by itself
    import json

    from cbx import state

    with SessionLocal() as s:
        state.set(s, C.QUIET_READ, json.dumps({str(tid): (utcnow() - timedelta(hours=2)).isoformat()}))
        s.commit()
        assert not C.is_quiet_read(s, s.get(SupportTicket, tid))


def test_the_repair_judges_finished_at_the_time_the_mail_came(box, owner, monkeypatch):
    _setup(owner)
    # finished since the mail came: a note in its history, never reopened
    tid, mid = _t87_state(box, owner, monkeypatch, f"rina14@{DOM}")
    with SessionLocal() as s:
        t = s.get(SupportTicket, tid)
        t.status, t.closed_at, t.resolution = "closed", utcnow() + timedelta(minutes=5), "Cleared."
        s.commit()
        assert I.undo_new_problems(s) == 1
        s.commit()
        t = s.get(SupportTicket, tid)
        assert t.status == "closed" and s.get(SupportMail, mid).outcome == "note_after_all"
        assert any("INV-55501-26" in m.text and m.who == "note" for m in s.query(SupportMessage).filter(SupportMessage.ticket_id == tid))
    # closed with no closed_at: can't tell — left alone
    tid2, mid2 = _t87_state(box, owner, monkeypatch, f"rina15@{DOM}")
    with SessionLocal() as s:
        t = s.get(SupportTicket, tid2)
        t.status, t.closed_at = "closed", None
        s.commit()
        I.undo_new_problems(s)
        s.commit()
        assert s.get(SupportMail, mid2).outcome == "new_problem" and s.get(SupportTicket, tid2).status == "closed"
    # closed when the mail came, reopened since: left alone
    tid3, mid3 = _t87_state(box, owner, monkeypatch, f"rina16@{DOM}")
    with SessionLocal() as s:
        row = s.get(SupportMail, mid3)
        row.at = utcnow() + timedelta(minutes=1)
        s.add(AuditLog(action="support_ticket_closed", target=f"ticket:T-{tid3:04d}", detail="x", at=utcnow()))
        s.commit()
        I.undo_new_problems(s)
        s.commit()
        assert s.get(SupportMail, mid3).outcome == "new_problem" and s.get(SupportTicket, tid3).status == "pending"


def test_a_quiet_reading_never_auto_assigns(box, owner, monkeypatch):
    _setup(owner)
    a, _ = _deskp(owner, "ann12.r3@test.local", "Ann Twelve")
    _done("chase", a, 9, words="Chase invoice still shows after approval")
    chase = _sys("chase")
    owner.post("/api/support/system-people", json={"systems": [{"id": chase, "people": [a]}]})
    owner.post("/api/support/assign-settings", json={"systems": [{"id": chase, "auto": True}], "margin": 3, "min_history": 5})
    tid, mid = _t87_state(box, owner, monkeypatch, f"rina17@{DOM}")
    _model_says(monkeypatch, {"system": "Chase", "questions": [], "summary": "Chase invoice INV-55501-26 still shows after approval."})
    with SessionLocal() as s:
        assert I.undo_new_problems(s) == 1
        s.commit()
        C.read_safely(s, tid)
        s.commit()
    t = ticket(tid=tid)
    assert t.status == "ready" and t.assigned_to_id is None


def test_the_po_number_is_not_the_invoice_number():
    assert C.sharpen(["What is the invoice number?", "What is the PO number?"], None, "") == ["What is the invoice number?", "What is the PO number?"]
    assert C.sharpen(["Which approver should approve the invoice?", "Which approver must approve the invoice?"], None, "") == [
        "Which approver should approve the invoice?"]


def test_the_old_support_lead_add_and_remove_keep_who_works_each_system(box, owner):
    _setup(owner)
    a, _ = _deskp(owner, "ann13.r3@test.local", "Ann Thirteen")
    l_, _ = _desk(owner, "lead13.r3@test.local", "Lulu Lead")
    chase = _sys("chase")
    owner.post("/api/support/system-people", json={"systems": [{"id": chase, "people": [a, l_]}]})
    assert owner.post("/api/support/leads", json={"user_id": a}).status_code == 200  # made a support lead too: no systems sent
    with SessionLocal() as s:
        assert {u.id for u in C.covering(s, chase)} == {a, l_}
    assert owner.delete(f"/api/support/leads/{a}").status_code == 200  # still has the desk grant: keeps Chase
    assert owner.delete(f"/api/support/leads/{l_}").status_code == 200  # no grant: off the desk, off Chase
    with SessionLocal() as s:
        assert [u.id for u in C.covering(s, chase)] == [a]


def test_set_up_refuses_bad_shapes_with_a_400(box, owner):
    _setup(owner)
    chase = _sys("chase")
    for bad in ({"systems": ["x"]}, {"systems": [{"id": chase, "people": "12"}]}, {"systems": [{"id": "abc", "people": []}]}, {"systems": [[chase]]}):
        r = owner.post("/api/support/system-people", json=bad)
        assert r.status_code in (400, 422), (bad, r.status_code, r.text)


def test_the_watchdog_reads_an_over_10_minute_ticket_by_rules_never_a_model(box, owner, monkeypatch):
    _setup(owner)
    with monkeypatch.context() as mp:
        mp.setattr(C, "start_reading", lambda db, t: db.commit())
        deliver(box, owner, f"rina18@{DOM}", "Chase invoice approval 3", "Hi, the Chase invoice INV-5503 for PO PO7700112028 still shows after I approved it. Please help.",
                "Rina Patel", conv="CW3")
    t = ticket(f"rina18@{DOM}")
    later = utcnow() + timedelta(minutes=11)
    with SessionLocal() as s:
        x = s.get(SupportTicket, t.id)
        x.updated_at = later - timedelta(minutes=1)  # touched a minute ago: not "left unread", but new for 11 minutes
        s.commit()
        monkeypatch.setattr(C, "_model", lambda *a: (_ for _ in ()).throw(AssertionError("no model reading in the watchdog")))
        assert C.watch_new(s, now=later) == [(t.id, "rules")]
        s.commit()
    assert ticket(tid=t.id).status != "new"
