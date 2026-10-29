"""1.0.103 (from CBX-Connect 2.10.8; Edwin, 19 Oct): "if it's not assigned, another person could close it — make it so they
can only see theirs that has been assigned" and "let them grab any unassigned tickets, so if they can do it, they can take it
and finish it". On the old desk: the support team (support leads and the Support desk grant — not the owner or an admin)
sees its own tickets and the free ones; takes a free one; finishes only its own; may give it back. The owner and admins
keep the whole desk. Outlook through outlook.Fake. Names are made up."""
import re

from fastapi.testclient import TestClient

from cbx.db import SessionLocal
from cbx.main import app
from cbx.models import AuditLog, SupportMail, SupportMessage, SupportTicket, User
from cbx.support import core as C
from cbx.support import own as OWN

from .conftest import _make, login
from .test_support_1098 import GENERATOR, _setup, box, deliver, lead, sig, ticket, to, uid  # noqa: F401

A_MAIL, B_MAIL, C_MAIL = "anele.1103@test.local", "bongani.1103@test.local", "carol.1103@test.local"


def _desk_person(owner, email, name):
    desk = next(t["id"] for t in owner.get("/api/users").json()["tools"] if t["slug"] == "support-desk")
    pw = _make(owner, email, name, "manager", tools=[desk])
    c = TestClient(app)
    login(c, email, pw)
    return uid(email), c


def _staff(owner):
    """A: a support lead (covers CBX-Connect); B: the Support desk grant. Both are 'the rest of the desk'."""
    a_id, a = lead(owner, A_MAIL, "Anele Lead", systems=("cbx-connect",), client=True)
    b_id, b = _desk_person(owner, B_MAIL, "Bongani Desk")
    return a, b


def _ready(fake, owner, sender, subject):
    deliver(fake, owner, sender, subject, GENERATOR + sig("Omar Hassan"), "Omar Hassan", conv="C1103-" + subject)
    with SessionLocal() as s:
        t = s.query(SupportTicket).filter(SupportTicket.requester_email == sender, SupportTicket.title.like(f"%{subject}%")).order_by(SupportTicket.id.desc()).first()
        assert t is not None and t.status == "ready", (t and t.status, t and t.missing)
        return t.id


def _queue(c):
    r = c.get("/api/support/queue?status=all")
    assert r.status_code == 200, r.text
    return {x["id"] for x in r.json()["tickets"]}, r.json()


def _last_audit() -> int:
    with SessionLocal() as s:
        return s.query(AuditLog.id).order_by(AuditLog.id.desc()).limit(1).scalar() or 0


def _audits(action, tid, since) -> int:
    with SessionLocal() as s:
        return s.query(AuditLog).filter(AuditLog.id > since, AuditLog.action == action, AuditLog.target == f"ticket:T-{tid:04d}").count()


def _notes(tid):
    with SessionLocal() as s:
        return [m.text for m in s.query(SupportMessage).filter(SupportMessage.ticket_id == tid, SupportMessage.who == "note")]


def _cbx_system(tid):
    """Put the ticket on CBX-Connect (the system lead A covers: before 1.0.103 A saw every ticket of it)."""
    with SessionLocal() as s:
        from cbx.models import SupportSystem

        s.get(SupportTicket, tid).system_id = s.query(SupportSystem).filter(SupportSystem.key == "cbx-connect").one().id
        s.commit()


