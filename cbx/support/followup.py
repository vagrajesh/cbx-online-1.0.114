"""1.0.109 (round 2, 23 Oct): the follow-up timer and the escalation, and the responsible person (owner) of a ticket.

Edwin: when the desk is waiting on the requester (AXL's questions, or a person's question marked "waiting for the
requester") and nothing comes back —

    after N working days (Support set-up, default 2)      one follow-up mail to the requester, through the outbox, once:
                                                          "Hi <name>, just checking in on T-00NN: we still need … Please reply"
    after M more working days (default 2)                 escalate to the ticket's responsible person: a mail to them with
                                                          the summary and what is missing, a note on the ticket, the status
                                                          "Escalated to <name>", and the person it is assigned to is told

The responsible person: the one set on the ticket (a CBX user, or someone not on CBX: a name and an e-mail), else the
system's default, else the desk default (both in Support set-up), else the escalation person (Nudge's, Support set-up).

The clock stops when the requester replies (core.requester_reply), when a person writes to them without marking the
message as waiting, and when the ticket is done, removed or closed as not relevant. Working days skip Saturdays, Sundays and
South Africa's public holidays (the Public Holidays Act: a holiday on a Sunday moves to the Monday), in Johannesburg time.
`run(db, now=…)` takes a stand-in clock (tests); the routine runs it every 15 minutes.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from .. import jobs
from ..audit import log
from ..db import session_scope, utcnow
from ..models import SupportSystem, SupportTicket, User
from . import sla

KEY = "support.followup"
DEFAULTS = {"follow_days": 2, "escalate_days": 2}
DAY_START = time(8, 0)  # a wait that starts on a weekend or a holiday counts from the next working day, 08:00


# ---- South Africa's public holidays --------------------------------------------------------------------------------
def _easter(y: int) -> date:
    """Easter Sunday (the Gregorian computus)."""
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month = (h + m - 7 * n + 114) // 31
    day = (h + m - 7 * n + 114) % 31 + 1
    return date(y, month, day)


FIXED = [((1, 1), "New Year's Day"), ((3, 21), "Human Rights Day"), ((4, 27), "Freedom Day"), ((5, 1), "Workers' Day"),
         ((6, 16), "Youth Day"), ((8, 9), "National Women's Day"), ((9, 24), "Heritage Day"), ((12, 16), "Day of Reconciliation"),
         ((12, 25), "Christmas Day"), ((12, 26), "Day of Goodwill")]


def holidays(year: int) -> dict[date, str]:
    """The public holidays of one year (built in; checked for 2026 and 2027), with the Sunday rule."""
    out: dict[date, str] = {}
    e = _easter(year)
    days = [(date(year, m, d), name) for (m, d), name in FIXED] + [(e - timedelta(days=2), "Good Friday"), (e + timedelta(days=1), "Family Day")]
    for d, name in days:
        out[d] = name
    for d, name in days:
        if d.weekday() == 6:  # a Sunday: the Monday after is the holiday
            nxt = d + timedelta(days=1)
            while nxt in out:
                nxt += timedelta(days=1)
            out[nxt] = f"{name} (observed)"
    return out


def is_working(d: date) -> bool:
    return d.weekday() < 5 and d not in holidays(d.year)


def add_working_days(start: datetime, n: int) -> datetime:
    """`n` working days after `start` (aware or UTC-naive), at the same time of day; a start on a day off counts from the next
    working day at 08:00 (Johannesburg)."""
    loc = sla.local(start)
    if not is_working(loc.date()):
        d = loc.date()
        while not is_working(d):
            d += timedelta(days=1)
        loc = datetime.combine(d, DAY_START, tzinfo=loc.tzinfo)
    d, left = loc.date(), max(0, int(n))
    while left:
        d += timedelta(days=1)
        if is_working(d):
            left -= 1
    return datetime.combine(d, loc.timetz()).astimezone(sla.aware(utcnow()).tzinfo)


# ---- settings (Support set-up, admins) ------------------------------------------------------------------------------
def settings_of(db: Session) -> dict:
    from .. import state

    try:
        v = json.loads(state.get(db, KEY) or "{}")
    except ValueError:
        v = {}
    v = v if isinstance(v, dict) else {}
    out = dict(DEFAULTS)
    for k in DEFAULTS:
        try:
            out[k] = min(20, max(1, int(v.get(k, DEFAULTS[k]))))
        except (TypeError, ValueError):
            pass
    out["desk_owner"] = v.get("desk_owner") if isinstance(v.get("desk_owner"), dict) else None
    out["systems"] = {str(k): x for k, x in (v.get("systems") or {}).items() if isinstance(x, dict)} if isinstance(v.get("systems"), dict) else {}
    return out


def save_settings(db: Session, body: dict, by: User) -> dict:
    from .. import state

    cur = settings_of(db)
    for k in DEFAULTS:
        if k in body and body[k] is not None:
            try:
                n = int(body[k])
            except (TypeError, ValueError):
                raise ValueError("Working days are a whole number from 1 to 20.")
            if not 1 <= n <= 20:
                raise ValueError("Working days are a whole number from 1 to 20.")
            cur[k] = n
    if "desk_owner" in body:
        cur["desk_owner"] = clean_owner(db, body["desk_owner"], by)
    if isinstance(body.get("systems"), list):
        ids = {s.id for s in db.query(SupportSystem).all()}
        sys_ = dict(cur["systems"])
        for row in body["systems"]:
            if not isinstance(row, dict) or "owner" not in row:
                continue
            try:
                sid = int(row.get("id"))
            except (TypeError, ValueError):
                continue
            if sid not in ids:
                continue
            o = clean_owner(db, row.get("owner"), by)
            if o:
                sys_[str(sid)] = o
            else:
                sys_.pop(str(sid), None)
        cur["systems"] = sys_
    state.set(db, KEY, json.dumps(cur))
    log(db, "support_followup_settings", actor=by, target="support",
        detail=f"follow-up after {cur['follow_days']} working day(s), escalate {cur['escalate_days']} later; desk default: {(cur['desk_owner'] or {}).get('name') or 'none'}")
    return settings_out(db)


def settings_out(db: Session) -> dict:
    s = settings_of(db)
    today = sla.local(utcnow()).date()
    hol = [{"date": d.isoformat(), "name": n} for y in (today.year, today.year + 1) for d, n in sorted(holidays(y).items()) if d >= today]
    from . import core as C

    return {"follow_days": s["follow_days"], "escalate_days": s["escalate_days"], "desk_owner": owner_view(db, s["desk_owner"]),
            "systems": [{"id": x.id, "name": x.name, "owner": owner_view(db, s["systems"].get(str(x.id)))} for x in C.systems(db, active_only=False)],
            "holidays": hol[:16]}


# ---- the responsible person ----------------------------------------------------------------------------------------
def clean_owner(db: Session, o, by: Optional[User] = None) -> Optional[dict]:
    """{kind: user, id} | {kind: other, name, email} → what is stored; None / {kind: none} → nobody. ValueError in plain words."""
    from .. import mail

    if not o or not isinstance(o, dict) or o.get("kind") in (None, "", "none"):
        return None
    stamp = {"by": (by.name or by.email or "")[:160] if by else "", "at": utcnow().isoformat()}
    if o.get("kind") == "user":
        try:
            u = db.get(User, int(o.get("id")))
        except (TypeError, ValueError):
            u = None
        if u is None or not u.is_active or u.is_axl:
            raise ValueError("Pick a person on CBX.")
        return {"kind": "user", "id": u.id, "name": u.name or u.email, "email": (u.email or "").lower(), **stamp}
    if o.get("kind") == "other":
        name = " ".join(str(o.get("name") or "").split())[:160]
        email = str(o.get("email") or "").strip().lower()[:255]
        if len(name) < 2:
            raise ValueError("Give the responsible person's name.")
        if not mail.valid(email):
            raise ValueError("Give the responsible person's e-mail address.")
        return {"kind": "other", "name": name, "email": email, **stamp}
    raise ValueError("Pick a person on CBX, or someone not on CBX with a name and an e-mail.")


def owner_view(db: Session, o: Optional[dict], source: str = "") -> Optional[dict]:
    if not o or not isinstance(o, dict) or o.get("kind") not in ("user", "other"):
        return None
    if o["kind"] == "user":
        u = db.get(User, o.get("id")) if o.get("id") else None
        if u is None or not u.is_active:
            return None
        d = {"kind": "user", "id": u.id, "name": u.name or u.email, "email": u.email}
    else:
        d = {"kind": "other", "name": o.get("name", ""), "email": o.get("email", "")}
    if source:
        d["from"] = source
    return d


def owner_of(db: Session, t: SupportTicket) -> Optional[dict]:
    """The ticket's responsible person: its own, else the system's default, else the desk's, else the escalation person."""
    from . import people as P

    own = owner_view(db, t.owner, "ticket")
    if own:
        return own
    s = settings_of(db)
    if t.system_id:
        o = owner_view(db, s["systems"].get(str(t.system_id)), "system")
        if o:
            return o
    o = owner_view(db, s["desk_owner"], "desk")
    if o:
        return o
    esc = P.escalation(db)
    return owner_view(db, {"kind": "user", "id": esc.id}, "escalation") if esc is not None else None


