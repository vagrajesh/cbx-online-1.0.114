"""1.0.103 (from CBX-Connect 2.10.8): the support team sees its own tickets and the free ones, and takes a free one.

Edwin (19 Oct): "the ticketer: if it's not assigned, another person could close it — make it so they can only see theirs
that has been assigned" and "let them grab any unassigned tickets, so if they can do it, they can take it and finish it".

    desk managers  the people who may assign (core.can_assign: the owner and the admins): the whole desk, as before
    support staff  everyone else on the desk (the support leads on Support → Settings, and the Support desk grant): the
                   tickets assigned to them (any status, closed ones too) and the FREE ones (no one has it, not closed) —
                   whatever the system. Someone else's ticket is not there for them: the dashboard, the queue, its counts,
                   the Mailer, a direct link or API call (404, not 403). Their own requests stay theirs (My tickets).
    take           a free ticket → theirs: the assign path (core.assign: the lead's mail, the requester is told), accepted
                   at once (they chose it: the SLA starts and it goes on their To-do); a note "Taken by …"; audit
                   support_taken. One conditional update that lands only while the ticket is still free, so of two people
                   pressing at once the second hears "Already taken by <name>."
    give back      their own open ticket → free again (ready to assign): their To-do item removed, the mail's Accept link
                   dead (Mark as done dies with the assignment), a note "Given back by …"; audit support_released
Only the assignee (and the managers) changes the status, closes, writes to the requester (in CBX or by replying to the
ticket mail) or uses the mail's Mark as done (core.can_work, core.read_done_token, inbox.take).
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..audit import log
from ..db import utcnow
from ..models import SupportTicket, User

TAKEABLE = ("new", "pending", "ready")  # no one has it yet (core.assign moves these to "assigned")
RACE_WINDOW = timedelta(minutes=15)  # "Already taken by …" names the taker only for a take this recent (else: no such ticket)


class Taken(Exception):
    """The ticket isn't free any more: someone took it (or a manager assigned it) first."""

    def __init__(self, name: str):
        super().__init__(f"Already taken by {name}.")
        self.name = name


def restricted(db: Session, u: Optional[User]) -> bool:
    """On the desk (a support lead or the Support desk grant) but not a manager: their own tickets and the free ones."""
    from . import core as C

    return bool(u is not None and not C.can_assign(u) and C.is_staff(db, u))


def is_mine(t: SupportTicket, u: Optional[User]) -> bool:
    return bool(u is not None and t.assigned_to_id and t.assigned_to_id == u.id)


def is_free(t: SupportTicket) -> bool:
    """No one has it and it isn't closed (one C-EXA is still reading, or asking the requester about, is free too)."""
    return not t.assigned_to_id and t.status != "closed"


def seen_filter(u: User):
    """What a support staff member sees, as a query filter: theirs, or free."""
    return or_(SupportTicket.assigned_to_id == u.id, and_(SupportTicket.assigned_to_id.is_(None), SupportTicket.status != "closed"))


def counts(db: Session, u: User) -> dict:
    """Nudge's two numbers (waiting to be accepted, accepted and open over 48 h) for their own tickets only."""
    from . import nudge as N
    from . import sla

    now = utcnow()
    mine = [t for t in N.open_tickets(db) if t.assigned_to_id == u.id]
    return {"waiting": sum(1 for t in mine if not t.accepted_at),
            "over48": sum(1 for t in mine if t.accepted_at and sla.hours_since(t.accepted_at, now) >= N.OPEN_HOURS)}


def queue_order(rows: list, u: Optional[User]) -> list:
    """1.0.106 (Edwin: the free tickets in order of age): the support team's list in the order to work it — their own open
    tickets first (the oldest first), then the free ones: those ready to work before those C-EXA is still reading or asking
    about, each the oldest first; then the rest (closed ones), the newest first."""
    from . import sla

    def at(t: SupportTicket) -> float:
        d = sla.aware(t.created_at)
        return d.timestamp() if d else 0.0

    def key(t: SupportTicket):
        if t.status != "closed" and is_mine(t, u):
            return (0, 0, at(t))
        if is_free(t):
            return (1, 0 if t.status == "ready" else 1, at(t))
        return (2, 0, -at(t))

    return sorted(rows, key=key)


def taken_lately(db: Session, t: SupportTicket) -> bool:
    """For the second of two people pressing Take it at once: was this ticket claimed through Take it (audit support_taken by
    the person who has it now) in the last 15 minutes, with no assignment, give-back or close after it? Never for an owner's
    or admin's assignment (1.0.103 review: that would name the assignee of a ticket the person may not see)."""
    if not t.assigned_to_id or t.status == "closed":
        return False
    from ..models import AuditLog
    from . import core as C
    from . import sla

    row = (db.query(AuditLog).filter(AuditLog.target == f"ticket:{C.ref(t)}",
                                     AuditLog.action.in_(("support_taken", "support_ticket_assigned", "support_released", "support_closed_not_relevant")))
           .order_by(AuditLog.id.desc()).first())
    if row is None or row.action != "support_taken" or sla.aware(utcnow()) - sla.aware(row.at) > RACE_WINDOW:
        return False
    return row.actor_id == t.assigned_to_id


