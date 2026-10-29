"""The mailer, and the rails that let it be switched on against a live database.

CBX now holds 32 real legal entities and the addresses of the controllers who answer for them. The
first time email is enabled it must not be possible for a sweep to reach all of them — so the rails
are tested harder than the sending is.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from cbx import mail
from cbx.config import settings
from cbx.models import User, WatchedDoc
from cbx.models.docs import Entity
from cbx.util import local_today


@pytest.fixture()
def sent(monkeypatch):
    """Capture what would have gone out instead of opening a socket."""
    out = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
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
    monkeypatch.setattr(settings, "mail_daily_cap", 200)
    mail._sent["day"], mail._sent["n"] = None, 0
    return out


def test_it_says_what_is_missing_instead_of_failing_silently(monkeypatch):
    # off by default, and it says where to turn it on in plain words (1.0.83: no setting names on
    # screen) — pinned, so a server whose .env has mail on (the live install) still passes its own suite
    monkeypatch.setattr(settings, "mail_enabled", False)
    ok, why = mail.configured()
    assert not ok and "Mail set-up" in why and "CBX_" not in why


def test_nothing_is_sent_until_someone_turns_it_on(monkeypatch):
    monkeypatch.setattr(settings, "mail_enabled", False)
    r = mail.send(["real.person@cbreexcellerate.com"], "Expiry", "body")
    assert not r.ok and "switched off" in r.detail


def test_the_redirect_takes_every_message_and_says_who_it_was_for(sent, monkeypatch):
    """The rail that makes switching this on survivable: point it at yourself, run the real sweep,
    read what 32 controllers would have received."""
    monkeypatch.setattr(settings, "mail_only_to", "edwin.botha@cbreexcellerate.com")
    r = mail.send(["otsile.molobi@cbreexcellerate.com"], "AFS expires in 7 days", "the body")
    assert r.ok
    assert len(sent) == 1
    assert sent[0]["to"] == "edwin.botha@cbreexcellerate.com"
    assert "otsile.molobi@cbreexcellerate.com" in sent[0]["body"]  # who it was really for
    assert sent[0]["subject"].startswith("[test]")


def test_a_runaway_loop_stops_at_the_cap_not_at_an_inbox(sent, monkeypatch):
    monkeypatch.setattr(settings, "mail_daily_cap", 3)
    results = [mail.send([f"p{i}@test.local"], "s", "b") for i in range(6)]
    assert sum(1 for r in results if r.ok) == 3
    assert len(sent) == 3
    assert "Daily send limit reached" in results[-1].detail


def test_a_bad_address_is_refused_before_the_socket_opens(sent):
    assert not mail.send(["not-an-address"], "s", "b").ok
    assert not mail.send([], "s", "b").ok
    assert sent == []


def test_a_dead_mail_server_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(settings, "mail_enabled", True)
    monkeypatch.setattr(settings, "smtp_host", "smtp.test.local")
    monkeypatch.setattr(settings, "mail_only_to", "")

    def boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(mail.smtplib, "SMTP", boom)
    r = mail.send(["a@b.com"], "s", "b")
    assert not r.ok and "Could not reach the mail server" in r.detail


# ---- the reminder sweep ---------------------------------------------------------------------------
def _expiring(db, owner, name, days, email=""):
    e = Entity(name=name, responsible_email=email)
    db.add(e)
    db.flush()
    for i, title in enumerate(("Tax clearance", "Public liability", "VAT certificate")):
        db.add(WatchedDoc(entity_id=e.id, title=title, entity=name, owner_id=owner.id,
                          expiry=local_today() + timedelta(days=days), warn_days=90))
    db.flush()
    return e


def test_a_controller_gets_one_email_not_one_per_certificate(owner, db, sent):
    from cbx.docwatch import reminders as R

    me = db.query(User).filter(User.role == "owner", User.is_axl.is_(False)).one()
    _expiring(db, me, "Mail Test Mozambique", 5)
    db.flush()
    R.run_once(db)
    mine = [m for m in sent if me.email in m["to"]]
    # One email, however many certificates moved — other tests leave their own documents behind, so
    # the digest may carry more than these three. That IS the behaviour: one mail, everything in it.
    assert len(mine) == 1, f"one digest expected, got {len(mine)}"
    body = mine[0]["body"]
    for title in ("Tax clearance", "Public liability", "VAT certificate"):
        assert title in body
    assert "Mail Test Mozambique" in body
    assert "expiring" in mine[0]["subject"] or "expired" in mine[0]["subject"]


def test_a_controller_with_no_account_still_gets_the_mail(owner, db, sent):
    """Most of the finance list has no account here. A name on the grid with nobody reachable behind
    it is how a certificate quietly lapses."""
    from cbx.docwatch import reminders as R

    me = db.query(User).filter(User.role == "owner", User.is_axl.is_(False)).one()
    _expiring(db, me, "Mail Test Angola", 4, email="otsile.external@example.com")
    db.flush()
    R.run_once(db)
    assert any("otsile.external@example.com" in m["to"] for m in sent)


def test_the_in_app_reminder_still_lands_when_email_is_off(owner, db, monkeypatch):
    """Email is an addition, not a replacement. A dead mail server must not cost anyone a reminder."""
    from cbx.docwatch import reminders as R
    from cbx.models import CexaMessage

    monkeypatch.setattr(settings, "mail_enabled", False)
    me = db.query(User).filter(User.role == "owner", User.is_axl.is_(False)).one()
    _expiring(db, me, "Mail Test Ghana", 3)
    db.flush()
    before = db.query(CexaMessage).filter(CexaMessage.user_id == me.id).count()
    n = R.run_once(db)
    assert n >= 3
    assert db.query(CexaMessage).filter(CexaMessage.user_id == me.id).count() > before


def test_the_test_mail_is_for_admins_and_goes_only_to_people_cbx_knows(owner, admin, user, monkeypatch, sent):
    """1.0.79: one test button — Mailer → Settings (Settings → Mail is gone)."""
    assert user.post("/api/mailer/test", json={}).status_code == 403
    r = owner.post("/api/mailer/test", json={})
    assert r.status_code == 200 and r.json()["ok"], r.text
    assert sent and "owner@test.local" in sent[-1]["to"]
    # not a way to send mail from the company mailbox to anywhere
    bad = owner.post("/api/mailer/test", json={"to": "someone.else@elsewhere.com"}).json()
    assert bad["ok"] is False and len(sent) == 1


def test_the_redirect_is_shown_loudly(owner, monkeypatch):
    monkeypatch.setattr(settings, "mail_only_to", "edwin.botha@cbreexcellerate.com")
    s = owner.get("/api/mailer/settings").json()
    assert s["redirect"] == ["edwin.botha@cbreexcellerate.com"]
