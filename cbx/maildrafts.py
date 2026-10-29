"""Drafts (1.0.71): automatic mail that waits for a person to check it before it goes.

Which kinds wait is ticked on Mailer → Mail set-up ("check first"): every ticket mail, or one
ticket mail at a time, compliance reminders, flow mails. Sign-in codes and tests never wait — nobody could
sign in. A held mail:

  * is kept here with its message (`text`) and how it is dressed (`extra`), so a person can change the
    words and it is dressed again;
  * through Outlook, is also saved as an Outlook draft — a reply in the requester's own thread when the
    ticket came by mail, so the chain is underneath. Sent or deleted in Outlook instead, CBX notices
    ("handled in Outlook");
  * goes when a person presses Send (owner or admin), or is thrown away (its Outlook draft with it — only
    ever CBX's own unsent draft).
"""
from __future__ import annotations

from typing import Iterable, Optional

from sqlalchemy.orm import Session

from . import mail, state
from .audit import log
from .db import utcnow
from .models import MailDraft, MailLog, User

GROUPS = [("support", "Ticket mails"), ("reminder", "Compliance reminders"), ("flow", "Flow mails"), ("welcome", "Welcome mails to new people")]


def label(hold: str) -> str:
    g, _, k = hold.partition(":")
    if g == "support" and k:
        from .support import templates

        return "Ticket mail · " + (templates.DEFAULTS[k][1] if k in templates.DEFAULTS else k)
    return dict(GROUPS).get(g, hold or "Mail")


def _dress(d: MailDraft) -> tuple[str, Optional[str]]:
    from .support import templates

    return templates.build(d.text, d.extra or {})


def _people(d: MailDraft) -> list[str]:
    return [a.strip() for a in (d.to or "").split(",") if a.strip()]


def _save_outlook(d: MailDraft) -> None:
    from .support import templates

    # 1.0.109 (round 2): Outlook's own copy carries no files, so a rich message goes there as its plain words
    plain, html = templates.build(d.text, {k: v for k, v in (d.extra or {}).items() if k not in ("rich", "inline")}) if d.extra else (d.text, None)
    eid, note = mail.save_draft(_people(d), d.subject, plain, html=html, reply_to=d.reply_to, thread=d.thread_entry_id, mark=(d.extra or {}).get("mark", ""))
    d.outlook_entry_id, d.detail = eid, note[:400]


def hold(db: Session, *, to: Iterable[str], subject: str, text: str, extra: dict, kind: str, ref: str, hold: str, reply_to: str = "", thread: str = "",
         by: Optional[User] = None, ticket_id: Optional[int] = None) -> MailDraft:
    d = MailDraft(hold=hold[:40], kind=kind[:24], ref=ref[:64], ticket_id=ticket_id, to=", ".join(to)[:500], subject=subject[:300], text=text, extra=extra or {},
                  reply_to=reply_to[:255], thread_entry_id=(thread or "")[:400], by_id=by.id if by else None)
    db.add(d)
    _save_outlook(d)
    db.flush()
    log(db, "mail_held", actor=by, actor_label="" if by else "server", target=d.to[:200], detail=f"{label(hold)}: {subject[:150]} — waits in Mailer → Drafts")
    return d


def _check_outlook(d: MailDraft) -> None:
    """A waiting draft that is gone from Outlook was sent or deleted there."""
    if d.status != "waiting" or not d.outlook_entry_id:
        return
    from .support import outlook

    try:
        alive = outlook.draft_alive(d.outlook_entry_id)
    except Exception:  # noqa: BLE001 — Outlook closed: can't tell, leave it waiting
        return
    if not alive:
        d.status, d.detail, d.decided_at = "outlook", "Sent or deleted in Outlook, not from CBX.", utcnow()


def out(db: Session, d: MailDraft) -> dict:
    from .models import SupportTicket
    from .support import core as C

    t = db.get(SupportTicket, d.ticket_id) if d.ticket_id else None
    plain, _ = _dress(d)
    who = db.get(User, d.decided_by_id) if d.decided_by_id else None
    return {"id": d.id, "hold": d.hold, "what": label(d.hold), "kind": d.kind, "to": d.to, "subject": d.subject, "text": d.text, "full": plain,
            "ticket": {"id": t.id, "ref": C.ref(t)} if t else None, "thread": bool(d.thread_entry_id), "outlook": bool(d.outlook_entry_id), "status": d.status,
            "detail": d.detail, "edited": d.edited, "at": d.created_at.isoformat() if d.created_at else None,
            "decided_by": who.name if who else "", "decided_at": d.decided_at.isoformat() if d.decided_at else None, "redirect": mail.only_to()}


def waiting(db: Session, check: bool = True) -> list[MailDraft]:
    rows = db.query(MailDraft).filter(MailDraft.status == "waiting").order_by(MailDraft.created_at.desc()).limit(200).all()
    if check:
        for d in rows[:60]:
            _check_outlook(d)
        db.commit()
    return [d for d in rows if d.status == "waiting"]


