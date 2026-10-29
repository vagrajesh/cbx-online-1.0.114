import pytest
"""1.0.109 (review of round 2, 23 Oct): a regression test for each finding — the follow-up clock stops whenever the requester writes;
a hostile .msg can't blow up the preview; files under a note never reach the requester; the one-time robot repair and the
acknowledgement rule need two signals and never take a person's forward; the list's status filter is never injected; the editor's
pictures are refused by size before decoding; a new problem's files move with it; and the minor ones (restore keeps who solved it,
Ask AXL never reads the admins' lines, a dismissed robot mail's chain, quick find's highlighting). Everything made up."""
import io
import json
import re
import struct
import subprocess
import time
from datetime import timedelta, timezone
from pathlib import Path

from cbx.db import SessionLocal, utcnow
from cbx.models import SupportMail, SupportTicket
from cbx.support import core as C
from cbx.support import followup as FU
from cbx.support import inbox as I

from .test_support_1098 import BOX, _setup, assign, box, deliver, ticket, to  # noqa: F401
from .test_support_flow_1109 import ACK, _desk, _ready, _thread

REQ, LEAD1, LEAD2 = "naledi.rv@client.example", "merna.rv@test.local", "tebogo.rv@test.local"
UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[1]


def _wait(box, owner, conv):
    l1, lc = _desk(owner, LEAD1, "Merna Pillay")
    tid = _ready(box, owner, REQ, "Generator Nile Tower " + conv, conv=conv)
    assign(owner, ticket(tid=tid), l1)
    assert lc.post(f"/api/support/tickets/{tid}/message", json={"text": "Which generator is it - basement or roof?", "to_requester": True, "wait": True}).status_code == 200
    assert ticket(tid=tid).waiting_since is not None
    return tid


# ---- 1. the follow-up clock stops whenever the requester writes ------------------------------------------------------------
def test_a_thank_you_answer_stops_the_follow_up_clock(box, owner):
    _setup(owner)
    tid = _wait(box, owner, "RV-thx")
    deliver(box, owner, REQ, f"RE: [T-{tid:04d}] A message", "Thank you. It is the basement one.", "Naledi Dube", conv="RV-thx")
    assert ticket(tid=tid).waiting_since is None
    n0 = len(to(box, REQ))
    with SessionLocal() as s:
        assert FU.run(s, now=utcnow().replace(tzinfo=UTC) + timedelta(days=10))["followed"] == 0
    assert len(to(box, REQ)) == n0


def test_a_see_attached_answer_stops_the_follow_up_clock(box, owner):
    _setup(owner)
    tid = _wait(box, owner, "RV-ptr")
    deliver(box, owner, REQ, f"RE: [T-{tid:04d}] A message", "Please see attached as requested.", "Naledi Dube", conv="RV-ptr",
            files=[("basement.pdf", b"%PDF-1.4 basement generator")])
    assert ticket(tid=tid).waiting_since is None


# ---- 2. a hostile .msg -------------------------------------------------------------------------------------------------------
def _msg_bomb() -> bytes:
    h = bytearray(512)
    h[0:8] = bytes.fromhex("D0CF11E0A1B11AE1")
    struct.pack_into("<HHHHH", h, 0x18, 0x3E, 3, 0xFFFE, 9, 6)
    struct.pack_into("<I", h, 0x2C, 1)
    struct.pack_into("<I", h, 0x30, 0xFFFFFFFE)
    struct.pack_into("<I", h, 0x38, 4096)
    struct.pack_into("<I", h, 0x3C, 0xFFFFFFFE)
    struct.pack_into("<I", h, 0x40, 0)
    struct.pack_into("<I", h, 0x44, 0)  # the first DIFAT sector is 0 …
    struct.pack_into("<I", h, 0x48, 0xFFFFFF00)  # … "many" of them
    struct.pack_into("<109I", h, 0x4C, *([1] * 109))
    return bytes(h) + struct.pack("<128I", *([1] * 127 + [0])) + struct.pack("<128I", *([0x7FFFFFF0 + i for i in range(128)]))


def test_a_self_referencing_msg_is_read_in_a_moment_and_gives_nothing():
    from cbx.support import msgread

    t0 = time.time()
    assert msgread.read(_msg_bomb()) == {}
    assert time.time() - t0 < 2
    bad = bytearray(_msg_bomb())
    struct.pack_into("<H", bad, 0x1E, 40)  # a sector shift no compound file has
    assert msgread.read(bytes(bad)) == {}


# ---- 3. files under a note never reach the requester ------------------------------------------------------------------------
def test_a_desk_persons_mail_on_the_chain_keeps_its_files_from_the_requester(box, owner):
    _setup(owner)
    l1, _ = _desk(owner, LEAD1, "Merna Pillay")
    _desk(owner, LEAD2, "Tebogo Mokoena")
    tid = _ready(box, owner, REQ, "Generator chain test", conv="RV-chain")
    assign(owner, ticket(tid=tid), l1)
    deliver(box, owner, LEAD2, "RE: Generator chain test", "Internal: see the attached quote from our supplier.", "Tebogo Mokoena", conv="RV-chain",
            files=[("supplier-quote.pdf", b"%PDF-1.4 internal quote")])
    with SessionLocal() as s:
        t = s.get(SupportTicket, tid)
        assert any(f["name"] == "supplier-quote.pdf" for f in t.files)
        assert "supplier-quote.pdf" not in [f["name"] for f in C.files_seen(s, t, False)]


