"""Support tickets by mail (1.0.66): the support mailbox is read (through Outlook on this server, see
outlook.py) and every new mail becomes a ticket — or, when its subject carries a ticket number and it comes
from that ticket's requester, an answer on that ticket. C-EXA then reads it like any other ticket and writes
back to the sender with the ticket number and anything she still needs.

Not turned into tickets (recorded in support_mail, marked read in Outlook):
  * automatic replies, out-of-office, bounces, bulk mail (headers and subjects that say so)
  * mail from the support mailbox itself (a loop)
  * more than CBX_SUPPORT_MAX_NEW_PER_SENDER new tickets from one address in an hour (a flood)
A mail is only ever answered to its own sender; nothing in a mail is acted on — it is read as the person's words.
"""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import jobs, state
from ..audit import log
from ..config import settings
from ..db import SessionLocal, utcnow
from ..models import SupportMail, SupportTicket, User
from . import core as C
from . import mailtext as X
from . import outlook

AUTO_SUBJECT = re.compile(r"^\s*(automatic reply|auto(matic)?[- ]?reply|auto[- ]?response|out of (the )?office|ooo\b|undeliverable|delivery status notification|mail delivery (failed|subsystem)|returned mail|read:|not read:)", re.I)
AUTO_HEADER = re.compile(r"^(auto-submitted:\s*(?!no\b)\S|x-autoreply:|x-autorespond:|precedence:\s*(bulk|junk|list|auto_reply)|list-unsubscribe:"
                         r"|x-auto-response-suppress:\s*all\b)", re.I | re.M)
ROBOT_SENDER = re.compile(r"^(mailer-daemon|postmaster|no-?reply|do-?not-?reply|donotreply|notifications?-noreply)[@+.\-]", re.I)
# 1.0.109 (T-0018: REFLEX Support's "Your ticket with ID 3901517 has been acknowledged" became a ticket and was sent AXL's four
# questions): another helpdesk's acknowledgement — read in the mail's own new words, never in what it quotes
# 1.0.109 (review): two signals, never one — the acknowledgement itself AND (its ticket id, "do not reply", "automated"): a person who
# writes "please do not reply all" or quotes a reference number is never a robot
ACK_SAYS = re.compile(r"\byour (?:ticket|case|call|request|query|incident)\b[^.\n]{0,80}?\b(?:has|have) been (?:acknowledged|logged|received|created|opened|registered|raised)"
                      r"|\b(?:ticket|case|call|reference|incident) (?:number|no\.?|#|id)\s*[:#]?\s*[A-Z0-9-]{4,}\b[^.\n]{0,60}\b(?:has been |was )?(?:logged|acknowledged|created|registered)\b", re.I)
ACK_SIGN = re.compile(r"\bticket with (?:the )?id\s*[:#]?\s*[A-Z0-9-]{3,}|\b(?:ticket|case|call|reference|incident|request) (?:number|no\.?|#|id)\s*[:#]?\s*[A-Z0-9-]{4,}"
                      r"|\b(?:please )?do not (?:reply|respond) to this (?:e-?mail|message|address)\b|\bdo-not-reply\b|\bthis is an automated (?:message|e-?mail|response|notification)"
                      r"|\bthis (?:e-?mail|message) (?:was|has been) (?:sent|generated) automatically\b", re.I)


def is_ack(words: str) -> bool:
    """1.0.109 (review): another helpdesk's acknowledgement — what it says AND a mark of a machine (an id, "do not reply to this e-mail")."""
    return bool(ACK_SAYS.search(words or "") and ACK_SIGN.search(words or ""))
STATE_KEY = "support.mail.last"


def strip_quoted(body: str) -> str:
    """The new words of a reply: everything above the quoted earlier mail — banner, disclaimer, tracking links,
    contact lines and any password or code taken out (1.0.84)."""
    ms = X.messages(body or "")
    return X.redact(ms[0]["text"] if ms else "")[:8000]


def _ours() -> set[str]:
    from .. import mailcfg

    c = mailcfg.get()
    return {x for x in mailcfg.support_stores() | {(c.get("send_address") or "").lower(), (settings.mail_from or "").lower()} if "@" in x}


def _why_ignore(m: dict) -> str:
    if not m.get("sender") or "@" not in m["sender"]:
        return "no sender"
    if m["sender"] in _ours():
        return "our own mailbox"
    if robot(m):
        return "automatic"
    return ""


def robot(m: dict, text: Optional[str] = None, person: bool = False) -> str:
    """1.0.109: why this mail was sent by a machine ('' = a person wrote it): its headers (Auto-Submitted other than "no",
    X-Autoreply, X-Autorespond, Precedence bulk/junk/list/auto_reply, X-Auto-Response-Suppress: All), an out-of-office or
    automatic-reply subject, a noreply / mailer-daemon / postmaster sender, or — not for a CBX person's own mail — another
    helpdesk's acknowledgement in its new words ("Your ticket … has been acknowledged", "ticket with ID", "do not reply")."""
    h = AUTO_HEADER.search(m.get("headers") or "")
    if h:
        return "an automatic mail (" + h.group(0).split(":", 1)[0].strip() + ")"
    subj = m.get("subject") or ""
    if AUTO_SUBJECT.search(subj):
        return "an automatic reply (" + subj.strip()[:60] + ")"
    if ROBOT_SENDER.match(m.get("sender") or ""):
        return "an automatic sender (" + (m.get("sender") or "")[:80] + ")"
    if person:
        return ""
    words = strip_quoted(m.get("body") or "") if text is None else text
    own_subject = "" if re.match(r"(?i)^\s*(re|fw|fwd|aw|tr|sv|antw)\s*:", subj) else subj
    if is_ack(words or "") or (own_subject and is_ack(f"{own_subject}\n{words or ''}")):
        return "another helpdesk's acknowledgement"
    return ""


def _person(db: Session, email: str) -> Optional[User]:
    """1.0.109: the CBX person (not AXL) this address belongs to, if any."""
    return db.query(User).filter(func.lower(User.email) == (email or "").lower(), User.is_active.is_(True), User.is_axl.is_(False)).first()


