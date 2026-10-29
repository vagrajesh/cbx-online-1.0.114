from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..audit import log
from ..db import get_db
from ..models import LoginSession, User
from ..security.deps import current_user
from .schemas import iso, user_out

router = APIRouter(prefix="/api/settings", tags=["settings"])


# 1.0.114: the themes are CBX-Connect 2's (web/theme-boot.js keeps the same list for the browser)
THEMES = ("classic", "classic-dark", "ocean", "sand", "forest", "fresh", "paper", "slate", "night", "contrast")
LOOK = {"theme": THEMES, "menu": ("side", "top"), "size": ("standard", "large")}
SERVER_KEYS = ("ms_oid",)


class PrefsIn(BaseModel):
    name: str | None = None
    prefs: dict | None = None


@router.get("")
def get_settings(db: Session = Depends(get_db), me: User = Depends(current_user)):
    sessions = db.query(LoginSession).filter(LoginSession.user_id == me.id, LoginSession.revoked.is_(False)).order_by(LoginSession.last_seen.desc()).all()
    return {"user": user_out(me, full=True), "prefs": dict(me.prefs or {}), "sessions": [{"id": s.id[:8], "ip": s.ip, "agent": s.agent, "last_seen": iso(s.last_seen)} for s in sessions]}


@router.patch("")
def patch_settings(body: PrefsIn, db: Session = Depends(get_db), me: User = Depends(current_user)):
    if body.name and body.name.strip():
        me.name = body.name.strip()[:160]
    if body.prefs is not None:
        # only simple switches are stored; nothing arbitrary or large lands in the users table
        clean = {str(k)[:64]: v for k, v in body.prefs.items() if isinstance(v, (bool, int, float, str)) and (not isinstance(v, str) or len(v) <= 200)}
        # 1.0.114: the look (Settings → Appearance) takes only the words the app knows — they become class names on the
        # page; and the Microsoft account a person is pinned to is the server's to set, never the person's
        for k, ok in LOOK.items():
            if k in clean and clean[k] not in ok:
                raise HTTPException(400, f"Unknown {k}: {str(clean[k])[:40]}")
        for k in SERVER_KEYS:
            clean.pop(k, None)
        me.prefs = {**(me.prefs or {}), **clean}
    log(db, "settings_changed", actor=me)
    return {"user": user_out(me, full=True), "prefs": me.prefs}
