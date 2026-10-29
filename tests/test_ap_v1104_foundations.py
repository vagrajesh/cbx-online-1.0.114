"""1.0.104 (SPEC-1104 Phase 1, Builder F): the Valuence AP controls' foundations — portfolios (F0a) and AP roles with
separation of duties (F0b). Portfolios resolve from company codes, their overrides reach Pip (Shell's electricity posts to
SA101), admins edit them; AP roles per portfolio with the bootstrap rule; a reviewer is never someone who touched the
document; the self-approval paths are closed. Account numbers and invoices are made up."""
from __future__ import annotations

import random
import uuid

import pytest

from cbx.ap import capture as C
from cbx.ap import masters as M
from cbx.ap import portfolio as PF
from cbx.ap import roles as R
from cbx.db import SessionLocal
from cbx.models import AuditLog, User
from cbx.models.ap import ApBatch, ApCapture, ApEntity, ApItem, ApPortfolio, ApRole, ApUtilityAccount

from .test_ap_capture_1098 import _admin, _app, _chart_of_accounts, _drop, _sheet, _user, eskom_pdf, signed_in  # noqa: F401


@pytest.fixture(autouse=True)
def _clean():
    """No AP role, changed portfolio, test account or company code is left behind (other tests run in bootstrap)."""
    with SessionLocal() as s:
        before = {p.code: (dict(p.settings or {}), dict(p.fixed or {}), dict(p.gl or {}), p.company_codes, p.name, p.is_active, p.is_default) for p in s.query(ApPortfolio).all()}
    yield
    with SessionLocal() as s:
        s.query(ApRole).delete()
        for p in s.query(ApPortfolio).all():
            if p.code not in before:
                s.delete(p)
                continue
            p.settings, p.fixed, p.gl, p.company_codes, p.name, p.is_active, p.is_default = before[p.code]
        s.query(ApUtilityAccount).filter(ApUtilityAccount.updated_by == "v1104 test").delete()
        s.query(ApEntity).filter(ApEntity.updated_by == "v1104 test").delete()
        s.commit()


def _acct(company: str = "", portfolio: str = "") -> str:
    no = "50" + "".join(random.choice("0123456789") for _ in range(8))
    with SessionLocal() as s:
        s.add(ApUtilityAccount(account_key=M.acct_key(no), account=no, vendor="700999", cost_centre="01306", site="Test Site " + no, municipality="ESKOM",
                               company_code=company, portfolio=portfolio, source="excel", updated_by="v1104 test"))
        s.commit()
    return no


def _entity(code: str) -> None:
    with SessionLocal() as s:
        if not s.query(ApEntity).filter(ApEntity.code == code).first():
            s.add(ApEntity(code=code, name=f"Test {code}", entity="Test", source="person", updated_by="v1104 test"))
            s.commit()


def _uid(email: str) -> int:
    with SessionLocal() as s:
        return s.query(User).filter(User.email == email).one().id


def _cap(c, bid: int, name: str) -> dict:
    return c.get(f"/api/ap/captures/{_sheet(c, bid)[name]['id']}").json()


