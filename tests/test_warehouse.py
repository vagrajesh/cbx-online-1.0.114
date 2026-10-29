from cbx.db import SessionLocal
import os
from cbx.models import Folder, StoredFile

from tests._pdfs import municipal_bill, scanned_pdf, vendor_invoice


def _upload(client, name, data, **fields):
    return client.post("/api/warehouse/upload", files=[("files", (name, data, "application/pdf"))], data=fields)


def test_upload_goes_to_inbox_and_library(user):
    r = _upload(user, "EKU_2026-07_acc4471.pdf", municipal_bill())
    assert r.status_code == 200, r.text
    f = r.json()["files"][0]
    assert f["in_library"] and f["library"] == "PDFs"
    db = SessionLocal()
    try:
        orig = db.get(StoredFile, f["id"])
        folder = db.get(Folder, orig.folder_id)
        assert folder.name == "Inbox" and folder.path.startswith("Teams/Utilities billing/Thandi Mokoena")
        copies = db.query(StoredFile).filter(StoredFile.library_copy_of == orig.id).all()
        assert len(copies) == 1 and db.get(Folder, copies[0].folder_id).path == "Shared library/PDFs"
    finally:
        db.close()
    mine = user.get("/api/warehouse/mine").json()
    assert [s["name"] for s in mine["subs"]] == ["Inbox", "Work", "Results"]
    assert mine["subs"][0]["files"] >= 1


def test_duplicate_detection_exact_and_near(user):
    data = municipal_bill(account="9999 1111 22")
    r1 = _upload(user, "bill_a.pdf", data)
    assert r1.json()["files"][0]["duplicates"] == []
    r2 = _upload(user, "bill_a_copy.pdf", data)  # exact
    d = r2.json()["files"][0]
    assert d["duplicates"] and d["duplicates"][0]["exact"] is True
    assert "looks like a copy of bill_a.pdf" in d["duplicate_note"].lower()
    # near: same text, different bytes (different PDF title → different sha256)
    from tests._pdfs import text_pdf

    lines = ["NEAR DUP TEST", "Account number: 5555", "TOTAL DUE R 1.00"]
    a = text_pdf(lines, title="one")
    b = text_pdf(lines, title="two")
    assert a != b
    _upload(user, "near1.pdf", a)
    r3 = _upload(user, "near2.pdf", b)
    d3 = r3.json()["files"][0]
    assert d3["duplicates"] and d3["duplicates"][0]["exact"] is False


def test_download_permissions(user, admin, owner):
    fid = _upload(user, "private_bill.pdf", municipal_bill(account="1234")).json()["files"][0]["id"]
    assert user.get(f"/api/warehouse/files/{fid}/download").status_code == 200
    assert admin.get(f"/api/warehouse/files/{fid}/download").status_code == 200
    # another user in a different team can't
    pw = owner.post("/api/users", json={"email": "other@test.local", "name": "Other Person", "role": "user", "team_name": "Procurement"}).json().get("temp_password")
    if not pw:
        uid = next(u["id"] for u in owner.get("/api/users").json()["users"] if u["email"] == "other@test.local")
        pw = owner.post(f"/api/users/{uid}/temp-password").json()["temp_password"]
    from fastapi.testclient import TestClient

    from cbx.main import app
    from tests.conftest import login

    other = TestClient(app)
    login(other, "other@test.local", pw)
    assert other.get(f"/api/warehouse/files/{fid}/download").status_code == 403
    # user can't list another person's folder
    db = SessionLocal()
    try:
        f = db.get(StoredFile, fid)
        folder_id = f.folder_id
    finally:
        db.close()
    assert other.get(f"/api/warehouse/folders/{folder_id}/files").status_code == 403
    assert user.get(f"/api/warehouse/folders/{folder_id}/files").status_code == 200


