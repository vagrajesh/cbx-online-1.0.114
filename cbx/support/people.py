# 1.0.98: from CBX-Connect 2.10.3 cbx/support/people.py (fitted to the old desk: support people are CBX users)
"""Each support person's own desk settings — "My settings" (1.0.98, from CBX-Connect 2.10: Edwin: "support agents set
their own notification times and days … users must be able to make themselves invisible … maybe a settings table").

A support person is a CBX user: the owner and admins, a support lead (Support set-up → Support leads), anyone with the
Support desk grant, the escalation person, or anyone a ticket was given to. Their row (support_settings) is made the
first time it is needed.

    schedule   daily (at a time) · every N days (at a time) · weekly (on a day, at a time) — Johannesburg time.
               Everything Nudge has for them waits for it, except the acceptance alerts (cbx/support/nudge.py).
    invisible  requesters never see their name, address or signature: every mail and page they see says the
               ticket "has been assigned to the responsible person". Staff and admins still see who it is.

They change them on the Support page (My settings); admins see and change everyone's in a table (Support set-up).
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..audit import log
from ..db import utcnow
from ..models import SupportSettings, SupportTicket, User
from . import sla

SCHEDULES = ("daily", "every", "weekly")
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
DEFAULT_TIME = "07:45"
TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
STATE_KEY = "support.desk"  # {escalate_to: user id}


class Bad(Exception):
    """Wrong input (400)."""


def desk_state(db: Session) -> dict:
    from .. import compat2

    v = compat2.state_get(db, STATE_KEY, {}) or {}
    return v if isinstance(v, dict) else {}


def _save_desk_state(db: Session, v: dict) -> None:
    from .. import compat2

    compat2.state_set(db, STATE_KEY, v)


# ---- the row and its schedule ---------------------------------------------------------------------------------
def defaults() -> dict:
    return {"kind": "daily", "every_days": 3, "weekday": 0, "at": DEFAULT_TIME, "anchor": sla.local(utcnow()).date().isoformat()}


def sched(p) -> dict:
    """The schedule with its defaults filled in (a row made before a field existed keeps working)."""
    s = dict(defaults())
    s.update({k: v for k, v in (getattr(p, "schedule", None) or {}).items() if v not in (None, "")})
    if s["kind"] not in SCHEDULES:
        s["kind"] = "daily"
    return s


def find(db: Session, user_id: Optional[int]) -> Optional[SupportSettings]:
    return db.query(SupportSettings).filter(SupportSettings.user_id == user_id).one_or_none() if user_id else None


def get(db: Session, user: User) -> SupportSettings:
    """The person's row — made with the defaults (once a day at 07:45, visible) the first time it is needed."""
    p = find(db, user.id)
    if p is None:
        p = SupportSettings(user_id=user.id, schedule=defaults(), invisible=False, updated_by="")
        db.add(p)
        db.flush()
    return p


def describe(p) -> str:
    """The schedule in words (mails stay English)."""
    s = sched(p)
    if s["kind"] == "every":
        return f"Every {s['every_days']} days at {s['at']}"
    if s["kind"] == "weekly":
        return f"Once a week, on {DAYS[int(s['weekday']) % 7]} at {s['at']}"
    return f"Once a day at {s['at']}"


def out(db: Session, u: User, p: Optional[SupportSettings] = None) -> dict:
    p = p if p is not None else find(db, u.id)
    s = sched(p)
    return {"user_id": u.id, "name": u.name, "email": u.email, "schedule": s["kind"], "every_days": int(s["every_days"]), "weekday": int(s["weekday"]),
            "at_time": s["at"], "invisible": bool(p.invisible) if p else False, "says": describe(p), "updated_by": (p.updated_by if p else "") or "",
            "digest_sent": sla.shown(p.digest_sent_at) if p else ""}


