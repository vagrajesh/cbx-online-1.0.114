"""CBX-Connect application. `python -m cbx.main` or `uvicorn cbx.main:app`."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import VERSION
from . import branding
from .config import settings
from .db import init_db, session_scope

log = logging.getLogger("cbx")
WEB = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from .bootstrap import ensure_cexa, ensure_library
    from .security.roles import seed as seed_roles
    from .tools import seed_tools

    with session_scope() as db:
        seed_tools(db)
        # Every start, not only a fresh install: an existing server updated to 1.0.32 gets an empty
        # `roles` table from init_db(), and without this nobody could add a user until someone ran
        # bootstrap by hand. Built-ins are re-synced here too, so they cannot drift.
        seed_roles(db)
        ensure_cexa(db)
        ensure_library(db)
        # The Versions page must show the version that is actually running as live — also when it was
        # applied with update.bat or by copying files, which never touched the page's records.
        try:
            from .versions.injector import reconcile as reconcile_versions

            fixed = reconcile_versions(db)
            if fixed:
                print("  Versions: %s is what is running — marked live." % fixed, flush=True)
        except Exception as e:  # noqa: BLE001 — bookkeeping must never stop the boot
            log.warning("version reconcile skipped: %s", e)
        try:  # 1.0.80: the work areas' slots and ports, so the door knows them after a restart
            from .versions.areas import load as load_areas

            load_areas(db)
        except Exception as e:  # noqa: BLE001
            log.warning("work areas not loaded: %s", e)
        # Entity Compliance: every certificate in its company's folder, every named responsible person
        # linked to their account where that is certain. Idempotent; the audit trail says what moved.
        from .docwatch.housekeeping import run_at_start as docwatch_housekeeping

        hk = docwatch_housekeeping(db)
        if (hk.get("relocate") or {}).get("moved") or hk.get("linked"):
            print("  Entity Compliance: %s file(s) moved into company folders, %s responsible person(s) linked." % ((hk.get("relocate") or {}).get("moved", 0), hk.get("linked", 0)), flush=True)
        # To-do (1.0.55): the tool folder replaces the built-in page — its old rows are copied in once
        try:
            from .todo.copy_in import run_at_start as todo_copy_in

            got = todo_copy_in(db)
            if got and any(got.values()):
                print("  To-do: copied into the To-do tool — %s." % ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in got.items()), flush=True)
        except Exception as e:  # noqa: BLE001 — never block the boot; the old page stays until it has run
            log.warning("To-do copy skipped: %s", e)
        # Site Schedules and IFRS 16 (1.0.56), the same way
        from importlib import import_module

        for mod, title in (("cbx.schedules.copy_in", "Site Schedules"), ("cbx.ifrs16.copy_in", "IFRS 16"), ("cbx.docwatch.copy_in", "Entity Compliance"),
                           ("cbx.schedules.copy_in:folder_to_page", "Site Schedules page"), ("cbx.ifrs16.copy_in:folder_to_page", "IFRS 16 page")):
            try:
                m, _, fn = mod.partition(":")
                got = getattr(import_module(m), fn or "run_at_start")(db)
                if got and any(got.values()):
                    print("  %s: copied into the %s tool — %s." % (title, title, ", ".join(f"{v} {k}" for k, v in got.items())), flush=True)
            except Exception as e:  # noqa: BLE001 — never block the boot; the old page's rows stay where they are
                log.warning("%s copy skipped: %s", title, e)
    # An update announced before a restart (a 10-minute notice interrupted by the restart itself, or by
    # something else) is either arrived at — then forgotten — or re-armed.
    try:
        from .versions import rollout

        note = rollout.resume_on_boot()
        if note:
            print("  Update: %s." % note, flush=True)
    except Exception as e:  # noqa: BLE001 — never block the boot
        log.warning("pending update check skipped: %s", e)
    from .jobs import start_scheduler, stop_scheduler

    start_scheduler()
    try:  # 1.0.68: her studio voice (graphics card), if it is set up — in the background, never blocking the boot
        from .voice import studio as _studio

        if _studio.installed():
            import threading

            threading.Thread(target=_studio.start, daemon=True).start()
    except Exception as e:  # noqa: BLE001
        log.warning("studio voice not started: %s", e)
    addrs = [a for a in local_addresses() if a not in ("127.0.0.1",)]
    print("\n  %s %s is running (model mode: %s)." % (branding.product_name(), VERSION, settings.model_mode))
    print("  On this machine:   http://localhost:%s" % settings.port)
    for a in addrs:
        if a != "localhost":
            print("  From other PCs:    http://%s:%s" % (a, settings.port))
    print("  Leave this window open. Ctrl+C stops the server.\n", flush=True)
    yield
    stop_scheduler()
    try:
        from .voice import studio as _studio

        _studio.stop()
    except Exception:
        pass
    try:
        from .versions import rollout

        rollout.stop()
    except Exception:
        pass
    try:
        from .versions import sandbox_runner

        sandbox_runner.stop_all()
    except Exception:
        pass


app = FastAPI(title=settings.product_name, version=VERSION, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

def local_addresses() -> list[str]:
    """This machine's name and LAN IPs — always allowed, and printed at startup so the owner knows what to open."""
    import socket

    out = ["localhost", "127.0.0.1"]
    try:
        host = socket.gethostname()
        out += [host, host.lower(), socket.getfqdn()]
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                out.append(ip)
    except OSError:
        pass
    seen, uniq = set(), []
    for h in out:
        if h and h not in seen:
            seen.add(h)
            uniq.append(h)
    return uniq


