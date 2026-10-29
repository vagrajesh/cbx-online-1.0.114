"""Sidebar counts, and `/attention` for owner/admins (things that went unnoticed on the server — stuck batches,
a model half on the CPU, a failed version). The Home page itself is the tools/home folder (1.0.58); the old
summary, check-ins and getting-started list went in 1.0.79."""
from __future__ import annotations

import importlib
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..audit import log
from ..config import settings
from ..db import get_db, utcnow
from ..models import AppVersion, CexaMessage, Batch, HarvestSource, Proposal, Request, Task, Ticket, User, WatchedDoc
from ..security.deps import current_user, require_admin
from .schemas import iso

router = APIRouter(prefix="/api/home", tags=["home"])

# thresholds for "Needs attention" — an hour in `uploading` means the person walked away; a day in `review`
# means nobody has looked at the sheet; a harvest that missed three runs has stopped
UPLOADING_STUCK_H = 1
REVIEW_STUCK_H = 24
FAILED_WINDOW_DAYS = 30
HARVEST_MISSED_RUNS = 3


def _n(n: int) -> str:
    """1 493 — thousands separated with a space, the way the rest of the UI writes numbers."""
    return f"{int(n):,}".replace(",", " ")


def _ago(dt, now) -> str:
    if not dt:
        return ""
    s = max(0, int((now - dt).total_seconds()))
    if s < 3600:
        return f"{max(1, s // 60)} min"
    if s < 48 * 3600:
        return f"{s // 3600} h"
    return f"{s // 86400} days"


def _opt(mod: str, fn: str):
    try:
        return getattr(importlib.import_module(mod), fn)
    except (ModuleNotFoundError, AttributeError):
        return None


@router.get("/counts")
def counts(db: Session = Depends(get_db), me: User = Depends(current_user)):
    out = {
        "my_requests": db.query(Request).filter(Request.user_id == me.id, Request.origin != "admin", Request.status.notin_(["ready", "declined"])).count(),
        "messages": db.query(CexaMessage).filter(CexaMessage.user_id == me.id, CexaMessage.from_axl.is_(True), CexaMessage.read.is_(False), CexaMessage.cleared.is_(False)).count(),
        "docs_due": 0,
    }
    # Entity Compliance: cells the person can see (their companies) that have expired or are due
    from ..docwatch.service import due_count

    out["docs_due"] = due_count(db, me)
    from ..chat.service import unread_total

    # People-to-people chat lives on the same Messages page as C-EXA's threads, so its unread count
    # joins the same badge.
    out["chat"] = unread_total(db, me)
    out["messages"] += out["chat"]
    # To-do: the open tasks I hold — my personal ones, team tasks assigned to me, and tasks on active jobs
    from sqlalchemy import or_

    from ..models import TodoJob, TodoTask

    from ..engine.metrics import METRICS

    out["approvals"] = METRICS["flows.waiting_for_me"].fn(db, me)
    from ..todo.copy_in import open_for

    n = open_for(db, me)
    out["todo"] = n if n is not None else db.query(TodoTask).outerjoin(TodoJob, TodoJob.id == TodoTask.job_id).filter(TodoTask.assignee_id == me.id, TodoTask.status == "open", or_(TodoTask.job_id.is_(None), TodoJob.status == "active")).count()
    if me.is_admin:
        out["new_requests"] = db.query(Request).filter(Request.status == "new").count()
        out["tasks_open"] = db.query(Task).filter(Task.status != "done").count()
        out["tickets_open"] = db.query(Ticket).filter(Ticket.status != "ready").count()
    if me.is_owner:
        out["proposals"] = db.query(Proposal).filter(Proposal.status.in_(["noticed", "signoff"])).count()
        agents = _opt("cbx.cexa.agents", "running_count")
        out["agents_running"] = agents(db) if agents else 0
        from ..models import Mission

        # C-EXA's jobs that need the owner: questions to answer, a plan waiting for Go, work in for review
        out["agents_running"] += db.query(Mission).filter(Mission.status.in_(["asking", "plan", "review"])).count()
        # her questions and what waits for the owner's tap: one inbox, Approvals (1.0.83)
        from ..cexa.goals import owner_waiting_count

        # 1.0.114: a goal's stuck work is AXL's to solve; what reaches the owner is his taps and asks, and goals waiting for him
        out["approvals"] = (out.get("approvals") or 0) + owner_waiting_count(db)
    return out


@router.post("/messages/read")
def mark_read(db: Session = Depends(get_db), me: User = Depends(current_user)):
    db.query(CexaMessage).filter(CexaMessage.user_id == me.id, CexaMessage.from_axl.is_(True), CexaMessage.read.is_(False)).update({"read": True})
    return {"ok": True}


