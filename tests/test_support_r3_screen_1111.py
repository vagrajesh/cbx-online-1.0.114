"""1.0.111 round 3 (23 Oct, afternoon) — the screens (Screen): the ticket's calmer Details card (2), the list's multi-select filters
(3) and the set-up of "Who works each system" (1). The look is checked in the browser; here: what the screens send and get, the
page's own whitelist (cleanStatuses, cleanIds), the filter labels and the status line. Names, numbers and mails are made up."""
import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from cbx.db import SessionLocal
from cbx.main import app
from cbx.models import SupportSystem, SupportTicket

from .conftest import _make, login
from .test_support_1098 import _setup, box, lead, uid  # noqa: F401
from .test_support_r2_screen_1109 import app_ticket
from ._node import run_module

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "web/views/support.js").read_text(encoding="utf-8")


def ids(c, **q):
    r = c.get("/api/support/queue", params=q)
    assert r.status_code == 200, r.text
    return [x["id"] for x in r.json()["tickets"]]


def desk_person(owner, email, name, role="user"):
    """Someone on the desk only through the Support desk tool (not a support lead)."""
    tools = owner.get("/api/users").json()["tools"]
    desk = [t["id"] for t in tools if t["slug"] == "support-desk"]
    pw = _make(owner, email, name, role, tools=desk)
    c = TestClient(app)
    login(c, email, pw)
    return uid(email), c


def chase_id():
    with SessionLocal() as s:
        return s.query(SupportSystem).filter(SupportSystem.key == "chase").one().id


def four_tickets(owner, user, l1):
    """a free, b with l1 (not accepted), c done by l1, d closed as not relevant."""
    a = app_ticket(user, text="The Chase quote screen errors on approve for site 3 in Sandton, see the screenshot.")
    b = app_ticket(user, text="The Chase job card list is empty for site 9 in Midrand since this morning.")
    c = app_ticket(user, text="Chase shows the wrong supplier on PO 4410 for the Rosebank site, please fix it.")
    d = app_ticket(user, text="Please add me to the Chase newsletter for the Pretoria office site this month.")
    for t in (b, c):
        assert owner.post(f"/api/support/tickets/{t}/assign", json={"user_id": l1}).status_code == 200
    assert owner.post(f"/api/support/tickets/{c}/status", json={"status": "closed", "note": "Fixed the supplier on the PO."}).status_code == 200
    assert owner.post(f"/api/support/tickets/{d}/not-relevant", json={"reason": "a newsletter", "tell": False}).status_code == 200
    return a, b, c, d


# ---- 3. multi-select filters ---------------------------------------------------------------------------------------------------------
def test_one_two_and_four_statuses(box, owner, user):
    l1 = lead(owner, email="lulu.r3@test.local", name="Lulu Filter")
    a, b, c, d = four_tickets(owner, user, l1)
    assert ids(owner, status="done") == [c]
    assert set(ids(owner, status="done,not_relevant")) == {c, d}
    assert set(ids(owner, status="free,not_accepted,with,requester")) == {a, b}
    assert set(ids(owner, status="open")) == {a, b} and set(ids(owner, status="all")) == {a, b, c, d}


def test_status_and_person_combine(box, owner, user):
    l1 = lead(owner, email="lulu.r3@test.local", name="Lulu Filter")
    l2 = lead(owner, email="pete.r3@test.local", name="Pete Filter")
    a, b, c, d = four_tickets(owner, user, l1)
    assert owner.post(f"/api/support/tickets/{a}/assign", json={"user_id": l2}).status_code == 200
    assert set(ids(owner, status="with,done", lead=f"{l1},{l2}")) == {a, b, c}
    assert set(ids(owner, status="with,done", lead=str(l1))) == {b, c}
    assert ids(owner, status="done", lead=str(l2)) == []
    assert set(ids(owner, status="with", lead=f"{l2}", system=f"{chase_id()},0")) == {a}


def test_unknown_values_are_ignored(box, owner, user):
    l1 = lead(owner, email="lulu.r3@test.local", name="Lulu Filter")
    a, b, c, d = four_tickets(owner, user, l1)
    assert ids(owner, status='bogus,done,"><img src=x>') == [c]
    assert set(ids(owner, status="bogus,constructor")) == {a, b}  # nothing known: the default (open)
    assert set(ids(owner, status="with,done", lead="x,12abc,;drop")) == {b, c}
    assert set(ids(owner, status="open", system="1;DROP TABLE,abc")) == {a, b}


