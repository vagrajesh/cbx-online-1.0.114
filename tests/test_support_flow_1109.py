"""1.0.109 (Edwin, 21 Oct: "it doesn't add screenshots to the ticketer — fix that; I need to be able to remove a ticket, either by
letting them know or silently (owners); clean up and fix the tickets so that it just flows and works").

    T-0028  Tanuja's reply to her closed ticket carried an SAP screenshot pasted in the body; the ticket reopened to Tebogo without
            it (the reader wanted 30 KB for a pasted picture, and dropped Outlook's image001.png without a content id unseen)
    T-0018  another helpdesk's automatic acknowledgement became a ticket and was sent AXL's questions; Sashen's reply by mail left
            it "Pending — waiting for the requester", Lead: nobody

Outlook through outlook.Fake (its `raw` attachments are read by the reader's own rules). Names, numbers and mails are made up."""
import io
from datetime import timedelta

from cbx.db import SessionLocal, utcnow
from cbx.models import AuditLog, MailLog, SupportMail, SupportMessage, SupportTicket, User
from cbx.support import core as C
from cbx.support import inbox as I
from cbx.support import outlook
from cbx.support import own as OWN

from .test_support_1098 import (BOX, GENERATOR, _Att, _read, _setup, assign, box, deliver, lead, noise_png, sig, ticket, to, uid)  # noqa: F401
from .conftest import _make, login

TANUJA, TEBOGO, SASHEN = "tanuja.1109@client.example", "tebogo.1109@test.local", "sashen.1109@test.local"


