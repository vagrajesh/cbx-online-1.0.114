"""Signing in from a new browser, and the per-IP limits on the door.

The first test is the one that matters most: if this server cannot send email, the code cannot
arrive, and a second factor nobody can complete locks out all 1 900 people. That fallback is
deliberate and it is tested before anything else here.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from cbx import mail
from cbx.config import settings
from cbx.main import app
from cbx.models import LoginCode, TrustedDevice, User
from cbx.security import devices as D
from cbx.security import ratelimit as RL
from tests.conftest import STRONG, _make


@pytest.fixture(autouse=True)
def clean_limits():
    RL.reset_all()
    yield
    RL.reset_all()


@pytest.fixture()
def mailbox(monkeypatch):
    out = []

    class FakeSMTP:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, context=None):
            pass

        def login(self, u, p):
            pass

        def send_message(self, msg):
            out.append({"to": msg["To"], "subject": msg["Subject"], "body": msg.get_content()})

    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(settings, "mail_enabled", True)
    monkeypatch.setattr(settings, "smtp_host", "smtp.test.local")
    monkeypatch.setattr(settings, "mail_only_to", "")
    monkeypatch.setattr(settings, "device_verification", True)
    mail._sent["day"], mail._sent["n"] = None, 0
    # These tests are about a server whose mailbox works. Codes only arm once one has actually been
    # delivered (cbx/state.py), so say so — otherwise every test here silently tests the fallback.
    from cbx.db import SessionLocal
    from cbx import state

    with SessionLocal() as s:
        state.set(s, state.MAIL_PROVEN, "2026-09-18T00:00:00+00:00")
        s.commit()
    return out


def _code_from(mailbox) -> str:
    import re

    return re.search(r"\b(\d{6})\b", mailbox[-1]["subject"] + " " + mailbox[-1]["body"]).group(1)


def test_when_the_server_cannot_send_mail_nobody_is_locked_out(owner, db, monkeypatch):
    """A second factor that cannot be delivered is not security, it is a locked building. Sign-in
    proceeds and the gap is written down instead of being assumed away."""
    monkeypatch.setattr(settings, "code_fallback", "let_in")  # the pre-1.0.47 fail-safe; the terminal one is tested below
    monkeypatch.setattr(settings, "device_verification", True)
    monkeypatch.setattr(settings, "mail_enabled", False)
    pw = _make(owner, "nomail@test.local", "No Mail", "user")
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"email": "nomail@test.local", "password": pw})
    assert r.status_code == 200 and not r.json().get("needs_code")
    from cbx.models import AuditLog

    assert db.query(AuditLog).filter(AuditLog.action == "login_unverified").count() >= 1


def test_when_the_smtp_password_is_wrong_nobody_is_locked_out_either(owner, db, monkeypatch):
    """The dangerous state is not "no email configured" — it is "configured and broken", which is
    exactly what the first restart after pasting in an app password looks like if the password is
    wrong or the port is blocked. `mail.configured()` only checks that the settings have values, so
    the challenge arms itself and then cannot deliver. Nobody has a confirmed device yet, so every
    person is refused at once, the owner included, and the only way back in is editing .env on the
    server. Sign-in proceeds and the failure is written down with the SMTP error in it."""
    monkeypatch.setattr(settings, "code_fallback", "let_in")  # the pre-1.0.47 fail-safe; the terminal one is tested below
    import smtplib

    def explode(*a, **k):
        raise smtplib.SMTPAuthenticationError(535, b"5.7.3 Authentication unsuccessful")

    monkeypatch.setattr(settings, "device_verification", True)
    monkeypatch.setattr(settings, "mail_enabled", True)
    monkeypatch.setattr(settings, "smtp_host", "smtp.test.local")
    monkeypatch.setattr(mail.smtplib, "SMTP", explode)
    # The mailbox worked once — so codes are armed — and then the password was rotated behind us.
    from cbx import state

    state.set(db, state.MAIL_PROVEN, "2026-09-18T00:00:00+00:00")
    db.commit()
    pw = _make(owner, "badsmtp@test.local", "Bad Smtp", "user")
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"email": "badsmtp@test.local", "password": pw})
    assert r.status_code == 200, r.text
    assert not r.json().get("needs_code")
    assert c.get("/api/auth/me").status_code == 200          # they really are signed in
    from cbx.models import AuditLog

    row = (db.query(AuditLog).filter(AuditLog.action == "login_unverified")
           .order_by(AuditLog.id.desc()).first())
    assert row is not None and "could not be emailed" in (row.detail or "")


def test_a_new_browser_is_asked_for_a_code_and_the_password_alone_is_not_enough(owner, mailbox):
    pw = _make(owner, "newbrowser@test.local", "New Browser", "user")
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"email": "newbrowser@test.local", "password": pw})
    assert r.status_code == 200
    body = r.json()
    assert body["needs_code"] is True
    assert "@" in body["sent_to"] and "newbrowser" not in body["sent_to"]  # masked
    # the password alone did NOT open a session
    assert c.get("/api/auth/me").status_code == 401
    assert mailbox and "sign-in code" in mailbox[-1]["subject"]

    r2 = c.post("/api/auth/verify", json={"email": "newbrowser@test.local", "code": _code_from(mailbox)})
    assert r2.status_code == 200, r2.text
    assert c.get("/api/auth/me").status_code == 200


def test_the_same_browser_is_not_asked_again(owner, mailbox, db):
    pw = _make(owner, "remembered@test.local", "Remembered", "user")
    c = TestClient(app)
    c.post("/api/auth/login", json={"email": "remembered@test.local", "password": pw})
    c.post("/api/auth/verify", json={"email": "remembered@test.local", "code": _code_from(mailbox)})
    c.post("/api/auth/change-password", json={"new_password": STRONG, "repeat": STRONG})
    sent_before = len(mailbox)
    c.post("/api/auth/logout")
    r = c.post("/api/auth/login", json={"email": "remembered@test.local", "password": STRONG})
    assert r.status_code == 200 and not r.json().get("needs_code")
    assert len(mailbox) == sent_before  # no second code


def test_a_wrong_code_is_refused_and_runs_out_of_tries(owner, mailbox, db):
    pw = _make(owner, "wrongcode@test.local", "Wrong Code", "user")
    c = TestClient(app)
    c.post("/api/auth/login", json={"email": "wrongcode@test.local", "password": pw})
    for i in range(D.MAX_CODE_ATTEMPTS):
        r = c.post("/api/auth/verify", json={"email": "wrongcode@test.local", "code": "000000"})
        assert r.status_code == 401
    assert c.get("/api/auth/me").status_code == 401
    # the real code is now spent too — a burnt code cannot be brute-forced at leisure
    r = c.post("/api/auth/verify", json={"email": "wrongcode@test.local", "code": _code_from(mailbox)})
    assert r.status_code == 401


def test_a_code_is_single_use(owner, mailbox):
    pw = _make(owner, "onceonly@test.local", "Once Only", "user")
    c = TestClient(app)
    c.post("/api/auth/login", json={"email": "onceonly@test.local", "password": pw})
    code = _code_from(mailbox)
    assert c.post("/api/auth/verify", json={"email": "onceonly@test.local", "code": code}).status_code == 200
    c2 = TestClient(app)
    assert c2.post("/api/auth/verify", json={"email": "onceonly@test.local", "code": code}).status_code == 401


def test_codes_are_not_stored_in_a_form_anyone_can_read(owner, mailbox, db):
    pw = _make(owner, "hashed@test.local", "Hashed", "user")
    c = TestClient(app)
    c.post("/api/auth/login", json={"email": "hashed@test.local", "password": pw})
    code = _code_from(mailbox)
    row = db.query(LoginCode).order_by(LoginCode.id.desc()).first()
    assert code not in row.code_hash and len(row.code_hash) == 64


def test_changing_your_password_stops_trusting_your_other_devices(owner, mailbox, db):
    """If someone changes their password because they think somebody got in, a browser that never
    has to ask for a code again is exactly what they are trying to shut. The one they are sitting
    at keeps its trust, so they are not challenged in the middle of changing it."""
    pw = _make(owner, "rotate@test.local", "Rotate", "user")
    first = TestClient(app)   # laptop
    first.post("/api/auth/login", json={"email": "rotate@test.local", "password": pw})
    first.post("/api/auth/verify", json={"email": "rotate@test.local", "code": _code_from(mailbox)})
    first.post("/api/auth/change-password", json={"new_password": STRONG, "repeat": STRONG})

    second = TestClient(app)  # phone, confirmed separately
    second.post("/api/auth/login", json={"email": "rotate@test.local", "password": STRONG})
    second.post("/api/auth/verify", json={"email": "rotate@test.local", "code": _code_from(mailbox)})
    u = db.query(User).filter(User.email == "rotate@test.local").one()
    db.expire_all()
    assert db.query(TrustedDevice).filter(TrustedDevice.user_id == u.id, TrustedDevice.revoked.is_(False)).count() == 2

    # change it again from the laptop: the phone loses its trust, the laptop keeps its own
    newpw = "another-good-passphrase-77"
    r = first.post("/api/auth/change-password", json={"current_password": STRONG, "new_password": newpw, "repeat": newpw})
    assert r.status_code == 200, r.text
    db.expire_all()
    live = db.query(TrustedDevice).filter(TrustedDevice.user_id == u.id, TrustedDevice.revoked.is_(False)).all()
    assert len(live) == 1, "every browser but the one in front of you should need a code again"

    sent_before = len(mailbox)
    second.post("/api/auth/logout")
    r = second.post("/api/auth/login", json={"email": "rotate@test.local", "password": newpw})
    assert r.json().get("needs_code") is True  # the phone is asked again
    assert len(mailbox) > sent_before


# ---- per-IP limits --------------------------------------------------------------------------------
def test_walking_the_address_list_hits_a_wall(owner):
    """The 17 Sep pattern: addresses that have no account here, tried again and again. Account
    lockout does nothing about it — this does."""
    c = TestClient(app)
    codes = []
    for i in range(settings.login_rate_limit + 3):
        r = c.post("/api/auth/login", json={"email": f"guess{i}@cbreexcellerate.com", "password": "x"})
        codes.append(r.status_code)
    assert 429 in codes
    last = c.post("/api/auth/login", json={"email": "guess99@cbreexcellerate.com", "password": "x"})
    assert last.status_code == 429
    assert "Try again in" in last.json()["detail"]
    assert last.headers.get("Retry-After")


def test_the_limit_is_forgotten_once_someone_signs_in(owner, monkeypatch):
    """A person who fumbles their password twice and then gets it right must not carry those
    attempts around for the rest of the window."""
    monkeypatch.setattr(settings, "device_verification", False)
    pw = _make(owner, "fumble@test.local", "Fumble", "user")
    c = TestClient(app)
    for _ in range(3):
        c.post("/api/auth/login", json={"email": "fumble@test.local", "password": "nope"})
    assert RL.peek("testclient", window=settings.login_rate_window_seconds) >= 3
    assert c.post("/api/auth/login", json={"email": "fumble@test.local", "password": pw}).status_code == 200
    assert RL.peek("testclient", window=settings.login_rate_window_seconds) == 0


def test_verify_is_rate_limited_too(owner, mailbox):
    pw = _make(owner, "codespam@test.local", "Code Spam", "user")
    c = TestClient(app)
    c.post("/api/auth/login", json={"email": "codespam@test.local", "password": pw})
    codes = [c.post("/api/auth/verify", json={"email": "codespam@test.local", "code": "111111"}).status_code
             for _ in range(settings.login_rate_limit + 3)]
    assert 429 in codes


def test_verify_never_confirms_whether_an_address_exists(owner):
    c = TestClient(app)
    r = c.post("/api/auth/verify", json={"email": "nobody-here@nowhere.test", "code": "123456"})
    # 1.0.113: without a password-passed ticket /verify answers the same for every address, real or not
    real = c.post("/api/auth/verify", json={"email": "owner@test.local", "code": "123456"})
    assert r.status_code == real.status_code == 401 and r.json()["detail"] == real.json()["detail"]


def test_a_device_label_is_readable_and_not_a_fingerprint():
    assert D.describe("Mozilla/5.0 (Windows NT 10.0) AppleWebKit Chrome/140 Safari/537") == "Chrome on Windows"
    assert D.describe("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0) Safari/604") == "Safari on iPhone"
    assert D.describe("") == "A browser"


def test_sign_in_codes_stay_off_until_a_message_has_actually_arrived(owner, db, monkeypatch):
    """The switch is proof, not intent. Filling in .env does not arm anything — the app password
    could be wrong and every person would be refused at once, nobody having a confirmed device yet.
    One test message that actually leaves the machine is what turns it on."""
    monkeypatch.setattr(settings, "code_fallback", "let_in")  # the pre-1.0.47 fail-safe; the terminal one is tested below
    from cbx import state
    from cbx.models import AppState

    db.query(AppState).delete()
    db.commit()
    monkeypatch.setattr(settings, "device_verification", True)
    monkeypatch.setattr(settings, "mail_enabled", True)
    monkeypatch.setattr(settings, "smtp_host", "smtp.test.local")
    monkeypatch.setattr(settings, "mail_only_to", "")
    assert state.mail_proven(db) is None

    # .env looks configured, but nothing has ever been delivered → no code, sign-in as before
    pw = _make(owner, "unproven@test.local", "Un Proven", "user")
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"email": "unproven@test.local", "password": pw})
    assert r.status_code == 200 and not r.json().get("needs_code")

    # the owner sends one test message that really goes out
    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, context=None):
            pass

        def login(self, u, p):
            pass

        def send_message(self, msg):
            sent.append(msg["To"])

    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)
    mail._sent["day"], mail._sent["n"] = None, 0
    t = owner.post("/api/mailer/test", json={})
    assert t.status_code == 200 and t.json()["ok"], t.text
    assert sent and owner.get("/api/mailer/settings").json()["sign_in_codes"] == "on"
    db.expire_all()
    assert state.mail_proven(db) is not None

    # ...and from that moment a browser nobody has confirmed is asked for a code
    c2 = TestClient(app)
    r = c2.post("/api/auth/login", json={"email": "unproven@test.local", "password": pw})
    assert r.status_code == 200 and r.json().get("needs_code") is True
    assert owner.get("/api/mailer/settings").json()["sign_in_codes"] == "on"


# ---- the terminal fail-safe (1.0.47) -------------------------------------------------------------


def _terminal_login(owner, email, monkeypatch, capsys):
    monkeypatch.setattr(settings, "device_verification", True)
    monkeypatch.setattr(settings, "code_fallback", "terminal")
    monkeypatch.setattr(settings, "mail_enabled", False)
    pw = _make(owner, email, "Term Inal", "user")
    c = TestClient(app)
    capsys.readouterr()
    r = c.post("/api/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return c, r.json(), pw


def test_when_email_fails_the_code_goes_to_the_server_window_and_the_door_stays_shut(owner, db, monkeypatch, capsys):
    import re
    import sys

    monkeypatch.setattr(sys, "__stdout__", sys.stdout)  # capture what the server window would show
    c, r, _ = _terminal_login(owner, "term@test.local", monkeypatch, capsys)
    assert r["needs_code"] and r["channel"] == "terminal" and "window on the server" in r["message"]
    assert c.get("/api/auth/me").status_code == 401  # not in on the password alone
    shown = capsys.readouterr().out
    assert "SIGN-IN CODE" in shown and "term@test.local" in shown
    code = re.search(r"\n\s+(\d{3}) (\d{3})\n", shown)
    assert code, shown
    ok = c.post("/api/auth/verify", json={"email": "term@test.local", "code": code.group(1) + code.group(2)})
    assert ok.status_code == 200 and c.get("/api/auth/me").status_code == 200
    from cbx.models import AuditLog

    assert db.query(AuditLog).filter(AuditLog.action == "login_code_terminal").count() >= 1
    assert db.query(AuditLog).filter(AuditLog.action == "login_unverified", AuditLog.target == "term@test.local").count() == 0
    # used: gone from the waiting list
    assert not [w for w in owner.get("/api/users/codes-waiting").json()["codes"] if w["email"] == "term@test.local"]


def test_an_admin_can_read_a_waiting_code_for_people_they_manage_only(owner, admin, db, monkeypatch, capsys):
    _terminal_login(owner, "term2@test.local", monkeypatch, capsys)
    w = [x for x in owner.get("/api/users/codes-waiting").json()["codes"] if x["email"] == "term2@test.local"]
    assert len(w) == 1 and len(w[0]["code"]) == 6
    assert [x for x in admin.get("/api/users/codes-waiting").json()["codes"] if x["email"] == "term2@test.local"]  # a user: yes
    from cbx.models import AuditLog

    assert db.query(AuditLog).filter(AuditLog.action == "login_codes_viewed").count() >= 1
    # the admin's own code, or an owner's, is never shown to another admin
    pw = _make(owner, "admin2@test.local", "Second Admin", "admin")
    c = TestClient(app)
    c.post("/api/auth/login", json={"email": "admin2@test.local", "password": pw})
    assert not [x for x in admin.get("/api/users/codes-waiting").json()["codes"] if x["email"] == "admin2@test.local"]
    user = TestClient(app)
    assert user.get("/api/users/codes-waiting").status_code == 401


def test_when_email_works_nothing_goes_to_the_window(owner, mailbox, monkeypatch, capsys):
    import sys

    monkeypatch.setattr(sys, "__stdout__", sys.stdout)
    monkeypatch.setattr(settings, "code_fallback", "terminal")
    pw = _make(owner, "mailok@test.local", "Mail Ok", "user")
    c = TestClient(app)
    capsys.readouterr()
    r = c.post("/api/auth/login", json={"email": "mailok@test.local", "password": pw})
    assert r.json()["needs_code"] and r.json().get("channel") != "terminal" and mailbox
    assert "SIGN-IN CODE" not in capsys.readouterr().out
