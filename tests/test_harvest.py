"""PDF Studio → Harvest: a folder source with synthetic bills is scanned, read with the existing
pipeline, kept as dated rows and appended to its own master sheet; date-range query, export, re-run
with history, incremental column authoring, profile seeding, RLS, OneDrive without Graph."""
from __future__ import annotations

import io
import re
from datetime import date
from pathlib import Path

import pytest
from openpyxl import load_workbook

from cbx.db import SessionLocal
from cbx.models import HarvestRow, HarvestSource, MasterSheet, Profile, ProfileVersion, Template
from cbx.pdfstudio import harvest as H
from tests.fixtures.make_bills import make_bills
from tests.test_pdfstudio import COLUMNS

EMF = [f"emfuleni-EMF-7712{i}.pdf" for i in range(3)]


@pytest.fixture(scope="module")
def folder(tmp_path_factory):
    """A 'Telkom' folder with three Emfuleni bills (June, July) and a subfolder with one more."""
    src = tmp_path_factory.mktemp("bills-src")
    bills = make_bills(src)
    d = tmp_path_factory.mktemp("telkom")
    (d / "2026-07").mkdir()
    gold = {}
    for b in bills:
        if b["name"] in EMF[:2]:
            (d / b["name"]).write_bytes(Path(b["file"]).read_bytes())
        elif b["name"] == EMF[2]:
            (d / "2026-07" / b["name"]).write_bytes(Path(b["file"]).read_bytes())
        gold[b["name"]] = b["gold"]
    (d / "notes.txt").write_text("not a pdf", encoding="utf-8")
    (d / "~$lock.pdf").write_bytes(b"junk")
    return d, gold, bills


def _template(client, name="Telkom bills"):
    r = client.post("/api/pdfstudio/templates", json={"name": name, "columns": COLUMNS})
    assert r.status_code == 200, r.text
    return r.json()


def _source(client, folder, template_id, name="Telkom", **kw):
    body = {"name": name, "template_id": template_id, "kind": "local", "path": str(folder).replace("/", "\\"), "include_subfolders": True, "every_minutes": 15}
    body.update(kw)
    r = client.post("/api/pdfstudio/harvest/sources", json=body)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture()
def harvested(owner, folder):
    d, gold, _ = folder
    existing = [x for x in owner.get("/api/pdfstudio/harvest/sources").json()["sources"] if x["name"] == "Telkom"]
    if existing:  # one shared source for the module (tests build on each other)
        t = next(x for x in owner.get("/api/pdfstudio/templates").json()["templates"] if x["id"] == existing[0]["template_id"])
        return t, existing[0], gold
    t = _template(owner)
    s = _source(owner, d, t["id"])
    r = owner.post(f"/api/pdfstudio/harvest/sources/{s['id']}/run")
    assert r.status_code == 200, r.text
    s = owner.get("/api/pdfstudio/harvest/sources").json()["sources"]
    s = next(x for x in s if x["name"] == "Telkom")
    return t, s, gold


# ---- scan --------------------------------------------------------------------------------------------------
def test_check_folder_counts_pdfs_and_windows_style_path(owner, folder):
    d, _, _ = folder
    r = owner.post("/api/pdfstudio/harvest/check-folder", json={"kind": "local", "path": str(d).replace("/", "\\"), "include_subfolders": True})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] and r.json()["count"] == 3, r.json()  # the .txt and the ~$ lock file are ignored
    r = owner.post("/api/pdfstudio/harvest/check-folder", json={"kind": "local", "path": str(d), "include_subfolders": False})
    assert r.json()["count"] == 2
    r = owner.post("/api/pdfstudio/harvest/check-folder", json={"kind": "local", "path": str(d / "nope"), "include_subfolders": True})
    assert r.json()["ok"] is False and "can't find the folder" in r.json()["message"]