def take(db: Session, m: dict, store: str = "") -> str:
    """One mail → a ticket, an answer, a reminder or thanks on its ticket, or nothing. Returns what became of it.

    1.0.84, from the real Chase mailbox: a forward or a reminder ("kindly assist with the below") brings the
    earlier messages with it so the ticket says what is wanted; a reminder, the same request sent again, or a
    colleague chasing on the same chain lands on the open ticket instead of opening another; a thank-you is
    never a ticket and never reopens a closed one; passwords and codes in a mail are removed before it is kept."""
    if db.query(SupportMail).filter(SupportMail.message_id == m["id"][:400]).first():
        return "seen"
    # 1.0.109: which ticket it belongs to — only by its tag ([T-0012]) or its own thread links, never by its subject — and, for the
    # requester's own mail, whether it is clearly a different problem (asked before anything is written: no write lock is held
    # while the model reads)
    t, by_chain = find_ticket(db, m)
    text = strip_quoted(m.get("body") or "")
    what = X.kind(text)
    person = _person(db, m.get("sender") or "")
    staff_person = person is not None and C.is_staff(db, person)
    verdict, reason = "", ""
    # 1.0.111 (T-0087): only a FINISHED ticket (done / not relevant) is judged — on an open one (new, waiting for the requester, with
    # someone) a reply in its thread is always a reply
    if (t is not None and m.get("sender") and m["sender"] == t.requester_email and not staff_person and not C.is_removed(t) and what != "thanks"
            and t.status == "closed" and not robot(m, text) and m["sender"] not in _ours()):
        verdict, reason = C.judge_new_problem(db, t, text)
    row = SupportMail(message_id=m["id"][:400], sender=m.get("sender", "")[:255], subject=(m.get("subject") or "")[:300], entry_id=(m.get("entry_id") or "")[:400], conversation=(m.get("conversation") or "")[:200])
    db.add(row)
    why = "no sender" if not m.get("sender") or "@" not in m["sender"] else "our own mailbox" if m["sender"] in _ours() else ""  # 1.0.109: robots below
    if why:
        row.outcome = f"ignored:{why}"[:24]
        log(db, "support_mail_ignored", actor_label=row.sender, target="support-mail", detail=f"{why}: {row.subject[:150]}")
        return row.outcome
    bot = robot(m, text, person=staff_person)
    if bot:  # 1.0.109: a machine never opens a ticket and is never mailed
        return _automatic(db, row, t, m, text, bot)
    if t is not None and C.is_removed(t):  # 1.0.109: a removed ticket never reopens and never mails anyone
        return _on_removed(db, row, t, m, text)
    if t is None and person is not None and C.is_staff(db, person) and m.get("conversation") and db.query(SupportMail.id).filter(
            SupportMail.conversation == m["conversation"][:200], SupportMail.outcome.like("ignored:auto%") | (SupportMail.outcome == "ignored:dismissed"),
            SupportMail.message_id != m["id"][:400]).first():  # 1.0.109 (review): a dismissed robot mail's chain too
        # 1.0.109: a desk person answering an automatic mail's chain (T-0018: "log this directly with the Reflex team") is never a ticket
        row.outcome, row.why = "ignored:auto_thread", "the desk answered an automatic mail's chain"
        log(db, "support_mail_ignored", actor=person, target="support-mail", detail=f"the desk on an automatic mail's chain: {row.subject[:150]}")
        return row.outcome
    if verdict == "different":  # 1.0.109: a new problem sent as a reply to an old ticket — asked to send it as a new mail
        return _new_problem(db, row, t, m, text, reason)
    if t is not None and t.requester_email != m["sender"] and not staff_person and colleague(db, t, m, by_chain):
        return _colleague(db, row, t, m, text)  # 1.0.111 (T-0086): a colleague in the requester's thread joins the ticket
    if t is not None and t.requester_email != m["sender"]:
        # 1.0.74: the support person answered a CBX mail — what they ask goes to the requester
        staff = person
        if staff is not None and C.is_staff(db, staff) and t.status != "closed" and (C.staff_may_see(db, staff, t) or C.can_assign(staff)):
            got = _staff_reply(db, row, t, m, text, staff)  # 1.0.109: a desk person's reply by mail counts
            if got:
                return got
        if staff is not None and C.is_staff(db, staff) and C.staff_may_see(db, staff, t) and not C.can_work(db, staff, t):
            # 1.0.103: someone on the desk mailing about a free ticket: a note for the team, never passed to the requester —
            # only the person it is assigned to writes to them (they take it first)
            n = C.add_message(db, t, "note", f"{staff.name} wrote by e-mail (not passed on — only the person it is assigned to writes to the requester):\n"
                              f"{C.mailtext.unlink(C.DONE_LINK.sub('[link removed]', text or '')).strip() or '(no new words)'}", author=staff, via="mail")
            _files(db, t, m, author_id=staff.id, msg=n, staff_mail=True)  # 1.0.109: their pictures too (for the desk only)
            row.ticket_id, row.outcome = t.id, "staff_note"
            log(db, "support_mail_staff_note", actor=staff, target=f"ticket:{C.ref(t)}", detail=row.subject[:150])
            return "staff_note"
        if staff is not None and C.is_staff(db, staff) and C.can_work(db, staff, t):  # 1.0.103: the assignee (or a manager), not anyone who sees it
            did = C.lead_by_mail(db, t, staff, text)
            # 1.0.109: a lead's pictures and files are kept for the desk (signatures and logos stay out by the picture rules), never
            # shown to the requester (1.0.98 dropped every pasted picture of a lead's mail)
            _files(db, t, m, author_id=staff.id, msg=_last_note(db, t), staff_mail=True)
            row.ticket_id, row.outcome = t.id, "lead"
            log(db, "support_mail_lead", actor=staff, target=f"ticket:{C.ref(t)}", detail=f"{did}: {row.subject[:150]}")
            return "lead"
        if by_chain and t.status != "closed":
            # 1.0.84: someone else on the same chain (their manager, a colleague) chasing it — a note on the ticket, not a new one
            n = C.add_message(db, t, "note", f"On the same mail chain, from {m.get('sender_name') or m['sender']} <{m['sender']}>:\n\n{text or '(no new words)'}", via="mail")
            # 1.0.109: kept like any mail's — under a note, so never the requester's (a desk person's or a colleague's files)
            _files(db, t, m, msg=n, author_id=person.id if person is not None else None, staff_mail=True)
            _chased(db, t, m.get("sender_name") or m["sender"], text)
            row.ticket_id, row.outcome = t.id, "follow_up"
            log(db, "support_mail_follow_up", actor_label=m["sender"], target=f"ticket:{C.ref(t)}", detail=f"someone else on the chain: {row.subject[:150]}")
            return "follow_up"
    if t is not None and t.requester_email == m["sender"]:
        return _from_requester(db, row, t, m, text, what)
    if what == "thanks":  # nothing asked and no ticket to put it on
        row.outcome = "ignored:thanks"
        log(db, "support_mail_ignored", actor_label=m["sender"], target="support-mail", detail=f"only a thank-you: {row.subject[:150]}")
        return row.outcome
    since = utcnow() - timedelta(hours=1)
    n = db.query(SupportTicket).filter(SupportTicket.requester_email == m["sender"], SupportTicket.source == "mail", SupportTicket.created_at >= since).count()
    if n >= max(1, settings.support_max_new_per_sender):
        row.outcome = "ignored:flood"
        log(db, "support_mail_ignored", actor_label=m["sender"], target="support-mail", detail=f"more than {settings.support_max_new_per_sender} new tickets this hour", outcome="blocked")
        return row.outcome
    t = _new_ticket(db, m)
    row.ticket_id, row.outcome = t.id, "new_ticket"
    C.start_reading(db, t)
    return "new_ticket"


SUBJ_NOISE = re.compile(r"^\s*((re|fw|fwd|aw|tr|sv|antw)\s*:\s*|\[?external\]?\s*:?\s*|urgent\s*[:!-]?\s*)+", re.I)


def subject_of(subject: str) -> str:
    """The subject without RE:/FW:/[External]/URGENT in front."""
    s = subject or ""
    for _ in range(4):
        s2 = SUBJ_NOISE.sub("", s).strip()
        if s2 == s:
            break
        s = s2
    return s or (subject or "").strip()


