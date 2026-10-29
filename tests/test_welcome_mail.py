"""1.0.93: a new person gets Edwin's welcome mail — the address, their email and temporary password, set their own
password at once; Entity Compliance people also read which entities they are responsible for."""
from cbx import mail, mailcfg
from cbx.models import MailLog


def _capture(monkeypatch):
    sent = []

    def fake(to, subject, body, **kw):
        sent.append({"to": list(to), "subject": subject, "body": body})
        return mail.Result(True, "Sent.", tuple(to))

    monkeypatch.setattr(mail, "send", fake)
    monkeypatch.setattr(mail, "configured", lambda: (True, "Sending."))
    return sent


def test_a_new_person_gets_the_welcome_mail_with_how_to_sign_in(owner, db, monkeypatch):
    sent = _capture(monkeypatch)
    ec = next(t["id"] for t in owner.get("/api/users").json()["tools"] if t["slug"] == "doc-watch")
    r = owner.post("/api/users", json={"email": "saveshin.w@test.local", "name": "Saveshin Welcome", "role": "user", "team_name": "Finance", "tools": [ec]})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["welcome_mail"]["ok"], j["welcome_mail"]
    m = sent[-1]
    assert m["to"] == ["saveshin.w@test.local"] and m["subject"] == "Your access to Entity Compliance"
    b = m["body"]
    assert b.startswith("Hi Saveshin") and "Entity Compliance system" in b and "COIDA" in b
    assert f"   Email: saveshin.w@test.local\n   Password: {j['temp_password']}" in b and "12+ characters" in b and "not applicable" in b
    assert b.rstrip().endswith("Thanks\nTest")
    row = db.query(MailLog).filter(MailLog.kind == "welcome").order_by(MailLog.id.desc()).first()
    assert row is not None and j["temp_password"] not in row.preview  # never readable in the Sent list


def test_entities_and_other_tools_and_unticked(owner, db, monkeypatch):
    from cbx import welcome
    from cbx.models import User

    sent = _capture(monkeypatch)
    monkeypatch.setattr(welcome, "entities_for", lambda db, u: ["CBRE Excellerate SA", "CTS", "EFM SA"])
    ec = next(t["id"] for t in owner.get("/api/users").json()["tools"] if t["slug"] == "doc-watch")
    owner.post("/api/users", json={"email": "ravi.w@test.local", "name": "Ravi Singh", "role": "user", "team_name": "Finance", "tools": [ec]})
    assert "Your entities include CBRE Excellerate SA, CTS and EFM SA, and you're shown as the responsible person for them." in sent[-1]["body"]
    ifrs = next(t["id"] for t in owner.get("/api/users").json()["tools"] if t["slug"] == "ifrs16")
    owner.post("/api/users", json={"email": "lea.w@test.local", "name": "Lea Leases", "role": "user", "team_name": "Finance", "tools": [ifrs]})
    assert "IFRS 16" in sent[-1]["body"] and "Entity Compliance system" not in sent[-1]["body"]
    n = len(sent)
    r = owner.post("/api/users", json={"email": "quiet.w@test.local", "name": "Quiet Person", "role": "user", "team_name": "Finance", "welcome_mail": False})
    assert r.status_code == 200 and len(sent) == n and not r.json()["welcome_mail"]["ok"]
    _ = (db, User)


def test_the_mailer_can_hold_welcome_mails_to_check_first(owner, db, monkeypatch):
    sent = _capture(monkeypatch)
    mailcfg.save(db, {"drafts": ["welcome"]})
    db.commit()
    try:
        r = owner.post("/api/users", json={"email": "held.w@test.local", "name": "Held Person", "role": "user", "team_name": "Finance"}).json()
        assert r["welcome_mail"]["held"] and not sent
    finally:
        mailcfg.save(db, {"drafts": []})
        db.commit()


def test_with_mail_off_nothing_is_tried(owner, db, monkeypatch):
    n = db.query(MailLog).filter(MailLog.kind == "welcome").count()
    monkeypatch.setattr(mail, "configured", lambda: (False, "Email is switched off on this server."))
    r = owner.post("/api/users", json={"email": "off.w@test.local", "name": "Off Mail", "role": "user", "team_name": "Finance"}).json()
    assert not r["welcome_mail"]["ok"] and "switched off" in r["welcome_mail"]["detail"]
    assert db.query(MailLog).filter(MailLog.kind == "welcome").count() == n
