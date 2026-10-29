"""The update ceremony: announce → countdown on /api/version → switch (the ordinary promote) → the marker
is forgotten once the announced version is what runs. Cancel undoes the announcement; a second
announcement, or a switch-now while one is announced, is refused."""
import os
import time
from pathlib import Path

import pytest

from cbx.config import settings
from cbx.versions import injector, rollout

from .test_versions import _fake_live, _zip_from_tree

NESTED = bool(os.environ.get("CBX_SANDBOX_RUN"))


@pytest.fixture(autouse=True)
def _clean_marker():
    rollout.stop()
    rollout._clear()
    yield
    rollout.stop()
    rollout._clear()


def _staged(owner, tmp_path, monkeypatch, tag="9.9.5"):
    live = _fake_live(tmp_path)
    monkeypatch.setattr(injector, "LIVE_ROOT", live)
    monkeypatch.setattr(injector, "TEST_CMD", [*injector.TEST_CMD[:4], "tests/test_core.py", "-x"])
    monkeypatch.setattr(injector, "RESTART_ENABLED", False)
    monkeypatch.setattr(injector, "RUN_PIP", False)
    r = owner.post("/api/versions/upload", files={"files": (f"cbx-{tag}.zip", _zip_from_tree(tag), "application/zip")}, data={"notes": "rollout test"})
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["status"] == "sandbox", v
    return live, v


def test_version_endpoint_is_public_and_uncached(client):
    r = client.get("/api/version")
    assert r.status_code == 200
    assert r.json()["version"] and r.json()["pending"] is None and r.json()["restarting"] is False
    assert r.headers["cache-control"] == "no-store"


@pytest.mark.skipif(NESTED, reason="no nested sandbox test runs")
def test_announce_countdown_cancel(owner, admin, client, tmp_path, monkeypatch):
    live, v = _staged(owner, tmp_path, monkeypatch)
    r = owner.post(f"/api/versions/{v['id']}/promote?skip_test=1&minutes=10")
    assert r.status_code == 200, r.text
    a = r.json()["announced"]
    assert a["tag"] == v["tag"] and 590 <= a["seconds_left"] <= 600 and a["minutes"] == 10
    assert r.json()["status"] == "sandbox"  # nothing has switched
    assert (live / "VERSION").read_text(encoding="utf-8").strip() == "0.9.9"  # nothing on disk changed
    assert rollout.marker_path().exists()
    # every browser sees the countdown, signed in or not
    p = client.get("/api/version").json()["pending"]
    assert p["tag"] == v["tag"] and p["seconds_left"] > 0 and p["switching"] is False
    assert owner.get("/api/versions").json()["pending"]["tag"] == v["tag"]
    # a second announcement, and a switch-now, are refused while one is pending
    assert "already announced" in owner.post(f"/api/versions/{v['id']}/promote?skip_test=1&minutes=5").json()["detail"]
    assert "Cancel that first" in owner.post(f"/api/versions/{v['id']}/promote?skip_test=1&minutes=0").json()["detail"]
    # admins can't touch it; the owner cancels
    assert admin.post("/api/versions/pending/cancel").status_code == 403
    r = owner.post("/api/versions/pending/cancel")
    assert r.status_code == 200 and r.json()["was"] == v["tag"]
    assert client.get("/api/version").json()["pending"] is None
    assert not rollout.marker_path().exists()
    rows = owner.get("/api/audit?action=version_announced").json()["rows"]
    assert rows and rows[0]["target"] == v["tag"]
    assert owner.get("/api/audit?action=version_announce_cancelled").json()["rows"]


@pytest.mark.skipif(NESTED, reason="no nested sandbox test runs")
def test_switch_runs_the_promote_when_the_timer_ends(owner, tmp_path, monkeypatch):
    live, v = _staged(owner, tmp_path, monkeypatch, tag="9.9.6")
    monkeypatch.setattr(rollout, "MAX_MINUTES", 240)
    r = owner.post(f"/api/versions/{v['id']}/promote?skip_test=1&minutes=1")
    assert r.status_code == 200, r.text
    # don't wait a minute: fire the timer by hand, as it would fire itself
    rollout._disarm()
    (Path(settings.data_dir) / "RESTART_REQUESTED").unlink(missing_ok=True)
    rollout._fire()
    assert (live / "VERSION").read_text(encoding="utf-8").strip() == "9.9.6"
    assert (Path(settings.data_dir) / "RESTART_REQUESTED").exists()
    # with no restart (tests), the marker is dropped at once; live shows on the page
    assert not rollout.marker_path().exists()
    lst = owner.get("/api/versions").json()
    assert next(x for x in lst["versions"] if x["tag"] == "9.9.6")["status"] == "live" and lst["pending"] is None
    assert owner.get("/api/audit?action=version_switched").json()["rows"]


def test_resume_on_boot_forgets_an_arrived_update_and_rearms_a_pending_one(monkeypatch):
    from cbx import VERSION

    rollout._write({"tag": VERSION, "version_id": 1, "by_id": 1, "by_email": "o@x.com", "announced_at": "2026-01-01T00:00:00+00:00", "switch_at": "2026-01-01T00:10:00+00:00", "minutes": 10})
    assert "arrived" in rollout.resume_on_boot()
    assert not rollout.marker_path().exists()
    rollout._write({"tag": "0.0.1", "version_id": 1, "by_id": 1, "by_email": "o@x.com", "announced_at": "2026-01-01T00:00:00+00:00", "switch_at": "2026-01-01T00:10:00+00:00", "minutes": 10})
    note = rollout.resume_on_boot()
    assert note and "0.0.1" in note and rollout._timer is not None
    # overdue: the browsers still get a minute to notice before the switch runs
    assert rollout.pending()["seconds_left"] == 0 and rollout.pending()["switching"] is True
    rollout.stop()
    assert rollout._timer is None


def test_fire_with_a_missing_version_clears_the_marker(db):
    rollout._write({"tag": "0.0.2", "version_id": 999999, "by_id": 1, "by_email": "o@x.com", "announced_at": "2026-01-01T00:00:00+00:00", "switch_at": "2026-01-01T00:10:00+00:00", "minutes": 10})
    rollout._fire()
    assert not rollout.marker_path().exists()
    time.sleep(0)  # nothing else was started