# ---- pictures, made up ----------------------------------------------------------------------------------------------------
def flat_png(w=800, h=600, lines=2, seed=1) -> bytes:
    """A flat-colour screenshot (an SAP screen, 800×600): a title bar, grey panels, a few rows of text — about 12 KB as PNG."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (w, h), (236, 239, 241))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w, 34], fill=(16, 66, 122))
    d.text((10, 10), "Post Document: Header Data   Company Code Z320", fill=(255, 255, 255))
    d.rectangle([10, 50, w - 10, h - 60], outline=(160, 170, 180), fill=(250, 250, 250))
    for i in range(lines):
        y = 60 + i * 14
        if y > h - 80:
            break
        d.text((20, y), f"{4000 + i * 7 + seed:06d}  GL 7{seed}{i:03d}  Firmenich reallocation  {i * 1234.56:>12.2f} ZAR", fill=(30, 30, 30))
    d.rectangle([0, h - 40, w, h], fill=(255, 238, 186))
    d.text((10, h - 28), "E  Balance in transaction currency: posting not possible (FI 702)", fill=(150, 30, 30))
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def logo_png() -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (160, 160), (255, 255, 255))
    ImageDraw.Draw(img).ellipse([20, 20, 140, 140], fill=(0, 106, 77))
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def banner_png() -> bytes:
    return noise_png(900, 120, seed=21)


def att(name, data, cid=True):
    return {"name": name, "data": data, "cid": f"{name}@01DB2C3D.4E5F6A70" if cid else ""}


def _desk(owner, email, name, role="manager", lead_too=True):
    pw = _make(owner, email, name, role)
    if lead_too:
        assert owner.post("/api/support/leads", json={"user_id": uid(email), "system_ids": []}).status_code == 200
    from fastapi.testclient import TestClient

    from cbx.main import app

    c = TestClient(app)
    login(c, email, pw)
    return uid(email), c


def _thread(c, tid):
    r = c.get(f"/api/support/tickets/{tid}")
    assert r.status_code == 200, r.text
    return r.json()


def _ready(fake, owner, sender, subject, name="Omar Hassan", conv=None, raw=None):
    fake.deliver(sender, subject, GENERATOR + sig(name), name=name, store=BOX, conversation=conv or ("C1109-" + subject), raw=raw)
    assert owner.post("/api/mailer/read", json={"store": BOX}).status_code == 200
    t = ticket(sender)
    assert t is not None and t.status == "ready", (t and t.status, t and t.missing)
    return t.id


def _audits(action, tid):
    """Audit rows for this ticket since it was made (ticket numbers come round again in this suite: the desk is cleaned each test)."""
    with SessionLocal() as s:
        t = s.get(SupportTicket, tid)
        return s.query(AuditLog).filter(AuditLog.action == action, AuditLog.target == f"ticket:T-{tid:04d}", AuditLog.at >= t.created_at).count()


# ---- 1. every screenshot reaches the ticket ------------------------------------------------------------------------------
def test_the_picture_rules_size_alone_never_drops_a_screenshot(box, owner):
    shot = flat_png()
    assert 8_000 < len(shot) < 30_000, len(shot)  # a flat SAP screen: what 1.0.98's 30 KB rule dropped
    logo, banner = logo_png(), banner_png()
    assert len(logo) < 5_000 and outlook.image_size(banner) == (900, 120)
    files, inline, left = outlook.pick([{"name": n, "size": len(d), "cid": c, "hidden": False, "save": (lambda d=d: d)}
                                        for n, d, c in (("image001.png", shot, "a@b"), ("image002.png", logo, "c@d"), ("image003.png", banner, "e@f"))],
                                       "tanuja@client.example", "<m@x>")
    assert [n for n, _ in files] == ["image001.png"] and inline == ["image001.png"]
    why = {x["name"]: x["why"] for x in left}
    assert "signature logo" in why["image002.png"] and "banner" in why["image003.png"]
    # a 100-pixel-high strip or a 64×64 icon is a logo by its pixels, whatever its bytes
    assert "signature logo" in outlook.signature_like(noise_png(300, 100, seed=3), "x@y", "<1>")
    assert outlook.signature_like(shot, "x@y", "<2>") == ""


def test_the_reader_keeps_a_rich_text_pasted_picture_and_says_what_it_left_out(box, owner):
    shot = flat_png(seed=2)
    # Outlook names a picture pasted in a rich-text mail image001.png, with no content id: 1.0.98 dropped it unseen
    out = _read([_Att("image001.png", shot), _Att("image002.png", logo_png(), cid="image002.png@01DB"), _Att("Journal.xlsx", b"PK\x03\x04 sheet")], mid="<rt@x>")
    assert [n for n, _ in out["files"]] == ["Journal.xlsx", "image001.png"] and out["inline"] == ["image001.png"]
    assert [x["name"] for x in out["left_out"]] == ["image002.png"] and "logo" in out["left_out"][0]["why"]
    assert out["recipients"] == []  # no Recipients on the stand-in item: the old behaviour


def test_a_reply_to_a_closed_ticket_keeps_its_12_kb_screenshot_and_shows_it(box, owner, admin):
    """T-0028: closed by Tebogo; Tanuja replies with an SAP screenshot pasted in the body — it is on the ticket, under her reply, for
    the desk and for her, and the mail to Tebogo carries it; a 3 KB logo and a 900×120 banner are left out with an admin line."""
    _setup(owner)
    teb, tc = _desk(owner, TEBOGO, "Tebogo Mabaso")
    tid = _ready(box, owner, TANUJA, "Z320 Reallocation – Firmenich", name="Tanuja Singh")
    assign(owner, ticket(tid=tid), teb)
    assert tc.post(f"/api/support/tickets/{tid}/accept").status_code == 200
    assert tc.post(f"/api/support/tickets/{tid}/status", json={"status": "closed", "note": "Reallocated the cost centre for Firmenich."}).status_code == 200
    shot = flat_png(seed=28)
    assert 8_000 < len(shot) < 20_000, len(shot)  # about 12 KB
    box.deliver(TANUJA, f"RE: [T-{tid:04d}] Your ticket is done", "Please see below which comes up when trying to process the journal:\n\n[cid:image001.png]\n\nThanks\nTanuja",
                name="Tanuja Singh", store=BOX, conversation="C1109-Z320 Reallocation – Firmenich",
                raw=[att("image001.png", shot), att("image002.png", logo_png()), att("image003.png", banner_png())])
    assert owner.post("/api/mailer/read", json={"store": BOX}).status_code == 200
    t = ticket(tid=tid)
    assert t.status == "discussing" and t.assigned_to_id == teb  # back with Tebogo
    pics = [f for f in t.files if f["name"] == "image001.png" and f["size"] == len(shot)]
    assert len(pics) == 1 and pics[0].get("msg")
    view = _thread(owner, tid)
    reply = next(m for m in view["thread"] if m["id"] == pics[0]["msg"])
    assert reply["who"] == "requester" and "process the journal" in reply["text"]
    assert [f["name"] for f in reply["files"]] == ["image001.png"] and reply["files"][0]["picture"]
    whys = {x["name"]: x["why"] for x in reply["left_out"]}  # the admins see what was left out, and why
    assert "logo" in whys["image002.png"] and "banner" in whys["image003.png"]
    assert view["word"] == "With Tebogo — waiting for them to accept"
    staff = _thread(tc, tid)  # Tebogo (not an admin) sees the picture, not the admin line
    r2 = next(m for m in staff["thread"] if m["id"] == reply["id"])
    assert r2["files"] and "left_out" not in r2
    # the picture is shown on the page (not a download) — and only as a picture
    got = tc.get(f"/api/support/tickets/{tid}/files/{pics[0]['n']}?view=1")
    assert got.status_code == 200 and got.headers["content-type"] == "image/png" and got.headers["content-disposition"].startswith("inline")
    assert got.content == shot
    # the relay mail to Tebogo carries the picture
    m = to(box, TEBOGO)[-1]  # in Tebogo's own chain of this ticket ("RE: [T-…] Assigned to you: …")
    assert "process the journal" in m["body"] and m["attachments"] == ["image001.png"]


def test_a_new_mail_with_two_screenshots_keeps_both_and_the_requester_sees_them(box, owner, user):
    _setup(owner)
    a, b = flat_png(seed=31), flat_png(seed=32, lines=4)
    box.deliver("thandi@test.local", "SAP posting error on MIRO", "Hi,\n\nMIRO gives the error below for invoice 5100012345, company code Z320. Please fix the posting.\n\nThanks",
                name="Thandi Mokoena", store=BOX, conversation="C1109-two", raw=[att("image001.png", a), att("image002.png", b), att("image003.png", logo_png())])
    assert owner.post("/api/mailer/read", json={"store": BOX}).status_code == 200
    t = ticket("thandi@test.local")
    assert sorted(f["name"] for f in t.files) == ["image001.png", "image002.png"]
    mine = _thread(user, t.id)  # the requester's own page shows them under her mail, without the admin line
    first = mine["thread"][0]
    assert sorted(f["name"] for f in first["files"]) == ["image001.png", "image002.png"] and "left_out" not in first
    assert user.get(f"/api/support/tickets/{t.id}/files/{first['files'][0]['n']}?view=1").status_code == 200


# ---- 2. remove a ticket -------------------------------------------------------------------------------------------------
def test_remove_letting_the_requester_know_then_restore(box, owner):
    _setup(owner)
    lid, lc = _desk(owner, "lebo.1109@test.local", "Lebo Lead")
    tid = _ready(box, owner, "omar.1109@client.example", "Generator Nile Tower")
    assign(owner, ticket(tid=tid), lid)
    before = len(to(box, "omar.1109@client.example"))
    v = _thread(owner, tid)
    assert v["can_remove"] and v["can_not_relevant"] and v["remove_notice"] == f"Hi Omar, we've closed T-{tid:04d} without action."
    r = owner.post(f"/api/support/tickets/{tid}/remove", json={"tell": True, "reason": "logged with the landlord", "message": f"Hi Omar, we've closed T-{tid:04d} without action: logged with the landlord."})
    assert r.status_code == 200, r.text
    assert r.json()["removed"] and r.json()["word"] == "Removed"
    sent = to(box, "omar.1109@client.example")
    assert len(sent) == before + 1 and "without action: logged with the landlord." in sent[-1]["body"]  # once, through the outbox
    t = ticket(tid=tid)
    assert t.status == "closed" and t.closed_as == "removed" and t.assigned_to_id is None and t.removal["how"] == "tell" and t.removal["told"]
    assert _audits("support_ticket_removed", tid) == 1
    # gone from every list and count, for everyone
    assert tid not in {x["id"] for x in owner.get("/api/support/queue?status=all").json()["tickets"]}
    assert tid not in {x["id"] for x in owner.get("/api/support/queue?status=closed").json()["tickets"]}
    d = owner.get("/api/support/dashboard").json()
    assert d["totals"]["all"] == 0 and not d["leads"]
    assert lc.get(f"/api/support/tickets/{tid}").status_code == 404 and tid not in {x["id"] for x in lc.get("/api/support/queue?status=all").json()["tickets"]}
    assert lc.get("/api/support/queue?status=removed").status_code == 403
    # the owner and admins find it under Removed
    assert [x["id"] for x in owner.get("/api/support/queue?status=removed").json()["tickets"]] == [tid]
    r = owner.post(f"/api/support/tickets/{tid}/restore")
    assert r.status_code == 200 and not r.json().get("removed")
    t = ticket(tid=tid)
    assert t.status == "ready" and not t.closed_as and t.assigned_to_id is None and _audits("support_ticket_restored", tid) == 1
    assert tid in {x["id"] for x in owner.get("/api/support/queue?status=open").json()["tickets"]}
    assert len(to(box, "omar.1109@client.example")) == before + 1  # restoring mails nobody


def test_remove_silently_a_later_reply_stays_quiet_and_staff_cannot_remove(box, owner, user):
    _setup(owner)
    lid, lc = _desk(owner, "lebo.1109b@test.local", "Lebo Lead")
    r = user.post("/api/support/tickets", json={"system": "chase", "problem": "other", "description": "The front door at Hyde Park does not lock at all.",
                                                "where": "Hyde Park", "outcome": "Fix the lock"})
    tid = r.json()["ticket"]["id"]
    assert lc.post(f"/api/support/tickets/{tid}/remove", json={"tell": False}).status_code == 403  # the desk can't remove
    sent = len(box.sent)
    assert owner.post(f"/api/support/tickets/{tid}/remove", json={"tell": False, "reason": "a test"}).status_code == 200
    assert len(box.sent) == sent  # silently: no mail
    assert ticket(tid=tid).removal["how"] == "silent"
    # the requester: not in "My tickets", not on a link, can't reply
    assert tid not in {x["id"] for x in user.get("/api/support/tickets/mine").json()["tickets"]}
    assert user.get(f"/api/support/tickets/{tid}").status_code == 404
    assert user.post(f"/api/support/tickets/{tid}/reply", json={"text": "Hello?"}).status_code == 404
    # a later mail in its thread: kept quietly, never reopens it, never mails anyone
    deliver(box, owner, "thandi@test.local", f"RE: [T-{tid:04d}] Front door", "Is anyone looking at the door? It still does not lock and it is not safe.", "Thandi Mokoena")
    t = ticket(tid=tid)
    assert t.status == "closed" and t.closed_as == "removed" and len(box.sent) == sent
    with SessionLocal() as s:
        assert s.query(SupportMail).filter(SupportMail.ticket_id == tid, SupportMail.outcome == "removed").count() == 1
    v = _thread(owner, tid)  # the admins see it under Removed
    assert any("still does not lock" in m["text"] for m in v["thread"]) and v["can_restore"]
    assert owner.get("/api/support/dashboard").json()["totals"]["closed"] == 0  # never counted as solved
    # "Not relevant — close" stays as it was: a closed state, still listed
    r = user.post("/api/support/tickets", json={"system": "chase", "problem": "other", "description": "Please send me the canteen menu for next week.",
                                                "where": "Hyde Park", "outcome": "Send the menu"})
    t2 = r.json()["ticket"]["id"]
    assert owner.post(f"/api/support/tickets/{t2}/not-relevant", json={"reason": "not support"}).status_code == 200
    assert t2 in {x["id"] for x in owner.get("/api/support/queue?status=closed").json()["tickets"]}
    assert t2 in {x["id"] for x in user.get("/api/support/tickets/mine").json()["tickets"]}


# ---- 3. a desk that flows: robots ----------------------------------------------------------------------------------------
REFLEX = "support@reflex.1109.example"
ACK = ("Dear Customer,\n\nYour ticket with ID 3901517 has been acknowledged. One of our agents will contact you shortly.\n\n"
       "Please do not reply to this e-mail.\n\nREFLEX Support")


def test_another_helpdesks_acknowledgement_never_becomes_a_ticket_and_is_never_mailed(box, owner):
    """T-0018: REFLEX's acknowledgement goes to Automatic mails; Sashen's reply on its chain is no ticket either; nothing is mailed."""
    _setup(owner)
    sid, sc = _desk(owner, SASHEN, "Sashen Moodley", role="admin", lead_too=False)
    deliver(box, owner, REFLEX, "Laptop assessment request [Ticket ID: 3901517]", ACK, "REFLEX Support", conv="C1109-reflex")
    with SessionLocal() as s:
        assert s.query(SupportTicket).filter(SupportTicket.requester_email == REFLEX).count() == 0
        row = s.query(SupportMail).filter(SupportMail.sender == REFLEX).one()
        assert row.outcome == "ignored:automatic" and "acknowledgement" in row.why
    assert to(box, REFLEX) == []
    auto = owner.get("/api/support/automatic").json()["mails"]
    assert [x["sender"] for x in auto] == [REFLEX] and "3901517" in auto[0]["text"]
    assert owner.get("/api/support/dashboard").json()["automatic"] == 1
    deliver(box, owner, SASHEN, "RE: Laptop assessment request [Ticket ID: 3901517]",
            "Reflex conducts laptop assessments. Kindly log this directly with the Reflex team.\n\nFrom: REFLEX Support\nSent: Monday\n\n" + ACK, "Sashen Moodley",
            conv="C1109-reflex")
    with SessionLocal() as s:
        assert s.query(SupportTicket).count() == 0
    assert to(box, REFLEX) == []
    # Dismiss: one line gone
    assert owner.post(f"/api/support/automatic/{auto[0]['id']}/dismiss").json()["mails"] == []


def test_auto_replies_by_headers_subjects_and_senders_and_a_quiet_note_in_a_thread(box, owner):
    _setup(owner)
    tid = _ready(box, owner, "omar.1109c@client.example", "Generator Nile Tower C", conv="C1109-ooo")
    sent = len(box.sent)
    st = ticket(tid=tid).status
    for sender, subject, headers in (("omar.1109c@client.example", "Automatic reply: Generator Nile Tower C", ""),
                                     ("omar.1109c@client.example", "Re: Generator Nile Tower C", "Auto-Submitted: auto-replied\r\n"),
                                     ("omar.1109c@client.example", "Re: Generator Nile Tower C", "X-Auto-Response-Suppress: All\r\n"),
                                     ("omar.1109c@client.example", "Re: Generator Nile Tower C", "Precedence: bulk\r\n")):
        box.deliver(sender, subject, "I am out of the office until Monday.", headers=headers, store=BOX, conversation="C1109-ooo")
    assert owner.post("/api/mailer/read", json={"store": BOX}).status_code == 200
    t = ticket(tid=tid)
    assert t.status == st and len(box.sent) == sent  # no status change, nobody mailed
    with SessionLocal() as s:
        assert s.query(SupportMail).filter(SupportMail.ticket_id == tid, SupportMail.outcome == "auto_note").count() == 4
    quiet = [m for m in _thread(owner, tid)["thread"] if m.get("quiet")]
    assert len([m for m in quiet if m["text"].startswith("An automatic mail on the same chain")]) == 4
    # Auto-Submitted: no is a person; a noreply sender is not
    assert I.robot({"sender": "a@b.example", "subject": "Hi", "headers": "Auto-Submitted: no\r\n", "body": "Please help"}) == ""
    assert I.robot({"sender": "no-reply@b.example", "subject": "Hi", "headers": "", "body": "Please help"})
    # a person who quotes an acknowledgement below their own words is a person
    assert I.robot({"sender": "a@b.example", "subject": "RE: Your ticket with ID 1", "headers": "", "body": "Please check my laptop.\n\nFrom: X\nSent: Monday\n\n" + ACK}) == ""


def test_the_loop_guard_holds_the_fourth_automatic_mail_in_an_hour(box, owner, admin):
    _setup(owner)
    tid = _ready(box, owner, "loop.1109@client.example", "Generator Nile Tower L")
    with SessionLocal() as s:
        for _ in range(3):
            s.add(MailLog(to="loop.1109@client.example", subject="x", preview="x", kind="support", ok=True, detail="", created_at=utcnow() - timedelta(minutes=5)))
        s.commit()
        t = s.get(SupportTicket, tid)
        sent = len(box.sent)
        assert C.mail_requester(s, t, "ready", C.values(s, t)) is False
        s.commit()
    assert len(box.sent) == sent
    v = _thread(owner, tid)
    held = [m for m in v["thread"] if m["who"] == "admin"]
    assert len(held) == 1 and "loop guard" in held[0]["text"]
    lid, lc = _desk(owner, "lebo.1109l@test.local", "Lebo Lead")
    assert not [m for m in _thread(lc, tid)["thread"] if m["who"] == "admin"]  # the admins' line
    # a person's own words still go (and count)
    assign(owner, ticket(tid=tid), lid)
    assert lc.post(f"/api/support/tickets/{tid}/accept").status_code == 200
    before = len(to(box, "loop.1109@client.example"))
    assert lc.post(f"/api/support/tickets/{tid}/message", json={"text": "Which floor is the generator on?", "to_requester": True}).status_code == 200
    assert len(to(box, "loop.1109@client.example")) == before + 1


def test_the_one_time_repair_moves_a_robots_open_ticket_to_automatic_mails(box, owner):
    _setup(owner)
    with SessionLocal() as s:  # T-0018 as it was: a ticket from the acknowledgement, waiting for the robot's answer
        t = C.create(s, system=None, description=ACK, title="Laptop assessment request", name="REFLEX Support", email=REFLEX, source="mail")
        t.status, t.missing, t.asks = "pending", ["Which laptop?"], 1
        s.add(SupportMail(message_id="<t18@x>", sender=REFLEX, subject="Laptop assessment request", ticket_id=t.id, outcome="new_ticket"))
        human = C.create(s, system=None, description=GENERATOR, title="Generator", name="Omar", email="omar.1109r@client.example", source="mail")
        human.status = "ready"
        from cbx import state

        state.set(s, I.REPAIR_KEY, "")
        s.commit()
        tid, hid = t.id, human.id
    sent = len(box.sent)
    d = owner.get("/api/support/dashboard").json()  # the repair runs once, on the admins' dashboard (or the next mail read)
    t = ticket(tid=tid)
    assert t.closed_as == "removed" and t.removal["how"] == "automatic" and ticket(tid=hid).status == "ready"
    assert len(box.sent) == sent and _audits("support_ticket_removed", tid) == 1
    assert d["automatic"] == 1 and owner.get("/api/support/automatic").json()["mails"][0]["ticket"]["id"] == tid
    with SessionLocal() as s:
        assert s.query(AuditLog).filter(AuditLog.action == "support_repair_automatic").count() >= 1
        assert I.repair_once(s) == 0  # once


# ---- 3. a desk that flows: a desk person's reply by mail; the walks ----------------------------------------------------------
def test_t0018_a_staff_reply_by_mail_takes_the_ticket_and_moves_the_status(box, owner):
    """Sashen (an admin) answers a free ticket from Outlook, to the requester: it is his, "With Sashen", no longer waiting for the
    requester; the requester's next reply goes to him."""
    _setup(owner)
    sid, sc = _desk(owner, SASHEN, "Sashen Moodley", role="admin", lead_too=False)
    box.deliver("omar.1109s@client.example", "Laptop assessment", "Hi,\n\nMy laptop at Hyde Park is slow and the battery is weak. Can I get an assessment?\n\nOmar",
                name="Omar Hassan", store=BOX, conversation="C1109-laptop")
    assert owner.post("/api/mailer/read", json={"store": BOX}).status_code == 200
    t = ticket("omar.1109s@client.example")
    steps = [(t.status, t.assigned_to_id)]
    assert t.assigned_to_id is None
    sent = len(to(box, "omar.1109s@client.example"))
    box.deliver(SASHEN, "RE: Laptop assessment", "Reflex conducts laptop assessments. Kindly log this directly with the Reflex team.\n\nSashen", name="Sashen Moodley",
                store=BOX, conversation="C1109-laptop", recipients=["omar.1109s@client.example", BOX])
    assert owner.post("/api/mailer/read", json={"store": BOX}).status_code == 200
    t = ticket(tid=t.id)
    steps.append((t.status, t.assigned_to_id))
    assert t.assigned_to_id == sid and t.status == "discussing" and t.accepted_at is not None
    assert len(to(box, "omar.1109s@client.example")) == sent  # his mail went to Omar already: not passed on again
    v = _thread(owner, t.id)
    assert v["word"] == "With Sashen" and any(m["who"] == "staff" and m["name"] == "Sashen Moodley" and "Reflex team" in m["text"] for m in v["thread"])
    assert any(m.get("quiet") and m["text"] == "Taken by Sashen Moodley — replied by mail." for m in v["thread"])
    box.deliver("omar.1109s@client.example", "RE: Laptop assessment", "I logged it with Reflex but they want a CBRE asset number for the laptop. Which number do I give them?",
                name="Omar Hassan", store=BOX, conversation="C1109-laptop")
    assert owner.post("/api/mailer/read", json={"store": BOX}).status_code == 200
    assert [m for m in to(box, SASHEN) if "replied" in m["subject"]]  # the requester's reply goes to Sashen
    assert ticket(tid=t.id).assigned_to_id == sid
    assert steps[0][1] is None


