"""Sending mail from inside the tool, with a record: the outbox.

`send(db, ...)` is `mail.send` plus the bookkeeping every message from the app should have — a
MailLog row (the Mail page's outbox), the "mailbox proven" mark, and an audit line. It also answers
the question `mail.send` never asked: MAY this go to that address? A tool that can mail anyone from
the company's own mailbox is a tool for sending mail as the company. So:

  * a recipient must be someone CBX knows — an account here, or a responsible person's address on
    the compliance grid — or an address on the company's own domain (the domain of CBX_MAIL_FROM);
  * everything else is refused with a sentence, before anything is sent.
"""
from __future__ import annotations

from typing import Iterable, Optional

from sqlalchemy.orm import Session

from . import mail, state
from .audit import log
from .config import settings
from .models import MailLog, User
from .models.docs import Entity

PREVIEW = 600


def company_domain() -> str:
    from . import mailcfg

    c = mailcfg.get()
    frm = (c.get("send_address") if c.get("transport") == "outlook" else "") or settings.mail_from or ""
    return frm.rsplit("@", 1)[-1].lower().strip() if "@" in frm else ""


def known_addresses(db: Session) -> dict[str, str]:
    """address → name, for everyone the app may write to by name."""
    out: dict[str, str] = {}
    for u in db.query(User).filter(User.is_active.is_(True), User.is_axl.is_(False)).all():
        if u.email and mail.valid(u.email):
            out[u.email.lower()] = u.name
    for e in db.query(Entity).filter(Entity.responsible_email != "").all():
        if mail.valid(e.responsible_email):
            out.setdefault(e.responsible_email.lower(), (e.responsible or e.responsible_email) + f" ({e.name})")
    for x in tool_addresses():
        out.setdefault(x["email"].lower(), x["name"] + (f" ({x['entity']})" if x["entity"] else ""))
    return out


def tool_addresses() -> list[dict]:
    """1.0.63: addresses kept in the tools' own tables, in a text column marked mail: true (a company's
    responsible person in Entity Compliance). {email, name, kind, entity}."""
    out = []
    try:
        from .engine import loader
        from .engine.loader import link_show
        from .engine.store import Store

        for t in loader.registry().tools.values():
            for name, b in (t.data or {}).items():
                mcols = [c["key"] for c in b.get("columns") or [] if isinstance(c, dict) and c.get("mail") and c.get("type", "text") == "text"]
                if not mcols:
                    continue
                st = Store(t.slug)
                if not st.exists(name):
                    continue
                show = link_show(b)
                for r in st.rows(name, b.get("columns") or [], limit=2000)["rows"]:
                    for k in mcols:
                        a = str(r.get(k) or "").strip()
                        if a and mail.valid(a):
                            out.append({"email": a, "name": str(r.get("responsible_name") or a), "kind": "responsible", "entity": str(r.get(show) or "")})
    except Exception:  # noqa: BLE001 — a broken tool folder must not stop mail
        pass
    return out


def allowed(db: Session, address: str) -> tuple[bool, str]:
    a = (address or "").strip().lower()
    if not mail.valid(a):
        return False, f"'{address}' doesn't look like an email address."
    if a in known_addresses(db):
        return True, ""
    dom = company_domain()
    if dom and a.endswith("@" + dom):
        return True, ""
    return False, f"'{address}' is not someone CBX knows (an account here, a responsible person on the grid, or an address at {dom or 'the company domain'}). Add the person first."


def recipients_for_picker(db: Session) -> list[dict]:
    """The To-list the pages offer: people first, then responsible addresses without an account."""
    rows = []
    seen = set()
    for u in db.query(User).filter(User.is_active.is_(True), User.is_axl.is_(False)).order_by(User.name).all():
        if u.email and mail.valid(u.email):
            rows.append({"email": u.email, "name": u.name, "kind": "account", "user_id": u.id})
            seen.add(u.email.lower())
    for e in db.query(Entity).filter(Entity.responsible_email != "", Entity.is_demo.is_(False)).order_by(Entity.name).all():
        a = e.responsible_email.lower()
        if mail.valid(a) and a not in seen:
            rows.append({"email": e.responsible_email, "name": e.responsible or e.responsible_email, "kind": "responsible", "entity": e.name})
            seen.add(a)
    for x in tool_addresses():
        if x["email"].lower() not in seen:
            rows.append(x)
            seen.add(x["email"].lower())
    return rows