REFS = re.compile(r"<[^<>\s]{3,300}>")


def backfill_sent(db: Session, days: int = 3) -> int:
    """1.0.109: CBX's ticket mails sent through Outlook are kept with a placeholder until their Message-ID is known; it is read from Sent
    Items (by the mail's CBX mark), oldest unclaimed first, and stored — with Outlook's conversation. Returns how many were filled."""
    from collections import defaultdict
    from datetime import timezone

    rows = (db.query(SupportMail).filter(SupportMail.outcome == "sent", SupportMail.message_id.like("cbx-sent:%"), SupportMail.at >= utcnow() - timedelta(days=days))
            .order_by(SupportMail.id).all())
    by_mark = defaultdict(list)
    for r in rows:
        if r.why:
            by_mark[r.why].append(r)
    n = 0
    for mark, rs in by_mark.items():
        try:
            items = outlook.sent_ids(mark)
        except Exception:  # noqa: BLE001 — Outlook closed: the next read tries again
            continue
        items = sorted((x for x in items if x.get("message_id")), key=lambda x: str(x.get("at") or ""))
        known = {m for (m,) in db.query(SupportMail.message_id).filter(SupportMail.message_id.in_([x["message_id"][:400] for x in items] or [""])).all()}
        free = [x for x in items if x["message_id"][:400] not in known]
        for r in rs:
            sent_at = (r.at if r.at.tzinfo else r.at.replace(tzinfo=timezone.utc)).astimezone().replace(tzinfo=None) if r.at else None  # local, as Outlook's
            pick = next((x for x in free if not x.get("at") or not sent_at or x["at"].replace(tzinfo=None) >= sent_at - timedelta(minutes=10)), None)
            if pick is None:
                continue
            free.remove(pick)
            r.message_id, r.conversation = pick["message_id"][:400], (pick.get("conversation") or r.conversation or "")[:200]
            n += 1
    db.flush()
    return n


def find_ticket(db: Session, m: dict) -> tuple[Optional[SupportTicket], bool]:
    """1.0.109 (Edwin: a new mail with the same subject as a resolved ticket continued that ticket): a mail belongs to a ticket only
    by the ticket tag in its subject ([T-0012]) or by its own thread links — In-Reply-To / References naming a mail CBX kept for
    that ticket, or Outlook's conversation of one. The same (or a similar) subject alone is a new ticket. (ticket, by_chain)."""
    hit = C.REF_RE.search(m.get("subject") or "")
    t: Optional[SupportTicket] = db.get(SupportTicket, int(hit.group(1))) if hit else None
    if t is not None:
        return t, False
    return _by_links(db, m)


def _by_links(db: Session, m: dict) -> tuple[Optional[SupportTicket], bool]:
    """The ticket the mail's own thread links name (In-Reply-To / References, Outlook's conversation), if any."""
    mid = (m.get("id") or "")[:400]
    refs = []
    for h in re.finditer(r"(?im)^(in-reply-to|references):((?:[^\n]*\n?(?=[ \t]))*[^\n]*)", m.get("headers") or ""):
        refs += REFS.findall(h.group(2))
    refs = [r[:400] for r in dict.fromkeys(refs) if r[:400] != mid][:40]
    if refs:
        prev = db.query(SupportMail).filter(SupportMail.message_id.in_(refs), SupportMail.ticket_id.isnot(None)).order_by(SupportMail.id.desc()).first()
        t = db.get(SupportTicket, prev.ticket_id) if prev else None
        if t is not None:
            return t, True
    if m.get("conversation"):  # 1.0.73: a reply in the chain with the ticket number taken out (Outlook's conversation)
        prev = db.query(SupportMail).filter(SupportMail.conversation == m["conversation"][:200], SupportMail.ticket_id.isnot(None),
                                            SupportMail.message_id != mid).order_by(SupportMail.id.desc()).first()
        t = db.get(SupportTicket, prev.ticket_id) if prev else None
        if t is not None:
            return t, True
    return None, False


def colleague(db: Session, t: SupportTicket, m: dict, by_chain: bool) -> bool:
    """1.0.111 (T-0086): a colleague of the requester in the ticket's thread — the ticket's tag AND a thread link to that same ticket,
    from someone else at the requester's own company domain (never a generic mail domain like gmail.com). A tag without a thread
    link, or another domain, is not (a new ticket as before)."""
    from . import suggest as SG

    sender = (m.get("sender") or "").lower()
    if not sender or sender == (t.requester_email or "").lower() or C.is_removed(t) or by_chain:
        return False
    dom = SG._domain(t.requester_email)
    if not dom or SG._domain(sender) != dom:
        return False
    # 1.0.111 (review): a real reply in the requester's thread — In-Reply-To / References naming a mail of this ticket that came FROM the
    # requester or that the desk SENT (never a note's mail, never the colleague's own); Outlook's conversation alone is not enough
    refs = _refs(m)
    if not refs:
        return False
    req = (t.requester_email or "").lower()
    return db.query(SupportMail.id).filter(SupportMail.ticket_id == t.id, SupportMail.message_id.in_(refs),
                                           (func.lower(SupportMail.sender) == req) | (SupportMail.outcome == "sent")).first() is not None


def _refs(m: dict) -> list[str]:
    """The Message-IDs the mail's In-Reply-To / References headers name (not its own)."""
    mid = (m.get("id") or "")[:400]
    refs = []
    for h in re.finditer(r"(?im)^(in-reply-to|references):((?:[^\n]*\n?(?=[ \t]))*[^\n]*)", m.get("headers") or ""):
        refs += REFS.findall(h.group(2))
    return [r[:400] for r in dict.fromkeys(refs) if r[:400] != mid][:40]


def _colleague(db: Session, row: SupportMail, t: SupportTicket, m: dict, text: str) -> str:
    """Joins the ticket as "<name> (in the thread)" on the requester's side of the conversation; the requester stays who they were and
    the colleague gets no CBX mails (staff add them by hand if they should)."""
    name = (m.get("sender_name") or m["sender"]).strip()[:120]
    words = text or ""
    if empty_reply(t, m, words):
        n = C.add_message(db, t, "note", f"{name} (in the thread) replied with nothing new", name="Support desk", via="mail")
        _files(db, t, m, msg=n)
        row.ticket_id, row.outcome = t.id, "empty"
        return "empty"
    n = C.add_message(db, t, "requester", words or "(see the files)", name=f"{name}{C.IN_THREAD}", via="mail")
    _files(db, t, m, msg=n)
    row.ticket_id, row.outcome = t.id, "colleague"
    log(db, "support_mail_colleague", actor_label=m["sender"], target=f"ticket:{C.ref(t)}", detail=f"{name} in the requester's thread: {row.subject[:150]}"[:300])
    if t.status in ("pending", "ready", "new"):  # more of the same request — read again with it (AXL may have what it asked for now)
        if t.status == "pending":
            from . import followup as FU

            FU.stop(db, t, f"{C._first(name)} replied in the thread")
        t.status = "new"
        C.start_reading(db, t)
    elif t.assigned_to_id:  # with someone (or done): they are told, nothing reopens
        lead = db.get(User, t.assigned_to_id)
        C.tell(db, lead, f"{C.ref(t)}: {name} wrote in the thread", (words or "")[:600], C._link(t, True))
    return "colleague"