def test_t0028_walk_status_and_lead_after_each_step(box, owner):
    _setup(owner)
    teb, tc = _desk(owner, TEBOGO, "Tebogo Mabaso")
    seen = []

    def step(label):
        v = _thread(owner, tid)
        seen.append((label, v["status"], v["word"], v["assigned_to"]))
        return seen[-1]

    tid = _ready(box, owner, TANUJA, "Z320 Reallocation – Firmenich", name="Tanuja Singh", conv="C1109-t28")
    assert step("mail in")[1:] == ("ready", "Ready to assign", "")
    assign(owner, ticket(tid=tid), teb)
    assert step("assigned")[1:] == ("assigned", "With Tebogo — waiting for them to accept", "Tebogo Mabaso")
    tc.post(f"/api/support/tickets/{tid}/accept")
    assert step("accepted")[1:] == ("assigned", "With Tebogo", "Tebogo Mabaso")
    tc.post(f"/api/support/tickets/{tid}/message", json={"text": "Which journal number?", "to_requester": True})
    assert step("reply out")[1:] == ("discussing", "With Tebogo", "Tebogo Mabaso")
    tc.post(f"/api/support/tickets/{tid}/status", json={"status": "closed", "note": "Reallocated the cost centre."})
    assert step("done")[1:] == ("closed", "Closed", "Tebogo Mabaso")
    box.deliver(TANUJA, f"RE: [T-{tid:04d}] Your ticket is done", "Please see below which comes up when trying to process the journal:", name="Tanuja Singh",
                store=BOX, conversation="C1109-t28", raw=[att("image001.png", flat_png(seed=8))])
    assert owner.post("/api/mailer/read", json={"store": BOX}).status_code == 200
    assert step("reopened by mail")[1:] == ("discussing", "With Tebogo — waiting for them to accept", "Tebogo Mabaso")
    last = [m for m in _thread(owner, tid)["thread"] if m["who"] == "requester"][-1]
    assert [f["name"] for f in last["files"]] == ["image001.png"]  # the screenshot is visible, under her reply
    tc.post(f"/api/support/tickets/{tid}/give-back")
    assert step("given back")[1:] == ("ready", "Ready to assign", "")
    assert owner.post(f"/api/support/tickets/{tid}/remove", json={"tell": False}).status_code == 200
    assert step("removed")[1:3] == ("closed", "Removed")


