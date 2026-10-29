"""Requests → assessment → interview → ticket → a tool folder that answers it. The shared service
behind cbx.api.requests_api, cbx.api.interviews and cbx.api.tickets.

Everything that changes a Request, Rule, Interview or Ticket lives here so the status mirror
(ticket ↔ request) and the audit trail stay consistent no matter which page triggered it."""
from __future__ import annotations

import re
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from .audit import log
from .cexa import context as AC
from .cexa.gateway import Gateway
from .cexa.persona import persona
from .db import SessionLocal, utcnow
from .util import clip
from .models import CexaMessage, Interview, InterviewMessage, Request, Rule, Ticket, User
from .api.schemas import iso, user_out

FIRST_NUMBER = 2001
STATUS_WORDS = {"sent": "Sent · being assessed", "new": "New", "interview": "Interview", "working": "Working", "testing": "Testing", "ready": "Ready", "declined": "Declined", "cancelled": "Cancelled"}


# ----------------------------------------------------------------------------- helpers
def cexa_user(db: Session) -> User:
    return db.query(User).filter(User.is_axl.is_(True)).one()


def first_name(u: Optional[User]) -> str:
    return (u.name.split()[0] if u and u.name else "there")


def next_number(db: Session) -> int:
    m = db.query(func.max(Request.number)).scalar()
    return (m or FIRST_NUMBER - 1) + 1


def notify(db: Session, user: User, title: str, text: str, link: str = "#/messages", kind: str = "needs_you") -> CexaMessage:
    """A note from C-EXA in the person's 1-on-1 inbox (drives the Messages badge)."""
    m = CexaMessage(user_id=user.id, from_axl=True, kind=kind, title=title, text=text, link=link)
    db.add(m)
    db.flush()
    return m


# ----------------------------------------------------------------------------- serialisers
def rule_out(r: Rule) -> dict:
    return {"id": r.id, "ordinal": r.ordinal, "text": r.text, "source": r.source, "changed_by_user": r.changed_by_user, "green": r.green, "active": r.active, "history": r.history or []}


def message_out(m: InterviewMessage) -> dict:
    return {"id": m.id, "at": iso(m.at), "sender": m.sender, "sender_id": m.sender_id, "text": m.text, "rule_ids": m.rule_ids or []}


def interview_out(db: Session, iv: Interview, *, with_messages: bool = True) -> dict:
    req = iv.request
    lead = db.get(User, iv.lead_id) if iv.lead_id else None
    d = {
        "id": iv.id, "request_id": req.id, "number": req.number, "title": req.title or req.problem[:60], "mode": iv.mode, "status": iv.status,
        "lead": user_out(lead) if lead else None, "summary": iv.summary, "confidence_now": iv.confidence_now, "plan": iv.plan or [],
        "started_at": iso(iv.started_at), "finished_at": iso(iv.finished_at), "request_status": req.status,
        "ticket_id": req.ticket.id if req.ticket else None, "requester": user_out(db.get(User, req.user_id)),
        "last": message_out(iv.messages[-1]) if iv.messages else None,
    }
    if with_messages:
        d["messages"] = [message_out(m) for m in iv.messages]
        d["rules"] = [rule_out(r) for r in req.rules if r.active]
    return d


def ticket_out(db: Session, t: Ticket, *, full: bool = False) -> dict:
    from .engine import loader

    req = t.request
    workers = []
    for w in t.workers or []:
        if w == "axl":
            workers.append({"id": "axl", "name": "C-EXA", "initials": "AX", "is_axl": True})
        elif (u := db.get(User, w)):
            workers.append({"id": u.id, "name": u.name, "initials": u.initials(), "is_axl": False})
    reg = loader.registry()
    live = reg.tools.get(t.tool_slug) if t.tool_slug else None
    d = {
        "id": t.id, "request_id": req.id, "number": req.number, "title": t.title, "status": t.status, "summary": t.summary,
        "requester": user_out(db.get(User, req.user_id)), "workers": workers, "published_at": iso(t.published_at), "created_at": iso(t.created_at),
        "interview_id": req.interview.id if req.interview else None, "origin": req.origin or "user", "cadence": req.cadence,
        "tool_slug": t.tool_slug or "",
        "tool": {"slug": live.slug, "title": live.text(live.manifest.get("title", "tool.title")), "home": live.manifest.get("home") or next(iter(live.pages), ""), "live": True} if live else None,
    }
    if full:
        d["request"] = request_out(db, req, full=True)
        d["wants_back"] = wants_back(req)
    return d


