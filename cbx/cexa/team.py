"""C-EXA as a team member: morning briefs, team pulse, her journal (memory) and the
context-aware suggestions the drawer shows. Everything here reads the database and writes
CexaMessage / JournalEntry rows — the model is only asked for wording.

Public surface used by other features:
    team_pulse(db) -> {people: [{name, load, level, label}], note}
    remember(db, kind, subject, text) -> JournalEntry | None
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import (
    AuditLog,
    CexaMessage,
    Batch,
    JournalEntry,
    Proposal,
    Request,
    StoredFile,
    Task,
    Ticket,
    User,
    WatchedDoc,
)
from ..branding import ai_name
from . import agents
from .brain import system_prompt
from .gateway import Gateway, ModelError

SAST = timezone(timedelta(hours=2))
PULSE_NOTE = "C-EXA checks in with each admin once a day and flags when someone's load stays red for 3 days."  # the shell renames C-EXA on screen

# ---- journal -------------------------------------------------------------------------------

# Memory is about work only. Anything that looks personal, medical, political or otherwise
# about a person's private life is refused, whoever asks.
_PERSONAL = re.compile(
    r"\b(pregnan|divorce|marri(ed|age)|wife|husband|boyfriend|girlfriend|dating|religio|church|mosque|"
    r"politic|vote[sd]?|party member|ANC|DA\b|EFF\b|salary|salaries|paid|earns?|medical|diagnos|illness|sick note|"
    r"depress|anxiety|therap|medication|disab|HIV|cancer|drunk|alcohol|drugs?|sexual|gay|lesbian|trans\b|race|racial|"
    r"ethnic|home address|ID number|passport|bank account|debt|loan|arrest|criminal|police)\w*",
    flags=re.I,
)


def remember(db: Session, kind: str, subject: str, text: str, *, pinned: bool = False) -> Optional[JournalEntry]:
    """Write to C-EXA's journal. Scope rule: work only. If `text` mentions anything from the personal /
    health / political blocklist the entry is refused (returns None) and nothing is stored — C-EXA's memory
    holds layouts, preferences about how people like to work, lessons about the system. Owners can read
    and delete every entry from the Talk to C-EXA page."""
    text = (text or "").strip()
    if not text or _PERSONAL.search(text):
        return None
    if kind not in ("learned", "team", "self", "plan", "idea"):
        kind = "learned"
    e = JournalEntry(kind=kind, subject=subject[:160], text=text[:2000], pinned=pinned)
    db.add(e)
    db.flush()
    return e


def journal(db: Session, limit: int = 60) -> list[dict]:
    rows = db.query(JournalEntry).filter(JournalEntry.kind.in_(["learned", "team", "self", "idea"])).order_by(JournalEntry.pinned.desc(), JournalEntry.at.desc()).limit(limit).all()
    return [{"id": e.id, "at": e.at.isoformat(), "kind": e.kind, "subject": e.subject, "text": e.text, "pinned": e.pinned} for e in rows]


def _has_entry(db: Session, kind: str, subject: str) -> bool:
    return db.query(JournalEntry).filter(JournalEntry.kind == kind, JournalEntry.subject == subject).count() > 0


# ---- people ---------------------------------------------------------------------------------


def staff(db: Session) -> list[User]:
    """Admins and owners who are people (not C-EXA) and active."""
    return db.query(User).filter(User.role.in_(["owner", "admin"]), User.is_axl.is_(False), User.is_active.is_(True)).order_by(User.name).all()


def _open_tasks(db: Session) -> list[Task]:
    return db.query(Task).filter(Task.status != "done").all()


def _load_for(tasks: list[Task], now: datetime) -> tuple[int, dict]:
    working = sum(1 for t in tasks if t.status == "working")
    todo = sum(1 for t in tasks if t.status == "todo")
    overdue = sum(1 for t in tasks if t.due and t.due < now)
    load = min(100, 15 * working + 25 * overdue + 5 * todo)
    return load, {"working": working, "todo": todo, "overdue": overdue}


def _label(n: dict) -> str:
    parts = []
    if n["working"]:
        parts.append(f"{n['working']} working")
    if n["overdue"]:
        parts.append(f"{n['overdue']} late")
    if not parts and n["todo"]:
        parts.append(f"{n['todo']} to do")
    return " · ".join(parts) or "quiet"


def _level(load: int) -> str:
    return "hot" if load >= 80 else "warm" if load >= 50 else ""


def team_pulse(db: Session) -> dict:
    """Load per admin/owner from the Develop board, plus C-EXA's own row (her tasks + running agents)."""
    now = utcnow()
    tasks = _open_tasks(db)
    people = []
    for u in staff(db):
        mine = [t for t in tasks if t.assignee_id == u.id and not t.assignee_axl]
        load, n = _load_for(mine, now)
        people.append({"id": u.id, "name": u.name.split()[0] if u.name else u.email, "load": load, "level": _level(load), "label": _label(n)})
    cexa_tasks = [t for t in tasks if t.assignee_axl]
    running = agents.running_count(db)
    load, n = _load_for(cexa_tasks, now)
    load = min(100, load + 10 * running)
    lbl = _label(n)
    if running:
        lbl = f"{running} agent{'s' if running != 1 else ''} running" + ("" if lbl == "quiet" else " · " + lbl)
    people.append({"id": None, "name": ai_name(), "load": load, "level": _level(load), "label": lbl})
    return {"people": people, "note": PULSE_NOTE}


