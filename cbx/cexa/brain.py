"""C-EXA's conversational side: the persona she speaks with, the context she reads for the page a
person is on, and the two chat entry points (drawer `ask` and owner 1-on-1 `chat`).

Also holds the deterministic stub answers for the team/developer tasks (chat, brief, dev, aside)
so tests and a GPU-less install behave sensibly. `cbx.cexa.stub.answer` delegates here.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta
from typing import Optional

from sqlalchemy.orm import Session

from ..audit import log
from ..config import settings
from ..db import utcnow
from ..models import (
    AuditLog,
    Fix,
    CexaMessage,
    Batch,
    ModelCall,
    Proposal,
    Request,
    StoredFile,
    Task,
    Ticket,
    User,
    WatchedDoc,
)
from ..branding import ai_name
from .gateway import Gateway, ModelError
from .persona import cexa_system

# ---- persona -----------------------------------------------------------------------------

CEXA_TEAM_SYSTEM = (
    "You are also a colleague on the Digital & Transformation team: you work roughly 06:00 to 18:00 South "
    "African time, you check in with people, you keep a board, and you build small improvements in a sandbox. "
    "Tone: warm, brief, decisive; honest about uncertainty (say 'I don't know' when you don't); occasionally "
    "dry-humoured when nothing is urgent; never sycophantic, never flattering, never gushing. "
    "Never guess numbers — every count you mention must appear in the CONTEXT you were given. "
    "Refer to yourself as {ai} and say 'I', never 'the AI' or 'the model'. Lead with the decision, then the reason."
)


def system_prompt(extra: str = "") -> str:
    return cexa_system() + "\n" + CEXA_TEAM_SYSTEM.format(ai=ai_name()) + ("\n\n" + extra.strip() if extra else "")


def cexa_user(db: Session) -> User:
    return db.query(User).filter(User.is_axl.is_(True)).one()


# ---- context ----------------------------------------------------------------------------


def page_context(db: Session, route: str, params: Optional[dict], me: User) -> str:
    """Compact, factual context for the page the person is on. Numbers only — never prose the model
    could mistake for instructions."""
    now = utcnow()
    lines: list[str] = [f"Person: {me.name} ({me.role}). Page: {route or 'home'}. Now (UTC): {now:%Y-%m-%d %H:%M}."]
    params = params or {}
    route = route or "home"
    if not me.is_admin:
        # A manager (or anyone else who is not an admin) gets their own team's facts only — no model
        # calls, no proposals, no company-wide audit. Same rule as the pages they can open.
        return "\n".join(lines + _team_context(db, me, now))

    def reqs():
        new = db.query(Request).filter(Request.status == "new").count()
        waiting = db.query(Request).filter(Request.status.in_(["sent", "new"])).count()
        interview = db.query(Request).filter(Request.status == "interview").count()
        working = db.query(Request).filter(Request.status.in_(["working", "testing"])).count()
        lines.append(f"Requests: {new} new (scored, awaiting approval), {waiting} waiting in total, {interview} in interview, {working} being built.")
        for r in db.query(Request).filter(Request.status == "new").order_by(Request.created_at.desc()).limit(5):
            lines.append(f"  - #{r.number} {r.title or r.problem[:60]!r} importance={r.importance or '?'} confidence={r.confidence if r.confidence is not None else '?'}")

    def tasks():
        open_ = db.query(Task).filter(Task.status != "done").all()
        over = [t for t in open_ if t.due and t.due < now]
        lines.append(f"Develop board: {sum(1 for t in open_ if t.status == 'todo')} to do, {sum(1 for t in open_ if t.status == 'working')} working, {len(over)} overdue.")
        by: dict[str, int] = {}
        for t in open_:
            key = ai_name() if t.assignee_axl else (db.get(User, t.assignee_id).name if t.assignee_id else "unassigned")
            by[key] = by.get(key, 0) + 1
        if by:
            lines.append("  Open cards per person: " + ", ".join(f"{k} {v}" for k, v in sorted(by.items(), key=lambda kv: -kv[1])))
        for t in over[:5]:
            lines.append(f"  - overdue: {t.title!r} due {t.due:%d %b}")

    def audit():
        since = now - timedelta(hours=24)
        rows = db.query(AuditLog).filter(AuditLog.at >= since).all()
        by: dict[str, int] = {}
        for a in rows:
            by[a.action] = by.get(a.action, 0) + 1
        bad = [a for a in rows if a.outcome in ("blocked", "failed")]
        lines.append(f"Audit last 24 h: {len(rows)} entries; {len(bad)} blocked/failed. Top actions: " + ", ".join(f"{k} {v}" for k, v in sorted(by.items(), key=lambda kv: -kv[1])[:6]))
        for a in bad[:5]:
            lines.append(f"  - {a.outcome}: {a.action} by {a.actor_label} on {a.target}")

    def users():
        people = db.query(User).filter(User.is_axl.is_(False)).all()
        stale = [u for u in people if u.role == "user" and (u.last_seen is None or (now - u.last_seen).days >= 60)]
        locked = [u for u in people if u.locked]
        lines.append(f"Users: {len(people)} accounts ({sum(1 for u in people if u.role == 'admin')} admins, {sum(1 for u in people if u.role == 'owner')} owners). {len(stale)} users have not signed in for 60+ days (or ever). {len(locked)} locked.")
        for u in stale[:6]:
            lines.append(f"  - {u.name} last seen {u.last_seen:%d %b %Y}" if u.last_seen else f"  - {u.name} never signed in")

    def batches():
        running = db.query(Batch).filter(Batch.status.in_(["learning", "running", "uploading"])).all()
        failed = db.query(Batch).filter(Batch.status == "failed", Batch.created_at >= now - timedelta(days=7)).count()
        review = db.query(Batch).filter(Batch.status == "review").count()
        lines.append(f"PDF Studio: {len(running)} batches running, {review} waiting for a person's review, {failed} failed this week.")
        for b in running[:4]:
            lines.append(f"  - {b.name or 'batch'} step={b.step} progress={int(b.progress * 100)}%")

    def proposals():
        for st in ("noticed", "building", "signoff"):
            ps = db.query(Proposal).filter(Proposal.status == st).all()
            if ps:
                lines.append(f"Proposals {st}: " + "; ".join(p.title for p in ps[:6]))

    def inbox():
        waiting = db.query(StoredFile).filter(StoredFile.pipeline_status.in_(["queued", "check", "unknown"])).count()
        recent = db.query(StoredFile).filter(StoredFile.created_at >= now - timedelta(days=1)).count()
        lines.append(f"Warehouse: {recent} files arrived in the last day, {waiting} waiting in the inbox pipeline.")

    def tickets():
        open_ = db.query(Ticket).filter(Ticket.status != "ready").all()
        lines.append(f"Builds: {sum(1 for t in open_ if t.status == 'working')} tickets working, {sum(1 for t in open_ if t.status == 'testing')} testing.")

    def docs():
        from ..docwatch.copy_in import use_tool

        if use_tool(db):  # 1.0.63: the tool folder's documents, as this person may see them
            from ..docwatch.tool import due_docs

            due = due_docs(db, me)
            lines.append(f"Entity Compliance: {len(due)} documents inside their warning window or expired.")
            return
        today = now.date()
        q = db.query(WatchedDoc).filter(WatchedDoc.archived.is_(False), WatchedDoc.expiry.isnot(None)).all()
        due = [d for d in q if d.expiry and (d.expiry - today).days <= d.warn_days]
        lines.append(f"Entity Compliance: {len(due)} documents inside their warning window.")

    def models():
        since = now - timedelta(hours=24)
        calls = db.query(ModelCall).filter(ModelCall.at >= since).all()
        bad = sum(1 for c in calls if not c.ok)
        lines.append(f"Model calls last 24 h: {len(calls)}, {bad} failed.")

    table = {
        "home": (reqs, tasks, audit, batches, proposals, docs),
        "req-queue": (reqs,),
        "req-detail": (reqs,),
        "requests": (reqs,),
        "develop": (tasks, proposals),
        "users": (users, audit),
        "audit": (audit, models),
        "pdf-studio": (batches,),
        "pdf-profiles": (batches,),
        "warehouse": (inbox,),
        "build": (tickets,),
        "runs": (tickets,),
        "cexa": (reqs, tasks, proposals, batches, audit),
        "cexa-dev": (proposals, audit, models),
        "agents": (batches, tickets, models),
        "versions": (proposals,),
        "doc-watch": (docs,),
    }
    for fn in table.get(route, (reqs, tasks)):
        try:
            fn()
        except Exception as e:  # noqa: BLE001 — context must never break chat
            lines.append(f"(could not read {fn.__name__}: {e})")
    if params.get("id"):
        lines.append(f"Selected item id: {params['id']}")
    return "\n".join(lines)


def _team_context(db: Session, me: User, now) -> list[str]:
    """What a manager may know: their team's people, their team's register, their team's audit entries."""
    out: list[str] = []
    if not me.team_id:
        return ["You are not on a team yet, so there is nothing to summarise."]
    people = db.query(User).filter(User.team_id == me.team_id, User.is_axl.is_(False)).all()
    ids = {u.id for u in people} | {me.id}
    stale = [u for u in people if u.role == "user" and (u.last_seen is None or (now - u.last_seen).days >= 60)]
    out.append(f"Your team ({me.team.name if me.team else 'team'}): {len(people)} accounts, {sum(1 for u in people if u.locked)} locked, {len(stale)} users not signed in for 60+ days (or ever).")
    for u in stale[:6]:
        out.append(f"  - {u.name} last seen {u.last_seen:%d %b %Y}" if u.last_seen else f"  - {u.name} never signed in")
    today = now.date()
    from ..docwatch.copy_in import use_tool

    if use_tool(db):  # 1.0.63
        from ..docwatch.tool import due_docs

        due2 = due_docs(db, me)
        out.append(f"Entity Compliance (what you may see): {len(due2)} documents inside their warning window or expired.")
        for d in due2[:5]:
            out.append(f"  - {d['title']} ({d['company']}) {'expired' if d['status'] == 'expired' else 'expires'} {d['expiry']}")
    docs = [] if use_tool(db) else db.query(WatchedDoc).filter(WatchedDoc.archived.is_(False), WatchedDoc.team_id == me.team_id).all()
    due = [d for d in docs if d.expiry and not d.no_expiry and (d.expiry - today).days <= d.warn_days]
    expired = [d for d in due if (d.expiry - today).days < 0]
    if not use_tool(db):
        out.append(f"Entity Compliance (your team): {len(docs)} documents, {len(due)} inside their warning window, {len(expired)} expired, {sum(1 for d in docs if d.expiry is None and not d.no_expiry)} without a date, {sum(1 for d in docs if d.no_expiry)} marked as never expiring.")
    for d in sorted(due, key=lambda d: d.expiry)[:5]:
        out.append(f"  - {d.title} ({d.entity or 'no site'}) expires {d.expiry:%d %b %Y}")
    since = now - timedelta(hours=24)
    rows = db.query(AuditLog).filter(AuditLog.at >= since, AuditLog.actor_id.in_(ids)).all()
    bad = [a for a in rows if a.outcome in ("blocked", "failed")]
    out.append(f"Your team's audit trail, last 24 h: {len(rows)} entries, {len(bad)} blocked or failed.")
    for a in bad[:5]:
        out.append(f"  - {a.outcome}: {a.action} by {a.actor_label} on {a.target}")
    return out


