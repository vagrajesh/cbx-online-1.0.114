"""The Mailer (1.0.70) — every mailbox CBX works with, in one place.

    support mailboxes   each one's recent mail straight from Outlook, and what became of every mail
                        (its ticket, an answer on a ticket, skipped and why, not read yet). A person can make
                        a ticket from a mail the agents skipped, or put a mail on an existing ticket.
                        Seen by the owner, admins and support leads.
    mine                the owner's own linked mailbox (My mail, 1.0.69) — only the person who linked it.
    settings            mail on/off, out through Outlook or SMTP and from which mailbox, which mailboxes are
                        support mailboxes and from when they are read (owner and admins; kept in CBX —
                        no .env edit, no restart).

    GET  /api/mailer                         the mailboxes this person may open
    GET  /api/mailer/box?store=&days=7       a support mailbox's mail, live, with what became of each
    GET  /api/mailer/mail?entry_id=          one mail's text (support mailboxes only)
    POST /api/mailer/ticket {entry_id}       make a ticket from it
    POST /api/mailer/attach {entry_id, ticket_id}
    POST /api/mailer/read {store}            read that mailbox now
    GET/PUT /api/mailer/settings · POST /api/mailer/test

1.0.71 (owner and admins):
    GET  /api/mailer/drafts                  mail waiting to be checked (and the ones decided lately)
    PUT  /api/mailer/drafts/{id} {subject, text} · POST /api/mailer/drafts/{id}/send · /discard
    GET  /api/mailer/sent                    what CBX sent lately (the outbox)
    GET  /api/mailer/templates · PUT /api/mailer/templates/{key} {subject, body} · POST /api/mailer/templates/{key}/reset
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import mail, mailbox, maildrafts, mailcfg
from ..audit import log
from ..db import get_db, utcnow
from ..models import MailDraft, MailLink, SupportMail, SupportTicket, User
from ..security.deps import current_user, require_human
from ..support import core as C
from ..support import inbox as I
from ..support import mailtext as X
from ..support import outlook as O
from ..support import templates as TP

router = APIRouter(prefix="/api/mailer", tags=["mailer"])


def staff(db: Session = Depends(get_db), me: User = Depends(current_user)) -> User:
    if not C.is_staff(db, me):
        raise HTTPException(403, "The Mailer is for the support team.")
    return me


def admin(me: User = Depends(current_user)) -> User:
    if not C.can_assign(me):
        raise HTTPException(403, "Only the owner or an admin changes the mail settings.")
    return me


class EntryIn(BaseModel):
    entry_id: str


class AttachIn(BaseModel):
    entry_id: str
    ticket_id: int


class ReadIn(BaseModel):
    store: str = ""


class BoxIn(BaseModel):
    store: str
    folder: str = "Inbox"
    on: bool = True
    since: Optional[str] = None  # YYYY-MM-DD: read mail from this day on (empty: from now)


class SettingsIn(BaseModel):
    enabled: bool
    transport: str
    send_store: str = ""
    boxes: list[BoxIn] = []
    drafts: Optional[list[str]] = None  # 1.0.71: which automatic mail waits for a person ("check first")


class TestIn(BaseModel):
    to: str = ""


class DraftIn(BaseModel):
    subject: str
    text: str


class TemplateIn(BaseModel):
    subject: str
    body: str


def _box(store: str) -> dict:
    b = next((x for x in I.boxes() if x["store"] == store), None)
    if b is None:
        raise HTTPException(404, "That isn't a support mailbox.")
    return b


def _mail(entry_id: str) -> dict:
    try:
        m = O.get(entry_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(409, f"Outlook couldn't open that mail: {e}")
    if (m.get("store") or "").strip().lower() not in mailcfg.support_stores():
        raise HTTPException(404, "That mail isn't in a support mailbox.")  # never someone's own mailbox through here
    return m


@router.get("")
def overview(db: Session = Depends(get_db), me: User = Depends(current_user)):
    is_staff = C.is_staff(db, me)
    if not is_staff and me.role != "owner":
        raise HTTPException(403, "The Mailer is for the support team.")
    st = I.box_state(db)
    boxes = []
    if is_staff:
        for b in I.boxes():
            s = st.get(b["store"]) or {}
            boxes.append({**b, "last_at": s.get("at"), "last": s.get("last", "")})
    l = db.query(MailLink).filter(MailLink.user_id == me.id).first()
    c = mailcfg.get()
    return {"boxes": boxes, "mine": {"linked": bool(l), "store": l.store if l else "", "address": l.address if l else ""} if me.role == "owner" else None,
            "can_settings": C.can_assign(me), "drafts": maildrafts.count(db) if C.can_assign(me) else 0, "sending": {"enabled": c["enabled"], "transport": c["transport"], "from": c["send_address"] or c["send_store"], "ready": mail.configured()[0], "detail": mail.configured()[1]}}


@router.get("/box")
def box(store: str, days: int = 7, db: Session = Depends(get_db), me: User = Depends(staff)):
    b = _box(store)
    since = (datetime.now() - timedelta(days=max(1, min(days, 60))))
    try:
        rows = O.fetch_since(b["store"], b.get("folder") or "Inbox", since, 150, set(), False)
    except Exception as e:  # noqa: BLE001
        return {"box": b, "mail": [], "error": str(e)[:300]}
    ids = [m.get("id", "")[:400] for m in rows]
    seen = {r.message_id: r for r in db.query(SupportMail).filter(SupportMail.message_id.in_(ids or [""])).all()}
    tickets = {t.id: t for t in db.query(SupportTicket).filter(SupportTicket.id.in_({r.ticket_id for r in seen.values() if r.ticket_id} or {0})).all()}
    out = []
    for m in rows:
        r = seen.get(m.get("id", "")[:400])
        t = tickets.get(r.ticket_id) if r and r.ticket_id else None
        if r is not None and r.ticket_id and (t is None or not C.staff_may_see(db, me, t)):
            continue  # 1.0.103: the support team doesn't see the mails of someone else's ticket (the owner and admins see all)
        if r is None:
            what, why = "unread", "Not read yet" if not m.get("cbx") else "Read before CBX kept a record"
        elif r.outcome.startswith("ignored"):
            what, why = "skipped", {"automatic": "Skipped — an automatic mail", "our own mailbox": "Skipped — from our own mailbox", "flood": "Skipped — too many from this sender in an hour",
                                    "no sender": "Skipped — no sender", "thanks": "Skipped — only a thank-you, no ticket to put it on",
                                    # 1.0.109
                                    "auto_moved": "Skipped — an automatic mail (its ticket was removed)", "dismissed": "Skipped — an automatic mail (dismissed)",
                                    "auto_thread": "Skipped — the desk answering an automatic mail", "to_ticket": "Made a ticket from Automatic mails"}.get(r.outcome.split(":", 1)[-1], r.outcome)
        else:
            what, why = {"new_ticket": ("ticket", "New ticket"), "reply": ("reply", "Answer on a ticket"), "attached": ("attached", "Put on a ticket by hand"),
                         "lead": ("reply", "The support person — asked the requester"), "follow_up": ("reply", "A reminder — put on its ticket"),
                         "duplicate": ("reply", "The same request again — put on its ticket"), "thanks": ("reply", "A thank-you on a ticket"),
                         "staff_note": ("reply", "The support team — kept as a note (only the person it is assigned to writes to the requester)"),
                         # 1.0.109
                         "staff_reply": ("reply", "The support team replied to the requester"), "auto_note": ("reply", "An automatic mail — a quiet note on its ticket"),
                         "removed": ("reply", "On a removed ticket — kept quietly")}.get(r.outcome, ("other", r.outcome))
        body = I.strip_quoted(m.get("body") or "")
        out.append({"entry_id": m["entry_id"], "sender": m.get("sender", ""), "sender_name": m.get("sender_name", ""), "subject": m.get("subject", ""),
                    "received": m.get("received_local"), "preview": body[:220], "what": what, "why": why,
                    "ticket": {"id": t.id, "ref": C.ref(t), "status": t.status, "word": "Removed" if C.is_removed(t) else C.STAFF_WORD.get(t.status, t.status)} if t else None})
    return {"box": b, "mail": out, "error": ""}


def _not_theirs(db: Session, me: User, entry_id: str) -> None:
    """1.0.103: a mail on someone else's ticket isn't there for the support team (the owner and admins see all), as in /box."""
    # 1.0.109: never by an empty id (CBX's own sent ticket mails are kept without one)
    r = db.query(SupportMail).filter(SupportMail.entry_id == (entry_id or "")[:400], SupportMail.entry_id != "", SupportMail.ticket_id.isnot(None)).first()
    t = db.get(SupportTicket, r.ticket_id) if r is not None else None
    if r is not None and (t is None or not C.staff_may_see(db, me, t)):
        raise HTTPException(404, "That mail isn't in a support mailbox.")


@router.get("/mail")
def one(entry_id: str, db: Session = Depends(get_db), me: User = Depends(staff)):
    _not_theirs(db, me, entry_id)
    m = _mail(entry_id)
    return {"entry_id": entry_id, "sender": m.get("sender"), "sender_name": m.get("sender_name"), "subject": m.get("subject"), "received": m.get("received_local"),
            "body": (X.readable(m.get("body") or "") or I.strip_quoted(m.get("body") or ""))[:8000], "files": [n for n, _ in (m.get("files") or [])]}


@router.post("/ticket")
def ticket(body: EntryIn, db: Session = Depends(get_db), me: User = Depends(staff), _h: User = Depends(require_human)):
    _not_theirs(db, me, body.entry_id)  # 1.0.103
    m = _mail(body.entry_id)
    try:
        t = I.make_ticket(db, m, me, m.get("store", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.refresh(t)
    return {"ticket": C.ticket_out(db, t, staff=True)}


@router.post("/attach")
def attach(body: AttachIn, db: Session = Depends(get_db), me: User = Depends(staff), _h: User = Depends(require_human)):
    t = db.get(SupportTicket, body.ticket_id)
    if t is None or not C.staff_may_see(db, me, t):
        raise HTTPException(404, "No such ticket.")
    _not_theirs(db, me, body.entry_id)  # 1.0.103
    m = _mail(body.entry_id)
    I.attach(db, m, t, me)
    return {"ticket": C.ticket_out(db, t, staff=True)}


@router.post("/read")
def read_now(body: ReadIn, db: Session = Depends(get_db), me: User = Depends(staff), _h: User = Depends(require_human)):
    if body.store:
        _box(body.store)
    got = I.poll(db, only=body.store)
    log(db, "support_mail_checked", actor=me, target="support-mail", detail=f"{body.store or 'all'}: {got}"[:300])
    return got


# ---- settings ----------------------------------------------------------------------------------------------

@router.get("/settings")
def get_settings(db: Session = Depends(get_db), me: User = Depends(admin)):
    try:
        stores, err = O.list_stores(), ""
    except Exception as e:  # noqa: BLE001
        stores, err = [], str(e)[:300]
    mine = {l.store.lower() for l in db.query(MailLink).all()}
    return {"settings": mailcfg.get(), "stores": [{**s, "personal": s["name"].lower() in mine} for s in stores], "outlook_error": err,
            "groups": [{"key": g, "label": l} for g, l in maildrafts.GROUPS], "templates": TP.all_(db), "redirect": mail.only_to(),
            "sending": {"ready": mail.configured()[0], "detail": mail.configured()[1]}, "proven_at": _proven(db),
            "sign_in_codes": _codes(db)}


def _proven(db: Session) -> Optional[str]:
    from .. import state

    p = state.mail_proven(db)
    return p.isoformat() if p else None


def _codes(db: Session) -> str:
    """Sign-in codes by mail switch on once one mail has actually gone out (a test from here does it)."""
    from ..config import settings as S

    if not S.device_verification:
        return "off"
    return "on" if _proven(db) else "waiting"


@router.put("/settings")
def put_settings(body: SettingsIn, db: Session = Depends(get_db), me: User = Depends(admin), _h: User = Depends(require_human)):
    if body.transport not in ("outlook", "smtp"):
        raise HTTPException(400, "Out through Outlook or SMTP.")
    try:
        stores = {s["name"]: s for s in O.list_stores()}
    except Exception:  # noqa: BLE001 — Outlook not open: keep what was typed, it is checked when it is used
        stores = {}
    personal = {l.store for l in db.query(MailLink).all()}
    old = {b["store"]: b for b in I.boxes()}
    boxes = []
    for b in body.boxes:
        st = b.store.strip()
        if not st:
            continue
        if st in personal:
            raise HTTPException(400, f"{st} is someone's own mailbox (My mail) — it can't be a support mailbox.")
        if stores and st not in stores:
            raise HTTPException(400, f"Outlook on the server has no mailbox called {st}.")
        since = None
        if b.since:
            try:
                since = datetime.fromisoformat(b.since[:10]).replace(tzinfo=None).isoformat()
            except ValueError:
                raise HTTPException(400, "The start date: YYYY-MM-DD.")
        elif b.on:
            prev = old.get(st)
            since = (prev or {}).get("since") or utcnow().isoformat()  # switched on: from now, never the whole history
        boxes.append({"store": st, "address": (stores.get(st) or {}).get("address") or (st if "@" in st else ""), "folder": (b.folder or "Inbox").strip()[:80], "on": bool(b.on), "since": since})
    if len({b["store"] for b in boxes}) != len(boxes):
        raise HTTPException(400, "A mailbox is listed twice.")
    send_store = body.send_store.strip()
    if body.transport == "outlook" and body.enabled and not send_store:
        raise HTTPException(400, "Pick the mailbox mail goes out from.")
    send_addr = (stores.get(send_store) or {}).get("address") or (send_store if "@" in send_store else "")
    changes = {"enabled": body.enabled, "transport": body.transport, "send_store": send_store, "send_address": send_addr, "boxes": boxes}
    if body.drafts is not None:
        ok = {g for g, _ in maildrafts.GROUPS} | {f"support:{k}" for k in TP.DEFAULTS}
        changes["drafts"] = sorted({x for x in body.drafts if x in ok})
    c = mailcfg.save(db, changes)
    log(db, "mail_settings_changed", actor=me, target="mail", detail=f"{'on' if c['enabled'] else 'off'} · {c['transport']} · from {send_store or '—'} · support: {', '.join(b['store'] + ('' if b['on'] else ' (off)') for b in boxes) or 'none'} · check first: {', '.join(c.get('drafts') or []) or 'nothing'}")
    return {"settings": c, "sending": {"ready": mail.configured()[0], "detail": mail.configured()[1]}}


@router.post("/test")
def test_mail(body: TestIn, db: Session = Depends(get_db), me: User = Depends(admin), _h: User = Depends(require_human)):
    from .. import mailbox

    to = (body.to or me.email or "").strip()
    r = mailbox.send(db, to=[to], subject="CBX-Connect: test mail", body=f"This is a test from the Mailer, sent by {me.name}. If you can read it, mail from CBX works.", kind="test", by=me)
    return {"ok": bool(r.ok), "detail": r.detail}


# ---- 1.0.71: drafts — mail a person checks before it goes ------------------------------------------------

def _draft(db: Session, draft_id: int) -> MailDraft:
    d = db.get(MailDraft, draft_id)
    if d is None:
        raise HTTPException(404, "No such draft.")
    return d


@router.get("/drafts")
def drafts(db: Session = Depends(get_db), me: User = Depends(admin)):
    rows = maildrafts.waiting(db)
    ok, why = mail.configured()
    return {"waiting": [maildrafts.out(db, d) for d in rows], "decided": [maildrafts.out(db, d) for d in maildrafts.recent(db, 30)], "redirect": mail.only_to(), "ready": ok, "why": why}


@router.put("/drafts/{draft_id}")
def draft_edit(draft_id: int, body: DraftIn, db: Session = Depends(get_db), me: User = Depends(admin), _h: User = Depends(require_human)):
    try:
        d = maildrafts.edit(db, _draft(db, draft_id), body.subject, body.text, me)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"draft": maildrafts.out(db, d)}


@router.post("/drafts/{draft_id}/send")
def draft_send(draft_id: int, db: Session = Depends(get_db), me: User = Depends(admin), _h: User = Depends(require_human)):
    d = _draft(db, draft_id)
    try:
        ok, detail = maildrafts.send(db, d, me)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": ok, "detail": detail, "draft": maildrafts.out(db, d)}


@router.post("/drafts/{draft_id}/discard")
def draft_discard(draft_id: int, db: Session = Depends(get_db), me: User = Depends(admin), _h: User = Depends(require_human)):
    d = _draft(db, draft_id)
    try:
        maildrafts.discard(db, d, me)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"draft": maildrafts.out(db, d)}


@router.get("/sent")
def sent(db: Session = Depends(get_db), me: User = Depends(admin)):
    return {"mail": mailbox.outbox(db, 80), "redirect": mail.only_to(), "ready": mail.configured()[0],
            "recipients": mailbox.recipients_for_picker(db), "domain": mailbox.company_domain()}


class ComposeIn(BaseModel):
    to: list[str]
    subject: str
    message: str


@router.post("/compose")
def compose(body: ComposeIn, db: Session = Depends(get_db), me: User = Depends(admin), _h: User = Depends(require_human)):
    """One message, written by a person, to people CBX knows (or the company's own domain). Signed with who
    sent it and where the app is; kept under Sent whatever happens. (Settings → Mail until 1.0.79.)"""
    to = [a.strip() for a in body.to if a and a.strip()]
    if not to:
        raise HTTPException(400, "Choose who it goes to.")
    if len(to) > 20:
        raise HTTPException(400, "Twenty people at most in one message.")
    for a in to:
        ok, why = mailbox.allowed(db, a)
        if not ok:
            raise HTTPException(400, why)
    subject = " ".join(body.subject.split())[:200]
    message = body.message.strip()
    if len(subject) < 2:
        raise HTTPException(400, "Give it a subject.")
    if len(message) < 2:
        raise HTTPException(400, "Write the message.")
    r = mailbox.send(db, to=to, subject=subject, body=f"{message}\n" + mailbox.footer(db, me), kind="compose", by=me, reply_to=me.email or "")
    # not raised on failure: the Sent row that says WHY must survive the request (a 4xx rolls it back)
    return {"ok": bool(r.ok), "message": r.detail if r.ok else f"Not sent — {r.detail}", "row": mailbox.outbox(db, 1)[0]}


@router.get("/templates")
def templates_list(db: Session = Depends(get_db), me: User = Depends(admin)):
    return {"templates": TP.all_(db), "held": mailcfg.get().get("drafts") or []}


@router.put("/templates/{key}")
def template_save(key: str, body: TemplateIn, db: Session = Depends(get_db), me: User = Depends(admin), _h: User = Depends(require_human)):
    if key not in TP.DEFAULTS:
        raise HTTPException(404, "No such ticket mail.")
    try:
        t = TP.save(db, key, body.subject, body.body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    log(db, "mail_template_changed", actor=me, target=f"template:{key}", detail=t["subject"][:200])
    db.commit()
    return {"template": t}


@router.post("/templates/{key}/reset")
def template_reset(key: str, db: Session = Depends(get_db), me: User = Depends(admin), _h: User = Depends(require_human)):
    if key not in TP.DEFAULTS:
        raise HTTPException(404, "No such ticket mail.")
    t = TP.reset(db, key)
    log(db, "mail_template_reset", actor=me, target=f"template:{key}")
    db.commit()
    return {"template": t}
