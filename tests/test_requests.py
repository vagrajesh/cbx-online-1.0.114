"""Requests → C-EXA assessment → approval → interview → ticket → workflow. Stub mode, inline jobs."""
from fastapi.testclient import TestClient

from cbx.main import app
from cbx.models import LoginSession, User
from cbx.security.auth import SESSION_COOKIE

from tests.conftest import _make, login

PART1 = {
    "problem": "Every month-end I open about 40 Absa utility bills one by one, type the totals into the master sheet, then check them against the SAP bill list.",
    "impact": "Me and Arnab, every month. Last quarter two bills were missed and paid late — R 3 400 in penalties.",
    "wish": "The bills get read and added to the master sheet on their own, anything that doesn't match SAP goes in an exceptions tab, and I get one email when it's done.",
}


def _send(user, **over):
    """1.0.66: only the owner and admins open requests (everyone else opens a support ticket); the requester's
    side — interview, cancel, privacy — still works for the requests people opened before, so they are made here."""
    from cbx import requests_flow as F
    from cbx.db import SessionLocal
    from cbx.models import Request

    body = {**PART1, **over}
    me = user.get("/api/auth/me").json()["user"]
    db = SessionLocal()
    try:
        req = Request(number=F.next_number(db), user_id=me["id"], team_id=me["team_id"], problem=body["problem"], impact=body.get("impact", ""), wish=body.get("wish", ""), status="sent")
        db.add(req)
        db.commit()
        F.assess(db, req)
        db.commit()
        return F.request_out(db, req, full=True)
    finally:
        db.close()


def _admin_id(admin):
    return admin.get("/api/auth/me").json()["user"]["id"]


def _approve(admin, rid, mode="axl"):
    r = admin.post(f"/api/requests/{rid}/approve", json={"mode": mode, "assignee_id": _admin_id(admin)})
    assert r.status_code == 200, r.text
    return r.json()


def test_create_assessed_and_queue_is_admin_only(user, admin):
    assert user.post("/api/requests", json=PART1).status_code == 403  # 1.0.66: Ticket request is theirs
    assert admin.post("/api/requests", json=PART1).status_code == 200
    req = _send(user)
    assert req["number"] >= 2001
    assert req["status"] == "new"  # assessed inline in stub mode
    assert user.get("/api/requests/queue").status_code == 403
    q = admin.get("/api/requests/queue").json()
    row = next(x for x in q["requests"] if x["id"] == req["id"])
    assert row["importance"] in ("low", "medium", "high", "critical")
    assert 0 <= row["confidence"] <= 1
    assert row["title"]
    assert q["counts"]["new"] >= 1
    mine = user.get("/api/requests/mine").json()["requests"]
    assert mine[0]["id"] == req["id"] and mine[0]["status_word"] == "New"
    # the user never sees C-EXA's scores
    d = user.get(f"/api/requests/{req['id']}").json()
    assert "confidence" not in d and d["problem"] == PART1["problem"]


def test_second_number_increments(user):
    a = _send(user)
    b = _send(user)
    assert b["number"] == a["number"] + 1


