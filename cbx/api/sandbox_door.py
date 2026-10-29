"""The sandbox at the live address: /sandbox/<slot>/enter … /sandbox/leave.

Why. A sandbox instance listens on 127.0.0.1:8081 — reachable from the server's own screen and from
nowhere else. Edwin works through the tunnel; "Open app" sent his browser to an address that only the
server can see. The sandbox's pages and API all use absolute paths (/api/…, /static/…), so it cannot be
hung under a prefix the way Node-RED is. Instead the door is a cookie: `enter` sets a signed routing
cookie on the live address, and while it is present this middleware forwards EVERY request — page,
API, upload — to the sandbox's port, untouched. `leave` clears it and the same address is live again.

Keys. Two are needed at once: a valid CBX session of a person who may enter that slot (1.0.80: your own
Dev area, Test if you test, Sandbox A/B for admins — cbx/versions/areas.may_enter; C-EXA's own `cexa-`
sessions are refused) and the routing cookie, which is HMAC-signed over the slot AND that
session id, so it cannot be minted, moved to another browser, or outlive the session it was made for.
A stale routing cookie is cleared on the way through to the live app; the person simply sees live.

What the sandbox never sees. The live session and device cookies are stripped from what is forwarded
— C-EXA's code runs in there. The sandbox's own cookies come back renamed (cbx_session → cbx_sbx_session)
so they cannot overwrite the live ones on the same address, and are renamed back on the way in. That
rename is the door's, not the package's: any version, old or new, works behind it unchanged.

The strip. Every HTML page from the sandbox gets a thin bar at the top: which sandbox this is, that its
data is throwaway, and one link back to live. Nobody should mistake a sandbox for production.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..audit import log
from ..config import settings
from ..db import session_scope
from ..security.auth import SESSION_COOKIE
from ..security.devices import DEVICE_COOKIE
from ..versions import sandbox_runner
from ..security.deps import person_from_cookie as _person_from_cookie
from ..versions import areas as AREAS


def _allowed(sid: Optional[str], slot: str):
    u = _person_from_cookie(sid)
    return u if AREAS.may_enter(u, slot) else None

log_ = logging.getLogger("cbx.sandbox.door")
router = APIRouter(tags=["sandbox"])

ROUTE_COOKIE = "cbx_sandbox"
DOOR_PREFIX = "/sandbox/"
DOOR_HOURS = 8
# the sandbox's own cookies, as the browser stores them on the live address
RENAME_IN = {"cbx_sbx_session": SESSION_COOKIE, "cbx_sbx_device": DEVICE_COOKIE}
RENAME_OUT = {v: k for k, v in RENAME_IN.items()}
LIVE_ONLY = {SESSION_COOKIE, DEVICE_COOKIE, ROUTE_COOKIE}  # never forwarded into a sandbox
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade", "host", "cookie", "content-length"}
HOP_BACK = {"connection", "keep-alive", "transfer-encoding", "upgrade", "content-length"}

_client: Optional[httpx.AsyncClient] = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=3.0), trust_env=False, follow_redirects=False)
    return _client


# ---- the signed routing cookie ----------------------------------------------------------------------

def _sign(slot: str, sid: str) -> str:
    return hmac.new(settings.secret_key.encode(), f"sandbox-door|{slot}|{sid}".encode(), hashlib.sha256).hexdigest()[:40]


def make_cookie(slot: str, sid: str) -> str:
    return f"{slot}.{_sign(slot, sid)}"


def slot_from_cookie(value: Optional[str], sid: Optional[str]) -> Optional[str]:
    """The slot this routing cookie opens — only when it was signed for this very session."""
    if not value or not sid or "." not in value:
        return None
    slot, sig = value.rsplit(".", 1)
    if not sandbox_runner.known(slot):
        return None
    return slot if hmac.compare_digest(sig, _sign(slot, sid)) else None


def _set_route(resp, slot: str, sid: str) -> None:
    resp.set_cookie(ROUTE_COOKIE, make_cookie(slot, sid), httponly=True, samesite="lax", secure=settings.secure_cookies, max_age=DOOR_HOURS * 3600, path="/")


def _clear_route(resp) -> None:
    resp.delete_cookie(ROUTE_COOKIE, path="/")


# ---- enter / leave ---------------------------------------------------------------------------------

def _page(title: str, text: str, code: int = 200) -> HTMLResponse:
    return HTMLResponse(f"<!doctype html><meta charset=utf-8><title>{title}</title><body style='font:15px system-ui;padding:40px;max-width:560px'><h2 style='margin:0 0 8px'>{title}</h2><p>{text}</p><p><a href='/#/versions'>Versions &amp; sandboxes</a></p></body>", status_code=code)


@router.get(DOOR_PREFIX + "{slot}/enter", include_in_schema=False)
def enter(slot: str, request: Request):
    sid = request.cookies.get(SESSION_COOKIE)
    if not sandbox_runner.known(slot):
        with session_scope() as db:
            AREAS.load(db)
    if not sandbox_runner.known(slot):
        return _page("No such sandbox", f"There is no slot called {slot}.", 404)
    me = _allowed(sid, slot)
    if me is None:
        return _page("Not for you", "Sign in to CBX first. Your own Dev area is yours; Test is for the people who test; Sandbox A and B are for admins.", 403)
    running = next((r for r in sandbox_runner.status() if r["slot"] == slot), None)
    if not running or not running.get("running"):
        return _page("That sandbox is not running", "Start it from Versions &amp; sandboxes, then come back through this door.", 409)
    resp = RedirectResponse("/", status_code=303)
    _set_route(resp, slot, sid or "")
    with session_scope() as db:
        log(db, "sandbox_entered", actor=me, target=slot, detail=f"through the door at the live address; sign-ins: {', '.join(x['email'] for x in sandbox_runner.logins(slot)) or 'none seeded'}")
    return resp


@router.get(DOOR_PREFIX + "leave", include_in_schema=False)
def leave(request: Request):
    resp = RedirectResponse("/#/areas", status_code=303)
    _clear_route(resp)
    sid = request.cookies.get(SESSION_COOKIE)
    slot = slot_from_cookie(request.cookies.get(ROUTE_COOKIE), sid)
    if slot:
        me = _person_from_cookie(sid)
        if me is not None:
            with session_scope() as db:
                log(db, "sandbox_left", actor=me, target=slot)
    return resp


@router.get(DOOR_PREFIX + "where", include_in_schema=False)
def where(request: Request):
    """Which sandbox this browser is looking at, if any — a plain answer for the page strip and tests."""
    slot = slot_from_cookie(request.cookies.get(ROUTE_COOKIE), request.cookies.get(SESSION_COOKIE))
    return {"slot": slot, "live": slot is None}


# ---- the forwarding middleware ----------------------------------------------------------------------

def _strip_html(slot: str) -> bytes:
    label = "Test" if slot == "test" else "Dev area" if slot.startswith("dev-") else "Sandbox " + slot[-1].upper()
    return (
        "<div id='cbx-sandbox-strip' style=\"position:fixed;top:0;left:0;right:0;z-index:2147483647;background:#7a3e00;color:#fff;font:600 13px system-ui,sans-serif;"
        "padding:6px 14px;display:flex;gap:16px;align-items:center;box-shadow:0 1px 4px rgba(0,0,0,.25)\">"
        f"<span>{label}</span><span style='font-weight:400;opacity:.9'>throwaway data — nothing here is production</span>"
        "<a href='/sandbox/leave' style='margin-left:auto;color:#fff;text-decoration:underline'>Back to live</a></div>"
        "<style>html{margin-top:32px !important}</style>"
    ).encode()


def parse_cookies(raw: str) -> list[tuple[str, str]]:
    """name=value pairs from a Cookie header, tolerant of whatever a tunnel or a browser extension adds
    (the stdlib parser gives up on the whole header at the first odd name)."""
    out = []
    for part in (raw or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        out.append((name.strip(), value.strip()))
    return out


def _cookie_header_in(raw: str) -> str:
    """The Cookie header the sandbox receives: the live cookies removed, its own renamed back."""
    parts = []
    for name, value in parse_cookies(raw):
        if name in LIVE_ONLY:
            continue
        parts.append(f"{RENAME_IN.get(name, name)}={value}")
    return "; ".join(parts)


def _set_cookie_out(value: str) -> Optional[str]:
    """A Set-Cookie from the sandbox, renamed so it can never touch the live cookies. The door's own
    routing cookie is not the sandbox's to set."""
    name = value.split("=", 1)[0].strip()
    if name == ROUTE_COOKIE:
        return None
    if name in RENAME_OUT:
        return RENAME_OUT[name] + value[len(name):]
    return value