def send(db: Session, *, to: Iterable[str], subject: str, body: str, kind: str = "compose", ref: str = "", by: Optional[User] = None, html: Optional[str] = None, reply_to: str = "", check: bool = True,
         hold: str = "", thread: str = "", extra: Optional[dict] = None, ticket_id: Optional[int] = None, mark: str = "") -> MailLog:
    """Send one message and record it. Never raises: the MailLog row says whether it went.

    1.0.71: `extra` dresses `body` (the message) for sending (support/templates.build: heading, button, the
    earlier messages); `thread` answers that Outlook mail in its own thread; `hold` names what kind of
    automatic mail this is — when that kind is "check first" on the Mailer's Settings it waits as a draft
    (Mailer → Drafts) instead of going."""
    people = [a.strip() for a in to if a and a.strip()]
    if check:
        for a in people:
            ok, why = allowed(db, a)
            if not ok:
                row = MailLog(to=", ".join(people), subject=subject[:300], preview=body[:PREVIEW], kind=kind, ref=ref, sent_by_id=by.id if by else None, ok=False, detail=("refused: " + why)[:400])
                db.add(row)
                db.flush()
                log(db, "mail_refused", actor=by, actor_label="" if by else "server", target=", ".join(people)[:200], detail=why[:300], outcome="blocked")
                return row
    from . import mailcfg

    if hold and mailcfg.held(hold):
        from . import maildrafts

        d = maildrafts.hold(db, to=people, subject=subject, text=body, extra=dict(extra or {}, **({"mark": mark} if mark else {})), kind=kind, ref=ref, hold=hold, reply_to=reply_to, thread=thread, by=by, ticket_id=ticket_id)
        row = MailLog(to=", ".join(people)[:500], subject=subject[:300], preview=body[:PREVIEW], kind=kind, ref=ref, sent_by_id=by.id if by else None, ok=False,
                      detail=f"draft: waiting in Mailer → Drafts (#{d.id}) — not sent yet."[:400])
        db.add(row)
        db.flush()
        return row
    body, html, files, inline = dress(db, body, html, extra)  # 1.0.109: the pictures; (round 2) a rich message's in place
    r = mail.send(people, subject, body, html=html, reply_to=reply_to, **({"thread": thread} if thread else {}), **({"mark": mark} if mark else {}),
                  **({"attachments": files} if files else {}), **({"inline": inline} if inline else {}))
    state.record_send(db, r.ok)
    row = MailLog(to=", ".join(people)[:500], delivered_to=", ".join(r.to)[:500] if r.to else "", subject=subject[:300], preview=body[:PREVIEW], kind=kind, ref=ref, sent_by_id=by.id if by else None, ok=bool(r.ok), detail=r.detail[:400])
    db.add(row)
    db.flush()
    log(db, "mail_sent" if r.ok else "mail_failed", actor=by, actor_label="" if by else "server", target=", ".join(people)[:200], detail=f"{kind}: {subject[:120]} — {r.detail[:200]}", outcome="ok" if r.ok else "failed")
    if r.ok and ticket_id and kind == "support":
        remember_sent(db, row, r, ticket_id, subject, mark)
    return row


def remember_sent(db: Session, row: MailLog, r, ticket_id: int, subject: str, mark: str = "") -> None:
    """1.0.109: every ticket mail CBX sends is kept with what it is known by (its Message-ID, Outlook's conversation), so a reply that
    lost the [T-00NN] tag still finds its ticket by In-Reply-To / References (support/inbox.find_ticket). Through Outlook the Message-ID
    is only known once the mail is in Sent Items: a placeholder is kept and the mailbox read fills it in (inbox.backfill_sent)."""
    from . import mailcfg
    from .models import SupportMail

    mid = (getattr(r, "message_id", "") or "").strip()[:400]
    if mid and db.query(SupportMail.id).filter(SupportMail.message_id == mid).first():
        return
    c = mailcfg.get()
    ours = ((c.get("send_address") if c.get("transport") == "outlook" else "") or settings.mail_from or "cbx").lower()[:255]
    db.add(SupportMail(message_id=mid or f"cbx-sent:{row.id}", sender=ours, subject=(subject or "")[:300], ticket_id=ticket_id, outcome="sent",
                       conversation=(getattr(r, "conversation", "") or "")[:200], why=(mark or "")[:200]))
    db.flush()