# ---- F0a: portfolios ---------------------------------------------------------------------------------------------------------
def test_the_portfolios_are_seeded_and_resolve_from_company_codes():
    with SessionLocal() as db:
        assert [p.code for p in PF.portfolios(db)] == ["EFM-SA", "GENSEC", "SHELL", "TELKOM", "SWIFTNET", "EGYPT"]
        assert PF.default(db).code == "EFM-SA"
        want = {"X143": "EFM-SA", "XA00": "EFM-SA", "XA33": "EFM-SA", "XA60": "EFM-SA", "Z999": "GENSEC", "Z102": "GENSEC", "T100": "SHELL",
                "T101": "SHELL", "t102": "SHELL", "T000": "TELKOM", "X104": "EGYPT"}
        for code, pf in want.items():
            assert PF.for_company(db, code).code == pf, code
        # not claimed: the default (EFM-SA), or None when asked without a fallback; a range is letters + the same digits
        for code in ("XA61", "XB00", "X1430", ""):
            assert PF.for_company(db, code).code == "EFM-SA" and PF.for_company(db, code, fallback=False) is None, code
        # the settings: the portfolio's own, else the default; Shell's 15 % carries Edwin's open question
        shell = PF.get(db, "shell")
        assert PF.setting(db, shell, "variance_pct") == 15 and PF.setting(db, "TELKOM", "variance_pct") == 20 and PF.setting(db, "EFM-SA", "variance_pct") == 20
        assert "Senior FM" in shell.notes["variance_pct"]
        assert PF.setting(db, "EFM-SA", "review_threshold_r") == 50000 and PF.setting(db, None, "review_sample_pct") == 10
        assert PF.setting(db, "EFM-SA", "vendor_review_from") == "2026-10-19" and PF.setting(db, "EFM-SA", "retention_years") == {"default": 5}
        assert PF.setting(db, "TELKOM", "approval_matrix")[0] == {"up_to_r": 4000000, "approver": "SFM"}
        assert "Proof of payment" in PF.setting(db, "SHELL", "evidence_checklist") and PF.setting(db, "EFM-SA", "evidence_checklist") == ["SAP upload file", "Review workbook"]
        # a later builder's key: its default, offered in Settings → Portfolios
        assert PF.setting(db, "SHELL", "v1104_test_days", 7) == 7 and any(k["key"] == "v1104_test_days" and k["default"] == 7 for k in PF.keys_out())
        # Pip's tables: Shell's overrides over the global ones; EFM-SA has none
        assert PF.gl_for(db, "SHELL")["electricity"] == "SA101" and PF.gl_for(db, "EFM-SA")["electricity"] == "SA110"
        assert PF.fixed_for(db, "SHELL")["Company"] == "T100" and PF.fixed_for(db, "EGYPT")["Currency"] == "EGP" and PF.fixed_for(db, "EFM-SA")["Company"] == "T000"
        t = PF.tables_for(db, "SHELL", {**M.tables(db), "postdate": "01012026"})
        assert t["postdate"] == "01012026" and t["gl"]["vat"] == "JA600" and t["gl"]["electricity"] == "SA101"