def snapshot_loads(db: Session, now: Optional[datetime] = None) -> list[dict]:
    """Once a day: record each person's load (JournalEntry kind=team subject=load:<uid>) and, when it has
    been red three days running, tell the owners with concrete advice. Returns the messages created."""
    now = now or utcnow()
    day = now.astimezone(SAST).date().isoformat()
    pulse = team_pulse(db)
    created = []
    for p in pulse["people"]:
        if p["id"] is None:
            continue
        subject = f"load:{p['id']}"
        today_key = f"{day} {p['load']}"
        rows = db.query(JournalEntry).filter(JournalEntry.kind == "plan", JournalEntry.subject == subject).order_by(JournalEntry.at.desc()).limit(3).all()
        if rows and rows[0].text.startswith(day):
            continue
        db.add(JournalEntry(kind="plan", subject=subject, text=today_key))
        db.flush()
        recent = [rows[0].text, rows[1].text] if len(rows) >= 2 else []
        red_streak = p["level"] == "hot" and len(recent) == 2 and all(int(r.split()[-1]) >= 80 for r in recent)
        if red_streak:
            created += _advise_owners(db, p, day)
    return created


def _advise_owners(db: Session, p: dict, day: str) -> list[dict]:
    now = utcnow()
    tasks = [t for t in _open_tasks(db) if t.assignee_id == p["id"] and not t.assignee_axl]
    tasks.sort(key=lambda t: (t.due or now + timedelta(days=365)))
    cexa_can = [t for t in tasks if CEXA_CAN.search(t.title)]
    pick = (cexa_can or tasks)[:1]
    others = [u for u in staff(db) if u.id != p["id"]]
    others_load = {u.id: _load_for([t for t in _open_tasks(db) if t.assignee_id == u.id], now)[0] for u in others}
    lightest = min(others, key=lambda u: others_load[u.id]) if others else None
    if pick:
        t = pick[0]
        advice = f"Move “{t.title}” to me — it's the kind of thing I can do" if cexa_can else (f"Move “{t.title}” to {lightest.name.split()[0]} (load {others_load[lightest.id]})" if lightest else f"Take “{t.title}” off the board for now")
    else:
        advice = "Nothing on the board to move — the load is coming from elsewhere; worth a word"
    text = f"{p['name']}'s load has been red for three days ({p['label']}). {advice}."
    out = []
    for o in [u for u in staff(db) if u.is_owner]:
        key = f"red3:{p['id']}:{day}"
        if any((m.meta or {}).get("key") == key for m in db.query(CexaMessage).filter(CexaMessage.user_id == o.id, CexaMessage.kind == "team").all()):
            continue
        m = CexaMessage(user_id=o.id, from_axl=True, kind="team", title="About the team", text=text, link="#/develop", meta={"key": key})
        db.add(m)
        out.append({"user_id": o.id, "text": text})
    db.flush()
    return out


