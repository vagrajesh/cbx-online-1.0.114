"""Training: lessons offered by what you hold, progress that is yours, practice data that stays out
of the real grid, and the two halves of a lesson (server catalogue, browser script) kept in step."""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from cbx.main import app
from cbx.models import TrainingProgress
from cbx.models.docs import Entity
from cbx.training import catalog as CAT
from tests.conftest import _make, login


def _client(owner, email, name, role, tools=None):
    pw = _make(owner, email, name, role, tools=tools)
    c = TestClient(app)
    login(c, email, pw)
    return c


def test_the_owner_sees_every_lesson_and_a_user_sees_only_theirs(owner, db):
    d = owner.get("/api/training").json()
    ids = {l["id"] for l in d["lessons"]}
    assert ids == {l.id for l in CAT.LESSONS}
    assert all(l["available"] for l in d["lessons"])          # the owner holds every tool and capability
    assert d["can_reset"] is True

    c = _client(owner, "trainee@test.local", "Tra Inee", "user", tools=[])
    d = c.get("/api/training").json()
    ids = {l["id"] for l in d["lessons"]}
    assert "admin.people" not in ids and "owner.versions" not in ids   # capability lessons: not theirs to see
    ent = next(l for l in d["lessons"] if l["id"] == "pdf.batch")
    assert ent["available"] is False and "PDF Studio" in ent["why_not"]  # tool lessons: shown, greyed, with the reason
    assert next(l for l in d["lessons"] if l["id"] == "around.sidebar")["available"] is True
    assert d["can_reset"] is False


def test_progress_is_recorded_and_finishing_is_audited(owner, db):
    r = owner.post("/api/training/around.sidebar", json={"step": 3, "total": 7})
    assert r.status_code == 200 and r.json()["step"] == 3 and r.json()["done"] is False
    r = owner.post("/api/training/around.sidebar", json={"step": 7, "total": 7, "done": True})
    assert r.json()["done"] is True and r.json()["times_done"] == 1
    d = owner.get("/api/training").json()
    assert d["done"] >= 1
    from cbx.models import AuditLog

    assert db.query(AuditLog).filter(AuditLog.action == "training_done", AuditLog.target == "around.sidebar").count() >= 1
    # restart puts them back at the start without forgetting it was done once
    r = owner.post("/api/training/around.sidebar", json={"restart": True})
    assert r.json()["step"] == 0 and r.json()["done"] is False and r.json()["times_done"] == 1
    assert owner.post("/api/training/no.such.lesson", json={"step": 1}).status_code == 404


def test_practice_entities_stay_off_the_real_grid(owner, db):
    real = owner.get("/api/docwatch").json()
    before = {e["name"] for e in real["entities"]}
    t = owner.get("/api/docwatch?training=1").json()
    names = {e["name"] for e in t["entities"]}
    assert names == {"Training Co (practice)", "Training Site (practice)"} and t["training"] is True
    after = {e["name"] for e in owner.get("/api/docwatch").json()["entities"]}
    assert after == before                      # creating them changed nothing on the real grid
    assert not (after & names)
    db.expire_all()
    assert all(e.is_demo for e in db.query(Entity).filter(Entity.name.in_(names)).all())
    # the archived list, the reminders and the counts all go through visible_entities(): demo=False by default
    arch = owner.get("/api/docwatch/entities/archived").json()
    assert not any(e["name"] in names for e in (arch.get("entities") or arch.get("rows") or []))


def test_practice_files_are_generated_and_hold_nothing_real(owner):
    for name in ("fire-certificate.pdf", "tax-clearance.pdf", "cover-no-date.pdf", "bill-1.pdf"):
        r = owner.get(f"/api/training/demo/{name}")
        assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content[:4] == b"%PDF"
    assert owner.get("/api/training/demo/../../etc/passwd").status_code in (404, 400)
    assert owner.get("/api/training/demo/nope.pdf").status_code == 404
    # and the expiry in the fire certificate really is readable by the scanner
    import io

    import pdfplumber

    from cbx.docwatch.scanner import parse_date

    text = "\n".join((p.extract_text() or "") for p in pdfplumber.open(io.BytesIO(owner.get("/api/training/demo/fire-certificate.pdf").content)).pages)
    assert "TRAINING DOCUMENT" in text
    m = re.search(r"Valid until: (\d\d/\d\d/\d{4})", text)
    assert m and parse_date(m.group(1)) is not None


def test_reset_is_the_owners_and_clears_only_practice_cells(owner, db):
    owner.get("/api/docwatch?training=1")
    r = owner.post("/api/training/reset")
    assert r.status_code == 200 and r.json()["entities"] == 2
    c = _client(owner, "notowner@test.local", "Not Owner", "admin")
    assert c.post("/api/training/reset").status_code == 403


def test_every_lesson_in_the_catalogue_has_a_script_in_the_browser_and_vice_versa():
    """The server decides who may take a lesson; the browser knows how to play it. One without the
    other is a button that does nothing, so the two lists must agree exactly."""
    js = (Path(__file__).resolve().parents[1] / "web" / "training" / "lessons.js").read_text(encoding="utf-8")
    in_js = set(re.findall(r"^\s*'([a-z]+\.[a-z]+)':\s*\{", js, re.M))
    in_py = {l.id for l in CAT.LESSONS}
    assert in_js == in_py, f"only in lessons.js: {in_js - in_py} · only in catalog.py: {in_py - in_js}"
