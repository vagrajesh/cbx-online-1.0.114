"""1.0.109 (round 2, 23 Oct): who should take a ticket — suggestions that learn, and AXL's auto-assign.

Up to three people for a ticket, each with a one-line why ("handled 7 SAP tickets (3 like this one), 2 open now"). A plain
weighted score — deterministic and explainable, no model:

    +2.0  per ticket of this system they finished (at most 10)          the past year, never removed or "not relevant"
    +1.5  per one of those in the same problem area (at most 10)
    +2.5  per finished ticket like this one (2+ marking words in common, cbx/support/learn.words_of; at most 10)
    +1.0  per finished ticket from the same company or department (at most 5)
    +3.0  they are set for the system (Support set-up → Who works each system)
    −1.5  per ticket open with them now
    learnt from the picks (support_picks, the last 180 days, this system):
    +1.5  each time a person picked them (at most 5)
    −3.0  each time a person passed them over saying "wrong area", or undid AXL's assignment to them (at most 3 times)
    −2.0  "too busy" in the last 7 days · −1.0 "other" · on leave: left out for 14 days (or while prefs.away_until is ahead)

A person who assigns someone else than the first suggestion is asked once, optionally, "Why not <name>?" (why_not).

Auto-assign (Support set-up, off by default, per system): when the ticket is ready and the first suggestion is clearly
ahead — at least `margin` points over the second and at least `min_history` finished tickets of that system — AXL assigns it
("Assigned by AXL: …"); the person gets the usual mail; an admin can undo it in one click (the undo is learnt, as above).
"How AXL picks": how often the first suggestion was the one picked over the last 30 days.

1.0.111 (round 3, Edwin: "I don't do Chase, it should not recommend me"): the candidates are only the people set for the
ticket's system (anyone who can work tickets: support leads and the Support desk grant); nobody set → the desk people who
finished tickets of that system, never an owner or admin; no system yet → nobody. "Done" counts only tickets a person was
assigned AND closed themselves (an owner closing someone's ticket counts for nobody). Auto-assign only picks a set person.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from ..audit import log
from ..db import utcnow
from ..models import SupportLead, SupportSystem, SupportTicket, User
from ..models.support_flow import SupportPick

KEY = "support.autoassign"
DEFAULTS = {"margin": 3.0, "min_history": 5}
REASONS = {"wrong_area": "Wrong area", "too_busy": "Too busy", "on_leave": "On leave", "other": "Other"}
GENERIC = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "hotmail.co.za", "yahoo.com", "yahoo.co.za", "yahoo.co.uk", "ymail.com",
           "icloud.com", "me.com", "live.com", "live.co.za", "msn.com", "aol.com", "protonmail.com", "proton.me", "gmx.com", "gmx.net", "zoho.com",
           "mail.com",
           # 1.0.111 (review): South Africa's own free mail
           "mweb.co.za", "telkomsa.net", "vodamail.co.za", "webmail.co.za", "iafrica.com", "absamail.co.za", "lantic.net", "cybersmart.co.za",
           "afrihost.co.za", "polka.co.za", "xsinet.co.za"}


# ---- settings ------------------------------------------------------------------------------------------------------
def settings_of(db: Session) -> dict:
    from .. import state

    try:
        v = json.loads(state.get(db, KEY) or "{}")
    except ValueError:
        v = {}
    v = v if isinstance(v, dict) else {}
    out = {"margin": DEFAULTS["margin"], "min_history": DEFAULTS["min_history"], "auto": {}}
    try:
        out["margin"] = min(50.0, max(0.5, float(v.get("margin", DEFAULTS["margin"]))))
    except (TypeError, ValueError):
        pass
    try:
        out["min_history"] = min(100, max(1, int(v.get("min_history", DEFAULTS["min_history"]))))
    except (TypeError, ValueError):
        pass
    if isinstance(v.get("auto"), dict):
        out["auto"] = {str(k): bool(x) for k, x in v["auto"].items()}
    return out


def save_settings(db: Session, body: dict, by: User) -> dict:
    from .. import state

    cur = settings_of(db)
    if body.get("margin") is not None:
        try:
            m = float(body["margin"])
        except (TypeError, ValueError):
            raise ValueError("The margin is a number of points (0.5 to 50).")
        if not 0.5 <= m <= 50:
            raise ValueError("The margin is a number of points (0.5 to 50).")
        cur["margin"] = m
    if body.get("min_history") is not None:
        try:
            n = int(body["min_history"])
        except (TypeError, ValueError):
            raise ValueError("The minimum history is a whole number of tickets (1 to 100).")
        if not 1 <= n <= 100:
            raise ValueError("The minimum history is a whole number of tickets (1 to 100).")
        cur["min_history"] = n
    if isinstance(body.get("systems"), list):
        ids = {s.id for s in db.query(SupportSystem).all()}
        for row in body["systems"]:
            try:
                sid = int((row or {}).get("id"))
            except (TypeError, ValueError):
                continue
            if sid in ids and "auto" in row:
                cur["auto"][str(sid)] = bool(row["auto"])
    state.set(db, KEY, json.dumps(cur))
    on = [s.name for s in db.query(SupportSystem).all() if cur["auto"].get(str(s.id))]
    log(db, "support_autoassign_settings", actor=by, target="support",
        detail=f"auto-assign: {', '.join(on) or 'off'}; margin {cur['margin']:g}; at least {cur['min_history']} finished tickets")
    return settings_out(db)


def settings_out(db: Session) -> dict:
    from . import core as C

    s = settings_of(db)
    return {"systems": [{"id": x.id, "name": x.name, "auto": bool(s["auto"].get(str(x.id)))} for x in C.systems(db, active_only=False)],
            "margin": s["margin"], "min_history": s["min_history"], "stats": stats(db)}


# ---- the score -----------------------------------------------------------------------------------------------------
def _words(t: SupportTicket) -> set[str]:
    from . import learn as L

    return set(L.words_of(f"{t.title or ''} {t.outcome or ''} {(t.summary or '').split(chr(10))[0]}"))


def _domain(email: str) -> str:
    d = (email or "").rsplit("@", 1)[-1].lower() if "@" in (email or "") else ""
    return "" if d in GENERIC else d


def _away(u: User, today: date) -> bool:
    v = (u.prefs or {}).get("away_until") if isinstance(u.prefs, dict) else None
    try:
        return bool(v) and date.fromisoformat(str(v)[:10]) >= today
    except ValueError:
        return False


def _finished(r: SupportTicket, uid: int) -> bool:
    """1.0.111: a ticket this person finished themselves — it was theirs and they closed it as done (never removed or "not
    relevant"). An owner or admin closing someone else's ticket counts for nobody."""
    return r.status == "closed" and (r.closed_as or "") == "" and r.assigned_to_id == uid and r.closed_by_id == uid


def _manager(u: User) -> bool:
    return (u.role or "") in ("owner", "admin")


def history(db: Session, system_id: int, ids: list[int], since=None) -> dict[int, list[SupportTicket]]:
    """1.0.111: the tickets of this system each person finished themselves (the past year), newest first."""
    from . import core as C

    if not system_id or not ids:
        return {}
    since = since or (utcnow() - timedelta(days=365))
    rows = (db.query(SupportTicket).filter(SupportTicket.system_id == system_id, SupportTicket.assigned_to_id.in_(ids), SupportTicket.status == "closed",
                                           SupportTicket.created_at >= since).filter(C.live()).order_by(SupportTicket.id.desc()).limit(5000).all())
    out: dict[int, list[SupportTicket]] = {}
    for r in rows:
        if _finished(r, r.assigned_to_id):
            out.setdefault(r.assigned_to_id, []).append(r)
    return out


def candidates(db: Session, t: SupportTicket) -> tuple[list[User], bool]:
    """1.0.111 (Edwin: "I don't do Chase, it should not recommend me"): who may be suggested for the ticket, and whether they are
    the people set for its system. Set people → only them. Nobody set → the desk people (never an owner or admin) who finished
    tickets of that system themselves, and nobody else. No system yet → nobody."""
    from . import core as C

    if not t.system_id:
        return [], False
    set_ = [u for u in C.covering(db, t.system_id) if u.is_active and not u.is_axl]
    if set_:
        return set_, True
    pool = [u for u in C.work_people(db) if not _manager(u)]
    done = history(db, t.system_id, [u.id for u in pool])
    return [u for u in pool if done.get(u.id)], False


def score(db: Session, t: SupportTicket) -> list[dict]:
    """Every candidate with its score and facts, best first."""
    from . import core as C
    from . import sla

    now = utcnow()
    today = sla.local(now).date()
    cands, is_set = candidates(db, t)
    cands = [u for u in cands if u.id != t.assigned_to_id]
    if not cands:
        return []
    ids = [u.id for u in cands]
    year = now - timedelta(days=365)
    rows = (db.query(SupportTicket).filter(SupportTicket.assigned_to_id.in_(ids), SupportTicket.id != t.id, SupportTicket.created_at >= year)
            .filter(C.live()).order_by(SupportTicket.id.desc()).limit(3000).all())
    covers = set(ids) if is_set else set()
    mine_words, dom, dept = _words(t), _domain(t.requester_email), (t.requester_department or "").strip().lower()
    s = db.get(SupportSystem, t.system_id) if t.system_id else None
    key = s.key if s else ""
    picks = db.query(SupportPick).filter(SupportPick.at >= now - timedelta(days=180)).all()
    out = []
    for u in cands:
        mine = [r for r in rows if r.assigned_to_id == u.id]
        done = [r for r in mine if _finished(r, u.id)]  # 1.0.111: only what they finished themselves
        open_now = sum(1 for r in mine if r.status in ("assigned", "discussing"))
        sys_done = [r for r in done if t.system_id and r.system_id == t.system_id]
        area = sum(1 for r in sys_done if t.problem_key and r.problem_key == t.problem_key)
        like = sum(1 for r in sys_done if len(mine_words & _words(r)) >= 2)
        company = sum(1 for r in done if (dom and _domain(r.requester_email) == dom) or (dept and (r.requester_department or "").strip().lower() == dept))
        sc = 2.0 * min(len(sys_done), 10) + 1.5 * min(area, 10) + 2.5 * min(like, 10) + 1.0 * min(company, 5) + (3.0 if u.id in covers else 0.0) - 1.5 * open_now
        picked = wrong = busy = other = 0
        leave_at = None
        for p in picks:
            same = not key or not p.system_key or p.system_key == key
            if p.kind in ("pick", "axl", "auto") and p.picked_id == u.id and same:
                picked += 1
            if p.suggested_id == u.id and p.picked_id != u.id:
                age = (sla.aware(now) - sla.aware(p.at)).days
                if p.kind == "undo" or p.reason == "wrong_area":
                    wrong += 1 if same else 0
                elif p.reason == "too_busy" and age <= 7:
                    busy += 1
                elif p.reason == "on_leave" and age <= 14:
                    leave_at = max(leave_at, sla.aware(p.at)) if leave_at else sla.aware(p.at)
                elif p.reason == "other" and same:
                    other += 1
        # on leave: left out — until someone picks them again
        leave = leave_at is not None and not any(p.picked_id == u.id and sla.aware(p.at) > leave_at for p in picks)
        if leave or _away(u, today):
            continue
        sc += 1.5 * min(picked, 5) - 3.0 * min(wrong, 3) - 2.0 * busy - 1.0 * min(other, 3)
        out.append({"id": u.id, "name": u.name or u.email, "score": round(sc, 2), "history": len(sys_done), "like": like, "area": area,
                    "open": open_now, "covers": u.id in covers, "picked": picked, "wrong": wrong, "system": s.name if s else ""})
    out.sort(key=lambda x: (-x["score"], -x["history"], x["name"].lower(), x["id"]))
    return out


def why(x: dict) -> str:
    """One line: "works Chase · 13 done (4 like this one) · 2 open now" (1.0.111)."""
    bits = []
    if x["covers"]:
        bits.append(f"works {x['system'] or 'this system'}")
    if x["history"]:
        bits.append(f"{x['history']} done" + (f" ({x['like']} like this one)" if x["like"] else ""))
    elif not x["covers"]:
        bits.append("on the support desk")
    bits.append(f"{x['open']} open now")
    return " · ".join(bits)


def suggest(db: Session, t: SupportTicket, n: int = 3) -> list[dict]:
    """Up to `n` people for the ticket, best first: [{id, name, why, score}]."""
    return [{"id": x["id"], "name": x["name"], "why": why(x), "score": x["score"]} for x in score(db, t)[:max(0, n)]]


def note(db: Session, t: SupportTicket, sugg: list) -> str:
    """1.0.111: the one grey line in place of suggestions there are none of."""
    if sugg or t.status == "closed":
        return ""
    if not t.system_id:
        return "AXL suggests once the system is known"
    from . import core as C

    if not C.covering(db, t.system_id):
        s = db.get(SupportSystem, t.system_id)
        name = s.name if s else "this system"
        return f"Nobody has finished a {name} ticket yet — set who works {name} in Support set-up"
    return ""


def out(db: Session, t: SupportTicket, viewer: Optional[User]) -> dict:
    """What the ticket page shows (staff): the suggestions on an open ticket, and AXL's assignment (with Undo for admins)."""
    from . import core as C

    sugg = suggest(db, t) if t.status != "closed" and not C.is_removed(t) else []
    d = {"suggestions": sugg, "suggest_note": note(db, t, sugg) if not C.is_removed(t) else "", "auto_assigned": None}
    a = t.auto or {}
    if a.get("lead_id") and not a.get("undone_at"):
        live = t.assigned_to_id == a["lead_id"] and t.status in ("assigned", "discussing")
        d["auto_assigned"] = {"why": a.get("why", ""), "at": a.get("at"), "can_undo": bool(live and viewer is not None and C.can_assign(viewer))}
    return d


# ---- 1.0.111: who works each system (Support set-up) ---------------------------------------------------------------------
GUESS_MAX = 5


def guess(db: Session, system_id: int) -> list[dict]:
    """"Use AXL's guess": the non-owner/admin people who finished most tickets of the system themselves, best first (nothing saved)."""
    from . import core as C

    pool = [u for u in C.work_people(db) if not _manager(u)]
    done = history(db, system_id, [u.id for u in pool])
    rows = sorted(((u, len(done.get(u.id, []))) for u in pool), key=lambda x: (-x[1], (x[0].name or "").lower()))
    return [{"id": u.id, "name": u.name or u.email, "done": n} for u, n in rows if n][:GUESS_MAX]


def people_out(db: Session) -> dict:
    from . import core as C

    desk = C.desk_ids(db)
    leads = {r.user_id for r in db.query(C.SupportPerson).all()}
    systems = []
    for s in C.systems(db, active_only=False):
        ppl = [{"id": u.id, "name": u.name or u.email, "email": u.email} for u in C.covering(db, s.id)]
        systems.append({"id": s.id, "name": s.name, "active": s.active, "people": ppl, "guess": [] if ppl else guess(db, s.id)})
    return {"systems": systems, "pool": [{"id": u.id, "name": u.name or u.email, "email": u.email, "role": u.role, "lead": u.id in leads, "desk": u.id in desk}
                                         for u in C.work_people(db)]}


def save_people(db: Session, body: dict, by: User) -> dict:
    """Replace the people of each system sent ({systems: [{id, people: [user ids]}]}); audited per changed system."""
    from . import core as C

    pool = {u.id: u for u in C.work_people(db)}
    sys_by_id = {s.id: s for s in db.query(SupportSystem).all()}
    plan = []
    rows = body.get("systems") or []
    if not isinstance(rows, list):
        raise ValueError("Send the systems as a list.")
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("people", []), list):  # 1.0.111 (review): a 400, never a 500
            raise ValueError("Each system is {id, people: [user ids]}.")
        try:
            sid = int(row.get("id"))
        except (TypeError, ValueError):
            raise ValueError("Pick a system.")
        if sid not in sys_by_id:
            raise ValueError("That system isn't there any more.")
        want = []
        for x in (row.get("people") or [])[:50]:
            try:
                uid = int(x)
            except (TypeError, ValueError):
                raise ValueError("Pick people from the list.")
            if uid not in pool:
                u = db.get(User, uid)
                raise ValueError(f"{(u.name if u else 'That person')} can't work tickets — make them a support lead or give them the Support desk on People.")
            if uid not in want:
                want.append(uid)
        plan.append((sys_by_id[sid], want))
    for s, want in plan:
        have = {r.user_id: r for r in db.query(SupportLead).filter(SupportLead.system_id == s.id).all()}
        if set(have) == set(want):
            continue
        for uid, r in have.items():
            if uid not in want:
                db.delete(r)
        for uid in want:
            if uid not in have:
                db.add(SupportLead(system_id=s.id, user_id=uid))
        db.flush()
        log(db, "support_system_people", actor=by, target=f"support:{s.key}",
            detail=f"{s.name}: {', '.join(pool[u].name or pool[u].email for u in want) or 'nobody'}"[:300])
    return people_out(db)