def save(db: Session, p: SupportSettings, data: dict, by_label: str) -> SupportSettings:
    cur = sched(p)
    kind = str(data.get("schedule", cur["kind"]) or "daily")
    if kind not in SCHEDULES:
        raise Bad("Pick once a day, every few days or once a week.")
    at = str(data.get("at_time", cur["at"]) or DEFAULT_TIME).strip()
    if not TIME_RE.match(at):
        raise Bad("Give the time as HH:MM, e.g. 07:45.")
    try:
        every = int(data.get("every_days", cur["every_days"]) or 3)
        day = int(data.get("weekday", cur["weekday"]) or 0)
    except (TypeError, ValueError):
        raise Bad("Give the days as a number.") from None
    if not 2 <= every <= 14:
        raise Bad("Every 2 to 14 days.")
    if not 0 <= day <= 6:
        raise Bad("Pick a day of the week.")
    before = (cur["kind"], int(cur["every_days"]), int(cur["weekday"]), cur["at"], bool(p.invisible))
    new = dict(cur, kind=kind, every_days=every, weekday=day, at=at)
    if (kind, every) != (cur["kind"], int(cur["every_days"])):
        new["anchor"] = sla.local(utcnow()).date().isoformat()  # "every N days" counts from the day it was set
    p.schedule = new
    if "invisible" in data:
        p.invisible = bool(data.get("invisible"))
    p.updated_by = (by_label or "")[:160]
    after = (kind, every, day, at, bool(p.invisible))
    if before != after:
        u = db.get(User, p.user_id)
        log(db, "support_settings_saved", actor_label=(by_label or "")[:120], target=f"user:{p.user_id}",
            detail=f"{u.email if u else p.user_id}: {describe(p)}; {'invisible' if p.invisible else 'visible'}")
    return p


# ---- who is a support person --------------------------------------------------------------------------------------
def escalation(db: Session) -> Optional[User]:
    """The person told when a ticket isn't accepted in 12 hours (Support set-up; an admin picks them; Edwin picks Sashen)."""
    uid = int(desk_state(db).get("escalate_to") or 0)
    u = db.get(User, uid) if uid else None
    if u is None or not u.is_active or u.is_axl or u.role not in ("owner", "admin"):
        return None
    return u


def set_escalation(db: Session, uid: Optional[int], by: User) -> Optional[User]:
    u = db.get(User, int(uid)) if uid else None
    if uid and (u is None or not u.is_active or u.is_axl or u.role not in ("owner", "admin")):
        raise Bad("Pick an admin — they reassign the ticket.")
    v = desk_state(db)
    v["escalate_to"] = u.id if u else 0
    _save_desk_state(db, v)
    log(db, "support_escalation_set", actor=by, target="support", detail=(u.email if u else "nobody"))
    return u


def is_person(db: Session, user: Optional[User]) -> bool:
    """Has "My settings": the owner and admins, the support leads, the Support desk, the escalation person, anyone given a ticket."""
    if user is None or user.is_axl:
        return False
    from . import core as C

    if C.is_staff(db, user):
        return True
    esc = escalation(db)
    if esc is not None and esc.id == user.id:
        return True
    return bool(db.query(SupportTicket.id).filter(SupportTicket.assigned_to_id == user.id).first())


def everyone(db: Session) -> list[dict]:
    """The admins' table: every support person — one line each, with their settings (the defaults until they change them)."""
    from . import core as C

    ids = {u.id for u in C.lead_people(db)} | C.desk_ids(db) | {u.id for u in C.assigners(db)}
    ids |= {uid for (uid,) in db.query(SupportTicket.assigned_to_id).filter(SupportTicket.assigned_to_id.isnot(None)).distinct().all()}
    ids |= {p.user_id for p in db.query(SupportSettings).all()}
    esc = escalation(db)
    if esc is not None:
        ids.add(esc.id)
    rows = db.query(User).filter(User.id.in_(ids or {0}), User.is_active.is_(True), User.is_axl.is_(False)).all()
    covers = {u.id for u in C.lead_people(db)}
    out_ = []
    for u in rows:
        d = out(db, u)
        d["lead"] = u.id in covers
        d["role"] = u.role
        out_.append(d)
    return sorted(out_, key=lambda r: (r["name"] or r["email"] or "").lower())


# ---- the schedule -----------------------------------------------------------------------------------------
def _at(p) -> tuple[int, int]:
    m = TIME_RE.match(sched(p)["at"]) or TIME_RE.match(DEFAULT_TIME)
    return int(m.group(1)), int(m.group(2))