def test_shell_s_overrides_reach_pip_and_tess_knows_them():
    _entity("T100")
    u = _user()
    shell_acct, plain_acct = _acct("T100"), _acct("")
    inv = str(random.randint(700000000, 799999999))
    b = _drop(u, {"shell.pdf": eskom_pdf(shell_acct, inv), "plain.pdf": eskom_pdf(plain_acct, str(int(inv) + 1))}, f"Auto {inv}")
    shell, plain = _cap(u, b["id"], "shell.pdf"), _cap(u, b["id"], "plain.pdf")
    gls = lambda c: [r["account"] for r in c["sap_rows"] if r["pkey"] in ("40", "50")]  # noqa: E731
    assert "SA101" in gls(shell) and "SA110" not in gls(shell) and shell["sap_rows"][0]["comp"] == "T100"
    assert not any("SA101" in x["text"] for x in shell["sap_issues"]), shell["sap_issues"]  # the portfolio's G/L is admin-kept
    assert "SA110" in gls(plain) and plain["sap_rows"][0]["comp"] == "T000"  # a bill with no company code posts as before (T000: Telkom)
    with SessionLocal() as s:
        caps = {s.get(ApItem, c.item_id).name: c for c in s.query(ApCapture).filter(ApCapture.batch_id == b["id"]).all()}
        assert (caps["shell.pdf"].portfolio, caps["plain.pdf"].portfolio) == ("SHELL", "TELKOM")
    assert {p["code"] for p in u.get("/api/ap").json()["portfolios"]} >= {"EFM-SA", "SHELL"}
    # picked in the drop form: the batch's portfolio wins, and an account with no company code posts to Shell's T100
    acct2 = _acct("")
    b2 = u.post("/api/ap/batches", files=[("files", ("picked.pdf", eskom_pdf(acct2, str(int(inv) + 2)), "application/pdf"))], data={"name": f"Picked {inv}", "portfolio": "shell"})
    assert b2.status_code == 200, b2.text
    picked = _cap(u, b2.json()["id"], "picked.pdf")
    assert picked["sap_rows"][0]["comp"] == "T100" and "SA101" in gls(picked)
    sheet = u.get(f"/api/ap/batches/{b2.json()['id']}/sap").json()
    assert sheet["portfolio"] == {"code": "SHELL", "name": "Shell", "picked": True}
    with SessionLocal() as s:
        assert s.get(ApBatch, b2.json()["id"]).portfolio_how == "picked"
        assert s.query(AuditLog).filter(AuditLog.action == "ap_batch_portfolio", AuditLog.target == f"ap_batch:{b2.json()['id']}").count() == 1
    # back to "from the company codes": Pip builds it again (T000, SA110), audited
    r = u.post(f"/api/ap/batches/{b2.json()['id']}/portfolio", json={"portfolio": ""})
    assert r.status_code == 200 and r.json()["portfolio"]["picked"] is False
    again = _cap(u, b2.json()["id"], "picked.pdf")
    assert again["sap_rows"][0]["comp"] == "T000" and "SA110" in gls(again)
    folders = {x["id"]: x["portfolio"] for x in u.get("/api/ap").json()["batches"]}  # the folder list: picked, or most of its documents'
    assert folders[b2.json()["id"]] == {"code": "TELKOM", "name": "Telkom", "picked": False} and folders[b["id"]]["code"] in ("SHELL", "TELKOM")
    # a portfolio that isn't one is refused in plain words
    bad = u.post("/api/ap/batches", files=[("files", ("x.pdf", eskom_pdf(acct2, "1"), "application/pdf"))], data={"portfolio": "NOPE"})
    assert bad.status_code == 400 and "NOPE is not a portfolio" in bad.json()["detail"]


def test_admins_edit_a_portfolio_and_pip_builds_again():
    _entity("T100")
    adm, u = _admin(), _user()
    assert u.get("/api/ap/portfolios").status_code == 403 and u.put("/api/ap/portfolios/SHELL", json={"name": "x"}).status_code == 403
    got = adm.get("/api/ap/portfolios").json()
    shell = next(p for p in got["portfolios"] if p["code"] == "SHELL")
    assert shell["settings"]["variance_pct"] == 15 and shell["own"] == ["evidence_checklist", "variance_pct"] and shell["gl"] == {"electricity": "SA101"}
    keys = {k["key"]: k for k in got["keys"]}
    assert keys["estimate_basis"]["choices"] == ["last", "avg3", "avg6", "daily", "prepaid"] and keys["evidence_checklist"]["kind"] == "lines"
    acct = _acct("T101")
    b = _drop(u, {"bill.pdf": eskom_pdf(acct, str(random.randint(600000000, 699999999)))}, "Edit run")
    assert "SA101" in [r["account"] for r in _cap(u, b["id"], "bill.pdf")["sap_rows"]]
    r = adm.put("/api/ap/portfolios/SHELL", json={"gl": {"electricity": "SA102"}, "settings": {"variance_pct": "18", "review_sample_pct": 10, "estimate_basis": "avg3",
                                                                                                "retention_years": "default: 7\nPOP: 10", "evidence_checklist": "A\nB"}})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["settings"]["variance_pct"] == 18 and p["settings"]["estimate_basis"] == "avg3" and p["settings"]["retention_years"] == {"default": 7, "POP": 10}
    assert "review_sample_pct" not in p["own"]  # the same as the default: nothing of its own
    assert "SA102" in [x["account"] for x in _cap(u, b["id"], "bill.pdf")["sap_rows"]]  # not uploaded yet: built again
    with SessionLocal() as s:
        a = s.query(AuditLog).filter(AuditLog.action == "ap_portfolio", AuditLog.target == "ap_portfolio:SHELL").order_by(AuditLog.id.desc()).first()
        assert "variance_pct 15 → 18" in a.detail and "SA102" in a.detail
    # back to the default; plain-words refusals; not found
    assert adm.put("/api/ap/portfolios/SHELL", json={"settings": {"variance_pct": None}}).json()["settings"]["variance_pct"] == 20
    for body, words in (({"settings": {"variance_pct": "lots"}}, "give a number"), ({"settings": {"estimate_basis": "guess"}}, "one of last"),
                        ({"settings": {"vendor_review_from": "soon"}}, "yyyy-mm-dd"), ({"company_codes": "T1-X9"}, "not a range"), ({"name": ""}, "a name")):
        r = adm.put("/api/ap/portfolios/SHELL", json=body)
        assert r.status_code == 400 and words in r.json()["detail"], (body, r.text)
    assert adm.put("/api/ap/portfolios/NOPE", json={"name": "x"}).status_code == 404
    # a new portfolio
    r = adm.post("/api/ap/portfolios", json={"code": "acme-1", "name": "Acme", "company_codes": "XK00-XK07", "settings": {"review_threshold_r": 10000}})
    assert r.status_code == 200 and r.json()["code"] == "ACME-1" and r.json()["settings"]["review_threshold_r"] == 10000
    with SessionLocal() as s:
        assert PF.for_company(s, "XK05").code == "ACME-1"
    assert adm.post("/api/ap/portfolios", json={"code": "ACME-1"}).status_code == 400