# ---- the picks -----------------------------------------------------------------------------------------------------
def _sys_key(db: Session, t: SupportTicket) -> str:
    s = db.get(SupportSystem, t.system_id) if t.system_id else None
    return s.key if s else ""


def record(db: Session, t: SupportTicket, before: list[dict], picked_id: int, by: Optional[User], *, kind: str = "pick", reason: str = "",
           text: str = "") -> Optional[SupportPick]:
    """A person (or AXL) assigned the ticket; `before`: the suggestions as they were. Nothing kept when there were none."""
    if not before and kind == "pick":
        return None
    reason = reason if reason in REASONS else ""
    p = SupportPick(ticket_id=t.id, kind=kind, system_key=_sys_key(db, t), area=t.problem_key or "", suggested=[{"id": x["id"], "score": x["score"]} for x in before][:3],
                    suggested_id=before[0]["id"] if before else None, picked_id=picked_id, reason=reason, text=(text or "").strip()[:300],
                    by_id=by.id if by else None, by_name=((by.name or by.email) if by else "AXL")[:160])
    db.add(p)
    db.flush()
    return p


def ask_why(db: Session, before: list[dict], picked_id: int, reason: str = "") -> Optional[dict]:
    if before and before[0]["id"] != picked_id and reason not in REASONS:
        return {"id": before[0]["id"], "name": before[0]["name"]}
    return None


