"""1.0.103: from CBX-Connect 2.10.8 tests/test_bot_busy_2108.py, on the old conftest.
Edwin (19 Oct): "I want to be able to see if a bot is busy with a document, and make sure these work."
A bot marks the document it works on (busy_bot / busy_step / busy_since, a beat while it runs) and the mark goes when the
step ends — a crash too; a mark with no sign of life for 3 minutes is put back in the queue once; the start clears every
mark; the busy endpoints show only what the person may see. The fake steps block on an Event, so the test looks while
the bot is busy. The AI is off in tests: the capture call is replaced. Names are made up."""
from __future__ import annotations

import threading
import time
import uuid
from datetime import timedelta

import pytest

from cbx import busy, jobs
from cbx.ap import capture as C
from cbx.ap import engine as E
from cbx.ap import service as S
from cbx.db import SessionLocal, session_scope, utcnow
from cbx.models import AuditLog, SupportMessage, SupportTicket, User
from cbx.models.ap import ApCapture, ApItem

from .fixtures import make_ap
from .test_ap_capture_1098 import _app, _drop, _invoice_answer, _user, signed_in  # noqa: F401

WAIT = 20  # seconds a test waits for the other thread at most


def _run(fn, *a):
    out = {}

    def go():
        try:
            out["result"] = fn(*a)
        except BaseException as e:  # noqa: BLE001
            out["error"] = e

    th = threading.Thread(target=go, daemon=True)
    th.start()
    return th, out


def _wait_for(cond, secs=WAIT):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def _cap_of(bid) -> ApCapture:
    with SessionLocal() as s:
        return s.query(ApCapture).filter(ApCapture.batch_id == bid).order_by(ApCapture.id).first()


def _marks(kind, rid):
    with SessionLocal() as s:
        r = s.get(busy.model(kind), rid)
        return (r.busy_bot or "", r.busy_step or "", r.busy_since, r.busy_beat)


def _audits(action, target) -> int:
    with SessionLocal() as s:
        return s.query(AuditLog).filter(AuditLog.action == action, AuditLog.target == target).count()


def _ticket(**kw) -> int:
    with SessionLocal() as s:
        t = SupportTicket(source="app", **kw)
        s.add(t)
        s.commit()
        return t.id


def _drop_ticket(tid):
    with SessionLocal() as s:
        s.query(SupportMessage).filter(SupportMessage.ticket_id == tid).delete()
        s.query(SupportTicket).filter(SupportTicket.id == tid).delete()
        s.commit()


@pytest.fixture(autouse=True)
def _leave_no_reading():
    """Every capture a test here made that is still in a reading stage is skipped afterwards: nothing is left in the
    readers' queue for the next test file's capture jobs."""
    with SessionLocal() as s:
        top = s.query(ApCapture.id).order_by(ApCapture.id.desc()).limit(1).scalar() or 0
    yield
    with SessionLocal() as s:
        for x in s.query(ApCapture).filter(ApCapture.id > top, ApCapture.stage.in_(C.READING)).all():
            x.stage, x.bucket, x.needs, x.claimed_at = "skipped", "skipped", "", None
            x.busy_bot, x.busy_step, x.busy_since, x.busy_beat = "", "", None, None
        s.commit()


@pytest.fixture()
def queued(monkeypatch):
    """One manual invoice waiting in the readers' queue (dropped while the AI is off), the next one a reader takes."""
    run = uuid.uuid4().hex[:6].upper()
    monkeypatch.setattr(C, "model_on", lambda: False)
    u = _user(f"busy.{run.lower()}@test.local", "Busy Person")
    b = _drop(u, {f"busy_{run}.pdf": make_ap.invoice(no=f"BZ-{run}")}, f"Busy {run}")
    c = _cap_of(b["id"])
    assert c.stage == "queued"
    held = {}
    with SessionLocal() as s:
        x = s.get(ApCapture, c.id)
        x.read = {}
        for o in s.query(ApCapture).filter(ApCapture.stage.in_(("queued", "senior")), ApCapture.id != c.id).all():
            held[o.id] = o.claimed_at
            o.claimed_at = utcnow() + timedelta(days=1)
        s.commit()
    monkeypatch.setattr(C, "model_on", lambda: True)
    yield u, b, c, run
    with SessionLocal() as s:  # out of the way of the other tests
        x = s.get(ApCapture, c.id)
        x.stage, x.busy_bot, x.busy_step, x.busy_since, x.busy_beat = "skipped", "", "", None, None
        for oid, at in held.items():
            s.get(ApCapture, oid).claimed_at = at
        s.commit()


