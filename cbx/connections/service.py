"""API connections: outside services (address + key) that tools and workflows call through CBX.

    create / update / remove       — admin work, every change audited, the key never in a log line
    describe(c)                    — what the page may see: everything but the key (a hint of it)
    call(db, c, method, path, …)   — CBX adds the key and makes the request; the caller (a flow mid-run,
                                     a tool page) never holds it
    test(db, c, by)                — one GET at the address, to see it answers

Guard rails: the path a caller asks for is joined to the connection's base URL — it can't name another
host — and the relay only serves a connection that is switched on and attached to the calling tool.
"""
from __future__ import annotations

import os
import base64
import json
import re
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx
from sqlalchemy.orm import Session

from ..audit import log
from ..config import settings
from ..db import utcnow
from ..models import AUTH_KINDS, ApiConnection, Tool, User
from ..security import secretbox

TIMEOUT = 30.0
MAX_BODY = 512 * 1024  # what the relay hands back at most
HEADER_OK = re.compile(r"^[A-Za-z0-9-]{1,80}$")
BLOCKED_REQUEST_HEADERS = {"authorization", "cookie", "host", "content-length", "connection", "transfer-encoding", "proxy-authorization"}


class ConnectionError_(Exception):
    pass


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48] or "api"


def _check_url(url: str) -> str:
    u = (url or "").strip()
    p = urlsplit(u)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise ConnectionError_("The address must start with http:// or https:// and name a server, e.g. https://api.example.com/v1.")
    if "@" in p.netloc:
        raise ConnectionError_("Put the key in the key field, not in the address.")
    host = (p.hostname or "").lower()
    why = host_refused(host)
    if why:
        raise ConnectionError_(why)
    return u.rstrip("/")


def private_hosts() -> set[str]:
    """CBX_API_PRIVATE_HOSTS: named company hosts allowed to be on the private network (1.0.86)."""
    return {h.strip().lower() for h in (settings.api_private_hosts or "").split(",") if h.strip()}


def host_refused(host: str) -> str:
    """Why this host may not be the target of a connection — or "" when it may.

    The relay calls out with the server's own network position, so a connection pointing back at this
    machine or its neighbours would let a workflow reach Ollama (:11434), the old Node-RED port (:1880,
    which runs code), or a cloud host's metadata address — none of which is an "API connection". Loopback
    and link-local addresses are always refused; the company's own private ranges (10.x, 172.16–31.x,
    192.168.x) only when CBX_API_ALLOW_PRIVATE is on, for an internal API someone really means to call.
    Names are resolved here so a DNS name for 127.0.0.1 is caught the same as the literal."""
    import ipaddress
    import socket

    if not host:
        return "The address must name a server."
    if host in ("localhost",) or host.endswith((".localhost", ".local", ".internal")):
        return "That address is this machine or its own network — a connection points at an outside service."
    addrs: list[str] = []
    try:
        ipaddress.ip_address(host)
        addrs = [host]
    except ValueError:
        try:
            addrs = sorted({ai[4][0] for ai in socket.getaddrinfo(host, None)})
        except socket.gaierror:
            return ""  # unknown name today: the call itself will fail with a plain 'could not connect'
    for a in addrs:
        try:
            ip = ipaddress.ip_address(a.split("%")[0])
        except ValueError:
            continue
        if ip.is_loopback and os.environ.get("CBX_TESTING") and settings.api_allow_private:
            continue  # a test run's fake service lives on loopback; nowhere else is this door open
        if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast or ip.is_reserved:
            return f"{host} points at this machine or a link-local address ({a}) — a connection points at an outside service."
        if ip.is_private and not settings.api_allow_private and host.lower() not in private_hosts():
            return f"{host} is on the private network ({a}). Connections to the company's own network are off on this server (CBX_API_ALLOW_PRIVATE)."
    return ""


def _check_headers(h: Any) -> dict:
    if not h:
        return {}
    if not isinstance(h, dict):
        raise ConnectionError_("Extra headers must be name → value pairs.")
    out = {}
    for k, v in h.items():
        k = str(k).strip()
        if not HEADER_OK.match(k) or k.lower() in BLOCKED_REQUEST_HEADERS:
            raise ConnectionError_(f"'{k}' is not a header this connection may set.")
        out[k] = str(v)[:500]
    return out