def _would_keep(t: SupportTicket, m: dict) -> bool:
    """1.0.111: would any of the mail's files be kept on the ticket (the rules of C.add_file and _files)?"""
    from pathlib import Path

    for name, data in m.get("files") or []:
        safe = re.sub(r"[^\w.\- ]", "_", name or "")[:120]
        if any(f["name"] == safe and f["size"] == len(data or b"") for f in t.files or []):
            continue
        if Path(name or "").suffix.lower() in C.FILE_EXT and data and len(data) <= C.MAX_FILE and len(t.files or []) < C.TICKET_MAX_FILES:
            return True
    return False


# 1.0.111: a reference the requester may answer with on its own — letters and digits ("Inv55501-26", "PO9990001234") or 5+ digits
REF_TOKEN = re.compile(r"(?<![\w+])(?=[\w\-/]*\d)(?:[A-Za-z][\w\-/]*\d[\w\-/]*|\d{5,}[\w\-/]*)")
GREET_HEAD = re.compile(r"^\s*(?:hi|hello|dear|good (?:day|morning|afternoon|evening)|morning|afternoon|greetings|hey)\b[\s,.!:;-]*", re.I)
NAME_ONLY = re.compile(r"^(?:[A-Z][\w'\-]*\s*){1,3}[,.!]?\s*$")


def new_words(text: str) -> list[str]:
    """1.0.111: the words of a reply once a greeting ("Hi", "Good day Rina,"), the sign-off with the name under it and signature
    contact lines are gone. Never lost (review): the words after a greeting ("Hi, Inv55501-26", "Hi, done"), a reference alone on its
    line ("4500012345" is not a phone number), and a reference under a sign-off ("Thanks\nInv55501-26")."""
    kept = []
    for ln in (text or "").splitlines():
        if X.CONTACT.search(ln) and not re.fullmatch(r"\s*\d[\w\-/]{4,}\s*", ln):
            continue  # "T: +27 …", "+971 4 000 0000" — but a bare "4500012345" is an answer
        g = GREET_HEAD.match(ln)
        if g:
            rest = ln[g.end():]
            if not REF_TOKEN.search(rest) and NAME_ONLY.match(rest or "x"):
                rest = ""  # "Hi Rina," — the greeting and a name only
            ln = rest
        kept.append(ln)
    body = "\n".join(kept)
    so = X.signoff(body)
    if so is None:
        return re.findall(r"\w+", body)
    return re.findall(r"\w+", body[: so.start()]) + REF_TOKEN.findall(body[so.end():])


def empty_reply(t: SupportTicket, m: dict, text: str) -> bool:
    """1.0.111 (T-0087): no new words once the quoted mail, the greeting and the signature are gone, and no file that would be kept."""
    return not new_words(text) and not _would_keep(t, m)


def _new_problem(db: Session, row: SupportMail, t: SupportTicket, m: dict, text: str, reason: str) -> str:
    """1.0.109: the requester sent a clearly different problem in an old ticket's thread. It is not added to that ticket and does not
    reopen it: they are asked once (through the outbox, the loop guard applies) to send it as a new mail; a quiet note goes on the old
    ticket, from which an admin can make it a ticket with one click (nobody is told)."""
    from .. import mailcfg

    row.ticket_id, row.outcome, row.why = t.id, "new_problem", (reason or "a different problem")[:200]
    row.text = (X.readable(m.get("body") or "") or text or "")[:4000]
    db.flush()
    n = C.add_message(db, t, "note", f"New problem sent in this thread — asked to log a new call: {reason or 'a different problem'}", name="Support desk", via="mail")
    n.mail_ref = row.id
    # 1.0.109 (review): its files and pictures are kept under the note (for the desk only) and move to the new ticket when an admin makes it
    _files(db, t, m, msg=n, staff_mail=True)
    # 1.0.109 (round 2): asked once — a second new problem in the same thread within a day gets the note, not another mail
    asked = db.query(SupportMail.id).filter(SupportMail.ticket_id == t.id, SupportMail.outcome.in_(("new_problem", "new_ticket_by_hand")),
                                            SupportMail.id != row.id, SupportMail.at >= utcnow() - timedelta(days=1)).first()
    if asked is None:
        C.mail_requester(db, t, "new_problem", C.values(db, t, support_address=mailcfg.desk_address() or "the support mailbox"))
    log(db, "support_mail_new_problem", actor_label=m["sender"], target=f"ticket:{C.ref(t)}", detail=f"{reason}: {row.subject[:150]}"[:300])
    return "new_problem"


UNDONE_LINE = "Added to the ticket after all (CBX 1.0.111)"


def _after_all(db: Session, row: SupportMail, t: SupportTicket, line: str, *, quiet: bool) -> None:
    """1.0.111: a mail kept aside as a "new problem" is the requester's reply after all — added as one (its new words only, as a
    normal reply would be; its files move from the note to the reply, theirs to see), the note gets `line`. `quiet`: no one is
    mailed, the reading that follows mails nobody either (the one-time repair)."""
    from ..models import SupportMessage

    note = db.query(SupportMessage).filter(SupportMessage.ticket_id == t.id, SupportMessage.mail_ref == row.id).first()
    words = (row.text or "").split("\n\n— Earlier, from ", 1)[0].strip()
    files = [f for f in (t.files or []) if note is not None and f.get("msg") == note.id]
    if new_words(words) or files:
        def move(m):
            if files:
                keep = []
                for f in t.files or []:
                    if note is not None and f.get("msg") == note.id:
                        f = {k: v for k, v in f.items() if k != "staff_mail"} | {"msg": m.id}
                    keep.append(f)
                t.files = keep
        C.requester_reply(db, t, words or "(see the files)", via="mail", files=move, quiet=quiet)
        if quiet:
            C.quiet_read(db, t)  # 1.0.111 (review): only when it is "new" now (a reading follows) — never on a ticket with someone
    else:
        C.add_message(db, t, "note", f"{C._first(t.requester_name) or 'The requester'} replied with nothing new", name="Support desk", via="mail")
    if note is not None:
        note.text = f"{note.text} — {line}"[:8000]  # one grey line on the page
    row.outcome = "reply_after_all"


def belongs_here(db: Session, mid: int, by: User) -> SupportTicket:
    """1.0.111: "It belongs here" — an admin says the mail kept aside as a new problem is a reply on its ticket after all (the reverse
    of "Make it a new ticket"). Added as the requester's reply — the person it is assigned to gets it as usual, the requester is not
    mailed; nothing learnt; audited. The caller starts the reading when it is new."""
    row = db.get(SupportMail, mid)
    if row is None or row.outcome != "new_problem" or not row.ticket_id:
        raise ValueError("That mail isn't kept aside as a new problem any more.")
    t = db.get(SupportTicket, row.ticket_id)
    if t is None or C.is_removed(t):
        raise ValueError("Its ticket isn't there any more.")
    _after_all(db, row, t, f"Added to the ticket after all by {by.name or by.email}", quiet=False)
    log(db, "support_new_problem_belongs", actor=by, target=f"ticket:{C.ref(t)}", detail=f"{row.sender}: {row.subject[:150]}"[:300])
    return t