def test_scan_stores_files_creates_dated_rows_and_master_sheet(harvested, db):
    t, s, gold = harvested
    assert s["status"] == "ok", s
    assert s["rows"] == 3 and s["files_seen"] == 3 and s["last_new"] == 3
    assert s["template"] == "Telkom bills" and s["master_sheet_id"]
    rows = db.query(HarvestRow).filter(HarvestRow.source_id == s["id"]).all()
    assert len(rows) == 3
    for r in rows:
        g = gold[re.sub(r" \(\d+\)(?=\.pdf$)", "", r.filename)]  # the warehouse suffixes a re-uploaded name with ' (2)'
        assert r.doc_date == date.fromisoformat(g["period"][:10]), (r.filename, r.doc_date)
        assert abs(float(r.cells["total"]["value"]) - g["total"]) < 0.005
        assert r.cells["total"]["quote"] and r.cells["total"]["page"] == 1  # provenance kept
        assert r.file_id and r.document_id and r.profile_version_id
    sheet = db.get(MasterSheet, s["master_sheet_id"])
    assert sheet.name == "Harvest · Telkom"
    assert len(sheet.rows) + len(sheet.exceptions) == 3
    # stored in the warehouse under the owner, mirrored to the library, kept out of the inbox pipeline
    from cbx.models import StoredFile

    files = db.query(StoredFile).filter(StoredFile.source == "harvest").all()
    assert len(files) == 6  # 3 originals + 3 library copies
    assert all(f.pipeline_status == "added" for f in files if not f.library_copy_of)


def test_second_scan_adds_nothing(harvested, db):
    t, s, _ = harvested
    out = H.run_source(s["id"])
    assert out["new"] == 0 and out["seen"] == 3 and out["rows"] == 3, out
    src = db.get(HarvestSource, s["id"])
    assert src.status == "ok" and src.stats["runs"] == 1  # no new batch was made
    sheet = db.get(MasterSheet, src.master_sheet_id)
    assert len(sheet.rows) + len(sheet.exceptions) == 3


def test_routine_respects_every_minutes_and_never_overlaps(harvested, db):
    t, s, _ = harvested
    src = db.get(HarvestSource, s["id"])
    assert H.due(src) is False  # just ran
    src.every_minutes = 0  # coerced to at least 1 minute
    from datetime import timedelta

    src.last_run_at = src.last_run_at - timedelta(minutes=2)
    db.commit()
    assert H.due(src) is True
    H._RUNNING.add(src.id)
    try:
        assert H.due(src) is False
        assert H.run_source(src.id) == {"skipped": "already running"}
        assert H.submit_run(src.id) is False
    finally:
        H._RUNNING.discard(src.id)
    src.active = False
    db.commit()
    assert H.due(src) is False
    H.harvest_routine()  # nothing due, nothing breaks


# ---- query / export ----------------------------------------------------------------------------------------
def test_date_range_query_is_served_from_rows(harvested, owner):
    t, s, gold = harvested
    r = owner.get(f"/api/pdfstudio/harvest/sources/{s['id']}/rows")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["total"] == 3 and len(j["rows"]) == 3 and j["date_column"] == "period"
    row = j["rows"][0]
    assert row["open_url"] == f"/api/pdfstudio/harvest/rows/{row['id']}/pdf" and row["download_url"].endswith("/download")
    assert row["cells"]["total"]["value"] is not None
    # June only
    r = owner.get(f"/api/pdfstudio/harvest/sources/{s['id']}/rows", params={"from": "2026-06-01", "to": "2026-06-30"})
    june = r.json()
    assert june["total"] == sum(1 for g in gold.values() if g["period"].startswith("2026-06") and any(g["account_no"].replace(" ", "") in n for n in EMF))
    assert all(x["date"].startswith("2026-06") for x in june["rows"])
    # SA date formats are accepted for the range
    r = owner.get(f"/api/pdfstudio/harvest/sources/{s['id']}/rows", params={"from": "01/07/2026", "to": "31 Jul 2026"})
    assert r.status_code == 200 and r.json()["total"] == 3 - june["total"]
    r = owner.get(f"/api/pdfstudio/harvest/sources/{s['id']}/rows", params={"from": "next tuesday"})
    assert r.status_code == 400
    # search
    r = owner.get(f"/api/pdfstudio/harvest/sources/{s['id']}/rows", params={"q": "EMF-77120"})
    assert r.json()["total"] == 1
    # the PDF behind a row opens inline
    r = owner.get(row["open_url"])
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF")
    assert "inline" in r.headers["content-disposition"]