CEXA_CAN = re.compile(r"\b(export|ocr|rename|renam|report|summar|sort|dedup|duplicate|reminder|check)\w*", re.I)


# ---- check-ins ----------------------------------------------------------------------------


# ---- morning brief -----------------------------------------------------------------------


def _overnight(db: Session, now: datetime) -> dict:
    since = now - timedelta(hours=14)
    rows = db.query(AuditLog).filter(AuditLog.at >= since).all()
    by: dict[str, int] = {}
    for a in rows:
        by[a.action] = by.get(a.action, 0) + 1
    today = now.date()
    from ..docwatch.copy_in import use_tool

    if use_tool(db):  # 1.0.63: the tool folder's documents
        from ..docwatch.tool import due_docs

        docs = due_docs(db)
    else:
        docs = [d for d in db.query(WatchedDoc).filter(WatchedDoc.archived.is_(False), WatchedDoc.expiry.isnot(None)).all() if d.expiry and (d.expiry - today).days <= d.warn_days]
    return {
        "audit": len(rows),
        "by_action": by,
        "failed": sum(1 for a in rows if a.outcome in ("failed", "blocked")),
        "blocked": sum(1 for a in rows if a.outcome == "blocked"),
        "batches": db.query(Batch).filter(Batch.finished_at.isnot(None), Batch.finished_at >= since).count(),
        "tickets": sum(1 for t in db.query(Ticket).filter(Ticket.created_at >= since).all()),
        "docs": len(docs),
        "inbox": db.query(StoredFile).filter(StoredFile.pipeline_status.in_(["queued", "check", "unknown"])).count(),
        "new_requests": db.query(Request).filter(Request.status == "new").count(),
        "signoff": db.query(Proposal).filter(Proposal.status == "signoff").count(),
        "profiles_pending": _pending_profiles(db),
    }


def _pending_profiles(db: Session) -> int:
    try:
        import importlib

        fn = getattr(importlib.import_module("cbx.pdfstudio.profiles"), "pending_count", None)
        return int(fn(db)) if fn else 0
    except Exception:  # noqa: BLE001
        return 0


def _wording(db: Session, facts: dict) -> str:
    gw = Gateway(db)
    user = " ".join(f"{k}={v}" for k, v in facts.items() if isinstance(v, int)) + "\nby_action=" + ", ".join(f"{k}:{v}" for k, v in sorted(facts["by_action"].items(), key=lambda kv: -kv[1])[:8])
    try:
        return gw.text(task="brief", system=system_prompt("Write the two-to-four sentence morning brief for an administrator from these overnight numbers. Only use numbers given. Plain English, no headings."), user=user).strip()
    except ModelError:
        return f"Overnight: {facts['audit']} audit entries, {facts['failed']} failed or blocked. {facts['batches']} batches finished."