def test_staff_still_see_only_their_own_and_the_free_ones(box, owner, user):
    l1, lc = lead(owner, email="lulu.r3@test.local", name="Lulu Filter", client=True)
    l2 = lead(owner, email="pete.r3@test.local", name="Pete Filter")
    a, b, c, d = four_tickets(owner, user, l1)
    e = app_ticket(user, text="The Chase site list for the Durban office is missing two buildings since Monday.")
    assert owner.post(f"/api/support/tickets/{e}/assign", json={"user_id": l2}).status_code == 200
    assert set(ids(lc, status="free,not_accepted,with,done,not_relevant,all")) == {a, b, c}
    assert set(ids(lc, status="with", lead=str(l2))) == {b}  # the person filter never opens someone else's
    assert lc.get("/api/support/queue", params={"status": "removed"}).status_code == 403
    assert owner.post(f"/api/support/tickets/{a}/remove", json={"tell": False}).status_code == 200
    assert ids(lc, status="removed,free") == []
    assert ids(owner, status="removed") == [a] and set(ids(owner, status="removed,done")) == {a, c}
    # the Details card's rights are the server's, as before: a staff member never gets the admin-only actions
    t = lc.get(f"/api/support/tickets/{b}").json()
    assert t["can_accept"] and not t["can_remove"] and not t["can_not_relevant"] and not t["can_assign"]
    t = owner.get(f"/api/support/tickets/{b}").json()
    assert t["can_remove"] and t["can_not_relevant"] and t["can_assign"]


# ---- 1. the set-up: who works each system ---------------------------------------------------------------------------------------------
def test_setup_lists_desk_people_saves_them_and_the_ticket_page_offers_them_first(box, owner, user):
    n_id, nc = desk_person(owner, "nomsa.r3@test.local", "Nomsa Desk")
    sid = chase_id()
    d = owner.get("/api/support/system-people").json()
    pool = {p["id"]: p for p in d["pool"]}
    assert pool[n_id]["desk"] and not pool[n_id]["lead"]
    assert next(s for s in d["systems"] if s["id"] == sid)["people"] == []
    r = owner.post("/api/support/system-people", json={"systems": [{"id": sid, "people": [n_id]}]})
    assert r.status_code == 200, r.text
    assert [p["id"] for p in next(s for s in r.json()["systems"] if s["id"] == sid)["people"]] == [n_id]
    tid = app_ticket(user)
    t = owner.get(f"/api/support/tickets/{tid}").json()
    me = next(u for u in t["leads"] if u["id"] == n_id)
    assert me["covers"] and t["leads"][0]["id"] == n_id  # "Works Chase" first in the Assigned-to picker
    # the settings page's ticket-type cards read who works each system from the same rows
    s = next(x for x in owner.get("/api/support/settings").json()["systems"] if x["id"] == sid)
    assert [x["id"] for x in s["leads"]] == [n_id]


def test_use_axls_guess_proposes_who_finished_that_systems_tickets(box, owner, user):
    n_id, nc = desk_person(owner, "nomsa.r3@test.local", "Nomsa Desk")
    sid = chase_id()
    tid = app_ticket(user)
    assert nc.post(f"/api/support/tickets/{tid}/take").status_code == 200
    assert nc.post(f"/api/support/tickets/{tid}/status", json={"status": "closed", "note": "Approved the quote by hand."}).status_code == 200
    # the owner closes another one himself: that is not "doing Chase"
    t2 = app_ticket(user, text="The Chase approval for site 14 in Centurion hangs on the last step since Friday.")
    from cbx.db import utcnow

    with SessionLocal() as s:
        x, o = s.get(SupportTicket, t2), uid("owner@test.local")
        x.assigned_to_id = x.closed_by_id = o
        x.status, x.closed_at, x.resolution = "closed", utcnow(), "Approved it."
        s.commit()
    d = owner.get("/api/support/system-people").json()
    row = next(s for s in d["systems"] if s["id"] == sid)
    assert [p["id"] for p in row["guess"]] == [n_id] and row["guess"][0]["done"] == 1
    assert [p["id"] for p in owner.get(f"/api/support/system-people/{sid}/guess").json()["people"]] == [n_id]
    with SessionLocal() as s:
        assert s.get(SupportTicket, tid).closed_by_id == n_id


# ---- the page's own whitelist and words (Node) ----------------------------------------------------------------------------------------
def _fn(name: str, end: str) -> str:
    i = SRC.index(name)
    return SRC[i:SRC.index(end, i)] + "}"


CONSTS = "\n".join(re.findall(r"(?ms)^const (?:LIST_STATUS|OLD_STATUS|REMOVED_OPT|SHORT_STATUS) = .*?;$", SRC))


