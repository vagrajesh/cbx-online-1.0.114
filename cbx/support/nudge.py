# 1.0.98: from CBX-Connect 2.10.3 cbx/support/nudge.py (the old desk: leads are CBX users; mail through cbx/mailbox.py)
"""Nudge — the desk's reminders (1.0.98, from CBX-Connect 2.10: Edwin: "Any ticket not accepted: a notification. One mail
per assignee: if they have 1 notification it says there is 1, get on CBX; if 10, it says 10 outstanding … not-accepted
tickets: after 6 hours to the assignee, and after 12 hours to Sashen so he can reassign. Any ticket active for longer
than 48 hours needs a notification. Make the notification a small dashboard in a single mail").

Every 15 minutes, while the desk sends (CBX may send mail: Mailer → Mail set-up):
    not accepted 6 h after assignment   → one mail to the lead, at once
    not accepted after 12 h             → one mail to the escalation person (Support set-up), with a link to reassign
    not accepted after 2 days           → one more to both
    accepted but still open after 48 h  → a notification in the lead's digest (the escalation person sees the count)
Each fires once per ticket, per rule, per assignment: a reassignment starts the clock again.

Everything else waits for the person's own schedule (My settings, cbx/support/people.py): one digest per person per run,
only when they have something — "You have N outstanding notifications", a small dashboard, the list. An acceptance
alert looks the same, with its reason highlighted on top. Mail only through cbx/mailbox.py (its allow-list, Outlook or
SMTP, "check first" drafts); HTML Outlook can show (tables, inline styles) with a plain-text part; English.
"""
from __future__ import annotations

import html as H
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from .. import jobs
from ..audit import log
from ..db import session_scope, utcnow
from ..models import SupportNotice, SupportSettings, SupportTicket, User
from . import people as P
from . import sla

logger = logging.getLogger("cbx.support.nudge")
RULES = {"accept6": 6, "accept12": 12, "accept2d": 48}
OPEN_HOURS = 48
LEAD_RULES = ("accept2d", "accept6")  # the highest due one goes; a lower one it passed counts as sent
ESC_RULES = ("accept2d", "accept12")
WORDS = {"accept6": "6 hours", "accept12": "12 hours", "accept2d": "2 days"}
STATE_KEY = "support.nudge"
STARTED_KEY = "support.started_1098"  # 1.0.98 fix: when 1.0.98 first ran here — no reminder clock starts before it


def started_at(db: Session) -> Optional[datetime]:
    from .. import compat2

    v = compat2.state_get(db, STARTED_KEY, {}) or {}
    try:
        return sla.aware(datetime.fromisoformat(v["at"])) if isinstance(v, dict) and v.get("at") else None
    except ValueError:
        return None


def clock_start(t: SupportTicket, since: Optional[datetime]) -> Optional[datetime]:
    """When the lead could first be reminded: the assignment — or, for a ticket assigned before 1.0.98, the moment 1.0.98
    first started (its lead never had an Accept button before), whichever is later (as CBX-Connect's clock_start)."""
    a = sla.aware(t.assigned_at)
    if a is None:
        return None
    return max(a, since) if since else a


