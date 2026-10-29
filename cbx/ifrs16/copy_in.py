"""1.0.56: IFRS 16 is a tool folder (tools/ifrs16). Once, at start, the built-in page's kept uploads, its
months and its ledger checks are copied into the folder's tables, with their files — who and when kept.
A month's import workbooks go into one zip, its report beside it. Hidden (cleared) history is not copied.
The old rows and files are left as they were; the copy is marked done in app_state and never runs again.
"""
from __future__ import annotations

import io
import zipfile

from sqlalchemy.orm import Session

from .. import state
from ..audit import log

KEY = "ifrs16_copied_to_folder"


def run_at_start(db: Session) -> dict:
    from ..db import utcnow
    from ..engine import files as FILES
    from ..engine import loader
    from ..engine.store import Store
    from ..models import IfrsRun, SourceBatch, StoredFile
    from ..warehouse import storage

    if state.get(db, KEY):
        return {}
    t = loader.registry().tools.get("ifrs16")
    if t is None or not all(n in t.data for n in ("uploads", "months", "ledger")):
        return {}
    st = Store("ifrs16")
    cols = {n: t.data[n].get("columns") or [] for n in ("uploads", "months", "ledger")}
    if any(st.exists(n) and st.rows(n, c, limit=1)["total"] for n, c in cols.items()):
        state.set(db, KEY, "not copied — the IFRS 16 tool already had rows")
        return {}
    got = {"uploads": 0, "months": 0, "ledger checks": 0, "files": 0}

    def read(fid):
        f = db.get(StoredFile, fid) if fid else None
        p = storage.abs_path(f) if f is not None else None
        return (f.name, p.read_bytes()) if p is not None and p.is_file() else None

    def put_file(table, rid, col, item, by):
        if not item:
            return
        c = next(x for x in cols[table] if x["key"] == col)
        try:
            meta = FILES.save("ifrs16", table, rid, c, item[0], item[1], by, utcnow().isoformat())
        except FILES.FileError:
            return
        st.set_file(table, cols[table], rid, col, meta)
        got["files"] += 1

    uploads: dict[int, int] = {}
    for b in db.query(SourceBatch).filter(SourceBatch.hidden.is_(False)).order_by(SourceBatch.id).all():
        scan = b.scan or {}
        months = scan.get("months") or []
        row = st.insert("uploads", cols["uploads"], {
            "summary": f"{scan.get('la_rows', 0)} LA register rows · {scan.get('move_rows', 0)} movement rows in {len(months)} month{'s' if len(months) != 1 else ''}" if months else "",
            "movement": scan.get("movement_label") or "", "months": ", ".join(m.get("value", "") for m in months), "latest": months[0].get("value", "") if months else "",
            "status": "Ready" if b.status == "ready" else "Could not read" if b.status == "failed" else "New", "note": b.scan_error or ""},
            b.user_id, at=b.created_at.isoformat() if b.created_at else None)
        uploads[b.id] = row["id"]
        got["uploads"] += 1
        for col, fid in (("la", b.la_file_id), ("movements", b.movements_file_id), ("sequentra", b.sequentra_file_id), ("steps", b.steps_file_id)):
            put_file("uploads", row["id"], col, read(fid), b.user_id)
    for r in db.query(IfrsRun).filter(IfrsRun.hidden.is_(False), IfrsRun.status.in_(("done", "failed"))).order_by(IfrsRun.id).all():
        outs = [x for x in (read(i) for i in (r.output_file_ids or [])) if x]
        m = r.metrics or {}
        at = r.started_at.isoformat() if r.started_at else None
        if r.kind == "ledger":
            row = st.insert("ledger", cols["ledger"], {"as_at": (m.get("as_at") or "")[:7], "months_back": m.get("months_back") or 12, "status": "Done" if r.status == "done" else "Failed",
                                                       "overview": "\n".join(f"{x.get('item')}: {x.get('value')}" for x in m.get("overview") or []), "note": r.error or ""}, r.user_id, at=at)
            put_file("ledger", row["id"], "ledger", read(m.get("ledger_file_id")), r.user_id)
            put_file("ledger", row["id"], "report", outs[0] if outs else None, r.user_id)
            got["ledger checks"] += 1
            continue
        report = next((x for x in outs if x[0].startswith("IFRS16_Report_")), None)
        rest = [x for x in outs if x is not report]
        row = st.insert("months", cols["months"], {"month": r.month or "", "upload": uploads.get(r.batch_id), "status": "Done" if r.status == "done" else "Failed",
                                                   "imports": m.get("import_workbooks") if m.get("import_workbooks") is not None else len(rest),
                                                   "recon": "\n".join(f"{x.get('item')}: {x.get('value')}" for x in m.get("recon") or []), "note": r.error or ""}, r.user_id, at=at)
        got["months"] += 1
        put_file("months", row["id"], "report", report, r.user_id)
        if rest:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for name, data in rest:
                    z.writestr(name, data)
            put_file("months", row["id"], "workbooks", (f"IFRS16_Imports_{r.month}.zip", buf.getvalue()), r.user_id)
    text = ", ".join(f"{v} {k}" for k, v in got.items())
    state.set(db, KEY, text)
    log(db, "ifrs16_copied_to_folder", target="tools/ifrs16", detail=text)
    return got


KEY_PAGE = "ifrs16_folder_to_page"