def _check_tools(db: Session, slugs: Any) -> list[str]:
    if not slugs:
        return []
    want = [str(s) for s in slugs]
    from ..engine import loader
    from ..api.builder_api import drafts_root

    # tool folders (live or a Builder draft), and the pre-1.0.48 workflow tools still in the table
    have = {t.slug for t in db.query(Tool).filter(Tool.slug.in_(want)).all()} | {s for s in want if s in loader.registry().tools or s in loader.registry().broken or (drafts_root() / s / "tool.json").exists()}
    missing = [s for s in want if s not in have]
    if missing:
        raise ConnectionError_(f"No such tool: {', '.join(missing)}.")
    return sorted(have)


def create(db: Session, by: User, *, name: str, base_url: str, auth_kind: str = "bearer", header_name: str = "", secret: str = "", description: str = "", extra_headers: Any = None, tools: Any = None) -> ApiConnection:
    name = (name or "").strip()
    if len(name) < 2:
        raise ConnectionError_("Give the connection a name — the service it reaches, e.g. 'Sage accounting'.")
    if auth_kind not in AUTH_KINDS:
        raise ConnectionError_("Choose how the key is sent: bearer, header, basic, query or none.")
    slug = slugify(name)
    if db.query(ApiConnection).filter(ApiConnection.slug == slug).one_or_none():
        raise ConnectionError_(f"A connection called '{name}' already exists.")
    c = ApiConnection(slug=slug, name=name[:120], description=(description or "").strip()[:2000], base_url=_check_url(base_url), auth_kind=auth_kind, header_name=_header_for(auth_kind, header_name), extra_headers=_check_headers(extra_headers), tools=_check_tools(db, tools), created_by_id=by.id)
    _set_secret(c, secret, auth_kind)
    db.add(c)
    db.flush()
    log(db, "api_connection_added", actor=by, target=slug, detail=f"{c.base_url}; auth {c.auth_kind}; key {'set' if c.secret_sealed else 'none'}; tools {', '.join(c.tools) or 'none'}")
    return c


def _header_for(kind: str, given: str) -> str:
    g = (given or "").strip()
    if kind == "header":
        if not HEADER_OK.match(g or "X-API-Key") or g.lower() in BLOCKED_REQUEST_HEADERS - {"authorization"}:
            raise ConnectionError_("The header name may only have letters, digits and dashes.")
        return g or "X-API-Key"
    if kind == "query":
        return re.sub(r"[^A-Za-z0-9_\-]", "", g) or "api_key"
    if kind == "basic":
        return g  # the username
    return ""


def _set_secret(c: ApiConnection, secret: str, kind: str) -> None:
    s = (secret or "").strip()
    if kind != "none" and not s and not c.secret_sealed:
        raise ConnectionError_("Paste the key (or choose 'none' if the service needs no key).")
    if s:
        c.secret_sealed = secretbox.seal(s)
        c.secret_hint = secretbox.hint(s)
    if kind == "none":
        c.secret_sealed = ""
        c.secret_hint = ""


