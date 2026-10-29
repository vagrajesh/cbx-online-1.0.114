"""My mail (1.0.69) — the owner's own mailbox and its three agents. See cbx/maildesk/core.py.
Everything here is the owner's alone (and refused to a session C-EXA drives for him).

    GET  /api/mymail                       the linked mailbox (or none), its agents, counts
    GET  /api/mymail/mailboxes             the launcher: every mailbox in the server's Outlook
    POST /api/mymail/link {store}          link one          DELETE /api/mymail   unlink (CBX forgets it; Outlook untouched)
    PATCH /api/mymail {active, auto_drafts, auto_todo}
    POST /api/mymail/run                   the agents go now
    GET  /api/mymail/items?category=&q=    what they made of each mail
    POST /api/mymail/items/{id}/draft      the Writer drafts a reply into Drafts now
    POST /api/mymail/items/{id}/todo       add its actions to To-do now
    PATCH /api/mymail/items/{id} {category}
    GET/POST /api/mymail/rules · POST /rules/{id}/approve|decline|off · DELETE /rules/{id}
    GET/POST /api/mymail/templates · PUT/DELETE /templates/{id} · POST /templates/{id}/keep|drop
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..audit import log
from ..db import get_db
from ..maildesk import core as M
from ..models import MailItem, MailLink, MailRule, MailTemplate, User
from ..security.deps import require_human, require_owner
from ..support.outlook import OutlookError

router = APIRouter(prefix="/api/mymail", tags=["mymail"])


def me_owner(me: User = Depends(require_owner), _h: User = Depends(require_human)) -> User:
    return me  # the owner, as a person — never a session C-EXA is driving


class LinkIn(BaseModel):
    store: str


class LinkPatch(BaseModel):
    active: Optional[bool] = None
    auto_drafts: Optional[bool] = None
    auto_todo: Optional[bool] = None


class ItemPatch(BaseModel):
    category: str


class RuleIn(BaseModel):
    name: str = ""
    sender: str = ""
    subject: str = ""
    category: str = ""
    then_category: str = ""
    folder: str = ""
    flag: bool = False
    no_draft: bool = False


class TemplateIn(BaseModel):
    name: str
    when: str = ""
    body: str


def _link(db: Session, me: User) -> MailLink:
    l = db.query(MailLink).filter(MailLink.user_id == me.id).first()
    if l is None:
        raise HTTPException(404, "No mailbox linked yet.")
    return l


def _item(db: Session, l: MailLink, iid: int) -> MailItem:
    it = db.get(MailItem, iid)
    if it is None or it.link_id != l.id:
        raise HTTPException(404, "No such mail.")
    return it


@router.get("")
def overview(db: Session = Depends(get_db), me: User = Depends(me_owner)):
    l = db.query(MailLink).filter(MailLink.user_id == me.id).first()
    return {"link": M.link_out(db, l) if l else None, "categories": M.CATS}


@router.get("/mailboxes")
def mailboxes(db: Session = Depends(get_db), me: User = Depends(me_owner)):
    try:
        return {"mailboxes": M.mailboxes(db, me)}
    except OutlookError as e:
        raise HTTPException(409, str(e))


@router.post("/link")
def link(body: LinkIn, db: Session = Depends(get_db), me: User = Depends(me_owner)):
    if db.query(MailLink).filter(MailLink.user_id == me.id).first():
        raise HTTPException(400, "You have a mailbox linked already — unlink it first.")
    try:
        l = M.link(db, me, body.store)
    except OutlookError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"link": M.link_out(db, l)}


@router.delete("")
def unlink(db: Session = Depends(get_db), me: User = Depends(me_owner)):
    M.unlink(db, me, _link(db, me))
    return {"ok": True}


@router.patch("")
def patch(body: LinkPatch, db: Session = Depends(get_db), me: User = Depends(me_owner)):
    l = _link(db, me)
    for k in ("active", "auto_drafts", "auto_todo"):
        v = getattr(body, k)
        if v is not None:
            setattr(l, k, v)
    log(db, "mail_settings", actor=me, target=f"mail:{l.id}", detail=f"active={l.active} drafts={l.auto_drafts} todo={l.auto_todo}")
    return {"link": M.link_out(db, l)}


@router.post("/run")
def run_now(db: Session = Depends(get_db), me: User = Depends(me_owner)):
    l = _link(db, me)
    got = M.run(db, l)
    return {**got, "link": M.link_out(db, l)}


@router.get("/items")
def items(category: str = "", q: str = "", limit: int = 200, db: Session = Depends(get_db), me: User = Depends(me_owner)):
    l = _link(db, me)
    qq = db.query(MailItem).filter(MailItem.link_id == l.id)
    if category == "drafts":
        qq = qq.filter(MailItem.draft_state == "saved")
    elif category in M.CATS:
        qq = qq.filter(MailItem.category == category)
    if q.strip():
        like = f"%{q.strip()[:80]}%"
        qq = qq.filter((MailItem.subject.ilike(like)) | (MailItem.sender.ilike(like)) | (MailItem.sender_name.ilike(like)) | (MailItem.summary.ilike(like)))
    rows = qq.order_by(MailItem.received_at.desc(), MailItem.id.desc()).limit(min(max(limit, 1), 500)).all()
    return {"items": [M.item_out(x) for x in rows]}


@router.post("/items/{iid}/draft")
def draft(iid: int, db: Session = Depends(get_db), me: User = Depends(me_owner)):
    from ..support import outlook as O

    l = _link(db, me)
    it = _item(db, l, iid)
    it.draft = M.write_draft(db, l, it, me)
    try:
        O.draft_reply(it.entry_id, it.draft)
        it.draft_state = "saved"
    except Exception as e:  # noqa: BLE001
        it.draft_state = "failed"
        raise HTTPException(409, f"The draft is written but Outlook didn't save it: {e}")
    log(db, "mail_draft", actor=me, target=f"mail:{l.id}", detail=it.subject[:150])
    return M.item_out(it)


@router.post("/items/{iid}/todo")
def todo(iid: int, db: Session = Depends(get_db), me: User = Depends(me_owner)):
    from ..cexa.tools_engine import give_task

    l = _link(db, me)
    it = _item(db, l, iid)
    acts = it.actions or [{"task": it.subject or it.summary[:100], "due": ""}]
    done = list(it.todos or [])
    for a in acts:
        title = f"{a['task']} — {it.sender_name or it.sender}"[:200]
        if title in done:
            continue
        res = give_task(db, me, None, to="me", title=title, steps=f"From the mail “{it.subject[:80]}”", due=a.get("due", ""))
        if res.startswith(("The To-do tool", "What is the task")) or "doesn't have" in res:
            raise HTTPException(409, res)
        done.append(title)
    it.todos = done
    return M.item_out(it)


@router.patch("/items/{iid}")
def recategorise(iid: int, body: ItemPatch, db: Session = Depends(get_db), me: User = Depends(me_owner)):
    from ..support import outlook as O

    l = _link(db, me)
    it = _item(db, l, iid)
    if body.category not in M.CATS:
        raise HTTPException(400, "Pick one of the categories.")
    it.category = body.category
    try:
        O.tag(it.entry_id, [f"CBX: {it.category}"], False)
    except Exception:  # noqa: BLE001 — the page still shows the new category
        pass
    return M.item_out(it)


# ---- rules -------------------------------------------------------------------------------------------------

@router.get("/rules")
def rules(db: Session = Depends(get_db), me: User = Depends(me_owner)):
    l = _link(db, me)
    return {"rules": [M.rule_out(r) for r in db.query(MailRule).filter(MailRule.link_id == l.id, MailRule.status != "declined").order_by(MailRule.id).all()]}


@router.post("/rules")
def add_rule(body: RuleIn, db: Session = Depends(get_db), me: User = Depends(me_owner)):
    l = _link(db, me)
    when = {k: v.strip()[:120] for k, v in (("sender", body.sender), ("subject", body.subject), ("category", body.category)) if v and v.strip()}
    if when.get("category") and when["category"] not in M.CATS:
        raise HTTPException(400, "Pick one of the categories.")
    if not when:
        raise HTTPException(400, "Say which mail: a sender, words in the subject, or a category.")
    then = {"category": body.then_category if body.then_category in M.CATS else "", "folder": body.folder.strip()[:80], "flag": body.flag, "no_draft": body.no_draft}
    if not (then["category"] or then["folder"] or then["flag"] or then["no_draft"]):
        raise HTTPException(400, "Say what happens to it.")
    if then["folder"] and not then["folder"].replace(" ", "").replace("-", "").isalnum():
        raise HTTPException(400, "A folder name: letters, numbers, spaces and dashes.")
    r = MailRule(link_id=l.id, name=body.name.strip()[:160] or " · ".join(f"{k} {v}" for k, v in when.items()), when=when, then=then, status="on", proposed_by="owner")
    db.add(r)
    db.flush()
    log(db, "mail_rule_added", actor=me, target=f"mail:{l.id}", detail=f"{r.name}: {then}")
    return M.rule_out(r)


@router.post("/rules/{rid}/{what}")
def rule_do(rid: int, what: str, db: Session = Depends(get_db), me: User = Depends(me_owner)):
    l = _link(db, me)
    r = db.get(MailRule, rid)
    if r is None or r.link_id != l.id:
        raise HTTPException(404, "No such rule.")
    new = {"approve": "on", "on": "on", "decline": "declined", "off": "off"}.get(what)
    if new is None:
        raise HTTPException(404, "No such action.")
    r.status = new
    log(db, "mail_rule_" + new, actor=me, target=f"mail:{l.id}", detail=r.name[:200])
    return M.rule_out(r)


@router.delete("/rules/{rid}")
def rule_delete(rid: int, db: Session = Depends(get_db), me: User = Depends(me_owner)):
    l = _link(db, me)
    r = db.get(MailRule, rid)
    if r is None or r.link_id != l.id:
        raise HTTPException(404, "No such rule.")
    db.delete(r)
    return {"ok": True}


# ---- templates ---------------------------------------------------------------------------------------------

@router.get("/templates")
def templates(db: Session = Depends(get_db), me: User = Depends(me_owner)):
    return {"templates": [M.template_out(t) for t in db.query(MailTemplate).filter(MailTemplate.user_id == me.id, MailTemplate.status != "off").order_by(MailTemplate.status.desc(), MailTemplate.name).all()]}


@router.post("/templates")
def add_template(body: TemplateIn, db: Session = Depends(get_db), me: User = Depends(me_owner)):
    if not body.name.strip() or not body.body.strip():
        raise HTTPException(400, "Give it a name and the text.")
    t = MailTemplate(user_id=me.id, name=body.name.strip()[:120], when=body.when.strip()[:300], body=body.body.strip()[:4000], status="on")
    db.add(t)
    db.flush()
    return M.template_out(t)


def _tpl(db: Session, me: User, tid: int) -> MailTemplate:
    t = db.get(MailTemplate, tid)
    if t is None or t.user_id != me.id:
        raise HTTPException(404, "No such template.")
    return t


@router.put("/templates/{tid}")
def edit_template(tid: int, body: TemplateIn, db: Session = Depends(get_db), me: User = Depends(me_owner)):
    t = _tpl(db, me, tid)
    t.name, t.when, t.body = body.name.strip()[:120] or t.name, body.when.strip()[:300], body.body.strip()[:4000] or t.body
    return M.template_out(t)


@router.post("/templates/{tid}/{what}")
def template_do(tid: int, what: str, db: Session = Depends(get_db), me: User = Depends(me_owner)):
    t = _tpl(db, me, tid)
    if what not in ("keep", "drop"):
        raise HTTPException(404, "No such action.")
    t.status = "on" if what == "keep" else "off"
    return M.template_out(t)


@router.delete("/templates/{tid}")
def delete_template(tid: int, db: Session = Depends(get_db), me: User = Depends(me_owner)):
    db.delete(_tpl(db, me, tid))
    return {"ok": True}