def test_export_builds_workbook_with_dates_and_provenance(harvested, owner):
    t, s, _ = harvested
    r = owner.get(f"/api/pdfstudio/harvest/sources/{s['id']}/export.xlsx", params={"from": "2026-06-01", "to": "2026-07-31"})
    assert r.status_code == 200, r.text
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["Rows", "Provenance", "Misses"]
    ws = wb["Rows"]
    head = [c.value for c in ws[1]]
    assert head[:3] == ["#", "File", "Date"] and "Total due" in head
    assert ws.max_row == 4
    assert all(str(ws.cell(row=i, column=3).value).startswith("2026-0") for i in range(2, 5))
    prov = wb["Provenance"]
    assert prov.max_row > 4


# ---- re-run with the current profile -----------------------------------------------------------------------
def test_rerun_range_keeps_history_and_updates_master_in_place(harvested, owner, db):
    t, s, _ = harvested
    before = {r.id: (dict(r.cells), r.profile_version_id) for r in db.query(HarvestRow).filter(HarvestRow.source_id == s["id"]).all()}
    sheet = db.get(MasterSheet, s["master_sheet_id"])
    n_master = len(sheet.rows) + len(sheet.exceptions)
    r = owner.post(f"/api/pdfstudio/harvest/sources/{s['id']}/rerun", params={"from": "2026-06-01", "to": "2026-06-30"})
    assert r.status_code == 200, r.text
    assert r.json()["started"] and r.json()["rows"] >= 1
    db.expire_all()
    rows = db.query(HarvestRow).filter(HarvestRow.source_id == s["id"]).all()
    june = [x for x in rows if x.doc_date and x.doc_date.month == 6]
    july = [x for x in rows if x.doc_date and x.doc_date.month == 7]
    assert june and july
    for x in june:
        assert len(x.history) == 1
        assert x.history[0]["cells"] == before[x.id][0] and x.history[0]["profile_version_id"] == before[x.id][1]
        assert x.cells["total"]["value"] == before[x.id][0]["total"]["value"]  # same PDF, same answer
    for x in july:
        assert x.history == []
    sheet = db.get(MasterSheet, s["master_sheet_id"])
    assert len(sheet.rows) + len(sheet.exceptions) == n_master  # updated in place, not appended
    assert any((row.get("_src") or {}).get("rerun") for row in sheet.rows + sheet.exceptions)
    src = db.get(HarvestSource, s["id"])
    assert src.status == "ok" and src.stats["last_rerun"]["rows"] == len(june)
    r = owner.get(f"/api/pdfstudio/harvest/sources/{s['id']}/rows", params={"from": "2026-06-01", "to": "2026-06-30"})
    assert all(x["reruns"] == 1 for x in r.json()["rows"])
    # an empty range is a plain-English 400
    r = owner.post(f"/api/pdfstudio/harvest/sources/{s['id']}/rerun", params={"from": "2030-01-01", "to": "2030-01-31"})
    assert r.status_code == 400 and "no rows" in r.json()["detail"]