def request_out(db: Session, r: Request, *, full: bool = False, admin: bool = False) -> dict:
    d = {
        "id": r.id, "number": r.number, "title": r.title or r.problem[:60], "status": r.status, "status_word": STATUS_WORDS.get(r.status, r.status),
        "created_at": iso(r.created_at), "updated_at": iso(r.updated_at), "interview_id": r.interview.id if r.interview else None,
        "interview_mode": r.interview_mode, "ticket_id": r.ticket.id if r.ticket else None, "rule_count": sum(1 for x in r.rules if x.active),
        "requester": user_out(db.get(User, r.user_id)), "origin": r.origin or "user",
        "result_kind": r.result_kind, "cadence": r.cadence,  # My requests shows "You get" from the list call
    }
    assignee = db.get(User, r.assignee_id) if r.assignee_id else None
    d["assignee"] = user_out(assignee) if assignee else None
    if full:
        d.update(problem=r.problem, impact=r.impact, wish=r.wish, attachment_id=r.attachment_id, decline_note=r.decline_note,
                 result_kind=r.result_kind, cadence=r.cadence, recipients=r.recipients, done_looks_like=r.done_looks_like,
                 rules=[rule_out(x) for x in r.rules if x.active])
    if admin:
        d.update(importance=r.importance, importance_reason=r.importance_reason, confidence=r.confidence, confidence_reason=r.confidence_reason,
                 would_ask=r.would_ask, wont_guess=r.wont_guess, assessed_at=iso(r.assessed_at), approved_at=iso(r.approved_at),
                 interview_status=r.interview.status if r.interview else None)
    return d


# ----------------------------------------------------------------------------- assessment
def _live_titles(db: Session, exclude_id: int) -> list[str]:
    reqs = db.query(Request).filter(Request.id != exclude_id, Request.status.notin_(["declined"])).order_by(Request.created_at.desc()).limit(40).all()
    return [f"#{x.number} {x.title or x.problem[:60]} ({STATUS_WORDS.get(x.status, x.status)})" for x in reqs]


def assess(db: Session, req: Request) -> None:
    """C-EXA reads a fresh request: importance, confidence she could do it alone, a short title."""
    system = persona(
        "Task: assess a new request. Score importance as one of low|medium|high|critical with a one-paragraph reason "
        "(money at risk, how often, how many people). Give confidence 0..1 that you could complete this yourself with NO "
        "guessing — explain what you'd still need to ask the person (would_ask) and what you will never guess (wont_guess). "
        "Propose a short plain-English title (under 60 characters). If it overlaps a live request or ticket, say so in importance_reason."
    )
    # What CBX actually holds, so the app's own nouns ("entity", "column", "Mozambique") reach the
    # model as the things they are. Without this C-EXA asked a controller what an "entity" was.
    user = (
        AC.for_request(db)
        + f"\n\n---\n\nProblem: {req.problem}\n\nImpact: {req.impact}\n\nHow they'd like it fixed: {req.wish}"
        + "\n\nExisting live requests and tickets (for overlap):\n"
        + ("\n".join(_live_titles(db, req.id)) or "(none)")
    )
    out = Gateway(db).json(task="assess", system=system, user=user, schema={"importance": "low|medium|high|critical", "importance_reason": "str", "confidence": "float 0..1", "confidence_reason": "str", "would_ask": "str", "wont_guess": "str", "title": "str"})
    imp = str(out.get("importance", "medium")).lower()
    req.importance = imp if imp in ("low", "medium", "high", "critical") else "medium"
    req.importance_reason = str(out.get("importance_reason", ""))
    try:
        req.confidence = max(0.0, min(1.0, float(out.get("confidence", 0.5))))
    except (TypeError, ValueError):
        req.confidence = 0.5
    req.confidence_reason = str(out.get("confidence_reason", ""))
    req.would_ask = str(out.get("would_ask", ""))
    req.wont_guess = str(out.get("wont_guess", ""))
    if not req.title:
        req.title = clip(str(out.get("title", "")), 200) or clip(req.problem, 60)
    req.assessed_at = utcnow()
    req.status = "new"
    log(db, "request_assessed", actor=cexa_user(db), target=f"request:{req.number}", detail=f"importance={req.importance} confidence={req.confidence:.2f}")


