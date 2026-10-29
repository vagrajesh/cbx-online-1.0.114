"""1.0.85: the report chain. A helper asks its team lead, a lead asks C-EXA, and only C-EXA asks the owner —
so the owner is not asked what the teams can answer themselves. A day's work comes to him in one line."""
import json
import threading
import time

import pytest

from cbx.cexa import gateway as G
from cbx.cexa import loop as L
from cbx.cexa import teams as TM
from cbx.config import settings
from cbx.models import AgentTeam, AgentWork, CexaMessage, TeamMember, User


@pytest.fixture()
def clean(db, owner, tmp_path, monkeypatch):
    db.query(AgentWork).delete()
    db.query(CexaMessage).filter(CexaMessage.kind.in_(("needs_you", "team"))).delete()
    db.commit()
    TM.ensure_teams(db)
    db.commit()
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    yield db
    db.rollback()
    db.query(AgentWork).delete()
    db.commit()


def _me(db):
    return db.query(User).filter(User.email == "owner@test.local").one()


def _worker(db):
    """A team member who is not a lead, on a team with a lead switched on."""
    for t in db.query(AgentTeam).filter(AgentTeam.enabled.is_(True)).order_by(AgentTeam.sort).all():
        ms = db.query(TeamMember).filter(TeamMember.team_id == t.id, TeamMember.enabled.is_(True)).all()
        lead = next((m for m in ms if m.lead), None)
        other = next((m for m in ms if not m.lead), None)
        if lead and other:
            return other, lead
    raise AssertionError("no team with a lead and a member")


def _give(db, to, title, *lines):
    w = L.give(db, to, title, "\n".join(lines), _me(db))
    db.commit()
    return w.id


def _w(db, wid):
    db.expire_all()
    return db.get(AgentWork, wid)


def _owner_asked(db):
    return db.query(CexaMessage).filter(CexaMessage.kind == "needs_you").count()


def _run_one(db, wid=None):
    """Work exactly one piece of open work (the given one, else the next)."""
    db.expire_all()
    return L.run_work(wid or L.next_work(db))


def _script(db, kid_id, *lines):
    """What the one above will do when it works the question (the no-model stub follows these lines)."""
    k = db.get(AgentWork, kid_id)
    k.detail = "\n".join(lines) + "\n" + (k.detail or "")
    db.commit()


def _ask_of(db, wid):
    db.expire_all()
    return db.query(AgentWork).filter(AgentWork.parent_id == wid, AgentWork.source == "ask_up").order_by(AgentWork.id.desc()).first()


def test_a_helper_asks_its_lead_not_the_owner(clean, db):
    worker, lead = _worker(db)
    wid = _give(db, worker.name, "Which folder for the March bills?", '>> ask_up {"question": "Inbox/Telkom or Inbox/Bills?", "options": "Inbox/Telkom | Inbox/Bills"}')
    _run_one(db, wid)
    w = _w(db, wid)
    up = _ask_of(db, wid)
    assert w.status == "held" and up is not None and up.to_member_id == lead.id and up.from_label == worker.name
    assert "Inbox/Telkom or Inbox/Bills?" in up.detail and "ask_up" not in up.detail.split("THEIR QUESTION")[0]  # its script is not copied up
    assert _owner_asked(db) == 0
    _script(db, up.id, '>> finish {"result": "Inbox/Telkom — that is where the team files them.", "confidence": "90"}')
    L.work_all()
    w = _w(db, wid)
    assert w.status == "done" and "Inbox/Telkom" in json.dumps(w.transcript) and _owner_asked(db) == 0


def test_a_lead_asks_c_exa_and_only_c_exa_asks_the_owner(clean, db, owner):
    worker, lead = _worker(db)
    wid = _give(db, worker.name, "Give Thandi access to Chase?", '>> ask_up {"question": "Should Thandi get Chase access?", "options": "Yes | No"}')
    _run_one(db, wid)
    to_lead = _ask_of(db, wid)
    _script(db, to_lead.id, '>> ask_up {"question": "Access is a person\'s call — ask the owner?", "options": "Ask | No"}')
    _run_one(db, to_lead.id)
    to_cexa = _ask_of(db, to_lead.id)
    assert to_cexa.to_member_id is None and to_cexa.source == "ask_up" and _owner_asked(db) == 0
    _script(db, to_cexa.id, '>> ask_owner {"question": "I recommend yes: she needs it for the vendor team. Give Thandi Chase access?", "options": "Yes | No"}')
    _run_one(db, to_cexa.id)
    assert _w(db, to_cexa.id).status == "waiting" and _owner_asked(db) == 1  # one question reaches the owner, from C-EXA, with her recommendation
    r = owner.post(f"/api/cexa/work/{to_cexa.id}/answer", json={"text": "Yes"})
    assert r.status_code == 200
    L.work_all()
    assert _w(db, to_cexa.id).status == "done" and _w(db, to_lead.id).status == "done" and _w(db, wid).status == "done"
    assert _owner_asked(db) == 1


def test_stop_from_above_stops_the_work(clean, db):
    worker, lead = _worker(db)
    wid = _give(db, worker.name, "Delete the old rows?", '>> ask_up {"question": "Delete all 2019 rows?", "options": "Yes | No"}')
    _run_one(db, wid)
    _script(db, _ask_of(db, wid).id, '>> finish {"result": "Stop — deleting is the owner\'s call and not part of this job.", "confidence": "95"}')
    L.work_all()
    w = _w(db, wid)
    assert w.status == "stopped" and "Stop" in w.result and _owner_asked(db) == 0