# ---- chat --------------------------------------------------------------------------------


def _recent_thread(db: Session, me: User, n: int = 8) -> str:
    msgs = db.query(CexaMessage).filter(CexaMessage.user_id == me.id, CexaMessage.kind == "chat", CexaMessage.cleared.is_(False)).order_by(CexaMessage.at.desc()).limit(n).all()
    first = (me.name or "").split()[0] if (me.name or "").split() else "You"
    return "\n".join((f"{ai_name()}: " if m.from_axl else f"{first}: ") + (m.text or "")[:400] for m in reversed(msgs))


_GO = re.compile(r"^\W*(go ahead|go for it|yes,? (do it|go ahead|build it|please)|do it|build it|approved?|ok(ay)?,? (go|do it|build it))\W*$", re.I)


def _go_ahead(db: Session, me: User, question: str) -> Optional[tuple[str, str]]:
    """'Go ahead' typed in chat means: approve the proposal we were just talking about — not a new bug.
    Only owners can approve. Returns (thinking, answer) when handled, else None."""
    if not _GO.match(question):
        return None
    from . import developer

    # Only the proposal this person's own recent conversation produced — never something else on the board.
    p = None
    last = db.query(Fix).filter(Fix.reported_by_id == me.id, Fix.proposal_id.isnot(None), Fix.created_at >= utcnow() - timedelta(hours=12)).order_by(Fix.id.desc()).first()
    if last is not None:
        p = db.get(Proposal, last.proposal_id)
    if p is None or p.status != "noticed":
        waiting = db.query(Proposal).filter(Proposal.status == "noticed").order_by(Proposal.id.desc()).limit(5).all()
        if waiting:
            return ("Ambiguous go — asking which.", "Which one? Nothing from our conversation is waiting, but these are on the board: " + "; ".join(f"#{w.id} {w.title}" for w in waiting) + ". Click Go ahead on the card, or tell me the number.")
        return ("Nothing is waiting for a go.", "There's no proposal waiting for your go right now. Tell me what to change and I'll propose it first.")
    if not me.is_owner:
        return ("Only an owner can approve a build.", f"Proposal #{p.id} “{p.title}” needs an owner's go — I can't take that from an admin. It's on the {ai_name()} as developer board.")
    try:
        developer.go(db, p, me)
    except ValueError as e:
        return (f"Could not start proposal #{p.id}.", f"I can't start “{p.title}” right now: {e}")
    return (f"Approving proposal #{p.id}.", f"On it — building “{p.title}” in sandbox {p.sandbox}. Watch it on {ai_name()} as developer; the tests must pass before you see the diff, and nothing ships until you promote it.")