def test_the_team_sees_its_own_and_the_free_ones_never_someone_elses(box, owner):  # noqa: F811
    _setup(owner)
    a, b = _staff(owner)
    carol = lead(owner, C_MAIL, "Carol Lead", systems=("cbx-connect",))
    free = _ready(box, owner, "omar1103@client.example", "Generator one")
    bs = _ready(box, owner, "omar1103b@client.example", "Generator two")
    cs = _ready(box, owner, "omar1103c@client.example", "Generator three")
    for x in (free, bs, cs):
        _cbx_system(x)
    assert b.post(f"/api/support/tickets/{bs}/take").status_code == 200
    assert owner.post(f"/api/support/tickets/{cs}/assign", json={"user_id": carol}).status_code == 200
    ids, r = _queue(a)
    assert free in ids and bs not in ids and cs not in ids and r["own_only"] is True  # a lead no longer sees their system's other tickets
    d = a.get("/api/support/dashboard").json()
    assert d["totals"]["all"] == 1 and d["totals"]["ready"] == 1 and d["own_only"] is True and d["nudge"] == {"waiting": 0, "over48": 0}
    for path, body in ((f"/api/support/tickets/{bs}", None), (f"/api/support/tickets/{bs}/status", {"status": "closed", "note": "Fixed it all."}),
                       (f"/api/support/tickets/{bs}/message", {"text": "Hello there", "to_requester": True}), (f"/api/support/tickets/{bs}/give-back", {}),
                       (f"/api/support/tickets/{bs}/system", {"system_id": None}), (f"/api/support/tickets/{bs}/accept", {}), (f"/api/support/tickets/{cs}", None),
                       (f"/api/support/tickets/{cs}/files/1", None)):
        rr = a.get(path) if body is None else a.post(path, json=body)
        assert rr.status_code == 404, (path, rr.status_code, rr.text)
    ids_b, rb = _queue(b)
    assert {free, bs} <= ids_b and cs not in ids_b and b.get("/api/support/dashboard").json()["totals"]["all"] == 2
    ids_o, ro = _queue(owner)
    assert {free, bs, cs} <= ids_o and ro["own_only"] is False and owner.get("/api/support/dashboard").json()["totals"]["all"] >= 3
    # the Mailer: the mail that opened B's ticket isn't listed for A (nor opened by its id); the owner sees it
    with SessionLocal() as s:
        entry = s.query(SupportMail).filter(SupportMail.ticket_id == bs).first().entry_id
    assert entry not in {m["entry_id"] for m in a.get(f"/api/mailer/box?store={box_store()}").json()["mail"]}
    assert entry in {m["entry_id"] for m in owner.get(f"/api/mailer/box?store={box_store()}").json()["mail"]}
    assert a.get("/api/mailer/mail", params={"entry_id": entry}).status_code == 404


def box_store():
    from .test_support_1098 import BOX

    return BOX


def test_take_makes_it_theirs_and_the_second_hears_who_was_first(box, owner):  # noqa: F811
    _setup(owner)
    a, b = _staff(owner)
    tid = _ready(box, owner, "omar1103@client.example", "Pump room")
    since = _last_audit()
    one = a.get(f"/api/support/tickets/{tid}").json()
    assert one["can_take"] and one["free"] and not one["can_work"] and not one["can_give_back"]
    r = a.post(f"/api/support/tickets/{tid}/take")
    assert r.status_code == 200, r.text
    t = ticket(tid=tid)
    assert t.status == "assigned" and t.assigned_to_id == uid(A_MAIL) and t.accepted_at is not None and t.todo_id  # accepted: on their To-do
    view = a.get(f"/api/support/tickets/{tid}").json()
    assert view["can_work"] and view["can_give_back"] and not view["can_take"]
    assert "Taken by Anele Lead." in _notes(tid)
    assert _audits("support_taken", tid, since) == 1 and _audits("support_ticket_assigned", tid, since) == 1  # the assign path
    assert any("Assigned to you" in m["subject"] for m in to(box, A_MAIL))  # the lead's ticket mail
    r2 = b.post(f"/api/support/tickets/{tid}/take")
    assert r2.status_code == 409 and r2.json()["detail"] == "Already taken by Anele Lead."
    assert ticket(tid=tid).assigned_to_id == uid(A_MAIL)


def test_two_takes_at_once_the_update_is_conditional_not_read_then_write(box, owner):  # noqa: F811
    _setup(owner)
    _staff(owner)
    tid = _ready(box, owner, "omar1103@client.example", "Lift motor")
    s1, s2 = SessionLocal(), SessionLocal()
    try:
        t1, t2 = s1.get(SupportTicket, tid), s2.get(SupportTicket, tid)  # both read it while it was free
        a = s1.query(User).filter(User.email == A_MAIL).one()
        b = s2.query(User).filter(User.email == B_MAIL).one()
        assert OWN.is_free(t1) and OWN.is_free(t2)
        OWN.take(s1, t1, a)
        s1.commit()
        try:
            OWN.take(s2, t2, b)
            raise AssertionError("the second take went through")
        except OWN.Taken as e:
            assert str(e) == "Already taken by Anele Lead."
        s2.rollback()
    finally:
        s1.close()
        s2.close()
    assert ticket(tid=tid).assigned_to_id == uid(A_MAIL)


