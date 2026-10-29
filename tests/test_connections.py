"""API connections: keys sealed at rest and never returned; admins manage, users can't; the relay adds
the key and only serves a run whose tool has the connection; tools receive the connections in their payload."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from cbx.security import secretbox


def test_secretbox_seals_and_opens_and_refuses_tampering():
    tok = secretbox.seal("sk-live-abcdef123456")
    assert tok.startswith("v1.") and "abcdef" not in tok
    assert secretbox.open_(tok) == "sk-live-abcdef123456"
    assert secretbox.seal("x") != secretbox.seal("x")  # fresh nonce every time
    v, n, c, t = tok.split(".")
    with pytest.raises(secretbox.SecretBoxError):
        secretbox.open_(f"{v}.{n}.{c}.{t[:-2]}AA")
    with pytest.raises(secretbox.SecretBoxError):
        secretbox.open_(tok, master=b"another server's key")
    with pytest.raises(secretbox.SecretBoxError):
        secretbox.open_("not a token")
    assert secretbox.hint("sk-live-abcdef123456") == "••••••••3456" and secretbox.hint("ab") == "••"
    assert secretbox.open_(secretbox.seal("")) == "" and secretbox.open_(secretbox.seal("ünïcödé ✓")) == "ünïcödé ✓"


class _Api(BaseHTTPRequestHandler):
    def _echo(self, method):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode() if n else ""
        out = {"method": method, "path": self.path, "auth": self.headers.get("Authorization"), "xkey": self.headers.get("X-API-Key"), "accept": self.headers.get("Accept"), "cookie": self.headers.get("Cookie"), "body": body}
        code = 401 if self.path.startswith("/needs-key") and not (self.headers.get("Authorization") or self.headers.get("X-API-Key")) else 200
        data = json.dumps(out).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        self._echo("GET")

    def do_POST(self):  # noqa: N802
        self._echo("POST")

    def log_message(self, *a):
        pass


@pytest.fixture()
def api_server(monkeypatch):
    # the fake service lives on loopback, which the relay refuses (1.0.41); in a test run
    # CBX_API_ALLOW_PRIVATE opens that one door — see connections.service.host_refused
    from cbx.config import settings

    monkeypatch.setattr(settings, "api_allow_private", True)
    srv = HTTPServer(("127.0.0.1", 0), _Api)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}"
    finally:
        srv.shutdown()
        srv.server_close()


def test_admin_manages_connections_and_the_key_never_comes_back(owner, admin, user, api_server):
    # users see nothing; admins see the page
    assert user.get("/api/connections").status_code == 403
    d = admin.get("/api/connections").json()
    assert d["auth_kinds"] == ["none", "bearer", "header", "basic", "query"] and any(t["slug"] == "entity-compliance" for t in d["tools"])
    # validation, in plain words
    r = admin.post("/api/connections", json={"name": "Sage", "base_url": "ftp://x", "secret": "k"})
    assert r.status_code == 400 and "http" in r.json()["detail"]
    r = admin.post("/api/connections", json={"name": "Sage", "base_url": api_server, "auth_kind": "bearer"})
    assert r.status_code == 400 and "Paste the key" in r.json()["detail"]
    r = admin.post("/api/connections", json={"name": "Sage", "base_url": api_server, "tools": ["nope"], "secret": "k"})
    assert r.status_code == 400 and "No such tool" in r.json()["detail"]
    r = admin.post("/api/connections", json={"name": "Sage", "base_url": api_server, "extra_headers": {"Cookie": "x"}, "secret": "k"})
    assert r.status_code == 400
    # a real one
    r = admin.post("/api/connections", json={"name": "Sage accounting", "description": "Invoices", "base_url": api_server + "/", "auth_kind": "bearer", "secret": "sk-live-abcdef123456", "extra_headers": {"Accept": "application/json"}, "tools": ["pdf-studio"]})
    assert r.status_code == 200, r.text
    c = r.json()["connection"]
    assert c["slug"] == "sage-accounting" and c["base_url"] == api_server and c["has_key"] and c["key_hint"] == "••••••••3456" and c["key_readable"]
    assert "secret" not in json.dumps(c) and "abcdef" not in json.dumps(c)
    assert c["tools"] == ["pdf-studio"] and c["tool_names"] == ["PDF Studio"]
    # the row in the list carries no key either; the audit trail names the change, never the key
    lst = owner.get("/api/connections").json()["connections"]
    assert [x["slug"] for x in lst] == ["sage-accounting"] and "abcdef" not in json.dumps(lst)
    rows = owner.get("/api/audit?action=api_connection_added").json()["rows"]
    assert rows and rows[0]["target"] == "sage-accounting" and "key set" in rows[0]["detail"] and "abcdef" not in rows[0]["detail"]
    # test: the address answers, the key works
    r = admin.post(f"/api/connections/{c['id']}/test", json={"path": "/needs-key"})
    assert r.status_code == 200, r.text
    assert r.json()["result"]["ok"] and r.json()["result"]["status"] == 200 and "key works" in r.json()["result"]["note"]
    assert r.json()["connection"]["last_test"]["ok"] is True
    # change: switch to a header key, replace the key; the old hint goes
    r = admin.patch(f"/api/connections/{c['id']}", json={"auth_kind": "header", "header_name": "X-API-Key", "secret": "hdr-key-999"})
    assert r.status_code == 200, r.text
    c2 = r.json()["connection"]
    assert c2["auth_kind"] == "header" and c2["header_name"] == "X-API-Key" and c2["key_hint"] == "•••••••-999"
    # a key that isn't replaced stays (the patch didn't send one)
    r = admin.patch(f"/api/connections/{c['id']}", json={"description": "Invoices and customers"})
    assert r.json()["connection"]["has_key"] and r.json()["connection"]["key_hint"] == "•••••••-999"
    # C-EXA-style sessions and users can't change anything
    assert user.patch(f"/api/connections/{c['id']}", json={"description": "x"}).status_code == 403
    assert user.delete(f"/api/connections/{c['id']}").status_code == 403
    # a call (what a Builder rule or data source does — 1.0.79: no browser relay any more): key added on the
    # way out, cookie not forwarded, path stays under the address
    from cbx.connections import service as CS
    from cbx.db import SessionLocal
    from cbx.models import ApiConnection

    def call(path, **kw):
        with SessionLocal() as s:
            con = s.query(ApiConnection).filter(ApiConnection.slug == "sage-accounting").one()
            try:
                out = CS.call(s, con, method="GET", path=path, by_label="test", **kw)
                s.commit()
                return out
            except CS.ConnectionError_ as e:
                return {"error": str(e)}
    out = call("/customers", params={"page": 2})
    assert out["ok"] and out["status"] == 200 and out["json"]["path"] == "/customers?page=2" and out["json"]["xkey"] == "hdr-key-999" and out["json"]["accept"] == "application/json" and out["json"]["cookie"] is None
    assert "under the connection's address" in call("http://evil.example/steal")["error"]
    assert call("//evil.example/steal").get("error")
    rows = owner.get("/api/audit?action=api_call").json()["rows"]
    assert rows and "GET /customers → 200" in rows[0]["detail"] and "hdr-key" not in json.dumps(rows)
    # switched off → refused
    admin.patch(f"/api/connections/{c['id']}", json={"is_active": False})
    assert "switched off" in call("/x")["error"]
    # delete
    assert admin.delete(f"/api/connections/{c['id']}").status_code == 200
    assert owner.get("/api/connections").json()["connections"] == []


def test_a_flow_uses_only_the_connections_ticked_for_its_tool(owner, admin, api_server, db, tmp_path, monkeypatch):
    """Since 1.0.48 tools reach outside services from a flow's Call an API step (or a page's API
    source), inside the server: the key goes out, never into the flow; a connection not ticked for the
    tool is refused."""
    from pathlib import Path

    from cbx.engine import loader
    from cbx.models import FlowRun
    from tests.test_flows import _tool

    _tool(tmp_path, {"sync": {"trigger": {"type": "button"}, "run_as": "starter", "start": "a", "steps": {
        "a": {"type": "api", "connection": "sage", "method": "POST", "path": "invoices", "payload": '{"n": {{trigger.values.n}}}', "as": "answer", "next": "b"},
        "b": {"type": "api", "connection": "other", "method": "GET", "path": "/x"}}}})
    monkeypatch.setattr(loader, "TOOLS_ROOT", tmp_path)
    loader.reload(tmp_path)
    try:
        r = admin.post("/api/connections", json={"name": "Sage", "base_url": api_server, "secret": "sage-key", "tools": ["flowy"]})
        assert r.status_code == 200, r.text
        r = admin.post("/api/connections", json={"name": "Other", "base_url": api_server, "secret": "other-key"})
        assert r.status_code == 200, r.text
        assert any(t["slug"] == "flowy" for t in admin.get("/api/connections").json()["tools"])
        rid = owner.post("/api/flows/flowy/sync/run", json={"values": {"n": 1}}).json()["run"]["id"]
        run = db.get(FlowRun, rid)
        ans = run.ctx["answer"]
        assert ans["auth"] == "Bearer sage-key" and ans["path"] == "/invoices" and json.loads(ans["body"]) == {"n": 1}
        assert run.status == "failed" and "not open to this tool" in run.note  # step b: Other is not ticked
        assert "sage-key" not in json.dumps(run.log)
    finally:
        loader.reload(Path(__file__).resolve().parents[1] / "tools")


def test_a_named_company_host_may_be_on_the_private_network_and_nothing_else(monkeypatch):
    """1.0.86: on the office network api.excellerate.tech (Sequentra) resolves to 10.x — that one host is
    allowed; any other private name still is not, and loopback never is."""
    import socket

    from cbx.config import settings
    from cbx.connections import service as SV

    monkeypatch.setattr(settings, "api_allow_private", False)
    monkeypatch.setattr(settings, "api_private_hosts", "api.excellerate.tech")
    table = {"api.excellerate.tech": "10.232.229.250", "intranet.example": "10.1.2.3", "sneaky.example": "127.0.0.1"}
    monkeypatch.setattr(socket, "getaddrinfo", lambda h, *a, **k: [(0, 0, 0, "", (table[h], 0))])
    assert SV.host_refused("api.excellerate.tech") == ""
    assert "private network" in SV.host_refused("intranet.example")
    assert "this machine" in SV.host_refused("sneaky.example")
    monkeypatch.setattr(settings, "api_private_hosts", "sneaky.example")
    assert "this machine" in SV.host_refused("sneaky.example")  # never loopback, even when named