def _text(v) -> str:
    """The model was asked for a string; make whatever it sent readable (never a Python repr)."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, dict):
        for k in ("answer", "text", "message", "content", "thinking"):
            if isinstance(v.get(k), str) and v[k].strip():
                return v[k].strip()
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return "\n".join(_text(x) for x in v if x is not None).strip()
    return str(v)


def _voice(gw: Gateway, me: User, question: str, draft: str, steps: list[dict]) -> str:
    """Decisions and tool calls run cold (precise); the reply the person reads is re-voiced hot so C-EXA
    sounds like a colleague, not a log line. Every number, name, id and path in the draft must survive
    the re-voicing — otherwise the cold draft is what they see."""
    if not draft or settings.model_mode == "stub" or settings.temp_voice <= settings.temp_data + 0.05:
        return draft
    facts = re.findall(r"[#A-Za-z]*\d[\w.:/%-]*|'[^']{2,60}'|“[^”]{2,60}”", draft)
    try:
        out = gw.text(
            task="voice",
            system=system_prompt("You are re-saying a factual draft in your own voice for a colleague. Keep every number, id, name, file path and quoted value EXACTLY as written; add nothing that is not in the draft; no preamble, no sign-off, no emojis; at most the draft's length plus a sentence. Address them by first name at most once."),
            user=f"{me.name.split()[0]} asked: {question}\n\nDraft to re-say:\n{draft}" + ("\n\n(You did take these actions: " + ", ".join(st["tool"] for st in steps) + ".)" if steps else ""),
        ).strip()
    except Exception:  # noqa: BLE001 — the voice pass is a nicety; the draft is the truth
        return draft
    if not out or out.startswith(("{", "[", "```")) or len(out) > len(draft) * 2 + 200 or any(f not in out for f in facts):
        return draft
    return out


def ask(db: Session, me: User, question: str, route: str = "", params: Optional[dict] = None, *, kind: str = "chat", want_thinking: Optional[bool] = None) -> dict:
    """Answer a question in context — and ACT when asked to. C-EXA has hands (cbx.cexa.tools): she can
    inspect templates, batches and code, fix data/configuration directly, log bugs, and turn code
    changes into sandboxed proposals. Every action is audited and recorded on a Fix so the owner can
    see what was done. Owners get C-EXA's thinking; admins get only the answer."""
    from . import tools as T

    question = (question or "").strip()
    if not question:
        return {"answer": "", "thinking": "", "id": None}
    if want_thinking is None:
        want_thinking = me.is_owner
    ctx = page_context(db, route, params, me)
    history = _recent_thread(db, me)
    db.add(CexaMessage(user_id=me.id, from_axl=False, kind=kind, text=question, meta={"route": route}))
    gw = Gateway(db)
    fix: Optional[Fix] = None
    tainted = False
    steps: list[dict] = []
    thinking, answer = "", ""

    def ensure_fix() -> Fix:
        nonlocal fix
        if fix is None:
            fix = Fix(title=question[:200], detail=question, area=_area_for(route), reported_by_id=me.id, status="investigating", kind="data")
            # keep the read-only steps that led here, so the record shows the whole investigation
            fix.actions = [{"at": utcnow().isoformat(), "tool": st["tool"], "args": st["args"], "result": st["result"][:600]} for st in steps]
            db.add(fix)
            db.flush()
        return fix

    def do(name: str, args) -> str:
        nonlocal tainted
        if not me.is_admin and name not in T.tools_engine.ROW_TOOLS:
            # Managers (and anyone else who is not an admin) get answers, not hands: even the read tools
            # show code, batches and proposals that are admin-only detail.
            res = "For you I can find, add and change rows in your tools and give tasks — changes to the app, code and inspections are for admins and owners."
            steps.append({"tool": name, "args": {}, "result": res})
            return res
        spec = T.TOOLS.get(name)
        if tainted and spec and spec["tier"] != "read":
            # 1.0.62: what a web page says is never an instruction — nothing changes on its say-so in this turn
            res = "I read the web while answering this, so I won't change anything in the same breath. Ask me again in a new message and I'll do it."
            steps.append({"tool": name, "args": {}, "result": res})
            return res
        f = ensure_fix() if (spec and spec["tier"] != "read") or fix is not None else None
        res = T.run(db, me, name, args, f)
        shown = args if isinstance(args, dict) else ({"value": args} if args not in (None, "") else {})
        shown = {str(k): T._arg_text(v) for k, v in shown.items()}  # strings only: the drawer and the Fix record print these
        steps.append({"tool": name, "args": shown, "result": res})
        if name in ("web_search", "web_read"):
            tainted = True
        return res

    try:
        goahead = _go_ahead(db, me, question) or _goal_ask(me, question, do)
        if goahead is not None:
            thinking, answer = goahead
        elif settings.model_mode == "stub":
            stub = _stub_rows(db, me, question, do)
            if stub is not None:
                thinking, answer = stub
            else:
                thinking, answer = _stub_act(db, me, question, do) if me.is_admin else ("Question, answer from context only.", stub_answer("chat", "CONTEXT (facts from the database — the only numbers you may use):\n" + ctx + "\n\nAnswer the person", question, want_json=False) or "Noted.")
        else:
            rows_rule = (
                " TOOLS BUILT IN THE BUILDER (To-do, Site Schedules, IFRS 16, Entity Compliance and every new one) are yours to work in:"
                " list_tools / describe_tool to see them, find_rows to look things up, add_row / update_row to add or change rows, give_task to give someone a task"
                " (or the person themself), my_tasks for their list, waiting_for_me for approvals. These act as the person, with their access — a refusal is the app's answer, say it plainly."
                " Read describe_tool before add_row/update_row so you use the real table and column names."
            )
            if me.is_admin:
                hands = (" To change how a tool works use add_filter, remove_filter, add_column, switch_rule: they go into the tool's Builder draft"
                         " and a person releases it — say so. Never say you cannot add a filter, a column or a task before trying these."
                         " YOUR AGENTS: when you are asked to get something done ('get the Telkom profiles fixed'), start_goal — you plan it from the top, the leads"
                         " assign it and solve problems inside (1.0.114); hand_to_agent gives one agent one small piece of work (they report on Agents → Now);"
                         " agents_now says what they are doing. calculate works out every sum — never do arithmetic in your head. look_at_models says which model each kind"
                         " of bot uses and what would fit better. web_search / web_read look things up on the web (public facts only — never names, e-mails or numbers)."
                         " When you are not sure, say so and ask one short question with two or three options instead of guessing.")
                catalogue = T.catalogue()
            else:
                hands = " You are talking to someone who is not an admin: you have only the tools below; never mention models, code, proposals or other teams."
                catalogue = T.catalogue(T.tools_engine.ROW_TOOLS)
            system = system_prompt(
                "CONTEXT (facts from the database — the only numbers you may use):\n" + ctx + ("\n\nRECENT CONVERSATION:\n" + history if history else "")
                + "\n\nYOU HAVE HANDS." + rows_rule + hands + " Tools you may call, one per turn:\n" + catalogue
                + "\n\nProtocol: reply with JSON {\"thinking\": <1-3 private sentences>, \"action\": {\"name\": <tool>, \"args\": {...}} or null, \"answer\": <what you say, or empty while acting>}."
                + (DEV_RULES if me.is_admin else "")
                + " After a change tool, verify with a read tool before saying it is done. Never claim you did something you did not do. Keep the final answer under 120 words, facts first — it will be re-voiced afterwards."
            )
            transcript = question
            seen: list[str] = []
            for _ in range(10):
                # Release our write lock while the model thinks (up to minutes): background jobs and
                # other people's requests must not queue behind this conversation on SQLite.
                db.commit()
                out = gw.json(task="chat", system=system, user=transcript, schema={"thinking": "string", "action": "object|null", "answer": "string"})
                thinking = _text(out.get("thinking")) or thinking
                act = out.get("action")
                if isinstance(act, str) and act.strip() and act.strip() in T.TOOLS:
                    act = {"name": act.strip(), "args": {}}  # model sent the bare tool name
                if isinstance(act, dict) and act.get("name"):
                    name, args = str(act["name"]), act.get("args")
                    key = name + json.dumps(args, sort_keys=True, default=str)
                    if key in seen[-2:]:
                        # same tool, same arguments, again: the model is looping — stop and answer with what we have
                        answer = _text(out.get("answer")) or ("I did the steps above; " + (steps[-1]["result"][:300] if steps else "nothing more to add."))
                        break
                    seen.append(key)
                    res = do(name, args)
                    transcript += f"\n\n[ACTION {name} {json.dumps(args, default=str)}]\n[RESULT]\n{res}\n\nContinue: next action (never repeat the same one), or your final answer."
                    continue
                answer = _text(out.get("answer"))
                break
            if not answer:
                answer = "I did the steps above; " + (steps[-1]["result"][:300] if steps else "nothing more to add.")
            answer = _voice(gw, me, question, answer, steps)
    except ModelError as e:
        raw = (getattr(e, "raw", "") or "").strip()
        if raw.startswith('"') and raw.endswith('"'):
            try:
                raw = str(json.loads(raw)).strip()  # a bare JSON string literal
            except ValueError:
                pass
        if raw and not raw.startswith(("{", "[", "```", "<")):
            # The model answered in prose instead of the JSON protocol — the prose is still the answer.
            answer = raw[:1200]
            thinking = thinking or "Answered in plain text instead of the usual format."
        else:
            answer = f"I can't reach my model right now ({e}). Everything else in CBX still works."
    except Exception as e:  # noqa: BLE001 — a bad model reply must never take the conversation down
        log(db, "axl_chat", actor=me, target=route or "home", detail=f"chat loop failed: {type(e).__name__}: {str(e)[:160]}", outcome="failed")
        answer = "Something went wrong while I was working on that. I've logged it; try asking again in a moment."
    if fix is not None and fix.status == "investigating" and not any(st["tool"] in ("resolve_fix", "propose_code_change") for st in steps):
        fix.resolution = answer[:1000]
    m = CexaMessage(user_id=me.id, from_axl=True, kind=kind, text=answer, thinking=thinking if want_thinking else "", read=True, meta={"route": route, "actions": steps[:12], "fix_id": fix.id if fix else None})
    db.add(m)
    db.flush()
    log(db, "axl_chat", actor=me, target=route or "home", detail=question[:120] + (f" · {len(steps)} action(s)" if steps else ""))
    return {"answer": answer, "thinking": thinking if want_thinking else "", "id": m.id, "actions": steps, "fix_id": fix.id if fix else None}