def test_only_the_assignee_finishes_it_and_a_free_one_is_taken_first(box, owner):  # noqa: F811
    _setup(owner)
    a, b = _staff(owner)
    tid = _ready(box, owner, "omar1103@client.example", "Chiller")
    r = b.post(f"/api/support/tickets/{tid}/status", json={"status": "closed", "note": "Fixed the valve."})
    assert r.status_code == 403 and "Take it first" in r.json()["detail"]
    r = b.post(f"/api/support/tickets/{tid}/message", json={"text": "We are on it.", "to_requester": True})
    assert r.status_code == 403 and "Take it first" in r.json()["detail"]
    assert b.post(f"/api/support/tickets/{tid}/message", json={"text": "Looks like the valve.", "to_requester": False}).status_code == 200  # a note is fine
    assert b.post(f"/api/support/tickets/{tid}/take").status_code == 200
    assert a.post(f"/api/support/tickets/{tid}/status", json={"status": "closed", "note": "x" * 10}).status_code == 404
    assert b.post(f"/api/support/tickets/{tid}/status", json={"status": "closed", "note": "Fixed the valve."}).status_code == 200
    assert ticket(tid=tid).status == "closed"
    assert tid in _queue(b)[0] and tid not in _queue(a)[0]  # a closed ticket that was theirs stays in their list


def test_give_back_makes_it_free_and_the_old_links_die(box, owner):  # noqa: F811
    _setup(owner)
    a, b = _staff(owner)
    tid = _ready(box, owner, "omar1103@client.example", "Fire panel")
    assert b.post(f"/api/support/tickets/{tid}/take").status_code == 200
    mail = [m for m in to(box, B_MAIL) if "Assigned to you" in m["subject"]][-1]
    b_done = re.search(r"/api/support/done/([A-Za-z0-9_\-=]+)", mail["body"]).group(0)
    todo = ticket(tid=tid).todo_id
    since = _last_audit()
    assert a.post(f"/api/support/tickets/{tid}/give-back").status_code == 404  # not A's
    r = b.post(f"/api/support/tickets/{tid}/give-back")
    assert r.status_code == 200, r.text
    t = ticket(tid=tid)
    assert t.status == "ready" and t.assigned_to_id is None and not t.accept_hash and t.accepted_at is None and t.todo_id is None
    assert todo and _audits("support_released", tid, since) == 1 and any(n.startswith("Given back by Bongani Desk.") for n in _notes(tid))
    assert tid in _queue(a)[0] and tid in _queue(b)[0]  # free again: everyone on the desk sees it
    assert b.post(f"/api/support/tickets/{tid}/give-back").status_code == 400  # nothing to give back now
    anon = TestClient(app)
    assert "isn't assigned to you" in anon.get(b_done).text
    # A takes it: B's old Mark as done does nothing to A's ticket
    assert a.post(f"/api/support/tickets/{tid}/take").status_code == 200
    page = anon.post(b_done, data={"note": "Done by Bongani."})
    assert "isn't assigned to you" in page.text and ticket(tid=tid).status == "assigned" and ticket(tid=tid).assigned_to_id == uid(A_MAIL)


def test_managers_see_everything_and_still_assign(box, owner, admin):  # noqa: F811
    _setup(owner)
    a, b = _staff(owner)
    tid = _ready(box, owner, "omar1103@client.example", "Boiler")
    b.post(f"/api/support/tickets/{tid}/take")
    for m in (owner, admin):
        assert m.get(f"/api/support/tickets/{tid}").status_code == 200 and tid in _queue(m)[0]
    assert admin.post(f"/api/support/tickets/{tid}/assign", json={"user_id": uid(A_MAIL)}).status_code == 200  # reassigned to A
    assert b.get(f"/api/support/tickets/{tid}").status_code == 404 and a.get(f"/api/support/tickets/{tid}").status_code == 200
    assert a.post(f"/api/support/tickets/{tid}/assign", json={"user_id": uid(B_MAIL)}).status_code == 403  # the team never assigns
    assert owner.post(f"/api/support/tickets/{tid}/status", json={"status": "closed", "note": "Closed by the owner."}).status_code == 200