# ---- sources are separate; admin controls ------------------------------------------------------------------
def test_sources_are_separate_and_admin_controls(harvested, owner, folder):
    t, s, _ = harvested
    d, _, _ = folder
    dup = owner.post("/api/pdfstudio/harvest/sources", json={"name": "Telkom", "template_id": t["id"], "kind": "local", "path": str(d)})
    assert dup.status_code == 400 and "already" in dup.json()["detail"]
    s2 = _source(owner, d / "2026-07", t["id"], name="Swiftnet")
    assert s2["master_sheet_id"] != s["master_sheet_id"]
    owner.post(f"/api/pdfstudio/harvest/sources/{s2['id']}/run")
    r = owner.get(f"/api/pdfstudio/harvest/sources/{s2['id']}/rows").json()
    assert r["total"] == 1 and r["rows"][0]["filename"].startswith("emfuleni-EMF-77122")
    # pause / resume / edit
    p = owner.patch(f"/api/pdfstudio/harvest/sources/{s2['id']}", json={"active": False, "every_minutes": 60})
    assert p.status_code == 200 and p.json()["status"] == "paused" and p.json()["every_minutes"] == 60
    assert owner.patch(f"/api/pdfstudio/harvest/sources/{s2['id']}", json={"every_minutes": 0}).status_code == 400
    assert owner.patch(f"/api/pdfstudio/harvest/sources/{s2['id']}", json={"template_id": t["id"] + 999}).status_code == 404
    assert owner.delete(f"/api/pdfstudio/harvest/sources/{s2['id']}").status_code == 200
    names = [x["name"] for x in owner.get("/api/pdfstudio/harvest/sources").json()["sources"]]
    assert "Swiftnet" not in names and "Telkom" in names


def test_onedrive_source_without_graph_has_clear_status(owner, folder):
    _, _, _ = folder
    t = _template(owner, "OneDrive bills")
    r = owner.post("/api/pdfstudio/harvest/check-folder", json={"kind": "onedrive", "path": "Bills/Telkom"})
    assert r.status_code == 200 and r.json()["ok"] is False and "OneDrive" in r.json()["message"]
    s = _source(owner, "Bills/Telkom", t["id"], name="Telkom OneDrive", kind="onedrive")
    r = owner.post(f"/api/pdfstudio/harvest/sources/{s['id']}/run")
    assert r.status_code == 200, r.text
    src = r.json()["source"]
    assert src["status"] == "error" and "OneDrive" in src["last_error"], src
    assert src["rows"] == 0


# ---- RLS ---------------------------------------------------------------------------------------------------
def test_user_without_grant_sees_no_rows_and_cannot_open_pdfs(harvested, owner, user):
    t, s, _ = harvested
    rid = owner.get(f"/api/pdfstudio/harvest/sources/{s['id']}/rows").json()["rows"][0]["id"]
    # Thandi is in the owner's team? The owner has no team, so the source is not hers to see at all
    r = user.get("/api/pdfstudio/harvest/sources")
    assert r.status_code == 200 and all(x["id"] != s["id"] for x in r.json()["sources"]) and r.json()["can_manage"] is False
    assert user.get(f"/api/pdfstudio/harvest/sources/{s['id']}/rows").status_code == 404
    assert user.get(f"/api/pdfstudio/harvest/rows/{rid}/pdf").status_code in (403, 404)
    assert user.get(f"/api/pdfstudio/harvest/sources/{s['id']}/export.xlsx").status_code == 404
    # admin-only controls are refused
    assert user.post("/api/pdfstudio/harvest/sources", json={"name": "X", "template_id": t["id"], "kind": "local", "path": "C:\\x"}).status_code == 403
    assert user.post(f"/api/pdfstudio/harvest/sources/{s['id']}/run").status_code == 403
    assert user.post(f"/api/pdfstudio/harvest/sources/{s['id']}/rerun").status_code == 403