def test_tree_and_folders_admin_only(admin, user):
    assert user.get("/api/warehouse/tree").status_code == 403
    t = admin.get("/api/warehouse/tree").json()
    names = [r["name"] for r in t["roots"]]
    # Shared library first, Tools (when a tool has filed something) before Teams
    assert names[0] == "Shared library" and names.index("Teams") == (2 if "Tools" in names else 1)
    lib = t["roots"][0]
    assert {c["name"] for c in lib["children"]} >= {"PDFs", "Workflows", "Master sheets"}
    teams = next(r for r in t["roots"] if r["name"] == "Teams")
    ut = next(c for c in teams["children"] if c["name"] == "Utilities billing")
    person = next(c for c in ut["children"] if c["name"] == "Thandi Mokoena")
    assert {c["name"] for c in person["children"]} == {"Inbox", "Work", "Results"}
    rows = admin.get(f"/api/warehouse/folders/{person['id']}/files").json()
    assert [r["name"] for r in rows["rows"][:3]] == ["Inbox", "Work", "Results"]
    assert rows["display_path"].startswith("Warehouse/Teams/")
    s = admin.get("/api/warehouse/stats").json()
    assert s["files"] >= 1 and s["people"] >= 1 and "inbox_waiting" in s


def _run_pipeline():
    """What the warehouse-inbox routine does every 30 s (the button-less /pipeline/run went in 1.0.79)."""
    from cbx.warehouse.pipeline import run_once

    with SessionLocal() as db:
        done = [{"id": f.id, "file": f.name, "detected_as": f.detected_as, "goes_to": f.goes_to, "status": f.pipeline_status} for f in run_once(db)]
        db.commit()
    return done


def test_pipeline_detects_municipal_bill_and_ocr(user, admin):
    fid = _upload(user, "EKU_pipeline.pdf", municipal_bill(account="7777")).json()["files"][0]["id"]
    vid = _upload(user, "bidvest_INV-20441.pdf", vendor_invoice()).json()["files"][0]["id"]
    sid = _upload(user, "IMG_2041.pdf", scanned_pdf(["CITY OF TSHWANE", "MUNICIPAL ACCOUNT", "Account number 555 001", "Electricity 4 200 kWh", "TOTAL DUE R 12 345.00"])).json()["files"][0]["id"]
    done = {p["id"]: p for p in _run_pipeline()}
    assert done[fid]["detected_as"] == "Ekurhuleni bill"
    assert done[fid]["goes_to"] == "Municipal accounts"
    assert done[fid]["status"] in ("detected", "added")
    assert done[vid]["detected_as"].startswith("Vendor invoice")
    import shutil

    from cbx.config import settings as _st

    has_ocr = os.path.exists(_st.tesseract_cmd) if _st.tesseract_cmd else bool(shutil.which("tesseract"))
    if has_ocr:
        assert done[sid]["detected_as"] in ("Tshwane bill", "City of Tshwane bill"), done[sid]
    else:
        # No Tesseract on this server: the scan is still recognised as a scan and parked, not lost.
        assert done[sid]["detected_as"] == "Unknown · scanned", done[sid]
    db = SessionLocal()
    try:
        s = db.get(StoredFile, sid)
        if has_ocr:
            assert s.meta.get("ocr") is True
            assert s.pipeline_status in ("detected", "added")
        else:
            assert s.pipeline_status in ("ocr", "unknown", "detected")
    finally:
        db.close()
    rows = admin.get("/api/warehouse/pipeline").json()["rows"]
    assert any(x["file"] == "EKU_pipeline.pdf" and x["from"] == "Thandi" for x in rows)
    # second pass has nothing left to do
    assert _run_pipeline() == []


def test_onedrive_disabled_is_harmless(owner, user):
    s = owner.get("/api/warehouse/onedrive/status").json()
    assert s["enabled"] is False and s["connected"] is False
    t = owner.post("/api/warehouse/onedrive/test").json()
    assert t["ok"] is False and "switched off" in t["message"]
    assert user.post("/api/warehouse/onedrive/test").status_code == 403
    # the sync routine is a no-op
    from cbx.warehouse import onedrive

    onedrive.sync_routine()
    # an upload still works
    assert _upload(user, "after_od.pdf", municipal_bill(account="42")).status_code == 200