hosts = [h.strip() for h in settings.allowed_hosts.split(",") if h.strip()]
if hosts and hosts != ["*"]:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=sorted(set(hosts + local_addresses() + ["testserver"])))


# Every script, style and image of the app comes from this server; nothing is inline but the style
# attributes the views set (`style-src 'unsafe-inline'`), and the favicon is a data: URI. A page that
# tried to load or run anything else — a stored script in a name, a form posting elsewhere — is stopped
# by the browser. `frame-src https:` lets a tool page embed another website in a frame (an Embed block);
# a Code block is served as its own document with its own policy (engine_api.CODE_CSP) in a sandboxed
# frame.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "font-src 'self' data:; connect-src 'self'; media-src 'self' blob:; frame-src 'self' https:; object-src 'none'; "
       "base-uri 'self'; form-action 'self'; frame-ancestors 'self'")
STATIC_VERSIONED = f"/static/{VERSION}/"


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    if not commit_before_response(request, resp):
        # the work could not be saved (the database stayed busy for 30 s): say so, never a 200 for lost work
        resp = JSONResponse({"detail": "The server was busy and did not save that. Try again in a moment."}, status_code=503, headers={"Retry-After": "2"})
    path = request.url.path
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Content-Security-Policy", CSP)
    if path.startswith(STATIC_VERSIONED):
        # the version is in the address: a promoted version has new addresses, so these may be kept for good
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        resp.headers.setdefault("Cache-Control", "no-store" if path.startswith("/api/") else "no-cache")
    return resp


def commit_before_response(request: Request, resp) -> bool:
    """FastAPI 0.118+ runs a yield-dependency's exit code — `get_db`'s commit — only AFTER the response has
    been sent. A browser that saves and immediately re-reads then races its own commit (sign in → /me came
    back 401 about half the time). `get_db` parks its session on request.state; here, once the endpoint has
    returned and before the body goes out, a successful request is committed. Errors are left to the
    dependency's own exit (rollback that keeps the audit rows)."""
    db = getattr(request.state, "db", None)
    if db is None or resp.status_code >= 400:
        return True
    try:
        db.commit()
        return True
    except Exception as e:  # noqa: BLE001
        # rolled back here, so the dependency's own exit finds a clean session (it used to raise
        # PendingRollbackError and turn one busy moment into a page of errors and a 500)
        log.warning("could not save on %s: %s", request.url.path, str(e).splitlines()[0][:200])
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return False


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled error on %s", request.url.path)
    from .audit import record_error

    # The watcher (cbx/cexa/watcher.py) reads these, so a 500 reaches the owner without anyone opening a log file.
    record_error("server_error", request.url.path, f"{type(exc).__name__}: {str(exc)[:300]}")
    return JSONResponse({"detail": "Something went wrong on our side. It has been logged."}, status_code=500)


