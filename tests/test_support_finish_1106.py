"""1.0.106 (Edwin, 19 Oct: "the ticketing — finish it"): the old desk walked end to end and its gaps closed, as in CBX-Connect
2.10.9. A taken ticket's mail has no Accept and says they took it; the support team's list and overview put their own
tickets first and then the free ones in order of age (an "Free to take" filter too), with no desk workings; a free ticket
reads "Free to take" to them; a reply to a ticket closed as not relevant brings it back to the desk without a mail going
out; Nudge's "sending is off" goes once mail is on. Outlook through outlook.Fake. Names are made up."""
from datetime import timedelta

from cbx.db import SessionLocal, utcnow
from cbx.models import SupportTicket
from cbx.support import own as OWN

from .test_support_1098 import _setup, box, deliver, lead, ticket, to, uid  # noqa: F401
from .test_support_own_1103 import A_MAIL, B_MAIL, _notes, _queue, _ready, _staff


def test_a_taken_tickets_mail_has_no_accept_and_says_they_took_it(box, owner):  # noqa: F811
    _setup(owner)
    a, _ = _staff(owner)
    tid = _ready(box, owner, "omar1106@client.example", "Pump one")
    assert a.post(f"/api/support/tickets/{tid}/take").status_code == 200
    m = [x for x in to(box, A_MAIL) if "Assigned to you" in x["subject"]][-1]
    assert "You took this support ticket" in m["body"] and "/api/support/accept/" not in m["body"] and "/api/support/done/" in m["body"]
    assert "assigned you a support ticket" not in m["body"]
    # given by an admin: the usual mail, with Accept
    lebo = lead(owner, "lebo1106@test.local", "Lebo Lead")
    t2 = _ready(box, owner, "omar1106b@client.example", "Pump two")
    assert owner.post(f"/api/support/tickets/{t2}/assign", json={"user_id": lebo}).status_code == 200
    m2 = [x for x in to(box, "lebo1106@test.local") if "Assigned to you" in x["subject"]][-1]
    assert "/api/support/accept/" in m2["body"] and "assigned you a support ticket" in m2["body"]


def test_the_support_team_gets_its_own_first_then_the_free_ones_by_age(box, owner):  # noqa: F811
    _setup(owner)
    a, b = _staff(owner)
    old = _ready(box, owner, "omar1106c@client.example", "Boiler old")
    new = _ready(box, owner, "omar1106d@client.example", "Boiler new")
    mine = _ready(box, owner, "omar1106e@client.example", "Boiler mine")
    with SessionLocal() as s:  # an old ticket changed lately must still come by age, not by its last change
        s.get(SupportTicket, old).created_at = utcnow() - timedelta(days=2)
        s.get(SupportTicket, old).updated_at = utcnow() + timedelta(minutes=5)
        s.commit()
    a.post(f"/api/support/tickets/{mine}/take")
    rows = a.get("/api/support/queue?status=open").json()["tickets"]
    ids = [x["id"] for x in rows]
    assert ids.index(mine) < ids.index(old) < ids.index(new), ids
    free = [x["id"] for x in owner.get("/api/support/queue?status=free").json()["tickets"]]
    assert mine not in free and free.index(old) < free.index(new)
    # the overview: their numbers and lists, not the desk's tables
    d = a.get("/api/support/dashboard").json()
    assert d["own_only"] and [x["id"] for x in d["mine"]] == [mine] and [x["id"] for x in d["free"]][:2] == [old, new]
    assert d["mine_counts"]["open"] == 1 and d["mine_counts"]["free"] >= 2
    assert "mine" not in owner.get("/api/support/dashboard").json()
    # a free ticket reads "Free to take" to them; the managers still read "Ready to assign"
    assert a.get(f"/api/support/tickets/{new}").json()["word"] == "Free to take"
    assert owner.get(f"/api/support/tickets/{new}").json()["word"] == "Ready to assign"


def test_a_reply_after_not_relevant_comes_back_to_the_desk_and_nothing_is_mailed(box, owner):  # noqa: F811
    _setup(owner)
    a, _ = _staff(owner)
    tid = _ready(box, owner, "spam1106@client.example", "Prize draw")
    assert owner.post(f"/api/support/tickets/{tid}/not-relevant", json={"reason": "spam", "tell": False}).status_code == 200
    assert tid not in _queue(a)[0]
    sent = len(box.sent)
    deliver(box, owner, "spam1106@client.example", f"RE: Prize draw [T-{tid:04d}]", "Please answer, we want to give you the prize money for your company today.",
            "Spam", conv="C1103-Prize draw")
    t = ticket(tid=tid)
    assert t.status == "ready" and not t.closed_as and t.assigned_to_id is None and t.closed_at is None
    assert len(box.sent) == sent  # no mail back to a spammer
    assert any("replied after it was closed as not relevant" in n for n in _notes(tid))
    assert tid in _queue(a)[0]  # free to take again
    # closed as not relevant is never a solved ticket: the support team's "done in 30 days" leaves it out
    assert owner.post(f"/api/support/tickets/{tid}/not-relevant", json={}).status_code == 200
    assert a.get("/api/support/dashboard").json()["mine_counts"]["done_30d"] == 0


def test_the_team_tab_says_sending_is_on_once_it_is(box, owner, monkeypatch):  # noqa: F811
    from cbx import compat2
    from cbx.support import nudge as N
    from cbx.support import team as TM

    with SessionLocal() as s:
        compat2.state_set(s, "support.nudge", {"last_run": "2026-10-19T15:00:00+00:00", "note": "Sending is off — nothing to send.", "lines": []})
        s.commit()
        monkeypatch.setattr(N, "sending", lambda: True)
        assert TM.live(s)["nudge"]["note"] == "Sending is on — the next run sends what is due."
        monkeypatch.setattr(N, "sending", lambda: False)
        assert TM.live(s)["nudge"]["note"] == "Sending is off — nothing to send."


def test_queue_order_their_own_first_then_free_by_readiness_and_age():
    from datetime import datetime, timezone

    from cbx.models import User

    me = User(id=7, email="me@test.local")
    now = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)

    def tk(i, status, who=None, age=0):
        return SupportTicket(id=i, status=status, assigned_to_id=who, created_at=now - timedelta(hours=age))

    rows = [tk(1, "ready", age=1), tk(2, "pending", age=9), tk(3, "assigned", 7, 0), tk(4, "ready", age=5), tk(5, "closed", 7, 2), tk(6, "discussing", 7, 4),
            tk(7, "assigned", 8, 3)]
    assert [t.id for t in OWN.queue_order(rows, me)] == [6, 3, 4, 1, 2, 5, 7]