def why_not(db: Session, t: SupportTicket, reason: str, text: str, by: User) -> SupportPick:
    """The admin's optional answer to "Why not <name>?" — on their latest pick on this ticket."""
    from . import core as C

    if reason not in REASONS:
        raise ValueError("Pick a reason: wrong area, too busy, on leave, or other.")
    p = (db.query(SupportPick).filter(SupportPick.ticket_id == t.id, SupportPick.kind.in_(("pick", "axl")), SupportPick.by_id == by.id)
         .order_by(SupportPick.id.desc()).first())
    if p is None:
        raise ValueError("There is no pick on this ticket to explain.")
    p.reason, p.text = reason, (text or "").strip()[:300]
    sug = db.get(User, p.suggested_id) if p.suggested_id else None
    log(db, "support_pick_reason", actor=by, target=f"ticket:{C.ref(t)}", detail=f"not {sug.name if sug else '-'}: {REASONS[reason]}{(' — ' + p.text) if p.text else ''}"[:300])
    return p


def learn(db: Session, t: SupportTicket, picked_id: Optional[int], suggested_id: Optional[int], reason: str, by: Optional[User], text: str = "") -> SupportPick:
    """From "Ask AXL" (a confirmed reassign, or a person telling AXL it was wrongly assigned): kept like a pick. `reason` may be one of
    REASONS or free words (kept as text; a free reason counts as "wrong area" when the person said it was wrongly assigned)."""
    r = reason if reason in REASONS else ("wrong_area" if "wrong" in (reason or "").lower() else "other" if suggested_id and suggested_id != picked_id else "")
    p = SupportPick(ticket_id=t.id, kind="axl", system_key=_sys_key(db, t), area=t.problem_key or "", suggested=[{"id": suggested_id, "score": 0}] if suggested_id else [],
                    suggested_id=suggested_id, picked_id=picked_id, reason=r, text=((text or "") or ("" if reason in REASONS else reason or "")).strip()[:300],
                    by_id=by.id if by else None, by_name=((by.name or by.email) if by else "")[:160])
    db.add(p)
    db.flush()
    return p