def undo_new_problems(db: Session) -> int:
    """1.0.111, once (cbx/migrate.py, T-0087): every mail kept aside as a "new problem" whose ticket was NOT finished when it came in
    (before 1.0.111 the check ran on open tickets too) is added to its ticket as the requester's reply; the reading runs again (the
    support-triage routine picks up the tickets now "new"). No mail is sent — neither by the repair nor by that reading. Audited
    `support_new_problem_undone`. Returns how many."""
    from . import sla

    n = 0
    for row in db.query(SupportMail).filter(SupportMail.outcome == "new_problem", SupportMail.ticket_id.isnot(None)).order_by(SupportMail.id).all():
        t = db.get(SupportTicket, row.ticket_id)
        if t is None or C.is_removed(t):
            continue
        at = sla.aware(row.at) if row.at else None
        if at is None or _finished_then(db, t, at):
            continue  # it was finished when the mail came: a real new problem (or we can't tell — left as it is)
        if t.status == "closed":
            # 1.0.111 (review): finished since — the mail goes into its history as a note; the ticket is never reopened by the repair
            words = (row.text or "").split("\n\n— Earlier, from ", 1)[0].strip()
            C.add_message(db, t, "note", f"{t.requester_name or row.sender} wrote this while the ticket was still open (kept aside as a new problem "
                          f"before CBX 1.0.111):\n\n{words or '(no new words)'}", name="Support desk", via="mail")
            note = db.query(C.SupportMessage).filter(C.SupportMessage.ticket_id == t.id, C.SupportMessage.mail_ref == row.id).first()
            if note is not None:
                note.text = f"{note.text} — Added to the ticket's history after all (CBX 1.0.111)"[:8000]
            row.outcome = "note_after_all"
        else:
            _after_all(db, row, t, UNDONE_LINE, quiet=True)
        log(db, "support_new_problem_undone", actor_label="Support desk", target=f"ticket:{C.ref(t)}",
            detail=f"{'history note' if t.status == 'closed' else 'reply'} · {row.sender}: {row.subject[:150]}"[:300])
        n += 1
    db.flush()
    return n


CLOSING = ("support_ticket_closed", "support_ticket_done_by_link", "support_closed_not_relevant", "support_ticket_removed")
OPENING = ("support_ticket_reopened", "support_ticket_restored")


def _finished_then(db: Session, t: SupportTicket, at) -> bool:
    """1.0.111 (review): was the ticket finished (done / not relevant) when the mail came at `at`? Closed now: by its closed_at (no
    closed_at: we can't tell → treated as finished, so left alone). Open now: by its audit rows — the last close before the mail, with
    no reopen (or reply of the requester, which reopens a done ticket) between it and the mail."""
    from ..models import AuditLog
    from . import sla

    if t.status == "closed":
        return t.closed_at is None or sla.aware(t.closed_at) <= at
    since = sla.aware(t.created_at) if t.created_at else None
    q = db.query(AuditLog).filter(AuditLog.target == f"ticket:{C.ref(t)}", AuditLog.action.in_(CLOSING + OPENING))
    rows = [r for r in q.order_by(AuditLog.id).all() if (since is None or sla.aware(r.at) >= since) and sla.aware(r.at) <= at]
    last = rows[-1] if rows else None
    if last is None or last.action in OPENING:
        return False
    closed_at = sla.aware(last.at)
    replied = db.query(C.SupportMessage.id).filter(C.SupportMessage.ticket_id == t.id, C.SupportMessage.who == "requester",
                                                   C.SupportMessage.at > closed_at, C.SupportMessage.at < at).first()
    return replied is None


def new_problem_ticket(db: Session, mid: int, by: User) -> SupportTicket:
    """1.0.109: an admin makes the mail a ticket of its own (from the note on the old ticket). Nobody is told: it waits as free to
    take, its summary from the mail's words."""
    row = db.get(SupportMail, mid)
    if row is None or row.outcome != "new_problem":
        raise ValueError("That mail is a ticket already, or isn't there any more.")
    old = db.get(SupportTicket, row.ticket_id) if row.ticket_id else None
    u = db.query(User).filter(func.lower(User.email) == row.sender, User.is_active.is_(True), User.is_axl.is_(False)).first()
    title = subject_of(C.REF_RE.sub("", row.subject or "").strip()) or "A new problem"  # not the old ticket's tag
    t = C.create(db, system=None, description=row.text or row.subject or "(an empty mail)", title=title, requester=u,
                 name=(u.name if u else ""), email=row.sender, source="mail")
    t.status, t.summary = "ready", (f"{title} — from {t.requester_name or t.requester_email}. Sent in the thread of "
                                    f"{C.ref(old) if old else 'an older ticket'}; made a ticket of its own by {by.name}.\n{(row.text or '')[:1500]}")[:3000]
    row.outcome = "new_ticket_by_hand"[:24]
    row.ticket_id = t.id
    if old is not None:  # 1.0.109 (review): the mail's files and pictures come with it, under its first message
        from ..models import SupportMessage

        note = db.query(SupportMessage).filter(SupportMessage.ticket_id == old.id, SupportMessage.mail_ref == row.id).first()
        first = _last_msg(db, t, "requester")
        left = []
        for f in [f for f in (old.files or []) if note is not None and f.get("msg") == note.id]:
            p = C.file_path(old, f["n"])
            try:
                if p is None:
                    raise ValueError("not there any more")
                C.add_file(db, t, f["name"], p.read_bytes(), f.get("by") or row.sender, inline=bool(f.get("inline")), msg_id=first.id if first else None)
            except (OSError, ValueError) as e:
                left.append({"name": f["name"], "why": str(e).split(": ", 1)[-1][:160]})
        if left and first is not None:
            _left_out(db, t, first.id, left)
    C.add_message(db, t, "note", f"Made a ticket by {by.name} from a mail sent in the thread of {C.ref(old) if old else 'an older ticket'}. Nobody was told.", author=by)
    if old is not None:
        C.add_message(db, old, "note", f"{by.name} made the new problem a ticket of its own: {C.ref(t)}.", author=by)
    log(db, "support_new_problem_ticket", actor=by, target=f"ticket:{C.ref(t)}", detail=f"from {C.ref(old) if old else '-'}: {row.subject[:150]}")
    return t


def _new_ticket(db: Session, m: dict, by=None) -> SupportTicket:
    body = m.get("body") or ""
    u = db.query(User).filter(User.email == m["sender"], User.is_active.is_(True), User.is_axl.is_(False)).first()
    det = C.details_of(db, u) if u else {}
    subject = subject_of(m.get("subject") or "")
    desc = X.readable(body) or strip_quoted(body)
    h = X.hints(X.messages(body)[0]["text"] if not X.thin(strip_quoted(body)) else desc)
    t = C.create(db, system=None, description=desc or subject or "(an empty mail)", title=subject, requester=u, name=(u.name if u else m.get("sender_name") or ""),
                 email=m["sender"], department=det.get("department", ""), location=det.get("location", ""), phone=det.get("phone", ""), source="mail",
                 where=X.where_of(h))
    first = _last_msg(db, t, "requester")
    if X.had_secret(body):
        C.add_message(db, t, "note", "A password or code was in this mail. CBX removed it before keeping the mail — it is not on this ticket and is never sent on.", via="mail")
        log(db, "support_mail_secret_removed", actor_label=m["sender"], target=f"ticket:{C.ref(t)}", detail="a password or code was removed from the mail")
    _files(db, t, m, msg=first)
    return t


