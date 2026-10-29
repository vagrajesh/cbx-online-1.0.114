"""Develop board CRUD, moves, notes, and C-EXA watching the board; C-EXA as developer (notice → go → signoff)."""
import os

import pytest

from cbx.cexa import developer
from cbx.models import Folder, Proposal, Request, StoredFile, Task, User
from cbx.versions import injector

NESTED = bool(os.environ.get("CBX_SANDBOX_RUN"))


def _uid(db, email):
    return db.query(User).filter(User.email == email).one().id


def test_task_crud_move_notes(owner, admin, db):
    aid = _uid(db, "admin@test.local")
    r = owner.post("/api/develop/tasks", json={"title": "Exceptions tab export for master sheets", "detail": "Needed by #2293 R3.", "assignee_id": aid, "due": "2099-09-30"})
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["status"] == "todo" and t["assignee"] == "Arnab Admin" and t["overdue"] is False
    cols = admin.get("/api/develop/tasks").json()["columns"]
    assert any(x["id"] == t["id"] for x in cols["todo"])

    r = admin.patch(f"/api/develop/tasks/{t['id']}", json={"status": "working"})
    assert r.json()["status"] == "working" and r.json()["started_at"] and r.json()["trace"][-1]["to"] == "working"
    r = admin.post(f"/api/develop/tasks/{t['id']}/notes", json={"text": "Waiting on the exceptions export."})
    assert r.json()["notes"][-1]["by"] == "user" and r.json()["notes"][-1]["by_name"] == "Arnab Admin"
    r = admin.patch(f"/api/develop/tasks/{t['id']}", json={"status": "done"})
    assert r.json()["status"] == "done" and r.json()["done_at"]
    assert len(r.json()["trace"]) == 3
    assert admin.patch(f"/api/develop/tasks/{t['id']}", json={"status": "nope"}).status_code == 400
    assert owner.delete(f"/api/develop/tasks/{t['id']}").status_code == 200
    assert owner.get(f"/api/develop/tasks/{t['id']}").status_code == 404
    # users are refused
    from fastapi.testclient import TestClient

    from cbx.main import app

    assert TestClient(app).get("/api/develop/tasks").status_code == 401


def _review():
    """What the cexa-board routine does every 5 minutes (the button-less /cexa-review went in 1.0.79)."""
    from cbx.db import SessionLocal

    with SessionLocal() as s:
        n = len(developer.review_board(s))
        s.commit()
    return n


def test_cexa_reviews_board_once(owner, admin, db):
    aid = _uid(db, "admin@test.local")
    late = owner.post("/api/develop/tasks", json={"title": "Absa month-end workflow", "assignee_id": aid, "status": "working", "due": "2026-01-05"}).json()
    free = owner.post("/api/develop/tasks", json={"title": "Export the vendor report as xlsx"}).json()
    assert _review() >= 2
    assert _review() == 0  # never the same text twice
    late_t = owner.get(f"/api/develop/tasks/{late['id']}").json()
    assert [n for n in late_t["notes"] if n["by"] == "axl" and "late" in n["text"]]
    free_t = owner.get(f"/api/develop/tasks/{free['id']}").json()
    assert [n for n in free_t["notes"] if n["by"] == "axl" and "myself" in n["text"]]
    assert db.query(Proposal).filter(Proposal.title == "Export the vendor report as xlsx", Proposal.evidence_kind == "owner").count() == 1
    # duplicate of a proposal → merged
    p = developer.propose(db, "Ageing chart on Recon home", "requests", "x", "y")
    db.commit()
    dup = owner.post("/api/develop/tasks", json={"title": "Ageing chart for Recon home"}).json()
    _review()
    dup_t = owner.get(f"/api/develop/tasks/{dup['id']}").json()
    assert dup_t["origin_ref"] == f"proposal:{p.id}" and any("merging" in n["text"] for n in dup_t["notes"])


def _folder(db):
    f = db.query(Folder).filter(Folder.path == "Shared library/PDFs").one()
    return f.id


