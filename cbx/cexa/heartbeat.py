"""C-EXA's heartbeat (1.0.62). She is never asleep: every few minutes she looks, and when something is new
she hands it to the agent whose work it is; at night she looks deeper.

Light beat (every CBX_HEARTBEAT_MINUTES, default 15) — no model at all unless something new turned up. Since 1.0.114 each
signal opens a problem goal (cbx/cexa/goals.py) that AXL plans from the top, instead of landing on one lead:
    * the pulse readings that are wrong twice running             → the Operations lead
    * new errors in the audit trail, rules (flows) that failed      → the Fix lead
    * tool folders or drafts the Builder says are broken            → the Change lead (fixes it in a copy draft)
    * work left "working" by a restart goes back to open; questions waiting a day get one reminder
  One open piece of work per signal (its fingerprint); a signal that was dealt with in the last six hours waits.

Deep beat (nightly at CBX_HEARTBEAT_DEEP_AT, default 01:30):
    * one tool a night, in turn: "could this be better for the people who use it?" — small things in its draft
    * the models: is every kind of bot on the right one; anything newer on the web that fits the card?
    * the code: the places the day's errors point at (proposals only, with the real file and lines)
    * C-EXA herself: how did the agents do today; keep the lessons; does a team need another agent?

A team that is switched off gets nothing: its work goes to C-EXA herself. Nothing here changes anything live.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from .. import jobs, state
from ..audit import log
from ..branding import ai_name
from ..config import settings
from ..db import SessionLocal, utcnow
from ..models import AgentTeam, AgentWork, AuditLog, CexaMessage, PulseState, TeamMember, User
from . import loop as L

logger = logging.getLogger("cbx.cexa.heartbeat")
KEY = "cexa.heartbeat"
ERRORS = ("server_error", "job_failed", "routine_failed", "flow_failed", "pdf_batch_failed", "proposal_build_failed", "cexa_agent_failed")
COOL_HOURS = 6


def _state(db: Session) -> dict:
    try:
        d = json.loads(state.get(db, KEY) or "{}")
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def _save(db: Session, d: dict) -> None:
    state.set(db, KEY, json.dumps(d, default=str))


def lead_of(db: Session, team_key: str) -> Optional[TeamMember]:
    t = db.query(AgentTeam).filter(AgentTeam.key == team_key, AgentTeam.enabled.is_(True)).first()
    if t is None:
        return None
    return db.query(TeamMember).filter(TeamMember.team_id == t.id, TeamMember.enabled.is_(True)).order_by(TeamMember.lead.desc(), TeamMember.sort).first()


def _busy(db: Session, fp: str) -> bool:
    if db.query(AgentWork).filter(AgentWork.fingerprint == fp, AgentWork.status.in_((L.OPEN, L.WORKING, L.HELD, L.WAITING))).first():
        return True
    cut = utcnow() - timedelta(hours=COOL_HOURS)
    return db.query(AgentWork).filter(AgentWork.fingerprint == fp, AgentWork.finished_at >= cut).first() is not None


def hand(db: Session, team_key: str, fp: str, title: str, detail: str) -> Optional[AgentWork]:
    if _busy(db, fp):
        return None
    m = lead_of(db, team_key)
    w = AgentWork(kind="task", title=title[:300], detail=detail[:4000], to_member_id=m.id if m else None, from_label="heartbeat", source="heartbeat", fingerprint=fp[:80])
    db.add(w)
    db.flush()
    return w


def signals(db: Session, st: dict) -> list[dict]:
    """What is new since the last beat — read from the system, no model."""
    out: list[dict] = []
    for r in db.query(PulseState).filter(PulseState.ok.is_(False), PulseState.bad_streak >= 2).all():
        out.append({"team": "ops", "fp": f"pulse:{r.key}", "title": f"The pulse says {r.key} is wrong", "detail": f"{r.detail}\nReading: {r.value}. Wrong since {r.changed_at}."})
    last = int(st.get("last_error_id") or 0)
    rows = db.query(AuditLog).filter(AuditLog.id > last, AuditLog.action.in_(ERRORS)).order_by(AuditLog.id).limit(300).all()
    groups: dict = {}
    for r in rows:
        g = groups.setdefault((r.action, (r.target or "")[:60]), {"n": 0, "ids": []})
        g["n"] += 1
        g["ids"].append(r.id)
    from . import goals as G

    once = dict(st.get("seen_once") or {})  # 1.0.114: a job or routine that failed once gets one plain retry (its next run) first
    for (action, target), g in list(groups.items())[:8]:
        if G.own_work(db, target):
            continue  # work a goal spawned never opens a goal about itself
        fp = f"err:{action}:{target}"[:80]
        if action in ("job_failed", "routine_failed", "pdf_batch_failed") and g["n"] < 2 and fp not in once:
            once[fp] = utcnow().isoformat()
            continue
        once.pop(fp, None)
        # 1.0.114: a target can be an address someone outside typed (a 500 on a path): only plain characters reach the title,
        # and a server error's goal is tainted (its work reads and reports, never changes by itself)
        target = re.sub(r"[^A-Za-z0-9/_:.-]", "", target)[:60]
        out.append({"team": "fix", "fp": fp, "outside": action == "server_error", "title": f"{action.replace('_', ' ')} at {target or 'the server'} ×{g['n']}",
                    # 1.0.114: system words only — the rows' own text can carry what a person, a mail or a page wrote; the agent reads them with its tools
                    "detail": f"New in the audit trail since the last beat: {action} at {target} happened {g['n']} time(s) (audit rows #{g['ids'][0]}–#{g['ids'][-1]}).\n"
                              "Find the cause (what_changed, search_code/read_code). If a tool's draft can fix it, hand it to Change; if it is core code, a proposal with the real file and lines; if it is a person's to fix, suggest_task_for_person."})
    if rows:
        st["last_error_id"] = rows[-1].id
    from ..models import FlowRun

    since = datetime.fromisoformat(st["last_light"]) if st.get("last_light") else utcnow() - timedelta(hours=1)
    if since.tzinfo is None:
        from datetime import timezone

        since = since.replace(tzinfo=timezone.utc)
    fl: dict = {}
    for r in db.query(FlowRun).filter(FlowRun.status == "failed", FlowRun.dry.is_(False), FlowRun.started_at >= since).limit(100).all():
        k = f"{r.tool}/{r.flow}"
        fl.setdefault(k, {"n": 0, "runs": []})["n"] += 1
        fl[k]["runs"].append(r.id)
    for k, v in list(fl.items())[:5]:
        fp = f"flow:{k}"[:80]
        if v["n"] < 2 and fp not in once:  # 1.0.114: a rule that failed once is watched; failing again is a problem
            once[fp] = utcnow().isoformat()
            continue
        once.pop(fp, None)
        out.append({"team": "fix", "fp": f"flow:{k}"[:80], "title": f"The rule {k} failed ×{v['n']}", "detail": f"The rule (flow) {k} failed {v['n']} time(s) since the last beat (runs #{', #'.join(map(str, v['runs'][:5]))}).\nFind why; a fix to the rule goes in the tool's draft (hand it to Change)."})
    from .teams import broken_tools

    try:
        broken = broken_tools()
    except Exception:  # noqa: BLE001
        broken = []
    for b in broken[:6]:
        out.append({"team": "change", "fp": f"tool:{b['where']}:{b['tool']}"[:80], "title": f"The Builder says {b['tool']} ({b['where']}) has problems",
                    "detail": "Problems:\n- " + "\n- ".join(b["problems"]) + "\nFix what you can in the tool's Builder draft; ask the owner about anything that changes how people work."})
    day = (utcnow() - timedelta(hours=24)).isoformat()
    st["seen_once"] = {k: v for k, v in once.items() if v >= day}
    out += stuck_tickets(db)
    return out


def stuck_tickets(db: Session) -> list[dict]:
    """1.0.114: support tickets nobody has picked up — waiting to be assigned over 12 hours, or assigned and not accepted in
    2 days. Only numbers and references: a ticket's own words are the requester's, never a signal's text."""
    try:
        from ..models import SupportTicket
    except ImportError:
        return []
    now = utcnow()
    try:
        ready = db.query(SupportTicket.id).filter(SupportTicket.status == "ready", SupportTicket.updated_at < now - timedelta(hours=12)).limit(50).all()
        unacc = (db.query(SupportTicket.id).filter(SupportTicket.status == "assigned", SupportTicket.accepted_at.is_(None),
                                                   SupportTicket.assigned_at < now - timedelta(days=2)).limit(50).all())
    except Exception:  # noqa: BLE001 — an older database: no signal
        return []
    ids = sorted({r[0] for r in ready} | {r[0] for r in unacc})
    if not ids:
        return []
    refs = ", ".join(f"T-{i:04d}" for i in ids[:8]) + (" …" if len(ids) > 8 else "")
    return [{"team": "ops", "fp": "support:stuck", "title": f"{len(ids)} support ticket{'s' if len(ids) != 1 else ''} not picked up ({refs})",
             "detail": f"{len(ready)} waiting to be assigned for over 12 hours; {len(unacc)} assigned but not accepted in 2 days. Tickets: {refs}. "
                       "Find who should have them (the system's lead) and remind them, or tell the support lead; never answer the requester yourself.",
             "done_when": "Every one of them is assigned and accepted, or a person knows why not."}]