def update(db: Session, c: ApiConnection, by: User, **fields: Any) -> ApiConnection:
    changes = []
    if "name" in fields and fields["name"] is not None:
        n = str(fields["name"]).strip()
        if len(n) < 2:
            raise ConnectionError_("The name can't be empty.")
        if n != c.name:
            c.name = n[:120]
            changes.append("name")
    if fields.get("description") is not None:
        c.description = str(fields["description"]).strip()[:2000]
        changes.append("description")
    if fields.get("base_url") is not None:
        u = _check_url(str(fields["base_url"]))
        if u != c.base_url:
            c.base_url = u
            changes.append("address")
    kind = fields.get("auth_kind") or c.auth_kind
    if kind not in AUTH_KINDS:
        raise ConnectionError_("Choose how the key is sent: bearer, header, basic, query or none.")
    kind_changed = kind != c.auth_kind
    if kind_changed:
        c.auth_kind = kind
        changes.append(f"auth {kind}")
    if fields.get("header_name") is not None or kind_changed:
        c.header_name = _header_for(kind, str(fields.get("header_name") or ("" if kind_changed else c.header_name)))
    if fields.get("secret"):
        _set_secret(c, str(fields["secret"]), kind)
        changes.append("key replaced")
    elif kind == "none" and c.secret_sealed:
        c.secret_sealed, c.secret_hint = "", ""
        changes.append("key removed")
    if fields.get("extra_headers") is not None:
        c.extra_headers = _check_headers(fields["extra_headers"])
        changes.append("headers")
    if fields.get("tools") is not None:
        t = _check_tools(db, fields["tools"])
        if t != list(c.tools or []):
            c.tools = t
            changes.append(f"tools {', '.join(t) or 'none'}")
    if fields.get("is_active") is not None and bool(fields["is_active"]) != c.is_active:
        c.is_active = bool(fields["is_active"])
        changes.append("switched on" if c.is_active else "switched off")
    if c.auth_kind != "none" and not c.secret_sealed:
        raise ConnectionError_("This connection needs a key — paste it, or set how it authenticates to 'none'.")
    db.flush()
    log(db, "api_connection_changed", actor=by, target=c.slug, detail=", ".join(changes) or "no change")
    return c


def remove(db: Session, c: ApiConnection, by: User) -> None:
    slug = c.slug
    db.delete(c)
    db.flush()
    log(db, "api_connection_deleted", actor=by, target=slug)


def describe(c: ApiConnection, db: Optional[Session] = None) -> dict:
    d = {
        "id": c.id, "slug": c.slug, "name": c.name, "description": c.description, "base_url": c.base_url,
        "auth_kind": c.auth_kind, "header_name": c.header_name, "has_key": bool(c.secret_sealed), "key_hint": c.secret_hint,
        "key_readable": _readable(c), "extra_headers": dict(c.extra_headers or {}), "tools": list(c.tools or []),
        "is_active": c.is_active, "last_test": dict(c.last_test or {}), "created_at": c.created_at.isoformat() if c.created_at else None,
    }
    if db is not None:
        names = {t.slug: t.name for t in db.query(Tool).filter(Tool.slug.in_(c.tools or ["-"])).all()}
        d["tool_names"] = [names.get(s, s) for s in (c.tools or [])]
    return d


def _readable(c: ApiConnection) -> bool:
    if not c.secret_sealed:
        return True
    try:
        secretbox.open_(c.secret_sealed)
        return True
    except secretbox.SecretBoxError:
        return False


def secret_of(c: ApiConnection) -> str:
    """The key, for a server-side job that must hand it to a library (IFRS 16's Sequentra client) — never to a
    page, a log line or a row."""
    return _secret(c)


def _secret(c: ApiConnection) -> str:
    if not c.secret_sealed:
        return ""
    try:
        return secretbox.open_(c.secret_sealed)
    except secretbox.SecretBoxError as e:
        raise ConnectionError_(str(e)) from e


def _apply_auth(c: ApiConnection, headers: dict, params: dict) -> None:
    key = _secret(c)
    if c.auth_kind == "bearer" and key:
        headers["Authorization"] = f"Bearer {key}"
    elif c.auth_kind == "header" and key:
        headers[c.header_name or "X-API-Key"] = key
    elif c.auth_kind == "basic" and key:
        headers["Authorization"] = "Basic " + base64.b64encode(f"{c.header_name}:{key}".encode("utf-8")).decode("ascii")
    elif c.auth_kind == "query" and key:
        params[c.header_name or "api_key"] = key


def _join(base: str, path: str) -> str:
    """base_url + path, and nothing else: a caller may not steer the request to another host."""
    p = (path or "").strip()
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", p) or p.startswith("//"):
        raise ConnectionError_("Give a path under the connection's address (e.g. /customers?page=2), not a full URL.")
    if "\n" in p or "\r" in p:
        raise ConnectionError_("That path is not valid.")
    return base + ("/" + p.lstrip("/") if p else "")