def test_notice_from_requests_and_unnamed_uploads(owner, admin, user, db):
    uid = _uid(db, "thandi@test.local")
    aid = _uid(db, "admin@test.local")
    base = db.query(Request).count() * 10 + 9000
    db.add(Request(number=base + 1, user_id=uid, title="Ageing buckets for vendor invoices", problem="Finance keeps asking for ageing buckets on vendor invoices every month end", status="new"))
    db.add(Request(number=base + 2, user_id=aid, title="30/60/90 day view of vendor invoices", problem="We need ageing buckets 30/60/90 for the vendor invoices report", status="new"))
    fid = _folder(db)
    for i in range(6):
        db.add(StoredFile(folder_id=fid, name=f"scan00{i}.pdf", rel_path=f"x/scan00{i}.pdf", sha256=f"s{i}", uploaded_by_id=uid, source="upload"))
    db.add(StoredFile(folder_id=fid, name="Emfuleni_Jul_2026.pdf", rel_path="x/e.pdf", sha256="e1", uploaded_by_id=uid, source="upload"))
    db.commit()
    made = developer.notice(db)
    db.commit()
    titles = [p.title for p in made]
    assert any("asked for" in t and "different words" in t for t in titles), titles
    assert any("unnamed" in t for t in titles), titles
    assert developer.notice(db) == []  # idempotent
    b = owner.get("/api/cexa/proposals").json()
    assert {p["title"] for p in b["noticed"]} >= set(titles)
    assert owner.post("/api/cexa/proposals/notice").json()["created"] == []


@pytest.mark.skipif(NESTED, reason="no nested sandbox builds")
def test_go_build_signoff_not_now(owner, admin, db, monkeypatch):
    monkeypatch.setattr(injector, "TEST_CMD", [*injector.TEST_CMD[:4], "tests/test_core.py", "-x"])
    p = developer.propose(db, "Rename inbox files from their content", "audit", "40% unnamed", "Name files municipality · account · period on arrival.")
    db.commit()
    r = owner.post(f"/api/cexa/proposals/{p.id}/go")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "signoff", d
    assert d["tests"]["passed"] >= 1 and d["tests"]["failed"] == 0
    assert d["branch"].startswith("cexa/rename-inbox-files") and d["sandbox"] in ("sandbox-a", "sandbox-b")
    assert "docs/proposals" in d["diff"]
    assert d["task_id"]
    t = owner.get(f"/api/develop/tasks/{d['task_id']}").json()
    assert t["assignee_axl"] and t["origin"] == "proposal" and any(n["by"] == "axl" for n in t["notes"])
    board = owner.get("/api/cexa/proposals").json()
    assert any(x["id"] == p.id for x in board["signoff"])
    # not now on another proposal → declined and not re-proposed
    q = developer.propose(db, "Hide tools people don't have", "audit", "x", "y")
    db.commit()
    r = owner.post(f"/api/cexa/proposals/{q.id}/not-now")
    assert r.json()["status"] == "declined"
    assert developer.propose(db, "Hide tools people don't have", "audit", "x", "y") is None
    # ship → staged version linked to the proposal
    r = owner.post(f"/api/cexa/proposals/{p.id}/ship")
    assert r.status_code == 200, r.text
    v = r.json()["version"]
    assert v["status"] in ("sandbox", "staged") and r.json()["proposal"]["version_id"] == v["id"]
    db.expire_all()
    assert db.get(Proposal, p.id).status == "signoff"  # shipped only after promotion


def test_cexa_cannot_go_or_ship(owner, db):
    """C-EXA's own account is refused by require_human."""
    from fastapi.testclient import TestClient

    from cbx.main import app
    from cbx.security.auth import SESSION_COOKIE, open_session

    cexa = db.query(User).filter(User.is_axl.is_(True)).one()
    p = developer.propose(db, "Something C-EXA wants to ship", "owner", "x", "y")
    s = open_session(db, cexa, ip="test", agent="test")
    db.commit()
    c = TestClient(app)
    c.cookies.set(SESSION_COOKIE, s.id)
    assert c.get("/api/auth/me").status_code == 200
    assert c.post(f"/api/cexa/proposals/{p.id}/go").status_code == 403
    assert c.post(f"/api/cexa/proposals/{p.id}/ship").status_code == 403
    assert c.post("/api/versions/1/promote").status_code == 403