def _from_requester(db: Session, row: SupportMail, t: SupportTicket, m: dict, text: str, what: str) -> str:
    from . import followup as FU

    if empty_reply(t, m, text):
        # 1.0.111 (T-0087): an empty reply (only signature pictures) changes nothing — a quiet note, no mail back, no re-ask, the status
        # and the follow-up clock as they were
        n = C.add_message(db, t, "note", f"{C._first(t.requester_name) or t.requester_name or 'The requester'} replied with nothing new", name="Support desk", via="mail")
        _files(db, t, m, msg=n)  # what was left out (the signature pictures) is said under it, for the admins
        row.ticket_id, row.outcome = t.id, "empty"
        log(db, "support_mail_empty", actor_label=m["sender"], target=f"ticket:{C.ref(t)}", detail=row.subject[:200])
        return "empty"
    # 1.0.109: whatever they wrote (an answer, a thank-you, "see attached"), the requester wrote: the follow-up clock stops
    FU.stop(db, t, f"{C._first(t.requester_name) or 'the requester'} replied")
    if what == "thanks":  # 1.0.84: a thank-you never reopens a ticket or starts a new round of questions
        n = C.add_message(db, t, "requester", text or "(Thank you.)", via="mail")
        _files(db, t, m, msg=n)
        row.ticket_id, row.outcome = t.id, "thanks"
        log(db, "support_mail_thanks", actor_label=m["sender"], target=f"ticket:{C.ref(t)}", detail=row.subject[:200])
        return "thanks"
    if what == "follow_up" and t.status in ("ready", "assigned", "discussing"):
        n = C.add_message(db, t, "requester", text or "(A reminder.)", via="mail")
        _files(db, t, m, msg=n)
        _chased(db, t, t.requester_name or m["sender"], text)
        row.ticket_id, row.outcome = t.id, "follow_up"
        log(db, "support_mail_follow_up", actor_label=m["sender"], target=f"ticket:{C.ref(t)}", detail=row.subject[:200])
        return "follow_up"
    # 1.0.109: the reply's files and pasted pictures go on the ticket under its message BEFORE the lead is told (their mail carries them)
    C.requester_reply(db, t, text or "(an empty reply)", via="mail", files=lambda n: _files(db, t, m, msg=n))
    row.ticket_id, row.outcome = t.id, "reply"
    log(db, "support_mail_reply", actor_label=m["sender"], target=f"ticket:{C.ref(t)}", detail=row.subject[:200])
    if t.status == "new":
        C.start_reading(db, t)
    return "reply"


def _chased(db: Session, t: SupportTicket, who: str, text: str) -> None:
    """Someone is waiting on this ticket: tell whoever has it (the lead, else the people who assign)."""
    n = db.query(SupportMail).filter(SupportMail.ticket_id == t.id, SupportMail.outcome.in_(("follow_up", "duplicate"))).count() + 1
    title = f"{C.ref(t)}: {who} is waiting on it" + (f" (reminder {n})" if n > 1 else "")
    lead = db.get(User, t.assigned_to_id) if t.assigned_to_id else None
    for u in ([lead] if lead is not None else C.assigners(db)):
        C.tell(db, u, title, (text or "A reminder.")[:600], C._link(t, True))


def _files(db: Session, t: SupportTicket, m: dict, *, pasted: bool = True, author_id: Optional[int] = None, msg=None, staff_mail: bool = False) -> None:
    """The mail's attachments onto the ticket, under its message (`msg`, a SupportMessage or its id). 1.0.109: pictures pasted in
    the text are kept from every mail like attachments (the reader already left signatures and logos out); `staff_mail` — a desk
    person's mail (the requester never sees those files). Whatever is left out — by the reader or here — is said under the message
    (admins): never silent. `pasted=False` (kept for callers that still want it) leaves the pasted pictures out, and says so."""
    inline = set(m.get("inline") or [])
    mid = getattr(msg, "id", msg)
    left = [dict(x) for x in (m.get("left_out") or []) if isinstance(x, dict)]
    for name, data in m.get("files") or []:
        if name in inline and not pasted:
            left.append({"name": name, "why": "a picture pasted by someone other than the requester"})
            continue
        safe = re.sub(r"[^\w.\- ]", "_", name or "")[:120]
        if any(f["name"] == safe and f["size"] == len(data or b"") for f in t.files or []):
            left.append({"name": name, "why": "already on the ticket"})  # 1.0.84: a reminder carries the same spreadsheet again — once is enough
            continue
        try:
            C.add_file(db, t, name, data, m.get("sender", ""), inline=name in inline, author_id=author_id, msg_id=mid, staff_mail=staff_mail)
        except ValueError as e:
            left.append({"name": name, "why": str(e).split(": ", 1)[-1][:160]})
            continue
    if left:
        _left_out(db, t, mid, left)


def _left_out(db: Session, t: SupportTicket, mid, left: list) -> None:
    """1.0.109: the admin-only line under a message: "1 picture left out: looked like a signature logo"."""
    from ..models import SupportMessage

    target = db.get(SupportMessage, mid) if mid else None
    if target is None:
        target = C.add_message(db, t, "admin", "", name="Support desk", via="mail")
    target.left_out = list(target.left_out or []) + [{"name": str(x.get("name", ""))[:120], "why": str(x.get("why", ""))[:200]} for x in left][:40]
    db.flush()


def _last_msg(db: Session, t: SupportTicket, who: str):
    from ..models import SupportMessage

    return db.query(SupportMessage).filter(SupportMessage.ticket_id == t.id, SupportMessage.who == who).order_by(SupportMessage.id.desc()).first()


def _last_note(db: Session, t: SupportTicket):
    return _last_msg(db, t, "note")


# ---- 1.0.109: robots, removed tickets, a desk person's reply by mail --------------------------------------------------------

def _automatic(db: Session, row: SupportMail, t: Optional[SupportTicket], m: dict, text: str, why: str) -> str:
    """A machine's mail: in an existing ticket's thread, a quiet note on it (nothing changes, nobody is mailed); else it goes to the
    admins' "Automatic mails" (one line each, Open / Dismiss). Never a ticket, never mailed."""
    row.why, row.text = why[:200], (X.readable(m.get("body") or "") or text or "")[:4000]
    if t is not None:
        C.add_message(db, t, "note", f"An automatic mail on the same chain, from {m.get('sender_name') or m['sender']} <{m['sender']}> ({why}) — "
                      f"kept here; nothing changed and nobody was mailed:\n\n{(text or '(no words)')[:1500]}", via="mail")
        row.ticket_id, row.outcome = t.id, "auto_note"
        log(db, "support_mail_automatic", actor_label=m["sender"], target=f"ticket:{C.ref(t)}", detail=f"{why}: {row.subject[:150]}")
        return "auto_note"
    row.outcome = "ignored:automatic"
    log(db, "support_mail_ignored", actor_label=m["sender"], target="support-mail", detail=f"automatic — {why}: {row.subject[:150]}")
    return row.outcome


