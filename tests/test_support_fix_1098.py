"""1.0.98 review fixes on the Support desk (the probes tests/probe_1098_{nudge,invisible,inline}.py, turned around):

1. the first Nudge run after the upgrade never mails reminders for tickets assigned before 1.0.98 — the clocks start when
   1.0.98 first started; tickets already being worked count as accepted; every open ticket gets an Accept link, and no
   mail ever carries an empty one;
2. an invisible lead's earlier messages never reach the requester's later mails unscrubbed (history());
3. a lead's pasted pictures are not ticket files, and an invisible lead's files never show on the requester's page;
4. one attachment Outlook can't save never loses the whole mail;
5. a Nudge run that fails after mailing never mails the same thing again.
Names are made up."""
import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from cbx import compat2
from cbx.db import SessionLocal, utcnow
from cbx.main import app
from cbx.models import AuditLog, SupportMessage, SupportNotice, SupportSystem, SupportTicket, User
from cbx.support import core as C
from cbx.support import nudge as N
from cbx.support import outlook

from .test_support_1098 import (NAMES, _Att, _Item, _setup, accept_link, assign, assigned_mail, box, deliver, lead, noise_png, nudge, ready_ticket,  # noqa: F401
                                ticket, to, uid)


def _old_tickets(lebo, statuses=("assigned", "discussing", "assigned")):
    """Rows as a live 1.0.97 install has them after the upgrade (add_missing_columns: no token, no accepted_at, seq 0)."""
    ids = []
    with SessionLocal() as s:
        chase = s.query(SupportSystem).filter(SupportSystem.key == "chase").one()
        for i, st in enumerate(statuses):
            t = SupportTicket(system_id=chase.id, title=f"Old ticket {i}", description="x", requester_email=f"old{i}@client.example", requester_name="R",
                              source="mail", status=st, assigned_to_id=lebo, assigned_at=utcnow() - timedelta(days=5 + i), summary="s", accept_nonce="",
                              accept_hash="", assign_seq=0, accepted_at=None)
            s.add(t)
            s.flush()
            ids.append(t.id)
        s.commit()
    return ids


def _not_accepted(fake, who):
    return [m for m in to(fake, who) if "not accepted" in m["subject"].lower()]


# ---- 1. the first run after the upgrade ----------------------------------------------------------------------------------
def test_the_first_nudge_after_the_upgrade_mails_nothing_for_tickets_assigned_before(box, owner):  # noqa: F811
    _setup(owner)
    owner.post("/api/support/escalation", json={"user_id": uid("owner@test.local")})
    with SessionLocal() as s:  # the suite shares one database: only this test's upgrade rows count
        from sqlalchemy import func

        first = s.query(func.max(AuditLog.id)).scalar() or 0
    lebo = lead(owner)
    ids = _old_tickets(lebo)
    with SessionLocal() as s:  # the lead had already written on the first one (by mail, after the assignment)
        m = SupportMessage(ticket_id=ids[0], who="note", author_id=lebo, author_name="Lebo Lead", text="Lebo Lead wrote by e-mail:\nOn my way.", via="mail",
                           at=utcnow() - timedelta(days=4))
        s.add(m)
        s.commit()
    nudge(utcnow())  # the routine's first round after the restart
    assert _not_accepted(box, "lebo@test.local") == [] and _not_accepted(box, "owner@test.local") == []
    with SessionLocal() as s:
        rows = [s.get(SupportTicket, i) for i in ids]
        assert all(r.accept_nonce and r.accept_hash and C.accept_link(s, r) for r in rows)  # every open ticket has a working Accept link
        assert rows[0].accepted_at is not None and rows[0].accepted_by == "Lebo Lead"  # the lead wrote after the assignment: accepted from then
        assert rows[1].accepted_at is not None  # under discussion: being worked
        assert rows[2].accepted_at is None  # nobody touched it yet: it waits for Accept, its clock from now
        assert s.query(AuditLog).filter(AuditLog.action == "support_upgrade_1098", AuditLog.id > first).count() == 1
        assert s.query(AuditLog).filter(AuditLog.action == "support_accepted", AuditLog.actor_label == "1.0.98 upgrade", AuditLog.id > first).count() >= 2
        started = N.started_at(s)
    # once: a second run (or a restart) does not repair again; the reminder comes 6 hours after the upgrade, with a live link
    with SessionLocal() as s:
        assert N.upgrade(s) == {}
    nudge(started + timedelta(hours=6.2))
    a = _not_accepted(box, "lebo@test.local")
    assert len(a) == 1 and a[0]["subject"] == "[T-%04d] Not accepted yet — assigned 6 h ago" % ids[2]
    link = accept_link(a[0]["body"])
    assert 'href=""' not in a[0]["html"] and not re.search(r"Accept: (\s|$)", a[0]["body"])
    assert "Accepted. Thank you." in TestClient(app).post(link).text