def test_a_later_mail_on_a_removed_ticket_from_someone_else_keeps_its_files_from_the_requester(box, owner):
    _setup(owner)
    tid = _ready(box, owner, REQ, "Generator removed chain", conv="RV-rm")
    assert owner.post(f"/api/support/tickets/{tid}/remove", json={"tell": False}).status_code == 200
    deliver(box, owner, "colleague.rv@client.example", "RE: Generator removed chain", "Adding the site plan for you.", "Colleague", conv="RV-rm",
            files=[("site-plan.pdf", b"%PDF-1.4 plan")])
    with SessionLocal() as s:
        t = s.get(SupportTicket, tid)
        assert any(f["name"] == "site-plan.pdf" for f in t.files)
        assert "site-plan.pdf" not in [f["name"] for f in C.files_seen(s, t, False)]


# ---- 4. the robot rule needs two signals; the repair never takes a person's forward -----------------------------------------
def test_the_one_time_repair_leaves_a_forwarded_acknowledgement_alone(box, owner):
    from cbx import state

    _setup(owner)
    deliver(box, owner, REQ, "FW: Your call 4471 has been logged",
            "Hi team, Reflex logged this but nobody came for 3 days. The laptop screen is cracked, please escalate with them urgently.", "Naledi Dube", conv="RV-fw")
    t = ticket(REQ)
    assert t is not None and not C.is_removed(t)
    with SessionLocal() as s:
        state.set(s, I.REPAIR_KEY, "")
        s.commit()
        I.repair_once(s)
    assert not C.is_removed(ticket(tid=t.id))


def test_one_phrase_alone_never_makes_a_person_a_robot(box, owner):
    _setup(owner)
    m = lambda body, subject="Help": {"sender": "x@client.example", "subject": subject, "headers": "", "body": body}  # noqa: E731
    assert I.robot(m("Please do not reply all on this one. The printer on floor 2 is broken, please send someone.")) == ""
    assert I.robot(m("Our reference number INV-20441 is still unpaid, please check the payment run.")) == ""
    assert I.robot(m("My request has been logged with Reflex but nobody came; please chase them.")) == ""
    assert I.robot(m(ACK))  # T-0018: the acknowledgement and its ticket id
    assert I.robot(m("Thank you. This is an automated message.", subject="Your ticket TCK-90210 has been received"))
    deliver(box, owner, REQ, "Printer broken", "Please do not reply all. The printer on floor 2 jams on every page; reference number PR-55120. Please fix it.",
            "Naledi Dube", conv="RV-two")
    t = ticket(REQ)
    assert t is not None and not C.is_removed(t) and t.status != "closed"


# ---- 5. the list's status filter is never injected --------------------------------------------------------------------------
def _js_fn(path: str, name: str, end: str) -> str:
    src = (ROOT / path).read_text(encoding="utf-8")
    i = src.index(name)
    return src[i:src.index(end, i)]


from ._node import run_module  # 1.0.110: any Node, or skipped without one


def _node(code: str) -> str:
    return run_module(code)


ESC = "const esc = (s) => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;').replace(/'/g,'&#39;');"


def test_an_unknown_status_in_the_address_is_dropped_and_options_are_escaped():
    src = (ROOT / "web/views/support.js").read_text(encoding="utf-8")
    consts = "\n".join(re.findall(r"(?ms)^const (?:LIST_STATUS|OLD_STATUS|REMOVED_OPT) = .*?;$", src))
    fn = _js_fn("web/views/support.js", "function cleanStatus", "}  // cleanStatus end") + "}"
    out = _node(consts + "\n" + fn + "\nconsole.log(JSON.stringify(['open','escalated','pending','removed','\"><img src=x onerror=alert(1)>','constructor'].map(cleanStatus)))")
    assert json.loads(out) == ["open", "escalated", "pending", "removed", "open", "open"]
    assert '<option value="${esc(v)}"' in src


# ---- 6. the editor's pictures: refused by size before decoding ---------------------------------------------------------------
def test_a_huge_picture_is_refused_before_it_is_decoded_and_pil_is_left_alone():
    from PIL import Image

    from cbx.support import rich as R

    before = Image.MAX_IMAGE_PIXELS
    img = Image.new("1", (7000, 7000), 1)
    b = io.BytesIO()
    img.save(b, "PNG")
    try:
        R.picture("x.png", b.getvalue())
        raise AssertionError("a 49 MP picture was taken")
    except ValueError as e:
        assert "too large" in str(e)
    assert Image.MAX_IMAGE_PIXELS == before
    frames = [Image.new("P", (20, 20), i % 200) for i in range(150)]
    g = io.BytesIO()
    frames[0].save(g, "GIF", save_all=True, append_images=frames[1:], duration=20)
    name, out = R.picture("anim.gif", g.getvalue())
    assert getattr(Image.open(io.BytesIO(out)), "n_frames", 1) == 1  # a long animation keeps its first frame