def test_upload_to_folder_requires_ownership(user, admin):
    tree = admin.get("/api/warehouse/tree").json()
    lib = next(c for c in tree["roots"][0]["children"] if c["name"] == "PDFs")
    assert _upload(user, "x.pdf", municipal_bill(), folder_id=str(lib["id"])).status_code == 403
    assert _upload(admin, "x.pdf", municipal_bill(), folder_id=str(lib["id"])).status_code == 200


def test_library_upload_is_not_mirrored_twice_and_results_stay_private(admin, user):
    tree = admin.get("/api/warehouse/tree").json()
    lib = next(c for c in tree["roots"][0]["children"] if c["name"] == "PDFs")
    r = _upload(admin, "lib_direct.pdf", municipal_bill(account="lib-1"), folder_id=str(lib["id"]))
    assert r.status_code == 200, r.text
    fid = r.json()["files"][0]["id"]
    db = SessionLocal()
    try:
        assert db.query(StoredFile).filter(StoredFile.library_copy_of == fid).count() == 0  # no "lib_direct (2).pdf"
        assert db.get(Folder, db.get(StoredFile, fid).folder_id).path == "Shared library/PDFs"
    finally:
        db.close()
    # a user dropping something straight into Results keeps it to themselves
    mine = user.get("/api/warehouse/mine").json()
    results = next(s for s in mine["subs"] if s["name"] == "Results")
    r = _upload(user, "my_result.pdf", municipal_bill(account="res-1"), folder_id=str(results["id"]))
    assert r.status_code == 200, r.text
    f = r.json()["files"][0]
    assert f["source"] == "result" and f["in_library"] is False
    db = SessionLocal()
    try:
        assert db.query(StoredFile).filter(StoredFile.library_copy_of == f["id"]).count() == 0
    finally:
        db.close()


def test_safe_name_never_makes_a_path_step():
    from cbx.warehouse.folders import safe_name

    assert safe_name("..") == "unnamed"
    assert safe_name(" . ") == "unnamed"
    assert safe_name("../../etc/passwd") == "-etc-passwd" or "/" not in safe_name("../../etc/passwd")
    assert "\\" not in safe_name("C:\\Users\\x\\bill.pdf") and safe_name("C:\\Users\\x\\bill.pdf").endswith("bill.pdf")
    assert safe_name("CON.pdf") == "_CON.pdf"  # Windows reserved device name
    assert safe_name("Utilities billing") == "Utilities billing"
    assert len(safe_name("x" * 400)) <= 150


def test_onedrive_mirror_failure_is_surfaced_not_swallowed(owner, user, monkeypatch):
    from cbx.config import settings
    from cbx.warehouse import onedrive

    def broken(_f):
        raise onedrive.OneDriveError("Graph said no")

    monkeypatch.setattr(settings, "onedrive_enabled", True)
    monkeypatch.setattr(onedrive, "upload_file", broken)
    onedrive._state["last_error"] = ""
    try:
        r = _upload(user, "mirror_fail.pdf", municipal_bill(account="od-1"))
        assert r.status_code == 200, r.text  # local save never blocks on the mirror
        assert "Graph said no" in onedrive._state["last_error"]
        assert "Graph said no" in owner.get("/api/warehouse/onedrive/status").json()["last_error"]
        assert user.get("/api/warehouse/onedrive/status").json()["last_error"] == ""  # users don't get Graph internals
    finally:
        onedrive._state["last_error"] = ""


def test_denied_download_is_in_the_audit_trail(owner, user, admin):
    fid = _upload(admin, "admins_own.pdf", municipal_bill(account="adm-1")).json()["files"][0]["id"]
    assert user.get(f"/api/warehouse/files/{fid}/download").status_code == 403
    rows = owner.get("/api/audit?action=file_denied").json()["rows"]
    assert rows and rows[0]["outcome"] == "blocked" and rows[0]["actor"] == "thandi"