# ---- Needs attention (owner/admin) ----------------------------------------------------------------
# One plain sentence and one button per item. Only items that exist: an empty list hides the card.


def _item(key: str, text: str, *, level: str = "warn", go: Optional[str] = None, label: str = "Open", n: int = 0) -> dict:
    return {"key": key, "text": text, "level": level, "go": go, "label": label, "n": n}


def _owner_name(db: Session, uid: Optional[int]) -> str:
    u = db.get(User, uid) if uid else None
    return (u.name.split()[0] if u and u.name else "someone")


def _batch_items(db: Session, now, items: list[dict]) -> None:
    def bname(b: Batch) -> str:
        return b.name or f"batch #{b.id}"

    # stuck in uploading (with or without PDFs)
    stuck = db.query(Batch).filter(Batch.status == "uploading", Batch.created_at < now - timedelta(hours=UPLOADING_STUCK_H)).order_by(Batch.created_at).all()
    if len(stuck) > 3:
        empty = sum(1 for b in stuck if not b.documents)
        items.append(_item("batches_uploading", f"{len(stuck)} PDF batches never got past 'uploading' (oldest {_ago(stuck[0].created_at, now)} ago{f', {empty} of them have no PDFs' if empty else ''}). They were probably abandoned; open PDF Studio to tidy up.", go="pdf-studio", label="PDF Studio", n=len(stuck)))
    else:
        for b in stuck:
            nd = len(b.documents)
            pdfs = f"{_n(nd)} PDF{'s' if nd != 1 else ''}" if nd else "no PDFs"
            items.append(_item(f"batch_uploading:{b.id}", f"'{bname(b)}' has sat in 'uploading' for {_ago(b.created_at, now)} with {pdfs} — {_owner_name(db, b.user_id)} started it and never pressed Start." + ("" if nd else " Probably abandoned."), go=f"pdf-studio?batch={b.id}", n=nd))
    # waiting for a person
    rev = db.query(Batch).filter(Batch.status == "review", Batch.cells_for_user > 0).all()
    for b in rev:
        since = b.finished_at or b.created_at
        if since and since < now - timedelta(hours=REVIEW_STUCK_H):
            nd = len(b.documents)
            who = f"{_n(nd)} {'invoice' if nd == 1 else 'invoices'} in '{bname(b)}' {'is' if nd == 1 else 'are'}" if nd else f"'{bname(b)}' is"
            items.append(_item(f"batch_review:{b.id}", f"{who} waiting for a person — {_n(b.cells_for_user)} cell{'s' if b.cells_for_user != 1 else ''} to fill in, untouched for {_ago(since, now)} ({_owner_name(db, b.user_id)}'s batch).", go=f"pdf-studio?batch={b.id}", n=b.cells_for_user))
    # failed
    failed = db.query(Batch).filter(Batch.status == "failed", Batch.created_at > now - timedelta(days=FAILED_WINDOW_DAYS)).order_by(Batch.created_at.desc()).limit(5).all()
    for b in failed:
        why = (b.error or "").strip().splitlines()[0][:140] if (b.error or "").strip() else "no reason recorded"
        items.append(_item(f"batch_failed:{b.id}", f"'{bname(b)}' failed {_ago(b.finished_at or b.created_at, now)} ago: {why}", level="crit", go=f"pdf-studio?batch={b.id}"))


def _harvest_items(db: Session, now, items: list[dict]) -> None:
    for s in db.query(HarvestSource).order_by(HarvestSource.name).all():
        if s.status == "error":
            why = (s.last_error or "").strip().splitlines()[0][:140] if (s.last_error or "").strip() else "no detail recorded"
            items.append(_item(f"harvest_error:{s.id}", f"Harvest '{s.name}' stopped with an error: {why}", level="crit", go="pdf-harvest", label="Harvest"))
            continue
        if not s.active or s.status == "paused":
            continue
        every = s.every_minutes or 15
        limit = timedelta(minutes=every * HARVEST_MISSED_RUNS)
        ref = s.last_run_at or s.created_at
        if ref and ref < now - limit and s.status != "running":
            when = f"has not run for {_ago(s.last_run_at, now)}" if s.last_run_at else f"has never run ({_ago(s.created_at, now)} since it was added)"
            items.append(_item(f"harvest_stale:{s.id}", f"Harvest '{s.name}' {when} — it should run every {every} min. Is the server awake and the folder reachable?", go="pdf-harvest", label="Harvest"))


