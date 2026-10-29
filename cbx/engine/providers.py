"""Providers: the data a page can show that is not one of the tool's own tables.

A provider is a named Python function in Core that returns one of two plain shapes — a grid or a
table — from data the app already has. It is how an existing screen (the Entity Compliance grid) is
drawn by the engine without its logic being rewritten, and how a page can list something Core keeps
(sign-in attempts) without the tool owning that table. The Builder offers this list.

    grid:  {"columns": [{"id", "label", "title"}], "rows": [{"id", "label", "link", "cells": {col_id: {"dot", "title", "link"}}, "summary"}],
            "summary": {...}, "legend": [{"dot", "label"}]}
    table: {"columns": [{"key", "label", "type"}], "rows": [{...}], "total": n}

A provider's `roles` say who may read it at all (empty = anyone the tool lets in); the data itself
is still filtered by the person, exactly as the original screen filters it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import AuditLog, User
from ..util import clip


@dataclass
class Provider:
    name: str
    kind: str  # grid | table
    title: str
    description: str
    fn: Callable
    columns: list = field(default_factory=list)  # table providers: what a page may show
    filters: list = field(default_factory=list)  # [{key, label, type, options}]
    roles: list = field(default_factory=list)
    tool: str = ""  # a grant the person must hold (admins hold all); empty = none

    def describe(self) -> dict:
        return {"name": self.name, "kind": self.kind, "title": self.title, "description": self.description, "columns": self.columns, "filters": self.filters, "roles": self.roles, "tool": self.tool}


PROVIDERS: dict[str, Provider] = {}


def provider(name: str, kind: str, title: str, description: str, *, columns=None, filters=None, roles=None, tool=""):
    def deco(fn):
        PROVIDERS[name] = Provider(name, kind, title, description, fn, list(columns or []), list(filters or []), list(roles or []), tool)
        return fn
    return deco


def _link(route: str, **params) -> dict:
    return {"route": route, "params": params}


# ---- Entity Compliance ----------------------------------------------------------------------------


@provider("compliance.grid", "grid", "Entity Compliance grid", "Companies down the side, document types across the top, one dot per cell — what the person may see, worst company first.")
def compliance_grid(db: Session, me: User, params: dict) -> dict:
    from ..docwatch import service as S

    g = S.grid(db, me, demo=bool(params.get("training")))
    cols = [{"id": str(t["id"]), "label": t["name"], "title": t["name"] + (f" · warns {t['warn_days']} days before" if t.get("warn_days") else "")} for t in g["types"]]
    rows = []
    for e in g["entities"]:
        cells = {}
        for c in e["cells"]:
            d = c.get("doc")
            cells[str(c["type_id"])] = {"dot": d["dot"] if d else "open", "title": d["dot_title"] if d else "Not set up yet", "link": _link("doc-watch-entity", id=e["id"], doc=d["id"]) if d else _link("doc-watch-entity", id=e["id"])}
        rows.append({"id": e["id"], "label": e["name"], "sub": e.get("team") or "", "link": _link("doc-watch-entity", id=e["id"]), "cells": cells, "summary": e["summary"], "worst": e["summary"]["worst"]})
    s = g["summary"]
    return {"columns": cols, "rows": rows,
            "summary": {"companies": s["companies"], "expired": s["expired"], "expiring": s["expiring"], "outstanding": s["outstanding"], "in_date": s["in_date"]},
            "legend": [{"dot": "ok", "label": "legend.in_date"}, {"dot": "warn", "label": "legend.expiring"}, {"dot": "bad", "label": "legend.expired"}, {"dot": "open", "label": "legend.outstanding"}, {"dot": "grey", "label": "legend.not_required"}]}


@provider("compliance.documents", "table", "Entity Compliance documents", "Every live certificate, permit and contract the person may see, one row each, with its expiry and status.",
          columns=[{"key": "entity", "label": "col.entity", "type": "text"}, {"key": "doc_type", "label": "col.doc_type", "type": "text"}, {"key": "title", "label": "col.title", "type": "text"},
                   {"key": "expiry", "label": "col.expiry", "type": "date"}, {"key": "days_left", "label": "col.days_left", "type": "int"}, {"key": "status", "label": "col.status", "type": "text"},
                   {"key": "responsible", "label": "col.responsible", "type": "text"}, {"key": "responsible_email", "label": "col.responsible_email", "type": "text"}],
          filters=[{"key": "status", "label": "col.status", "type": "choice", "options": ["expired", "expiring", "ok", "needs_date", "missing", "no_expiry", "not_required"]}])
def compliance_documents(db: Session, me: User, params: dict) -> dict:
    from ..docwatch import service as S

    from ..models.docs import Entity

    g = S.grid(db, me)
    ents = {e.id: e for e in db.query(Entity).filter(Entity.id.in_({d["entity_id"] for d in g["docs"] if d.get("entity_id")} or {0})).all()}
    who = lambda d: ents.get(d.get("entity_id"))  # noqa: E731 — the company's responsible person (a flow emails them)
    rows = [{"id": d["id"], "entity": d["entity"], "doc_type": d["doc_type"], "title": d["title"], "expiry": d["expiry"], "days_left": d["days_left"], "status": d["status"], "dot": d["dot"],
             "responsible": (who(d).responsible if who(d) else "") or "", "responsible_email": (who(d).responsible_email if who(d) else "") or "",
             "link": _link("doc-watch-entity", id=d["entity_id"], doc=d["id"])} for d in g["docs"]]
    st = params.get("status")
    if st:
        rows = [r for r in rows if r["status"] == st]
    return {"columns": PROVIDERS["compliance.documents"].columns, "rows": rows, "total": len(rows)}


# ---- Core: sign-in attempts -----------------------------------------------------------------------


@provider("core.sign_in_attempts", "table", "Sign-in attempts", "Failed sign-ins from the audit trail, one row per email address: how often, the last time, from where, and whether that address belongs to a registered account.",
          columns=[{"key": "email", "label": "col.email", "type": "text"}, {"key": "attempts", "label": "col.attempts", "type": "int"}, {"key": "last_at", "label": "col.last_at", "type": "date"},
                   {"key": "last_ip", "label": "col.last_ip", "type": "text"}, {"key": "registered", "label": "col.registered", "type": "bool"}],
          filters=[{"key": "registered", "label": "col.registered", "type": "choice", "options": ["no", "yes"]}, {"key": "days", "label": "filter.days", "type": "int"}],
          roles=["owner", "admin"])
def sign_in_attempts(db: Session, me: User, params: dict) -> dict:
    from datetime import timedelta

    from ..db import utcnow

    q = db.query(AuditLog.actor_label, func.count(AuditLog.id), func.max(AuditLog.at)).filter(AuditLog.action == "login_failed", AuditLog.actor_label != "")
    try:
        days = int(params.get("days") or 0)
    except (TypeError, ValueError):
        days = 0
    if days > 0:
        q = q.filter(AuditLog.at >= utcnow() - timedelta(days=days))
    grouped = q.group_by(AuditLog.actor_label).all()
    emails = {e for e, _, _ in grouped}
    known = {u.email.lower() for u in db.query(User).filter(func.lower(User.email).in_([e.lower() for e in emails] or [""])).all()}
    rows = []
    for email, n, last in grouped:
        last_row = db.query(AuditLog).filter(AuditLog.action == "login_failed", AuditLog.actor_label == email).order_by(AuditLog.at.desc()).first()
        rows.append({"id": email, "email": email, "attempts": int(n), "last_at": last.isoformat() if last else None, "last_ip": (last_row.ip if last_row else "") or "", "registered": email.lower() in known})
    want = str(params.get("registered") or "").lower()
    if want in ("yes", "true", "1"):
        rows = [r for r in rows if r["registered"]]
    elif want in ("no", "false", "0"):
        rows = [r for r in rows if not r["registered"]]
    rows.sort(key=lambda r: (r["last_at"] or ""), reverse=True)
    return {"columns": PROVIDERS["core.sign_in_attempts"].columns, "rows": rows, "total": len(rows)}


# ---- for dashboards (1.0.46) -----------------------------------------------------------------------


@provider("core.my_requests", "table", "My requests", "The person's own requests, newest first, with where each one is.",
          columns=[{"key": "number", "label": "col.number", "type": "text"}, {"key": "title", "label": "col.title", "type": "text"},
                   {"key": "status", "label": "col.status", "type": "text", "format": "status"}, {"key": "created_at", "label": "col.created_at", "type": "date"}])
def my_requests(db: Session, me: User, params: dict) -> dict:
    from ..models import Request

    rs = db.query(Request).filter(Request.user_id == me.id, Request.origin != "admin").order_by(Request.created_at.desc()).limit(50).all()
    rows = [{"id": r.id, "number": r.number, "title": r.title or clip(r.problem or "", 80), "status": r.status, "created_at": r.created_at.isoformat() if r.created_at else None,
             "link": _link("req-detail", id=r.id)} for r in rs]
    return {"columns": PROVIDERS["core.my_requests"].columns, "rows": rows, "total": len(rows)}


@provider("core.my_batches", "table", "My PDF batches", "The person's PDF Studio batches, newest first: how far each one is and how many cells wait for them.",
          columns=[{"key": "name", "label": "col.name", "type": "text"}, {"key": "status", "label": "col.status", "type": "text"},
                   {"key": "docs", "label": "col.docs", "type": "int"}, {"key": "for_you", "label": "col.for_you", "type": "int"}, {"key": "created_at", "label": "col.created_at", "type": "date"}],
          tool="pdf-studio")
def my_batches(db: Session, me: User, params: dict) -> dict:
    from ..models import Batch
    from ..security.deps import has_tool

    if not has_tool(db, me, "pdf-studio"):
        return {"columns": PROVIDERS["core.my_batches"].columns, "rows": [], "total": 0}
    bs = db.query(Batch).filter(Batch.user_id == me.id).order_by(Batch.created_at.desc()).limit(30).all()
    rows = [{"id": b.id, "name": b.name or f"Batch {b.id}", "status": b.status, "docs": len(b.documents), "for_you": b.cells_for_user or 0,
             "created_at": b.created_at.isoformat() if b.created_at else None, "link": _link("pdf-studio", batch=b.id)} for b in bs]
    return {"columns": PROVIDERS["core.my_batches"].columns, "rows": rows, "total": len(rows)}


@provider("core.attention", "table", "Things to look at", "What the server noticed and nobody has dealt with: stuck batches, a harvest that stopped, documents expiring, the model or Node-RED down.",
          columns=[{"key": "level", "label": "col.level", "type": "text"}, {"key": "text", "label": "col.text", "type": "text"}],
          roles=["owner", "admin"])
def attention(db: Session, me: User, params: dict) -> dict:
    from ..api.home import attention_items

    words = {"crit": "Urgent", "warn": "Look at", "info": "For your information"}
    rows = [{"id": i.get("key") or str(n), "level": words.get(i.get("level"), i.get("level")), "text": i.get("text", ""),
             "link": _link(i["go"]) if i.get("go") else None} for n, i in enumerate(attention_items(db, me))]
    return {"columns": PROVIDERS["core.attention"].columns, "rows": rows, "total": len(rows)}


def describe_all() -> list[dict]:
    return [p.describe() for p in sorted(PROVIDERS.values(), key=lambda p: p.name)]