def assess_job(request_id: int) -> None:
    """Background entry point (own session)."""
    db = SessionLocal()
    try:
        req = db.get(Request, request_id)
        if req and req.status == "sent":
            try:
                assess(db, req)
            except Exception as e:  # noqa: BLE001 — a model hiccup must not leave the request stuck on "Sent"
                db.rollback()
                req = db.get(Request, request_id)
                req.status = "new"
                req.importance = req.importance or "medium"
                req.importance_reason = req.importance_reason or "C-EXA couldn't read this request (the model wasn't available). Please assess it yourself."
                req.confidence_reason = req.confidence_reason or "Not assessed — C-EXA was unavailable."
                req.assessed_at = utcnow()
                log(db, "request_assessed", actor=cexa_user(db), target=f"request:{req.number}", detail=f"assessment failed: {str(e)[:160]}", outcome="failed")
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        raise
    finally:
        db.close()


# ----------------------------------------------------------------------------- rules
def add_rule(db: Session, req: Request, text: str, *, source: str, actor: User, by_user: bool = False) -> Rule:
    text = text.strip()
    ordinal = (max((r.ordinal for r in req.rules), default=0) + 1)
    rule = Rule(request_id=req.id, ordinal=ordinal, text=text, source=source, changed_by_user=False, history=[{"at": utcnow().isoformat(), "by": actor.name, "text": text}])
    db.add(rule)
    db.flush()
    db.refresh(req)
    log(db, "rule_added", actor=actor, target=f"request:{req.number}", detail=f"R{ordinal} ({source}): {text[:120]}")
    return rule


def edit_rule(db: Session, rule: Rule, text: str, *, actor: User, by_user: bool) -> Rule:
    text = text.strip()
    if text and text != rule.text:
        rule.history = (rule.history or []) + [{"at": utcnow().isoformat(), "by": actor.name, "text": text}]
        rule.text = text
        if by_user:
            rule.changed_by_user = True
        log(db, "rule_changed", actor=actor, target=f"request:{rule.request.number}", detail=f"R{rule.ordinal}: {text[:120]}" + (" (by the requester)" if by_user else ""))
    return rule


def remove_rule(db: Session, rule: Rule, *, actor: User) -> None:
    rule.active = False
    log(db, "rule_removed", actor=actor, target=f"request:{rule.request.number}", detail=f"R{rule.ordinal}: {rule.text[:120]}")


def rules_text(req: Request) -> str:
    return "\n".join(f"R{r.ordinal}: {r.text}" for r in req.rules if r.active) or "(none yet)"


# ----------------------------------------------------------------------------- interview
def greeting(req: Request, requester: User) -> str:
    return (f"Hi {first_name(requester)}, I'm C-EXA. Your request “{req.title or req.problem[:60]}” was approved and I've been asked to work out "
            f"the detail with you. It'll take about ten minutes — five or six questions, one at a time. Ready?")


def say(db: Session, iv: Interview, text: str, *, sender: str = "axl", sender_id: Optional[int] = None, rule_ids: Optional[list] = None) -> InterviewMessage:
    m = InterviewMessage(interview_id=iv.id, sender=sender, sender_id=sender_id, text=text, rule_ids=rule_ids or [])
    db.add(m)
    db.flush()
    db.refresh(iv)
    return m