# ---- 1. a stage sets and clears the mark ---------------------------------------------------------------------------------
def test_a_reader_marks_the_invoice_while_it_reads_and_the_page_sees_it(monkeypatch, queued):
    u, b, c, run = queued
    entered, release = threading.Event(), threading.Event()

    def slow(task, system, user, images, model="", db=None):
        entered.set()
        release.wait(WAIT)
        return _invoice_answer(f"BZ-{run}")

    monkeypatch.setattr(C, "ask_vision", slow)
    real_match, at_match = C.match_one, []
    monkeypatch.setattr(C, "match_one", lambda db, cap, it, fixed: at_match.append(_marks("ap_capture", cap.id)[:2]) or real_match(db, cap, it, fixed))
    th, out = _run(C.step, "cara")
    try:
        assert entered.wait(WAIT)
        r = u.get("/api/ap/busy")
        assert r.status_code == 200, r.text
        m = r.json()["captures"][str(c.id)]
        assert (m["bot"], m["step"], m["stuck"], m["item_id"]) == ("Cara", "reading", False, c.item_id)
        cara = next(x for x in u.get("/api/ap/live").json()["bots"] if x["key"] == "cara")
        assert cara["doc"]["capture_id"] == c.id and cara["doc"]["file"] == f"busy_{run}.pdf"
        with SessionLocal() as s:
            s.get(ApCapture, c.id).busy_beat = utcnow() - timedelta(minutes=1)
            s.commit()
        assert busy.beat_now() >= 1
        assert utcnow() - _marks("ap_capture", c.id)[3] < timedelta(seconds=30)
    finally:
        release.set()
        th.join(WAIT)
    assert "error" not in out, out
    assert _marks("ap_capture", c.id) == ("", "", None, None)
    assert busy.current("ap_capture", c.id) is None
    assert str(c.id) not in u.get("/api/ap/busy").json()["captures"]
    assert at_match == [("Mia", "matching")]


def test_the_queue_order_shows_in_line(queued):
    u, b, c, run = queued
    line = {x["id"]: x["n"] for x in u.get("/api/ap/busy").json()["line"]}
    assert line[c.id] == len([x for x in line if x < c.id]) + 1  # first come, first read


# ---- 2. an exception clears it ---------------------------------------------------------------------------------------------
def test_a_crash_clears_the_mark(monkeypatch, queued):
    u, b, c, run = queued
    seen = {}

    def boom(db):
        seen["mark"] = _marks("ap_capture", c.id)[:2]
        raise RuntimeError("the reader fell over")

    monkeypatch.setattr(C, "settings_of", boom)
    with pytest.raises(RuntimeError):
        C.step("dex")
    assert seen["mark"] == ("Dex", "reading")
    assert _marks("ap_capture", c.id) == ("", "", None, None)
    assert busy.current("ap_capture", c.id) is None


def test_working_clears_on_an_exception_in_the_callers_session(queued):
    u, b, c, run = queued
    with pytest.raises(ValueError):
        with session_scope() as db:
            with busy.working("ap_capture", c.id, "Mia", "matching", db=db):
                assert _marks("ap_capture", c.id)[:2] == ("Mia", "matching")
                db.get(ApCapture, c.id).needs = "odd"  # the caller's work, rolled back with the crash
                raise ValueError("no")
    assert _marks("ap_capture", c.id) == ("", "", None, None)
    with SessionLocal() as s:
        assert s.get(ApCapture, c.id).needs == ""


