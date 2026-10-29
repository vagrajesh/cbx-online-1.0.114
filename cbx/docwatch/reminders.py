"""Hourly: C-EXA leaves a 'needs you' message for the person who filed a document (and the managers and
admins of the company's team) when it enters its warning window (warn_days), 30 and 7 days before it
expires, and again on the day. Each stage goes once — recorded in WatchedDoc.reminders_sent as the
stage's day count ("90", "30", "7", "0"). Cells marked "not required" or "doesn't expire", and archived
ones, are never in the cycle. Every date is worked out in the business's own day (local_today), not UTC."""
from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from .. import mail, state
from ..audit import log
from ..db import session_scope
from ..jobs import routine
from ..models import CexaMessage, User, WatchedDoc
from ..models.docs import Entity
from ..util import human_date, local_today

STAGES = (30, 7, 0)


def stages_for(doc: WatchedDoc) -> tuple[int, ...]:
    """The fixed stages plus the document's own warning window, furthest-out first."""
    return tuple(sorted({int(doc.warn_days or 30), *STAGES}, reverse=True))


def _recipients(db: Session, doc: WatchedDoc, ent: Optional[Entity]) -> list[User]:
    people: dict[int, User] = {}
    owner = db.get(User, doc.owner_id)
    if owner is not None and owner.is_active:
        people[owner.id] = owner
    # The controller answerable for this company, when they have an account here.
    if ent is not None and ent.responsible_user_id:
        who = db.get(User, ent.responsible_user_id)
        if who is not None and who.is_active and not who.is_axl:
            people[who.id] = who
    team_id = ent.team_id if ent is not None else doc.team_id
    if team_id:
        for a in db.query(User).filter(User.team_id == team_id, User.role.in_(["admin", "manager"]), User.is_active.is_(True), User.is_axl.is_(False)).all():
            people[a.id] = a
    return list(people.values())


def _extra_addresses(ent: Optional[Entity]) -> list[str]:
    """Controllers with no account here. Most of the finance list is in this position, and a name on
    the grid with nobody reachable behind it is how a certificate quietly lapses."""
    if ent is None or not ent.responsible_email:
        return []
    return [ent.responsible_email] if mail.valid(ent.responsible_email) else []


def _text(doc: WatchedDoc, company: str, days: int) -> tuple[str, str]:
    what = f"{doc.title}" + (f" ({company})" if company else "")
    when = human_date(doc.expiry) if doc.expiry else ""
    if days < 0:
        return f"{doc.title} has expired", f"{what} expired on {when} ({-days} day{'s' if days != -1 else ''} ago). Drop the renewed certificate onto its cell in Entity Compliance and I'll file it."
    if days == 0:
        return f"{doc.title} expires today", f"{what} expires today, {when}. Drop the new one onto its cell in Entity Compliance when it arrives."
    return f"{doc.title} expires in {days} day{'s' if days != 1 else ''}", f"{what} expires on {when} — {days} day{'s' if days != 1 else ''} from now. When the renewal comes in, drop it onto its cell in Entity Compliance and I'll update the date."


def _digest(lines: list[tuple[int, str, str]]) -> tuple[str, str]:
    """One email listing everything that moved for this person today.

    Not one mail per certificate: a controller can hold thirty entities across twelve columns, and a
    morning of twenty separate emails is a morning of twenty deleted emails."""
    lines = sorted(lines, key=lambda x: x[0])  # soonest (or most overdue) first
    overdue = [t for d, t, _ in lines if d < 0]
    today_ = [t for d, t, _ in lines if d == 0]
    n = len(lines)
    if n == 1:
        subject = lines[0][1]
    elif overdue:
        subject = f"{len(overdue)} document{'s' if len(overdue) != 1 else ''} expired, {n} needing attention"
    else:
        subject = f"{n} document{'s' if n != 1 else ''} expiring"
    body = ["Certificates on the Entity Compliance grid that need you:", ""]
    body += [f"  - {text}" for _, _, text in lines]
    body += ["", "Drop each renewal onto its cell in Entity Compliance and C-EXA will read the new date off it.",
             "", "You are getting this because you filed the document, you are the responsible person for the",
             "company, or you manage the team it belongs to."]
    if today_ or overdue:
        body.insert(1, "")
    return subject, "\n".join(body)