def _anchor(p, now: datetime) -> date:
    a = sched(p).get("anchor")
    try:
        return date.fromisoformat(str(a)) if a else sla.local(getattr(p, "created_at", None) or now).date()
    except ValueError:
        return sla.local(getattr(p, "created_at", None) or now).date()


def due_slot(p, now: Optional[datetime] = None) -> Optional[datetime]:
    """The latest time (UTC) at or before `now` the person's digest was due — None before their first one."""
    now = sla.aware(now or utcnow())
    z = sla.tz()
    here = now.astimezone(z)
    hh, mm = _at(p)
    s = sched(p)

    def at_day(d: date) -> datetime:
        return datetime.combine(d, time(hh, mm), tzinfo=z)

    if s["kind"] == "weekly":
        slot = at_day(here.date() - timedelta(days=(here.weekday() - (int(s["weekday"]) % 7)) % 7))
        if slot > here:
            slot -= timedelta(days=7)
    elif s["kind"] == "every":
        n = max(2, int(s["every_days"] or 3))
        anchor = _anchor(p, now)
        k = (here.date() - anchor).days // n
        if k < 0:
            return None
        slot = at_day(anchor + timedelta(days=k * n))
        if slot > here:
            if k == 0:
                return None
            slot = at_day(anchor + timedelta(days=(k - 1) * n))
    else:
        slot = at_day(here.date())
        if slot > here:
            slot -= timedelta(days=1)
    return slot.astimezone(timezone.utc)


def digest_due(p, now: Optional[datetime] = None) -> bool:
    slot = due_slot(p, now)
    if slot is None:
        return False
    since = sla.aware(p.digest_checked_at or p.created_at)
    return since is None or slot > since


# ---- invisible ------------------------------------------------------------------------------------------
def is_invisible(db: Session, user_id: Optional[int]) -> bool:
    p = find(db, user_id)
    return bool(p and p.invisible)


def hidden_people(db: Session, t: SupportTicket) -> list[tuple[str, str]]:
    """The invisible people on this ticket — every lead it has had (an earlier one too) — as (name, email)."""
    ids = [i for i in list(t.lead_ids or []) + ([t.assigned_to_id] if t.assigned_to_id else []) if isinstance(i, int)]
    out_ = []
    for uid in dict.fromkeys(ids):
        if not is_invisible(db, uid):
            continue
        u = db.get(User, uid)
        if u is not None:
            out_.append((u.name or "", (u.email or "").lower()))
    return out_


RESPONSIBLE = "the responsible person"


SIGN = re.compile(r"^[ \t]*(?:(?:kind(?:est)?|best|warm)?\s*regards|br|thanks(?: a lot)?|thank you(?: very much)?|many thanks|cheers|sincerely|yours[^\n]*"
                  r"|sent from my [^\n]*)\s*[,.!]*\s*$", re.I | re.M)
# 2.10.0: name parts that are everyday words ("Given", "Gift", "Lucky", "Precious"): replaced only where they are plainly a
# name — at a greeting, a sign-off, a line of their own, or with the surname (the relay said "you were the responsible person")
COMMON = frozenset("""given gift lucky precious blessing patience hope faith grace joy charity mercy prince princess king queen rose lily daisy may
june april august bill will mark pat sunny sunday innocent prudence justice wisdom goodness beauty happiness pretty brilliant clever bright victory
lovely honest major rich frank earnest constance felicity amber ruby pearl crystal dawn summer winter sky river stone brown green white black young
long little hill wood ford field rock hunter cook baker miller walker price bond banks best swift strong love day chase penny ray art don sue""".split())
GREET_WORDS = r"(?:hi|hello|dear|hey|morning|afternoon|good (?:day|morning|afternoon|evening))"
SIGN_WORDS = r"(?:(?:kind(?:est)?|best|warm)?\s*regards|thanks|thank you|many thanks|cheers|sincerely|best)"


def unsigned(text: str) -> str:
    """The words above the sign-off: the name, title and numbers under it are dropped."""
    from . import mailtext as X

    m = X.signoff(text or "", SIGN)  # 2.10.0: a "Thanks." above the question no longer empties the relay
    return (text[: m.start()] if m else (text or "")).rstrip()


def _common(part: str, text: str) -> bool:
    """An everyday word: on the list, or written in lower case as a plain word in this very text."""
    low = part.lower()
    return low in COMMON or re.search(r"(?<![A-Za-z])" + re.escape(low) + r"(?![A-Za-z])", text) is not None


