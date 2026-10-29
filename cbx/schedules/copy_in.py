"""1.0.56: Site Schedules is a tool folder (tools/site-schedules). Once, at start, the quotations read by
the built-in page are copied into the folder's table — the quote PDF and the built agreement with them,
who added each and when kept. What was still unclear becomes unticked lines under *What to check*. The
old rows and files are left as they were; the copy is marked done in app_state and never runs again.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from .. import state
from ..audit import log

KEY = "site_schedules_copied_to_folder"


def run_at_start(db: Session) -> dict:
    from ..db import utcnow
    from ..engine import files as FILES
    from ..engine import loader
    from ..engine.store import Store
    from ..models import ScheduleJob, StoredFile
    from ..warehouse import storage

    if state.get(db, KEY):
        return {}
    t = loader.registry().tools.get("site-schedules")
    if t is None or "quotes" not in t.data:
        return {}
    cols = t.data["quotes"].get("columns") or []
    bycol = {c["key"]: c for c in cols}
    st = Store("site-schedules")
    if st.exists("quotes") and st.rows("quotes", cols, limit=1)["total"]:
        state.set(db, KEY, "not copied — the Site Schedules tool already had rows")
        return {}
    got = {"quotations": 0, "files": 0}

    def file_of(fid):
        f = db.get(StoredFile, fid) if fid else None
        p = storage.abs_path(f) if f is not None else None
        return (f.name, p.read_bytes()) if p is not None and p.is_file() else None

    for j in db.query(ScheduleJob).order_by(ScheduleJob.id).all():
        f = j.fields or {}
        v = lambda k: (f.get(k) or {}).get("value")  # noqa: E731
        # 1.0.88: the table has CBX-Connect 2's fields; an old quotation keeps its site, and is read again with "Read it again"
        row = st.insert("quotes", cols, {
            "site_name": j.site or v("site") or "", "col_id": j.quote_no or v("quote_no") or "",
            "checks": [{"text": i.get("text", ""), "done": False} for i in (j.issues or []) if isinstance(i, dict)],
            "status": "Built" if j.status == "built" else "Read"}, j.user_id, at=j.created_at.isoformat() if j.created_at else None)
        got["quotations"] += 1
        for col, fid in (("quote", j.quote_file_id), ("agreement", j.output_file_id)):
            item = file_of(fid)
            if item is None:
                continue
            try:
                meta = FILES.save("site-schedules", "quotes", row["id"], {**bycol[col], "max_mb": 100}, item[0], item[1], j.user_id, utcnow().isoformat())
            except FILES.FileError:
                continue
            st.set_file("quotes", cols, row["id"], col, meta)
            got["files"] += 1
    state.set(db, KEY, f"{got['quotations']} quotations, {got['files']} files")
    log(db, "site_schedules_copied_to_folder", target="tools/site-schedules", detail=f"{got['quotations']} quotations, {got['files']} files")
    return got


# ---- 1.0.89: the Site Schedules page (as CBX-Connect 2) replaces the tool folder's table ----------------
KEY_PAGE = "site_schedules_folder_to_page"


def folder_to_page(db: Session) -> dict:
    """Once: every row of the tool folder's table (1.0.56–1.0.88) becomes an agreement on the page — its PDFs read
    again by the same reader, then the fields the person corrected on the row put back. The folder's rows stay."""
    import json as _json

    from ..engine import files as FILES
    from ..engine import loader
    from ..engine.store import Store
    from ..models import User
    from . import service as S

    if state.get(db, KEY_PAGE):
        return {}
    t = loader.registry().tools.get("site-schedules")
    st = Store("site-schedules")
    if t is None or "quotes" not in t.data or not st.exists("quotes"):
        state.set(db, KEY_PAGE, "nothing to copy")
        return {}
    cols = t.data["quotes"].get("columns") or []
    got = {"agreements": 0}
    for row in st.rows("quotes", cols, limit=2000)["rows"]:
        who = db.get(User, row.get("created_by") or 0)
        if who is None:
            continue
        ups = []
        for col in ("quote", "more"):
            meta = row.get(col)
            path = FILES.path_of("site-schedules", "quotes", int(row["id"]), meta if isinstance(meta, dict) else None)
            if path is not None and path.is_file():
                ups.append(((meta or {}).get("name") or path.name, path.read_bytes()))
        try:
            a = S.create(db, who, [u for u in ups if u[0].lower().endswith(".pdf")])
        except S.Refused:
            a = S.create(db, who, [])
        fixed = {k: row.get(k) for k in S.KNOWN if row.get(k) not in (None, "")}
        if fixed:
            try:
                S.save(db, who, a, {"fields": fixed})
            except S.Refused:
                pass
        signers = {}
        for side, key in (("swift", "swift_signers"), ("couser", "couser_signers")):
            ppl = [{"name": ln.partition(",")[0].strip(), "title": ln.partition(",")[2].strip()} for ln in str(row.get(key) or "").splitlines() if ln.strip()]
            signers[side] = {**(a.signing or {}).get(side, {}), "people": ppl}
        if any(v["people"] for v in signers.values()):
            S.save(db, who, a, {"signing": signers})
        a.history = [*(a.history or []), {"at": a.created_at.isoformat() if a.created_at else "", "by": "", "what": f"Copied from the old Site Schedules table (row {row['id']})"}]
        got["agreements"] += 1
    _ = _json
    state.set(db, KEY_PAGE, f"{got['agreements']} agreements")
    log(db, "site_schedules_folder_to_page", target="site-schedules", detail=f"{got['agreements']} agreements")
    return got
