# 1.0.98: from CBX-Connect 2.10.3 cbx/support/learn.py (the old desk's tickets: leads are CBX users, systems by key)
"""The desk learns (1.0.98, from CBX-Connect 2.10: Edwin: "If the assigned person asks for more info the AI missed … the AI
must learn why and ask for it the next time in that situation, and grow").

    the lead mails back asking the requester for something extra (core.lead_by_mail, the relay path)
      → a learner (support_learn, the quick model, on one of the readers) works out what the desk missed and why, and
        words the question for next time; Ada (support_lead) reviews it — keep, reword, or drop
      → a question lesson: the situation (system, kind of problem, words that mark it), the question, an example ticket, who/when
    a new ticket that matches a lesson is asked its question before it is summed up for assignment (core.triage)

2.10.3, where tickets go: when a person moves a ticket to another system, or reassigns it to another lead within 7 days of
the first assignment, the desk keeps a route lesson: tickets marked by these words (or this SAP transaction) went to this
system / this lead. On a new ticket that matches (2 marking words or the same transaction) the readers get it as a hint, it
decides the system when they can't tell, and it pre-selects the lead in the Assign box — a person still clicks; nothing is
assigned by itself. A lesson people go against 3 times switches itself off; an admin's "off" sticks.

Without the AI box the lead's question itself is the lesson (rules only), so it still works. Admins see every lesson
(Support → What the desk has learned): on/off, edit, delete, how often used. A lesson's question goes to requesters by
itself, so it is cleaned first: no links, addresses or phone numbers, no names, nothing that asks for a password or code.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Optional

from sqlalchemy.orm import Session

from ..audit import log
from ..db import session_scope, utcnow
from ..models import SupportLesson, SupportMessage, SupportTicket, User
from . import mailtext as X
from . import team as TM

logger = logging.getLogger("cbx.support.learn")
MAX_WORDS = 8
QUESTION = re.compile(r"[^.?!\n]{6,300}\?")
REQUEST = re.compile(r"(?i)\b(?:(?:please|kindly)\s+(?:send|share|provide|attach|confirm|advise|tell|give|let (?:me|us) know)|(?:can|could|would) "
                     r"(?:you|someone)|(?:we|i)(?:'ll| will)? need)\b[^.?!\n]{4,300}[.?!]?")
STOP = set("""about above after again against also another anyone anything around asked because been before being below between both cannot could
did does doing done down during each every from further have having here hers into just more most much need needs needed only other ours over please
same should some such than that their them then there these they this those through under until very want wants were what when where which while
whom with would your yours kindly assist help support issue issues problem problems request requested urgent urgently thanks thank regards hello dear
morning afternoon today tomorrow yesterday still since site sites working work works broken team someone something week weekly""".split())
LINKY = re.compile(r"https?://\S+|www\.\S+|\S+@\S+|\+?\d[\d ()-]{7,}\d"
                   # a bare link too — any word.tld or word.tld/path (Outlook makes "cbx-support.co/verify" clickable)
                   r"|(?<![\w.-])[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.[a-z]{2,24}(?![\w-])(?:/\S*)?", re.I)
# 2.10.0: wider than the old NEVER — passcodes, sign-in details, banking and ID details
NEVER = re.compile(r"\b(pass ?-?words?|passcodes?|pwd|pins?\b|otps?\b|one[- ]time|credentials?|log ?-?ins?\b|logons?\b|user ?-?names?\b|sign[- ]?in (?:details|name)"
                   r"|bank(?:ing)? (?:account|details)|account (?:number|no)\b|acc(?:ount)? ?no\b|iban\b|swift\b|card (?:number|no)\b|cvv|"
                   r"id (?:number|no)\b|identity number|passport number|verification code|security code)", re.I)
MAX_ACTIVE = 200  # lessons the desk asks; past this the oldest unused one is turned off when a new one is learned (questions and routes apart)
PAGE = 50  # lessons per page on the admins' list


def words_of(text: str) -> list[str]:
    """The words that mark a situation: plain words of four letters or more, not the everyday ones, first seen first."""
    out: list[str] = []
    for w in re.findall(r"[A-Za-z][A-Za-z'-]{3,}", text or ""):
        w = w.lower().strip("'-")
        if len(w) >= 4 and w not in STOP and w not in out:
            out.append(w)
    return out


def asked_for(text: str) -> str:
    """What the lead asked the requester for — their question sentences (or "please send …" lines); '' when they only told."""
    core = X.core_words(text or "")
    hits = [m.group(0).strip() for m in QUESTION.finditer(core)]
    if not hits:
        hits = [m.group(0).strip() for m in REQUEST.finditer(core)]
    return " ".join(hits[:2])[:400]


def unsafe(q: str) -> bool:
    """What the desk never asks a requester, in a lesson or in a question a model wrote: a link (a bare one too), an address
    or a number to call, a password, code, sign-in, bank, card or ID detail."""
    return bool(LINKY.search(q or "") or NEVER.search(q or "") or "[removed" in (q or "") or "[link" in (q or "") or X.had_secret(q or ""))


def clean_question(q: str) -> str:
    """A lesson's question goes to requesters by itself: one plain question, no links, addresses or numbers to call,
    nothing asking for a secret. '' when it can't be made safe."""
    q = re.sub(r"\s+", " ", X.redact(q or "")).strip().strip("-•* ")
    if not q or unsafe(q):
        return ""
    q = q[:300].rstrip(" .")
    return q if q.endswith("?") else q + "?"