def open_interview(db: Session, req: Request, *, mode: str, assignee: Optional[User], actor: User) -> Interview:
    requester = db.get(User, req.user_id)
    iv = Interview(request_id=req.id, mode=mode, lead_id=(assignee.id if assignee else None), status="open")
    db.add(iv)
    db.flush()
    db.refresh(req)
    req.status = "interview"
    req.interview_mode = mode
    req.assignee_id = assignee.id if assignee else None
    req.approved_by_id = actor.id
    req.approved_at = utcnow()
    log(db, "request_approved", actor=actor, target=f"request:{req.number}", detail=f"mode={mode} assignee={assignee.email if assignee else '-'}")
    if mode == "axl":
        text = greeting(req, requester)
        say(db, iv, text)
        log(db, "interview_turn", actor=cexa_user(db), target=f"interview:{iv.id}", detail="opened with greeting")
        notify(db, requester, f"Your request #{req.number} was approved", "I'd like to ask you a few questions — open Messages when you have ten minutes.", link="#/messages?interview=%d" % iv.id)
    else:
        who = first_name(assignee) if assignee else "someone from the team"
        notify(db, requester, f"Your request #{req.number} was approved", f"{who} will set up a short meeting with you to work out the detail.", link="#/requests")
    return iv


def transcript(iv: Interview) -> str:
    lines = []
    for m in iv.messages:
        who = "C-EXA" if m.sender == "axl" else "USER" if m.sender == "user" else "ADMIN"
        lines.append(f"{who}: {m.text}")
    return "\n".join(lines)


def _fill_wants(req: Request, iv: Interview) -> None:
    """Best-effort: read result kind / cadence / recipients / done from the person's answers."""
    answers = [m.text for m in iv.messages if m.sender == "user"]
    blob = " ".join(answers + [req.wish]).lower()
    if not req.cadence:
        req.cadence = "monthly" if re.search(r"month", blob) else "weekly" if re.search(r"week", blob) else "daily" if "daily" in blob or "every day" in blob else "on_file" if re.search(r"(when|as) .*(arriv|land|comes in)", blob) else "once"
    if not req.result_kind:
        req.result_kind = "filled_sheet" if "sheet" in blob else "excel" if "excel" in blob or "xlsx" in blob else "pdf_report" if "pdf" in blob or "report" in blob else "check" if re.search(r"\b(check|flag|alert)", blob) else "check"
    if not req.recipients or not req.done_looks_like:
        # the last thing they said usually answers "who gets it / what is done"
        for m in reversed(iv.messages):
            if m.sender == "user":
                if not req.recipients:
                    req.recipients = m.text[:255]
                if not req.done_looks_like:
                    req.done_looks_like = m.text
                break


def wants_back(req: Request) -> str:
    kind = {"filled_sheet": "Filled sheet", "excel": "Excel file", "pdf_report": "PDF report", "check": "A check with flags"}.get(req.result_kind, req.result_kind or "—")
    cad = {"once": "once", "monthly": "every month", "weekly": "every week", "daily": "every day", "on_file": "whenever a file arrives"}.get(req.cadence, req.cadence or "")
    parts = [kind]
    if cad:
        parts.append(cad)
    if req.recipients:
        parts.append(f"to {req.recipients}")
    return " · ".join(parts)