def call(db: Session, c: ApiConnection, *, method: str = "GET", path: str = "", params: Optional[dict] = None, json_body: Any = None, body: Optional[str] = None, headers: Optional[dict] = None, by_label: str = "", by: Optional[User] = None) -> dict:
    """One request to the service, with the key added here. Returns status, headers worth keeping and the
    body (parsed as JSON when it is JSON). The key is never in the result, the log or the audit trail."""
    if not c.is_active:
        raise ConnectionError_(f"The connection '{c.name}' is switched off.")
    m = (method or "GET").upper()
    if m not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"):
        raise ConnectionError_(f"Method {m} is not allowed.")
    url = _join(c.base_url, path)
    # checked again at call time, not only when saved: a name can be pointed somewhere else later
    why = host_refused((urlsplit(c.base_url).hostname or "").lower())
    if why:
        log(db, "api_call", actor=by, actor_label=by_label, target=c.slug, detail=f"{m} {path or '/'} refused: {why}", outcome="blocked")
        raise ConnectionError_(why)
    h = {k: v for k, v in (c.extra_headers or {}).items()}
    for k, v in (headers or {}).items():
        if HEADER_OK.match(str(k)) and str(k).lower() not in BLOCKED_REQUEST_HEADERS:
            h[str(k)] = str(v)[:2000]
    q = {str(k): str(v) for k, v in (params or {}).items()}
    _apply_auth(c, h, q)
    t0 = utcnow()
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=False, trust_env=True) as cl:
            r = cl.request(m, url, params=q or None, headers=h, json=json_body if json_body is not None else None, content=body.encode("utf-8") if (body is not None and json_body is None) else None)
    except httpx.HTTPError as e:
        log(db, "api_call", actor=by, actor_label=by_label, target=c.slug, detail=f"{m} {path or '/'} → no answer ({type(e).__name__})", outcome="failed")
        why = {"ConnectError": "could not connect — is the address right, and reachable from the server?", "ConnectTimeout": "timed out connecting", "ReadTimeout": "took too long to answer", "ProxyError": "the server's outbound proxy refused it", "RemoteProtocolError": "answered with something that isn't HTTP"}.get(type(e).__name__, type(e).__name__)
        raise ConnectionError_(f"{c.name} did not answer: {why}.") from e
    ms = int((utcnow() - t0).total_seconds() * 1000)
    text = r.text[:MAX_BODY]
    parsed: Any = None
    if "json" in (r.headers.get("content-type") or ""):
        try:
            parsed = json.loads(text) if len(r.text) <= MAX_BODY else None
        except ValueError:
            parsed = None
    log(db, "api_call", actor=by, actor_label=by_label, target=c.slug, detail=f"{m} {path or '/'} → {r.status_code} in {ms} ms", outcome="ok" if r.status_code < 400 else "failed")
    return {"ok": r.status_code < 400, "status": r.status_code, "ms": ms, "headers": {k: v for k, v in r.headers.items() if k.lower() in ("content-type", "content-length", "x-request-id", "retry-after", "location")}, "json": parsed, "text": text if parsed is None else None, "truncated": len(r.text) > MAX_BODY}


def test(db: Session, c: ApiConnection, by: User, path: str = "") -> dict:
    """Does the service answer this key? One GET at the address (or a path under it); remembered on the row."""
    from ..ifrs16 import api as _SQ  # Sequentra answers only on a report address: test it the way the desktop does

    if _SQ.is_sequentra(c):
        res = _SQ.test(db, c)
        db.flush()
        return {k: v for k, v in res.items() if k != "lines"}
    try:
        out = call(db, c, method="GET", path=path, by=by)
        note = f"Answered {out['status']}" + (" — the key works." if out["status"] < 400 else " — the address answers but refused this key." if out["status"] in (401, 403) else " — the address answers; check the path.")
        res = {"at": utcnow().isoformat(), "ok": out["ok"], "status": out["status"], "note": note, "ms": out["ms"]}
    except ConnectionError_ as e:
        res = {"at": utcnow().isoformat(), "ok": False, "status": 0, "note": str(e)}
    c.last_test = res
    db.flush()
    return res