def test_cexa_interview_to_ticket_to_ready(user, admin):
    req = _send(user)
    out = _approve(admin, req["id"], "axl")
    assert out["request"]["status"] == "interview"
    iv = out["interview"]
    assert iv["mode"] == "axl" and iv["messages"][0]["sender"] == "axl"
    assert "Hi Thandi, I'm C-EXA" in iv["messages"][0]["text"]
    # the user sees it under Messages
    convs = user.get("/api/interviews").json()
    assert any(c["id"] == iv["id"] for c in convs["interviews"])

    answers = [
        "Yes go ahead",
        "Client code starts with ABS, it's on the top right of every bill",
        "Hold it back, put it in an exceptions tab so I can look",
        "They do re-issue sometimes. The newer one replaces the old one",
        "Check VAT is 15%, flag it if not, don't fix it yourself",
        "Me and Arnab, every month-end, into the master sheet. Happy with that",
    ]
    last = None
    for a in answers:
        last = user.post(f"/api/interviews/{iv['id']}/reply", json={"text": a})
        assert last.status_code == 200, last.text
        last = last.json()
    assert last["done"] is True
    rules = last["rules"]
    assert len(rules) == 5 and all(r["green"] and r["source"] == "user" for r in rules)
    assert [r["ordinal"] for r in rules] == [1, 2, 3, 4, 5]
    assert last["ticket_id"]
    assert "ticket #" in last["message"]["text"]

    d = admin.get(f"/api/requests/{req['id']}").json()
    assert d["status"] == "working" and d["ticket_id"] == last["ticket_id"]
    assert d["cadence"] == "monthly" and d["result_kind"] == "filled_sheet"

    # user cannot see tickets; admin can
    assert user.get(f"/api/tickets/{last['ticket_id']}").status_code == 403
    t = admin.get(f"/api/tickets/{last['ticket_id']}").json()
    assert t["status"] == "working" and t["tool_slug"] == "" and t["tool"] is None and "Rules:" in t["summary"]

    # testing needs the tool that answers it; live needs that tool to be live
    assert admin.post(f"/api/tickets/{t['id']}/test").status_code == 400
    assert admin.patch(f"/api/tickets/{t['id']}", json={"tool_slug": "Not A Slug"}).status_code == 400
    r = admin.patch(f"/api/tickets/{t['id']}", json={"tool_slug": "absa-month-end"})
    assert r.status_code == 200 and r.json()["ticket"]["tool"] is None
    r = admin.post(f"/api/tickets/{t['id']}/test", json={"note": "in sandbox B"}).json()
    assert r["ticket"]["status"] == "testing"
    assert user.get("/api/requests/mine").json()["requests"][0]["status"] == "testing"
    r = admin.post(f"/api/tickets/{t['id']}/publish")
    assert r.status_code == 400 and "not live yet" in r.json()["detail"]

    # the tool is live (a folder in the running app) → ready, requester told where it is
    admin.patch(f"/api/tickets/{t['id']}", json={"tool_slug": "entity-compliance"})
    r = admin.post(f"/api/tickets/{t['id']}/publish")
    assert r.status_code == 200, r.text
    assert r.json()["ticket"]["status"] == "ready" and r.json()["ticket"]["tool"]["slug"] == "entity-compliance"
    mine = user.get("/api/requests/mine").json()["requests"][0]
    assert mine["status"] == "ready" and mine["status_word"] == "Ready"
    thread = user.get(f"/api/interviews/{iv['id']}").json()["interview"]["messages"]
    assert "is live" in thread[-1]["text"]
    from cbx.db import SessionLocal
    from cbx.models import CexaMessage

    with SessionLocal() as s:
        note = s.query(CexaMessage).filter(CexaMessage.title.like("Your request #% is live")).order_by(CexaMessage.id.desc()).first()
        assert note is not None and note.link.startswith("#/tool?t=entity-compliance")

    # follow-up chat still works after the ticket exists
    r = user.post(f"/api/interviews/{iv['id']}/reply", json={"text": "Thanks! One more: from now on the email should also go to Priya"}).json()
    assert r["message"]["sender"] == "axl" and len(r["rules"]) == 6

    # tickets list + home counts
    assert any(x["id"] == t["id"] and x["status"] == "ready" for x in admin.get("/api/tickets").json()["tickets"])
    counts = admin.get("/api/home/counts").json()
    assert "tickets_open" in counts


def test_human_interview_rules_and_finish(user, admin):
    req = _send(user, problem="Split scanned lease packs per property — they are scanned with mixed layouts and I do it by hand each week.")
    out = _approve(admin, req["id"], "human")
    iv = out["interview"]
    assert iv["mode"] == "human"
    # the user can't type into a meeting-mode interview while it's open
    assert user.post(f"/api/interviews/{iv['id']}/reply", json={"text": "hello"}).status_code == 400

    rid = req["id"]
    r1 = admin.post(f"/api/requests/{rid}/rules", json={"text": "A lease pack is one PDF per property", "source": "user"}).json()["rule"]
    r2 = admin.post(f"/api/requests/{rid}/rules", json={"text": "Renewal date = Lease end on the schedule page", "source": "admin"}).json()["rule"]
    assert r1["green"] and not r2["green"]
    s = admin.get(f"/api/interviews/{iv['id']}/suggest").json()
    assert len(s["questions"]) == 3 and all(q["text"] and q["why"] for q in s["questions"])
    assert abs(s["confidence"] - 0.51) < 1e-6
    admin.post(f"/api/interviews/{iv['id']}/asked", json={"text": s["questions"][0]["text"]})

    # admin edits: stays admin-coloured; user edits: turns green + audited
    p = admin.patch(f"/api/requests/{rid}/rules/{r2['id']}", json={"text": "Renewal date = 'Lease end' on the schedule page"}).json()["rule"]
    assert not p["green"] and len(p["history"]) == 2
    p = user.patch(f"/api/requests/{rid}/rules/{r2['id']}", json={"text": "Renewal date = 'Lease end' on page 2"}).json()["rule"]
    assert p["green"] and p["changed_by_user"]
    r3 = admin.post(f"/api/requests/{rid}/rules", json={"text": "temporary"}).json()["rule"]
    assert admin.delete(f"/api/requests/{rid}/rules/{r3['id']}").json()["ok"]
    assert len(admin.get(f"/api/requests/{rid}/rules").json()["rules"]) == 2
    u = user.post(f"/api/requests/{rid}/rules", json={"text": "Flag anything ending within 6 months"}).json()["rule"]
    assert u["source"] == "user" and u["green"]

    f = admin.post(f"/api/interviews/{iv['id']}/finish")
    assert f.status_code == 200, f.text
    assert f.json()["ticket"]["status"] == "working"
    assert admin.get(f"/api/requests/{rid}").json()["status"] == "working"
    assert admin.post(f"/api/interviews/{iv['id']}/finish").status_code == 400
    # meeting notes reached the requester's thread; they can now chat
    thread = user.get(f"/api/interviews/{iv['id']}").json()["interview"]["messages"]
    assert thread and "ticket #" in thread[-1]["text"]
    audit = admin.get("/api/audit").json()["rows"]
    assert {"rule_added", "rule_changed", "rule_removed", "ticket_created"} <= {a["action"] for a in audit}