def upgrade(db: Session) -> dict:
    """1.0.98 fix, once, at first start (the start routine below, and before Nudge's first run): open tickets assigned before
    1.0.98 get an Accept link (a mail never carries an empty one); those already being worked — under discussion, or the lead
    or the support team wrote after the assignment — count as accepted from that first activity; the reminder clocks start
    now. Audited. Returns what it did ({} when it ran before)."""
    from sqlalchemy import or_

    from .. import compat2
    from ..models import SupportMessage
    from . import core as C
    from . import tokens as K

    if compat2.state_get(db, STARTED_KEY, None):
        return {}
    now = utcnow()
    linked = accepted = 0
    for t in open_tickets(db):
        if not t.accept_nonce:
            t.accept_nonce = K.nonce()
            t.accept_hash = K.digest(K.make("accept", t.id, t.accept_nonce))
            linked += 1
        if t.accepted_at is not None:
            continue
        lead = db.get(User, t.assigned_to_id)
        q = db.query(SupportMessage).filter(SupportMessage.ticket_id == t.id, or_(SupportMessage.who == "staff", SupportMessage.author_id == t.assigned_to_id))
        if t.assigned_at is not None:
            q = q.filter(SupportMessage.at >= t.assigned_at)
        first = q.order_by(SupportMessage.id).first()
        if first is None and t.status != "discussing":
            continue
        t.accepted_at = first.at if first is not None else (t.assigned_at or now)
        t.accepted_by = ((lead.name if lead else "") or "the lead")[:160]
        accepted += 1
        log(db, "support_accepted", actor_label="1.0.98 upgrade", target=f"ticket:{C.ref(t)}",
            detail=f"assigned before 1.0.98 and already being worked: counted as accepted by {t.accepted_by} from {sla.shown(t.accepted_at)}")
    compat2.state_set(db, STARTED_KEY, {"at": now.isoformat(), "linked": linked, "accepted": accepted})
    log(db, "support_upgrade_1098", actor_label="Support desk", target="support",
        detail=f"Accept links for {linked} open ticket(s); {accepted} already being worked counted as accepted; reminder clocks start now")
    db.flush()
    return {"linked": linked, "accepted": accepted}


def sending() -> bool:
    """The desk sends while CBX may send mail at all (Mailer → Mail set-up)."""
    from .. import mailcfg

    return bool(mailcfg.get().get("enabled"))


def open_tickets(db: Session) -> list[SupportTicket]:
    return db.query(SupportTicket).filter(SupportTicket.status.in_(("assigned", "discussing")), SupportTicket.assigned_to_id.isnot(None)) \
        .order_by(SupportTicket.id).all()


def _fired(db: Session, t: SupportTicket, rule: str, uid: int) -> bool:
    return db.query(SupportNotice.id).filter(SupportNotice.ticket_id == t.id, SupportNotice.rule == rule, SupportNotice.seq == (t.assign_seq or 0),
                                             SupportNotice.user_id == uid).first() is not None


def _mark(db: Session, t: Optional[SupportTicket], rule: str, uid: int, mail_id: Optional[int] = None, now: Optional[datetime] = None) -> SupportNotice:
    n = SupportNotice(ticket_id=t.id if t else None, rule=rule, seq=(t.assign_seq or 0) if t else 0, user_id=uid, mail_id=mail_id, at=now or utcnow())
    db.add(n)
    return n


def _first(u: Optional[User]) -> str:
    return ((u.name if u else "") or "").split(" ")[0] or "there"


