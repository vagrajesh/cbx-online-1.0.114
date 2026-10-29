"""Other systems reading our data with a key (1.0.59 — the Connector-expose half of Connections).

A tool folder's table says which of its columns other systems may read (data/<table>.expose, set in the
Builder: "Other systems may read it, with a key"). The owner makes a key for one such table, for one
system, with a limit of calls a minute, an end date (90 days unless said; a year at most) and, if wanted,
the addresses it may come from. The key is shown once; only its SHA-256 is kept.

    GET /api/shared/<tool>/<table>?limit=100&offset=0&after_id=0
    Authorization: Bearer cbxk_…        (or  X-CBX-Key: cbxk_…)

Reading only, the exposed columns only, every row of the table (a key is not a person, so "each person
their own" does not narrow it — the Builder says so when the table is ticked). Never a file. A key is
never taken from the address (it would sit in logs) and never from a signed-in browser: this door takes
keys and nothing else. Over plain HTTP it is refused unless the call comes from this machine
(CBX_DATA_KEYS_HTTPS_ONLY). Wrong keys are counted per address: 20 in ten minutes and that address waits.
"""
from __future__ import annotations

import hashlib
import ipaddress
import secrets
import threading
import time
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..audit import log
from ..config import settings
from ..db import get_db, utcnow
from ..engine import loader
from ..engine.store import Store
from ..models import DataKey, User
from ..security import ratelimit
from ..security.deps import client_ip, is_https, require_admin, require_human, require_owner

router = APIRouter(tags=["data keys"])

PREFIX = "cbxk_"
MAX_DAYS, DEFAULT_DAYS = 365, 90
MAX_RATE = 600
BAD_KEYS, BAD_WINDOW = 20, 600
LOCAL = {"127.0.0.1", "::1", "localhost"}
_AUDIT_EVERY = 600  # a key's good reads are written to the audit trail at most once in ten minutes
_last_audit: dict[int, float] = {}
_audit_lock = threading.Lock()