def set_owner(db: Session, t: SupportTicket, o, by: User) -> None:
    from . import core as C

    new = clean_owner(db, o, by)
    old = owner_view(db, t.owner)
    t.owner = new or {}
    words = f"{by.name} set the responsible person: {(old or {}).get('name') or 'the default'} → {(new or {}).get('name') or 'the default'}."
    C.add_message(db, t, "note", words, author=by)
    log(db, "support_owner_set", actor=by, target=f"ticket:{C.ref(t)}", detail=f"{(new or {}).get('name') or 'default'} <{(new or {}).get('email') or '-'}>")


# ---- the wait on the requester --------------------------------------------------------------------------------------
def start(db: Session, t: SupportTicket, kind: str, asked: str = "") -> None:
    """The desk now waits on the requester (kind axl: AXL's questions; person: a question marked waiting). Starts the clock again."""
    now = utcnow()
    t.waiting_since = now
    t.followup = {"kind": kind, "since": now.isoformat(), "asked": (asked or "").strip()[:1500]}


def stop(db: Session, t: SupportTicket, why: str = "") -> None:
    """The wait is over (the requester replied, a person wrote without waiting, the ticket is closed). An escalation ends too."""
    if t.waiting_since is None and not t.followup:
        return
    from . import core as C

    if (t.followup or {}).get("escalated_at") and why:
        C.add_message(db, t, "note", f"No longer escalated — {why}.", name="Support desk")
    t.waiting_since, t.followup = None, {}