def run_once(db: Session, today: Optional[date] = None) -> int:
    today = today or local_today()
    sent = 0
    # person id (or a bare address) -> the lines destined for them this run
    digest: dict[object, list[tuple[int, str, str]]] = {}
    addresses: dict[object, str] = {}
    docs = db.query(WatchedDoc).filter(WatchedDoc.archived.is_(False), WatchedDoc.required.is_(True), WatchedDoc.expiry.isnot(None), WatchedDoc.no_expiry.is_(False)).all()
    ents = {e.id: e for e in db.query(Entity).filter(Entity.id.in_({d.entity_id for d in docs if d.entity_id} or {0})).all()}
    for doc in docs:
        days = (doc.expiry - today).days
        done = set(str(x) for x in (doc.reminders_sent or []))
        stages = stages_for(doc)
        stage = None
        for s in stages:
            if days <= s and str(s) not in done:
                stage = s  # the nearest stage we have reached wins; the ones we skipped past count as done
        if stage is None:
            continue
        ent = ents.get(doc.entity_id) if doc.entity_id else None
        title, text = _text(doc, ent.name if ent else doc.entity, days)
        link = f"#/doc-watch-entity?id={ent.id}" if ent else "#/doc-watch"
        for who in _recipients(db, doc, ent):
            db.add(CexaMessage(user_id=who.id, from_axl=True, kind="needs_you", title=title, text=text, link=link, meta={"doc_id": doc.id, "entity_id": doc.entity_id, "stage": stage}))
            sent += 1
            if mail.valid(who.email):
                digest.setdefault(who.id, []).append((days, title, text))
                addresses[who.id] = who.email
        for addr in _extra_addresses(ent):
            digest.setdefault(addr, []).append((days, title, text))
            addresses[addr] = addr
        doc.reminders_sent = sorted(done | {str(s) for s in stages if days <= s}, key=lambda x: -int(x))
        db.add(doc)
        log(db, "docwatch_reminder", actor_label="axl", target=doc.title, detail=f"{days} days left · stage {stage}")
    db.commit()  # the reminders are recorded; the emails below take seconds each and need no lock
    _mail_digests(db, digest, addresses)
    return sent


def _mail_digests(db: Session, digest: dict, addresses: dict) -> None:
    """Send one email per person. A failure is recorded and never raised — the in-app message has
    already been written, so a mail server being down must not cost anyone their reminder."""
    if not digest:
        return
    ready, why = mail.configured()
    if not ready:
        log(db, "docwatch_reminder_mail", actor_label="axl", target="email", detail=f"{len(digest)} reminder email(s) not sent — {why}", outcome="skipped")
        return
    from .. import mailbox

    ok = fail = 0
    for key, lines in digest.items():
        subject, body = _digest(lines)
        # through the outbox (Admin → Mail lists it); record_send inside proves the mailbox when it lands
        r = mailbox.send(db, to=[addresses[key]], subject=subject, body=body + "\n" + mailbox.footer(db, None, "#/doc-watch"), kind="reminder", check=False, hold="reminder")
        if r.ok:
            ok += 1
        elif r.detail.startswith("draft:"):
            pass  # 1.0.71: waiting in Mailer → Drafts for a person to check
        else:
            fail += 1
            log(db, "docwatch_reminder_mail", actor_label="axl", target=addresses[key], detail=r.detail, outcome="failed")
        db.commit()  # one row per email, released before the next SMTP conversation
    log(db, "docwatch_reminder_mail", actor_label="axl", target="email", detail=f"{ok} sent, {fail} failed", outcome="ok" if not fail else "failed")
    db.flush()


@routine("docwatch-reminders", 3600)
def reminders_routine() -> None:
    from .copy_in import use_tool

    with session_scope() as db:
        if use_tool(db):
            return  # 1.0.63: the tool folder's reminders (cbx/docwatch/tool.py) send them now
        run_once(db)