def take(db: Session, t: SupportTicket, me: User) -> None:
    """A support staff member takes a free ticket: it becomes theirs, as an assign does, and is accepted at once.
    Raises Taken when someone was first, ValueError when the ticket can't be taken (closed)."""
    from . import core as C

    if t.status == "closed":
        raise ValueError(f"{C.ref(t)} is closed; it can't be taken.")
    # the claim: one conditional update — only while the ticket is still free — never read-then-write
    n = (db.query(SupportTicket)
         .filter(SupportTicket.id == t.id, SupportTicket.assigned_to_id.is_(None), SupportTicket.status.in_(TAKEABLE))
         .update({SupportTicket.assigned_to_id: me.id}, synchronize_session=False))
    db.refresh(t)
    if n != 1:
        if t.assigned_to_id and t.assigned_to_id != me.id:
            who = db.get(User, t.assigned_to_id)
            raise Taken(who.name if who and who.name else "someone else")
        if is_mine(t, me):
            return  # theirs already (a second tap)
        raise ValueError(f"{C.ref(t)} is {t.status}; it can't be taken now.")
    C.assign(db, t, me, me, taken=True)
    C.accept(db, t, me)  # they chose it: the SLA starts now and it goes on their To-do
    log(db, "support_taken", actor=me, target=f"ticket:{C.ref(t)}", detail=(t.title or "")[:200])


def take_by_mail(db: Session, t: SupportTicket, me: User) -> bool:
    """1.0.109: a desk person (a lead, the Support desk, an admin) replied in a free ticket's thread from Outlook: it is theirs, as
    Take it does — accepted at once — without the mails (they are already writing to the requester). False when someone was first."""
    from . import core as C

    if t.status == "closed" or not (C.is_lead(db, me) or C.can_assign(me)):
        return False
    n = (db.query(SupportTicket)
         .filter(SupportTicket.id == t.id, SupportTicket.assigned_to_id.is_(None), SupportTicket.status.in_(TAKEABLE))
         .update({SupportTicket.assigned_to_id: me.id}, synchronize_session=False))
    db.refresh(t)
    if n != 1:
        return False
    C.assign(db, t, me, me, taken=True, by_mail=True)
    C.accept(db, t, me)
    log(db, "support_taken", actor=me, target=f"ticket:{C.ref(t)}", detail=f"replied by mail · {(t.title or '')[:180]}")
    return True


def drop_todo(db: Session, t: SupportTicket) -> None:
    """1.0.103 (review): the ticket's To-do item comes off the person's list (deleted while still open) — a ticket given back or
    closed as not relevant was not finished by them, so it never counts as a done task."""
    from . import core as C

    if not t.todo_id:
        return
    st, cols = C._todo()
    if st is not None:
        try:
            cur = st.get("tasks", cols, t.todo_id) or {}
            if cur and cur.get("status") != "Done":
                st.delete("tasks", cols, t.todo_id)
        except Exception as e:  # noqa: BLE001 — the ticket matters more than the list
            log(db, "support_todo_failed", target=f"ticket:{C.ref(t)}", detail=str(e)[:200], outcome="failed")
    t.todo_id = None


def give_back(db: Session, t: SupportTicket, me: User) -> None:
    """Their own open ticket back to the desk, free for anyone to take. The mail's links die with it."""
    from . import core as C
    from . import team as TM

    if not is_mine(t, me) or t.status not in ("assigned", "discussing"):
        raise ValueError("Only an open ticket of yours can be given back.")
    drop_todo(db, t)  # 1.0.103 (review): gone from their To-do, not "finished" (it wasn't)
    t.assigned_to_id, t.assigned_by_id, t.assigned_at = None, None, None  # Mark as done: the link is for the person it is assigned to
    t.accept_nonce, t.accept_hash, t.accepted_at, t.accepted_by = "", "", None, ""  # the Accept link dies
    t.status = "ready"
    C.add_message(db, t, "note", f"Given back by {me.name}. It is free for anyone on the desk to take.", author=me)
    log(db, "support_released", actor=me, target=f"ticket:{C.ref(t)}", detail=(t.title or "")[:200])
    TM.say(db, "Ada", f"{C.ref(t)} given back by {C._first(me.name) or 'its lead'}; free to take.", ref=f"ticket:{t.id}")