def test_team_member_sees_source_but_only_granted_rows(harvested, owner, db):
    """A source made by an admin in a team is visible to the team; rows follow the master-sheet row rules."""
    t, s, _ = harvested
    from fastapi.testclient import TestClient

    from cbx.main import app
    from cbx.models import RowRule, Team, User
    from tests.conftest import _make, login

    src = db.get(HarvestSource, s["id"])
    team = db.query(Team).filter(Team.name == "Utilities billing").one_or_none() or Team(name="Utilities billing", slug="utilities-billing")
    if team.id is None:
        db.add(team)
        db.flush()
    src.team_id = team.id
    db.commit()
    tools = [x["id"] for x in owner.get("/api/users").json()["tools"] if not x["admin_only"]]
    pw = _make(owner, "lerato.hv@test.local", "Lerato Nkosi", "user", tools=tools, team="Utilities billing")
    c = TestClient(app)
    login(c, "lerato.hv@test.local", pw)
    assert any(x["id"] == s["id"] for x in c.get("/api/pdfstudio/harvest/sources").json()["sources"])
    r = c.get(f"/api/pdfstudio/harvest/sources/{s['id']}/rows")
    assert r.status_code == 200 and r.json()["total"] == 0  # no grant → nothing
    rid = owner.get(f"/api/pdfstudio/harvest/sources/{s['id']}/rows").json()["rows"][0]["id"]
    assert c.get(f"/api/pdfstudio/harvest/rows/{rid}/pdf").status_code == 403
    # a row rule on the municipality grants the rows
    u = db.query(User).filter(User.email == "lerato.hv@test.local").one()
    db.add(RowRule(table="master_rows", field="municipality", op="eq", value="Emfuleni", user_id=u.id))
    db.commit()
    r = c.get(f"/api/pdfstudio/harvest/sources/{s['id']}/rows")
    assert r.json()["total"] == 3
    assert c.get(f"/api/pdfstudio/harvest/rows/{rid}/pdf").status_code == 200
    # users never see paths, scores or version ids
    row = r.json()["rows"][0]
    assert "row_score" not in row and "origin" not in row
    assert all(x["path"] == "" for x in c.get("/api/pdfstudio/harvest/sources").json()["sources"])


# ---- profiles: incremental and shared ----------------------------------------------------------------------
def test_added_column_authors_only_that_field(harvested, owner, db):
    t, s, _ = harvested
    prof = db.query(Profile).filter(Profile.template_id == t["id"]).one()
    live_before = db.get(ProfileVersion, prof.live_version_id)
    n_before = len(prof.versions)
    fields_before = {k: v["rules"] for k, v in live_before.ruleset["fields"].items()}
    # the template gains a column
    cols = [dict(c) for c in COLUMNS] + [{"key": "site_name", "label": "Site", "ctype": "text", "required": False, "critical": False, "hint": "Site"}]
    r = owner.patch(f"/api/pdfstudio/templates/{t['id']}", json={"columns": cols})
    assert r.status_code == 200, r.text
    from cbx.pdfstudio import pipeline

    tpl = db.get(Template, t["id"])
    db.refresh(prof)
    v = pipeline.author_missing_columns(db, tpl, prof)
    db.commit()
    assert v is not None and v.number == live_before.number + 1
    assert v.reason.startswith("Added column Site") and "unchanged" in v.reason
    for k, rules in fields_before.items():
        assert v.ruleset["fields"][k]["rules"] == rules, k  # every other field identical
    assert v.ruleset["fields"]["site_name"]["rules"], v.ruleset["fields"]["site_name"]
    assert v.ruleset["fields"]["site_name"]["rules"][0]["label"].lower().startswith("site")
    assert prof.live_version_id == v.id and len(prof.versions) == n_before + 1
    # calling again does nothing (no churn)
    assert pipeline.author_missing_columns(db, tpl, prof) is None
    # a changed hint re-authors only that column
    tpl.columns[-1].hint = "Site: right of the label"
    db.commit()
    v2 = pipeline.author_missing_columns(db, tpl, prof)
    assert v2 is not None and v2.number == v.number + 1 and "Site" in v2.reason
    for k, rules in fields_before.items():
        assert v2.ruleset["fields"][k]["rules"] == rules, k
    db.commit()
    # the admin endpoint re-authors one column on demand
    cid = next(c["id"] for c in r.json()["columns"] if c["key"] == "total")
    rr = owner.post(f"/api/pdfstudio/templates/{t['id']}/columns/{cid}/reauthor")
    assert rr.status_code == 200, rr.text
    assert rr.json()["versions"] and rr.json()["versions"][0]["reason"].startswith("Re-authored column Total due")
    # the profile page lists rules per column
    pj = owner.get(f"/api/pdfstudio/profiles/{prof.id}").json()
    assert pj["fields"] and all("rules" in f for f in pj["fields"])
    assert any(f["key"] == "site_name" and f["has_rules"] for f in pj["fields"])
    # the next harvest run picks the new column up: a fresh source on the same folder, same template
    s3 = _source(owner, Path(H.local_root(db.get(HarvestSource, s["id"]).path)), t["id"], name="Telkom again")
    owner.post(f"/api/pdfstudio/harvest/sources/{s3['id']}/run")
    rows = owner.get(f"/api/pdfstudio/harvest/sources/{s3['id']}/rows").json()["rows"]
    assert rows and all("site_name" in x["cells"] for x in rows)