def send_briefs(db: Session, now: Optional[datetime] = None) -> list[CexaMessage]:
    """Once per day per admin/owner, after 06:00 SAST. Idempotent: JournalEntry kind=plan
    subject=brief:<date>:<uid> marks who already got theirs."""
    now = now or utcnow()
    local = now.astimezone(SAST)
    if local.hour < 6:
        return []
    day = local.date().isoformat()
    created: list[CexaMessage] = []
    facts = None
    wording = None
    for u in staff(db):
        subject = f"brief:{day}:{u.id}"
        if _has_entry(db, "plan", subject):
            continue
        if facts is None:
            facts = _overnight(db, now)
            wording = _wording(db, facts)
            # the pulse's one line: "Systems: all fine" or what isn't — the snapshot C-EXA keeps every minute
            try:
                from .pulse import systems_line

                wording = (wording.rstrip() + " " + systems_line(db)).strip()
            except Exception:  # noqa: BLE001 — the brief must never fail on its own footnote
                pass
        m = CexaMessage(user_id=u.id, from_axl=True, kind="brief", title="Overnight", text=wording, meta={"facts": {k: v for k, v in facts.items() if k != "by_action"}})
        db.add(m)
        created.append(m)
        needs = []
        if u.is_owner and facts["signoff"]:
            needs.append((f"{facts['signoff']} proposal{'s' if facts['signoff'] != 1 else ''} built and tested — waiting for your sign-off", "#/cexa-dev"))
        if facts["profiles_pending"]:
            needs.append((f"{facts['profiles_pending']} profile patch{'es' if facts['profiles_pending'] != 1 else ''} to approve", "#/pdf-profiles"))
        if facts["new_requests"]:
            needs.append((f"{facts['new_requests']} new request{'s' if facts['new_requests'] != 1 else ''} scored and waiting", "#/req-queue"))
        for text, link in needs:
            db.add(CexaMessage(user_id=u.id, from_axl=True, kind="needs_you", title="Needs you", text=text, link=link))
        if u.is_owner:
            hot = [p for p in team_pulse(db)["people"] if p["level"] == "hot" and p["id"] is not None]
            if hot:
                names = ", ".join(p["name"] for p in hot)
                db.add(CexaMessage(user_id=u.id, from_axl=True, kind="team", title="About the team", text=f"{names} {'is' if len(hot) == 1 else 'are'} running hot ({'; '.join(p['label'] for p in hot)}). I'll check in with them today; say the word if you'd rather I take something off the board.", link="#/develop"))
        red = facts["failed"] or facts["new_requests"] or facts["signoff"] or facts["profiles_pending"] or facts["docs"]
        if not red and not _has_entry(db, "plan", f"aside:{day}"):
            db.add(JournalEntry(kind="plan", subject=f"aside:{day}", text="sent"))
            db.commit()  # the marker is in; don't hold the write lock while the model writes the remark
            try:
                aside = Gateway(db).text(task="aside", system=system_prompt("One short, dry, work-related remark (max 20 words) for a quiet morning with nothing red. Tasteful; nothing about people, health or politics."), user=f"quiet morning {day} for {u.name}").strip()
            except ModelError:
                aside = ""
            if aside:
                db.add(CexaMessage(user_id=u.id, from_axl=True, kind="aside", title="And", text=aside))
        db.add(JournalEntry(kind="plan", subject=subject, text=day))
    db.flush()
    return created


# ---- plan ------------------------------------------------------------------------------------


def today_plan(db: Session, now: Optional[datetime] = None) -> dict:
    """Derived, not stored: what C-EXA is on today and how far she is."""
    now = now or utcnow()
    day_start = now.astimezone(SAST).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    items: list[dict] = []
    briefed = db.query(JournalEntry).filter(JournalEntry.kind == "plan", JournalEntry.subject.like(f"brief:{now.astimezone(SAST).date().isoformat()}:%")).count()
    items.append({"text": "Morning brief to the team", "done": briefed > 0})
    pend = db.query(Request).filter(Request.status == "sent").count()
    items.append({"text": f"Assess new requests ({pend} pending)" if pend else "Assess new requests", "done": pend == 0})
    for b in db.query(Batch).filter(Batch.status.in_(["learning", "running"])).limit(4):
        items.append({"text": f"Finish batch “{b.name or b.id}” ({int(b.progress * 100)}%)", "done": False})
    for b in db.query(Batch).filter(Batch.status == "done", Batch.finished_at >= day_start).limit(3):
        items.append({"text": f"Batch “{b.name or b.id}”", "done": True})
    for p in db.query(Proposal).filter(Proposal.status == "building").limit(4):
        items.append({"text": f"Build: {p.title}", "done": False})
    for p in db.query(Proposal).filter(Proposal.status == "signoff", Proposal.updated_at >= day_start).limit(3):
        items.append({"text": f"Built and tested: {p.title}", "done": True})
    for t in db.query(Ticket).filter(Ticket.status == "testing").limit(3):
        items.append({"text": f"Testing: {t.title}", "done": False})
    for t in db.query(Task).filter(Task.assignee_axl.is_(True), Task.status != "done").limit(4):
        items.append({"text": f"Board: {t.title}", "done": False})
    return {"items": items, "done": sum(1 for i in items if i["done"]), "total": len(items)}


# ---- suggestions ---------------------------------------------------------------------------


