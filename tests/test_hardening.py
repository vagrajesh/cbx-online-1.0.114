"""1.0.41 hardening: forwarded headers believed only from a trusted proxy, uploads capped while they are
read, Secure cookies over HTTPS, every script at a versioned address with a CSP behind it, the API relay
refusing this machine and its neighbours, atomic file names, hashed device cookies, request numbers that
never collide, one winner for approve/decline, a mail check on the pulse, a git snapshot per promote."""
from __future__ import annotations

import io
import sqlite3
import threading
from pathlib import Path

import pytest
from fastapi import Request

from cbx import VERSION
from cbx.config import settings
from cbx.security import deps


def _req(peer: str, headers: dict | None = None, scheme: str = "http") -> Request:
    hdrs = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": hdrs, "client": (peer, 1234), "scheme": scheme, "query_string": b"", "server": ("testserver", 80)})


# ---- forwarded headers -----------------------------------------------------------------------------

def test_forwarded_for_is_believed_only_from_the_proxy():
    # the ngrok agent on this machine appends the real address: believed, and it is the LAST one
    assert deps.client_ip(_req("127.0.0.1", {"X-Forwarded-For": "6.6.6.6, 41.1.2.3"})) == "41.1.2.3"
    # a browser on the LAN talking to us directly is its own address, whatever it claims
    assert deps.client_ip(_req("192.168.1.50", {"X-Forwarded-For": "6.6.6.6"})) == "192.168.1.50"
    assert deps.client_ip(_req("192.168.1.50")) == "192.168.1.50"
    assert deps.is_https(_req("127.0.0.1", {"X-Forwarded-Proto": "https"})) is True
    assert deps.is_https(_req("192.168.1.50", {"X-Forwarded-Proto": "https"})) is False
    assert deps.is_https(_req("192.168.1.50", scheme="https")) is True


def test_sign_in_limit_cannot_be_dodged_with_a_new_forwarded_address(client, monkeypatch):
    from cbx.security import ratelimit as RL

    RL.forget("testclient")
    monkeypatch.setattr(settings, "login_rate_limit", 4)
    codes = []
    for i in range(6):
        r = client.post("/api/auth/login", json={"email": f"nobody{i}@test.local", "password": "wrong"}, headers={"X-Forwarded-For": f"10.9.{i}.{i}"})
        codes.append(r.status_code)
    RL.forget("testclient")
    assert 429 in codes and codes[:4] == [401] * 4


def test_forgot_is_rate_limited_too(client, monkeypatch):
    from cbx.security import ratelimit as RL

    RL.forget("testclient")
    monkeypatch.setattr(settings, "login_rate_limit", 3)
    codes = [client.post("/api/auth/forgot", json={"email": "x@test.local"}).status_code for _ in range(5)]
    RL.forget("testclient")
    assert codes[:3] == [200] * 3 and 429 in codes[3:]


# ---- uploads --------------------------------------------------------------------------------------

def test_uploads_are_refused_at_the_cap_not_after(owner, monkeypatch):
    monkeypatch.setattr(settings, "max_attachment_mb", 1)
    big = io.BytesIO(b"x" * (2 * 1024 * 1024 + 10))
    r = owner.post("/api/requests/attachment", files={"files": ("big.bin", big, "application/octet-stream")})
    assert r.status_code == 413 and "1 MB" in r.json()["detail"]
    small = io.BytesIO(b"y" * (200 * 1024))
    r = owner.post("/api/requests/attachment", files={"files": ("small.bin", small, "application/octet-stream")})
    assert r.status_code == 200 and r.json()["size"] == 200 * 1024


# ---- cookies ---------------------------------------------------------------------------------------