# ---- one run ----------------------------------------------------------------------------------------------
def run(db: Session, now: Optional[datetime] = None) -> dict:
    from .. import compat2
    from . import core as C
    from . import team as TM

    now = sla.aware(now or utcnow())
    upgrade(db)  # 1.0.98 fix: once — before any reminder can count from an assignment made before 1.0.98
    since = started_at(db)
    prev = compat2.state_get(db, STATE_KEY, {}) or {}
    res = {"last_run": now.isoformat(), "alerts": 0, "digests": 0, "lines": list(prev.get("lines") or [])[:8], "note": ""}
    if not sending():
        res["note"] = "Sending is off — nothing to send."
        return _save(db, res)
    esc = P.escalation(db)
    tickets = open_tickets(db)
    sent: list[dict] = []
    due_alerts: dict[int, list[tuple[SupportTicket, str]]] = {}  # person → [(ticket, rule)]: one mail per person per run
    for t in tickets:
        if t.accepted_at:
            if sla.hours_since(t.accepted_at, now) >= OPEN_HOURS and not _fired(db, t, "open48", t.assigned_to_id):
                _mark(db, t, "open48", t.assigned_to_id, now=now)  # goes in their digest; the escalation person sees the count
            continue
        start = clock_start(t, since)
        if start is None:
            continue
        h = (now - start).total_seconds() / 3600
        who = [(t.assigned_to_id, LEAD_RULES)] + ([(esc.id, ESC_RULES)] if esc else [])
        for uid, rules in who:
            due = [r for r in rules if h >= RULES[r] and not _fired(db, t, r, uid)]
            if not due:
                continue
            for lower in due[1:]:
                _mark(db, t, lower, uid, now=now)  # a rule it passed while nobody ran (the server was off) counts as sent
            if rules is ESC_RULES and uid == t.assigned_to_id:
                _mark(db, t, due[0], uid, now=now)  # the escalation person is the lead: their own reminder is enough
                continue
            due_alerts.setdefault(uid, []).append((t, due[0]))
    for uid, items in due_alerts.items():
        u = db.get(User, uid)
        if u is None or not u.is_active or not u.email:
            continue
        # 1.0.98 fix: the "sent" marks are saved BEFORE the mail goes — a later failure (the run's commit) can never resend it
        marks = [_mark(db, t, rule, uid, None, now=now) for t, rule in items]
        db.commit()
        o = _alerts(db, u, items, esc=esc, tickets=tickets, now=now, since=since)
        for n in marks:
            n.mail_id = o.id if o is not None else None
        res["alerts"] += 1
        sent.append({"at": sla.shown(now), "text": f"{'Told' if esc is not None and uid == esc.id else 'Reminded'} {_first(u)} about " + ", ".join(C.ref(t) for t, _ in items)})
    # the digests: every person whose time has come (the slot is used up even when they have nothing)
    people = {t.assigned_to_id for t in tickets} | ({esc.id} if esc else set())
    for uid in sorted(people):
        u = db.get(User, uid)
        if u is not None and u.is_active:
            P.get(db, u)
    for p in db.query(SupportSettings).order_by(SupportSettings.id).all():
        if not P.digest_due(p, now):
            continue
        p.digest_checked_at = now
        if p.user_id in due_alerts:  # an alert went this run and carried the same dashboard: one mail is enough
            continue
        u = db.get(User, p.user_id)
        if u is None or not u.is_active or not u.email:
            continue
        dash = dashboard(db, u, tickets, now, esc_view=esc is not None and u.id == esc.id, since=since)
        if not dash["n"]:
            continue
        subject, html, text = render(db, u, p, dash, now=now)
        p.digest_sent_at = now
        db.commit()  # 1.0.98 fix: the slot is used up before the mail goes (a later failure never sends it twice)
        o = _send(db, u, subject, text, html, "digest")
        _mark(db, None, "digest", u.id, o.id if o is not None else None, now=now)
        res["digests"] += 1
        sent.append({"at": sla.shown(now), "text": f"Digest to {_first(u)}: {dash['n']} notification{'s' if dash['n'] != 1 else ''}"})
    if sent:
        res["lines"] = (sent[::-1] + res["lines"])[:8]
        with compat2.working(db, TM.NUDGE, "Sending reminders and digests", "support", does=TM.DOES["Nudge"]):
            TM.say(db, "Nudge", f"Sent {res['alerts']} reminder{'s' if res['alerts'] != 1 else ''} and {res['digests']} digest{'s' if res['digests'] != 1 else ''}.",
                   ref="support")
        log(db, "support_nudge", actor_label="Nudge", target="support", detail="; ".join(x["text"] for x in sent)[:500])
    return _save(db, res)


def _save(db: Session, res: dict) -> dict:
    from .. import compat2

    compat2.state_set(db, STATE_KEY, res)
    return res


def _send(db: Session, u: User, subject: str, text: str, html: str, kind: str, ticket: Optional[SupportTicket] = None):
    """Through the old outbox (cbx/mailbox.py): its allow-list (a CBX person always passes), Outlook or SMTP, and "check
    first" drafts (Mailer → Mail set-up: support, or support:nudge / support:digest)."""
    from .. import mailbox

    return mailbox.send(db, to=[u.email], subject=subject, body=text, html=html, kind="support", ref=f"ticket:{ticket.id}" if ticket else "support:nudge",
                        check=True, hold=f"support:{kind}", ticket_id=ticket.id if ticket else None)