# ---- 1.0.103 (from CBX-Connect 2.10.8): Not relevant — close (Edwin: "we need to be able to close a call that doesn't make
# sense — if it's not relevant I need to be able to close it, without having to assign it"). The owner and admins only. -----
NOT_RELEVANT = "not_relevant"


def is_not_relevant(t: SupportTicket) -> bool:
    return (t.closed_as or "") == NOT_RELEVANT


def close_not_relevant(db: Session, t: SupportTicket, by: User, reason: str = "", tell: bool = False) -> None:
    """Close an open ticket, free or assigned, without assigning it: status closed, closed_as not_relevant, the reason as
    its resolution. Never a solved ticket (the dashboard leaves it out). Whoever had it loses it: their To-do item is removed,
    the mail's Accept and Mark as done links die. The requester hears only when `tell` (the not_relevant ticket mail)."""
    from . import core as C
    from . import team as TM

    if not C.can_assign(by):
        raise PermissionError("Only the owner or an admin closes a ticket as not relevant.")
    if t.status == "closed":
        raise ValueError(f"{C.ref(t)} is closed already.")
    reason = " ".join((reason or "").split())[:200]
    had = db.get(User, t.assigned_to_id) if t.assigned_to_id else None
    drop_todo(db, t)  # 1.0.103 (review): gone from the assignee's To-do, not "finished"
    t.assigned_to_id, t.assigned_by_id, t.assigned_at = None, None, None  # Mark as done is for the person it is assigned to
    t.accept_nonce, t.accept_hash, t.accepted_at, t.accepted_by = "", "", None, ""
    t.status, t.closed_at, t.closed_by_id, t.missing = "closed", utcnow(), by.id, []
    t.resolution, t.closed_as = (f"Not relevant: {reason}" if reason else "Not relevant"), NOT_RELEVANT
    C.add_message(db, t, "note", f"Closed as not relevant by {by.name}" + (f": {reason}" if reason else ".") + (f" (it was with {had.name})" if had else ""), author=by)
    if tell:
        C.requester_says(db, t, "not_relevant", C.values(db, t, reason_line=f"\n\nReason: {reason}" if reason else ""))
    log(db, "support_closed_not_relevant", actor=by, target=f"ticket:{C.ref(t)}", detail=(reason or "-") + (" · requester told" if tell else ""))
    TM.say(db, "Ada", f"{C.ref(t)} closed as not relevant by {C._first(by.name) or 'an admin'}.", ref=f"ticket:{t.id}")


# ---- 1.0.109: Remove a ticket (Edwin: "I need to be able to remove a ticket, either by letting them know or silently (owners)").
# The owner and admins only. A removed ticket is gone from every list, count, tile, badge, SLA and statistic, for everyone; the owner
# and admins see it under "Removed", with Restore. A later mail in its thread never reopens it and never mails anyone (inbox). -------
def notice(t: SupportTicket, reason: str = "") -> str:
    from . import core as C

    first = C._first(t.requester_name) or "there"
    reason = " ".join((reason or "").split()).rstrip(".")
    return f"Hi {first}, we've closed {C.ref(t)} without action" + (f": {reason}." if reason else ".")


def remove(db: Session, t: SupportTicket, by: Optional[User], *, tell: bool = False, reason: str = "", message: str = "", how: str = "") -> None:
    """Remove the ticket. `tell`: the requester gets `message` (default: notice()) — once, through the outbox; else silently.
    `how` "automatic": the one-time repair moved a robot's ticket to Automatic mails."""
    from . import core as C
    from . import team as TM

    if how != "automatic" and not C.can_assign(by):
        raise PermissionError("Only the owner or an admin removes a ticket.")
    if C.is_removed(t):
        raise ValueError(f"{C.ref(t)} is removed already.")
    reason = " ".join((reason or "").split())[:200]
    was = {"status": t.status, "closed_as": t.closed_as or "", "assigned_to_id": t.assigned_to_id, "resolution": t.resolution or "",
           "closed_at": t.closed_at.isoformat() if t.closed_at else ""}
    _drop_waiting_mail(db, t)  # nothing it had waiting goes out after it is removed (the notice below is the one mail)
    told = False
    if tell:
        text = ((message or "").strip() or notice(t, reason))[:2000]
        if len(text) < 5:
            raise ValueError("Write the message to the requester, or remove it silently.")
        m = C.add_message(db, t, "staff", text, author=by)
        if C.mail_requester(db, t, "removed", C.values(db, t, message=text), msg_id=m.id):
            m.via, m.mailed, told = "mail", True, True
    had = db.get(User, t.assigned_to_id) if t.assigned_to_id else None
    drop_todo(db, t)  # off the assignee's To-do, not "finished"
    t.assigned_to_id, t.assigned_by_id, t.assigned_at = None, None, None
    t.accept_nonce, t.accept_hash, t.accepted_at, t.accepted_by = "", "", None, ""
    if t.status != "closed":
        t.closed_at, t.closed_by_id = utcnow(), (by.id if by else None)
    t.status, t.missing, t.closed_as = "closed", [], C.REMOVED
    kind = "automatic" if how == "automatic" else ("tell" if tell else "silent")
    t.removal = {"by": (by.name or by.email or "") if by else "the desk", "by_id": by.id if by else None, "at": utcnow().isoformat(), "how": kind, "reason": reason,
                 "was": was, "told": told}
    name = by.name if by else "the desk"
    words = {"tell": f"Removed and the requester told by {name}", "silent": f"Removed silently by {name}",
             "automatic": "Removed by the desk — its first mail was an automatic mail (now under Automatic mails)"}[kind]
    C.add_message(db, t, "note", words + (f": {reason}" if reason else ".") + (f" (it was with {had.name})" if had else ""), author=by if kind != "automatic" else None,
                  name="" if kind != "automatic" else "Support desk")
    log(db, "support_ticket_removed", actor=by if kind != "automatic" else None, actor_label="Support desk" if kind == "automatic" else "", target=f"ticket:{C.ref(t)}",
        detail=f"{kind}{' · requester told' if told else ''}: {reason or '-'}")
    TM.say(db, "Ada", f"{C.ref(t)} removed" + (" (an automatic mail)." if kind == "automatic" else f" by {C._first(name) or 'an admin'}."), ref=f"ticket:{t.id}")


