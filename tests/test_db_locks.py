"""1.0.60: a model call never holds SQLite's one write lock ("database is locked", 30 Sep)."""
import pytest

from cbx.cexa import gateway as G
from cbx.config import settings
from cbx.db import SessionLocal
from cbx.models import AppState, ModelCall


def _held(db):
    db.add(AppState(key="lock-test", value="1"))
    db.flush()


def test_a_model_call_with_an_open_write_is_caught_in_the_suite(client):
    db = SessionLocal()
    try:
        _held(db)
        assert G.holds_write_lock(db)
        with pytest.raises(AssertionError, match="write lock"):
            G.Gateway(db).text(task="chat", system="s", user="u")
    finally:
        db.rollback()
        db.close()


def test_on_a_server_the_work_is_saved_first_and_others_are_not_blocked(client, monkeypatch):
    monkeypatch.setattr(settings, "strict_db_locks", False)
    db = SessionLocal()
    try:
        _held(db)
        G.Gateway(db).text(task="chat", system="s", user="u")
        assert not G.holds_write_lock(db)
        other = SessionLocal()
        try:
            assert other.get(AppState, "lock-test") is not None      # saved, visible to everyone
            other.add(AppState(key="lock-test-2", value="1"))
            other.commit()                                           # and nobody waits
            other.delete(other.get(AppState, "lock-test-2"))
            other.delete(other.get(AppState, "lock-test"))
            other.commit()
        finally:
            other.close()
    finally:
        db.close()


def test_the_model_call_row_does_not_open_a_write_in_the_callers_session(client):
    db = SessionLocal()
    try:
        before = db.query(ModelCall).count()
        G.Gateway(db).text(task="chat", system="s", user="u")
        assert not G.holds_write_lock(db)
        G.Gateway(db).text(task="chat", system="s", user="u")       # a second call is not refused
        db.rollback()
        assert db.query(ModelCall).count() == before + 2              # both written on their own
    finally:
        db.close()


def test_a_save_that_cannot_happen_is_a_503_not_a_200_or_a_page_of_errors(owner, monkeypatch):
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import Session

    real = Session.commit
    calls = {"n": 0}

    def busy(self):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OperationalError("UPDATE x", {}, Exception("database is locked"))
        return real(self)

    monkeypatch.setattr(Session, "commit", busy)
    r = owner.post("/api/develop/tasks", json={"title": "A task that does not get saved this time"})
    monkeypatch.setattr(Session, "commit", real)
    assert r.status_code == 503 and "busy" in r.json()["detail"] and r.headers.get("retry-after") == "2"
    assert all(t["title"] != "A task that does not get saved this time" for col in owner.get("/api/develop/tasks").json()["columns"].values() for t in col)