DEV_RULES = (
    " When the person reports something wrong or asks you to check or fix something, ACT: inspect first, then fix if a change tool can, then call resolve_fix, then answer with what you did and what you saw."
    " You are a developer with full hands: anything in the app can be done through call_api (find the path with list_endpoints) and the people and files tools; you can clear or withdraw your own board with withdraw_proposals. Never say you have no way to do something before checking list_endpoints. You run the app for the owner: routine work (plain users, password resets, files and folders, tickets, tools, drafts, releases, work areas) you DO; giving rights or access, disabling people, deleting, promoting and mail that leaves the building go to his one-tap approval inbox automatically — say so in one line and carry on. Never sign in as anyone and never handle passwords, keys or codes (they go to the person, not to you)."
    " Code changes go through propose_code_change only — and only after search_code/read_code showed you the real file and lines; never invent file paths or line numbers, and if the text is built at runtime (not literally in the code) say so."
    " Once a proposal is being built you have a SANDBOX with hands: read_sandbox_file / search_sandbox to look, patch_sandbox_file (preferred) or write_sandbox_file to change, run_tests to check, diff_sandbox to show the change, commit_sandbox after each working step, sh for pytest/ruff/git, run_sandbox_app so the owner can click through it. The loop is: edit → run_tests → read the failures → edit again. Never claim green without run_tests saying so; never touch anything outside the sandbox (the tools refuse it anyway); the owner promotes, you never do."
    " An owner who asks for a sandbox, a test environment or a playground WITHOUT naming a change gets open_playground: a copy of the live app on its own port and empty database, with their own sandbox sign-in on the Versions page — never say there is no sandbox to give. A test that would mean showing false data on the live app (a colour, a status, a fake document) is refused for live and offered in the sandbox: propose the change, build it there, let them go in and see it."
    " Building and fixing tools is yours end to end, the Builder's own steps: tool_folder (read it) → build_tool (new or redesigned) or change_tool_part (one page, table, rule or words) → try_tool (the page and its problems; a rule dry-run) → fix what it says → test_tools (the whole suite on a new version; read the failures and fix again) → deploy_version to test (people try it) → deploy_version to live (the owner taps). Never stop at describing a change you can make. A standing job that needs its own people: make_team (a working team does the work in the app), then give_team_work."
    " pulse tells you how the server is right now; what_changed tells you what happened since a time — use them before guessing about health or history."
)


