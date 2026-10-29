"""C-EXA as a team member and developer: the drawer (`ask`, `suggestions`), the owner's 1-on-1
(`chat`, `thread`, `plan`, `journal`), the Agents page, and the proposals board."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import VERSION
from ..audit import log
from ..cexa import agents, brain, developer, team
from ..cexa.gateway import ollama_status
from ..config import settings
from ..db import get_db
from ..models import CexaMessage, Fix, JournalEntry, Proposal, Task, TaskNote, User
from ..security.deps import require_admin, require_human, require_manager, require_owner
from .schemas import iso

router = APIRouter(prefix="/api/cexa", tags=["cexa"])


class AskBody(BaseModel):
    question: str
    route: str = ""
    params: Optional[dict] = None


class TextBody(BaseModel):
    text: str


def _msg_out(m: CexaMessage, me: User) -> dict:
    return {"id": m.id, "at": iso(m.at), "from_axl": m.from_axl, "kind": m.kind, "title": m.title, "text": m.text, "thinking": m.thinking if me.is_owner else "", "link": m.link, "read": m.read}


# ---- drawer ------------------------------------------------------------------------------------------


# The drawer is open to managers too — brain.ask gives a manager team-scoped answers and no tools.
@router.get("/suggestions")
def suggestions(route: str = "", db: Session = Depends(get_db), me: User = Depends(require_manager)):
    return {"suggestions": team.suggestions(db, route, me)}


@router.post("/ask")
def ask(body: AskBody, db: Session = Depends(get_db), me: User = Depends(require_manager)):
    if not body.question.strip():
        raise HTTPException(400, "Type a question first.")
    return brain.ask(db, me, body.question, body.route, body.params)


# ---- 1-on-1 ---------------------------------------------------------------------------------------------


@router.post("/chat")
def chat(body: TextBody, db: Session = Depends(get_db), me: User = Depends(require_owner)):
    if not body.text.strip():
        raise HTTPException(400, "Type a message first.")
    return brain.ask(db, me, body.text, "cexa", None)


@router.get("/thread")
def thread(limit: int = 60, db: Session = Depends(get_db), me: User = Depends(require_manager)):
    rows = db.query(CexaMessage).filter(CexaMessage.user_id == me.id, CexaMessage.cleared.is_(False)).order_by(CexaMessage.at.desc(), CexaMessage.id.desc()).limit(limit).all()
    return {"messages": [_msg_out(m, me) for m in reversed(rows)]}


@router.post("/thread/clear")
def clear_thread(db: Session = Depends(get_db), me: User = Depends(require_manager)):
    """Clear my conversation with C-EXA. Messages stay in the database and audit trail; C-EXA starts fresh
    (she no longer sees them as recent conversation)."""
    n = db.query(CexaMessage).filter(CexaMessage.user_id == me.id, CexaMessage.cleared.is_(False)).update({"cleared": True, "read": True})
    log(db, "axl_chat_cleared", actor=me, target="axl", detail=f"{n} messages")
    return {"cleared": n}


@router.get("/status")
def status(db: Session = Depends(get_db), me: User = Depends(require_admin)):
    """Model health for the owner/admins: is Ollama up, is the model fully on the GPU (`loaded[].gpu_pct`),
    what does that mean in plain English (`advice`), how slow were calls per task over the last 24 h."""
    st = ollama_status(db)
    return {
        "model": settings.cexa_model, "mode": st.get("mode"), "online": st.get("online"), "version": VERSION, "agents_running": agents.running_count(),
        "num_ctx": st.get("num_ctx"), "loaded": st.get("loaded", []), "advice": st.get("advice", ""), "latency": st.get("latency", []), "slow": st.get("slow", False), "error": st.get("error", ""),
    }


@router.get("/plan")
def plan(db: Session = Depends(get_db), me: User = Depends(require_owner)):
    return team.today_plan(db)


# ---- journal ---------------------------------------------------------------------------------------------


@router.get("/journal")
def journal(db: Session = Depends(get_db), me: User = Depends(require_owner)):
    return {"entries": team.journal(db)}


@router.delete("/journal/{eid}")
def journal_delete(eid: int, db: Session = Depends(get_db), me: User = Depends(require_owner)):
    e = db.get(JournalEntry, eid)
    if not e:
        raise HTTPException(404, "That entry is already gone.")
    log(db, "journal_removed", actor=me, target=f"journal:{eid}", detail=e.text[:120])
    db.delete(e)
    return {"ok": True}


# ---- agents -----------------------------------------------------------------------------------------------


@router.get("/agents")
def agents_page(db: Session = Depends(get_db), me: User = Depends(require_owner)):
    snap = agents.snapshot()
    notes = []
    for a in snap["agents"]:
        for n in a.get("notes") or []:
            notes.append({"agent": a["name"], "text": n})
    hist = snap["history"][:12]
    for h in hist:
        if not h["ok"]:
            notes.append({"agent": h["agent"], "text": f"“{h['task']}” failed after {h['seconds']} s. I'll look at the log before it runs again."})
    slow = [h for h in hist if h["ok"] and h["seconds"] > 120]
    for h in slow[:2]:
        notes.append({"agent": h["agent"], "text": f"“{h['task']}” took {int(h['seconds'] // 60)} min. Acceptable, but worth pre-processing if it repeats."})
    if not notes:
        notes.append({"agent": "team", "text": "Everyone is idle or on schedule. Nothing to flag."})
    return {"agents": snap["agents"], "history": hist, "notes": notes[:8], "running": sum(1 for a in snap["agents"] if a["state"] == "busy")}


# ---- proposals -------------------------------------------------------------------------------------------


@router.get("/proposals")
def proposals(db: Session = Depends(get_db), me: User = Depends(require_owner)):
    out = developer.board(db)
    out["sandbox_label"] = f"dev sandbox · branch {out['branch']}" if out["branch"] else "dev sandbox · idle"
    return out


@router.post("/proposals/notice")
def proposals_notice(db: Session = Depends(get_db), me: User = Depends(require_owner)):
    made = developer.notice(db)
    return {"created": [developer.proposal_out(p) for p in made]}


@router.get("/proposals/{pid}")
def proposal_get(pid: int, db: Session = Depends(get_db), me: User = Depends(require_owner)):
    p = db.get(Proposal, pid)
    if not p:
        raise HTTPException(404, "That proposal doesn't exist.")
    return developer.proposal_out(p, db)


@router.delete("/proposals/{pid}")
def proposal_delete(pid: int, db: Session = Depends(get_db), me: User = Depends(require_owner)):
    """The owner can remove a card that isn't worth the board's space. A card that is building or
    waiting for sign-off has a sandbox attached — decline it instead, so the sandbox is cleaned up."""
    p = db.get(Proposal, pid)
    if not p:
        raise HTTPException(404, "That proposal doesn't exist.")
    if p.status in ("building", "signoff"):
        raise HTTPException(400, "This one is building in a sandbox — use 'Not now' to stop it cleanly first.")
    title = p.title
    log(db, "proposal_deleted", actor=me, target=f"proposal:{p.id}", detail=title)
    db.delete(p)
    db.flush()
    return {"ok": True}


@router.post("/proposals/{pid}/go")
def proposal_go(pid: int, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    p = db.get(Proposal, pid)
    if not p:
        raise HTTPException(404, "That proposal doesn't exist.")
    try:
        developer.go(db, p, me)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.expire_all()
    p = db.get(Proposal, pid)
    return developer.proposal_out(p, db)


@router.post("/proposals/{pid}/not-now")
def proposal_not_now(pid: int, db: Session = Depends(get_db), me: User = Depends(require_owner)):
    p = db.get(Proposal, pid)
    if not p:
        raise HTTPException(404, "That proposal doesn't exist.")
    if p.status == "shipped":
        raise HTTPException(400, "That one has already shipped — roll it back on Versions instead.")
    if p.status == "declined":
        return developer.proposal_out(p, db)
    return developer.proposal_out(developer.not_now(db, p, me), db)


@router.post("/proposals/{pid}/ship")
def proposal_ship(pid: int, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    p = db.get(Proposal, pid)
    if not p:
        raise HTTPException(404, "That proposal doesn't exist.")
    try:
        from ..versions.injector import VersionError, version_out

        v = developer.ship(db, p, me)
    except (ValueError, VersionError) as e:
        raise HTTPException(400, str(e))
    return {"proposal": developer.proposal_out(p, db), "version": version_out(v)}


# ---- Bugs & fixes: what C-EXA did, for the owner's feedback ---------------------------------------


def _fix_out(f: Fix, db: Session) -> dict:
    rep = db.get(User, f.reported_by_id) if f.reported_by_id else None
    return {
        "id": f.id, "title": f.title, "area": f.area, "status": f.status, "kind": f.kind, "detail": f.detail,
        "reported_by": rep.name if rep else "", "actions": f.actions or [], "resolution": f.resolution, "feedback": f.feedback,
        "proposal_id": f.proposal_id, "proposal_status": (db.get(Proposal, f.proposal_id).status if f.proposal_id and db.get(Proposal, f.proposal_id) else None),
        "task_id": f.task_id, "created_at": f.created_at.isoformat(), "updated_at": f.updated_at.isoformat(),
    }


@router.get("/fixes")
def fixes(db: Session = Depends(get_db), me: User = Depends(require_admin)):
    rows = db.query(Fix).order_by(Fix.updated_at.desc()).limit(200).all()
    counts = {}
    for f in rows:
        counts[f.status] = counts.get(f.status, 0) + 1
    return {"fixes": [_fix_out(f, db) for f in rows], "counts": counts}


@router.post("/fixes/{fid}/feedback")
def fix_feedback(fid: int, body: dict, db: Session = Depends(get_db), me: User = Depends(require_admin)):
    f = db.get(Fix, fid)
    if f is None:
        raise HTTPException(404, "No such fix.")
    verdict = str(body.get("verdict", ""))[:32]  # works | still_broken | note
    note = str(body.get("note", ""))[:2000]
    f.feedback = (verdict + (": " + note if note else "")).strip()
    if verdict == "still_broken":
        f.status = "open"
        if f.task_id and (t := db.get(Task, f.task_id)):
            t.status = "todo"
            t.done_at = None
            db.add(TaskNote(task_id=t.id, by="user", by_id=me.id, text=f"Still broken: {note}" if note else "Still broken — reopened."))
    log(db, "fix_feedback", actor=me, target=f"fix:{f.id}", detail=f.feedback)
    return _fix_out(f, db)