def digest(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def exposed(t, table: str) -> list[str]:
    """The columns of a table other systems may read, as the folder says now ([] = none)."""
    d = (t.data or {}).get(table) if t else None
    ex = (d or {}).get("expose") if isinstance(d, dict) else None
    cols = {c.get("key"): c for c in (d or {}).get("columns") or [] if isinstance(c, dict)}
    return [k for k in (ex or {}).get("columns") or [] if k in cols and cols[k].get("type") != "file"]


def exposable(reg=None) -> list[dict]:
    out = []
    for t in sorted((reg or loader.registry()).tools.values(), key=lambda x: x.slug):  # sound folders only
        for name, d in (t.data or {}).items():
            keys = exposed(t, name)
            if keys:
                cols = {c["key"]: c for c in d.get("columns") or [] if isinstance(c, dict) and c.get("key")}
                out.append({"tool": t.slug, "tool_name": t.text(t.manifest.get("title", "tool.title")), "table": name,
                            "columns": [{"key": k, "label": t.text(cols[k].get("label") or k)} for k in keys],
                            "scope": d.get("scope", "all")})
    return sorted(out, key=lambda x: (x["tool_name"].lower(), x["table"]))


def _ip_ok(ip: str, allowed: list) -> bool:
    if not allowed:
        return True
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for n in allowed:
        try:
            if a in ipaddress.ip_network(str(n), strict=False):
                return True
        except ValueError:
            continue
    return False


def _refuse(db: Session, request: Request, ip: str, status: int, why: str, target: str, k: Optional[DataKey] = None):
    """A refused call: counted against the address, written down while the address is under the limit."""
    try:
        ratelimit.check(f"datakey-bad:{ip}", limit=BAD_KEYS, window=BAD_WINDOW, what="wrong keys")
    except ratelimit.TooMany as e:
        return JSONResponse({"detail": e.message}, status_code=429, headers={"Retry-After": str(e.retry_after)})
    log(db, "data_key_refused", actor_label=f"key {k.prefix}…" if k else "no key", target=target, detail=why, outcome="blocked", ip=ip)
    db.commit()
    return JSONResponse({"detail": why}, status_code=status)


@router.get("/api/shared/{tool}/{table}")
def read_shared(tool: str, table: str, request: Request, limit: int = 100, offset: int = 0, after_id: int = 0, db: Session = Depends(get_db)):
    ip = client_ip(request)
    target = f"{tool}/{table}"
    if ratelimit.peek(f"datakey-bad:{ip}", window=BAD_WINDOW) >= BAD_KEYS:
        return JSONResponse({"detail": "Too many wrong keys from this address. Try again later."}, status_code=429, headers={"Retry-After": str(BAD_WINDOW)})
    if settings.data_keys_https_only and not is_https(request) and ip not in LOCAL:
        return _refuse(db, request, ip, 403, "Keys are taken over HTTPS only. Use the https:// address of the server.", target)
    auth = request.headers.get("authorization", "")
    key = auth[7:].strip() if auth.lower().startswith("bearer ") else request.headers.get("x-cbx-key", "").strip()
    if not key.startswith(PREFIX) or len(key) > 200:
        return _refuse(db, request, ip, 401, "A key is needed: Authorization: Bearer cbxk_… (or X-CBX-Key).", target)
    k = db.query(DataKey).filter(DataKey.key_hash == digest(key)).first()
    now = utcnow()
    if k is None:
        return _refuse(db, request, ip, 401, "That key is not one of ours.", target)
    if k.revoked_at is not None:
        return _refuse(db, request, ip, 401, "That key was revoked.", target, k)
    if k.expires_at <= now:
        return _refuse(db, request, ip, 401, "That key has expired. Ask the owner for a new one.", target, k)
    if (k.tool, k.table) != (tool, table):
        return _refuse(db, request, ip, 403, "That key is for another table.", target, k)
    if not _ip_ok(ip, k.allowed_ips or []):
        return _refuse(db, request, ip, 403, "That key may not be used from this address.", target, k)
    t = loader.get(tool)
    keys = exposed(t, table)
    if not keys:
        return _refuse(db, request, ip, 404, "That table is not open to other systems (any more).", target, k)
    try:
        ratelimit.check(f"datakey:{k.id}", limit=max(1, int(k.rate_per_min or 60)), window=60, what="calls")
    except ratelimit.TooMany as e:
        return JSONResponse({"detail": f"This key may make {k.rate_per_min} calls a minute. Try again in {e.retry_after} seconds."}, status_code=429, headers={"Retry-After": str(e.retry_after)})

    d = t.data[table]
    columns = d.get("columns") or []
    lim = max(1, min(int(limit), 1000))
    st = Store(tool)
    r = st.rows(table, columns, sort="id", desc=False, limit=lim, offset=max(0, int(offset)), after_id=max(0, int(after_id)), tables=t.data)
    from .engine_api import label_people

    label_people(db, r["rows"], columns)
    types = {c["key"]: c.get("type", "text") for c in columns if isinstance(c, dict)}
    rows = []
    for row in r["rows"]:
        out = {"id": row["id"], "added": row.get("created_at")}
        for kk in keys:
            v = row.get(kk)
            if types.get(kk) in ("person", "link") and v is not None:
                v = (row.get("_labels") or {}).get(kk, v)  # a name or a label: ids mean nothing outside
            out[kk] = v
        rows.append(out)
    k.calls = (k.calls or 0) + 1
    k.last_used_at, k.last_ip = now, ip[:64]
    with _audit_lock:
        due = time.monotonic() - _last_audit.get(k.id, -1e9) >= _AUDIT_EVERY
        if due:
            _last_audit[k.id] = time.monotonic()
    if due:
        log(db, "data_key_read", actor_label=f"key {k.prefix}… ({k.name})", target=target, detail=f"{len(rows)} row(s); {k.calls} call(s) so far", ip=ip)
    db.commit()
    return {"tool": tool, "table": table, "columns": keys, "total": r["total"], "rows": rows,
            "next_after_id": rows[-1]["id"] if rows else max(0, int(after_id))}


# ---- the owner's side ---------------------------------------------------------------------------

def describe(k: DataKey, db: Session) -> dict:
    now = utcnow()
    who = db.get(User, k.created_by_id) if k.created_by_id else None
    state = "revoked" if k.revoked_at else "expired" if k.expires_at <= now else "active"
    return {"id": k.id, "name": k.name, "tool": k.tool, "table": k.table, "prefix": k.prefix, "rate_per_min": k.rate_per_min,
            "allowed_ips": k.allowed_ips or [], "expires_at": k.expires_at.isoformat(), "state": state,
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None, "last_ip": k.last_ip, "calls": k.calls or 0,
            "created_by": who.name if who else "", "created_at": k.created_at.isoformat() if k.created_at else None}


@router.get("/api/data-keys")
def list_keys(request: Request, db: Session = Depends(get_db), me: User = Depends(require_admin)):
    base = str(request.base_url).rstrip("/")
    return {"keys": [describe(k, db) for k in db.query(DataKey).order_by(DataKey.id.desc()).all()],
            "tables": exposable(), "address": f"{base}/api/shared/<tool>/<table>", "can_make": me.role == "owner",
            "https_only": settings.data_keys_https_only}


class KeyIn(BaseModel):
    name: str
    tool: str
    table: str
    days: int = DEFAULT_DAYS
    rate_per_min: int = 60
    allowed_ips: list[str] = []


@router.post("/api/data-keys")
def make_key(body: KeyIn, request: Request, db: Session = Depends(get_db), me: User = Depends(require_owner), _h: User = Depends(require_human)):
    name = (body.name or "").strip()[:120]
    if not name:
        raise HTTPException(400, "Say who the key is for (e.g. Finance system).")
    if not exposed(loader.get(body.tool), body.table):
        raise HTTPException(400, "That table is not open to other systems. Tick it in the Builder first (Other systems may read it).")
    if not 1 <= int(body.days) <= MAX_DAYS:
        raise HTTPException(400, f"A key lasts 1 to {MAX_DAYS} days.")
    if not 1 <= int(body.rate_per_min) <= MAX_RATE:
        raise HTTPException(400, f"1 to {MAX_RATE} calls a minute.")
    ips = [x.strip() for x in body.allowed_ips if x and x.strip()][:20]
    for x in ips:
        try:
            ipaddress.ip_network(x, strict=False)
        except ValueError:
            raise HTTPException(400, f"'{x}' is not an address (e.g. 10.0.0.5 or 10.0.0.0/24).")
    key = PREFIX + secrets.token_urlsafe(32)
    k = DataKey(name=name, tool=body.tool, table=body.table, key_hash=digest(key), prefix=key[:12], rate_per_min=int(body.rate_per_min),
                allowed_ips=ips, expires_at=utcnow() + timedelta(days=int(body.days)), created_by_id=me.id)
    db.add(k)
    db.flush()
    log(db, "data_key_made", actor=me, target=f"{body.tool}/{body.table}",
        detail=f"'{name}', {body.days} days, {body.rate_per_min} a minute" + (f", from {', '.join(ips)}" if ips else ", from any address"), ip=client_ip(request))
    return {"key": key, "shown_once": True, "data_key": describe(k, db)}


@router.post("/api/data-keys/{kid}/revoke")
def revoke_key(kid: int, request: Request, db: Session = Depends(get_db), me: User = Depends(require_admin), _h: User = Depends(require_human)):
    k = db.get(DataKey, kid)
    if k is None:
        raise HTTPException(404, "No such key.")
    if k.revoked_at is None:
        k.revoked_at, k.revoked_by_id = utcnow(), me.id
        log(db, "data_key_revoked", actor=me, target=f"{k.tool}/{k.table}", detail=f"'{k.name}' ({k.prefix}…)", ip=client_ip(request))
    return {"data_key": describe(k, db)}