def test_the_desks_bookkeeping_notes_are_quiet_and_requesters_never_see_notes(box, owner, user):
    _setup(owner)
    lid, lc = _desk(owner, "lebo.1109q@test.local", "Lebo Lead")
    r = user.post("/api/support/tickets", json={"system": "chase", "problem": "other", "description": "The aircon on floor 3 of Sandton Building B is not cooling.",
                                                "where": "Sandton, Building B", "outcome": "Send a technician"})
    tid = r.json()["ticket"]["id"]
    with SessionLocal() as s:
        sap = next(x.id for x in C.systems(s) if x.key == "sap")
    assert owner.post(f"/api/support/tickets/{tid}/system", json={"system_id": sap}).status_code == 200
    assign(owner, ticket(tid=tid), lid)
    assert lc.post(f"/api/support/tickets/{tid}/accept").status_code == 200
    th = _thread(owner, tid)["thread"]
    quiet = [m["text"] for m in th if m.get("quiet")]
    assert any(x.startswith("Accepted by Lebo Lead") for x in quiet) and any("set the system" in x for x in quiet)
    assert not any(m["who"] in ("note", "admin") for m in _thread(user, tid)["thread"])


# ---- 1.0.109 (Edwin, later the same day): a mail joins a ticket only by its tag or its thread, never by its subject -------------
def test_the_same_subject_alone_is_a_new_ticket_a_tag_or_the_thread_is_the_same_ticket(box, owner):
    _setup(owner)
    tid = _ready(box, owner, "omar.1109t@client.example", "Generator Nile Tower T", conv="C1109-subj")
    with SessionLocal() as s:  # resolved
        t = s.get(SupportTicket, tid)
        t.status, t.resolution = "closed", "Fixed."
        s.commit()
    words = "The lift at the Nile Tower stops between floors 3 and 4 since this morning. Please send a technician today."
    deliver(box, owner, "omar.1109t@client.example", "Generator Nile Tower T", words, "Omar Hassan", conv="C1109-other")  # same subject, no tag, no link
    with SessionLocal() as s:
        mine = s.query(SupportTicket).filter(SupportTicket.requester_email == "omar.1109t@client.example").order_by(SupportTicket.id).all()
        assert len(mine) == 2 and mine[0].status == "closed"  # the resolved one stays closed
    n = len(mine)
    deliver(box, owner, "omar.1109t@client.example", f"RE: [T-{tid:04d}] Generator", "It stopped again this morning, the same fault on the weekly test.", "Omar Hassan",
            conv="C1109-x1")  # the tag
    deliver(box, owner, "omar.1109t@client.example", "RE: Generator Nile Tower T", "And it failed once more at noon, the same alarm on the panel.", "Omar Hassan",
            conv="C1109-subj")  # the thread, no tag
    box.deliver("omar.1109t@client.example", "Generator", "One more: the panel shows code E12 when it stops.", name="Omar Hassan", store=BOX, conversation="C1109-x2",
                headers=f"In-Reply-To: {box.inbox[0]['id']}\r\nReferences: <unknown@x> {box.inbox[0]['id']}\r\n")  # In-Reply-To names the first mail
    assert owner.post("/api/mailer/read", json={"store": BOX}).status_code == 200
    with SessionLocal() as s:
        assert s.query(SupportTicket).filter(SupportTicket.requester_email == "omar.1109t@client.example").count() == n
        assert s.query(SupportMail).filter(SupportMail.ticket_id == tid, SupportMail.outcome.in_(("reply", "follow_up"))).count() == 3
    assert I.find_ticket(SessionLocal(), {"id": "<new@x>", "subject": "Generator Nile Tower T", "headers": "", "conversation": ""}) == (None, False)