# ---- AXL assigns ---------------------------------------------------------------------------------------------------
def maybe_auto(db: Session, t: SupportTicket) -> Optional[User]:
    """The ticket is ready: when auto-assign is on for its system and the first suggestion is clearly ahead, AXL assigns it."""
    from . import core as C

    if t.status != "ready" or t.assigned_to_id or not t.system_id or C.is_removed(t):
        return None
    s = settings_of(db)
    if not s["auto"].get(str(t.system_id)):
        return None
    ranked = score(db, t)
    if not ranked:
        return None
    top, second = ranked[0], (ranked[1] if len(ranked) > 1 else None)
    gap = top["score"] - (second["score"] if second else 0.0)
    if not top["covers"]:  # 1.0.111: only ever someone set for the system — never from the fallback
        return None
    if top["history"] < s["min_history"] or gap < s["margin"]:
        return None
    lead = db.get(User, top["id"])
    if lead is None or not C.is_lead(db, lead):
        return None
    line = why(top) + (f"; {gap:g} points ahead of {second['name']}" if second else "")
    C.assign(db, t, lead, None, auto=line)
    t.auto = {"lead_id": lead.id, "why": line, "at": utcnow().isoformat()}
    record(db, t, [{"id": x["id"], "name": x["name"], "score": x["score"]} for x in ranked[:3]], lead.id, None, kind="auto")
    return lead