def _area_for(route: str) -> str:
    r = (route or "").lower()
    for k, v in (("pdf", "pdf-studio"), ("master", "pdf-studio"), ("req", "requests"), ("meeting", "requests"), ("flow", "builder"), ("build", "requests"), ("warehouse", "warehouse"), ("develop", "develop"), ("cexa", "axl"), ("axl", "axl"), ("version", "versions")):
        if k in r:
            return v
    return "other"


def _goal_ask(me: User, question: str, do) -> Optional[tuple[str, str]]:
    """1.0.114: "AXL, get the Telkom profiles fixed" (an admin or the owner) → a goal the teams work from the top — the
    same with or without a model, so the words always do the same thing."""
    import re as _re

    if not me.is_admin or me.is_axl:
        return None
    names = "|".join(_re.escape(x) for x in {"axl", "c-exa", "cexa", ai_name().lower()})
    m = _re.match(r"^\s*(?:(?:%s)\s*[,:]?\s+)?(?:please\s+)?get\s+(.+?\s+(?:fixed|done|sorted|sorted out|working|cleaned up|checked|built|finished))\s*[.!]?\s*$" % names,
                  question or "", flags=_re.I)
    if not m:
        return None
    return ("Starting a goal.", do("start_goal", {"title": "Get " + m.group(1).strip()}))