# ---- the old desk's ticket, in the words the lessons use ----------------------------------------------------------
def sys_key(db: Session, t) -> str:
    from ..models import SupportSystem

    sid = getattr(t, "system_id", None)
    s = db.get(SupportSystem, sid) if sid else None
    return s.key if s else ""


def sys_name(db: Session, key: str) -> str:
    from ..models import SupportSystem

    s = db.query(SupportSystem).filter(SupportSystem.key == key).first() if key else None
    return s.name if s else (key or "")


def _lead(db: Session, t) -> Optional[User]:
    uid = getattr(t, "assigned_to_id", None)
    return db.get(User, uid) if uid else None


def _names(db: Session, t) -> tuple[list[str], list[str], list[str]]:
    """(the lead's names, the requester's names, everyone else's full names: CBX people)."""
    lead_u = _lead(db, t)
    lead = [lead_u.name if lead_u else ""]
    for uid in list(getattr(t, "lead_ids", None) or []):
        u = db.get(User, uid) if isinstance(uid, int) else None
        if u is not None:
            lead.append(u.name or "")
    req = [getattr(t, "requester_name", "") or ""]
    others = [n for (n,) in db.query(User.name).filter(User.is_axl.is_(False)).all()]
    clean = lambda xs: sorted({re.sub(r"\s+", " ", x).strip() for x in xs if x and x.strip()}, key=len, reverse=True)  # noqa: E731
    return clean(lead), clean(req), clean(others)


def unnamed(db: Session, t, q: str) -> str:
    """A lesson is asked of other requesters, so it names no one: the person spoken to ("Omar, could you …") goes, the lead
    who asked becomes "us" ("send Zanele a photo" → "send us a photo"); '' when it still names someone."""
    from ..models import SupportSystem
    from . import people as P

    lead, req, others = _names(db, t)
    parts = lambda names: sorted({w for n in names for w in n.split(" ") if len(w) >= 3}, key=len, reverse=True)  # noqa: E731
    for n in [*req, *parts(req)]:  # the requester, spoken to: first or last
        e = re.escape(n)
        q = re.sub(rf"^\s*(?:(?:hi|hello|dear)\s+)?{e}\s*[,:]\s*", "", q, flags=re.I)
        q = re.sub(rf"\s*,\s*{e}\s*(?=[?.!]*$)", "", q, flags=re.I)
    to = r"\b(send|give|tell|show|mail|email|e-mail|call|ask|let|forward|contact|phone|with|to|for)\s+"
    for n in [*lead, *(w for w in parts(lead) if not P._common(w, q))]:  # the lead who asked: the desk asks it now
        q = re.sub(to + re.escape(n) + r"(?![\w'])", r"\1 us", q, flags=re.I)
    q = q.strip()
    sys_words = {w.lower() for (n,) in db.query(SupportSystem.name).all() for w in re.findall(r"[A-Za-z][A-Za-z'-]+", n or "")}
    for n in [*lead, *req, *(x for x in others if " " in x)]:  # a full name anywhere
        if re.search(r"(?<![\w])" + re.escape(n) + r"(?![\w])", q, re.I):
            return ""
    for w in parts(lead + req):  # the lead's or requester's own name, written as a name
        if w.lower() in sys_words:
            continue
        if P._common(w, q):  # an everyday word: only capitalised, and not as the first word
            cap = w[:1].upper() + w[1:].lower()
            if any(m.start() > 0 for m in re.finditer(r"(?<![\w])" + re.escape(cap) + r"(?![\w])", q)):
                return ""
        elif re.search(r"(?<![\w])" + re.escape(w) + r"(?![\w])", q, re.I):
            return ""
    return (q[:1].upper() + q[1:]) if q else ""