def test_cookies_are_secure_over_https_and_plain_on_the_lan(client, owner_pw):
    from cbx.api import auth as AUTH

    assert AUTH._secure(_req("127.0.0.1", {"X-Forwarded-Proto": "https"})) is True
    assert AUTH._secure(_req("192.168.1.50")) is False
    assert AUTH._secure(None) is False
    # the test client is a plain-HTTP peer that is not a proxy: the session cookie is not Secure
    r = client.post("/api/auth/login", json={"email": "owner@test.local", "password": owner_pw})
    assert r.status_code in (200, 401)  # 401 when the owner already changed the temporary password
    if r.status_code == 200:
        assert "secure" not in " ".join(r.headers.get_list("set-cookie")).lower()


# ---- static addresses and CSP ----------------------------------------------------------------------

def test_every_script_has_a_versioned_address_and_a_policy(client):
    r = client.get("/")
    assert r.status_code == 200
    assert f'"/static/{VERSION}/app.js"' in r.text and f'"/static/{VERSION}/styles.css"' in r.text
    assert '"/static/app.js"' not in r.text
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert "object-src 'none'" in r.headers["content-security-policy"]
    v = client.get(f"/static/{VERSION}/views/index.js")
    assert v.status_code == 200 and "immutable" in v.headers["cache-control"]
    plain = client.get("/static/app.js")
    assert plain.status_code == 200 and plain.headers["cache-control"] == "no-cache"
    api = client.get("/api/health")
    assert "content-security-policy" in api.headers and api.headers["cache-control"] == "no-store"
    gone = client.get("/node-red/", follow_redirects=False)  # Node-RED left CBX in 1.0.48: an ordinary page, with the policy
    assert "content-security-policy" in gone.headers


def test_no_inline_handlers_anywhere_in_the_shell():
    import re

    web = Path(__file__).resolve().parents[1] / "web"
    bad = []
    for f in list(web.rglob("*.js")) + list(web.rglob("*.html")):
        for n, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if re.search(r"""\son(click|change|input|load|submit|keyup|keydown)=["']""", line):
                bad.append(f"{f.relative_to(web)}:{n}")
    assert not bad, bad


# ---- API relay: this machine and its neighbours ----------------------------------------------------

def test_relay_refuses_this_machine_and_link_local_and_private_by_default(monkeypatch):
    from cbx.connections import service as C

    monkeypatch.setattr(settings, "api_allow_private", False)
    assert C.host_refused("127.0.0.1")
    assert C.host_refused("localhost")
    assert C.host_refused("::1")
    assert C.host_refused("169.254.169.254")
    assert C.host_refused("0.0.0.0")
    assert C.host_refused("10.1.2.3") and "CBX_API_ALLOW_PRIVATE" in C.host_refused("192.168.0.9")
    assert C.host_refused("8.8.8.8") == ""
    assert C.host_refused("no-such-host-xyz.invalid") == ""  # unknown names fail at call time, plainly
    monkeypatch.setattr(settings, "api_allow_private", True)
    assert C.host_refused("10.1.2.3") == ""
    assert C.host_refused("169.254.169.254") and C.host_refused("0.0.0.0")  # never, whatever the setting
    # loopback stays shut too — except in a test run with the flag on, which is how the fake services in
    # tests/test_connections.py are reached; that exception is keyed on CBX_TESTING and nothing else
    import os

    assert (C.host_refused("127.0.0.1") == "") == bool(os.environ.get("CBX_TESTING"))


def test_relay_refuses_ollama_and_node_red_addresses_when_saved(owner):
    for url in ("http://127.0.0.1:11434", "http://localhost:1880/flows", "http://169.254.169.254/latest"):
        r = owner.post("/api/connections", json={"name": "bad " + url[-6:], "base_url": url, "auth_kind": "none"})
        assert r.status_code == 400, (url, r.text)
        assert "outside service" in r.json()["detail"] or "private" in r.json()["detail"]


# ---- files -----------------------------------------------------------------------------------------