# ---- F0b: AP roles ----------------------------------------------------------------------------------------------------------
def test_the_roles_grid_and_the_bootstrap_rule():
    adm, u = _admin(), _user()
    viewer = signed_in("v1104.viewer@test.local", "viewer", name="Vic Viewer", tools=["ap"])
    uid, aid = _uid("cap.user@test.local"), _uid("cap.admin@test.local")
    g = adm.get("/api/ap/roles").json()
    assert [r["key"] for r in g["roles"]] == list(R.ROLES) and set(g["bootstrap"]) == {"EFM-SA", "GENSEC", "SHELL", "TELKOM", "SWIFTNET", "EGYPT"}
    assert any(p["id"] == uid for p in g["people"]) and not any(p["email"] == "v1104.viewer@test.local" for p in g["people"])
    with SessionLocal() as s:
        cara = s.get(User, uid)
        # bootstrap: every AP user with write access captures, reviews, posts and approves; not reconciliation review; viewers nothing
        assert all(R.may(s, cara, r, "SHELL") for r in ("capturer", "reviewer", "poster", "approver")) and not R.may(s, cara, "recon_reviewer", "SHELL")
        assert not R.may(s, s.query(User).filter(User.email == "v1104.viewer@test.local").one(), "capturer", "SHELL")
    assert u.get("/api/ap").json()["controls"]["controls"] is False  # no role row of her own: no Controls tab
    # only admins give roles
    assert u.put(f"/api/ap/roles/{uid}", json={"portfolio": "SHELL", "roles": ["reviewer"]}).status_code == 403
    r = adm.put(f"/api/ap/roles/{aid}", json={"portfolio": "SHELL", "roles": ["reviewer", "poster"]})
    assert r.status_code == 200 and r.json()["added"] == ["reviewer", "poster"]
    for body, code in (({"portfolio": "SHELL", "roles": ["boss"]}, 400), ({"portfolio": "NOPE", "roles": ["reviewer"]}, 400)):
        assert adm.put(f"/api/ap/roles/{uid}", json=body).status_code == code
    assert adm.put("/api/ap/roles/999999", json={"portfolio": "SHELL", "roles": ["reviewer"]}).status_code == 404
    assert adm.put(f"/api/ap/roles/{_uid('v1104.viewer@test.local')}", json={"portfolio": "SHELL", "roles": ["reviewer"]}).status_code == 400  # a viewer can't
    with SessionLocal() as s:
        cara, ayanda = s.get(User, uid), s.get(User, aid)
        # SHELL has rows now: bootstrap is over there (Cara holds nothing in SHELL), still on elsewhere
        assert not R.may(s, cara, "reviewer", "SHELL") and not R.may(s, cara, "capturer", "SHELL") and R.may(s, cara, "reviewer", "TELKOM")
        assert R.may(s, ayanda, "reviewer", "SHELL") and not R.may(s, ayanda, "approver", "SHELL")
        assert R.may(s, cara, "reviewer", None)  # in some portfolio
        a = s.query(AuditLog).filter(AuditLog.action == "ap_role").order_by(AuditLog.id.desc()).first()
        assert "in SHELL: added reviewer, poster" in a.detail
    g = adm.get("/api/ap/roles").json()
    assert "SHELL" not in g["bootstrap"] and next(p for p in g["people"] if p["id"] == aid)["roles"]["SHELL"] == ["reviewer", "poster"]
    # a row for every portfolio ends bootstrap everywhere
    adm.put(f"/api/ap/roles/{uid}", json={"portfolio": "", "roles": ["capturer"]})
    with SessionLocal() as s:
        cara = s.get(User, uid)
        assert R.may(s, cara, "capturer", "TELKOM") and not R.may(s, cara, "reviewer", "TELKOM") and R.roles_of(s, cara) == {p: ["capturer"] for p in ("EFM-SA", "GENSEC", "SHELL", "TELKOM", "SWIFTNET", "EGYPT")}
    assert adm.get("/api/ap/roles").json()["bootstrap"] == []
    me = adm.get("/api/ap").json()["controls"]
    assert me["controls"] is True and me["roles"]["SHELL"] == ["reviewer", "poster"]
    # a capturer only drops a folder for a portfolio they capture in
    assert u.post("/api/ap/batches", files=[("files", ("a.pdf", b"%PDF-1.4", "application/pdf"))], data={"portfolio": "SHELL"}).status_code == 200
    adm.put(f"/api/ap/roles/{uid}", json={"portfolio": "", "roles": []})
    adm.put(f"/api/ap/roles/{uid}", json={"portfolio": "TELKOM", "roles": ["capturer"]})
    r = u.post("/api/ap/batches", files=[("files", ("a.pdf", b"%PDF-1.4", "application/pdf"))], data={"portfolio": "SHELL"})
    assert r.status_code == 403 and "capturer role in SHELL" in r.json()["detail"]
    # removed: back to bootstrap
    adm.put(f"/api/ap/roles/{aid}", json={"portfolio": "SHELL", "roles": []})
    adm.put(f"/api/ap/roles/{uid}", json={"portfolio": "TELKOM", "roles": []})
    with SessionLocal() as s:
        assert R.may(s, s.get(User, uid), "reviewer", "SHELL")