def test_a_mail_from_the_team_about_a_free_ticket_is_not_passed_to_the_requester(box, owner):  # noqa: F811
    _setup(owner)
    a, b = _staff(owner)
    tid = _ready(box, owner, "omar1103@client.example", "Sprinkler")
    before = len(to(box, "omar1103@client.example"))
    deliver(box, owner, B_MAIL, f"RE: [T-{tid:04d}] Sprinkler", "Which floor is it on?", "Bongani Desk")
    assert len(to(box, "omar1103@client.example")) == before and any("not passed on" in n for n in _notes(tid))
    b.post(f"/api/support/tickets/{tid}/take")
    deliver(box, owner, B_MAIL, f"RE: [T-{tid:04d}] Sprinkler", "Which floor is the sprinkler on, please?", "Bongani Desk")
    assert len(to(box, "omar1103@client.example")) > before  # the assignee's question goes to the requester


# ---- 1.0.103: Not relevant — close (Edwin: "if it's not relevant I need to be able to close it, without having to assign it")
def test_the_owner_closes_a_free_ticket_as_not_relevant_without_assigning_it(box, owner):  # noqa: F811
    _setup(owner)
    a, b = _staff(owner)
    tid = _ready(box, owner, "omar1103@client.example", "Lottery winner")
    closed_before = owner.get("/api/support/dashboard").json()["totals"]["closed"]
    mails_before, since = len(to(box, "omar1103@client.example")), _last_audit()
    assert owner.get(f"/api/support/tickets/{tid}").json()["can_not_relevant"] is True
    assert a.get(f"/api/support/tickets/{tid}").json()["can_not_relevant"] is False  # the support team never gets it
    assert a.post(f"/api/support/tickets/{tid}/not-relevant", json={"reason": "mine"}).status_code == 403
    r = owner.post(f"/api/support/tickets/{tid}/not-relevant", json={"reason": "spam"})
    assert r.status_code == 200, r.text
    t = ticket(tid=tid)
    assert t.status == "closed" and t.closed_as == "not_relevant" and t.resolution == "Not relevant: spam" and t.assigned_to_id is None
    assert r.json()["not_relevant"] is True and r.json()["word"] == "Not relevant"
    assert "Closed as not relevant by Test Owner: spam" in _notes(tid) and _audits("support_closed_not_relevant", tid, since) == 1
    assert len(to(box, "omar1103@client.example")) == mails_before  # not ticked: the requester isn't mailed
    assert not any("isn't something the support desk handles" in m["body"] for m in to(box, "omar1103@client.example"))
    # it leaves every queue and count, and is never a solved ticket
    assert tid not in {x["id"] for x in owner.get("/api/support/queue?status=open").json()["tickets"]} and tid not in _queue(a)[0] and tid not in _queue(b)[0]
    dash = owner.get("/api/support/dashboard").json()
    assert dash["totals"]["closed"] == closed_before and dash["totals"]["closed_30d"] <= closed_before
    assert owner.post(f"/api/support/tickets/{tid}/not-relevant", json={}).status_code == 400  # closed already
    # reopen works as for any closed ticket (the requester writes again) and it is an ordinary ticket again
    deliver(box, owner, "omar1103@client.example", f"RE: [T-{tid:04d}] Lottery winner", "No really, the generator at Nile Tower is down. Please help.", "Omar Hassan")
    t = ticket(tid=tid)
    assert t.status != "closed" and not t.closed_as