# ---- learning from the lead's question ------------------------------------------------------------------------
def start(db: Session, t: SupportTicket, lead: User, text: str) -> None:
    """Called when the lead's mail is passed on to the requester: if it asks for something, learn from it (a job).
    `text`: the lead's NEW words (never the chain they quoted, where a requester's own words could become the desk's
    question to other requesters)."""
    if not asked_for(text):
        return
    from .. import compat2

    db.flush()
    compat2.enqueue(db, tool="support", kind="support_learn", title=f"Learn from the lead's question on {TM.label(t)}", ref=f"support-learn:{t.id}:{utcnow().timestamp()}",
                    payload={"ticket_id": t.id, "lead_id": lead.id, "text": (text or "")[:4000]}, priority=7, watcher="Ada")


def _learn_job(job_id, payload: dict) -> dict:
    with session_scope() as db:
        t = db.get(SupportTicket, int(payload["ticket_id"]))
        lead = db.get(User, int(payload.get("lead_id") or 0))
        if t is None:
            return {}
        ls = learn(db, t, lead, str(payload.get("text") or ""))
        return {"lesson": ls.id if ls else None}


def _register() -> None:
    from .. import compat2

    compat2.handler("support_learn")(_learn_job)


def learn(db: Session, t: SupportTicket, lead: Optional[User], text: str = "") -> Optional[SupportLesson]:
    raw = asked_for(text)
    if not raw:
        return None
    from . import core as C

    who = (lead.name if lead else "") or "the lead"
    first = who.split(" ")[0]
    situation = f"{t.title or ''} {t.outcome or ''} {t.problem_where or ''}"
    with TM.reading(db, t, task=f"Learning from {first}'s question on {TM.label(t)}") as r:
        got = _model(db, t, raw, r.name)
        if got is None:  # rules: the lead's question itself
            q, words, problem, why, source = clean_question(raw), words_of(t.title or "")[:MAX_WORDS] or words_of(situation)[:MAX_WORDS], \
                (t.title or "")[:255], "The lead asked this after the desk had summed the ticket up.", "rules"
        else:
            if not got.get("missed", True):
                r.say(f"{first}'s message on {C.ref(t)} isn't something the desk should have asked — nothing to learn.")
                return None
            q = clean_question(str(got.get("question") or raw))
            ws = got.get("words")
            ws = [ws] if isinstance(ws, str) else (ws if isinstance(ws, list) else [])
            words = words_of(" ".join(str(x) for x in ws))[:MAX_WORDS] or words_of(t.title or "")[:MAX_WORDS]
            problem, why, source = str(got.get("problem") or t.title or "")[:255], str(got.get("why") or "")[:1000], "ai"
        q = clean_question(unnamed(db, t, q)) if q else ""  # no one's name goes to other requesters
        if not q:
            r.say(f"Left out a question from {C.ref(t)}: it can't be sent to requesters as it is (a link, an address, a secret or a name).")
            return None
        ls = _keep(db, t, q, words, problem, why, source, who)
        cap(db, keep=ls)
        r.say(f"Learned from {C.ref(t)}: next time ask \"{q[:120]}\"")
    TM.say(db, "Ada", f"Learned from {who}'s question on {C.ref(t)}: \"{q[:160]}\"", ref=f"ticket:{t.id}")
    log(db, "support_lesson_learned", actor_label="Support desk", target=f"lesson:{ls.id}", detail=q[:300])
    return ls


def _discussed(db: Session, t: SupportTicket) -> str:
    rows = db.query(SupportMessage).filter(SupportMessage.ticket_id == t.id, SupportMessage.who.in_(("requester", "cexa", "staff"))).order_by(SupportMessage.id).all()
    return "\n".join(f"{'REQUESTER' if m.who == 'requester' else 'SUPPORT'}: {(m.text or '')[:400]}" for m in rows)