def waiting(t: SupportTicket) -> bool:
    return t.waiting_since is not None and t.status != "closed"


def escalated_to(t: SupportTicket) -> str:
    f = t.followup or {}
    return f.get("escalated_to", "") if waiting(t) and f.get("escalated_at") else ""


def _dt(s) -> Optional[datetime]:
    try:
        return sla.aware(datetime.fromisoformat(s)) if s else None
    except (TypeError, ValueError):
        return None


def out(db: Session, t: SupportTicket) -> dict:
    """What the ticket page shows (staff): `owner`, `owner_own`, `followup`."""
    d = {"owner": owner_of(db, t), "owner_own": bool(owner_view(db, t.owner)), "followup": None}
    if not waiting(t):
        return d
    s = settings_of(db)
    f = t.followup or {}
    since = sla.aware(t.waiting_since)
    sent = _dt(f.get("sent_at"))
    state = "escalated" if f.get("escalated_at") else "followed" if sent else "waiting"
    d["followup"] = {"state": state, "kind": f.get("kind", "axl"), "since": since.isoformat() if since else None, "asked": f.get("asked", ""),
                     "next_at": add_working_days(since, s["follow_days"]).isoformat() if since else None, "sent_at": f.get("sent_at"),
                     "escalate_at": add_working_days(sent, s["escalate_days"]).isoformat() if sent else None,
                     "escalated_at": f.get("escalated_at"), "escalated_to": f.get("escalated_to", "")}
    return d


def _missing(t: SupportTicket) -> str:
    f = t.followup or {}
    if f.get("kind") == "axl" and t.missing:
        return "\n".join(f"{i + 1}. {q}" for i, q in enumerate(t.missing))
    return (f.get("asked") or "").strip() or "\n".join(f"{i + 1}. {q}" for i, q in enumerate(t.missing or [])) or "your answer to our last message"


