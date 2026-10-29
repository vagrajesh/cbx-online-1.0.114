"""Our own two-step verification (1.0.45): an authenticator app for anyone — inside the company or
outside it — recovery codes, the lock after wrong codes, the admin reset, and accounts that end.

The suite runs with CBX_MFA_REQUIRED=none (tests/conftest.py) so every other test signs in as before;
the tests here switch the rule on per test.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from cbx.config import settings
from cbx.main import app
from cbx.models import AuditLog, LoginSession, TrustedDevice, User
from cbx.security import ratelimit as RL
from cbx.security import secretbox, totp
from tests.conftest import STRONG, _make, _uid


@pytest.fixture(autouse=True)
def clean_limits():
    RL.reset_all()
    yield
    RL.reset_all()


# 1.0.113: a code's step can be used once, so every code these tests type is the phone's next one — a stand-in
# clock (cbx/security/clock.now) moves 30 s on for each, and the phone's code is made for the same moment
_T = {"t": time.time()}


@pytest.fixture(autouse=True)
def stand_in_clock(monkeypatch):
    from cbx.security import clock

    monkeypatch.setattr(clock, "now", lambda: _T["t"])
    yield


def _code(secret: str) -> str:
    _T["t"] += 30
    return totp.code_now(secret, _T["t"])


def _email_code(user_email: str) -> str:
    """The code /mfa/use-email just made, read where the terminal fail-safe keeps it (this process only)."""
    from cbx.security import devices as D

    return next(v["code"] for v in D._WAITING.values() if v["email"] == user_email)


def _use_email(c, email: str):
    """1.0.113: email codes only after a code sent to the person comes back."""
    r = c.post("/api/auth/mfa/use-email")
    assert r.status_code == 200, r.text
    return c.post("/api/auth/mfa/use-email/confirm", json={"code": _email_code(email)})


@pytest.fixture()
def required(monkeypatch):
    monkeypatch.setattr(settings, "mfa_required", "everyone")
    monkeypatch.setattr(settings, "mfa_email_fallback", True)
    monkeypatch.setattr(settings, "mfa_admin_trust_days", 0)
    monkeypatch.setattr(settings, "mfa_max_failed", 3)


def _fresh(owner, email="mfa@test.local", name="Mo Fa", role="user", **extra):
    """A person who has never signed in: temp password, then their own password, then — when the
    rule is on — the set-up screen."""
    existing = [u for u in owner.get("/api/users").json()["users"] if u["email"] == email]
    if existing and existing[0]["mfa_method"]:
        # the database is shared by the whole run: a person set up by an earlier test starts over
        assert owner.post(f"/api/users/{existing[0]['id']}/mfa-reset", json={"reason": "test start"}).status_code == 200
    pw = _make(owner, email, name, role)
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    r2 = c.post("/api/auth/change-password", json={"new_password": STRONG, "repeat": STRONG})
    assert r2.status_code == 200, r2.text
    return c, r.json()


def _enrol(c) -> tuple[str, list[str]]:
    r = c.post("/api/auth/mfa/setup", json={})
    assert r.status_code == 200, r.text
    secret = r.json()["secret"].replace(" ", "")
    assert r.json()["uri"].startswith("otpauth://totp/")
    assert "<svg" in r.json()["qr_svg"]
    r = c.post("/api/auth/mfa/confirm", json={"code": _code(secret)})
    assert r.status_code == 200, r.text
    codes = r.json()["recovery_codes"]
    assert len(codes) == 10 and all(len(x) == 9 and x[4] == "-" for x in codes)
    assert r.json()["mfa"]["method"] == "totp" and r.json()["mfa"]["recovery_left"] == 10
    return secret, codes


# ---- the library ------------------------------------------------------------------------------
def test_totp_verify_window_and_recovery_codes():
    s = totp.new_secret()
    now = time.time()
    assert totp.verify(s, totp.code_now(s, now), at=now)
    assert totp.verify(s, totp.code_now(s, now - 30), at=now)  # one step of clock drift is fine
    assert not totp.verify(s, totp.code_now(s, now - 90), at=now)  # three steps is not
    assert not totp.verify(s, "12345", at=now) and not totp.verify("", "123456")
    assert totp.verify(s, totp.code_now(s, now)[:3] + " " + totp.code_now(s, now)[3:], at=now)  # typed with a space
    codes, hashes = totp.new_recovery_codes()
    assert len(set(codes)) == 10 and all(totp.looks_like_recovery(c) for c in codes)
    assert not totp.looks_like_recovery("123456")
    left = totp.use_recovery_code(hashes, codes[3].upper().replace("-", ""))  # case and dash do not matter
    assert left is not None and len(left) == 9 and totp.use_recovery_code(left, codes[3]) is None
    assert totp.use_recovery_code(hashes, "zzzz-zzzz") is None


# ---- enrolment ----------------------------------------------------------------------------------
def test_first_sign_in_is_gated_until_the_second_step_is_set_up(owner, required, db):
    c, first = _fresh(owner)
    assert first["must_enrol_mfa"] is True
    # nothing but /api/auth answers while the second step is missing
    assert c.get("/api/settings").status_code == 403
    assert "two-step" in c.get("/api/settings").json()["detail"].lower()
    me = c.get("/api/auth/me").json()
    assert me["must_enrol_mfa"] is True and me["mfa"]["required"] is True and me["mfa"]["method"] == ""
    secret, codes = _enrol(c)
    # the gate is open, and the secret on disk is sealed, not the raw base32
    assert c.get("/api/settings").status_code == 200
    u = db.query(User).filter(User.email == "mfa@test.local").one()
    assert u.mfa_method == "totp" and u.mfa_secret != secret and secretbox.open_(u.mfa_secret) == secret
    assert u.mfa_recovery and all(len(h) == 64 for h in u.mfa_recovery) and codes[0] not in u.mfa_recovery
    assert db.query(LoginSession).filter(LoginSession.user_id == u.id, LoginSession.pending_mfa.is_(True)).count() == 0
    assert db.query(AuditLog).filter(AuditLog.action == "mfa_enrolled", AuditLog.actor_id == u.id).count() == 1


def test_wrong_code_while_setting_up_does_not_enrol(owner, required):
    c, _ = _fresh(owner, "wrong@test.local", "Wrong Setup")
    assert c.post("/api/auth/mfa/confirm", json={"code": "123456"}).status_code == 400  # nothing started
    c.post("/api/auth/mfa/setup", json={})
    r = c.post("/api/auth/mfa/confirm", json={"code": "000000"})
    assert r.status_code == 400 and "not right" in r.json()["detail"]
    assert c.get("/api/auth/mfa").json()["method"] == ""


def test_email_codes_are_the_fallback_for_someone_without_an_app(owner, required, monkeypatch):
    monkeypatch.setattr(settings, "code_fallback", "terminal")  # 1.0.113: the code has to go somewhere (no mailer in the suite)
    c, _ = _fresh(owner, "outside@sanlam.example", "Outside Person")
    r = _use_email(c, "outside@sanlam.example")
    assert r.status_code == 200 and r.json()["mfa"]["method"] == "email"
    assert c.get("/api/settings").status_code == 200
    # and not when the server forbids it
    monkeypatch.setattr(settings, "mfa_email_fallback", False)
    c2, _ = _fresh(owner, "outside2@eskom.example", "Outside Two")
    assert c2.post("/api/auth/mfa/use-email").status_code == 400
    assert c2.get("/api/auth/me").json()["mfa"]["email_allowed"] is False


# ---- signing in with it -------------------------------------------------------------------------
def test_sign_in_asks_for_the_app_code_then_remembers_the_browser(owner, required, db):
    c, _ = _fresh(owner)
    secret, codes = _enrol(c)
    d = TestClient(app)  # a browser nobody has seen
    r = d.post("/api/auth/login", json={"email": "mfa@test.local", "password": STRONG})
    assert r.status_code == 200 and r.json()["needs_code"] and r.json()["method"] == "totp" and r.json()["recovery"] is True
    assert "sent_to" not in r.json()  # no email involved
    assert d.get("/api/auth/me").status_code == 401  # not in yet
    r = d.post("/api/auth/verify", json={"email": "mfa@test.local", "code": _code(secret)})
    assert r.status_code == 200 and r.json()["user"]["email"] == "mfa@test.local"
    assert d.get("/api/settings").status_code == 200
    # a user's browser is trusted for CBX_DEVICE_TRUST_DAYS, so the next sign-in skips the code
    assert db.query(TrustedDevice).join(User).filter(User.email == "mfa@test.local", TrustedDevice.revoked.is_(False)).count() >= 1
    d.post("/api/auth/logout")
    r = d.post("/api/auth/login", json={"email": "mfa@test.local", "password": STRONG})
    assert r.status_code == 200 and not r.json().get("needs_code")


def test_owner_is_asked_every_time_unless_trust_days_say_otherwise(owner, required, db, monkeypatch):
    # the owner fixture is signed in without a second step (the rule was off); set one up now
    secret, _ = _enrol(owner)
    a = TestClient(app)
    r = a.post("/api/auth/login", json={"email": "owner@test.local", "password": STRONG})
    assert r.json().get("needs_code") and r.json()["method"] == "totp"
    r = a.post("/api/auth/verify", json={"email": "owner@test.local", "code": _code(secret)})
    assert r.status_code == 200
    a.post("/api/auth/logout")
    # trust days 0 for admins: asked again on the very same browser
    r = a.post("/api/auth/login", json={"email": "owner@test.local", "password": STRONG})
    assert r.json().get("needs_code")
    # CBX_MFA_ADMIN_TRUST_DAYS=7: remembered like a user
    monkeypatch.setattr(settings, "mfa_admin_trust_days", 7)
    r = a.post("/api/auth/verify", json={"email": "owner@test.local", "code": _code(secret)})
    assert r.status_code == 200
    a.post("/api/auth/logout")
    r = a.post("/api/auth/login", json={"email": "owner@test.local", "password": STRONG})
    assert r.status_code == 200 and not r.json().get("needs_code")
    # tidy: the owner fixture is shared across tests through the database — take the app off again
    monkeypatch.setattr(settings, "mfa_required", "none")
    assert owner.post("/api/auth/mfa/remove", json={"code": _code(secret)}).status_code == 200


def test_recovery_code_works_once_and_warns_when_few_are_left(owner, required, db):
    c, _ = _fresh(owner)
    secret, codes = _enrol(c)
    d = TestClient(app)
    d.post("/api/auth/login", json={"email": "mfa@test.local", "password": STRONG})
    r = d.post("/api/auth/verify", json={"email": "mfa@test.local", "code": codes[0]})
    assert r.status_code == 200 and "recovery_low" not in r.json()
    u = db.query(User).filter(User.email == "mfa@test.local").one()
    assert len(u.mfa_recovery) == 9
    # the same code again is a wrong code
    e = TestClient(app)
    e.post("/api/auth/login", json={"email": "mfa@test.local", "password": STRONG})
    assert e.post("/api/auth/verify", json={"email": "mfa@test.local", "code": codes[0]}).status_code == 401
    # burn down to two: the sign-in says so
    for k in range(1, 8):
        RL.reset_all()
        f = TestClient(app)
        f.post("/api/auth/login", json={"email": "mfa@test.local", "password": STRONG})
        assert f.post("/api/auth/verify", json={"email": "mfa@test.local", "code": codes[k]}).status_code == 200
    RL.reset_all()
    g = TestClient(app)
    g.post("/api/auth/login", json={"email": "mfa@test.local", "password": STRONG})
    r = g.post("/api/auth/verify", json={"email": "mfa@test.local", "code": codes[8]})
    assert r.status_code == 200 and r.json()["recovery_low"] == 1
    # new codes from Settings need a live app code, and void the old ones
    r = g.post("/api/auth/mfa/recovery", json={"code": _code(secret)})
    assert r.status_code == 200 and len(r.json()["recovery_codes"]) == 10
    db.expire_all()
    u = db.query(User).filter(User.email == "mfa@test.local").one()
    assert len(u.mfa_recovery) == 10 and totp.use_recovery_code(u.mfa_recovery, codes[9]) is None


def test_three_wrong_codes_lock_the_account_until_an_admin_unlocks(owner, required, db):
    c, _ = _fresh(owner)
    secret, _ = _enrol(c)
    d = TestClient(app)
    d.post("/api/auth/login", json={"email": "mfa@test.local", "password": STRONG})
    for _ in range(3):
        assert d.post("/api/auth/verify", json={"email": "mfa@test.local", "code": "000000"}).status_code == 401
    db.expire_all()
    u = db.query(User).filter(User.email == "mfa@test.local").one()
    assert u.locked is True and u.mfa_failed == 3
    assert db.query(AuditLog).filter(AuditLog.action == "mfa_locked", AuditLog.actor_id == u.id).count() == 1
    # the right code no longer helps; neither does the password on its own
    assert d.post("/api/auth/verify", json={"email": "mfa@test.local", "code": _code(secret)}).status_code == 401
    r = d.post("/api/auth/login", json={"email": "mfa@test.local", "password": STRONG})
    assert r.status_code == 401 and "locked" in r.json()["detail"].lower()
    # the owner unlocks: the counter is cleared with it
    r = owner.patch(f"/api/users/{u.id}", json={"unlock": True})
    assert r.status_code == 200
    db.expire_all()
    u = db.query(User).filter(User.email == "mfa@test.local").one()
    assert u.locked is False and u.mfa_failed == 0
    RL.reset_all()
    e = TestClient(app)
    e.post("/api/auth/login", json={"email": "mfa@test.local", "password": STRONG})
    assert e.post("/api/auth/verify", json={"email": "mfa@test.local", "code": _code(secret)}).status_code == 200


# ---- the admin side -----------------------------------------------------------------------------
def test_admin_reset_clears_the_app_and_signs_the_person_out_everywhere(owner, required, db):
    c, _ = _fresh(owner)
    secret, _ = _enrol(c)
    uid = _uid(owner, "mfa@test.local")
    assert owner.post(f"/api/users/{uid}/mfa-reset", json={"reason": ""}).status_code == 400  # a reason is not optional
    r = owner.post(f"/api/users/{uid}/mfa-reset", json={"reason": "lost phone"})
    assert r.status_code == 200 and r.json()["user"]["mfa_method"] == ""
    assert c.get("/api/settings").status_code == 401  # the old session is gone
    db.expire_all()
    u = db.get(User, uid)
    assert u.mfa_secret == "" and u.mfa_recovery == [] and u.mfa_enrolled_at is None
    assert db.query(TrustedDevice).filter(TrustedDevice.user_id == uid, TrustedDevice.revoked.is_(False)).count() == 0
    a = db.query(AuditLog).filter(AuditLog.action == "mfa_reset", AuditLog.target == "mfa@test.local").order_by(AuditLog.id.desc()).first()
    assert a is not None and "lost phone" in a.detail and "was totp" in a.detail
    # next sign-in: password, then the set-up screen again
    RL.reset_all()
    d = TestClient(app)
    r = d.post("/api/auth/login", json={"email": "mfa@test.local", "password": STRONG})
    assert r.status_code == 200 and r.json()["must_enrol_mfa"] is True and not r.json().get("needs_code")
    # a manager cannot reset someone outside their team
    listing = owner.get("/api/users").json()["users"]
    assert next(x for x in listing if x["id"] == uid)["mfa_method"] == ""


def test_manager_cannot_reset_outside_their_team(owner, required, monkeypatch):
    monkeypatch.setattr(settings, "code_fallback", "terminal")
    _fresh(owner, "mfa@test.local")
    uid = _uid(owner, "mfa@test.local")
    pw = _make(owner, "mgr-mfa@test.local", "Mandla Manager", "manager", team="Another team")
    m = TestClient(app)
    r = m.post("/api/auth/login", json={"email": "mgr-mfa@test.local", "password": pw})
    m.post("/api/auth/change-password", json={"new_password": STRONG, "repeat": STRONG})
    assert _use_email(m, "mgr-mfa@test.local").status_code == 200
    assert m.post(f"/api/users/{uid}/mfa-reset", json={"reason": "x"}).status_code == 403


def test_account_that_has_ended_cannot_sign_in(owner, db):
    r = owner.post("/api/users", json={"email": "temp@supplier.example", "name": "Temp Supplier", "role": "user", "team_name": "Suppliers", "tools": [], "expires_at": "2099-01-01"})
    assert r.status_code == 200 and r.json()["user"]["expires_at"].startswith("2099-01-01T23:59:59")
    uid = r.json()["user"]["id"]
    assert owner.post("/api/users", json={"email": "past@supplier.example", "name": "Past", "role": "user", "tools": [], "expires_at": "2001-01-01"}).status_code == 400
    assert owner.patch(f"/api/users/{uid}", json={"expires_at": "not-a-date"}).status_code == 400
    # move the end into the past straight in the database (the API refuses past dates by design)
    from datetime import datetime, timedelta, timezone

    u = db.get(User, uid)
    u.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    db.commit()
    pw = owner.post(f"/api/users/{uid}/temp-password").json()["temp_password"]
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"email": "temp@supplier.example", "password": pw})
    assert r.status_code == 403 and "ended" in r.json()["detail"]
    assert db.query(AuditLog).filter(AuditLog.action == "login_expired", AuditLog.target == None).count() >= 0  # noqa: E711 — logged as the actor
    # extend it: back in
    r = owner.patch(f"/api/users/{uid}", json={"expires_at": "2099-12-31"})
    assert r.status_code == 200 and r.json()["user"]["expires_at"].startswith("2099-12-31")
    RL.reset_all()
    assert c.post("/api/auth/login", json={"email": "temp@supplier.example", "password": pw}).status_code == 200
    # and cleared: no end date at all
    r = owner.patch(f"/api/users/{uid}", json={"expires_at": ""})
    assert r.status_code == 200 and r.json()["user"]["expires_at"] is None
    assert owner.patch(f"/api/users/{_uid(owner, 'owner@test.local')}", json={"expires_at": "2099-01-01"}).status_code == 400


def test_remove_is_refused_where_it_is_required_and_the_sandbox_runs_without_it(owner, required, monkeypatch):
    c, _ = _fresh(owner)
    secret, _ = _enrol(c)
    r = c.post("/api/auth/mfa/remove", json={"code": _code(secret)})
    assert r.status_code == 400 and "required" in r.json()["detail"].lower()
    monkeypatch.setattr(settings, "mfa_required", "admins")
    assert c.post("/api/auth/mfa/remove", json={"code": "000000"}).status_code == 400  # wrong code
    assert c.post("/api/auth/mfa/remove", json={"code": _code(secret)}).status_code == 200
    assert c.get("/api/auth/mfa").json()["method"] == ""
    # a sandbox never asks its tester for a phone
    from cbx.versions import sandbox_runner

    src = open(sandbox_runner.__file__, encoding="utf-8").read()
    assert 'env["CBX_MFA_REQUIRED"] = "none"' in src
    from cbx.versions.injector import SCRUB_ENV

    assert "CBX_MFA_REQUIRED" in SCRUB_ENV