def _model(db: Session, t: SupportTicket, asked: str, who: str) -> Optional[dict]:
    """The learner works out what was missed and why; Ada reviews it. None without the AI box."""
    from .. import compat2
    from . import core as C

    data = (f"<ticket>\nSubject: {t.title}\nSystem: {sys_name(db, sys_key(db, t)) or 'not said'}\nWhere: {t.problem_where or 'not said'}\n"
            f"Needed: {t.outcome or 'not said'}\nSummary the desk wrote:\n{(t.summary or '')[:2000]}\nWhat was discussed:\n" + _discussed(db, t)[:3000] +
            f"\nThe lead then asked the requester:\n{asked[:600]}\n</ticket>")
    got = TM._ai(db, "support_learn", f"You are {who}, a reader on the CBX-Connect support desk. {TM.DATA_RULE}\nAfter the desk summed this ticket up and "
                 "assigned it, the support lead had to ask the requester for something more. Work out whether the desk should have asked it before "
                 "assigning (missed: true/false — false for scheduling, a progress note or something only the lead could know), why it was missed, and "
                 "word ONE short plain question the desk can ask next time in tickets like this (general, not about this one ticket; never for "
                 "passwords, codes, bank or ID numbers; no links). problem: the kind of problem in a few words. words: up to 6 words that mark tickets like this.",
                 data, {"missed": "boolean", "why": "string", "question": "string", "problem": "string", "words": "array"}, ["missed", "question"])
    if got is None:
        return None
    if not got.get("missed", True):
        return got
    with compat2.working(db, TM.ADA, f"Reviewing what {who} learned from {TM.label(t)}", f"ticket:{t.id}", does=TM.DOES["Ada"]) as h:
        ada = TM._ai(db, "support_lead", f"You are Ada, the lead of the CBX-Connect support desk's readers. {TM.DATA_RULE}\nA reader drafted a lesson from a "
                     "support lead's follow-up question: the question the desk will ask requesters in tickets like this one from now on. Review it: keep it, "
                     "reword it so any requester understands it, or drop it (keep false) when the desk should not ask it. One plain question; never for "
                     "passwords, codes, bank or ID numbers; no links.", data + "\n<lesson>\n" + str({k: got.get(k) for k in ("why", "question", "problem", "words")})[:2000]
                     + "\n</lesson>", {"keep": "boolean", "question": "string", "problem": "string", "words": "array", "note": "string"}, ["keep"])
        if ada is not None and not ada.get("keep", True):
            h.say(f"Dropped a lesson from {C.ref(t)}: {str(ada.get('note') or '')[:160]}")
    if ada is not None:
        if not ada.get("keep", True):
            return {"missed": False}
        for k in ("question", "problem", "words"):
            if ada.get(k):
                got[k] = ada[k]
    return got


def _example(t) -> str:
    from . import core as C

    return (f"{C.ref(t)} " if getattr(t, "id", None) else "") + f"{getattr(t, 'title', '') or ''}"[:240]


def _keep(db: Session, t: SupportTicket, q: str, words: list, problem: str, why: str, source: str, lead: str) -> SupportLesson:
    """A new lesson — or, when the leads asked much the same before in the same system, that one grows."""
    system = sys_key(db, t)
    for ls in db.query(SupportLesson).filter(SupportLesson.system == system, _questions()).all():
        if SequenceMatcher(None, (ls.question or "").lower(), q.lower()).ratio() >= 0.85:
            ls.seen = (ls.seen or 1) + 1
            ls.words = list(dict.fromkeys(list(ls.words or []) + list(words)))[:MAX_WORDS + 4]
            ls.example_ticket_id, ls.example = t.id, _example(t)
            ls.learned_from = lead[:255]
            return ls
    ls = SupportLesson(kind="question", system=system, problem=problem, words=list(words), question=q, why=why, example_ticket_id=t.id,
                       example=_example(t), learned_from=lead[:255], source=source, active=True, used=0, seen=1)
    db.add(ls)
    db.flush()
    return ls


