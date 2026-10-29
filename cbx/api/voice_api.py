"""Voice (1.0.64): talk with C-EXA hands-free, and her calls inside CBX. Ears and voice run on this server
(cbx/voice/engine.py); only words are kept, never sound.

    GET  /api/voice/status                  is voice ready (the owner also sees the set-up log and recent calls)
    POST /api/voice/setup                   owner, a person: install Whisper + Kokoro and fetch Kokoro's files
    POST /api/voice/listen                  audio (≤10 MB) → {text, language}
    POST /api/voice/say                     {text, lang} → audio/wav; 204 = let the browser say it
    GET  /api/voice/ringing                 a call ringing for me (the app looks every few seconds)
    GET  /api/voice/calls/{id}              the person called, whoever started it, or the owner
    POST /api/voice/calls/{id}/answer       → {say}            the person called
    POST /api/voice/calls/{id}/turn         {text} → {say, done}
    POST /api/voice/calls/{id}/decline | /hangup
Her voices (1.0.68, the owner):
    GET  /api/voice/voices           every voice, which one she uses, the studio voice's state
    POST /api/voice/choice {id}      she uses this voice (everyone hears it)
    POST /api/voice/sample {id}      audio/wav: a sentence in that voice, to hear it before choosing
    POST /api/voice/studio/setup     install the studio voice (graphics card) in its own Python
    POST /api/voice/studio/start     start it (CBX also starts it when CBX starts)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import jobs
from ..audit import log
from ..db import get_db
from ..models import User, VoiceCall
from ..security import ratelimit as RL
from ..security.deps import current_user, require_human, require_owner
from ..voice import calls as VC
from ..voice import engine as E
from ..voice import studio as ST
from ..voice import voices as V

router = APIRouter(prefix="/api/voice", tags=["voice"])


class SayIn(BaseModel):
    text: str
    lang: str = "en"


class VoiceIn(BaseModel):
    id: str


class TurnIn(BaseModel):
    text: str = ""


def _limit(me: User, what: str, n: int) -> None:
    try:
        RL.check(f"voice-{what}:{me.id}", limit=n, window=60, what="voice requests")
    except RL.TooMany as e:
        raise HTTPException(429, e.message, headers={"Retry-After": str(e.retry_after)})


def _call(db: Session, cid: int) -> VoiceCall:
    c = db.get(VoiceCall, cid)
    if c is None:
        raise HTTPException(404, "No such call.")
    return c


def _do(fn, *a):
    try:
        return fn(*a)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.get("/status")
def status(db: Session = Depends(get_db), me: User = Depends(current_user)):
    s = E.status()
    if not me.is_owner:
        s.pop("setup_log", None)
        return s
    rows = db.query(VoiceCall).order_by(VoiceCall.id.desc()).limit(10).all()
    s["calls"] = [VC.out(db, c) for c in rows]
    s["choice"] = V.chosen()
    s["studio"] = ST.status()
    return s


@router.post("/setup")
def setup(db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    if E.SETUP["running"]:
        return {"ok": True, "running": True}
    log(db, "voice_setup", actor=me, target="voice", detail="install Whisper + Kokoro, fetch Kokoro's files")
    jobs.submit(E.setup)
    return {"ok": True, "running": True}


@router.post("/listen")
def listen(audio: UploadFile = File(...), lang: str = Form(""), me: User = Depends(require_human)):
    _limit(me, "listen", 40)
    data = audio.file.read(E.MAX_AUDIO + 1)
    try:
        return E.transcribe(data, lang[:2])
    except E.VoiceError as e:
        raise HTTPException(409, str(e))


@router.post("/say")
def say(body: SayIn, me: User = Depends(current_user)):
    _limit(me, "say", 240)  # her answers come in sentences
    try:
        wav = E.speak(body.text, body.lang)
    except E.VoiceError as e:
        return Response(status_code=204, headers={"X-Voice-Why": str(e)[:200]})
    if wav is None:
        return Response(status_code=204)
    return Response(wav, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@router.get("/ringing")
def ringing(db: Session = Depends(get_db), me: User = Depends(current_user)):
    if me.is_axl:
        return {"call": None}
    c = VC.ringing_for(db, me)
    return {"call": VC.out(db, c) if c else None}


@router.get("/calls/{cid}")
def one(cid: int, db: Session = Depends(get_db), me: User = Depends(current_user)):
    c = _call(db, cid)
    if me.id not in (c.to_user_id, c.started_by_id) and not me.is_owner:
        raise HTTPException(404, "No such call.")
    return VC.out(db, c, full=True)


@router.post("/calls/{cid}/answer")
def answer(cid: int, db: Session = Depends(get_db), me: User = Depends(require_human)):
    c = _call(db, cid)
    VC.expire(db)
    said = _do(VC.answer, db, c, me)
    return {"say": said, "call": VC.out(db, c)}


@router.post("/calls/{cid}/turn")
def turn(cid: int, body: TurnIn, db: Session = Depends(get_db), me: User = Depends(require_human)):
    _limit(me, "turn", 40)
    c = _call(db, cid)
    said, done = _do(VC.turn, db, c, me, body.text)
    return {"say": said, "done": done, "call": VC.out(db, c)}


@router.post("/calls/{cid}/decline")
def decline(cid: int, db: Session = Depends(get_db), me: User = Depends(require_human)):
    c = _call(db, cid)
    _do(VC.decline, db, c, me)
    return {"ok": True, "status": c.status}


@router.post("/calls/{cid}/hangup")
def hangup(cid: int, db: Session = Depends(get_db), me: User = Depends(require_human)):
    c = _call(db, cid)
    _do(VC.hang_up, db, c, me)
    return {"ok": True, "status": c.status}


# ---- her voices (the owner) ---------------------------------------------------------------------------

@router.get("/voices")
def list_voices(me: User = Depends(require_owner)):
    st = ST.status()
    quick = E.status()["voice"]["ready"]
    return {"choice": V.chosen(), "studio": st,
            "voices": [V.out(k, ready=(st["running"] and st["loaded"]) if row[1] == "studio" else quick) for k, row in V.CATALOG.items()]}


@router.post("/choice")
def set_choice(body: VoiceIn, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    try:
        V.choose(body.id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    E._cache.clear()
    log(db, "voice_chosen", actor=me, target="voice", detail=f"{V.CATALOG[body.id][0]} ({V.CATALOG[body.id][1]})")
    return {"choice": V.chosen()}


@router.post("/sample")
def sample(body: VoiceIn, me: User = Depends(require_owner)):
    _limit(me, "sample", 30)
    if body.id not in V.CATALOG:
        raise HTTPException(400, "No such voice.")
    from ..branding import ai_name

    try:
        wav = E.speak(V.SAMPLE.format(ai=ai_name()), "en", voice=body.id)
    except E.VoiceError as e:
        raise HTTPException(409, str(e))
    return Response(wav, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@router.post("/studio/setup")
def studio_setup(db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    if ST.SETUP["running"]:
        return {"ok": True, "running": True}
    log(db, "voice_studio_setup", actor=me, target="voice", detail="own Python, PyTorch (CUDA 12.8), chatterbox-tts, voice prints")
    jobs.submit(ST.setup)
    return {"ok": True, "running": True}


@router.post("/studio/start")
def studio_start(me: User = Depends(require_owner), _h: User = Depends(require_human)):
    if not ST.installed():
        raise HTTPException(409, "Set up the studio voice first.")
    jobs.submit(ST.start)
    return {"ok": True}