class SandboxDoor:
    """Pure ASGI middleware: while the routing cookie is valid for this admin session, every HTTP
    request except the door's own paths goes to the sandbox's port."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"].startswith(DOOR_PREFIX):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        raw_cookie = headers.get("cookie", "")
        if ROUTE_COOKIE not in raw_cookie:
            return await self.app(scope, receive, send)
        jar = dict(parse_cookies(raw_cookie))
        route = jar.get(ROUTE_COOKIE)
        sid = jar.get(SESSION_COOKIE)
        slot = slot_from_cookie(route, sid)
        if slot is None or _allowed(sid, slot) is None or not sandbox_runner.is_running(slot):
            # stale door (session gone, cookie forged, sandbox stopped): live answers, and the cookie goes
            return await self._pass_and_clear(scope, receive, send)
        return await self._forward(slot, scope, receive, send, headers)

    async def _pass_and_clear(self, scope, receive, send):
        async def send_wrapped(message):
            if message["type"] == "http.response.start":
                hdrs = list(message.get("headers", []))
                hdrs.append((b"set-cookie", f'{ROUTE_COOKIE}=""; Max-Age=0; Path=/; HttpOnly; SameSite=lax'.encode("latin-1")))
                message = {**message, "headers": hdrs}
            await send(message)

        await self.app(scope, receive, send_wrapped)

    async def _forward(self, slot, scope, receive, send, headers):
        body = b""
        while True:
            m = await receive()
            if m["type"] == "http.request":
                body += m.get("body", b"")
                if not m.get("more_body"):
                    break
            elif m["type"] == "http.disconnect":
                return
        out_headers = {k: v for k, v in headers.items() if k not in HOP}
        ck = _cookie_header_in(headers.get("cookie", ""))
        if ck:
            out_headers["cookie"] = ck
        # the sandbox builds links from these — the same address the person is using
        out_headers.setdefault("x-forwarded-proto", scope.get("scheme", "http"))
        if headers.get("host"):
            out_headers.setdefault("x-forwarded-host", headers["host"])
        out_headers["x-cbx-sandbox-door"] = slot
        base = f"http://127.0.0.1:{sandbox_runner._port_for(slot)}"
        qs = scope.get("query_string", b"").decode("latin-1")
        url = base + scope["path"] + (f"?{qs}" if qs else "")
        try:
            req = _http().build_request(scope["method"], url, headers=out_headers, content=body)
            up = await _http().send(req, stream=True)
        except httpx.HTTPError as e:
            log_.info("sandbox door: %s %s → %s", scope["method"], scope["path"], type(e).__name__)
            html = (f"<!doctype html><meta charset=utf-8><title>Sandbox not answering</title><body style='font:15px system-ui;padding:40px;max-width:560px'>"
                    f"<h2 style='margin:0 0 8px'>{slot} is not answering</h2><p>The sandbox app may still be starting, or it was stopped.</p>"
                    f"<p><a href='/sandbox/leave'>Back to live</a></p></body>").encode()
            await send({"type": "http.response.start", "status": 502, "headers": [(b"content-type", b"text/html; charset=utf-8"), (b"content-length", str(len(html)).encode())]})
            await send({"type": "http.response.body", "body": html})
            return
        ctype = up.headers.get("content-type", "")
        is_html = ctype.startswith("text/html")
        resp_headers: list[tuple[bytes, bytes]] = []
        for k, v in up.headers.multi_items():
            lk = k.lower()
            if lk in HOP_BACK or (is_html and lk == "content-encoding"):
                continue
            if lk == "set-cookie":
                v2 = _set_cookie_out(v)
                if v2 is None:
                    continue
                v = v2
            elif lk == "location" and v.startswith(base):
                v = v[len(base):] or "/"
            resp_headers.append((lk.encode("latin-1"), v.encode("latin-1")))
        if is_html:
            # small (the SPA shell): buffer, put the strip in, send with the right length
            content = await up.aread()
            await up.aclose()
            strip = _strip_html(slot)
            low = content.lower()
            i = low.find(b"<body")
            if i >= 0:
                j = low.find(b">", i)
                content = content[: j + 1] + strip + content[j + 1:] if j >= 0 else strip + content
            else:
                content = strip + content
            resp_headers.append((b"content-length", str(len(content)).encode()))
            await send({"type": "http.response.start", "status": up.status_code, "headers": resp_headers})
            await send({"type": "http.response.body", "body": content})
            return
        await send({"type": "http.response.start", "status": up.status_code, "headers": resp_headers})
        try:
            async for chunk in up.aiter_raw():
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
        finally:
            await up.aclose()
        await send({"type": "http.response.body", "body": b""})