# ---- 3. stuck: back in the queue once, and cleared ---------------------------------------------------------------------------
def test_a_bot_with_no_sign_of_life_loses_its_invoice_back_to_the_queue_once(monkeypatch, queued):
    u, b, c, run = queued
    monkeypatch.setattr(C, "model_on", lambda: False)  # the wake parks it (no reader runs in the test)
    old = utcnow() - timedelta(minutes=5)
    with SessionLocal() as s:
        x = s.get(ApCapture, c.id)
        x.stage, x.worker, x.claimed_at = "capturing", "eli", utcnow() - timedelta(minutes=5)
        x.busy_bot, x.busy_step, x.busy_since, x.busy_beat = "Eli", "reading", old, old
        s.commit()
    m = u.get("/api/ap/busy").json()["captures"][str(c.id)]
    assert m["stuck"] is True and m["quiet"] >= 299
    C.wake_routine()
    C.wake_routine()
    busy.requeue_stuck()
    with SessionLocal() as s:
        x = s.get(ApCapture, c.id)
        assert (x.stage, x.worker, x.claimed_at, x.busy_bot, x.busy_since) == ("queued", "", None, "", None)
        hist = [h["text"] for h in s.get(ApItem, c.item_id).history]
    assert hist.count("Eli stopped answering; back in the queue.") == 1
    assert _audits("ap_stuck_requeued", f"ap_item:{c.item_id}") == 1


def test_a_beat_keeps_a_long_step_alive(queued):
    u, b, c, run = queued
    with SessionLocal() as s:
        x = s.get(ApCapture, c.id)
        x.stage, x.worker = "capturing", "cara"
        x.busy_bot, x.busy_step, x.busy_since, x.busy_beat = "Cara", "reading", utcnow() - timedelta(minutes=10), utcnow() - timedelta(seconds=30)
        s.commit()
    assert busy.requeue_stuck(("ap_capture",)) == 0
    assert _marks("ap_capture", c.id)[0] == "Cara"
    busy.clear("ap_capture", c.id)


# ---- 4. the start clears every mark -----------------------------------------------------------------------------------------
def test_the_start_clears_every_mark(monkeypatch, queued):
    u, b, c, run = queued
    now = utcnow()
    with SessionLocal() as s:
        x = s.get(ApCapture, c.id)
        x.busy_bot, x.busy_step, x.busy_since, x.busy_beat = "Seth", "reading", now, now
        it = s.get(ApItem, c.item_id)
        it.busy_bot, it.busy_step, it.busy_since, it.busy_beat = "Paula", "reading", now, now
        s.commit()
    tid = _ticket(title=f"Busy start {run}", requester_email="busy.start@test.local", status="new", busy_bot="Mo", busy_step="reading", busy_since=now, busy_beat=now)

    class _NoThread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

    monkeypatch.setattr(jobs, "_inline", False)
    monkeypatch.setattr(jobs, "_sched", None)
    monkeypatch.setattr(jobs.threading, "Thread", _NoThread)
    jobs.start_scheduler()  # clears the marks before the routines and the first job
    assert _marks("ap_capture", c.id) == ("", "", None, None)
    assert _marks("ap_item", c.item_id) == ("", "", None, None)
    assert _marks("ticket", tid) == ("", "", None, None)
    _drop_ticket(tid)


# ---- 5. the busy endpoints show only what the person may see -----------------------------------------------------------------
def test_the_busy_endpoints_show_only_what_the_person_may_see(queued):
    u, b, c, run = queued
    stranger = signed_in(f"nobusy.{run.lower()}@test.local", "user", name="No Tools")
    assert stranger.get("/api/ap/busy").status_code == 403  # no AP Tool
    assert stranger.get("/api/support/busy").status_code == 403  # not on the desk
    now = utcnow()
    other = signed_in(f"other.lead.{run.lower()}@test.local", "user", name="Other Lead", tools=["support-desk"])
    with SessionLocal() as s:
        other_id = s.query(User.id).filter(User.email == f"other.lead.{run.lower()}@test.local").scalar()
    fid = _ticket(title=f"Free {run}", requester_email="someone@client.example", status="ready", busy_bot="Lu", busy_step="reading", busy_since=now, busy_beat=now)
    oid = _ticket(title=f"Theirs {run}", requester_email="someone@client.example", status="assigned", assigned_to_id=other_id,
                  busy_bot="Kit", busy_step="reading", busy_since=now, busy_beat=now)
    try:
        mgr = signed_in(f"busy.mgr.{run.lower()}@test.local", "admin", name="Desk Manager")
        got = mgr.get("/api/support/busy").json()["tickets"]
        assert {str(fid), str(oid)} <= set(got) and got[str(fid)]["bot"] == "Lu"
        staff = signed_in(f"busy.staff.{run.lower()}@test.local", "user", name="Desk Staff", tools=["support-desk"])
        mine = staff.get("/api/support/busy").json()["tickets"]
        assert str(fid) in mine and str(oid) not in mine  # someone else's ticket is not theirs to see
        assert str(oid) in other.get("/api/support/busy").json()["tickets"]
    finally:
        _drop_ticket(fid)
        _drop_ticket(oid)