def cexa_turn(db: Session, iv: Interview, *, user: User) -> dict:
    """The person just spoke. C-EXA captures a rule (if there is one), asks the next question, or wraps up."""
    req = iv.request
    cexa = cexa_user(db)
    followup = iv.status == "done"
    tail = (
        "This interview is finished and a ticket exists. [follow-up] The person is messaging you about it later. Reply briefly and helpfully as "
        "C-EXA; put your reply in `question`. If they are changing or adding a rule, put the rule in `rule` (else empty). done is always true."
        if followup
        else "Interview the person about their request. Ask ONE question at a time. If the previous answer contains a business rule, restate it "
        "briefly in `rule` (else empty). Ask about who gets the result, how often, and what done looks like before you finish, if not yet known. "
        "When the person says they are happy or done, return done=true with a short plain-English summary of what you will build."
    )
    # Same vocabulary as the assessment: the interview is where a wrong question costs the most,
    # because the person is sitting there waiting for it.
    system = persona(tail) + "\n" + AC.for_request(db) + f"\n\nThe request:\nTitle: {req.title}\nProblem: {req.problem}\nImpact: {req.impact}\nWish: {req.wish}\n\nRules so far:\n{rules_text(req)}"
    db.commit()  # the person's message is saved before C-EXA thinks: never hold the write lock through a model call
    out = Gateway(db).json(task="interview", system=system, user=transcript(iv), schema={"done": "bool", "question": "str", "rule": "str", "summary": "str"})
    rule_ids: list[int] = []
    rule_txt = str(out.get("rule") or "").strip()
    if rule_txt:
        r = add_rule(db, req, rule_txt, source="user", actor=cexa)
        rule_ids.append(r.id)
    done = bool(out.get("done")) and not followup
    if done:
        iv.summary = str(out.get("summary") or "")
        iv.status = "done"
        iv.finished_at = utcnow()
        _fill_wants(req, iv)
        ticket = create_ticket(db, req, actor=cexa)
        assignee = db.get(User, req.assignee_id) if req.assignee_id else None
        text = (f"Thank you — that was quick. Here's what I'll build:\n\n{iv.summary}\n\nRules I captured: " + ", ".join(f"R{r.ordinal}" for r in req.rules if r.active) +
                f".\n\nI've created build ticket #{req.number} for {first_name(assignee) if assignee else 'the team'}. If anything changes, message me here — this conversation stays attached to the ticket.")
        msg = say(db, iv, text, rule_ids=rule_ids)
        log(db, "interview_done", actor=cexa, target=f"interview:{iv.id}", detail=f"{len(rule_ids)} rule(s) this turn; ticket {ticket.id}")
    else:
        q = str(out.get("question") or "").strip() or ("Noted." if followup else "Could you tell me a bit more?")
        prefix = ""
        if rule_txt and not followup:
            prefix = f"Got it — R{req.rules[-1].ordinal}: {rule_txt}\n\n"
        msg = say(db, iv, prefix + q, rule_ids=rule_ids)
        log(db, "interview_turn", actor=cexa, target=f"interview:{iv.id}", detail=("follow-up" if followup else "question") + (f"; rule R{req.rules[-1].ordinal}" if rule_txt else ""))
        if followup and rule_txt and req.ticket:
            for w in req.ticket.workers or []:
                if w != "axl" and (u := db.get(User, w)):
                    notify(db, u, f"{first_name(user)} changed a rule on #{req.number}", rule_txt, link=f"#/build?id={req.ticket.id}", kind="team")
    return {"message": message_out(msg), "done": iv.status == "done", "rules": [rule_out(r) for r in req.rules if r.active], "ticket_id": req.ticket.id if req.ticket else None}


def suggest(db: Session, iv: Interview) -> dict:
    req = iv.request
    system = persona("A person is interviewing the requester; you are listening. From the rules so far, suggest up to three questions the interviewer should ask next, "
                     "each with a one-word reason (Gap / Overlap / Because of R<n>). Give your confidence 0..1 that you could build this now, and name the gaps.")
    user = f"Request: {req.title}\nProblem: {req.problem}\nWish: {req.wish}\n\nRules so far:\n{rules_text(req)}\n\nAlready asked:\n" + ("\n".join(p.get("q", "") for p in (iv.plan or [])) or "(nothing)")
    out = Gateway(db).json(task="interview_suggest", system=system, user=user, schema={"questions": [{"why": "str", "text": "str"}], "confidence": "float", "gaps": "str"})
    try:
        iv.confidence_now = max(0.0, min(1.0, float(out.get("confidence", 0.5))))
    except (TypeError, ValueError):
        iv.confidence_now = 0.5
    qs = [{"why": str(q.get("why", "")), "text": str(q.get("text", ""))} for q in (out.get("questions") or []) if isinstance(q, dict)][:3]
    return {"questions": qs, "confidence": iv.confidence_now, "gaps": str(out.get("gaps", "")), "was": req.confidence}