def test_separation_of_duties_a_reviewer_never_touched_the_document():
    adm, u = _admin(), _user()
    other = signed_in("v1104.rev@test.local", "user", name="Rene Reviewer", tools=["ap"])  # noqa: F841 — signed in once (a person)
    acct = _acct("")
    b = _drop(u, {"sod.pdf": eskom_pdf(acct, str(random.randint(800000000, 899999999)))}, f"SoD {uuid.uuid4().hex[:4]}")
    cid = _sheet(u, b["id"])["sod.pdf"]["id"]
    # Cara corrects the account number she typed: she touched it
    c = u.get(f"/api/ap/captures/{cid}").json()
    r = u.post(f"/api/ap/captures/{cid}/decide", json={"action": "save", "data": {"fields": {"account": acct, "invoice_no": c["fields"].get("invoice_no") or "1"}}})
    assert r.status_code == 200, r.text
    uid, aid, rid = _uid("cap.user@test.local"), _uid("cap.admin@test.local"), _uid("v1104.rev@test.local")
    with SessionLocal() as s:
        cap = s.get(ApCapture, cid)
        assert uid in R.touched_by(s, cap) and aid not in R.touched_by(s, cap) and uid not in R.touched_by(s, cap, exclude_actions=("ap_capture_save",))
        assert R.touched_by(s, cap, include_dropper=True) >= {uid}
        cara, ayanda, rene = s.get(User, uid), s.get(User, aid), s.get(User, rid)
        # bootstrap: everyone may review — but never what they touched
        with pytest.raises(R.Forbidden, match="a different person reviews it"):
            R.require(s, cara, "reviewer", capture=cap)
        R.require(s, rene, "reviewer", capture=cap)
        R.require(s, ayanda, "approver", capture=cap)
        s.add(ApRole(user_id=aid, portfolio="", role="reviewer"))
        s.flush()
        # roles exist: Rene holds none; the admin holds reviewer, not approver — admins don't pass by being admins
        with pytest.raises(R.Forbidden, match="needs the reviewer role"):
            R.require(s, rene, "reviewer", capture=cap)
        with pytest.raises(R.Forbidden, match="approver role"):
            R.require(s, ayanda, "approver", capture=cap)
        R.require(s, ayanda, "reviewer", capture=cap)
        # once the admin touched it (a correction of her own), she can't review it either
        s.add(AuditLog(actor_id=aid, actor_label="cap.admin", action="ap_capture_save", target=f"ap_item:{cap.item_id}", detail="test"))
        s.flush()
        with pytest.raises(R.Forbidden):
            R.require(s, ayanda, "reviewer", capture=cap)
        s.rollback()