# ---- one run ------------------------------------------------------------------------------------------------------
def run(db: Session, now: Optional[datetime] = None) -> dict:
    """Send the follow-ups and escalations that are due. Each is marked before its mail goes (never twice)."""
    from . import core as C

    now = sla.aware(now or utcnow())
    s = settings_of(db)
    got = {"followed": 0, "escalated": 0, "stopped": 0}
    for t in db.query(SupportTicket).filter(SupportTicket.waiting_since.isnot(None)).order_by(SupportTicket.id).all():
        if t.status == "closed" or C.is_removed(t):
            stop(db, t)
            got["stopped"] += 1
            continue
        f = dict(t.followup or {})
        since = sla.aware(t.waiting_since)
        sent = _dt(f.get("sent_at"))
        if sent is None:
            if now < add_working_days(since, s["follow_days"]):
                continue
            f["sent_at"] = now.isoformat()
            t.followup = f
            db.commit()  # marked first: a failure later never sends it twice
            _follow_up(db, t, s["follow_days"])
            got["followed"] += 1
        elif not f.get("escalated_at"):
            if now < add_working_days(sent, s["escalate_days"]):
                continue
            o = owner_of(db, t)
            f["escalated_at"], f["escalated_to"] = now.isoformat(), (o or {}).get("name", "")
            t.followup = f
            db.commit()
            _escalate(db, t, o, s["follow_days"] + s["escalate_days"])
            got["escalated"] += 1
        db.commit()
    db.commit()
    return got


def _follow_up(db: Session, t: SupportTicket, days: int) -> None:
    from . import core as C

    C.requester_says(db, t, "follow_up", C.values(db, t, missing=_missing(t), days=str(days)))
    C.add_message(db, t, "note", f"Follow-up sent to {t.requester_name or t.requester_email} — no reply in {days} working day{'s' if days != 1 else ''}.",
                  name="Support desk")
    log(db, "support_followup_sent", actor_label="Support desk", target=f"ticket:{C.ref(t)}", detail=f"no reply in {days} working days")


def _escalate(db: Session, t: SupportTicket, o: Optional[dict], days: int) -> None:
    from .. import mailbox
    from . import core as C

    lead = db.get(User, t.assigned_to_id) if t.assigned_to_id else None
    missing = _missing(t)
    if o is None:
        C.add_message(db, t, "note", f"No reply from {t.requester_name or t.requester_email} after a follow-up, and no responsible person is set "
                      "(Support set-up) — the admins were told.", name="Support desk")
        for u in C.assigners(db):
            C.tell(db, u, f"{C.ref(t)}: no reply after a follow-up", f"{t.title}\n\nSet a responsible person on the ticket or in Support set-up.", C._link(t, True))
        log(db, "support_escalated", actor_label="Support desk", target=f"ticket:{C.ref(t)}", detail="no responsible person set: the admins were told")
        return
    vals = C.values(db, t, owner_first=C._first(o["name"]) or "there", owner_name=o["name"], missing=missing, days=str(days),
                    lead=(lead.name if lead else "nobody yet"), requester_name=t.requester_name or t.requester_email)
    u = db.get(User, o["id"]) if o.get("kind") == "user" else None
    if u is not None:
        C.mail_staff(db, u, "escalated", vals, t)
        C.tell(db, u, f"{C.ref(t)} escalated to you", f"{t.title}\n\nNo reply from {vals['requester_name']} after a follow-up.\n\nStill missing:\n{missing[:500]}", C._link(t, True))
    else:
        from . import templates

        subject, body = templates.render(db, "escalated", dict(vals, owner_link=""))
        mailbox.send(db, to=[o["email"]], subject=f"[{C.ref(t)}] {subject}", body=body, kind="support", ref=f"ticket:{C.ref(t)}", check=False,
                     hold="support:escalated", extra={"heading": f"CBX-Connect Support · {C.ref(t)}"}, ticket_id=t.id)
    C.add_message(db, t, "note", f"Escalated to {o['name']} — no reply from {vals['requester_name']} in {days} working days, a follow-up included.",
                  name="Support desk")
    if lead is not None and (o.get("kind") != "user" or o.get("id") != lead.id):
        C.tell(db, lead, f"{C.ref(t)} escalated to {o['name']}", f"No reply from {vals['requester_name']} after a follow-up.", C._link(t, True))
        C.mail_staff(db, lead, "lead_escalated", vals, t)
    log(db, "support_escalated", actor_label="Support desk", target=f"ticket:{C.ref(t)}", detail=f"to {o['name']} <{o.get('email', '')}>"[:300])


@jobs.routine("support-followup", 900)
def followup_routine() -> None:
    """Every 15 minutes: the follow-ups and escalations that are due."""
    with session_scope() as db:
        run(db)