def cap(db: Session, keep: Optional[SupportLesson] = None, kind: str = "") -> int:
    """At most MAX_ACTIVE lessons are on: past that the oldest unused ones (then the longest unused) are turned off.
    Returns how many were turned off. Questions and routes are counted apart (200 of each)."""
    kind = kind or ((keep.kind or "question") if keep is not None else "question")
    active = db.query(SupportLesson).filter(SupportLesson.active.is_(True), _questions() if kind == "question" else SupportLesson.kind == kind)
    over = active.count() - MAX_ACTIVE
    if over <= 0:
        return 0
    rows = sorted(active.all(), key=lambda ls: ((ls.used or 0) > 0, ts(ls.last_used_at), ls.id))
    n = 0
    for ls in rows:
        if n >= over:
            break
        if keep is not None and ls.id == keep.id:
            continue
        ls.active, ls.edited_by = False, "Support desk (over 200 lessons)"
        n += 1
    if n:
        log(db, "support_lessons_retired", actor_label="Support desk", target="support", detail=f"{n} oldest unused lesson(s) turned off")
    return n


# ---- using what was learned -------------------------------------------------------------------------------
def matching(db: Session, t: SupportTicket, text: str) -> list[SupportLesson]:
    """The question lessons for this ticket: its system (or a lesson for any system), and enough of the lesson's words in
    the ticket — at least two, and at least half of them. The ones the leads asked most and the desk used most recently first."""
    have = set(words_of(f"{getattr(t, 'title', '') or ''} {getattr(t, 'outcome', '') or ''} {text or ''}"))
    mine = sys_key(db, t)
    out = []
    for ls in db.query(SupportLesson).filter(SupportLesson.active.is_(True), _questions()).all():  # a route is not asked
        if ls.system and mine and ls.system != mine:
            continue
        lw = [w for w in (ls.words or []) if isinstance(w, str)]
        hits = sum(1 for w in lw if w in have)
        if len(lw) >= 2 and hits >= max(2, (len(lw) + 1) // 2):
            out.append(ls)
    out.sort(key=lambda ls: ((ls.seen or 1) + (ls.used or 0), ts(ls.last_used_at or ls.created_at), ls.id), reverse=True)
    return out[:4]


def ts(d) -> float:
    from . import sla

    d = sla.aware(d)
    return d.timestamp() if d else 0.0


def used(db: Session, t: SupportTicket, lessons: list[SupportLesson]) -> None:
    if not lessons:
        return
    t.lessons = list(dict.fromkeys(list(t.lessons or []) + [ls.id for ls in lessons]))
    for ls in lessons:
        ls.used = (ls.used or 0) + 1
        ls.last_used_at = utcnow()


def lesson_out(ls: SupportLesson, db: Optional[Session] = None) -> dict:
    from . import sla

    lead = ""
    if (ls.kind or "") == "route" and ls.lead_id and db is not None:  # the admins' list names the lead (the readers get the role)
        u = db.get(User, ls.lead_id)
        lead = f"{u.name} · {lead_role(db, u)}" if u else "a lead no longer on the list"
    return {"lead": lead, "id": ls.id, "kind": ls.kind or "question", "tcode": ls.tcode or "", "overridden": ls.overridden or 0,
            "system": sys_name(db, ls.system) if db is not None else ls.system, "system_key": ls.system or "", "problem": ls.problem,
            "words": ls.words or [], "question": ls.question, "why": ls.why, "example": ls.example, "example_ticket_id": ls.example_ticket_id,
            "learned_from": ls.learned_from, "source": ls.source, "active": bool(ls.active), "used": ls.used or 0, "seen": ls.seen or 1,
            "learned": sla.shown(ls.created_at), "last_used": sla.shown(ls.last_used_at), "edited_by": ls.edited_by}


# ---- 2.10.3: where tickets go -------------------------------------------------------------------------------------
ROUTE_DAYS = 7
OVERRIDES_OFF = 3
OFF_OVERRIDES = f"Support desk (people went against it {OVERRIDES_OFF} times)"  # the only "off" a lesson may come back from


def _questions():
    from sqlalchemy import or_

    return or_(SupportLesson.kind == "question", SupportLesson.kind.is_(None), SupportLesson.kind == "")


def lead_role(db: Session, lead: Optional[User]) -> str:
    """'the SAP lead' — never a name (the readers and the hints say this)."""
    from ..models import SupportLead, SupportSystem

    if lead is None:
        return "a support lead"
    names = [s.name for s in db.query(SupportSystem).join(SupportLead, SupportLead.system_id == SupportSystem.id).filter(SupportLead.user_id == lead.id)
             .order_by(SupportSystem.sort, SupportSystem.id).all()]
    if not names:
        return "a support lead for any system"
    return f"the {' and '.join(names[:3])} lead"


EMAIL = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"\+?\d[\d ()./-]{6,}\d")