def test_new_template_on_same_layout_is_seeded_from_existing_profile(harvested, owner, folder, db):
    t, s, _ = harvested
    d, _, bills = folder
    # "Swiftnet uses the same PDFs but different data": a template with different keys/labels
    cols = [
        {"key": "acc", "label": "Account no", "ctype": "text"},
        {"key": "total_due", "label": "Total payable", "ctype": "money"},
        {"key": "amount_excl", "label": "Amount excluding VAT", "ctype": "money"},
        {"key": "invoice_dt", "label": "Invoice date", "ctype": "date"},
    ]
    r = owner.post("/api/pdfstudio/templates", json={"name": "Swiftnet bills", "columns": cols})
    t2 = r.json()
    from tests.test_pdfstudio import _run_batch

    b = _run_batch(owner, bills, EMF, t2["id"], name="Swiftnet test")
    assert b["status"] in ("done", "review")
    prof2 = db.query(Profile).filter(Profile.template_id == t2["id"]).one()
    prof_telkom = db.query(Profile).filter(Profile.template_id == t["id"]).one()
    assert prof2.id != prof_telkom.id  # kept separate
    v = db.get(ProfileVersion, prof2.live_version_id)
    first = prof2.versions[0]
    seeded = first.ruleset.get("seeded_from")
    assert seeded and seeded["profile_id"] != prof2.id, first.ruleset
    # the seed is the most accurate shared profile of this layout — Telkom's when this module runs
    # alone; an earlier module's identical Emfuleni profile when the whole suite runs. Same rules.
    prof1 = db.get(Profile, seeded["profile_id"])
    assert prof1 is not None and prof1.template_id != t2["id"] and prof1.key.endswith(".emfuleni")
    assert seeded["columns"].get("acc") == "account_no" and seeded["columns"].get("total_due") == "total" and seeded["columns"].get("amount_excl") == "excl_vat"
    assert "seeded from" in first.reason
    # the seeded rules are the ones the seed profile already had (minus derived fallbacks)
    v1 = db.get(ProfileVersion, prof1.versions[0].id)
    telkom_total = [r for r in v1.ruleset["fields"]["total"]["rules"] if r["type"] != "derived"]
    swift_total = [r for r in first.ruleset["fields"]["total_due"]["rules"] if r["type"] != "derived" and not str(r.get("id", "")).startswith("axl")]
    assert swift_total[: len(telkom_total)] == telkom_total
    # the column with no counterpart was authored from scratch
    assert first.ruleset["fields"]["invoice_dt"]["rules"]
    assert first.ruleset["fields"]["invoice_dt"].get("seeded_from") is None
    # Telkom's profile did not gain Swiftnet's columns
    db.refresh(prof_telkom)
    live1 = db.get(ProfileVersion, prof_telkom.live_version_id)
    assert "total_due" not in live1.ruleset["fields"]
    # the admin page shows the pill
    pj = owner.get(f"/api/pdfstudio/profiles/{prof2.id}").json()
    assert pj["seeded_from"]["profile"] == prof1.label
    assert any(f["seeded_from"] == "total" for f in pj["fields"] if f["key"] == "total_due")
    assert any(x["seeded_from"] for x in owner.get("/api/pdfstudio/profiles").json()["profiles"])


# ---- dates -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("raw,val", [("2026-06-01", date(2026, 6, 1)), ("2026-06", date(2026, 6, 1)), ("2026-06-01 – 2026-06-30", date(2026, 6, 1)), ("01/06/2026", date(2026, 6, 1)), ("01 Jun 2026", date(2026, 6, 1)), ("June 2026", date(2026, 6, 1)), ("", None), ("hello", None)])
def test_parse_doc_date(raw, val):
    assert H.parse_doc_date(raw) == val