def _on_removed(db: Session, row: SupportMail, t: SupportTicket, m: dict, text: str) -> str:
    """A later mail in a removed ticket's thread: added to its history quietly (the admins see it under Removed); it never reopens
    the ticket and never mails anyone."""
    who = "requester" if m["sender"] == t.requester_email else "note"
    body = text or "(no new words)"
    n = C.add_message(db, t, who, body if who == "requester" else f"On the same mail chain, from {m.get('sender_name') or m['sender']} <{m['sender']}>:\n\n{body}",
                      via="mail")
    other = _person(db, m["sender"]) if who != "requester" else None
    _files(db, t, m, msg=n, author_id=other.id if other is not None else None, staff_mail=who != "requester")  # 1.0.109: a note's files: never the requester's
    C.add_message(db, t, "note", "A later mail on this removed ticket — it stays removed; nobody was told.", name="Support desk")
    row.ticket_id, row.outcome = t.id, "removed"
    log(db, "support_mail_on_removed", actor_label=m["sender"], target=f"ticket:{C.ref(t)}", detail=row.subject[:150])
    return "removed"


def _staff_reply(db: Session, row: SupportMail, t: SupportTicket, m: dict, text: str, staff: User) -> str:
    """A desk person, lead or admin replied in the ticket's thread from Outlook. When the ticket is free it becomes theirs ("Taken by
    … — replied by mail"); a reply that already went to the requester (they are on its To or Cc) is shown as their reply and moves
    the status on ("With <name>": no longer waiting for the requester); the requester's next reply goes to them. A reply only to
    CBX's own mail (no requester on it) is left to the old paths ('' — the lead's question is passed on)."""
    from . import own as OWN

    to_requester = bool(t.requester_email) and t.requester_email in (m.get("recipients") or [])
    took = False
    if OWN.is_free(t) and (to_requester or C.can_assign(staff)):  # someone on the desk writing only to the desk mailbox about a free ticket: a note (1.0.103)
        took = OWN.take_by_mail(db, t, staff)
    if not to_requester:
        return ""  # to CBX's own mail: the assignee's (or a manager's) question is passed on, anyone else's is a note
    words = C.mailtext.unlink(C.DONE_LINK.sub("[link removed]", text or "")).strip() or "(no new words)"
    n = C.add_message(db, t, "staff", words, author=staff, via="mail", mailed=True)
    _files(db, t, m, author_id=staff.id, msg=n, staff_mail=True)
    if t.assigned_to_id == staff.id and t.status in ("assigned", "pending", "ready", "new"):
        t.status = "discussing"
    from . import followup as FU

    if "?" in words:  # 1.0.109 (round 2): they asked the requester something — the desk waits on the answer (the follow-up clock)
        FU.start(db, t, "person", words)
    else:
        FU.stop(db, t, f"{C._first(staff.name) or 'the desk'} replied by mail")
    row.ticket_id, row.outcome = t.id, "staff_reply"
    log(db, "support_mail_staff_reply", actor=staff, target=f"ticket:{C.ref(t)}", detail=f"{'took it · ' if took else ''}{row.subject[:150]}")
    return "staff_reply"


def boxes() -> list[dict]:
    from .. import mailcfg

    return [b for b in mailcfg.get()["boxes"] if b.get("store")]


def _since(b: dict):
    """Local time to read from: the box's start date, else the last two days (never the whole history)."""
    from datetime import datetime, timezone

    s = b.get("since")
    try:
        d = datetime.fromisoformat(s) if s else utcnow() - timedelta(days=2)
    except ValueError:
        d = utcnow() - timedelta(days=2)
    return d.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None) if d.tzinfo is None else d.astimezone().replace(tzinfo=None)


def poll(db: Session, only: str = "") -> dict:
    """Read the support mailboxes once (or one of them). {read, tickets, replies, ignored, error}."""
    got = {"read": 0, "tickets": 0, "replies": 0, "ignored": 0, "error": ""}
    errors = []
    try:
        repair_once(db)  # 1.0.109
    except Exception:  # noqa: BLE001 — never stops the mail being read
        db.rollback()
    try:
        backfill_sent(db)  # 1.0.109: the Message-IDs of CBX's own sent ticket mails, from Sent Items
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    for b in boxes():
        if only and b["store"] != only:
            continue
        if not only and not b.get("on"):
            continue
        try:
            mails = outlook.fetch_since(b["store"], b.get("folder") or "Inbox", _since(b), 20, set(), True)
        except Exception as e:  # noqa: BLE001 — Outlook closed, mailbox missing: said, never raised
            errors.append(f"{b['store']}: {e}"[:300])
            _note(db, b["store"], str(e)[:300])
            continue
        n = 0
        for m in mails:
            try:
                what = take(db, m, b["store"])
                db.commit()
            except Exception as e:  # noqa: BLE001 — one bad mail never stops the rest
                db.rollback()
                log(db, "support_mail_failed", actor_label=m.get("sender", ""), target="support-mail", detail=str(e)[:300], outcome="failed")
                db.commit()
                what = "failed"
            try:
                outlook.mark_read(m["entry_id"])
            except Exception:  # noqa: BLE001
                pass
            got["read"] += 1
            n += 1
            got["tickets" if what == "new_ticket" else "replies" if what in ("reply", "lead", "staff_reply") else "ignored"] += 1 if what != "seen" else 0
        _note(db, b["store"], f"ok: read {n}")
    got["error"] = "; ".join(errors)
    state.set(db, STATE_KEY, f"{utcnow().isoformat()}|{got['error'] or 'ok: read ' + str(got['read'])}")
    db.commit()
    return got


def _note(db: Session, store: str, what: str) -> None:
    import json

    try:
        d = json.loads(state.get(db, STATE_KEY + ".boxes") or "{}")
    except ValueError:
        d = {}
    d[store] = {"at": utcnow().isoformat(), "last": what}
    state.set(db, STATE_KEY + ".boxes", json.dumps(d))


def box_state(db: Session) -> dict:
    import json

    try:
        return json.loads(state.get(db, STATE_KEY + ".boxes") or "{}")
    except ValueError:
        return {}


def status(db: Session) -> dict:
    from .. import mailcfg

    c = mailcfg.get()
    ok, why = outlook.available() if c["transport"] == "outlook" else (True, "")
    last = (state.get(db, STATE_KEY) or "|").split("|", 1)
    bs = boxes()
    on = [b for b in bs if b.get("on")]
    return {"on": bool(on), "reachable": ok, "detail": why, "mailbox": ", ".join(b["store"] for b in on) or (bs[0]["store"] if bs else ""),
            "folder": on[0].get("folder", "Inbox") if on else "Inbox", "transport": c["transport"], "last_at": last[0] or None, "last": last[1] if len(last) > 1 else ""}


# ---- 1.0.109: Automatic mails (the owner and admins) — one line each: Open, Dismiss; and the one-time repair ---------------------
AUTO_LISTED = ("ignored:automatic", "ignored:auto_moved")
REPAIR_KEY = "support.repair.1109"