def _parts(*xs: str) -> set[str]:
    """The words of names and of e-mail local parts ("zanele.khumalo@…" → zanele, khumalo)."""
    out = set()
    for x in xs:
        for w in re.split(r"[^a-z]+", (x or "").lower().split("@")[0] if "@" in (x or "") else (x or "").lower()):
            if len(w) >= 2:
                out.add(w)
    return out


def route_words(db: Session, t) -> list[str]:
    """The ticket's marking words — the same picking as the questions' — never a name, a number or a secret: e-mail
    addresses and phone numbers go before the words are picked, and no word of the requester's or the lead's name or
    address is kept."""
    lead, req, others = _names(db, t)
    names = {w.lower() for n in [*lead, *req, *others] for w in re.split(r"[\s.'-]+", n) if w}
    lu = _lead(db, t)
    names |= _parts(getattr(t, "requester_name", "") or "", getattr(t, "requester_email", "") or "", lu.name if lu else "", lu.email if lu else "")
    raw = f"{getattr(t, 'title', '') or ''} {getattr(t, 'outcome', '') or ''} {(getattr(t, 'summary', '') or '').split(chr(10))[0]}"
    for addr in EMAIL.findall(raw):
        names |= _parts(addr)
    text = X.redact(PHONE.sub(" ", EMAIL.sub(" ", raw)))
    return [w for w in words_of(text) if w not in names][:MAX_WORDS]


def _tcode(t) -> str:
    sap = getattr(t, "sap", None) or {}
    if sap.get("tcode"):
        return str(sap["tcode"]).upper()[:16]
    for s in getattr(t, "seen", None) or []:
        tc = ((s or {}).get("sap") or {}).get("tcode")
        if tc:
            return str(tc).upper()[:16]
    return ""


def first_assigned(t) -> Optional[datetime]:
    """When the ticket first went to a lead (kept on the ticket as a "first:" mark), or None."""
    from . import sla

    got = _marks(t, "first:")
    try:
        return sla.aware(datetime.fromisoformat(got[0])) if got else None
    except ValueError:
        return None


def routes_for(db: Session, t: SupportTicket, text: str, tcode: str = "") -> list[SupportLesson]:
    """The route lessons for this ticket: at least 2 of the lesson's words in it, or the same SAP transaction."""
    have = set(words_of(f"{t.title or ''} {t.outcome or ''} {text or ''}"))
    tcode = (tcode or "").upper()
    out = []
    for ls in db.query(SupportLesson).filter(SupportLesson.active.is_(True), SupportLesson.kind == "route").all():
        hits = sum(1 for w in (ls.words or []) if isinstance(w, str) and w in have)
        if hits >= 2 or (tcode and ls.tcode and ls.tcode.upper() == tcode):
            out.append((hits + (2 if tcode and ls.tcode and ls.tcode.upper() == tcode else 0), ls))
    out.sort(key=lambda x: (x[0], (x[1].seen or 1) - (x[1].overridden or 0), x[1].id), reverse=True)
    return [ls for _, ls in out[:3]]


def routes_text(db: Session, routes: list[SupportLesson]) -> str:
    """The readers' hint: 'Tickets like this went to SAP / to the SAP lead' — a role, never a name."""
    lines = []
    for ls in routes:
        bits = []
        if ls.system:
            bits.append(sys_name(db, ls.system))
        if ls.lead_id:
            bits.append(lead_role(db, db.get(User, ls.lead_id)))
        if bits:
            lines.append("Tickets like this went to " + " / to ".join(bits) + (f" (marked by: {', '.join((ls.words or [])[:6])})" if ls.words else ""))
    return ("\nWhere tickets like this went before (a person moved them; a hint, not a rule):\n" + "\n".join(lines)) if lines else ""


