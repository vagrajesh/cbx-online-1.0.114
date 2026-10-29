"""The sandbox at the live address (/sandbox/<slot>/enter … /sandbox/leave) and the sandbox's own
sign-in: an admin session and a signed cookie open the door; everything is forwarded; the live cookies
never reach the sandbox; the sandbox's cookies come back renamed; every page gets the strip; a stale
cookie is cleared; the owner gets a sandbox-only password made by the package's own bootstrap."""
from __future__ import annotations

import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from cbx import VERSION
from cbx.api import sandbox_door as D
from cbx.config import settings
from cbx.versions import sandbox_runner as R

ROOT = Path(__file__).resolve().parents[1]


class _FakeSandbox(BaseHTTPRequestHandler):
    """A very small sandbox app: HTML at /, a cookie-setting login, an absolute redirect, an echo."""

    def _send(self, code, body: bytes, headers=None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path == "/":
            return self._send(200, b"<!doctype html><html><head><title>sbx</title></head><body class='x'><div id='app'>hello</div></body></html>", {"Content-Type": "text/html; charset=utf-8"})
        if self.path == "/redir":
            return self._send(302, b"", {"Location": f"http://127.0.0.1:{self.server.server_port}/after?x=1"})
        echo = {"method": "GET", "path": self.path, "cookie": self.headers.get("Cookie"), "door": self.headers.get("X-Cbx-Sandbox-Door"), "fhost": self.headers.get("X-Forwarded-Host")}
        return self._send(200, json.dumps(echo).encode(), {"Content-Type": "application/json", "X-Upstream": "fake-sandbox"})

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        if self.path == "/api/auth/login":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Set-Cookie", "cbx_session=sbx-sid-1; Path=/; HttpOnly")
            self.send_header("Set-Cookie", "cbx_sandbox=forged.x; Path=/")
            self.send_header("Set-Cookie", "other=1; Path=/")
            out = b'{"ok": true}'
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            return
        return self._send(201, json.dumps({"method": "POST", "path": self.path, "body": body.decode()}).encode(), {"Content-Type": "application/json"})

    def log_message(self, *a):
        pass


@pytest.fixture()
def fake_sandbox(monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), _FakeSandbox)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    port = srv.server_port
    monkeypatch.setattr(R, "_port_for", lambda slot: port if slot == "sandbox-a" else 8082)
    monkeypatch.setattr(R, "is_running", lambda slot: slot == "sandbox-a")
    monkeypatch.setattr(R, "status", lambda: [{"slot": "sandbox-a", "port": port, "url": f"http://127.0.0.1:{port}", "running": True, "healthy": True, "pid": 1}, {"slot": "sandbox-b", "port": 8082, "url": "http://127.0.0.1:8082", "running": False, "healthy": False, "pid": None}])
    R.LOGINS["sandbox-a"] = [{"email": "owner@test.local", "password": "abcd-efgh-123", "at": "now"}]
    try:
        yield srv
    finally:
        R.LOGINS.pop("sandbox-a", None)
        srv.shutdown()


def test_door_needs_an_admin_session(client):
    r = client.get("/sandbox/sandbox-a/enter", follow_redirects=False)
    assert r.status_code == 403
    assert "cbx_sandbox" not in r.cookies


def test_door_refuses_a_sandbox_that_is_not_running(owner):
    r = owner.get("/sandbox/sandbox-a/enter", follow_redirects=False)
    assert r.status_code == 409
    assert "not running" in r.text
    r = owner.get("/sandbox/sandbox-z/enter", follow_redirects=False)
    assert r.status_code == 404