def test_no_mail_ever_carries_an_empty_accept_link(box, owner):  # noqa: F811
    _setup(owner)
    t = ready_ticket(box, owner)
    lebo = lead(owner)
    assign(owner, t, lebo)
    nudge(utcnow())  # the upgrade has run
    with SessionLocal() as s:  # a ticket that somehow has no token
        x = s.get(SupportTicket, t.id)
        x.accept_nonce, x.accept_hash = "", ""
        s.commit()
        at = x.assigned_at
    nudge(at + timedelta(hours=6.2))
    a = _not_accepted(box, "lebo@test.local")
    assert len(a) == 1 and 'href=""' not in a[0]["html"] and "Accept: " not in a[0]["body"] and ">Accept<" not in a[0]["html"]
    # the lead's ticket mail too: no token, no Accept button (Mark as done stays)
    with SessionLocal() as s:
        x = s.get(SupportTicket, t.id)
        C.mail_staff(s, s.get(User, lebo), "lead_replied", C.values(s, x, message="Still broken."), x)
        s.commit()
    m = to(box, "lebo@test.local")[-1]
    assert 'href=""' not in (m["html"] or "") and ">Accept<" not in m["html"] and ">Mark as done<" in m["html"]


# ---- 5. a run that fails after mailing never mails again ----------------------------------------------------------------
def test_a_nudge_run_whose_commit_fails_never_mails_the_same_reminder_again(box, owner):  # noqa: F811
    _setup(owner)
    t = ready_ticket(box, owner)
    assign(owner, t, lead(owner))
    at = ticket(tid=t.id).assigned_at + timedelta(hours=6.1)
    for _ in range(3):
        with SessionLocal() as s:
            N.run(s, now=at)
            s.rollback()  # what session_scope does when the run's commit (or anything after the send) fails
    assert len([m for m in to(box, "lebo@test.local") if "Not accepted yet" in m["subject"]]) == 1


def test_a_nudge_run_that_crashes_after_mailing_never_mails_again(box, owner, monkeypatch):  # noqa: F811
    _setup(owner)
    t = ready_ticket(box, owner)
    assign(owner, t, lead(owner))
    at = ticket(tid=t.id).assigned_at + timedelta(hours=6.1)

    def boom(db, res):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(N, "_save", boom)
    for _ in range(3):
        with SessionLocal() as s:
            with pytest.raises(RuntimeError):
                N.run(s, now=at)
            s.rollback()
    assert len([m for m in to(box, "lebo@test.local") if "Not accepted yet" in m["subject"]]) == 1
    with SessionLocal() as s:
        assert s.query(SupportNotice).filter(SupportNotice.ticket_id == t.id, SupportNotice.rule == "accept6").count() == 1


# ---- 2. an invisible lead's earlier messages in the requester's next mails -----------------------------------------------
def test_an_invisible_leads_app_message_never_reaches_the_requester_in_a_later_mails_history(box, owner, user, monkeypatch):  # noqa: F811
    _setup(owner)
    monkeypatch.setattr(C, "thread_mail", lambda *a, **k: "")  # no Outlook thread (SMTP, or the earlier mail is gone): the history goes underneath
    z, zc = lead(owner, "zanele.98@test.local", "Zanele Khumalo", client=True)
    assert owner.post("/api/support/people", json={"user_id": z, "schedule": "daily", "at_time": "07:45", "invisible": True}).status_code == 200
    r = user.post("/api/support/tickets", json={"system": "chase", "problem": "other", "description": "The aircon on floor 3 of Sandton Building B is not cooling.",
                                                "where": "Sandton, Building B", "outcome": "Send a technician"})
    t = ticket(tid=r.json()["ticket"]["id"])
    assign(owner, t, z)
    r = zc.post(f"/api/support/tickets/{t.id}/message", json={"text": "Hi Thandi,\n\nCould you send a photo of the unit's label?\n\nKind regards,\nZanele Khumalo\n"
                                                                       "Senior Technician | CBRE Excellerate\nCell: 082 555 1234", "to_requester": True})
    assert r.status_code == 200, r.text
    first = to(box, "thandi@test.local")[-1]
    assert not NAMES.search(first["body"] + (first["html"] or "")) and "082 555 1234" not in first["body"]
    assert zc.post(f"/api/support/tickets/{t.id}/status", json={"status": "closed", "note": "Replaced the fan motor."}).status_code == 200
    done = to(box, "thandi@test.local")[-1]
    shown = done["body"] + (done["html"] or "")
    assert "photo of the unit's label" in shown  # the earlier message is still underneath ...
    assert not NAMES.search(shown) and "082 555 1234" not in shown and "Senior Technician" not in shown  # ... as the requester may see it
    with SessionLocal() as s:  # the stored message stays true for the staff
        assert s.query(SupportMessage).filter(SupportMessage.ticket_id == t.id, SupportMessage.author_name == "Zanele Khumalo", SupportMessage.who == "staff").count() >= 1
    assert "Zanele Khumalo" in str(owner.get(f"/api/support/tickets/{t.id}").json()["thread"])