def light(db: Session, now: Optional[datetime] = None) -> dict:
    """One light beat. Returns {signals, made, unstuck, reminded}."""
    now = now or utcnow()
    st = _state(db)
    found = signals(db, st)
    made = []
    from . import goals as G

    for s in found:  # 1.0.114: a problem is a goal — AXL plans it from the top; one open goal per signal, a cooldown after
        g = G.open_from_signal(db, s)
        if g is not None:
            made.append(g.id)
    unstuck = L.unstick(db)
    reminded = 0
    day = utcnow() - timedelta(hours=24)
    for w in db.query(AgentWork).filter(AgentWork.status == L.WAITING, AgentWork.updated_at < day, AgentWork.goal_id.is_(None)).all():
        if (w.proposal or {}).get("reminded"):
            continue
        w.proposal = {**(w.proposal or {}), "reminded": True}
        L.tell_owner(db, w)
        reminded += 1
    st.update(last_light=now.isoformat(), beats=int(st.get("beats") or 0) + 1, last_signals=[s["title"] for s in found][:12], last_made=made)
    _save(db, st)
    if made:
        log(db, "cexa_heartbeat", actor=L._cexa(db), target="light", detail=f"{len(found)} signal(s), {len(made)} new goal(s): " + "; ".join(s["title"] for s in found)[:300])
    return {"signals": found, "made": made, "unstuck": unstuck, "reminded": reminded}