def test_closing_an_assigned_ticket_as_not_relevant_closes_the_todo_and_kills_the_links(box, owner, admin):  # noqa: F811
    _setup(owner)
    a, b = _staff(owner)
    tid = _ready(box, owner, "omar1103@client.example", "Duplicate chiller")
    assert b.post(f"/api/support/tickets/{tid}/take").status_code == 200
    mail = [m for m in to(box, B_MAIL) if "Assigned to you" in m["subject"]][-1]
    b_done = re.search(r"/api/support/done/([A-Za-z0-9_\-=]+)", mail["body"]).group(0)
    b_accept = re.search(r"/api/support/accept/([A-Za-z0-9_\-]{40,})", mail["body"])
    assert ticket(tid=tid).todo_id
    assert b.post(f"/api/support/tickets/{tid}/not-relevant", json={"reason": "dup"}).status_code == 403  # not even the assignee
    r = admin.post(f"/api/support/tickets/{tid}/not-relevant", json={"reason": "duplicate of T-0001", "tell": True})
    assert r.status_code == 200, r.text
    t = ticket(tid=tid)
    assert t.status == "closed" and t.closed_as == "not_relevant" and t.assigned_to_id is None and not t.accept_hash and t.todo_id is None
    assert "(it was with Bongani Desk)" in " ".join(_notes(tid))
    anon = TestClient(app)
    assert "isn't assigned to you" in anon.post(b_done, data={"note": "Done by Bongani."}).text  # Mark as done is dead
    if b_accept:
        assert "expired" in anon.post(b_accept.group(0)).text  # and Accept
    assert tid not in _queue(b)[0] and b.get(f"/api/support/tickets/{tid}").status_code == 404
    told = [m for m in to(box, "omar1103@client.example") if "because it isn't something the support desk handles" in m["body"]]  # in their own mail thread
    assert len(told) == 1 and f"We closed your request T-{tid:04d}" in told[0]["body"] and "Reason: duplicate of T-0001" in told[0]["body"]
    assert r.json()["resolution"] == "Not relevant: duplicate of T-0001"  # the desk sees the reason


def test_c_exa_marks_not_for_support_and_the_owner_closes_it_from_the_list(box, owner):  # noqa: F811
    _setup(owner)
    tid = _ready(box, owner, "omar1103@client.example", "Lunch menu")
    with SessionLocal() as s:  # as C-EXA's reading leaves a mail it thinks isn't a support request (core._triage, the quiet branch)
        s.get(SupportTicket, tid).summary = "Probably not for support: a lunch menu for Friday."
        s.commit()
    row = next(x for x in owner.get("/api/support/queue").json()["tickets"] if x["id"] == tid)
    assert row["not_support"] is True
    assert owner.post(f"/api/support/tickets/{tid}/not-relevant", json={"reason": "not a support request"}).status_code == 200
    assert next(x for x in owner.get("/api/support/queue?status=closed").json()["tickets"] if x["id"] == tid)["word"] == "Not relevant"


# ---- 1.0.103 review fixes ------------------------------------------------------------------------------------------------
def _reading_while(monkeypatch, act):
    """C-EXA's readers read for a while; `act(ticket_id)` is what a person does meanwhile (in their own request)."""
    def slow_model(db, t, asked):
        db.commit()  # as the real reading: never hold a write while a model thinks
        act(t.id)
        return {"kind": "request", "title": t.title, "summary": "Generator fails", "questions": ["Which floor is it on?"], "where": "", "outcome": ""}

    monkeypatch.setattr(C, "_model", slow_model)


def _app_ticket(c, text="The generator at Nile Tower fails its weekly test since Friday."):
    systems = c.get("/api/support/systems").json()["systems"]
    s = next(x for x in systems if x["key"] == "cbx-connect")
    prob = s["problems"][-1]
    answers = {f["key"]: "x" for f in prob.get("fields", []) if f.get("required")}
    r = c.post("/api/support/tickets", json={"system": "cbx-connect", "problem": prob["key"], "description": text, "answers": answers})
    assert r.status_code == 200, r.text
    return r.json()["ticket"]["id"]