# ---- 1.0.109: a new problem sent as a reply to an old thread ------------------------------------------------------------------
NEW_WORDS = "Separately, my SAP user is locked and I cannot log on to post any invoices at all. Please unlock it."


def _closed_ticket(box, owner, sender, subject, conv):
    tid = _ready(box, owner, sender, subject, conv=conv)
    with SessionLocal() as s:
        t = s.get(SupportTicket, tid)
        t.status, t.resolution, t.closed_at = "closed", "Serviced the generator.", utcnow()
        s.commit()
    return tid


def test_a_clearly_different_problem_in_an_old_thread_is_not_added_and_the_sender_is_asked_once(box, owner, admin, monkeypatch):
    _setup(owner)
    seen = []
    monkeypatch.setattr(C, "JUDGE", lambda about, words: seen.append((about, words)) or {"different": True, "confidence": "high", "reason": "SAP log-on, not the generator"})
    tid = _closed_ticket(box, owner, "omar.1109n@client.example", "Generator NP", "C1109-np")
    before = len(to(box, "omar.1109n@client.example"))
    deliver(box, owner, "omar.1109n@client.example", f"RE: [T-{tid:04d}] Generator NP", NEW_WORDS + "\n\nFrom: Support\nSent: Monday\n\nYour ticket is done. The generator was serviced.",
            "Omar Hassan", conv="C1109-np")
    assert seen and seen[-1][1].startswith("Separately") and "Your ticket is done" not in seen[-1][1]  # the new words only, never the quoted part
    t = ticket(tid=tid)
    assert t.status == "closed"  # not reopened
    with SessionLocal() as s:
        assert not s.query(SupportMessage).filter(SupportMessage.ticket_id == tid, SupportMessage.who == "requester", SupportMessage.text.contains("SAP user")).count()
        row = s.query(SupportMail).filter(SupportMail.outcome == "new_problem").one()
    mails = to(box, "omar.1109n@client.example")
    assert len(mails) == before + 1  # once, through the outbox
    assert f"this looks like a new problem, not part of T-{tid:04d}" in mails[-1]["body"] and BOX in mails[-1]["body"] and "as a new e-mail" in mails[-1]["body"]
    v = _thread(owner, tid)
    note = next(m for m in v["thread"] if m["text"].startswith("New problem sent in this thread"))
    assert note.get("quiet") and "SAP log-on, not the generator" in note["text"] and note["new_problem"] == row.id
    lid, lc = _desk(owner, "lebo.1109n@test.local", "Lebo Lead")
    assert lc.post(f"/api/support/new-problem/{row.id}").status_code == 403  # the admins only
    sent = len(box.sent)
    r = admin.post(f"/api/support/new-problem/{row.id}")
    assert r.status_code == 200, r.text
    nt = ticket(tid=r.json()["ticket"]["id"])
    assert nt.id != tid and nt.status == "ready" and "SAP user" in nt.description and nt.requester_email == "omar.1109n@client.example"
    assert len(box.sent) == sent  # nobody is told
    assert admin.post(f"/api/support/new-problem/{row.id}").status_code == 400  # once


