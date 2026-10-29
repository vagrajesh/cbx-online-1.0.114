"""1.0.113: authenticator codes against a drifting server clock, the owner's clock line, and email codes that are proven.

A stand-in clock throughout: `PHONE` is what the person's phone thinks the time is (the right time), the server's
clock (cbx/security/clock.now) is PHONE + FAST. The CBX PC ran about 60 s fast in Oct 2026.
"""
from __future__ import annotations

import threading
import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from cbx.config import settings
from cbx.main import app
from cbx.models import AuditLog, LoginSession, TrustedDevice, User
from cbx.security import clock, totp
from cbx.security import devices as D
from cbx.security import ratelimit as RL
from tests.conftest import STRONG, _make

CLK = {"phone": float(int(time.time() // 30) * 30), "fast": 0.0}


@pytest.fixture(autouse=True)
def stand_in(monkeypatch):
    RL.reset_all()
    clock.reset()
    CLK["fast"] = 0.0
    monkeypatch.setattr(clock, "now", lambda: CLK["phone"] + CLK["fast"])
    yield
    RL.reset_all()
    clock.reset()


@pytest.fixture()
def required(monkeypatch, owner):
    """Two-step verification on for everyone (after the owner fixture has signed in without it), no mailer, and the
    terminal fail-safe: where a code goes when email cannot."""
    monkeypatch.setattr(settings, "mfa_required", "everyone")
    monkeypatch.setattr(settings, "mfa_email_fallback", True)
    monkeypatch.setattr(settings, "mfa_admin_trust_days", 0)
    monkeypatch.setattr(settings, "mfa_max_failed", 3)
    monkeypatch.setattr(settings, "device_verification", True)
    monkeypatch.setattr(settings, "code_fallback", "terminal")
    monkeypatch.setattr(settings, "mail_enabled", False)


def tick(seconds: float = 30) -> None:
    CLK["phone"] += seconds


def phone_code(secret: str, offset: float = 0) -> str:
    return totp.code_now(secret, CLK["phone"] + offset)


def _person(owner, db, email, name="Clock Person", role="user"):
    """A signed-in person at the set-up screen (password changed, second step not yet set up)."""
    u = db.query(User).filter(User.email == email).one_or_none()
    if u is not None and u.mfa_method:
        assert owner.post(f"/api/users/{u.id}/mfa-reset", json={"reason": "test start"}).status_code == 200
    pw = _make(owner, email, name, role)
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200 and r.json()["must_enrol_mfa"] is True, r.text
    assert c.post("/api/auth/change-password", json={"new_password": STRONG, "repeat": STRONG}).status_code == 200
    return c


def _enrol(c) -> str:
    secret = c.post("/api/auth/mfa/setup", json={}).json()["secret"].replace(" ", "")
    r = c.post("/api/auth/mfa/confirm", json={"code": phone_code(secret)})
    assert r.status_code == 200, r.text
    return secret


def _sign_in(email: str, code: str, agent: str = "Phone test") -> TestClient:
    RL.reset_all()
    d = TestClient(app, headers={"user-agent": agent})
    r = d.post("/api/auth/login", json={"email": email, "password": STRONG})
    assert r.status_code == 200 and r.json().get("needs_code"), r.text
    d.last = d.post("/api/auth/verify", json={"email": email, "code": code})
    return d


def _user(db, email) -> User:
    db.expire_all()
    return db.query(User).filter(User.email == email).one()


# ---- 1. codes that survive a drifting server clock ------------------------------------------------
def test_server_a_minute_fast_the_phones_code_works_at_any_second_of_its_step():
    s = totp.new_secret()
    base = CLK["phone"]
    for sec in range(30):
        phone = base + sec
        step = totp.match_step(s, totp.code_now(s, phone), drift=0, at=phone + 60)
        assert step == int(phone // 30), sec  # found, and it is the phone's step
    # the 1.0.112 rule (±1 step around the server) missed it for most of the step
    misses = sum(not totp.verify(s, totp.code_now(s, base + sec), at=base + sec + 60) for sec in range(30))
    assert misses >= 25
    # a non-ASCII digit is a wrong code, not a crash
    assert totp.match_step(s, "١٢٣٤٥٦") is None and not totp.verify(s, "١٢٣٤٥٦")


def test_server_a_minute_fast_set_up_and_sign_in_end_to_end(owner, db, required):
    CLK["fast"] = 60
    c = _person(owner, db, "fast@test.local")
    secret = _enrol(c)
    assert _user(db, "fast@test.local").mfa_drift == -2  # the phone is two steps behind the server
    for sec in (1, 14, 29):  # any second of the next steps
        CLK["phone"] = (int(CLK["phone"] // 30) + 1) * 30 + sec
        d = _sign_in("fast@test.local", phone_code(secret))
        assert d.last.status_code == 200, (sec, d.last.text)


def test_plus_minus_two_steps_around_the_learned_difference(owner, db, required):
    s = totp.new_secret()
    at = CLK["phone"]
    ok = lambda off, drift: totp.match_step(s, totp.code_now(s, at + off * 30), drift=drift, at=at) is not None  # noqa: E731
    assert all(ok(k, 0) for k in (-2, -1, 0, 1, 2)) and not ok(-3, 0) and not ok(3, 0)
    assert all(ok(k, -4) for k in (-6, -5, -4, -3, -2)) and not ok(-7, -4) and not ok(-1, -4)
    assert totp.clamp_drift(-40) == -10 and totp.clamp_drift(25) == 10


def test_the_difference_is_learnt_and_followed_as_the_clock_wanders(owner, db, required):
    CLK["fast"] = 60
    c = _person(owner, db, "wander@test.local")
    secret = _enrol(c)
    assert _user(db, "wander@test.local").mfa_drift == -2
    for fast, want in ((120, -4), (180, -6), (240, -8)):  # a minute further each time: always inside ±2 of the last
        tick()
        CLK["fast"] = fast
        assert _sign_in("wander@test.local", phone_code(secret)).last.status_code == 200, fast
        assert _user(db, "wander@test.local").mfa_drift == want
    # a jump straight past the learned window is a wrong code (and counts as one)
    tick()
    CLK["fast"] = 240 + 120
    d = _sign_in("wander@test.local", phone_code(secret))
    assert d.last.status_code == 401 and d.last.json()["detail"].startswith("That code is not right.")
    assert _user(db, "wander@test.local").mfa_failed == 1


def test_a_code_works_once(owner, db, required):
    c = _person(owner, db, "replay@test.local")
    secret = _enrol(c)
    tick()
    code = phone_code(secret)
    assert _sign_in("replay@test.local", code, "Browser A").last.status_code == 200
    d = _sign_in("replay@test.local", code, "Browser B")  # the same 30 seconds, another browser
    assert d.last.status_code == 401 and "used already" in d.last.json()["detail"]
    assert d.get("/api/settings").status_code == 401
    # an earlier step is refused too, even inside the window
    assert _sign_in("replay@test.local", phone_code(secret, -30), "Browser B").last.status_code == 401
    # the phone's next code is fine
    tick()
    assert _sign_in("replay@test.local", phone_code(secret), "Browser B").last.status_code == 200
    assert db.query(AuditLog).filter(AuditLog.action == "mfa_failed", AuditLog.detail == "authenticator code used before").count() >= 1


def test_set_up_finds_a_four_minute_difference_and_says_so(owner, db, required):
    CLK["fast"] = 240
    c = _person(owner, db, "fourmin@test.local")
    secret = _enrol(c)
    u = _user(db, "fourmin@test.local")
    assert u.mfa_drift == -8 and u.mfa_method == "totp" and u.mfa_proven_at is not None
    a = db.query(AuditLog).filter(AuditLog.action == "mfa_enrolled", AuditLog.actor_id == u.id).order_by(AuditLog.id.desc()).first()
    assert "clock difference ~240 s" in a.detail and "phone is behind the server" in a.detail
    # beyond ±5 minutes set-up does not find it
    CLK["fast"] = 0
    c2 = _person(owner, db, "toofar@test.local")
    s2 = c2.post("/api/auth/mfa/setup", json={}).json()["secret"].replace(" ", "")
    r = c2.post("/api/auth/mfa/confirm", json={"code": phone_code(s2, -330)})
    assert r.status_code == 400 and "not right" in r.json()["detail"]
    # and the same code twice at set-up is refused the second time
    tick()
    code = phone_code(s2)
    assert c2.post("/api/auth/mfa/confirm", json={"code": code}).status_code == 200
    assert c2.post("/api/auth/mfa/recovery", json={"code": code}).json()["detail"].startswith("That code was used already")


def test_recovery_codes_and_remove_follow_the_same_rules(owner, db, monkeypatch, required):
    CLK["fast"] = 60
    c = _person(owner, db, "rules@test.local")
    secret = _enrol(c)
    tick()
    code = phone_code(secret)
    r = c.post("/api/auth/mfa/recovery", json={"code": code})
    assert r.status_code == 200 and len(r.json()["recovery_codes"]) == 10  # a minute-fast server, a phone code
    monkeypatch.setattr(settings, "mfa_required", "admins")
    r = c.post("/api/auth/mfa/remove", json={"code": code})  # the same code again
    assert r.status_code == 400 and "used already" in r.json()["detail"]
    assert c.post("/api/auth/mfa/remove", json={"code": phone_code(secret, 4 * 30)}).status_code == 400  # outside ±2
    tick()
    assert c.post("/api/auth/mfa/remove", json={"code": phone_code(secret)}).status_code == 200
    assert _user(db, "rules@test.local").mfa_method == ""


# ---- 2. the owner hears when the server clock is off ----------------------------------------------
def _sample(c, browser_offset: float = 0):
    """A signed-in person's browser (its clock right) says what time it is: web/api.js sends X-Client-Time on /api/auth/*."""
    ms = int((CLK["phone"] + browser_offset) * 1000)
    return c.get("/api/auth/me", headers={"X-Client-Time": str(ms)})


def _no_learned_differences(db):
    """The database is shared by the run: earlier tests left people with learned differences."""
    for u in db.query(User).filter(User.mfa_method == "totp").all():
        u.mfa_drift = 0
    db.commit()


def test_the_clock_line_needs_two_signed_in_people_and_only_owners_see_it(owner, user, admin, db):
    _no_learned_differences(db)
    CLK["fast"] = 62
    home = lambda c: c.get("/api/engine/home").json()["clock"]  # noqa: E731
    assert home(owner) == ""
    for _ in range(3):
        _sample(owner)
    assert home(owner) == ""  # one person, however many times: could be that one PC
    _sample(user)
    line = home(owner)
    assert line.startswith("This server's clock is about 1 minute fast.") and "Sync now" in line
    assert home(user) == ""  # staff never see it
    assert user.get("/api/home/attention").status_code == 403
    assert any(i["key"] == "server_clock_off" for i in owner.get("/api/home/attention").json()["items"])
    home(owner)
    assert db.query(AuditLog).filter(AuditLog.action == "server_clock_off").count() == 1  # once a day
    # a person whose own PC clock is wrong is outvoted
    clock.reset()
    CLK["fast"] = 0
    _sample(owner)
    _sample(user)
    _sample(admin, -300)
    assert home(owner) == ""
    # slow, in plain words
    clock.reset()
    CLK["fast"] = -200
    _sample(owner)
    _sample(user)
    assert "about 3 minutes slow" in home(owner)


def test_the_sign_in_form_and_pending_sessions_cannot_plant_clock_samples(owner, db, required):
    """Security review: anyone can post the sign-in form, so its X-Client-Time never counts; nor does a session that
    has not passed its second step."""
    _no_learned_differences(db)
    fake = str(int((CLK["phone"] - 3600) * 1000))
    for ua in ("a", "b", "c"):
        for _ in range(2):
            RL.reset_all()
            TestClient(app, headers={"user-agent": ua, "x-client-time": fake}).post("/api/auth/login", json={"email": "clock-sample-1113@test.local", "password": "x"})
    pend = _person(owner, db, "pending-clock@test.local")
    pend.get("/api/auth/me", headers={"X-Client-Time": fake})
    assert len(clock._SAMPLES) == 0
    assert clock.check(db) == ""


def test_two_peoples_learned_differences_also_raise_it(owner, db, required):
    _no_learned_differences(db)
    assert clock.offset(db) is None
    CLK["fast"] = 60
    for e in ("drift1@test.local", "drift2@test.local"):
        _enrol(_person(owner, db, e))
    db.expire_all()
    assert round(clock.offset(db)) == 60
    assert "about 1 minute fast" in owner.get("/api/engine/home").json()["clock"]
    # it never changes what a code is accepted for: a code outside the window is still wrong
    tick()
    d = _sign_in("drift1@test.local", phone_code(_secret_of(db, "drift1@test.local"), -5 * 30))
    assert d.last.status_code == 401


def _secret_of(db, email):
    from cbx.security import secretbox

    return secretbox.open_(_user(db, email).mfa_secret)


# ---- 3. email codes are proven ---------------------------------------------------------------------
def _waiting(email: str) -> str:
    return next(v["code"] for v in D._WAITING.values() if v["email"] == email)


def test_use_email_sends_a_code_and_switches_only_when_it_comes_back(owner, db, required):
    c = _person(owner, db, "proveme@test.local")
    r = c.post("/api/auth/mfa/use-email")
    assert r.status_code == 200 and r.json()["sent_to"] == "the server"  # the terminal fail-safe: this server's way
    u = _user(db, "proveme@test.local")
    assert u.mfa_method == "" and u.mfa_proven_at is None
    assert c.get("/api/settings").status_code == 403  # still pending: nothing else opens
    assert c.get("/api/auth/me").json()["must_enrol_mfa"] is True
    # a wrong code counts like any wrong code
    r = c.post("/api/auth/mfa/use-email/confirm", json={"code": "000000" if _waiting("proveme@test.local") != "000000" else "111111"})
    assert r.status_code == 400 and "not right" in r.json()["detail"]
    assert _user(db, "proveme@test.local").mfa_failed == 1
    assert c.get("/api/settings").status_code == 403
    r = c.post("/api/auth/mfa/use-email/confirm", json={"code": _waiting("proveme@test.local")})
    assert r.status_code == 200 and r.json()["mfa"]["method"] == "email" and r.json()["mfa"]["proven"] is True
    u = _user(db, "proveme@test.local")
    assert u.mfa_method == "email" and u.mfa_proven_at is not None and u.mfa_failed == 0
    assert c.get("/api/settings").status_code == 200
    assert D.DEVICE_COOKIE in c.cookies  # remembered like a browser that passed a sign-in code
    assert db.query(LoginSession).filter(LoginSession.user_id == u.id, LoginSession.pending_mfa.is_(True), LoginSession.revoked.is_(False)).count() == 0


def test_wrong_email_codes_lock_like_app_codes(owner, db, required):
    c = _person(owner, db, "lockmail@test.local")
    c.post("/api/auth/mfa/use-email")
    bad = "000000" if _waiting("lockmail@test.local") != "000000" else "111111"
    for _ in range(3):
        assert c.post("/api/auth/mfa/use-email/confirm", json={"code": bad}).status_code == 400
    u = _user(db, "lockmail@test.local")
    assert u.locked is True and u.mfa_method == ""
    assert c.post("/api/auth/mfa/use-email/confirm", json={"code": _waiting("lockmail@test.local")}).status_code == 403  # locked


def test_no_way_to_send_a_code_refuses_email_codes(owner, db, monkeypatch, required):
    monkeypatch.setattr(settings, "code_fallback", "let_in")  # no terminal fail-safe and no mailer
    c = _person(owner, db, "no-mailer-1113@test.local")
    r = c.post("/api/auth/mfa/use-email")
    assert r.status_code == 400 and r.json()["detail"] == "Email codes can't be sent from this server right now — set up an authenticator app."
    assert _user(db, "no-mailer-1113@test.local").mfa_method == ""
    assert c.get("/api/settings").status_code == 403
    assert c.post("/api/auth/mfa/use-email/confirm", json={"code": "123456"}).status_code == 400
    assert c.get("/api/settings").status_code == 403


def _as_old_email_account(db, email):
    """An account switched to email codes the 1.0.112 way: no code ever checked."""
    u = _user(db, email)
    u.mfa_method, u.mfa_proven_at, u.mfa_enrolled_at = "email", None, u.created_at
    db.commit()


def test_the_one_time_repair_marks_email_accounts_unproven(owner, db, required):
    from sqlalchemy import text

    from cbx.db import engine
    from cbx.migrate import run_migrations

    _person(owner, db, "oldmail@test.local")
    _person(owner, db, "oldapp@test.local")
    for e, m in (("oldmail@test.local", "email"), ("oldapp@test.local", "totp")):
        u = _user(db, e)
        u.mfa_method, u.mfa_proven_at = m, None
        if m == "email":
            u.mfa_proven_at = u.created_at  # whatever it said, the repair does not trust it
    db.commit()
    with engine.begin() as c:
        c.execute(text("DELETE FROM schema_migrations WHERE id = '1113_mfa_email_unproven'"))
    before = db.query(AuditLog).filter(AuditLog.action == "mfa_email_unproven").count()
    run_migrations(engine)
    assert _user(db, "oldmail@test.local").mfa_proven_at is None
    assert _user(db, "oldapp@test.local").mfa_proven_at is not None
    a = db.query(AuditLog).filter(AuditLog.action == "mfa_email_unproven").order_by(AuditLog.id.desc()).first()
    assert db.query(AuditLog).filter(AuditLog.action == "mfa_email_unproven").count() == before + 1 and "account(s)" in a.target
    run_migrations(engine)  # once only
    assert db.query(AuditLog).filter(AuditLog.action == "mfa_email_unproven").count() == before + 1


def test_an_unproven_email_account_is_asked_for_a_code_even_on_a_remembered_browser(owner, db, required):
    _person(owner, db, "remembered-1113@test.local")
    _as_old_email_account(db, "remembered-1113@test.local")
    u = _user(db, "remembered-1113@test.local")
    dev, raw = D.remember(db, u, agent="Edge on Windows")
    db.commit()
    b = TestClient(app)
    b.cookies.set(D.DEVICE_COOKIE, raw)
    r = b.post("/api/auth/login", json={"email": "remembered-1113@test.local", "password": STRONG})
    assert r.status_code == 200 and r.json().get("needs_code"), r.text
    assert b.get("/api/auth/me").status_code == 401
    r = b.post("/api/auth/verify", json={"email": "remembered-1113@test.local", "code": _waiting("remembered-1113@test.local")})
    assert r.status_code == 200
    assert _user(db, "remembered-1113@test.local").mfa_proven_at is not None  # the inbox answered: proven now
    assert db.query(AuditLog).filter(AuditLog.action == "mfa_email_proven", AuditLog.actor_id == u.id).count() == 1
    # proven: the remembered browser is trusted again
    b.post("/api/auth/logout")
    r = b.post("/api/auth/login", json={"email": "remembered-1113@test.local", "password": STRONG})
    assert r.status_code == 200 and not r.json().get("needs_code")


def test_fallbacks_never_give_a_full_session_to_an_unproven_person(owner, db, monkeypatch, required):
    from cbx import mail

    monkeypatch.setattr(settings, "code_fallback", "let_in")  # the deliberate "mailer broken: let them in, loudly"
    _person(owner, db, "never-proven@test.local")
    _as_old_email_account(db, "never-proven@test.local")
    _person(owner, db, "was-proven@test.local")
    _as_old_email_account(db, "was-proven@test.local")
    p = _user(db, "was-proven@test.local")
    p.mfa_proven_at = p.created_at
    db.commit()

    def login(email):
        RL.reset_all()
        b = TestClient(app)
        return b, b.post("/api/auth/login", json={"email": email, "password": STRONG})

    # mail switched off
    b, r = login("never-proven@test.local")
    assert r.status_code == 200 and r.json()["must_enrol_mfa"] is True and not r.json().get("needs_code")
    assert b.get("/api/settings").status_code == 403
    b, r = login("was-proven@test.local")
    assert r.status_code == 200 and r.json()["must_enrol_mfa"] is False and b.get("/api/settings").status_code == 200  # kept, and loud
    assert db.query(AuditLog).filter(AuditLog.action == "login_unverified", AuditLog.actor_id == p.id).count() >= 1

    # mail configured but the send fails (wrong password, blocked port)
    def explode(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(settings, "mail_enabled", True)
    monkeypatch.setattr(settings, "smtp_host", "smtp.test.local")
    monkeypatch.setattr(settings, "mail_only_to", "")
    monkeypatch.setattr(mail.smtplib, "SMTP", explode)
    b, r = login("never-proven@test.local")
    assert r.json()["must_enrol_mfa"] is True and b.get("/api/settings").status_code == 403
    # device verification switched off: still no full session for the unproven one
    monkeypatch.setattr(settings, "device_verification", False)
    b, r = login("never-proven@test.local")
    assert r.json()["must_enrol_mfa"] is True and b.get("/api/settings").status_code == 403
    u = _user(db, "never-proven@test.local")
    assert u.mfa_proven_at is None and u.mfa_method == "email"
    # from the pending session: no code can go, so email codes are refused and the app is the way on
    assert b.post("/api/auth/mfa/use-email").status_code == 400
    secret = b.post("/api/auth/mfa/setup", json={}).json()["secret"].replace(" ", "")
    assert b.post("/api/auth/mfa/confirm", json={"code": phone_code(secret)}).status_code == 200
    assert b.get("/api/settings").status_code == 200
    assert db.query(TrustedDevice).filter(TrustedDevice.user_id == u.id).count() >= 1


# ---- the security review (1.0.113): each proof case, now failing to exploit ---------------------------
def test_verify_needs_the_password_first(owner, db, required):
    """/verify used to look the person up by the email typed: email + one current app code (or a recovery code) was a
    full session with no password."""
    c = _person(owner, db, "nopw@test.local")
    secret = c.post("/api/auth/mfa/setup", json={}).json()["secret"].replace(" ", "")
    codes = c.post("/api/auth/mfa/confirm", json={"code": phone_code(secret)}).json()["recovery_codes"]
    tick(60)
    RL.reset_all()
    x = TestClient(app)  # never called /login
    r = x.post("/api/auth/verify", json={"email": "nopw@test.local", "code": phone_code(secret)})
    assert r.status_code == 401 and "Sign in again" in r.json()["detail"]
    assert x.post("/api/auth/verify", json={"email": "nopw@test.local", "code": codes[0]}).status_code == 401
    assert x.get("/api/auth/me").status_code == 401
    assert _user(db, "nopw@test.local").mfa_last_step < int(CLK["phone"] // 30)  # the code was never even tried
    # another person's ticket is no good either: signed in as A, a code typed for B
    _enrol(_person(owner, db, "ticket-a@test.local"))
    RL.reset_all()
    y = TestClient(app)
    assert y.post("/api/auth/login", json={"email": "ticket-a@test.local", "password": STRONG}).json()["needs_code"]
    r = y.post("/api/auth/verify", json={"email": "nopw@test.local", "code": phone_code(secret)})
    assert r.status_code == 401 and y.get("/api/auth/me").status_code == 401


def test_the_ticket_is_single_use_and_counts_wrong_codes(owner, db, required, monkeypatch):
    from cbx.models import SignInTicket

    monkeypatch.setattr(settings, "mfa_max_failed", 99)  # so the ticket's own limit shows
    c = _person(owner, db, "ticket@test.local")
    secret = _enrol(c)
    tick()
    RL.reset_all()
    d = TestClient(app)
    d.post("/api/auth/login", json={"email": "ticket@test.local", "password": STRONG})
    raw = d.cookies.get("cbx_signin")
    assert raw and not db.query(SignInTicket).filter(SignInTicket.token_hash == raw).count()  # only the hash is stored
    bad = "000000" if phone_code(secret) != "000000" else "111111"
    for _ in range(5):
        RL.reset_all()
        assert d.post("/api/auth/verify", json={"email": "ticket@test.local", "code": bad}).status_code == 401
    RL.reset_all()
    r = d.post("/api/auth/verify", json={"email": "ticket@test.local", "code": phone_code(secret)})
    assert r.status_code == 401 and "Too many wrong codes" in r.json()["detail"]
    # a new password sign-in, a right code: in, and the ticket is spent
    RL.reset_all()
    d.post("/api/auth/login", json={"email": "ticket@test.local", "password": STRONG})
    kept = d.cookies.get("cbx_signin")
    assert d.post("/api/auth/verify", json={"email": "ticket@test.local", "code": phone_code(secret)}).status_code == 200
    tick()
    e = TestClient(app)
    e.cookies.set("cbx_signin", kept, path="/api/auth/")
    assert e.post("/api/auth/verify", json={"email": "ticket@test.local", "code": phone_code(secret)}).status_code == 401
    # and a ticket runs out after 10 minutes
    RL.reset_all()
    f = TestClient(app)
    f.post("/api/auth/login", json={"email": "ticket@test.local", "password": STRONG})
    t = db.query(SignInTicket).filter(SignInTicket.used_at.is_(None)).order_by(SignInTicket.id.desc()).first()
    t.expires_at = t.created_at - timedelta(seconds=1)
    db.commit()
    assert f.post("/api/auth/verify", json={"email": "ticket@test.local", "code": phone_code(secret)}).status_code == 401


def test_an_emailed_sign_in_code_also_needs_the_ticket(owner, db, required):
    _person(owner, db, "mailticket@test.local")
    _as_old_email_account(db, "mailticket@test.local")
    RL.reset_all()
    b = TestClient(app)
    assert b.post("/api/auth/login", json={"email": "mailticket@test.local", "password": STRONG}).json()["needs_code"]
    code = _waiting("mailticket@test.local")
    x = TestClient(app)
    assert x.post("/api/auth/verify", json={"email": "mailticket@test.local", "code": code}).status_code == 401
    assert b.post("/api/auth/verify", json={"email": "mailticket@test.local", "code": code}).status_code == 200


def test_a_use_email_code_is_not_a_sign_in_code_and_back(owner, db, required):
    c = _person(owner, db, "purpose@test.local")  # pending, at the set-up screen
    _as_old_email_account(db, "purpose@test.local")
    RL.reset_all()
    x = TestClient(app)  # signs in with the password elsewhere: a sign-in code + ticket
    assert x.post("/api/auth/login", json={"email": "purpose@test.local", "password": STRONG}).json()["needs_code"]
    assert c.post("/api/auth/mfa/use-email").status_code == 200  # the set-up's code (the sign-in one is spent)
    use_code = _waiting("purpose@test.local")
    r = x.post("/api/auth/verify", json={"email": "purpose@test.local", "code": use_code})
    assert r.status_code == 401 and x.get("/api/auth/me").status_code == 401
    assert c.post("/api/auth/mfa/use-email/confirm", json={"code": use_code}).status_code == 200
    # a sign-in code at use-email/confirm is refused (and counts)
    RL.reset_all()
    y = TestClient(app)
    assert y.post("/api/auth/login", json={"email": "purpose@test.local", "password": STRONG}).json()["needs_code"]
    r = c.post("/api/auth/mfa/use-email/confirm", json={"code": _waiting("purpose@test.local")})
    assert r.status_code == 400
    from cbx.models import LoginCode

    assert {p for (p,) in db.query(LoginCode.purpose).filter(LoginCode.user_id == _user(db, "purpose@test.local").id)} == {"signin", "use_email"}


def test_two_requests_with_the_same_code_cannot_both_pass(owner, db, required):
    from cbx.api import auth as AA
    from cbx.db import SessionLocal

    c = _person(owner, db, "race@test.local")
    secret = _enrol(c)
    tick(60)
    code = phone_code(secret)
    s1, s2 = SessionLocal(), SessionLocal()
    try:
        u1 = s1.query(User).filter(User.email == "race@test.local").one()
        u2 = s2.query(User).filter(User.email == "race@test.local").one()  # both read before either writes
        assert AA._app_code(s1, u1, code) == "ok"
        s1.commit()
        assert AA._app_code(s2, u2, code) == "used"
        s2.commit()
    finally:
        s1.close()
        s2.close()
    # and through the door, at the same moment from two browsers
    tick()
    code = phone_code(secret)
    RL.reset_all()
    browsers = [TestClient(app, headers={"user-agent": f"Racer {i}"}) for i in range(2)]
    for b in browsers:
        assert b.post("/api/auth/login", json={"email": "race@test.local", "password": STRONG}).json()["needs_code"]
    out = []
    go = threading.Barrier(2)

    def hit(b):
        go.wait()
        out.append(b.post("/api/auth/verify", json={"email": "race@test.local", "code": code}).status_code)

    ts = [threading.Thread(target=hit, args=(b,)) for b in browsers]
    [t.start() for t in ts]
    [t.join(60) for t in ts]
    assert sorted(out) == [200, 401], out


def test_the_wide_search_is_only_open_while_setting_up(owner, db, required):
    CLK["fast"] = 240
    c = _person(owner, db, "window@test.local")
    secret = c.post("/api/auth/mfa/setup", json={}).json()["secret"].replace(" ", "")
    u = _user(db, "window@test.local")
    u.mfa_setup_at = u.mfa_setup_at - timedelta(minutes=11)  # the set-up began too long ago
    db.commit()
    r = c.post("/api/auth/mfa/confirm", json={"code": phone_code(secret)})
    assert r.status_code == 400 and _user(db, "window@test.local").mfa_failed == 1  # ±2 now, and it counted
    secret = c.post("/api/auth/mfa/setup", json={}).json()["secret"].replace(" ", "")  # start again: open for 10 minutes
    assert c.post("/api/auth/mfa/confirm", json={"code": phone_code(secret)}).status_code == 200
    assert _user(db, "window@test.local").mfa_setup_at is None
    # an enrolled person's /mfa/confirm (no set-up running) is ±2 around their difference, not ±10
    tick()
    assert c.post("/api/auth/mfa/confirm", json={"code": phone_code(secret, -5 * 30)}).status_code == 400


def test_wrong_codes_at_set_up_recovery_and_remove_count_and_are_rate_limited(owner, db, required, monkeypatch):
    c = _person(owner, db, "counted@test.local")
    secret = c.post("/api/auth/mfa/setup", json={}).json()["secret"].replace(" ", "")
    bad = lambda: "000000" if phone_code(secret) != "000000" else "111111"  # noqa: E731
    assert c.post("/api/auth/mfa/confirm", json={"code": bad()}).status_code == 400
    assert c.post("/api/auth/mfa/confirm", json={"code": phone_code(secret)}).status_code == 200
    assert _user(db, "counted@test.local").mfa_failed == 0
    assert c.post("/api/auth/mfa/recovery", json={"code": bad()}).status_code == 400
    monkeypatch.setattr(settings, "mfa_required", "admins")
    assert c.post("/api/auth/mfa/remove", json={"code": bad()}).status_code == 400
    assert _user(db, "counted@test.local").mfa_failed == 2
    assert c.post("/api/auth/mfa/recovery", json={"code": bad()}).status_code == 400
    u = _user(db, "counted@test.local")
    assert u.locked is True and u.mfa_failed == 3
    tick()
    assert c.post("/api/auth/mfa/recovery", json={"code": phone_code(secret)}).status_code == 403  # locked
    # the per-connection limit on these doors
    monkeypatch.setattr(settings, "mfa_required", "everyone")
    RL.reset_all()
    d = _person(owner, db, "limited@test.local")
    d.post("/api/auth/mfa/setup", json={})
    monkeypatch.setattr(settings, "mfa_max_failed", 99)
    codes = [d.post("/api/auth/mfa/confirm", json={"code": "000000"}).status_code for _ in range(settings.login_rate_limit + 1)]
    assert codes[-1] == 429


def test_an_unproven_email_method_is_never_a_second_step_even_where_none_is_required(owner, db, required, monkeypatch):
    monkeypatch.setattr(settings, "mfa_required", "none")
    monkeypatch.setattr(settings, "device_verification", False)  # just to give the account its own password
    _person_none = _make(owner, "optional@test.local", "Optional Person", "user")
    RL.reset_all()
    c = TestClient(app)
    c.post("/api/auth/login", json={"email": "optional@test.local", "password": _person_none})
    c.post("/api/auth/change-password", json={"new_password": STRONG, "repeat": STRONG})
    monkeypatch.setattr(settings, "device_verification", True)
    _as_old_email_account(db, "optional@test.local")
    u = _user(db, "optional@test.local")
    _, raw = D.remember(db, u, agent="Edge on Windows")
    db.commit()
    RL.reset_all()
    b = TestClient(app)
    b.cookies.set(D.DEVICE_COOKIE, raw)
    r = b.post("/api/auth/login", json={"email": "optional@test.local", "password": STRONG})
    assert r.json().get("needs_code"), r.text  # a remembered browser does not skip it
    monkeypatch.setattr(settings, "code_fallback", "let_in")
    RL.reset_all()
    e = TestClient(app)
    r = e.post("/api/auth/login", json={"email": "optional@test.local", "password": STRONG})
    assert r.json()["must_enrol_mfa"] is True and e.get("/api/settings").status_code == 403
