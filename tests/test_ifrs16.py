"""IFRS 16: the one-time copy of the old page's uploads and months into the tool folder (tools/ifrs16; the
old page and its API went in 1.0.79 — the tool's own tests are in tools/ifrs16/tests)."""
from __future__ import annotations

from pathlib import Path

from cbx.db import SessionLocal
from cbx.models import SourceBatch

FIX = Path(__file__).parent / "fixtures" / "ifrs16"


def test_old_uploads_and_runs_are_copied_into_the_tool_once(user, tmp_path, monkeypatch):
    """1.0.56: the built-in page's kept uploads, months and ledger checks are copied into tools/ifrs16 once."""
    from cbx import state
    from cbx.config import ROOT
    from cbx.engine import files as FILES
    from cbx.engine import loader
    from cbx.engine import store as STORE
    from cbx.ifrs16 import copy_in
    from cbx.models import AppState

    from cbx.ifrs16 import service as SV
    from cbx.models import User

    with SessionLocal() as db:  # an upload and a month as the old IFRS 16 page kept them (before 1.0.56)
        me = db.query(User).filter(User.email == "thandi@test.local").one()
        b = SV.create_batch(db, me, {"la": ("la_register.xlsx", (FIX / "la_register.xlsx").read_bytes()), "movements": ("movements_rr06.xlsx", (FIX / "movements_rr06.xlsx").read_bytes())})
        db.commit()
        SV.scan_job(b.id)
        run = SV.create_workflow_run(db, me, db.get(SourceBatch, b.id), "2026-08")
        db.commit()
        SV.workflow_job(run.id)
        r = {"id": run.id}
    loader.reload(ROOT / "tools")
    monkeypatch.setattr(STORE, "path_for", lambda slug: tmp_path / f"{slug}.sqlite")
    monkeypatch.setattr(FILES, "root_for", lambda slug: tmp_path / "_files" / slug)
    with SessionLocal() as db:
        db.query(AppState).filter(AppState.key == copy_in.KEY).delete()
        got = copy_in.run_at_start(db)
        assert got["uploads"] >= 1 and got["months"] >= 1 and got["files"] >= 3
        t = loader.registry().tools["ifrs16"]
        st = STORE.Store("ifrs16")
        ups = st.rows("uploads", t.data["uploads"]["columns"], limit=2000)["rows"]
        u = next(x for x in ups if x["la"] and "2026-08" in x["months"])
        assert u["status"] == "Ready" and u["movements"]["name"].startswith("movements_rr06")
        m = next(x for x in st.rows("months", t.data["months"]["columns"], limit=2000)["rows"] if x["month"] == "2026-08")
        assert m["status"] == "Done" and m["report"]["name"].startswith("IFRS16_Report_2026-08") and m["upload"]
        assert copy_in.run_at_start(db) == {} and state.get(db, copy_in.KEY)
        db.rollback()
    assert r["id"]