def test_same_name_uploaded_at_the_same_moment_gives_two_files(tmp_path):
    from cbx.warehouse import storage

    made: list[Path] = []
    lock = threading.Lock()

    def one(i):
        p = storage._create_unique(tmp_path / "report.pdf", f"body {i}".encode())
        with lock:
            made.append(p)

    ts = [threading.Thread(target=one, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(made) == 8 and len(set(made)) == 8
    assert (tmp_path / "report.pdf").exists() and (tmp_path / "report (2).pdf").exists() and (tmp_path / "report (8).pdf").exists()
    assert sorted(p.read_bytes() for p in made) == sorted(f"body {i}".encode() for i in range(8))


# ---- device cookies ---------------------------------------------------------------------------------

def test_device_cookies_are_stored_hashed(db):
    from cbx.models import User
    from cbx.security import devices as D

    u = db.query(User).filter(User.email == "owner@test.local").one()
    row, raw = D.remember(db, u, ip="1.2.3.4", agent="pytest")
    assert row.token != raw and row.token == D.token_hash(raw) and len(row.token) == 64
    assert D.trusted(db, u, raw) is not None
    assert D.trusted(db, u, row.token) is None  # the stored value itself opens nothing
    row.revoked = True
    db.flush()


def test_migration_hashes_old_raw_device_tokens(tmp_path):
    from sqlalchemy import create_engine, text

    from cbx import migrate as M
    from cbx.security.devices import token_hash

    eng = create_engine(f"sqlite:///{tmp_path / 'm.sqlite3'}")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE trusted_devices (id INTEGER PRIMARY KEY, token VARCHAR(80))"))
        c.execute(text("INSERT INTO trusted_devices (id, token) VALUES (1, 'rawtokenvalue-abc'), (2, :h)"), {"h": token_hash("already")})
        M._hash_device_tokens(c)
        rows = dict(c.execute(text("SELECT id, token FROM trusted_devices")).all())
    assert rows[1] == token_hash("rawtokenvalue-abc") and rows[2] == token_hash("already")


def test_migration_makes_grid_cells_unique_keeping_the_one_with_a_file(tmp_path):
    from sqlalchemy import create_engine, text

    from cbx import migrate as M

    eng = create_engine(f"sqlite:///{tmp_path / 'g.sqlite3'}")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE watched_docs (id INTEGER PRIMARY KEY, entity_id INTEGER, doc_type_id INTEGER, file_id INTEGER)"))
        c.execute(text("INSERT INTO watched_docs VALUES (1, 5, 7, NULL), (2, 5, 7, 99), (3, 5, 7, NULL), (4, 5, 8, NULL), (5, 6, NULL, NULL), (6, 6, NULL, NULL)"))
        M._unique_grid_cells(c)
        left = [r[0] for r in c.execute(text("SELECT id FROM watched_docs ORDER BY id")).all()]
        assert left == [2, 4, 5, 6]  # the cell with the file survives; untyped lines are untouched
        with pytest.raises(Exception):
            c.execute(text("INSERT INTO watched_docs VALUES (7, 5, 8, NULL)"))


# ---- requests --------------------------------------------------------------------------------------

def test_a_request_number_collision_is_retried_not_a_500(owner, monkeypatch):
    from cbx import requests_flow as F

    real = F.next_number
    calls = {"n": 0}

    def clashing(db):
        calls["n"] += 1
        n = real(db)
        return n - 1 if calls["n"] == 1 and n > F.FIRST_NUMBER else n  # first try: a number already taken

    monkeypatch.setattr(F, "next_number", clashing)
    r0 = owner.post("/api/requests", json={"problem": "The first request so a number exists already.", "impact": "", "wish": ""})
    assert r0.status_code == 200
    calls["n"] = 0
    r = owner.post("/api/requests", json={"problem": "The second request collides on its number once.", "impact": "", "wish": ""})
    assert r.status_code == 200, r.text
    assert calls["n"] == 2
    assert r.json()["request"]["number"] == r0.json()["request"]["number"] + 1


def test_only_one_admin_wins_approve_or_decline(owner, db):
    from cbx.api import requests_api as RA
    from cbx.models import Request

    r = owner.post("/api/requests", json={"problem": "Something two admins will both click on.", "impact": "", "wish": ""})
    rid = r.json()["request"]["id"]
    req = db.get(Request, rid)
    st = req.status
    assert RA._claim(db, req, st, "declined") is True
    assert req.status == "declined"
    assert RA._claim(db, req, st, "approving") is False
    db.commit()


# ---- the pulse watches the mailbox ---------------------------------------------------------------

def test_pulse_mail_check(db, monkeypatch):
    from cbx import mail
    from cbx.cexa import pulse as P
    from cbx.models import MailLog

    monkeypatch.setattr(mail, "configured", lambda: (False, "Email is switched off on this server (CBX_MAIL_ENABLED is not true)."))
    ok, val, _ = P.CHECKS["mail"](db)
    assert ok and val == "off"
    monkeypatch.setattr(mail, "configured", lambda: (True, "Sending as x through y."))
    db.add(MailLog(to="a@test.local", subject="reminder", preview="", kind="reminder", ref="", ok=False, detail="SMTP said no"))
    db.flush()
    ok, val, sentence = P.CHECKS["mail"](db)
    assert not ok and "failed" in val and "Sign-in codes" in sentence
    db.add(MailLog(to="a@test.local", subject="later", preview="", kind="test", ref="", ok=True, detail="sent"))
    db.flush()
    ok, _, _ = P.CHECKS["mail"](db)
    assert ok  # something went out after the failure: the mailbox works again
    for r in db.query(MailLog).filter(MailLog.to == "a@test.local").all():
        db.delete(r)
    db.add(MailLog(to="b@test.local", subject="x", preview="", kind="compose", ref="", ok=False, detail="refused: not an address CBX may write to"))
    db.flush()
    ok, _, _ = P.CHECKS["mail"](db)
    assert ok  # a refused address is a rule working, not the mailbox failing
    for r in db.query(MailLog).filter(MailLog.to == "b@test.local").all():
        db.delete(r)
    db.commit()


# ---- a git snapshot per promote ---------------------------------------------------------------------

def test_git_snapshot_of_the_live_tree(tmp_path, monkeypatch):
    import shutil

    from cbx.versions import injector

    if not shutil.which("git"):
        pytest.skip("no git here")
    live = tmp_path / "live"
    (live / "cbx").mkdir(parents=True)
    (live / "cbx" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (live / ".env").write_text("CBX_SMTP_PASSWORD=secret\n", encoding="utf-8")
    (live / "data").mkdir()
    (live / "data" / "cbx.sqlite3").write_bytes(b"db")
    monkeypatch.setattr(injector, "LIVE_ROOT", live)
    assert injector.git_snapshot("promote 1.0.41 by test") == "snapshot committed"
    assert injector.git_snapshot("promote again") == "nothing changed — no snapshot"
    (live / "cbx" / "a.py").write_text("x = 2\n", encoding="utf-8")
    assert injector.git_snapshot("promote 1.0.42") == "snapshot committed"
    import subprocess

    tracked = subprocess.run(["git", "ls-files"], cwd=str(live), capture_output=True, text=True).stdout.split()
    assert "cbx/a.py" in tracked and ".gitignore" in tracked
    assert ".env" not in tracked and not any(t.startswith("data/") for t in tracked)
    log = subprocess.run(["git", "log", "--oneline"], cwd=str(live), capture_output=True, text=True).stdout.splitlines()
    assert len(log) == 2 and "1.0.42" in log[0]


def test_sqlite_read_of_grid_constraint_exists_in_a_fresh_database():
    con = sqlite3.connect(f"file:{settings.data_dir / 'cbx.sqlite3'}?mode=ro", uri=True)
    try:
        idx = [r[1] for r in con.execute("PRAGMA index_list('watched_docs')").fetchall()]
    finally:
        con.close()
    assert any("uq_entity_doctype_cell" in i or i.startswith("sqlite_autoindex") for i in idx)