def _stopped_item(dropper_email: str) -> int:
    with SessionLocal() as s:
        b = ApBatch(user_id=_uid(dropper_email), name=f"Self {uuid.uuid4().hex[:4]}", status="ready", portfolio="SHELL", portfolio_how="picked")
        s.add(b)
        s.flush()
        it = ApItem(batch_id=b.id, name="stopped.pdf", kind="invoice", status="exception", level=3, stops=1, findings=[{"severity": "stop", "text": "duplicate"}])
        s.add(it)
        s.commit()
        return it.id


def test_the_self_approval_paths_are_closed():
    adm, u = _admin(), _user()
    rev = signed_in("v1104.rev@test.local", "user", name="Rene Reviewer", tools=["ap"])
    # bootstrap: the dropper may clear Ivy's stop on her own file — audited ap_self_clear
    iid = _stopped_item("cap.user@test.local")
    r = u.post(f"/api/ap/items/{iid}/sign", json={"action": "clear", "note": "checked with the supplier"})
    assert r.status_code == 200 and r.json()["status"] == "cleared"
    with SessionLocal() as s:
        assert s.query(AuditLog).filter(AuditLog.action == "ap_self_clear", AuditLog.target == f"ap_item:{iid}").count() == 1
    # once SHELL has roles: refused; a second person clears it
    adm.put(f"/api/ap/roles/{_uid('v1104.rev@test.local')}", json={"portfolio": "SHELL", "roles": ["reviewer"]})
    iid = _stopped_item("cap.user@test.local")
    r = u.post(f"/api/ap/items/{iid}/sign", json={"action": "clear", "note": "fine"})
    assert r.status_code == 403 and "a second person clears" in r.json()["detail"]
    assert rev.post(f"/api/ap/items/{iid}/sign", json={"action": "clear", "note": "fine"}).status_code == 200
    # an admin's own vendor coding is learned (yellow) and she can't confirm it herself: a second admin does (test_ap_fix_1098)