def _drop_waiting_mail(db: Session, t: SupportTicket) -> None:
    """1.0.109: the ticket's mails still waiting as drafts (Mailer → Drafts) are thrown away — a removed ticket mails nobody."""
    from ..models import MailDraft
    from . import outlook

    for d in db.query(MailDraft).filter(MailDraft.ticket_id == t.id, MailDraft.status == "waiting").all():
        if d.outlook_entry_id:
            try:
                outlook.drop_draft(d.outlook_entry_id)
            except Exception:  # noqa: BLE001
                pass
        d.status, d.decided_at, d.detail = "discarded", utcnow(), "Not sent — the ticket was removed."


def restore(db: Session, t: SupportTicket, by: User) -> None:
    """Back from Removed: as it was when it was closed; else free to take (ready) — nobody is mailed."""
    from . import core as C

    if not C.can_assign(by):
        raise PermissionError("Only the owner or an admin restores a ticket.")
    if not C.is_removed(t):
        raise ValueError(f"{C.ref(t)} isn't removed.")
    was = (t.removal or {}).get("was") or {}
    if was.get("status") == "closed":
        t.status, t.closed_as, t.resolution = "closed", (was.get("closed_as") or ""), (was.get("resolution") or t.resolution)
        lead = db.get(User, was.get("assigned_to_id")) if was.get("assigned_to_id") else None
        if lead is not None and (was.get("closed_as") or "") != NOT_RELEVANT:  # 1.0.109 (review): who solved it stays on it (their figures)
            t.assigned_to_id = lead.id
        words = f"Restored by {by.name} — closed, as it was."
    else:
        t.status, t.closed_at, t.closed_by_id, t.closed_as = "ready", None, None, ""
        words = f"Restored by {by.name} — free to take."
    t.removal = dict(t.removal or {}, restored_by=by.name or "", restored_at=utcnow().isoformat())
    C.add_message(db, t, "note", words, author=by)
    log(db, "support_ticket_restored", actor=by, target=f"ticket:{C.ref(t)}", detail=f"was removed ({(t.removal or {}).get('how', '')}) · now {t.status}")


def reopened(t: SupportTicket) -> None:
    """A reopened ticket is an ordinary one again."""
    t.closed_as = ""


# ---- 1.0.103 (review): a reading that comes back after a person acted ----------------------------------------------------
def still_new(db: Session, t: SupportTicket) -> bool:
    """Before C-EXA's reader writes its result: is the ticket still new, with no one, not closed? One conditional update, so
    the row is locked for this job's transaction and nobody can Take, Assign or close it before the job commits. False: a
    person acted while the models were reading — the reading is dropped (no status, no mail, no note)."""
    n = (db.query(SupportTicket)
         .filter(SupportTicket.id == t.id, SupportTicket.status == "new", SupportTicket.assigned_to_id.is_(None),
                 or_(SupportTicket.closed_as == "", SupportTicket.closed_as.is_(None)))
         .update({SupportTicket.status: "new"}, synchronize_session=False))
    if n == 1:
        return True
    db.refresh(t, ["status", "assigned_to_id", "closed_as", "assigned_at", "accepted_at", "todo_id", "resolution"])
    return False
