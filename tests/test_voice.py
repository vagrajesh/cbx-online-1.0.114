"""1.0.64: talk with C-EXA, and her calls inside CBX. Ears and voice are stand-ins in the suite (model mode
stub): listening returns what the test put after b"say:", speaking returns a short silent WAV."""
from datetime import timedelta
from types import SimpleNamespace

import pytest

from cbx.cexa import loop as L
from cbx.cexa import tools as T
from cbx.config import settings
from cbx.db import utcnow
from cbx.models import AgentWork, CexaMessage, User, VoiceCall
from cbx.security import ratelimit
from cbx.voice import calls as VC
from cbx.voice import engine as E


@pytest.fixture()
def calls(db, user):
    ratelimit.reset_all()
    db.query(VoiceCall).delete()
    db.commit()
    yield db
    db.rollback()
    db.query(VoiceCall).delete()
    db.query(AgentWork).filter(AgentWork.kind.in_(("call", "task")), AgentWork.title.like("VOICE %")).delete(synchronize_session=False)
    db.commit()


def _u(db, email) -> User:
    return db.query(User).filter(User.email == email).one()


def test_listen_and_say_stand_ins(owner):
    r = owner.post("/api/voice/listen", files={"audio": ("a.webm", b"say:what is due this week", "audio/webm")}, data={"lang": "en"})
    assert r.status_code == 200 and r.json()["text"] == "what is due this week"
    r = owner.post("/api/voice/say", json={"text": "Hello **there**", "lang": "en"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.content[:4] == b"RIFF"
    assert owner.post("/api/voice/say", json={"text": "مرحبا", "lang": "ar"}).status_code == 204  # the browser says Arabic
    s = owner.get("/api/voice/status").json()
    assert s["on"] and "calls" in s


def test_say_strips_links_and_markdown():
    assert E.plain("See [the grid](#/x) at https://x.y/z `code` **now**") == "See the grid at a link code now"


def test_setup_is_the_owner_s_alone(user, monkeypatch):
    ran = []
    monkeypatch.setattr(E, "setup", lambda: ran.append(1))
    assert user.post("/api/voice/setup").status_code == 403
    assert ran == []


def test_a_call_rings_is_answered_and_the_note_goes_back(calls, owner, user, db):
    me, th = _u(db, "owner@test.local"), _u(db, "thandi@test.local")
    c = VC.start(db, th, me, "Check the Telkom run", ["Did the Telkom run go out?", "Any rejects?"])
    db.commit()
    assert owner.post(f"/api/voice/calls/{c.id}/answer").status_code == 403  # only the person called answers
    ring = user.get("/api/voice/ringing").json()["call"]
    assert ring["id"] == c.id and ring["by"] == me.name
    r = user.post(f"/api/voice/calls/{c.id}/answer").json()
    assert "Did the Telkom run go out?" in r["say"] and "Thandi" in r["say"]
    r = user.post(f"/api/voice/calls/{c.id}/listen-less", json={})  # no such thing
    assert r.status_code in (404, 405)
    r = user.post(f"/api/voice/calls/{c.id}/turn", json={"text": "Yes, on Monday"}).json()
    assert not r["done"] and "Any rejects?" in r["say"]
    r = user.post(f"/api/voice/calls/{c.id}/turn", json={"text": "Two, both fixed"}).json()
    assert r["done"]
    full = owner.get(f"/api/voice/calls/{c.id}").json()
    assert full["status"] == "done" and full["answers"]["Any rejects?"] == "Two, both fixed"
    assert [t["who"] for t in full["transcript"]][:2] == ["cexa", "them"]
    db.expire_all()
    m = db.query(CexaMessage).filter(CexaMessage.user_id == me.id, CexaMessage.kind == "call").order_by(CexaMessage.id.desc()).first()
    assert "Did the Telkom run go out? → Yes, on Monday" in m.text
    assert user.post(f"/api/voice/calls/{c.id}/turn", json={"text": "hello?"}).status_code == 409


def test_someone_else_cannot_see_a_call(calls, owner, admin, user, db):
    c = VC.start(db, _u(db, "thandi@test.local"), _u(db, "owner@test.local"), "A quick one", ["Is it done?"])
    db.commit()
    assert admin.get(f"/api/voice/calls/{c.id}").status_code == 404
    assert admin.get("/api/voice/ringing").json()["call"] is None


def test_declined_missed_and_bye(calls, user, db):
    me, th = _u(db, "owner@test.local"), _u(db, "thandi@test.local")
    c = VC.start(db, th, me, "One", ["Q?"])
    db.commit()
    with pytest.raises(ValueError):
        VC.start(db, th, me, "Two", ["Q?"])  # one call at a time
    assert user.post(f"/api/voice/calls/{c.id}/decline").json()["status"] == "declined"
    c2 = VC.start(db, th, me, "Three", ["Q?"])
    c2.created_at = utcnow() - timedelta(seconds=settings.call_ring_seconds + 5)
    db.commit()
    assert user.get("/api/voice/ringing").json()["call"] is None
    db.expire_all()
    assert db.get(VoiceCall, c2.id).status == "missed"
    c3 = VC.start(db, th, me, "Four", ["Q?", "R?"])
    db.commit()
    user.post(f"/api/voice/calls/{c3.id}/answer")
    r = user.post(f"/api/voice/calls/{c3.id}/turn", json={"text": "sorry, call me back later"}).json()
    assert r["done"]
    db.expire_all()
    assert "(not answered)" in db.get(VoiceCall, c3.id).summary


def test_she_never_asks_for_secrets(calls, db, user):
    with pytest.raises(ValueError):
        VC.start(db, _u(db, "thandi@test.local"), _u(db, "owner@test.local"), "Access", ["What is your password?"])
    with pytest.raises(ValueError):
        VC.start(db, _u(db, "thandi@test.local"), _u(db, "owner@test.local"), "Pay", ["Read me the card number"])


def test_chat_can_ring_someone_for_an_admin_only(calls, db, user):
    me, th = _u(db, "owner@test.local"), _u(db, "thandi@test.local")
    out = T.run(db, me, "call_person", {"to": "thandi@test.local", "purpose": "Telkom run", "questions": "Did it go out? | Any rejects?"}, None)
    assert out.startswith("Ringing Thandi Mokoena"), out
    c = db.query(VoiceCall).filter(VoiceCall.to_user_id == th.id).one()
    assert c.questions == ["Did it go out?", "Any rejects?"] and c.started_by_id == me.id
    db.commit()
    refused = T.run(db, th, "call_person", {"to": "owner", "purpose": "x", "questions": "y?"}, None)
    assert not refused.startswith("Ringing")


def test_an_agent_suggests_a_call_the_owner_rings_and_the_answer_reopens_the_work(calls, db, user):
    me, th = _u(db, "owner@test.local"), _u(db, "thandi@test.local")
    task = AgentWork(kind="task", title="VOICE find out about Telkom", status="done", from_label="test", source="owner")
    db.add(task)
    db.flush()
    ctx = SimpleNamespace(db=db, agent=SimpleNamespace(id=None, name="Analyst-1"), work=task)
    fn = L.OWN["suggest_call"]["fn"]
    assert "never" in fn(ctx, to="Thandi", purpose="x", questions="your PIN?")
    assert fn(ctx, to="thandi@test.local", purpose="VOICE Telkom run", questions="Did it go out?").startswith("Suggested")
    p = db.query(AgentWork).filter(AgentWork.kind == "call", AgentWork.parent_id == task.id).one()
    assert p.options == ["Call", "Not now"]
    db.commit()
    assert "suggest_call" in L.ROLE_HANDS["lead"] and "suggest_call" not in L.ROLE_HANDS["developer"]
    L.answer(db, p, me, "Call")
    db.commit()
    assert p.status == "done"
    c = db.get(VoiceCall, p.proposal["call_id"])
    assert c.work_id == task.id and c.started_by_id == me.id and c.to_user_id == th.id
    VC.answer(db, c, th)
    VC.turn(db, c, th, "Yes it did")
    db.commit()
    db.expire_all()
    w = db.get(AgentWork, task.id)
    assert w.status == "open" and "Did it go out? → Yes it did" in w.transcript[-1]["content"]


def test_voice_calls_are_words_only():
    cols = set(VoiceCall.__table__.columns.keys())
    assert not any(k in cols for k in ("audio", "sound", "recording"))


# ---- 1.0.68: her voices, and the studio voice on the graphics card -----------------------------------

def test_the_owner_picks_her_voice_everyone_hears_it(owner, user, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    from cbx.voice import voices as V

    d = owner.get("/api/voice/voices").json()
    assert d["choice"] == "quick:bf_emma"
    ids = [v["id"] for v in d["voices"]]
    assert "studio:charlotte" in ids and "quick:bm_george" in ids
    assert all(V.CATALOG[i][2] in ("British", "American") for i in ids)
    assert user.get("/api/voice/voices").status_code == 403
    assert user.post("/api/voice/choice", json={"id": "quick:bf_lily"}).status_code == 403
    assert owner.post("/api/voice/choice", json={"id": "nope"}).status_code == 400
    assert owner.post("/api/voice/choice", json={"id": "quick:charlotte"}).json()["choice"] == "quick:charlotte"
    assert V.chosen() == "quick:charlotte"
    r = owner.post("/api/voice/sample", json={"id": "quick:bm_george"})
    assert r.status_code == 200 and r.content[:4] == b"RIFF"
    assert "Charlotte" in owner.get("/api/voice/status").json()["voice"]["voice"]


def test_a_mixed_voice_is_a_blend_of_kokoro_styles():
    import numpy as np

    from cbx.voice import voices as V

    class K:
        def get_voice_style(self, n):
            return {"bf_emma": np.ones((2, 3)), "bf_isabella": np.zeros((2, 3))}[n]

    st = V.style("quick:charlotte", K())
    assert st.shape == (2, 3) and abs(float(st[0][0]) - 0.6) < 1e-6
    assert V.style("quick:bf_emma", K()) == "bf_emma"
    assert V.kokoro_twin("studio:harriet") == "quick:harriet"


def test_the_studio_voice_runs_in_its_own_process_on_localhost(tmp_path, monkeypatch):
    import socket

    from cbx.voice import studio as ST
    from cbx.voice import voices as V

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    free = s.getsockname()[1]
    s.close()
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "voice_studio_port", free)
    monkeypatch.setenv("CBX_STUDIO_FAKE", "1")
    try:
        assert ST.start()
        h = ST.health()
        assert h["ok"] and h["device"] == "fake"
        V.choose("studio:charlotte")
        E._cache.clear()
        wav = E.speak("Good morning, Edwin.", "en")
        assert wav[:4] == b"RIFF" and ST.print_path("studio:charlotte").is_file()  # the print was made by Kokoro
        # the service only answers with the token, and only speaks prints from its own folder
        import httpx

        assert httpx.get(f"http://127.0.0.1:{free}/health", timeout=2).status_code == 403
        r = httpx.post(f"http://127.0.0.1:{free}/speak", json={"text": "hi", "print": "../../secret.wav"}, headers={"X-Token": ST.token()}, timeout=5)
        assert r.status_code == 400
    finally:
        ST.stop()
    assert ST.health() is None
    # stopped: her quick twin says it instead
    monkeypatch.delenv("CBX_STUDIO_FAKE")
    E._cache.clear()
    assert E.speak("Still here.", "en")[:4] == b"RIFF"
