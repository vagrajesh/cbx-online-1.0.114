"""The per-request session touch is throttled to once a minute.

Every request used to write `last_seen`, which made every request a writer. A browser fires several
at once (the page, the sidebar counts, the drawer); two of them reading the same session row and then
both updating it is the one SQLite lock that busy_timeout cannot wait out — the second transaction's
snapshot is stale, so it fails immediately. That was "database is locked" on a fresh install with
one person signed in. Reads must not write.
"""
from __future__ import annotations

from datetime import timedelta

from cbx.db import utcnow
from cbx.models import LoginSession, User
from cbx.security.auth import SESSION_COOKIE


def test_a_request_inside_the_minute_does_not_touch_last_seen(owner, db):
    sid = owner.cookies.get(SESSION_COOKIE)
    s = db.get(LoginSession, sid)
    before = s.last_seen
    owner.get("/api/home/counts")
    owner.get("/api/training")
    db.expire_all()
    assert db.get(LoginSession, sid).last_seen == before      # read, not written


def test_but_an_old_touch_is_refreshed_and_idle_timeout_still_works(owner, db):
    sid = owner.cookies.get(SESSION_COOKIE)
    s = db.get(LoginSession, sid)
    s.last_seen = utcnow() - timedelta(minutes=5)
    db.commit()
    owner.get("/api/home/counts")
    db.expire_all()
    assert utcnow() - db.get(LoginSession, sid).last_seen < timedelta(seconds=30)
    u = db.get(User, s.user_id)
    assert utcnow() - u.last_seen < timedelta(seconds=30)
    # and a session idle past the limit is still refused
    s = db.get(LoginSession, sid)
    s.last_seen = utcnow() - timedelta(hours=9)
    db.commit()
    assert owner.get("/api/home/counts").status_code == 401