# ---- 3. a lead's pictures ----------------------------------------------------------------------------------------------------
def test_a_leads_pasted_picture_is_not_a_ticket_file_and_an_invisible_leads_files_never_reach_the_requester(box, owner, user):  # noqa: F811
    _setup(owner)
    z = lead(owner, "zanele.98@test.local", "Zanele Khumalo")
    assert owner.post("/api/support/people", json={"user_id": z, "schedule": "daily", "at_time": "07:45", "invisible": True}).status_code == 200
    r = user.post("/api/support/tickets", json={"system": "chase", "problem": "other", "description": "The aircon on floor 3 of Sandton Building B is not cooling.",
                                                "where": "Sandton, Building B", "outcome": "Send a technician"})
    t = ticket(tid=r.json()["ticket"]["id"])
    assign(owner, t, z)
    photo = noise_png(240, 180, seed=42)  # the lead's signature photo: 4:3, well over 30 KB
    assert len(photo) >= outlook.INLINE_MIN
    report = b"%PDF-1.4 the service report"
    deliver(box, owner, "zanele.98@test.local", f"RE: [{C.ref(t)}] Assigned to you", "Hi Thandi,\n\nCould you send a photo of the unit's label?\n\nKind regards,\nZanele Khumalo",
            "Zanele Khumalo", files=[("image001.png", photo), ("service-report.pdf", report)], inline=["image001.png"])
    staff = owner.get(f"/api/support/tickets/{t.id}").json()
    # 1.0.109: a lead's pasted picture is kept like any mail's (it isn't a signature by the picture rules) — for the desk only
    assert sorted(f["name"] for f in staff["files"]) == ["image001.png", "service-report.pdf"]
    seen = user.get(f"/api/support/tickets/{t.id}").json()  # the requester's own page: nothing of the invisible lead's
    assert seen["files"] == []
    n = next(f["n"] for f in staff["files"] if f["name"] == "service-report.pdf")
    assert user.get(f"/api/support/tickets/{t.id}/files/{next(f['n'] for f in staff['files'] if f['name'] == 'image001.png')}").status_code == 404
    assert user.get(f"/api/support/tickets/{t.id}/files/{n}").status_code == 404
    assert owner.get(f"/api/support/tickets/{t.id}/files/{n}").status_code == 200
    # the requester's own pasted picture is still kept (Iris reads it)
    shot = noise_png(240, 180, seed=43)
    deliver(box, owner, "thandi@test.local", f"RE: [{C.ref(t)}] Aircon", "Here is the label.", "Thandi Mokoena", files=[("image002.png", shot)], inline=["image002.png"])
    assert "image002.png" in [f["name"] for f in user.get(f"/api/support/tickets/{t.id}").json()["files"]]


# ---- 4. one attachment Outlook can't save ------------------------------------------------------------------------------------
class _BadAtt(_Att):
    def SaveAsFile(self, path):  # noqa: N802 — Outlook: "Cannot save the attachment"
        raise OSError("Cannot save the attachment.")


def test_a_picture_outlook_cannot_save_is_skipped_and_the_mail_is_read(box, caplog):  # noqa: F811
    ok = _Att("Invoice-77.pdf", b"%PDF-1.4 " + b"x" * 300_000)
    bad = _BadAtt("image001.png", noise_png(240, 180, seed=3), cid="image001.png@01DB")
    got = outlook._Com._read(object.__new__(outlook._Com), _Item([ok, bad], mid="<m9@x>"))
    assert [n for n, _ in got["files"]] == ["Invoice-77.pdf"] and got["inline"] == []
    assert "<m9@x>" in caplog.text and "not saved" in caplog.text
    worse = _BadAtt("Invoice-78.pdf", b"%PDF-1.4 y")
    assert outlook._Com._read(object.__new__(outlook._Com), _Item([worse, ok], mid="<m10@x>"))["files"][0][0] == "Invoice-77.pdf"


def test_the_office_and_the_team_view_still_show_nudge(box, owner):  # noqa: F811
    _setup(owner)
    t = ready_ticket(box, owner)
    assign(owner, t, lead(owner))
    nudge(ticket(tid=t.id).assigned_at + timedelta(hours=6.2))
    assert compat2.status_of(["Nudge (Support)"])["Nudge (Support)"]["done_today"] >= 1
