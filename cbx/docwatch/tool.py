"""Entity Compliance read from its tool folder (1.0.63): the counts on the menu and Home, C-EXA's summaries,
and the expiry reminders — the same rules as the built-in ones, on the tool's own tables, seen through the
tool's own "who sees what" rules.

Reminders: hourly, for each document that is required, dated, not archived and not "doesn't expire", once
per stage — its own warning days, 30, 7 and the day itself (and once more when it has expired) — a message in
CBX-Connect to whoever filed it, the company's responsible person (an account), and the company's people who
are managers or admins; one e-mail digest per person, and one to a responsible address that has no account.
Which stages went is kept in app_state (entity_compliance.reminders), so a renewal starts afresh.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from .. import state
from ..audit import log
from ..db import session_scope
from ..jobs import routine
from ..models import CexaMessage, User
from .copy_in import SLUG, use_tool

SENT_KEY = "entity_compliance.reminders"


def _tool():
    from ..engine import loader

    t = loader.registry().tools.get(SLUG)
    return t if t is not None and all(n in t.data for n in ("companies", "documents")) else None


def rows(db: Session, me: Optional[User], block: str, limit: int = 10000) -> list[dict]:
    """The rows of one of the tool's tables that this person may see (None: everyone's)."""
    from ..api.engine_api import _mine_scope
    from ..engine.store import Store

    t = _tool()
    if t is None or block not in t.data:
        return []
    b = {**t.data[block], "_slug": SLUG, "_name": block}
    mine = _mine_scope(db, b, me, t) if me is not None else None
    return Store(SLUG).rows(block, b.get("columns") or [], limit=limit, mine=mine, tables=t.data)["rows"]


def due_count(db: Session, me: User) -> int:
    live = {c["id"] for c in rows(db, me, "companies") if not c.get("archived")}
    return sum(1 for d in rows(db, me, "documents") if d.get("company") in live and d.get("status") in ("expired", "expiring"))


def companies_count(db: Session, me: User) -> int:
    return sum(1 for c in rows(db, me, "companies") if not c.get("archived"))


def due_docs(db: Session, me: Optional[User] = None) -> list[dict]:
    """Every document inside its warning window or expired (for C-EXA and Home): {title, company, expiry, status}."""
    comps = {c["id"]: c for c in rows(db, me, "companies") if not c.get("archived")}
    out = []
    for d in rows(db, me, "documents"):
        if d.get("company") in comps and d.get("status") in ("expired", "expiring"):
            out.append({"id": d["id"], "title": d.get("title") or (d.get("_labels") or {}).get("doc_type") or "document", "company": comps[d["company"]].get("name") or "",
                        "company_id": d["company"], "expiry": d.get("expiry"), "status": d.get("status"), "days_left": d.get("days_left")})
    return sorted(out, key=lambda x: str(x.get("expiry") or ""))


def counts(db: Session) -> dict:
    docs = rows(db, None, "documents")
    return {"documents": len(docs), "expired": sum(1 for d in docs if d.get("status") == "expired"), "expiring": sum(1 for d in docs if d.get("status") == "expiring"),
            "companies": sum(1 for c in rows(db, None, "companies") if not c.get("archived"))}


# ---- reminders ---------------------------------------------------------------------------------------


def _stages(warn: Optional[int]) -> tuple[int, ...]:
    return tuple(sorted({int(warn or 30), 30, 7, 0}, reverse=True))


def remind(db: Session, today: Optional[date] = None) -> dict:
    from .. import mail
    from ..util import human_date, local_today

    t = _tool()
    if t is None:
        return {"messages": 0, "mails": 0}
    today = today or local_today()
    try:
        sent: dict = json.loads(state.get(db, SENT_KEY) or "{}")
    except ValueError:
        sent = {}
    comps = {c["id"]: c for c in rows(db, None, "companies")}
    people = {}
    for p in rows(db, None, "company_people"):
        people.setdefault(p.get("company"), set()).add(p.get("person"))
    digest: dict[int, list[str]] = {}
    extra: dict[str, list[str]] = {}
    n_msg = 0
    for d in rows(db, None, "documents"):
        c = comps.get(d.get("company"))
        if c is None or c.get("archived") or d.get("archived") or d.get("no_expiry") or d.get("required") is False or not d.get("expiry"):
            continue
        try:
            exp = date.fromisoformat(str(d["expiry"])[:10])
        except ValueError:
            continue
        left = (exp - today).days
        key = f"{d['id']}:{exp.isoformat()}"  # a new expiry date (a renewal) starts the stages again
        done = set(sent.get(key) or [])
        due = [s for s in _stages(d.get("warn_days")) if left <= s and str(s) not in done]
        if left < 0 and "expired" not in done:
            due.append("expired")
        if not due:
            continue
        done.update(str(s) for s in due)
        sent[key] = sorted(done)
        title = d.get("title") or (d.get("_labels") or {}).get("doc_type") or "A document"
        what = f"{title} ({c.get('name')})"
        text = (f"{what} expired on {human_date(exp)} ({-left} day{'s' if left != -1 else ''} ago)." if left < 0 else
                f"{what} expires today, {human_date(exp)}." if left == 0 else f"{what} expires on {human_date(exp)} — in {left} day{'s' if left != 1 else ''}.")
        link = f"#/tool?t={SLUG}&p=company&company={c['id']}&open={d['id']}"
        who: dict[int, User] = {}
        for uid in [d.get("filed_by"), c.get("responsible_account"), *(people.get(c["id"]) or [])]:
            u = db.get(User, uid) if uid else None
            if u is None or not u.is_active or u.is_axl:
                continue
            if uid in (d.get("filed_by"), c.get("responsible_account")) or u.is_admin or u.is_manager:
                who[u.id] = u
        for u in who.values():
            db.add(CexaMessage(user_id=u.id, from_axl=True, kind="needs_you", title=f"{title} — {'expired' if left < 0 else 'expires soon'}", text=text, link=link, meta={"tool": SLUG, "row": d["id"]}))
            digest.setdefault(u.id, []).append(text)
            n_msg += 1
        addr = str(c.get("responsible_email") or "").strip()
        if addr and mail.valid(addr) and not c.get("responsible_account"):
            extra.setdefault(addr, []).append(text)
    n_mail = 0
    if digest or extra:
        from .. import mailbox

        for uid, lines in digest.items():
            u = db.get(User, uid)
            if u is not None and u.email:
                r = mailbox.send(db, to=[u.email], subject=f"Entity Compliance: {len(lines)} document{'s' if len(lines) != 1 else ''} need{'s' if len(lines) == 1 else ''} attention",
                                 body="\n".join(f"- {x}" for x in lines) + "\n" + mailbox.footer(db, None, f"#/tool?t={SLUG}&p=grid"), kind="docwatch", ref="reminders", check=False, hold="reminder")
                n_mail += 1 if r.ok else 0
        for addr, lines in extra.items():
            r = mailbox.send(db, to=[addr], subject="Entity Compliance: documents that need attention", body="\n".join(f"- {x}" for x in lines) + "\n" + mailbox.footer(db, None, ""), kind="docwatch", ref="reminders", hold="reminder")
            n_mail += 1 if r.ok else 0
        log(db, "docwatch_reminders", target=f"tools/{SLUG}", detail=f"{n_msg} message(s), {n_mail} mail(s)")
    state.set(db, SENT_KEY, json.dumps(sent))
    return {"messages": n_msg, "mails": n_mail}


@routine("entity-compliance-reminders", 3600)
def reminders_routine() -> None:
    with session_scope() as db:
        if use_tool(db):
            remind(db)