def route_used(db: Session, t: SupportTicket, routes: list[SupportLesson]) -> None:
    """The route lessons that shaped this ticket (kept with its lessons, so a person going against them is counted)."""
    if not routes:
        return
    t.lessons = list(dict.fromkeys(list(t.lessons or []) + [ls.id for ls in routes]))
    for ls in routes:
        ls.used = (ls.used or 0) + 1
        ls.last_used_at = utcnow()


def suggested(db: Session, t: SupportTicket):
    """(the lead a route lesson names, the lesson) for the Assign box — the first active one whose lead is still a lead."""
    from . import core as C

    ids = [i for i in (t.lessons or []) if isinstance(i, int)]
    if not ids:
        return None, None
    mine = sys_key(db, t)
    for ls in db.query(SupportLesson).filter(SupportLesson.id.in_(ids), SupportLesson.kind == "route", SupportLesson.active.is_(True)).order_by(SupportLesson.id.desc()):
        if ls.lead_id and (not ls.system or not mine or ls.system == mine):
            u = db.get(User, ls.lead_id)
            if u is not None and u.is_active and C.is_lead(db, u):
                return u, ls
    return None, None


def _marks(t, prefix: str) -> list[str]:
    """What the ticket remembers about its route lessons, kept with its lessons as "prefix:value" words: desk:<system> (the
    desk's own system before a person changed it), over:<id> (a person went against that lesson on this ticket — counted
    once), sys:<system> / lead:<id> (a lesson this ticket taught), first:<when> (its first assignment)."""
    return [x[len(prefix):] for x in (getattr(t, "lessons", None) or []) if isinstance(x, str) and x.startswith(prefix)]


def _mark(t, m: str, on: bool = True) -> None:
    cur = list(t.lessons or [])
    t.lessons = (cur + [m]) if on and m not in cur else ([x for x in cur if x != m] if not on else cur)


def _judge(db: Session, t: SupportTicket, lead_id: Optional[int]) -> int:
    """The route lessons that shaped this ticket, judged on where it ended up (its system and lead when it is assigned) —
    not on each click on the way. At most one count per lesson per ticket; a lesson the ticket agrees with again is
    uncounted; "Not sure" (no system) is never against a lesson. The third count switches a lesson off."""
    ids = [i for i in (t.lessons or []) if isinstance(i, int)]
    counted = set(_marks(t, "over:"))
    mine = sys_key(db, t)
    n = 0
    for ls in (db.query(SupportLesson).filter(SupportLesson.id.in_(ids), SupportLesson.kind == "route").all() if ids else []):
        against = bool((ls.system and mine and ls.system != mine) or (ls.lead_id and lead_id != ls.lead_id))
        was = str(ls.id) in counted
        if against and not was and ls.active:
            ls.overridden = (ls.overridden or 0) + 1
            _mark(t, f"over:{ls.id}")
            n += 1
            if ls.overridden >= OVERRIDES_OFF:
                ls.active, ls.edited_by = False, OFF_OVERRIDES
                log(db, "support_lesson_retired", actor_label="Support desk", target=f"lesson:{ls.id}", detail=f"route overridden {ls.overridden} times")
        elif not against and was:  # this ticket went back to where the lesson said: its count goes
            ls.overridden = max(0, (ls.overridden or 0) - 1)
            _mark(t, f"over:{ls.id}", on=False)
            if not ls.active and ls.edited_by == OFF_OVERRIDES and ls.overridden < OVERRIDES_OFF:
                ls.active, ls.edited_by = True, ""
                db.flush()
                cap(db, keep=ls, kind="route")
    return n