def deep(db: Session, today: str) -> list[int]:
    """The nightly beat: the work it hands out (ids)."""
    from ..engine import loader
    from . import models_fit as F
    from . import web

    made: list[int] = []
    slugs = sorted(loader.registry().tools)
    if slugs:
        n = int(datetime.fromisoformat(today).strftime("%j"))
        slug = slugs[n % len(slugs)]
        t = loader.get(slug)
        name = t.text(t.manifest.get("title", "tool.title")) if t else slug
        w = hand(db, "change", f"deep:{today}:tool", f"Tonight's look: could {name} be better?",
                 f"Look over the tool {name} ({slug}) with describe_tool. Is anything missing that the people who use it would want — a filter on a busy table, a column for a status, a rule that is off but should be on? "
                 "Make the small, clearly useful changes in its Builder draft (add_filter, add_column, switch_rule — never more than three), then finish saying what you changed and what bigger thing you would do. "
                 "If you are not sure a change is wanted, ask_up instead of making it.")
        if w:
            made.append(w.id)
    gpu = F.gpu_gb()
    w = hand(db, "ops", f"deep:{today}:models", "Tonight's look: is every bot on the right model?",
             f"Use look_at_models. The card has {gpu or 'an unknown amount of'} GB. "
             + ("Then web_search for newer small models that call tools well and run in Ollama (one or two searches, e.g. 'best small ollama model tool calling'). " if web.enabled() else "")
             + "Suggest a model (suggest_model) only where it clearly helps — faster for the same work, or able to do what the bot needs (tools, vision). Never suggest a change for its own sake; if all is well, finish saying so.")
    if w:
        made.append(w.id)
    day = utcnow() - timedelta(hours=24)
    errs = db.query(AuditLog).filter(AuditLog.at >= day, AuditLog.action.in_(ERRORS)).count()
    if errs:
        w = hand(db, "fix", f"deep:{today}:code", f"Tonight's look: the code behind today's {errs} error(s)",
                 "Use what_changed (since 24h) for the errors; for the worst one, search_code / read_code to find the real place. If the cause is in core code, hand it to the core developer of Build "
                 "with the file and lines (they propose the change; the owner ships it). If you cannot find it, say what you looked at.")
        if w:
            made.append(w.id)
    done = db.query(AgentWork).filter(AgentWork.finished_at >= day, AgentWork.kind == "task").count()
    if done or made:
        w = AgentWork(kind="task", title="Tonight's look: how did the agents do today?", from_label="heartbeat", source="heartbeat", fingerprint=f"deep:{today}:self",
                      detail=f"{done} piece(s) of work finished today. Use agents_now. Keep a lesson (note) from what went well or badly. If one kind of work keeps waiting or failing, "
                             "propose_agent for the team that needs help (say why) — or say all is well.")
        if not _busy(db, w.fingerprint):
            db.add(w)
            db.flush()
            made.append(w.id)
    st = _state(db)
    st.update(last_deep=today, last_deep_made=made)
    _save(db, st)
    log(db, "cexa_heartbeat", actor=L._cexa(db), target="deep", detail=f"{len(made)} piece(s) of work for tonight")
    return made


