"""Metrics: the numbers a dashboard tile can show.

A metric is a named Python function in Core that returns one number for the person asking — their
open requests, the documents due on the companies they may see, failed sign-ins in the last day. The
Number block (`metrics` on a page) lists metric keys; Home is a tool folder (`tools/home/`) whose pages
are rows of these tiles, one page per role; each person can rearrange their own. The Builder offers
this list.

Who may read a metric: `roles` (empty = anyone) and `tool` (the grant it follows, admins hold all).
A metric the person may not read is not shown to them at all — it does not show as zero.

    value(db, me) -> int | (int, sub)        sub is a short line under the number
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Callable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import User

GROUPS = ("My work", "PDF Studio", "Entity Compliance", "People & sign-in", "Requests & build", "System")


@dataclass
class Metric:
    key: str
    label: str
    group: str
    description: str
    fn: Callable
    roles: list = field(default_factory=list)
    tool: Optional[str] = None
    route: str = ""  # where the tile goes when clicked
    warn: bool = False  # more than zero is something to look at

    def describe(self) -> dict:
        return {"key": self.key, "label": self.label, "group": self.group, "description": self.description, "roles": self.roles, "tool": self.tool, "route": self.route, "warn": self.warn}


METRICS: dict[str, Metric] = {}
ADMIN_ROLES = ["owner", "admin"]


def metric(key: str, label: str, group: str, description: str, *, roles=None, tool=None, route="", warn=False):
    def deco(fn):
        METRICS[key] = Metric(key, label, group, description, fn, list(roles or []), tool, route, warn)
        return fn
    return deco


def may_read(db: Session, me: User, m: Metric) -> bool:
    if m.roles and me.role not in m.roles and not (me.is_admin and set(m.roles) & set(ADMIN_ROLES)):
        return False
    if m.tool:
        from ..security.deps import has_tool

        return has_tool(db, me, m.tool)
    return True


def available(db: Session, me: User) -> list[dict]:
    return [m.describe() for m in METRICS.values() if may_read(db, me, m)]


def values(db: Session, me: User, keys: list[str]) -> dict:
    """{key: {value, sub, tone, label, route}} for the keys the person may read; the rest are left out."""
    out: dict = {}
    for k in keys:
        m = METRICS.get(k)
        if m is None or k in out or not may_read(db, me, m):
            continue
        try:
            v = m.fn(db, me)
        except Exception as e:  # noqa: BLE001 — one broken number must not blank the dashboard
            out[k] = {"value": None, "sub": f"could not be counted ({type(e).__name__})", "tone": "warn", "label": m.label, "route": m.route}
            continue
        sub = ""
        if isinstance(v, tuple):
            v, sub = v
        tone = "warn" if m.warn and (v or 0) > 0 else ""
        out[k] = {"value": v, "sub": sub, "tone": tone, "label": m.label, "route": m.route}
    return out


def _since(days: float):
    return utcnow() - timedelta(days=days)


# ---- My work ------------------------------------------------------------------------------------


@metric("my.requests_open", "My open requests", "My work", "Requests you sent that are not ready or declined yet.", route="my-requests")
def _my_requests(db, me):
    from ..models import Request

    return db.query(Request).filter(Request.user_id == me.id, Request.origin != "admin", Request.status.notin_(["ready", "declined"])).count()


@metric("my.messages_unread", "Unread messages", "My work", "Messages from colleagues and the AI you have not opened.", route="messages", warn=True)
def _my_messages(db, me):
    from ..chat.service import unread_total
    from ..models import CexaMessage

    ai = db.query(CexaMessage).filter(CexaMessage.user_id == me.id, CexaMessage.from_axl.is_(True), CexaMessage.read.is_(False), CexaMessage.cleared.is_(False)).count()
    return ai + unread_total(db, me)


@metric("my.todo_open", "My open tasks", "My work", "To-do tasks you hold: your own, and team tasks assigned to you.", tool="todo", route="todo")
def _my_todo(db, me):
    from sqlalchemy import or_

    from ..models import TodoJob, TodoTask
    from ..todo.copy_in import open_for

    n = open_for(db, me)  # the To-do tool folder (1.0.55), when it is there
    if n is not None:
        return n
    return db.query(TodoTask).outerjoin(TodoJob, TodoJob.id == TodoTask.job_id).filter(TodoTask.assignee_id == me.id, TodoTask.status == "open", or_(TodoTask.job_id.is_(None), TodoJob.status == "active")).count()


@metric("flows.waiting_for_me", "Approvals waiting for you", "My work", "Flows that have stopped to ask you to approve something.", route="runs", warn=True)
def _approvals(db, me):
    from ..models import FlowRun

    return sum(1 for r in db.query(FlowRun).filter(FlowRun.status == "waiting", FlowRun.dry.is_(False)).all()
               if (r.waiting or {}).get("kind") == "approval" and me.id in ((r.waiting or {}).get("approvers") or []))


# ---- PDF Studio ---------------------------------------------------------------------------------


@metric("pdf.running", "Batches reading", "PDF Studio", "Your PDF batches being read right now.", tool="pdf-studio", route="pdf-studio")
def _pdf_running(db, me):
    from ..models import Batch

    return db.query(Batch).filter(Batch.user_id == me.id, Batch.status.in_(["learning", "running", "uploading"])).count()


@metric("pdf.to_check", "Cells for you to check", "PDF Studio", "Cells in your finished batches that could not be read with confidence.", tool="pdf-studio", route="pdf-studio", warn=True)
def _pdf_cells(db, me):
    from ..models import Batch

    n = db.query(func.coalesce(func.sum(Batch.cells_for_user), 0)).filter(Batch.user_id == me.id, Batch.status == "review").scalar() or 0
    b = db.query(Batch).filter(Batch.user_id == me.id, Batch.status == "review").count()
    return int(n), (f"in {b} batch{'es' if b != 1 else ''}" if b else "")


@metric("pdf.pages_month", "Pages read this month", "PDF Studio", "Pages in the PDFs you sent to PDF Studio since the 1st.", tool="pdf-studio", route="pdf-studio")
def _pdf_pages(db, me):
    from ..models import Batch, Document

    start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return int(db.query(func.coalesce(func.sum(Document.pages), 0)).join(Batch).filter(Batch.user_id == me.id, Batch.created_at >= start).scalar() or 0)


@metric("sheets.rows", "Master sheet rows", "PDF Studio", "Rows you can see across the master sheets.", tool="master-sheets", route="master-sheets")
def _sheet_rows(db, me):
    from ..models import MasterSheet
    from ..security.rls import filter_rows

    sheets = db.query(MasterSheet).filter(MasterSheet.is_active.is_(True)).all()
    return sum(len(filter_rows(db, me, "master_rows", s.rows or [])) for s in sheets), f"{len(sheets)} sheet{'s' if len(sheets) != 1 else ''}"


@metric("sheets.exceptions", "Master sheet exceptions", "PDF Studio", "Rows held back from the master sheets because a field is missing or the figures don't add up.", tool="master-sheets", route="master-sheets", warn=True)
def _sheet_exc(db, me):
    from ..models import MasterSheet
    from ..security.rls import filter_rows

    return sum(len(filter_rows(db, me, "master_rows", s.exceptions or [])) for s in db.query(MasterSheet).filter(MasterSheet.is_active.is_(True)).all())


@metric("harvest.sources", "Harvest folders watched", "PDF Studio", "Folders PDF Studio reads by itself, all day.", tool="pdf-studio", route="pdf-harvest")
def _harvest(db, me):
    from ..models import HarvestSource

    q = db.query(HarvestSource).filter(HarvestSource.active.is_(True))
    if not me.is_admin:
        q = q.filter(HarvestSource.owner_id == me.id)
    return q.count()


# ---- Entity Compliance --------------------------------------------------------------------------


@metric("compliance.due", "Documents due or expired", "Entity Compliance", "Required documents on the companies you can see that have expired or fall inside their warning window.", tool="doc-watch", route="doc-watch", warn=True)
def _due(db, me):
    from ..docwatch.service import due_count

    return due_count(db, me)


@metric("compliance.companies", "Companies you can see", "Entity Compliance", "Company entities on the grid for you.", tool="doc-watch", route="doc-watch")
def _companies(db, me):
    from ..docwatch.copy_in import use_tool
    from ..docwatch.service import visible_entities

    if use_tool(db):
        from ..docwatch.tool import companies_count

        return companies_count(db, me)
    return len(visible_entities(db, me))


# ---- People & sign-in (owners, admins) ----------------------------------------------------------


@metric("people.active", "Active accounts", "People & sign-in", "People who can sign in (not disabled, not the AI).", roles=ADMIN_ROLES, route="users")
def _people(db, me):
    return db.query(User).filter(User.is_axl.is_(False), User.is_active.is_(True)).count()


@metric("people.seen_7d", "Signed in this week", "People & sign-in", "People seen in the last 7 days.", roles=ADMIN_ROLES, route="users")
def _seen(db, me):
    return db.query(User).filter(User.is_axl.is_(False), User.last_seen >= _since(7)).count()


@metric("people.locked", "Locked accounts", "People & sign-in", "Accounts locked by wrong passwords or wrong codes — waiting for you to unlock.", roles=ADMIN_ROLES, route="users", warn=True)
def _locked(db, me):
    return db.query(User).filter(User.is_axl.is_(False), User.locked.is_(True)).count()


@metric("people.no_two_step", "Without two-step", "People & sign-in", "Active people who have not set up two-step verification yet.", roles=ADMIN_ROLES, route="users", warn=True)
def _no2(db, me):
    return db.query(User).filter(User.is_axl.is_(False), User.is_active.is_(True), (User.mfa_method == "") | (User.mfa_method.is_(None))).count()


@metric("people.ending_30d", "Access ending in 30 days", "People & sign-in", "Accounts with an end date in the next 30 days — outside people whose access runs out.", roles=ADMIN_ROLES, route="users")
def _ending(db, me):
    now = utcnow()
    return db.query(User).filter(User.expires_at.isnot(None), User.expires_at > now, User.expires_at <= now + timedelta(days=30)).count()


@metric("signin.failed_24h", "Failed sign-ins today", "People & sign-in", "Wrong passwords in the last 24 hours, from any address.", roles=ADMIN_ROLES, route="audit", warn=True)
def _failed(db, me):
    from ..models import AuditLog

    return db.query(AuditLog).filter(AuditLog.action == "login_failed", AuditLog.at >= _since(1)).count()


@metric("signin.unregistered_7d", "Unknown addresses trying", "People & sign-in", "Different email addresses that tried to sign in this week and are not accounts here.", roles=ADMIN_ROLES, route="audit", warn=True)
def _unknown(db, me):
    from ..models import AuditLog

    emails = {(r[0] or "").lower() for r in db.query(AuditLog.actor_label).filter(AuditLog.action == "login_failed", AuditLog.at >= _since(7)).distinct()} - {""}
    known = {(r[0] or "").lower() for r in db.query(User.email).filter(func.lower(User.email).in_(emails))} if emails else set()
    return len(emails - known)


# ---- Requests & build (owners, admins) ----------------------------------------------------------


@metric("requests.new", "New requests", "Requests & build", "Requests nobody has picked up yet.", roles=ADMIN_ROLES, route="req-queue", warn=True)
def _new_requests(db, me):
    from ..models import Request

    return db.query(Request).filter(Request.status == "new").count()


@metric("requests.open", "Requests in progress", "Requests & build", "Requests being worked on or tested.", roles=ADMIN_ROLES, route="req-queue")
def _open_requests(db, me):
    from ..models import Request

    return db.query(Request).filter(Request.status.notin_(["new", "ready", "declined"])).count()


@metric("build.tickets_open", "Open tickets", "Requests & build", "Build tickets not ready yet.", roles=ADMIN_ROLES, route="develop")
def _tickets(db, me):
    from ..models import Ticket

    return db.query(Ticket).filter(Ticket.status != "ready").count()


# ---- System (owner) -----------------------------------------------------------------------------


@metric("system.attention", "Things to look at", "System", "Problems the server noticed: stuck batches, a harvest that stopped, the model or Node-RED down.", roles=ADMIN_ROLES, route="home", warn=True)
def _attention(db, me):
    from ..api.home import attention_items

    items = [i for i in attention_items(db, me) if i.get("level") in ("crit", "warn")]
    return len(items)


@metric("flows.failed_7d", "Flow runs that failed", "System", "Runs of any flow that stopped with a problem in the last 7 days.", roles=ADMIN_ROLES, route="runs", warn=True)
def _flows_failed(db, me):
    from ..models import FlowRun

    return db.query(FlowRun).filter(FlowRun.status == "failed", FlowRun.dry.is_(False), FlowRun.started_at >= _since(7)).count()


@metric("flows.runs_today", "Flow runs today", "System", "Times any flow ran since midnight.", roles=ADMIN_ROLES, route="runs")
def _flows_today(db, me):
    from ..models import FlowRun
    from .flows import local_now

    midnight = local_now().replace(hour=0, minute=0, second=0, microsecond=0)
    return db.query(FlowRun).filter(FlowRun.dry.is_(False), FlowRun.started_at >= midnight).count()


@metric("system.versions_waiting", "Versions waiting", "System", "Uploaded versions that passed their tests and are not promoted yet.", roles=["owner"], route="versions")
def _versions(db, me):
    from ..models import AppVersion

    return db.query(AppVersion).filter(AppVersion.status == "sandbox").count()