def _route(db: Session, t, *, system: str, lead_id: Optional[int], why: str, who: str) -> Optional[SupportLesson]:
    """Keep a route lesson (or grow the one like it: same system and lead, 2 words in common). A lesson that is off stays
    off — unless the 3-override rule switched it off (then a person making the same move again brings it back, within the
    200 cap); an admin's "off" always sticks."""
    words = route_words(db, t)
    if len(words) < 2:
        return None
    tcode = _tcode(t)
    q = db.query(SupportLesson).filter(SupportLesson.kind == "route", SupportLesson.system == (system or ""))
    q = q.filter(SupportLesson.lead_id.is_(None)) if lead_id is None else q.filter(SupportLesson.lead_id == lead_id)
    for ls in q.all():
        if len(set(ls.words or []) & set(words)) >= 2 or (tcode and ls.tcode == tcode):
            if not ls.active and ls.edited_by != OFF_OVERRIDES:
                return None  # an admin (or the cap) turned it off: it is not learned again behind their back
            ls.seen = (ls.seen or 1) + 1
            ls.words = list(dict.fromkeys(list(ls.words or []) + words))[:MAX_WORDS + 4]
            ls.example_ticket_id, ls.example, ls.learned_from = getattr(t, "id", None), _example(t), who[:255]
            ls.tcode = ls.tcode or tcode
            if not ls.active:
                ls.active, ls.overridden, ls.edited_by = True, 0, ""
                db.flush()
                cap(db, keep=ls, kind="route")
            return ls
    seen = []
    from . import vision as V

    if V.seen_system(t):
        seen.append(f"Iris saw {V.seen_system(t)}" + (f" ({tcode})" if tcode else ""))
    ls = SupportLesson(kind="route", system=system or "", lead_id=lead_id, tcode=tcode, words=words, question="", problem=(getattr(t, "title", "") or "")[:255],
                       why=(why + (" " + "; ".join(seen) + "." if seen else ""))[:1000], example_ticket_id=getattr(t, "id", None), example=_example(t),
                       learned_from=who[:255], source="person", active=True, used=0, seen=1)
    db.add(ls)
    db.flush()
    cap(db, keep=ls, kind="route")
    log(db, "support_lesson_learned", actor_label="Support desk", target=f"lesson:{ls.id}", detail=f"route: {system or 'any system'} / lead {lead_id or '-'}: {', '.join(words)}"[:300])
    return ls


def _in_window(t) -> bool:
    from . import sla

    first = first_assigned(t)
    return first is None or (sla.aware(utcnow()) - first) <= timedelta(days=ROUTE_DAYS)


def _settle(db: Session, t: SupportTicket, who: str, lead_id: Optional[int]) -> Optional[SupportLesson]:
    """The ticket is with a lead — its system and lead are what a person decided. Judge the lessons that shaped it, and
    when a person moved it from the desk's system (within 7 days of the first assignment), learn where it went — once
    per ticket and system."""
    _judge(db, t, lead_id)
    desk = _marks(t, "desk:")
    mine = sys_key(db, t)
    if not desk or t.system_set_by != "person" or not mine or mine == desk[0]:
        return None
    if mine in _marks(t, "sys:") or not _in_window(t):
        return None
    ls = _route(db, t, system=mine, lead_id=None, why=f"{who} moved it from {sys_name(db, desk[0]) or 'no system'} to {sys_name(db, mine)}.", who=who)
    if ls is not None:
        _mark(t, f"sys:{mine}")
    return ls


def after_system(db: Session, t: SupportTicket, old: str, new: str, who: str) -> Optional[SupportLesson]:
    """A person changed the ticket's system (keys). The clicks on the way don't teach or count: the desk remembers its own
    system, and what the ticket ends up with is judged and learned when it is assigned (at once when it already is)."""
    if (old or "") == (new or ""):
        return None
    if not _marks(t, "desk:"):
        _mark(t, f"desk:{old or ''}")
    if t.status in ("assigned", "discussing") and t.assigned_to_id:
        return _settle(db, t, who, t.assigned_to_id)
    return None


def after_assign(db: Session, t: SupportTicket, prev_id: Optional[int], lead_id: Optional[int], who: str) -> Optional[SupportLesson]:
    """The ticket went to a lead: the lessons that shaped it are judged on its final system and lead; a system a person set
    is learned; a reassignment (another lead, within 7 days of the first assignment) is kept as a route lesson."""
    if not _marks(t, "first:"):
        _mark(t, f"first:{utcnow().isoformat()}")
    sys_ls = _settle(db, t, who, lead_id)
    if not prev_id or prev_id == lead_id or not lead_id or not _in_window(t):
        return sys_ls
    if str(lead_id) in _marks(t, "lead:"):
        return sys_ls
    ls = _route(db, t, system=sys_key(db, t), lead_id=lead_id, why=f"{who} reassigned it to another lead.", who=who)
    if ls is not None:
        _mark(t, f"lead:{lead_id}")
    return ls


_register()