def _alerts(db: Session, u: User, items: list, *, esc: Optional[User], tickets: list, now: datetime, since: Optional[datetime] = None):
    """The acceptance alerts for one person this run, in one mail: each reason highlighted on top, their dashboard below."""
    from . import core as C

    as_esc = esc is not None and u.id == esc.id
    reasons = []
    for t, rule in items:
        ago = sla.span((now - (clock_start(t, since) or now)).total_seconds())
        lead = db.get(User, t.assigned_to_id)
        mine = t.assigned_to_id == u.id
        if mine:
            reasons.append({"title": f"Ticket {C.ref(t)} isn't accepted yet", "link": C.accept_link(db, t), "label": "Accept",  # no link: no button
                            "line": f"{t.title} was assigned to you {ago} ago. Accept it when you take it on — the time it takes counts from then."})
        else:
            reasons.append({"title": f"Ticket {C.ref(t)} isn't accepted in {WORDS['accept12' if rule == 'accept12' else 'accept2d']}",
                            "line": f"{t.title} — assigned to {lead.name if lead else 'a lead'} {ago} ago. Reassign it, or ask them to accept it.",
                            "link": C.staff_url(db, t), "label": "Reassign in CBX"})
        C.add_message(db, t, "note", (f"Reminded {lead.name if lead else 'the lead'}: not accepted {ago} after assignment." if mine else
                                      f"Told {u.name}, who reassigns: not accepted {ago} after assignment."), name="Nudge")
    dash = dashboard(db, u, tickets, now, esc_view=as_esc, since=since)
    _, html, text = render(db, u, P.get(db, u), dash, now=now, reasons=reasons)
    if len(items) == 1:
        t, rule = items[0]
        ago = sla.span((now - (clock_start(t, since) or now)).total_seconds())
        lead = db.get(User, t.assigned_to_id)
        subject = (f"[{C.ref(t)}] Not accepted yet — assigned {ago} ago" if t.assigned_to_id == u.id else
                   f"[{C.ref(t)}] Not accepted in {ago} — {lead.name if lead else 'the lead'}")
    else:
        subject = f"[CBX] {len(items)} tickets not accepted yet"
    return _send(db, u, subject, text, html, "nudge", ticket=items[0][0] if len(items) == 1 else None)


# ---- what one person has --------------------------------------------------------------------------------
def dashboard(db: Session, u: User, tickets: list, now: datetime, *, esc_view: bool = False, since: Optional[datetime] = None) -> dict:
    """Their own tickets (with an Accept link where it waits for them). The escalation person's boxes count the whole
    desk, and their list adds the other leads' tickets not accepted in 12 hours (to reassign)."""
    def over48(t) -> bool:
        return bool(t.accepted_at) and sla.hours_since(t.accepted_at, now) >= OPEN_HOURS

    def waited(t) -> float:
        return (now - (clock_start(t, since) or now)).total_seconds()

    mine = [t for t in tickets if t.assigned_to_id == u.id]
    rows = []
    for t in sorted((t for t in mine if not t.accepted_at), key=waited, reverse=True):
        rows.append({"t": t, "state": "Not accepted", "how": sla.span(waited(t)), "link": ("accept", "Accept"), "flag": True})
    for t in sorted((t for t in mine if t.accepted_at), key=lambda x: sla.aware(x.accepted_at)):
        rows.append({"t": t, "state": "Open over 48 h" if over48(t) else "Accepted", "how": sla.span((now - sla.aware(t.accepted_at)).total_seconds()),
                     "link": None, "flag": over48(t)})
    n = sum(1 for t in mine if not t.accepted_at or over48(t))
    scope = mine
    if esc_view:
        scope = tickets
        late = [t for t in tickets if t.assigned_to_id != u.id and not t.accepted_at and waited(t) >= RULES["accept12"] * 3600]
        for t in sorted(late, key=waited, reverse=True):
            lead = db.get(User, t.assigned_to_id)
            rows.append({"t": t, "state": f"Not accepted · {lead.name if lead else 'a lead'}", "how": sla.span(waited(t)), "link": ("ticket", "Reassign"), "flag": True})
        n += len(late) + sum(1 for t in tickets if t.assigned_to_id != u.id and over48(t))
    return {"desk": esc_view, "outstanding": len(scope), "accepted": sum(1 for t in scope if t.accepted_at), "waiting": sum(1 for t in scope if not t.accepted_at),
            "over48": sum(1 for t in scope if over48(t)), "rows": rows, "n": n}