def _docwatch_items(db: Session, now, items: list[dict]) -> None:
    from ..docwatch.copy_in import use_tool

    today = now.date()
    if use_tool(db):  # 1.0.63
        from ..docwatch.tool import counts

        n = counts(db)["expired"]
    else:
        n = db.query(WatchedDoc).filter(WatchedDoc.archived.is_(False), WatchedDoc.expiry.isnot(None), WatchedDoc.expiry < today).count()
    if n:
        items.append(_item("docs_expired", f"{_n(n)} document{'s' if n != 1 else ''} on the compliance register {'have' if n != 1 else 'has'} expired and {'are' if n != 1 else 'is'} still listed as current.", level="crit", go="doc-watch", label="Open register", n=n))


def model_status(db: Session) -> dict:
    """The gateway's diagnostic, never raising (a missing Ollama is a finding, not an error)."""
    try:
        from ..cexa.gateway import ollama_status

        return ollama_status(db)
    except Exception as e:  # noqa: BLE001
        return {"online": False, "mode": settings.model_mode, "error": str(e)[:160], "loaded": [], "advice": "", "latency": [], "slow": False}


def _model_items(db: Session, me: User, items: list[dict]) -> dict:
    st = model_status(db)
    go = "cexa" if me.is_owner else None
    if settings.model_mode == "live" and not st.get("online"):
        items.append(_item("model_offline", f"C-EXA's model is not reachable (Ollama at {settings.ollama_url}). She is answering with fixed sentences and PDF Studio runs on rules only. Start Ollama on the server, or check CBX_OLLAMA_URL in .env.", level="crit", go=go, label="Talk to C-EXA"))
    elif st.get("advice"):
        items.append(_item("model_slow", st["advice"], go=go, label="Talk to C-EXA"))
    return st


def _version_items(db: Session, me: User, items: list[dict]) -> None:
    if not me.is_owner:
        return
    for v in db.query(AppVersion).filter(AppVersion.status == "failed").order_by(AppVersion.created_at.desc()).limit(3).all():
        t = v.tests or {}
        fails = list(t.get("failures") or [])
        which = (" First: " + fails[0].split(" — ")[0].replace("tests/", "") + ".") if fails else ""
        items.append(_item(f"version_failed:{v.id}", f"Version {v.tag} failed {t.get('failed', '?')} of its sandbox tests ({t.get('passed', 0)} passed).{which} On the Versions page: see the failing tests, re-test, delete it, or promote it anyway.", level="crit", go="versions", label="Versions"))
    # the last promote overwrote files someone had edited by hand on the server: say so for a week
    live = db.query(AppVersion).filter(AppVersion.status == "live").order_by(AppVersion.promoted_at.desc()).first()
    edited = list((live.tests or {}).get("hand_edited") or []) if live else []
    if live and edited and live.promoted_at and live.promoted_at > utcnow() - timedelta(days=7):
        from ..versions.injector import hand_edits_sentence

        items.append(_item(f"version_hand_edits:{live.id}", f"Promoting {live.tag} on {live.promoted_at.strftime('%d %b')} overwrote files someone had changed by hand on the server — {hand_edits_sentence(edited)}. Copies are in data\\backups; ask for the change as a request so it survives the next version.", level="info", go="versions", label="Versions", n=len(edited)))


def _clock_items(db: Session, me: User, items: list[dict]) -> None:
    """1.0.113: this server's clock is off (cbx/security/clock.py) — owners and admins only."""
    if not me.is_admin:
        return
    from ..security import clock

    line = clock.check(db)
    if line:
        items.append(_item("server_clock_off", line, level="warn"))


def attention_items(db: Session, me: User) -> list[dict]:
    now = utcnow()
    items: list[dict] = []
    for fn in (lambda: _batch_items(db, now, items), lambda: _harvest_items(db, now, items), lambda: _docwatch_items(db, now, items), lambda: _model_items(db, me, items), lambda: _version_items(db, me, items), lambda: _clock_items(db, me, items)):
        try:
            fn()
        except Exception as e:  # noqa: BLE001 — one broken check must not hide the others
            items.append(_item("check_failed", f"One of the Home checks could not run ({type(e).__name__}: {str(e)[:100]}). The audit trail has the detail.", level="warn"))
            log(db, "home_check_failed", actor=me, target="attention", detail=f"{type(e).__name__}: {e}"[:400], outcome="failed")
    order = {"crit": 0, "warn": 1, "info": 2}
    items.sort(key=lambda x: (order.get(x["level"], 9), -x.get("n", 0)))
    return items


@router.get("/attention")
def attention(db: Session = Depends(get_db), me: User = Depends(require_admin)):
    return {"items": attention_items(db, me), "at": iso(utcnow())}