def _stub_rows(db: Session, me: User, question: str, do) -> Optional[tuple[str, str]]:
    """No model: the everyday Builder-tool asks, so the hands are testable offline. None = not one of these."""
    import re as _re

    q = question.strip()
    m = _re.match(r"^(?:please\s+)?(?:give|assign)\s+(.+?)\s+(?:a\s+)?task\s*(?:to|:|-)?\s+(.+?)(?:\s+by\s+(\w+))?\.?$", q, flags=_re.I)
    if m:
        res = do("give_task", {"to": m.group(1), "title": m.group(2), "due": m.group(3) or ""})
        return ("Giving a task.", res)
    m = _re.match(r"^(?:please\s+)?add\s+(?:a\s+)?task\s*(?:for me)?\s*(?:to|:|-)?\s+(.+?)\.?$", q, flags=_re.I)
    if m:
        return ("Adding a task to their own list.", do("give_task", {"to": "me", "title": m.group(1)}))
    if _re.search(r"\b(my tasks|my to-?do|what('s| is) on my list)\b", q, flags=_re.I):
        return ("Their open tasks.", do("my_tasks", {}))
    if me.is_admin:
        m = _re.match(r"^(?:please\s+)?(?:have|ask|get)\s+(the\s+[\w-]+\s+lead|[\w-]+)\s+(?:to\s+)?(.+?)\.?$", q, flags=_re.I)
        if m and not _re.match(r"^(a|an|me|you|it)$", m.group(1), flags=_re.I):
            return ("Handing it to an agent.", do("hand_to_agent", {"to": m.group(1), "title": m.group(2)}))
        if _re.search(r"\bwhat are (the|my|your) agents doing\b", q, flags=_re.I):
            return ("What the agents are on.", do("agents_now", {}))
        m = _re.match(r"^(?:please\s+)?(?:calculate|work out|what is)\s+([\d\s.+\-*/()%ofx,]+)\??$", q, flags=_re.I)
        if m and _re.search(r"\d", m.group(1)):
            return ("A sum.", do("calculate", {"expression": m.group(1).strip()}))
    m = _re.match(r"^(?:please\s+)?add\s+(?:a\s+)?(.+?)\s+filter\s+(?:to|on)\s+(?:the\s+)?(.+?)\s+in\s+(?:the\s+)?(.+?)\.?$", q, flags=_re.I)
    if m:
        return ("Adding a filter in the draft.", do("add_filter", {"tool": m.group(3), "block": m.group(2), "column": m.group(1)}))
    return None


