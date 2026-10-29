"""The watcher: reads the audit trail, alerts owners on what is wrong, reports once a day, never
repeats itself, never touches anything else."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from cbx import state
from cbx.audit import record_error
from cbx.cexa import watcher as W
from cbx.models import AuditLog, CexaMessage, User


def _owner_id(db):
    return db.query(User).filter(User.role == "owner", User.is_axl.is_(False)).first().id


def _inbox(db, kind=None):
    q = db.query(CexaMessage).filter(CexaMessage.user_id == _owner_id(db), CexaMessage.from_axl.is_(True))
    if kind:
        q = q.filter(CexaMessage.kind == kind)
    return q.order_by(CexaMessage.id.asc()).all()


def _catch_up(db):
    """Point the cursor at the newest row, so a test only sees what it writes itself."""
    last = db.query(AuditLog).order_by(AuditLog.id.desc()).first()
    state.set(db, W.CURSOR, str(last.id if last else 0))
    state.set(db, W.TALLY, "")
    db.commit()


def test_a_burst_of_failed_sign_ins_reaches_the_owner_once(owner, db):
    _catch_up(db)
    before = len(_inbox(db, "watch"))
    for _ in range(5):
        db.add(AuditLog(actor_label="stranger@test.local", action="login_failed", outcome="failed", ip="41.0.0.9", detail="wrong password"))
    db.commit()
    r = W.run(db)
    db.commit()
    assert r["read"] == 5 and r["alerts"] == 1
    msgs = _inbox(db, "watch")
    assert len(msgs) == before + 1
    assert "5 failed sign-ins for stranger@test.local" in msgs[-1].text and "41.0.0.9" in msgs[-1].text
    assert db.query(AuditLog).filter(AuditLog.action == "watcher_alert").count() >= 1
    # the same rows are never read twice
    r2 = W.run(db)
    assert r2["read"] == 0 and r2["alerts"] == 0
    assert len(_inbox(db, "watch")) == before + 1


def test_four_failures_is_not_a_burst(owner, db):
    _catch_up(db)
    before = len(_inbox(db, "watch"))
    for _ in range(4):
        db.add(AuditLog(actor_label="fumbler@test.local", action="login_failed", outcome="failed"))
    db.commit()
    assert W.run(db)["alerts"] == 0
    assert len(_inbox(db, "watch")) == before


def test_a_server_error_is_recorded_and_reported(owner, db):
    _catch_up(db)
    record_error("server_error", "/api/pdfstudio/batches", "ZeroDivisionError: division by zero")
    db.expire_all()
    assert db.query(AuditLog).filter(AuditLog.action == "server_error", AuditLog.target == "/api/pdfstudio/batches").count() == 1
    r = W.run(db)
    db.commit()
    assert r["alerts"] == 1
    m = _inbox(db, "watch")[-1]
    assert "server error on /api/pdfstudio/batches" in m.text and m.link == "#/audit?action=server_error"


def test_the_daily_report_goes_out_once_after_seven_local(owner, db):
    _catch_up(db)
    # yesterday's tally, as the watcher would have left it
    yesterday = (datetime.now(timezone.utc) + W.LOCAL_OFFSET - timedelta(days=1)).date().isoformat()
    state.set(db, W.TALLY, json.dumps({"day": yesterday, "logins": 7, "people": ["a", "b", "c"], "failed": 1, "unverified": 0, "blocked": 0, "errors": 0, "filed": 3, "batches": 2, "rows": 40, "trusted": 1, "roles": 0}))
    state.set(db, W.REPORTED, "")
    db.commit()
    six = datetime.now(timezone.utc).replace(hour=4, minute=0)    # 06:00 Johannesburg — too early
    r = W.run(db, now=six)
    assert r["report"] is False
    eight = datetime.now(timezone.utc).replace(hour=6, minute=5)  # 08:05 Johannesburg
    r = W.run(db, now=eight)
    db.commit()
    assert r["report"] is True
    rep = _inbox(db, "brief")[-1]
    assert rep.title.startswith("Watcher —") and "7 sign-ins by 3 people" in rep.text and "3 certificates" in rep.text and "2 PDF batches" in rep.text
    # and not again today
    assert W.run(db, now=eight)["report"] is False
    assert db.query(AuditLog).filter(AuditLog.action == "watcher_report").count() >= 1


def test_the_watcher_is_a_real_standing_agent(owner):
    from cbx.cexa import agents

    snap = agents.snapshot()
    kinds = {a["kind"] for a in snap["agents"]}
    assert {"harvester", "dev", "watcher"} <= kinds
    assert "recon" not in kinds and "sheets" not in kinds          # decoration removed
    W.watch()                                                       # the routine itself runs clean
    a = next(x for x in agents.snapshot()["agents"] if x["kind"] == "watcher")
    assert a["state"] == "idle" and a["notes"] and "Read" in a["notes"][-1]
