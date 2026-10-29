"""Owner Home: the "Needs attention" list (stuck batches, harvest, expired documents, model placement,
failed versions) and the model diagnostic behind /api/cexa/status."""
from datetime import timedelta

from cbx.db import utcnow
from cbx.models import Batch, HarvestSource, ModelCall, Template, User, WatchedDoc


def _owner(db) -> User:
    return db.query(User).filter(User.email == "owner@test.local").one()


def _template(db, owner_id: int) -> Template:
    t = Template(name="Home test template", owner_id=owner_id)
    db.add(t)
    db.flush()
    return t


def _items(client):
    r = client.get("/api/home/attention")
    assert r.status_code == 200, r.text
    return r.json()["items"]


def test_attention_is_quiet_when_nothing_is_wrong(owner):
    items = _items(owner)
    keys = [i["key"] for i in items]
    # Node-RED left CBX in 1.0.48; under pytest nothing is probed for it
    assert not [k for k in keys if k.startswith("nodered")]
    # the database is shared with the other test files (a harvest source left in error, a version that failed
    # its sandbox tests) and those are rightly listed; what can never appear under pytest is a model finding
    assert not [k for k in keys if k.startswith("model_")]
    for i in items:
        assert set(i) >= {"key", "text", "level", "go", "label"}


def test_attention_lists_stuck_review_and_failed_batches(owner, db):
    me = _owner(db)
    t = _template(db, me.id)
    now = utcnow()
    db.add(Batch(template_id=t.id, user_id=me.id, name="Forgotten upload", status="uploading", created_at=now - timedelta(hours=3)))
    db.add(Batch(template_id=t.id, user_id=me.id, name="Fresh upload", status="uploading", created_at=now - timedelta(minutes=10)))  # too new to nag about
    db.add(Batch(template_id=t.id, user_id=me.id, name="Harvest · Telkom", status="review", cells_for_user=17455, finished_at=now - timedelta(days=1, hours=2), created_at=now - timedelta(days=2)))
    db.add(Batch(template_id=t.id, user_id=me.id, name="Reviewed today", status="review", cells_for_user=3, finished_at=now - timedelta(hours=2)))  # under a day: fine
    db.add(Batch(template_id=t.id, user_id=me.id, name="Broken", status="failed", error="Ollama not reachable at http://127.0.0.1:11434\nmore detail", finished_at=now - timedelta(hours=5)))
    db.commit()
    try:
        items = _items(owner)
        by_text = " | ".join(i["text"] for i in items)
        assert "'Forgotten upload' has sat in 'uploading' for 3 h with no PDFs" in by_text and "Probably abandoned" in by_text
        assert "Fresh upload" not in by_text and "Reviewed today" not in by_text
        rev = next(i for i in items if i["key"].startswith("batch_review:"))
        assert "waiting for a person" in rev["text"] and "17 455 cells" in rev["text"] and rev["go"].startswith("pdf-studio?batch=")
        fail = next(i for i in items if i["key"].startswith("batch_failed:"))
        assert fail["level"] == "crit" and "Ollama not reachable" in fail["text"] and "more detail" not in fail["text"]
        # critical first, then the biggest number
        levels = [i["level"] for i in items]
        assert levels == sorted(levels, key={"crit": 0, "warn": 1, "info": 2}.get)
    finally:
        for b in db.query(Batch).filter(Batch.template_id == t.id).all():
            db.delete(b)
        db.commit()


def test_many_stuck_uploads_collapse_into_one_line(owner, db):
    me = _owner(db)
    t = _template(db, me.id)
    now = utcnow()
    for i in range(6):
        db.add(Batch(template_id=t.id, user_id=me.id, name=f"Empty {i}", status="uploading", created_at=now - timedelta(days=1, hours=i)))
    db.commit()
    try:
        items = _items(owner)
        ups = [i for i in items if i["key"].startswith("batch")]
        assert len(ups) == 1 and ups[0]["key"] == "batches_uploading"
        assert "6 PDF batches never got past 'uploading'" in ups[0]["text"] and "6 of them have no PDFs" in ups[0]["text"]
        assert ups[0]["go"] == "pdf-studio" and ups[0]["n"] == 6
    finally:
        for b in db.query(Batch).filter(Batch.template_id == t.id).all():
            db.delete(b)
        db.commit()