def test_the_controls_tab_and_the_pages_are_served():
    u = _user()
    page = u.get("/static/views/ap.js").text
    assert "ap_controls/index.js" in page and 'data-tab=\\"controls\\"' in page and "goControls" in page
    idx = u.get("/static/views/ap_controls/index.js")
    assert idx.status_code == 200 and "export const ORDER" in idx.text
    assert u.get("/static/views/ap_controls/settings.js").status_code == 200


# ---- 1.0.104 review fixes --------------------------------------------------------------------------------------------------------
def test_answered_is_held_to_the_self_clear_rule_too():
    adm, u = _admin(), _user()
    rev = signed_in("v1104.rev@test.local", "user", name="Rene Reviewer", tools=["ap"])
    iid = _stopped_item("cap.user@test.local")
    assert u.post(f"/api/ap/items/{iid}/sign", json={"action": "answered", "note": "replied"}).status_code == 200  # bootstrap: allowed, audited
    with SessionLocal() as s:
        assert s.query(AuditLog).filter(AuditLog.action == "ap_self_clear", AuditLog.target == f"ap_item:{iid}").count() == 1
    adm.put(f"/api/ap/roles/{_uid('v1104.rev@test.local')}", json={"portfolio": "SHELL", "roles": ["reviewer"]})
    iid = _stopped_item("cap.user@test.local")
    r = u.post(f"/api/ap/items/{iid}/sign", json={"action": "answered", "note": "replied"})
    assert r.status_code == 403 and "a second person clears" in r.json()["detail"]
    assert rev.post(f"/api/ap/items/{iid}/sign", json={"action": "answered", "note": "replied"}).status_code == 200


def test_the_person_who_dropped_the_folder_never_reviews_its_documents():
    adm = _admin()
    signed_in("v1104.rev@test.local", "user", name="Rene Reviewer", tools=["ap"])
    b = _drop(adm, {"dropped.pdf": eskom_pdf(_acct(""), str(random.randint(300000000, 399999999)))}, f"Drop {uuid.uuid4().hex[:4]}")
    cid = _sheet(adm, b["id"])["dropped.pdf"]["id"]
    with SessionLocal() as s:
        cap, ayanda, rene = s.get(ApCapture, cid), s.get(User, _uid("cap.admin@test.local")), s.get(User, _uid("v1104.rev@test.local"))
        assert ayanda.id not in R.touched_by(s, cap) and not R.independent(s, ayanda, cap) and R.independent(s, ayanda, cap, include_dropper=False)
        for role in ("reviewer", "approver", "recon_reviewer"):
            s.add(ApRole(user_id=ayanda.id, portfolio="", role=role))
        s.flush()
        for role in ("reviewer", "approver", "recon_reviewer"):
            with pytest.raises(R.Forbidden, match="dropped its folder"):
                R.require(s, ayanda, role, capture=cap)
        R.require(s, ayanda, "reviewer", capture=cap, include_dropper=False)
        s.rollback()
        R.require(s, rene, "reviewer", capture=cap)