def undo_auto(db: Session, t: SupportTicket, by: User) -> None:
    """An admin undoes AXL's assignment: the ticket is free again (ready), the person loses it, and the desk learns."""
    from . import core as C
    from . import own as OWN

    if not C.can_assign(by):
        raise PermissionError("Only the owner or an admin undoes AXL's assignment.")
    a = dict(t.auto or {})
    if not a.get("lead_id") or a.get("undone_at"):
        raise ValueError("AXL didn't assign this ticket (or it was undone already).")
    if t.assigned_to_id != a["lead_id"] or t.status not in ("assigned", "discussing"):
        raise ValueError("It has moved on since AXL assigned it — assign it as usual.")
    lead = db.get(User, a["lead_id"])
    OWN.drop_todo(db, t)
    t.assigned_to_id, t.assigned_by_id, t.assigned_at = None, None, None
    t.accept_nonce, t.accept_hash, t.accepted_at, t.accepted_by = "", "", None, ""
    t.status = "ready"
    a.update(undone_by=by.name or by.email or "", undone_at=utcnow().isoformat())
    t.auto = a
    db.add(SupportPick(ticket_id=t.id, kind="undo", system_key=_sys_key(db, t), area=t.problem_key or "", suggested=[{"id": a["lead_id"], "score": 0}],
                       suggested_id=a["lead_id"], picked_id=None, reason="wrong_area", by_id=by.id, by_name=(by.name or by.email or "")[:160]))
    C.add_message(db, t, "note", f"{by.name} undid AXL's assignment to {lead.name if lead else 'the lead'}. It is free to take again.", author=by)
    if lead is not None:
        C.tell(db, lead, f"{C.ref(t)} is no longer yours", f"{by.name} took back {C.ref(t)} — AXL had assigned it to you. Nothing to do.", C._link(t, True))
    log(db, "support_autoassign_undone", actor=by, target=f"ticket:{C.ref(t)}", detail=f"was {lead.name if lead else a['lead_id']}")


