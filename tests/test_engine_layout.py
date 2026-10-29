"""1.0.44: pages as blocks on a canvas — layout validation, per-block data, pictures, the sandboxed code
block, and an API connection as a source."""
import io
import json
import struct
import zlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

import pytest

from cbx.config import settings
from cbx.engine import loader


def _png(w=2, h=2):
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def _folder(root, slug, pages, data=None, en=None, assets=None):
    d = root / slug
    for sub in ("pages", "data", "languages", "assets"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    (d / "tool.json").write_text(json.dumps({"slug": slug, "title": "tool.title", "icon": "list", "menu": {"section": "My tools", "order": 5}, "access": {"roles": ["owner", "admin"]}, "home": next(iter(pages))}), encoding="utf-8")
    for n, p in pages.items():
        (d / "pages" / f"{n}.json").write_text(json.dumps(p), encoding="utf-8")
    for n, b in (data or {}).items():
        (d / "data" / f"{n}.json").write_text(json.dumps(b), encoding="utf-8")
    (d / "languages" / "en.json").write_text(json.dumps({"tool.title": "Layout", **(en or {})}), encoding="utf-8")
    for name, blob in (assets or {}).items():
        (d / "assets" / name).write_bytes(blob)
    (d / "README.md").write_text("# Layout\n", encoding="utf-8")
    return d


def test_layout_validation_speaks_plainly(tmp_path):
    bad = {"title": "page.p.title", "layout": {"sections": [{"id": "s1", "columns": [
        {"width": 8, "widgets": [{"id": "a", "type": "bogus"}, {"id": "a", "type": "heading"}, {"id": "b", "type": "embed", "url": "http://plain"}, {"id": "c", "type": "code", "code": "  "}]},
        {"width": 8, "widgets": [{"id": "d", "type": "image", "asset": "nope.png"}, {"id": "e", "type": "table", "source": {"api": "x"}}, {"id": "f", "type": "form", "source": {"provider": "compliance.grid"}}]}]}]}}
    _folder(tmp_path, "lay", {"p": bad}, en={"page.p.title": "P"})
    t = loader.load_all(tmp_path).broken["lay"]
    errs = " | ".join(t.errors)
    for needle in ("type must be one of", "block id 'a' twice", "needs 'text'", "needs an https:// address", "the code block is empty", "assets/nope.png is not in the folder",
                   "api needs 'rows'", "api needs 'columns'", "a form writes to one of the tool's own data blocks", "column widths add up to more than 12"):
        assert needle in errs, needle
    # a sound layout: two columns, every block kind, a real picture
    good = {"title": "page.p.title", "menu": True, "layout": {"sections": [{"id": "s1", "columns": [
        {"width": 8, "widgets": [{"id": "h", "type": "heading", "text": "w.h", "level": 1}, {"id": "t", "type": "text", "text": "w.t"}, {"id": "tb", "type": "table", "source": {"provider": "compliance.documents"}}, {"id": "st", "type": "stats", "source": {"provider": "compliance.grid"}}]},
        {"width": 4, "widgets": [{"id": "im", "type": "image", "asset": "logo.png", "alt": "w.im"}, {"id": "em", "type": "embed", "url": "https://example.org", "height": 300}, {"id": "cd", "type": "code", "code": "<b>hi</b><script>document.body.append('!')</script>"}, {"id": "ln", "type": "links", "kind": "buttons", "items": [{"label": "w.l0", "url": "https://example.org"}, {"label": "w.l1", "route": "home"}]}, {"id": "dv", "type": "divider"}, {"id": "sp", "type": "spacer", "height": 30}]}]}]}}
    _folder(tmp_path, "good", {"p": good}, en={"page.p.title": "P", "w.h": "Hello", "w.t": "Some **bold** text", "w.im": "Logo", "w.l0": "Example", "w.l1": "Home"}, assets={"logo.png": _png()})
    reg = loader.load_all(tmp_path)
    assert "good" in reg.tools, reg.broken.get("good") and reg.broken["good"].errors
    assert [w["id"] for w in loader.widgets_of("p", reg.tools["good"].pages["p"])] == ["h", "t", "tb", "st", "im", "em", "cd", "ln", "dv", "sp"]
    # a 1.0.43 page (type + source) is one full-width block
    lay = loader.effective_layout("x", {"type": "table", "title": "t", "source": {"provider": "compliance.documents"}})
    assert lay["sections"][0]["columns"][0]["width"] == 12 and lay["sections"][0]["columns"][0]["widgets"][0]["id"] == "main"


def test_blocks_answer_by_id_and_code_runs_in_its_own_sandbox(owner, user, tmp_path, monkeypatch):
    good = {"title": "page.p.title", "layout": {"sections": [{"id": "s1", "columns": [{"width": 12, "widgets": [
        {"id": "st", "type": "stats", "source": {"provider": "compliance.grid"}, "items": [{"key": "companies"}]},
        {"id": "tb", "type": "table", "source": {"provider": "compliance.documents"}, "columns": [{"key": "entity", "label": "col.entity"}]},
        {"id": "cd", "type": "code", "code": "<b>hi</b><script>document.title='x'</script>"},
        {"id": "im", "type": "image", "asset": "logo.png"}]}]}]}}
    _folder(tmp_path, "blk", {"p": good}, en={"page.p.title": "P", "col.entity": "Company"}, assets={"logo.png": _png()})
    monkeypatch.setattr(loader, "TOOLS_ROOT", tmp_path)
    loader.reload(tmp_path)
    try:
        p = owner.get("/api/engine/tools/blk/pages/p").json()
        assert [w["id"] for w in p["page"]["layout"]["sections"][0]["columns"][0]["widgets"]] == ["st", "tb", "cd", "im"]
        assert p["widgets"]["st"]["provider"]["name"] == "compliance.grid" and p["widgets"]["tb"]["provider"]["kind"] == "table"
        assert "summary" in owner.get("/api/engine/tools/blk/data/rows?page=p&widget=st").json()
        assert owner.get("/api/engine/tools/blk/data/rows?page=p&widget=tb").json()["columns"][0]["key"] == "entity"
        assert owner.get("/api/engine/tools/blk/data/rows?page=p&widget=zz").status_code == 404
        # the code block: its own document, its own (looser) policy, framed by us only
        r = owner.get("/api/engine/tools/blk/html/p/cd")
        assert r.status_code == 200 and "text/html" in r.headers["content-type"] and "<b>hi</b>" in r.text
        assert "unsafe-inline" in r.headers["content-security-policy"] and "frame-ancestors 'self'" in r.headers["content-security-policy"]
        assert owner.get("/api/engine/tools/blk/html/p/tb").status_code == 404  # not a code block
        # the picture, and never anything else from the folder
        r = owner.get("/api/engine/tools/blk/assets/logo.png")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:4] == b"\x89PNG"
        assert owner.get("/api/engine/tools/blk/assets/../tool.json").status_code in (404, 400)
        assert owner.get("/api/engine/tools/blk/assets/tool.json").status_code == 404
        assert user.get("/api/engine/tools/blk/assets/logo.png").status_code == 403
        # the app's own pages keep the strict policy, and may frame https sites
        home = owner.get("/")
        csp = home.headers["content-security-policy"]
        assert "script-src 'self'" in csp and "frame-src 'self' https:" in csp
    finally:
        from cbx.config import ROOT

        loader.reload(ROOT / "tools")