# ---- 6. the AP team's batch reading: the file shows busy while its reader runs ----------------------------------------------
def test_the_batch_file_shows_busy_while_its_reader_runs(monkeypatch):
    run = uuid.uuid4().hex[:6].upper()
    monkeypatch.setattr(C, "model_on", lambda: False)
    c = _user(f"busyb.{run.lower()}@test.local", "Batch Person")
    entered, release = threading.Event(), threading.Event()
    real = E.read

    def slow(name, data):
        if name.startswith("slow_"):
            entered.set()
            release.wait(WAIT)
        return real(name, data)

    monkeypatch.setattr(E, "read", slow)
    with SessionLocal() as s:
        me = s.query(User).filter(User.email == f"busyb.{run.lower()}@test.local").one()

    def drop():
        with session_scope() as db:
            S.create_batch(db, db.get(User, me.id), [(f"slow_{run}.pdf", make_ap.invoice(no=f"SL-{run}"))], f"Slow {run}")

    th, out = _run(drop)
    try:
        assert entered.wait(WAIT)
        got = {}
        assert _wait_for(lambda: got.update(c.get("/api/ap/busy").json()) or got["items"])
        (iid, m), = got["items"].items()
        assert (m["bot"], m["step"]) == ("Paula", "reading")
        team = c.get("/api/ap/live").json()["team_docs"]
        assert team["Paula"]["file"] == f"slow_{run}.pdf" and team["Paula"]["capture_id"] is None
    finally:
        release.set()
        th.join(WAIT)
    assert "error" not in out, out
    assert _marks("ap_item", int(iid)) == ("", "", None, None)


# ---- 7. the desk's reading of a ticket -----------------------------------------------------------------------------------------
def test_the_desk_reader_marks_the_ticket_while_it_reads(monkeypatch):
    from cbx.support import core as SC

    run = uuid.uuid4().hex[:6].upper()
    tid = _ticket(title=f"Printer {run}", description="The printer on floor 3 at Hyde Park does not print.", requester_email=f"req.{run.lower()}@client.example",
                  requester_name="Req Person", status="new")
    entered, release = threading.Event(), threading.Event()

    def slow(db, t, asked):
        db.commit()  # as the readers' model call does (team._ai): no write lock is held while a model thinks
        entered.set()
        release.wait(WAIT)
        return None

    monkeypatch.setattr(SC, "_model", slow)

    def read():
        db = SessionLocal()
        try:
            SC.read_safely(db, tid)
        finally:
            db.close()

    th, out = _run(read)
    try:
        assert entered.wait(WAIT)
        mgr = signed_in(f"busy.desk.{run.lower()}@test.local", "admin", name="Desk Boss")
        m = mgr.get("/api/support/busy").json()["tickets"][str(tid)]
        assert m["step"] == "reading" and m["bot"]
    finally:
        release.set()
        th.join(WAIT)
    assert "error" not in out, out
    assert _marks("ticket", tid) == ("", "", None, None)
    assert busy.current("ticket", tid) is None
    _drop_ticket(tid)


def test_a_stuck_ticket_reading_goes_back_in_the_queue_once():
    run = uuid.uuid4().hex[:6].upper()
    old = utcnow() - timedelta(minutes=4)
    tid = _ticket(title=f"Stuck {run}", requester_email=f"stuck.{run.lower()}@client.example", status="ready",
                  busy_bot="Mo", busy_step="reading", busy_since=old, busy_beat=old)
    busy.requeue_stuck(("ticket",))
    busy.requeue_stuck(("ticket",))
    assert _marks("ticket", tid) == ("", "", None, None)
    with SessionLocal() as s:
        notes = s.query(SupportMessage).filter(SupportMessage.ticket_id == tid, SupportMessage.text == "Mo stopped answering; back in the queue.").count()
    assert notes == 1
    assert _audits("support_stuck_requeued", f"ticket:T-{tid:04d}") == 1
    _drop_ticket(tid)