def dress(db: Session, body: str, html: Optional[str], extra: Optional[dict]) -> tuple[str, Optional[str], list, list]:
    """(plain, html, attachments, inline pictures) for a message and its `extra` (support/templates.build). 1.0.109 (round 2): a
    message written in the reply editor carries its pictures inline (cid) where they were pasted; when its words can't be placed,
    the pictures go as ordinary attachments (never lost)."""
    files = attachments_of(db, extra)
    if not extra:
        return body, html, files, []
    from .support import templates

    plain, html, placed = templates.build_rich(body, extra)
    inline = inline_of(db, extra)
    if inline and not placed:
        have = {n for n, _ in files}
        files += [(n, d) for n, d, _ in inline if n not in have]
        inline = []
    return plain, html, files, inline


def inline_of(db: Session, extra: Optional[dict]) -> list:
    """1.0.109 (round 2): `extra["inline"]` — [[ticket id, file n, cid], …] — as [(name, bytes, cid)] read from the ticket's files
    when the mail goes (a held draft keeps the references). At most 10, within the mail's size limit."""
    refs = (extra or {}).get("inline") or []
    if not refs:
        return []
    from .models import SupportTicket
    from .support import core as SC

    out, total = [], 0
    for ref in refs[:10]:
        try:
            t = db.get(SupportTicket, int(ref[0]))
            p = SC.file_path(t, int(ref[1])) if t is not None else None
            if p is None:
                continue
            data = p.read_bytes()
        except (OSError, ValueError, TypeError, IndexError):
            continue
        if total + len(data) > SC.MAIL_ATTACH_MAX:
            break
        total += len(data)
        out.append((p.name.split("_", 1)[-1], data, str(ref[2])[:120]))
    return out


def attachments_of(db: Session, extra: Optional[dict]) -> list:
    """1.0.109: `extra["attach"]` — [[ticket id, file n], …] — as [(name, bytes)] read from the ticket's files (a held draft keeps
    the references, so the files go when it is sent). A file that is gone is left out."""
    refs = (extra or {}).get("attach") or []
    if not refs:
        return []
    from .models import SupportTicket
    from .support import core as SC

    out, total = [], 0
    for ref in refs[:12]:
        try:
            t = db.get(SupportTicket, int(ref[0]))
            p = SC.file_path(t, int(ref[1])) if t is not None else None
            if p is None:
                continue
            data = p.read_bytes()
        except (OSError, ValueError, TypeError, IndexError):
            continue
        if total + len(data) > SC.MAIL_ATTACH_MAX:
            break
        total += len(data)
        out.append((p.name.split("_", 1)[-1], data))
    return out


def outbox(db: Session, limit: int = 100) -> list[dict]:
    rows = db.query(MailLog).order_by(MailLog.created_at.desc()).limit(limit).all()
    users = {u.id: u for u in db.query(User).filter(User.id.in_({r.sent_by_id for r in rows if r.sent_by_id} or {0})).all()}
    return [{
        "id": r.id, "to": r.to, "delivered_to": r.delivered_to, "subject": r.subject, "preview": r.preview, "kind": r.kind, "ref": r.ref,
        "by": users[r.sent_by_id].name if r.sent_by_id in users else ("CBX" if not r.sent_by_id else "?"), "ok": r.ok, "detail": r.detail,
        "at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]


def footer(db: Session, by: Optional[User], link: str = "") -> str:
    base = state.public_url(db)
    lines = [""]
    if link:
        lines.append(f"Open it in CBX-Connect: {base}/{link.lstrip('/')}")
    lines.append(f"CBX-Connect: {base}")
    if by is not None:
        lines.append(f"Sent from CBX-Connect by {by.name}" + (f" ({by.email})" if by.email else ""))
    return "\n".join(lines)