def due(st: dict, now: datetime, local: datetime) -> tuple[bool, bool]:
    last = st.get("last_light")
    light_due = True
    if last:
        try:
            t = datetime.fromisoformat(last)
            light_due = (now - (t if t.tzinfo else t.replace(tzinfo=now.tzinfo))).total_seconds() >= max(1, settings.heartbeat_minutes) * 60 - 5
        except ValueError:
            light_due = True
    hh, _, mm = (settings.heartbeat_deep_at or "01:30").partition(":")
    try:
        at = local.replace(hour=int(hh), minute=int(mm or 0), second=0, microsecond=0)
    except ValueError:
        at = local.replace(hour=1, minute=30, second=0, microsecond=0)
    deep_due = local >= at and st.get("last_deep") != local.date().isoformat()
    return light_due, deep_due


def beat(db: Session, *, force_light: bool = False) -> dict:
    from ..engine import flows as FL

    st = _state(db)
    now, local = utcnow(), FL.local_now()
    if not st.get("last_light") and not st.get("last_deep"):
        st["last_deep"] = local.date().isoformat()  # the very first beat: the deep look starts tonight, not in the middle of a working day
        _save(db, st)
    light_due, deep_due = due(st, now, local)
    out: dict = {}
    if light_due or force_light:
        out["light"] = light(db, now)
    if deep_due and not force_light:
        out["deep"] = deep(db, local.date().isoformat())
    return out


@jobs.routine("cexa-heartbeat", 60)
def heartbeat_routine() -> None:
    if not settings.heartbeat:
        return
    from sqlalchemy.exc import OperationalError

    db = SessionLocal()
    try:
        beat(db)
        db.commit()
    except OperationalError as e:
        db.rollback()
        if "locked" not in str(e).lower():
            raise
    finally:
        db.close()


def status(db: Session) -> dict:
    """What the Now page shows at the top: her heartbeat, what she is doing, what waits for the owner."""
    st = _state(db)
    working = db.query(AgentWork).filter(AgentWork.status == L.WORKING).order_by(AgentWork.started_at.desc()).first()
    counts = {k: db.query(AgentWork).filter(AgentWork.status == k).count() for k in (L.OPEN, L.WORKING, L.HELD)}
    from . import goals as G

    counts[L.WAITING] = G.owner_waiting_count(db)  # 1.0.114: only what a person must answer (a goal's stuck work is AXL's)
    today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    done_today = db.query(AgentWork).filter(AgentWork.finished_at >= today, AgentWork.status == "done").count()
    if working is not None:
        mood = f"{L._who(db, working.to_member_id)} is working on: {working.title}"
    elif counts[L.WAITING]:
        mood = f"Waiting for you on {counts[L.WAITING]} thing{'s' if counts[L.WAITING] != 1 else ''}."
    elif counts[L.OPEN] or counts[L.HELD]:
        mood = f"{counts[L.OPEN] + counts[L.HELD]} piece(s) of work lined up."
    else:
        mood = "All quiet. I'm watching."
    nxt = None
    if st.get("last_light"):
        try:
            nxt = (datetime.fromisoformat(st["last_light"]) + timedelta(minutes=settings.heartbeat_minutes)).isoformat()
        except ValueError:
            nxt = None
    from . import web

    return {"on": settings.heartbeat, "every_minutes": settings.heartbeat_minutes, "deep_at": settings.heartbeat_deep_at, "last_beat": st.get("last_light"),
            "next_beat": nxt, "beats": st.get("beats", 0), "last_signals": st.get("last_signals", []), "last_deep": st.get("last_deep"), "mood": mood,
            "counts": counts, "done_today": done_today, "calls_today": L.calls_today(db), "calls_cap": settings.agent_calls_per_day,
            "web": web.enabled(), "web_why": "" if web.enabled() else web.why_off(), "ai": ai_name()}