class _Api(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        data = json.dumps({"data": {"items": [{"id": 7, "name": "Bolts", "qty": 12, "meta": {"supplier": "Acme"}}, {"id": 8, "name": "Nuts", "qty": 3, "meta": {"supplier": "Bolt & Co"}}]}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture()
def api_server(monkeypatch):
    monkeypatch.setattr(settings, "api_allow_private", True)
    srv = HTTPServer(("127.0.0.1", 0), _Api)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_an_api_connection_feeds_a_table(owner, admin, api_server, tmp_path, monkeypatch):
    r = admin.post("/api/connections", json={"name": "Stock service", "base_url": api_server, "auth_kind": "header", "header_name": "X-API-Key", "secret": "k-123"})
    assert r.status_code == 200, r.text
    slug = r.json()["connection"]["slug"]
    r_conn = r.json()["connection"]
    page = {"title": "page.p.title", "layout": {"sections": [{"id": "s1", "columns": [{"width": 12, "widgets": [
        {"id": "tb", "type": "table", "source": {"api": slug, "path": "/items", "rows": "data.items", "columns": [{"key": "name", "label": "col.name"}, {"key": "qty", "label": "col.qty", "type": "int"}, {"key": "supplier", "from": "meta.supplier", "label": "col.supplier"}]}}]}]}]}}
    _folder(tmp_path, "stock", {"p": page}, en={"page.p.title": "P", "col.name": "Name", "col.qty": "Qty", "col.supplier": "Supplier"})
    monkeypatch.setattr(loader, "TOOLS_ROOT", tmp_path)
    loader.reload(tmp_path)
    try:
        from cbx.api import engine_api

        engine_api._api_cache.clear()
        # 1.0.48: a connection is only open to the tools ticked on it
        r = owner.get("/api/engine/tools/stock/data/rows?page=p&widget=tb")
        assert r.status_code == 403 and "not open to this tool" in r.json()["detail"]
        cid = r_conn["id"]
        assert admin.patch(f"/api/connections/{cid}", json={"tools": ["stock"]}).status_code == 200
        engine_api._api_cache.clear()
        d = owner.get("/api/engine/tools/stock/data/rows?page=p&widget=tb").json()
        assert d["total"] == 2 and d["rows"][0]["name"] == "Bolts" and d["rows"][0]["supplier"] == "Acme" and d["rows"][1]["qty"] == 3
        assert d["source"] == "Stock service" and [c["key"] for c in d["columns"]] == ["name", "qty", "supplier"]
        # cached for the next half minute: the second read is not a second call
        assert owner.get("/api/engine/tools/stock/data/rows?page=p&widget=tb").json()["total"] == 2
        assert len(engine_api._api_cache) == 1
        # the Builder can try a source before it is saved
        pv = owner.post("/api/builder/tools/stock/preview-widget", json={"widget": {"id": "x", "type": "table", "source": {"api": slug, "path": "/items", "rows": "data.items", "columns": [{"key": "name"}]}}}).json()
        assert pv["total"] == 2 and pv["rows"][0]["name"] == "Bolts"
        bad = owner.post("/api/builder/tools/stock/preview-widget", json={"widget": {"id": "x", "type": "table", "source": {"api": "no-such", "path": "/", "rows": ".", "columns": [{"key": "a"}]}}}).json()
        assert "does not exist" in bad["note"]
    finally:
        from cbx.config import ROOT

        loader.reload(ROOT / "tools")


def test_builder_pictures_and_code_preview(owner, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    up = owner.post("/api/builder/tools/pics/assets", files={"files": ("Our Logo.png", _png(), "image/png")})
    assert up.status_code == 200, up.text
    assert up.json()["saved"][0]["name"] == "Our_Logo.png" and (tmp_path / "builder" / "tools" / "pics" / "assets" / "Our_Logo.png").exists()
    assert owner.get("/api/builder/tools/pics/assets/Our_Logo.png").headers["content-type"] == "image/png"
    assert owner.post("/api/builder/tools/pics/assets", files={"files": ("evil.svg", b"<svg><script>alert(1)</script></svg>", "image/svg+xml")}).status_code == 400
    assert owner.post("/api/builder/tools/pics/assets", files={"files": ("notes.txt", b"hi", "text/plain")}).status_code == 400
    # saving the folder keeps the pictures
    folder = {"manifest": {"title": "tool.title", "icon": "list", "menu": {"section": "My tools", "order": 9}, "access": {"roles": ["owner"]}, "home": "p"},
              "pages": {"p": {"title": "page.p.title", "layout": {"sections": [{"id": "s1", "columns": [{"width": 12, "widgets": [{"id": "im", "type": "image", "asset": "Our_Logo.png"}, {"id": "cd", "type": "code", "code": "<i>x</i>"}]}]}]}}},
              "data": {}, "languages": {"en": {"tool.title": "Pics", "page.p.title": "P"}}, "readme": "# Pics\n"}
    d = owner.put("/api/builder/tools/pics", json=folder).json()
    assert d["errors"] == [] and d["assets"][0]["name"] == "Our_Logo.png" and d["layouts"]["p"]["sections"][0]["columns"][0]["widgets"][0]["asset"] == "Our_Logo.png"
    r = owner.get("/api/builder/tools/pics/html/p/cd")
    assert r.status_code == 200 and "<i>x</i>" in r.text and "unsafe-inline" in r.headers["content-security-policy"]
    assert owner.delete("/api/builder/tools/pics/assets/Our_Logo.png").json()["ok"]
    assert owner.get("/api/builder/tools/pics").json()["errors"]  # the picture is gone: the block says so