def test_decline(user, admin):
    req = _send(user)
    r = admin.post(f"/api/requests/{req['id']}/decline", json={"note": "We already have this in Vendor recon."})
    assert r.status_code == 200 and r.json()["request"]["status"] == "declined"
    mine = user.get("/api/requests/mine").json()["requests"][0]
    assert mine["status_word"] == "Declined"
    notes = user.get("/api/interviews").json()["notes"]
    assert any("Vendor recon" in n["text"] for n in notes)
    assert admin.post(f"/api/requests/{req['id']}/approve", json={"mode": "axl"}).status_code == 400


def test_cexa_cannot_publish_or_approve(user, admin, db):
    req = _send(user)
    _approve(admin, req["id"], "human")
    admin.post(f"/api/requests/{req['id']}/rules", json={"text": "One rule"})
    iid = admin.get(f"/api/requests/{req['id']}").json()["interview_id"]
    tid = admin.post(f"/api/interviews/{iid}/finish").json()["ticket"]["id"]
    cexa = db.query(User).filter(User.is_axl.is_(True)).one()
    db.add(LoginSession(id="axl-test-session", user_id=cexa.id))
    db.commit()
    c = TestClient(app)
    c.cookies.set(SESSION_COOKIE, "axl-test-session")
    assert c.get("/api/auth/me").json()["user"]["is_axl"] is True
    r = c.post(f"/api/tickets/{tid}/publish")
    assert r.status_code == 403 and "person" in r.json()["detail"]
    fresh = _send(user)
    assert c.post(f"/api/requests/{fresh['id']}/approve", json={"mode": "axl"}).status_code == 403
    # C-EXA may still mark it as testing — that's not a promotion
    admin.patch(f"/api/tickets/{tid}", json={"tool_slug": "some-tool"})
    assert c.post(f"/api/tickets/{tid}/test").status_code == 200


def test_rls_other_user_cannot_read(user, admin, owner):
    req = _send(user)
    _approve(admin, req["id"], "axl")
    iid = admin.get(f"/api/requests/{req['id']}").json()["interview_id"]
    pw = _make(owner, "lerato@test.local", "Lerato Dlamini", "user")
    b = TestClient(app)
    login(b, "lerato@test.local", pw)
    assert b.get(f"/api/requests/{req['id']}").status_code == 404
    assert b.get(f"/api/interviews/{iid}").status_code == 404
    assert b.post(f"/api/interviews/{iid}/reply", json={"text": "hi"}).status_code == 404
    assert b.post(f"/api/requests/{req['id']}/rules", json={"text": "sneaky"}).status_code == 404
    assert all(c["id"] != iid for c in b.get("/api/interviews").json()["interviews"])
    assert b.get("/api/requests/mine").json()["requests"] == []
    assert b.get("/api/tickets").status_code == 403