def test_same_unsure_or_no_ai_is_handled_as_before_and_the_desk_is_never_judged(box, owner, monkeypatch):
    _setup(owner)
    for i, answer in enumerate(({"different": False, "confidence": "high", "reason": "the same generator"},
                                {"different": True, "confidence": "medium", "reason": "maybe"}, None)):
        monkeypatch.setattr(C, "JUDGE", lambda about, words, a=answer: a)
        tid = _closed_ticket(box, owner, f"omar.1109u{i}@client.example", f"Generator U{i}", f"C1109-u{i}")
        deliver(box, owner, f"omar.1109u{i}@client.example", f"RE: [T-{tid:04d}] Generator U{i}", NEW_WORDS, "Omar Hassan", conv=f"C1109-u{i}")
        assert ticket(tid=tid).status != "closed", answer  # reopened, as before
    monkeypatch.setattr(C, "JUDGE", None)  # no AI box (stub): "off" — as before
    tid = _closed_ticket(box, owner, "omar.1109off@client.example", "Generator OFF", "C1109-off")
    deliver(box, owner, "omar.1109off@client.example", f"RE: [T-{tid:04d}] Generator OFF", NEW_WORDS, "Omar Hassan", conv="C1109-off")
    assert ticket(tid=tid).status != "closed"
    with SessionLocal() as s:
        assert C.judge_new_problem(s, s.get(SupportTicket, tid), NEW_WORDS) == ("off", "")
    calls = []
    monkeypatch.setattr(C, "JUDGE", lambda about, words: calls.append(words) or {"different": True, "confidence": "high", "reason": "x"})
    lid, lc = _desk(owner, "lebo.1109j@test.local", "Lebo Lead")
    tid = _ready(box, owner, "omar.1109j@client.example", "Generator J", conv="C1109-j")
    assign(owner, ticket(tid=tid), lid)
    deliver(box, owner, "lebo.1109j@test.local", f"RE: [T-{tid:04d}] Assigned to you", NEW_WORDS, "Lebo Lead", conv="C1109-j")
    assert calls == []  # the desk writing in the thread is never judged