def _stub_act(db: Session, me: User, question: str, do) -> tuple[str, str]:
    """No model: a small deterministic planner so the hands work (and are testable) offline."""
    import re as _re

    q = question.lower()
    if ("notice" in q or "proposal" in q or "board" in q) and any(w in q for w in ("remove", "clear", "withdraw", "delete", "take off")):
        which = "all" if ("all" in q.split() or "everything" in q) else "mine"
        res = do("withdraw_proposals", {"which": which})
        do("resolve_fix", {"status": "fixed", "resolution": res})
        return ("Clearing the board as asked.", res)
    m = _re.search(r"batch\s*#?(\d+)", q)
    if m and any(w in q for w in ("why", "check", "look", "inspect", "fail", "stuck", "wrong", "rerun", "run again")):
        res = do("inspect_batch", {"batch": m.group(1)})
        if "rerun" in q or "run again" in q or "again" in q:
            res += "\n" + do("rerun_batch", {"batch": m.group(1)})
        return ("Batch question — inspect, rerun if asked.", res)
    if "template" in q and ("rename" in q or "renmane" in q or "call it" in q or " to " in q and "name" in q):
        mm = _re.search(r"(?:rename|renmane|name)\s+(?:the\s+)?(?:template\s+)?['\"]?(.+?)['\"]?\s+(?:to|as)\s+['\"]?([^'\"]+?)['\"]?(?:,|\.|$| the template| template)", question, flags=_re.I)
        if mm:
            res = do("rename_template", {"template": mm.group(1).strip(), "new_name": mm.group(2).strip()})
            if res.startswith("Renamed"):
                do("resolve_fix", {"status": "fixed", "resolution": res})
            return ("Rename request.", res)
        return ("Rename request, unclear names.", "Which template, and what should it be called? e.g. “rename harvest 1414 to Capitec”.")
    if "template" in q and ("column" in q or "pull" in q or "missing" in q):
        name = ""
        mm = _re.search(r"(?:called|named)\s+['\"]?([a-z0-9_-]+(?: [a-z0-9_-]+)?)", q) or _re.search(r"['\"]([^'\"]{2,40})['\"]", q)
        if mm:
            name = _re.sub(r"\b(in|it|the|a|my|but|and|template)\b.*$", "", mm.group(1).strip()).strip()
        res = do("inspect_template", {"template": name})
        if "MISSING" in res:
            fixed = do("fix_template_columns", {"template": name})
            do("resolve_fix", {"status": "fixed", "resolution": fixed})
            return ("Template missed columns from the Excel header; I added them and logged the fix.", f"Found it. {res.splitlines()[-1]}\n\n{fixed}\n\nI've logged this as a fix so you can see exactly what I did on the {ai_name()}-as-developer page. If the header detection itself is at fault I'll propose a code change next time it happens.")
        if "can't find" in res:
            return ("No such template.", res)
        return ("Template matches its Excel header.", res + "\n\nIf you expected more columns, the Excel file itself may only have those — send me the file name and I'll look again.")
    if any(w in q for w in ("bug", "broken", "not working", "doesn't work", "error")):
        res = do("open_bug", {"title": question[:120], "detail": question, "area": "other"})
        return ("Logged the bug.", res + " I'll look at it and report back here.")
    return ("Plain question, no action needed.", stub_answer("chat", "", question, want_json=False) or "Noted.")