# ---- the mail ------------------------------------------------------------------------------------------
INK, MUTE, RULE_C, WARN_BG, WARN, GREEN = "#1f2937", "#6b7280", "#e5e7eb", "#fff4d6", "#b8731a", "#0b7a52"


def _e(s) -> str:
    return H.escape(str(s or ""))


def render(db: Session, u: User, p, dash: dict, *, now: datetime, reasons: Optional[list] = None) -> tuple[str, str, str]:
    """(subject, html, text): the reasons on top when there are some, then the dashboard and the list."""
    from .. import state
    from . import core as C

    base = state.public_url(db)
    n = dash["n"]
    first = _first(u)
    noun = f"{n} outstanding notification{'s' if n != 1 else ''}"
    lead_line = f"You have {noun} — please open CBX."
    desk_link = f"{base}/#/support"
    h: list[str] = [f"<p style=\"margin:0 0 14px\">Hi {_e(first)},</p>"]
    tx: list[str] = [f"Hi {first},", ""]
    for r in reasons or []:
        h.append("<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" style=\"border-collapse:collapse;margin:0 0 12px\"><tr>"
                 f"<td style=\"background:{WARN_BG};border-left:4px solid {WARN};padding:12px 14px\">"
                 f"<div style=\"font-weight:600;font-size:15px;margin:0 0 4px;color:{INK}\">{_e(r['title'])}</div>"
                 f"<div style=\"margin:0 0 10px\">{_e(r['line'])}</div>" + (_button(r["link"], r["label"]) if r["link"] else "") + "</td></tr></table>")
        tx += [f">> {r['title']}", r["line"]] + ([f"{r['label']}: {r['link']}"] if r["link"] else []) + [""]
    h.append(f"<p style=\"margin:{'18px' if reasons else '0'} 0 12px\">{_e(lead_line)} <a href=\"{_e(desk_link)}\" style=\"color:{GREEN}\">Open the Support desk</a></p>")
    tx += [f"{lead_line} {desk_link}", ""]
    if dash["desk"]:
        h.append(f"<div style=\"font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:{MUTE};margin:0 0 6px\">Across the desk</div>")
        tx.append("Across the desk")
    cells = [("Outstanding", dash["outstanding"]), ("Accepted", dash["accepted"]), ("Not accepted", dash["waiting"]), ("Open over 48 h", dash["over48"])]
    h.append("<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" style=\"border-collapse:collapse;margin:0 0 16px\"><tr>" + "".join(
        f"<td width=\"25%\" style=\"border:1px solid {RULE_C};padding:10px 12px;vertical-align:top\"><div style=\"font-size:11px;letter-spacing:.06em;"
        f"text-transform:uppercase;color:{MUTE}\">{_e(k)}</div><div style=\"font-size:26px;line-height:1.2;font-weight:600;color:"
        f"{WARN if k in ('Not accepted', 'Open over 48 h') and v else INK}\">{v}</div></td>" for k, v in cells) + "</tr></table>")
    tx.append(" · ".join(f"{k} {v}" for k, v in cells))

    def link_of(r):
        if not r["link"]:
            return ""
        kind, label = r["link"]
        href = C.accept_link(db, r["t"]) if kind == "accept" else C.staff_url(db, r["t"])
        return (href, label) if href else ""  # 1.0.98 fix: never an empty link

    if dash["rows"]:
        h.append(_table(["Ticket", "Subject", "State", "How long", ""], [[C.ref(r["t"]), r["t"].title or "", r["state"], r["how"], link_of(r)] for r in dash["rows"]],
                        flags=[r["flag"] for r in dash["rows"]]))
        tx.append("")
        for r in dash["rows"]:
            lk = link_of(r)
            tx.append(f"{C.ref(r['t'])}  {r['t'].title or ''}  ·  {r['state']}  ·  {r['how']}" + (f"  ·  {lk[1]}: {lk[0]}" if lk else ""))
    link = f"{base}/#/support?tab=me"
    says = P.describe(p)
    says = says[:1].lower() + says[1:]
    h.append(f"<p style=\"margin:18px 0 0;font-size:12px;color:{MUTE}\">Your digest comes {_e(says)}; tickets waiting to be accepted are mailed at once. "
             f"<a href=\"{_e(link)}\" style=\"color:{MUTE}\">My settings</a></p><p style=\"margin:12px 0 0\">CBX-Connect Support desk</p>")
    tx += ["", f"Your digest comes {says}; tickets waiting to be accepted are mailed at once.", f"My settings: {link}", "", "CBX-Connect Support desk"]
    html = f"<div style=\"font-family:'Segoe UI',Arial,sans-serif;font-size:14px;line-height:1.55;color:{INK};max-width:640px\">" + "".join(h) + "</div>"
    return f"[CBX] You have {noun}", html, "\n".join(tx)