def test_a_batch_moves_only_with_the_roles_and_never_against_a_document_s_own_portfolio():
    _entity("T100")
    adm, u = _admin(), _user()
    signed_in("v1104.rev@test.local", "user", name="Rene Reviewer", tools=["ap"])
    inv = random.randint(400000000, 499999999)
    # picked TELKOM in the drop form, but the bill's account is Shell's (T100): the document stays SHELL
    r = u.post("/api/ap/batches", files=[("files", ("shellbill.pdf", eskom_pdf(_acct("T100"), str(inv)), "application/pdf"))], data={"name": f"Pick {inv}", "portfolio": "TELKOM"})
    assert r.status_code == 200, r.text
    c = _cap(u, r.json()["id"], "shellbill.pdf")
    assert c["sap_rows"][0]["comp"] == "T100" and "SA101" in [x["account"] for x in c["sap_rows"]]
    with SessionLocal() as s:
        assert s.get(ApCapture, c["id"]).portfolio == "SHELL"
    # moving it to TELKOM later is refused in plain words
    r = u.post(f"/api/ap/batches/{r.json()['id']}/portfolio", json={"portfolio": "TELKOM"})
    assert r.status_code == 400 and "company code T100 (utility account" in r.json()["detail"] and "in SHELL, not TELKOM" in r.json()["detail"]
    # a utility account's own portfolio counts too
    acct = _acct("", portfolio="EGYPT")
    b2 = _drop(u, {"egypt.pdf": eskom_pdf(acct, str(inv + 1))}, f"Eg {inv}")
    r = u.post(f"/api/ap/batches/{b2['id']}/portfolio", json={"portfolio": "SHELL"})
    assert r.status_code == 400 and f"utility account {acct} is in EGYPT, not SHELL" in r.json()["detail"]
    # the capturer role in the OLD portfolio too: Cara captures in EFM-SA, Rene alone in TELKOM
    b3 = _drop(u, {"plain.pdf": eskom_pdf(_acct(""), str(inv + 2))}, f"Old {inv}")  # no company code of its own: TELKOM (T000)
    adm.put(f"/api/ap/roles/{_uid('v1104.rev@test.local')}", json={"portfolio": "TELKOM", "roles": ["capturer"]})
    r = u.post(f"/api/ap/batches/{b3['id']}/portfolio", json={"portfolio": "EFM-SA"})
    assert r.status_code == 403 and "capturer role in TELKOM" in r.json()["detail"]
    adm.put(f"/api/ap/roles/{_uid('cap.user@test.local')}", json={"portfolio": "TELKOM", "roles": ["capturer"]})
    assert u.post(f"/api/ap/batches/{b3['id']}/portfolio", json={"portfolio": "EFM-SA"}).status_code == 200


def test_an_admins_own_utility_account_waits_for_a_second_admin():
    adm = _admin()
    adm2 = signed_in("cap.admin2@test.local", "admin", name="Bongani Admin")
    no = "51" + "".join(random.choice("0123456789") for _ in range(8))
    with SessionLocal() as s:
        a = M.add_account(s, s.query(User).filter(User.email == "cap.admin@test.local").one(), {"account": no, "vendor": "700999", "cost_centre": "1", "site": "x"})
        s.commit()
        aid = a.id
        assert a.source == "person"
    try:
        r = adm.post(f"/api/ap/masters/utilities/{aid}/confirm")
        assert r.status_code == 403 and "You added this account" in r.json()["detail"]
        assert adm2.post(f"/api/ap/masters/utilities/{aid}/confirm").status_code == 200
    finally:
        with SessionLocal() as s:
            s.query(ApUtilityAccount).filter(ApUtilityAccount.id == aid).delete()
            s.commit()


def test_tess_knows_only_the_document_s_own_portfolio_overrides():
    with SessionLocal() as s:
        base = {"gls": {"SA110"}, "companies": {"X143"}, "tax_codes": {"I9"}}
        shell, telkom = ApCapture(portfolio="SHELL"), ApCapture(portfolio="TELKOM")
        k = PF.known_for(s, shell, base)
        assert "SA101" in k["gls"] and "T100" in k["companies"]
        k = PF.known_for(s, telkom, base)
        assert "SA101" not in k["gls"] and "T100" not in k["companies"] and k["gls"] == {"SA110"}


def test_the_portfolio_index_is_made_on_an_upgrade():
    from sqlalchemy import inspect, text

    from cbx.db import engine
    from cbx.migrate import MIGRATIONS

    step = dict(MIGRATIONS)["1104_ap_captures_portfolio_index"]
    with engine.begin() as c:
        c.execute(text("DROP INDEX IF EXISTS ix_ap_captures_portfolio"))
        assert "ix_ap_captures_portfolio" not in {i["name"] for i in inspect(c).get_indexes("ap_captures")}
        step(c)
        step(c)  # idempotent
        assert "ix_ap_captures_portfolio" in {i["name"] for i in inspect(c).get_indexes("ap_captures")}
