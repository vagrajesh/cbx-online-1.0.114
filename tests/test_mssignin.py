"""1.0.97 (CBX-Connect 2.8.8's tests): sign in with Microsoft. The company signs the person in with its own MFA;
CBX lets in only an active CBX account with that email, pins the Microsoft account at the first sign-in, and
refuses everything that doesn't check out. Microsoft itself is faked here: no network."""
import base64
import json
import time
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from cbx import mssignin as M
from cbx.config import settings
from cbx.db import SessionLocal
from cbx.main import app
from cbx.models import AppState, User

from .conftest import _make, login

TENANT = "11111111-2222-3333-4444-555555555555"
CLIENT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _jwt(claims: dict) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    return f"{enc({'alg': 'RS256', 'typ': 'JWT'})}.{enc(claims)}.c2ln"


@pytest.fixture()
def ms(owner, monkeypatch):
    monkeypatch.setattr(settings, "public_url", "https://cbx.test.local")
    r = owner.put("/api/auth/microsoft/settings", json={"tenant": TENANT, "client_id": CLIENT, "secret": "s3cret-from-entra-xyz", "enabled": True})
    assert r.status_code == 200, r.text
    seen = {}

    def fake_exchange(db, cfg, code, verifier):
        assert cfg["secret"] == "s3cret-from-entra-xyz" and code == "the-code" and len(verifier) > 40
        return {"id_token": _jwt(seen["claims"])}

    monkeypatch.setattr(M, "_exchange", fake_exchange)
    yield owner, seen
    with SessionLocal() as s:
        s.query(AppState).filter_by(key=M.STATE_KEY).delete()
        s.commit()


def _go(c, seen, email, oid="oid-1", **over):
    r = c.get("/auth/microsoft", follow_redirects=False)
    assert r.status_code == 303, r.text
    q = parse_qs(urlparse(r.headers["location"]).query)
    now = int(time.time())
    seen["claims"] = {"iss": f"https://login.microsoftonline.com/{TENANT}/v2.0", "aud": CLIENT, "tid": TENANT, "oid": oid, "nonce": q["nonce"][0],
                      "preferred_username": email, "iat": now, "nbf": now, "exp": now + 3600, **over}
    return c.get(f"/auth/microsoft/callback?code=the-code&state={q['state'][0]}", follow_redirects=False), q


def test_set_up_sign_in_and_refusals(ms):
    owner, seen = ms
    card = owner.get("/api/auth/microsoft/settings").json()
    assert card["working"] and card["redirect_uri"] == "https://cbx.test.local/auth/microsoft/callback" and "s3cret" not in json.dumps(card)
    assert TestClient(app).get("/api/auth/microsoft").json() == {"enabled": True}
    _make(owner, "thabo.ms@test.local", "Thabo Ms", "user")
    c = TestClient(app)
    r, q = _go(c, seen, "Thabo.Ms@test.local")
    assert q["code_challenge_method"] == ["S256"] and r.headers["location"] == "/", r.headers.get("location")
    assert c.get("/api/auth/me").json()["user"]["email"] == "thabo.ms@test.local"
    with SessionLocal() as s:
        assert s.query(User).filter_by(email="thabo.ms@test.local").one().prefs["ms_oid"] == "oid-1"
    assert _go(TestClient(app), seen, "nobody@test.local")[0].headers["location"] == "/?ms=nouser"
    assert _go(TestClient(app), seen, "thabo.ms@test.local", tid="99999999-2222-3333-4444-555555555555")[0].headers["location"] == "/?ms=failed"
    assert _go(TestClient(app), seen, "thabo.ms@test.local", nonce="replayed")[0].headers["location"] == "/?ms=failed"
    assert _go(TestClient(app), seen, "thabo.ms@test.local", oid="oid-other")[0].headers["location"] == "/?ms=other"
    c2 = TestClient(app)
    c2.get("/auth/microsoft", follow_redirects=False)
    c2.cookies.clear()
    assert c2.get("/auth/microsoft/callback?code=the-code&state=x", follow_redirects=False).headers["location"] == "/?ms=state"


def test_only_the_owner_and_only_on_https(ms, admin, monkeypatch):
    owner, _ = ms
    assert admin.get("/api/auth/microsoft/settings").status_code == 403
    assert owner.put("/api/auth/microsoft/settings", json={"tenant": "common"}).status_code == 400
    monkeypatch.setattr(settings, "public_url", "http://cbx-server:8080")
    assert TestClient(app).get("/api/auth/microsoft").json() == {"enabled": False}
    assert TestClient(app).get("/auth/microsoft", follow_redirects=False).headers["location"] == "/?ms=off"
