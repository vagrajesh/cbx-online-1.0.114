"""1.0.59: other systems read a table with a key — the exposed columns only, within the key's limits."""
import pytest

from cbx.config import ROOT, settings
from cbx.engine import loader
from cbx.security import ratelimit
from tests.test_builder_types import _tool


@pytest.fixture()
def shared(tmp_path, monkeypatch):
    data = {
        "sites": {"columns": [{"key": "name", "type": "text", "label": "col.name"}, {"key": "owner", "type": "person", "label": "col.owner"},
                              {"key": "account", "type": "text", "label": "col.account"}, {"key": "paper", "type": "file", "label": "col.paper"}],
                  "expose": {"columns": ["name", "owner"]}},
        "private": {"columns": [{"key": "text", "type": "text", "label": "col.text"}]},
    }
    pages = {"tasks": {"title": "page.tasks.title", "layout": {"sections": [{"id": "s1", "columns": [{"width": 12, "widgets": [{"id": "t", "type": "table", "source": {"data": "sites"}}]}]}]}}}
    _tool(tmp_path, "keyed", pages=pages, data=data, flows={}, en={"col.owner": "Owner", "col.account": "Account", "col.paper": "Paper"})
    monkeypatch.setattr(loader, "TOOLS_ROOT", tmp_path)
    reg = loader.reload(tmp_path)
    assert "keyed" in reg.tools, reg.broken["keyed"].errors
    monkeypatch.setattr(settings, "data_keys_https_only", False)
    ratelimit.reset_all()
    yield
    ratelimit.reset_all()
    loader.reload(ROOT / "tools")


@pytest.fixture()
def outside(client):
    """Another system: its own connection, no one signed in."""
    from fastapi.testclient import TestClient

    from cbx.main import app

    return TestClient(app)


def _key(owner, **kw):
    r = owner.post("/api/data-keys", json={"name": "Finance system", "tool": "keyed", "table": "sites", **kw})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_key_reads_the_exposed_columns_and_nothing_else(shared, owner, outside):
    owner.post("/api/engine/tools/keyed/data/sites", json={"values": {"name": "Rosebank", "owner": None, "account": "ACC-SECRET-1"}})
    made = _key(owner)
    key = made["key"]
    assert key.startswith("cbxk_") and made["data_key"]["state"] == "active"
    listed = owner.get("/api/data-keys").json()
    assert key not in str(listed) and listed["tables"][0]["table"] == "sites" and all(t["table"] != "private" for t in listed["tables"])

    r = outside.get("/api/shared/keyed/sites", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["columns"] == ["name", "owner"] and body["rows"][0]["name"] == "Rosebank"
    assert "account" not in body["rows"][0] and "paper" not in body["rows"][0] and "ACC-SECRET" not in r.text
    assert outside.get("/api/shared/keyed/sites", headers={"X-CBX-Key": key}).status_code == 200
    # new rows only
    assert outside.get(f"/api/shared/keyed/sites?after_id={body['next_after_id']}", headers={"X-CBX-Key": key}).json()["rows"] == []
    k = next(x for x in owner.get("/api/data-keys").json()["keys"] if x["id"] == made["data_key"]["id"])
    assert k["calls"] == 3 and k["last_used_at"]


def test_what_a_key_may_not_do(shared, owner, outside, user):
    key = _key(owner)["key"]
    other = owner.post("/api/data-keys", json={"name": "x", "tool": "keyed", "table": "private"})
    assert other.status_code == 400 and "not open to other systems" in other.json()["detail"]
    assert owner.post("/api/data-keys", json={"name": "x", "tool": "keyed", "table": "sites", "days": 400}).status_code == 400
    assert user.post("/api/data-keys", json={"name": "x", "tool": "keyed", "table": "sites"}).status_code == 403
    assert outside.get("/api/shared/keyed/sites").status_code == 401                          # no key
    assert outside.get("/api/shared/keyed/sites?key=" + key).status_code == 401                # never from the address
    assert outside.get("/api/shared/keyed/private", headers={"X-CBX-Key": key}).status_code == 403  # another table
    assert outside.get("/api/shared/keyed/sites", headers={"X-CBX-Key": "cbxk_nope"}).status_code == 401
    # a signed-in browser is not a key
    assert owner.get("/api/shared/keyed/sites").status_code == 401


def test_limits_addresses_revoking_and_https(shared, owner, outside, monkeypatch):
    made = _key(owner, rate_per_min=2)
    key, kid = made["key"], made["data_key"]["id"]
    h = {"X-CBX-Key": key}
    assert [outside.get("/api/shared/keyed/sites", headers=h).status_code for _ in range(3)] == [200, 200, 429]
    far = _key(owner, allowed_ips=["10.9.9.0/24"])["key"]
    assert outside.get("/api/shared/keyed/sites", headers={"X-CBX-Key": far}).status_code == 403
    assert owner.post("/api/data-keys", json={"name": "x", "tool": "keyed", "table": "sites", "allowed_ips": ["not an ip"]}).status_code == 400
    ratelimit.reset_all()
    assert owner.post(f"/api/data-keys/{kid}/revoke").json()["data_key"]["state"] == "revoked"
    r = outside.get("/api/shared/keyed/sites", headers=h)
    assert r.status_code == 401 and "revoked" in r.json()["detail"]
    monkeypatch.setattr(settings, "data_keys_https_only", True)
    fresh = _key(owner)["key"]
    r = outside.get("/api/shared/keyed/sites", headers={"X-CBX-Key": fresh})
    assert r.status_code == 403 and "HTTPS" in r.json()["detail"]


def test_wrong_keys_make_an_address_wait(shared, outside):
    codes = [outside.get("/api/shared/keyed/sites", headers={"X-CBX-Key": f"cbxk_guess{i}"}).status_code for i in range(22)]
    assert codes[:20] == [401] * 20 and codes[-1] == 429


def test_the_folder_says_what_may_be_read(tmp_path):
    data = {"a": {"columns": [{"key": "t", "type": "text"}, {"key": "f", "type": "file"}], "expose": {"columns": ["t", "f", "nope"]}},
            "b": {"columns": [{"key": "t", "type": "text"}], "expose": {"columns": []}}}
    _tool(tmp_path, "bad59", data=data, pages={}, flows={})
    errs = " | ".join(loader.load_all(tmp_path).broken["bad59"].errors)
    for needle in ("'f' is a file column", "'nope', which is not a column", "needs the columns they may read"):
        assert needle in errs, needle


def test_the_owners_document_lists_what_is_open_but_never_a_key(shared, owner):
    key = _key(owner)["key"]
    doc = owner.get("/api/owner/document").json()["markdown"]
    assert "## Open to other systems" in doc and "Finance system" in doc and "keyed · sites" in doc
    assert key not in doc and key[:12] not in doc