def recent(db: Session, limit: int = 40) -> list[MailDraft]:
    return db.query(MailDraft).filter(MailDraft.status != "waiting").order_by(MailDraft.decided_at.desc()).limit(limit).all()


def count(db: Session) -> int:
    return db.query(MailDraft).filter(MailDraft.status == "waiting").count()


def edit(db: Session, d: MailDraft, subject: str, text: str, by: User) -> MailDraft:
    if d.status != "waiting":
        raise ValueError("That draft isn't waiting any more.")
    subject, text = " ".join((subject or "").split())[:300], (text or "").replace("\r\n", "\n").strip()
    if not subject or not text:
        raise ValueError("A subject and a message, please.")
    if d.outlook_entry_id:
        from .support import outlook

        try:
            if not outlook.draft_alive(d.outlook_entry_id):
                d.status, d.detail, d.decided_at = "outlook", "Sent or deleted in Outlook, not from CBX.", utcnow()
                db.commit()
                raise ValueError("It was already sent or deleted in Outlook.")
            outlook.drop_draft(d.outlook_entry_id)  # CBX's own draft: replaced by the changed one
        except ValueError:
            raise
        except Exception:  # noqa: BLE001
            pass
    d.subject, d.text, d.edited = subject, text, True
    _save_outlook(d)
    log(db, "mail_draft_changed", actor=by, target=d.to[:200], detail=f"#{d.id} {subject[:150]}")
    db.commit()
    return d


def send(db: Session, d: MailDraft, by: User) -> tuple[bool, str]:
    if d.status != "waiting":
        raise ValueError("That draft isn't waiting any more.")
    people = _people(d)
    plain, html = _dress(d)
    if d.outlook_entry_id:
        r = mail.send_saved_draft(d.outlook_entry_id, people)
        if not r.ok and "isn't in Outlook" in r.detail:
            d.status, d.detail, d.decided_at = "outlook", "Sent or deleted in Outlook, not from CBX.", utcnow()
            db.commit()
            return False, "It was already sent or deleted in Outlook."
    else:
        from .mailbox import dress

        plain, html, files, inline = dress(db, d.text, None, d.extra)  # 1.0.109: the pictures a held ticket mail carries (round 2: inline)
        r = mail.send(people, d.subject, plain, html=html, reply_to=d.reply_to, **({"thread": d.thread_entry_id} if d.thread_entry_id else {}), **({"mark": d.extra["mark"]} if (d.extra or {}).get("mark") else {}),
                      **({"attachments": files} if files else {}), **({"inline": inline} if inline else {}))
    state.record_send(db, r.ok)
    db.add(MailLog(to=d.to, delivered_to=", ".join(r.to)[:500] if r.to else "", subject=d.subject, preview=plain[:600], kind=d.kind, ref=d.ref, sent_by_id=by.id, ok=bool(r.ok),
                   detail=(f"checked by {by.name}: " + r.detail)[:400]))
    if r.ok:
        d.status, d.decided_by_id, d.decided_at, d.detail = "sent", by.id, utcnow(), r.detail[:400]
        _mark_mailed(db, d)
        if d.ticket_id and d.kind == "support":  # 1.0.109: a reply to it finds the ticket without the tag
            from .mailbox import remember_sent

            db.flush()
            remember_sent(db, db.query(MailLog).order_by(MailLog.id.desc()).first(), r, d.ticket_id, d.subject, (d.extra or {}).get("mark", ""))
    else:
        d.detail = r.detail[:400]
    log(db, "mail_sent" if r.ok else "mail_failed", actor=by, target=d.to[:200], detail=f"draft #{d.id} {d.subject[:120]} — {r.detail[:200]}", outcome="ok" if r.ok else "failed")
    db.commit()
    return bool(r.ok), r.detail


def _mark_mailed(db: Session, d: MailDraft) -> None:
    """The ticket's thread shows the message as mailed once it really went."""
    mid = (d.extra or {}).get("msg_id")
    if not mid:
        return
    from .models import SupportMessage

    m = db.get(SupportMessage, mid)
    if m is not None and m.ticket_id == d.ticket_id:
        m.mailed, m.via = True, "mail"


def discard(db: Session, d: MailDraft, by: User) -> None:
    if d.status != "waiting":
        raise ValueError("That draft isn't waiting any more.")
    if d.outlook_entry_id:
        from .support import outlook

        try:
            outlook.drop_draft(d.outlook_entry_id)
        except Exception:  # noqa: BLE001
            pass
    d.status, d.decided_by_id, d.decided_at, d.detail = "discarded", by.id, utcnow(), f"Not sent — {by.name} threw it away."
    log(db, "mail_draft_discarded", actor=by, target=d.to[:200], detail=f"#{d.id} {d.subject[:150]}")
    db.commit()
