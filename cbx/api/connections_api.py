"""API connections — the place where outside APIs (address + key) are attached to tools.

Admins manage them (a person; C-EXA is refused for changes). The key is write-only: it goes in, it is
stored sealed, and it never comes back out — the page sees a hint. Tool pages (an API source) and
flows (a Call an API step) use a connection from inside the server: CBX adds the key and makes the
request. (The browser relay `/call` went in 1.0.79: nothing used it; Test on the page checks a connection.)
"""
from __future__ import annotations

from typing import Optional


from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..connections import service as S
from ..connections.service import ConnectionError_
from ..db import get_db
from ..models import AUTH_KINDS, ApiConnection, User
from ..security.deps import require_admin, require_human

router = APIRouter(prefix="/api/connections", tags=["connections"])


def _get(db: Session, cid: int) -> ApiConnection:
    c = db.get(ApiConnection, cid)
    if c is None:
        raise HTTPException(404, "No such connection.")
    return c


@router.get("")
def list_connections(db: Session = Depends(get_db), me: User = Depends(require_admin)):
    rows = db.query(ApiConnection).order_by(ApiConnection.name).all()
    from ..engine import loader
    from .builder_api import drafts_root

    folders = {t.slug: t.text(t.manifest.get("title", "tool.title")) for t in loader.registry().all()}
    if drafts_root().is_dir():
        for d in drafts_root().iterdir():
            if (d / "tool.json").exists() and d.name not in folders:
                folders[d.name] = f"{d.name} (draft)"
    return {
        "connections": [S.describe(c, db) for c in rows],
        "tools": [{"slug": k, "name": v, "kind": "folder"} for k, v in sorted(folders.items(), key=lambda x: x[1].lower())],
        "auth_kinds": list(AUTH_KINDS),
        "how": "A tool folder uses a connection from a page (an API source) or a flow (Call an API) — CBX adds the key on the server. Only the tools ticked here may use it.",
    }


class ConnectionIn(BaseModel):
    name: str
    base_url: str
    auth_kind: str = "bearer"
    header_name: str = ""
    secret: str = ""
    description: str = ""
    extra_headers: Optional[dict] = None
    tools: Optional[list[str]] = None


@router.post("")
def add_connection(body: ConnectionIn, request: Request, db: Session = Depends(get_db), me: User = Depends(require_admin), _h: User = Depends(require_human)):
    try:
        c = S.create(db, me, name=body.name, base_url=body.base_url, auth_kind=body.auth_kind, header_name=body.header_name, secret=body.secret, description=body.description, extra_headers=body.extra_headers, tools=body.tools)
    except ConnectionError_ as e:
        raise HTTPException(400, str(e))
    return {"connection": S.describe(c, db)}


class ConnectionPatch(BaseModel):
    name: Optional[str] = None
    base_url: Optional[str] = None
    auth_kind: Optional[str] = None
    header_name: Optional[str] = None
    secret: Optional[str] = None
    description: Optional[str] = None
    extra_headers: Optional[dict] = None
    tools: Optional[list[str]] = None
    is_active: Optional[bool] = None


@router.patch("/{cid}")
def change_connection(cid: int, body: ConnectionPatch, db: Session = Depends(get_db), me: User = Depends(require_admin), _h: User = Depends(require_human)):
    c = _get(db, cid)
    try:
        S.update(db, c, me, **body.model_dump(exclude_unset=True))
    except ConnectionError_ as e:
        raise HTTPException(400, str(e))
    return {"connection": S.describe(c, db)}


@router.delete("/{cid}")
def delete_connection(cid: int, db: Session = Depends(get_db), me: User = Depends(require_admin), _h: User = Depends(require_human)):
    c = _get(db, cid)
    S.remove(db, c, me)
    return {"ok": True}


class TestIn(BaseModel):
    path: str = ""


@router.post("/{cid}/test")
def test_connection(cid: int, body: TestIn = TestIn(), db: Session = Depends(get_db), me: User = Depends(require_admin)):
    c = _get(db, cid)
    try:
        S._join(c.base_url, body.path)
    except ConnectionError_ as e:
        raise HTTPException(400, str(e))
    return {"result": S.test(db, c, me, path=body.path), "connection": S.describe(c, db)}