def test_cancel_request_at_any_stage(owner, user, db):
    """A requester or admin can cancel even after approval/build; a live tool is switched off."""
    from cbx.models import Request, Ticket, Tool

    r = _send(user, problem="Every month I retype 40 Absa bills, it takes a day.", impact="Me and Arnab, monthly, late-payment penalties.", wish="Read them into the master sheet.")
    rid = r["id"]
    # approve → C-EXA interview → keeps going; then cancel from the user side
    owner.post(f"/api/requests/{rid}/approve", json={"mode": "axl"})
    db.expire_all()
    assert db.get(Request, rid).status in ("interview", "working")
    out = user.post(f"/api/requests/{rid}/cancel", json={"note": "not needed anymore"})
    assert out.status_code == 200, out.text
    db.expire_all()
    req = db.get(Request, rid)
    assert req.status == "cancelled" and req.decline_note == "not needed anymore"
    if req.ticket:
        assert req.ticket.status == "cancelled"
    # closed requests can't be cancelled twice
    assert user.post(f"/api/requests/{rid}/cancel", json={}).status_code == 400
    # another user cannot cancel someone else's request
    r2 = _send(user, problem="Another thing that takes time every week.", impact="", wish="")
    pw = owner.post("/api/users", json={"email": "other@test.local", "name": "Other Person", "role": "user"}).json()["temp_password"]
    from fastapi.testclient import TestClient

    from cbx.main import app
    from tests.conftest import login

    other = TestClient(app)
    login(other, "other@test.local", pw)
    assert other.post(f"/api/requests/{r2['id']}/cancel", json={}).status_code in (403, 404)
    # admin can cancel it; both sides see it as Cancelled (history is kept, nothing deleted)
    assert owner.post(f"/api/requests/{r2['id']}/cancel", json={"note": "duplicate of #1"}).status_code == 200
    mine = user.get("/api/requests/mine").json()["requests"]
    assert next(x for x in mine if x["id"] == r2["id"])["status"] == "cancelled"
    assert next(x for x in owner.get("/api/requests/queue").json()["requests"] if x["id"] == r2["id"])["status"] == "cancelled"


def test_closed_ticket_is_frozen_and_cexa_cannot_lead(user, admin, db):
    """Cancelling must stick: testing/publishing/drafting a cancelled ticket used to flip the request
    back to Testing/Ready, and C-EXA kept chatting as if a ticket existed."""
    req = _send(user)
    _approve(admin, req["id"], "human")
    admin.post(f"/api/requests/{req['id']}/rules", json={"text": "One rule here"})
    iid = admin.get(f"/api/requests/{req['id']}").json()["interview_id"]
    tid = admin.post(f"/api/interviews/{iid}/finish").json()["ticket"]["id"]
    assert user.post(f"/api/requests/{req['id']}/cancel", json={"note": "no longer needed"}).status_code == 200
    for call in (lambda: admin.post(f"/api/tickets/{tid}/test"), lambda: admin.post(f"/api/tickets/{tid}/publish"),
                 lambda: admin.post(f"/api/tickets/{tid}/reopen"), lambda: admin.patch(f"/api/tickets/{tid}", json={"summary": "x"})):
        r = call()
        assert r.status_code == 400 and "cancelled" in r.json()["detail"], r.text
    assert admin.get(f"/api/requests/{req['id']}").json()["status"] == "cancelled"
    assert admin.get(f"/api/tickets/{tid}").json()["status"] == "cancelled"
    r = user.post(f"/api/interviews/{iid}/reply", json={"text": "hello?"})
    assert r.status_code == 400 and "cancelled" in r.json()["detail"]
    assert admin.post(f"/api/interviews/{iid}/ask", json={"text": "still there?"}).status_code == 400
    # the frozen page still reads fine
    assert admin.get(f"/api/tickets/{tid}").status_code == 200

    # C-EXA is an owner-role account but must never lead an interview
    cexa = db.query(User).filter(User.is_axl.is_(True)).one()
    fresh = _send(user)
    r = admin.post(f"/api/requests/{fresh['id']}/approve", json={"mode": "human", "assignee_id": cexa.id})
    assert r.status_code == 400 and "C-EXA" in r.json()["detail"]
    # My requests can say what the person gets back
    assert "result_kind" in user.get("/api/requests/mine").json()["requests"][0]


def test_failed_assessment_does_not_strand_request(user, db, monkeypatch):
    from cbx import requests_flow as F
    from cbx.models import Request

    req = _send(user)
    row = db.get(Request, req["id"])
    row.status = "sent"
    row.importance = None
    row.importance_reason = ""
    db.commit()

    def boom(_db, _req):
        raise RuntimeError("model down")

    monkeypatch.setattr(F, "assess", boom)
    F.assess_job(req["id"])
    db.expire_all()
    row = db.get(Request, req["id"])
    assert row.status == "new" and row.importance == "medium" and "C-EXA couldn't read" in row.importance_reason