def test_attention_harvest_and_expired_documents(owner, admin, user, db):
    me = _owner(db)
    t = _template(db, me.id)
    now = utcnow()
    bad = HarvestSource(name="Telkom", template_id=t.id, kind="local", path="C:\\Bills\\Telkom", owner_id=me.id, status="error", last_error="The folder C:\\Bills\\Telkom does not exist.\nstack")
    stale = HarvestSource(name="Swiftnet", template_id=t.id, kind="local", path="C:\\Bills\\Swiftnet", owner_id=me.id, status="ok", every_minutes=15, last_run_at=now - timedelta(hours=2))
    fine = HarvestSource(name="Eskom", template_id=t.id, kind="local", path="C:\\Bills\\Eskom", owner_id=me.id, status="ok", every_minutes=15, last_run_at=now - timedelta(minutes=20))
    paused = HarvestSource(name="Old", template_id=t.id, kind="local", path="C:\\Bills\\Old", owner_id=me.id, status="paused", active=False, last_run_at=now - timedelta(days=30))
    db.add_all([bad, stale, fine, paused])
    db.add(WatchedDoc(title="Fire certificate", owner_id=me.id, expiry=(now - timedelta(days=3)).date()))
    db.add(WatchedDoc(title="Still valid", owner_id=me.id, expiry=(now + timedelta(days=3)).date()))
    db.commit()
    try:
        items = _items(owner)
        texts = {i["key"]: i for i in items}
        err = texts[f"harvest_error:{bad.id}"]
        assert err["level"] == "crit" and "does not exist" in err["text"] and "stack" not in err["text"] and err["go"] == "pdf-harvest"
        st = texts[f"harvest_stale:{stale.id}"]
        assert "has not run for 2 h" in st["text"] and "every 15 min" in st["text"]
        assert f"harvest_stale:{fine.id}" not in texts and f"harvest_stale:{paused.id}" not in texts
        assert texts["docs_expired"]["text"].startswith("1 document on the compliance register has expired") and texts["docs_expired"]["go"] == "doc-watch"
        # admins see the card too; users never do
        assert admin.get("/api/home/attention").status_code == 200
        assert user.get("/api/home/attention").status_code == 403
        # the admin's list has no owner-only buttons or version rows
        assert all(i["go"] != "cexa" and not i["key"].startswith("version_") for i in admin.get("/api/home/attention").json()["items"])
    finally:
        for s in (bad, stale, fine, paused):
            db.delete(s)
        for d in db.query(WatchedDoc).filter(WatchedDoc.title.in_(["Fire certificate", "Still valid"])).all():
            db.delete(d)
        db.commit()




class _R:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p

def _fake_ollama(monkeypatch, *, size=18_000_000_000, vram=None, down=False):
    """Pretend Ollama is live with qwen3.8:27b loaded; `vram` < `size` means part of it sits on the CPU."""
    import httpx

    from cbx.config import settings

    vram = size if vram is None else vram

    def get(url, timeout=None, **_):
        if down:
            raise httpx.ConnectError("connection refused")
        if url.endswith("/api/tags"):
            return _R({"models": [{"name": "qwen3.8:27b"}, {"name": "qwen3.5:9b"}]})
        if url.endswith("/api/ps"):
            return _R({"models": [{"name": "qwen3.8:27b", "size": size, "size_vram": vram, "context_length": 32768}]})
        raise AssertionError(url)

    monkeypatch.setattr(settings, "model_mode", "live")
    monkeypatch.setattr(httpx, "get", get)