def folder_to_page(db: Session) -> dict:
    """1.0.92, once: the IFRS 16 tool folder's rows (1.0.57–1.0.91) come to the IFRS 16 page — its kept uploads,
    months, ledger checks and the Rates table, with their files, who and when. The folder's rows stay."""
    import json as _json
    from datetime import datetime

    from ..db import utcnow
    from ..engine import files as FILES
    from ..engine import loader
    from ..engine.store import Store
    from ..models import User
    from ..models.ifrs16_page import IfrsLedgerCheck, IfrsMonth, IfrsRate, IfrsUpload
    from ..warehouse import storage

    if state.get(db, KEY_PAGE):
        return {}
    t = loader.registry().tools.get("ifrs16")
    st = Store("ifrs16")
    if t is None or not any(n in t.data and st.exists(n) for n in ("uploads", "months", "ledger", "rates")):
        state.set(db, KEY_PAGE, "nothing to copy")
        return {}
    if db.query(IfrsUpload).count() or db.query(IfrsMonth).count() or db.query(IfrsLedgerCheck).count():
        state.set(db, KEY_PAGE, "not copied — the page already had rows")
        return {}

    def rows(name):
        if name not in t.data or not st.exists(name):
            return []
        return sorted(st.rows(name, t.data[name].get("columns") or [], limit=5000)["rows"], key=lambda r: int(r["id"]))

    def when(v):
        try:
            return datetime.fromisoformat(str(v)) if v else None
        except ValueError:
            return None

    def keep(table, r, col, user, sub):
        meta = r.get(col)
        if isinstance(meta, str):
            try:
                meta = _json.loads(meta)
            except ValueError:
                meta = None
        p = FILES.path_of("ifrs16", table, int(r["id"]), meta if isinstance(meta, dict) else None)
        if p is None or user is None:
            return None
        f = storage.store(db, user, (meta or {}).get("name") or p.name, p.read_bytes(), sub=sub, source="result" if sub == "Results" else "ifrs16", mirror=False,
                          meta={"purpose": "ifrs16", "ifrs16": col, "copied_from": f"tools/ifrs16/{table}:{r['id']}"})
        got["files"] += 1
        return f.id

    got = {"uploads": 0, "months": 0, "ledger checks": 0, "rates": 0, "files": 0}
    umap: dict[int, int] = {}
    status = {"New": "new", "Reading": "failed", "Ready": "ready", "Could not read": "failed"}
    for r in rows("uploads"):
        user = db.get(User, r.get("created_by") or 0)
        if user is None:
            continue
        months = [m.strip() for m in str(r.get("months") or "").split(",") if len(m.strip()) == 7]
        scan = {"months": [{"value": m, "label": datetime(int(m[:4]), int(m[5:7]), 1).strftime("%B %Y"), "rows": 0} for m in months], "movement_label": r.get("movement") or ""}
        u = IfrsUpload(user_id=user.id, status=status.get(r.get("status") or "", "new"), scan=scan, note=str(r.get("summary") or r.get("note") or "")[:2000],
                       created_at=when(r.get("created_at")) or utcnow())
        for k, col in (("la", "la"), ("movements", "movements"), ("sequentra", "sequentra"), ("steps", "steps"), ("assets", "assets"), ("ciw", "ciw_master")):
            setattr(u, f"{k}_file_id", keep("uploads", r, col, user, "Inbox"))
        db.add(u)
        db.flush()
        umap[int(r["id"])] = u.id
        got["uploads"] += 1
    for r in rows("months"):
        user = db.get(User, r.get("created_by") or 0)
        if user is None:
            continue
        up = r.get("upload")
        m = IfrsMonth(upload_id=umap.get(int(up)) if str(up or "").isdigit() else None, user_id=user.id, month=str(r.get("month") or "")[:7],
                      source="api" if "api" in str(r.get("source") or "").lower() else "files", status="done" if r.get("status") == "Done" else "failed",
                      imports=int(r.get("imports") or 0), recon=str(r.get("recon") or ""),
                      note=str(r.get("note") or ("Was still working when it was copied to the page — run it again." if r.get("status") == "Working" else ""))[:2000],
                      created_at=when(r.get("created_at")) or utcnow(), finished_at=when(r.get("created_at")))
        m.workbooks_file_id = keep("months", r, "workbooks", user, "Results")
        m.report_file_id = keep("months", r, "report", user, "Results")
        m.papers_file_id = keep("months", r, "papers", user, "Results")
        db.add(m)
        got["months"] += 1
    for r in rows("ledger"):
        user = db.get(User, r.get("created_by") or 0)
        if user is None:
            continue
        db.add(IfrsLedgerCheck(user_id=user.id, ledger_file_id=keep("ledger", r, "ledger", user, "Inbox"), report_file_id=keep("ledger", r, "report", user, "Results"),
                               as_at=str(r.get("as_at") or "")[:10], months_back=int(r.get("months_back") or 12), status="done" if r.get("status") == "Done" else "failed",
                               overview=str(r.get("overview") or ""), note=str(r.get("note") or ""), created_at=when(r.get("created_at")) or utcnow()))
        got["ledger checks"] += 1
    for r in rows("rates"):
        mth = str(r.get("month") or "")[:7]
        if len(mth) != 7 or db.query(IfrsRate).filter(IfrsRate.month == mth).count():
            continue
        vals = {}
        for b in "abcdef":
            try:
                vals[b] = float(r[b]) if r.get(b) not in (None, "") else None
            except (TypeError, ValueError):
                vals[b] = None
        db.add(IfrsRate(month=mth, **vals, note=str(r.get("note") or "")[:300], created_by_id=r.get("created_by")))
        db.flush()
        got["rates"] += 1
    db.flush()
    text = ", ".join(f"{v} {k}" for k, v in got.items())
    state.set(db, KEY_PAGE, text)
    log(db, "ifrs16_folder_to_page", target="ifrs16", detail=text)
    return got