def test_the_page_keeps_only_known_statuses_and_whole_number_ids():
    code = CONSTS + "\n" + _fn("function cleanStatus(", "}  // cleanStatus end") + "\n" + _fn("function cleanStatuses(", "}  // cleanStatuses end") + "\n" \
        + _fn("function cleanIds(", "}  // cleanIds end") + "\n" + """
console.log(JSON.stringify([
  cleanStatuses('done,not_relevant', false), cleanStatuses('bogus,"><img>,constructor', false), cleanStatuses('removed,free', false),
  cleanStatuses('removed,free,free', true), cleanStatuses(['pending', 'with'], false), cleanStatuses('', false),
  cleanIds('12,15,x,12,1e3,-4, 0'), cleanIds([3, '4']), cleanIds(undefined)]))"""
    out = json.loads(run_module(code))
    assert out == [["done", "not_relevant"], ["open"], ["free"], ["removed", "free"], ["pending", "with"], ["open"], ["12", "15", "0"], ["3", "4"], []]


def test_the_filter_button_says_what_is_chosen():
    code = _fn("function msLabel(", "\n}\n") + "\n" + """
console.log(JSON.stringify([msLabel('Status', ['Done', 'Not relevant'], 'Open'), msLabel('Status', ['A', 'B', 'C', 'D'], 'Open'),
  msLabel('Person', [], 'Anyone'), msLabel('System', ['Chase'], 'Every system')]))"""
    assert json.loads(run_module(code)) == ["Status: Done, Not relevant", "Status: 4 chosen", "Person: Anyone", "System: Chase"]


def test_the_details_card_says_what_happens_next():
    pre = "const fmtDate = (x, o) => (o && o.rel ? 'yesterday' : '27 Oct');\n" + "\n".join(
        re.findall(r"(?m)^const (?:firstName|clipQ) = .*;$", SRC))
    code = pre + "\n" + _fn("function nowLine(t) {", "\n}\n") + "\n" + """
const base = { requester: 'Rajani Moodley', status: 'pending', missing: ['What is the invoice number?'], followup: null };
console.log(JSON.stringify([
  nowLine(base),
  nowLine({ ...base, missing: ['What is the invoice number?', 'Which site?'] }),
  nowLine({ ...base, status: 'assigned', assigned_to_id: 3, followup: { state: 'waiting', kind: 'person', asked: 'Please send the PO.' } }),
  nowLine({ ...base, status: 'assigned', assigned_to_id: 3, word: 'With Nandi — waiting for them to accept', missing: [] }),
  nowLine({ ...base, status: 'ready', missing: [], free: true, can_assign: true }),
  nowLine({ ...base, status: 'closed', assigned_to: 'Nandi Sello', closed_at: 'x', missing: [] }),
  nowLine({ ...base, status: 'closed', not_relevant: true, resolution: 'Not relevant: a newsletter' }),
  nowLine({ ...base, removed: true, removal: { by: 'Owen', how: 'silent', reason: 'duplicate' } }),
  nowLine({ ...base, followup: { state: 'escalated', escalated_to: 'Theo' } })]))"""
    assert json.loads(run_module(code)) == [
        ["Waiting for Rajani", "asked: What is the invoice number?"],
        ["Waiting for Rajani", "asked: What is the invoice number? (+1 more)"],
        ["Waiting for Rajani", "asked: Please send the PO."],
        ["With Nandi — waiting for them to accept", ""],
        ["Free — nobody has it yet", "assign it, or take it yourself"],
        ["Done by Nandi", "yesterday"],
        ["Closed as not relevant", "a newsletter"],
        ["Removed", "by Owen · silently · duplicate"],
        ["Escalated to Theo", "no answer from Rajani"],
    ]


def test_the_details_card_has_no_permanent_dropdowns_and_admin_items_follow_the_rights():
    i = SRC.index("async function staffTicket(")
    body = SRC[i:SRC.index("\n}\n", i)]
    # 1.0.112: Assigned to has its dropdown again for owner/admins (tests/test_support_assign_1112.py); the System stays click-to-open
    assert "<select id=\"sp-sys\"" in body and 'id="sp-sysbox" hidden' in body  # System: a click opens it
    for flag, item in (("t.can_not_relevant", "sp-notrel"), ("t.can_remove", "sp-remove"), ("t.can_give_back", "sp-giveback"), ("t.can_restore", "sp-restore")):
        assert f"{flag} ? ['{item}'" in body
    assert "data-belongs" in (ROOT / "web/views/support-ui.js").read_text(encoding="utf-8")
    # review: an old route lesson's lead is offered only when set for the system; suggest_note shows otherwise
    assert "t.suggested_lead && t.leads.some((u) => u.id === t.suggested_lead.id && u.covers)" in body and "t.suggest_note" in body
    # review: Mark as being discussed never drops pictures pasted in the reply box
    assert "bind('#sp-disc', (b) => { if (noPics())" in body