# ---- routers ---------------------------------------------------------------------
from .api import audit as r_audit  # noqa: E402
from .api import auth as r_auth  # noqa: E402
from .api import branding as r_branding  # noqa: E402
from .api import roles_api as r_roles  # noqa: E402
from .api import training as r_training  # noqa: E402
from .api import chat as r_chat  # noqa: E402
from .api import connections_api as r_connections  # noqa: E402
from .api import org as r_org  # noqa: E402
from .api import settings_api as r_settings  # noqa: E402
from .api import users as r_users  # noqa: E402

for r in (r_auth, r_users, r_audit, r_settings, r_branding, r_org, r_roles, r_training, r_chat, r_connections):
    app.include_router(r.router)

# Feature routers are registered from their packages so each can be developed independently.
from .api.registry import include_feature_routers  # noqa: E402

include_feature_routers(app)

# The sandbox door: /sandbox/<slot>/enter sets a signed cookie, and while it is valid the middleware
# (outermost — added last) forwards every request to the sandbox's own port. /sandbox/leave clears it.
from .api import sandbox_door as r_door  # noqa: E402

app.include_router(r_door.router)


class OldAddresses:
    """AXL became C-EXA in 1.0.49 and /api/axl/… became /api/cexa/…. A browser tab still running the old
    screens (or a bookmark) keeps working until it reloads: the old address is served by the new one."""

    OLD, NEW = "/api/axl", "/api/cexa"

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            p = scope.get("path") or ""
            if p == self.OLD or p.startswith(self.OLD + "/"):
                scope = dict(scope, path=self.NEW + p[len(self.OLD):], raw_path=(self.NEW + p[len(self.OLD):]).encode())
        await self.app(scope, receive, send)


app.add_middleware(OldAddresses)
app.add_middleware(r_door.SandboxDoor)


@app.get("/api/version")
def version_now():
    """What every open page asks every half-minute: the running version, an announced update with the
    seconds left before the switch, and whether the server is in the middle of restarting. Unauthenticated
    — there is nothing here the sign-in screen does not already show — and never cached."""
    from .versions import injector, rollout

    return JSONResponse({"version": VERSION, "pending": rollout.pending(), "restarting": injector.restart_pending()}, headers={"Cache-Control": "no-store"})


@app.get("/api/health")
def health():
    from .cexa.gateway import ollama_status

    st = ollama_status()
    # unauthenticated: enough for the Versions page to know we're back up, no model names
    return {"ok": True, "version": VERSION, "product": branding.product_name(), "model": {"online": bool(st.get("online")), "mode": st.get("mode")}}


# ---- static SPA ------------------------------------------------------------------
# The shell loads everything from /static/<VERSION>/…, and a module's relative imports
# (`./views/index.js`) resolve under that same prefix — so after a promote every script has a new
# address and no browser can keep serving the old one. Before, only app.js carried a version stamp and
# the 37 view modules it imports did not: the "press Ctrl+Shift+R after every update" problem.
app.mount(STATIC_VERSIONED.rstrip("/"), StaticFiles(directory=WEB), name="static-versioned")
app.mount("/static", StaticFiles(directory=WEB), name="static")


_INDEX_CACHE: dict[str, str] = {}


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    """Serve the SPA shell with the version stamped on every static URL, so a promoted version is
    never served from a browser's cache of the old scripts."""
    from fastapi.responses import HTMLResponse

    # An unknown API path must not come back as the HTML shell with a 200 — the client would try
    # to read it as data. (Static files are mounted before this route, so /static/ never lands here.)
    if path == "api" or path.startswith("api/"):
        return JSONResponse({"detail": "That isn't here."}, status_code=404)
    html = _INDEX_CACHE.get(VERSION)
    if html is None:
        html = (WEB / "index.html").read_text(encoding="utf-8").replace('"/static/', f'"{STATIC_VERSIONED}')
        _INDEX_CACHE[VERSION] = html
    # the tab title before app.js runs; the shell keeps it in step with branding afterwards
    from html import escape

    html = html.replace("<title>CBX-Connect</title>", f"<title>{escape(branding.product_name())}</title>", 1)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("cbx.main:app", host=settings.host, port=settings.port, reload=False)