def test_door_forwards_everything_and_keeps_the_live_cookies_out(owner, fake_sandbox):
    # live answers before the door
    assert owner.get("/api/health").json()["version"] == VERSION
    r = owner.get("/sandbox/sandbox-a/enter", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert owner.cookies.get("cbx_sandbox", "").startswith("sandbox-a.")
    assert owner.get("/sandbox/where").json() == {"slot": "sandbox-a", "live": False}

    # a page: served by the sandbox, with the strip and the way back
    r = owner.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "Sandbox A" in r.text and "/sandbox/leave" in r.text and "<div id='app'>hello</div>" in r.text
    assert r.text.index("cbx-sandbox-strip") < r.text.index("<div id='app'>")

    # an API call: forwarded as-is; the live session cookie is not in what the sandbox sees, but the
    # sandbox's own (stored renamed on this address) is — under its real name
    owner.cookies.set("cbx_sbx_session", "sbx-old")
    owner.cookies.set("unrelated", "yes")
    r = owner.get("/api/anything?a=1", headers={"Host": "testserver"})
    assert r.headers.get("x-upstream") == "fake-sandbox"
    e = r.json()
    assert e["path"] == "/api/anything?a=1" and e["door"] == "sandbox-a" and e["fhost"] == "testserver"
    live_sid = owner.cookies.get("cbx_session")
    assert live_sid and live_sid not in (e["cookie"] or "")
    assert "cbx_sandbox=" not in (e["cookie"] or "")
    assert "cbx_session=sbx-old" in e["cookie"] and "unrelated=yes" in e["cookie"]

    # the sandbox signing someone in: its cookie comes back renamed, the door's cookie it may not set
    owner.cookies.delete("cbx_sbx_session")
    owner.cookies.delete("unrelated")
    r = owner.post("/api/auth/login", json={"email": "x", "password": "y"})
    assert r.status_code == 200
    set_cookies = r.headers.get_list("set-cookie")
    names = [c.split("=", 1)[0] for c in set_cookies]
    assert "cbx_sbx_session" in names and "other" in names
    assert "cbx_session" not in names and "cbx_sandbox" not in names
    assert owner.cookies.get("cbx_sbx_session") == "sbx-sid-1"
    assert owner.cookies.get("cbx_session") == live_sid  # the live session survived the sandbox sign-in

    # an absolute redirect from the sandbox becomes a path on this address
    r = owner.get("/redir", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/after?x=1"

    # a POST body goes through
    r = owner.post("/api/things", json={"k": "v"})
    assert r.status_code == 201 and json.loads(r.json()["body"]) == {"k": "v"}

    # leaving: cookie gone, live answers again
    r = owner.get("/sandbox/leave", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/#/areas"
    assert not owner.cookies.get("cbx_sandbox")
    assert owner.get("/api/health").json()["version"] == VERSION
    assert owner.get("/sandbox/where").json()["live"] is True


def test_forged_or_stale_door_cookie_is_ignored_and_cleared(owner, fake_sandbox):
    owner.cookies.set("cbx_sandbox", "sandbox-a." + "0" * 40)
    r = owner.get("/api/health")
    assert r.json()["version"] == VERSION  # live, not the sandbox
    assert "x-upstream" not in r.headers
    cleared = [c for c in r.headers.get_list("set-cookie") if c.startswith("cbx_sandbox=") and "Max-Age=0" in c]
    assert cleared
    # a cookie signed for another session is just as dead
    r_other = D.make_cookie("sandbox-a", "some-other-session")
    owner.cookies.set("cbx_sandbox", r_other)
    assert owner.get("/api/health").json()["version"] == VERSION


def test_door_cookie_is_bound_to_the_session():
    a = D.make_cookie("sandbox-a", "sid-1")
    assert D.slot_from_cookie(a, "sid-1") == "sandbox-a"
    assert D.slot_from_cookie(a, "sid-2") is None
    assert D.slot_from_cookie(a.replace("sandbox-a", "sandbox-b"), "sid-1") is None
    assert D.slot_from_cookie("sandbox-a", "sid-1") is None
    assert D.slot_from_cookie(None, "sid-1") is None


def test_versions_page_shows_the_sandbox_signin_only_to_the_owner(owner, fake_sandbox):
    r = owner.get("/api/versions")
    assert r.status_code == 200
    rt = {x["slot"]: x for x in r.json()["runtime"]}
    assert rt["sandbox-a"]["door"] == "/sandbox/sandbox-a/enter"
    assert rt["sandbox-a"]["login"] == [{"email": "owner@test.local", "password": "abcd-efgh-123"}]
    assert rt["sandbox-b"]["login"] == []
    # what C-EXA's tools read (sandbox_runner.status) never carries a password
    assert "password" not in json.dumps(R.status()) and "abcd-efgh-123" not in json.dumps(R.status())


def test_seed_owners_makes_a_sandbox_only_signin(monkeypatch):
    """The package's own bootstrap creates the owner in the sandbox database with a fresh password and
    no forced change; stop() forgets it. Runs this tree as the package."""
    monkeypatch.setattr(R, "_port_for", lambda slot: 18999)
    logins, note = R.seed_owners("sandbox-b", ROOT, [("owner@test.local", "Test Owner"), ("not-an-email", "x")])
    assert note == "", note
    assert len(logins) == 1 and logins[0]["email"] == "owner@test.local"
    pw = logins[0]["password"]
    assert len(pw) >= 12 and "-" in pw
    assert R.logins("sandbox-b") == logins
    db = settings.data_dir / "sandboxes" / "sandbox-b-data" / "cbx.sqlite3"
    assert db.exists()
    con = sqlite3.connect(str(db))
    try:
        row = con.execute("select role, must_change_password, is_axl from users where email='owner@test.local'").fetchone()
    finally:
        con.close()
    assert row == ("owner", 0, 0)
    # a second start gives a different password; stop forgets them
    logins2, _ = R.seed_owners("sandbox-b", ROOT, [("owner@test.local", "Test Owner")])
    assert logins2[0]["password"] != pw
    R.stop("sandbox-b")
    assert R.logins("sandbox-b") == []


def test_proposal_occupant_points_at_its_sandbox_copy(db):
    from cbx.versions import injector

    for s in injector.sandboxes(db):
        if s["busy"] and s["occupant"]["kind"] == "proposal":
            assert s["occupant"]["path"].startswith(str(injector.sandboxes_dir()))


def test_playground_is_a_copy_of_the_live_app_on_a_free_slot(owner, db, monkeypatch):
    """The owner asks for a playground: the live tree is copied, the runner is started on the free slot
    with the owners seeded and kind=playground; a slot holding a build is refused."""
    from cbx.db import session_scope
    from cbx.models import AppVersion
    from cbx.versions import injector

    calls = []

    def fake_start(slot, path, owners=None, *, kind="version", title=""):
        calls.append({"slot": slot, "path": path, "owners": list(owners or []), "kind": kind, "title": title})
        return {"ok": True, "url": "http://127.0.0.1:8081", "door": f"/sandbox/{slot}/enter", "login": [{"email": e, "password": "sbx-pw"} for e, _ in (owners or [])]}

    monkeypatch.setattr(R, "start", fake_start)
    # other test files leave builds in the shared database: step them aside for this test only
    from cbx.models import Proposal

    parked = []
    with session_scope() as s2:
        for pr in s2.query(Proposal).filter(Proposal.status.in_(["building", "signoff"])).all():
            parked.append(("p", pr.id, pr.status)); pr.status = "noticed"
        for av in s2.query(AppVersion).filter(AppVersion.status.in_(["staged", "sandbox"])).all():
            parked.append(("v", av.id, av.status)); av.status = "superseded"
    try:
        _playground_flow(owner, db, calls, injector, session_scope, AppVersion)
    finally:
        with session_scope() as s2:
            for kind, pid, st in parked:
                row = s2.get(Proposal if kind == "p" else AppVersion, pid)
                if row is not None:
                    row.status = st


def _playground_flow(owner, db, calls, injector, session_scope, AppVersion):
    free = injector.free_slot(db)
    assert free, "no free slot even after parking"
    r = owner.post(f"/api/versions/sandboxes/{free}/playground")
    assert r.status_code == 200, r.text
    assert r.json()["login"][0]["email"] == "owner@test.local"
    c = calls[-1]
    assert c["kind"] == "playground" and c["slot"] == free and ("owner@test.local", "Test Owner") in c["owners"]
    dest = Path(c["path"])
    assert dest == settings.data_dir / "sandboxes" / f"playground-{free}"
    assert (dest / "cbx" / "main.py").is_file() and (dest / "web" / "app.js").is_file() and (dest / "VERSION").is_file()
    assert not (dest / "data").exists() and not (dest / ".env").exists() and not (dest / "run.bat").exists()
    # a slot with a staged version in it is not pushed aside
    with session_scope() as s2:
        s2.add(AppVersion(tag="9.9.9-play", notes="", status="staged", env=free, package_path=str(dest), sha256="x"))
    try:
        r = owner.post(f"/api/versions/sandboxes/{free}/playground")
        assert r.status_code == 409 and "9.9.9-play" in r.json()["detail"]
    finally:
        with session_scope() as s2:
            for v in s2.query(AppVersion).filter(AppVersion.tag == "9.9.9-play").all():
                s2.delete(v)