# ---- 1.0.109: the ticket list's filters -----------------------------------------------------------------------------------
def test_the_list_filters_by_person_and_status_and_staff_only_see_theirs(box, owner):
    _setup(owner)
    a_id, a = _desk(owner, "anele.1109f@test.local", "Anele Lead")
    b_id, b = _desk(owner, "merna.1109f@test.local", "Merna Lead")
    t1 = _ready(box, owner, "omar.1109f1@client.example", "Pump F1")
    t2 = _ready(box, owner, "omar.1109f2@client.example", "Pump F2")
    t3 = _ready(box, owner, "omar.1109f3@client.example", "Pump F3")
    assign(owner, ticket(tid=t1), a_id)
    assign(owner, ticket(tid=t2), b_id)
    assert b.post(f"/api/support/tickets/{t2}/accept").status_code == 200
    q = lambda c, qs: [x["id"] for x in c.get("/api/support/queue?" + qs).json()["tickets"]]  # noqa: E731
    assert q(owner, f"status=all&lead={b_id}") == [t2]  # "just Merna's tickets"
    assert q(owner, "status=not_accepted") == [t1] and q(owner, "status=accepted") == [t2] and set(q(owner, "status=free")) == {t3}
    assert owner.post(f"/api/support/tickets/{t3}/not-relevant", json={"reason": "x"}).status_code == 200
    assert q(owner, "status=not_relevant") == [t3] and q(owner, "status=done") == []
    assert b.post(f"/api/support/tickets/{t2}/status", json={"status": "closed", "note": "Pump fixed."}).status_code == 200
    assert q(owner, "status=done") == [t2]
    names = {x["name"] for x in owner.get("/api/support/queue").json()["leads"]}
    assert {"Anele Lead", "Merna Lead"} <= names
    # the rest of the desk: their own and the free ones, whatever person they ask for; no picker
    r = a.get(f"/api/support/queue?status=all&lead={b_id}").json()
    assert t2 not in [x["id"] for x in r["tickets"]] and r["leads"] == [] and r["own_only"]
