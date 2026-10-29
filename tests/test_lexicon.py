"""C-EXA's memory across layouts: the lexicon, what a person's correction teaches it, and the honest
accuracy that stops a profile from marking its own homework.

Runs with no GPU and no network (CBX_TESTING=1 → the model gateway is stubbed).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cbx.db import SessionLocal
from cbx.models import Document, Profile, ProfileVersion
from cbx.pdfstudio import corrections as C
from cbx.pdfstudio import lexicon as LX
from cbx.pdfstudio import repair as R
from cbx.pdfstudio.spans import read_document
from tests.fixtures.make_bills import make_bills

COLUMNS = [
    {"key": "account_no", "label": "Account number", "ctype": "text"},
    {"key": "kwh", "label": "kWh consumed", "ctype": "number"},
    {"key": "excl_vat", "label": "Amount excl. VAT", "ctype": "money"},
    {"key": "vat", "label": "VAT", "ctype": "money"},
    {"key": "total", "label": "Total due", "ctype": "money"},
    {"key": "deposit", "label": "Deposit held", "ctype": "money", "required": False},
]
EMF = [f"emfuleni-EMF-7712{i}.pdf" for i in range(3)]
EKU = [f"ekurhuleni-550144{i}.pdf" for i in range(28, 32)]  # the four Ekurhuleni bills (all text)


@pytest.fixture(scope="session", autouse=True)
def _schema():
    """These tests touch the database directly, so the schema must exist even when no client has
    started the app yet (pdf_lexicon is a new table — create_all makes it)."""
    from cbx.db import init_db

    init_db()


@pytest.fixture(scope="session")
def bills(tmp_path_factory):
    return make_bills(tmp_path_factory.mktemp("lexbills"))


def _bill(bills, name):
    return next(b for b in bills if b["name"] == name)


def _spans(bills, name):
    b = _bill(bills, name)
    data = Path(b["file"]).read_bytes()
    import hashlib

    return read_document(data, hashlib.sha256(data).hexdigest())


def _files(bills, names):
    return [("files", (b["name"], Path(b["file"]).read_bytes(), "application/pdf")) for b in bills if b["name"] in names]


def _run_batch(client, bills, names, name="Lexicon batch"):
    r = client.post("/api/pdfstudio/templates", json={"name": name + " template", "columns": COLUMNS})
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    r = client.post("/api/pdfstudio/batches", json={"template_id": tid, "name": name})
    assert r.status_code == 200, r.text
    bid = r.json()["id"]
    assert client.post(f"/api/pdfstudio/batches/{bid}/files", files=_files(bills, names)).status_code == 200
    assert client.post(f"/api/pdfstudio/batches/{bid}/start").status_code == 200
    r = client.get(f"/api/pdfstudio/batches/{bid}")
    assert r.status_code == 200, r.text
    return tid, bid, r.json()


# ---- the interface ------------------------------------------------------------------------------
def test_candidates_with_no_db_returns_the_shipped_vocabulary():
    rows = LX.candidates(None, {"key": "account_number", "label": "Account number", "ctype": "text", "hint": ""})
    assert rows, "day one must not be zero"
    labels = [r["label"] for r in rows]
    assert "Account Number" in labels and "Account No" in labels
    for r in rows:
        assert r["placement"] in ("right", "below", "left")
        assert set(("label", "placement", "confirmed", "failed")) <= set(r)
        assert r["failed"] == 0 and r["confirmed"] >= 1
    # the vocabulary covers the shapes these documents actually use
    for key, want in (("total_due", "Total Due"), ("new_reading", "New Reading"), ("consumption", "Consumption"), ("meter_no", "Meter No"), ("deposit", "Deposit"), ("vat_reg_no", "VAT No"), ("old_reading", "Old Reading"), ("closing_balance", "Closing Balance"), ("work_order", "Work Order")):
        got = [r["label"] for r in LX.candidates(None, {"key": key, "label": key.replace("_", " "), "ctype": "text"})]
        assert want in got, f"{key}: expected {want!r} in {got}"


def test_seed_vocabulary_is_shipped_and_sane():
    assert LX.seed_count() >= 100
    seen = set()
    for group, label, placement, n in LX.SEED:
        assert placement in ("right", "below", "left")
        assert 1 <= n <= 2, "a seed is a prior, not a rival: real confirmations must overtake it"
        assert (group, label.lower(), placement) not in seen
        seen.add((group, label.lower(), placement))
        assert LX._usable(label)
    # the banking-details block of a municipal statement must never be seeded: reading the
    # municipality's own bank account as the customer's account number is the bug this table exists
    # to prevent
    account = [l.lower() for g, l, _, _ in LX.SEED if g == "account"]
    assert not any("bank" in l or "branch" in l for l in account)


def test_confirm_raises_a_label_and_reject_demotes(db):
    # a meaning no batch in the suite reads, so the ranking here is only what this test writes
    col = {"key": "work_order_no", "label": "Work order no", "ctype": "text"}
    before = LX.candidates(db, col)
    assert before[0]["label"] != "Werksopdrag", [r["label"] for r in before[:3]]
    for layout in ("layout-a", "layout-b", "layout-c"):
        LX.confirm(db, column_key="work_order_no", label="Werksopdrag", placement="right", layout=layout, example="WO-4471")
    db.commit()
    after = LX.candidates(db, col)
    assert after[0]["label"] == "Werksopdrag", [r["label"] for r in after[:3]]
    assert after[0]["confirmed"] >= 3 and after[0]["failed"] == 0
    assert after[0]["example"] == "WO-4471"
    assert len(after[0]["layouts"]) == 3

    for layout in ("layout-d", "layout-e", "layout-f", "layout-g"):
        LX.reject(db, column_key="work_order_no", label="Werksopdrag", placement="right", layout=layout)
    db.commit()
    demoted = LX.candidates(db, col)
    assert demoted[0]["label"] != "Werksopdrag"
    row = next(r for r in demoted if r["label"] == "Werksopdrag")
    assert row["failed"] == 4 and row["confirmed"] - row["failed"] < 0


def test_a_label_learned_on_one_key_is_offered_for_a_synonym(db):
    LX.confirm(db, column_key="account_number", label="Klantenummer", placement="right", layout="sap-invoice")
    LX.confirm(db, column_key="account_number", label="Klantenummer", placement="right", layout="sap-invoice-2")
    db.commit()
    for key, label in (("acc_no", "Acc No"), ("account_no", "Account No"), ("customer_account", "Customer account"), ("ACCOUNT_NUMBER", "Account Number")):
        labels = [r["label"] for r in LX.candidates(db, {"key": key, "label": label, "ctype": "text"})]
        assert "Klantenummer" in labels, f"{key} did not inherit what account_number learned"
    # a different meaning must NOT inherit it
    assert "Klantenummer" not in [r["label"] for r in LX.candidates(db, {"key": "total_due", "label": "Total due", "ctype": "money"})]


def test_group_for_reads_the_columns_meaning():
    g = lambda k: LX.group_for({"key": k, "label": k.replace("_", " ")})  # noqa: E731
    assert g("account_number") == g("acc_no") == g("account_no") == g("customer_account") == "account"
    assert g("total_due") == g("closing_balance") == g("amount_due") == "total"
    assert g("old_reading") == g("previous_reading") == "reading_prev"
    assert g("new_reading") == g("current_reading") == "reading_curr"
    assert g("kwh") == g("units_consumed") == g("consumption") == "consumption"
    assert g("vat_reg_no") == "vat_number" and g("vat") == "vat"
    assert g("invoice_date") == g("statement_date") == "date"
    assert g("due_date") == "due_date"
    assert g("something_we_have_never_seen") == "something_we_have_never_seen"


def test_the_lexicon_never_raises_however_broken_the_database():
    class Exploding:
        def query(self, *a, **k):
            raise RuntimeError("the database is on fire")

        def in_nested_transaction(self):
            raise RuntimeError("still on fire")

        def begin_nested(self):
            raise RuntimeError("and again")

    bad = Exploding()
    col = {"key": "total_due", "label": "Total due", "ctype": "money"}
    rows = LX.candidates(bad, col)
    assert rows and rows[0]["label"], "a broken database must fall back to the shipped vocabulary"
    assert LX.confirm(bad, column_key="total_due", label="Totaal", placement="right", layout="x") is None
    assert LX.reject(bad, column_key="total_due", label="Totaal", placement="right", layout="x") is None
    assert LX.stats(bad)["learned"] == 0
    assert LX.candidates(bad, None) == [] or LX.candidates(bad, {}) is not None


# ---- a person's fix teaches the label -------------------------------------------------------------
def test_label_near_value_reads_the_printed_label_off_a_real_bill(bills):
    sp = _spans(bills, EMF[0])
    gold = _bill(bills, EMF[0])["gold"]
    got = C.label_near_value(sp, {"key": "deposit", "label": "Deposit held", "ctype": "money"}, gold["deposit"])
    assert got is not None, "the deposit is printed next to a label on this bill"  # noqa: S101
    label, placement = got
    assert label.lower() == "security held" and placement == "right"
    got = C.label_near_value(sp, {"key": "account_no", "label": "Account number", "ctype": "text"}, gold["account_no"])
    assert got is not None and got[0].lower().startswith("account no") and got[1] == "right"


def test_a_correction_teaches_the_lexicon_the_label(user, bills):
    tid, bid, batch = _run_batch(user, bills, EMF, name="Correction teaches")
    want = _bill(bills, EMF[0])
    doc = next(d for d in batch["documents"] if want["gold"]["account_no"].replace(" ", "") in d["filename"])
    gold = want["gold"]
    r = user.post(f"/api/pdfstudio/batches/{bid}/cells", json={"document_id": doc["id"], "column_key": "deposit", "value": f"{gold['deposit']:.2f}"})
    assert r.status_code == 200, r.text
    assert r.json()["verified"] is True, r.json()

    db = SessionLocal()
    try:
        rows = LX.candidates(db, {"key": "deposit_held", "label": "Deposit held", "ctype": "money"})
        learned = [x for x in rows if x.get("source") == "learned"]
        assert learned, f"a fix taught nothing: {[x['label'] for x in rows[:4]]}"
        top = learned[0]
        assert top["label"].lower() == "security held", top
        assert top["placement"] == "right"
        assert top["corrections"] >= 1, "the fix must be recorded as having come from a person"
        assert top["example"] == f"{gold['deposit']:.2f}" or top["example"]
        # and it is offered for a column of the same meaning under a different key
        other = [x["label"].lower() for x in LX.candidates(db, {"key": "security_deposit", "label": "Security deposit", "ctype": "money"})]
        assert "security held" in other
        # C-EXA logged what she learned
        from cbx.models import AuditLog

        assert db.query(AuditLog).filter(AuditLog.action == "pdf_lexicon_learned").count() >= 1
        s = LX.stats(db)
        assert s["learned"] >= 1 and s["from_corrections"] >= 1 and s["seeds"] == LX.seed_count()
    finally:
        db.close()


def test_a_broken_lexicon_never_blocks_a_persons_fix(user, bills, monkeypatch):
    tid, bid, batch = _run_batch(user, bills, EMF, name="Broken lexicon")
    doc = batch["documents"][0]

    def boom(*a, **k):
        raise RuntimeError("lexicon is down")

    monkeypatch.setattr(C.LX, "confirm", boom)
    monkeypatch.setattr(C.LX, "reject", boom)
    monkeypatch.setattr(C, "label_near_value", boom)
    r = user.post(f"/api/pdfstudio/batches/{bid}/cells", json={"document_id": doc["id"], "column_key": "deposit", "value": "1234.00"})
    assert r.status_code == 200, r.text
    assert r.json()["cell"]["value"] == 1234.0


# ---- honest accuracy ------------------------------------------------------------------------------
def test_split_holds_documents_back_and_says_so_when_it_cannot():
    docs = [{"id": i} for i in range(12)]
    train, hold = R.split(docs)
    assert len(hold) == 4 and len(train) == 8
    assert not ({d["id"] for d in train} & {d["id"] for d in hold})
    assert max(d["id"] for d in hold) - min(d["id"] for d in hold) > 4, "held-out docs must span the batch"
    for n in (0, 1, 2, 3):
        t, h = R.split([{"id": i} for i in range(n)])
        assert h == [] and len(t) == n, "under four documents there is nothing honest to hold back"
    t, h = R.split([{"id": i} for i in range(4)])
    assert len(h) == 1 and len(t) == 3


def test_an_overfitted_profile_scores_worse_on_documents_it_never_saw(bills):
    """A ruleset written for one layout and measured on another: training accuracy flatters it,
    held-out accuracy does not. This is the shape of the 90%-reported / 52.7%-wrong bug."""
    columns = [dict(c) for c in COLUMNS]
    train = [{"id": i, "ordinal": i, "name": n, "filename": n, "spans": _spans(bills, n), "cells": {}} for i, n in enumerate(EMF)]
    other = [f"centlec-CEN-2092{i}.pdf" for i in (4, 5, 7)]  # a different layout entirely
    hold = [{"id": 90 + i, "ordinal": 90 + i, "name": n, "filename": n, "spans": _spans(bills, n), "cells": {}} for i, n in enumerate(other)]
    rs = {
        "match": {"all": [], "none": []},
        "validators": {"vat_rate": 0.15, "sum_check": True},
        "fields": {
            "account_no": {"rules": [{"id": "r1", "type": "label_right", "label": "Account No.", "pick": "first", "quality": "exact"}]},
            "kwh": {"rules": [{"id": "r1", "type": "label_right", "label": "Consumption (kWh)", "pick": "first", "quality": "exact"}]},
            "excl_vat": {"rules": [{"id": "r1", "type": "label_right", "label": "Sub-total (excl. VAT)", "pick": "last", "quality": "exact"}]},
            "vat": {"rules": [{"id": "r1", "type": "label_right", "label": "VAT @ 15%", "pick": "last", "quality": "exact"}]},
            "total": {"rules": [{"id": "r1", "type": "label_right", "label": "TOTAL DUE", "pick": "last", "quality": "exact"}]},
            "deposit": {"rules": [{"id": "r1", "type": "label_right", "label": "Security held", "pick": "last", "quality": "exact"}]},
        },
    }
    bt = R.measure(columns, train, hold, rs=rs)
    assert bt["trained_on"] == 3 and bt["held_out"] == 3
    assert bt["holdout_accuracy"] is not None
    assert bt["accuracy"] > bt["holdout_accuracy"], bt
    assert bt["accuracy"] >= 0.9, "the rules really do work on the layout they were written for"
    assert R.honest(bt) == bt["holdout_accuracy"]
    assert "not built from" in bt["note"]

    too_few = R.measure(columns, train, [], rs=rs)
    assert too_few["holdout_accuracy"] is None and R.honest(too_few) is None
    assert "too few to check" in too_few["note"]


def test_a_profile_reports_the_held_out_number_not_the_training_one(user, bills):
    tid, bid, batch = _run_batch(user, bills, EKU, name="Honest accuracy")
    assert batch["status"] in ("review", "done"), batch.get("error")
    db = SessionLocal()
    try:
        prof = db.query(Profile).filter(Profile.template_id == tid).order_by(Profile.id.desc()).first()
        assert prof is not None
        ver = db.get(ProfileVersion, prof.live_version_id)
        bt = ver.backtest or {}
        for key in ("docs", "passed", "accuracy", "holdout_accuracy", "by_column", "trained_on", "held_out"):
            assert key in bt, f"the stored backtest is missing {key}: {sorted(bt)}"
        assert bt["held_out"] >= 1 and bt["trained_on"] >= 2
        assert bt["trained_on"] + bt["held_out"] == len(EKU)
        assert bt["holdout_accuracy"] is not None
        # the number kept on the profile — the one the gates and the page use — is the held-out one
        assert prof.accuracy == pytest.approx(round(bt["holdout_accuracy"], 4))
        from cbx.pdfstudio import profiles as PR

        h = PR.honest_accuracy(prof)
        assert h["basis"] == "held_out" and h["accuracy"] == pytest.approx(bt["holdout_accuracy"])
        assert "had not seen" in h["phrase"]
        # the held-out documents were never used to build the profile
        held = bt["held_out"]
        assert db.query(Document).filter(Document.profile_id == prof.id, Document.is_sample.is_(True)).count() <= len(EKU) - held
    finally:
        db.close()
    r = user.get("/api/pdfstudio/profiles")
    if r.status_code == 200:  # admin-only; a plain user gets 403 and that is fine
        for p in r.json()["profiles"]:
            assert "honest" in p and p["accuracy"] == p["honest"]["accuracy"]


def test_a_batch_teaches_the_lexicon_the_labels_that_worked(user, bills):
    """Every batch adds to what C-EXA knows — not only the batches someone had to correct."""
    tid, bid, batch = _run_batch(user, bills, EMF, name="Batch teaches")
    db = SessionLocal()
    try:
        s = LX.stats(db, top=50)
        assert s["learned"] >= 3, "a whole layout read should leave more than a couple of labels behind"
        learned = {(r["group"], r["label"].lower()) for r in s["top"]}
        assert any(g == "total" for g, _ in learned), learned
        # the label is recorded against the layout it worked on, with a real example value
        rows = LX.candidates(db, {"key": "total", "label": "Total due", "ctype": "money"})
        top = next(r for r in rows if r.get("source") == "learned")
        assert top["layouts"] and top["example"]
    finally:
        db.close()


def test_a_batch_refuses_to_learn_a_printed_heading_as_an_answer(bills):
    """`meter_number = "METER TYPE"` scored 100% for months because the rule fired consistently.
    A value printed on every document of the layout is the layout, not the answer."""
    from cbx.pdfstudio import pipeline as PL

    docs = [{"id": i, "spans": _spans(bills, n)} for i, n in enumerate(EMF)]
    blobs = PL._page_text(docs)
    assert PL._is_heading_text("Consumption (kWh)", blobs) is True
    assert PL._is_heading_text("Security held", blobs) is True
    assert PL._is_heading_text(_bill(bills, EMF[0])["gold"]["account_no"], blobs) is False
    assert PL._is_heading_text("", blobs) is False


def test_a_batch_refuses_to_learn_a_rule_that_never_moves(bills):
    """A date or an amount that is identical on every bill of a layout is a fixed field on the page,
    not that bill's figure — the design's variance test, applied before anything is remembered."""
    from cbx.pdfstudio import pipeline as PL

    columns = [{"key": "account_date", "label": "Account date", "ctype": "date"}]
    fields = {"account_date": {"rules": [{"id": "r1", "type": "label_below", "label": "Deposit Date"}]}}
    cells = {"account_date": {"value": "1999-01-01", "status": "ok", "score": 1.0, "method": "r1"}}
    fam = {
        "family": "fixed-date-layout",
        "rs": {"fields": fields},
        "backtest": {"by_column": {"account_date": 1.0}},
        "docs": [{"id": i, "spans": _spans(bills, n), "cells": {k: dict(v) for k, v in cells.items()}} for i, n in enumerate(EMF)],
    }
    db = SessionLocal()
    try:
        assert PL._remember_labels(db, columns, fam) == 0, "the same date on every bill teaches nothing"
        # the same label with a value that moves is remembered
        for i, d in enumerate(fam["docs"]):
            d["cells"]["account_date"]["value"] = f"2026-0{i + 1}-01"
        assert PL._remember_labels(db, columns, fam) == 1
        db.commit()
        assert "Deposit Date" in [r["label"] for r in LX.candidates(db, {"key": "account_date", "label": "Account date", "ctype": "date"})]
    finally:
        db.close()


def test_too_few_documents_says_too_few_rather_than_inventing_a_number(user, bills):
    tid, bid, batch = _run_batch(user, bills, EMF[:2], name="Too few")
    db = SessionLocal()
    try:
        from cbx.pdfstudio import profiles as PR

        prof = db.query(Profile).filter(Profile.template_id == tid).order_by(Profile.id.desc()).first()
        assert prof is not None
        assert prof.accuracy is None, "two bills cannot produce an honest accuracy"
        h = PR.honest_accuracy(prof)
        assert h["basis"] == "too_few" and h["accuracy"] is None
        assert "too few to check" in h["phrase"]
    finally:
        db.close()


def test_cexa_can_say_what_he_knows(db):
    from cbx.cexa import tools as T

    assert "what_i_know" in T.TOOLS
    LX.confirm(db, column_key="total_due", label="Bedrag Verskuldig", placement="right", layout="afr-layout", source="correction")
    db.commit()
    out = T.TOOLS["what_i_know"]["fn"](db, None, None, column="total_due")
    assert "Bedrag Verskuldig" in out and "value to the right" in out
    overview = T.TOOLS["what_i_know"]["fn"](db, None, None, column="")
    assert "shipped as a starting vocabulary" in overview