# ---- 7. a new problem's files move with it -----------------------------------------------------------------------------------
def test_a_new_problems_files_are_kept_and_move_to_its_own_ticket(box, owner, admin, monkeypatch):
    _setup(owner)
    tid = _ready(box, owner, REQ, "Generator Nile Tower", conv="RV-np")
    with SessionLocal() as s:
        t = s.get(SupportTicket, tid)
        t.status, t.resolution, t.closed_at = "closed", "Serviced.", utcnow()
        s.commit()
    monkeypatch.setattr(C, "JUDGE", lambda about, words: {"different": True, "confidence": "high", "reason": "SAP log-on"})
    deliver(box, owner, REQ, f"RE: [T-{tid:04d}] Your ticket is done", "Separately, my SAP user is locked and I cannot post invoices. Error attached.", "Naledi Dube",
            conv="RV-np", files=[("sap-error.pdf", b"%PDF-1.4 locked user")])
    with SessionLocal() as s:
        old = s.get(SupportTicket, tid)
        assert [f["name"] for f in old.files if f.get("staff_mail")] == ["sap-error.pdf"]  # kept, for the desk only
        assert "sap-error.pdf" not in [f["name"] for f in C.files_seen(s, old, False)]
        mid = s.query(SupportMail).filter(SupportMail.outcome == "new_problem").one().id
    r = admin.post(f"/api/support/new-problem/{mid}")
    assert r.status_code == 200, r.text
    nid = r.json()["ticket"]["id"]
    new = _thread(admin, nid)
    first = next(m for m in new["thread"] if m["who"] == "requester")
    assert [f["name"] for f in first["files"]] == ["sap-error.pdf"]


# ---- the minor ones ----------------------------------------------------------------------------------------------------------
def test_restore_of_a_closed_ticket_keeps_who_solved_it(box, owner):
    _setup(owner)
    l1, lc = _desk(owner, LEAD1, "Merna Pillay")
    tid = _ready(box, owner, REQ, "Generator restore", conv="RV-rs")
    assign(owner, ticket(tid=tid), l1)
    assert lc.post(f"/api/support/tickets/{tid}/status", json={"status": "closed", "note": "Serviced the generator."}).status_code == 200
    assert owner.post(f"/api/support/tickets/{tid}/remove", json={"tell": False}).status_code == 200
    assert ticket(tid=tid).assigned_to_id is None
    assert owner.post(f"/api/support/tickets/{tid}/restore").status_code == 200
    t = ticket(tid=tid)
    assert t.status == "closed" and t.assigned_to_id == l1


def test_ask_axl_never_reads_the_admins_lines(box, owner):
    from cbx.support import askaxl

    _setup(owner)
    tid = _ready(box, owner, REQ, "Generator askaxl", conv="RV-ax")
    with SessionLocal() as s:
        t = s.get(SupportTicket, tid)
        C.add_message(s, t, "admin", "ADMIN-ONLY: mail held by the loop guard", name="Support desk")
        C.add_message(s, t, "note", "Desk note: checked the generator log", name="Support desk")
        s.commit()
        ctx = askaxl.context(s, s.get(SupportTicket, tid))
    assert "ADMIN-ONLY" not in ctx and "checked the generator log" in ctx


def test_a_dismissed_robot_mails_chain_never_turns_a_staff_reply_into_a_ticket(box, owner):
    _setup(owner)
    _desk(owner, LEAD1, "Merna Pillay")
    deliver(box, owner, "support@reflex.example", "Your ticket with ID 3901517 has been acknowledged", ACK, "REFLEX Support", conv="RV-robot")
    with SessionLocal() as s:
        row = s.query(SupportMail).filter(SupportMail.conversation == "RV-robot").one()
        assert row.outcome == "ignored:automatic"
        n = s.query(SupportTicket).count()
    assert owner.post(f"/api/support/automatic/{row.id}/dismiss").status_code == 200
    deliver(box, owner, LEAD1, "RE: Your ticket with ID 3901517 has been acknowledged", "Reflex conducts laptop assessments. Kindly log this directly with them.",
            "Merna Pillay", conv="RV-robot")
    with SessionLocal() as s:
        assert s.query(SupportTicket).count() == n
        assert s.query(SupportMail).filter(SupportMail.conversation == "RV-robot", SupportMail.outcome == "ignored:auto_thread").count() == 1


def test_quick_find_marks_words_without_splitting_html_entities():
    fn = _js_fn("web/views/support-ui.js", "export function markWords", "}  // markWords end").replace("export function", "function") + "}"
    out = _node(ESC + "\n" + fn + "\nconsole.log(markWords('Tom & Jerry <b>', ['amp', 'jerry']));")
    assert out == "Tom &amp; <mark>Jerry</mark> &lt;b&gt;"
    assert _node(ESC + "\n" + fn + "\nconsole.log(markWords('a < b', ['lt']));") == "a &lt; b"