def test_an_unsure_result_is_checked_by_the_lead(clean, db):
    worker, lead = _worker(db)
    wid = _give(db, worker.name, "How many open tickets?", '>> finish {"result": "About 12, I think.", "confidence": "30"}')
    _run_one(db, wid)
    up = _ask_of(db, wid)
    assert up.to_member_id == lead.id and (up.proposal or {}).get("up") == "review" and _owner_asked(db) == 0
    _script(db, up.id, '>> finish {"result": "Yes, that\'s right — 12 open.", "confidence": "90"}')
    L.work_all()
    w = _w(db, wid)
    assert w.status == "done" and "Checked by" in w.result


def test_when_the_one_above_cannot_answer_it_goes_to_the_owner(clean, db):
    worker, lead = _worker(db)
    wid = _give(db, worker.name, "Odd thing", '>> ask_up {"question": "What is this?", "options": "A | B"}')
    _run_one(db, wid)
    up = _ask_of(db, wid)
    k = db.get(AgentWork, up.id)
    k.status, k.result = "failed", "My model could not be used."
    L._wake_parent(db, k)
    db.commit()
    w = _w(db, wid)
    assert w.status == "waiting" and _owner_asked(db) == 1


def test_the_third_question_up_on_one_work_goes_to_the_owner(clean, db):
    worker, lead = _worker(db)
    wid = _give(db, worker.name, "Keeps asking")
    w = db.get(AgentWork, wid)
    a = L.Agent(db, worker)
    for i in range(3):
        assert L.escalate(db, w, a, f"q{i}", ["A", "B"]) == lead.name
    assert L.escalate(db, w, a, "q3", ["A", "B"]) == "" and w.status == "waiting"
    db.flush()
    assert _owner_asked(db) == 1


def test_who_has_which_ask_tool(clean, db):
    worker, lead = _worker(db)
    assert "ask_up" in L.Agent(db, worker).hands(False) and "ask_owner" not in L.Agent(db, worker).hands(False)
    assert "ask_up" in L.Agent(db, lead).hands(False) and "ask_owner" not in L.Agent(db, lead).hands(False)
    assert "ask_owner" in L.Agent(db, None).hands(False) and "ask_up" not in L.Agent(db, None).hands(False)
    assert L.above(db, L.Agent(db, worker)).id == lead.id and L.above(db, L.Agent(db, lead)) is None


def test_the_day_in_one_line(clean, db, monkeypatch):
    from cbx import state
    from cbx.db import utcnow

    worker, lead = _worker(db)
    wid = _give(db, worker.name, "Which folder?", '>> ask_up {"question": "A or B?", "options": "A | B"}')
    _run_one(db, wid)
    _script(db, _ask_of(db, wid).id, '>> finish {"result": "A.", "confidence": "90"}')
    L.work_all()
    text = L.digest(db, utcnow().replace(year=2000))
    assert "1 question answered inside the teams" in text and lead.name in text and "nothing waits for you" in text
    state.set(db, L.DIGEST_KEY, "")
    db.commit()
    monkeypatch.setattr(settings, "digest_hour", 0)
    L.digest_routine()
    L.digest_routine()  # once a day
    db.expire_all()
    assert db.query(CexaMessage).filter(CexaMessage.kind == "team", CexaMessage.title == "The teams today").count() == 1


def test_one_gpu_one_call_at_a_time_and_people_first(monkeypatch):
    monkeypatch.setattr(settings, "gpu_slots", 1)
    gate = G.GpuGate()
    order, hold = [], threading.Event()

    def bg_first():
        with gate.use("agent"):
            order.append("bg1")
            hold.wait(2)

    def later(task, tag):
        with gate.use(task):
            order.append(tag)

    t1 = threading.Thread(target=bg_first)
    t1.start()
    time.sleep(0.05)
    t2 = threading.Thread(target=later, args=("support_triage", "bg2"))
    t2.start()
    time.sleep(0.05)
    t3 = threading.Thread(target=later, args=("chat", "person"))
    t3.start()
    time.sleep(0.05)
    assert order == ["bg1"] and gate.state()["busy"] == 1  # the others wait
    hold.set()
    for t in (t1, t2, t3):
        t.join(3)
    assert order == ["bg1", "person", "bg2"]  # the person waiting went before the background call


def test_c_exa_carries_on_by_herself_before_asking(clean, db, monkeypatch):
    """1.0.87: out of steps, C-EXA keeps going by herself (CBX_CEXA_ROUNDS times) — a big job is not a question."""
    monkeypatch.setattr(settings, "cexa_max_steps", 2)
    monkeypatch.setattr(settings, "cexa_rounds", 2)
    wid = _give(db, "", "A long job", *['>> calculate {"expression": "%d+1"}' % i for i in range(7)])
    _run_one(db, wid)
    w = _w(db, wid)
    assert w.status == "open" and w.proposal.get("rounds") == 1 and _owner_asked(db) == 0
    _run_one(db, wid)
    assert _w(db, wid).proposal.get("rounds") == 2 and _owner_asked(db) == 0
    _run_one(db, wid)
    w = _w(db, wid)
    assert w.status == "waiting" and "steps" in w.question
    db.flush()
    assert _owner_asked(db) == 1


def test_a_long_run_never_forgets_what_it_was_asked():
    from cbx.cexa import loop as LL

    msgs = [{"role": "system", "content": "rules"}, {"role": "user", "content": "WORK #1: the job"}]
    for i in range(80):
        msgs += [{"role": "assistant", "content": f"step {i}"}, {"role": "tool", "content": f"result {i}"}]
    t = LL._trim(msgs, 20)
    assert t[0]["content"] == "rules" and t[1]["content"] == "WORK #1: the job" and len(t) <= 20 and t[2]["role"] != "tool"