def _button(link: str, label: str) -> str:
    return ("<table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" style=\"border-collapse:collapse\"><tr>"
            f"<td style=\"background:{GREEN};border-radius:6px\"><a href=\"{_e(link)}\" style=\"display:inline-block;padding:8px 18px;"
            f"font-family:'Segoe UI',Arial,sans-serif;font-size:14px;font-weight:600;color:#ffffff;text-decoration:none\">{_e(label)}</a></td></tr></table>")


def _table(heads: list[str], rows: list[list], flags: list[bool]) -> str:
    th = "".join(f"<th align=\"left\" style=\"font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:{MUTE};font-weight:500;padding:6px 8px;"
                 f"border-bottom:1px solid {INK}\">{_e(x)}</th>" for x in heads)
    body = []
    for r, flag in zip(rows, flags):
        tds = []
        for c in r:
            if isinstance(c, tuple):
                tds.append(f"<td style=\"padding:7px 8px;border-bottom:1px solid {RULE_C}\"><a href=\"{_e(c[0])}\" style=\"color:{GREEN};font-weight:600\">{_e(c[1])}</a></td>")
            else:
                tds.append(f"<td style=\"padding:7px 8px;border-bottom:1px solid {RULE_C}\">{_e(c)}</td>")
        body.append(f"<tr style=\"{'background:' + WARN_BG if flag else ''}\">" + "".join(tds) + "</tr>")
    return ("<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" style=\"border-collapse:collapse;font-size:13px;margin:0 0 8px\">"
            f"<tr>{th}</tr>" + "".join(body) + "</table>")


@jobs.routine("support-1098-start", 30 * 24 * 3600)
def upgrade_routine() -> None:
    """At first start (the scheduler runs every routine once when it starts): the 1.0.98 repair, once."""
    with session_scope() as db:
        upgrade(db)


@jobs.routine("support-nudge", 900)
def nudge_routine() -> None:
    """Every 15 minutes: the acceptance alerts and the digests that are due."""
    with session_scope() as db:
        run(db)


def counts(db: Session) -> dict:
    """For the staff list: how many wait to be accepted, and how many are open over 48 hours."""
    tickets = open_tickets(db)
    now = utcnow()
    return {"waiting": sum(1 for t in tickets if not t.accepted_at),
            "over48": sum(1 for t in tickets if t.accepted_at and sla.hours_since(t.accepted_at, now) >= OPEN_HOURS)}