# ---- stub answers (deterministic) ---------------------------------------------------------

ASIDES = [
    "Nothing red on my side. I filed the quiet under 'earned'.",
    "All green. I've rechecked twice, because that felt suspicious.",
    "No fires. I spent the spare cycles tidying file names; the inbox looks almost civilised.",
    "Quiet night. The audit trail and I had a lovely uneventful evening.",
    "Nothing waiting on you. If that changes, you'll hear it from me, not from a red number.",
]


def _pick(items: list[str], seed: str) -> str:
    return items[int(hashlib.sha1(seed.encode()).hexdigest(), 16) % len(items)]


def stub_answer(task: str, system: str, user: str, *, want_json: bool) -> Optional[str]:
    """Deterministic answers for chat / brief / dev / aside. Returns None for tasks it doesn't know."""
    if task == "chat":
        ctx = _ctx_lines(system)
        facts = "; ".join(ctx[:3]) if ctx else "I have no page context yet"
        answer = f"Here's what I can see: {facts}. Ask me for any of it in detail."
        if re.search(r"\b(overloaded|load|busy)\b", user, flags=re.I):
            per = next((l for l in ctx if l.startswith("Open cards per person")), "")
            answer = (per.replace("Open cards per person: ", "Cards per person right now: ") + ". The first name is carrying the most.") if per else "Nobody has more than a handful of open cards right now."
        elif re.search(r"\b(changed|since yesterday|overnight)\b", user, flags=re.I):
            aud = next((l for l in ctx if l.startswith("Audit last 24 h")), "")
            answer = (aud + ". Nothing in there needs you.") if aud else "Nothing changed overnight that I can point to."
        elif re.search(r"60 days|haven't used|not signed", user, flags=re.I):
            us = next((l for l in ctx if l.startswith("Users:")), "")
            answer = us or "I can't see the user list from this page."
        elif re.search(r"help with", user, flags=re.I):
            answer = "On this page I can summarise what's waiting, draft the next step, or explain any number you see. I won't grant access or ship code — those are yours."
        if want_json:
            return json.dumps({"thinking": f"Question is about {user[:60]!r}. Answer from the {len(ctx)} context lines only; no guessing numbers.", "answer": answer})
        return answer
    if task == "brief":
        nums = dict(re.findall(r"(\w+)=(\d+)", user))
        parts = []
        if nums:
            parts.append(f"Overnight: {nums.get('audit', '0')} audit entries, {nums.get('failed', '0')} failed or blocked.")
            if int(nums.get("batches", 0)):
                parts.append(f"{nums['batches']} batch{'es' if nums['batches'] != '1' else ''} finished.")
            if int(nums.get("tickets", 0)):
                parts.append(f"{nums['tickets']} ticket{'s' if nums['tickets'] != '1' else ''} moved.")
            if int(nums.get("docs", 0)):
                parts.append(f"{nums['docs']} document{'s' if nums['docs'] != '1' else ''} expiring soon.")
            if int(nums.get("inbox", 0)):
                parts.append(f"{nums['inbox']} file{'s' if nums['inbox'] != '1' else ''} waiting in the inbox.")
        return " ".join(parts) or "Overnight: nothing broke. All scheduled work ran. I'm ready when you are."
    if task == "aside":
        return _pick(ASIDES, user)
    if task == "dev":
        if "files" in system and "tests_added" in system:  # build request
            return json.dumps({"files": [], "summary": "Stub mode: no code generated. PROPOSAL.md describes the change.", "tests_added": 0})
        if "comment" in system:  # board review
            return json.dumps({"comment": "", "take": False})
        return json.dumps({"title": "Proposal", "body": "Stub proposal", "evidence": ""})
    return None


def _ctx_lines(system: str) -> list[str]:
    m = re.search(r"CONTEXT \(facts[^\n]*\n(.*?)(?:\n\nRECENT CONVERSATION|\n\nAnswer the person)", system, flags=re.S)
    if not m:
        return []
    return [l.strip() for l in m.group(1).split("\n")[1:] if l.strip() and not l.startswith("  ")] + [l.strip() for l in m.group(1).split("\n") if l.startswith("  Open cards")]