def test_cexa_status_reports_gpu_placement_and_latency(owner, db, monkeypatch):
    from cbx.cexa import gateway

    # 24 h latency per task from ModelCall — the owner's 70 s calls
    # (task names other tests never use, so their stub calls don't change the averages)
    mine = [ModelCall(task="home_chat", model="qwen3.8:27b", latency_ms=70_000, tokens_in=10, tokens_out=10) for _ in range(3)]
    mine.append(ModelCall(task="home_ocr", model="glm-ocr", latency_ms=900, ok=False))
    mine.append(ModelCall(task="home_chat", model="qwen3.8:27b", latency_ms=1000, at=utcnow() - timedelta(days=2)))  # too old to count
    db.add_all(mine)
    db.commit()
    try:
        st = owner.get("/api/cexa/status").json()
        assert set(st) >= {"model", "mode", "online", "num_ctx", "loaded", "advice", "latency", "slow"}
        assert st["mode"] == "stub" and st["online"] is True and st["num_ctx"] == 32768 and st["slow"] is False
        chat = next(x for x in st["latency"] if x["task"] == "home_chat")
        assert chat["calls"] == 3 and chat["avg_s"] == 70.0 and chat["max_s"] == 70.0
        assert next(x for x in st["latency"] if x["task"] == "home_ocr")["failed"] == 1
        # live, model 62 % on the GPU → plain-English advice, slow verdict, and the Home card carries it
        _fake_ollama(monkeypatch, vram=int(18_000_000_000 * 0.62))
        st = owner.get("/api/cexa/status").json()
        assert st["online"] is True and st["loaded"][0]["gpu_pct"] == 62 and st["loaded"][0]["context"] == 32768
        assert st["advice"].startswith("qwen3.8:27b is 62% on the GPU — 38% is running on the CPU, which is why C-EXA is slow.")
        assert "CBX_NUM_CTX" in st["advice"] and "27b-nvfp4" in st["advice"] and st["slow"] is True
        home = _items(owner)
        slow = next(i for i in home if i["key"] == "model_slow")
        assert slow["text"] == st["advice"] and slow["go"] == "cexa"
        # fully on the GPU → no advice, but the 70 s average still says slow
        _fake_ollama(monkeypatch)
        st = owner.get("/api/cexa/status").json()
        assert st["loaded"][0]["gpu_pct"] == 100 and st["slow"] is True and "averaged 70 s" in st["advice"]
        # Ollama down in live mode → offline, critical Home item
        _fake_ollama(monkeypatch, down=True)
        st = owner.get("/api/cexa/status").json()
        assert st["online"] is False and "not answering" in st["advice"]
        off = next(i for i in _items(owner) if i["key"] == "model_offline")
        assert off["level"] == "crit" and "Ollama" in off["text"]
        # the plain-text version C-EXA reads
        txt = gateway.model_health_text(db)
        assert "OFFLINE" in txt and "home_chat 3 calls, avg 70.0 s" in txt
    finally:
        for m in mine:
            db.delete(m)
        db.commit()


def test_num_ctx_comes_from_env_or_dotenv(monkeypatch, tmp_path):
    from cbx.cexa import gateway

    monkeypatch.delenv("CBX_NUM_CTX", raising=False)
    monkeypatch.setattr(gateway, "ROOT", tmp_path)  # no .env here
    assert gateway.num_ctx() == 32768
    monkeypatch.setenv("CBX_NUM_CTX", "16384")
    assert gateway.num_ctx() == 16384
    monkeypatch.setenv("CBX_NUM_CTX", "nonsense")
    assert gateway.num_ctx() == 32768
    monkeypatch.setenv("CBX_NUM_CTX", "12")  # below any useful window: clamped
    assert gateway.num_ctx() == 2048
    monkeypatch.delenv("CBX_NUM_CTX")
    (tmp_path / ".env").write_text("CBX_PORT=8080\nCBX_NUM_CTX=8192  # smaller window\n", encoding="utf-8")
    assert gateway.num_ctx() == 8192
    assert gateway.placement_advice([{"name": "qwen3.8:27b", "gpu_pct": 100}]) == ""
    assert "now 8192" in gateway.placement_advice([{"name": "qwen3.8:27b", "gpu_pct": 62}])


def test_attention_names_hand_edited_files_after_a_promote(owner, admin, db):
    from cbx.models import AppVersion

    v = AppVersion(tag="8.8.8-home", status="live", env="production", package_path="/nonexistent/8.8.8-home", promoted_at=utcnow() - timedelta(hours=2), tests={"passed": 10, "failed": 0, "hand_edited": ["web/app.js", "cbx/api/pdfstudio.py"]})
    f = AppVersion(tag="8.8.9-home", status="failed", env="sandbox-a", package_path="/nonexistent/8.8.9-home", tests={"passed": 3, "failed": 2, "ok": False})
    db.add_all([v, f])
    db.commit()
    try:
        items = {i["key"]: i for i in _items(owner)}
        he = items[f"version_hand_edits:{v.id}"]
        assert he["level"] == "info" and he["go"] == "versions" and he["n"] == 2
        assert "2 hand-edited files were replaced: web/app.js, cbx/api/pdfstudio.py" in he["text"] and "backups" in he["text"]
        fl = items[f"version_failed:{f.id}"]
        assert fl["level"] == "crit" and "8.8.9-home failed 2 of its sandbox tests (3 passed)" in fl["text"] and "promote it anyway" in fl["text"]
        # versions are the owner's business, not the admins'
        assert not [k for k in admin.get("/api/home/attention").json()["items"] if k["key"].startswith("version_")]
    finally:
        db.delete(v)
        db.delete(f)
        db.commit()