def finish_human(db: Session, iv: Interview, *, actor: User) -> Ticket:
    req = iv.request
    requester = db.get(User, req.user_id)
    iv.status = "done"
    iv.finished_at = utcnow()
    iv.summary = f"Agreed in a meeting with {first_name(requester)} led by {actor.name}: " + "; ".join(r.text for r in req.rules if r.active)
    _fill_wants(req, iv)
    ticket = create_ticket(db, req, actor=actor)
    say(db, iv, f"Hi {first_name(requester)}, I'm C-EXA. Thanks for meeting {first_name(actor)}. Here's what we'll build from your rules:\n\n" + "\n".join(f"R{r.ordinal}: {r.text}" for r in req.rules if r.active) +
        f"\n\nI've created build ticket #{req.number}. If anything changes, message me here — this conversation stays attached to the ticket.")
    notify(db, requester, f"Ticket #{req.number} created from your meeting", "The rules you agreed are in the ticket. Message me if anything changes.", link=f"#/messages?interview={iv.id}")
    log(db, "interview_done", actor=actor, target=f"interview:{iv.id}", detail=f"human mode; ticket {ticket.id}")
    return ticket


def relay_question(db: Session, iv: Interview, *, admin: User, text: str) -> InterviewMessage:
    req = iv.request
    requester = db.get(User, req.user_id)
    m = say(db, iv, f"{first_name(admin)}, who is building this, asks: {text.strip()}", sender="axl")
    notify(db, requester, f"A question about your request #{req.number}", text.strip()[:200], link=f"#/messages?interview={iv.id}")
    log(db, "interview_question_relayed", actor=admin, target=f"interview:{iv.id}", detail=text[:160])
    return m


# ----------------------------------------------------------------------------- tickets
def create_ticket(db: Session, req: Request, *, actor: User) -> Ticket:
    if req.ticket:
        return req.ticket
    summary = "Rules:\n" + rules_text(req) + "\n\nWants back: " + wants_back(req) + (f"\nDone looks like: {req.done_looks_like}" if req.done_looks_like else "")
    workers = [w for w in [req.assignee_id] if w] + ["axl"]
    t = Ticket(request_id=req.id, title=req.title or clip(req.problem, 60), summary=summary, status="working", workers=workers)
    db.add(t)
    db.flush()
    db.refresh(req)
    req.status = "working"
    log(db, "ticket_created", actor=actor, target=f"ticket:{t.id}", detail=f"from request #{req.number}; {sum(1 for r in req.rules if r.active)} rule(s)")
    return t


def ticket_closed(t: Ticket) -> bool:
    """A ticket whose request was cancelled (or declined) must not be drafted, tested or published —
    doing so would silently re-open the request."""
    return t.status == "cancelled" or t.request.status in ("cancelled", "declined")


def set_ticket_status(db: Session, t: Ticket, status: str, *, actor: User, detail: str = "") -> None:
    """The one place a ticket's status changes. Mirrors onto the request and audits both."""
    if status not in ("working", "testing", "ready"):
        raise ValueError(status)
    old = t.status
    t.status = status
    t.request.status = status
    if status == "ready":
        t.published_at = utcnow()
    log(db, "ticket_status", actor=actor, target=f"ticket:{t.id}", detail=f"{old} → {status}; request #{t.request.number} follows" + (f"; {detail}" if detail else ""))


def related(db: Session, req: Request) -> list[dict]:
    """Live tickets/requests whose titles share a keyword — a simple overlap hint for the approver."""
    stop = {"the", "and", "for", "into", "with", "from", "per", "a", "an", "of", "to", "in", "on", "my", "our", "all", "every"}
    words = {w for w in re.findall(r"[a-z0-9]+", (req.title + " " + req.problem[:120]).lower()) if len(w) > 2 and w not in stop}
    out = []
    for other in db.query(Request).filter(Request.id != req.id, Request.status.notin_(["declined", "sent"])).order_by(Request.created_at.desc()).limit(200):
        ow = {w for w in re.findall(r"[a-z0-9]+", (other.title or other.problem[:60]).lower()) if len(w) > 2 and w not in stop}
        hit = words & ow
        if len(hit) >= 2:
            out.append({"id": other.id, "number": other.number, "title": other.title or other.problem[:60], "status": other.status, "ticket_id": other.ticket.id if other.ticket else None, "shared": sorted(hit)[:4]})
    return out[:5]