def test_a_take_while_the_reader_reads_stands_and_the_reading_is_dropped(box, owner, monkeypatch):  # noqa: F811
    _setup(owner)
    a, b = _staff(owner)
    _reading_while(monkeypatch, lambda tid: a.post(f"/api/support/tickets/{tid}/take"))
    since = _last_audit()
    tid = _app_ticket(owner)
    t = ticket(tid=tid)
    assert t.status == "assigned" and t.assigned_to_id == uid(A_MAIL) and not t.missing  # not "pending" over A's take
    assert _audits("support_ticket_asked", tid, since) == 0 and _audits("support_ticket_ready", tid, since) == 0


def test_a_not_relevant_close_while_the_reader_reads_stands(box, owner, monkeypatch):  # noqa: F811
    _setup(owner)
    _reading_while(monkeypatch, lambda tid: owner.post(f"/api/support/tickets/{tid}/not-relevant", json={"reason": "spam"}))
    tid = _app_ticket(owner)
    t = ticket(tid=tid)
    assert t.status == "closed" and t.closed_as == "not_relevant"


def test_an_owners_assignment_never_answers_409_to_the_team(box, owner):  # noqa: F811
    _setup(owner)
    a, b = _staff(owner)
    tid = _ready(box, owner, "omar1103@client.example", "Cooling tower")
    assert a.get(f"/api/support/tickets/{tid}").json()["can_take"]  # A sees it free ...
    assert owner.post(f"/api/support/tickets/{tid}/assign", json={"user_id": uid(B_MAIL)}).status_code == 200
    r = a.post(f"/api/support/tickets/{tid}/take")  # ... the owner gives it to B before A presses: no name, no 409
    assert r.status_code == 404 and "Bongani" not in r.text


def test_the_team_tab_hides_lines_about_other_peoples_tickets(box, owner):  # noqa: F811
    from cbx import compat2

    _setup(owner)
    a, b = _staff(owner)
    bs = _ready(box, owner, "omar1103@client.example", "Pressure valve")
    b.post(f"/api/support/tickets/{bs}/take")
    compat2.say("support", f"T-{bs:04d} accepted by Bongani Desk: Pressure valve", who="Ada", ref=f"ticket:{bs}")  # with its ref
    compat2.say("support", f"Checked the reading of T-{bs:04d} (Pressure valve)", who="Ada")  # bare, no ref
    seen = a.get("/api/support/team").text
    assert f"T-{bs:04d}" not in seen and "Pressure valve" not in seen
    assert f"T-{bs:04d}" in owner.get("/api/support/team").text  # the owner and admins see it all


def test_a_ticket_the_team_only_raised_shows_them_the_requester_view(box, owner):  # noqa: F811
    _setup(owner)
    a, b = _staff(owner)
    tid = _app_ticket(a)
    with SessionLocal() as s:
        s.get(SupportTicket, tid).status = "ready"
        s.commit()
    assert owner.post(f"/api/support/tickets/{tid}/assign", json={"user_id": uid(B_MAIL)}).status_code == 200
    v = a.get(f"/api/support/tickets/{tid}").json()
    assert v["mine"] and not v["can_work"] and "leads" not in v and "requester_email" not in v and not v["can_take"]
    assert not any(m.get("who") == "note" for m in v["thread"])  # the desk's notes are not theirs
    assert tid not in _queue(a)[0]
    assert a.post(f"/api/support/tickets/{tid}/system", json={"system_id": None}).status_code == 404


def test_give_back_and_not_relevant_drop_the_todo_rather_than_finish_it(box, owner, admin):  # noqa: F811
    _setup(owner)
    a, b = _staff(owner)
    store, cols = C._todo()
    t1 = _ready(box, owner, "omar1103@client.example", "Door one")
    t2 = _ready(box, owner, "omar1103b@client.example", "Door two")
    for tid in (t1, t2):
        assert b.post(f"/api/support/tickets/{tid}/take").status_code == 200
    todo1, todo2 = ticket(tid=t1).todo_id, ticket(tid=t2).todo_id
    assert todo1 and todo2 and store.get("tasks", cols, todo1)
    b.post(f"/api/support/tickets/{t1}/give-back")
    admin.post(f"/api/support/tickets/{t2}/not-relevant", json={"reason": "dup"})
    assert store.get("tasks", cols, todo1) is None and store.get("tasks", cols, todo2) is None  # gone, never "Done"