def _signature_cut(text: str, people: list[tuple[str, str]]) -> str:
    """2.10.0: an invisible lead's signature with no sign-off above it (Outlook's own): the line that is their name, and
    everything under it (title, numbers), goes."""
    forms = []
    for name, _ in people:
        full = re.sub(r"\s+", " ", (name or "").strip())
        if not full:
            continue
        forms.append(re.escape(full))
        first = full.split(" ")[0]
        if len(first) >= 3 and " " in full and not _common(first, text):
            forms.append(re.escape(first))
    if not forms:
        return text
    from . import mailtext as X

    rx = re.compile(rf"^[ \t]*(?:{SIGN_WORDS}[ \t]*[,.!-]*[ \t]*)?(?:{'|'.join(sorted(set(forms), key=len, reverse=True))})[ \t]*(?:$|[|,(/–—-])", re.I | re.M)
    for m in rx.finditer(text):
        end = text.find("\n", m.end())
        rest = text[m.end(): end if end >= 0 else len(text)].strip(" \t|,(/–—-")
        if X._goes_on(rest) or (rest[:1].islower() and len(rest.split()) >= 3):  # "Zanele, could you …": the message, not a signature
            continue
        if text[: m.start()].strip():  # never the whole message
            return text[: m.start()].rstrip()
    return text


def scrub(text: str, people: list[tuple[str, str]], *, sign: bool = True) -> str:
    """Their names and addresses out of the text — "the responsible person" in their place; no sign-off (sign=False:
    only the names, for a visible lead's words that mention an invisible earlier one)."""
    if not text or not people:
        return text or ""
    t = unsigned(text) if sign else text
    if sign:
        t = _signature_cut(t, people)
    for name, email in people:
        bits = [email] if email else []
        local = email.split("@")[0] if email else ""
        parts = []
        if local and re.search(r"[^A-Za-z]", local):  # 2.10.0: "sipho.dlamini" — a single word ("given@…") is a name part below
            bits.append(local)
        elif local:
            parts.append(local)
        full = re.sub(r"\s+", " ", (name or "").strip())
        if full:
            bits.append(full)
            ws = [w for w in re.split(r"[\s,]+", full) if w]
            if len(ws) >= 2:
                bits.append(f"{ws[-1]}, {' '.join(ws[:-1])}")  # "Mokoena, Given"
            parts += [w for w in ws if len(w) >= 3]
        for b in sorted(set(bits), key=len, reverse=True):
            t = re.sub(r"(?<![\w@.])" + re.escape(b) + r"(?![\w@])", RESPONSIBLE, t, flags=re.I)
        for w in sorted(set(parts), key=len, reverse=True):
            if not _common(w, t):
                t = re.sub(r"(?<![\w@.])" + re.escape(w) + r"(?![\w@])", RESPONSIBLE, t, flags=re.I)
                continue
            # 2.10.0: an everyday word, capitalised as a name: only at a greeting, a sign-off or on a line of its own
            cap = "(?:" + re.escape(w[:1].upper() + w[1:].lower()) + "|" + re.escape(w.upper()) + ")"
            t = re.sub(rf"(?im)^([ \t]*{GREET_WORDS}[ \t,]+)" + cap.replace("(?:", "(?-i:", 1) + r"\b", rf"\1{RESPONSIBLE}", t)
            t = re.sub(rf"(?im)^([ \t]*{SIGN_WORDS}[ \t]*[,.!-]*[ \t]+)" + cap.replace("(?:", "(?-i:", 1) + r"\b", rf"\1{RESPONSIBLE}", t)
            t = re.sub(r"(?m)^([ \t]*)" + cap + r"([ \t]*[,.!]?[ \t]*)$", rf"\1{RESPONSIBLE}\2", t)
    t = re.sub(rf"(?:{RESPONSIBLE}\s*){{2,}}", RESPONSIBLE + " ", t)  # "Sipho Dlamini" → one, not two
    lines = t.rstrip().split("\n")
    while sign and lines and lines[-1].strip().strip(",.").lower() in ("", RESPONSIBLE):  # a name alone on the last line
        lines.pop()
    return "\n".join(lines).strip()