def automatic(db: Session, limit: int = 100) -> list[dict]:
    rows = db.query(SupportMail).filter(SupportMail.outcome.in_(AUTO_LISTED)).order_by(SupportMail.id.desc()).limit(limit).all()
    out = []
    for r in rows:
        t = db.get(SupportTicket, r.ticket_id) if r.ticket_id else None
        out.append({"id": r.id, "sender": r.sender, "subject": r.subject, "why": r.why or "an automatic mail", "text": (r.text or "")[:4000],
                    "at": r.at.isoformat() if r.at else None, "ticket": {"id": t.id, "ref": C.ref(t), "removed": C.is_removed(t)} if t else None})
    return out


def automatic_count(db: Session) -> int:
    return db.query(SupportMail).filter(SupportMail.outcome.in_(AUTO_LISTED)).count()


def _auto_row(db: Session, mid: int) -> SupportMail:
    r = db.get(SupportMail, mid)
    if r is None or r.outcome not in AUTO_LISTED:
        raise ValueError("That automatic mail isn't on the list any more.")
    return r


def dismiss(db: Session, mid: int, by: User) -> None:
    r = _auto_row(db, mid)
    r.outcome = "ignored:dismissed"
    log(db, "support_automatic_dismissed", actor=by, target="support-mail", detail=f"{r.sender}: {r.subject[:150]}")


def automatic_to_ticket(db: Session, mid: int, by: User) -> SupportTicket:
    """A person decided it is a request after all: a ticket from its words (its removed ticket restored, when it had one)."""
    from . import own as OWN

    r = _auto_row(db, mid)
    t = db.get(SupportTicket, r.ticket_id) if r.ticket_id else None
    if t is not None:
        if C.is_removed(t):
            OWN.restore(db, t, by)
        r.outcome = "new_ticket"
        log(db, "support_automatic_to_ticket", actor=by, target=f"ticket:{C.ref(t)}", detail=f"{r.sender}: {r.subject[:150]}")
        return t
    m = {"id": r.message_id, "entry_id": r.entry_id, "sender": r.sender, "sender_name": "", "subject": r.subject, "body": r.text or r.subject,
         "conversation": r.conversation, "files": []}
    r.outcome = "ignored:to_ticket"
    t = make_ticket(db, m, by)
    return t


def repair_once(db: Session) -> int:
    """1.0.109, once: an open ticket whose first mail was a machine's (T-0018: REFLEX Support's acknowledgement) is moved to
    Automatic mails — removed quietly, nobody mailed, audited. Returns how many."""
    if state.get(db, REPAIR_KEY) == "1":
        return 0
    from . import own as OWN

    n = 0
    for t in db.query(SupportTicket).filter(SupportTicket.status != "closed", SupportTicket.source == "mail").order_by(SupportTicket.id).all():
        # 1.0.109 (review): the first mail as it came (its own subject, not the ticket's title), the sender's own words only; a forward is a
        # person passing something on — never a robot
        mail = db.query(SupportMail).filter(SupportMail.ticket_id == t.id, SupportMail.outcome == "new_ticket").order_by(SupportMail.id).first()
        subj = (mail.subject if mail is not None else "") or ""
        if re.match(r"(?i)^\s*(fw|fwd|tr|wg)\s*:", subj):
            continue
        first = (t.description or "").split("\n\n— Earlier", 1)[0]
        person = _person(db, t.requester_email)
        why = robot({"sender": t.requester_email, "subject": subj, "body": first, "headers": ""}, first, person=person is not None and C.is_staff(db, person))
        if not why:
            continue
        OWN.remove(db, t, None, how="automatic", reason=why)
        row = db.query(SupportMail).filter(SupportMail.ticket_id == t.id, SupportMail.outcome == "new_ticket").order_by(SupportMail.id).first()
        if row is not None:
            row.outcome, row.why, row.text = "ignored:auto_moved", why[:200], first[:4000]
        log(db, "support_repair_automatic", actor_label="Support desk", target=f"ticket:{C.ref(t)}", detail=f"moved to Automatic mails: {why}")
        n += 1
    state.set(db, REPAIR_KEY, "1")
    db.commit()
    return n


# ---- by hand, from the Mailer: the mail the agents skipped, or one that belongs on a ticket ----------

def make_ticket(db: Session, m: dict, by, store: str = "") -> SupportTicket:
    """Turn this mail into a ticket even if it was skipped (automatic, a flood) — a person decided."""
    row = db.query(SupportMail).filter(SupportMail.message_id == m["id"][:400]).first()
    if row is not None and row.ticket_id and row.outcome in ("new_ticket", "reply", "attached", "staff_reply"):
        raise ValueError(f"That mail is already on ticket {C.ref(db.get(SupportTicket, row.ticket_id))}.")
    if row is None:
        row = SupportMail(message_id=m["id"][:400], sender=m.get("sender", "")[:255], subject=(m.get("subject") or "")[:300], entry_id=(m.get("entry_id") or "")[:400], conversation=(m.get("conversation") or "")[:200])
        db.add(row)
    t = _new_ticket(db, m, by)
    row.ticket_id, row.outcome, row.entry_id = t.id, "new_ticket", (m.get("entry_id") or "")[:400]
    log(db, "support_mail_made_ticket", actor=by, target=f"ticket:{C.ref(t)}", detail=f"{m.get('sender')}: {row.subject[:150]} (by hand, from {store or 'the mailbox'})")
    try:
        outlook.mark_read(m["entry_id"])
    except Exception:  # noqa: BLE001
        pass
    C.start_reading(db, t)
    return t


def attach(db: Session, m: dict, t: SupportTicket, by) -> None:
    """Put this mail on an existing ticket: as the requester's answer when it is theirs, else as a note."""
    row = db.query(SupportMail).filter(SupportMail.message_id == m["id"][:400]).first()
    if row is None:
        row = SupportMail(message_id=m["id"][:400], sender=m.get("sender", "")[:255], subject=(m.get("subject") or "")[:300], entry_id=(m.get("entry_id") or "")[:400], conversation=(m.get("conversation") or "")[:200])
        db.add(row)
    text = X.readable(m.get("body") or "") or "(an empty mail)"
    if m.get("sender") == t.requester_email:  # 1.0.109: its files under its message, before the lead is told
        C.requester_reply(db, t, text, via="mail", files=lambda n: _files(db, t, m, msg=n))
    else:
        n = C.add_message(db, t, "note", f"Mail from {m.get('sender_name') or m.get('sender')} <{m.get('sender')}> — {m.get('subject') or ''}\n\n{text}", author=by)
        _files(db, t, m, msg=n, staff_mail=True)  # 1.0.109: under a note — never the requester's
    row.ticket_id, row.outcome, row.entry_id = t.id, "attached", (m.get("entry_id") or "")[:400]
    log(db, "support_mail_attached", actor=by, target=f"ticket:{C.ref(t)}", detail=f"{m.get('sender')}: {row.subject[:150]}")
    try:
        outlook.mark_read(m["entry_id"])
    except Exception:  # noqa: BLE001
        pass
    if t.status == "new":
        C.start_reading(db, t)


@jobs.routine("support-mail", max(15, settings.support_mail_seconds))
def mail_routine() -> None:
    if not any(b.get("on") for b in boxes()):
        return
    db = SessionLocal()
    try:
        poll(db)
    finally:
        db.close()