# ---- "How AXL picks" ---------------------------------------------------------------------------------------------
def stats(db: Session, days: int = 30) -> dict:
    since = utcnow() - timedelta(days=days)
    rows = db.query(SupportPick).filter(SupportPick.at >= since).all()
    judged = [p for p in rows if p.kind in ("pick", "axl") and p.suggested_id]
    top = sum(1 for p in judged if p.picked_id == p.suggested_id)
    top3 = sum(1 for p in judged if p.picked_id in {x.get("id") for x in (p.suggested or [])})
    auto = sum(1 for p in rows if p.kind == "auto")
    undone = sum(1 for p in rows if p.kind == "undo")
    rate = round(100 * top / len(judged)) if judged else None
    lines = []
    if judged:
        lines.append(f"AXL's first suggestion was the one picked {top} of {len(judged)} times ({rate}%); one of its three {top3} times.")
    else:
        lines.append(f"No tickets assigned from AXL's suggestions in the last {days} days yet.")
    if auto:
        lines.append(f"AXL assigned {auto} ticket{'s' if auto != 1 else ''} by itself; {undone} undone.")
    reasons = {}
    for p in judged:
        if p.reason:
            reasons[p.reason] = reasons.get(p.reason, 0) + 1
    if reasons:
        lines.append("Why not the first: " + ", ".join(f"{REASONS[k].lower()} {v}" for k, v in sorted(reasons.items(), key=lambda x: -x[1])) + ".")
    return {"days": days, "suggested": len(judged), "top": top, "top3": top3, "rate": rate, "auto": auto, "undone": undone, "lines": lines}
