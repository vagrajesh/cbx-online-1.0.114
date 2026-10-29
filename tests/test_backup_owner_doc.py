"""1.0.58: the nightly backup of the data, and the owner's document (compiled, sealed into every zip)."""
import io
import json
import zipfile
from datetime import datetime, timedelta

from cbx import backup as BK
from cbx import owner_doc
from cbx.config import ROOT, settings


def test_a_backup_holds_the_databases_the_key_and_the_files(owner, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "backup_dir", str(tmp_path / "bk"))
    (settings.data_dir / "tools").mkdir(parents=True, exist_ok=True)
    owner.post("/api/engine/tools/todo/data/tasks", json={"values": {"title": "Backed up"}})  # a tool database with a row
    (settings.data_dir / "warehouse").mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "warehouse" / "probe.txt").write_text("a file people uploaded", encoding="utf-8")
    old = tmp_path / "bk" / (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d_010000")
    old.mkdir(parents=True)
    got = BK.run("test", "owner@test.local")
    assert got["ok"], got
    snap = tmp_path / "bk" / got["name"]
    assert (snap / "cbx.sqlite3").stat().st_size > 0 and (snap / "secret.key").is_file()
    assert any(p.startswith("tools/todo") for p in got["databases"])
    import sqlite3

    c = sqlite3.connect(snap / "tools" / "todo.sqlite")
    assert c.execute("SELECT COUNT(*) FROM tasks WHERE title = 'Backed up'").fetchone()[0] >= 1
    assert (tmp_path / "bk" / "files" / "warehouse" / "probe.txt").read_text(encoding="utf-8") == "a file people uploaded"
    assert not old.exists() and got["removed"] == 1  # older than the days kept
    m = json.loads((snap / "manifest.json").read_text(encoding="utf-8"))
    assert m["reason"] == "test" and m["version"]
    # the second night copies only what changed
    again = BK.run("test")
    assert again["files_copied"] == 0
    # the owner sees them; nobody else does
    r = owner.get("/api/owner/backups").json()
    assert r["snapshots"][0]["ok"] and r["folder"] == str(tmp_path / "bk")


def test_backup_is_due_once_a_day_after_its_time(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "backup_dir", str(tmp_path / "bk2"))
    monkeypatch.setattr(settings, "backup_at", "01:30")
    tz = datetime.now().astimezone().tzinfo
    assert not BK.due(datetime(2026, 10, 1, 1, 0, tzinfo=tz))
    assert BK.due(datetime(2026, 10, 1, 2, 0, tzinfo=tz))
    (tmp_path / "bk2" / "2026-10-01_020000").mkdir(parents=True)
    assert not BK.due(datetime(2026, 10, 1, 23, 0, tzinfo=tz))


def test_promotes_never_prune_the_data_backups(tmp_path, monkeypatch):
    from cbx.versions import injector

    monkeypatch.setattr(injector, "backups_dir", lambda: tmp_path)
    (tmp_path / "data").mkdir()
    for i in range(12):
        (tmp_path / f"1.0.{i}-x").mkdir()
    injector._prune_backups(set())
    assert (tmp_path / "data").is_dir()


def test_the_owners_document_is_the_owners_and_holds_no_secret(owner, admin, monkeypatch):
    monkeypatch.setattr(settings, "smtp_password", "hunter2-Secret-99", raising=False)
    r = owner.get("/api/owner/document")
    assert r.status_code == 200
    md = r.json()["markdown"]
    for part in ("## Where is what", "## The tools", "## Who owns what", "## Releases", "## Backups", "## How to", "## The ten things", "To-do", "Site Schedules", "IFRS 16"):
        assert part in md, part
    assert "hunter2-Secret-99" not in md and "CBX_SMTP_PASSWORD | set" in md
    assert "<table>" in r.json()["html"]
    assert admin.get("/api/owner/document").status_code == 403
    d = owner.get("/api/owner/document.html")
    assert d.status_code == 200 and "attachment" in d.headers["content-disposition"] and d.text.startswith("<!doctype html>")


def test_every_release_zip_carries_the_sealed_document():
    from cbx.versions import injector

    data = injector.build_zip(ROOT, "9.9.9")
    z = zipfile.ZipFile(io.BytesIO(data))
    text = z.read("docs/OWNERS-DOCUMENT.md").decode()
    assert "the owner's document" in text and "## The tools" in text and "## Who owns what" not in text  # no people in a zip
    assert injector.verify_zip(z) == "9.9.9"  # a server checking the package accepts it (docs/ is allowed everywhere)
    assert len(z.namelist()) == len(set(z.namelist()))


def test_markdown_to_html_is_escaped():
    h = owner_doc.body_html("# T\n\n| a | b |\n| --- | --- |\n| <script>x</script> | **y** |\n\n1. one\n2. two")
    assert "<script>" not in h and "&lt;script&gt;" in h and "<b>y</b>" in h and "<ol>" in h