def suggestions(db: Session, route: str, me: User) -> list[dict]:
    now = utcnow()
    route = route or "home"
    s: list[dict] = []
    if not me.is_admin:
        return _manager_suggestions(db, route, me, now)
    if route in ("req-queue", "requests", "req-detail"):
        n = db.query(Request).filter(Request.status.in_(["new", "sent"])).count()
        s.append({"q": f"Outline tools for the {n} waiting request{'s' if n != 1 else ''}" if n else "What usually comes in as a request here?", "hint": "I'll outline steps and rules for each, you approve" if n else "Patterns from past requests"})
        s.append({"q": "Which requests are similar to each other?", "hint": "One build could close several"})
    elif route == "users":
        stale = sum(1 for u in db.query(User).filter(User.role == "user", User.is_axl.is_(False)) if u.last_seen is None or (now - u.last_seen).days >= 60)
        s.append({"q": "Who has access they haven't used in 60 days?", "hint": f"{stale} account{'s' if stale != 1 else ''} right now"})
        s.append({"q": "Anything odd in sign-ins this week?", "hint": "Failed logins, lockouts, new devices"})
    elif route == "develop":
        s.append({"q": "Who is overloaded?", "hint": "From the cards in Working and what's late"})
        s.append({"q": "What can you take off the board yourself?", "hint": "Exports, renames, reports, OCR"})
    elif route in ("home",):
        s.append({"q": "Summarise what changed since yesterday", "hint": "Audit trail, batches, requests"})
        s.append({"q": "What needs me today?", "hint": "Decisions only, in order"})
    elif route in ("pdf-studio", "pdf-profiles"):
        s.append({"q": "Which profiles are below the gate?", "hint": "And what would get them over it"})
        s.append({"q": "What's running right now and how far is it?", "hint": "Batches and their steps"})
    elif route == "audit":
        s.append({"q": "Anything blocked or failed in the last 24 hours?", "hint": "With who and what"})
        s.append({"q": "What did you do overnight?", "hint": "Your own actions, from the trail"})
    elif route == "warehouse":
        s.append({"q": "What's waiting in the inbox and where is it going?", "hint": "Files by detected type"})
    elif route in ("build", "builder"):
        s.append({"q": "Which tickets are stuck in testing?", "hint": "And what the last test said"})
    elif route == "cexa-dev":
        s.append({"q": "Why did you propose these?", "hint": "Evidence behind each card"})
        s.append({"q": "What's the risk in shipping the sign-off card?", "hint": "Tests, diff size, what it touches"})
    elif route == "agents":
        s.append({"q": "Who on your team is slow and why?", "hint": "From recent runs"})
    else:
        s.append({"q": "What can you help with here?", "hint": "What I can see on this page"})
    s.append({"q": "What can you help with here?", "hint": "Quick tour of this page"} if route != "home" and not any(x["q"].startswith("What can you help") for x in s) else {"q": "Anything I should know before I start?", "hint": "Short version"})
    return s[:3]


def _manager_suggestions(db: Session, route: str, me: User, now: datetime) -> list[dict]:
    """A manager's drawer talks about their team only: their people, their register, their audit entries."""
    s: list[dict] = []
    if route == "users":
        stale = sum(1 for u in db.query(User).filter(User.role == "user", User.is_axl.is_(False), User.team_id == (me.team_id or 0)) if u.last_seen is None or (now - u.last_seen).days >= 60)
        s.append({"q": "Who on my team hasn't signed in for 60 days?", "hint": f"{stale} account{'s' if stale != 1 else ''} right now"})
        s.append({"q": "Is anyone on my team locked out?", "hint": "Locked accounts and reset requests"})
    elif route == "audit":
        s.append({"q": "Anything blocked or failed for my team in the last 24 hours?", "hint": "With who and what"})
    elif route == "doc-watch":
        s.append({"q": "Which of my team's documents expire soonest?", "hint": "From the register"})
        s.append({"q": "What still needs a date?", "hint": "Filed documents without an expiry"})
    else:
        s.append({"q": "What needs my team's attention today?", "hint": "Expiring documents, locked accounts"})
    s.append({"q": "What can you help with here?", "hint": "What I can see for your team"})
    return s[:3]
